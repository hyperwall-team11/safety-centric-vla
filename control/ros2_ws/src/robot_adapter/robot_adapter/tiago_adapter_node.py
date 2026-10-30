"""TIAGo(MuJoCo) 제어 어댑터 노드.

Mock 어댑터와 같은 토픽·규약을 쓰므로 검증 서버·하네스 코드는 바꾸지 않아도 된다.
추가로 MuJoCo 브리지와 /odom(위치 입력), /cmd_vel(이동 명령)로 연결된다.

    ros2 run robot_adapter tiago_adapter --ros-args -p robot_id:=robot-01

정지 원칙: E-Stop·HOLD 상태에서는 매 틱 0 속도를 보낸다. 다른 노드가 /cmd_vel을 보내도
브리지가 0.5초 뒤 멈추지만, 어댑터가 정지를 계속 덮어써서 정지 상태를 유지한다.
"""
import time

import rclpy
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from rclpy.executors import ExternalShutdownException

from .diff_drive_core import DiffDriveCore, yaw_from_quat
from .mock_adapter_node import MockAdapterNode

HALTED = ("HOLD", "ESTOP", "ERROR")


class TiagoAdapterNode(MockAdapterNode):
    def __init__(self):
        super().__init__("tiago_adapter")
        self.cmd_pub = self.create_publisher(Twist, "/cmd_vel", 10)
        self.create_subscription(Odometry, "/odom", self._on_odom, 10)
        self.was_moving = False

    def _make_core(self, robot_id):
        return DiffDriveCore(
            robot_id,
            robot_type=self.declare_parameter("robot_type", "MOBILE").value,
            frame_id=self.declare_parameter("frame_id", "world").value,
            max_speed=float(self.declare_parameter("max_speed", 0.4).value),
            max_angular=float(self.declare_parameter("max_angular", 0.8).value),
        )

    def _on_odom(self, msg: Odometry):
        p, q, t = msg.pose.pose.position, msg.pose.pose.orientation, msg.twist.twist
        # 시뮬레이션에서는 odom 원점 = world 원점 (브리지가 world→odom 항등 TF를 발행)
        self.core.update_odom(p.x, p.y, yaw_from_quat(q.x, q.y, q.z, q.w),
                              t.linear.x, t.angular.z, time.time())

    def _on_estop(self, msg):
        super()._on_estop(msg)
        if self.core.status in HALTED:
            self._send(0.0, 0.0)  # 다음 틱을 기다리지 않고 바로 정지 명령

    def _tick(self):
        super()._tick()
        status = self.core.status
        if status == "MOVING":
            self._send(*self.core.command)
            self.was_moving = True
        elif status in HALTED or self.was_moving:
            # 정지 상태는 계속 0을 보내고, 이동이 끝난 직후에도 한 번 0을 보낸다
            self._send(0.0, 0.0)
            self.was_moving = False

    def _send(self, linear, angular):
        cmd = Twist()
        cmd.linear.x, cmd.angular.z = float(linear), float(angular)
        self.cmd_pub.publish(cmd)


def main():
    rclpy.init()
    node = TiagoAdapterNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
