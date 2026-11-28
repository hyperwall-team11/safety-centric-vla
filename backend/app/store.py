from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from os import getenv
from threading import RLock, local
from typing import Any, Callable, Protocol

from .models import (
    ActionProposal,
    AdapterAckRecord,
    AdapterResultReport,
    ExecutionLog,
    HazardClearanceEvidence,
    HazardEvent,
    PhysicalObject,
    Robot,
    SafetyRule,
    Sensor,
    TaskOrder,
    Worker,
    Zone,
)


SNAPSHOT_VERSION = 1
DEFAULT_REDIS_KEY = "safety-centric-vla:state:v1"
LOGGER = logging.getLogger(__name__)
ATOMIC_SNAPSHOT_SAVE_SCRIPT = """
if redis.call('get', KEYS[1]) ~= ARGV[1] then
    return 0
end
redis.call('set', KEYS[2], ARGV[2])
return 1
"""


class StoreUnavailableError(RuntimeError):
    """The configured persistent store cannot safely serve the request."""


class RedisLock(Protocol):
    def acquire(
        self,
        blocking: bool = True,
        blocking_timeout: float | None = None,
    ) -> bool: ...

    def release(self) -> None: ...

    def owned(self) -> bool: ...

    def extend(self, additional_time: float, replace_ttl: bool = False) -> bool: ...


