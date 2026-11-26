import json
import sys

import pytest

import openvla_closed_loop as cli
from backend_client import BackendApiError
from closed_loop import RunResult, StopReason


def arguments():
    return [
        "openvla_closed_loop.py",
        "--task-id", "task-1",
        "--image", "frame.png",
        "--instruction", "grasp the box",
        "--manipulation", "PICK",
    ]


def install_fakes(monkeypatch, result=None, failure=None):
    monkeypatch.setattr(cli, "BackendClient", lambda *args, **kwargs: object())
    monkeypatch.setattr(cli, "OpenVlaInferenceEngine", lambda *args, **kwargs: object())
    monkeypatch.setattr(cli, "FileFrameSource", lambda *args, **kwargs: object())

    class Loop:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, *args, **kwargs):
            if failure is not None:
                raise failure
            return result

    monkeypatch.setattr(cli, "OpenVlaClosedLoop", Loop)


def test_cli_returns_zero_for_confirmed_phase_completion(monkeypatch, capsys):
    install_fakes(
        monkeypatch,
        RunResult(StopReason.PHASE_COMPLETE, 3, "PLANNING", "p3"),
    )
    monkeypatch.setattr(sys, "argv", arguments())

    with pytest.raises(SystemExit) as stopped:
        cli.main()

    assert stopped.value.code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["reason"] == "PHASE_COMPLETE" and output["completed_steps"] == 3


def test_cli_returns_nonzero_for_step_limit(monkeypatch, capsys):
    install_fakes(
        monkeypatch,
        RunResult(StopReason.STEP_LIMIT, 20, "PLANNING", "p20"),
    )
    monkeypatch.setattr(sys, "argv", arguments())

    with pytest.raises(SystemExit) as stopped:
        cli.main()

    assert stopped.value.code == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "STEP_LIMIT"


def test_cli_normalizes_backend_failure(monkeypatch, capsys):
    install_fakes(monkeypatch, failure=BackendApiError("backend unavailable"))
    monkeypatch.setattr(sys, "argv", arguments())

    with pytest.raises(SystemExit) as stopped:
        cli.main()

    assert stopped.value.code == 1
    output = json.loads(capsys.readouterr().out)
    assert output == {"status": "ERROR", "detail": "backend unavailable"}
