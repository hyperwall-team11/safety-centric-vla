"""/odom을 한 번 받아 x, y, yaw, 속도를 한 줄로 출력한다.  ros2 run mujoco_bridge odom_echo"""
import math

import rclpy
from nav_msgs.msg import Odometry


def main():
    rclpy.init()
    node = rclpy.create_node("odom_echo")
    received = []
    node.create_subscription(Odometry, "/odom", received.append, 10)
    try:
        while rclpy.ok() and not received:
            rclpy.spin_once(node, timeout_sec=0.1)
    except KeyboardInterrupt:
        return
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
    msg = received[0]
    p, q, t = msg.pose.pose.position, msg.pose.pose.orientation, msg.twist.twist
    yaw = math.degrees(math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y ** 2 + q.z ** 2)))
    print(f"x={p.x:.3f} m  y={p.y:.3f} m  yaw={yaw:.1f}°  v={t.linear.x:.3f} m/s  w={t.angular.z:.3f} rad/s")
