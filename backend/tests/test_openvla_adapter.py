import unittest

from app.models import ManipulationType, Robot, TaskOrder
from app.openvla_adapter import convert_openvla_action_7d
from app.safety import evaluate_proposal, default_rules
from app.vla_output import VlaOutputConversionError


ROBOT = Robot.model_validate({
    "robot_id": "robot-01",
    "robot_type": "MOBILE",
    "control_adapter": "mujoco_diff_drive",
    "status": "IDLE",
    "pose": {
        "frame_id": "world",
        "position": {"x": 0, "y": 0, "z": 0},
        "orientation": {"x": 0, "y": 0, "z": 0, "w": 1},
    },
    "velocity": {"x": 0, "y": 0, "z": 0},
    "motion_limits": {"max_speed": 0.4, "max_acceleration": 0.5},
})


def transport_task(*, object_id="box-red") -> TaskOrder:
    return TaskOrder.model_validate({
        "task_id": "task-box",
        "instruction": "robot-01이 빨간 상자를 B구역으로 옮겨줘",
        "target_robot_id": "robot-01",
        "target_object_id": object_id,
        "destination": {"type": "ZONE", "zone_id": "B구역"},
    })


class OpenVlaAdapterTest(unittest.TestCase):
    def test_maps_all_seven_values_to_typed_ee_delta(self):
        proposal = convert_openvla_action_7d(
            [0.01, -0.02, 0.03, 0.04, -0.05, 0.06, 1.0],
            transport_task(),
            ROBOT,
            manipulation=ManipulationType.PICK,
            speed_limit=0.2,
            model_version="7b-test",
        )

        self.assertEqual(proposal.action_type, "MANIPULATE")
        self.assertEqual(proposal.parameters["object_id"], "box-red")
        self.assertEqual(proposal.parameters["target_pose"], {"type": "ZONE", "zone_id": "B구역"})
        self.assertEqual(proposal.parameters["ee_delta"], {
            "translation": {"x": 0.01, "y": -0.02, "z": 0.03},
            "rotation_rpy": {"x": 0.04, "y": -0.05, "z": 0.06},
            "gripper": 1.0,
            "phase_complete": False,
        })
        self.assertEqual(proposal.frame_id, "world")

    def test_rejects_wrong_dimension_non_finite_and_boolean(self):
        for action in (
            [0.0] * 6,
            [0.0] * 6 + [float("nan")],
            [0.0] * 6 + [True],
        ):
            with self.subTest(action=action):
                with self.assertRaisesRegex(VlaOutputConversionError, "7개"):
                    convert_openvla_action_7d(
                        action,
                        transport_task(),
                        ROBOT,
                        manipulation=ManipulationType.PICK,
                        speed_limit=0.2,
                    )

    def test_rejects_gripper_outside_normalized_range(self):
        with self.assertRaisesRegex(VlaOutputConversionError, "출력 구조"):
            convert_openvla_action_7d(
                [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.1],
                transport_task(),
                ROBOT,
                manipulation=ManipulationType.PICK,
                speed_limit=0.2,
            )

    def test_requires_trusted_target_object(self):
        with self.assertRaisesRegex(VlaOutputConversionError, "target_object_id"):
            convert_openvla_action_7d(
                [0.0] * 7,
                transport_task(object_id=None),
                ROBOT,
                manipulation=ManipulationType.PICK,
                speed_limit=0.2,
            )

    def test_safety_accepts_small_step_and_rejects_large_step(self):
        task = transport_task()
        small = convert_openvla_action_7d(
            [0.01, -0.02, 0.03, 0.1, 0.0, -0.2, 0.5],
            task,
            ROBOT,
            manipulation=ManipulationType.PICK,
            speed_limit=0.2,
        )
        large = convert_openvla_action_7d(
            [0.051, 0.0, 0.0, 0.0, 0.0, 0.251, 0.5],
            task,
            ROBOT,
            manipulation=ManipulationType.PICK,
            speed_limit=0.2,
        )

        small_results, _, _ = evaluate_proposal(small, task, ROBOT, [], default_rules(), [])
        large_results, _, _ = evaluate_proposal(large, task, ROBOT, [], default_rules(), [])
        small_bound = next(r for r in small_results if r.rule_id == "precondition-ee-delta-bounds")
        large_bound = next(r for r in large_results if r.rule_id == "precondition-ee-delta-bounds")

        self.assertTrue(small_bound.passed)
        self.assertFalse(large_bound.passed)
        self.assertIn("한계 초과", large_bound.reason)


if __name__ == "__main__":
    unittest.main()
