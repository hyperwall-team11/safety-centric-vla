import json
import math

import mujoco
import pytest

from scenario import WorkerScript
from simulation import DiffDriveSim
from twin import (WORKER_BODY_RADIUS_M, WORKER_MARGIN_M, TwinError, build_twin, build_twin_xml,
                  position_errors, state_from_backend, twin_positions, zone_ring)

KEEP_OUT = [[0.5, -0.6], [1.9, -0.6], [1.9, 0.6], [0.5, 0.6]]


def state(workers=(("worker-01", 2.0, 0.4),), robot=(0.0, 0.0)):
    return {
        "robots": [{"robot_id": "robot-01", "robot_type": "MOBILE", "frame_id": "world",
                    "position": [robot[0], robot[1], 0.0], "yaw_rad": 0.0}],
        "workers": {"observed_at": "2026-11-11T00:00:00Z",
                    "items": [{"worker_id": w, "frame_id": "world", "position": [x, y, 0.0]}
                              for w, x, y in workers]},
        "zones": [{"zone_id": "keep-out-1", "zone_type": "KEEP_OUT", "frame_id": "world",
                   "geometry": {"type": "polygon", "points": KEEP_OUT}}],
    }


def test_twin_places_robot_and_workers_from_state():
    model, data = build_twin(state(workers=(("worker-01", 2.0, 0.4), ("worker-02", -1.0, 1.5))))
    pos = twin_positions(model, data)
    assert pos["robot:robot-01"] == pytest.approx((0.0, 0.0))
    assert pos["worker:worker-01"] == pytest.approx((2.0, 0.4))
    assert pos["worker:worker-02"] == pytest.approx((-1.0, 1.5))


def test_worker_capsule_includes_cv_margin():
    model, _ = build_twin(state())
    assert model.geom("worker:worker-01").size[0] == pytest.approx(WORKER_BODY_RADIUS_M + WORKER_MARGIN_M)
    assert WORKER_MARGIN_M >= 0.20  # CV 위치 오차 0.15~0.20 m 이상


def test_robot_has_planar_joints_for_simulation():
    model, data = build_twin(state())
    data.qpos[model.joint("robot:robot-01:x").qposadr[0]] = 1.0
    mujoco.mj_forward(model, data)
    assert twin_positions(model, data)["robot:robot-01"] == pytest.approx((1.0, 0.0))


def test_robot_and_worker_contact_is_detected():
    model, data = build_twin(state(workers=(("worker-01", 0.5, 0.0),)))  # 0.3 + 0.45 > 0.5
    names = {(model.geom(c.geom1).name, model.geom(c.geom2).name) for c in data.contact[:data.ncon]}
    assert any("robot:robot-01" in pair and "worker:worker-01" in pair for pair in names)


def test_zone_does_not_collide_and_supports_geojson():
    xml = build_twin_xml(state())
    assert 'name="zone:keep-out-1"' in xml and 'contype="0"' in xml
    ring = zone_ring({"type": "Polygon", "coordinates": [KEEP_OUT + [KEEP_OUT[0]]]}, "z")
    assert ring == [tuple(p) for p in KEEP_OUT]


def test_missing_worker_snapshot_fails_closed():
    s = state()
    s["workers"] = None
    with pytest.raises(TwinError):
        build_twin(s)


def test_empty_worker_snapshot_is_allowed():
    model, data = build_twin(state(workers=()))
    assert not any(k.startswith("worker:") for k in twin_positions(model, data))


@pytest.mark.parametrize("change", [
    lambda s: s["robots"][0].update(frame_id="map"),
    lambda s: s["robots"][0].update(robot_type="DRONE"),
    lambda s: s["workers"]["items"][0].update(position=[float("nan"), 0, 0]),
    lambda s: s["zones"][0]["geometry"].update(points=[[0, 0], [1, 1]]),
    lambda s: s["workers"]["items"].append(dict(s["workers"]["items"][0])),
    lambda s: s.update(robots=[]),
])
def test_invalid_state_is_rejected(change):
    s = state()
    change(s)
    with pytest.raises(TwinError):
        build_twin(s)


def test_backend_objects_are_converted():
    robots = [{"robot_id": "robot-01", "robot_type": "MOBILE",
               "pose": {"frame_id": "world", "position": {"x": 1, "y": 2, "z": 0},
                        "orientation": {"x": 0, "y": 0, "z": math.sin(0.25), "w": math.cos(0.25)}}}]
    snapshot = {"observed_at": "2026-11-11T00:00:00Z", "workers": [
        {"worker_id": "worker-01", "pose": {"frame_id": "world", "position": {"x": 3, "y": 0, "z": 0}}}]}
    zones = [{"zone_id": "keep-out-1", "zone_type": "KEEP_OUT", "frame_id": "world",
              "geometry": {"type": "polygon", "points": KEEP_OUT}}]
    s = state_from_backend(robots, snapshot, zones)
    assert s["robots"][0]["yaw_rad"] == pytest.approx(0.5)
    assert s["workers"]["items"][0]["position"] == [3, 0, 0]
    json.dumps(s)
    assert state_from_backend(robots, None, zones)["workers"] is None


def test_errors_against_scenario_ground_truth():
    # MuJoCo ① 정답 위치에 CV 오차만큼 비켜 놓은 관측으로 트윈을 만들고 오차를 잰다.
    sim = DiffDriveSim("scenario")
    x, y, _ = WorkerScript().apply(sim.model, sim.data)
    model, data = build_twin(state(workers=(("worker-01", x + 0.15, y),)))
    report = position_errors(model, data, {"worker:worker-01": (x, y), "worker:worker-02": (0, 0)})
    assert report["errors_m"]["worker:worker-01"] == pytest.approx(0.15)
    assert report["missing"] == ["worker:worker-02"]
    assert report["extra"] == ["robot:robot-01"]
