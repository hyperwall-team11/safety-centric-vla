from __future__ import annotations

import json
from collections.abc import Mapping
from math import dist
from typing import Any

from pydantic import ValidationError

from .instruction_intent import IntentDetectionError, detect_requested_action
from .models import (
    ActionProposal,
    ActionType,
    CoordinateDestination,
    NonBlankString,
    OntologyModel,
    Robot,
    TaskOrder,
    Vector3,
    Zone,
)


# CTL diff-drive 도착 판정 및 Backend 실행 직전 경로 시작점 검사와 같은 허용 오차다.
TRAJECTORY_CONTEXT_TOLERANCE_M = 0.05


class VlaOutputValidationError(ValueError):
    """VLA 제안이 TaskOrder 문맥과 일치하지 않을 때 발생한다."""


class VlaOutputConversionError(ValueError):
    """VLA 원시 출력을 ActionProposal로 변환할 수 없을 때 발생한다."""


class VlaModelOutput(OntologyModel):
    """VLA가 생성하는 신뢰하지 않는 행동 출력 경계.

    Proposal의 ID·대상 Robot·상태는 VLA가 결정하지 않고 Backend가
    TaskOrder 문맥에서 주입한다.
    """

    action_type: ActionType
    parameters: dict[str, Any]
    trajectory: list[dict[str, Any]] | None = None
    frame_id: NonBlankString
    speed_limit: float


def convert_vla_output_to_proposal(
    output: str | bytes | bytearray | Mapping[str, Any],
    task: TaskOrder,
    robot: Robot,
    *,
    model_name: NonBlankString,
    model_version: NonBlankString,
    destination_zone: Zone | None = None,
) -> ActionProposal:
    """VLA JSON 출력을 검증된 ``ActionProposal(PROPOSED)``로 변환한다.

    VLA에서는 행동 필드만 받고 ``task_id``와 ``robot_id``는 신뢰된
    Backend 문맥에서 주입한다. 구조 검증 후 TaskOrder 문맥 검증까지
    통과해야 변환된 Proposal을 반환한다.
    """

    try:
        payload = json.loads(output) if isinstance(output, (str, bytes, bytearray)) else output
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise VlaOutputConversionError("VLA 출력이 유효한 JSON이 아닙니다.") from exc

    try:
        model_output = VlaModelOutput.model_validate(payload)
        proposal = ActionProposal(
            task_id=task.task_id,
            robot_id=task.target_robot_id,
            action_type=model_output.action_type,
            parameters=model_output.parameters,
            trajectory=model_output.trajectory,
            frame_id=model_output.frame_id,
            speed_limit=model_output.speed_limit,
            model_name=model_name,
            model_version=model_version,
        )
    except ValidationError as exc:
        raise VlaOutputConversionError(f"VLA 출력 구조가 유효하지 않습니다: {exc}") from exc

    try:
        validate_vla_proposal(
            proposal,
            task,
            robot,
            destination_zone=destination_zone,
        )
    except VlaOutputValidationError as exc:
        raise VlaOutputConversionError(str(exc)) from exc
    return proposal


