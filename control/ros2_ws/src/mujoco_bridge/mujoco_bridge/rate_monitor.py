"""브리지 토픽의 실제 발행 주기를 측정해 평균·p95(Hz)를 출력한다 (KPI 1: 위험 인식 주기 p95 ≥ 7Hz).

    ros2 run mujoco_bridge rate_monitor --ros-args -p duration:=60.0

p95(Hz)는 메시지 간격의 95번째 백분위를 주기로 바꾼 값이다. 즉 간격의 95%가 이보다 짧다.
"""
import time

import numpy as np
import rclpy
from nav_msgs.msg import Odometry
from rcl_interfaces.msg import ParameterDescriptor
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image, JointState

TOPICS = [
    ("/cctv_1/image_raw", Image, qos_profile_sensor_data),
    ("/cctv_2/image_raw", Image, qos_profile_sensor_data),
    ("/robot/head_camera/image_raw", Image, qos_profile_sensor_data),
    ("/joint_states", JointState, 10),
    ("/odom", Odometry, 10),
]
TARGET_HZ, KPI_P95_HZ = 10.0, 7.0


def main():
    rclpy.init()
    node = rclpy.create_node("rate_monitor")
    duration = float(node.declare_parameter("duration", 60.0, ParameterDescriptor(dynamic_typing=True)).value)
    stamps = {topic: [] for topic, _, _ in TOPICS}
    for topic, msg_type, qos in TOPICS:
        node.create_subscription(msg_type, topic, lambda _m, t=topic: stamps[t].append(time.monotonic()), qos)

    # 첫 영상이 들어온 뒤부터 측정
    while rclpy.ok() and not stamps[TOPICS[0][0]]:
        rclpy.spin_once(node, timeout_sec=0.1)
    for s in stamps.values():
        s.clear()
    node.get_logger().info(f"측정 시작: {duration:.0f}초")
    end = time.monotonic() + duration
    while rclpy.ok() and time.monotonic() < end:
        rclpy.spin_once(node, timeout_sec=0.05)
    node.destroy_node()
    rclpy.try_shutdown()

    print(f"\n{'토픽':<32}{'수신':>7}{'평균 Hz':>10}{'p95 Hz':>9}{'최대 간격 ms':>13}  판정")
    ok_all = True
    for topic, msg_type, _ in TOPICS:
        t = np.array(stamps[topic])
        if len(t) < 3:
            print(f"{topic:<32}{len(t):>7}{'-':>10}{'-':>9}{'-':>13}  FAIL (수신 부족)")
            ok_all = False
            continue
        dt = np.diff(t)
        mean_hz = (len(t) - 1) / (t[-1] - t[0])
        p95_hz = 1.0 / np.percentile(dt, 95)
        verdict = ""
        if msg_type is Image:
            ok = mean_hz >= TARGET_HZ and p95_hz >= KPI_P95_HZ
            ok_all &= ok
            verdict = "PASS" if ok else "FAIL"
        print(f"{topic:<32}{len(t):>7}{mean_hz:>10.2f}{p95_hz:>9.2f}{dt.max() * 1000:>13.1f}  {verdict}")
    print(f"\n카메라 기준: 평균 ≥ {TARGET_HZ:.0f}Hz, p95 ≥ {KPI_P95_HZ:.0f}Hz → {'PASS' if ok_all else 'FAIL'}")
    raise SystemExit(0 if ok_all else 1)
