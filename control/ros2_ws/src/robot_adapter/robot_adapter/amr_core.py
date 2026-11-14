"""이동형 운반 로봇(AMR) 어댑터 상태 로직. rclpy 없이 단위 테스트할 수 있도록 노드와 분리한다.

TIAGo 어댑터(diff_drive_core.DiffDriveCore)와 같은 규약·MOVE_TO 제어기를 그대로 쓴다.
- MOVE_TO: /odom 위치로 제자리 회전 → 직진 (DiffDriveCore와 같음)
- MANIPULATE PICK: 차체 앞 거치대의 팔레트를 싣는다. PLACE: 실린 팔레트를 차체 앞 거치대(target_pose)에 내린다.
  싣기·내리기는 브리지가 운동학적으로 처리하고(``load_outbox`` → /<seg>/amr/load_command), 결과를 받아 RESULT로 보고한다.
- 실린 팔레트(``holding``)는 상태에 ``holding_object_id``로 싣는다. 사전 시뮬레이션은 이 값으로 차체 크기를 키운다.
"""
from __future__ import annotations

from .core import INVALID
from .diff_drive_core import DiffDriveCore

ROBOT_MODEL = "amr"
DEFAULT_MAX_SPEED = 0.4  # SR-01 기본값 (m/s). 팔레트를 실어도 같은 상한 (온톨로지 확정 전 잠정)


class AmrCore(DiffDriveCore):
    adapter_name = "mujoco_amr"

    def __init__(self, robot_id, frame_id="world", max_speed=DEFAULT_MAX_SPEED, max_angular=0.8,
                 robot_model=ROBOT_MODEL):
        super().__init__(robot_id, robot_type="MOBILE", frame_id=frame_id, max_speed=max_speed,
                         max_angular=max_angular)
        self.robot_model = robot_model
        self.holding = None  # 실린 팔레트 object_id

    @property
    def load_outbox(self):
        # DiffDriveCore는 팔 명령을 arm_outbox에 쌓는다. AMR은 같은 형식을 싣기·내리기 명령으로 쓴다
        return self.arm_outbox

    def _on_manipulate(self, msg, now):
        params = msg.get("parameters") or {}
        kind = params.get("manipulation")
        object_id = params.get("object_id")
        pid = msg.get("proposal_id")
        if self.status not in ("HOLD", "ESTOP", "ERROR", "MOVING") and not self.gate.heartbeat_lost(now):
            if kind == "PICK" and self.holding is not None:
                return [self._result(pid, False, INVALID, f"이미 {self.holding}을(를) 싣고 있음", now)]
            if kind == "PLACE" and object_id and self.holding != object_id:
                return [self._result(pid, False, INVALID, f"{object_id}을(를) 싣고 있지 않음", now)]
        return super()._on_manipulate(msg, now)

    def handle_arm_result(self, result, now):
        params = (self.current or {}).get("parameters") or {}
        events = super().handle_arm_result(result, now)
        if events and events[-1].get("success"):
            self.holding = params.get("object_id") if params.get("manipulation") == "PICK" else None
        return events

    handle_load_result = handle_arm_result

    def state(self, now):
        out = super().state(now)
        out["robot_model"] = self.robot_model
        out["holding_object_id"] = self.holding
        return out
