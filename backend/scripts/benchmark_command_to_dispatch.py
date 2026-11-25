from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from dataclasses import asdict, dataclass
from time import monotonic, sleep
from typing import Any, Callable
from urllib import error, request
from uuid import uuid4


KPI9_THRESHOLD_MS = 1000.0  # 개발계획서 v1.0 9장 KPI 9
SCENARIO_A_RUNS = 10  # 사용자정의서 시스템구조도 v0.1 시나리오 A
SCENARIO_A_MIN_SUCCESS_RATE_PERCENT = 90.0  # 사용자정의서 시스템구조도 v0.1 시나리오 A
ACK_POLL_INTERVAL_SECONDS = 0.02
RESULT_POLL_INTERVAL_SECONDS = 0.1


@dataclass(frozen=True)
class Trial:
    index: int
    execution_succeeded: bool
    elapsed_ms: float | None
    task_id: str | None
    proposal_id: str | None
    latency_passed: bool
    error: str | None = None


def percentile(values: list[float], ratio: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, math.ceil(len(ordered) * ratio) - 1))]


def build_summary(trials: list[Trial], threshold_ms: float) -> dict[str, Any]:
    successful = [trial for trial in trials if trial.execution_succeeded]
    elapsed = [trial.elapsed_ms for trial in successful if trial.elapsed_ms is not None]
    latency_passed = sum(trial.latency_passed for trial in successful)
    return {
        "trials": len(trials),
        "threshold_ms": threshold_ms,
        "execution_successes": len(successful),
        "execution_success_rate_percent": round(100 * len(successful) / len(trials), 2),
        "latency_observations": len(elapsed),
        "latency_passed": latency_passed,
        "latency_pass_rate_percent": round(100 * latency_passed / len(elapsed), 2) if elapsed else 0.0,
        "mean_ms": round(statistics.fmean(elapsed), 2) if elapsed else None,
        # n=10에서는 p95가 최댓값과 같아 의미가 약하므로 표본이 20개 이상일 때만 표시한다.
        "p95_ms": round(percentile(elapsed, 0.95), 2) if len(elapsed) >= 20 else None,
        "max_ms": round(max(elapsed), 2) if elapsed else None,
    }


