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
            # CRITICAL HazardEvent 중 E-Stop을 발행하게 한 hazard_id. 모두 해제되면 정지 신호를 푼다.
            self.estop_hazard_ids: set[str] = set()
            self.logs: list[ExecutionLog] = []
            # 평가 단계 사전 시뮬레이션 PASS의 snapshot_id. 실행 직전 같은 상태일 때만 재사용한다.
            self.twin_simulation_passes: dict[str, str] = {}
            self.rules: dict[str, SafetyRule] = {}

    @property
    def lock(self) -> RLock:
        return self._lock


store = InMemoryStore()
