"""Build backend Sensor heartbeats and Worker snapshots from CCTV detections, without ROS.

The backend observation-gap rule needs an ONLINE Sensor covering the destination Zone with a
frame under 2 s old, and the safety-distance rule needs a Worker snapshot under 2 s old
(docs/backend-api-spec-v0.2.md §3.8). Both are sent from a background thread so a slow
backend never delays inference.
"""

import math
import threading
from datetime import datetime, timezone

from hazard_reporter import point_in_ring


# 스키마 v1.1 Sensor.last_frame_at: 2초 무수신 시 DEGRADED.
SENSOR_FRAME_TIMEOUT_SECONDS = 2.0
# 백엔드 최신성 기준(2초)보다 충분히 짧게 센서·작업자 상태를 보낸다.
FEED_INTERVAL_SECONDS = 0.5
# 스냅샷에 넣을 카메라 프레임의 최대 나이. 오래된 카메라의 작업자 위치는 쓰지 않는다.
WORKER_FRAME_MAX_AGE_SECONDS = 1.0
# 다른 카메라가 이 거리 안에서 본 작업자는 한 명으로 합친다(바닥 투영 오차 0.15~0.20m 측정값의 2배 이상).
WORKER_MERGE_DISTANCE_M = 0.5
# Florence-2 <OD>는 점수가 없어, 신뢰도 대신 카메라 최근 프레임 중 사람을 본 비율을 쓴다.
SEEN_WINDOW_FRAMES = 5


def rotation_to_quaternion(rotation):
    """Unit quaternion {x, y, z, w} for a 3x3 rotation matrix."""
    (m00, m01, m02), (m10, m11, m12), (m20, m21, m22) = rotation
    trace = m00 + m11 + m22
    if trace > 0:
        s = 2 * math.sqrt(trace + 1)
        w, x, y, z = s / 4, (m21 - m12) / s, (m02 - m20) / s, (m10 - m01) / s
    elif m00 > m11 and m00 > m22:
        s = 2 * math.sqrt(1 + m00 - m11 - m22)
        w, x, y, z = (m21 - m12) / s, s / 4, (m01 + m10) / s, (m02 + m20) / s
    elif m11 > m22:
        s = 2 * math.sqrt(1 + m11 - m00 - m22)
        w, x, y, z = (m02 - m20) / s, (m01 + m10) / s, s / 4, (m12 + m21) / s
    else:
        s = 2 * math.sqrt(1 + m22 - m00 - m11)
        w, x, y, z = (m10 - m01) / s, (m02 + m20) / s, (m12 + m21) / s, s / 4
    return {"x": x, "y": y, "z": z, "w": w}


def sensor_payload(sensor_id, topic, camera, covered_zone_ids, last_frame_at, now, offline=False):
    """Full Sensor object for ``PUT /sensors/{sensor_id}``; DEGRADED once frames stop."""
    if offline:
        status = "OFFLINE"
    elif last_frame_at is None or (now - last_frame_at).total_seconds() >= SENSOR_FRAME_TIMEOUT_SECONDS:
        status = "DEGRADED"
    else:
        status = "ONLINE"
    x, y, z = camera["position_m"]
    return {
        "sensor_id": sensor_id,
        "sensor_type": "CCTV",
        "source_uri": f"ros2://{topic}",
        "frame_id": f"camera_{sensor_id}",
        "pose": {"frame_id": "world", "position": {"x": x, "y": y, "z": z},
                 "orientation": rotation_to_quaternion(camera["rotation_cam_to_world"])},
        "status": status,
        "mounted_robot_id": None,
        "covered_zone_ids": list(covered_zone_ids),
        "last_frame_at": None if last_frame_at is None else last_frame_at.isoformat(),
        "calibrated": True,
    }


def zone_rings(zones):
    """(zone_id, ring) for every world-frame single-ring Polygon Zone; other zones are skipped."""
    rings = []
    for zone in zones:
        geometry = zone.get("geometry") or {}
        coordinates = geometry.get("coordinates")
        if (zone.get("frame_id") != "world" or geometry.get("type") != "Polygon"
                or not isinstance(coordinates, list) or len(coordinates) != 1):
            continue
        try:
            ring = [(float(p[0]), float(p[1])) for p in coordinates[0]]
        except (TypeError, ValueError, IndexError):
            continue
        if len(ring) >= 4 and ring[0] == ring[-1]:
            rings.append((zone["zone_id"], ring))
    return rings


