import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main
from app.main import app, apply_ctl_safety_event, seed_rules
from app.models import Decision, ExecutionLog
from app.store import store
from tests.zone_fixtures import register_work_zones


ROBOT = {
    "robot_id": "robot-ws-01",
    "robot_type": "MOBILE",
    "control_adapter": "mock-mobile-adapter",
    "status": "IDLE",
    "pose": {
        "frame_id": "map",
        "position": {"x": 0.0, "y": 0.0, "z": 0.0},
        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
    },
    "velocity": {"x": 0.0, "y": 0.0, "z": 0.0},
    "motion_limits": {"max_speed": 1.0, "max_acceleration": 0.5},
}


class StateWebSocketTest(unittest.TestCase):
    def setUp(self) -> None:
        store.reset()
        seed_rules()
        self.client = TestClient(app)

    def test_connection_receives_initial_full_snapshot(self) -> None:
        with self.client.websocket_connect("/ws/state") as websocket:
            snapshot = websocket.receive_json()

        self.assertEqual(snapshot["type"], "STATE_SNAPSHOT")
        self.assertIsInstance(snapshot["revision"], int)
        self.assertIsNotNone(snapshot["observed_at"])
        self.assertIsNone(snapshot["workers_observed_at"])
        self.assertIsNone(snapshot["workers"])
        self.assertEqual(snapshot["robots"], [])
        self.assertEqual(snapshot["sensors"], [])
        self.assertEqual(snapshot["zones"], [])
        self.assertEqual(snapshot["task_orders"], [])
        self.assertEqual(snapshot["action_proposals"], [])
        self.assertEqual(snapshot["hazards"], [])
        self.assertEqual(snapshot["execution_logs"], [])
        self.assertGreaterEqual(len(snapshot["safety_rules"]), 2)

    def test_successful_write_pushes_updated_state(self) -> None:
        with self.client.websocket_connect("/ws/state") as websocket:
            initial = websocket.receive_json()
            response = self.client.post("/robots", json=ROBOT)
            self.assertEqual(response.status_code, 201, response.text)
            updated = websocket.receive_json()

        self.assertGreater(updated["revision"], initial["revision"])
        self.assertEqual(
            [robot["robot_id"] for robot in updated["robots"]],
            ["robot-ws-01"],
        )

    def test_worker_snapshot_distinguishes_missing_and_observed_empty(self) -> None:
        with self.client.websocket_connect("/ws/state") as websocket:
            missing = websocket.receive_json()
            response = self.client.put(
                "/workers/snapshot",
                json={
                    "observed_at": (
                        datetime.now(timezone.utc) - timedelta(seconds=1)
                    ).isoformat(),
                    "workers": [],
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            observed = websocket.receive_json()

        self.assertIsNone(missing["workers"])
        self.assertIsNone(missing["workers_observed_at"])
        self.assertEqual(observed["workers"], [])
        self.assertIsNotNone(observed["workers_observed_at"])

    def test_ctl_safety_event_pushes_state_without_http_request(self) -> None:
        with self.client.websocket_connect("/ws/state") as websocket:
            websocket.receive_json()
            response = self.client.post("/robots", json=ROBOT)
            self.assertEqual(response.status_code, 201, response.text)
            before_event = websocket.receive_json()

            apply_ctl_safety_event({"kind": "HOLD", "robot_id": "robot-ws-01"})
            after_event = websocket.receive_json()

        self.assertGreater(after_event["revision"], before_event["revision"])
        self.assertEqual(after_event["robots"][0]["status"], "HOLD")

    def test_snapshot_limits_execution_logs_to_most_recent_100(self) -> None:
        with store.lock:
            store.logs.extend(
                ExecutionLog(
                    proposal_id=f"proposal-{index}",
                    decision=Decision.APPROVE,
                    rule_results=[],
                    hazard_ids=[],
                    robot_id="robot-ws-01",
                    result_message="테스트 로그",
                )
                for index in range(105)
            )

        snapshot = main.make_state_snapshot()

        self.assertEqual(len(snapshot["execution_logs"]), 100)
        self.assertEqual(snapshot["execution_logs"][0]["proposal_id"], "proposal-5")
        self.assertEqual(snapshot["execution_logs"][-1]["proposal_id"], "proposal-104")

    def test_slow_snapshot_does_not_block_hazard_or_ctl_estop(self) -> None:
        register_work_zones(self.client, "A구역")
        response = self.client.post("/robots", json=ROBOT)
        self.assertEqual(response.status_code, 201, response.text)
        entered = threading.Event()
        proceed = threading.Event()
        original_model_dump = main.StateSnapshot.model_dump
        call_count = 0

        def slow_first_model_dump(snapshot, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                entered.set()
                proceed.wait(5)
            return original_model_dump(snapshot, *args, **kwargs)

        hazard_result = {}

        def register_hazard() -> None:
            hazard_result["response"] = self.client.post(
                "/hazards",
                json={
                    "hazard_type": "WORKER_INTRUSION",
                    "severity": "CRITICAL",
                    "source_observation_ids": ["cctv-1@1.0"],
                    "related_robot_ids": ["robot-ws-01"],
                    "related_worker_ids": [],
                    "zone_id": "A구역",
                    "confidence": 1.0,
                },
            )

        with patch.object(main.StateSnapshot, "model_dump", slow_first_model_dump):
            with self.client.websocket_connect("/ws/state") as websocket:
                self.assertTrue(entered.wait(5), "초기 스냅샷 직렬화가 시작되지 않았습니다.")
                hazard_thread = threading.Thread(target=register_hazard)
                ctl_thread = threading.Thread(
                    target=apply_ctl_safety_event,
                    args=({"kind": "ESTOP", "robot_id": "robot-ws-01"},),
                )
                hazard_thread.start()
                ctl_thread.start()
                hazard_thread.join(1)
                ctl_thread.join(1)
                try:
                    self.assertFalse(hazard_thread.is_alive(), "위험 등록이 스냅샷 직렬화에 막혔습니다.")
                    self.assertFalse(ctl_thread.is_alive(), "CTL E-Stop 반영이 스냅샷 직렬화에 막혔습니다.")
                finally:
                    proceed.set()
                    hazard_thread.join(5)
                    ctl_thread.join(5)
                initial = websocket.receive_json()

        self.assertEqual(hazard_result["response"].status_code, 201)
        self.assertEqual(store.robots["robot-ws-01"].status, "ESTOP")
        self.assertEqual(initial["type"], "STATE_SNAPSHOT")


if __name__ == "__main__":
    unittest.main()
