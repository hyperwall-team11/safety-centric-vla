from __future__ import annotations

from typing import Protocol

from .instruction_intent import IntentDetectionError, detect_requested_action
from .models import (
    ActionProposal,
    ActionType,
    CoordinateDestination,
    ProposalGenerationRequest,
    Robot,
    TaskOrder,
)


class ActionPlanningError(ValueError):
    pass


class ActionPlanner(Protocol):
    model_name: str
    model_version: str

    def plan(
        self,
        task: TaskOrder,
        robot: Robot,
        request: ProposalGenerationRequest,
    ) -> ActionProposal: ...


class RuleBasedActionPlanner:
    """Deterministic MVP adapter for the future VLA model boundary."""

    model_name = "rule-based-action-planner"
    model_version = "0.2.0"
    default_motion_speed = 0.4

    def plan(
        self,
        task: TaskOrder,
        robot: Robot,
        request: ProposalGenerationRequest,
    ) -> ActionProposal:
        if task.target_robot_id != robot.robot_id:
            raise ActionPlanningError("TaskOrder와 Robot의 ID가 일치하지 않습니다.")

        try:
            action_type = detect_requested_action(
                task.instruction,
                has_target_object=task.target_object_id is not None,
            )
        except IntentDetectionError as exc:
            raise ActionPlanningError(str(exc)) from exc

        if action_type in {ActionType.STOP, ActionType.RESUME}:
            raise ActionPlanningError(
                f"{action_type.value}은 현재 TaskOrder v1.1 자동 제안에서 지원하지 않습니다."
            )

        speed_limit = (
            request.speed_limit
            if request.speed_limit is not None
            else min(self.default_motion_speed, robot.motion_limits.max_speed)
        )
        destination = task.destination.model_dump(mode="json")

        if action_type == ActionType.MANIPULATE:
            if task.target_object_id is None:
                raise ActionPlanningError("MANIPULATE 작업에는 대상 물체 ID가 필요합니다.")
            if request.manipulation is None:
                raise ActionPlanningError(
                    "물체 작업은 manipulation을 PICK 또는 PLACE로 명시해야 합니다."
                )
            parameters: dict[str, object] = {
                "manipulation": request.manipulation,
                "object_id": task.target_object_id,
                "target_pose": destination,
            }
        elif action_type == ActionType.MOVE_TO:
            if task.target_object_id is not None:
                raise ActionPlanningError("MOVE_TO 작업에는 대상 물체를 지정할 수 없습니다.")
            if request.manipulation is not None:
                raise ActionPlanningError(
                    "MOVE_TO 작업에는 manipulation을 지정할 수 없습니다."
                )
            parameters = {
                "target_pose": destination,
                "speed_limit": speed_limit,
            }
        else:
            raise ActionPlanningError(f"지원하지 않는 Action 유형입니다: {action_type.value}")

        default_frame_id = (
            task.destination.frame_id
            if isinstance(task.destination, CoordinateDestination)
            else robot.pose.frame_id
        )
        if not default_frame_id.strip():
            raise ActionPlanningError("ActionProposal의 좌표계 frame_id를 결정할 수 없습니다.")

        return ActionProposal(
            task_id=task.task_id,
            robot_id=task.target_robot_id,
            action_type=action_type,
            parameters=parameters,
            trajectory=None,
            frame_id=default_frame_id,
            speed_limit=speed_limit,
            model_name=self.model_name,
            model_version=self.model_version,
        )
