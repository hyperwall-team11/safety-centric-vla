"""MuJoCo ② 사전 시뮬레이션 호출(승인 규칙 9번 `COLLISION`).

근거: docs/backend-ctl-contract-v0.1.md 13장, docs/ontology-schema-v1.1.md 5장 9번.

- 규칙 1~8을 통과한 후보만 CTL `POST /twin/simulate`로 보낸다.
- `PASS`가 아닌 모든 결과, 시간 초과, 연결 실패, 2xx가 아닌 응답, 형식 오류는 실행 차단이다(Fail-Closed).
- HTTP 응답을 기다리는 동안 저장소 잠금을 잡지 않도록 요청 생성(잠금 안)과 호출(잠금 밖)을 나눈다.
- `snapshot_id`는 검증에 쓴 상태를 식별한다. 실행 직전에 다시 계산해 다르면 기존 `PASS`를 쓰지 않는다(13.3절).
"""
from __future__ import annotations

import json
import math
from collections.abc import Iterable
from datetime import datetime
from hashlib import sha256
from os import getenv
from typing import Any, Protocol
from urllib import error, request

from .ctl_bridge import CommandSerializationError, build_approved_action_command
from .models import (
    ActionProposal,
    ActionType,
    HazardEvent,
    HazardStatus,
    Robot,
    RuleResult,
    Worker,
    Zone,
)

CONTRACT_VERSION = "backend-ctl/0.2"
COLLISION_RULE_ID = "rule-collision"
TWIN_SIMULATION_ENABLED_ENV = "BACKEND_TWIN_SIMULATION_ENABLED"
TWIN_SIMULATE_URL_ENV = "BACKEND_TWIN_SIMULATE_URL"
# 제어 계약 13장 기본 주소
DEFAULT_TWIN_SIMULATE_URL = "http://127.0.0.1:8100/twin/simulate"
# 제어 계약 13.2: 시간 제한 값은 ONT·백엔드가 정한다. #50 확정 전 잠정값이며 확정되면 그 값으로 바꾼다.
TWIN_SIMULATE_TIMEOUT_SECONDS = 1.0
# 시뮬레이션 대기 중 상태가 바뀌면 다시 시뮬레이션하는 최대 횟수(평가·실행 직전 각각). 넘으면 차단한다(13.3절).
MAX_SIMULATION_ATTEMPTS = 2
# trajectory 끝점과 실제 전송 목표의 허용 오차. control/robot_adapter/diff_drive_core.py의 도착 판정 허용 오차와 같다.
TRAJECTORY_TARGET_TOLERANCE_M = 0.05

# 제어 계약 13.1: 첫 버전은 MOVE_TO만 지원한다.
SIMULATED_ACTION_TYPES = {ActionType.MOVE_TO}
# 정지·재개는 사전 시뮬레이션을 기다리지 않는다(13장: 긴급 정지 경로가 우선).
EXEMPT_ACTION_TYPES = {ActionType.STOP, ActionType.RESUME}
SIMULATION_RESULTS = {"PASS", "COLLISION", "UNSAFE_DISTANCE", "GOAL_NOT_REACHED", "ERROR"}
REQUIRED_RESPONSE_FIELDS = (
    "result",
    "snapshot_id",
    "min_worker_distance_m",
    "first_contact_at_s",
    "simulated_horizon_s",
    "elapsed_ms",
)


class TwinSimulationError(RuntimeError):
    """사전 시뮬레이션을 요청할 수 없거나 응답을 믿을 수 없는 경우. 항상 실행 차단으로 처리한다."""


class TwinSimulator(Protocol):
    def simulate(self, payload: dict[str, Any]) -> dict[str, Any]:
        """요청 한 건을 보내고 응답 본문을 돌려준다. 실패는 TwinSimulationError로 알린다."""


class HttpTwinSimulator:
    """CTL MuJoCo ② 서비스 HTTP 클라이언트."""

    def __init__(self, url: str | None = None, timeout_s: float = TWIN_SIMULATE_TIMEOUT_SECONDS):
        self._url = url
        self.timeout_s = timeout_s

    @property
    def url(self) -> str:
        return self._url or getenv(TWIN_SIMULATE_URL_ENV) or DEFAULT_TWIN_SIMULATE_URL

    def simulate(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            self.url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout_s) as response:
                raw = response.read()
        except error.HTTPError as exc:
            raise TwinSimulationError(f"HTTP {exc.code} 응답") from exc
        except (error.URLError, TimeoutError, OSError) as exc:
            raise TwinSimulationError(f"연결 실패 또는 시간 초과: {exc}") from exc
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise TwinSimulationError("응답이 JSON이 아님") from exc