class RedisClient(Protocol):
    def ping(self) -> Any: ...

    def get(self, key: str) -> str | bytes | None: ...

    def set(self, key: str, value: str) -> Any: ...

    def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Any: ...

    def publish(self, channel: str, message: str) -> Any: ...

    def pubsub(self, **kwargs: Any) -> Any: ...

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
    target.objects: dict[str, PhysicalObject] = {}
    target.tasks: dict[str, TaskOrder] = {}
    target.task_received_at: dict[str, datetime] = {}
    target.task_idempotency: dict[str, tuple[str, str]] = {}
    target.proposals: dict[str, ActionProposal] = {}
    target.proposal_idempotency: dict[tuple[str, str], tuple[str, str]] = {}
    target.proposal_results: dict[str, AdapterResultReport] = {}
    target.proposal_acks: dict[str, AdapterAckRecord] = {}
    target.hazards: dict[str, HazardEvent] = {}
    target.hazard_clearances: dict[str, HazardClearanceEvidence] = {}
    # CRITICAL HazardEvent 중 E-Stop을 발행하게 한 hazard_id.
    target.estop_hazard_ids: set[str] = set()
    # 운영자 수동 E-Stop은 HazardEvent 해제와 독립적으로 유지한다.
    target.operator_estop_active: bool = False
    target.operator_estop_reason: str | None = None
    target.operator_estop_requested_by: str | None = None
    target.operator_estop_issued_at: datetime | None = None
    target.logs: list[ExecutionLog] = []
    # 평가 단계 사전 시뮬레이션 PASS의 snapshot_id. 실행 직전 같은 상태일 때만 재사용한다.
    target.twin_simulation_passes: dict[str, str] = {}
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
        "objects": {key: _dump_model(value) for key, value in source.objects.items()},
        "tasks": {key: _dump_model(value) for key, value in source.tasks.items()},
        "task_received_at": {
            key: value.isoformat() for key, value in source.task_received_at.items()
        },
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
        "proposal_acks": {
            key: _dump_model(value) for key, value in source.proposal_acks.items()
        },
        "hazards": {key: _dump_model(value) for key, value in source.hazards.items()},
        "hazard_clearances": {
            key: _dump_model(value) for key, value in source.hazard_clearances.items()
        },
        "estop_hazard_ids": sorted(source.estop_hazard_ids),
        "operator_estop_active": source.operator_estop_active,
        "operator_estop_reason": source.operator_estop_reason,
        "operator_estop_requested_by": source.operator_estop_requested_by,
        "operator_estop_issued_at": (
            source.operator_estop_issued_at.isoformat()
            if source.operator_estop_issued_at is not None
            else None
        ),
        "logs": [_dump_model(value) for value in source.logs],
        "twin_simulation_passes": dict(source.twin_simulation_passes),
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
    target.objects = {
        key: PhysicalObject.model_validate(value)
        for key, value in snapshot.get("objects", {}).items()
    }
    target.tasks = {
        key: TaskOrder.model_validate(value) for key, value in snapshot["tasks"].items()
    }
    # KPI 9 ACK 측정 필드는 이 필드가 없던 스냅샷도 읽을 수 있게 빈 값으로 복구한다.
    target.task_received_at = {
        key: datetime.fromisoformat(value)
        for key, value in snapshot.get("task_received_at", {}).items()
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
    target.proposal_acks = {
        key: AdapterAckRecord.model_validate(value)
        for key, value in snapshot.get("proposal_acks", {}).items()
    }
    target.hazards = {
        key: HazardEvent.model_validate(value)
        for key, value in snapshot["hazards"].items()
    }
    target.hazard_clearances = {
        key: HazardClearanceEvidence.model_validate(value)
        for key, value in snapshot.get("hazard_clearances", {}).items()
    }
    target.estop_hazard_ids = set(snapshot["estop_hazard_ids"])
    target.operator_estop_active = bool(snapshot.get("operator_estop_active", False))
    target.operator_estop_reason = snapshot.get("operator_estop_reason")
    target.operator_estop_requested_by = snapshot.get("operator_estop_requested_by")
    operator_estop_issued_at = snapshot.get("operator_estop_issued_at")
    target.operator_estop_issued_at = (
        datetime.fromisoformat(operator_estop_issued_at)
        if operator_estop_issued_at is not None
        else None
    )
    target.logs = [ExecutionLog.model_validate(value) for value in snapshot["logs"]]
    # 이 필드가 없던 스냅샷은 저장된 PASS가 없는 것으로 복구한다(실행 직전 다시 시뮬레이션).
    target.twin_simulation_passes = dict(snapshot.get("twin_simulation_passes", {}))
    target.rules = {
        key: SafetyRule.model_validate(value) for key, value in snapshot["rules"].items()
    }


class InMemoryStore:
    """Default development store used when Redis is not explicitly enabled."""

    backend_name = "memory"

    def __init__(self) -> None:
        self._lock = RLock()
        self._safety_publish_lock = RLock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            _initialize_empty_state(self)

    def ensure_available(self) -> None:
        return None

    def synchronize_safety_publish(self, publish: Callable[[bool], None]) -> None:
        with self._safety_publish_lock:
            with self.lock:
                publish(self.operator_estop_active or bool(self.estop_hazard_ids))

    def start_change_notifications(self, callback: Callable[[], Any]) -> None:
        del callback

    def stop_change_notifications(self) -> None:
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
                current = _serialize_state(store)
                if current != before:
                    store._save(current, distributed_lock)
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
            if isinstance(failure, StoreUnavailableError):
                raise failure
            raise StoreUnavailableError("Redis에 상태를 안전하게 반영하지 못했습니다.") from failure
        return False


class RedisStore:
    """Redis-backed state store with atomic snapshots and a distributed lock.

    The API currently mutates Pydantic objects in place. A single versioned JSON
    snapshot preserves that contract while Redis provides restart recovery and
    cross-process serialization. Every outer lock reloads the latest snapshot;
    every exit atomically verifies the lock token and writes the complete state.
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
        self._safety_publish_lock_key = f"{key}:estop-publish-lock"
        self._change_channel = f"{key}:changes"
        self._lock_timeout_seconds = lock_timeout_seconds
        self._lock_wait_seconds = lock_wait_seconds
        self._local_lock = RLock()
        self._thread_state = local()
        self._notification_worker: Any = None
        self._notification_pubsub: Any = None
        self._instance_id = uuid.uuid4().hex
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

    def _acquire_named_lock(self, name: str) -> RedisLock:
        distributed_lock = self._client.lock(
            name,
            timeout=self._lock_timeout_seconds,
            blocking_timeout=self._lock_wait_seconds,
        )
        if not distributed_lock.acquire(
            blocking=True,
            blocking_timeout=self._lock_wait_seconds,
        ):
            raise StoreUnavailableError("Redis 상태 잠금을 획득하지 못했습니다.")
        return distributed_lock

    def _ensure_lock_owned(self, distributed_lock: RedisLock) -> None:
        """만료된 잠금으로 다른 프로세스의 최신 스냅샷을 덮어쓰지 않는다."""
        try:
            if not distributed_lock.owned():
                raise StoreUnavailableError("Redis 상태 잠금 소유권이 만료되었습니다.")
            if not distributed_lock.extend(
                self._lock_timeout_seconds,
                replace_ttl=True,
            ):
                raise StoreUnavailableError("Redis 상태 잠금 만료시간을 연장하지 못했습니다.")
        except StoreUnavailableError:
            raise
        except Exception as exc:
            raise StoreUnavailableError("Redis 상태 잠금 소유권을 확인할 수 없습니다.") from exc

    def synchronize_safety_publish(self, publish: Callable[[bool], None]) -> None:
        """다중 Backend의 E-Stop 발행을 직렬화하고 최신 공유 상태만 발행한다."""
        safety_lock = self._acquire_named_lock(self._safety_publish_lock_key)
        try:
            with self.lock:
                self._ensure_lock_owned(safety_lock)
                publish(self.operator_estop_active or bool(self.estop_hazard_ids))
        finally:
            try:
                safety_lock.release()
            except Exception as exc:
                raise StoreUnavailableError("Redis E-Stop 발행 잠금을 해제하지 못했습니다.") from exc

    def start_change_notifications(self, callback: Callable[[], Any]) -> None:
        """다른 Backend 프로세스의 상태 저장을 WebSocket 구독자에게 알린다."""
        if self._notification_worker is not None:
            return
        try:
            pubsub = self._client.pubsub(ignore_subscribe_messages=True)

            def on_message(message: dict[str, Any]) -> None:
                data = message.get("data")
                if isinstance(data, bytes):
                    data = data.decode("utf-8", errors="replace")
                if data != self._instance_id:
                    callback()

            pubsub.subscribe(**{self._change_channel: on_message})
            self._notification_pubsub = pubsub
            self._notification_worker = pubsub.run_in_thread(
                sleep_time=0.1,
                daemon=True,
            )
        except Exception as exc:
            self._notification_pubsub = None
            self._notification_worker = None
            raise StoreUnavailableError("Redis 상태 변경 구독을 시작할 수 없습니다.") from exc

    def stop_change_notifications(self) -> None:
        worker = self._notification_worker
        pubsub = self._notification_pubsub
        self._notification_worker = None
        self._notification_pubsub = None
        if worker is not None:
            worker.stop()
        if pubsub is not None:
            pubsub.close()

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

    @staticmethod
    def _lock_token(distributed_lock: RedisLock) -> str | bytes:
        token = getattr(getattr(distributed_lock, "local", None), "token", None)
        if token is None:
            raise StoreUnavailableError("Redis 상태 잠금 토큰을 확인할 수 없습니다.")
        return token

    def _save(
        self,
        snapshot: dict[str, Any],
        distributed_lock: RedisLock,
    ) -> None:
        payload = json.dumps(
            snapshot,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            saved = self._client.eval(
                ATOMIC_SNAPSHOT_SAVE_SCRIPT,
                2,
                self._lock_key,
                self._key,
                self._lock_token(distributed_lock),
                payload,
            )
            if saved != 1:
                raise StoreUnavailableError(
                    "Redis 상태 잠금이 만료되어 스냅샷을 저장하지 않았습니다."
                )
        except StoreUnavailableError:
            raise
        except Exception as exc:
            raise StoreUnavailableError("Redis 상태 스냅샷을 저장할 수 없습니다.") from exc
        try:
            self._client.publish(self._change_channel, self._instance_id)
        except Exception as exc:
            # 상태 저장은 이미 원자적으로 완료됐다. 알림 실패로 저장 성공을 되돌린 척하지 않는다.
            LOGGER.warning("Redis 상태 변경 알림 발행 실패: %s", exc)


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
