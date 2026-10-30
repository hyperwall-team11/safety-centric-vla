import math

import pytest

from simulation import ROBOTS, DiffDriveSim


@pytest.fixture(params=sorted(ROBOTS))
def sim(request):
    return DiffDriveSim(request.param)


def test_simple_robot_geometry():
    sim = DiffDriveSim("simple")
    assert sim.wheel_radius == pytest.approx(0.05)
    assert sim.wheel_separation == pytest.approx(0.35)


def test_tiago_starts_on_floor_and_holds_arm():
    sim = DiffDriveSim("tiago")
    sim.step(1.0)
    assert abs(sim.data.qpos[sim.qpos_adr + 2]) < 0.01  # 바닥 위에 서 있음
    arm1 = sim.model.joint("arm_1_joint").qposadr[0]
    assert sim.data.qpos[arm1] == pytest.approx(0.2, abs=0.02)  # home 자세 유지


def test_forward(sim):
    sim.set_velocity(0.3, 0.0)
    sim.step(2.0)
    x, y, yaw = sim.pose()
    assert x == pytest.approx(0.6, abs=0.06)
    assert abs(y) < 0.02 and abs(yaw) < math.radians(3)


def test_rotate_in_place(sim):
    sim.set_velocity(0.0, 0.5)
    sim.step(math.pi / 2 / 0.5)
    x, y, yaw = sim.pose()
    assert math.degrees(yaw) == pytest.approx(90, abs=10)
    assert math.hypot(x, y) < 0.05


def test_stop(sim):
    sim.set_velocity(0.3, 0.2)
    sim.step(1.0)
    sim.stop()
    sim.step(1.0)
    v, w = sim.twist()
    assert abs(v) < 0.01 and abs(w) < 0.01
