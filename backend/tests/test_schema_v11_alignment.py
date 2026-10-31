import unittest
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.main import app, seed_rules
from app.models import (
    ExecutionLog,
    HazardEvent,
    Observation,
    Robot,
    SafetyRule,
    Sensor,
    Worker,
    Zone,
)
from app.store import store


NOW = datetime(2026, 10, 30, tzinfo=timezone.utc)
POSE = {
    "frame_id": "world",
    "position": {"x": 0, "y": 0, "z": 0},
    "orientation": {"x": 0, "y": 0, "z": 0, "w": 1},
}
ROBOT = {
    "robot_id": "robot-01",
    "robot_type": "MOBILE",
    "control_adapter": "mock-mobile-adapter",
    "status": "IDLE",
    "pose": POSE,
    "velocity": {"x": 0, "y": 0, "z": 0},
    "motion_limits": {"max_speed": 1.0, "max_acceleration": 0.5},
}


class SchemaV11ModelTest(unittest.TestCase):
    def test_v11_optional_fields_are_accepted(self) -> None:
        robot = Robot.model_validate(
            {**ROBOT, "last_heartbeat_at": NOW, "battery_level": 0.75}
        )
        self.assertEqual(robot.battery_level, 0.75)

        sensor = Sensor.model_validate(
            {
                "sensor_id": "camera-01",
                "sensor_type": "CCTV",
                "source_uri": "rtsp://camera-01",
                "frame_id": "camera_camera-01",
                "pose": POSE,
                "status": "ONLINE",
                "mounted_robot_id": None,
                "covered_zone_ids": ["zone-a"],
                "last_frame_at": NOW,
                "calibrated": True,
            }
        )
        self.assertEqual(sensor.covered_zone_ids, ["zone-a"])

        observation = Observation.model_validate(
            {
                "observation_id": "obs-01",
                "sensor_id": "camera-01",
                "timestamp": NOW,
                "detected_type": "WORKER",
                "position": {"x": 1, "y": 2, "z": 0},
                "frame_id": "world",
                "confidence": 0.9,
                "track_id": "track-01",
                "ingested_at": NOW,
                "bbox": [10, 20, 30, 40],
                "position_source": "WORLD",
            }
        )
        self.assertEqual(observation.bbox, (10, 20, 30, 40))

        worker = Worker.model_validate(
            {
                "worker_id": "worker-01",
                "pose": POSE,
                "current_zone_id": "zone-a",
                "safety_status": "SAFE",
                "last_seen_at": NOW,
                "confidence": 0.9,
                "source_sensor_ids": ["camera-01"],
            }
        )
        self.assertEqual(worker.source_sensor_ids, ["camera-01"])

        zone = Zone.model_validate(
            {
                "zone_id": "zone-a",
                "zone_type": "WORK",
                "geometry": {"type": "polygon", "points": []},
                "frame_id": "world",
                "minimum_safety_distance": 1.2,
                "max_speed": 0.3,
            }
        )
        self.assertEqual(zone.estop_distance, 0.6)

        rule = SafetyRule.model_validate(
            {
                "rule_id": "rule-observation-gap",
                "rule_type": "OBSERVATION_GAP",
                "target_robot_type": "ALL",
                "condition": {},
                "severity": "CRITICAL",
                "parameters": {"timeout_seconds": 2},
            }
        )
        self.assertEqual(rule.on_violation, "ESTOP")

        hazard = HazardEvent.model_validate(
            {
                "hazard_type": "FIRE",
                "severity": "WARNING",
                "source_observation_ids": ["obs-01"],
                "related_robot_ids": [],
                "related_worker_ids": [],
                "zone_id": "zone-a",
                "confidence": 0.8,
                "last_observed_at": NOW,
                "resolved_by": "SENSOR_AUTO",
            }
        )
        self.assertEqual(hazard.resolved_by, "SENSOR_AUTO")

        log = ExecutionLog.model_validate(
            {
                "proposal_id": "proposal-01",
                "decision": "ESTOP",
                "rule_results": [],
                "hazard_ids": [hazard.hazard_id],
                "robot_id": "robot-01",
                "result_message": "stopped",
                "evaluated_in_ms": 12.5,
                "triggered_by": "RULE_ENGINE",
            }
        )
        self.assertEqual(log.triggered_by, "RULE_ENGINE")


