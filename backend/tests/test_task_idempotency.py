import unittest
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from app.main import app, seed_rules
from app.store import store


ROBOT = {
    "robot_id": "robot-01",
    "robot_type": "MOBILE",
    "control_adapter": "mock-mobile-adapter",
    "status": "IDLE",
    "pose": {
        "frame_id": "map",
        "position": {"x": 0, "y": 0, "z": 0},
        "orientation": {"x": 0, "y": 0, "z": 0, "w": 1},
    },
    "velocity": {"x": 0, "y": 0, "z": 0},
    "motion_limits": {"max_speed": 1.0, "max_acceleration": 0.5},
}


class TaskOrderIdempotencyTest(unittest.TestCase):
    def setUp(self) -> None:
        store.reset()
        seed_rules()
        self.client = TestClient(app)
        response = self.client.post("/robots", json=ROBOT)
        self.assertEqual(response.status_code, 201, response.text)

    def create_task(self, instruction: str, key: str):
        return self.client.post(
            "/task-orders/from-natural-language",
            json={"instruction": instruction},
            headers={"Idempotency-Key": key},
        )

    def test_replay_returns_existing_task_without_duplicate(self) -> None:
        first = self.create_task("robot-01 로봇을 A구역으로 이동해", " task-request-1 ")
        replay = self.create_task("robot-01 로봇을 A구역으로 이동해", "task-request-1")

        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["task_id"], first.json()["task_id"])
        self.assertEqual(first.headers["Idempotency-Replayed"], "false")
        self.assertEqual(replay.headers["Idempotency-Replayed"], "true")
        self.assertEqual(
            replay.headers["Location"],
            f"/task-orders/{first.json()['task_id']}",
        )
        self.assertEqual(len(store.tasks), 1)

    def test_openapi_documents_idempotency_response_headers(self) -> None:
        responses = app.openapi()["paths"]["/task-orders/from-natural-language"]["post"][
            "responses"
        ]
        for status_code in ("200", "201"):
            self.assertEqual(
                set(responses[status_code]["headers"]),
                {"Location", "Idempotency-Replayed"},
            )

    def test_same_key_with_different_body_returns_conflict(self) -> None:
        first = self.create_task("robot-01 로봇을 A구역으로 이동해", "task-request-1")
        conflict = self.create_task("robot-01 로봇을 B구역으로 이동해", "task-request-1")

        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(len(store.tasks), 1)

    def test_rejects_blank_or_oversized_key_without_mutation(self) -> None:
        blank = self.create_task("robot-01 로봇을 A구역으로 이동해", "   ")
        oversized = self.create_task(
            "robot-01 로봇을 A구역으로 이동해",
            "x" * 129,
        )

        self.assertEqual(blank.status_code, 422, blank.text)
        self.assertEqual(oversized.status_code, 422, oversized.text)
        self.assertEqual(store.tasks, {})
        self.assertEqual(store.task_idempotency, {})

    def test_failed_parse_does_not_reserve_key(self) -> None:
        failed = self.create_task("robot-01 로봇 움직여", "task-request-1")
        retried = self.create_task(
            "robot-01 로봇을 A구역으로 이동해",
            "task-request-1",
        )

        self.assertEqual(failed.status_code, 422, failed.text)
        self.assertEqual(retried.status_code, 201, retried.text)
        self.assertEqual(len(store.tasks), 1)

    def test_different_keys_create_independent_tasks(self) -> None:
        first = self.create_task("robot-01 로봇을 A구역으로 이동해", "task-request-1")
        second = self.create_task("robot-01 로봇을 A구역으로 이동해", "task-request-2")

        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(second.status_code, 201, second.text)
        self.assertNotEqual(first.json()["task_id"], second.json()["task_id"])
        self.assertEqual(len(store.tasks), 2)

    def test_concurrent_retries_create_exactly_one_task(self) -> None:
        def submit():
            with TestClient(app) as client:
                return client.post(
                    "/task-orders/from-natural-language",
                    json={"instruction": "robot-01 로봇을 A구역으로 이동해"},
                    headers={"Idempotency-Key": "concurrent-request"},
                )

        with ThreadPoolExecutor(max_workers=8) as executor:
            responses = list(executor.map(lambda _: submit(), range(8)))

        self.assertEqual(
            sorted(response.status_code for response in responses),
            [200] * 7 + [201],
        )
        self.assertEqual(
            {response.json()["task_id"] for response in responses},
            set(store.tasks),
        )
        self.assertEqual(len(store.tasks), 1)


if __name__ == "__main__":
    unittest.main()
