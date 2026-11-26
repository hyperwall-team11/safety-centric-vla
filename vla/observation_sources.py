"""File-based observation and CV phase-completion boundaries."""
from __future__ import annotations

import json
import time
from pathlib import Path


class ObservationTimeout(TimeoutError):
    pass


class CompletionSignalError(ValueError):
    pass


class FileFrameSource:
    """Return a file once per distinct mtime/size observation."""

    def __init__(
        self,
        path: str | Path,
        *,
        timeout_seconds: float = 30.0,
        poll_interval_seconds: float = 0.1,
    ) -> None:
        if timeout_seconds <= 0 or poll_interval_seconds <= 0:
            raise ValueError("frame source timeouts must be positive")
        self.path = Path(path)
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.last_signature: tuple[int, int] | None = None

    def next_frame(self, step: int) -> Path:
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            try:
                stat = self.path.stat()
            except FileNotFoundError:
                stat = None
            if stat is not None and stat.st_size > 0:
                signature = (stat.st_mtime_ns, stat.st_size)
                if signature != self.last_signature:
                    self.last_signature = signature
                    return self.path
            time.sleep(self.poll_interval_seconds)
        raise ObservationTimeout(
            f"No new frame at {self.path} within {self.timeout_seconds:g}s (step={step})"
        )


class JsonPhaseCompletionSource:
    """Accept only an explicit CV/operator JSON completion signal.

    Expected shape: ``{"manipulation":"PICK", "complete":true}``.
    Optional ``frame`` and ``step`` fields bind the signal to one observation.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def is_complete(self, manipulation: str, frame: Path, step: int) -> bool:
        if not self.path.exists():
            return False
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CompletionSignalError(f"Invalid completion signal: {self.path}") from exc
        if not isinstance(payload, dict):
            raise CompletionSignalError("Completion signal must be a JSON object")
        if payload.get("complete") is not True:
            return False
        if payload.get("manipulation") != manipulation:
            return False
        if "frame" in payload and Path(str(payload["frame"])).name != frame.name:
            return False
        if "step" in payload and payload["step"] != step:
            return False
        return True
