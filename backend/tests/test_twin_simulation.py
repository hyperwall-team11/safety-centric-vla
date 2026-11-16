"""MuJoCo ② 사전 시뮬레이션(승인 규칙 9번 COLLISION) 호출 테스트 (이슈 #59).

근거: docs/backend-ctl-contract-v0.1.md 13장. CTL 서비스는 가짜 시뮬레이터로 바꿔 Backend 쪽 규약만 시험한다.

    cd backend && python3 -m pytest -q tests/test_twin_simulation.py
"""
import json
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import app, seed_rules
from app.store import store
from app.twin_simulation import (
    COLLISION_RULE_ID,
    CONTRACT_VERSION,
    MAX_SIMULATION_ATTEMPTS,
    TWIN_SIMULATION_ENABLED_ENV,
    HttpTwinSimulator,
    TwinSimulationError,
)

WORLD_POSE = {
    "frame_id": "world",
    "position": {"x": 0, "y": 0, "z": 0},
    # yaw 90도
    "orientation": {"x": 0, "y": 0, "z": 0.7071067811865476, "w": 0.7071067811865476},
}
NAV_TARGET = {"x": 2.0, "y": 1.0, "z": 0.0}


class FakeSimulator:
    """요청을 기록하고 정해 둔 응답을 돌려준다. on_call로 대기 중 상태 변화를 흉내 낸다."""

    def __init__(self):
        self.payloads = []
        self.result = "PASS"
        self.response = None  # 응답 전체를 바꿀 때 (payload -> 응답)
        self.error = None
        self.on_call = None

    def simulate(self, payload):
        self.payloads.append(json.loads(json.dumps(payload)))  # JSON 직렬화 가능 확인
        if self.on_call:
            self.on_call(payload)
        if self.error:
            raise self.error
        if self.response is not None:
            return self.response(payload)
        return {
            "result": self.result,
            "snapshot_id": payload["snapshot_id"],
            "min_worker_distance_m": 1.5,
            "first_contact_at_s": None,
            "simulated_horizon_s": 7.45,
            "elapsed_ms": 12.3,
        }


def worker(x=3.0, y=3.0):
    return {
        "worker_id": "worker-01",
        "pose": {**WORLD_POSE, "position": {"x": x, "y": y, "z": 0}},
        "current_zone_id": "A구역",
        "safety_status": "SAFE",
        "last_seen_at": datetime.now(timezone.utc).isoformat(),
        "confidence": 1,
    }


def put_workers(client, workers, *, observed_at=None):
    observed_at = observed_at or datetime.now(timezone.utc)
    response = client.put("/workers/snapshot", json={
        "observed_at": observed_at.isoformat(),
        "workers": workers,
    })
    assert response.status_code == 200, response.text


@pytest.fixture
def sim(monkeypatch):
    fake = FakeSimulator()
    monkeypatch.setattr(main, "twin_simulator", fake)
    return fake


@pytest.fixture
def client(monkeypatch, sim):
    monkeypatch.setenv(TWIN_SIMULATION_ENABLED_ENV, "1")
    store.reset()
    seed_rules()
    c = TestClient(app)
    r = c.post("/robots", json={
        "robot_id": "robot-01",
        "robot_type": "MOBILE",
        "control_adapter": "mock-mobile-adapter",
        "status": "IDLE",
        "pose": WORLD_POSE,
        "velocity": {"x": 0, "y": 0, "z": 0},
        "motion_limits": {"max_speed": 1.0, "max_acceleration": 0.5},
    })
    assert r.status_code == 201, r.text
    r = c.post("/zones", json={
        "zone_id": "A구역",
        "zone_type": "WORK",
        "frame_id": "world",
        "geometry": {
            "type": "polygon",
            "points": [[1, 0], [3, 0], [3, 2], [1, 2]],
            "navigation_target": NAV_TARGET,
        },
        "minimum_safety_distance": 1.2,
    })
    assert r.status_code == 201, r.text
    put_workers(c, [worker()])
    return c


def propose(client, *, speed=0.3, trajectory=None):
    r = client.post(
        "/task-orders/from-natural-language",
        json={"instruction": "robot-01 로봇을 A구역으로 이동해"},
    )
    assert r.status_code == 201, r.text
    r = client.post("/action-proposals", json={
        "task_id": r.json()["task_id"],
        "robot_id": "robot-01",
        "action_type": "MOVE_TO",
        "parameters": {"target_pose": {"type": "ZONE", "zone_id": "A구역"}, "speed_limit": speed},
        "trajectory": trajectory,
        "frame_id": "world",
        "speed_limit": speed,
        "model_name": "mock-vla",
        "model_version": "test-0.1",
    })
    assert r.status_code == 201, r.text
    return r.json()["proposal_id"]


