import subprocess
from pathlib import Path


CONTROL_DIR = Path(__file__).resolve().parents[1]
SCRIPT = CONTROL_DIR / "scripts" / "backend_move_e2e.sh"


def test_backend_move_e2e_script_has_valid_bash_syntax():
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_backend_move_e2e_sources_ros_setup_without_nounset():
    script = SCRIPT.read_text(encoding="utf-8")
    assert 'set +u\nsource "$LIB_DIR/setup.bash"\nset -u' in script


def test_backend_move_e2e_task_builds_ros_workspace_first():
    pixi = (CONTROL_DIR / "pixi.toml").read_text(encoding="utf-8")
    assert 'backend-move-e2e = { cmd = "bash scripts/backend_move_e2e.sh", depends-on = ["build"] }' in pixi
    assert 'backend-openai-e2e = { cmd = "bash scripts/backend_openai_e2e.sh", depends-on = ["build"] }' in pixi
    assert 'backend-hazard-e2e = { cmd = "bash scripts/backend_hazard_e2e.sh", depends-on = ["build"] }' in pixi


def test_backend_move_e2e_cleans_up_all_child_processes():
    script = SCRIPT.read_text(encoding="utf-8")
    assert "trap cleanup EXIT" in script
    assert 'kill -TERM "${PIDS[@]}"' in script
    assert 'wait "${PIDS[@]}"' in script


def test_backend_move_e2e_waits_for_fresh_process_ready_logs():
    script = SCRIPT.read_text(encoding="utf-8")
    assert '"MuJoCo bridge ready"' in script
    assert '"tiago_adapter ready"' in script


def test_backend_move_e2e_port_check_allows_recently_closed_server():
    script = SCRIPT.read_text(encoding="utf-8")
    assert "socket.SO_REUSEADDR" in script


def test_backend_move_e2e_supports_hazard_scenario():
    script = SCRIPT.read_text(encoding="utf-8")
    assert '"$ROOT_DIR/backend/scripts/verify_hazard_estop.py"' in script
    assert '--result-topic "/adapter/${ROBOT_TOPIC_ID}/result"' in script
    assert 'tee "$LOG_DIR/hazard.log"' in script


def test_backend_move_e2e_keeps_ack_latency_as_a_report_only_metric():
    script = SCRIPT.read_text(encoding="utf-8")
    assert "--no-latency-gate" in script
    assert '--timeout "$BACKEND_HTTP_TIMEOUT"' in script
    assert '--expected-model-name "${EXPECTED_ACTION_MODEL:-}"' in script


def test_openai_e2e_fails_without_key_and_selects_openai_provider():
    script = (CONTROL_DIR / "scripts" / "backend_openai_e2e.sh").read_text(
        encoding="utf-8"
    )
    assert 'if [[ -z "${OPENAI_API_KEY:-}" ]]' in script
    assert "export BACKEND_ACTION_PLANNER=openai" in script
    assert "export EXPECTED_ACTION_MODEL=openai-responses-action-provider" in script
    assert 'export BACKEND_HTTP_TIMEOUT="${BACKEND_HTTP_TIMEOUT:-45}"' in script
