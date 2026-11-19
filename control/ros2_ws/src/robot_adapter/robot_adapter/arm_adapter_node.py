"""로봇 팔 공통 제어 어댑터 노드 (이슈 #95).

같은 노드 하나를 robot_model 파라미터만 바꿔 여러 팔에 쓴다. 검증 서버 쪽 토픽·규약은 Mock·TIAGo 어댑터와 같다.

    ros2 run robot_adapter arm_adapter --ros-args -p robot_id:=arm-ur5e -p robot_model:=ur5e
    ros2 launch robot_adapter arms.launch.py      # 공장 현장의 팔 4대 (UR5e·UR10e·iiwa14·Panda)

robot_id가 control/mujoco/arms.PLANT_ARMS에 있으면 베이스 위치·방향을 거기서 가져온다. 다른 위치면
base_position·base_yaw 파라미터로 준다.

MuJoCo 브리지(arms:=true)와는 아래 토픽으로 연결된다. <seg>는 robot_id의 토픽용 이름(arm-ur5e → arm_ur5e).
    발행: /<seg>/arm/joint_command (JointState 위치 목표), /<seg>/arm/gripper (Bool, 닫힘)
    구독: /<seg>/joint_states (JointState, 실제 관절각)
"""
import os
import sys
import time

import rclpy
from rclpy.executors import ExternalShutdownException
from sensor_msgs.msg import JointState
from std_msgs.msg import Bool

from safety_control.interfaces import topic_segment

from .arm_core import DEFAULT_TCP_SPEED, ArmCore
from .mock_adapter_node import MockAdapterNode


def load_arms(mujoco_dir):
    if not os.path.isfile(os.path.join(mujoco_dir, "arms.py")):
        raise FileNotFoundError(f"arms.py 없음: {mujoco_dir} (mujoco_dir 파라미터 확인)")
    sys.path.insert(0, mujoco_dir)
    import arms
    return arms


class ArmAdapterNode(MockAdapterNode):
    def __init__(self):
        super().__init__("arm_adapter")
        seg = topic_segment(self.core.robot_id)
        self.joint_pub = self.create_publisher(JointState, f"/{seg}/arm/joint_command", 10)
        self.gripper_pub = self.create_publisher(Bool, f"/{seg}/arm/gripper", 10)
        self.create_subscription(JointState, f"/{seg}/joint_states", self._on_joint_states, 10)
        self._flush()

    def _make_core(self, robot_id):
        default_dir = os.path.join(os.environ.get("PIXI_PROJECT_ROOT", ""), "mujoco")
        arms = load_arms(self.declare_parameter("mujoco_dir", default_dir).value)
        layout = {a.robot_id: a for a in arms.PLANT_ARMS}.get(robot_id)
        model = self.declare_parameter("robot_model", layout.robot_model if layout else "").value
        if model not in arms.ARM_MODELS:
            raise ValueError(f"robot_model {model!r} (지원: {', '.join(sorted(arms.ARM_MODELS))})")
        if layout:
            default_base = [*layout.base_xy, layout.pedestal_top]
            default_yaw = arms._base_yaw(layout)
        else:
            default_base, default_yaw = [0.0, 0.0, 0.0], 0.0
        base = [float(v) for v in self.declare_parameter("base_position", default_base).value]
        yaw = float(self.declare_parameter("base_yaw", default_yaw).value)
        planner = arms.ArmPlanner(model, base, yaw)
        return ArmCore(
            robot_id, planner, model,
            frame_id=self.declare_parameter("frame_id", "world").value,
            max_speed=float(self.declare_parameter("max_speed", DEFAULT_TCP_SPEED).value),
            base_position=base,
        )

    def _on_joint_states(self, msg: JointState):
        self.core.update_joints(list(msg.position))

    def _flush(self):
        while self.core.outbox:
            kind, value = self.core.outbox.pop(0)
            if kind == "joints":
                js = JointState()
                js.header.stamp = self.get_clock().now().to_msg()
                js.name = list(self.core.planner.joint_names)
                js.position = [float(v) for v in value]
                self.joint_pub.publish(js)
            else:
                self.gripper_pub.publish(Bool(data=bool(value)))

    def _on_action(self, msg):
        super()._on_action(msg)
        self._flush()

    def _on_estop(self, msg):
        super()._on_estop(msg)
        self._flush()

    def _tick(self):
        super()._tick()
        self._flush()


def main():
    rclpy.init()
    node = ArmAdapterNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
