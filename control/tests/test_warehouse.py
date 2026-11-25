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
