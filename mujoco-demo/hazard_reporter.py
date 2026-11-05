"""Turn CCTV worker detections inside a KEEP_OUT Zone into backend HazardEvents, without ROS.

Team decision (2026-11-05): a worker seen inside the KEEP_OUT Zone by any one camera is a
CRITICAL WORKER_INTRUSION. CRITICAL events cannot be cleared by sensors (schema v1.1 §HazardEvent),
so an operator resolves them; this module only opens events.
"""

import json
import math
from datetime import datetime, timezone
from urllib import error, request
from uuid import NAMESPACE_URL, uuid5

from observation_export import ROTATION_CONVENTION, ground_position


# 팀 결정(2026-11-05): 카메라 1대라도 연속 2프레임에서 금지구역 안 작업자를 보면 위험으로 본다.
REQUIRED_CONSECUTIVE_FRAMES = 2
# 모든 카메라에서 이 시간 동안 금지구역 안 작업자가 없으면 한 번의 진입이 끝난 것으로 본다.
EPISODE_CLEAR_SECONDS = 2.0
# Florence-2 <OD>는 점수가 없어, 신뢰도 대신 트리거 카메라 최근 프레임 중 금지구역 탐지 비율을 쓴다.
CONFIDENCE_WINDOW_FRAMES = 5


def point_in_ring(x, y, ring):
    """Even-odd test against a closed ring of [x, y] points; points on an edge count as inside."""
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        cross = (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)
        if (abs(cross) < 1e-9 and min(x1, x2) - 1e-9 <= x <= max(x1, x2) + 1e-9
                and min(y1, y2) - 1e-9 <= y <= max(y1, y2) + 1e-9):
            return True
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def keep_out_ring(zone):
    """Validated outer ring of a world-frame KEEP_OUT Zone stored as a GeoJSON Polygon."""
    if zone.get("zone_type") != "KEEP_OUT":
        raise ValueError(f"Zone {zone.get('zone_id')} is not KEEP_OUT")
    if zone.get("frame_id") != "world":
        raise ValueError(f"Zone {zone.get('zone_id')} must use the world frame")
    geometry = zone.get("geometry") or {}
    rings = geometry.get("coordinates")
    if geometry.get("type") != "Polygon" or not isinstance(rings, list) or len(rings) != 1:
        raise ValueError(f"Zone {zone.get('zone_id')} needs a single-ring GeoJSON Polygon")
    ring = []
    for point in rings[0]:
        if len(point) < 2:
            raise ValueError("Polygon points need x and y")
        x, y = float(point[0]), float(point[1])
        if not (math.isfinite(x) and math.isfinite(y)):
            raise ValueError("Polygon points must be finite")
        ring.append((x, y))
    if len(ring) < 4 or ring[0] != ring[-1] or len(set(ring[:-1])) < 3:
        raise ValueError(f"Zone {zone.get('zone_id')} has an invalid Polygon ring")
    return ring


def camera_from_pose(position, rotation, fovy_deg, ground_z=0.0):
    """Camera calibration in the form ``ground_position`` expects."""
    return {"position_m": list(position), "rotation_cam_to_world": [list(row) for row in rotation],
            "fovy_deg": float(fovy_deg), "ground_plane_z_m": float(ground_z),
            "rotation_convention": ROTATION_CONVENTION}


def workers_in_zone(people, width, height, camera, ring):
    """Ground positions of detected people and whether any stands inside ``ring``."""
    positions = []
    for person in people:
        try:
            point = ground_position(person["bbox_xyxy"], width, height, camera)
        except ValueError:
            continue
        positions.append({**point, "in_zone": point_in_ring(point["x"], point["y"], ring)})
    return positions, any(p["in_zone"] for p in positions)


def observation_id(sensor_id, sec, nanosec, index):
    return f"obs-{uuid5(NAMESPACE_URL, f'{sensor_id}:{sec}.{nanosec:09d}:{index}')}"


class IntrusionTracker:
    """Open one HazardEvent per entry; any camera reaching the consecutive-frame rule triggers it."""

    def __init__(self, required=REQUIRED_CONSECUTIVE_FRAMES, clear_seconds=EPISODE_CLEAR_SECONDS,
                 window=CONFIDENCE_WINDOW_FRAMES):
        self.required = required
        self.clear_seconds = clear_seconds
        self.window = window
        self.streaks = {}
        self.history = {}
        self.observations = {}
        self.active = False
        self.reported = False
        self.last_seen = None

    def update(self, sensor_id, in_zone, now, observation_ids=()):
        """Record one processed frame; return trigger details when an event should be opened."""
        history = self.history.setdefault(sensor_id, [])
        history.append(bool(in_zone))
        del history[:-self.window]
        if in_zone:
            self.streaks[sensor_id] = self.streaks.get(sensor_id, 0) + 1
            pending = self.observations.setdefault(sensor_id, [])
            pending.extend(observation_ids)
            del pending[:-self.window]
            self.last_seen = now
        else:
            self.streaks[sensor_id] = 0
            self.observations[sensor_id] = []
            if self.active and now - self.last_seen >= self.clear_seconds:
                self.active = self.reported = False
        if in_zone and self.streaks[sensor_id] >= self.required:
            self.active = True
        if not self.active or self.reported:
            return None
        return {"sensor_id": sensor_id, "confidence": sum(history) / len(history),
                "observation_ids": list(self.observations[sensor_id])}

    def mark_reported(self):
        self.reported = True


def build_hazard(zone_id, robot_ids, trigger, detected_at=None):
    return {
        "hazard_type": "WORKER_INTRUSION",
        "severity": "CRITICAL",
        "source_observation_ids": trigger["observation_ids"],
        "related_robot_ids": list(robot_ids),
        "related_worker_ids": [],
        "zone_id": zone_id,
        "confidence": round(trigger["confidence"], 3),
        "detected_at": (detected_at or datetime.now(timezone.utc)).isoformat(),
    }


class BackendClient:
    def __init__(self, base_url, timeout=2.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _call(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = request.Request(self.base_url + path, data=data, method=method,
                              headers={"Content-Type": "application/json"})
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise RuntimeError(f"{method} {path} failed with HTTP {exc.code}: {detail}") from exc

    def zone(self, zone_id):
        for zone in self._call("GET", "/zones"):
            if zone.get("zone_id") == zone_id:
                return zone
        raise ValueError(f"Zone {zone_id} is not registered in the backend")

    def post_hazard(self, hazard):
        return self._call("POST", "/hazards", hazard)
