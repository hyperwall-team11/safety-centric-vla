import pytest

from robot_adapter.core import MockRobotCore
from safety_control.gate import utc_iso

T0 = 1_000_000.0


def proposal(action_type, status="APPROVED", **params):
    return {"proposal_id": "p1", "robot_id": "robot-01", "action_type": action_type,
            "parameters": params, "frame_id": "world", "speed_limit": 0.4, "status": status}


@pytest.fixture
def robot():
    core = MockRobotCore("robot-01")
    core.handle_heartbeat(T0)
    return core


def run_until_idle(core, start, dt=0.02, limit=1000):
    now, events = start, []
    for _ in range(limit):
        now += dt
        core.handle_heartbeat(now)
        events += core.tick(now, dt)
        if core.status != "MOVING":
            break
    return events


def test_rejects_unapproved_without_ack(robot):
    events = robot.handle_action(proposal("MOVE_TO", status="PROPOSED", target_pose={"position": [1, 0, 0]}), T0)
    assert [e["kind"] for e in events] == ["RESULT"]
    assert events[0]["reason_code"] == "NOT_APPROVED"
    assert robot.status == "IDLE"


def test_ignores_other_robot(robot):
    msg = proposal("MOVE_TO", target_pose={"position": [1, 0, 0]})
    msg["robot_id"] = "robot-02"
    assert robot.handle_action(msg, T0) == []


def test_move_to_acks_then_completes(robot):
    events = robot.handle_action(proposal("MOVE_TO", target_pose={"position": [0.4, 0, 0]}), T0)
    assert events[0]["kind"] == "ACK"
    assert robot.status == "MOVING"
    done = run_until_idle(robot, T0)
    assert done[-1]["success"] is True
    assert robot.position == [0.4, 0.0, 0.0]


def test_speed_is_capped_by_robot_limit(robot):
    msg = proposal("MOVE_TO", target_pose={"position": [1, 0, 0]})
    msg["speed_limit"] = 2.0
    robot.handle_action(msg, T0)
    assert robot.speed == pytest.approx(0.4)


def test_estop_stops_motion_and_requires_release_and_resume(robot):
    robot.handle_action(proposal("MOVE_TO", target_pose={"position": [1, 0, 0]}), T0)
    events = robot.handle_estop({"active": True, "triggered_by": "OPERATOR", "issued_at": utc_iso(T0)}, T0 + 0.01)
    assert robot.status == "ESTOP"
    assert events[0]["reason_code"] == "ESTOP"
    assert events[-1]["kind"] == "ESTOP" and events[-1]["latency_ms"] == pytest.approx(10, abs=0.1)

    blocked = robot.handle_action(proposal("RESUME", task_id="t"), T0 + 0.1)
    assert blocked[-1]["success"] is False

    robot.handle_estop({"active": False}, T0 + 0.2)
    assert robot.status == "ESTOP"  # 해제만으로는 복귀하지 않음
    resumed = robot.handle_action(proposal("RESUME", task_id="t"), T0 + 0.3)
    assert resumed[-1]["success"] is True and robot.status == "IDLE"


def test_heartbeat_loss_enters_hold(robot):
    robot.handle_action(proposal("MOVE_TO", target_pose={"position": [1, 0, 0]}), T0)
    assert robot.tick(T0 + 0.9, 0.02) == []
    events = robot.tick(T0 + 1.05, 0.02)
    assert robot.status == "HOLD"
    assert events[-1]["kind"] == "HOLD" and events[-1]["triggered_by"] == "HEARTBEAT_LOSS"


def test_move_refused_without_heartbeat():
    core = MockRobotCore("robot-01")
    events = core.handle_action(proposal("MOVE_TO", target_pose={"position": [1, 0, 0]}), T0)
    assert events[-1]["reason_code"] == "NO_HEARTBEAT"


def test_manipulate_is_unsupported_after_ack(robot):
    events = robot.handle_action(proposal("MANIPULATE", manipulation="PICK", object_id="box"), T0)
    assert [e["kind"] for e in events] == ["ACK", "RESULT"]
    assert events[-1]["reason_code"] == "UNSUPPORTED"


def test_stop_cancels_current(robot):
    robot.handle_action(proposal("MOVE_TO", target_pose={"position": [1, 0, 0]}), T0)
    stop = proposal("STOP")
    stop["proposal_id"] = "p2"
    events = robot.handle_action(stop, T0 + 0.1)
    assert events[1]["proposal_id"] == "p1" and events[1]["reason_code"] == "CANCELLED"
    assert events[-1]["success"] is True and robot.status == "IDLE"


def test_frame_mismatch_is_invalid(robot):
    events = robot.handle_action(proposal("MOVE_TO", target_pose={"position": [1, 0, 0], "frame_id": "map"}), T0)
    assert events[-1]["reason_code"] == "INVALID"
