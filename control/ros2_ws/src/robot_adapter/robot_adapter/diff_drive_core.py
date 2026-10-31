"""차동 구동 모바일 로봇(TIAGo) 어댑터 로직.

Mock 어댑터와 같은 규약(APPROVED만 실행, ACK·RESULT, E-Stop·HOLD, 2단계 복귀)을 그대로 쓰고,
위치는 /odom에서 받고 이동은 (선속도, 각속도) 명령으로 낸다. rclpy에 의존하지 않는다.

MOVE_TO 제어기 (CT-07 경로 추종의 첫 단계):
- 목표 방향과 50°(HEADING_TOLERANCE) 이상 어긋나 있으면 제자리 회전
- 그 안이면 목표를 향해 전진하면서 방향 오차에 비례해 회전
- 목표 0.05 m 안에 들어오면 정지하고 성공 보고
- 직선 경로만 따라가므로 장애물 회피는 하지 않는다 (경로는 SR-02 검증 서버가 확인)

MANIPULATE (PICK·PLACE): 브리지의 /arm/command로 넘기고 /arm/result를 RESULT로 바꿔 보고한다.
정지(E-Stop·HOLD·STOP·TIMEOUT)되면 CANCEL을 보내 브리지가 팔·베이스를 그 자리에서 멈추게 한다.
"""
from __future__ import annotations

import math

from .core import (HW_ERROR, INVALID, NO_HEARTBEAT, ROBOT_BUSY, ROBOT_HELD, TIMEOUT, UNSUPPORTED,
                   MockRobotCore)

ARRIVAL_TOLERANCE_M = 0.05
HEADING_TOLERANCE_RAD = math.radians(50)
K_ANGULAR = 2.0          # 방향 오차(rad) → 각속도(rad/s)
K_LINEAR = 1.0           # 남은 거리(m) → 선속도(m/s), 도착 직전 감속
MAX_ANGULAR = 0.8        # rad/s
ODOM_TIMEOUT_S = 0.5     # odom이 이보다 오래 끊기면 이동 거부·정지
TIMEOUT_MARGIN_S = 10.0  # 예상 이동 시간의 2배 + 여유
MANIPULATE_TIMEOUT_S = 90.0  # 집기·놓기 한 번 (베이스 이동 포함) 제한 시간


def wrap_angle(a: float) -> float:
    return math.atan2(math.sin(a), math.cos(a))


def yaw_from_quat(x, y, z, w) -> float:
    return math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))


