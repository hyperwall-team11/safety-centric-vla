import json
import unittest
from datetime import timedelta

from fastapi.testclient import TestClient

from app.auth import TokenAuthenticator, token_sha256
from app.main import app, apply_ctl_safety_event, seed_rules
from app.models import utc_now
from app.store import store
from tests.zone_fixtures import register_work_zones


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


class HazardRecoveryFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.original_authenticator = app.state.token_authenticator
        app.state.token_authenticator = TokenAuthenticator.disabled()
        store.reset()
        seed_rules()
        self.client = TestClient(app)
        register_work_zones(self.client, "A구역", "B구역")
        response = self.client.post("/robots", json=ROBOT)
        self.assertEqual(response.status_code, 201, response.text)

    def tearDown(self) -> None:
        app.state.token_authenticator = self.original_authenticator

    def create_hazard(self) -> dict:
        response = self.client.post(
            "/hazards",
            json={
                "hazard_type": "WORKER_INTRUSION",
                "severity": "CRITICAL",
                "source_observation_ids": ["obs-enter-1", "obs-enter-2"],
                "related_robot_ids": ["robot-01"],
                "related_worker_ids": [],
                "zone_id": "B구역",
                "confidence": 1.0,
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def put_clear_observations(self, *, worker_in_zone: bool = False) -> str:
        observed_at = utc_now()
        sensor = self.client.put(
            "/sensors/cctv-1",
            json={
                "sensor_id": "cctv-1",
                "sensor_type": "CCTV",
                "source_uri": "ros:///cctv_1/image_raw",
                "frame_id": "camera_cctv_1",
                "pose": ROBOT["pose"],
                "status": "ONLINE",
                "mounted_robot_id": None,
                "covered_zone_ids": ["B구역"],
                "last_frame_at": observed_at.isoformat(),
                "calibrated": True,
            },
        )
        self.assertEqual(sensor.status_code, 200, sensor.text)
        workers = []
        if worker_in_zone:
            workers.append(
                {
                    "worker_id": "worker-1",
                    "pose": ROBOT["pose"],
                    "current_zone_id": "B구역",
                    "safety_status": "DANGER",
                    "last_seen_at": observed_at.isoformat(),
                    "confidence": 1.0,
                    "source_sensor_ids": ["cctv-1"],
                }
            )
        snapshot = self.client.put(
            "/workers/snapshot",
            json={"observed_at": observed_at.isoformat(), "workers": workers},
        )
        self.assertEqual(snapshot.status_code, 200, snapshot.text)
        return observed_at.isoformat()

    def acknowledge_clearance(self, hazard_id: str, observed_at: str, headers=None):
        return self.client.post(
            f"/hazards/{hazard_id}/clearance",
            json={
                "observed_at": observed_at,
                "source_sensor_ids": ["cctv-1"],
                "clear_duration_seconds": 2,
            },
            headers=headers,
        )

    def create_held_task(self) -> tuple[dict, dict]:
        task_response = self.client.post(
            "/task-orders/from-natural-language",
            json={"instruction": "robot-01 로봇을 A구역으로 이동해"},
        )
        self.assertEqual(task_response.status_code, 201, task_response.text)
        task = task_response.json()
        proposal_response = self.client.post(
            f"/task-orders/{task['task_id']}/action-proposals",
            json={},
        )
        self.assertEqual(proposal_response.status_code, 201, proposal_response.text)
        proposal = proposal_response.json()
        self.assertEqual(
            self.client.post(f"/action-proposals/{proposal['proposal_id']}/evaluate").status_code,
            200,
        )
        self.assertEqual(
            self.client.post(f"/action-proposals/{proposal['proposal_id']}/execute").status_code,
            200,
        )
        apply_ctl_safety_event(
            {
                "kind": "ESTOP",
                "robot_id": "robot-01",
                "proposal_id": proposal["proposal_id"],
                "triggered_by": "RULE_ENGINE",
            }
        )
        return task, proposal

    def test_cv_clearance_then_operator_resolve_then_resume(self) -> None:
        task, _ = self.create_held_task()
        hazard = self.create_hazard()

        premature = self.client.post(
            f"/hazards/{hazard['hazard_id']}/resolve",
            json={"resolved_by": "OPERATOR"},
        )
        self.assertEqual(premature.status_code, 409, premature.text)
        blocked_resume = self.client.post(
            f"/task-orders/{task['task_id']}/resume-proposal"
        )
        self.assertEqual(blocked_resume.status_code, 409, blocked_resume.text)

        observed_at = self.put_clear_observations()
        clearance = self.acknowledge_clearance(hazard["hazard_id"], observed_at)
        self.assertEqual(clearance.status_code, 200, clearance.text)
        self.assertEqual(store.hazards[hazard["hazard_id"]].status, "ACKNOWLEDGED")

        still_blocked = self.client.post(
            f"/task-orders/{task['task_id']}/resume-proposal"
        )
        self.assertEqual(still_blocked.status_code, 409, still_blocked.text)
        resolved = self.client.post(
            f"/hazards/{hazard['hazard_id']}/resolve",
            json={"resolved_by": "OPERATOR"},
        )
        self.assertEqual(resolved.status_code, 200, resolved.text)

        with store.lock:
            store.operator_estop_active = True
        manual_stop_blocked = self.client.post(
            f"/task-orders/{task['task_id']}/resume-proposal"
        )
        self.assertEqual(manual_stop_blocked.status_code, 409, manual_stop_blocked.text)
        self.assertIn("E-Stop", manual_stop_blocked.json()["detail"])
        with store.lock:
            store.operator_estop_active = False

        resume = self.client.post(
            f"/task-orders/{task['task_id']}/resume-proposal"
        )
        self.assertEqual(resume.status_code, 201, resume.text)
        self.assertEqual(resume.json()["action_type"], "RESUME")
        self.assertEqual(resume.json()["parameters"]["priority"], "SAFETY")
        proposal_id = resume.json()["proposal_id"]
        evaluated = self.client.post(f"/action-proposals/{proposal_id}/evaluate")
        self.assertEqual(evaluated.json()["status"], "APPROVED", evaluated.text)
        executed = self.client.post(f"/action-proposals/{proposal_id}/execute")
        self.assertEqual(executed.json()["status"], "EXECUTING", executed.text)
        result = self.client.post(
            f"/action-proposals/{proposal_id}/result",
            json={"status": "SUCCEEDED", "detail": "IDLE 복귀"},
        )
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(store.robots["robot-01"].status, "IDLE")
        self.assertEqual(store.tasks[task["task_id"]].status, "PLANNING")

        # 과거 HELD 제안은 복구 이후 다음 단계 계획을 막지 않는다.
        next_proposal = self.client.post(
            f"/task-orders/{task['task_id']}/action-proposals",
            json={},
        )
        self.assertEqual(next_proposal.status_code, 201, next_proposal.text)
        self.assertEqual(next_proposal.json()["action_type"], "MOVE_TO")

    def test_operator_estop_reactivation_rejects_existing_resume_proposal(self) -> None:
        task, _ = self.create_held_task()
        resume = self.client.post(
            f"/task-orders/{task['task_id']}/resume-proposal"
        )
        self.assertEqual(resume.status_code, 201, resume.text)
        with store.lock:
            store.operator_estop_active = True

        evaluated = self.client.post(
            f"/action-proposals/{resume.json()['proposal_id']}/evaluate"
        )

        self.assertEqual(evaluated.status_code, 200, evaluated.text)
        self.assertEqual(evaluated.json()["status"], "REJECTED")
        self.assertEqual(store.tasks[task["task_id"]].status, "HELD")

    def test_clearance_fails_closed_when_worker_remains(self) -> None:
        hazard = self.create_hazard()
        observed_at = self.put_clear_observations(worker_in_zone=True)

        response = self.acknowledge_clearance(hazard["hazard_id"], observed_at)

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(store.hazards[hazard["hazard_id"]].status, "OPEN")

    def test_clearance_rejects_stale_observation(self) -> None:
        hazard = self.create_hazard()
        self.put_clear_observations()

        response = self.acknowledge_clearance(
            hazard["hazard_id"],
            (utc_now() - timedelta(seconds=3)).isoformat(),
        )

        self.assertEqual(response.status_code, 409, response.text)

    def test_roles_separate_sensor_operator_and_viewer(self) -> None:
        hazard = self.create_hazard()
        observed_at = self.put_clear_observations()
        tokens = {
            "sensor-token": ("cv-node", "SENSOR"),
            "operator-token": ("manager", "OPERATOR"),
            "viewer-token": ("viewer", "VIEWER"),
        }
        app.state.token_authenticator = TokenAuthenticator.from_json(
            json.dumps(
                [
                    {
                        "token_sha256": token_sha256(token),
                        "subject": subject,
                        "role": role,
                    }
                    for token, (subject, role) in tokens.items()
                ]
            )
        )
        header = lambda token: {"Authorization": f"Bearer {token}"}

        denied_clearance = self.acknowledge_clearance(
            hazard["hazard_id"], observed_at, header("viewer-token")
        )
        self.assertEqual(denied_clearance.status_code, 403, denied_clearance.text)
        clearance = self.acknowledge_clearance(
            hazard["hazard_id"], observed_at, header("sensor-token")
        )
        self.assertEqual(clearance.status_code, 200, clearance.text)
        denied_resolve = self.client.post(
            f"/hazards/{hazard['hazard_id']}/resolve",
            json={"resolved_by": "OPERATOR"},
            headers=header("sensor-token"),
        )
        self.assertEqual(denied_resolve.status_code, 403, denied_resolve.text)
        resolved = self.client.post(
            f"/hazards/{hazard['hazard_id']}/resolve",
            json={"resolved_by": "OPERATOR"},
            headers=header("operator-token"),
        )
        self.assertEqual(resolved.status_code, 200, resolved.text)


if __name__ == "__main__":
    unittest.main()