def collision_rule_applies(action_type: ActionType) -> bool:
    return action_type not in EXEMPT_ACTION_TYPES


def _enum_value(value: Any) -> Any:
    # OntologyModel은 열거값을 문자열로 저장한다.
    return getattr(value, "value", value)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _xyz(pose) -> list[float]:
    return [pose.position.x, pose.position.y, pose.position.z]


def _yaw(pose) -> float:
    q = pose.orientation
    return math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))


def _check_straight_trajectory(proposal: ActionProposal, target_pose: dict[str, Any]) -> None:
    """트윈과 CTL 어댑터는 현재 위치에서 `target_pose`까지 직선으로 움직인다.

    경유점이 있거나 끝점이 목표와 다른 trajectory는 시뮬레이션한 경로와 규칙 1~8이 검사한 경로가 달라지므로 차단한다.
    """
    trajectory = proposal.trajectory
    if not trajectory:
        return
    if len(trajectory) != 2:
        raise TwinSimulationError(
            f"경유점이 있는 trajectory({len(trajectory)}개 점)는 직선 이동만 시뮬레이션하는 트윈으로 검증할 수 없음"
        )
    end = trajectory[-1].get("position") if isinstance(trajectory[-1], dict) else None
    try:
        end_xyz = [float(end["x"]), float(end["y"]), float(end["z"])]
    except (KeyError, TypeError, ValueError) as exc:
        raise TwinSimulationError("trajectory 끝점 position 형식 오류") from exc
    if (
        target_pose.get("frame_id") != proposal.frame_id
        or math.dist(end_xyz, target_pose["position"]) > TRAJECTORY_TARGET_TOLERANCE_M
    ):
        raise TwinSimulationError("trajectory 끝점이 실제 전송 목표(target_pose)와 다름")


def compute_snapshot_id(
    robots: Iterable[Robot],
    workers: list[Worker] | None,
    workers_observed_at: datetime | None,
    hazards: Iterable[HazardEvent],
    zones: Iterable[Zone],
) -> str:
    """사전 시뮬레이션에 쓴 상태의 식별자(제어 계약 13.3절).

    로봇 상태(트윈에 장애물로 들어가는 다른 로봇 포함), Worker 스냅샷, 열린 HazardEvent, Zone이 같으면 같은 값이다.
    """
    payload = {
        "robots": sorted(
            (r.model_dump(mode="json", include={"robot_id", "robot_type", "status", "pose"})
             for r in robots),
            key=lambda item: item["robot_id"],
        ),
        "workers_observed_at": None if workers_observed_at is None else _iso(workers_observed_at),
        "workers": None if workers is None else sorted(
            (w.model_dump(mode="json", include={"worker_id", "pose"}) for w in workers),
            key=lambda item: item["worker_id"],
        ),
        "hazards": sorted(
            (h.model_dump(mode="json", include={
                "hazard_id", "hazard_type", "severity", "status", "zone_id", "related_robot_ids",
            }) for h in hazards if h.status == HazardStatus.OPEN),
            key=lambda item: item["hazard_id"],
        ),
        "zones": sorted((z.model_dump(mode="json") for z in zones), key=lambda item: item["zone_id"]),
    }
    digest = sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    return f"snap-{digest.hexdigest()[:32]}"


