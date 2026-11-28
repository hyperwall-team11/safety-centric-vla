"""Verify /arm/command EE_DELTA -> MuJoCo joints -> /arm/result."""
import json
import time

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import JointState
from std_msgs.msg import String


COMMAND_ID = "tiago-ee-delta-e2e"


class EeDeltaProbe(Node):
    def __init__(self):
        super().__init__("tiago_ee_delta_test")
        self.publisher = self.create_publisher(String, "/arm/command", 10)
        self.create_subscription(String, "/arm/result", self._on_result, 10)
        self.create_subscription(JointState, "/joint_states", self._on_joints, 10)
        self.initial_joints = None
        self.latest_joints = None
        self.result = None

    def _on_result(self, msg):
        try:
            result = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        if result.get("command_id") == COMMAND_ID:
            self.result = result

    def _on_joints(self, msg):
        by_name = dict(zip(msg.name, msg.position))
        arm = tuple(by_name.get(f"arm_{index}_joint") for index in range(1, 8))
        if any(value is None for value in arm):
            return
        self.latest_joints = arm
        if self.initial_joints is None:
            self.initial_joints = arm


def main():
    rclpy.init()
    node = EeDeltaProbe()
    try:
        ready_deadline = time.monotonic() + 10
        while time.monotonic() < ready_deadline and (
            node.initial_joints is None or node.publisher.get_subscription_count() == 0
        ):
            rclpy.spin_once(node, timeout_sec=0.1)
        if node.initial_joints is None or node.publisher.get_subscription_count() == 0:
            raise RuntimeError("MuJoCo 브리지의 팔 토픽이 준비되지 않음")

        command = {
            "command_id": COMMAND_ID,
            "manipulation": "PICK",
            "object_id": "box-01",
            "frame_id": "world",
            "ee_delta": {
                "translation": {"x": 0.005, "y": 0.0, "z": 0.0},
                "rotation_rpy": {"x": 0.0, "y": 0.0, "z": 0.0},
                "gripper": 0.0,
            },
        }
        node.publisher.publish(String(data=json.dumps(command)))

        result_deadline = time.monotonic() + 15
        while time.monotonic() < result_deadline and node.result is None:
            rclpy.spin_once(node, timeout_sec=0.1)
        if node.result is None:
            raise RuntimeError("EE_DELTA /arm/result 시간 초과")
        if not node.result.get("success"):
            raise RuntimeError(f"EE_DELTA 실패: {node.result}")

        moved = max(
            abs(after - before)
            for before, after in zip(node.initial_joints, node.latest_joints)
        )
        if moved <= 1e-4:
            raise RuntimeError(f"팔 관절이 움직이지 않음: max_delta={moved}")
        print(f"[PASS] EE_DELTA ROS 2 E2E: result=success, max_joint_delta={moved:.6f} rad")
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
