"""발표 화면과 ROS 관측 기록. 실행은 기존 Backend 검증·발행 API에 위임한다."""
import argparse
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import rclpy
from geometry_msgs.msg import PoseStamped
from nav_msgs.msg import Odometry
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from std_msgs.msg import String
from safety_control.interfaces import ESTOP_QOS, ESTOP_TOPIC, state_topic

from warehouse import AMR, CROSSING_ENTRY, WALK_END_Y

CROSSING = "횡단진입구역"
DESTINATION = "검사스테이션"
HERE = Path(__file__).resolve().parent


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def request(base, path, payload=None):
    req = Request(base + path, data=None if payload is None else json.dumps(payload).encode(),
                  headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=5) as response:
            return json.load(response)
    except HTTPError as exc:
        raise ValueError(f"Backend {exc.code} {exc.read().decode()}") from exc


def zone(zone_id, kind, points, target=None):
    geometry = {"type": "polygon", "points": points}
    if target:
        geometry["navigation_target"] = dict(zip(("x", "y", "z"), target))
    return {"zone_id": zone_id, "zone_type": kind, "geometry": geometry, "frame_id": "world",
            "minimum_safety_distance": 0.8, "estop_distance": 0.6}


def register(base):
    x, y, _ = AMR.warehouse_dock
    for path, payload in (
        ("/robots", {"robot_id": AMR.robot_id, "robot_type": "MOBILE", "control_adapter": "amr_adapter",
                     "status": "IDLE", "pose": {"frame_id": "world", "position": {"x": x, "y": y, "z": 0}},
                     "velocity": {"x": 0, "y": 0, "z": 0},
                     "motion_limits": {"max_speed": 0.4, "max_acceleration": 0.5}}),
        ("/zones", zone(CROSSING, "KEEP_OUT", CROSSING_ENTRY)),
        ("/zones", zone("자재공급구역", "WORK", [(-4.8, -4.7), (-3.2, -4.7), (-3.2, -3.3), (-4.8, -3.3)])),
        ("/zones", zone(DESTINATION, "WORK", [(3.2, -4.7), (4.8, -4.7), (4.8, -3.3), (3.2, -3.3)],
                        (*AMR.cell_dock[:2], 0.0))),
    ):
        request(base, path, payload)


def calibration(source, output):
    meta = json.loads(source.read_text())
    from camera_calib import ROTATION_CONVENTION, normalize_camera

    camera = meta["cameras"]["cctv_2"]
    entry = {"sensor_id": "cctv_2", "topic": "/cctv_2/image_raw", "width": camera["width"],
             "height": camera["height"], "position_m": camera["pos"],
             "rotation_cam_to_world": [camera["mat"][i:i + 3] for i in range(0, 9, 3)],
             "rotation_convention": ROTATION_CONVENTION, "world_frame": "mujoco_z_up",
             "fovy_deg": camera["fovy"], "ground_plane_z_m": 0.0}
    normalize_camera(entry)
    output.write_text(json.dumps({"version": 1, "cameras": [entry]}))