def build_simulation_request(
    proposal: ActionProposal,
    robot: Robot,
    *,
    robots: Iterable[Robot],
    workers: list[Worker] | None,
    workers_observed_at: datetime | None,
    zones: list[Zone],
    snapshot_id: str,
    snapshot_at: datetime,
) -> dict[str, Any]:
    """제어 계약 13.1절 요청. 목표 좌표·속도는 4.1절 직렬화(`build_approved_action_command`)를 그대로 쓴다."""
    if proposal.action_type not in SIMULATED_ACTION_TYPES:
        raise TwinSimulationError(
            f"사전 시뮬레이션 미지원 action_type {_enum_value(proposal.action_type)} (제어 계약 13.1절)"
        )
    try:
        command = build_approved_action_command(proposal, robot, zones)
    except CommandSerializationError as exc:
        raise TwinSimulationError(f"요청 생성 실패: {exc}") from exc
    target_pose = command["parameters"].get("target_pose")
    if not isinstance(target_pose, dict):
        raise TwinSimulationError("요청 생성 실패: target_pose 없음")
    _check_straight_trajectory(proposal, target_pose)

    worker_state = None
    if workers is not None and workers_observed_at is not None:
        worker_state = {
            "observed_at": _iso(workers_observed_at),
            "items": [
                {"worker_id": w.worker_id, "frame_id": w.pose.frame_id, "position": _xyz(w.pose)}
                for w in workers
            ],
        }
    return {
        "contract_version": CONTRACT_VERSION,
        "proposal_id": proposal.proposal_id,
        "snapshot_id": snapshot_id,
        "snapshot_at": _iso(snapshot_at),
        "action": {
            "action_type": _enum_value(proposal.action_type),
            "robot_id": proposal.robot_id,
            "target_pose": target_pose,
            "speed_limit": command["speed_limit"],
        },
        "state": {
            "robots": [
                {
                    "robot_id": r.robot_id,
                    "robot_type": _enum_value(r.robot_type),
                    "frame_id": r.pose.frame_id,
                    "position": _xyz(r.pose),
                    "yaw_rad": _yaw(r.pose),
                }
                for r in robots
            ],
            # 관측이 없으면 None을 그대로 보낸다. 트윈은 빈 현장으로 가정하지 않고 ERROR를 돌려준다.
            "workers": worker_state,
            "zones": [
                {
                    "zone_id": z.zone_id,
                    "zone_type": _enum_value(z.zone_type),
                    "frame_id": z.frame_id,
                    "geometry": z.geometry,
                }
                for z in zones
            ],
        },
    }


def _validate_response(response: Any, snapshot_id: str) -> dict[str, Any]:
    if not isinstance(response, dict):
        raise TwinSimulationError("응답 형식 오류: JSON 객체가 아님")
    missing = [field for field in REQUIRED_RESPONSE_FIELDS if field not in response]
    if missing:
        raise TwinSimulationError(f"응답 형식 오류: 필수 필드 누락 {', '.join(missing)}")
    if response["result"] not in SIMULATION_RESULTS:
        raise TwinSimulationError(f"응답 형식 오류: 알 수 없는 result {response['result']!r}")
    if response["snapshot_id"] != snapshot_id:
        raise TwinSimulationError("응답 형식 오류: 요청과 다른 snapshot_id")
    return response


def _format_value(value: Any, unit: str) -> str:
    return "없음" if value is None else f"{value}{unit}"


def run_simulation(simulator: TwinSimulator, payload: dict[str, Any]) -> RuleResult:
    """사전 시뮬레이션을 호출해 COLLISION 규칙 결과로 바꾼다. 저장소 잠금 밖에서 부른다."""
    snapshot_id = payload["snapshot_id"]
    try:
        response = _validate_response(simulator.simulate(payload), snapshot_id)
    except TwinSimulationError as exc:
        return blocked_result(f"사전 시뮬레이션 실패로 차단: {exc}", snapshot_id)
    except Exception as exc:  # 클라이언트 구현 오류도 통과로 보지 않는다(Fail-Closed)
        return blocked_result(f"사전 시뮬레이션 실패로 차단: {type(exc).__name__}: {exc}", snapshot_id)

    result = response["result"]
    detail = (
        f"사전 시뮬레이션 {result} (snapshot_id={snapshot_id}, "
        f"최소 작업자 거리 {_format_value(response['min_worker_distance_m'], 'm')}, "
        f"첫 접촉 {_format_value(response['first_contact_at_s'], 's')}, "
        f"구간 {response['simulated_horizon_s']}s, {response['elapsed_ms']}ms)"
    )
    if response.get("detail"):
        detail = f"{detail}: {response['detail']}"
    return RuleResult(rule_id=COLLISION_RULE_ID, passed=result == "PASS", reason=detail)


def blocked_result(reason: str, snapshot_id: str | None = None) -> RuleResult:
    suffix = f" (snapshot_id={snapshot_id})" if snapshot_id else ""
    return RuleResult(rule_id=COLLISION_RULE_ID, passed=False, reason=f"{reason}{suffix}")


def stale_result(snapshot_id: str, attempts: int) -> RuleResult:
    return blocked_result(
        f"사전 시뮬레이션 중 상태가 바뀌어 {attempts}회 모두 결과를 쓸 수 없어 차단", snapshot_id
    )


def reused_pass_result(snapshot_id: str) -> RuleResult:
    return RuleResult(
        rule_id=COLLISION_RULE_ID,
        passed=True,
        reason=f"같은 상태의 사전 시뮬레이션 PASS 재사용 (snapshot_id={snapshot_id})",
    )
