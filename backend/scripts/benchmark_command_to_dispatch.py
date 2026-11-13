from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from dataclasses import asdict, dataclass
from time import perf_counter, time
from typing import Any
from urllib import error, request
from uuid import uuid4


@dataclass(frozen=True)
class Trial:
    index: int
    elapsed_ms: float
    task_id: str
    proposal_id: str
    passed_threshold: bool


def percentile(values: list[float], ratio: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, math.ceil(len(ordered) * ratio) - 1))]


def build_summary(trials: list[Trial], threshold_ms: float) -> dict[str, Any]:
    elapsed = [trial.elapsed_ms for trial in trials]
    return {
        "trials": len(trials),
        "threshold_ms": threshold_ms,
        "passed": sum(trial.passed_threshold for trial in trials),
        "success_rate_percent": round(100 * sum(trial.passed_threshold for trial in trials) / len(trials), 2),
        "mean_ms": round(statistics.fmean(elapsed), 2),
        "p95_ms": round(percentile(elapsed, 0.95), 2),
        "max_ms": round(max(elapsed), 2),
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
    task = client.post(
        "/task-orders/from-natural-language",
        {"instruction": instruction},
        {"Idempotency-Key": f"benchmark-task-{uuid4()}"},
    )
    proposal = client.post(
        f"/task-orders/{task['task_id']}/action-proposals",
        {},
        {"Idempotency-Key": f"benchmark-proposal-{uuid4()}"},
    )
    approved = client.post(f"/action-proposals/{proposal['proposal_id']}/evaluate", {})
    if approved.get("status") != "APPROVED":
        raise RuntimeError(f"안전 평가가 승인되지 않았습니다: {approved.get('status')}")
    executing = client.post(f"/action-proposals/{proposal['proposal_id']}/execute", {})
    elapsed_ms = (perf_counter() - started) * 1000
    if executing.get("status") != "EXECUTING":
        raise RuntimeError(f"Action publish 상태가 EXECUTING이 아닙니다: {executing.get('status')}")

    client.post(
        f"/action-proposals/{proposal['proposal_id']}/result",
        {"status": "SUCCEEDED", "detail": "지연 측정 후 상태 복구"},
    )
    return Trial(
        index=index + 1,
        elapsed_ms=elapsed_ms,
        task_id=task["task_id"],
        proposal_id=proposal["proposal_id"],
        passed_threshold=elapsed_ms <= threshold_ms,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="자연어 명령부터 승인 Action publish까지 E2E 지연 측정")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--threshold-ms", type=float, default=1000)
    parser.add_argument("--timeout", type=float, default=5)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs는 1 이상이어야 합니다.")

    client = BackendClient(args.base_url, args.timeout)
    robot_id = f"benchmark-{os.getpid()}-{int(time())}"
    try:
        register_robot(client, robot_id)
        trials = [run_trial(client, robot_id, index, args.threshold_ms) for index in range(args.runs)]
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    report = {"scope": "자연어 TaskOrder 생성 → 안전 평가 → 승인 Action publish API 응답", "summary": build_summary(trials, args.threshold_ms), "trials": [asdict(trial) for trial in trials]}
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        summary = report["summary"]
        print(report["scope"])
        print(
            f"{summary['trials']}회 중 {summary['passed']}회 {summary['threshold_ms']}ms 이하 "
            f"({summary['success_rate_percent']}%)"
        )
        print(f"평균 {summary['mean_ms']}ms · p95 {summary['p95_ms']}ms · 최대 {summary['max_ms']}ms")
    return 0 if report["summary"]["passed"] == args.runs else 1


if __name__ == "__main__":
    sys.exit(main())