class WorkerFusion:
    """Keep each camera's latest worker positions and merge them into one snapshot."""

    def __init__(self, zones, keep_out_ids=(), max_age=WORKER_FRAME_MAX_AGE_SECONDS,
                 merge_distance=WORKER_MERGE_DISTANCE_M, window=SEEN_WINDOW_FRAMES):
        self.rings = zone_rings(zones)
        self.keep_out_ids = set(keep_out_ids)
        self.max_age = max_age
        self.merge_distance = merge_distance
        self.window = window
        self.lock = threading.Lock()
        self.latest = {}
        self.seen = {}
        self.sent_at = None

    def update(self, sensor_id, frame_at, positions):
        """Record one processed camera frame (UTC time and floor positions of detected people)."""
        with self.lock:
            history = self.seen.setdefault(sensor_id, [])
            history.append(bool(positions))
            del history[:-self.window]
            self.latest[sensor_id] = (frame_at, [dict(p) for p in positions])

    def last_frame_at(self, sensor_id):
        with self.lock:
            entry = self.latest.get(sensor_id)
            return None if entry is None else entry[0]

    def _zone_of(self, x, y):
        return next((zone_id for zone_id, ring in self.rings if point_in_ring(x, y, ring)), "")

    def snapshot(self, now):
        """``PUT /workers/snapshot`` body from fresh cameras, or None if nothing newer than the last one."""
        with self.lock:
            fresh = {sensor_id: entry for sensor_id, entry in self.latest.items()
                     if 0 <= (now - entry[0]).total_seconds() < self.max_age}
            if not fresh:
                return None
            # 가장 오래된 카메라 프레임 시각을 관측 시각으로 써서 최신성을 부풀리지 않는다.
            observed_at = min(frame_at for frame_at, _ in fresh.values())
            if self.sent_at is not None and observed_at <= self.sent_at:
                return None
            groups = []
            for sensor_id, (frame_at, positions) in sorted(fresh.items()):
                ratio = sum(self.seen[sensor_id]) / len(self.seen[sensor_id])
                for point in positions:
                    group = next((g for g in groups if math.hypot(g["x"] - point["x"], g["y"] - point["y"])
                                  <= self.merge_distance and sensor_id not in g["sensors"]), None)
                    if group is None:
                        group = {"x": point["x"], "y": point["y"], "points": [], "sensors": [],
                                 "seen_at": frame_at, "confidence": 0.0}
                        groups.append(group)
                    group["points"].append(point)
                    group["sensors"].append(sensor_id)
                    group["x"] = sum(p["x"] for p in group["points"]) / len(group["points"])
                    group["y"] = sum(p["y"] for p in group["points"]) / len(group["points"])
                    group["seen_at"] = max(group["seen_at"], frame_at)
                    group["confidence"] = max(group["confidence"], ratio)
            workers = []
            for index, group in enumerate(groups, start=1):
                zone_id = self._zone_of(group["x"], group["y"])
                workers.append({
                    "worker_id": f"worker-{index}",
                    "pose": {"frame_id": "world",
                             "position": {"x": group["x"], "y": group["y"], "z": 0.0}},
                    "current_zone_id": zone_id,
                    "safety_status": "DANGER" if zone_id in self.keep_out_ids else "UNKNOWN",
                    "last_seen_at": group["seen_at"].isoformat(),
                    "confidence": round(group["confidence"], 3),
                    "source_sensor_ids": group["sensors"],
                })
            self.sent_at = observed_at
            return {"observed_at": observed_at.isoformat(), "workers": workers}


class LatestSender:
    """Send the newest payload per key from one background thread; older unsent payloads are dropped."""

    def __init__(self, send, on_error=None):
        self.send = send
        self.on_error = on_error
        self.pending = {}
        self.condition = threading.Condition()
        self.closed = False
        self.sent = 0
        self.failures = 0
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def submit(self, key, *args):
        with self.condition:
            self.pending[key] = args
            self.condition.notify()

    def _run(self):
        while True:
            with self.condition:
                while not self.pending and not self.closed:
                    self.condition.wait()
                if not self.pending and self.closed:
                    return
                key = next(iter(self.pending))
                args = self.pending.pop(key)
            try:
                self.send(*args)
                self.sent += 1
            except Exception as exc:  # 다음 주기에 새 값으로 다시 보낸다
                self.failures += 1
                if self.on_error is not None:
                    self.on_error(key, exc)

    def close(self, timeout=3.0):
        """Flush what is queued, then stop."""
        with self.condition:
            self.closed = True
            self.condition.notify()
        self.thread.join(timeout)


def utc_now():
    return datetime.now(timezone.utc)


def parse_utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)

