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
    subprocess.run([sys.executable, str(SCRIPT), "--out", str(tmp_path), "--duration", "12", "--fps", "2"],
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
