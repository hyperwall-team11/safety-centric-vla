from __future__ import annotations

from .models import (
    ActionProposal,
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
)


DEFAULT_SPEED_LIMIT = 0.4


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
    return results, list(dict.fromkeys(blocked_hazard_ids))
