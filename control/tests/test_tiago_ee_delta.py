import numpy as np
import pytest

from simulation import DiffDriveSim
from tiago_manipulation import GRIPPER_CLOSED, ManipulationError, TiagoManipulator, run


@pytest.fixture
def tiago():
    sim = DiffDriveSim("scenario")
    manipulator = TiagoManipulator(sim, log=lambda _message: None)
    try:
        yield sim, manipulator
    finally:
        manipulator.close()


def test_ee_delta_moves_real_mujoco_tcp(tiago):
    sim, manipulator = tiago
    start, _ = manipulator.tcp()

    run(sim, manipulator.ee_delta([0.005, 0.0, 0.0], [0.0, 0.0, 0.0], 0.0))

    end, _ = manipulator.tcp()
    assert end[0] - start[0] > 0.003
    assert np.linalg.norm(end - (start + [0.005, 0.0, 0.0])) < 0.02


def test_ee_delta_maps_positive_gripper_to_close(tiago):
    sim, manipulator = tiago

    run(sim, manipulator.ee_delta([0.0, 0.0, 0.0], [0.0, 0.0, 0.0], 1.0))

    assert all(sim.data.ctrl[actuator] == pytest.approx(GRIPPER_CLOSED)
               for actuator in manipulator.grip_act)


def test_ee_delta_defense_in_depth_rejects_oversized_step(tiago):
    sim, manipulator = tiago

    with pytest.raises(ManipulationError, match="한계 초과"):
        run(sim, manipulator.ee_delta([0.051, 0.0, 0.0], [0.0, 0.0, 0.0], 0.0))