class Telemetry(Node):
    def __init__(self, directory):
        super().__init__("warehouse_presentation")
        self.lock = threading.Lock()
        self.trace = (directory / "telemetry.jsonl").open("w", buffering=1)
        self.events = deque(maxlen=80)
        self.odom = None
        self.worker = None
        self.adapter = None
        self.estop = False
        self.was_moving = False
        self.confirmed_stop = False
        self.create_subscription(String, ESTOP_TOPIC, self.on_estop, ESTOP_QOS)
        self.create_subscription(String, state_topic(AMR.robot_id), self.on_adapter, 10)
        self.create_subscription(Odometry, "/amr_01/odom", self.on_odom, 10)
        self.create_subscription(PoseStamped, "/sim/worker_pose", self.on_worker, 10)

    def record(self, kind, payload, event=False):
        row = {"kind": kind, "at": utc_now(), **payload}
        self.trace.write(json.dumps(row, ensure_ascii=False) + "\n")
        if event:
            self.events.append(row)

    def on_estop(self, msg):
        data = json.loads(msg.data)
        with self.lock:
            was_active = self.estop
            self.estop = data["active"]
            if self.estop or was_active:
                self.record("estop" if self.estop else "release", {"command": data}, event=True)

    def on_odom(self, msg):
        stamp = msg.header.stamp
        row = {"x": msg.pose.pose.position.x, "y": msg.pose.pose.position.y,
               "speed": msg.twist.twist.linear.x, "angular": msg.twist.twist.angular.z,
               "ros_time": stamp.sec + stamp.nanosec / 1e9}
        with self.lock:
            self.odom = row
            self.record("odom", row)
            moving = abs(row["speed"]) > 0.01 or abs(row["angular"]) > 0.01
            if moving:
                self.was_moving = True
            self.check_stop()

    def on_adapter(self, msg):
        data = json.loads(msg.data)
        with self.lock:
            if self.adapter != data.get("status"):
                self.adapter = data.get("status")
                self.record("adapter", {"status": self.adapter}, event=True)
            self.check_stop()

    def check_stop(self):
        if (not self.confirmed_stop and self.was_moving and self.estop and self.adapter == "ESTOP"
                and self.odom is not None and abs(self.odom["speed"]) <= 0.01
                and abs(self.odom["angular"]) <= 0.01):
            self.confirmed_stop = True
            self.record("stopped", self.odom, event=True)

    def on_worker(self, msg):
        with self.lock:
            self.worker = (msg.pose.position.y, time.monotonic())

    def snapshot(self):
        with self.lock:
            worker_clear = (self.worker is not None and time.monotonic() - self.worker[1] < 1.0
                            and self.worker[0] <= WALK_END_Y + 0.05)
            return {"odom": self.odom, "adapter": self.adapter, "events": list(self.events),
                    "worker_clear": worker_clear}


def detection_records(path):
    try:
        with path.open("rb") as file:
            file.seek(0, 2)
            position = max(0, file.tell() - 200_000)
            file.seek(position)
            lines = file.read().splitlines()
    except FileNotFoundError:
        return []
    if position:
        lines = lines[1:]
    records = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records



def fresh_detection(records):
    return bool(records and (datetime.now(timezone.utc) -
                datetime.fromisoformat(records[-1]["received_at_utc"])).total_seconds() < 2.0)

