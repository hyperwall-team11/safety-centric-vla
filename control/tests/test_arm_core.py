"""로봇 팔 공통 어댑터 코어 (이슈 #95). 계획기를 가짜로 바꿔 규약만 시험한다."""
from dataclasses import dataclass

import pytest

from robot_adapter.arm_core import ArmCore

T0 = 1_000_000.0


@dataclass
class P:
    t: float
    q: list
    phase: str
    holding: bool


@dataclass
class Plan:
    points: list
    reached: bool = True
    reason: str | None = None


class FakePlanner:
    home = [0.0, 0.0]
    joint_names = ["j1", "j2"]

    def __init__(self, reached=True):
        self.reached = reached
        self.calls = []

    def plan(self, manipulation, target, speed, q_now, holding=None):
        self.calls.append((manipulation, target, speed, list(q_now), holding))
        grab = manipulation == "PICK"
        return Plan([P(0.0, q_now, "APPROACH", not grab), P(0.5, [0.5, 0.5], "DESCEND", not grab),
                     P(1.0, [1.0, 1.0], "GRASP" if grab else "RELEASE", grab),
                     P(1.5, [0.5, 0.5], "LIFT", grab)], self.reached, None if self.reached else "far")

    def tcp(self, q):
        return [q[0], q[1], 0.0]


def manip(manipulation="PICK", pid="p1", object_id="box-1", position=(0.5, 0.0, 0.1), **extra):
    return {"proposal_id": pid, "robot_id": "arm-01", "action_type": "MANIPULATE", "status": "APPROVED",
            "frame_id": "world", "speed_limit": 0.4,
            "parameters": {"manipulation": manipulation, "object_id": object_id,
                           "target_pose": {"frame_id": "world", "position": list(position)}}, **extra}


@pytest.fixture
def arm():
    core = ArmCore("arm-01", FakePlanner(), "ur5e")
    core.handle_heartbeat(T0)
    core.outbox.clear()
    return core


def run(core, start, until=2.0, dt=0.1):
    now, events = start, []
    while now < start + until:
        now += dt
        core.handle_heartbeat(now)
        events += core.tick(now, dt)
    return events


def test_pick_streams_joints_closes_gripper_and_reports_result(arm):
    events = arm.handle_action(manip(), T0)
    assert [e["kind"] for e in events] == ["ACK", "STATUS"]
    assert arm.planner.calls[0][2] == pytest.approx(0.25)  # 어댑터 TCP 속도 상한이 speed_limit보다 작다
    events = run(arm, T0)
    result = [e for e in events if e["kind"] == "RESULT"]
    assert result and result[0]["success"] and result[0]["proposal_id"] == "p1"
    assert ("gripper", True) in arm.outbox
    assert arm.holding == "box-1" and arm.status == "IDLE"
    assert arm.state(T0)["holding_object_id"] == "box-1"
    assert arm.state(T0)["robot_model"] == "ur5e"


def test_place_requires_holding_same_object(arm):
    events = arm.handle_action(manip("PLACE"), T0)
    assert events[-1]["reason_code"] == "INVALID"
    arm.handle_action(manip(pid="p2"), T0)
    run(arm, T0)
    events = arm.handle_action(manip("PLACE", pid="p3", object_id="other"), T0 + 3)
    assert events[-1]["reason_code"] == "INVALID"
    arm.handle_action(manip("PLACE", pid="p4"), T0 + 3)
    run(arm, T0 + 3)
    assert arm.holding is None and ("gripper", False) in arm.outbox


def test_move_to_is_unsupported_for_fixed_arm(arm):
    msg = manip()
    msg["action_type"] = "MOVE_TO"
    assert arm.handle_action(msg, T0)[-1]["reason_code"] == "UNSUPPORTED"


def test_unreachable_target_is_rejected_without_motion(arm):
    arm.planner.reached = False
    events = arm.handle_action(manip(), T0)
    assert events[-1]["reason_code"] == "INVALID" and "far" in events[-1]["message"]
    assert arm.status == "IDLE" and not arm.outbox


def test_estop_freezes_joints_and_keeps_gripper(arm):
    arm.handle_action(manip(), T0)
    run(arm, T0, until=1.2)  # 파지 후 들어 올리는 중
    arm.outbox.clear()
    q_before = list(arm.q)
    events = arm.handle_estop({"active": True, "triggered_by": "RULE_ENGINE"}, T0 + 1.25)
    assert any(e["kind"] == "RESULT" and e["reason_code"] == "ESTOP" for e in events)
    assert arm.status == "ESTOP"
    assert arm.outbox == [("joints", q_before)]  # 그 자리에서 멈추고 그리퍼는 그대로(물체 유지)
    assert arm.gripper_closed


def test_duplicate_proposal_is_not_executed_twice(arm):
    arm.handle_action(manip(), T0)
    run(arm, T0)
    events = arm.handle_action(manip(), T0 + 3)
    assert [e["kind"] for e in events] == ["ACK", "RESULT"] and events[-1]["success"]
    assert len(arm.planner.calls) == 1


def test_joint_feedback_is_used_as_plan_start(arm):
    arm.update_joints([0.2, -0.1])
    arm.handle_action(manip(), T0)
    assert arm.planner.calls[0][3] == [0.2, -0.1]
