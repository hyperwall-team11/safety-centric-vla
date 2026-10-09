from __future__ import annotations

from threading import RLock

from .models import ActionProposal, ExecutionLog, HazardEvent, Robot, SafetyRule, TaskOrder


class InMemoryStore:
    """Replaceable MVP store. The API layer does not depend on Redis details."""

    def __init__(self) -> None:
        self._lock = RLock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.robots: dict[str, Robot] = {}
            self.tasks: dict[str, TaskOrder] = {}
            self.proposals: dict[str, ActionProposal] = {}
            self.hazards: dict[str, HazardEvent] = {}
            self.logs: list[ExecutionLog] = []
            self.rules: dict[str, SafetyRule] = {}


store = InMemoryStore()
