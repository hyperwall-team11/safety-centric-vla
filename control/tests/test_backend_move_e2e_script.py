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


def test_backend_move_e2e_task_builds_ros_workspace_first():
    pixi = (CONTROL_DIR / "pixi.toml").read_text(encoding="utf-8")
    assert 'backend-move-e2e = { cmd = "bash scripts/backend_move_e2e.sh", depends-on = ["build"] }' in pixi


def test_backend_move_e2e_cleans_up_all_child_processes():
    script = SCRIPT.read_text(encoding="utf-8")
    assert "trap cleanup EXIT" in script
    assert 'kill -TERM "${PIDS[@]}"' in script
    assert 'wait "${PIDS[@]}"' in script
