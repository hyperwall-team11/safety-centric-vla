from __future__ import annotations

import json
from datetime import datetime
from os import getenv
from threading import RLock, local
from typing import Any, Protocol

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


SNAPSHOT_VERSION = 1
DEFAULT_REDIS_KEY = "safety-centric-vla:state:v1"


class StoreUnavailableError(RuntimeError):
    """The configured persistent store cannot safely serve the request."""


class RedisLock(Protocol):
    def acquire(
        self,
        blocking: bool = True,
        blocking_timeout: float | None = None,
    ) -> bool: ...

    def release(self) -> None: ...


class RedisClient(Protocol):
    def ping(self) -> Any: ...

    def get(self, key: str) -> str | bytes | None: ...

    def set(self, key: str, value: str) -> Any: ...

    def lock(
        self,
        name: str,
        timeout: float | None = None,
        blocking_timeout: float | None = None,
    ) -> RedisLock: ...


def _initialize_empty_state(target: Any) -> None:
    target.robots: dict[str, Robot] = {}
    target.sensors: dict[str, Sensor] = {}
    target.workers: list[Worker] | None = None
    target.workers_observed_at: datetime | None = None
    target.zones: dict[str, Zone] = {}
    target.tasks: dict[str, TaskOrder] = {}
    target.task_idempotency: dict[str, tuple[str, str]] = {}
    target.proposals: dict[str, ActionProposal] = {}
    target.proposal_idempotency: dict[tuple[str, str], tuple[str, str]] = {}
    target.proposal_results: dict[str, AdapterResultReport] = {}
    target.hazards: dict[str, HazardEvent] = {}
    # CRITICAL HazardEvent 중 E-Stop을 발행하게 한 hazard_id.
    target.estop_hazard_ids: set[str] = set()
    target.logs: list[ExecutionLog] = []
    target.rules: dict[str, SafetyRule] = {}


def _dump_model(value: Any) -> dict[str, Any]:
    return value.model_dump(mode="json")


def _serialize_state(source: Any) -> dict[str, Any]:
    return {
        "version": SNAPSHOT_VERSION,
        "robots": {key: _dump_model(value) for key, value in source.robots.items()},
        "sensors": {key: _dump_model(value) for key, value in source.sensors.items()},
        "workers": (
            None if source.workers is None else [_dump_model(value) for value in source.workers]
        ),
        "workers_observed_at": (
            source.workers_observed_at.isoformat()
            if source.workers_observed_at is not None
            else None
        ),
        "zones": {key: _dump_model(value) for key, value in source.zones.items()},
        "tasks": {key: _dump_model(value) for key, value in source.tasks.items()},
        "task_idempotency": {
            key: list(value) for key, value in source.task_idempotency.items()
        },
        "proposals": {
            key: _dump_model(value) for key, value in source.proposals.items()
        },
        "proposal_idempotency": [
            [task_id, key, proposal_id, fingerprint]
            for (task_id, key), (
                proposal_id,
                fingerprint,
            ) in source.proposal_idempotency.items()
        ],
        "proposal_results": {
            key: _dump_model(value) for key, value in source.proposal_results.items()
        },
        "hazards": {key: _dump_model(value) for key, value in source.hazards.items()},
        "estop_hazard_ids": sorted(source.estop_hazard_ids),
        "logs": [_dump_model(value) for value in source.logs],
        "rules": {key: _dump_model(value) for key, value in source.rules.items()},
    }


def _restore_state(target: Any, snapshot: dict[str, Any]) -> None:
    if snapshot.get("version") != SNAPSHOT_VERSION:
        raise ValueError("지원하지 않는 Redis 스냅샷 버전입니다.")

    target.robots = {
        key: Robot.model_validate(value) for key, value in snapshot["robots"].items()
    }
    target.sensors = {
        key: Sensor.model_validate(value) for key, value in snapshot["sensors"].items()
    }
    workers = snapshot["workers"]
    target.workers = (
        None if workers is None else [Worker.model_validate(value) for value in workers]
    )
    observed_at = snapshot["workers_observed_at"]
    target.workers_observed_at = (
        datetime.fromisoformat(observed_at) if observed_at is not None else None
    )
    target.zones = {
        key: Zone.model_validate(value) for key, value in snapshot["zones"].items()
    }
    target.tasks = {
        key: TaskOrder.model_validate(value) for key, value in snapshot["tasks"].items()
    }
    target.task_idempotency = {
        key: (value[0], value[1])
        for key, value in snapshot["task_idempotency"].items()
    }
    target.proposals = {
        key: ActionProposal.model_validate(value)
        for key, value in snapshot["proposals"].items()
    }
    target.proposal_idempotency = {
        (task_id, key): (proposal_id, fingerprint)
        for task_id, key, proposal_id, fingerprint in snapshot["proposal_idempotency"]
    }
    target.proposal_results = {
        key: AdapterResultReport.model_validate(value)
        for key, value in snapshot["proposal_results"].items()
    }
    target.hazards = {
        key: HazardEvent.model_validate(value)
        for key, value in snapshot["hazards"].items()
    }
    target.estop_hazard_ids = set(snapshot["estop_hazard_ids"])
    target.logs = [ExecutionLog.model_validate(value) for value in snapshot["logs"]]
    target.rules = {
        key: SafetyRule.model_validate(value) for key, value in snapshot["rules"].items()
    }


