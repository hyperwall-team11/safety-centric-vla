from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from pydantic import ValidationError

from .instruction_intent import IntentDetectionError, detect_requested_action
from .models import (
    ActionProposal,
    ActionType,
    CoordinateDestination,
    ProposalGenerationRequest,
    Robot,
    TaskOrder,
    Vector3,
    Zone,
)
from .vla_output import VlaOutputConversionError, convert_vla_output_to_proposal


class ActionPlanningError(ValueError):
    pass


class ActionProviderError(RuntimeError):
    """VLA 추론 서비스를 호출하지 못했거나 결과를 받지 못한 경우."""


class ActionPlanner(Protocol):
    model_name: str
    model_version: str

    def plan(
        self,
        task: TaskOrder,
        robot: Robot,
        request: ProposalGenerationRequest,
        *,
        destination_zone: Zone | None = None,
    ) -> ActionProposal: ...


class VlaOutputProvider(Protocol):
    """TaskOrder 문맥으로 VLA 행동 JSON을 생성하는 추론 경계."""

    model_name: str
    model_version: str

    def generate(
        self,
        task: TaskOrder,
        robot: Robot,
        request: ProposalGenerationRequest,
        *,
        destination_zone: Zone | None = None,
    ) -> str | bytes | bytearray | Mapping[str, Any]: ...


class VlaActionPlanner:
    """VLA 추론 출력을 공통 ActionProposal로 변환하는 플래너."""

    def __init__(self, provider: VlaOutputProvider) -> None:
        self.provider = provider
        self.model_name = provider.model_name
        self.model_version = provider.model_version

    def plan(
        self,
        task: TaskOrder,
        robot: Robot,
        request: ProposalGenerationRequest,
        *,
        destination_zone: Zone | None = None,
    ) -> ActionProposal:
        try:
            output = self.provider.generate(
                task,
                robot,
                request,
                destination_zone=destination_zone,
            )
        except Exception as exc:
            raise ActionProviderError("VLA 추론 서비스 호출에 실패했습니다.") from exc
        try:
            return convert_vla_output_to_proposal(
                output,
                task,
                robot,
                model_name=self.model_name,
                model_version=self.model_version,
                destination_zone=destination_zone,
            )
        except VlaOutputConversionError as exc:
            raise ActionPlanningError(str(exc)) from exc


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
        *,
        destination_zone: Zone | None = None,
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

        trajectory = None
        if action_type == ActionType.MOVE_TO:
            target_position = None
            if isinstance(task.destination, CoordinateDestination):
                target_position = task.destination.position
            elif destination_zone is not None and destination_zone.frame_id == default_frame_id:
                navigation_target = destination_zone.geometry.get("navigation_target")
                if navigation_target is not None:
                    try:
                        target_position = Vector3.model_validate(navigation_target)
                    except ValidationError as exc:
                        raise ActionPlanningError("Zone의 navigation_target 좌표가 유효하지 않습니다.") from exc

            # 좌표 변환 없이 검증할 수 있는 직선 경로만 제안한다.
            if target_position is not None and robot.pose.frame_id == default_frame_id:
                trajectory = [
                    {"position": robot.pose.position.model_dump(mode="json")},
                    {"position": target_position.model_dump(mode="json")},
                ]

        return ActionProposal(
            task_id=task.task_id,
            robot_id=task.target_robot_id,
            action_type=action_type,
            parameters=parameters,
            trajectory=trajectory,
            frame_id=default_frame_id,
            speed_limit=speed_limit,
            model_name=self.model_name,
            model_version=self.model_version,
        )
