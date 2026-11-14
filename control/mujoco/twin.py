"""MuJoCo ② 디지털 트윈 재구성 (이슈 #54, 플랫폼 구조 v1.0 3.3절).

온톨로지 상태(어댑터가 보고한 Robot, CV Worker 스냅샷, 등록 Zone)만으로 MuJoCo 장면을 만든다.
MuJoCo ①(scenario_scene.xml)의 정답 좌표는 읽지 않는다. 정답은 ``position_errors``로 오차를 잴 때만 쓴다.

입력은 제어 계약 13.1절 요청의 ``state`` 객체와 같은 형식이다.

    {"robots": [{"robot_id", "robot_type", "frame_id", "position": [x, y, z], "yaw_rad"}],
     "workers": {"observed_at": "...Z", "items": [{"worker_id", "frame_id", "position": [x, y, z]}]},
     "zones": [{"zone_id", "zone_type", "frame_id", "geometry": {...}}]}

Backend 형식(Robot.pose, Worker.pose, Zone.geometry)은 ``state_from_backend``로 바꾼다.
좌표계는 ``world``(z 위), 단위는 m·rad.

    python mujoco/twin.py --backend http://127.0.0.1:8000 --workers workers.json --xml outputs/twin.xml
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from urllib import request

import mujoco

FRAME_ID = "world"
# 작업자 위치 여유 반경: CV 바닥 투영 오차 중앙값 0.15~0.20 m(2026-11-05 측정) 이상 (스키마 v1.1 COLLISION 규칙)
# 중앙값 기반 MVP 초기값이며 안전이 보장되는 오차 상한이 아니다 (control/README.md 적용 범위)
WORKER_MARGIN_M = 0.20
WORKER_BODY_RADIUS_M = 0.25  # 어깨 폭 기준 사람 반경
WORKER_HEIGHT_M = 1.75
# 로봇 종류별 충돌 형상(반경, 높이). MOBILE은 TIAGo 베이스(지름 약 0.54 m, 높이 약 1.1 m) 기준
# 사전 시뮬레이션용 단순화 모델. 팔·관절 자세는 반영하지 않으므로 정밀 충돌 검사로 쓰지 않는다
ROBOT_SHAPES = {
    "MOBILE": (0.30, 1.10),
    "QUADRUPED": (0.40, 0.60),
    "HUMANOID": (0.30, 1.70),
    "MANIPULATOR": (0.40, 1.20),
}
ZONE_HEIGHT_M = 0.01  # 구역은 바닥 표시용 얇은 판. 충돌하지 않는다
ZONE_COLORS = {"KEEP_OUT": "0.9 0.15 0.1 0.35", "WORK": "0.2 0.5 0.9 0.25"}


class TwinError(ValueError):
    """온톨로지 상태로 트윈을 만들 수 없는 경우. 사전 시뮬레이션은 이 경우 ERROR로 거부한다."""


def _xyz(values, name):
    if not isinstance(values, (list, tuple)) or len(values) != 3:
        raise TwinError(f"{name}: position needs [x, y, z]")
    out = [float(v) for v in values]
    if not all(math.isfinite(v) for v in out):
        raise TwinError(f"{name}: position must be finite")
    return out


def _check_frame(item, name):
    if item.get("frame_id") != FRAME_ID:
        raise TwinError(f"{name}: frame_id must be {FRAME_ID!r}, got {item.get('frame_id')!r}")


def zone_ring(geometry, name):
    """Zone 바깥 고리 [(x, y), ...]. GeoJSON Polygon과 제어 계약 polygon/points를 모두 받는다."""
    kind = str(geometry.get("type", "")).lower()
    if kind == "polygon" and "points" in geometry:
        ring = geometry["points"]
    elif kind == "polygon" and "coordinates" in geometry:
        rings = geometry["coordinates"]
        if not isinstance(rings, list) or len(rings) != 1:
            raise TwinError(f"{name}: GeoJSON Polygon needs exactly one ring")
        ring = rings[0]
    else:
        raise TwinError(f"{name}: unsupported geometry {geometry.get('type')!r}")
    points = []
    for p in ring:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            raise TwinError(f"{name}: polygon point needs [x, y]")
        points.append((float(p[0]), float(p[1])))
    if len(points) > 1 and points[0] == points[-1]:
        points = points[:-1]
    if len(points) < 3 or not all(math.isfinite(v) for p in points for v in p):
        raise TwinError(f"{name}: polygon needs at least 3 finite points")
    return points


def _quat_to_yaw(q):
    x, y, z, w = (float(q.get(k, d)) for k, d in (("x", 0), ("y", 0), ("z", 0), ("w", 1)))
    return math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))


def state_from_backend(robots, worker_snapshot, zones):
    """Backend 응답(GET /robots, Worker 스냅샷, GET /zones)을 트윈 입력 형식으로 바꾼다."""
    def pose_xyz(pose):
        p = pose["position"]
        return [p["x"], p["y"], p["z"]]

    state = {
        "robots": [{"robot_id": r["robot_id"], "robot_type": r["robot_type"],
                    "frame_id": r["pose"]["frame_id"], "position": pose_xyz(r["pose"]),
                    "yaw_rad": _quat_to_yaw(r["pose"].get("orientation") or {})} for r in robots],
        "workers": None,
        "zones": [{"zone_id": z["zone_id"], "zone_type": z["zone_type"], "frame_id": z["frame_id"],
                   "geometry": z["geometry"]} for z in zones],
    }
    if worker_snapshot is not None:
        state["workers"] = {
            "observed_at": worker_snapshot["observed_at"],
            "items": [{"worker_id": w["worker_id"], "frame_id": w["pose"]["frame_id"],
                       "position": pose_xyz(w["pose"])} for w in worker_snapshot["workers"]]}
    return state


def build_twin_xml(state, worker_margin=WORKER_MARGIN_M):
    """온톨로지 상태로 MJCF 문자열을 만든다.

    - 로봇: 평면 이동(x, y 슬라이드 + z 회전) 관절을 가진 원기둥. 사전 시뮬레이션이 이 관절을 움직인다.
    - 작업자: 여유 반경을 더한 캡슐 mocap 바디. 이동 예측은 mocap 위치를 바꿔 넣는다.
    - Zone: 볼록 껍질 메시 판(충돌 없음). 오목한 구역은 볼록 껍질로 넓어지므로 보수적이다.
    """
    robots = state.get("robots") or []
    if not robots:
        raise TwinError("state needs at least one robot")
    workers = state.get("workers")
    if workers is None:
        # 관측이 없으면 작업자가 없는 것이 아니라 판정 불가다(Fail-Closed).
        raise TwinError("worker snapshot is missing; the twin cannot assume an empty site")

    assets, zone_geoms, bodies = [], [], []
    ids = set()

    def unique(name):
        if name in ids:
            raise TwinError(f"duplicate id {name}")
        ids.add(name)
        return name

    for zone in state.get("zones") or []:
        zid = unique(f"zone:{zone['zone_id']}")
        _check_frame(zone, zid)
        ring = zone_ring(zone.get("geometry") or {}, zid)
        verts = " ".join(f"{x} {y} 0 {x} {y} {ZONE_HEIGHT_M}" for x, y in ring)
        assets.append(f'<mesh name="{zid}" vertex="{verts}"/>')
        rgba = ZONE_COLORS.get(zone.get("zone_type"), "0.5 0.5 0.5 0.2")
        zone_geoms.append(f'<geom name="{zid}" type="mesh" mesh="{zid}" contype="0" conaffinity="0" '
                          f'group="1" rgba="{rgba}"/>')

    for robot in robots:
        rid = unique(f"robot:{robot['robot_id']}")
        _check_frame(robot, rid)
        x, y, _ = _xyz(robot.get("position"), rid)
        shape = ROBOT_SHAPES.get(robot.get("robot_type"))
        if shape is None:
            raise TwinError(f"{rid}: unknown robot_type {robot.get('robot_type')!r}")
        radius, height = shape
        yaw = float(robot.get("yaw_rad") or 0.0)
        bodies.append(
            f'<body name="{rid}" pos="{x} {y} 0" euler="0 0 {yaw}">'
            f'<joint name="{rid}:x" type="slide" axis="1 0 0"/>'
            f'<joint name="{rid}:y" type="slide" axis="0 1 0"/>'
            f'<joint name="{rid}:yaw" type="hinge" axis="0 0 1"/>'
            f'<geom name="{rid}" type="cylinder" size="{radius} {height / 2}" pos="0 0 {height / 2}" '
            f'mass="40" rgba="1 0.8 0 1"/></body>')

    for worker in workers.get("items") or []:
        wid = unique(f"worker:{worker['worker_id']}")
        _check_frame(worker, wid)
        x, y, _ = _xyz(worker.get("position"), wid)
        radius = WORKER_BODY_RADIUS_M + worker_margin
        half = max(WORKER_HEIGHT_M / 2 - radius, 0.01)
        bodies.append(
            f'<body name="{wid}" mocap="true" pos="{x} {y} {WORKER_HEIGHT_M / 2}">'
            f'<geom name="{wid}" type="capsule" size="{radius} {half}" rgba="0.2 0.8 0.3 0.6"/></body>')

    return (
        '<mujoco model="twin">'
        '<option timestep="0.01" gravity="0 0 0"/>'
        f'<asset>{"".join(assets)}</asset>'
        '<worldbody>'
        '<geom name="floor" type="plane" size="0 0 0.1" contype="0" conaffinity="0" rgba="0.6 0.6 0.6 1"/>'
        f'{"".join(zone_geoms)}{"".join(bodies)}'
        '</worldbody></mujoco>')


def build_twin(state, worker_margin=WORKER_MARGIN_M):
    """온톨로지 상태로 (MjModel, MjData)를 만들고 정방향 계산까지 마친다."""
    model = mujoco.MjModel.from_xml_string(build_twin_xml(state, worker_margin))
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    return model, data


def twin_positions(model, data):
    """트윈의 로봇·작업자 바닥 위치 {'robot:robot-01': (x, y), 'worker:worker-01': (x, y)}."""
    out = {}
    for i in range(model.nbody):
        name = model.body(i).name
        if name.startswith(("robot:", "worker:")):
            out[name] = (float(data.xpos[i][0]), float(data.xpos[i][1]))
    return out


def position_errors(model, data, truth):
    """트윈 위치와 MuJoCo ① 정답 위치의 거리(m). truth: {'worker:worker-01': (x, y), ...}.

    트윈에는 있는데 정답에 없거나(오탐) 정답에는 있는데 트윈에 없는(미탐) 항목은 따로 돌려준다.
    """
    twin = twin_positions(model, data)
    errors = {k: math.dist(twin[k], truth[k]) for k in twin.keys() & truth.keys()}
    return {"errors_m": errors, "missing": sorted(truth.keys() - twin.keys()),
            "extra": sorted(twin.keys() - truth.keys())}


def _get_json(url):
    with request.urlopen(url, timeout=2) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_backend_state(base_url, worker_snapshot):
    """GET /robots, GET /zones를 읽는다. Backend에 Worker 조회 API가 없어 스냅샷은 호출자가 넘긴다."""
    base = base_url.rstrip("/")
    return state_from_backend(_get_json(f"{base}/robots"), worker_snapshot, _get_json(f"{base}/zones"))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--state", type=Path, help="트윈 입력 형식 JSON (제어 계약 13.1 state)")
    source.add_argument("--backend", help="Backend 주소 (GET /robots, GET /zones)")
    parser.add_argument("--workers", type=Path,
                        help="--backend와 함께: PUT /workers/snapshot 본문과 같은 Worker 스냅샷 JSON")
    parser.add_argument("--xml", type=Path, help="만든 MJCF를 저장할 경로")
    args = parser.parse_args()
    if args.state:
        state = json.loads(args.state.read_text(encoding="utf-8"))
    else:
        snapshot = json.loads(args.workers.read_text(encoding="utf-8")) if args.workers else None
        state = fetch_backend_state(args.backend, snapshot)
    xml = build_twin_xml(state)
    model, data = build_twin(state)
    if args.xml:
        args.xml.parent.mkdir(parents=True, exist_ok=True)
        args.xml.write_text(xml, encoding="utf-8")
    print(json.dumps({k: [round(v, 3) for v in p] for k, p in twin_positions(model, data).items()},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
