#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

LIB_DIR="$PWD/ros2_ws/install"
LOG_DIR="$(mktemp -d "${TMPDIR:-/tmp}/safety-vla-ee-delta-e2e.XXXXXX")"
BRIDGE_PID=""

cleanup() {
  local exit_code=$?
  if [[ -n "$BRIDGE_PID" ]]; then
    kill -TERM "$BRIDGE_PID" 2>/dev/null || true
    wait "$BRIDGE_PID" 2>/dev/null || true
  fi
  if ((exit_code == 0)) && [[ "${KEEP_E2E_LOGS:-0}" != "1" ]]; then
    rm -rf "$LOG_DIR"
  else
    echo "E2E 로그: $LOG_DIR" >&2
  fi
  exit "$exit_code"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [[ ! -f "$LIB_DIR/setup.bash" ]]; then
  echo "[FAIL] ROS 2 작업공간을 먼저 빌드하세요." >&2
  exit 1
fi

set +u
# shellcheck disable=SC1091
source "$LIB_DIR/setup.bash"
set -u

"$LIB_DIR/mujoco_bridge/lib/mujoco_bridge/bridge" \
  --ros-args -p cameras:=false -p worker_motion:=false >"$LOG_DIR/bridge.log" 2>&1 &
BRIDGE_PID=$!

# The probe itself waits for both /joint_states and an /arm/command subscriber.
# Avoid `ros2 topic list` here because a stale ROS daemon can hide a ready node.
"$LIB_DIR/safety_control/lib/safety_control/tiago_ee_delta_test"