def validate_vla_proposal(
    proposal: ActionProposal,
    task: TaskOrder,
    robot: Robot,
    *,
    destination_zone: Zone | None = None,
) -> None:
    """모델 출력의 구조 검증 뒤 TaskOrder 문맥 일치 여부를 검증한다.

    Pydantic ``ActionProposal``이 action별 필수 파라미터를 검증하고, 이 함수는
    모델이 TaskOrder의 행동·대상·목적지·좌표계를 바꾸지 않았는지 확인한다.
    속도·경로의 안전성은 이후 제출 기준 평가가 담당한다.
    """

    if proposal.task_id != task.task_id:
        raise VlaOutputValidationError("VLA 출력의 task_id가 TaskOrder와 다릅니다.")
    if proposal.robot_id != task.target_robot_id or proposal.robot_id != robot.robot_id:
        raise VlaOutputValidationError("VLA 출력의 robot_id가 TaskOrder 대상 로봇과 다릅니다.")

    try:
        expected_action = detect_requested_action(
            task.instruction,
            has_target_object=task.target_object_id is not None,
        )
    except IntentDetectionError as exc:
        raise VlaOutputValidationError(f"TaskOrder 행동을 확인할 수 없습니다: {exc}") from exc

    actual_action = ActionType(proposal.action_type)
    if actual_action != expected_action:
        raise VlaOutputValidationError(
            f"VLA 출력 action_type({actual_action.value})이 "
            f"TaskOrder 행동({expected_action.value})과 다릅니다."
        )
    if actual_action in {ActionType.STOP, ActionType.RESUME}:
        raise VlaOutputValidationError(
            "STOP·RESUME은 일반 VLA 제안이 아니라 안전 제어 경로에서 생성해야 합니다."
        )

    target_pose = proposal.parameters.get("target_pose")
    expected_destination = task.destination.model_dump(mode="json")
    if target_pose != expected_destination:
        raise VlaOutputValidationError("VLA 출력의 target_pose가 TaskOrder 목적지와 다릅니다.")

    if actual_action == ActionType.MANIPULATE:
        if proposal.parameters.get("object_id") != task.target_object_id:
            raise VlaOutputValidationError("VLA 출력의 object_id가 TaskOrder 대상 물체와 다릅니다.")
    elif task.target_object_id is not None:
        raise VlaOutputValidationError("대상 물체가 있는 TaskOrder에는 MANIPULATE 제안이 필요합니다.")

    expected_frame = (
        task.destination.frame_id
        if isinstance(task.destination, CoordinateDestination)
        else robot.pose.frame_id
    )
    if proposal.frame_id != expected_frame:
        raise VlaOutputValidationError(
            f"VLA 출력 frame_id({proposal.frame_id})가 예상 좌표계({expected_frame})와 다릅니다."
        )

    if actual_action != ActionType.MOVE_TO or proposal.trajectory is None:
        return
    if len(proposal.trajectory) < 2:
        raise VlaOutputValidationError("MOVE_TO trajectory에는 시작점과 끝점이 필요합니다.")

    def position(point: dict[str, Any], label: str) -> Vector3:
        try:
            return Vector3.model_validate(point.get("position"))
        except (AttributeError, ValidationError) as exc:
            raise VlaOutputValidationError(
                f"VLA 출력 trajectory {label}의 position이 유효하지 않습니다."
            ) from exc

    start = position(proposal.trajectory[0], "시작점")
    if dist(
        (start.x, start.y, start.z),
        (robot.pose.position.x, robot.pose.position.y, robot.pose.position.z),
    ) > TRAJECTORY_CONTEXT_TOLERANCE_M:
        raise VlaOutputValidationError("VLA 출력 trajectory 시작점이 Robot 현재 위치와 다릅니다.")

    expected_end: Vector3 | None = None
    if isinstance(task.destination, CoordinateDestination):
        expected_end = task.destination.position
    elif destination_zone is not None and destination_zone.frame_id == proposal.frame_id:
        navigation_target = destination_zone.geometry.get("navigation_target")
        if navigation_target is not None:
            try:
                expected_end = Vector3.model_validate(navigation_target)
            except ValidationError as exc:
                raise VlaOutputValidationError(
                    "TaskOrder 목적지 Zone의 navigation_target이 유효하지 않습니다."
                ) from exc

    if expected_end is not None:
        end = position(proposal.trajectory[-1], "끝점")
        if dist(
            (end.x, end.y, end.z),
            (expected_end.x, expected_end.y, expected_end.z),
        ) > TRAJECTORY_CONTEXT_TOLERANCE_M:
            raise VlaOutputValidationError("VLA 출력 trajectory 끝점이 TaskOrder 목적지와 다릅니다.")
