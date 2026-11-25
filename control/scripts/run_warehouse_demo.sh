#!/usr/bin/env bash
# pixi run warehouse-demo
set -eo pipefail
CONTROL="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="$(cd "$CONTROL/.." && pwd)"
RENDER="$CONTROL/scripts/threejs_cctv"
mkdir -p "$CONTROL/outputs"
LOG="$(mktemp -d "$CONTROL/outputs/warehouse.XXXXXX")"
BACKEND=http://127.0.0.1:8017
export PIXI_PROJECT_ROOT="$CONTROL"
WAREHOUSE_EXTRA_PYTHONPATH="${PYTHONPATH:-}"
if [[ -n "${WAREHOUSE_ROS_PREFIX:-}" ]]; then
  export PATH="$WAREHOUSE_ROS_PREFIX/bin:$PATH"
  source "$WAREHOUSE_ROS_PREFIX/setup.bash"
fi
export ROS_LOCALHOST_ONLY=1 ROS_DOMAIN_ID="${WAREHOUSE_ROS_DOMAIN_ID:-73}"
export PYTHONPATH="${WAREHOUSE_EXTRA_PYTHONPATH:+$WAREHOUSE_EXTRA_PYTHONPATH:}$CONTROL/mujoco:$ROOT/mujoco-demo:$CONTROL/ros2_ws/src/mujoco_bridge:$CONTROL/ros2_ws/src/robot_adapter:$CONTROL/ros2_ws/src/safety_control${PYTHONPATH:+:$PYTHONPATH}"
PYTHON="${WAREHOUSE_PYTHON:-python}"
DEMO="$CONTROL/scripts/warehouse_demo.py"
PIDS=()
cleanup() {
  if ((${#PIDS[@]})); then
    kill -TERM "${PIDS[@]}" 2>/dev/null || true
    wait "${PIDS[@]}" 2>/dev/null || true
  fi
  echo "시연 로그 $LOG"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

spawn() {
  local name=$1
  shift
  "$@" >"$LOG/$name.log" 2>&1 &
  latest_pid=$!
  PIDS+=("$latest_pid")
}

wait_until() {
  local pid=$1 name=$2 timeout=$3
  shift 3
  local deadline=$((SECONDS + timeout))
  while ((SECONDS < deadline)); do
    if ! kill -0 "$pid" 2>/dev/null; then
      tail -20 "$LOG/$name.log" >&2
      return 1
    fi
    if "$@"; then return 0; fi
    sleep 0.5
  done
  echo "$name 준비 시간 초과" >&2
  tail -20 "$LOG/$name.log" >&2
  return 1
}

http_ready() { curl -fsS --max-time 1 "$1" >/dev/null 2>&1; }

test -f "$RENDER/assets/manifest.json" && test -d "$RENDER/node_modules" || {
  echo "먼저 Three.js README의 npm 설치와 에셋 준비를 실행하세요." >&2
  exit 1
}
if [[ -n "${CHROMIUM_PATH:-}" && ! -x "$CHROMIUM_PATH" ]]; then
  echo "실행 가능한 Chromium이 없습니다. CHROMIUM_PATH를 확인하세요." >&2
  exit 1
fi
"$PYTHON" -c 'import rclpy, mujoco, uvicorn, fastapi, redis'
"$PYTHON" -c 'import rclpy, torch, transformers'
"$PYTHON" - <<'PY'
import socket
for port in (5181, 8017, 8081, 8100, 8765):
    with socket.socket() as test:
        test.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            test.bind(("127.0.0.1", port))
        except OSError as exc:
            raise SystemExit(f"포트 {port}를 사용할 수 없습니다. {exc}") from exc
PY

spawn twin "$PYTHON" "$CONTROL/mujoco/twin_simulate.py"
spawn backend env BACKEND_STORE=memory BACKEND_CTL_ROS_ENABLED=1 BACKEND_CTL_RESULT_API="$BACKEND" \
  BACKEND_PERCEPTION_SAFETY_ENABLED=1 BACKEND_TWIN_SIMULATION_ENABLED=1 \
  "$PYTHON" -m uvicorn app.main:app --app-dir "$ROOT/backend" --host 127.0.0.1 --port 8017
wait_until "$latest_pid" backend 35 http_ready "$BACKEND/health"
"$PYTHON" "$DEMO" register --directory "$LOG"

spawn bridge "$PYTHON" -c 'from mujoco_bridge.bridge_node import main; main()' --ros-args \
  -p warehouse_demo:=true -p cctv_renderer:=threejs -p camera_hz:=5.0 -p "threejs_data:=$LOG/data"
wait_until "$latest_pid" bridge 35 test -s "$LOG/data/scene.json"
"$PYTHON" "$DEMO" calibration --directory "$LOG"

spawn adapter "$PYTHON" -c 'from robot_adapter.amr_adapter_node import main; main()' --ros-args -p robot_id:=amr-01
spawn renderer node "$RENDER/render.mjs" --ros --data "$LOG/data"
spawn video "$PYTHON" -m mujoco_bridge.cctv_stream --ros-args -p show_sim_clock:=true
spawn screen "$PYTHON" "$DEMO" serve --directory "$LOG"
spawn detector "$PYTHON" "$ROOT/mujoco-demo/ros_camera_detect.py" \
  --topic /cctv_2/image_raw --detector rtdetr --device "${WAREHOUSE_CV_DEVICE:-cpu}" \
  --model "${WAREHOUSE_CV_MODEL:-PekingU/rtdetr_r18vd}" --backend-url "$BACKEND" \
  --covered-zone-id 횡단진입구역 --covered-zone-id 검사스테이션 \
  --hazard-zone-id 횡단진입구역 --robot-id amr-01 --camera-calib "$LOG/calibration.json" \
  --output "$LOG/detections.jsonl" --preview-jpeg "$LOG/detection.jpg"

echo "발표 화면 http://127.0.0.1:5181"
echo "시연 로그 $LOG"
wait_until "$latest_pid" detector 240 test -s "$LOG/detections.jsonl"
echo "영상과 검출 준비를 발표 화면에서 확인하세요. 초기화하려면 Ctrl-C 후 같은 명령을 다시 실행하세요."
while :; do
  for pid in "${PIDS[@]}"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "시연 서비스가 종료되었습니다. $LOG 로그를 확인하세요." >&2
      exit 1
    fi
  done
  sleep 1
done
