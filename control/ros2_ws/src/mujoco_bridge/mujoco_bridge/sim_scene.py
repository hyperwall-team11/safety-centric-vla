"""웹 콘솔 지도용 시뮬레이션 장면 정보(MuJoCo ① 정답).

정적 정보(구역 표시·작업대·CCTV 위치와 시야)는 장면 XML에서, 동적 정보(로봇·작업자 위치)는
브리지 토픽(/odom, /sim/worker_pose)에서 받는다. 좌표는 world(m, z 위)이며 bridge_node가
world → odom을 항등으로 두므로 /odom 위치를 그대로 world 좌표로 쓴다.

이 값은 시뮬레이션 정답이다. 온톨로지 상태(Backend Robot·Zone·Worker)를 대신하지 않으며,
지도에서는 "시뮬레이션" 레이어로만 구분해 보여 준다.
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field

# scenario_scene.xml의 바닥 표시 geom 이름 → 지도 구역
ZONE_GEOMS = {
    "keep_out_zone": ("금지구역", "KEEP_OUT"),
    "zone_a_marking": ("A구역", "WORK"),
    "zone_b_marking": ("B구역", "WORK"),
}
TABLE_GEOMS = {"table_a_top": "작업대 A", "table_b_top": "작업대 B"}
CAMERAS = ("cctv_1", "cctv_2")


def _box_polygon(cx: float, cy: float, hx: float, hy: float, yaw: float) -> list[list[float]]:
    c, s = math.cos(yaw), math.sin(yaw)
    return [[round(cx + c * x - s * y, 4), round(cy + s * x + c * y, 4)] for x, y in
            ((-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy))]


def load_static_scene(xml_path: str) -> dict:
    """장면 XML → 구역·작업대·카메라. 카메라는 바닥 위치, 바라보는 방향(yaw), 수평 시야각."""
    import mujoco

    model = mujoco.MjModel.from_xml_path(xml_path)
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)

    def geom_box(name: str):
        gid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, name)
        if gid < 0:
            return None
        x, y = data.geom_xpos[gid][:2]
        mat = data.geom_xmat[gid].reshape(3, 3)
        yaw = math.atan2(mat[1, 0], mat[0, 0])
        hx, hy = model.geom_size[gid][:2]
        return _box_polygon(float(x), float(y), float(hx), float(hy), yaw)

    zones = [{"zone_id": zid, "zone_type": ztype, "points": pts}
             for geom, (zid, ztype) in ZONE_GEOMS.items() if (pts := geom_box(geom))]
    tables = [{"name": label, "points": pts} for geom, label in TABLE_GEOMS.items() if (pts := geom_box(geom))]

    cameras = []
    for name in CAMERAS:
        cid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, name)
        if cid < 0:
            continue
        pos = data.cam_xpos[cid]
        mat = data.cam_xmat[cid].reshape(3, 3)
        forward = -mat[:, 2]  # MuJoCo 카메라는 -z를 본다
        fovy = math.radians(float(model.cam_fovy[cid]))
        # 640×480 기준 수평 시야각 (bridge 기본 영상 크기)
        hfov = 2 * math.atan(math.tan(fovy / 2) * 640 / 480)
        cameras.append({
            "sensor_id": name,
            "position": [round(float(pos[0]), 4), round(float(pos[1]), 4), round(float(pos[2]), 4)],
            "yaw": round(math.atan2(float(forward[1]), float(forward[0])), 4),
            "hfov": round(hfov, 4),
        })

    xs = [p[0] for z in zones + tables for p in z["points"]] + [c["position"][0] for c in cameras]
    ys = [p[1] for z in zones + tables for p in z["points"]] + [c["position"][1] for c in cameras]
    bounds = [min(xs), min(ys), max(xs), max(ys)] if xs else None
    return {"source": xml_path.rsplit("/", 1)[-1], "frame_id": "world", "zones": zones, "tables": tables,
            "cameras": cameras, "bounds": bounds}


@dataclass
class SceneState:
    """정적 장면 + 토픽으로 받은 최신 로봇·작업자 위치. ROS 콜백과 HTTP 스레드가 함께 쓴다."""

    static: dict
    robot_id: str = "robot-01"
    _robot: dict | None = None
    _workers: dict[str, dict] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def set_robot(self, x: float, y: float, yaw: float) -> None:
        with self._lock:
            self._robot = {"robot_id": self.robot_id, "position": [round(x, 4), round(y, 4)],
                           "yaw": round(yaw, 4), "updated_at": time.time()}

    def set_worker(self, worker_id: str, x: float, y: float) -> None:
        with self._lock:
            self._workers[worker_id] = {"worker_id": worker_id, "position": [round(x, 4), round(y, 4)],
                                        "updated_at": time.time()}

    def snapshot(self) -> dict:
        with self._lock:
            return {**self.static, "robots": [self._robot] if self._robot else [],
                    "workers": list(self._workers.values()), "observed_at": time.time()}


def yaw_from_quaternion(x: float, y: float, z: float, w: float) -> float:
    return math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