class BackendClient:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def post(self, path: str, body: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            f"{self.base_url}{path}",
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"POST {path} → HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"Backend 연결 실패({self.base_url}): {exc.reason}") from exc

    def put(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            f"{self.base_url}{path}",
            data=payload,
            method="PUT",
            headers={"Content-Type": "application/json"},
        )
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"PUT {path} → HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"Backend 연결 실패({self.base_url}): {exc.reason}") from exc

    def get(self, path: str) -> Any:
        req = request.Request(
            f"{self.base_url}{path}",
            method="GET",
            headers={"Accept": "application/json"},
        )
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"GET {path} → HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"Backend 연결 실패({self.base_url}): {exc.reason}") from exc


def wait_for_ack(client: BackendClient, proposal_id: str, timeout_seconds: float) -> dict[str, Any]:
    deadline = monotonic() + timeout_seconds
    while True:
        record = client.get(f"/action-proposals/{proposal_id}/ack")
        if record.get("status") != "PENDING":
            return record
        if monotonic() >= deadline:
            raise RuntimeError("Backend ACK 기록 대기 시간을 초과했습니다.")
        sleep(ACK_POLL_INTERVAL_SECONDS)


def wait_for_result(
    client: BackendClient,
    task_id: str,
    proposal_id: str,
    robot_id: str,
    timeout_seconds: float,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """실제 CTL RESULT가 반영될 때까지 기다린다. 벤치마크가 결과를 대신 만들지 않는다."""
    deadline = monotonic() + timeout_seconds
    while True:
        proposals = client.get(f"/task-orders/{task_id}/action-proposals")
        proposal = next(
            (item for item in proposals if item.get("proposal_id") == proposal_id),
            None,
        )
        if proposal is None:
            raise RuntimeError("실행 중인 ActionProposal을 조회할 수 없습니다.")
        if proposal.get("status") != "EXECUTING":
            task = client.get(f"/task-orders/{task_id}")
            robot = next(
                (
                    item
                    for item in client.get("/robots")
                    if item.get("robot_id") == robot_id
                ),
                None,
            )
            if robot is None:
                raise RuntimeError(f"robot_id={robot_id} 상태를 조회할 수 없습니다.")
            return proposal, task, robot
        if monotonic() >= deadline:
            raise RuntimeError("실제 CTL RESULT 대기 시간을 초과했습니다.")
        sleep(RESULT_POLL_INTERVAL_SECONDS)


def register_robot(
    client: BackendClient,
    robot_id: str,
    frame_id: str,
    control_adapter: str,
) -> None:
    existing = client.get("/robots")
    if any(robot.get("robot_id") == robot_id for robot in existing):
        return
    client.post("/robots", {
        "robot_id": robot_id,
        "robot_type": "MOBILE",
        "control_adapter": control_adapter,
        "status": "IDLE",
        "pose": {
            "frame_id": frame_id,
            "position": {"x": 0, "y": 0, "z": 0},
            "orientation": {"x": 0, "y": 0, "z": 0, "w": 1},
        },
        "velocity": {"x": 0, "y": 0, "z": 0},
        "motion_limits": {"max_speed": 0.5, "max_acceleration": 0.5},
    })


def run_trial(
    client: BackendClient,
    robot_id: str,
    index: int,
    threshold_ms: float,
    *,
    frame_id: str = "world",
    result_timeout_seconds: float = 120.0,
) -> Trial:
    target_x = 1 if index % 2 == 0 else 0
    instruction = f"{robot_id} 로봇을 좌표 ({target_x}, 0, 0)로 이동해"
    task_id: str | None = None
    proposal_id: str | None = None
    elapsed_ms: float | None = None
    ack_succeeded = False
    finalized = False
    failure: str | None = None

    try:
        task = client.post(
            "/task-orders/from-natural-language",
            {"instruction": instruction, "frame_id": frame_id},
            {"Idempotency-Key": f"benchmark-task-{uuid4()}"},
        )
        task_id = task["task_id"]
        proposal = client.post(
            f"/task-orders/{task_id}/action-proposals",
            {},
            {"Idempotency-Key": f"benchmark-proposal-{uuid4()}"},
        )
        proposal_id = proposal["proposal_id"]
        approved = client.post(f"/action-proposals/{proposal_id}/evaluate", {})
        if approved.get("status") != "APPROVED":
            raise RuntimeError(f"안전 평가가 승인되지 않았습니다: {approved.get('status')}")

        executing = client.post(f"/action-proposals/{proposal_id}/execute", {})
        if executing.get("status") != "EXECUTING":
            raise RuntimeError(f"Action publish 상태가 EXECUTING이 아닙니다: {executing.get('status')}")
        ack_failure: str | None = None
        try:
            ack = wait_for_ack(
                client,
                proposal_id,
                max(client.timeout, threshold_ms / 1000 + 1),
            )
            elapsed_ms = ack.get("command_to_ack_ms")
            if ack.get("status") != "ACKNOWLEDGED" or elapsed_ms is None:
                ack_failure = f"어댑터 ACK가 기한 내 도착하지 않았습니다: {ack.get('status')}"
            else:
                ack_succeeded = True
        except RuntimeError as exc:
            ack_failure = str(exc)

        completed_proposal, completed_task, idle_robot = wait_for_result(
            client,
            task_id,
            proposal_id,
            robot_id,
            result_timeout_seconds,
        )
        final_states = (
            completed_proposal.get("status"),
            completed_task.get("status"),
            idle_robot.get("status"),
        )
        if final_states != ("SUCCEEDED", "COMPLETED", "IDLE"):
            raise RuntimeError(
                "실제 CTL RESULT 성공 상태가 일치하지 않습니다: "
                f"Proposal={final_states[0]}, Task={final_states[1]}, "
                f"Robot={final_states[2]}"
            )
        finalized = True
        failure = ack_failure
    except (KeyError, RuntimeError) as exc:
        failure = str(exc)

    execution_succeeded = finalized
    return Trial(
        index=index + 1,
        execution_succeeded=execution_succeeded,
        elapsed_ms=elapsed_ms,
        task_id=task_id,
        proposal_id=proposal_id,
        latency_passed=(
            execution_succeeded
            and ack_succeeded
            and elapsed_ms is not None
            and elapsed_ms <= threshold_ms
        ),
        error=failure,
    )


def scenario_a_passed(summary: dict[str, Any], *, require_latency: bool = True) -> bool:
    execution_passed = (
        summary["execution_success_rate_percent"] >= SCENARIO_A_MIN_SUCCESS_RATE_PERCENT
    )
    latency_passed = (
        summary["latency_pass_rate_percent"] >= SCENARIO_A_MIN_SUCCESS_RATE_PERCENT
    )
    return execution_passed and (latency_passed or not require_latency)


def main(
    argv: list[str] | None = None,
    client_factory: Callable[[str, float], BackendClient] = BackendClient,
) -> int:
    parser = argparse.ArgumentParser(description="자연어 명령부터 어댑터 ACK까지 KPI 9 측정")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--runs", type=int, default=SCENARIO_A_RUNS)
    parser.add_argument("--threshold-ms", type=float, default=KPI9_THRESHOLD_MS)
    parser.add_argument("--timeout", type=float, default=5)
    parser.add_argument(
        "--result-timeout",
        type=float,
        default=120,
        help="각 회차에서 실제 CTL RESULT를 기다리는 최대 시간(초)",
    )
    parser.add_argument(
        "--robot-id",
        default="robot-01",
        help="실행 중인 CTL 어댑터와 같은 Robot ID",
    )
    parser.add_argument(
        "--control-adapter",
        default="mock-mobile-adapter",
        help="새 Robot 등록에 기록할 제어 어댑터 이름",
    )
    parser.add_argument("--frame-id", default="world")
    parser.add_argument(
        "--no-latency-gate",
        action="store_true",
        help="기능 E2E 종료 코드는 실행 성공률만 사용하고 ACK 지연은 출력만 함",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs는 1 이상이어야 합니다.")
    if args.result_timeout <= 0:
        parser.error("--result-timeout은 0보다 커야 합니다.")

    client = client_factory(args.base_url, args.timeout)
    robot_id = args.robot_id
    try:
        register_robot(client, robot_id, args.frame_id, args.control_adapter)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    trials = [
        run_trial(
            client,
            robot_id,
            index,
            args.threshold_ms,
            frame_id=args.frame_id,
            result_timeout_seconds=args.result_timeout,
        )
        for index in range(args.runs)
    ]
    summary = build_summary(trials, args.threshold_ms)
    report = {
        "scope": "KPI 9: 자연어 명령 Backend 수신 → 어댑터 ACK Backend 수신",
        "scenario_a_min_success_rate_percent": SCENARIO_A_MIN_SUCCESS_RATE_PERCENT,
        "summary": summary,
        "trials": [asdict(trial) for trial in trials],
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(report["scope"])
        print(
            f"실행 성공 {summary['execution_successes']}/{summary['trials']}회 "
            f"({summary['execution_success_rate_percent']}%)"
        )
        print(
            f"{summary['threshold_ms']}ms 이내 {summary['latency_passed']}/{summary['latency_observations']}회 "
            f"({summary['latency_pass_rate_percent']}%)"
        )
        if summary["mean_ms"] is not None:
            latency_line = f"평균 {summary['mean_ms']}ms · 최대 {summary['max_ms']}ms"
            if summary["p95_ms"] is not None:
                latency_line += f" · p95 {summary['p95_ms']}ms"
            print(latency_line)
        for trial in trials:
            if trial.error:
                label = "경고" if trial.execution_succeeded else "실패"
                print(f"#{trial.index} {label}: {trial.error}", file=sys.stderr)
    return 0 if scenario_a_passed(summary, require_latency=not args.no_latency_gate) else 1


if __name__ == "__main__":
    sys.exit(main())
