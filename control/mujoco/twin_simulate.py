"""MuJoCo ② 사전 시뮬레이션 서비스 POST /twin/simulate (이슈 #53, 제어 계약 13장).

Backend가 보낸 후보 행동과 온톨로지 상태로 트윈(``twin.py``)을 만들고, 로봇을 목표까지 움직여 본 결과만 돌려준다.
승인 판정과 감사 기록은 Backend(온톨로지)가 한다. MuJoCo ① 정답 좌표는 읽지 않는다.

    python mujoco/twin_simulate.py                       # http://127.0.0.1:8100/twin/simulate
    python mujoco/twin_simulate.py --request req.json    # 서버 없이 요청 한 건 판정

결과는 시뮬레이션용 단순화 모델(로봇 원기둥, 정지한 작업자)의 접촉 검사다. 안전 보장으로 해석하지 않고,
Safety Gate의 ``SAFETY_DISTANCE`` 판정을 대신하지 않는다.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import mujoco

from twin import ROBOT_SHAPES, WORKER_BODY_RADIUS_M, TwinError, build_twin

CONTRACT_VERSION = "backend-ctl/0.2"
SUPPORTED_ACTIONS = {"MOVE_TO"}  # 제어 계약 13.1: 첫 버전은 MOVE_TO만
DEFAULT_PORT = 8100  # 제어 계약 13장 기본 주소 http://127.0.0.1:8100

# 아래 세 값은 #50(ONT 판정 기준·시간 제한)이 확정되기 전의 잠정값이다. 확정되면 그 값으로 바꾼다.
# Worker 스냅샷 허용 나이: 스키마 v1.1 Sensor `last_frame_at` 2초 무수신 시 DEGRADED와 같은 기준
SNAPSHOT_MAX_AGE_S = 2.0
# 작업자와의 여유 거리(몸 표면 사이, CV 여유 반경 제외) 하한: 스키마 v1.1 Zone `estop_distance` 기본값 0.6 m
UNSAFE_DISTANCE_M = 0.6
# 한 요청에서 시뮬레이션할 최대 구간. 이 안에 목표에 닿지 못하면 GOAL_NOT_REACHED
MAX_HORIZON_S = 30.0

GOAL_TOLERANCE_M = 0.05
SNAPSHOT_CLOCK_SKEW_S = 0.5  # observed_at이 snapshot_at보다 이만큼 넘게 미래면 시계 오류로 거부
MAX_BODY_BYTES = 1_000_000
# robot_type이 없는 로봇은 가장 큰 반경의 형상으로 보수적으로 둔다
CONSERVATIVE_ROBOT_TYPE = max(ROBOT_SHAPES, key=lambda k: ROBOT_SHAPES[k][0])


class SimulationError(ValueError):
    """요청을 판정할 수 없는 경우. 응답 result는 ERROR다(Backend는 실행 차단)."""


def _parse_time(value, name):
    if not isinstance(value, str):
        raise SimulationError(f"{name} must be an ISO 8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SimulationError(f"{name}: {exc}") from exc
    if parsed.tzinfo is None:
        raise SimulationError(f"{name} must include a UTC offset")
    return parsed


def check_snapshot_fresh(snapshot_at, workers):
    """Worker 스냅샷이 snapshot_at 기준으로 오래되었으면 거부한다(Fail-Closed)."""
    if not isinstance(workers, dict):
        raise SimulationError("worker snapshot is missing; the twin cannot assume an empty site")
    age = (_parse_time(snapshot_at, "snapshot_at")
           - _parse_time(workers.get("observed_at"), "workers.observed_at")).total_seconds()
    if age > SNAPSHOT_MAX_AGE_S:
        raise SimulationError(f"worker snapshot is stale: {age:.2f}s old (max {SNAPSHOT_MAX_AGE_S}s)")
    if age < -SNAPSHOT_CLOCK_SKEW_S:
        raise SimulationError(f"worker snapshot observed_at is {-age:.2f}s after snapshot_at")


def _response(result, snapshot_id, started, *, min_distance=None, contact_at=None, horizon=0.0,
              detail=None):
    body = {
        "result": result,
        "snapshot_id": snapshot_id,
        "min_worker_distance_m": None if min_distance is None else round(min_distance, 3),
        "first_contact_at_s": None if contact_at is None else round(contact_at, 3),
        "simulated_horizon_s": round(horizon, 3),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }
    if detail:
        body["detail"] = detail
    return body


def _robot_contacts(model, data, geom_id):
    for contact in data.contact[:data.ncon]:
        if geom_id in (contact.geom1, contact.geom2):
            return True
    return False


def simulate(req):
    """요청 한 건을 시뮬레이션해 제어 계약 13.2절 응답을 돌려준다. 판정할 수 없으면 result=ERROR."""
    started = time.perf_counter()
    snapshot_id = req.get("snapshot_id") if isinstance(req, dict) else None
    try:
        return _simulate(req, started)
    except (SimulationError, TwinError, KeyError, TypeError, ValueError) as exc:
        return _response("ERROR", snapshot_id, started, detail=str(exc))


def _simulate(req, started):
    if not isinstance(req, dict):
        raise SimulationError("request body must be a JSON object")
    if req.get("contract_version") != CONTRACT_VERSION:
        raise SimulationError(f"contract_version must be {CONTRACT_VERSION!r}")
    snapshot_id = req.get("snapshot_id")
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise SimulationError("snapshot_id is required")
    action = req.get("action") or {}
    if action.get("action_type") not in SUPPORTED_ACTIONS:
        raise SimulationError(f"unsupported action_type {action.get('action_type')!r}")
    state = req.get("state")
    if not isinstance(state, dict):
        raise SimulationError("state is required")
    check_snapshot_fresh(req.get("snapshot_at"), state.get("workers"))

    target = action.get("target_pose") or {}
    if target.get("frame_id") != "world":
        raise SimulationError("target_pose.frame_id must be 'world'")
    position = target.get("position")
    if not isinstance(position, (list, tuple)) or len(position) != 3:
        raise SimulationError("target_pose.position needs [x, y, z]")
    goal = (float(position[0]), float(position[1]))
    speed = float(action.get("speed_limit"))
    if not (math.isfinite(speed) and speed > 0) or not all(math.isfinite(v) for v in goal):
        raise SimulationError("speed_limit and target_pose must be finite, speed_limit > 0")

    robot_id = action.get("robot_id")
    robots = [dict(r) for r in state.get("robots") or []]
    for robot in robots:
        robot.setdefault("robot_type", CONSERVATIVE_ROBOT_TYPE)
    if robot_id not in {r.get("robot_id") for r in robots}:
        raise SimulationError(f"robot {robot_id!r} is not in state.robots")
    model, data = build_twin({**state, "robots": robots})

    name = f"robot:{robot_id}"
    geom_id = model.geom(name).id
    qx = model.joint(f"{name}:x").qposadr[0]
    qy = model.joint(f"{name}:y").qposadr[0]
    robot_radius = float(model.geom_size[geom_id][0])
    # 슬라이드 관절 값은 로봇 바디(시작 위치·yaw로 놓임) 기준 상대 이동이므로 월드 좌표와 변환한다
    body = model.body(name)
    origin = (float(body.pos[0]), float(body.pos[1]))
    yaw = 2 * math.atan2(float(body.quat[3]), float(body.quat[0]))
    cos_y, sin_y = math.cos(yaw), math.sin(yaw)

    def set_world(x, y):
        dx, dy = x - origin[0], y - origin[1]
        data.qpos[qx], data.qpos[qy] = cos_y * dx + sin_y * dy, -sin_y * dx + cos_y * dy

    def world():
        return (float(data.xpos[body.id][0]), float(data.xpos[body.id][1]))

    start = world()
    workers = [(float(model.body(i).pos[0]), float(model.body(i).pos[1]))
               for i in range(model.nbody) if model.body(i).name.startswith("worker:")]

    def gap(x, y):
        # 로봇과 작업자 몸 표면 사이 거리. CV 여유 반경은 빼지 않고 접촉 판정(COLLISION)에만 쓴다
        return min((math.dist((x, y), w) - robot_radius - WORKER_BODY_RADIUS_M for w in workers),
                   default=None)

    distance = math.dist(start, goal)
    dt = float(model.opt.timestep)
    horizon = min(distance / speed, MAX_HORIZON_S)
    steps = math.ceil(horizon / dt - 1e-9)
    min_gap = gap(*start)
    if _robot_contacts(model, data, geom_id):
        return _response("COLLISION", snapshot_id, started, min_distance=min_gap, contact_at=0.0)

    t = 0.0
    for step in range(1, steps + 1):
        t = min(step * dt, horizon)
        frac = min(speed * t / distance, 1.0) if distance > 0 else 1.0
        x = start[0] + (goal[0] - start[0]) * frac
        y = start[1] + (goal[1] - start[1]) * frac
        set_world(x, y)
        mujoco.mj_forward(model, data)
        g = gap(x, y)
        if g is not None:
            min_gap = g if min_gap is None else min(min_gap, g)
        if _robot_contacts(model, data, geom_id):
            return _response("COLLISION", snapshot_id, started, min_distance=min_gap, contact_at=t,
                             horizon=t)

    reached = math.dist(world(), goal) <= GOAL_TOLERANCE_M
    if min_gap is not None and min_gap < UNSAFE_DISTANCE_M:
        result = "UNSAFE_DISTANCE"
    elif not reached:
        result = "GOAL_NOT_REACHED"
    else:
        result = "PASS"
    return _response(result, snapshot_id, started, min_distance=min_gap, horizon=t)


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body):
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        if self.path != "/twin/simulate":
            self._send(404, {"detail": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY_BYTES:
            self._send(400, {"result": "ERROR", "detail": "body is empty or too large"})
            return
        try:
            req = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._send(400, {"result": "ERROR", "detail": f"invalid JSON: {exc}"})
            return
        self._send(200, simulate(req))

    def log_message(self, fmt, *args):
        pass


def make_server(host="127.0.0.1", port=DEFAULT_PORT):
    return ThreadingHTTPServer((host, port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--request", type=Path, help="서버 없이 요청 JSON 한 건을 판정해 출력")
    args = parser.parse_args()
    if args.request:
        req = json.loads(args.request.read_text(encoding="utf-8"))
        print(json.dumps(simulate(req), ensure_ascii=False, indent=2))
        return
    server = make_server(args.host, args.port)
    print(f"MuJoCo ② 사전 시뮬레이션: http://{args.host}:{args.port}/twin/simulate")
    server.serve_forever()


if __name__ == "__main__":
    main()
