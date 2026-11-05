"""Three.js CCTV 내보내기: 정답 좌표·금지구역 여부와 프레임 데이터 크기를 확인한다."""
import csv
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "threejs_cctv" / "export_scene.py"


def test_export_writes_truth_and_frames(tmp_path):
    subprocess.run([sys.executable, str(SCRIPT), "--out", str(tmp_path), "--duration", "12", "--fps", "2"],
                   check=True)
    meta = json.loads((tmp_path / "scene.json").read_text())
    rows = list(csv.DictReader(open(tmp_path / "truth.csv")))
    assert len(rows) == meta["frames"] == 24
    frame_len = meta["ngeom_export"] * 12 + meta["nskinvert"] * 3
    assert (tmp_path / "frames.bin").stat().st_size == meta["frames"] * frame_len * 4
    # 경로에 금지구역 밖·안이 모두 있다 (t=10.5 s는 금지구역 안 정지 구간)
    assert {r["in_keep_out"] for r in rows} == {"0", "1"}
    r = next(r for r in rows if r["sim_time"] == "10.500")
    assert r["in_keep_out"] == "1" and abs(float(r["worker_y"]) + 0.5) < 1e-3
    # 카메라는 MuJoCo 정의와 같다
    assert meta["cameras"]["cctv_1"]["fovy"] == 60.0
    assert np.allclose(meta["cameras"]["cctv_1"]["pos"], [1.2, -4.2, 3.2])
