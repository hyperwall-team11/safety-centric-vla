import unittest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.main import app, seed_rules
from app.models import HazardEvent
from app.store import store


WORKER = {
    "worker_id": "worker-01",
    "pose": {
        "frame_id": "map",
        "position": {"x": 1.0, "y": 2.0, "z": 0.0},
        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
    },
    "current_zone_id": "zone-a",
    "safety_status": "SAFE",
    "last_seen_at": "2026-11-11T05:00:00Z",
    "confidence": 0.98,
    "source_sensor_ids": ["cctv-01"],
}


class StateQueryApiTest(unittest.TestCase):
    def setUp(self) -> None:
        store.reset()
        seed_rules()
        self.client = TestClient(app)

    def test_worker_snapshot_returns_404_until_first_observation(self) -> None:
        response = self.client.get("/workers/snapshot")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Worker 스냅샷을 찾을 수 없습니다.")

    def test_worker_snapshot_returns_latest_complete_snapshot(self) -> None:
        observed_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        payload = {
            "observed_at": observed_at.isoformat(),
            "workers": [WORKER],
        }
        stored = self.client.put("/workers/snapshot", json=payload)
        self.assertEqual(stored.status_code, 200, stored.text)

        response = self.client.get("/workers/snapshot")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), stored.json())

    def test_hazards_support_optional_combined_filters(self) -> None:
        hazards = [
            HazardEvent(
                hazard_id="hazard-open-critical",
                hazard_type="FIRE",
                severity="CRITICAL",
                source_observation_ids=["obs-01"],
                related_robot_ids=["robot-01"],
                related_worker_ids=[],
                zone_id="zone-a",
                status="OPEN",
                confidence=0.99,
            ),
            HazardEvent(
                hazard_id="hazard-open-warning",
                hazard_type="SMOKE",
                severity="WARNING",
                source_observation_ids=["obs-02"],
                related_robot_ids=["robot-02"],
                related_worker_ids=[],
                zone_id="zone-b",
                status="OPEN",
                confidence=0.85,
            ),
            HazardEvent(
                hazard_id="hazard-resolved-critical",
                hazard_type="COLLISION_RISK",
                severity="CRITICAL",
                source_observation_ids=["obs-03"],
                related_robot_ids=["robot-01", "robot-02"],
                related_worker_ids=[],
                zone_id="zone-a",
                status="RESOLVED",
                confidence=0.95,
            ),
        ]
        with store.lock:
            store.hazards.update({hazard.hazard_id: hazard for hazard in hazards})

        all_response = self.client.get("/hazards")
        filtered_response = self.client.get(
            "/hazards",
            params={
                "status": "OPEN",
                "severity": "CRITICAL",
                "robot_id": "robot-01",
            },
        )
        robot_response = self.client.get("/hazards", params={"robot_id": "robot-02"})

        self.assertEqual(all_response.status_code, 200)
        self.assertEqual(
            [hazard["hazard_id"] for hazard in all_response.json()],
            [hazard.hazard_id for hazard in hazards],
        )
        self.assertEqual(filtered_response.status_code, 200)
        self.assertEqual(
            [hazard["hazard_id"] for hazard in filtered_response.json()],
            ["hazard-open-critical"],
        )
        self.assertEqual(robot_response.status_code, 200)
        self.assertEqual(
            [hazard["hazard_id"] for hazard in robot_response.json()],
            ["hazard-open-warning", "hazard-resolved-critical"],
        )

    def test_hazard_filters_reject_unknown_enum_values(self) -> None:
        self.assertEqual(
            self.client.get("/hazards", params={"status": "UNKNOWN"}).status_code,
            422,
        )
        self.assertEqual(
            self.client.get("/hazards", params={"severity": "INFO"}).status_code,
            422,
        )


if __name__ == "__main__":
    unittest.main()
