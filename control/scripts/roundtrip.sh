#!/usr/bin/env bash
# 어댑터를 띄우고 하네스로 MOVE_TO·E-Stop·HOLD 왕복·적합성 시험을 실행한다.
#   bash scripts/roundtrip.sh          # Mock 어댑터
#   bash scripts/roundtrip.sh tiago    # MuJoCo 브리지 + TIAGo 어댑터 (하네스 코드는 같음)
cd "$(dirname "$0")/.."
source ros2_ws/install/setup.bash
ADAPTER="${1:-mock}"
ROBOT_ID="${ROBOT_ID:-robot-01}"
LIB=ros2_ws/install
PIDS=()
trap 'kill -TERM "${PIDS[@]}" 2>/dev/null; wait "${PIDS[@]}" 2>/dev/null' EXIT
# ros2 run 래퍼는 종료 신호를 자식에게 넘기지 않으므로 실행 파일을 직접 띄운다
if [ "$ADAPTER" = "tiago" ]; then
  "$LIB/mujoco_bridge/lib/mujoco_bridge/bridge" --ros-args -p cameras:=false &
  PIDS+=($!)
  for _ in $(seq 60); do ros2 topic list 2>/dev/null | grep -qx /odom && break; sleep 0.5; done
  "$LIB/robot_adapter/lib/robot_adapter/tiago_adapter" --ros-args -p robot_id:="$ROBOT_ID" &
else
  "$LIB/robot_adapter/lib/robot_adapter/mock_adapter" --ros-args -p robot_id:="$ROBOT_ID" &
fi
PIDS+=($!)
sleep 2
"$LIB/safety_control/lib/safety_control/test_harness" --ros-args -p robot_id:="$ROBOT_ID"
