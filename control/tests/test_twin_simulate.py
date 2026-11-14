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
    assert res["simulated_horizon_s"] == pytest.approx(1.4422 / 0.3, abs=0.01)  # 1.4422 m / 0.3 m/s
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
    (lambda r: r["action"].update(action_type="MANIPULATE"), "unsupported action_type"),
    (lambda r: r["state"].update(workers=None), "worker snapshot is missing"),
    (lambda r: r["state"]["workers"].update(observed_at="2026-11-05T23:59:50Z"), "stale"),
    (lambda r: r["state"]["workers"].update(observed_at="2026-11-06T00:00:05Z"), "after snapshot_at"),
    (lambda r: r.update(contract_version="backend-ctl/0.1"), "contract_version"),
    (lambda r: r.update(snapshot_at=None), "snapshot_at"),
    (lambda r: r["action"].update(robot_id="robot-99"), "not in state.robots"),
    (lambda r: r["action"].update(speed_limit=0), "speed_limit"),
    (lambda r: r["action"]["target_pose"].update(frame_id="map"), "frame_id"),
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
    assert res["simulated_horizon_s"] == pytest.approx(math.sqrt(2) / 0.3, abs=0.01)


def test_worker_on_path_of_robot_away_from_origin_is_collision():
    r = req(target=(-1.0, -1.0), workers=(("worker-01", -1.5, -0.5),))
    r["state"]["zones"] = []
    r["state"]["robots"][0].update(position=[-2.0, 0.0, 0.0], yaw_rad=0.7)
    assert ts.simulate(r)["result"] == "COLLISION"
