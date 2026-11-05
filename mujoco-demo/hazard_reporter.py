"""Turn CCTV worker detections inside a KEEP_OUT Zone into backend HazardEvents, without ROS.

Team decision (2026-11-05): a worker seen inside the KEEP_OUT Zone by any one camera is a
CRITICAL WORKER_INTRUSION. CRITICAL events cannot be cleared by sensors (schema v1.1 §HazardEvent),
so an operator resolves them; this module only opens events.
"""

import json
import math
from datetime import datetime, timezone
from urllib import error, request
from uuid import NAMESPACE_URL, uuid4, uuid5

from observation_export import ROTATION_CONVENTION, ground_position


# 팀 결정(2026-11-05): 카메라 1대라도 연속 2프레임에서 금지구역 안 작업자를 보면 위험으로 본다.
REQUIRED_CONSECUTIVE_FRAMES = 2
# 모든 카메라가 계속 관측하면서 이 시간 동안 금지구역 안 작업자를 못 보면 한 번의 진입이 끝난 것으로 본다.
# 카메라가 끊기거나 결과가 늦으면 작업자 부재로 보지 않고 진입을 유지한다.
EPISODE_CLEAR_SECONDS = 2.0
# Florence-2 <OD>는 점수가 없어, 모델 confidence 대신 트리거 카메라 최근 프레임 중 금지구역 탐지 비율
# (탐지 지속 비율)을 쓴다. 모델 점수가 아니므로 해석은 ONT와 정한다(PR #41).
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
    """Validated outer ring of a world-frame KEEP_OUT Zone.

    Accepts the GeoJSON ``Polygon/coordinates`` form and the Backend–CTL contract ``polygon/points``
    form (an open ring is closed here), matching what the backend safety gate accepts.
    """
    if zone.get("zone_type") != "KEEP_OUT":
        raise ValueError(f"Zone {zone.get('zone_id')} is not KEEP_OUT")
    if zone.get("frame_id") != "world":
        raise ValueError(f"Zone {zone.get('zone_id')} must use the world frame")
    geometry = zone.get("geometry") or {}
    if geometry.get("type") == "polygon":
        points = geometry.get("points")
        if not isinstance(points, list) or not points:
            raise ValueError(f"Zone {zone.get('zone_id')} needs polygon points")
        points = points if points[0] == points[-1] else [*points, points[0]]
    else:
        rings = geometry.get("coordinates")
        if geometry.get("type") != "Polygon" or not isinstance(rings, list) or len(rings) != 1:
            raise ValueError(f"Zone {zone.get('zone_id')} needs a single-ring Polygon or polygon points")
        points = rings[0]
    ring = []
    for point in points:
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
    """Ground positions of detected people and whether any stands inside ``ring`` (None: no zone)."""
    positions = []
    for person in people:
        try:
            point = ground_position(person["bbox_xyxy"], width, height, camera)
        except ValueError:
            continue
        inside = ring is not None and point_in_ring(point["x"], point["y"], ring)
        positions.append({**point, "in_zone": inside})
    return positions, any(p["in_zone"] for p in positions)


def observation_id(sensor_id, sec, nanosec, index):
    return f"obs-{uuid5(NAMESPACE_URL, f'{sensor_id}:{sec}.{nanosec:09d}:{index}')}"


class IntrusionTracker:
    """Open one HazardEvent per entry; any camera reaching the consecutive-frame rule triggers it.

    An entry ends only when every camera in ``sensor_ids`` has processed frames after the last
    in-zone sighting for ``clear_seconds``; a stalled camera keeps the entry open. Each entry has
    a fixed ``hazard_id`` so a retried POST cannot register the same entry twice. The evidence of
    the first trigger (sensor, observation IDs, confidence, time) is frozen and resent unchanged
    until the event is reported, and an unreported entry never ends, so a failed POST is not lost.
    """

    def __init__(self, sensor_ids=(), required=REQUIRED_CONSECUTIVE_FRAMES,
                 clear_seconds=EPISODE_CLEAR_SECONDS, window=CONFIDENCE_WINDOW_FRAMES):
        self.sensor_ids = set(sensor_ids)
        self.required = required
        self.clear_seconds = clear_seconds
        self.window = window
        self.streaks = {}
        self.history = {}
        self.observations = {}
        self.last_frame = {}
        self.active = False
        self.reported = False
        self.last_seen = None
        self.hazard_id = None
        self.trigger = None

    def _entry_over(self, now):
        observed_since = all(self.last_frame.get(s, float("-inf")) > self.last_seen
                             for s in self.sensor_ids | set(self.last_frame))
        return observed_since and now - self.last_seen >= self.clear_seconds

    def update(self, sensor_id, in_zone, now, observation_ids=(), detected_at=None):
        """Record one processed frame; return trigger details when an event should be opened."""
        history = self.history.setdefault(sensor_id, [])
        history.append(bool(in_zone))
        del history[:-self.window]
        self.last_frame[sensor_id] = now
        if in_zone:
            self.streaks[sensor_id] = self.streaks.get(sensor_id, 0) + 1
            pending = self.observations.setdefault(sensor_id, [])
            pending.extend(observation_ids)
            del pending[:-self.window]
            self.last_seen = now
        else:
            self.streaks[sensor_id] = 0
            self.observations[sensor_id] = []
            if self.active and self.reported and self._entry_over(now):
                self.active = self.reported = False
                self.hazard_id = self.trigger = None
        if in_zone and self.streaks[sensor_id] >= self.required and not self.active:
            self.active = True
            self.hazard_id = f"hazard-{uuid4()}"
            self.trigger = {"hazard_id": self.hazard_id, "sensor_id": sensor_id,
                            "confidence": sum(history) / len(history),
                            "observation_ids": list(self.observations[sensor_id]),
                            "detected_at": detected_at or datetime.now(timezone.utc)}
        if not self.active or self.reported:
            return None
        return dict(self.trigger, observation_ids=list(self.trigger["observation_ids"]))

    def mark_reported(self):
        self.reported = True


def build_hazard(zone_id, robot_ids, trigger, detected_at=None):
    return {
        "hazard_id": trigger["hazard_id"],
        "hazard_type": "WORKER_INTRUSION",
        "severity": "CRITICAL",
        "source_observation_ids": trigger["observation_ids"],
        "related_robot_ids": list(robot_ids),
        "related_worker_ids": [],
        "zone_id": zone_id,
        "confidence": round(trigger["confidence"], 3),
        "detected_at": (detected_at or trigger.get("detected_at") or datetime.now(timezone.utc)).isoformat(),
    }


class BackendHTTPError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


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
            raise BackendHTTPError(exc.code, f"{method} {path} failed with HTTP {exc.code}: {detail}") from exc

    def zones(self):
        return self._call("GET", "/zones")

    def zone(self, zone_id):
        for zone in self.zones():
            if zone.get("zone_id") == zone_id:
                return zone
        raise ValueError(f"Zone {zone_id} is not registered in the backend")

    def put_sensor(self, sensor):
        return self._call("PUT", f"/sensors/{sensor['sensor_id']}", sensor)

    def put_worker_snapshot(self, snapshot):
        return self._call("PUT", "/workers/snapshot", snapshot)

    def post_hazard(self, hazard):
        """Create the event; a 409 for the same fixed ``hazard_id`` means an earlier try already did."""
        try:
            return self._call("POST", "/hazards", hazard)
        except BackendHTTPError as exc:
            if exc.code == 409:
                return {"hazard_id": hazard["hazard_id"], "already_registered": True}
            raise