class DiffDriveCore(MockRobotCore):
    adapter_name = "mujoco_diff_drive"
    supported = {"MOVE_TO", "MANIPULATE", "STOP", "RESUME"}

    def __init__(self, robot_id, robot_type="MOBILE", frame_id="world", max_speed=0.4,
                 max_angular=MAX_ANGULAR):
        super().__init__(robot_id, robot_type=robot_type, frame_id=frame_id, max_speed=max_speed)
        self.max_angular = max_angular
        self.yaw = 0.0
        self.angular_velocity = 0.0
        self.odom_at = None
        self.command = (0.0, 0.0)  # 노드가 /cmd_vel로 내보낼 (선속도, 각속도)
        self.deadline = None
        self.mode = None            # "move" | "manipulate"
        self.arm_outbox = []        # 노드가 /arm/command로 내보낼 명령

    # ---- odom 입력 ----
    def update_odom(self, x, y, yaw, v, w, now):
        self.position = [x, y, 0.0]
        self.yaw, self.velocity, self.angular_velocity = yaw, v, w
        self.odom_at = now

    def odom_lost(self, now) -> bool:
        return self.odom_at is None or now - self.odom_at > ODOM_TIMEOUT_S

    def orientation(self):
        return [0.0, 0.0, math.sin(self.yaw / 2), math.cos(self.yaw / 2)]

    # ---- Action ----
    def _on_move_to(self, msg, now):
        if self.odom_lost(now):
            return [self._result(msg.get("proposal_id"), False, HW_ERROR, "로봇 위치(/odom) 수신 없음", now)]
        events = super()._on_move_to(msg, now)
        if self.status == "MOVING" and self.current is msg:
            self.mode = "move"
            dist = math.hypot(self.target[0] - self.position[0], self.target[1] - self.position[1])
            turn = math.pi / self.max_angular
            self.deadline = now + 2 * (dist / self.speed + turn) + TIMEOUT_MARGIN_S
        return events

    def _on_manipulate(self, msg, now):
        pid = msg.get("proposal_id")
        if self.status in ("HOLD", "ESTOP", "ERROR"):
            return [self._result(pid, False, ROBOT_HELD, f"로봇 상태 {self.status}", now)]
        if self.gate.heartbeat_lost(now):
            return [self._result(pid, False, NO_HEARTBEAT, "검증 서버 하트비트 없음", now)]
        if self.status == "MOVING":
            return [self._result(pid, False, ROBOT_BUSY, "다른 제안 실행 중", now)]
        if self.odom_lost(now):
            return [self._result(pid, False, HW_ERROR, "로봇 위치(/odom) 수신 없음", now)]
        params = msg.get("parameters") or {}
        kind = params.get("manipulation")
        if kind not in ("PICK", "PLACE"):
            # 스키마 v1.1 7장: 지원하지 않는 parameters 조합은 거부가 아니라 UNSUPPORTED로 알린다
            return [self._result(pid, False, UNSUPPORTED, f"지원하지 않는 manipulation: {kind!r} (PICK·PLACE만)", now)]
        if not params.get("object_id"):
            return [self._result(pid, False, INVALID, "parameters.object_id 필요", now)]
        position = (params.get("target_pose") or {}).get("position")
        if kind == "PLACE" and not (isinstance(position, (list, tuple)) and len(position) == 3):
            return [self._result(pid, False, INVALID, "PLACE에는 target_pose.position [x, y, z] 필요", now)]
        self.arm_outbox.append({"command_id": pid, "manipulation": kind, "object_id": params["object_id"],
                                "target_pose": params.get("target_pose"),
                                "approach_yaw": params.get("approach_yaw")})
        self.current, self.status, self.mode = msg, "MOVING", "manipulate"
        self.deadline = now + MANIPULATE_TIMEOUT_S
        return [self._event("STATUS", pid, now, status="MOVING")]

    def handle_arm_result(self, result: dict, now: float) -> list[dict]:
        """브리지의 /arm/result를 실행 중인 MANIPULATE 제안의 RESULT로 바꾼다."""
        if self.mode != "manipulate" or not self.current:
            return []
        pid = self.current["proposal_id"]
        if result.get("command_id") != pid or result.get("reason_code") == "CANCELLED":
            return []  # 다른 명령의 결과이거나, 이쪽에서 취소한 결과 (이미 보고함)
        self._clear_motion(cancel_arm=False)
        self.status = "IDLE"
        return [self._result(pid, bool(result.get("success")), result.get("reason_code"),
                             result.get("message"), now)]

    def _on_resume(self, msg, now):
        # /odom이 끊겨 ERROR가 된 경우, 위치가 다시 들어오면 HOLD와 같은 절차로 복귀를 허용한다
        if self.status == "ERROR":
            if self.odom_lost(now):
                return [self._result(msg.get("proposal_id"), False, HW_ERROR, "/odom 복구 전", now)]
            self.status = "HOLD"
        return super()._on_resume(msg, now)

    def _step(self, now, dt):
        pid = self.current["proposal_id"]
        if self.odom_lost(now):
            self._clear_motion()
            self.status = "ERROR"
            return [self._result(pid, False, HW_ERROR, "이동 중 /odom 끊김", now)]
        if now > self.deadline:
            self._clear_motion()
            self.status = "IDLE"
            return [self._result(pid, False, TIMEOUT, "제한 시간 안에 끝나지 않음", now)]
        if self.mode == "manipulate":
            return []  # 브리지가 팔·베이스를 움직이고 /arm/result로 끝을 알린다

        dx, dy = self.target[0] - self.position[0], self.target[1] - self.position[1]
        dist = math.hypot(dx, dy)
        if dist <= ARRIVAL_TOLERANCE_M:
            self._clear_motion()
            self.status = "IDLE"
            return [self._result(pid, True, None, f"목표 도달 (오차 {dist * 100:.1f} cm)", now)]

        heading_error = wrap_angle(math.atan2(dy, dx) - self.yaw)
        angular = max(-self.max_angular, min(self.max_angular, K_ANGULAR * heading_error))
        linear = 0.0 if abs(heading_error) > HEADING_TOLERANCE_RAD else min(self.speed, K_LINEAR * dist)
        self.command = (linear, angular)
        return []

    def _clear_motion(self, cancel_arm=True):
        if cancel_arm and self.mode == "manipulate" and self.current:
            self.arm_outbox.append({"command_id": self.current["proposal_id"], "manipulation": "CANCEL"})
        super()._clear_motion()
        self.command = (0.0, 0.0)
        self.deadline = None
        self.mode = None
