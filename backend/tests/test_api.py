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


class ApiTest(unittest.TestCase):
    def setUp(self) -> None:
        store.reset()
        seed_rules()
        self.client = TestClient(app)
        response = self.client.post("/robots", json=ROBOT)
        self.assertEqual(response.status_code, 201)

    def create_task(self, instruction: str = "robot-01 로봇을 A구역으로 이동해") -> dict:
        response = self.client.post(
            "/task-orders/from-natural-language",
            json={"instruction": instruction},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def create_proposal(self, task_id: str, speed: float) -> dict:
        response = self.client.post(
            "/action-proposals",
            json={
                "task_id": task_id,
                "robot_id": "robot-01",
                "action_type": "MOVE_TO",
                "parameters": {"destination": "A구역"},
                "trajectory": None,
                "frame_id": "map",
                "speed_limit": speed,
                "model_name": "rule-demo",
                "model_version": "0.1",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_natural_language_to_task_order(self) -> None:
        task = self.create_task("robot-01 로봇을 좌표 (1.5, -2, 0) 위치로 이동해")
        self.assertEqual(task["target_robot_id"], "robot-01")
        self.assertEqual(task["destination"]["type"], "COORDINATE")
        self.assertEqual(task["destination"]["position"]["y"], -2.0)
        self.assertEqual(task["status"], "CREATED")

    def test_zone_and_object_are_extracted_without_neighboring_words(self) -> None:
        task = self.create_task("robot-01 로봇으로 상자 3을 B 작업대로 옮겨")
        self.assertEqual(task["destination"], {"type": "ZONE", "zone_id": "B작업대"})
        self.assertEqual(task["target_object_id"], "상자3")

    def test_parser_rejects_ambiguous_destination(self) -> None:
        response = self.client.post(
            "/task-orders/from-natural-language",
            json={"instruction": "robot-01 로봇 움직여"},
        )
        self.assertEqual(response.status_code, 422)

    def test_safe_proposal_is_approved_and_executed(self) -> None:
        task = self.create_task()
        proposal = self.create_proposal(task["task_id"], speed=0.5)

        evaluated = self.client.post(f"/action-proposals/{proposal['proposal_id']}/evaluate")
        self.assertEqual(evaluated.status_code, 200)
        self.assertEqual(evaluated.json()["status"], "APPROVED")

        executed = self.client.post(f"/action-proposals/{proposal['proposal_id']}/execute")
        self.assertEqual(executed.status_code, 200)
        self.assertEqual(executed.json()["status"], "EXECUTING")
        self.assertEqual(len(self.client.get("/execution-logs").json()), 2)

    def test_speed_limit_rejects_proposal(self) -> None:
        task = self.create_task()
        proposal = self.create_proposal(task["task_id"], speed=1.5)
        evaluated = self.client.post(f"/action-proposals/{proposal['proposal_id']}/evaluate")
        self.assertEqual(evaluated.json()["status"], "REJECTED")

    def test_critical_hazard_rejects_proposal(self) -> None:
        task = self.create_task()
        hazard = {
            "hazard_type": "FIRE",
            "severity": "CRITICAL",
            "source_observation_ids": ["obs-1"],
            "related_robot_ids": ["robot-01"],
            "related_worker_ids": [],
            "zone_id": "A구역",
            "confidence": 0.99,
        }
        self.assertEqual(self.client.post("/hazards", json=hazard).status_code, 201)
        proposal = self.create_proposal(task["task_id"], speed=0.5)
        evaluated = self.client.post(f"/action-proposals/{proposal['proposal_id']}/evaluate")
        self.assertEqual(evaluated.json()["status"], "REJECTED")
        logs = self.client.get("/execution-logs").json()
        self.assertEqual(logs[-1]["decision"], "REJECT")
        self.assertEqual(len(logs[-1]["hazard_ids"]), 1)

    def test_new_hazard_blocks_already_approved_proposal_before_execution(self) -> None:
        task = self.create_task()
        proposal = self.create_proposal(task["task_id"], speed=0.5)
        evaluated = self.client.post(f"/action-proposals/{proposal['proposal_id']}/evaluate")
        self.assertEqual(evaluated.json()["status"], "APPROVED")

        hazard = {
            "hazard_type": "FIRE",
            "severity": "CRITICAL",
            "source_observation_ids": ["obs-after-approval"],
            "related_robot_ids": ["robot-01"],
            "related_worker_ids": [],
            "zone_id": "A구역",
            "confidence": 0.99,
        }
        self.assertEqual(self.client.post("/hazards", json=hazard).status_code, 201)

        executed = self.client.post(f"/action-proposals/{proposal['proposal_id']}/execute")
        self.assertEqual(executed.status_code, 409)
        logs = self.client.get("/execution-logs").json()
        self.assertEqual(logs[-1]["decision"], "ESTOP")


if __name__ == "__main__":
    unittest.main()
