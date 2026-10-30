"""제어 어댑터 공통 상태 로직. rclpy 없이 단위 테스트할 수 있도록 노드와 분리한다.

규약 (스키마 v1.1 7·9장, 개발계획서 5-5·5-6장):
- APPROVED 제안만 실행. 미승인 제안은 ACK 없이 NOT_APPROVED로 거부한다.
- 수신 즉시 ACK, 완료·실패 시 RESULT. 미지원 조합은 UNSUPPORTED로 응답한다.
- E-Stop 수신 시 즉시 ESTOP, 하트비트 1초(5주기) 무수신 시 자율 HOLD.
- 정지 후 복귀는 E-Stop 해제 + 하트비트 정상 상태에서 승인된 RESUME으로만 한다.
  HOLD·E-Stop 판단 자체는 safety_control.gate.SafetyGate가 담당한다.
"""
from __future__ import annotations

import math

from safety_control.gate import SafetyGate, utc_iso

ACTION_TYPES = {"MOVE_TO", "MANIPULATE", "STOP", "RESUME"}
DEFAULT_MAX_SPEED = 0.4  # SR-01 기본값 (m/s)
ARRIVAL_TOLERANCE_M = 0.01

# 스키마 v1.1 사유 코드
UNSUPPORTED, TIMEOUT, HW_ERROR = "UNSUPPORTED", "TIMEOUT", "HW_ERROR"
# 어댑터 로컬 사유 코드 (스키마 반영 여부는 ONT와 협의 필요)
NOT_APPROVED = "NOT_APPROVED"
INVALID = "INVALID"
ROBOT_BUSY = "ROBOT_BUSY"
ROBOT_HELD = "ROBOT_HELD"
NO_HEARTBEAT = "NO_HEARTBEAT"
CANCELLED = "CANCELLED"
ESTOPPED = "ESTOP"


