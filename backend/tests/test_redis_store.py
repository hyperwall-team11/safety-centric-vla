from __future__ import annotations

import os
import threading
import time
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

import app.main as main_module
from app.models import MotionLimits, Pose, Robot, Vector3
from app.store import RedisStore, StoreUnavailableError


class FakeRedisLock:
    def __init__(self, client: FakeRedis, name: str) -> None:
        self.client = client
        self.name = name
        self.acquired = False

    def acquire(
        self,
        blocking: bool = True,
        blocking_timeout: float | None = None,
    ) -> bool:
        del blocking, blocking_timeout
        if self.client.fail_lock or self.name in self.client.locked_names:
            return False
        self.client.locked_names.add(self.name)
        self.client.lock_acquisitions += 1
        self.acquired = True
        return True

    def release(self) -> None:
        if not self.acquired:
            raise RuntimeError("획득하지 않은 잠금입니다.")
        self.client.locked_names.discard(self.name)
        self.acquired = False

    def owned(self) -> bool:
        return (
            self.acquired
            and self.name in self.client.locked_names
            and not self.client.expire_lock
        )

    def extend(self, additional_time: float, replace_ttl: bool = False) -> bool:
        del additional_time, replace_ttl
        if not self.owned():
            return False
        self.client.lock_extensions += 1
        return True


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.fail_ping = False
        self.fail_set = False
        self.fail_lock = False
        self.locked_names: set[str] = set()
        self.lock_acquisitions = 0
        self.lock_extensions = 0
        self.expire_lock = False
        self.published: list[tuple[str, str]] = []

    def ping(self) -> bool:
        if self.fail_ping:
            raise ConnectionError("redis unavailable")
        return True

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def set(self, key: str, value: str) -> bool:
        if self.fail_set:
            raise ConnectionError("redis write failed")
        self.data[key] = value
        return True

    def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        return 0

    def pubsub(self, **kwargs):
        del kwargs
        raise NotImplementedError

    def lock(
        self,
        name: str,
        timeout: float | None = None,
        blocking_timeout: float | None = None,
    ) -> FakeRedisLock:
        del timeout, blocking_timeout
        return FakeRedisLock(self, name)


def robot(robot_id: str = "robot-01") -> Robot:
    return Robot(
        robot_id=robot_id,
        robot_type="MOBILE",
        control_adapter="mock-mobile-adapter",
        status="IDLE",
        pose=Pose(
            frame_id="map",
            position=Vector3(x=0, y=0, z=0),
        ),
        velocity=Vector3(x=0, y=0, z=0),
        motion_limits=MotionLimits(max_speed=1.0, max_acceleration=0.5),
    )


class RedisStoreTest(unittest.TestCase):
    def test_restores_state_after_backend_restart(self) -> None:
        client = FakeRedis()
        first = RedisStore("redis://unused", client=client)
        with first.lock:
            first.robots["robot-01"] = robot()
            first.task_idempotency["request-1"] = ("task-1", "fingerprint-1")
            first.proposal_idempotency[("task-1", "proposal-request-1")] = (
                "proposal-1",
                "fingerprint-2",
            )
            first.estop_hazard_ids.add("hazard-1")

        restarted = RedisStore("redis://unused", client=client)

        self.assertEqual(restarted.robots["robot-01"].robot_id, "robot-01")
        self.assertEqual(
            restarted.task_idempotency["request-1"],
            ("task-1", "fingerprint-1"),
        )
        self.assertEqual(
            restarted.proposal_idempotency[("task-1", "proposal-request-1")],
            ("proposal-1", "fingerprint-2"),
        )
        self.assertEqual(restarted.estop_hazard_ids, {"hazard-1"})

    def test_reloads_latest_snapshot_before_each_outer_transaction(self) -> None:
        client = FakeRedis()
        first = RedisStore("redis://unused", client=client)
        second = RedisStore("redis://unused", client=client)

        with first.lock:
            first.robots["robot-01"] = robot()

        self.assertEqual(second.robots, {})
        with second.lock:
            self.assertIn("robot-01", second.robots)

    def test_nested_lock_uses_one_distributed_transaction(self) -> None:
        client = FakeRedis()
        store = RedisStore("redis://unused", client=client)
        initial_acquisitions = client.lock_acquisitions

        with store.lock:
            with store.lock:
                store.robots["robot-01"] = robot()

        self.assertEqual(client.lock_acquisitions, initial_acquisitions + 1)

    def test_failed_write_rolls_back_memory_and_persistent_state(self) -> None:
        client = FakeRedis()
        store = RedisStore("redis://unused", client=client)
        client.fail_set = True

        with self.assertRaises(StoreUnavailableError):
            with store.lock:
                store.robots["robot-01"] = robot()

        self.assertEqual(store.robots, {})
        client.fail_set = False
        restarted = RedisStore("redis://unused", client=client)
        self.assertEqual(restarted.robots, {})

    def test_expired_lock_never_overwrites_persistent_state(self) -> None:
        client = FakeRedis()
        store = RedisStore("redis://unused", client=client)

        with self.assertRaisesRegex(StoreUnavailableError, "잠금"):
            with store.lock:
                store.robots["robot-01"] = robot()
                client.expire_lock = True

        self.assertEqual(store.robots, {})
        self.assertNotIn("safety-centric-vla:state:v1", client.data)

    def test_owned_lock_is_extended_before_state_save(self) -> None:
        client = FakeRedis()
        store = RedisStore("redis://unused", client=client)

        with store.lock:
            store.robots["robot-01"] = robot()

        self.assertEqual(client.lock_extensions, 1)

    def test_estop_publish_uses_latest_shared_state(self) -> None:
        client = FakeRedis()
        first = RedisStore("redis://unused", client=client)
        second = RedisStore("redis://unused", client=client)
        with first.lock:
            first.estop_hazard_ids.add("hazard-1")

        published: list[bool] = []
        second.synchronize_safety_publish(published.append)

        self.assertEqual(published, [True])

    def test_unavailable_redis_fails_before_entering_transaction(self) -> None:
        client = FakeRedis()
        store = RedisStore("redis://unused", client=client)
        client.fail_ping = True

        with self.assertRaises(StoreUnavailableError):
            with store.lock:
                self.fail("Redis 연결 실패 후에 요청 본문을 실행했습니다.")

    def test_fastapi_returns_503_when_redis_is_unavailable(self) -> None:
        client = FakeRedis()
        redis_store = RedisStore("redis://unused", client=client)
        client.fail_ping = True
        original_store = main_module.store
        previous_estop_count = len(main_module.action_dispatcher.estop_commands)
        main_module.store = redis_store
        try:
            response = TestClient(main_module.app).get("/robots")
        finally:
            main_module.store = original_store

        self.assertEqual(response.status_code, 503, response.text)
        self.assertIn("Redis", response.json()["detail"])
        self.assertEqual(
            len(main_module.action_dispatcher.estop_commands),
            previous_estop_count + 1,
        )
        self.assertTrue(main_module.action_dispatcher.estop_commands[-1]["active"])

    def test_invalid_snapshot_prevents_startup(self) -> None:
        client = FakeRedis()
        client.data["safety-centric-vla:state:v1"] = "{not-json"

        with self.assertRaises(StoreUnavailableError):
            RedisStore("redis://unused", client=client)