def make_handler(args, telemetry):
    command_lock = threading.Lock()
    first_detection_at = None

    class Handler(BaseHTTPRequestHandler):
        def send_content(self, body, content_type, status=200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, data, status=200):
            self.send_content(json.dumps(data, ensure_ascii=False).encode(), "application/json", status)

        def do_GET(self):
            nonlocal first_detection_at
            path = self.path.split("?", 1)[0]
            if path == "/":
                return self.send_content((HERE / "warehouse_demo.html").read_bytes(), "text/html; charset=utf-8")
            if path == "/detection.jpg":
                try:
                    return self.send_content((args.directory / "detection.jpg").read_bytes(), "image/jpeg")
                except FileNotFoundError:
                    return self.send_json({"error": "검출 영상 준비 중"}, 404)
            if path != "/status":
                return self.send_json({"error": "경로 없음"}, 404)
            try:
                records = detection_records(args.directory / "detections.jsonl")
                state = telemetry.snapshot()
                camera_state = request(args.video, "/cameras")
                fresh_workers = False
                if fresh_detection(records):
                    try:
                        worker_snapshot = request(args.backend, "/workers/snapshot")
                        age = (datetime.now(timezone.utc) -
                               datetime.fromisoformat(worker_snapshot["observed_at"])).total_seconds()
                        fresh_workers = 0 <= age < 1.0
                    except ValueError:
                        pass
                state["ready"] = (fresh_workers and state["adapter"] == "IDLE" and
                                  all(any(camera["sensor_id"] == sensor and camera["last_frame_at"] is not None
                                          and time.time() - camera["last_frame_at"] < 2 for camera in camera_state)
                                      for sensor in ("cctv_1", "cctv_2", "amr_camera")))
                if first_detection_at is None:
                    first_detection_at = next((r["processed_at_utc"] for r in records if r.get("people")), None)
                state["first_detection_at"] = first_detection_at
                state["detection"] = records[-1] if records else None
                state["hazards"] = request(args.backend, "/hazards")
                return self.send_json(state)
            except (ValueError, OSError) as exc:
                return self.send_json({"error": str(exc)}, 503)

        def do_POST(self):
            local_origins = {"http://127.0.0.1:5181", "http://localhost:5181"}
            if (self.headers.get("Origin") not in local_origins | {None}
                    or self.headers.get_content_type() != "application/json"):
                return self.send_json({"error": "발표 화면에서 JSON 요청을 보내세요."}, 403)
            if not command_lock.acquire(blocking=False):
                return self.send_json({"error": "이전 요청 처리 중"}, 409)
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 4096:
                    raise ValueError("요청 크기가 올바르지 않습니다.")
                payload = json.loads(self.rfile.read(size))
                if self.path == "/command":
                    if telemetry.snapshot()["adapter"] != "IDLE" or not fresh_detection(detection_records(args.directory / "detections.jsonl")):
                        raise ValueError("어댑터 또는 검출기가 준비되지 않았습니다.")
                    data = {"instruction": payload["instruction"], "target_robot_id": AMR.robot_id, "frame_id": "world"}
                    parsed = request(args.backend, "/task-orders/preview", data)
                    if parsed["action_type"] != "MOVE_TO" or parsed["destination"].get("zone_id") != DESTINATION:
                        raise ValueError("발표 명령은 적재 AMR의 검사 스테이션 이동을 지원합니다.")
                    task = request(args.backend, "/task-orders/from-natural-language", data)
                    prefix = f"/task-orders/{task['task_id']}/action-proposals"
                    proposal = request(args.backend, prefix, {})
                    action = f"/action-proposals/{proposal['proposal_id']}"
                    evaluated = request(args.backend, action + "/evaluate", {})
                    executed = request(args.backend, action + "/execute", {}) if evaluated["status"] == "APPROVED" else None
                    logs = request(args.backend, "/execution-logs")
                    rules = next((log["rule_results"] for log in reversed(logs)
                                  if log["proposal_id"] == proposal["proposal_id"]), [])
                    return self.send_json({"parsed": parsed, "evaluated": evaluated,
                                           "executed": executed, "rules": rules})
                if self.path == "/resolve":
                    if not telemetry.snapshot()["worker_clear"]:
                        raise ValueError("작업자가 횡단을 끝낼 때까지 기다리세요.")
                    hazards = request(args.backend, "/hazards")
                    resolved = [request(args.backend, f"/hazards/{h['hazard_id']}/resolve", {"resolved_by": "OPERATOR"})
                                for h in hazards if h["status"] == "OPEN" and h["zone_id"] == CROSSING]
                    return self.send_json({"resolved": resolved})
                return self.send_json({"error": "경로 없음"}, 404)
            except (ValueError, OSError, KeyError) as exc:
                return self.send_json({"error": str(exc)}, 422)
            finally:
                command_lock.release()

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("register", "calibration", "serve"))
    parser.add_argument("--backend", default="http://127.0.0.1:8017")
    parser.add_argument("--video", default="http://127.0.0.1:8081")
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "register":
        register(args.backend)
    elif args.mode == "calibration":
        calibration(args.directory / "data/scene.json", args.directory / "calibration.json")
    else:
        rclpy.init()
        telemetry = Telemetry(args.directory)
        def spin():
            try:
                rclpy.spin(telemetry)
            except ExternalShutdownException:
                pass

        thread = threading.Thread(target=spin, daemon=True)
        thread.start()
        try:
            with ThreadingHTTPServer(("127.0.0.1", 5181), make_handler(args, telemetry)) as server:
                server.daemon_threads = True
                server.timeout = 0.25
                while rclpy.ok():
                    server.handle_request()
        finally:
            rclpy.try_shutdown()
            thread.join()
            telemetry.destroy_node()
            telemetry.trace.close()


if __name__ == "__main__":
    main()