class MockRobotCore:
    """위치만 갖는 가상 로봇. MOVE_TO·STOP·RESUME만 지원하고 MANIPULATE는 UNSUPPORTED."""

    supported = {"MOVE_TO", "STOP", "RESUME"}
    adapter_name = "mock"

    def __init__(self, robot_id, robot_type="QUADRUPED", frame_id="world",
                 max_speed=DEFAULT_MAX_SPEED, position=(0.0, 0.0, 0.0)):
        self.robot_id = robot_id
        self.robot_type = robot_type
        self.frame_id = frame_id
        self.max_speed = max_speed
        self.position = list(position)
        self.status = "IDLE"
        self.velocity = 0.0
        self.current = None  # 실행 중인 제안
        self.target = None
        self.speed = 0.0
        self.gate = SafetyGate()
        self.updated_at = None

    # ---- 입력 ----
    def handle_action(self, msg: dict, now: float) -> list[dict]:
        if msg.get("robot_id") != self.robot_id:
            return []
        pid = msg.get("proposal_id")
        if msg.get("status") != "APPROVED":
            return [self._result(pid, False, NOT_APPROVED, "APPROVED 상태가 아닌 제안", now)]

        action_type = msg.get("action_type")
        events = [self._event("ACK", pid, now)]
        if action_type not in ACTION_TYPES:
            return events + [self._result(pid, False, INVALID, f"알 수 없는 action_type: {action_type}", now)]
        if action_type not in self.supported:
            return events + [self._result(pid, False, UNSUPPORTED, f"{action_type} 미지원 로봇", now)]
        handler = getattr(self, f"_on_{action_type.lower()}")
        return events + handler(msg, now)

    def handle_heartbeat(self, now: float) -> None:
        self.gate.on_heartbeat(now)

    def handle_estop(self, msg: dict, now: float) -> list[dict]:
        trigger = self.gate.on_estop(msg, now)
        if trigger is None or self.status == "ESTOP":
            return []
        return self._halt("ESTOP", trigger["triggered_by"], now, trigger["latency_ms"])

    # ---- 주기 갱신 ----
    def tick(self, now: float, dt: float) -> list[dict]:
        events = []
        if self.gate.heartbeat_expired(now) and self.status not in ("HOLD", "ESTOP"):
            events += self._halt("HOLD", "HEARTBEAT_LOSS", now, None)
        if self.status == "MOVING":
            events += self._step(now, dt)
        self.updated_at = now
        return events

    def state(self, now: float) -> dict:
        return {
            "robot_id": self.robot_id,
            "robot_type": self.robot_type,
            "control_adapter": self.adapter_name,
            "status": self.status,
            "pose": {"frame_id": self.frame_id, "position": [round(v, 4) for v in self.position],
                     "orientation": self.orientation()},
            "velocity": round(self.velocity, 4),
            "motion_limits": {"max_speed": self.max_speed},
            "estop_active": self.gate.estop_active,
            "current_proposal_id": self.current["proposal_id"] if self.current else None,
            "updated_at": utc_iso(now),
            "last_heartbeat_at": utc_iso(self.gate.last_heartbeat) if self.gate.last_heartbeat else None,
        }

    def orientation(self):
        """(x, y, z, w) 쿼터니언. Mock은 방향이 없다."""
        return [0.0, 0.0, 0.0, 1.0]

    # ---- Action 처리 ----
    def _on_move_to(self, msg, now):
        pid = msg.get("proposal_id")
        if self.status in ("HOLD", "ESTOP", "ERROR"):
            return [self._result(pid, False, ROBOT_HELD, f"로봇 상태 {self.status}", now)]
        if self.gate.heartbeat_lost(now):
            return [self._result(pid, False, NO_HEARTBEAT, "검증 서버 하트비트 없음", now)]
        if self.status == "MOVING":
            return [self._result(pid, False, ROBOT_BUSY, "다른 제안 실행 중", now)]
        params = msg.get("parameters") or {}
        target_pose = params.get("target_pose") or {}
        position = target_pose.get("position")
        frame = target_pose.get("frame_id", msg.get("frame_id", self.frame_id))
        if not (isinstance(position, (list, tuple)) and len(position) == 3):
            return [self._result(pid, False, INVALID, "target_pose.position [x, y, z] 필요", now)]
        if frame != self.frame_id:
            return [self._result(pid, False, INVALID, f"frame_id {frame} != {self.frame_id}", now)]
        limits = [self.max_speed] + [v for v in (msg.get("speed_limit"), params.get("speed_limit")) if v]
        self.speed = min(limits)
        if self.speed <= 0:
            return [self._result(pid, False, INVALID, "speed_limit은 0보다 커야 함", now)]
        self.current, self.target, self.status = msg, [float(v) for v in position], "MOVING"
        return [self._event("STATUS", pid, now, status="MOVING")]

    def _on_stop(self, msg, now):
        events = []
        if self.current:
            events.append(self._result(self.current["proposal_id"], False, CANCELLED, "STOP 수신", now))
        self._clear_motion()
        if self.status == "MOVING":
            self.status = "IDLE"
        return events + [self._result(msg.get("proposal_id"), True, None, f"정지 완료 (상태 {self.status})", now)]

    def _on_resume(self, msg, now):
        pid = msg.get("proposal_id")
        if self.status not in ("HOLD", "ESTOP"):
            return [self._result(pid, True, None, f"이미 {self.status}", now)]
        blocker = self.gate.resume_blocker(now)
        if blocker:
            return [self._result(pid, False, blocker, "E-Stop 해제·하트비트 복구 전", now)]
        self.status = "IDLE"
        return [self._result(pid, True, None, "IDLE 복귀", now)]

    # ---- 내부 ----
    def _halt(self, status, triggered_by, now, latency_ms):
        events = []
        if self.current:
            reason = ESTOPPED if status == "ESTOP" else TIMEOUT
            events.append(self._result(self.current["proposal_id"], False, reason, f"{status} ({triggered_by})", now))
        self._clear_motion()
        self.status = status
        events.append(self._event(status, None, now, triggered_by=triggered_by, latency_ms=latency_ms))
        return events

    def _step(self, now, dt):
        delta = [t - p for t, p in zip(self.target, self.position)]
        dist = math.sqrt(sum(d * d for d in delta))
        step = self.speed * dt
        if dist <= max(step, ARRIVAL_TOLERANCE_M):
            self.position = list(self.target)
            pid = self.current["proposal_id"]
            self._clear_motion()
            self.status = "IDLE"
            return [self._result(pid, True, None, "목표 도달", now)]
        self.position = [p + d / dist * step for p, d in zip(self.position, delta)]
        self.velocity = self.speed
        return []

    def _clear_motion(self):
        self.current, self.target, self.velocity = None, None, 0.0

    def _event(self, kind, proposal_id, now, **extra):
        return {"kind": kind, "robot_id": self.robot_id, "proposal_id": proposal_id,
                "timestamp": utc_iso(now), **extra}

    def _result(self, proposal_id, success, reason_code, message, now):
        return self._event("RESULT", proposal_id, now, success=success,
                           reason_code=reason_code, message=message)
