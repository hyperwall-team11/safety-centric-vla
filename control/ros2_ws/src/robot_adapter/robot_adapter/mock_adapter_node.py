"""Mock 제어 어댑터 노드. 실제 로봇·시뮬레이터 없이 공통 Action 규약을 검증한다."""
import json
import time

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from std_msgs.msg import String

from safety_control.interfaces import (APPROVED_ACTION_TOPIC, ESTOP_QOS, ESTOP_TOPIC, HEARTBEAT_QOS,
                                      HEARTBEAT_TOPIC, RELIABLE, result_topic, state_topic)

from .core import MockRobotCore

TICK_HZ = 50
STATE_HZ = 10


class MockAdapterNode(Node):
    def __init__(self, node_name="mock_adapter"):
        super().__init__(node_name)
        robot_id = self.declare_parameter("robot_id", "robot-01").value
        self.core = self._make_core(robot_id)
        self.result_pub = self.create_publisher(String, result_topic(robot_id), RELIABLE)
        self.state_pub = self.create_publisher(String, state_topic(robot_id), RELIABLE)
        self.create_subscription(String, APPROVED_ACTION_TOPIC, self._on_action, RELIABLE)
        self.create_subscription(String, ESTOP_TOPIC, self._on_estop, ESTOP_QOS)
        self.create_subscription(String, HEARTBEAT_TOPIC, self._on_heartbeat, HEARTBEAT_QOS)
        self.create_timer(1.0 / TICK_HZ, self._tick)
        self.create_timer(1.0 / STATE_HZ, self._publish_state)
        self.get_logger().info(f"{self.core.adapter_name} adapter ready: {robot_id} ({self.core.robot_type})")

    def _make_core(self, robot_id):
        return MockRobotCore(
            robot_id,
            robot_type=self.declare_parameter("robot_type", "QUADRUPED").value,
            frame_id=self.declare_parameter("frame_id", "world").value,
            max_speed=self.declare_parameter("max_speed", 0.4).value,
        )

    def _on_action(self, msg):
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            self.get_logger().warning("JSON이 아닌 Action 무시")
            return
        self._emit(self.core.handle_action(payload, time.time()))

    def _on_estop(self, msg):
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            payload = {"active": True, "triggered_by": "RULE_ENGINE"}  # 해석 불가 시 정지 (Fail-Closed)
        self._emit(self.core.handle_estop(payload, time.time()))
        self._publish_state()

    def _on_heartbeat(self, _msg):
        self.core.handle_heartbeat(time.time())

    def _tick(self):
        self._emit(self.core.tick(time.time(), 1.0 / TICK_HZ))

    def _publish_state(self):
        self.state_pub.publish(String(data=json.dumps(self.core.state(time.time()), ensure_ascii=False)))

    def _emit(self, events):
        for event in events:
            self.result_pub.publish(String(data=json.dumps(event, ensure_ascii=False)))
            if event["kind"] in ("ESTOP", "HOLD"):
                self.get_logger().warning(f"{event['kind']} by {event['triggered_by']} "
                                          f"(latency {event.get('latency_ms')} ms)")


def main():
    rclpy.init()
    node = MockAdapterNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
