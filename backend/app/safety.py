from __future__ import annotations

from math import isfinite

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
    TaskOrder,
    ViolationAction,
    Zone,
    ZoneType,
)


# docs/ontology-schema-v1.1.md §5: 속도 상한 및 KEEP_OUT 경로 교차 금지.
DEFAULT_SPEED_LIMIT = 0.4
_GEOMETRY_EPS = 1e-9  # 부동소수점 오차 허용치(거리 안전 여유값이 아님).


# docs/ontology-schema-v1.1.md §5: KEEP_OUT 경로 교차 금지.
# 시범 구현: GeoJSON Polygon 2D, trajectory = [{"position": {"x": ..., "y": ...}}, ...].
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
            rings = geometry.get("coordinates")
            if geometry.get("type") != "Polygon" or not isinstance(rings, list) or len(rings) != 1:
                return False, f"{z.zone_id}의 지원되지 않는 geometry"
            ring = [xy(*point) for point in rings[0]]
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
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        return False, f"검증 불가능한 경로 또는 geometry: {exc}"
    return True, "KEEP_OUT 구역과 경로 교차 없음"

def default_rules() -> list[SafetyRule]:
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
    ]


def evaluate_proposal(
    proposal: ActionProposal,
    task: TaskOrder,
    robot: Robot,
    hazards: list[HazardEvent],
    rules: list[SafetyRule],
    zones: list[Zone],
) -> tuple[list[RuleResult], list[str]]:
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
    return results, list(dict.fromkeys(blocked_hazard_ids))
