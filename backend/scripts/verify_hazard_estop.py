from __future__ import annotations

import argparse
import json
import re
import sys
import threading
from datetime import datetime, timezone
from time import monotonic, sleep
from typing import Any, Callable
from uuid import uuid4

try:
    from .benchmark_command_to_dispatch import (
        BackendClient,
        register_robot,
        wait_for_ack,
        wait_for_result,
    )
except ImportError:
    from benchmark_command_to_dispatch import (
        BackendClient,
        register_robot,
        wait_for_ack,
        wait_for_result,
    )


POLL_INTERVAL_SECONDS = 0.02


def result_topic(robot_id: str) -> str:
    segment = re.sub(r"[^A-Za-z0-9_]", "_", robot_id)
    if segment[:1].isdigit():
        segment = f"r_{segment}"
    return f"/adapter/{segment}/result"


class RosAdapterEventMonitor:
    """어댑터 result 토픽에서 실제 CTL E-Stop 이벤트를 기다린다."""

    def __init__(self, topic: str, discovery_timeout_seconds: float) -> None:
        try:
            import rclpy
            from rclpy.context import Context
            from rclpy.executors import SingleThreadedExecutor
            from std_msgs.msg import String
        except ImportError as exc:
            raise RuntimeError(
                "ROS 2 Python 모듈을 찾을 수 없습니다. pixi 환경에서 실행하세요."
            ) from exc

        self._context = Context()
        rclpy.init(context=self._context)
        self._node = rclpy.create_node(
            "hazard_estop_event_monitor",
            context=self._context,
        )
        self._executor = SingleThreadedExecutor(context=self._context)
        self._executor.add_node(self._node)
        self._estop_received = threading.Event()
        self._estop_event: dict[str, Any] | None = None

        def on_result(message: Any) -> None:
            try:
                event = json.loads(message.data)
            except (json.JSONDecodeError, TypeError):
                return
            if event.get("kind") == "ESTOP" and self._estop_event is None:
                self._estop_event = event
                self._estop_received.set()

        self._subscription = self._node.create_subscription(
            String,
            topic,
            on_result,
            10,
        )
        self._spin_thread = threading.Thread(
            target=self._executor.spin,
            name="hazard-estop-result-monitor",
            daemon=True,
        )
        self._spin_thread.start()

        deadline = monotonic() + discovery_timeout_seconds
        while not self._node.get_publishers_info_by_topic(topic):
            if monotonic() >= deadline:
                self.close()
                raise RuntimeError(f"어댑터 result 토픽 발행자를 찾을 수 없습니다: {topic}")
            sleep(POLL_INTERVAL_SECONDS)

    def wait_for_estop(self, robot_id: str, timeout_seconds: float) -> dict[str, Any]:
        if not self._estop_received.wait(timeout_seconds):
            raise RuntimeError("어댑터 ESTOP 이벤트 대기 시간을 초과했습니다.")
        event = self._estop_event
        if event is None or event.get("robot_id") != robot_id:
            raise RuntimeError(
                "어댑터 ESTOP 이벤트의 robot_id가 일치하지 않습니다: "
                f"{None if event is None else event.get('robot_id')}"
            )
        return event

    def close(self) -> None:
        executor = getattr(self, "_executor", None)
        if executor is None:
            return
        self._executor = None
        executor.shutdown()
        self._spin_thread.join(timeout=1)
        self._node.destroy_node()
        if self._context.ok():
            self._context.shutdown()


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
    estop_event_waiter: Callable[[str, float], dict[str, Any]],
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

    active_proposal = _find(
        client.get(f"/task-orders/{task_id}/action-proposals"),
        "proposal_id",
        proposal_id,
    )
    if active_proposal.get("status") != "EXECUTING":
        raise RuntimeError(
            "HazardEvent 등록 전에 ActionProposal이 실행 중이 아닙니다: "
            f"{active_proposal.get('status')}"
        )

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
    estop_event = estop_event_waiter(robot_id, state_timeout_seconds)
    adapter_estop_latency_ms = estop_event.get("latency_ms")
    if not isinstance(adapter_estop_latency_ms, (int, float)):
        raise RuntimeError("어댑터 ESTOP 이벤트에 유효한 latency_ms가 없습니다.")
    robot, held_task, held_proposal = wait_for_estop_state(
        client,
        robot_id,
        task_id,
        proposal_id,
        state_timeout_seconds,
        clock=clock,
        sleep_fn=sleep_fn,
    )
    adapter_event_observed_ms = (clock() - started_at) * 1000
    return {
        "robot_id": robot_id,
        "task_id": task_id,
        "proposal_id": proposal_id,
        "hazard_id": hazard["hazard_id"],
        "ack_ms": ack.get("command_to_ack_ms"),
        "adapter_estop_latency_ms": round(float(adapter_estop_latency_ms), 2),
        "adapter_event_observed_ms": round(adapter_event_observed_ms, 2),
        "adapter_estop_triggered_by": estop_event.get("triggered_by"),
        "robot_status": robot["status"],
        "task_status": held_task["status"],
        "proposal_status": held_proposal["status"],
    }


