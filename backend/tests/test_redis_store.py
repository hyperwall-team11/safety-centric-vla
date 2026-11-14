from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

import app.main as main_module
from app.models import MotionLimits, Pose, Robot, Vector3
from app.store import RedisStore, StoreUnavailableError


class FakeRedisLock:
    def __init__(self, client: FakeRedis) -> None:
        self.client = client
        self.acquired = False

    def acquire(
        self,
        blocking: bool = True,
        blocking_timeout: float | None = None,
    ) -> bool:
        del blocking, blocking_timeout
        if self.client.fail_lock or self.client.locked:
            return False
        self.client.locked = True
        self.client.lock_acquisitions += 1
        self.acquired = True
        return True

    def release(self) -> None:
        if not self.acquired:
            raise RuntimeError("획득하지 않은 잠금입니다.")
        self.client.locked = False
        self.acquired = False


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.fail_ping = False
        self.fail_set = False
        self.fail_lock = False
        self.locked = False
        self.lock_acquisitions = 0

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

    def lock(
        self,
        name: str,
        timeout: float | None = None,
        blocking_timeout: float | None = None,
    ) -> FakeRedisLock:
        del name, timeout, blocking_timeout
        return FakeRedisLock(self)


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


if __name__ == "__main__":
    unittest.main()
