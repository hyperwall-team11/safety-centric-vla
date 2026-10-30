"""검증 서버 대역으로 어댑터 왕복·적합성 시험을 실행한다 (5~6주 차 DoD: MOVE_TO·HOLD 왕복 데모).

    ros2 run safety_control test_harness --ros-args -p robot_id:=robot-01
"""
import json
import threading
import time
import uuid

import rclpy
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import String

from .gate import utc_iso
from .interfaces import (APPROVED_ACTION_TOPIC, ESTOP_QOS, ESTOP_TOPIC, HEARTBEAT_QOS,
                         HEARTBEAT_TOPIC, RELIABLE, result_topic, state_topic)

HEARTBEAT_HZ = 5
ESTOP_LATENCY_LIMIT_MS = 100  # KPI 2


class Harness(Node):
    def __init__(self):
        super().__init__("control_test_harness")
        self.robot_id = self.declare_parameter("robot_id", "robot-01").value
        self.action_pub = self.create_publisher(String, APPROVED_ACTION_TOPIC, RELIABLE)
        self.estop_pub = self.create_publisher(String, ESTOP_TOPIC, ESTOP_QOS)
        self.heartbeat_pub = self.create_publisher(String, HEARTBEAT_TOPIC, HEARTBEAT_QOS)
        self.events = []  # 어댑터 보고 전체 이력 (대기 중 다른 이벤트를 버리지 않기 위해)
        self.cursor = 0
        self.lock = threading.Lock()
        self.state = None
        self.heartbeat_on = False
        self.create_subscription(String, result_topic(self.robot_id),
                                 self._on_result, RELIABLE)
        self.create_subscription(String, state_topic(self.robot_id), self._on_state, RELIABLE)
        self.create_timer(1.0 / HEARTBEAT_HZ, self._heartbeat)

    def _on_result(self, msg):
        with self.lock:
            self.events.append(json.loads(msg.data))

    def _on_state(self, msg):
        self.state = json.loads(msg.data)

    def _heartbeat(self):
        if self.heartbeat_on:
            self.heartbeat_pub.publish(String(data=json.dumps({"timestamp": utc_iso(time.time())})))

    def send(self, action_type, parameters=None, status="APPROVED", speed_limit=0.4):
        pid = f"prop-{uuid.uuid4().hex[:8]}"
        msg = {"proposal_id": pid, "task_id": "task-harness", "robot_id": self.robot_id,
               "action_type": action_type, "parameters": parameters or {}, "trajectory": None,
               "frame_id": "world", "speed_limit": speed_limit, "model_name": "harness",
               "model_version": "0", "status": status, "proposed_at": utc_iso(time.time())}
        self.action_pub.publish(String(data=json.dumps(msg)))
        return pid, time.time()

    def estop(self, active, triggered_by="OPERATOR"):
        self.estop_pub.publish(String(data=json.dumps(
            {"active": active, "triggered_by": triggered_by, "issued_at": utc_iso(time.time())})))

    def wait_for(self, predicate, timeout):
        """마지막 drain() 이후 수신한 이벤트 중 조건에 맞는 첫 이벤트를 기다린다."""
        deadline = time.time() + timeout
        while True:
            with self.lock:
                found = next((e for e in self.events[self.cursor:] if predicate(e)), None)
            if found or time.time() >= deadline:
                return found
            time.sleep(0.005)

    def wait_status(self, status, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.state and self.state["status"] == status:
                return True
            time.sleep(0.02)
        return False

    def drain(self):
        with self.lock:
            self.cursor = len(self.events)


def run_checks(h: Harness):
    checks = []

    def check(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    def result_of(pid, timeout):
        # 같은 제안의 RESULT는 하나뿐이므로 drain 이후 이력 전체에서 찾는다
        return h.wait_for(lambda e: e["kind"] == "RESULT" and e["proposal_id"] == pid, timeout)

    if not h.wait_status("IDLE", 10):
        check("어댑터 연결", False, "state 토픽 수신 없음 — mock_adapter 실행 여부 확인")
        return checks
    check("어댑터 연결", True, f"robot_id={h.robot_id}")

    h.estop(False)
    h.heartbeat_on = True
    time.sleep(0.5)
    h.drain()

    pid, _ = h.send("MOVE_TO", {"target_pose": {"position": [1, 0, 0]}}, status="PROPOSED")
    r = result_of(pid, 2)
    check("미승인 제안 거부", r and r["reason_code"] == "NOT_APPROVED" and h.state["status"] == "IDLE",
          r and r["reason_code"])

    pid, sent = h.send("MOVE_TO", {"target_pose": {"position": [0.4, 0, 0]}})
    ack = h.wait_for(lambda e: e["kind"] == "ACK" and e["proposal_id"] == pid, 2)
    check("MOVE_TO 즉시 ACK", ack, ack and f"{(time.time() - sent) * 1000:.1f} ms 이내")
    r = result_of(pid, 5)
    check("MOVE_TO 완료 보고", r and r["success"], r and r["message"])

    pid, _ = h.send("MOVE_TO", {"target_pose": {"position": [0, 0, 0]}})
    h.wait_status("MOVING", 2)
    time.sleep(0.3)
    h.estop(True, "OPERATOR")
    e = h.wait_for(lambda e: e["kind"] == "ESTOP", 2)
    latency = e and e.get("latency_ms")
    check("E-Stop 정지", e and h.wait_status("ESTOP", 1), e and f"triggered_by={e['triggered_by']}")
    check(f"E-Stop 지연 ≤ {ESTOP_LATENCY_LIMIT_MS} ms", latency is not None and latency <= ESTOP_LATENCY_LIMIT_MS,
          f"{latency} ms")
    r = result_of(pid, 1)
    check("실행 중 제안 중단 보고", r and r["reason_code"] == "ESTOP", r and r["reason_code"])

    pid, _ = h.send("MOVE_TO", {"target_pose": {"position": [1, 0, 0]}})
    r = result_of(pid, 2)
    check("ESTOP 중 이동 거부", r and r["reason_code"] == "ROBOT_HELD", r and r["reason_code"])

    pid, _ = h.send("RESUME", {"task_id": "task-harness"})
    r = result_of(pid, 2)
    check("E-Stop 해제 전 RESUME 거부", r and not r["success"], r and r["reason_code"])

    h.estop(False)
    time.sleep(0.2)
    pid, _ = h.send("RESUME", {"task_id": "task-harness"})
    r = result_of(pid, 2)
    check("해제 후 RESUME 복귀", r and r["success"] and h.wait_status("IDLE", 1), r and r["message"])

    # 어떤 어댑터도 지원하지 않는 조작 종류 → UNSUPPORTED (Mock은 MANIPULATE 자체를 지원하지 않음)
    pid, _ = h.send("MANIPULATE", {"manipulation": "POUR", "object_id": "box-01"})
    r = result_of(pid, 2)
    check("미지원 Action UNSUPPORTED 응답", r and r["reason_code"] == "UNSUPPORTED", r and r["reason_code"])

    pid, _ = h.send("MOVE_TO", {"target_pose": {"position": [0.4, 0.4, 0]}}, speed_limit=0.1)
    h.wait_status("MOVING", 2)
    lost_at = time.time()
    h.heartbeat_on = False
    e = h.wait_for(lambda e: e["kind"] == "HOLD", 3)
    elapsed = time.time() - lost_at
    check("하트비트 손실 시 자율 HOLD", e and e["triggered_by"] == "HEARTBEAT_LOSS",
          f"마지막 하트비트 후 {elapsed:.2f} s")

    h.heartbeat_on = True
    time.sleep(0.5)
    pid, _ = h.send("RESUME", {"task_id": "task-harness"})
    r = result_of(pid, 2)
    check("하트비트 복구 후 RESUME", r and r["success"], r and r["message"])
    return checks


def main():
    rclpy.init()
    harness = Harness()
    executor = MultiThreadedExecutor()
    executor.add_node(harness)
    spinner = threading.Thread(target=executor.spin, daemon=True)
    spinner.start()
    try:
        checks = run_checks(harness)
    finally:
        harness.heartbeat_on = False
        executor.shutdown()
        harness.destroy_node()
        rclpy.try_shutdown()

    width = max(len(name) for name, _, _ in checks)
    for name, ok, detail in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name.ljust(width)}  {detail or ''}")
    passed = sum(ok for _, ok, _ in checks)
    print(f"\n{passed}/{len(checks)} 통과")
    raise SystemExit(0 if passed == len(checks) else 1)