def evaluate(client, proposal_id):
    r = client.post(f"/action-proposals/{proposal_id}/evaluate")
    assert r.status_code == 200, r.text
    return r.json()["status"], store.logs[-1]


def collision_rule(log):
    return next(rule for rule in log.rule_results if rule.rule_id == COLLISION_RULE_ID)


def test_rule_is_disabled_by_default(monkeypatch, sim):
    monkeypatch.delenv(TWIN_SIMULATION_ENABLED_ENV, raising=False)
    store.reset()
    seed_rules()
    assert store.rules[COLLISION_RULE_ID].enabled is False
    assert store.rules[COLLISION_RULE_ID].rule_type == "COLLISION"


def test_seed_rules_adds_new_default_rules_to_existing_store(client):
    # 영속 저장소에서 복구한 규칙 목록에 새 기본 규칙이 없어도 추가되고, 기존 규칙은 덮어쓰지 않는다
    del store.rules[COLLISION_RULE_ID]
    other = next(rule for rule in store.rules.values() if rule.rule_id != COLLISION_RULE_ID)
    other.enabled = not other.enabled
    seed_rules()
    assert store.rules[COLLISION_RULE_ID].enabled is True
    assert store.rules[other.rule_id].enabled is other.enabled


def test_pass_approves_and_sends_contract_request(client, sim):
    proposal_id = propose(client)
    status, log = evaluate(client, proposal_id)

    assert status == "APPROVED"
    assert len(sim.payloads) == 1
    payload = sim.payloads[0]
    assert payload["contract_version"] == CONTRACT_VERSION
    assert payload["proposal_id"] == proposal_id
    assert payload["snapshot_id"].startswith("snap-")
    assert payload["action"] == {
        "action_type": "MOVE_TO",
        "robot_id": "robot-01",
        # ZONE 목적지는 4.1절대로 navigation_target 좌표로 보낸다
        "target_pose": {"frame_id": "world", "position": [2.0, 1.0, 0.0]},
        "speed_limit": 0.3,
    }
    robot = payload["state"]["robots"][0]
    assert robot["robot_id"] == "robot-01" and robot["robot_type"] == "MOBILE"
    assert robot["frame_id"] == "world" and robot["position"] == [0.0, 0.0, 0.0]
    assert robot["yaw_rad"] == pytest.approx(1.5708, abs=1e-4)
    workers = payload["state"]["workers"]
    assert workers["observed_at"].endswith("Z")
    assert workers["items"] == [{"worker_id": "worker-01", "frame_id": "world",
                                 "position": [3.0, 3.0, 0.0]}]
    assert payload["state"]["zones"][0]["zone_id"] == "A구역"

    rule = collision_rule(log)
    assert rule.passed and payload["snapshot_id"] in rule.reason
    assert store.twin_simulation_passes[proposal_id] == payload["snapshot_id"]


@pytest.mark.parametrize("result", ["COLLISION", "UNSAFE_DISTANCE", "GOAL_NOT_REACHED", "ERROR"])
def test_every_non_pass_result_rejects(client, sim, result):
    sim.result = result
    status, log = evaluate(client, propose(client))
    assert status == "REJECTED"
    rule = collision_rule(log)
    assert not rule.passed and result in rule.reason
    assert store.robots["robot-01"].status == "IDLE"  # 거절이며 E-Stop이 아니다
    assert not store.twin_simulation_passes


def drop(field):
    def response(payload):
        body = FakeSimulator().simulate(payload)
        body.pop(field)
        return body
    return response


@pytest.mark.parametrize("setup, reason", [
    (lambda s: setattr(s, "error", TwinSimulationError("연결 실패 또는 시간 초과: timed out")), "시간 초과"),
    (lambda s: setattr(s, "error", RuntimeError("bug")), "RuntimeError"),
    (lambda s: setattr(s, "response", lambda p: ["PASS"]), "JSON 객체가 아님"),
    (lambda s: setattr(s, "response", drop("first_contact_at_s")), "first_contact_at_s"),
    (lambda s: setattr(s, "response", lambda p: {**FakeSimulator().simulate(p), "result": "OK"}),
     "알 수 없는 result"),
    (lambda s: setattr(s, "response", lambda p: {**FakeSimulator().simulate(p), "snapshot_id": "snap-x"}),
     "다른 snapshot_id"),
])
def test_failures_and_malformed_responses_fail_closed(client, sim, setup, reason):
    setup(sim)
    status, log = evaluate(client, propose(client))
    assert status == "REJECTED"
    rule = collision_rule(log)
    assert not rule.passed and reason in rule.reason


def test_only_candidates_passing_rules_1_to_8_are_simulated(client, sim):
    status, log = evaluate(client, propose(client, speed=0.9))  # 기본 속도 상한 0.4 초과
    assert status == "REJECTED"
    assert sim.payloads == []
    assert all(rule.rule_id != COLLISION_RULE_ID for rule in log.rule_results)


