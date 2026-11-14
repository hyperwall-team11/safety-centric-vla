from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from dataclasses import asdict, dataclass
from time import perf_counter, time
from typing import Any, Callable
from urllib import error, request
from uuid import uuid4


KPI9_THRESHOLD_MS = 1000.0  # 개발계획서 v1.0 9장 KPI 9
SCENARIO_A_RUNS = 10  # 사용자정의서 시스템구조도 v0.1 시나리오 A
SCENARIO_A_MIN_SUCCESS_RATE_PERCENT = 90.0  # 사용자정의서 시스템구조도 v0.1 시나리오 A


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


def register_robot(client: BackendClient, robot_id: str) -> None:
    client.post("/robots", {
        "robot_id": robot_id,
        "robot_type": "MOBILE",
        "control_adapter": "mock-mobile-adapter",
        "status": "IDLE",
        "pose": {
            "frame_id": "map",
            "position": {"x": 0, "y": 0, "z": 0},
            "orientation": {"x": 0, "y": 0, "z": 0, "w": 1},
        },
        "velocity": {"x": 0, "y": 0, "z": 0},
        "motion_limits": {"max_speed": 0.5, "max_acceleration": 0.5},
    })


def run_trial(client: BackendClient, robot_id: str, index: int, threshold_ms: float) -> Trial:
    instruction = f"{robot_id} 로봇을 좌표 ({index + 1}, 0, 0)로 이동해"
    started = perf_counter()
    task_id: str | None = None
    proposal_id: str | None = None
    elapsed_ms: float | None = None
    execution_requested = False
    finalized = False
    failure: str | None = None

    try:
        task = client.post(
            "/task-orders/from-natural-language",
            {"instruction": instruction},
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

        execution_requested = True
        executing = client.post(f"/action-proposals/{proposal_id}/execute", {})
        elapsed_ms = (perf_counter() - started) * 1000
        if executing.get("status") != "EXECUTING":
            raise RuntimeError(f"Action publish 상태가 EXECUTING이 아닙니다: {executing.get('status')}")

        client.post(
            f"/action-proposals/{proposal_id}/result",
            {"status": "SUCCEEDED", "detail": "지연 측정 후 상태 복구"},
        )
        finalized = True
    except (KeyError, RuntimeError) as exc:
        failure = str(exc)
    finally:
        if execution_requested and proposal_id is not None and not finalized:
            try:
                client.post(
                    f"/action-proposals/{proposal_id}/result",
                    {
                        "status": "FAILED",
                        "reason_code": "TIMEOUT",
                        "detail": "지연 측정 실패 후 실행 상태 복구",
                    },
                )
            except RuntimeError as cleanup_exc:
                cleanup_message = f"실행 상태 복구 실패: {cleanup_exc}"
                failure = f"{failure}; {cleanup_message}" if failure else cleanup_message

    execution_succeeded = finalized and failure is None
    return Trial(
        index=index + 1,
        execution_succeeded=execution_succeeded,
        elapsed_ms=elapsed_ms,
        task_id=task_id,
        proposal_id=proposal_id,
        latency_passed=execution_succeeded and elapsed_ms is not None and elapsed_ms <= threshold_ms,
        error=failure,
    )


def scenario_a_passed(summary: dict[str, Any]) -> bool:
    return (
        summary["execution_success_rate_percent"] >= SCENARIO_A_MIN_SUCCESS_RATE_PERCENT
        and summary["latency_pass_rate_percent"] >= SCENARIO_A_MIN_SUCCESS_RATE_PERCENT
    )


def main(
    argv: list[str] | None = None,
    client_factory: Callable[[str, float], BackendClient] = BackendClient,
) -> int:
    parser = argparse.ArgumentParser(description="자연어 명령부터 승인 Action publish API 응답까지 KPI 9 proxy 측정")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--runs", type=int, default=SCENARIO_A_RUNS)
    parser.add_argument("--threshold-ms", type=float, default=KPI9_THRESHOLD_MS)
    parser.add_argument("--timeout", type=float, default=5)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs는 1 이상이어야 합니다.")

    client = client_factory(args.base_url, args.timeout)
    robot_id = f"benchmark-{os.getpid()}-{int(time())}"
    try:
        register_robot(client, robot_id)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    trials = [run_trial(client, robot_id, index, args.threshold_ms) for index in range(args.runs)]
    summary = build_summary(trials, args.threshold_ms)
    report = {
        "scope": "KPI 9 proxy (어댑터 ACK 제외): 자연어 TaskOrder 생성 → 안전 평가 → 승인 Action publish API 응답",
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
                print(f"#{trial.index} 실패: {trial.error}", file=sys.stderr)
    return 0 if scenario_a_passed(summary) else 1


if __name__ == "__main__":
    sys.exit(main())
