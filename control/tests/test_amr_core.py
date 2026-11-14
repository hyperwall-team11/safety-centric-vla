"""AmrCore: TIAGo 어댑터 규약 위에 팔레트 싣기·내리기 상태를 더한 것."""
import pytest

from robot_adapter.amr_core import AmrCore

T0 = 1_000_000.0


def manip(kind, pid, object_id="pallet:amr-01", position=(4.72, 1.75, 0.3)):
    return {"proposal_id": pid, "robot_id": "amr-01", "action_type": "MANIPULATE", "status": "APPROVED",
            "frame_id": "world", "speed_limit": 0.4,
            "parameters": {"manipulation": kind, "object_id": object_id,
                           "target_pose": {"frame_id": "world", "position": list(position)}}}


@pytest.fixture
def core():
    c = AmrCore("amr-01")
    c.handle_heartbeat(T0)
    c.update_odom(3.7, 1.75, 0.0, 0.0, 0.0, T0)
    return c


def finish(core, pid, success=True, now=T0 + 1):
    cmd = core.load_outbox.pop(0)
    assert cmd["command_id"] == pid
    return core.handle_load_result({"command_id": pid, "success": success,
                                    "reason_code": None if success else "INVALID", "message": "m"}, now)


def test_pick_then_place_tracks_holding_and_state(core):
    events = core.handle_action(manip("PICK", "p1"), T0)
    assert [e["kind"] for e in events] == ["ACK", "STATUS"]
    assert core.load_outbox[0]["manipulation"] == "PICK"
    assert finish(core, "p1")[-1]["success"]
    state = core.state(T0 + 1)
    assert state["holding_object_id"] == "pallet:amr-01"
    assert state["robot_model"] == "amr" and state["robot_type"] == "MOBILE"
    assert state["control_adapter"] == "mujoco_amr"
    core.handle_heartbeat(T0 + 1)
    core.update_odom(3.7, 1.75, 0.0, 0.0, 0.0, T0 + 1)
    core.handle_action(manip("PLACE", "p2"), T0 + 1)
    assert finish(core, "p2", now=T0 + 2)[-1]["success"]
    assert core.holding is None


def test_pick_while_loaded_and_place_without_load_are_invalid(core):
    core.handle_action(manip("PICK", "p1"), T0)
    finish(core, "p1")
    assert core.handle_action(manip("PICK", "p2"), T0 + 1)[-1]["reason_code"] == "INVALID"
    other = AmrCore("amr-01")
    other.handle_heartbeat(T0)
    other.update_odom(0, 0, 0, 0, 0, T0)
    assert other.handle_action(manip("PLACE", "p3"), T0)[-1]["reason_code"] == "INVALID"
    assert not other.load_outbox


def test_failed_pick_keeps_empty(core):
    core.handle_action(manip("PICK", "p1"), T0)
    assert not finish(core, "p1", success=False)[-1]["success"]
    assert core.holding is None and core.status == "IDLE"


def test_estop_during_transfer_sends_cancel_and_keeps_holding_unknown(core):
    core.handle_action(manip("PICK", "p1"), T0)
    core.load_outbox.clear()
    events = core.handle_estop({"active": True, "triggered_by": "OPERATOR"}, T0 + 0.1)
    assert events[0]["reason_code"] == "ESTOP"
    assert core.load_outbox == [{"command_id": "p1", "manipulation": "CANCEL"}]
    assert core.holding is None


def test_move_to_drives_like_diff_drive(core):
    msg = {"proposal_id": "m1", "robot_id": "amr-01", "action_type": "MOVE_TO", "status": "APPROVED",
           "frame_id": "world", "speed_limit": 0.4,
           "parameters": {"target_pose": {"frame_id": "world", "position": [2.05, 1.75, 0.0]}}}
    core.handle_action(msg, T0)
    core.tick(T0 + 0.02, 0.02)
    linear, angular = core.command
    assert linear == 0.0 and abs(angular) > 0  # 뒤쪽 목표: 제자리 회전부터
