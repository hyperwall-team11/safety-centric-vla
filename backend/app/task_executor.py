from __future__ import annotations

from pydantic import ValidationError

from .models import (
    ActionProposal,
    ActionType,
    CoordinateDestination,
    ManipulationType,
    PhysicalObject,
    ProposalGenerationRequest,
    ProposalStatus,
    Robot,
    TaskOrder,
    Vector3,
    Zone,
)


class TaskExecutionPlanningError(ValueError):
    """현재 Task 상태에서 안전한 다음 단계를 만들 수 없을 때 발생한다."""


def _coordinate_target(frame_id: str, position: Vector3) -> dict[str, object]:
    return {
        "type": "COORDINATE",
        "frame_id": frame_id,
        "position": position.model_dump(mode="json"),
    }


def _destination_position(
    task: TaskOrder,
    destination_zone: Zone | None,
) -> tuple[str, Vector3] | None:
    if isinstance(task.destination, CoordinateDestination):
        return task.destination.frame_id, task.destination.position
    if destination_zone is None:
        return None
    navigation_target = destination_zone.geometry.get("navigation_target")
    if navigation_target is None:
        return None
    try:
        return destination_zone.frame_id, Vector3.model_validate(navigation_target)
    except ValidationError as exc:
        raise TaskExecutionPlanningError(
            "목적지 Zone의 navigation_target 좌표가 유효하지 않습니다."
        ) from exc


def _move_proposal(
    task: TaskOrder,
    robot: Robot,
    target_pose: dict[str, object],
    target_position: tuple[str, Vector3] | None,
    speed_limit: float,
) -> ActionProposal:
    frame_id = target_position[0] if target_position is not None else robot.pose.frame_id
    trajectory = None
    if target_position is not None and robot.pose.frame_id == frame_id:
        trajectory = [
            {"position": robot.pose.position.model_dump(mode="json")},
            {"position": target_position[1].model_dump(mode="json")},
        ]
    return ActionProposal(
        task_id=task.task_id,
        robot_id=task.target_robot_id,
        action_type=ActionType.MOVE_TO,
        parameters={"target_pose": target_pose, "speed_limit": speed_limit},
        trajectory=trajectory,
        frame_id=frame_id,
        speed_limit=speed_limit,
        model_name="task-executor",
        model_version="0.1.0",
    )


def _manipulate_proposal(
    task: TaskOrder,
    robot: Robot,
    manipulation: ManipulationType,
    target_pose: dict[str, object],
    frame_id: str,
    speed_limit: float,
) -> ActionProposal:
    return ActionProposal(
        task_id=task.task_id,
        robot_id=task.target_robot_id,
        action_type=ActionType.MANIPULATE,
        parameters={
            "manipulation": manipulation,
            "object_id": task.target_object_id,
            "target_pose": target_pose,
        },
        trajectory=None,
        frame_id=frame_id,
        speed_limit=speed_limit,
        model_name="task-executor",
        model_version="0.1.0",
    )


def plan_next_task_action(
    task: TaskOrder,
    robot: Robot,
    target_object: PhysicalObject,
    proposals: list[ActionProposal],
    request: ProposalGenerationRequest,
    *,
    destination_zone: Zone | None = None,
) -> ActionProposal:
    """성공 이력에서 물체 운반 Task의 다음 단계를 결정한다.

    접근 MOVE_TO → PICK → 목적지 MOVE_TO → PLACE 순서이며, 실패·거절된
    Proposal은 단계 완료로 세지 않아 다음 호출에서 같은 단계를 재계획한다.
    """
    if task.target_object_id != target_object.object_id:
        raise TaskExecutionPlanningError("TaskOrder 대상과 PhysicalObject ID가 다릅니다.")
    if not target_object.movable:
        raise TaskExecutionPlanningError("이동할 수 없는 PhysicalObject입니다.")

    speed_limit = request.speed_limit or min(0.4, robot.motion_limits.max_speed)
    succeeded = [
        proposal
        for proposal in proposals
        if proposal.status == ProposalStatus.SUCCEEDED
        # EE_DELTA 한 번의 성공은 제어 주기 한 번이 완료된 것이지
        # PICK/PLACE 단계 전체가 끝난 것은 아니다.
        and (
            "ee_delta" not in proposal.parameters
            or proposal.parameters["ee_delta"].get("phase_complete") is True
        )
    ]
    pick_index = next(
        (
            index
            for index, proposal in enumerate(succeeded)
            if proposal.action_type == ActionType.MANIPULATE
            and proposal.parameters.get("manipulation") == ManipulationType.PICK.value
        ),
        None,
    )

    object_target = _coordinate_target(target_object.pose.frame_id, target_object.pose.position)
    if pick_index is None:
        approached = any(proposal.action_type == ActionType.MOVE_TO for proposal in succeeded)
        if not approached:
            approach_position = Vector3(
                x=target_object.pose.position.x,
                y=target_object.pose.position.y,
                z=robot.pose.position.z,
            )
            return _move_proposal(
                task,
                robot,
                _coordinate_target(target_object.pose.frame_id, approach_position),
                (target_object.pose.frame_id, approach_position),
                speed_limit,
            )
        return _manipulate_proposal(
            task,
            robot,
            ManipulationType.PICK,
            object_target,
            target_object.pose.frame_id,
            speed_limit,
        )

    after_pick = succeeded[pick_index + 1 :]
    transported = any(proposal.action_type == ActionType.MOVE_TO for proposal in after_pick)
    if not transported:
        destination = task.destination.model_dump(mode="json")
        return _move_proposal(
            task,
            robot,
            destination,
            _destination_position(task, destination_zone),
            speed_limit,
        )

    destination_frame = (
        task.destination.frame_id
        if isinstance(task.destination, CoordinateDestination)
        else destination_zone.frame_id if destination_zone is not None else robot.pose.frame_id
    )
    return _manipulate_proposal(
        task,
        robot,
        ManipulationType.PLACE,
        task.destination.model_dump(mode="json"),
        destination_frame,
        speed_limit,
    )
