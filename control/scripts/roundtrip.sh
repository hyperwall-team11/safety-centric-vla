#!/usr/bin/env bash
# Mock 어댑터를 띄우고 하네스로 MOVE_TO·E-Stop·HOLD 왕복 시험을 실행한다.
cd "$(dirname "$0")/.."
source ros2_ws/install/setup.bash
ROBOT_ID="${ROBOT_ID:-robot-01}"
LIB=ros2_ws/install
# ros2 run 래퍼는 종료 신호를 자식에게 넘기지 않으므로 실행 파일을 직접 띄운다
"$LIB/robot_adapter/lib/robot_adapter/mock_adapter" --ros-args -p robot_id:="$ROBOT_ID" &
ADAPTER=$!
trap 'kill -TERM $ADAPTER 2>/dev/null; wait $ADAPTER 2>/dev/null' EXIT
sleep 2
"$LIB/safety_control/lib/safety_control/test_harness" --ros-args -p robot_id:="$ROBOT_ID"
