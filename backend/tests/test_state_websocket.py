import unittest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.main import app, apply_ctl_safety_event, seed_rules
from app.store import store


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


if __name__ == "__main__":
    unittest.main()
