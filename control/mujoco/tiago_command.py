"""Non-blocking TIAGo arm command scheduler used by the ROS 2 bridge."""
from __future__ import annotations

from collections.abc import Callable, Generator
from math import isfinite


class TiagoCommandError(ValueError):
    pass


class TiagoCommandRunner:
    """Advance one manipulation generator without blocking ROS callbacks."""

    def __init__(
        self,
        task_factory: Callable[[dict], Generator[float, None, None]],
        cancel_motion: Callable[[], None],
    ) -> None:
        self.task_factory = task_factory
        self.cancel_motion = cancel_motion
        self.command_id: str | None = None
        self.task: Generator[float, None, None] | None = None
        self.wait_seconds = 0.0

    def submit(self, command: dict) -> list[dict]:
        command_id = command.get("command_id")
        if not isinstance(command_id, str) or not command_id:
            return [self._result("", False, "INVALID", "command_id 필요")]

        if command.get("manipulation") == "CANCEL":
            if self.task is None:
                return [self._result(command_id, False, "CANCELLED", "실행 중인 팔 명령 없음")]
            active_id = self.command_id
            self.task.close()
            self.cancel_motion()
            self._clear()
            return [self._result(active_id or command_id, False, "CANCELLED", "팔 명령 취소")]

        if self.task is not None:
            return [self._result(command_id, False, "ROBOT_BUSY", f"{self.command_id} 실행 중")]

        try:
            task = self.task_factory(command)
            if not hasattr(task, "__next__"):
                raise TiagoCommandError("조작 계획이 generator가 아님")
        except (KeyError, TypeError, ValueError, TiagoCommandError) as exc:
            return [self._result(command_id, False, "INVALID", str(exc))]

        self.command_id = command_id
        self.task = task
        self.wait_seconds = 0.0
        return []

    def tick(self, elapsed_seconds: float) -> list[dict]:
        if self.task is None:
            return []
        if not isfinite(elapsed_seconds) or elapsed_seconds < 0:
            raise ValueError("elapsed_seconds는 0 이상의 유한한 값이어야 함")

        self.wait_seconds -= elapsed_seconds
        # Advance at most one generator step per physics callback. Catch-up lag
        # must not collapse several trajectory setpoints before MuJoCo steps.
        if self.wait_seconds <= 1e-9:
            try:
                requested = next(self.task)
                if not isinstance(requested, (int, float)) or not isfinite(requested) or requested <= 0:
                    raise TiagoCommandError("조작 계획의 대기 시간은 양수여야 함")
                self.wait_seconds += float(requested)
            except StopIteration:
                command_id = self.command_id or ""
                self._clear()
                return [self._result(command_id, True, None, "팔 조작 완료")]
            except Exception as exc:
                command_id = self.command_id or ""
                self.cancel_motion()
                self._clear()
                return [self._result(command_id, False, "HW_ERROR", str(exc))]
        return []

    def _clear(self) -> None:
        self.command_id = None
        self.task = None
        self.wait_seconds = 0.0

    @staticmethod
    def _result(command_id: str, success: bool, reason_code: str | None, message: str) -> dict:
        return {
            "command_id": command_id,
            "success": success,
            "reason_code": reason_code,
            "message": message,
        }
