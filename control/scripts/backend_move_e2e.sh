#!/usr/bin/env bash
# 자연어 MOVE_TO를 Backend → ROS 2 → TIAGo → MuJoCo → RESULT 경로로 한 번 실행한다.
set -euo pipefail

cd "$(dirname "$0")/.."

ROOT_DIR="$(cd .. && pwd)"
LIB_DIR="$PWD/ros2_ws/install"
PYTHON_BIN="${PYTHON_BIN:-python}"
BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
BACKEND_URL="http://${BACKEND_HOST}:${BACKEND_PORT}"
ROBOT_ID="${ROBOT_ID:-robot-01}"
RESULT_TIMEOUT="${RESULT_TIMEOUT:-30}"
if [[ -n "${E2E_LOG_DIR:-}" ]]; then
  LOG_DIR="$E2E_LOG_DIR"
else
  LOG_DIR="$(mktemp -d "${TMPDIR:-/tmp}/safety-vla-move-e2e.XXXXXX")"
fi
PIDS=()

cleanup() {
  local exit_code=$?
  if ((${#PIDS[@]})); then
    kill -TERM "${PIDS[@]}" 2>/dev/null || true
    wait "${PIDS[@]}" 2>/dev/null || true
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

fail() {
  echo "[FAIL] $*" >&2
  return 1
}

wait_for_process() {
  local pid=$1
  local name=$2
  if ! kill -0 "$pid" 2>/dev/null; then
    fail "$name 프로세스가 준비 전에 종료되었습니다. 로그를 확인하세요."
  fi
}

wait_for_backend() {
  local pid=$1
  local deadline=$((SECONDS + 30))
  while ((SECONDS < deadline)); do
    wait_for_process "$pid" "Backend"
    if "$PYTHON_BIN" - "$BACKEND_URL/health" >/dev/null 2>&1 <<'PY'
import sys
from urllib.request import urlopen

with urlopen(sys.argv[1], timeout=1) as response:
    if response.status != 200:
        raise SystemExit(1)
PY
    then
      return 0
    fi
    sleep 0.2
  done
  fail "Backend 준비 대기 시간이 초과되었습니다: $BACKEND_URL"
}

ensure_backend_port_is_free() {
  if ! "$PYTHON_BIN" - "$BACKEND_HOST" "$BACKEND_PORT" <<'PY'
import socket
import sys

with socket.socket() as server:
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((sys.argv[1], int(sys.argv[2])))
PY
  then
    fail "Backend 주소를 사용할 수 없습니다: $BACKEND_URL"
  fi
}

wait_for_topic() {
  local pid=$1
  local name=$2
  local topic=$3
  local deadline=$((SECONDS + 30))
  while ((SECONDS < deadline)); do
    wait_for_process "$pid" "$name"
    if ros2 topic list 2>/dev/null | grep -Fqx "$topic"; then
      return 0
    fi
    sleep 0.2
  done
  fail "$name 준비 대기 시간이 초과되었습니다: $topic"
}

wait_for_log() {
  local pid=$1
  local name=$2
  local log_file=$3
  local marker=$4
  local deadline=$((SECONDS + 30))
  while ((SECONDS < deadline)); do
    wait_for_process "$pid" "$name"
    if grep -Fq "$marker" "$log_file" 2>/dev/null; then
      return 0
    fi
    sleep 0.2
  done
  fail "$name 준비 로그 대기 시간이 초과되었습니다: $marker"
}

if [[ ! -f "$LIB_DIR/setup.bash" ]]; then
  fail "ROS 2 작업공간이 빌드되지 않았습니다. 먼저 'pixi run build'를 실행하세요."
fi
# shellcheck disable=SC1091
set +u
source "$LIB_DIR/setup.bash"
set -u

command -v ros2 >/dev/null || fail "ros2 명령을 찾을 수 없습니다. pixi 환경에서 실행하세요."
"$PYTHON_BIN" -c "import fastapi, uvicorn" 2>/dev/null || fail \
  "Backend 의존성이 없습니다. pixi 환경에서 'python -m pip install -r ../backend/requirements.txt'를 실행하세요."
ensure_backend_port_is_free

mkdir -p "$LOG_DIR"
echo "[1/4] Backend 시작: $BACKEND_URL"
(
  cd "$ROOT_DIR/backend"
  BACKEND_CTL_ROS_ENABLED=1 \
  BACKEND_CTL_RESULT_API="$BACKEND_URL" \
    exec "$PYTHON_BIN" -m uvicorn app.main:app \
      --host "$BACKEND_HOST" --port "$BACKEND_PORT"
) >"$LOG_DIR/backend.log" 2>&1 &
BACKEND_PID=$!
PIDS+=("$BACKEND_PID")
wait_for_backend "$BACKEND_PID"

echo "[2/4] MuJoCo 브리지 시작"
"$LIB_DIR/mujoco_bridge/lib/mujoco_bridge/bridge" \
  --ros-args -p cameras:=false >"$LOG_DIR/bridge.log" 2>&1 &
BRIDGE_PID=$!
PIDS+=("$BRIDGE_PID")
wait_for_log "$BRIDGE_PID" "MuJoCo 브리지" "$LOG_DIR/bridge.log" "MuJoCo bridge ready"
wait_for_topic "$BRIDGE_PID" "MuJoCo 브리지" "/odom"

echo "[3/4] TIAGo 어댑터 시작: robot_id=$ROBOT_ID"
"$LIB_DIR/robot_adapter/lib/robot_adapter/tiago_adapter" \
  --ros-args -p robot_id:="$ROBOT_ID" >"$LOG_DIR/adapter.log" 2>&1 &
ADAPTER_PID=$!
PIDS+=("$ADAPTER_PID")
ROBOT_TOPIC_ID="$("$PYTHON_BIN" -c \
  'import sys; from safety_control.interfaces import topic_segment; print(topic_segment(sys.argv[1]))' \
  "$ROBOT_ID")"
wait_for_log "$ADAPTER_PID" "TIAGo 어댑터" "$LOG_DIR/adapter.log" "tiago_adapter ready"
wait_for_topic "$ADAPTER_PID" "TIAGo 어댑터" "/adapter/${ROBOT_TOPIC_ID}/state"

echo "[4/4] 자연어 MOVE_TO 실행·ACK·RESULT 확인"
"$PYTHON_BIN" "$ROOT_DIR/backend/scripts/benchmark_command_to_dispatch.py" \
  --base-url "$BACKEND_URL" \
  --robot-id "$ROBOT_ID" \
  --control-adapter tiago-adapter \
  --runs 1 \
  --no-latency-gate \
  --result-timeout "$RESULT_TIMEOUT" \
  2>&1 | tee "$LOG_DIR/benchmark.log"

echo "[PASS] 자연어 MOVE_TO E2E가 완료되었습니다."
