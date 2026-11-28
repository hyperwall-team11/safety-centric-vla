import unittest

from app.models import (
    ActionProposal,
    PhysicalObject,
    ProposalGenerationRequest,
    Robot,
    TaskOrder,
)
from app.task_executor import plan_next_task_action


ROBOT = Robot.model_validate({
    "robot_id": "robot-01",
    "robot_type": "MOBILE",
    "control_adapter": "mujoco_diff_drive",
    "status": "IDLE",
    "pose": {
        "frame_id": "world",
        "position": {"x": 0.5, "y": 0.3, "z": 0},
        "orientation": {"x": 0, "y": 0, "z": 0, "w": 1},
    },
    "velocity": {"x": 0, "y": 0, "z": 0},
    "motion_limits": {"max_speed": 0.4, "max_acceleration": 0.5},
})
TASK = TaskOrder.model_validate({
    "task_id": "task-vla",
    "instruction": "robot-01이 빨간 상자를 좌표 (1, 2, 0)로 옮겨",
    "target_robot_id": "robot-01",
    "target_object_id": "box-red",
    "destination": {
        "type": "COORDINATE",
        "frame_id": "world",
        "position": {"x": 1, "y": 2, "z": 0},
    },
})
OBJECT = PhysicalObject.model_validate({
    "object_id": "box-red",
    "object_type": "BOX",
    "pose": {
        "frame_id": "world",
        "position": {"x": 0.5, "y": 0.3, "z": 0.1},
        "orientation": {"x": 0, "y": 0, "z": 0, "w": 1},
    },
    "current_zone_id": "A",
    "movable": True,
})


def proposal(action_type, parameters, proposal_id):
    return ActionProposal.model_validate({
        "proposal_id": proposal_id,
        "task_id": "task-vla",
        "robot_id": "robot-01",
        "action_type": action_type,
        "parameters": parameters,
        "trajectory": None,
        "frame_id": "world",
        "speed_limit": 0.2,
        "model_name": "test",
        "model_version": "1",
        "status": "SUCCEEDED",
    })


APPROACH = proposal(
    "MOVE_TO",
    {
        "target_pose": {
            "type": "COORDINATE",
            "frame_id": "world",
            "position": {"x": 0.5, "y": 0.3, "z": 0},
        },
        "speed_limit": 0.2,
    },
    "approach",
)


class OpenVlaTaskProgressTest(unittest.TestCase):
    def ee_pick(self, complete):
        return proposal(
            "MANIPULATE",
            {
                "manipulation": "PICK",
                "object_id": "box-red",
                "target_pose": {
                    "type": "COORDINATE",
                    "frame_id": "world",
                    "position": {"x": 0.5, "y": 0.3, "z": 0.1},
                },
                "ee_delta": {
                    "translation": {"x": 0.01, "y": 0, "z": 0},
                    "rotation_rpy": {"x": 0, "y": 0, "z": 0},
                    "gripper": 0,
                    "phase_complete": complete,
                },
            },
            f"pick-{complete}",
        )

    def test_incomplete_delta_does_not_advance_pick_stage(self):
        next_action = plan_next_task_action(
            TASK,
            ROBOT,
            OBJECT,
            [APPROACH, self.ee_pick(False)],
            ProposalGenerationRequest(),
        )

        self.assertEqual(next_action.action_type, "MANIPULATE")
        self.assertEqual(next_action.parameters["manipulation"], "PICK")

    def test_observation_confirmed_delta_advances_to_transport(self):
        next_action = plan_next_task_action(
            TASK,
            ROBOT,
            OBJECT,
            [APPROACH, self.ee_pick(True)],
            ProposalGenerationRequest(),
        )

        self.assertEqual(next_action.action_type, "MOVE_TO")
        self.assertEqual(next_action.parameters["target_pose"], TASK.destination.model_dump(mode="json"))


if __name__ == "__main__":
    unittest.main()
