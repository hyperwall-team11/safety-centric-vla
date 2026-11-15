import unittest

from app.action_planner import ActionPlanningError, VlaActionPlanner
from app.models import ActionProposal, ProposalGenerationRequest, Robot, TaskOrder
from app.vla_output import (
    VlaOutputConversionError,
    VlaOutputValidationError,
    convert_vla_output_to_proposal,
    validate_vla_proposal,
)


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


def raw_move_output(**changes):
    payload = {
        "action_type": "MOVE_TO",
        "parameters": {
            "target_pose": {"type": "ZONE", "zone_id": "A구역"},
            "speed_limit": 0.3,
        },
        "trajectory": None,
        "frame_id": "map",
        "speed_limit": 0.3,
    }
    payload.update(changes)
    return payload


class StaticVlaOutputProvider:
    model_name = "mock-vla"
    model_version = "test-1"

    def __init__(self, output):
        self.output = output
        self.calls = []

    def generate(self, task, robot, request, *, destination_zone=None):
        self.calls.append((task, robot, request, destination_zone))
        return self.output


class VlaActionPlannerTest(unittest.TestCase):
    def test_planner_converts_provider_output(self):
        provider = StaticVlaOutputProvider(raw_move_output())
        planner = VlaActionPlanner(provider)
        request = ProposalGenerationRequest()

        proposal = planner.plan(move_task(), ROBOT, request)

        self.assertEqual(proposal.action_type, "MOVE_TO")
        self.assertEqual(proposal.model_name, provider.model_name)
        self.assertEqual(len(provider.calls), 1)
        self.assertIs(provider.calls[0][2], request)

    def test_planner_normalizes_invalid_output_as_planning_error(self):
        planner = VlaActionPlanner(StaticVlaOutputProvider({"action_type": "MOVE_TO"}))

        with self.assertRaisesRegex(ActionPlanningError, "VLA 출력 구조"):
            planner.plan(move_task(), ROBOT, ProposalGenerationRequest())


class VlaOutputConversionTest(unittest.TestCase):
    def test_converts_model_fields_and_injects_trusted_context(self):
        proposal = convert_vla_output_to_proposal(
            raw_move_output(),
            move_task(),
            ROBOT,
            model_name="mock-vla",
            model_version="test-1",
        )

        self.assertEqual(proposal.task_id, "task-move")
        self.assertEqual(proposal.robot_id, "robot-01")
        self.assertEqual(proposal.status, "PROPOSED")
        self.assertTrue(proposal.proposal_id.startswith("proposal-"))
        self.assertEqual(proposal.model_name, "mock-vla")

    def test_accepts_json_text(self):
        raw_json = """{
            "action_type": "MOVE_TO",
            "parameters": {
                "target_pose": {"type": "ZONE", "zone_id": "A구역"},
                "speed_limit": 0.3
            },
            "trajectory": null,
            "frame_id": "map",
            "speed_limit": 0.3
        }"""

        proposal = convert_vla_output_to_proposal(
            raw_json,
            move_task(),
            ROBOT,
            model_name="mock-vla",
            model_version="test-1",
        )

        self.assertEqual(proposal.action_type, "MOVE_TO")

    def test_converts_manipulation_output(self):
        task = TaskOrder.model_validate({
            "task_id": "task-box",
            "instruction": "robot-01 로봇이 상자3을 B작업대로 옮겨줘",
            "target_robot_id": "robot-01",
            "target_object_id": "box-3",
            "destination": {"type": "ZONE", "zone_id": "B작업대"},
        })
        output = raw_move_output(
            action_type="MANIPULATE",
            parameters={
                "manipulation": "PICK",
                "object_id": "box-3",
                "target_pose": {"type": "ZONE", "zone_id": "B작업대"},
            },
        )

        proposal = convert_vla_output_to_proposal(
            output,
            task,
            ROBOT,
            model_name="mock-vla",
            model_version="test-1",
        )

        self.assertEqual(proposal.action_type, "MANIPULATE")
        self.assertEqual(proposal.parameters["object_id"], "box-3")

    def test_rejects_malformed_json(self):
        with self.assertRaisesRegex(VlaOutputConversionError, "JSON"):
            convert_vla_output_to_proposal(
                "{not-json",
                move_task(),
                ROBOT,
                model_name="mock-vla",
                model_version="test-1",
            )

    def test_rejects_server_owned_fields_from_model(self):
        output = raw_move_output(task_id="model-selected-task")

        with self.assertRaisesRegex(VlaOutputConversionError, "task_id"):
            convert_vla_output_to_proposal(
                output,
                move_task(),
                ROBOT,
                model_name="mock-vla",
                model_version="test-1",
            )

    def test_rejects_spoofed_model_metadata(self):
        output = raw_move_output(model_name="spoofed-vla")

        with self.assertRaisesRegex(VlaOutputConversionError, "model_name"):
            convert_vla_output_to_proposal(
                output,
                move_task(),
                ROBOT,
                model_name="trusted-vla-client",
                model_version="1.0",
            )

    def test_rejects_invalid_action_parameters(self):
        output = raw_move_output(parameters={"speed_limit": 0.3})

        with self.assertRaisesRegex(VlaOutputConversionError, "target_pose"):
            convert_vla_output_to_proposal(
                output,
                move_task(),
                ROBOT,
                model_name="mock-vla",
                model_version="test-1",
            )

    def test_rejects_output_that_changes_task_action(self):
        output = raw_move_output(
            action_type="MANIPULATE",
            parameters={
                "manipulation": "PICK",
                "object_id": "box-1",
                "target_pose": {"type": "ZONE", "zone_id": "A구역"},
            },
        )

        with self.assertRaisesRegex(VlaOutputConversionError, "action_type"):
            convert_vla_output_to_proposal(
                output,
                move_task(),
                ROBOT,
                model_name="mock-vla",
                model_version="test-1",
            )


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
