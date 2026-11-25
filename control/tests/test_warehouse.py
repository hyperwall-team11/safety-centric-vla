import numpy as np

from amr import DECK_TOP, PlantAmrs
from arms import build_plant_with_arms
from warehouse import AMR, CROSS_X, TRIGGER_X, WALK_END_Y, WALK_START_Y, CrossingWorker, add_warehouse


def test_worker_crosses_once_after_amr_reaches_trigger_while_moving():
    worker = CrossingWorker()
    worker.observe_amr(TRIGGER_X, 0.0, 3.0)
    worker.observe_amr(TRIGGER_X - 0.01, 0.4, 4.0)
    assert worker.position(5.0)[:2] == (CROSS_X, WALK_START_Y)
    worker.observe_amr(TRIGGER_X, 0.4, 6.0)
    assert WALK_END_Y < worker.position(8.0)[1] < WALK_START_Y
    assert worker.position(60.0)[:2] == (CROSS_X, WALK_END_Y)


def test_loaded_pallet_and_front_camera_move_with_amr():
    import mujoco

    model, data, _ = build_plant_with_arms([], extras=(add_warehouse,))
    amrs = PlantAmrs(model, data, (AMR,), preloaded=True)
    x, y, *_ = amrs.odom(AMR.robot_id)
    pallet = amrs.pallets[AMR.pallet_id]
    np.testing.assert_allclose(data.mocap_pos[pallet], (x, y, DECK_TOP))
    camera_id = model.camera("amr_camera").id
    before = data.cam_xpos[camera_id].copy()

    amrs.set_velocity(AMR.robot_id, 0.4, 0)
    amrs.step(data, 0.2)
    mujoco.mj_forward(model, data)

    np.testing.assert_allclose(data.mocap_pos[pallet], (x + 0.08, y, DECK_TOP))
    np.testing.assert_allclose(data.cam_xpos[camera_id] - before, (0.08, 0, 0), atol=1e-9)


def test_approved_resume_and_new_move_deliver_loaded_pallet_in_control_core():
    from robot_adapter.amr_core import AmrCore
    from robot_adapter.diff_drive_core import ARRIVAL_TOLERANCE_M

    model, data, _ = build_plant_with_arms([], extras=(add_warehouse,))
    amrs = PlantAmrs(model, data, (AMR,), preloaded=True)
    core = AmrCore(AMR.robot_id)
    target = [*AMR.cell_dock[:2], 0.0]
    move = {"proposal_id": "initial", "robot_id": AMR.robot_id, "action_type": "MOVE_TO",
            "status": "APPROVED", "frame_id": "world", "speed_limit": 0.4,
            "parameters": {"target_pose": {"frame_id": "world", "position": target}}}

    core.handle_heartbeat(0.0)
    core.update_odom(*amrs.odom(AMR.robot_id), 0.0)
    assert core.handle_action(move, 0.0)[0]["kind"] == "ACK"
    core.tick(0.02, 0.02)
    amrs.set_velocity(AMR.robot_id, *core.command)
    amrs.step(data, 0.02)

    core.handle_estop({"active": True, "triggered_by": "RULE_ENGINE"}, 0.02)
    amrs.set_velocity(AMR.robot_id, *core.command)
    amrs.step(data, 0.02)
    assert core.status == "ESTOP"
    assert amrs.odom(AMR.robot_id)[3:] == (0.0, 0.0)
    core.handle_estop({"active": False, "triggered_by": "RULE_ENGINE"}, 0.04)
    assert core.status == "ESTOP"

    # CTL 계약에서 정의한 승인 스냅샷을 단위 테스트 입력으로 사용한다.
    resume = {**move, "proposal_id": "resume", "action_type": "RESUME", "parameters": {}}
    assert core.handle_action(resume, 0.04)[-1]["success"]
    assert core.status == "IDLE"
    core.update_odom(*amrs.odom(AMR.robot_id), 0.04)
    assert core.handle_action({**move, "proposal_id": "new-move"}, 0.04)[0]["kind"] == "ACK"
    for tick in range(3, 1500):
        now = tick * 0.02
        core.handle_heartbeat(now)
        core.update_odom(*amrs.odom(AMR.robot_id), now)
        events = core.tick(now, 0.02)
        amrs.set_velocity(AMR.robot_id, *core.command)
        amrs.step(data, 0.02)
        if events:
            assert events[-1]["kind"] == "RESULT" and events[-1]["success"]
            break
    else:
        raise AssertionError("검사 스테이션 도착 결과가 없음")

    x, y, _, speed, _ = amrs.odom(AMR.robot_id)
    assert abs(x - target[0]) <= ARRIVAL_TOLERANCE_M
    assert y == target[1] and speed == 0.0
    np.testing.assert_allclose(data.mocap_pos[amrs.pallets[AMR.pallet_id]], (x, y, DECK_TOP))
