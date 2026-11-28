"""Fail-closed OpenVLA observation/inference/action state machine."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from collections.abc import Callable
from typing import Protocol

from backend_client import BackendClient, TERMINAL_TASK_STATUSES


class StopReason(str, Enum):
    TASK_TERMINAL = "TASK_TERMINAL"
    PHASE_COMPLETE = "PHASE_COMPLETE"
    SAFETY_REJECTED = "SAFETY_REJECTED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    STEP_LIMIT = "STEP_LIMIT"


@dataclass(frozen=True)
class RunResult:
    reason: StopReason
    completed_steps: int
    task_status: str
    proposal_id: str | None = None


class InferenceEngine(Protocol):
    model_id: str
    unnorm_key: str

    def predict(self, image_path: str | Path, instruction: str) -> list[float]: ...


class FrameSource(Protocol):
    def next_frame(self, step: int) -> Path: ...


class PhaseCompletionSource(Protocol):
    def is_complete(self, manipulation: str, frame: Path, step: int) -> bool: ...


class NeverComplete:
    def is_complete(self, manipulation: str, frame: Path, step: int) -> bool:
        return False


class OpenVlaClosedLoop:
    def __init__(
        self,
        backend: BackendClient,
        engine: InferenceEngine,
        frames: FrameSource,
        *,
        completion: PhaseCompletionSource | None = None,
        result_timeout_seconds: float = 30.0,
        event_sink: Callable[[dict], None] | None = None,
    ) -> None:
        if result_timeout_seconds <= 0:
            raise ValueError("result_timeout_seconds must be positive")
        self.backend = backend
        self.engine = engine
        self.frames = frames
        self.completion = completion or NeverComplete()
        self.result_timeout_seconds = result_timeout_seconds
        self.event_sink = event_sink or (lambda _event: None)

    def run(
        self,
        task_id: str,
        instruction: str,
        *,
        manipulation: str,
        speed_limit: float,
        max_steps: int,
    ) -> RunResult:
        if manipulation not in {"PICK", "PLACE"}:
            raise ValueError("manipulation must be PICK or PLACE")
        if speed_limit <= 0 or max_steps <= 0:
            raise ValueError("speed_limit and max_steps must be positive")

        completed_steps = 0
        latest_proposal_id = None
        for step in range(max_steps):
            task = self.backend.get_task(task_id)
            task_status = task.get("status")
            self.event_sink({"event": "TASK_STATUS", "step": step, "status": task_status})
            if task_status in TERMINAL_TASK_STATUSES:
                return RunResult(
                    StopReason.TASK_TERMINAL,
                    completed_steps,
                    str(task_status),
                    latest_proposal_id,
                )

            frame = self.frames.next_frame(step)
            phase_complete = self.completion.is_complete(manipulation, frame, step)
            self.event_sink({
                "event": "OBSERVED",
                "step": step,
                "frame": str(frame),
                "phase_complete": phase_complete,
            })
            action = self.engine.predict(frame, instruction)
            proposal = self.backend.submit_openvla_action(
                task_id,
                action,
                manipulation=manipulation,
                speed_limit=speed_limit,
                phase_complete=phase_complete,
                model_name=self.engine.model_id,
                model_version=self.engine.unnorm_key,
            )
            latest_proposal_id = proposal["proposal_id"]
            self.event_sink({
                "event": "PROPOSED",
                "step": step,
                "proposal_id": latest_proposal_id,
            })

            evaluated = self.backend.evaluate(latest_proposal_id)
            self.event_sink({
                "event": "EVALUATED",
                "step": step,
                "proposal_id": latest_proposal_id,
                "status": evaluated.get("status"),
            })
            if evaluated.get("status") != "APPROVED":
                return RunResult(
                    StopReason.SAFETY_REJECTED,
                    completed_steps,
                    str(task_status),
                    latest_proposal_id,
                )

            executed = self.backend.execute(latest_proposal_id)
            self.event_sink({
                "event": "DISPATCHED",
                "step": step,
                "proposal_id": latest_proposal_id,
                "status": executed.get("status"),
            })
            if executed.get("status") != "EXECUTING":
                return RunResult(
                    StopReason.EXECUTION_FAILED,
                    completed_steps,
                    str(task_status),
                    latest_proposal_id,
                )
            terminal = self.backend.wait_for_proposal(
                task_id,
                latest_proposal_id,
                timeout_seconds=self.result_timeout_seconds,
            )
            self.event_sink({
                "event": "RESULT",
                "step": step,
                "proposal_id": latest_proposal_id,
                "status": terminal.get("status"),
            })
            if terminal.get("status") != "SUCCEEDED":
                return RunResult(
                    StopReason.EXECUTION_FAILED,
                    completed_steps,
                    str(task_status),
                    latest_proposal_id,
                )
            completed_steps += 1

            if phase_complete:
                task = self.backend.get_task(task_id)
                task_status = str(task.get("status"))
                return RunResult(
                    StopReason.TASK_TERMINAL
                    if task_status in TERMINAL_TASK_STATUSES
                    else StopReason.PHASE_COMPLETE,
                    completed_steps,
                    task_status,
                    latest_proposal_id,
                )

        task_status = str(self.backend.get_task(task_id).get("status"))
        return RunResult(
            StopReason.STEP_LIMIT,
            completed_steps,
            task_status,
            latest_proposal_id,
        )
