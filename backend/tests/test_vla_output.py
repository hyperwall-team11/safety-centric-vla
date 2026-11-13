import unittest

from app.models import ActionProposal, Robot, TaskOrder
from app.vla_output import VlaOutputValidationError, validate_vla_proposal


ROBOT = Robot.model_validate({
    "robot_id": "robot-01",
    "robot_type": "MOBILE",
    "control_adapter": "mock-mobile-adapter",
    "status": "IDLE",
    "pose": {
        "frame_id": "map",
        "position": {"x": 0, "y": 0, "z": 0},
        "orientation": {"x": 0, "y": 0, "z": 0, "w": 1},
    },
    "velocity": {"x": 0, "y": 0, "z": 0},
    "motion_limits": {"max_speed": 0.5, "max_acceleration": 0.5},
})


def move_task() -> TaskOrder:
    return TaskOrder.model_validate({
        "task_id": "task-move",
        "instruction": "robot-01 로봇을 A구역으로 이동해",
        "target_robot_id": "robot-01",
        "target_object_id": None,
        "destination": {"type": "ZONE", "zone_id": "A구역"},
    })


def move_proposal(**changes) -> ActionProposal:
    payload = {
        "proposal_id": "proposal-move",
        "task_id": "task-move",
        "robot_id": "robot-01",
        "action_type": "MOVE_TO",
        "parameters": {
            "target_pose": {"type": "ZONE", "zone_id": "A구역"},
            "speed_limit": 0.3,
        },
        "trajectory": None,
        "frame_id": "map",
        "speed_limit": 0.3,
        "model_name": "mock-vla",
        "model_version": "test-1",
    }
    payload.update(changes)
    return ActionProposal.model_validate(payload)


class VlaOutputValidationTest(unittest.TestCase):
    def test_accepts_proposal_matching_task_context(self):
        validate_vla_proposal(move_proposal(), move_task(), ROBOT)

    def test_rejects_action_type_changed_by_model(self):
        proposal = move_proposal(
            action_type="MANIPULATE",
            parameters={
                "manipulation": "PICK",
                "object_id": "box-1",
                "target_pose": {"type": "ZONE", "zone_id": "A구역"},
            },
        )
        with self.assertRaisesRegex(VlaOutputValidationError, "action_type"):
            validate_vla_proposal(proposal, move_task(), ROBOT)

    def test_rejects_destination_changed_by_model(self):
        proposal = move_proposal(parameters={
            "target_pose": {"type": "ZONE", "zone_id": "B구역"},
            "speed_limit": 0.3,
        })
        with self.assertRaisesRegex(VlaOutputValidationError, "target_pose"):
            validate_vla_proposal(proposal, move_task(), ROBOT)

    def test_rejects_frame_changed_without_transform(self):
        with self.assertRaisesRegex(VlaOutputValidationError, "frame_id"):
            validate_vla_proposal(move_proposal(frame_id="odom"), move_task(), ROBOT)

    def test_rejects_wrong_manipulation_object(self):
        task = TaskOrder.model_validate({
            "task_id": "task-box",
            "instruction": "robot-01 로봇이 상자3을 B작업대로 옮겨줘",
            "target_robot_id": "robot-01",
            "target_object_id": "box-3",
            "destination": {"type": "ZONE", "zone_id": "B작업대"},
        })
        proposal = move_proposal(
            task_id="task-box",
            action_type="MANIPULATE",
            parameters={
                "manipulation": "PICK",
                "object_id": "box-4",
                "target_pose": {"type": "ZONE", "zone_id": "B작업대"},
            },
        )
        with self.assertRaisesRegex(VlaOutputValidationError, "object_id"):
            validate_vla_proposal(proposal, task, ROBOT)


if __name__ == "__main__":
    unittest.main()
