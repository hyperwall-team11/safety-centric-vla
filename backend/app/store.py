from __future__ import annotations

from datetime import datetime
from threading import RLock

from .models import (
    ActionProposal,
    AdapterResultReport,
    ExecutionLog,
    HazardEvent,
    Robot,
    SafetyRule,
    Sensor,
    TaskOrder,
    Worker,
    Zone,
)


class InMemoryStore:
    """Replaceable MVP store. The API layer does not depend on Redis details."""

    def __init__(self) -> None:
        self._lock = RLock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.robots: dict[str, Robot] = {}
            self.sensors: dict[str, Sensor] = {}
            self.workers: list[Worker] | None = None
            self.workers_observed_at: datetime | None = None
            self.zones: dict[str, Zone] = {}
            self.tasks: dict[str, TaskOrder] = {}
            self.task_idempotency: dict[str, tuple[str, str]] = {}
            self.proposals: dict[str, ActionProposal] = {}
            self.proposal_idempotency: dict[tuple[str, str], tuple[str, str]] = {}
            self.proposal_results: dict[str, AdapterResultReport] = {}
            self.hazards: dict[str, HazardEvent] = {}
            self.logs: list[ExecutionLog] = []
            self.rules: dict[str, SafetyRule] = {}

    @property
    def lock(self) -> RLock:
        return self._lock


store = InMemoryStore()
