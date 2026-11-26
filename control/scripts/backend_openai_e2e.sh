#!/usr/bin/env bash
# OpenAI Responses API가 생성한 Proposal만 허용하고 실제 ROS 2/MuJoCo MOVE_TO를 검증한다.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
if [[ -f "$ROOT_DIR/.env.local" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env.local"
  set +a
fi

if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "[FAIL] OPENAI_API_KEY가 필요합니다. 키 값은 로그나 명령 인자로 넘기지 마세요." >&2
  exit 1
fi

export BACKEND_ACTION_PLANNER=openai
export EXPECTED_ACTION_MODEL=openai-responses-action-provider
export BACKEND_HTTP_TIMEOUT="${BACKEND_HTTP_TIMEOUT:-45}"
exec bash "$(dirname "$0")/backend_move_e2e.sh"