def recover_hazard_scenario(
    client: BackendClient,
    report: dict[str, Any],
    frame_id: str,
    ack_timeout_seconds: float,
    state_timeout_seconds: float,
    *,
    sleep_fn: Callable[[float], None] = sleep,
) -> dict[str, Any]:
    """CV 안전 확인부터 실제 CTL RESUME RESULT까지 이어서 검증한다."""
    observed_at = datetime.now(timezone.utc).isoformat()
    sensor_id = "e2e-cctv"
    zone_id = "e2e-hazard-zone"
    client.put(
        f"/sensors/{sensor_id}",
        {
            "sensor_id": sensor_id,
            "sensor_type": "CCTV",
            "source_uri": "e2e:///cctv",
            "frame_id": f"{sensor_id}_frame",
            "pose": {
                "frame_id": frame_id,
                "position": {"x": 0, "y": 0, "z": 3},
                "orientation": {"x": 0, "y": 0, "z": 0, "w": 1},
            },
            "status": "ONLINE",
            "mounted_robot_id": None,
            "covered_zone_ids": [zone_id],
            "last_frame_at": observed_at,
            "calibrated": True,
        },
    )
    client.put(
        "/workers/snapshot",
        {"observed_at": observed_at, "workers": []},
    )
    client.post(
        f"/hazards/{report['hazard_id']}/clearance",
        {
            "observed_at": observed_at,
            "source_sensor_ids": [sensor_id],
            "clear_duration_seconds": 2,
        },
    )
    acknowledged = _find(
        client.get("/hazards"), "hazard_id", report["hazard_id"]
    )
    if acknowledged.get("status") != "ACKNOWLEDGED":
        raise RuntimeError("CV 안전 확인 뒤 Hazard가 ACKNOWLEDGED가 아닙니다.")
    resolved = client.post(
        f"/hazards/{report['hazard_id']}/resolve",
        {"resolved_by": "OPERATOR"},
    )
    if resolved.get("status") != "RESOLVED":
        raise RuntimeError("관리자 해제 뒤 Hazard가 RESOLVED가 아닙니다.")

    # E-Stop 해제 latched 메시지가 어댑터에 반영된 뒤 RESUME을 발행한다.
    sleep_fn(0.2)
    resume = client.post(
        f"/task-orders/{report['task_id']}/resume-proposal",
        {},
    )
    if resume.get("action_type") != "RESUME":
        raise RuntimeError("RESUME ActionProposal이 생성되지 않았습니다.")
    resume_id = resume["proposal_id"]
    evaluated = client.post(f"/action-proposals/{resume_id}/evaluate", {})
    if evaluated.get("status") != "APPROVED":
        raise RuntimeError(f"RESUME 안전 평가 실패: {evaluated.get('status')}")
    executing = client.post(f"/action-proposals/{resume_id}/execute", {})
    if executing.get("status") != "EXECUTING":
        raise RuntimeError(f"RESUME 발행 실패: {executing.get('status')}")
    ack = wait_for_ack(client, resume_id, ack_timeout_seconds)
    proposal, task, robot = wait_for_result(
        client,
        report["task_id"],
        resume_id,
        report["robot_id"],
        state_timeout_seconds,
    )
    final_states = (
        proposal.get("status"),
        task.get("status"),
        robot.get("status"),
    )
    if final_states != ("SUCCEEDED", "PLANNING", "IDLE"):
        raise RuntimeError(
            "RESUME 결과 상태가 올바르지 않습니다: "
            f"Proposal={final_states[0]}, Task={final_states[1]}, Robot={final_states[2]}"
        )
    return {
        **report,
        "hazard_status_after_clearance": acknowledged["status"],
        "hazard_status": resolved["status"],
        "resume_proposal_id": resume_id,
        "resume_ack_ms": ack.get("command_to_ack_ms"),
        "resume_proposal_status": proposal["status"],
        "resumed_task_status": task["status"],
        "resumed_robot_status": robot["status"],
    }


def main(
    argv: list[str] | None = None,
    client_factory: Callable[[str, float], BackendClient] = BackendClient,
    monitor_factory: Callable[[str, float], RosAdapterEventMonitor] = RosAdapterEventMonitor,
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
    parser.add_argument("--adapter-event-timeout", type=float, default=5)
    parser.add_argument("--result-topic")
    parser.add_argument("--hazard-delay", type=float, default=0.2)
    parser.add_argument("--verify-recovery", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if (
        args.ack_timeout <= 0
        or args.state_timeout <= 0
        or args.adapter_event_timeout <= 0
    ):
        parser.error("대기 시간은 0보다 커야 합니다.")
    if args.hazard_delay < 0:
        parser.error("--hazard-delay는 0 이상이어야 합니다.")

    client = client_factory(args.base_url, args.timeout)
    monitor: RosAdapterEventMonitor | None = None
    try:
        monitor = monitor_factory(
            args.result_topic or result_topic(args.robot_id),
            args.adapter_event_timeout,
        )
        report = run_hazard_scenario(
            client,
            args.robot_id,
            args.control_adapter,
            args.frame_id,
            args.ack_timeout,
            args.state_timeout,
            args.hazard_delay,
            estop_event_waiter=monitor.wait_for_estop,
        )
        if args.verify_recovery:
            report = recover_hazard_scenario(
                client,
                report,
                args.frame_id,
                args.ack_timeout,
                args.state_timeout,
            )
    except (KeyError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        if monitor is not None:
            monitor.close()

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("CRITICAL HazardEvent → E-Stop E2E 통과")
        print(
            f"ACK {report['ack_ms']}ms · 어댑터 E-Stop 지연 "
            f"{report['adapter_estop_latency_ms']}ms · 이벤트 관측 "
            f"{report['adapter_event_observed_ms']}ms"
        )
        print(
            f"Robot {report['robot_status']} · Task {report['task_status']} · "
            f"Proposal {report['proposal_status']}"
        )
        if args.verify_recovery:
            print("CV 안전 확인 → 관리자 해제 → RESUME ROS 2 E2E 통과")
            print(
                f"Hazard {report['hazard_status_after_clearance']} → "
                f"{report['hazard_status']} · RESUME ACK {report['resume_ack_ms']}ms"
            )
            print(
                f"Robot {report['resumed_robot_status']} · "
                f"Task {report['resumed_task_status']} · "
                f"RESUME Proposal {report['resume_proposal_status']}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
