"""Three.js CCTV 내보내기: 작업자별 정답 좌표·금지구역 여부, 프레임 데이터 크기, 스키닝 검증을 확인한다."""
import csv
import json
import subprocess
import sys
from pathlib import Path

import mujoco
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "threejs_cctv" / "export_scene.py"


def test_export_writes_truth_and_frames(tmp_path):
    # export_scene.py는 작업자 2·3 스키닝이 MuJoCo 결과와 5 mm 이상 다르면 실패한다
    subprocess.run([sys.executable, str(SCRIPT), "--out", str(tmp_path), "--duration", "12", "--fps", "2",
                    "--plant", "bare"],
                   check=True)
    meta = json.loads((tmp_path / "scene.json").read_text())
    rows = list(csv.DictReader(open(tmp_path / "truth.csv")))
    workers = [w["id"] for w in meta["workers"]]
    assert workers == ["worker-01", "worker-02", "worker-03"]
    assert len(rows) == meta["frames"] * len(workers) == 24 * 3
    frame_len = meta["ngeom_export"] * 12 + len(workers) * (meta["nskinvert"] * 3 + 7)
    assert (tmp_path / "frames.bin").stat().st_size == meta["frames"] * frame_len * 4
    w1 = [r for r in rows if r["worker_id"] == "worker-01"]
    assert {r["in_keep_out"] for r in w1} == {"0", "1"}
    r = next(r for r in w1 if r["sim_time"] == "10.500")
    assert r["in_keep_out"] == "1" and abs(float(r["y"]) + 0.5) < 1e-3
    assert meta["cameras"]["cctv_1"]["fovy"] == 60.0
    for w in meta["workers"]:
        assert (tmp_path / w["suit"]).exists()


def test_plant_scene_keeps_robot_state_size():
    m = mujoco.MjModel.from_xml_path(str(ROOT / "mujoco" / "models" / "plant_scene.xml"))
    assert m.nq == 36 and m.nskin == 7
    for name in ("keep_out_zone", "table_a_top", "table_b_top", "prop_forklift_1"):
        m.geom(name)
    for cam in ("cctv_1", "cctv_2"):
        assert m.cam_fovy[m.camera(cam).id] == 60.0


ARM_MODELS_PRESENT = all((ROOT / "mujoco" / "models" / d).is_dir() for d in (
    "universal_robots_ur5e", "universal_robots_ur10e", "kuka_iiwa_14", "franka_emika_panda"))


import pytest  # noqa: E402


@pytest.mark.skipif(not ARM_MODELS_PRESENT, reason="pixi run fetch-models 필요")
def test_plant_export_includes_arms_and_moving_amrs(tmp_path):
    sys.path.insert(0, str(SCRIPT.parent))
    import export_scene

    sim, motion = export_scene.build_sim("full")
    m, d = sim.model, sim.data
    meta, packer = export_scene.prepare(m, d, tmp_path)
    names = {g["name"] for g in meta["geoms"]}
    assert any(n.startswith("amr:amr-01/") for n in names) and any(n.startswith("pallet:amr-02/") for n in names)
    assert any(n.startswith("rack:arm-ur10e_") for n in names) and any(n.startswith("pedestal:") for n in names)
    arm_geoms = [g["id"] for g in meta["geoms"] if g["name"].startswith("arm-ur5e/")
                 or m.geom_bodyid[g["id"]] in {b for b in range(m.nbody) if m.body(b).name.startswith("arm-ur5e/")}]
    assert arm_geoms
    chassis = m.geom("amr:amr-01/chassis").id
    pallet = m.geom("pallet:amr-01/deck").id
    start = d.geom_xpos[[chassis, pallet, *arm_geoms]].copy()
    for _ in range(20 * 50):  # 20초
        before = sim.time
        sim.step(0.02)
        motion.step(d, sim.time, sim.time - before)
    mujoco.mj_forward(m, d)
    moved = np.linalg.norm(d.geom_xpos[[chassis, pallet, *arm_geoms]] - start, axis=1)
    assert moved[0] > 1.0 and moved[1] > 1.0  # AMR가 팔레트를 싣고 통로를 지났다
    assert moved[2:].max() > 0.05  # 팔이 움직였다
    frame, _ = packer.pack(m, d, sim.time)
    assert len(frame) == packer.frame_length(m) * 4
