"""카메라 토픽을 웹 콘솔이 볼 수 있는 MJPEG(HTTP) 스트림으로 내보낸다.

    ros2 run mujoco_bridge cctv_stream --ros-args -p port:=8081

- GET /cameras              → [{"sensor_id", "topic", "last_frame_at", "width", "height"}]
- GET /stream/<sensor_id>   → multipart/x-mixed-replace MJPEG (브라우저 <img>로 재생)
- GET /snapshot/<sensor_id> → 최신 프레임 JPEG 한 장

관측용 출구일 뿐이며 로봇 상태나 명령을 바꾸지 않는다. 영상은 bridge_node가 발행한
`/cctv_{n}/image_raw`(rgb8)를 그대로 받아 JPEG로만 바꾼다.
"""
from __future__ import annotations

import io
import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image as PILImage

# bridge_node.py / README 토픽 표 기준
DEFAULT_CAMERAS = {
    "cctv_1": "/cctv_1/image_raw",
    "cctv_2": "/cctv_2/image_raw",
    "head_camera": "/robot/head_camera/image_raw",
}
BOUNDARY = "frame"
# 웹 시청용. 15Hz 원본을 그대로 보내되, 클라이언트가 느리면 최신 프레임만 보낸다.
MAX_STREAM_HZ = 15.0
JPEG_QUALITY = 80


def encode_jpeg(data: bytes, width: int, height: int, encoding: str, quality: int = JPEG_QUALITY) -> bytes:
    """sensor_msgs/Image 원시 바이트 → JPEG. rgb8·bgr8·mono8만 지원한다."""
    if encoding == "rgb8":
        img = PILImage.frombuffer("RGB", (width, height), data, "raw", "RGB", 0, 1)
    elif encoding == "bgr8":
        img = PILImage.frombuffer("RGB", (width, height), data, "raw", "BGR", 0, 1)
    elif encoding == "mono8":
        img = PILImage.frombuffer("L", (width, height), data, "raw", "L", 0, 1)
    else:
        raise ValueError(f"지원하지 않는 encoding: {encoding}")
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=quality)
    return out.getvalue()


@dataclass
class Frame:
    jpeg: bytes
    width: int
    height: int
    received_at: float  # time.time()
    seq: int


@dataclass
class FrameStore:
    """카메라별 최신 JPEG 한 장만 보관한다. ROS 콜백 스레드와 HTTP 스레드가 함께 쓴다."""

    topics: dict[str, str]
    _frames: dict[str, Frame] = field(default_factory=dict)
    _cond: threading.Condition = field(default_factory=threading.Condition)

    def put(self, sensor_id: str, jpeg: bytes, width: int, height: int) -> None:
        with self._cond:
            prev = self._frames.get(sensor_id)
            self._frames[sensor_id] = Frame(jpeg, width, height, time.time(), (prev.seq + 1) if prev else 1)
            self._cond.notify_all()

    def latest(self, sensor_id: str) -> Frame | None:
        with self._cond:
            return self._frames.get(sensor_id)

    def wait_newer(self, sensor_id: str, seq: int, timeout: float) -> Frame | None:
        with self._cond:
            self._cond.wait_for(lambda: (f := self._frames.get(sensor_id)) is not None and f.seq > seq, timeout)
            frame = self._frames.get(sensor_id)
            return frame if frame is not None and frame.seq > seq else None

    def describe(self) -> list[dict]:
        with self._cond:
            return [
                {
                    "sensor_id": sid,
                    "topic": topic,
                    "last_frame_at": f.received_at if (f := self._frames.get(sid)) else None,
                    "width": f.width if f else None,
                    "height": f.height if f else None,
                }
                for sid, topic in self.topics.items()
            ]


def make_handler(store: FrameStore, stop: threading.Event):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):  # 프레임마다 로그가 쌓이지 않게 한다
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802 (http.server 규칙)
            parts = [p for p in self.path.split("?")[0].split("/") if p]
            if parts == ["cameras"]:
                return self._send(200, json.dumps(store.describe()).encode(), "application/json")
            if len(parts) == 2 and parts[1] in store.topics:
                kind, sensor_id = parts
                if kind == "snapshot":
                    frame = store.latest(sensor_id)
                    if frame is None:
                        return self._send(404, b'{"detail":"no frame yet"}', "application/json")
                    return self._send(200, frame.jpeg, "image/jpeg")
                if kind == "stream":
                    return self._stream(sensor_id)
            return self._send(404, b'{"detail":"not found"}', "application/json")

        def _stream(self, sensor_id: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY}")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            seq, min_gap = 0, 1.0 / MAX_STREAM_HZ
            try:
                while not stop.is_set():
                    frame = store.wait_newer(sensor_id, seq, timeout=1.0)
                    if frame is None:
                        continue
                    seq = frame.seq
                    self.wfile.write(
                        f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(frame.jpeg)}\r\n\r\n".encode()
                        + frame.jpeg + b"\r\n"
                    )
                    self.wfile.flush()
                    time.sleep(min_gap)
            except (BrokenPipeError, ConnectionResetError):
                pass  # 브라우저가 화면을 떠남

    return Handler


def serve(store: FrameStore, host: str, port: int, stop: threading.Event) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(store, stop))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main():
    import rclpy
    from rcl_interfaces.msg import ParameterDescriptor
    from rclpy.qos import qos_profile_sensor_data
    from sensor_msgs.msg import Image

    rclpy.init()
    node = rclpy.create_node("cctv_stream")
    dyn = ParameterDescriptor(dynamic_typing=True)
    host = str(node.declare_parameter("host", "127.0.0.1", dyn).value)
    port = int(node.declare_parameter("port", 8081, dyn).value)

    store = FrameStore(dict(DEFAULT_CAMERAS))

    def on_image(sensor_id: str, msg: Image) -> None:
        try:
            jpeg = encode_jpeg(bytes(msg.data), msg.width, msg.height, msg.encoding)
        except ValueError as exc:
            node.get_logger().warn(f"{sensor_id}: {exc}", throttle_duration_sec=10.0)
            return
        store.put(sensor_id, jpeg, msg.width, msg.height)

    for sensor_id, topic in DEFAULT_CAMERAS.items():
        node.create_subscription(Image, topic, lambda m, s=sensor_id: on_image(s, m), qos_profile_sensor_data)

    stop = threading.Event()
    server = serve(store, host, port, stop)
    node.get_logger().info(f"CCTV 스트림: http://{host}:{port}/stream/<sensor_id> ({', '.join(DEFAULT_CAMERAS)})")
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.shutdown()
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
