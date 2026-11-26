"""OpenVLA 7D output adapter.

OpenVLA emits one closed-loop end-effector step as
``[dx, dy, dz, droll, dpitch, dyaw, gripper]``.  This module only translates
that untrusted vector into the typed ActionProposal contract.  Bounds that
depend on the deployment safety policy are evaluated by ``safety.py`` before
the command may reach ROS 2.
"""
from __future__ import annotations

from collections.abc import Sequence
from math import isfinite

from .models import ActionProposal, ManipulationType, NonBlankString, Robot, TaskOrder
from .vla_output import VlaOutputConversionError, convert_vla_output_to_proposal


OPENVLA_ACTION_DIMENSIONS = 7


def convert_openvla_action_7d(
    action_7d: Sequence[float],
    task: TaskOrder,
    robot: Robot,
    *,
    manipulation: ManipulationType,
    speed_limit: float,
    phase_complete: bool = False,
    model_name: NonBlankString = "openvla-7b",
    model_version: NonBlankString = "unknown",
) -> ActionProposal:
    """Convert one OpenVLA 7D step into a MANIPULATE/EE_DELTA proposal.

    Task, robot, object and destination identifiers are injected from trusted
    backend context; the model cannot override them.
    """

    if isinstance(action_7d, (str, bytes, bytearray)) or len(action_7d) != OPENVLA_ACTION_DIMENSIONS:
        raise VlaOutputConversionError("OpenVLA action_7d는 유한한 숫자 7개여야 합니다.")

    values: list[float] = []
    for value in action_7d:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
            raise VlaOutputConversionError("OpenVLA action_7d는 유한한 숫자 7개여야 합니다.")
        values.append(float(value))

    if task.target_object_id is None:
        raise VlaOutputConversionError("OpenVLA 조작에는 TaskOrder.target_object_id가 필요합니다.")

    dx, dy, dz, droll, dpitch, dyaw, gripper = values
    output = {
        "action_type": "MANIPULATE",
        "parameters": {
            "manipulation": manipulation,
            "object_id": task.target_object_id,
            "target_pose": task.destination.model_dump(mode="json"),
            "ee_delta": {
                "translation": {"x": dx, "y": dy, "z": dz},
                "rotation_rpy": {"x": droll, "y": dpitch, "z": dyaw},
                "gripper": gripper,
                "phase_complete": phase_complete,
            },
        },
        "trajectory": None,
        "frame_id": task.destination.frame_id if hasattr(task.destination, "frame_id") else robot.pose.frame_id,
        "speed_limit": speed_limit,
    }
    return convert_vla_output_to_proposal(
        output,
        task,
        robot,
        model_name=model_name,
        model_version=model_version,
    )
