from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass


LOGGER = logging.getLogger(__name__)
CLOSED_LOOP_ENABLED_ENV = "BACKEND_CLOSED_LOOP_ENABLED"
CLOSED_LOOP_POLL_INTERVAL_ENV = "BACKEND_CLOSED_LOOP_POLL_INTERVAL_SECONDS"
CLOSED_LOOP_MAX_ATTEMPTS_ENV = "BACKEND_CLOSED_LOOP_MAX_ATTEMPTS_PER_STEP"
CLOSED_LOOP_RESULT_TIMEOUT_ENV = "BACKEND_CLOSED_LOOP_RESULT_TIMEOUT_SECONDS"


def _positive_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name}는 양수여야 합니다.") from exc
    if value <= 0:
        raise RuntimeError(f"{name}는 양수여야 합니다.")
    return value


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name}는 양의 정수여야 합니다.") from exc
    if value <= 0:
        raise RuntimeError(f"{name}는 양의 정수여야 합니다.")
    return value


@dataclass(frozen=True)
class ClosedLoopConfig:
    enabled: bool = False
    poll_interval_seconds: float = 0.5
    max_attempts_per_step: int = 3
    result_timeout_seconds: float = 120.0

    @classmethod
    def from_environment(cls) -> ClosedLoopConfig:
        enabled = os.getenv(CLOSED_LOOP_ENABLED_ENV, "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        return cls(
            enabled=enabled,
            poll_interval_seconds=_positive_float(
                CLOSED_LOOP_POLL_INTERVAL_ENV,
                0.5,
            ),
            max_attempts_per_step=_positive_int(
                CLOSED_LOOP_MAX_ATTEMPTS_ENV,
                3,
            ),
            result_timeout_seconds=_positive_float(
                CLOSED_LOOP_RESULT_TIMEOUT_ENV,
                120.0,
            ),
        )


class ClosedLoopExecutor:
    """주기적으로 안전한 다음 Task 실행 단계를 진행하는 단일 Worker."""

    def __init__(self, config: ClosedLoopConfig, run_cycle: Callable[[], None]) -> None:
        self.config = config
        self._run_cycle = run_cycle
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if not self.config.enabled or self.running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="backend-closed-loop-executor",
            daemon=True,
        )
        self._thread.start()
        LOGGER.info(
            "Closed-loop executor 시작: poll=%.3fs, attempts=%d, result_timeout=%.1fs",
            self.config.poll_interval_seconds,
            self.config.max_attempts_per_step,
            self.config.result_timeout_seconds,
        )

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(5.0, self.config.poll_interval_seconds * 2))
            if thread.is_alive():
                LOGGER.warning("Closed-loop executor가 종료 대기 시간 안에 멈추지 않았습니다.")
                return
        self._thread = None

    def run_once(self) -> None:
        self._run_cycle()

    def _run(self) -> None:
        while not self._stop_event.wait(self.config.poll_interval_seconds):
            try:
                self.run_once()
            except Exception:
                # 한 번의 저장소·외부 서비스 실패가 Worker 자체를 종료시키지 않는다.
                LOGGER.exception("Closed-loop executor 주기 실행 실패")
