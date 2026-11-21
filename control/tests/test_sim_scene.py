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
