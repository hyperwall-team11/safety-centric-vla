import math
import json
import threading
from urllib import error, request

import pytest

import twin_simulate as ts

KEEP_OUT = [[0.5, -0.6], [1.9, -0.6], [1.9, 0.6], [0.5, 0.6]]


def req(target=(1.2, -0.8), workers=(("worker-01", 2.1, 0.4),), observed_at="2026-11-05T23:59:59.800Z",
        action_type="MOVE_TO", speed=0.3, robot_type="MOBILE"):
    robot = {"robot_id": "robot-01", "frame_id": "world", "position": [0.0, 0.0, 0.0], "yaw_rad": 0.0}
    if robot_type:
        robot["robot_type"] = robot_type
    return {
        "contract_version": "backend-ctl/0.2",
        "proposal_id": "proposal-123",
        "snapshot_id": "snap-1",
        "snapshot_at": "2026-11-06T00:00:00Z",
        "action": {"action_type": action_type, "robot_id": "robot-01",
                   "target_pose": {"frame_id": "world", "position": [target[0], target[1], 0.0]},
                   "speed_limit": speed},
        "state": {
            "robots": [robot],
            "workers": None if workers is None else {
                "observed_at": observed_at,
                "items": [{"worker_id": w, "frame_id": "world", "position": [x, y, 0.0]}
                          for w, x, y in workers]},
            "zones": [{"zone_id": "keep-out-1", "zone_type": "KEEP_OUT", "frame_id": "world",
                       "geometry": {"type": "polygon", "points": KEEP_OUT}}],
        },
    }


def test_contract_example_passes_and_echoes_snapshot_id():
    res = ts.simulate(req())
    assert res["result"] == "PASS"
    assert res["snapshot_id"] == "snap-1"
    assert res["first_contact_at_s"] is None
    assert res["min_worker_distance_m"] >= ts.UNSAFE_DISTANCE_M
    # 실제 제어기는 회전하며 전진하고 도착 직전 감속하므로 직선 최단 시간(1.4422 m / 0.3 m/s)보다 길다
    assert 1.4422 / 0.3 < res["simulated_horizon_s"] < ts.MAX_HORIZON_S
    assert set(res) >= {"result", "snapshot_id", "min_worker_distance_m", "first_contact_at_s",
                        "simulated_horizon_s", "elapsed_ms"}


def test_worker_on_path_is_collision_with_contact_time():
    res = ts.simulate(req(target=(3.0, 0.0), workers=(("worker-01", 2.0, 0.0),)))
    assert res["result"] == "COLLISION"
    # 중심 거리 0.3 + 0.25 + 0.20 = 0.75 m에서 접촉 → 1.25 m / 0.3 m/s
    assert res["first_contact_at_s"] == pytest.approx(1.25 / 0.3, abs=0.05)


def test_passing_close_without_contact_is_unsafe_distance():
    # 경로에서 옆으로 1.0 m: 몸 사이 0.45 m(< 0.6), 여유 반경 포함 캡슐과는 닿지 않음
    res = ts.simulate(req(target=(3.0, 0.0), workers=(("worker-01", 1.5, 1.0),)))
    assert res["result"] == "UNSAFE_DISTANCE"
    assert res["first_contact_at_s"] is None
    assert res["min_worker_distance_m"] == pytest.approx(0.45, abs=0.01)


def test_goal_beyond_horizon_is_not_reached():
    res = ts.simulate(req(target=(20.0, 0.0), workers=(), speed=0.3))
    assert res["result"] == "GOAL_NOT_REACHED"
    assert res["simulated_horizon_s"] == pytest.approx(ts.MAX_HORIZON_S)


def test_no_workers_gives_null_distance():
    res = ts.simulate(req(workers=()))
    assert res["result"] == "PASS"
    assert res["min_worker_distance_m"] is None


def test_initial_contact_is_collision_at_zero():
    res = ts.simulate(req(workers=(("worker-01", 0.3, 0.0),)))
    assert res["result"] == "COLLISION"
    assert res["first_contact_at_s"] == 0.0


@pytest.mark.parametrize("change, message", [
    (lambda r: r["action"].update(action_type="STOP"), "unsupported action_type"),
    (lambda r: r["state"].update(workers=None), "worker snapshot is missing"),
    (lambda r: r["state"]["workers"].update(observed_at="2026-11-05T23:59:50Z"), "stale"),
    (lambda r: r["state"]["workers"].update(observed_at="2026-11-06T00:00:05Z"), "after snapshot_at"),
    (lambda r: r.update(contract_version="backend-ctl/0.1"), "contract_version"),
    (lambda r: r.update(snapshot_at=None), "snapshot_at"),
    (lambda r: r["action"].update(robot_id="robot-99"), "not in state.robots"),
    (lambda r: r["action"].update(speed_limit=0), "speed_limit"),
    (lambda r: r["action"]["target_pose"].update(frame_id="map"), "frame_id"),
    (lambda r: r["action"]["target_pose"].update(position=[1.2, -0.8, 0.5]), "planar"),
    (lambda r: r["action"]["target_pose"].update(position=[1.2, -0.8, float("nan")]), "planar"),
])
def test_invalid_requests_are_error_fail_closed(change, message):
    r = req()
    change(r)
    res = ts.simulate(r)
    assert res["result"] == "ERROR"
    assert message in res["detail"]
    assert res["snapshot_id"] == r.get("snapshot_id")
    assert res["min_worker_distance_m"] is None and res["first_contact_at_s"] is None


