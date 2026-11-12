from datetime import datetime, timezone
import unittest

from fastapi.testclient import TestClient

from app.main import OntologyObjectType, app, seed_rules
from app.store import store


POSE = {
    "frame_id": "map",
    "position": {"x": 0, "y": 0, "z": 0},
    "orientation": {"x": 0, "y": 0, "z": 0, "w": 1},
}


class ObjectQueryApiTest(unittest.TestCase):
    def setUp(self) -> None:
        store.reset()
        seed_rules()
        self.client = TestClient(app)

    def register_objects(self) -> dict[str, str]:
        now = datetime.now(timezone.utc).isoformat()
        robot = self.client.post(
            "/robots",
            json={
                "robot_id": "robot-01",
                "robot_type": "MOBILE",
                "control_adapter": "mock-mobile-adapter",
                "status": "IDLE",
                "pose": POSE,
                "velocity": {"x": 0, "y": 0, "z": 0},
                "motion_limits": {"max_speed": 1.0, "max_acceleration": 0.5},
            },
        )
        self.assertEqual(robot.status_code, 201, robot.text)

        zone = self.client.post(
            "/zones",
            json={
                "zone_id": "A구역",
                "zone_type": "WORK",
                "geometry": {"type": "polygon", "points": [[0, 0], [1, 0], [1, 1]]},
                "frame_id": "map",
                "minimum_safety_distance": 0,
            },
        )
        self.assertEqual(zone.status_code, 201, zone.text)

        sensor = self.client.put(
            "/sensors/sensor-01",
            json={
                "sensor_id": "sensor-01",
                "sensor_type": "CCTV",
                "source_uri": "rtsp://sensor-01",
                "frame_id": "camera_sensor_01",
                "pose": POSE,
                "status": "ONLINE",
                "mounted_robot_id": None,
                "covered_zone_ids": ["A구역"],
                "last_frame_at": now,
                "calibrated": True,
            },
        )
        self.assertEqual(sensor.status_code, 200, sensor.text)

        workers = self.client.put(
            "/workers/snapshot",
            json={
                "observed_at": now,
                "workers": [
                    {
                        "worker_id": "worker-01",
                        "pose": {
                            **POSE,
                            "position": {"x": 10, "y": 10, "z": 0},
                        },
                        "current_zone_id": "A구역",
                        "safety_status": "SAFE",
                        "last_seen_at": now,
                        "confidence": 1,
                        "source_sensor_ids": ["sensor-01"],
                    }
                ],
            },
        )
        self.assertEqual(workers.status_code, 200, workers.text)

        task = self.client.post(
            "/task-orders/from-natural-language",
            json={"instruction": "robot-01 로봇을 A구역으로 이동해"},
        )
        self.assertEqual(task.status_code, 201, task.text)
        task_id = task.json()["task_id"]

        proposal = self.client.post(
            f"/task-orders/{task_id}/action-proposals",
            json={"speed_limit": 0.4},
        )
        self.assertEqual(proposal.status_code, 201, proposal.text)
        proposal_id = proposal.json()["proposal_id"]

        evaluated = self.client.post(f"/action-proposals/{proposal_id}/evaluate")
        self.assertEqual(evaluated.status_code, 200, evaluated.text)

        hazard = self.client.post(
            "/hazards",
            json={
                "hazard_type": "FIRE",
                "severity": "WARNING",
                "source_observation_ids": ["observation-01"],
                "related_robot_ids": ["robot-01"],
                "related_worker_ids": [],
                "zone_id": "A구역",
                "confidence": 0.99,
            },
        )
        self.assertEqual(hazard.status_code, 201, hazard.text)

        return {
            "TaskOrder": task_id,
            "ActionProposal": proposal_id,
            "HazardEvent": hazard.json()["hazard_id"],
        }

    def test_queries_every_supported_stored_object_type(self) -> None:
        generated_ids = self.register_objects()
        expected_identifiers = {
            "Robot": ("robot_id", "robot-01"),
            "Sensor": ("sensor_id", "sensor-01"),
            "Worker": ("worker_id", "worker-01"),
            "Zone": ("zone_id", "A구역"),
            "TaskOrder": ("task_id", generated_ids["TaskOrder"]),
            "ActionProposal": ("proposal_id", generated_ids["ActionProposal"]),
            "HazardEvent": ("hazard_id", generated_ids["HazardEvent"]),
        }

        for object_type, (id_field, expected_id) in expected_identifiers.items():
            with self.subTest(object_type=object_type):
                response = self.client.get(f"/api/v1/objects/{object_type}")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(len(response.json()), 1)
                self.assertEqual(response.json()[0][id_field], expected_id)

        rules = self.client.get("/api/v1/objects/SafetyRule")
        logs = self.client.get("/api/v1/objects/ExecutionLog")
        self.assertEqual(rules.status_code, 200, rules.text)
        self.assertGreater(len(rules.json()), 0)
        self.assertEqual(logs.status_code, 200, logs.text)
        self.assertEqual(len(logs.json()), 1)
        self.assertEqual(logs.json()[0]["proposal_id"], generated_ids["ActionProposal"])

    def test_supported_type_without_objects_returns_empty_list(self) -> None:
        response = self.client.get("/api/v1/objects/Worker")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), [])

    def test_unknown_or_wrong_case_type_is_rejected(self) -> None:
        for object_type in ("Observation", "robot", "Unknown"):
            with self.subTest(object_type=object_type):
                response = self.client.get(f"/api/v1/objects/{object_type}")
                self.assertEqual(response.status_code, 422, response.text)

    def test_openapi_lists_supported_object_types(self) -> None:
        operation = app.openapi()["paths"]["/api/v1/objects/{type}"]["get"]
        parameter_schema = operation["parameters"][0]["schema"]
        schema_name = parameter_schema["$ref"].split("/")[-1]
        enum_schema = app.openapi()["components"]["schemas"][schema_name]

        self.assertEqual(operation["parameters"][0]["name"], "type")
        self.assertEqual(
            set(enum_schema["enum"]),
            {object_type.value for object_type in OntologyObjectType},
        )


if __name__ == "__main__":
    unittest.main()
