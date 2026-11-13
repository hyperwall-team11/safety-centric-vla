"""Three.js 실시간 CCTV 연결(#55): 프레임 롱폴링, 영상 수신·검증, 시뮬레이션 시각 스탬프."""
import sys
import threading
import time
from pathlib import Path
from urllib import error, request

import pytest

from mujoco_bridge.threejs_live import LiveFrames, stamp_parts, start_server

W, H = 4, 3
ROOT = Path(__file__).resolve().parents[1]


def test_stamp_uses_sim_time():
    assert stamp_parts(0.0) == (0, 0)
    assert stamp_parts(12.345) == (12, 345_000_000)
    with pytest.raises(ValueError):
        stamp_parts(-1.0)
    with pytest.raises(ValueError):
        stamp_parts(float("nan"))


def test_accept_image_checks_camera_and_size():
    sent = []
    live = LiveFrames(["cctv_1"], W, H, lambda *a: sent.append(a))
    live.accept_image("cctv_1", 1.5, bytes(W * H * 3))
    assert sent == [("cctv_1", 1.5, bytes(W * H * 3))]
    with pytest.raises(ValueError, match="unknown camera"):
        live.accept_image("cctv_9", 1.5, bytes(W * H * 3))
    with pytest.raises(ValueError, match="bytes"):
        live.accept_image("cctv_1", 1.5, bytes(10))


def test_latest_waits_for_newer_frame():
    live = LiveFrames(["cctv_1"], W, H, lambda *a: None)
    assert live.latest(-1, 0.0) is None
    live.set_frame(b"a", 0.1)
    assert live.latest(-1)[:2] == (1, 0.1)
    assert live.latest(1, 0.05) is None  # 새 프레임 없음
    threading.Timer(0.05, live.set_frame, (b"b", 0.2)).start()
    t0 = time.monotonic()
    assert live.latest(1, 2.0) == (2, 0.2, b"b")
    assert time.monotonic() - t0 < 1.0


@pytest.fixture
def served():
    sent = []
    live = LiveFrames(["cctv_1", "cctv_2"], W, H, lambda *a: sent.append(a))
    server = start_server(live, port=0)
    yield f"http://127.0.0.1:{server.server_address[1]}", live, sent
    server.shutdown()
    server.server_close()


def call(url, data=None):
    req = request.Request(url, data=data, method="POST" if data is not None else "GET")
    try:
        with request.urlopen(req, timeout=5) as r:
            return r.status, dict(r.headers), r.read()
    except error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def test_http_frame_and_image_roundtrip(served):
    url, live, sent = served
    assert call(f"{url}/frame?after=-1")[0] == 204  # 1초 기다려도 프레임 없음
    live.set_frame(b"\x00\x01", 3.25)
    status, headers, body = call(f"{url}/frame?after=-1")
    assert (status, body, headers["X-Seq"], headers["X-Sim-Time"]) == (200, b"\x00\x01", "1", "3.25")
    assert call(f"{url}/image?cam=cctv_2&t=3.25", bytes(W * H * 3))[0] == 204
    assert sent == [("cctv_2", 3.25, bytes(W * H * 3))]
    assert call(f"{url}/image?cam=cctv_2&t=3.25", bytes(5))[0] == 400
    assert call(f"{url}/image?cam=cctv_2&t=-1", bytes(W * H * 3))[0] == 400
    assert call(f"{url}/frame?after=x")[0] == 400
    assert call(f"{url}/other")[0] == 404


def test_frame_packer_matches_render_layout(tmp_path):
    # 브리지가 쓰는 FramePacker 프레임 길이 = render.html이 기대하는 길이(ngeom*12 + 작업자*(nskinvert*3+7))
    sys.path.insert(0, str(ROOT / "scripts" / "threejs_cctv"))
    import export_scene
    from simulation import DiffDriveSim

    sim = DiffDriveSim("scenario")
    meta, packer = export_scene.prepare(sim.model, sim.data, tmp_path, False)
    frame, extra = packer.pack(sim.model, sim.data, 0.0)
    expected = meta["ngeom_export"] * 12 + len(meta["workers"]) * (meta["nskinvert"] * 3 + 7)
    assert len(frame) == expected * 4 == packer.frame_length(sim.model) * 4
    assert extra == [] and len(meta["workers"]) == 1  # 시나리오 장면은 작업자 1명
    assert set(meta["cameras"]) == {"cctv_1", "cctv_2"} and meta["cameras"]["cctv_1"]["fovy"] == 60.0