def test_missing_worker_snapshot_is_sent_as_null(client, sim):
    store.workers = None
    store.workers_observed_at = None
    evaluate(client, propose(client))
    assert sim.payloads[0]["state"]["workers"] is None


def test_store_lock_is_released_while_waiting(client, sim):
    acquired = []

    def other_request(_payload):
        # 다른 요청 스레드(위험 이벤트 등록, E-Stop)가 대기 중에 저장소를 쓸 수 있어야 한다
        def try_lock():
            ok = store.lock.acquire(timeout=1.0)
            acquired.append(ok)
            if ok:
                store.lock.release()

        thread = threading.Thread(target=try_lock)
        thread.start()
        thread.join()

    sim.on_call = other_request
    status, _ = evaluate(client, propose(client))
    assert status == "APPROVED"
    assert acquired == [True]


def test_hazard_registered_while_waiting_is_reflected_in_decision(client, sim):
    def register_hazard(_payload):
        response = client.post("/hazards", json={
            "hazard_type": "FIRE",
            "severity": "CRITICAL",
            "source_observation_ids": ["obs-01"],
            "related_robot_ids": ["robot-01"],
            "related_worker_ids": [],
            "zone_id": "A구역",
            "confidence": 0.99,
        })
        assert response.status_code == 201, response.text

    sim.on_call = register_hazard
    status, log = evaluate(client, propose(client))
    assert status == "REJECTED"
    assert any(rule.rule_id == "rule-critical-hazard" and not rule.passed for rule in log.rule_results)


def test_evaluate_resimulates_when_state_changed_during_simulation(client, sim):
    def new_snapshot_once(_payload):
        if len(sim.payloads) == 1:
            time.sleep(0.001)
            put_workers(client, [worker(x=3.5)])

    sim.on_call = new_snapshot_once
    proposal_id = propose(client)
    status, log = evaluate(client, proposal_id)
    assert status == "APPROVED"
    assert len(sim.payloads) == 2
    assert sim.payloads[1]["state"]["workers"]["items"][0]["position"] == [3.5, 3.0, 0.0]
    # 평가 완료 시점 상태로 시뮬레이션한 PASS만 기록·보관한다
    assert sim.payloads[1]["snapshot_id"] in collision_rule(log).reason
    assert store.twin_simulation_passes[proposal_id] == sim.payloads[1]["snapshot_id"]


def test_evaluate_rejects_when_state_keeps_changing_during_simulation(client, sim):
    def new_snapshot(_payload):
        time.sleep(0.001)
        put_workers(client, [worker()])

    sim.on_call = new_snapshot
    proposal_id = propose(client)
    status, log = evaluate(client, proposal_id)
    assert status == "REJECTED"
    assert len(sim.payloads) == MAX_SIMULATION_ATTEMPTS
    rule = collision_rule(log)
    assert not rule.passed and "상태가 바뀌어" in rule.reason
    assert proposal_id not in store.twin_simulation_passes


def test_evaluate_keeps_violation_from_older_snapshot(client, sim):
    def collide_then_change(_payload):
        time.sleep(0.001)
        put_workers(client, [worker(x=3.5)])

    sim.result = "COLLISION"
    sim.on_call = collide_then_change
    status, log = evaluate(client, propose(client))
    assert status == "REJECTED"
    assert len(sim.payloads) == 1  # 위반은 상태가 바뀌어도 다시 시뮬레이션하지 않고 차단한다
    assert "COLLISION" in collision_rule(log).reason


def straight(end=NAV_TARGET):
    return [{"position": {"x": 0.0, "y": 0.0, "z": 0.0}}, {"position": end}]


def test_straight_trajectory_to_target_is_simulated(client, sim):
    status, _ = evaluate(client, propose(client, trajectory=straight()))
    assert status == "APPROVED"
    assert len(sim.payloads) == 1


@pytest.mark.parametrize("trajectory, reason", [
    # 트윈·CTL 어댑터는 직선으로 움직이므로 경유점을 지나는 경로는 검증할 수 없다
    ([*straight()[:1], {"position": {"x": 1.0, "y": 0.5, "z": 0.0}}, straight()[1]], "경유점"),
    (straight({"x": 2.5, "y": 1.0, "z": 0.0}), "trajectory 끝점"),
])
def test_trajectory_different_from_simulated_path_is_blocked(client, sim, trajectory, reason):
    status, log = evaluate(client, propose(client, trajectory=trajectory))
    assert status == "REJECTED"
    assert sim.payloads == []
    rule = collision_rule(log)
    assert not rule.passed and reason in rule.reason


def approved(client, sim):
    proposal_id = propose(client)
    status, _ = evaluate(client, proposal_id)
    assert status == "APPROVED"
    sim.payloads.clear()
    return proposal_id