class InMemoryStore:
    """Default development store used when Redis is not explicitly enabled."""

    backend_name = "memory"

    def __init__(self) -> None:
        self._lock = RLock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            _initialize_empty_state(self)

    def ensure_available(self) -> None:
        return None

    @property
    def lock(self) -> RLock:
        return self._lock


class _RedisStoreLock:
    def __init__(self, store: RedisStore) -> None:
        self.store = store

    def __enter__(self) -> RedisStore:
        store = self.store
        store._local_lock.acquire()
        depth = getattr(store._thread_state, "depth", 0)
        try:
            if depth == 0:
                store.ensure_available()
                distributed_lock = store._client.lock(
                    store._lock_key,
                    timeout=store._lock_timeout_seconds,
                    blocking_timeout=store._lock_wait_seconds,
                )
                if not distributed_lock.acquire(
                    blocking=True,
                    blocking_timeout=store._lock_wait_seconds,
                ):
                    raise StoreUnavailableError("Redis 상태 잠금을 획득하지 못했습니다.")
                store._thread_state.distributed_lock = distributed_lock
                store._load()
                store._thread_state.before = _serialize_state(store)
            store._thread_state.depth = depth + 1
            return store
        except Exception:
            if depth == 0:
                distributed_lock = getattr(store._thread_state, "distributed_lock", None)
                if distributed_lock is not None:
                    try:
                        distributed_lock.release()
                    except Exception:
                        pass
                    store._thread_state.distributed_lock = None
            store._local_lock.release()
            raise

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        store = self.store
        depth = store._thread_state.depth - 1
        store._thread_state.depth = depth
        failure: Exception | None = None
        if depth == 0:
            before = store._thread_state.before
            distributed_lock = store._thread_state.distributed_lock
            try:
                store._save()
            except Exception as save_error:
                failure = save_error
                try:
                    _restore_state(store, before)
                except Exception as restore_error:
                    failure = restore_error
            finally:
                try:
                    distributed_lock.release()
                except Exception as release_error:
                    failure = failure or release_error
                store._thread_state.distributed_lock = None
                store._thread_state.before = None
        store._local_lock.release()
        if failure is not None:
            raise StoreUnavailableError("Redis에 상태를 안전하게 반영하지 못했습니다.") from failure
        return False


class RedisStore:
    """Redis-backed state store with atomic snapshots and a distributed lock.

    The API currently mutates Pydantic objects in place. A single versioned JSON
    snapshot preserves that contract while Redis provides restart recovery and
    cross-process serialization. Every outer lock reloads the latest snapshot;
    every exit writes the complete new state with one atomic SET.
    """

    backend_name = "redis"

    def __init__(
        self,
        redis_url: str,
        *,
        key: str = DEFAULT_REDIS_KEY,
        client: RedisClient | None = None,
        lock_timeout_seconds: float = 30.0,
        lock_wait_seconds: float = 2.0,
    ) -> None:
        if client is None:
            try:
                from redis import Redis
            except ImportError as exc:
                raise RuntimeError(
                    "Redis 저장소를 사용하려면 backend/requirements.txt 의존성을 설치하세요."
                ) from exc
            client = Redis.from_url(redis_url, decode_responses=True)
        self._client = client
        self._key = key
        self._lock_key = f"{key}:lock"
        self._lock_timeout_seconds = lock_timeout_seconds
        self._lock_wait_seconds = lock_wait_seconds
        self._local_lock = RLock()
        self._thread_state = local()
        _initialize_empty_state(self)
        # Start only after Redis connectivity and snapshot validity are confirmed.
        with self.lock:
            pass

    @property
    def lock(self) -> _RedisStoreLock:
        return _RedisStoreLock(self)

    def ensure_available(self) -> None:
        try:
            if not self._client.ping():
                raise StoreUnavailableError("Redis PING 응답이 정상이 아닙니다.")
        except StoreUnavailableError:
            raise
        except Exception as exc:
            raise StoreUnavailableError("Redis에 연결할 수 없습니다.") from exc

    def reset(self) -> None:
        with self.lock:
            _initialize_empty_state(self)

    def _load(self) -> None:
        try:
            payload = self._client.get(self._key)
            if payload is None:
                _initialize_empty_state(self)
                return
            if isinstance(payload, bytes):
                payload = payload.decode("utf-8")
            snapshot = json.loads(payload)
            _restore_state(self, snapshot)
        except Exception as exc:
            raise StoreUnavailableError("Redis 상태 스냅샷을 읽을 수 없습니다.") from exc

    def _save(self) -> None:
        payload = json.dumps(
            _serialize_state(self),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            if not self._client.set(self._key, payload):
                raise StoreUnavailableError("Redis SET 응답이 정상이 아닙니다.")
        except StoreUnavailableError:
            raise
        except Exception as exc:
            raise StoreUnavailableError("Redis 상태 스냅샷을 저장할 수 없습니다.") from exc


def create_store() -> InMemoryStore | RedisStore:
    backend = getenv("BACKEND_STORE", "memory").strip().lower()
    if backend == "memory":
        return InMemoryStore()
    if backend == "redis":
        redis_url = getenv("BACKEND_REDIS_URL", "redis://127.0.0.1:6379/0")
        redis_key = getenv("BACKEND_REDIS_KEY", DEFAULT_REDIS_KEY)
        return RedisStore(redis_url, key=redis_key)
    raise RuntimeError("BACKEND_STORE는 memory 또는 redis여야 합니다.")


store = create_store()
