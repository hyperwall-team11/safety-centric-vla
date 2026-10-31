import math

import mujoco
import numpy as np
import pytest

from scenario import CAMERAS, JOINT_STATE_NAMES, CameraRig, WorkerScript, camera_intrinsics, in_zone
from simulation import DiffDriveSim


@pytest.fixture
def sim():
    return DiffDriveSim("scenario")


def box_pos(sim):
    return sim.data.xpos[sim.model.body("part_box").id]


def test_scene_has_cameras_and_joints(sim):
    m = sim.model
    names = {m.camera(i).name for i in range(m.ncam)}
    assert {cam for cam, _, _ in CAMERAS.values()} <= names
    joints = {m.joint(i).name for i in range(m.njnt)}
    assert set(JOINT_STATE_NAMES) <= joints


def test_robot_stands_on_floor(sim):
    sim.step(2.0)
    assert abs(sim.data.qpos[sim.qpos_adr + 2]) < 0.01
    x, y, yaw = sim.pose()
    assert math.hypot(x, y) < 0.01 and abs(yaw) < 0.01


def test_box_rests_on_table_a(sim):
    sim.step(2.0)
    x, y, z = box_pos(sim)
    assert (x, y) == pytest.approx((1.2, 1.0), abs=0.01)
    assert z == pytest.approx(0.64, abs=0.005)  # 작업대 윗면 0.6 m + 상자 반높이 0.04 m


def test_dropped_box_lands_on_table_b_not_floor(sim):
    """작업대가 충돌을 갖는지: 상자를 B 작업대 위에서 떨어뜨리면 윗면에 멈춘다."""
    adr = sim.model.joint("part_box_joint").qposadr[0]
    sim.data.qpos[adr:adr + 7] = [1.2, -1.0, 0.9, 1, 0, 0, 0]
    sim.step(2.0)
    assert box_pos(sim)[2] == pytest.approx(0.64, abs=0.005)


def test_robot_cannot_drive_through_table(sim):
    """로봇과 작업대가 충돌하는지: 작업대 A를 향해 달려도 작업대를 통과하지 못한다."""
    sim.set_velocity(0.0, 0.7)
    sim.step(math.atan2(1.0, 1.2) / 0.7)  # 작업대 A 방향으로 회전
    sim.set_velocity(0.4, 0.0)
    sim.step(6.0)
    x, y, _ = sim.pose()
    assert math.hypot(x - 1.2, y - 1.0) > 0.3  # TIAGo 반지름 약 0.27 m보다 가까이 못 들어감


def test_keep_out_zone_has_no_collision(sim):
    g = sim.model.geom("keep_out_zone")
    assert g.contype == 0 and g.conaffinity == 0


def test_worker_path_enters_and_leaves_keep_out(sim):
    worker = WorkerScript()
    samples = [worker.position(t) for t in np.arange(0, worker.period, 0.1)]
    inside = [in_zone(sim.model, "keep_out_zone", x, y) for x, y, _ in samples]
    assert inside[0] is False and any(inside) and not all(inside)
    # 한 주기 뒤에는 같은 위치로 돌아온다
    assert worker.position(0.5)[:2] == pytest.approx(worker.position(0.5 + worker.period)[:2])


def test_worker_moves_at_configured_speed():
    worker = WorkerScript(speed=0.6)
    (x0, y0, _), (x1, y1, _) = worker.position(1.5), worker.position(2.5)  # 첫 정지(1 s) 뒤 이동 구간
    assert math.hypot(x1 - x0, y1 - y0) == pytest.approx(0.6, abs=1e-6)


def test_worker_apply_moves_mocap(sim):
    worker = WorkerScript()
    sim.data.time = 5.5
    x, y, _ = worker.apply(sim.model, sim.data)
    mujoco.mj_forward(sim.model, sim.data)
    assert sim.data.xpos[sim.model.body("worker").id][:2] == pytest.approx([x, y])


def test_camera_intrinsics_match_fovy(sim):
    fx, fy, cx, cy = camera_intrinsics(sim.model, "cctv_1", 640, 480)
    assert (cx, cy) == (320, 240)
    assert 2 * math.degrees(math.atan(240 / fy)) == pytest.approx(60)


def test_cameras_render_non_blank(sim):
    rig = CameraRig(sim.model, 160, 120)
    try:
        for sensor in rig.sensors:
            img = rig.render(sim.data, sensor)
            assert img.shape == (120, 160, 3) and img.std() > 5
    finally:
        rig.close()