def test_missing_robot_type_uses_conservative_shape():
    # MOBILE(0.30)이면 닿지 않지만 가장 큰 반경(0.40)이면 닿는 거리
    workers = (("worker-01", 1.5, 0.82),)
    assert ts.simulate(req(target=(3.0, 0.0), workers=workers))["result"] != "COLLISION"
    assert ts.simulate(req(target=(3.0, 0.0), workers=workers, robot_type=None))["result"] == "COLLISION"


@pytest.fixture
def server():
    srv = ts.make_server(port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def post(url, body):
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    r = request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
    try:
        with request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_http_endpoint(server):
    status, body = post(f"{server}/twin/simulate", req())
    assert status == 200 and body["result"] == "PASS" and body["snapshot_id"] == "snap-1"
    status, body = post(f"{server}/twin/simulate", b"{not json")
    assert status == 400 and body["result"] == "ERROR"
    status, _ = post(f"{server}/other", req())
    assert status == 404


@pytest.mark.parametrize("yaw", [0.0, 1.0, -2.5])
def test_robot_away_from_origin_moves_to_world_target(yaw):
    # 로봇이 원점이 아닌 곳에서 yaw로 돌아 있어도 목표는 월드 좌표다
    r = req(target=(-1.0, -1.0), workers=())
    r["state"]["zones"] = []
    r["state"]["robots"][0].update(position=[-2.0, 0.0, 0.0], yaw_rad=yaw)
    res = ts.simulate(r)
    assert res["result"] == "PASS", res
    assert math.sqrt(2) / 0.3 < res["simulated_horizon_s"] < ts.MAX_HORIZON_S


def test_worker_on_path_of_robot_away_from_origin_is_collision():
    r = req(target=(-1.0, -1.0), workers=(("worker-01", -1.5, -0.5),))
    r["state"]["zones"] = []
    r["state"]["robots"][0].update(position=[-2.0, 0.0, 0.0], yaw_rad=0.7)
    assert ts.simulate(r)["result"] == "COLLISION"


def test_controller_constants_match_adapter():
    """사전 시뮬레이션이 굴리는 제어기는 실제 어댑터와 같아야 한다 (PR #92 리뷰 P1)."""
    from robot_adapter import diff_drive_core as dd
    assert ts.DD_HEADING_TOLERANCE_RAD == dd.HEADING_TOLERANCE_RAD
    assert ts.DD_K_ANGULAR == dd.K_ANGULAR
    assert ts.DD_K_LINEAR == dd.K_LINEAR
    assert ts.DD_MAX_ANGULAR == dd.MAX_ANGULAR
    assert ts.GOAL_TOLERANCE_M == dd.ARRIVAL_TOLERANCE_M


@pytest.mark.parametrize("other_y, expected", [(0.75, "PASS"), (0.55, "COLLISION")])
def test_misaligned_start_turns_in_place_then_drives_straight(other_y, expected):
    """시작 yaw 49°에서 (3, 0)으로 간다. 리뷰 재현 사례(PR #92 P1)에서는 회전하며 전진해 경로가 직선 위로 휘어
    (0.55, 0.75)의 로봇과 닿았다. 이제 제자리에서 방향을 맞춘 뒤 직선으로 가므로 직선에서 0.15 m 떨어진
    로봇은 지나가고, 직선과 두 반경 합(0.6 m)보다 가까운 로봇은 여전히 충돌로 잡는다."""
    r = req(target=(3.0, 0.0), workers=(), speed=0.4)
    r["state"]["robots"][0]["yaw_rad"] = math.radians(49)
    r["state"]["robots"].append({"robot_id": "robot-02", "frame_id": "world", "position": [0.55, other_y, 0.0],
                                 "yaw_rad": 0.0, "robot_type": "MOBILE"})
    r["state"]["zones"] = []
    assert ts.simulate(r)["result"] == expected


def test_twin_robot_yaw_is_radians():
    """yaw_rad는 라디안이다. MuJoCo euler 기본 단위(도)로 읽히면 회전이 57배 작아진다."""
    from twin import build_twin
    state = req()["state"]
    state["robots"][0]["yaw_rad"] = math.radians(49)
    model, _ = build_twin(state)
    quat = model.body("robot:robot-01").quat
    assert 2 * math.atan2(quat[3], quat[0]) == pytest.approx(math.radians(49), abs=1e-6)
