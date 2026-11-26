import unittest

from fastapi.testclient import TestClient

from app.main import app, seed_rules
from app.models import Robot, TaskOrder
from app.store import store


class OpenVlaActionApiTest(unittest.TestCase):
    def setUp(self) -> None:
        store.reset()
        seed_rules()
        store.robots["robot-01"] = Robot.model_validate({
            "robot_id": "robot-01",
            "robot_type": "MOBILE",
            "control_adapter": "mujoco_diff_drive",
            "status": "IDLE",
            "pose": {
                "frame_id": "world",
                "position": {"x": 0, "y": 0, "z": 0},
                "orientation": {"x": 0, "y": 0, "z": 0, "w": 1},
            },
            "velocity": {"x": 0, "y": 0, "z": 0},
            "motion_limits": {"max_speed": 0.4, "max_acceleration": 0.5},
        })
        store.tasks["task-vla"] = TaskOrder.model_validate({
            "task_id": "task-vla",
            "instruction": "robot-01이 빨간 상자를 B구역으로 옮겨줘",
            "target_robot_id": "robot-01",
            "target_object_id": "box-red",
            "destination": {"type": "ZONE", "zone_id": "B구역"},
        })
        self.client = TestClient(app)

    def test_submits_7d_as_proposed_action_then_safety_approves(self):
        response = self.client.post(
            "/task-orders/task-vla/openvla-actions",
            json={
                "action_7d": [0.01, -0.02, 0.03, 0.1, 0.0, -0.2, 0.8],
                "manipulation": "PICK",
                "speed_limit": 0.2,
                "model_version": "bridge_orig",
            },
        )

        self.assertEqual(response.status_code, 201, response.text)
        proposal = response.json()
        self.assertEqual(proposal["status"], "PROPOSED")
        self.assertEqual(proposal["parameters"]["ee_delta"]["translation"]["x"], 0.01)
        evaluated = self.client.post(f"/action-proposals/{proposal['proposal_id']}/evaluate")
        self.assertEqual(evaluated.status_code, 200, evaluated.text)
        self.assertEqual(evaluated.json()["status"], "APPROVED")

    def test_safety_rejects_oversized_7d_step(self):
        response = self.client.post(
            "/task-orders/task-vla/openvla-actions",
            json={
                "action_7d": [0.051, 0, 0, 0, 0, 0.251, 0],
                "manipulation": "PICK",
                "speed_limit": 0.2,
            },
        )
        proposal = response.json()

        evaluated = self.client.post(f"/action-proposals/{proposal['proposal_id']}/evaluate")

        self.assertEqual(evaluated.status_code, 200, evaluated.text)
        self.assertEqual(evaluated.json()["status"], "REJECTED")
        self.assertFalse(store.logs[-1].rule_results[2].passed)
        self.assertEqual(store.logs[-1].rule_results[2].rule_id, "precondition-ee-delta-bounds")

    def test_does_not_accept_next_cycle_while_previous_is_active(self):
        body = {
            "action_7d": [0] * 7,
            "manipulation": "PICK",
            "speed_limit": 0.2,
        }
        first = self.client.post("/task-orders/task-vla/openvla-actions", json=body)
        second = self.client.post("/task-orders/task-vla/openvla-actions", json=body)

        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(second.status_code, 409, second.text)


if __name__ == "__main__":
    unittest.main()
