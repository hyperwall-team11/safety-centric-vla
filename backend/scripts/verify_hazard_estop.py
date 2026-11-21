from __future__ import annotations

import argparse
import json
import sys
from time import monotonic, sleep
from typing import Any, Callable
from uuid import uuid4

from benchmark_command_to_dispatch import (
    BackendClient,
    register_robot,
    wait_for_ack,
)


POLL_INTERVAL_SECONDS = 0.02


def register_hazard_zone(
    client: BackendClient,
    zone_id: str,
    frame_id: str,
) -> None:
    if any(zone.get("zone_id") == zone_id for zone in client.get("/zones")):
        return
    client.post(
        "/zones",
        {
            "zone_id": zone_id,
            "zone_type": "WORK",
            "geometry": {
                "type": "polygon",
                "points": [[-1, -1], [1, -1], [1, 1], [-1, 1]],
            },
            "frame_id": frame_id,
            "minimum_safety_distance": 0,
        },
    )


def _find(items: list[dict[str, Any]], key: str, value: str) -> dict[str, Any]:
    found = next((item for item in items if item.get(key) == value), None)
    if found is None:
        raise RuntimeError(f"{key}={value} 상태를 조회할 수 없습니다.")
    return found


def wait_for_estop_state(
    client: BackendClient,
    robot_id: str,
    task_id: str,
    proposal_id: str,
    timeout_seconds: float,
    *,
    clock: Callable[[], float] = monotonic,
    sleep_fn: Callable[[float], None] = sleep,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    deadline = clock() + timeout_seconds
    last_states: tuple[str | None, str | None, str | None] = (None, None, None)
    while True:
        robot = _find(client.get("/robots"), "robot_id", robot_id)
        task = client.get(f"/task-orders/{task_id}")
        proposals = client.get(f"/task-orders/{task_id}/action-proposals")
        proposal = _find(proposals, "proposal_id", proposal_id)
        last_states = (
            robot.get("status"),
            task.get("status"),
            proposal.get("status"),
        )
        if last_states == ("ESTOP", "HELD", "HELD"):
            return robot, task, proposal
        if clock() >= deadline:
            raise RuntimeError(
                "E-Stop 상태 반영 대기 시간을 초과했습니다: "
                f"Robot={last_states[0]}, Task={last_states[1]}, "
                f"Proposal={last_states[2]}"
            )
        sleep_fn(POLL_INTERVAL_SECONDS)


def run_hazard_scenario(
    client: BackendClient,
    robot_id: str,
    control_adapter: str,
    frame_id: str,
    ack_timeout_seconds: float,
    state_timeout_seconds: float,
    hazard_delay_seconds: float,
    *,
    clock: Callable[[], float] = monotonic,
    sleep_fn: Callable[[float], None] = sleep,
) -> dict[str, Any]:
    zone_id = "e2e-hazard-zone"
    register_robot(client, robot_id, frame_id, control_adapter)
    register_hazard_zone(client, zone_id, frame_id)

    task = client.post(
        "/task-orders/from-natural-language",
        {
            "instruction": f"{robot_id} 로봇을 좌표 (1, 0, 0)로 이동해",
            "frame_id": frame_id,
        },
        {"Idempotency-Key": f"hazard-e2e-task-{uuid4()}"},
    )
    task_id = task["task_id"]
    proposal = client.post(
        f"/task-orders/{task_id}/action-proposals",
        {},
        {"Idempotency-Key": f"hazard-e2e-proposal-{uuid4()}"},
    )
    proposal_id = proposal["proposal_id"]
    evaluated = client.post(f"/action-proposals/{proposal_id}/evaluate", {})
    if evaluated.get("status") != "APPROVED":
        raise RuntimeError(
            f"안전 평가가 승인되지 않았습니다: {evaluated.get('status')}"
        )
    executing = client.post(f"/action-proposals/{proposal_id}/execute", {})
    if executing.get("status") != "EXECUTING":
        raise RuntimeError(
            f"Action publish 상태가 EXECUTING이 아닙니다: {executing.get('status')}"
        )

    ack = wait_for_ack(client, proposal_id, ack_timeout_seconds)
    if ack.get("status") != "ACKNOWLEDGED":
        raise RuntimeError(f"어댑터 ACK를 받지 못했습니다: {ack.get('status')}")
    sleep_fn(hazard_delay_seconds)

    started_at = clock()
    hazard = client.post(
        "/hazards",
        {
            "hazard_type": "WORKER_INTRUSION",
            "severity": "CRITICAL",
            "source_observation_ids": [f"hazard-e2e@{uuid4()}"],
            "related_robot_ids": [robot_id],
            "related_worker_ids": [],
            "zone_id": zone_id,
            "confidence": 1.0,
        },
    )
    robot, held_task, held_proposal = wait_for_estop_state(
        client,
        robot_id,
        task_id,
        proposal_id,
        state_timeout_seconds,
        clock=clock,
        sleep_fn=sleep_fn,
    )
    estop_roundtrip_ms = (clock() - started_at) * 1000
    return {
        "task_id": task_id,
        "proposal_id": proposal_id,
        "hazard_id": hazard["hazard_id"],
        "ack_ms": ack.get("command_to_ack_ms"),
        "estop_roundtrip_ms": round(estop_roundtrip_ms, 2),
        "robot_status": robot["status"],
        "task_status": held_task["status"],
        "proposal_status": held_proposal["status"],
    }


def main(
    argv: list[str] | None = None,
    client_factory: Callable[[str, float], BackendClient] = BackendClient,
) -> int:
    parser = argparse.ArgumentParser(
        description="실행 중 CRITICAL HazardEvent → E-Stop 상태 왕복 검증"
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--robot-id", default="robot-01")
    parser.add_argument("--control-adapter", default="tiago-adapter")
    parser.add_argument("--frame-id", default="world")
    parser.add_argument("--timeout", type=float, default=5)
    parser.add_argument("--ack-timeout", type=float, default=3)
    parser.add_argument("--state-timeout", type=float, default=5)
    parser.add_argument("--hazard-delay", type=float, default=0.2)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.ack_timeout <= 0 or args.state_timeout <= 0:
        parser.error("대기 시간은 0보다 커야 합니다.")
    if args.hazard_delay < 0:
        parser.error("--hazard-delay는 0 이상이어야 합니다.")

    client = client_factory(args.base_url, args.timeout)
    try:
        report = run_hazard_scenario(
            client,
            args.robot_id,
            args.control_adapter,
            args.frame_id,
            args.ack_timeout,
            args.state_timeout,
            args.hazard_delay,
        )
    except (KeyError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("CRITICAL HazardEvent → E-Stop E2E 통과")
        print(
            f"ACK {report['ack_ms']}ms · E-Stop 상태 왕복 "
            f"{report['estop_roundtrip_ms']}ms"
        )
        print(
            f"Robot {report['robot_status']} · Task {report['task_status']} · "
            f"Proposal {report['proposal_status']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
