
"""HazardEvent linkage contract tests for safety-centric-vla (develop).

Copy to backend/tests/test_hazard_linkage.py and run from backend/:
    python3 -m unittest tests.test_hazard_linkage -v
Or:
    python3 -m unittest discover -s tests -p 'test_hazard_linkage.py' -v

These are API-level contract tests. They use mock proposals and do not
measure CV detection, semantic observation fusion, or real ROS 2 stopping.
"""

import unittest
from fastapi.testclient import TestClient
from app.main import app, seed_rules
from app.store import store
from tests.zone_fixtures import register_work_zones


def robot_payload(robot_id):
    return {
        "robot_id": robot_id,
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


class HazardLinkageTests(unittest.TestCase):
    def setUp(self):
        store.reset()
        seed_rules()
        self.client = TestClient(app)
        register_work_zones(self.client, "A구역", "B구역")
        for robot_id in ("robot-01", "robot-02"):
            response = self.client.post("/robots", json=robot_payload(robot_id))
            self.assertEqual(response.status_code, 201, response.text)

    def add_hazard(self, *, severity="CRITICAL", hazard_type="FIRE",
                   robots=None, zone="B구역", hazard_id=None):
        payload = {
            "hazard_type": hazard_type,
            "severity": severity,
            "source_observation_ids": ["obs-fixture-01"],
            "related_robot_ids": [] if robots is None else robots,
            "related_worker_ids": [],
            "zone_id": zone,
            "confidence": 0.99,
        }
        if hazard_id is not None:
            payload["hazard_id"] = hazard_id
        response = self.client.post("/hazards", json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def task_proposal(self, robot_id="robot-01", zone="A구역", coordinate=False):
        if coordinate:
            instruction = f"{robot_id} 로봇을 좌표 (1.5, -2, 0) 위치로 이동해"
        else:
            instruction = f"{robot_id} 로봇을 {zone}으로 이동해"
        response = self.client.post(
            "/task-orders/from-natural-language",
            json={"instruction": instruction},
        )
        self.assertEqual(response.status_code, 201, response.text)
        task = response.json()
        response = self.client.post(
            f"/task-orders/{task['task_id']}/action-proposals",
            json={"speed_limit": 0.4},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return task, response.json()

    def evaluate(self, proposal):
        response = self.client.post(
            f"/action-proposals/{proposal['proposal_id']}/evaluate"
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def last_log(self):
        response = self.client.get("/execution-logs")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()[-1]

    def test_h01_critical_explicit_robot_relation_rejects(self):
        hazard = self.add_hazard(robots=["robot-01"], zone="B구역")
        _, proposal = self.task_proposal("robot-01", "A구역")
        self.assertEqual(self.evaluate(proposal)["status"], "REJECTED")
        self.assertIn(hazard["hazard_id"], self.last_log()["hazard_ids"])

    def test_h02_unrelated_robot_is_not_blocked(self):
        hazard = self.add_hazard(robots=["robot-01"], zone="B구역")
        _, proposal = self.task_proposal("robot-02", "A구역")
        self.assertEqual(self.evaluate(proposal)["status"], "APPROVED")
        self.assertNotIn(hazard["hazard_id"], self.last_log()["hazard_ids"])

    def test_h03_same_destination_zone_rejects_even_without_robot_id(self):
        hazard = self.add_hazard(robots=[], zone="A구역")
        _, proposal = self.task_proposal("robot-02", "A구역")
        self.assertEqual(self.evaluate(proposal)["status"], "REJECTED")
        self.assertIn(hazard["hazard_id"], self.last_log()["hazard_ids"])

    def test_h04_empty_robot_ids_is_not_global_block(self):
        hazard = self.add_hazard(robots=[], zone="B구역")
        _, proposal = self.task_proposal("robot-01", "A구역")
        self.assertEqual(self.evaluate(proposal)["status"], "APPROVED")
        self.assertNotIn(hazard["hazard_id"], self.last_log()["hazard_ids"])

    def test_h05_warning_does_not_block_under_current_backend_rule(self):
        self.add_hazard(severity="WARNING", robots=["robot-01"], zone="A구역")
        _, proposal = self.task_proposal("robot-01", "A구역")
        self.assertEqual(self.evaluate(proposal)["status"], "APPROVED")

    def test_h06_resolved_critical_no_longer_blocks(self):
        hazard = self.add_hazard(robots=["robot-01"], zone="A구역")
        response = self.client.post(
            f"/hazards/{hazard['hazard_id']}/resolve",
            json={"resolved_by": "OPERATOR"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "RESOLVED")
        _, proposal = self.task_proposal("robot-01", "A구역")
        self.assertEqual(self.evaluate(proposal)["status"], "APPROVED")

    def test_h07_sensor_auto_cannot_resolve_critical(self):
        hazard = self.add_hazard(robots=["robot-01"], zone="A구역")
        response = self.client.post(
            f"/hazards/{hazard['hazard_id']}/resolve",
            json={"resolved_by": "SENSOR_AUTO"},
        )
        self.assertEqual(response.status_code, 409, response.text)
        _, proposal = self.task_proposal("robot-01", "A구역")
        self.assertEqual(self.evaluate(proposal)["status"], "REJECTED")

    def test_h08_coordinate_destination_does_not_infer_zone(self):
        hazard = self.add_hazard(robots=[], zone="A구역")
        _, proposal = self.task_proposal("robot-01", coordinate=True)
        self.assertEqual(self.evaluate(proposal)["status"], "APPROVED")
        self.assertNotIn(hazard["hazard_id"], self.last_log()["hazard_ids"])

    def test_h09_hazard_identifier_uniqueness(self):
        self.add_hazard(hazard_id="hazard-duplicate")
        response = self.client.post("/hazards", json={
            "hazard_id": "hazard-duplicate",
            "hazard_type": "FIRE",
            "severity": "CRITICAL",
            "source_observation_ids": ["obs-fixture-02"],
            "related_robot_ids": ["robot-01"],
            "related_worker_ids": [],
            "zone_id": "A구역",
            "confidence": 0.9,
        })
        self.assertEqual(response.status_code, 409, response.text)

    def test_h10_unknown_hazard_type_is_rejected(self):
        response = self.client.post("/hazards", json={
            "hazard_type": "RANDOM_HAZARD",
            "severity": "CRITICAL",
            "source_observation_ids": ["obs-fixture-03"],
            "related_robot_ids": ["robot-01"],
            "related_worker_ids": [],
            "zone_id": "A구역",
            "confidence": 0.9,
        })
        self.assertEqual(response.status_code, 422, response.text)

    def test_h11_approved_proposal_is_rechecked_before_execute(self):
        task, proposal = self.task_proposal("robot-01", "A구역")
        self.assertEqual(self.evaluate(proposal)["status"], "APPROVED")
        hazard = self.add_hazard(robots=["robot-01"], zone="A구역")
        result = self.client.post(
            f"/action-proposals/{proposal['proposal_id']}/execute"
        )
        self.assertEqual(result.status_code, 409, result.text)
        self.assertEqual(self.last_log()["decision"], "ESTOP")
        self.assertIn(hazard["hazard_id"], self.last_log()["hazard_ids"])
        task_after = self.client.get(f"/task-orders/{task['task_id']}")
        self.assertEqual(task_after.json()["status"], "HELD")
        robots_after = self.client.get("/robots").json()
        self.assertEqual(
            next(r["status"] for r in robots_after if r["robot_id"] == "robot-01"),
            "ESTOP",
        )

    def test_h12_each_declared_critical_hazard_type_blocks_related_robot(self):
        types = (
            "WORKER_INTRUSION", "COLLISION_RISK", "FIRE",
            "SMOKE", "FALLING_OBJECT", "UNKNOWN",
        )
        for hazard_type in types:
            with self.subTest(hazard_type=hazard_type):
                self.setUp()
                hazard = self.add_hazard(
                    hazard_type=hazard_type, robots=["robot-01"], zone="A구역"
                )
                _, proposal = self.task_proposal("robot-01", "A구역")
                self.assertEqual(self.evaluate(proposal)["status"], "REJECTED")
                self.assertIn(hazard["hazard_id"], self.last_log()["hazard_ids"])


if __name__ == "__main__":
    unittest.main()
