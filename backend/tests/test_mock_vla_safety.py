
"""Mock VLA ActionProposal 안전 규칙 테스트.

실행 위치: backend/
실행 명령:
    python3 -m pytest -v tests/test_mock_vla_safety.py

KEEP_OUT 관련 테스트는 현재 미구현 기능을 드러내기 위해
expectedFailure로 표시함.
"""

import unittest

from fastapi.testclient import TestClient

from app.main import app, seed_rules
from app.store import store


class MockVlaSafetyTests(unittest.TestCase):

    def setUp(self):
        store.reset()
        seed_rules()
        self.client = TestClient(app)
        self.robot("robot-01", max_speed=1.0)

    def post(self, path, payload):
        response = self.client.post(path, json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def robot(self, robot_id, max_speed):
        return self.post("/robots", {
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
                "max_speed": max_speed,
                "max_acceleration": 0.5,
            },
        })

    def zone(self, zone_id, zone_type, max_speed=None):
        # 테스트용 사각형 영역:
        # x = 1~2, y = -1~1
        # geometry 표현 방식은 최종 표준 확정 전 예시임.
        return self.post("/zones", {
            "zone_id": zone_id,
            "zone_type": zone_type,
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [1, -1], [2, -1], [2, 1],
                    [1, 1], [1, -1]
                ]],
            },
            "frame_id": "map",
            "minimum_safety_distance": 1.2,
            "max_speed": max_speed,
        })

    def propose(
        self,
        speed,
        *,
        robot_id="robot-01",
        destination="A구역",
        trajectory=None,
    ):
        # TaskOrder 생성
        task = self.post(
            "/task-orders/from-natural-language",
            {
                "instruction":
                    f"{robot_id} 로봇을 {destination}으로 이동해"
            },
        )

        # 실제 VLA 대신 Mock ActionProposal 등록
        proposal = self.post("/action-proposals", {
            "task_id": task["task_id"],
            "robot_id": robot_id,
            "action_type": "MOVE_TO",
            "parameters": {
                "target_pose": {
                    "type": "ZONE",
                    "zone_id": destination,
                },
                "speed_limit": speed,
            },
            "trajectory": trajectory,
            "frame_id": "map",
            "speed_limit": speed,
            "model_name": "mock-vla",
            "model_version": "test-0.1",
        })

        # 안전 규칙 평가
        result = self.client.post(
            f"/action-proposals/"
            f"{proposal['proposal_id']}/evaluate"
        )
        self.assertEqual(result.status_code, 200, result.text)

        # 실행 로그 확인
        logs = self.client.get("/execution-logs")
        self.assertEqual(logs.status_code, 200, logs.text)
        log = logs.json()[-1]

        self.assertEqual(
            log["proposal_id"],
            proposal["proposal_id"],
        )

        return result.json(), log

    def speed_rule(self, log):
        return next(
            r for r in log["rule_results"]
            if r["rule_id"] == "rule-speed-limit"
        )

    # 01. 정상 속도 승인
    def test_01_safe_speed_approved(self):
        result, log = self.propose(0.3)

        self.assertEqual(result["status"], "APPROVED")
        self.assertTrue(self.speed_rule(log)["passed"])

    # 02. 기본 속도 제한 초과
    def test_02_default_speed_cap_rejected(self):
        # 기존 backend 기본 제한: 0.4 m/s
        result, log = self.propose(0.5)

        self.assertEqual(result["status"], "REJECTED")
        self.assertFalse(self.speed_rule(log)["passed"])
        self.assertEqual(log["decision"], "REJECT")

    # 03. 로봇별 속도 제한 초과
    def test_03_robot_speed_cap_rejected(self):
        self.robot("robot-02", max_speed=0.2)

        result, log = self.propose(
            0.3,
            robot_id="robot-02",
        )

        self.assertEqual(result["status"], "REJECTED")
        self.assertFalse(self.speed_rule(log)["passed"])

    # 04. Zone별 속도 제한 초과
    def test_04_zone_speed_cap_rejected(self):
        self.zone(
            "A구역",
            "WORK",
            max_speed=0.2,
        )

        result, log = self.propose(0.3)

        self.assertEqual(result["status"], "REJECTED")
        self.assertFalse(self.speed_rule(log)["passed"])

    # 05. Zone 속도 제한 경계값
    def test_05_zone_speed_cap_boundary_approved(self):
        self.zone(
            "A구역",
            "WORK",
            max_speed=0.2,
        )

        result, log = self.propose(0.2)

        self.assertEqual(result["status"], "APPROVED")
        self.assertTrue(self.speed_rule(log)["passed"])

    # 06. KEEP_OUT Zone을 목적지로 지정
    # 현재 미구현이므로 예상 실패로 기록
    # @unittest.expectedFailure
    def test_06_keep_out_destination_must_be_rejected(self):
        self.zone("B구역", "KEEP_OUT")

        result, _ = self.propose(
            0.3,
            destination="B구역",
        )

        self.assertEqual(result["status"], "REJECTED")

    # 07. 경로가 KEEP_OUT Zone을 통과
    # 현재 미구현이므로 예상 실패로 기록
    # @unittest.expectedFailure
    def test_07_trajectory_crossing_keep_out_must_be_rejected(self):
        self.zone("B구역", "KEEP_OUT")

        # (0,0) -> (3,0) 직선 경로
        # 중간에 금지구역 x=1~2를 통과함.
        # trajectory 내부 표현은 검증용 예시임.
        trajectory = [
            {
                "position": {
                    "x": 0, "y": 0, "z": 0
                }
            },
            {
                "position": {
                    "x": 3, "y": 0, "z": 0
                }
            },
        ]

        result, _ = self.propose(
            0.3,
            trajectory=trajectory,
        )

        self.assertEqual(result["status"], "REJECTED")


if __name__ == "__main__":
    unittest.main()
