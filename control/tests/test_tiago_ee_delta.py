import numpy as np
import pytest

from simulation import DiffDriveSim
from tiago_manipulation import ManipulationError, TiagoManipulator, run, scenario_a


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


def test_ee_delta_close_fails_when_no_object_is_in_grasp_range(tiago):
    sim, manipulator = tiago

    with pytest.raises(ManipulationError, match="잡지 못함"):
        run(sim, manipulator.ee_delta([0.0, 0.0, 0.0], [0.0, 0.0, 0.0], 1.0))


def test_ee_delta_defense_in_depth_rejects_oversized_step(tiago):
    sim, manipulator = tiago

    with pytest.raises(ManipulationError, match="한계 초과"):
        run(sim, manipulator.ee_delta([0.051, 0.0, 0.0], [0.0, 0.0, 0.0], 0.0))


def test_pick_and_place_moves_actual_box_to_station_b(tiago):
    sim, manipulator = tiago

    _start, lifted, place, final = scenario_a(sim, manipulator)

    assert lifted > 0.75
    assert np.linalg.norm(final[:2] - place[:2]) < 0.05
    assert final[2] == pytest.approx(0.64, abs=0.015)
