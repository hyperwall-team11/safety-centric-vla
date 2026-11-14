"""AMR 2대가 창고 ↔ TIAGo 작업 셀 사이로 팔레트를 계속 나르는 데모. 검증 서버 대역 시험 도구다.

검증 서버(Backend)가 보낼 승인 명령과 하트비트를 대신 보내므로 데모·시험 전용이다. 실제 운영에서는
Backend가 온톨로지 검증을 통과한 MOVE_TO·MANIPULATE만 /safety/approved_action으로 보낸다(AGENTS.md 1장 원칙).

    ros2 run robot_adapter amr_cycle_demo

로봇마다 PICK(팔레트가 있는 거치대) → MOVE_TO(반대쪽 정차 위치) → PLACE → 다음 회차는 반대 방향을 반복한다.
E-Stop·HOLD 등으로 실패하면 그 로봇은 쉬었다가 같은 단계를 다시 보낸다.
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
PAUSE_S = 1.0
STEPS = ("PICK", "MOVE_TO", "PLACE")


def cycle_step(amr, at_warehouse: bool, step: str):
    """(action_type, parameters). at_warehouse: 팔레트·로봇이 지금 창고 쪽에 있는가."""
    here_stand = amr.warehouse_stand if at_warehouse else amr.cell_stand
    there_stand = amr.cell_stand if at_warehouse else amr.warehouse_stand
    there_dock = amr.cell_dock if at_warehouse else amr.warehouse_dock
    if step == "PICK":
        return "MANIPULATE", {"manipulation": "PICK", "object_id": amr.pallet_id,
                              "target_pose": {"frame_id": "world", "position": list(amr.place_pose(here_stand))}}
    if step == "MOVE_TO":
        return "MOVE_TO", {"target_pose": {"frame_id": "world", "position": [there_dock[0], there_dock[1], 0.0]}}
    return "MANIPULATE", {"manipulation": "PLACE", "object_id": amr.pallet_id,
                          "target_pose": {"frame_id": "world", "position": list(amr.place_pose(there_stand))}}


class AmrCycleDemo(Node):
    def __init__(self):
        super().__init__("amr_cycle_demo")
        default_dir = os.path.join(os.environ.get("PIXI_PROJECT_ROOT", ""), "mujoco")
        sys.path.insert(0, self.declare_parameter("mujoco_dir", default_dir).value)
        import amr
        self.speed = float(self.declare_parameter("speed_limit", 0.4).value)
        self.action_pub = self.create_publisher(String, APPROVED_ACTION_TOPIC, RELIABLE)
        self.heartbeat_pub = self.create_publisher(String, HEARTBEAT_TOPIC, HEARTBEAT_QOS)
        self.robots = {}
        for i, a in enumerate(amr.PLANT_AMRS):
            self.robots[a.robot_id] = {"layout": a, "at_warehouse": True, "step": 0, "pending": None,
                                       "next_at": time.time() + 2.0 + 4.0 * i}  # 2대가 엇갈려 움직이게
            self.create_subscription(String, result_topic(a.robot_id),
                                     lambda msg, r=a.robot_id: self._on_result(r, msg), RELIABLE)
        self.create_timer(1.0 / HEARTBEAT_HZ, self._heartbeat)
        self.create_timer(0.2, self._dispatch)
        self.get_logger().info(f"amr cycle demo: {', '.join(self.robots)}")

    def _heartbeat(self):
        self.heartbeat_pub.publish(String(data=json.dumps({"timestamp": utc_iso(time.time())})))

    def _dispatch(self):
        now = time.time()
        for rid, r in self.robots.items():
            if r["pending"] or now < r["next_at"]:
                continue
            action_type, params = cycle_step(r["layout"], r["at_warehouse"], STEPS[r["step"]])
            pid = f"demo-{uuid.uuid4().hex[:8]}"
            msg = {"proposal_id": pid, "task_id": "task-amr-cycle-demo", "robot_id": rid,
                   "action_type": action_type, "parameters": params, "trajectory": None, "frame_id": "world",
                   "speed_limit": self.speed, "model_name": "amr-cycle-demo", "model_version": "0",
                   "status": "APPROVED", "proposed_at": utc_iso(now)}
            r["pending"] = pid
            self.action_pub.publish(String(data=json.dumps(msg)))

    def _on_result(self, rid, msg):
        event = json.loads(msg.data)
        r = self.robots[rid]
        if event.get("kind") != "RESULT" or event.get("proposal_id") != r["pending"]:
            return
        r["pending"] = None
        step = STEPS[r["step"]]
        if not event.get("success"):
            self.get_logger().warning(f"{rid} {step} 실패: {event.get('reason_code')} {event.get('message')}")
            r["next_at"] = time.time() + RETRY_S
            return
        self.get_logger().info(f"{rid} {step} 완료")
        r["step"] = (r["step"] + 1) % len(STEPS)
        if step == "PLACE":
            r["at_warehouse"] = not r["at_warehouse"]
        r["next_at"] = time.time() + PAUSE_S


def main():
    rclpy.init()
    node = AmrCycleDemo()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
