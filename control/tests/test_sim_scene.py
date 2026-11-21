import json
import math
import threading
import urllib.request
from pathlib import Path

import pytest

from mujoco_bridge.cctv_stream import FrameStore, serve
from mujoco_bridge.sim_scene import SceneState, load_static_scene, yaw_from_quaternion

SCENE = Path(__file__).resolve().parents[1] / "mujoco" / "models" / "scenario_scene.xml"


@pytest.fixture(scope="module")
def static():
    return load_static_scene(str(SCENE))


def test_static_scene_matches_scenario_layout(static):
    zones = {z["zone_id"]: z for z in static["zones"]}
    assert zones["금지구역"]["zone_type"] == "KEEP_OUT"
    # keep_out_zone: pos (1.2, 0), size (0.7, 0.6)
    xs = [p[0] for p in zones["금지구역"]["points"]]
    ys = [p[1] for p in zones["금지구역"]["points"]]
    assert (min(xs), max(xs), min(ys), max(ys)) == pytest.approx((0.5, 1.9, -0.6, 0.6))
    assert {"A구역", "B구역"} <= set(zones)
    assert [t["name"] for t in static["tables"]] == ["작업대 A", "작업대 B"]


def test_cameras_point_at_work_cell(static):
    cams = {c["sensor_id"]: c for c in static["cameras"]}
    assert cams["cctv_1"]["position"][:2] == pytest.approx([1.2, -4.2])
    # cctv_1은 남쪽에서 작업 셀(+y)을 본다
    assert cams["cctv_1"]["yaw"] == pytest.approx(math.pi / 2, abs=0.05)
    for cam in cams.values():
        to_cell = math.atan2(0 - cam["position"][1], 1.2 - cam["position"][0])
        diff = (cam["yaw"] - to_cell + math.pi) % (2 * math.pi) - math.pi
        assert abs(diff) < cam["hfov"] / 2
    assert static["bounds"][1] == pytest.approx(-4.2)


def test_state_snapshot_and_http(static):
    state = SceneState(static)
    assert state.snapshot()["robots"] == []
    state.set_robot(0.5, -0.25, yaw_from_quaternion(0, 0, math.sin(0.5), math.cos(0.5)))
    state.set_worker("worker-01", 3.4, 1.8)

    stop = threading.Event()
    srv = serve(FrameStore({}), "127.0.0.1", 0, stop, state)
    try:
        body = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/scene").read())
    finally:
        stop.set()
        srv.shutdown()
    assert body["robots"][0]["position"] == [0.5, -0.25]
    assert body["robots"][0]["yaw"] == pytest.approx(1.0)
    assert body["workers"][0]["worker_id"] == "worker-01"
    assert {z["zone_id"] for z in body["zones"]} == {"금지구역", "A구역", "B구역"}


def test_scene_404_when_not_loaded():
    stop = threading.Event()
    srv = serve(FrameStore({}), "127.0.0.1", 0, stop)
    try:
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/scene")
        assert err.value.code == 404
    finally:
        stop.set()
        srv.shutdown()


PLANT = SCENE.with_name("plant_scene.xml")


def test_plant_props_on_map():
    """plant_scene의 주변 설비(prop_*)를 바닥 외곽으로 내보낸다."""
    props = {p["name"]: p for p in load_static_scene(str(PLANT))["props"]}
    fork = props["prop_forklift_1"]  # box pos (4.6, -3.0), size (1.3, 0.6)
    assert fork["label"] == "지게차"
    xs, ys = [p[0] for p in fork["points"]], [p[1] for p in fork["points"]]
    assert (min(xs), max(xs), min(ys), max(ys)) == pytest.approx((3.3, 5.9, -3.6, -2.4))
    tank = props["prop_tank_1"]  # 세운 원기둥 pos (-3.5, -4.5), r 1.1 → 12각형
    assert len(tank["points"]) == 12
    assert all(math.hypot(x + 3.5, y + 4.5) == pytest.approx(1.1, abs=1e-3) for x, y in tank["points"])
    drum = props["prop_drum_1"]  # 눕힌 원기둥(euler 90 0 0): 길이 1.2 × 지름 1.6 사각형
    xs, ys = [p[0] for p in drum["points"]], [p[1] for p in drum["points"]]
    assert sorted([max(xs) - min(xs), max(ys) - min(ys)]) == pytest.approx([1.2, 1.6])


def test_scenario_has_no_props(static):
    assert static["props"] == []
