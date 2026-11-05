
"""Sensor COVERS Zone / OBSERVATION_GAP contract tests.

실행:
    cd backend
    python3 -m pytest -v -rx tests/test_sensor_coverage.py

현재 Sensor 저장소와 안전 판정 연결은 미구현.
XFAIL은 앞으로 구현해야 하는 계약을 표현한다.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app, seed_rules
from app.models import Sensor
from app.store import store


POSE = {
    "frame_id": "map",
    "position": {"x": 0, "y": 0, "z": 0},
    "orientation": {"x": 0, "y": 0, "z": 0, "w": 1},
}


@pytest.fixture
def client(monkeypatch):
    store.reset()
    seed_rules()

    # 테스트 전용 가상 센서 저장소
    monkeypatch.setattr(store, "sensors", {}, raising=False)

    c = TestClient(app)

    r = c.post("/robots", json={
        "robot_id": "robot-01",
        "robot_type": "MOBILE",
        "control_adapter": "mock-mobile-adapter",
        "status": "IDLE",
        "pose": POSE,
        "velocity": {"x": 0, "y": 0, "z": 0},
        "motion_limits": {
            "max_speed": 1.0,
            "max_acceleration": 0.5,
        },
    })
    assert r.status_code == 201, r.text

    r = c.post("/zones", json={
        "zone_id": "A구역",
        "zone_type": "WORK",
        "frame_id": "map",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [0, 0], [4, 0], [4, 4],
                [0, 4], [0, 0],
            ]],
        },
        "minimum_safety_distance": 1.2,
    })
    assert r.status_code == 201, r.text

    return c


def sensor(sensor_id, status, covered_zones, *, age_seconds=0):
    return Sensor.model_validate({
        "sensor_id": sensor_id,
        "sensor_type": "CCTV",
        "source_uri": f"rtsp://{sensor_id}",
        "frame_id": f"camera_{sensor_id}",
        "pose": POSE,
        "status": status,
        "mounted_robot_id": None,
        "covered_zone_ids": covered_zones,
        "last_frame_at": (
            datetime.now(timezone.utc)
            - timedelta(seconds=age_seconds)
        ),
        "calibrated": True,
    })


def assess(client, *, expected_status, expected_sensor_rule_passed):
    # TaskOrder 생성
    r = client.post(
        "/task-orders/from-natural-language",
        json={"instruction": "robot-01 로봇을 A구역으로 이동해"},
    )
    assert r.status_code == 201, r.text
    task_id = r.json()["task_id"]

    # Mock VLA 행동 제안
    r = client.post("/action-proposals", json={
        "task_id": task_id,
        "robot_id": "robot-01",
        "action_type": "MOVE_TO",
        "parameters": {
            "target_pose": {
                "type": "ZONE",
                "zone_id": "A구역",
            },
            "speed_limit": 0.3,
        },
        "trajectory": [
            {"position": {"x": 0, "y": 0, "z": 0}},
            {"position": {"x": 1, "y": 1, "z": 0}},
        ],
        "frame_id": "map",
        "speed_limit": 0.3,
        "model_name": "mock-vla",
        "model_version": "test-0.1",
    })
    assert r.status_code == 201, r.text

    proposal_id = r.json()["proposal_id"]

    # 안전 규칙 평가
    r = client.post(
        f"/action-proposals/{proposal_id}/evaluate"
    )
    assert r.status_code == 200, r.text

    assert r.json()["status"] == expected_status, r.text

    # 센서 판정 규칙이 로그에 기록되는지 검사
    r = client.get("/execution-logs")
    assert r.status_code == 200, r.text

    log = r.json()[-1]
    rules = {
        item["rule_id"]: item
        for item in log["rule_results"]
    }

    assert "rule-observation-gap" in rules, (
        "OBSERVATION_GAP 판정 결과가 로그에 없음"
    )

    assert (
        rules["rule-observation-gap"]["passed"]
        is expected_sensor_rule_passed
    )


# S00. 센서 객체의 스키마 필드 검증
def test_00_sensor_schema_fields_exist():
    s = sensor("cctv-01", "ONLINE", ["A구역"])

    assert s.status == "ONLINE"
    assert s.covered_zone_ids == ["A구역"]
    assert s.last_frame_at is not None


# 미구현 기능: 구현 전에는 XFAIL 예상
@pytest.mark.xfail(
    strict=True,
    reason="Sensor 연결 및 OBSERVATION_GAP 규칙 미구현",
)
def test_s01_no_sensor_must_reject(client):
    assess(
        client,
        expected_status="REJECTED",
        expected_sensor_rule_passed=False,
    )


@pytest.mark.xfail(
    strict=True,
    reason="Sensor 연결 및 OBSERVATION_GAP 규칙 미구현",
)
def test_s02_offline_covering_sensor_must_reject(client):
    store.sensors["cctv-01"] = sensor(
        "cctv-01", "OFFLINE", ["A구역"]
    )
    assess(
        client,
        expected_status="REJECTED",
        expected_sensor_rule_passed=False,
    )


@pytest.mark.xfail(
    strict=True,
    reason="Sensor 연결 및 OBSERVATION_GAP 규칙 미구현",
)
def test_s03_online_sensor_in_other_zone_must_reject(client):
    store.sensors["cctv-01"] = sensor(
        "cctv-01", "ONLINE", ["B구역"]
    )
    assess(
        client,
        expected_status="REJECTED",
        expected_sensor_rule_passed=False,
    )


@pytest.mark.xfail(
    strict=True,
    reason="Sensor 연결 및 OBSERVATION_GAP 규칙 미구현",
)
def test_s04_online_covering_sensor_should_approve(client):
    store.sensors["cctv-01"] = sensor(
        "cctv-01", "ONLINE", ["A구역"]
    )
    assess(
        client,
        expected_status="APPROVED",
        expected_sensor_rule_passed=True,
    )


@pytest.mark.xfail(
    strict=True,
    reason="Sensor 연결 및 OBSERVATION_GAP 규칙 미구현",
)
def test_s05_one_of_two_covering_sensors_online_should_approve(client):
    store.sensors["cctv-01"] = sensor(
        "cctv-01", "OFFLINE", ["A구역"]
    )
    store.sensors["cctv-02"] = sensor(
        "cctv-02", "ONLINE", ["A구역"]
    )
    assess(
        client,
        expected_status="APPROVED",
        expected_sensor_rule_passed=True,
    )


@pytest.mark.xfail(
    strict=True,
    reason="Sensor 연결 및 OBSERVATION_GAP 규칙 미구현",
)
def test_s06_only_degraded_sensor_must_reject(client):
    store.sensors["cctv-01"] = sensor(
        "cctv-01", "DEGRADED", ["A구역"]
    )
    assess(
        client,
        expected_status="REJECTED",
        expected_sensor_rule_passed=False,
    )


@pytest.mark.xfail(
    strict=True,
    reason="프레임 2초 무수신 시 DEGRADED 전환 규칙 미구현",
)
def test_s07_stale_online_sensor_must_not_count_as_online(client):
    store.sensors["cctv-01"] = sensor(
        "cctv-01",
        "ONLINE",
        ["A구역"],
        age_seconds=5,
    )
    assess(
        client,
        expected_status="REJECTED",
        expected_sensor_rule_passed=False,
    )
