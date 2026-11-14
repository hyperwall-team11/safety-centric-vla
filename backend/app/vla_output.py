from __future__ import annotations

from .instruction_intent import IntentDetectionError, detect_requested_action
from .models import (
    ActionProposal,
    ActionType,
    CoordinateDestination,
    Robot,
    TaskOrder,
)


class VlaOutputValidationError(ValueError):
    """VLA 제안이 TaskOrder 문맥과 일치하지 않을 때 발생한다."""


def validate_vla_proposal(
    proposal: ActionProposal,
    task: TaskOrder,
    robot: Robot,
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
