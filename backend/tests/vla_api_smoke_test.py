#!/usr/bin/env python3
"""Safety-Centric VLA: non-executing HTTP smoke test against local backend.

Creates a unique mock robot, two natural-language tasks, and external ActionProposals.
Only evaluates proposals: NEVER calls the /execute endpoint or ROS 2.
"""
import argparse
import json
import sys
import urllib.error
import urllib.request
from uuid import uuid4


def api(base, method, path, payload=None):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        base.rstrip("/") + path,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} -> HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{method} {path} -> 연결 실패: {exc.reason}") from exc


def make_task(base, robot_id):
    return api(base, "POST", "/task-orders/from-natural-language", {
        "instruction": "로봇을 좌표 (1.5, -2, 0) 위치로 이동해",
        "target_robot_id": robot_id,
        "frame_id": "map",
    })


def make_proposal(base, task, robot_id, speed):
    return api(base, "POST", "/action-proposals", {
        "task_id": task["task_id"],
        "robot_id": robot_id,
        "action_type": "MOVE_TO",
        "parameters": {
            "target_pose": task["destination"],
            "speed_limit": speed,
        },
        "trajectory": None,
        "frame_id": "map",
        "speed_limit": speed,
        "model_name": "mock-vla",
        "model_version": "api-smoke-test-v1",
    })


def main():
    parser = argparse.ArgumentParser(description="ActionProposal API 연결 및 안전 판정 테스트")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    base = args.base_url

    health = api(base, "GET", "/health")
    assert health.get("status") == "ok", f"헬스체크 실패: {health}"
    print("[PASS] 1. 백엔드 연결: /health = ok")

    robot_id = "mock-vla-" + uuid4().hex[:10]
    api(base, "POST", "/robots", {
        "robot_id": robot_id,
        "robot_type": "MOBILE",
        "control_adapter": "mock-mobile-adapter",
        "status": "IDLE",
        "pose": {"frame_id": "map", "position": {"x": 0, "y": 0, "z": 0},
                 "orientation": {"x": 0, "y": 0, "z": 0, "w": 1}},
        "velocity": {"x": 0, "y": 0, "z": 0},
        "motion_limits": {"max_speed": 1.0, "max_acceleration": 0.5},
    })
    print(f"[PASS] 2. 테스트 로봇 등록: {robot_id}")

    # 정상 제안: 속도 0.2 m/s (기본 상한 0.4 m/s 이하)
    safe_task = make_task(base, robot_id)
    safe_proposal = make_proposal(base, safe_task, robot_id, speed=0.2)
    assert safe_proposal["status"] == "PROPOSED", safe_proposal
    evaluated_safe = api(base, "POST", f"/action-proposals/{safe_proposal['proposal_id']}/evaluate")
    print(f"[{'PASS' if evaluated_safe['status'] == 'APPROVED' else 'FAIL'}] 3. 안전한 제안: "
          f"{safe_proposal['proposal_id']} → {evaluated_safe['status']}")
    assert evaluated_safe["status"] == "APPROVED", evaluated_safe

    # 위험 제안: 속도 0.8 m/s (기본 상한 0.4 m/s 위반)
    unsafe_task = make_task(base, robot_id)
    unsafe_proposal = make_proposal(base, unsafe_task, robot_id, speed=0.8)
    assert unsafe_proposal["status"] == "PROPOSED", unsafe_proposal
    evaluated_unsafe = api(base, "POST", f"/action-proposals/{unsafe_proposal['proposal_id']}/evaluate")
    print(f"[{'PASS' if evaluated_unsafe['status'] == 'REJECTED' else 'FAIL'}] 4. 과속 제안: "
          f"{unsafe_proposal['proposal_id']} → {evaluated_unsafe['status']}")
    assert evaluated_unsafe["status"] == "REJECTED", evaluated_unsafe

    logs = api(base, "GET", "/execution-logs")
    decisions = {
        log["proposal_id"]: log["decision"] for log in logs
        if log.get("proposal_id") in {safe_proposal["proposal_id"], unsafe_proposal["proposal_id"]}
    }
    print(f"[PASS] 5. 감사 로그: {json.dumps(decisions, ensure_ascii=False)}")
    assert decisions.get(safe_proposal["proposal_id"]) == "APPROVE", decisions
    assert decisions.get(unsafe_proposal["proposal_id"]) in ("REJECT", "ESTOP"), decisions

    print("\n모두 통과! Mock VLA → ActionProposal → 안전 평가 → 감사 로그 연결 확인.")
    print("주의: /execute는 호출하지 않았으며, OpenVLA 추론이나 로봇 제어는 검증하지 않았습니다.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, AssertionError, KeyError, ValueError) as error:
        print(f"\n[FAIL] {error}", file=sys.stderr)
        sys.exit(1)
