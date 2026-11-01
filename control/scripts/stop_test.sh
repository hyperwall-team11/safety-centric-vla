#!/usr/bin/env bash
# MuJoCo 브리지 + TIAGo 어댑터를 띄우고 실제 정지 지연을 잰다 (E-Stop·하트비트 손실 번갈아).
#   bash scripts/stop_test.sh [반복 횟수=6]
cd "$(dirname "$0")/.."
source ros2_ws/install/setup.bash
LIB=ros2_ws/install
PIDS=()
trap 'kill -TERM "${PIDS[@]}" 2>/dev/null; wait "${PIDS[@]}" 2>/dev/null' EXIT
"$LIB/mujoco_bridge/lib/mujoco_bridge/bridge" --ros-args -p cameras:=false -p worker_motion:=false &
PIDS+=($!)
for _ in $(seq 60); do ros2 topic list 2>/dev/null | grep -qx /odom && break; sleep 0.5; done
"$LIB/robot_adapter/lib/robot_adapter/tiago_adapter" &
PIDS+=($!)
sleep 2
"$LIB/safety_control/lib/safety_control/stop_test" --ros-args -p repeats:="${1:-6}"
