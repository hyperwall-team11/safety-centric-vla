"""팔 4대가 상자를 계속 옮기는 데모 (이슈 #95). 검증 서버 대역 시험 도구다.

검증 서버(Backend)가 보낼 승인 명령과 하트비트를 대신 보내므로 데모·시험 전용이다. 실제 운영에서는
Backend가 온톨로지 검증을 통과한 MANIPULATE만 /safety/approved_action으로 보낸다(AGENTS.md 1장 원칙).

    ros2 run robot_adapter arm_cycle_demo

로봇마다 PICK(현재 상자 위치) → PLACE(반대쪽 작업대) → 다음 회차는 반대 방향을 반복한다.
E-Stop·HOLD로 실패하면 그 로봇은 쉬었다가 같은 단계를 다시 보낸다.
"""
import json
import os
import sys
import time
import uuid

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from std_msgs.msg import String

from safety_control.gate import utc_iso
from safety_control.interfaces import (APPROVED_ACTION_TOPIC, HEARTBEAT_QOS, HEARTBEAT_TOPIC, RELIABLE,
                                      result_topic)

HEARTBEAT_HZ = 5
RETRY_S = 3.0
PAUSE_S = 1.0  # 동작 사이 쉬는 시간


class ArmCycleDemo(Node):
    def __init__(self):
        super().__init__("arm_cycle_demo")
        default_dir = os.path.join(os.environ.get("PIXI_PROJECT_ROOT", ""), "mujoco")
        sys.path.insert(0, self.declare_parameter("mujoco_dir", default_dir).value)
        import arms
        self.speed = float(self.declare_parameter("speed_limit", 0.25).value)
        self.action_pub = self.create_publisher(String, APPROVED_ACTION_TOPIC, RELIABLE)
        self.heartbeat_pub = self.create_publisher(String, HEARTBEAT_TOPIC, HEARTBEAT_QOS)
        self.robots = {}
        for arm in arms.PLANT_ARMS:
            self.robots[arm.robot_id] = {
                "spots": [list(arm.pick), list(arm.place)], "box_at": 0, "step": "PICK",
                "pending": None, "next_at": time.time() + 2.0, "object_id": f"box:{arm.robot_id}"}
            self.create_subscription(String, result_topic(arm.robot_id),
                                     lambda msg, r=arm.robot_id: self._on_result(r, msg), RELIABLE)
        self.create_timer(1.0 / HEARTBEAT_HZ, self._heartbeat)
        self.create_timer(0.2, self._dispatch)
        self.get_logger().info(f"arm cycle demo: {', '.join(self.robots)}")

    def _heartbeat(self):
        self.heartbeat_pub.publish(String(data=json.dumps({"timestamp": utc_iso(time.time())})))

    def _dispatch(self):
        now = time.time()
        for rid, r in self.robots.items():
            if r["pending"] or now < r["next_at"]:
                continue
            spot = r["spots"][r["box_at"]] if r["step"] == "PICK" else r["spots"][1 - r["box_at"]]
            pid = f"demo-{uuid.uuid4().hex[:8]}"
            msg = {"proposal_id": pid, "task_id": "task-arm-cycle-demo", "robot_id": rid,
                   "action_type": "MANIPULATE",
                   "parameters": {"manipulation": r["step"], "object_id": r["object_id"],
                                  "target_pose": {"frame_id": "world", "position": spot}},
                   "trajectory": None, "frame_id": "world", "speed_limit": self.speed,
                   "model_name": "arm-cycle-demo", "model_version": "0", "status": "APPROVED",
                   "proposed_at": utc_iso(now)}
            r["pending"] = pid
            self.action_pub.publish(String(data=json.dumps(msg)))

    def _on_result(self, rid, msg):
        event = json.loads(msg.data)
        r = self.robots[rid]
        if event.get("kind") != "RESULT" or event.get("proposal_id") != r["pending"]:
            return
        r["pending"] = None
        if not event.get("success"):
            self.get_logger().warning(f"{rid} {r['step']} 실패: {event.get('reason_code')} {event.get('message')}")
            r["next_at"] = time.time() + RETRY_S
            return
        self.get_logger().info(f"{rid} {r['step']} 완료")
        if r["step"] == "PICK":
            r["step"] = "PLACE"
        else:
            r["step"], r["box_at"] = "PICK", 1 - r["box_at"]
        r["next_at"] = time.time() + PAUSE_S


def main():
    rclpy.init()
    node = ArmCycleDemo()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
