#!/usr/bin/env bash
# CT-01 성능 측정: 브리지(물리 + 카메라 3대 렌더)를 띄우고 토픽 주기를 측정한다.
#   bash scripts/measure_rates.sh [측정 초=60] [카메라 Hz=15] [가로=640] [세로=480]
cd "$(dirname "$0")/.."
source ros2_ws/install/setup.bash
DURATION="${1:-60}"; CAMERA_HZ="${2:-15}"; W="${3:-640}"; H="${4:-480}"
LIB=ros2_ws/install/mujoco_bridge/lib/mujoco_bridge
"$LIB/bridge" --ros-args -p camera_hz:="$CAMERA_HZ" -p image_width:="$W" -p image_height:="$H" &
BRIDGE=$!
trap 'kill -TERM $BRIDGE 2>/dev/null; wait $BRIDGE 2>/dev/null' EXIT
"$LIB/rate_monitor" --ros-args -p duration:="$DURATION"
