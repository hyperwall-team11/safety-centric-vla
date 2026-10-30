"""DiffDriveCore(TIAGo 어댑터 로직)를 단순 운동학 모델로 시험한다."""
import math

import pytest

from robot_adapter.diff_drive_core import DiffDriveCore
from safety_control.gate import utc_iso

T0 = 1_000_000.0
DT = 0.02


class Unicycle:
    """명령한 (선속도, 각속도)를 그대로 따르는 이상적인 로봇."""

    def __init__(self):
        self.x = self.y = self.yaw = 0.0

    def step(self, v, w, dt=DT):
        self.yaw += w * dt
        self.x += v * math.cos(self.yaw) * dt
        self.y += v * math.sin(self.yaw) * dt


def proposal(action_type, pid="p1", status="APPROVED", **params):
    return {"proposal_id": pid, "robot_id": "robot-01", "action_type": action_type,
            "parameters": params, "frame_id": "world", "speed_limit": 0.4, "status": status}


def run(core, robot, now, seconds, heartbeat=True, odom=True):
    events = []
    for _ in range(int(seconds / DT)):
        now += DT
        if heartbeat:
            core.handle_heartbeat(now)
        if odom:
            core.update_odom(robot.x, robot.y, robot.yaw, core.command[0], core.command[1], now)
        events += core.tick(now, DT)
        robot.step(*core.command)
    return events, now


@pytest.fixture
def setup():
    core, robot = DiffDriveCore("robot-01"), Unicycle()
    core.handle_heartbeat(T0)
    core.update_odom(0, 0, 0, 0, 0, T0)
    return core, robot


def test_move_to_reaches_goal_behind_robot(setup):
    """목표가 뒤쪽이면 먼저 제자리 회전한 뒤 전진해 도착한다."""
    core, robot = setup
    events = core.handle_action(proposal("MOVE_TO", target_pose={"position": [-1.0, 0.5, 0.0]}), T0)
    assert events[0]["kind"] == "ACK" and core.status == "MOVING"
    assert core.tick(T0 + DT, DT) == [] and core.command[0] == 0.0 and core.command[1] > 0  # 회전 먼저
    events, _ = run(core, robot, T0, 20)
    result = [e for e in events if e["kind"] == "RESULT"][-1]
    assert result["success"] and core.status == "IDLE"
    assert math.hypot(robot.x + 1.0, robot.y - 0.5) <= 0.05
    assert core.command == (0.0, 0.0)


def test_speed_never_exceeds_limits(setup):
    core, robot = setup
    msg = proposal("MOVE_TO", target_pose={"position": [3.0, 0.0, 0.0]})
    msg["speed_limit"] = 0.25
    core.handle_action(msg, T0)
    now = T0
    for _ in range(200):
        now += DT
        core.handle_heartbeat(now)
        core.update_odom(robot.x, robot.y, robot.yaw, 0, 0, now)
        core.tick(now, DT)
        assert core.command[0] <= 0.25 + 1e-9 and abs(core.command[1]) <= core.max_angular + 1e-9
        robot.step(*core.command)


def test_estop_zeroes_command_immediately(setup):
    core, robot = setup
    core.handle_action(proposal("MOVE_TO", target_pose={"position": [2.0, 0.0, 0.0]}), T0)
    _, now = run(core, robot, T0, 1.0)
    assert core.command[0] > 0
    events = core.handle_estop({"active": True, "triggered_by": "OPERATOR", "issued_at": utc_iso(now)}, now)
    assert core.status == "ESTOP" and core.command == (0.0, 0.0)
    assert events[0]["reason_code"] == "ESTOP"


def test_heartbeat_loss_holds_and_zeroes_command(setup):
    core, robot = setup
    core.handle_action(proposal("MOVE_TO", target_pose={"position": [2.0, 0.0, 0.0]}), T0)
    _, now = run(core, robot, T0, 0.5)
    events, _ = run(core, robot, now, 1.2, heartbeat=False)
    assert core.status == "HOLD" and core.command == (0.0, 0.0)
    assert any(e["kind"] == "HOLD" and e["triggered_by"] == "HEARTBEAT_LOSS" for e in events)


def test_refuses_move_without_odom():
    core = DiffDriveCore("robot-01")
    core.handle_heartbeat(T0)
    events = core.handle_action(proposal("MOVE_TO", target_pose={"position": [1.0, 0.0, 0.0]}), T0)
    assert events[-1]["reason_code"] == "HW_ERROR" and core.status == "IDLE"


def test_odom_loss_while_moving_is_hw_error_then_resume(setup):
    core, robot = setup
    core.handle_action(proposal("MOVE_TO", target_pose={"position": [2.0, 0.0, 0.0]}), T0)
    _, now = run(core, robot, T0, 0.5)
    events, now = run(core, robot, now, 1.0, odom=False)
    assert core.status == "ERROR" and core.command == (0.0, 0.0)
    assert any(e.get("reason_code") == "HW_ERROR" for e in events)
    core.update_odom(robot.x, robot.y, robot.yaw, 0, 0, now)
    resumed = core.handle_action(proposal("RESUME", pid="p2", task_id="t"), now)
    assert resumed[-1]["success"] and core.status == "IDLE"


def test_timeout_when_robot_cannot_move(setup):
    """로봇이 막혀 움직이지 못하면 제한 시간 뒤 TIMEOUT."""
    core, _ = setup
    core.handle_action(proposal("MOVE_TO", target_pose={"position": [1.0, 0.0, 0.0]}), T0)
    stuck = Unicycle()
    events, _ = run(core, stuck, T0, 0.1)
    stuck.step = lambda v, w, dt=DT: None  # 명령을 따르지 않음
    events, _ = run(core, stuck, T0 + 0.1, 30)
    result = [e for e in events if e["kind"] == "RESULT"][-1]
    assert result["reason_code"] == "TIMEOUT" and core.status == "IDLE"


def test_unapproved_does_not_move(setup):
    core, _ = setup
    core.handle_action(proposal("MOVE_TO", status="PROPOSED", target_pose={"position": [1.0, 0.0, 0.0]}), T0)
    assert core.status == "IDLE" and core.command == (0.0, 0.0)


def test_state_reports_yaw(setup):
    core, _ = setup
    core.update_odom(1.0, 2.0, math.pi / 2, 0, 0, T0)
    state = core.state(T0)
    assert state["control_adapter"] == "mujoco_diff_drive"
    assert state["pose"]["orientation"][2] == pytest.approx(math.sin(math.pi / 4))
