from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models import NaturalLanguageTaskRequest
from app.task_parser import TaskParseError, parse_task_order_preview


DEFAULT_CASES = Path(__file__).with_name("command_cases.jsonl")
KNOWN_ROBOTS = ["robot-01", "robot-02"]


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    passed: bool
    elapsed_ms: float
    message: str


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number} JSON 오류: {exc}") from exc
        cases.append(case)
    return cases


def _destination_value(preview) -> str | list[float]:
    if preview.destination.type == "ZONE":
        return preview.destination.zone_id
    position = preview.destination.position
    return [position.x, position.y, position.z]


def evaluate_case(case: dict[str, Any]) -> CaseResult:
    started = perf_counter()
    try:
        preview = parse_task_order_preview(
            NaturalLanguageTaskRequest(instruction=case["instruction"]),
            KNOWN_ROBOTS,
        )
    except TaskParseError as exc:
        elapsed_ms = (perf_counter() - started) * 1000
        if not case["valid"]:
            return CaseResult(case["id"], True, elapsed_ms, f"예상대로 거부: {exc.code.value}")
        return CaseResult(case["id"], False, elapsed_ms, f"예상과 달리 거부: {exc.code.value} · {exc}")

    elapsed_ms = (perf_counter() - started) * 1000
    if not case["valid"]:
        return CaseResult(case["id"], False, elapsed_ms, "거부해야 할 명령을 승인")

    actual = {
        "robot_id": preview.target_robot_id,
        "action_type": preview.action_type,
        "destination_type": preview.destination.type,
        "destination_value": _destination_value(preview),
        "object_id": preview.target_object_id,
    }
    mismatches = [
        f"{key}={actual[key]!r} (기대 {expected!r})"
        for key, expected in case.items()
        if key in actual and actual[key] != expected
    ]
    if mismatches:
        return CaseResult(case["id"], False, elapsed_ms, "; ".join(mismatches))
    return CaseResult(case["id"], True, elapsed_ms, "해석 일치")


def evaluate(cases: list[dict[str, Any]]) -> list[CaseResult]:
    return [evaluate_case(case) for case in cases]


def summary(cases: list[dict[str, Any]], results: list[CaseResult]) -> dict[str, Any]:
    valid_ids = {case["id"] for case in cases if case["valid"]}
    valid_results = [result for result in results if result.case_id in valid_ids]
    reject_results = [result for result in results if result.case_id not in valid_ids]
    elapsed = [result.elapsed_ms for result in results]
    return {
        "total": len(results),
        "passed": sum(result.passed for result in results),
        "accuracy_percent": round(100 * sum(result.passed for result in results) / len(results), 2),
        "valid_accuracy_percent": round(100 * sum(result.passed for result in valid_results) / len(valid_results), 2),
        "rejection_accuracy_percent": round(100 * sum(result.passed for result in reject_results) / len(reject_results), 2),
        "mean_ms": round(statistics.fmean(elapsed), 3),
        "p95_ms": round(sorted(elapsed)[max(0, int(len(elapsed) * 0.95) - 1)], 3),
        "max_ms": round(max(elapsed), 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="자연어 TaskOrder 파서 평가")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--json", action="store_true", help="기계 판독용 JSON 결과 출력")
    args = parser.parse_args()

    cases = load_cases(args.cases)
    if not cases:
        raise ValueError("평가 케이스가 비어 있습니다.")
    results = evaluate(cases)
    report = summary(cases, results)
    failures = [result for result in results if not result.passed]

    if args.json:
        print(json.dumps({"summary": report, "failures": [result.__dict__ for result in failures]}, ensure_ascii=False, indent=2))
    else:
        print(
            f"총 {report['total']}건 · 통과 {report['passed']}건 · 정확도 {report['accuracy_percent']}%\n"
            f"정상 명령 {report['valid_accuracy_percent']}% · 거부 명령 {report['rejection_accuracy_percent']}%\n"
            f"처리시간 평균 {report['mean_ms']}ms · p95 {report['p95_ms']}ms · 최대 {report['max_ms']}ms"
        )
        for result in failures:
            print(f"FAIL {result.case_id}: {result.message}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
