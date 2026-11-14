"""브리지 ↔ Three.js 실시간 CCTV 렌더 연결 (이슈 #55, 제어 계약 3장 가상 CCTV 영상).

브리지가 MuJoCo 상태를 프레임으로 묶어 두면(`set_frame`), 헤드리스 Chromium의 render.html(`?ros`)이
로컬 HTTP로 가져가 렌더하고 RGB를 돌려준다. 돌려받은 영상은 그 프레임의 시뮬레이션 시각을 붙여
`/cctv_{n}/image_raw`(rgb8, Best Effort)로 발행한다.

    GET  /frame?after=N            N보다 새 프레임(float32 바이트). 없으면 최대 1초 기다린 뒤 204. 헤더 X-Seq, X-Sim-Time
    POST /image?cam=cctv_1&t=12.3  JPEG(Content-Type: image/jpeg) 또는 rgb8 바이트(width*height*3) → rgb8로 발행

HTTP 요청은 서버 스레드에서 처리한다. MuJoCo 데이터는 건드리지 않고, 브리지 스레드가 만든 바이트만 읽는다.
"""
from __future__ import annotations

import io
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

MAX_FRAME_BYTES = 16_000_000
FRAME_WAIT_S = 1.0  # 새 프레임을 기다리는 최대 시간. 렌더러가 빈 응답을 계속 받아 가며 브리지 스레드를 막지 않게 한다


def stamp_parts(sim_time: float) -> tuple[int, int]:
    """시뮬레이션 시각(s) → (sec, nanosec). header.stamp는 시뮬레이션 시각을 쓴다(제어 계약 3장)."""
    if not sim_time >= 0:
        raise ValueError(f"sim time must be >= 0, got {sim_time!r}")
    total_ns = int(round(sim_time * 1e9))
    return total_ns // 1_000_000_000, total_ns % 1_000_000_000


class LiveFrames:
    """최신 프레임 보관과 렌더 결과 수신. publish(cam, sim_time, rgb_bytes)는 브리지가 넘긴다."""

    def __init__(self, cameras, width, height, publish):
        self.cameras = set(cameras)
        self.width, self.height = width, height
        self.publish = publish
        self._lock = threading.Condition()
        self._frame = None  # (seq, sim_time, bytes)
        self._seq = 0
        self.received = {c: 0 for c in self.cameras}

    def set_frame(self, data: bytes, sim_time: float) -> None:
        with self._lock:
            self._seq += 1
            self._frame = (self._seq, sim_time, data)
            self._lock.notify_all()

    def latest(self, after: int = -1, timeout: float = 0.0):
        """seq가 after보다 큰 최신 프레임. timeout 동안 기다려도 없으면 None."""
        with self._lock:
            self._lock.wait_for(lambda: self._frame is not None and self._frame[0] > after, timeout)
            frame = self._frame
            return frame if frame is not None and frame[0] > after else None

    def accept_image(self, cam: str, sim_time: float, data: bytes, jpeg: bool = False) -> None:
        if cam not in self.cameras:
            raise ValueError(f"unknown camera {cam!r}")
        if jpeg:
            # 렌더러는 CCTV 압축 효과를 겸해 JPEG(품질 0.72)로 보낸다. 원시 RGB보다 전송량이 약 20분의 1이다
            from PIL import Image
            try:
                with Image.open(io.BytesIO(data)) as img:
                    if img.size != (self.width, self.height):
                        raise ValueError(f"image must be {self.width}x{self.height}, got {img.size[0]}x{img.size[1]}")
                    data = img.convert("RGB").tobytes()
            except OSError as exc:
                raise ValueError(f"bad JPEG: {exc}") from exc
        expected = self.width * self.height * 3
        if len(data) != expected:
            raise ValueError(f"image must be {expected} bytes (rgb8 {self.width}x{self.height}), got {len(data)}")
        stamp_parts(sim_time)
        self.publish(cam, sim_time, data)
        self.received[cam] += 1


def make_handler(live: LiveFrames):
    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status, body=b"", headers=()):
            self.send_response(status)
            for k, v in headers:
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urlparse(self.path)
            if url.path != "/frame":
                self._reply(404)
                return
            try:
                after = int(parse_qs(url.query).get("after", ["-1"])[0])
            except ValueError:
                self._reply(400, b"after must be an integer")
                return
            frame = live.latest(after, FRAME_WAIT_S)
            if frame is None:
                self._reply(204)
                return
            seq, sim_time, data = frame
            self._reply(200, data, [("Content-Type", "application/octet-stream"), ("X-Seq", str(seq)),
                                    ("X-Sim-Time", repr(sim_time)), ("Cache-Control", "no-store")])

        def do_POST(self):
            url = urlparse(self.path)
            if url.path != "/image":
                self._reply(404)
                return
            q = parse_qs(url.query)
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= MAX_FRAME_BYTES:
                self._reply(400, b"bad length")
                return
            body = self.rfile.read(length)
            try:
                live.accept_image(q.get("cam", [""])[0], float(q.get("t", ["nan"])[0]), body,
                                  jpeg=self.headers.get("Content-Type", "") == "image/jpeg")
            except ValueError as exc:
                self._reply(400, str(exc).encode())
                return
            self._reply(204)

        def log_message(self, fmt, *args):
            pass

    return Handler


def start_server(live: LiveFrames, host: str = "127.0.0.1", port: int = 8765):
    server = ThreadingHTTPServer((host, port), make_handler(live))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