def test_execute_reuses_pass_only_for_same_snapshot(client, sim):
    proposal_id = approved(client, sim)
    r = client.post(f"/action-proposals/{proposal_id}/execute")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "EXECUTING"
    assert sim.payloads == []
    rule = collision_rule(store.logs[-1])
    assert rule.passed and "재사용" in rule.reason
    assert proposal_id not in store.twin_simulation_passes


def test_execute_resimulates_when_state_changed(client, sim):
    proposal_id = approved(client, sim)
    put_workers(client, [worker(x=2.5, y=0.5)])  # 새 Worker 스냅샷 → snapshot_id 변경
    sim.result = "COLLISION"
    r = client.post(f"/action-proposals/{proposal_id}/execute")
    assert r.status_code == 409, r.text
    assert len(sim.payloads) == 1
    assert sim.payloads[0]["state"]["workers"]["items"][0]["position"] == [2.5, 0.5, 0.0]
    proposal = store.proposals[proposal_id]
    assert proposal.status == "HELD"
    assert store.robots["robot-01"].status == "IDLE"  # 시뮬레이션 거부로 E-Stop하지 않는다
    log = store.logs[-1]
    assert log.decision == "REJECT" and not collision_rule(log).passed


def test_execute_after_fresh_pass_dispatches(client, sim):
    proposal_id = approved(client, sim)
    put_workers(client, [worker(x=3.5)])
    r = client.post(f"/action-proposals/{proposal_id}/execute")
    assert r.status_code == 200, r.text
    assert len(sim.payloads) == 1
    rule = collision_rule(store.logs[-1])
    assert rule.passed and sim.payloads[0]["snapshot_id"] in rule.reason


def test_execute_rejects_when_state_keeps_changing_during_simulation(client, sim):
    proposal_id = approved(client, sim)
    put_workers(client, [worker()])  # 평가 때의 PASS를 쓸 수 없게 상태를 바꾼다

    def new_snapshot(_payload):
        # CV가 시뮬레이션 대기 중마다 새 Worker 스냅샷을 보낸다
        time.sleep(0.001)
        put_workers(client, [worker()])

    sim.on_call = new_snapshot
    r = client.post(f"/action-proposals/{proposal_id}/execute")
    assert r.status_code == 409, r.text
    assert len(sim.payloads) == MAX_SIMULATION_ATTEMPTS
    rule = collision_rule(store.logs[-1])
    assert not rule.passed and "상태가 바뀌어" in rule.reason


def test_manipulate_is_blocked_until_contract_supports_it(client, sim):
    r = client.post(
        "/task-orders/from-natural-language",
        json={"instruction": "robot-01 로봇을 A구역으로 이동해"},
    )
    proposal = {
        "task_id": r.json()["task_id"],
        "robot_id": "robot-01",
        "action_type": "MANIPULATE",
        "parameters": {"manipulation": "PICK", "object_id": "box-1",
                       "target_pose": {"type": "ZONE", "zone_id": "A구역"}},
        "trajectory": None,
        "frame_id": "world",
        "speed_limit": 0.2,
        "model_name": "mock-vla",
        "model_version": "test-0.1",
    }
    r = client.post("/action-proposals", json=proposal)
    assert r.status_code == 201, r.text
    status, log = evaluate(client, r.json()["proposal_id"])
    assert status == "REJECTED"
    assert sim.payloads == []
    assert "MANIPULATE" in collision_rule(log).reason


# ---- HTTP 클라이언트 ----

class _Handler(BaseHTTPRequestHandler):
    delay_s = 0.0
    status = 200

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        time.sleep(self.delay_s)
        body = json.dumps({"result": "PASS", "snapshot_id": payload["snapshot_id"]}).encode()
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    _Handler.delay_s, _Handler.status = 0.0, 200


def url(httpd):
    return f"http://127.0.0.1:{httpd.server_address[1]}/twin/simulate"


def test_http_client_posts_json(server):
    body = HttpTwinSimulator(url(server)).simulate({"snapshot_id": "snap-1"})
    assert body == {"result": "PASS", "snapshot_id": "snap-1"}


def test_http_client_timeout_non_2xx_and_refused_raise(server):
    _Handler.delay_s = 0.5
    with pytest.raises(TwinSimulationError, match="시간 초과"):
        HttpTwinSimulator(url(server), timeout_s=0.1).simulate({"snapshot_id": "snap-1"})
    _Handler.delay_s, _Handler.status = 0.0, 503
    with pytest.raises(TwinSimulationError, match="HTTP 503"):
        HttpTwinSimulator(url(server)).simulate({"snapshot_id": "snap-1"})
    with pytest.raises(TwinSimulationError, match="연결 실패"):
        HttpTwinSimulator("http://127.0.0.1:9/twin/simulate", timeout_s=0.2).simulate({})
