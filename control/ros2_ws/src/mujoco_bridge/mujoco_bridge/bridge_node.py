"""ROS 2 ↔ MuJoCo 브리지: /cmd_vel(Twist)로 바퀴 로봇을 움직이고 상태를 /odom(Odometry)으로 보낸다.

    ros2 run mujoco_bridge bridge                          # TIAGo (기본)
    ros2 run mujoco_bridge bridge --ros-args -p robot:=simple   # 단순 시험용 바퀴 로봇
    mjpython <install>/lib/mujoco_bridge/bridge --ros-args -p viewer:=true   # 뷰어 (macOS)

시뮬레이션 모듈은 control/mujoco/simulation.py를 사용한다. 위치는 mujoco_dir 파라미터로 지정하며,
pixi 환경에서는 기본값($PIXI_PROJECT_ROOT/mujoco)을 그대로 쓰면 된다.
"""
import os
import sys
import time

import rclpy
from geometry_msgs.msg import TransformStamped, Twist
from nav_msgs.msg import Odometry
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from tf2_ros import TransformBroadcaster


def load_simulation(mujoco_dir: str):
    if not os.path.isfile(os.path.join(mujoco_dir, "simulation.py")):
        raise FileNotFoundError(f"simulation.py 없음: {mujoco_dir} (mujoco_dir 파라미터 확인)")
    sys.path.insert(0, mujoco_dir)
    import simulation
    return simulation


class MujocoBridge(Node):
    def __init__(self):
        super().__init__("mujoco_bridge")
        default_dir = os.path.join(os.environ.get("PIXI_PROJECT_ROOT", ""), "mujoco")
        mujoco_dir = self.declare_parameter("mujoco_dir", default_dir).value
        simulation = load_simulation(mujoco_dir)
        robot = self.declare_parameter("robot", simulation.DEFAULT_ROBOT).value
        model_path = self.declare_parameter("model_path", "").value or None  # 비우면 로봇 기본 장면
        self.rate_hz = self.declare_parameter("rate_hz", 50.0).value
        self.cmd_timeout = self.declare_parameter("cmd_timeout", 0.5).value
        self.odom_frame = self.declare_parameter("odom_frame", "odom").value
        self.base_frame = self.declare_parameter("base_frame", "base_link").value
        use_viewer = self.declare_parameter("viewer", False).value

        self.sim = simulation.DiffDriveSim(robot, model_path)
        self.last_cmd_at = None
        self.viewer = None
        if use_viewer:
            import mujoco.viewer
            self.viewer = mujoco.viewer.launch_passive(self.sim.model, self.sim.data)

        self.create_subscription(Twist, "/cmd_vel", self._on_cmd_vel, 10)
        self.odom_pub = self.create_publisher(Odometry, "/odom", 10)
        self.tf_pub = TransformBroadcaster(self)
        self.create_timer(1.0 / self.rate_hz, self._step)
        max_linear = self.sim.max_wheel_speed * self.sim.wheel_radius
        self.get_logger().info(
            f"MuJoCo bridge ready: {robot} ({model_path or self.sim.spec.model_path.name}, "
            f"wheel r={self.sim.wheel_radius} m, separation={self.sim.wheel_separation:.4f} m, "
            f"max linear {max_linear:.2f} m/s)")

    def _on_cmd_vel(self, msg: Twist):
        self.sim.set_velocity(msg.linear.x, msg.angular.z)
        self.last_cmd_at = time.monotonic()

    def _step(self):
        # 명령이 끊기면 정지 (일반적인 cmd_vel 안전 규칙)
        if self.last_cmd_at is not None and time.monotonic() - self.last_cmd_at > self.cmd_timeout:
            self.sim.stop()
            self.last_cmd_at = None
        self.sim.step(1.0 / self.rate_hz)
        if self.viewer is not None:
            if not self.viewer.is_running():
                raise SystemExit
            self.viewer.sync()
        self._publish_odom()

    def _publish_odom(self):
        x, y, _ = self.sim.pose()
        qx, qy, qz, qw = self.sim.orientation()
        v, w = self.sim.twist()
        stamp = self.get_clock().now().to_msg()

        odom = Odometry()
        odom.header.stamp = stamp
        odom.header.frame_id = self.odom_frame
        odom.child_frame_id = self.base_frame
        odom.pose.pose.position.x, odom.pose.pose.position.y = x, y
        o = odom.pose.pose.orientation
        o.x, o.y, o.z, o.w = qx, qy, qz, qw
        odom.twist.twist.linear.x = v
        odom.twist.twist.angular.z = w
        self.odom_pub.publish(odom)

        tf = TransformStamped()
        tf.header.stamp = stamp
        tf.header.frame_id = self.odom_frame
        tf.child_frame_id = self.base_frame
        tf.transform.translation.x, tf.transform.translation.y = x, y
        r = tf.transform.rotation
        r.x, r.y, r.z, r.w = qx, qy, qz, qw
        self.tf_pub.sendTransform(tf)


def main():
    rclpy.init()
    node = MujocoBridge()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException, SystemExit):
        pass
    finally:
        if node.viewer is not None:
            node.viewer.close()
        node.destroy_node()
        rclpy.try_shutdown()