class SchemaV11ApiTest(unittest.TestCase):
    def setUp(self) -> None:
        store.reset()
        seed_rules()
        self.client = TestClient(app)
        self.assertEqual(self.client.post("/robots", json=ROBOT).status_code, 201)

    def create_task(self, zone_id: str = "A구역") -> dict:
        response = self.client.post(
            "/task-orders/from-natural-language",
            json={"instruction": f"robot-01 로봇을 {zone_id}으로 이동해"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def create_proposal(self, task_id: str, speed: float) -> dict:
        response = self.client.post(
            f"/task-orders/{task_id}/action-proposals",
            json={"speed_limit": speed},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def create_hazard(
        self,
        *,
        severity: str = "CRITICAL",
        zone_id: str = "A구역",
        related_robot_ids: list[str] | None = None,
    ) -> dict:
        response = self.client.post(
            "/hazards",
            json={
                "hazard_type": "FIRE",
                "severity": severity,
                "source_observation_ids": ["obs-01"],
                "related_robot_ids": related_robot_ids or [],
                "related_worker_ids": [],
                "zone_id": zone_id,
                "confidence": 0.99,
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_default_speed_cap_rejects_direct_09_proposal(self) -> None:
        task = self.create_task()
        created = self.client.post(
            "/action-proposals",
            json={
                "task_id": task["task_id"],
                "robot_id": "robot-01",
                "action_type": "MOVE_TO",
                "parameters": {
                    "target_pose": {"type": "ZONE", "zone_id": "A구역"},
                    "speed_limit": 0.9,
                },
                "trajectory": None,
                "frame_id": "world",
                "speed_limit": 0.9,
                "model_name": "external-vla",
                "model_version": "0.1.0",
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        proposal = created.json()
        response = self.client.post(
            f"/action-proposals/{proposal['proposal_id']}/evaluate"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "REJECTED")
        speed_result = next(
            result
            for result in store.logs[-1].rule_results
            if result.rule_id == "rule-speed-limit"
        )
        self.assertIn("0.4m/s", speed_result.reason)

    def test_zone_speed_cap_is_applied(self) -> None:
        zone = {
            "zone_id": "A구역",
            "zone_type": "WORK",
            "geometry": {"type": "polygon", "points": []},
            "frame_id": "world",
            "minimum_safety_distance": 1.2,
            "max_speed": 0.2,
        }
        self.assertEqual(self.client.post("/zones", json=zone).status_code, 201)
        self.assertEqual(len(self.client.get("/zones").json()), 1)
        task = self.create_task()
        proposal = self.create_proposal(task["task_id"], 0.3)
        evaluated = self.client.post(
            f"/action-proposals/{proposal['proposal_id']}/evaluate"
        )
        self.assertEqual(evaluated.json()["status"], "REJECTED")

    def test_hazard_scope_uses_related_robot_or_destination_zone(self) -> None:
        self.create_hazard(zone_id="B구역", related_robot_ids=["robot-99"])
        task = self.create_task("A구역")
        proposal = self.create_proposal(task["task_id"], 0.4)
        evaluated = self.client.post(
            f"/action-proposals/{proposal['proposal_id']}/evaluate"
        )
        self.assertEqual(evaluated.json()["status"], "APPROVED")

    def test_empty_related_robots_blocks_matching_destination_zone(self) -> None:
        hazard = self.create_hazard(zone_id="A구역", related_robot_ids=[])
        task = self.create_task("A구역")
        proposal = self.create_proposal(task["task_id"], 0.4)
        evaluated = self.client.post(
            f"/action-proposals/{proposal['proposal_id']}/evaluate"
        )
        self.assertEqual(evaluated.json()["status"], "REJECTED")
        self.assertEqual(store.logs[-1].hazard_ids, [hazard["hazard_id"]])

    def test_execute_success_updates_task_robot_and_timing_log(self) -> None:
        task = self.create_task()
        proposal = self.create_proposal(task["task_id"], 0.4)
        self.assertEqual(
            self.client.post(
                f"/action-proposals/{proposal['proposal_id']}/evaluate"
            ).json()["status"],
            "APPROVED",
        )
        executed = self.client.post(
            f"/action-proposals/{proposal['proposal_id']}/execute"
        )
        self.assertEqual(executed.status_code, 200, executed.text)
        self.assertEqual(store.tasks[task["task_id"]].status, "RUNNING")
        self.assertEqual(store.robots["robot-01"].status, "MOVING")
        self.assertIsNotNone(store.logs[-1].evaluated_in_ms)

    def test_revalidation_failure_holds_task_and_estops_robot(self) -> None:
        task = self.create_task()
        proposal = self.create_proposal(task["task_id"], 0.4)
        self.assertEqual(
            self.client.post(
                f"/action-proposals/{proposal['proposal_id']}/evaluate"
            ).json()["status"],
            "APPROVED",
        )
        self.create_hazard(zone_id="A구역")
        response = self.client.post(
            f"/action-proposals/{proposal['proposal_id']}/execute"
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(store.proposals[proposal["proposal_id"]].status, "HELD")
        self.assertEqual(store.tasks[task["task_id"]].status, "HELD")
        self.assertEqual(store.robots["robot-01"].status, "ESTOP")
        self.assertEqual(store.logs[-1].triggered_by, "RULE_ENGINE")

    def test_critical_hazard_requires_operator_resolution(self) -> None:
        hazard = self.create_hazard()
        denied = self.client.post(
            f"/hazards/{hazard['hazard_id']}/resolve",
            json={"resolved_by": "SENSOR_AUTO"},
        )
        self.assertEqual(denied.status_code, 409)
        self.assertEqual(store.hazards[hazard["hazard_id"]].status, "OPEN")

        resolved = self.client.post(
            f"/hazards/{hazard['hazard_id']}/resolve",
            json={"resolved_by": "OPERATOR"},
        )
        self.assertEqual(resolved.status_code, 200, resolved.text)
        self.assertEqual(resolved.json()["resolved_by"], "OPERATOR")

    def test_warning_hazard_allows_sensor_auto_resolution(self) -> None:
        hazard = self.create_hazard(severity="WARNING")
        resolved = self.client.post(
            f"/hazards/{hazard['hazard_id']}/resolve",
            json={"resolved_by": "SENSOR_AUTO"},
        )
        self.assertEqual(resolved.status_code, 200, resolved.text)
        self.assertEqual(resolved.json()["status"], "RESOLVED")


if __name__ == "__main__":
    unittest.main()
