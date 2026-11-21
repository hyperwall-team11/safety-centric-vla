#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
E2E_SCENARIO=hazard exec bash scripts/backend_move_e2e.sh
