"""운영자 전역 E-Stop API와 Fail-Closed 상태 전이 검증."""

import json
import unittest

from fastapi.testclient import TestClient

from app import main
from app.auth import TokenAuthenticator, token_sha256
from app.ctl_bridge import DispatchError, MockActionDispatcher
from app.main import app, seed_rules
from app.store import store
from tests.zone_fixtures import register_work_zones


class OperatorEstopTests(unittest.TestCase):
    def setUp(self) -> None:
        store.reset()
        seed_rules()
        self.original_dispatcher = main.action_dispatcher
        self.original_authenticator = app.state.token_authenticator
        self.dispatcher = MockActionDispatcher()
        main.action_dispatcher = self.dispatcher
        app.state.token_authenticator = TokenAuthenticator.disabled()
        self.client = TestClient(app)
        register_work_zones(self.client, "A구역", "B구역")
        self._post("/robots", {
            "robot_id": "robot-01",
            "robot_type": "MOBILE",
            "control_adapter": "mock-mobile-adapter",
            "status": "IDLE",
            "pose": {"frame_id": "map", "position": {"x": 0, "y": 0, "z": 0}},
            "velocity": {"x": 0, "y": 0, "z": 0},
            "motion_limits": {"max_speed": 1.0, "max_acceleration": 0.5},
        })

    def tearDown(self) -> None:
        main.action_dispatcher = self.original_dispatcher
        app.state.token_authenticator = self.original_authenticator

    def _post(self, path: str, body=None, expected: int = 201, headers=None):
        response = self.client.post(path, json=body, headers=headers)
        self.assertEqual(response.status_code, expected, response.text)
        return response.json()

    def execute(self):
        task = self._post(
            "/task-orders/from-natural-language",
            {"instruction": "robot-01 로봇을 A구역으로 이동해"},
        )
        proposal = self._post("/action-proposals", {
            "task_id": task["task_id"],
            "robot_id": "robot-01",
            "action_type": "MOVE_TO",
            "parameters": {
                "target_pose": {"type": "ZONE", "zone_id": "A구역"},
                "speed_limit": 0.3,
            },
            "trajectory": None,
            "frame_id": "map",
            "speed_limit": 0.3,
            "model_name": "mock-vla",
            "model_version": "test-0.1",
        })
        self._post(
            f"/action-proposals/{proposal['proposal_id']}/evaluate",
            expected=200,
        )
        return self._post(
            f"/action-proposals/{proposal['proposal_id']}/execute",
            expected=200,
        )

    def test_operator_estop_holds_execution_and_publishes_global_stop(self):
        proposal = self.execute()

        response = self._post(
            "/safety/estop",
            {"reason": "작업자가 로봇 경로에 진입"},
            expected=200,
        )

        self.assertTrue(response["active"])
        self.assertTrue(response["operator_active"])
        self.assertEqual(response["held_robot_ids"], ["robot-01"])
        self.assertEqual(store.robots["robot-01"].status, "ESTOP")
        self.assertEqual(store.proposals[proposal["proposal_id"]].status, "HELD")
        self.assertEqual(store.tasks[proposal["task_id"]].status, "HELD")
        self.assertEqual(self.dispatcher.estop_commands[-1]["active"], True)
        self.assertEqual(self.dispatcher.estop_commands[-1]["triggered_by"], "OPERATOR")
        self.assertEqual(store.logs[-1].triggered_by, "OPERATOR")

    def test_repeated_estop_is_idempotent_for_audit_log(self):
        self.execute()
        self._post("/safety/estop", {"reason": "첫 정지"}, expected=200)
        first_log_count = len(store.logs)

        self._post("/safety/estop", {"reason": "재확인"}, expected=200)

        self.assertEqual(len(store.logs), first_log_count)
        self.assertEqual(store.operator_estop_reason, "재확인")
        self.assertEqual([item["active"] for item in self.dispatcher.estop_commands[-2:]], [True, True])

    def test_release_drops_latch_but_does_not_resume_robot(self):
        self._post("/safety/estop", {"reason": "점검"}, expected=200)

        response = self._post("/safety/estop/release", expected=200)

        self.assertFalse(response["active"])
        self.assertFalse(response["operator_active"])
        self.assertEqual(store.robots["robot-01"].status, "ESTOP")
        self.assertEqual(self.dispatcher.estop_commands[-1]["active"], False)

    def test_hazard_keeps_global_stop_active_after_operator_release(self):
        self._post("/safety/estop", {"reason": "점검"}, expected=200)
        with store.lock:
            store.estop_hazard_ids.add("hazard-1")

        response = self._post("/safety/estop/release", expected=200)

        self.assertTrue(response["active"])
        self.assertFalse(response["operator_active"])
        self.assertEqual(response["hazard_ids"], ["hazard-1"])
        self.assertEqual(self.dispatcher.estop_commands[-1]["active"], True)

    def test_authenticated_non_operator_cannot_control_estop(self):
        operator_token = "operator-token"
        viewer_token = "viewer-token"
        app.state.token_authenticator = TokenAuthenticator.from_json(json.dumps([
            {
                "token_sha256": token_sha256(operator_token),
                "subject": "operator-1",
                "role": "OPERATOR",
            },
            {
                "token_sha256": token_sha256(viewer_token),
                "subject": "viewer-1",
                "role": "VIEWER",
            },
        ]))

        denied = self.client.post(
            "/safety/estop",
            json={"reason": "권한 없음"},
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        allowed = self.client.post(
            "/safety/estop",
            json={"reason": "운영자 정지"},
            headers={"Authorization": f"Bearer {operator_token}"},
        )

        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(allowed.status_code, 200, allowed.text)
        self.assertEqual(allowed.json()["requested_by"], "operator-1")

    def test_dispatch_failure_returns_503_but_keeps_fail_closed_state(self):
        class FailingDispatcher(MockActionDispatcher):
            def publish_estop(self, active, triggered_by="RULE_ENGINE"):
                raise DispatchError("ROS 2 unavailable")

        main.action_dispatcher = FailingDispatcher()

        response = self.client.post("/safety/estop", json={"reason": "긴급"})

        self.assertEqual(response.status_code, 503, response.text)
        self.assertTrue(store.operator_estop_active)
        self.assertEqual(store.robots["robot-01"].status, "ESTOP")


if __name__ == "__main__":
    unittest.main()
