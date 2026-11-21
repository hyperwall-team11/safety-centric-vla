"""물류창고 교차로 장면: 기존 코드 호환 이름, 초기 배치, CCTV 가림 구도(관측 공백 시연)를 확인한다."""
import json
import subprocess
import sys
from pathlib import Path

import mujoco
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCENE = ROOT / "mujoco" / "models" / "warehouse_scene.xml"
EXPORT = ROOT / "scripts" / "threejs_cctv" / "export_scene.py"


@pytest.fixture(scope="module")
def sim():
    m = mujoco.MjModel.from_xml_path(str(SCENE))
    d = mujoco.MjData(m)
    mujoco.mj_resetDataKeyframe(m, d, m.key("home").id)
    mujoco.mj_forward(m, d)
    return m, d


def visible_fraction(m, d, camera, x, y):
    """작업자 높이(0.2~1.6 m) 표본점 중 카메라에서 정적 형상에 가려지지 않는 비율."""
    cam = d.cam_xpos[m.camera(camera).id]
    seen = 0
    zs = np.linspace(0.2, 1.6, 8)
    for z in zs:
        v = np.array([x, y, z]) - cam
        dist = np.linalg.norm(v)
        hit = mujoco.mj_ray(m, d, cam, v / dist, None, 1, -1, np.array([-1], np.int32))
        seen += hit < 0 or hit > dist - 0.05
    return seen / len(zs)


def test_scene_keeps_names_used_by_existing_code(sim):
    m, _ = sim
    for geom in ("floor", "keep_out_zone", "zone_a_marking", "zone_b_marking"):
        m.geom(geom)
    for body in ("worker", "worker_2", "worker_3", "part_box", "work_area_center"):
        m.body(body)
    assert {m.camera(i).name for i in range(m.ncam) if m.camera(i).name.startswith("cctv")} == {"cctv_1", "cctv_2"}
    assert sum(m.geom(i).name.startswith("prop_rack_") for i in range(m.ngeom)) == 12


def test_robot_starts_at_dock_and_box_on_table_a(sim):
    m, d = sim
    assert np.allclose(d.qpos[:2], [12.5, 0.0])
    assert np.allclose(d.qpos[-7:-4], [10.6, 2.3, 0.64])


@pytest.mark.parametrize("x, y, cctv_1, cctv_2", [
    (0.0, 0.0, 1.0, 1.0),     # 십자 교차로: 두 대 모두
    (6.0, 0.0, 1.0, 0.0),     # x통로: cctv_1만
    (10.0, 0.0, 1.0, 0.0),    # T자 교차로: cctv_1만
    (0.0, 6.0, 0.0, 1.0),     # y통로 북쪽: cctv_2만
    (4.0, 3.25, 0.0, 0.0),    # 보조 통로 안: 관측 공백
    (2.0, -3.25, 0.0, 0.0),   # 교차로 옆 보조 통로(돌발 진입 출발점): 관측 공백
])
def test_cctv_occlusion_layout(sim, x, y, cctv_1, cctv_2):
    m, d = sim
    assert visible_fraction(m, d, "cctv_1", x, y) == cctv_1
    assert visible_fraction(m, d, "cctv_2", x, y) == cctv_2


def static_clearance(m, d, x, y):
    """(x, y)에서 랙·설비·작업대 box까지 xy 평면 최소 거리 (음수면 안쪽)."""
    gaps = [np.max(np.abs(np.array([x, y]) - d.geom_xpos[i][:2]) - m.geom_size[i][:2])
            for i in range(m.ngeom)
            if m.geom(i).name.startswith(("prop_", "table_")) and m.geom_type[i] == mujoco.mjtGeom.mjGEOM_BOX]
    return min(gaps)


def warehouse_workers():
    sys.path.insert(0, str(EXPORT.parent))
    sys.path.insert(0, str(ROOT / "mujoco"))
    import export_scene
    from scenario import WorkerScript
    return [WorkerScript(waypoints=w["waypoints"], speed=w["speed"])
            for w in export_scene.SCENE_WORKERS[SCENE.name]]


def test_worker_routes_stay_clear_of_racks(sim):
    m, d = sim
    for script in warehouse_workers():
        assert min(static_clearance(m, d, *script.position(t)[:2]) for t in np.arange(0, 60, 0.1)) >= 0.25


def test_worker_routes_cover_observation_gap_and_reappearance(sim):
    # 작업자 1: 보조 통로(두 대 모두 가려짐)에서 출발해 십자 교차로(두 대 모두 보임)로 진입
    # 작업자 2: 보조 통로에 들어가 가려졌다가 다시 보인다
    m, d = sim
    w1, w2, _ = warehouse_workers()

    def both(script, t):
        x, y, _ = script.position(t)
        return visible_fraction(m, d, "cctv_1", x, y), visible_fraction(m, d, "cctv_2", x, y)

    w1_seen = [both(w1, t) for t in np.arange(0, 30, 0.5)]
    assert w1_seen[0] == (0.0, 0.0) and (1.0, 1.0) in w1_seen
    w2_any = [max(both(w2, t)) > 0 for t in np.arange(0, 30, 0.5)]
    first_gap = w2_any.index(False)
    assert any(w2_any[first_gap:])


def test_threejs_export_accepts_warehouse_scene(tmp_path):
    subprocess.run([sys.executable, str(EXPORT), "--out", str(tmp_path), "--duration", "6", "--fps", "2",
                    "--scene", SCENE.name], check=True)
    meta = json.loads((tmp_path / "scene.json").read_text())
    assert set(meta["cameras"]) == {"cctv_1", "cctv_2", "head_camera"}
    assert meta["cameras"]["head_camera"]["moving"] and not meta["cameras"]["cctv_1"]["moving"]
    assert meta["scene"] == SCENE.name and len(meta["workers"]) == 3
    # 로봇 탑재 카메라: 프레임마다 자세와 로봇 자기 위치. 로봇은 도크에서 서쪽(-x)으로 주행한다
    track = meta["camera_tracks"]["head_camera"]
    assert len(track) == meta["frames"] == 12
    xs = [f["base"][0] for f in track]
    assert xs[0] > 12.4 and xs[-1] < xs[0] - 1.5 and all(b <= a + 1e-3 for a, b in zip(xs, xs[1:]))  # 출발 전 바퀴 접촉 흔들림 < 1 mm
    assert all(abs(f["pos"][0] - f["base"][0]) < 0.5 and 1.0 < f["pos"][2] < 1.5 for f in track)
