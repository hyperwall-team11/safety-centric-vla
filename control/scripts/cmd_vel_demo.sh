#!/usr/bin/env bash
# STEP 5 확인: MuJoCo 브리지를 띄우고 /cmd_vel로 전진·회전·정지한 뒤 /odom을 출력한다.
cd "$(dirname "$0")/.."
source ros2_ws/install/setup.bash
ros2_ws/install/mujoco_bridge/lib/mujoco_bridge/bridge &
BRIDGE=$!
trap 'kill -TERM $BRIDGE 2>/dev/null; wait $BRIDGE 2>/dev/null' EXIT

for _ in $(seq 60); do ros2 topic list 2>/dev/null | grep -qx /odom && break; sleep 0.5; done
ODOM=ros2_ws/install/mujoco_bridge/lib/mujoco_bridge/odom_echo
pub() { ros2 topic pub -r 10 --times "$1" /cmd_vel geometry_msgs/msg/Twist "$2" >/dev/null; }

echo "[시작]                         $($ODOM)"
pub 20 "{linear: {x: 0.3}}"
echo "[전진 0.3 m/s, 2초]            $($ODOM)"
pub 30 "{angular: {z: 0.5}}"
echo "[회전 0.5 rad/s, 3초]          $($ODOM)"
pub 5 "{}"
sleep 0.5
echo "[정지]                         $($ODOM)"
