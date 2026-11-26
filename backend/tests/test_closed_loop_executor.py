import threading
import unittest
from datetime import timedelta

from fastapi.testclient import TestClient

from app.closed_loop_executor import ClosedLoopConfig, ClosedLoopExecutor
from app.main import app, run_closed_loop_cycle, seed_rules
from app.models import utc_now
from app.store import store


ROBOT = {
    "robot_id": "robot-01",
    "robot_type": "MOBILE",
    "control_adapter": "mock-mobile-adapter",
    "status": "IDLE",
    "pose": {
        "frame_id": "map",
        "position": {"x": 0, "y": 0, "z": 0},
        "orientation": {"x": 0, "y": 0, "z": 0, "w": 1},
    },
    "velocity": {"x": 0, "y": 0, "z": 0},
    "motion_limits": {"max_speed": 1.0, "max_acceleration": 0.5},
}


class ClosedLoopExecutorTest(unittest.TestCase):
    def setUp(self) -> None:
        store.reset()
        seed_rules()
        self.client = TestClient(app)
        response = self.client.post("/robots", json=ROBOT)
        self.assertEqual(response.status_code, 201, response.text)
        self.config = ClosedLoopConfig(
            enabled=True,
            poll_interval_seconds=0.5,
            max_attempts_per_step=3,
            result_timeout_seconds=120,
        )

    def create_task(self, instruction: str) -> dict:
        response = self.client.post(
            "/task-orders/from-natural-language",
            json={"instruction": instruction},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def cycle(self, config: ClosedLoopConfig | None = None) -> None:
        run_closed_loop_cycle(config or self.config)

    def report(self, proposal_id: str, body: dict) -> None:
        response = self.client.post(
            f"/action-proposals/{proposal_id}/result",
            json=body,
        )
        self.assertEqual(response.status_code, 200, response.text)

    def latest_proposal(self, task_id: str):
        proposals = [
            proposal
            for proposal in store.proposals.values()
            if proposal.task_id == task_id
        ]
        self.assertTrue(proposals)
        return proposals[-1]

    def test_cycle_completes_object_transport_without_manual_stage_calls(self) -> None:
        response = self.client.post(
            "/physical-objects",
            json={
                "object_id": "상자3",
                "object_type": "BOX",
                "pose": {
                    "frame_id": "map",
                    "position": {"x": 0.5, "y": 0.3, "z": 0.02},
                    "orientation": {"x": 0, "y": 0, "z": 0, "w": 1},
                },
                "current_zone_id": "A구역",
                "movable": True,
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        task = self.create_task(
            "robot-01 로봇으로 상자 3을 좌표 (1, 2, 0)로 옮겨"
        )

        expected = [
            ("MOVE_TO", None),
            ("MANIPULATE", "PICK"),
            ("MOVE_TO", None),
            ("MANIPULATE", "PLACE"),
        ]
        for action_type, manipulation in expected:
            self.cycle()
            proposal = self.latest_proposal(task["task_id"])
            self.assertEqual(proposal.status, "EXECUTING")
            self.assertEqual(proposal.action_type, action_type)
            self.assertEqual(proposal.parameters.get("manipulation"), manipulation)
            self.report(
                proposal.proposal_id,
                {"status": "SUCCEEDED", "detail": "closed-loop step done"},
            )

        self.assertEqual(store.tasks[task["task_id"]].status, "COMPLETED")
        self.assertEqual(store.objects["상자3"].pose.position.x, 1.0)
        self.assertEqual(store.objects["상자3"].pose.position.y, 2.0)

    def test_safety_rejection_holds_task_without_retry(self) -> None:
        task = self.create_task("robot-01 로봇을 좌표 (1, 2, 0)로 이동해")
        with store.lock:
            store.robots["robot-01"].control_adapter = ""

        self.cycle()

        proposal = self.latest_proposal(task["task_id"])
        self.assertEqual(proposal.status, "REJECTED")
        self.assertEqual(store.tasks[task["task_id"]].status, "HELD")
        self.assertEqual(store.robots["robot-01"].status, "HOLD")
        self.cycle()
        self.assertEqual(
            len([p for p in store.proposals.values() if p.task_id == task["task_id"]]),
            1,
        )

    def test_result_timeout_holds_without_unsafe_retry(self) -> None:
        config = ClosedLoopConfig(
            enabled=True,
            poll_interval_seconds=0.5,
            max_attempts_per_step=3,
            result_timeout_seconds=1,
        )
        task = self.create_task("robot-01 로봇을 좌표 (1, 2, 0)로 이동해")
        self.cycle(config)
        proposal = self.latest_proposal(task["task_id"])
        self.assertEqual(proposal.status, "EXECUTING")
        with store.lock:
            store.proposal_acks[proposal.proposal_id].dispatched_at = (
                utc_now() - timedelta(seconds=2)
            )
        self.cycle(config)

        proposals = [
            proposal
            for proposal in store.proposals.values()
            if proposal.task_id == task["task_id"]
        ]
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].status, "FAILED")
        self.assertEqual(store.tasks[task["task_id"]].status, "HELD")
        self.assertEqual(store.robots["robot-01"].status, "HOLD")

    def test_unsupported_result_replans_same_step(self) -> None:
        task = self.create_task("robot-01 로봇을 좌표 (1, 2, 0)로 이동해")
        self.cycle()
        first = self.latest_proposal(task["task_id"])
        self.report(
            first.proposal_id,
            {
                "status": "FAILED",
                "reason_code": "UNSUPPORTED",
                "detail": "adapter does not support action",
            },
        )

        self.cycle()

        second = self.latest_proposal(task["task_id"])
        self.assertNotEqual(second.proposal_id, first.proposal_id)
        self.assertEqual(second.status, "EXECUTING")
        self.assertEqual(store.tasks[task["task_id"]].status, "RUNNING")

    def test_unsupported_stops_after_attempt_budget(self) -> None:
        task = self.create_task("robot-01 로봇을 좌표 (1, 2, 0)로 이동해")

        for attempt in range(3):
            self.cycle()
            proposal = self.latest_proposal(task["task_id"])
            self.assertEqual(proposal.status, "EXECUTING")
            self.report(
                proposal.proposal_id,
                {
                    "status": "FAILED",
                    "reason_code": "UNSUPPORTED",
                    "detail": "adapter does not support action",
                },
            )
            if attempt < 2:
                self.assertEqual(store.tasks[task["task_id"]].status, "PLANNING")

        self.cycle()

        self.assertEqual(store.tasks[task["task_id"]].status, "FAILED")
        self.assertEqual(store.robots["robot-01"].status, "HOLD")
        self.assertEqual(
            len([p for p in store.proposals.values() if p.task_id == task["task_id"]]),
            3,
        )

    def test_hardware_error_stops_without_retry(self) -> None:
        task = self.create_task("robot-01 로봇을 좌표 (1, 2, 0)로 이동해")
        self.cycle()
        proposal = self.latest_proposal(task["task_id"])
        self.report(
            proposal.proposal_id,
            {"status": "FAILED", "reason_code": "HW_ERROR"},
        )

        self.cycle()

        self.assertEqual(store.tasks[task["task_id"]].status, "FAILED")
        self.assertEqual(store.robots["robot-01"].status, "ERROR")
        self.assertEqual(
            len([p for p in store.proposals.values() if p.task_id == task["task_id"]]),
            1,
        )


class ClosedLoopWorkerLifecycleTest(unittest.TestCase):
    def test_enabled_worker_runs_and_stops(self) -> None:
        called = threading.Event()
        executor = ClosedLoopExecutor(
            ClosedLoopConfig(
                enabled=True,
                poll_interval_seconds=0.01,
                max_attempts_per_step=3,
                result_timeout_seconds=1,
            ),
            called.set,
        )

        executor.start()
        try:
            self.assertTrue(called.wait(1))
            self.assertTrue(executor.running)
        finally:
            executor.stop()
        self.assertFalse(executor.running)


if __name__ == "__main__":
    unittest.main()
