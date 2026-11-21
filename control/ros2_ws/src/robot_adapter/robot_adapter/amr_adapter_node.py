"""이동형 운반 로봇(AMR) 제어 어댑터 노드.

TIAGo 어댑터와 같은 토픽·규약(승인 Action, ACK·RESULT, E-Stop·HOLD)을 쓰고, 로봇마다 토픽에 <seg>를 붙인다.
<seg>는 robot_id의 토픽용 이름(amr-01 → amr_01).

    ros2 run robot_adapter amr_adapter --ros-args -p robot_id:=amr-01
    ros2 launch robot_adapter amrs.launch.py      # 공장 현장의 AMR 2대

MuJoCo 브리지(amrs:=true)와는 아래 토픽으로 연결된다.
    발행: /<seg>/cmd_vel (Twist), /<seg>/amr/load_command (String JSON: 싣기·내리기·CANCEL)
    구독: /<seg>/odom (Odometry), /<seg>/amr/load_result (String JSON)
"""
import json
import time

import rclpy
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from rclpy.executors import ExternalShutdownException
from std_msgs.msg import String

from safety_control.interfaces import topic_segment

from .amr_core import DEFAULT_MAX_SPEED, AmrCore
from .diff_drive_core import yaw_from_quat
from .mock_adapter_node import MockAdapterNode

HALTED = ("HOLD", "ESTOP", "ERROR")


class AmrAdapterNode(MockAdapterNode):
    def __init__(self):
        super().__init__("amr_adapter")
        seg = topic_segment(self.core.robot_id)
        self.cmd_pub = self.create_publisher(Twist, f"/{seg}/cmd_vel", 10)
        self.load_pub = self.create_publisher(String, f"/{seg}/amr/load_command", 10)
        self.create_subscription(Odometry, f"/{seg}/odom", self._on_odom, 10)
        self.create_subscription(String, f"/{seg}/amr/load_result", self._on_load_result, 10)
        self.was_moving = False

    def _make_core(self, robot_id):
        return AmrCore(
            robot_id,
            frame_id=self.declare_parameter("frame_id", "world").value,
            max_speed=float(self.declare_parameter("max_speed", DEFAULT_MAX_SPEED).value),
            max_angular=float(self.declare_parameter("max_angular", 0.8).value),
        )

    def _on_odom(self, msg: Odometry):
        p, q, t = msg.pose.pose.position, msg.pose.pose.orientation, msg.twist.twist
        self.core.update_odom(p.x, p.y, yaw_from_quat(q.x, q.y, q.z, q.w), t.linear.x, t.angular.z, time.time())

    def _on_load_result(self, msg: String):
        try:
            result = json.loads(msg.data)
        except json.JSONDecodeError:
            return
        self._emit(self.core.handle_load_result(result, time.time()))

    def _flush_load(self):
        while self.core.load_outbox:
            self.load_pub.publish(String(data=json.dumps(self.core.load_outbox.pop(0), ensure_ascii=False)))

    def _on_action(self, msg):
        super()._on_action(msg)
        self._flush_load()

    def _on_estop(self, msg):
        super()._on_estop(msg)
        if self.core.status in HALTED:
            self._send(0.0, 0.0)
        self._flush_load()  # 싣기·내리기 중이었으면 CANCEL

    def _tick(self):
        super()._tick()
        self._flush_load()
        if self.core.status == "MOVING" and self.core.mode == "move":
            self._send(*self.core.command)
            self.was_moving = True
        elif self.core.status in HALTED or self.was_moving:
            self._send(0.0, 0.0)
            self.was_moving = False

    def _send(self, linear, angular):
        cmd = Twist()
        cmd.linear.x, cmd.angular.z = float(linear), float(angular)
        self.cmd_pub.publish(cmd)


def main():
    rclpy.init()
    node = AmrAdapterNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
