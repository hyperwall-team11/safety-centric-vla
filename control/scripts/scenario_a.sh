#!/usr/bin/env bash
# 시나리오 A를 ROS 2 전체 경로로 시험한다: 검증 서버 대역 → TIAGo 어댑터 → MuJoCo 브리지
#   bash scripts/scenario_a.sh
cd "$(dirname "$0")/.."
source ros2_ws/install/setup.bash
LIB=ros2_ws/install
PIDS=()
trap 'kill -TERM "${PIDS[@]}" 2>/dev/null; wait "${PIDS[@]}" 2>/dev/null' EXIT
"$LIB/mujoco_bridge/lib/mujoco_bridge/bridge" --ros-args -p cameras:=false &
PIDS+=($!)
for _ in $(seq 60); do ros2 topic list 2>/dev/null | grep -qx /odom && break; sleep 0.5; done
"$LIB/robot_adapter/lib/robot_adapter/tiago_adapter" &
PIDS+=($!)
sleep 2
"$LIB/safety_control/lib/safety_control/scenario_a_test"
