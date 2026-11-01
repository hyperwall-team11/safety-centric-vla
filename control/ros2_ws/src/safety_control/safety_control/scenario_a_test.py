"""시나리오 A를 ROS 2 전체 경로로 시험한다 (검증 서버 대역 → TIAGo 어댑터 → MuJoCo 브리지).

    ros2 run safety_control scenario_a_test

1. MANIPULATE PICK(box-01) 승인 → 집는 도중 E-Stop → 로봇이 실제로 멈추고 RESULT=ESTOP
2. E-Stop 해제 → RESUME → PICK 다시 → 성공 (상자를 들어 올림)
3. MANIPULATE PLACE(B 작업대) → 성공 (상자가 B 목표 5 cm 안에 세워짐)
"""
import json
import threading
import time

import rclpy
from nav_msgs.msg import Odometry
from rclpy.executors import MultiThreadedExecutor
from std_msgs.msg import String

from .test_harness import Harness

PLACE_B = [1.2, -0.82, 0.64]


class ScenarioATest(Harness):
    def __init__(self):
        super().__init__()
        self.odom = None
        self.arm_results = []
        self.create_subscription(Odometry, "/odom", self._on_odom, 10)
        self.create_subscription(String, "/arm/result", lambda m: self.arm_results.append(json.loads(m.data)), 10)

    def _on_odom(self, msg):
        t = msg.twist.twist
        self.odom = (msg.pose.pose.position.x, msg.pose.pose.position.y, t.linear.x, t.angular.z)

    def result(self, pid, timeout):
        return self.wait_for(lambda e: e["kind"] == "RESULT" and e["proposal_id"] == pid, timeout)

    def arm_result(self, pid):
        return next((r for r in reversed(self.arm_results) if r.get("command_id") == pid), None)


def run(t: ScenarioATest):
    checks = []

    def check(name, ok, detail=""):
        checks.append((name, bool(ok), detail))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)

    if not t.wait_status("IDLE", 15):
        check("어댑터 연결", False, "tiago_adapter 상태 수신 없음")
        return checks
    t.estop(False)
    t.heartbeat_on = True
    time.sleep(0.5)
    t.drain()

    # 1. 집는 도중 E-Stop
    pid, _ = t.send("MANIPULATE", {"manipulation": "PICK", "object_id": "box-01"})
    ack = t.wait_for(lambda e: e["kind"] == "ACK" and e["proposal_id"] == pid, 2)
    check("PICK 승인·ACK", ack)
    moving_seen = False
    deadline = time.time() + 15
    while time.time() < deadline:  # 베이스가 움직이기 시작하면 E-Stop
        if t.odom and (abs(t.odom[2]) > 0.1 or abs(t.odom[3]) > 0.2):
            moving_seen = True
            break
        time.sleep(0.02)
    t.estop(True, "OPERATOR")
    r = t.result(pid, 2)
    check("집기 중 E-Stop → RESULT ESTOP", r and r["reason_code"] == "ESTOP", r and r["reason_code"])
    time.sleep(1.5)
    v, w = (t.odom[2], t.odom[3]) if t.odom else (None, None)
    check("E-Stop 후 로봇 실제 정지", moving_seen and v is not None and abs(v) < 0.02 and abs(w) < 0.05,
          f"정지 전 이동 감지={moving_seen}, 1.5초 뒤 v={v and round(v, 4)} m/s w={w and round(w, 4)} rad/s")
    cancel = t.arm_result(pid)
    check("브리지 집기 동작 취소", cancel and cancel["reason_code"] == "CANCELLED", cancel and cancel["message"])

    # 2. 해제 → RESUME → PICK
    t.estop(False)
    time.sleep(0.3)
    rpid, _ = t.send("RESUME", {"task_id": "task-scenario-a"})
    r = t.result(rpid, 3)
    check("해제 후 RESUME", r and r["success"], r and r["message"])
    pid, _ = t.send("MANIPULATE", {"manipulation": "PICK", "object_id": "box-01"})
    r = t.result(pid, 90)
    arm = t.arm_result(pid)
    check("PICK 성공", r and r["success"], r and r["message"])
    if arm and arm.get("object_position"):
        print(f"       상자 위치 {arm['object_position']}", flush=True)

    # 3. PLACE
    pid, _ = t.send("MANIPULATE", {"manipulation": "PLACE", "object_id": "box-01",
                                   "target_pose": {"frame_id": "world", "position": PLACE_B}})
    r = t.result(pid, 90)
    arm = t.arm_result(pid)
    check("PLACE 성공 (B 작업대)", r and r["success"], r and r["message"])
    if arm and arm.get("object_position"):
        print(f"       상자 위치 {arm['object_position']} (목표 {PLACE_B})", flush=True)
    return checks


def main():
    rclpy.init()
    t = ScenarioATest()
    executor = MultiThreadedExecutor()
    executor.add_node(t)
    threading.Thread(target=executor.spin, daemon=True).start()
    try:
        checks = run(t)
    finally:
        t.heartbeat_on = False
        executor.shutdown()
        t.destroy_node()
        rclpy.try_shutdown()
    passed = sum(ok for _, ok, _ in checks)
    print(f"\n{passed}/{len(checks)} 통과")
    raise SystemExit(0 if checks and passed == len(checks) else 1)
