"""로봇 팔 공통 어댑터 상태 로직 (이슈 #95). rclpy 없이 단위 테스트할 수 있도록 노드와 분리한다.

로봇 종류와 무관하게 같은 규약(core.MockRobotCore: ACK·RESULT, 중복 실행 방지, E-Stop·HOLD)을 쓰고,
로봇마다 다른 부분은 궤적 계획기(``planner``) 하나로 넘긴다. 계획기는 아래만 제공하면 된다.

    planner.home                      # 시작 관절각
    planner.joint_names
    planner.plan(manipulation, target, speed, q_now, holding) -> plan
        # plan.points[i].t, .q, .holding / plan.reached / plan.reason
    planner.tcp(q) -> [x, y, z]

지금 계획기는 MuJoCo 운동학 모델(control/mujoco/arms.ArmPlanner)이고, 실제 로봇은 같은 형태로 MoveIt 등을
감싼 계획기를 넣으면 된다. 지원 Action은 MANIPULATE(PICK·PLACE)·STOP·RESUME이며, 고정형 팔이므로 MOVE_TO는
UNSUPPORTED로 응답한다.

출력은 ``outbox``에 쌓는다: ``("joints", [q...])``(관절 위치 목표), ``("gripper", bool)``(닫힘).
"""
from __future__ import annotations

from .core import INVALID, NO_HEARTBEAT, ROBOT_BUSY, ROBOT_HELD, MockRobotCore

MANIPULATIONS = ("PICK", "PLACE")
DEFAULT_TCP_SPEED = 0.25  # m/s. ISO/TS 15066 협동 운전 권장 범위 안의 보수적 값 (온톨로지 확정 전 잠정값)


class ArmCore(MockRobotCore):
    adapter_name = "arm"
    supported = {"MANIPULATE", "STOP", "RESUME"}

    def __init__(self, robot_id, planner, robot_model, robot_type="MANIPULATOR", frame_id="world",
                 max_speed=DEFAULT_TCP_SPEED, base_position=(0.0, 0.0, 0.0)):
        super().__init__(robot_id, robot_type=robot_type, frame_id=frame_id, max_speed=max_speed,
                         position=base_position)
        self.planner = planner
        self.robot_model = robot_model
        self.q = list(planner.home)  # 명령한 관절각 (피드백이 오면 그 값)
        self.q_measured = None
        self.gripper_closed = False
        self.holding = None  # 들고 있는 object_id
        self.plan = None
        self.plan_started = None
        self.outbox = [("joints", list(self.q)), ("gripper", False)]

    # ---- 피드백 ----
    def update_joints(self, positions):
        if len(positions) == len(self.q):
            self.q_measured = [float(v) for v in positions]

    # ---- Action ----
    def _on_manipulate(self, msg, now):
        pid = msg.get("proposal_id")
        if self.status in ("HOLD", "ESTOP", "ERROR"):
            return [self._result(pid, False, ROBOT_HELD, f"로봇 상태 {self.status}", now)]
        if self.gate.heartbeat_lost(now):
            return [self._result(pid, False, NO_HEARTBEAT, "검증 서버 하트비트 없음", now)]
        if self.status == "MOVING":
            return [self._result(pid, False, ROBOT_BUSY, "다른 제안 실행 중", now)]
        params = msg.get("parameters") or {}
        manipulation = str(params.get("manipulation", "")).upper()
        object_id = params.get("object_id")
        target_pose = params.get("target_pose") or {}
        position = target_pose.get("position")
        frame = target_pose.get("frame_id", msg.get("frame_id", self.frame_id))
        if manipulation not in MANIPULATIONS:
            return [self._result(pid, False, INVALID, "parameters.manipulation은 PICK 또는 PLACE", now)]
        if not isinstance(object_id, str) or not object_id:
            return [self._result(pid, False, INVALID, "parameters.object_id 필요", now)]
        if not (isinstance(position, (list, tuple)) and len(position) == 3):
            return [self._result(pid, False, INVALID, "target_pose.position [x, y, z] 필요", now)]
        if frame != self.frame_id:
            return [self._result(pid, False, INVALID, f"frame_id {frame} != {self.frame_id}", now)]
        if manipulation == "PICK" and self.holding is not None:
            return [self._result(pid, False, INVALID, f"이미 {self.holding}을(를) 들고 있음", now)]
        if manipulation == "PLACE" and self.holding != object_id:
            return [self._result(pid, False, INVALID, f"{object_id}을(를) 들고 있지 않음", now)]
        limits = [self.max_speed] + [v for v in (msg.get("speed_limit"), params.get("speed_limit")) if v]
        speed = min(limits)
        if speed <= 0:
            return [self._result(pid, False, INVALID, "speed_limit은 0보다 커야 함", now)]

        start = self.q_measured or self.q
        plan = self.planner.plan(manipulation, [float(v) for v in position], speed, start,
                                 holding=self.holding is not None)
        if not plan.reached:
            return [self._result(pid, False, INVALID, f"목표에 닿을 수 없음: {plan.reason}", now)]
        self.current, self.plan, self.plan_started = msg, plan, now
        self.target = [float(v) for v in position]
        self.speed = speed
        self.status = "MOVING"
        return [self._event("STATUS", pid, now, status="MOVING", manipulation=manipulation)]

    # ---- 주기 갱신 ----
    def _step(self, now, dt):
        points = self.plan.points
        elapsed = now - self.plan_started
        point = points[-1]
        for p in points:
            if p.t >= elapsed:
                point = p
                break
        self.q = [float(v) for v in point.q]
        self.outbox.append(("joints", list(self.q)))
        if point.holding != self.gripper_closed:
            self.gripper_closed = point.holding
            self.outbox.append(("gripper", self.gripper_closed))
        self.velocity = self.speed if point.phase in ("APPROACH", "DESCEND", "LIFT") else 0.0
        if elapsed < points[-1].t:
            return []
        params = self.current.get("parameters") or {}
        manipulation = str(params.get("manipulation")).upper()
        self.holding = params.get("object_id") if manipulation == "PICK" else None
        pid = self.current["proposal_id"]
        self._clear_motion()
        self.status = "IDLE"
        return [self._result(pid, True, None, f"{manipulation} 완료", now)]

    def _clear_motion(self):
        if self.plan is not None:
            # 정지·취소: 마지막으로 보낸 관절각을 그대로 유지한다(그리퍼 상태도 유지해 물체를 떨어뜨리지 않음)
            self.outbox.append(("joints", list(self.q)))
        self.plan, self.plan_started = None, None
        super()._clear_motion()

    def state(self, now):
        out = super().state(now)
        out["robot_model"] = self.robot_model
        out["joints"] = {"names": list(self.planner.joint_names),
                         "positions": [round(v, 4) for v in (self.q_measured or self.q)]}
        out["tcp_position"] = [round(float(v), 4) for v in self.planner.tcp(self.q_measured or self.q)]
        out["gripper_closed"] = self.gripper_closed
        out["holding_object_id"] = self.holding
        return out
