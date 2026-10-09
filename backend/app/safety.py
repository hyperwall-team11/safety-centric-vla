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
)


def default_rules() -> list[SafetyRule]:
    return [
        SafetyRule(
            rule_id="rule-speed-limit",
            rule_type=RuleType.SPEED,
            target_robot_type="ALL",
            condition={"operator": "lte", "reference": "robot.motion_limits.max_speed"},
            severity=RuleSeverity.CRITICAL,
        ),
        SafetyRule(
            rule_id="rule-critical-hazard",
            rule_type=RuleType.HAZARD_STATE,
            target_robot_type="ALL",
            condition={"blocked_severity": "CRITICAL", "blocked_status": "OPEN"},
            severity=RuleSeverity.CRITICAL,
        ),
    ]


def evaluate_proposal(
    proposal: ActionProposal,
    robot: Robot,
    hazards: list[HazardEvent],
    rules: list[SafetyRule],
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

    for rule in rules:
        if not rule.enabled:
            continue
        if rule.target_robot_type not in ("ALL", robot.robot_type):
            continue
        if rule.rule_type == RuleType.SPEED:
            passed = proposal.speed_limit <= robot.motion_limits.max_speed
            results.append(
                RuleResult(
                    rule_id=rule.rule_id,
                    passed=passed,
                    reason=(
                        f"요청 속도 {proposal.speed_limit}m/s <= 허용 속도 {robot.motion_limits.max_speed}m/s"
                        if passed
                        else f"요청 속도 {proposal.speed_limit}m/s > 허용 속도 {robot.motion_limits.max_speed}m/s"
                    ),
                )
            )
        elif rule.rule_type == RuleType.HAZARD_STATE:
            blocking = [
                hazard
                for hazard in hazards
                if hazard.status == HazardStatus.OPEN
                and hazard.severity == HazardSeverity.CRITICAL
                and (not hazard.related_robot_ids or robot.robot_id in hazard.related_robot_ids)
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
