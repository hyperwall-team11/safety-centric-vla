import io
import json
import threading
import urllib.request

import pytest
from PIL import Image

from mujoco_bridge.cctv_stream import BOUNDARY, FrameStore, encode_jpeg, serve


def rgb_frame(w=8, h=6, color=(255, 0, 0)):
    return bytes(color) * (w * h)


def test_encode_rgb8_and_bgr8_keep_colors():
    for encoding, raw, expected in [("rgb8", (255, 0, 0), (255, 0, 0)), ("bgr8", (255, 0, 0), (0, 0, 255))]:
        jpeg = encode_jpeg(rgb_frame(color=raw), 8, 6, encoding, quality=95)
        img = Image.open(io.BytesIO(jpeg))
        assert img.size == (8, 6)
        assert all(abs(a - b) < 20 for a, b in zip(img.convert("RGB").getpixel((4, 3)), expected))


def test_encode_rejects_unknown_encoding():
    with pytest.raises(ValueError):
        encode_jpeg(b"\0" * 16, 2, 2, "16UC1")


def test_store_keeps_latest_and_waits_for_newer():
    store = FrameStore({"cctv_1": "/cctv_1/image_raw"})
    assert store.latest("cctv_1") is None
    assert store.wait_newer("cctv_1", 0, timeout=0.01) is None
    store.put("cctv_1", b"a", 8, 6)
    store.put("cctv_1", b"b", 8, 6)
    frame = store.latest("cctv_1")
    assert (frame.jpeg, frame.seq) == (b"b", 2)
    assert store.wait_newer("cctv_1", 2, timeout=0.01) is None
    assert store.wait_newer("cctv_1", 1, timeout=0.01).jpeg == b"b"


@pytest.fixture
def server():
    store = FrameStore({"cctv_1": "/cctv_1/image_raw", "cctv_2": "/cctv_2/image_raw"})
    stop = threading.Event()
    srv = serve(store, "127.0.0.1", 0, stop)
    yield store, f"http://127.0.0.1:{srv.server_address[1]}"
    stop.set()
    srv.shutdown()


def test_http_cameras_snapshot_and_stream(server):
    store, base = server
    cams = json.loads(urllib.request.urlopen(f"{base}/cameras").read())
    assert [c["sensor_id"] for c in cams] == ["cctv_1", "cctv_2"]
    assert cams[0]["last_frame_at"] is None

    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(f"{base}/snapshot/cctv_1")
    assert err.value.code == 404
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(f"{base}/stream/unknown")

    jpeg = encode_jpeg(rgb_frame(), 8, 6, "rgb8")
    store.put("cctv_1", jpeg, 8, 6)
    assert urllib.request.urlopen(f"{base}/snapshot/cctv_1").read() == jpeg

    resp = urllib.request.urlopen(f"{base}/stream/cctv_1", timeout=3)
    assert resp.headers["Content-Type"] == f"multipart/x-mixed-replace; boundary={BOUNDARY}"
    head = resp.read(len(f"--{BOUNDARY}\r\n"))
    assert head == f"--{BOUNDARY}\r\n".encode()
    resp.close()
