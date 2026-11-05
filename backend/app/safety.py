from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from math import hypot, isfinite
from typing import Any

from .models import (
    ActionProposal,
    ActionType,
    HazardEvent,
    HazardSeverity,
    HazardStatus,
    Robot,
    RobotStatus,
    RuleResult,
    RuleSeverity,
    RuleType,
    SafetyRule,
    Sensor,
    SensorStatus,
    TaskOrder,
    ViolationAction,
    Worker,
    Zone,
    ZoneType,
)


# docs/ontology-schema-v1.1.md §5: 속도 상한 및 KEEP_OUT 경로 교차 금지.
DEFAULT_SPEED_LIMIT = 0.4
_GEOMETRY_EPS = 1e-9  # 부동소수점 오차 허용치(거리 안전 여유값이 아님).


# docs/ontology-schema-v1.1.md §5: KEEP_OUT 경로 교차 금지.
# 2D GeoJSON 및 Backend–CTL polygon/points 지원. trajectory = [{"position": {"x": ..., "y": ...}}, ...].
# 구조가 다르거나 좌표계가 다르면 안전하다고 판단하지 않는다(Fail-Closed).
def _keep_out_result(proposal, task, zones):
    if proposal.action_type != ActionType.MOVE_TO:
        return True, "MOVE_TO 이외의 행동"
    keep_out = [z for z in zones if z.zone_type == ZoneType.KEEP_OUT]
    if not keep_out:
        return True, "KEEP_OUT 구역 없음"

    target = proposal.parameters.get("target_pose", {})
    task_zone = getattr(task.destination, "zone_id", None)
    proposal_zone = target.get("zone_id") if isinstance(target, dict) else None
    if any(z.zone_id in (task_zone, proposal_zone) for z in keep_out):
        return False, "KEEP_OUT 구역을 목적지로 지정함"

    if not isinstance(proposal.trajectory, list) or len(proposal.trajectory) < 2:
        return False, "검증할 이동 경로가 없음"

    def xy(x, y):
        if (isinstance(x, bool) or isinstance(y, bool)
                or not isinstance(x, (int, float))
                or not isinstance(y, (int, float))
                or not (isfinite(x) and isfinite(y))):
            raise ValueError("좌표가 유효하지 않음")
        return (float(x), float(y))

    def cross(a, b, c):
        return ((b[0] - a[0]) * (c[1] - a[1])
                - (b[1] - a[1]) * (c[0] - a[0]))

    def on_segment(p, a, b):
        return (abs(cross(a, b, p)) < 1e-9
                and min(a[0], b[0]) - 1e-9 <= p[0] <= max(a[0], b[0]) + 1e-9
                and min(a[1], b[1]) - 1e-9 <= p[1] <= max(a[1], b[1]) + 1e-9)

    def inside(p, ring):
        result = False
        for a, b in zip(ring, ring[1:]):
            if on_segment(p, a, b):
                return True
            if (a[1] > p[1]) != (b[1] > p[1]):
                x = a[0] + (p[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
                if p[0] < x:
                    result = not result
        return result

    def intersects(a, b, c, d):
        if any((on_segment(a, c, d), on_segment(b, c, d),
                on_segment(c, a, b), on_segment(d, a, b))):
            return True
        return ((cross(a, b, c) > 0) != (cross(a, b, d) > 0)
                and (cross(c, d, a) > 0) != (cross(c, d, b) > 0))

    try:
        path = [xy(p["position"]["x"], p["position"]["y"])
                for p in proposal.trajectory]
        for z in keep_out:
            if z.frame_id != proposal.frame_id:
                return False, f"{z.zone_id} 좌표계 불일치"
            geometry = z.geometry
            if geometry.get("type") == "polygon":
                points = geometry.get("points")
                if not isinstance(points, list) or not points:
                    return False, f"{z.zone_id}의 잘못된 polygon points"
                ring = [xy(*point) for point in points]
                if ring[0] != ring[-1]:
                    ring.append(ring[0])
            elif geometry.get("type") == "Polygon":
                rings = geometry.get("coordinates")
                if not isinstance(rings, list) or len(rings) != 1:
                    return False, f"{z.zone_id}의 지원되지 않는 geometry"
                ring = [xy(*point) for point in rings[0]]
            else:
                return False, f"{z.zone_id}의 지원되지 않는 geometry"
            if len(ring) < 4 or ring[0] != ring[-1] or len(set(ring[:-1])) < 3:
                return False, f"{z.zone_id}의 잘못된 Polygon"
            if abs(sum(cross((0, 0), ring[i], ring[i+1]) for i in range(len(ring)-1))) < 1e-9:
                return False, f"{z.zone_id}의 면적 없는 Polygon"
            if any(inside(p, ring) for p in path):
                return False, f"{z.zone_id} KEEP_OUT 구역 내부 경로"
            if any(intersects(a, b, c, d)
                   for a, b in zip(path, path[1:])
                   for c, d in zip(ring, ring[1:])):
                return False, f"{z.zone_id} KEEP_OUT 구역 교차"
    except (KeyError, TypeError, ValueError, IndexError, OverflowError) as exc:
        return False, f"검증 불가능한 경로 또는 geometry: {exc}"
    return True, "KEEP_OUT 구역과 경로 교차 없음"

WORKER_SNAPSHOT_MAX_AGE_SECONDS = 2.0  # ONT MVP 정책


def default_rules(*, perception_enabled: bool = False) -> list[SafetyRule]:
    return [
        SafetyRule(
            rule_id="rule-speed-limit",
            rule_type=RuleType.SPEED,
            target_robot_type="ALL",
            condition={"operator": "lte", "reference": "robot.motion_limits.max_speed"},
            severity=RuleSeverity.CRITICAL,
            parameters={"default_max_speed": DEFAULT_SPEED_LIMIT},
            on_violation=ViolationAction.REJECT,
        ),
        SafetyRule(
            rule_id="rule-critical-hazard",
            rule_type=RuleType.HAZARD_STATE,
            target_robot_type="ALL",
            condition={"blocked_severity": "CRITICAL", "blocked_status": "OPEN"},
            severity=RuleSeverity.CRITICAL,
            on_violation=ViolationAction.REJECT,
        ),
        SafetyRule(
            rule_id="rule-keep-out",
            rule_type=RuleType.KEEP_OUT,
            target_robot_type="ALL",
            condition={"no_intersection": "KEEP_OUT"},
            severity=RuleSeverity.CRITICAL,
            on_violation=ViolationAction.REJECT,
        ),
        SafetyRule(
            rule_id="rule-observation-gap",
            rule_type=RuleType.OBSERVATION_GAP,
            target_robot_type="ALL",
            condition={},
            severity=RuleSeverity.CRITICAL,
            enabled=perception_enabled,
            on_violation=ViolationAction.REJECT,
        ),
        SafetyRule(
            rule_id="rule-safety-distance",
            rule_type=RuleType.SAFETY_DISTANCE,
            target_robot_type="ALL",
            condition={},
            severity=RuleSeverity.CRITICAL,
            enabled=perception_enabled,
            on_violation=ViolationAction.REJECT,
        ),
    ]


def evaluate_proposal(
    proposal: ActionProposal,
    task: TaskOrder,
    robot: Robot,
    hazards: list[HazardEvent],
    rules: list[SafetyRule],
    zones: list[Zone],
    *,
    sensors: list[Sensor] | None = None,
    workers: list[Worker] | None = None,
    now: datetime | None = None,
) -> tuple[list[RuleResult], list[str], bool]:
    results = [
        RuleResult(
            rule_id="precondition-robot-state",
            passed=robot.status in (RobotStatus.IDLE, RobotStatus.HOLD),
            reason=(
                f"로봇 상태 {robot.status}에서 계획 가능"
                if robot.status in (RobotStatus.IDLE, RobotStatus.HOLD)
                else f"로봇 상태 {robot.status}에서는 실행 계획 불가"
            ),
        ),
        RuleResult(
            rule_id="precondition-control-adapter",
            passed=bool(robot.control_adapter.strip()),
            reason="제어 어댑터 준비됨" if robot.control_adapter.strip() else "제어 어댑터 없음",
        ),
    ]
    blocked_hazard_ids: list[str] = []
    destination_zone_id = getattr(task.destination, "zone_id", None)
    destination_zone = next(
        (zone for zone in zones if zone.zone_id == destination_zone_id),
        None,
    )
    estop_requested = False
    target = proposal.parameters.get("target_pose") or {}
    target_zone = destination_zone if target.get("zone_id") == destination_zone_id else None
    for rule in rules:
        if not rule.enabled:
            continue
        if rule.target_robot_type not in ("ALL", robot.robot_type):
            continue
        if rule.rule_type == RuleType.SPEED:
            speed_caps = [DEFAULT_SPEED_LIMIT, robot.motion_limits.max_speed]
            if destination_zone is not None and destination_zone.max_speed is not None:
                speed_caps.append(destination_zone.max_speed)
            allowed_speed = min(speed_caps)
            passed = proposal.speed_limit <= allowed_speed
            results.append(
                RuleResult(
                    rule_id=rule.rule_id,
                    passed=passed,
                    reason=(
                        f"요청 속도 {proposal.speed_limit}m/s <= 허용 속도 {allowed_speed}m/s"
                        if passed
                        else f"요청 속도 {proposal.speed_limit}m/s > 허용 속도 {allowed_speed}m/s"
                    ),
                )
            )
        elif rule.rule_type == RuleType.HAZARD_STATE:
            blocking = [
                hazard
                for hazard in hazards
                if hazard.status == HazardStatus.OPEN
                and hazard.severity == HazardSeverity.CRITICAL
                and (
                    robot.robot_id in hazard.related_robot_ids
                    or (
                        destination_zone_id is not None
                        and hazard.zone_id == destination_zone_id
                    )
                )
            ]
            blocked_hazard_ids.extend(hazard.hazard_id for hazard in blocking)
            results.append(
                RuleResult(
                    rule_id=rule.rule_id,
                    passed=not blocking,
                    reason=(
                        "관련된 열린 CRITICAL 위험 없음"
                        if not blocking
                        else f"열린 CRITICAL 위험 {len(blocking)}건 존재"
                    ),
                )
            )
        elif rule.rule_type == RuleType.KEEP_OUT:
            passed, reason = _keep_out_result(proposal, task, zones)
            results.append(RuleResult(rule_id=rule.rule_id, passed=passed, reason=reason))
        elif rule.rule_type == RuleType.OBSERVATION_GAP and proposal.action_type != ActionType.STOP:
            result = evaluate_observation_gap(
                target_zone.zone_id if target_zone else None, sensors or [], now
            )
            results.append(result.model_copy(update={"rule_id": rule.rule_id}))
        elif rule.rule_type == RuleType.SAFETY_DISTANCE:
            result, action = evaluate_safety_distance(
                proposal, robot, target_zone, workers, now=now, rule_id=rule.rule_id
            )
            results.append(result)
            estop_requested |= action == ViolationAction.ESTOP
    return results, list(dict.fromkeys(blocked_hazard_ids)), estop_requested

# ontology-schema-v1.1.md
# Sensor.last_frame_at: 2초 무수신 시 DEGRADED
SENSOR_FRAME_TIMEOUT = timedelta(seconds=2)


def evaluate_observation_gap(
    destination_zone_id: str | None,
    sensors: list[Sensor],
    now: datetime | None = None,
) -> RuleResult:
    """Sensor COVERS Zone과 관측 공백 안전 규칙 평가."""

    now = now or datetime.now(timezone.utc)

    if destination_zone_id is None:
        return RuleResult(
            rule_id="rule-observation-gap",
            passed=False,
            reason="대상 Zone을 확인할 수 없어 센서 커버리지 검증 불가",
        )

    valid_sensors = []

    for sensor in sensors:
        # 1. 대상 Zone을 감시하는 센서인가?
        if destination_zone_id not in sensor.covered_zone_ids:
            continue

        # 2. 센서 상태가 ONLINE인가?
        if sensor.status != SensorStatus.ONLINE:
            continue

        # 3. 마지막 프레임 수신 기록이 있는가?
        if sensor.last_frame_at is None:
            continue

        frame_time = sensor.last_frame_at

        # 4. 시각 정보가 유효한가?
        if frame_time.tzinfo is None:
            continue

        frame_age = now - frame_time

        # 5. 마지막 프레임이 2초 이내인가?
        if not (
            timedelta(0)
            <= frame_age
            < SENSOR_FRAME_TIMEOUT
        ):
            continue

        valid_sensors.append(sensor.sensor_id)

    passed = len(valid_sensors) > 0

    return RuleResult(
        rule_id="rule-observation-gap",
        passed=passed,
        reason=(
            f"유효한 ONLINE 센서 {len(valid_sensors)}개 존재"
            if passed
            else f"{destination_zone_id} 관측 가능한 ONLINE 센서 없음"
        ),
    )


def _xy(value: Any) -> tuple[float, float] | None:
    """Vector3 또는 딕셔너리에서 XY 좌표 추출."""
    if isinstance(value, dict):
        x, y = value.get("x"), value.get("y")
    else:
        x = getattr(value, "x", None)
        y = getattr(value, "y", None)

    if any(
        isinstance(n, bool) or not isinstance(n, (int, float))
        for n in (x, y)
    ):
        return None

    if not (isfinite(x) and isfinite(y)):
        return None

    return float(x), float(y)


def _point_to_segment_distance_xy(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    """점과 선분 사이 최단거리(m)."""
    vx = end[0] - start[0]
    vy = end[1] - start[1]
    length_sq = vx * vx + vy * vy

    if length_sq == 0:
        return hypot(
            point[0] - start[0],
            point[1] - start[1],
        )

    ratio = (
        (point[0] - start[0]) * vx
        + (point[1] - start[1]) * vy
    ) / length_sq

    ratio = max(0.0, min(1.0, ratio))

    nearest_x = start[0] + ratio * vx
    nearest_y = start[1] + ratio * vy

    return hypot(
        point[0] - nearest_x,
        point[1] - nearest_y,
    )


def evaluate_safety_distance(
    proposal: ActionProposal,
    robot: Robot,
    zone: Zone | None,
    workers: Sequence[Worker] | None,
    *,
    now: datetime | None = None,
    max_worker_age_seconds: float = WORKER_SNAPSHOT_MAX_AGE_SECONDS,
    rule_id: str = "rule-safety-distance",
) -> tuple[RuleResult, ViolationAction | None]:
    """예상 경로와 작업자 사이의 안전거리 판정.

    반환:
        (RuleResult, 필요한 조치)

    조치는 REJECT / ESTOP / None이다.
    실제 로봇의 상태 변경이나 E-Stop 발행은 하지 않는다.
    """

    def reject(reason: str):
        return (
            RuleResult(
                rule_id=rule_id,
                passed=False,
                reason=reason,
            ),
            ViolationAction.REJECT,
        )

    # 정지 행동은 이 거리 규칙으로 차단하지 않음
    if proposal.action_type == "STOP":
        return (
            RuleResult(
                rule_id=rule_id,
                passed=True,
                reason="정지 명령: 이동 거리 검사 제외",
            ),
            None,
        )

    # 현재 MVP에서는 모바일 로봇 이동만 지원
    if proposal.action_type != "MOVE_TO":
        return reject("지원하지 않는 행동 유형")

    if robot.robot_type != "MOBILE":
        return reject("MOBILE 로봇 XY 경로만 지원")

    if zone is None:
        return reject("Zone 정보 없음")

    if workers is None:
        return reject("Worker 스냅샷 누락")

    warning_distance = zone.minimum_safety_distance
    estop_distance = zone.estop_distance

    if estop_distance > warning_distance:
        return reject("Zone 안전거리 설정 오류")

    # 좌표계가 다르면 거리를 계산할 수 없음
    if (
        proposal.frame_id not in ("map", "world")
        or robot.pose.frame_id != proposal.frame_id
        or zone.frame_id != proposal.frame_id
    ):
        return reject("로봇/Zone/경로 좌표계 불일치")

    if (
        not isfinite(max_worker_age_seconds)
        or max_worker_age_seconds <= 0
    ):
        return reject("Worker 최신성 기준값 오류")

    current_time = now or datetime.now(timezone.utc)

    if (
        current_time.tzinfo is None
        or current_time.utcoffset() is None
    ):
        return reject("비교 시각에 시간대 정보 없음")

    # 로봇 현재 위치부터 예상 경로를 검사
    start = _xy(robot.pose.position)

    if start is None or not proposal.trajectory:
        return reject("로봇 위치 또는 예상 경로 누락")

    path = [start]

    for waypoint in proposal.trajectory:
        if not isinstance(waypoint, dict):
            return reject("예상 경로 형식 오류")
        if waypoint.get("frame_id", proposal.frame_id) != proposal.frame_id:
            return reject("예상 경로 좌표계 불일치")

        point = _xy(waypoint.get("position"))

        if point is None:
            return reject("예상 경로 좌표 오류")

        path.append(point)

    closest = None

    # 모든 작업자에 대해 경로의 최단거리 계산
    for worker in workers:
        if worker.pose.frame_id != proposal.frame_id:
            return reject("Worker 좌표계 불일치")

        observed_at = worker.last_seen_at

        if (
            observed_at.tzinfo is None
            or observed_at.utcoffset() is None
        ):
            return reject("Worker 관측 시각 오류")

        age = (
            current_time - observed_at
        ).total_seconds()

        if not (0 <= age < max_worker_age_seconds):
            return reject("Worker 위치 정보가 오래됨")

        worker_xy = _xy(worker.pose.position)

        if worker_xy is None:
            return reject("Worker 좌표 오류")

        distance = min(
            _point_to_segment_distance_xy(
                worker_xy, left, right
            )
            for left, right in zip(path, path[1:])
        )

        if closest is None or distance < closest[0]:
            closest = (distance, worker.worker_id)

    # Worker 스냅샷은 정상적으로 전달됐지만 작업자 없음
    if closest is None:
        return (
            RuleResult(
                rule_id=rule_id,
                passed=True,
                reason="감지된 작업자 없음",
            ),
            None,
        )

    distance, worker_id = closest

    if distance < estop_distance:
        return (
            RuleResult(
                rule_id=rule_id,
                passed=False,
                reason=(
                    f"{worker_id}: {distance:.3f}m, "
                    "E-Stop 거리 위반"
                ),
            ),
            ViolationAction.ESTOP,
        )

    if distance < warning_distance:
        return (
            RuleResult(
                rule_id=rule_id,
                passed=False,
                reason=(
                    f"{worker_id}: {distance:.3f}m, "
                    "최소 안전거리 위반"
                ),
            ),
            ViolationAction.REJECT,
        )

    return (
        RuleResult(
            rule_id=rule_id,
            passed=True,
            reason=f"최단거리 {distance:.3f}m: 안전",
        ),
        None,
    )