@unittest.skipUnless(os.getenv("REDIS_TEST_URL"), "실제 Redis 통합 환경에서만 실행")
class RealRedisStoreIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from redis import Redis

        cls.redis_url = os.environ["REDIS_TEST_URL"]
        cls.client = Redis.from_url(cls.redis_url, decode_responses=True)
        cls.client.ping()

    def setUp(self) -> None:
        self.key = f"safety-centric-vla:test:{uuid4().hex}"

    def tearDown(self) -> None:
        self.client.delete(
            self.key,
            f"{self.key}:lock",
            f"{self.key}:estop-publish-lock",
        )

    def test_restart_recovery_concurrent_writes_and_latency(self) -> None:
        first = RedisStore(self.redis_url, key=self.key)
        second = RedisStore(self.redis_url, key=self.key)

        threads = [
            threading.Thread(
                target=lambda target, robot_id: self._write_robot(target, robot_id),
                args=(first if index % 2 == 0 else second, f"robot-{index}"),
            )
            for index in range(10)
        ]
        started = time.perf_counter()
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        elapsed_ms = (time.perf_counter() - started) * 1000

        restarted = RedisStore(self.redis_url, key=self.key)
        with restarted.lock:
            self.assertEqual(set(restarted.robots), {f"robot-{i}" for i in range(10)})
        self.assertLess(elapsed_ms, 5000)

    def test_pubsub_notifies_other_backend_instance(self) -> None:
        first = RedisStore(self.redis_url, key=self.key)
        second = RedisStore(self.redis_url, key=self.key)
        notified = threading.Event()
        second.start_change_notifications(notified.set)
        try:
            # Redis subscribe thread가 구독 명령을 처리할 시간을 준다.
            time.sleep(0.2)
            with first.lock:
                first.robots["robot-01"] = robot()
            self.assertTrue(notified.wait(2), "다른 Backend의 Redis 변경 알림을 받지 못했습니다.")
        finally:
            second.stop_change_notifications()

    def test_shared_estop_state_is_recovered_for_publish(self) -> None:
        first = RedisStore(self.redis_url, key=self.key)
        with first.lock:
            first.estop_hazard_ids.add("hazard-1")

        restarted = RedisStore(self.redis_url, key=self.key)
        published: list[bool] = []
        restarted.synchronize_safety_publish(published.append)

        self.assertEqual(published, [True])

    def test_action_submission_and_validation_with_real_redis(self) -> None:
        redis_store = RedisStore(self.redis_url, key=self.key)
        original_store = main_module.store
        main_module.store = redis_store
        try:
            main_module.seed_rules()
            client = TestClient(main_module.app)
            started = time.perf_counter()
            response = client.post(
                "/robots",
                json=robot().model_dump(mode="json"),
            )
            self.assertEqual(response.status_code, 201, response.text)
            task = client.post(
                "/task-orders/from-natural-language",
                json={"instruction": "robot-01 로봇을 A구역으로 이동해"},
            )
            self.assertEqual(task.status_code, 201, task.text)
            proposal = client.post(
                f"/task-orders/{task.json()['task_id']}/action-proposals",
                json={},
            )
            self.assertEqual(proposal.status_code, 201, proposal.text)
            evaluated = client.post(
                f"/action-proposals/{proposal.json()['proposal_id']}/evaluate",
            )
            self.assertEqual(evaluated.status_code, 200, evaluated.text)
            self.assertEqual(evaluated.json()["status"], "APPROVED")
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.assertLess(elapsed_ms, 5000)
        finally:
            main_module.store = original_store

    @staticmethod
    def _write_robot(store: RedisStore, robot_id: str) -> None:
        with store.lock:
            store.robots[robot_id] = robot(robot_id)


if __name__ == "__main__":
    unittest.main()
