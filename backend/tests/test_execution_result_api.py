import unittest

from fastapi.testclient import TestClient

from app.main import app, seed_rules
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


class ExecutionResultApiTest(unittest.TestCase):
    def setUp(self) -> None:
        store.reset()
        seed_rules()
        self.client = TestClient(app)
        response = self.client.post("/robots", json=ROBOT)
        self.assertEqual(response.status_code, 201, response.text)

    def start_execution(self) -> tuple[dict, dict]:
        task_response = self.client.post(
            "/task-orders/from-natural-language",
            json={"instruction": "robot-01 로봇을 A구역으로 이동해"},
        )
        self.assertEqual(task_response.status_code, 201, task_response.text)
        task = task_response.json()
        proposal_response = self.client.post(
            f"/task-orders/{task['task_id']}/action-proposals",
            json={"speed_limit": 0.4},
        )
        self.assertEqual(proposal_response.status_code, 201, proposal_response.text)
        proposal = proposal_response.json()
        evaluated = self.client.post(
            f"/action-proposals/{proposal['proposal_id']}/evaluate"
        )
        self.assertEqual(evaluated.json()["status"], "APPROVED")
        executed = self.client.post(
            f"/action-proposals/{proposal['proposal_id']}/execute"
        )
        self.assertEqual(executed.status_code, 200, executed.text)
        self.assertEqual(executed.json()["status"], "EXECUTING")
        return task, proposal

    def report(self, proposal_id: str, body: dict):
        return self.client.post(
            f"/action-proposals/{proposal_id}/result",
            json=body,
        )

    def test_success_completes_proposal_task_and_robot(self) -> None:
        task, proposal = self.start_execution()
        response = self.report(
            proposal["proposal_id"],
            {"status": "SUCCEEDED", "detail": "목적지 도착"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "SUCCEEDED")
        self.assertEqual(store.tasks[task["task_id"]].status, "COMPLETED")
        self.assertEqual(store.robots["robot-01"].status, "IDLE")
        self.assertEqual(store.logs[-1].decision, "COMPLETE")
        self.assertEqual(store.logs[-1].result_message, "목적지 도착")

    def test_unsupported_returns_task_to_planning_for_replan(self) -> None:
        task, proposal = self.start_execution()
        response = self.report(
            proposal["proposal_id"],
            {
                "status": "FAILED",
                "reason_code": "UNSUPPORTED",
                "detail": "adapter does not support MANIPULATE",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "FAILED")
        self.assertEqual(store.tasks[task["task_id"]].status, "PLANNING")
        self.assertEqual(store.robots["robot-01"].status, "HOLD")
        replanned = self.client.post(
            f"/task-orders/{task['task_id']}/action-proposals",
            json={"speed_limit": 0.3},
        )
        self.assertEqual(replanned.status_code, 201, replanned.text)

    def test_timeout_fails_task_and_holds_robot(self) -> None:
        task, proposal = self.start_execution()
        response = self.report(
            proposal["proposal_id"],
            {"status": "FAILED", "reason_code": "TIMEOUT"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(store.tasks[task["task_id"]].status, "FAILED")
        self.assertEqual(store.robots["robot-01"].status, "HOLD")
        self.assertEqual(store.logs[-1].decision, "FAIL")

    def test_hardware_error_fails_task_and_marks_robot_error(self) -> None:
        task, proposal = self.start_execution()
        response = self.report(
            proposal["proposal_id"],
            {"status": "FAILED", "reason_code": "HW_ERROR"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(store.tasks[task["task_id"]].status, "FAILED")
        self.assertEqual(store.robots["robot-01"].status, "ERROR")

    def test_exact_retry_is_idempotent_but_conflict_is_rejected(self) -> None:
        _, proposal = self.start_execution()
        body = {"status": "SUCCEEDED", "detail": "done"}
        first = self.report(proposal["proposal_id"], body)
        self.assertEqual(first.status_code, 200, first.text)
        log_count = len(store.logs)

        replay = self.report(proposal["proposal_id"], body)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(len(store.logs), log_count)

        conflict = self.report(
            proposal["proposal_id"],
            {"status": "FAILED", "reason_code": "TIMEOUT"},
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(len(store.logs), log_count)

    def test_result_contract_and_state_are_validated(self) -> None:
        task_response = self.client.post(
            "/task-orders/from-natural-language",
            json={"instruction": "robot-01 로봇을 A구역으로 이동해"},
        )
        task = task_response.json()
        proposal = self.client.post(
            f"/task-orders/{task['task_id']}/action-proposals",
            json={},
        ).json()

        not_executing = self.report(
            proposal["proposal_id"],
            {"status": "SUCCEEDED"},
        )
        self.assertEqual(not_executing.status_code, 409)
        missing_code = self.report(
            proposal["proposal_id"],
            {"status": "FAILED"},
        )
        self.assertEqual(missing_code.status_code, 422)
        success_with_code = self.report(
            proposal["proposal_id"],
            {"status": "SUCCEEDED", "reason_code": "TIMEOUT"},
        )
        self.assertEqual(success_with_code.status_code, 422)


if __name__ == "__main__":
    unittest.main()
