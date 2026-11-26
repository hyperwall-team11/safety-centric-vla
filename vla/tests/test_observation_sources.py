import json

import pytest

from observation_sources import (
    CompletionSignalError,
    FileFrameSource,
    JsonPhaseCompletionSource,
    ObservationTimeout,
)


def test_file_source_returns_only_new_file_versions(tmp_path):
    frame = tmp_path / "cctv.png"
    frame.write_bytes(b"first")
    source = FileFrameSource(frame, timeout_seconds=0.02, poll_interval_seconds=0.001)

    assert source.next_frame(0) == frame
    with pytest.raises(ObservationTimeout):
        source.next_frame(1)
    frame.write_bytes(b"second-frame")
    assert source.next_frame(1) == frame


def test_completion_signal_matches_phase_frame_and_step(tmp_path):
    signal = tmp_path / "complete.json"
    signal.write_text(json.dumps({
        "manipulation": "PICK",
        "complete": True,
        "frame": "cctv.png",
        "step": 2,
    }))
    source = JsonPhaseCompletionSource(signal)

    assert source.is_complete("PICK", tmp_path / "cctv.png", 2)
    assert not source.is_complete("PLACE", tmp_path / "cctv.png", 2)
    assert not source.is_complete("PICK", tmp_path / "other.png", 2)
    assert not source.is_complete("PICK", tmp_path / "cctv.png", 3)


def test_missing_completion_signal_is_fail_closed_false(tmp_path):
    source = JsonPhaseCompletionSource(tmp_path / "missing.json")
    assert not source.is_complete("PICK", tmp_path / "cctv.png", 0)


def test_malformed_completion_signal_stops_instead_of_completing(tmp_path):
    signal = tmp_path / "complete.json"
    signal.write_text("not-json")

    with pytest.raises(CompletionSignalError):
        JsonPhaseCompletionSource(signal).is_complete("PICK", tmp_path / "cctv.png", 0)
