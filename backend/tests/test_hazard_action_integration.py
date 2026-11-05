
"""HazardEvent + Mock VLA 통합 안전 판정 테스트.

실행:
    cd backend
    python3 -m pytest -v tests/test_hazard_action_integration.py

KEEP_OUT 규칙은 이번 테스트 범위에서 제외.
"""

import unittest

from fastapi.testclient import TestClient

from app.main import app, seed_rules
from app.store import store


class HazardActionIntegrationTests(unittest.TestCase):

    def setUp(self):
        store.reset()
        seed_rules()
        self.client = TestClient(app)

        for robot_id in ("robot-01", "robot-02"):
            self._create("/robots", {
                "robot_id": robot_id,
                "robot_type": "MOBILE",
                "control_adapter": "mock-mobile-adapter",
                "status": "IDLE",
                "pose": {
                    "frame_id": "map",
                    "position": {"x": 0, "y": 0, "z": 0},
                    "orientation": {
                        "x": 0, "y": 0, "z": 0, "w": 1
                    },
                },
                "velocity": {"x": 0, "y": 0, "z": 0},
                "motion_limits": {
                    "max_speed": 1.0,
                    "max_acceleration": 0.5,
                },
            })

    def _create(self, path, body):
        response = self.client.post(path, json=body)
        self.assertEqual(
            response.status_code, 201, response.text
        )
        return response.json()

    # 가상의 위험 이벤트 등록
    def add_hazard(
        self,
        *,
        severity="CRITICAL",
        robots=None,
        zone="B구역",
        hazard_type="FIRE",
    ):
        return self._create("/hazards", {
            "hazard_type": hazard_type,
            "severity": severity,
            "source_observation_ids": ["obs-mock-01"],
            "related_robot_ids": (
                robots if robots is not None else []
            ),
            "related_worker_ids": [],
            "zone_id": zone,
            "confidence": 0.99,
        })

    # 실제 VLA 대신 ActionProposal 직접 생성
    def mock_vla_proposal(
        self,
        *,
        robot_id="robot-01",
        zone="A구역",
        speed=0.3,
    ):
        task = self._create(
            "/task-orders/from-natural-language",
            {
                "instruction":
                    f"{robot_id} 로봇을 {zone}으로 이동해"
            },
        )

        proposal = self._create("/action-proposals", {
            "task_id": task["task_id"],
            "robot_id": robot_id,
            "action_type": "MOVE_TO",
            "parameters": {
                "target_pose": {
                    "type": "ZONE",
                    "zone_id": zone,
                },
                "speed_limit": speed,
            },
            "trajectory": None,
            "frame_id": "map",
            "speed_limit": speed,
            "model_name": "mock-vla",
            "model_version": "test-0.1",
        })

        self.assertEqual(proposal["status"], "PROPOSED")
        return task, proposal

    # 판정 결과 + 두 안전 규칙 + 로그 함께 검증
    def evaluate(
        self,
        proposal,
        *,
        status,
        speed_pass,
        hazard_pass,
        hazard_ids=(),
    ):
        response = self.client.post(
            f"/action-proposals/"
            f"{proposal['proposal_id']}/evaluate"
        )
        self.assertEqual(
            response.status_code, 200, response.text
        )

        result = response.json()
        self.assertEqual(result["status"], status)

        logs_response = self.client.get("/execution-logs")
        self.assertEqual(
            logs_response.status_code, 200,
            logs_response.text
        )

        log = logs_response.json()[-1]

        self.assertEqual(
            log["proposal_id"],
            proposal["proposal_id"],
        )

        expected_decision = (
            "APPROVE" if status == "APPROVED"
            else "REJECT"
        )
        self.assertEqual(
            log["decision"], expected_decision
        )

        rules = {
            r["rule_id"]: r
            for r in log["rule_results"]
        }

        self.assertEqual(
            rules["rule-speed-limit"]["passed"],
            speed_pass,
        )
        self.assertEqual(
            rules["rule-critical-hazard"]["passed"],
            hazard_pass,
        )

        self.assertEqual(
            set(log["hazard_ids"]),
            set(hazard_ids),
        )

        # 같은 HazardEvent가 중복 기록되지 않아야 함
        self.assertEqual(
            len(log["hazard_ids"]),
            len(set(log["hazard_ids"])),
        )

        return result, log

    # 01: 위험 없고 안전한 행동
    def test_01_safe_proposal_without_hazard_approved(self):
        _, p = self.mock_vla_proposal(speed=0.3)

        self.evaluate(
            p,
            status="APPROVED",
            speed_pass=True,
            hazard_pass=True,
        )

    # 02: 속도는 안전하지만 관련 CRITICAL 위험 존재
    def test_02_safe_speed_but_critical_hazard_rejected(self):
        h = self.add_hazard(robots=["robot-01"])
        _, p = self.mock_vla_proposal(speed=0.3)

        self.evaluate(
            p,
            status="REJECTED",
            speed_pass=True,
            hazard_pass=False,
            hazard_ids=[h["hazard_id"]],
        )

    # 03: 다른 로봇의 CRITICAL 위험은 차단하지 않음
    def test_03_unrelated_critical_hazard_does_not_block(self):
        self.add_hazard(
            robots=["robot-02"],
            zone="B구역",
        )

        _, p = self.mock_vla_proposal(
            robot_id="robot-01",
            zone="A구역",
        )

        self.evaluate(
            p,
            status="APPROVED",
            speed_pass=True,
            hazard_pass=True,
        )

    # 04: 목적지 Zone과 관련된 CRITICAL 위험
    def test_04_destination_zone_hazard_rejected(self):
        h = self.add_hazard(
            robots=[],
            zone="A구역",
        )

        _, p = self.mock_vla_proposal(
            robot_id="robot-02",
            zone="A구역",
        )

        self.evaluate(
            p,
            status="REJECTED",
            speed_pass=True,
            hazard_pass=False,
            hazard_ids=[h["hazard_id"]],
        )

    # 05: WARNING은 현재 규칙상 차단하지 않음
    def test_05_warning_with_safe_speed_approved(self):
        self.add_hazard(
            severity="WARNING",
            robots=["robot-01"],
            zone="A구역",
        )

        _, p = self.mock_vla_proposal(speed=0.3)

        self.evaluate(
            p,
            status="APPROVED",
            speed_pass=True,
            hazard_pass=True,
        )

    # 06: Hazard가 없어도 속도 위반이면 거부
    def test_06_fast_proposal_without_hazard_rejected(self):
        _, p = self.mock_vla_proposal(speed=0.5)

        self.evaluate(
            p,
            status="REJECTED",
            speed_pass=False,
            hazard_pass=True,
        )

    # 07: 속도 위반 + CRITICAL 위험 동시 존재
    def test_07_speed_and_critical_hazard_both_rejected(self):
        h = self.add_hazard(robots=["robot-01"])
        _, p = self.mock_vla_proposal(speed=0.5)

        self.evaluate(
            p,
            status="REJECTED",
            speed_pass=False,
            hazard_pass=False,
            hazard_ids=[h["hazard_id"]],
        )

    # 08: WARNING이 속도 위반을 무효화하지 않음
    def test_08_warning_does_not_override_speed_violation(self):
        self.add_hazard(
            severity="WARNING",
            robots=["robot-01"],
            zone="A구역",
        )

        _, p = self.mock_vla_proposal(speed=0.5)

        self.evaluate(
            p,
            status="REJECTED",
            speed_pass=False,
            hazard_pass=True,
        )

    # 09: Hazard 해제 후에도 과속은 거부
    def test_09_resolved_hazard_still_rejects_overspeed(self):
        h = self.add_hazard(robots=["robot-01"])

        r = self.client.post(
            f"/hazards/{h['hazard_id']}/resolve",
            json={"resolved_by": "OPERATOR"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "RESOLVED")

        _, p = self.mock_vla_proposal(speed=0.5)

        self.evaluate(
            p,
            status="REJECTED",
            speed_pass=False,
            hazard_pass=True,
        )

    # 10: 위험 해제 후 기존 REJECTED 제안 유지
    #     새로 제출한 안전 제안만 APPROVED
    def test_10_rejected_stays_rejected_after_resolution(self):
        h = self.add_hazard(robots=["robot-01"])

        first_task, first = self.mock_vla_proposal(
            speed=0.3
        )

        self.evaluate(
            first,
            status="REJECTED",
            speed_pass=True,
            hazard_pass=False,
            hazard_ids=[h["hazard_id"]],
        )

        r = self.client.post(
            f"/hazards/{h['hazard_id']}/resolve",
            json={"resolved_by": "OPERATOR"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "RESOLVED")

        old_list = self.client.get(
            f"/task-orders/{first_task['task_id']}"
            "/action-proposals"
        )
        self.assertEqual(
            old_list.status_code, 200, old_list.text
        )

        old_status = next(
            x["status"]
            for x in old_list.json()
            if x["proposal_id"] == first["proposal_id"]
        )
        self.assertEqual(old_status, "REJECTED")

        _, second = self.mock_vla_proposal(speed=0.3)
        self.assertNotEqual(
            first["proposal_id"],
            second["proposal_id"],
        )

        self.evaluate(
            second,
            status="APPROVED",
            speed_pass=True,
            hazard_pass=True,
        )

    # 11: 여러 위험이 동시에 발생한 경우
    def test_11_multiple_critical_hazards_recorded(self):
        h1 = self.add_hazard(
            robots=["robot-01"],
            zone="B구역",
        )
        h2 = self.add_hazard(
            robots=[],
            zone="A구역",
            hazard_type="SMOKE",
        )

        _, p = self.mock_vla_proposal(speed=0.3)

        self.evaluate(
            p,
            status="REJECTED",
            speed_pass=True,
            hazard_pass=False,
            hazard_ids=[
                h1["hazard_id"],
                h2["hazard_id"],
            ],
        )

    # 12: 승인 후 위험이 발생하면 실행 전 재검증
    def test_12_hazard_after_approval_blocks_execution(self):
        _, p = self.mock_vla_proposal(speed=0.3)

        self.evaluate(
            p,
            status="APPROVED",
            speed_pass=True,
            hazard_pass=True,
        )

        h = self.add_hazard(robots=["robot-01"])

        response = self.client.post(
            f"/action-proposals/"
            f"{p['proposal_id']}/execute"
        )
        self.assertEqual(
            response.status_code, 409, response.text
        )

        logs = self.client.get("/execution-logs")
        self.assertEqual(logs.status_code, 200, logs.text)

        log = logs.json()[-1]
        self.assertEqual(log["decision"], "ESTOP")
        self.assertIn(
            h["hazard_id"],
            log["hazard_ids"],
        )


if __name__ == "__main__":
    unittest.main()
