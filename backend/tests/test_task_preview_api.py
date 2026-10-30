import unittest

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


class TaskOrderPreviewApiTest(unittest.TestCase):
    def setUp(self) -> None:
        store.reset()
        seed_rules()
        self.client = TestClient(app)
        response = self.client.post("/robots", json=ROBOT)
        self.assertEqual(response.status_code, 201, response.text)

    def preview(self, instruction: str):
        return self.client.post(
            "/task-orders/preview",
            json={"instruction": instruction},
        )

    def batch_preview(self, items: list[dict[str, str]]):
        return self.client.post(
            "/task-orders/preview/batch",
            json={"items": items},
        )

    def test_previews_move_without_persisting_task(self) -> None:
        response = self.preview("robot-01 로봇을 A구역으로 이동해")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json(),
            {
                "instruction": "robot-01 로봇을 A구역으로 이동해",
                "target_robot_id": "robot-01",
                "target_object_id": None,
                "destination": {"type": "ZONE", "zone_id": "A구역"},
                "action_type": "MOVE_TO",
            },
        )
        self.assertEqual(store.tasks, {})

    def test_previews_manipulation_and_coordinate_frame(self) -> None:
        manipulate = self.preview("robot-01 로봇으로 상자3을 B작업대로 옮겨")
        coordinate = self.client.post(
            "/task-orders/preview",
            json={
                "instruction": "robot-01 로봇을 좌표 (1, 2)로 이동해",
                "frame_id": "odom",
            },
        )

        self.assertEqual(manipulate.status_code, 200, manipulate.text)
        self.assertEqual(manipulate.json()["action_type"], "MANIPULATE")
        self.assertEqual(manipulate.json()["target_object_id"], "상자3")
        self.assertEqual(coordinate.status_code, 200, coordinate.text)
        self.assertEqual(coordinate.json()["destination"]["frame_id"], "odom")
        self.assertEqual(store.tasks, {})

    def test_returns_machine_readable_parse_error_codes(self) -> None:
        cases = {
            "robot-99 로봇을 A구역으로 이동해": "ROBOT_NOT_FOUND",
            "robot-01 로봇 이동해": "DESTINATION_REQUIRED",
            "robot-01 로봇을 A구역으로 B구역까지 이동해": "DESTINATION_AMBIGUOUS",
            "robot-01 로봇을 정지해": "UNSUPPORTED_CONTROL_ACTION",
            "robot-01 로봇을 A구역으로 이동할까?": "INVALID_INSTRUCTION",
        }

        for instruction, expected_code in cases.items():
            with self.subTest(instruction=instruction):
                response = self.preview(instruction)
                self.assertEqual(response.status_code, 422, response.text)
                detail = response.json()["detail"]
                self.assertEqual(detail["code"], expected_code)
                self.assertTrue(detail["message"])
        self.assertEqual(store.tasks, {})

    def test_existing_create_endpoint_keeps_string_error_detail(self) -> None:
        response = self.client.post(
            "/task-orders/from-natural-language",
            json={"instruction": "robot-01 로봇 이동해"},
        )

        self.assertEqual(response.status_code, 422, response.text)
        self.assertIsInstance(response.json()["detail"], str)

    def test_batch_preview_returns_mixed_results_without_persistence(self) -> None:
        response = self.batch_preview(
            [
                {"instruction": "robot-01 로봇을 A구역으로 이동해"},
                {"instruction": "robot-99 로봇을 B구역으로 이동해"},
                {"instruction": "robot-01 로봇으로 상자3을 B작업대로 옮겨"},
            ]
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(
            (body["total"], body["valid_count"], body["invalid_count"]),
            (3, 2, 1),
        )
        self.assertEqual([result["index"] for result in body["results"]], [0, 1, 2])
        self.assertEqual(body["results"][0]["preview"]["action_type"], "MOVE_TO")
        self.assertEqual(body["results"][1]["error"]["code"], "ROBOT_NOT_FOUND")
        self.assertEqual(body["results"][2]["preview"]["action_type"], "MANIPULATE")
        self.assertEqual(store.tasks, {})

    def test_batch_preview_preserves_item_options_and_order(self) -> None:
        response = self.batch_preview(
            [
                {
                    "instruction": "robot-01 로봇을 좌표 (1, 2)로 이동해",
                    "frame_id": "odom",
                },
                {"instruction": "robot-01 로봇을 정지해"},
            ]
        )

        self.assertEqual(response.status_code, 200, response.text)
        results = response.json()["results"]
        self.assertEqual(results[0]["preview"]["destination"]["frame_id"], "odom")
        self.assertEqual(results[1]["error"]["code"], "UNSUPPORTED_CONTROL_ACTION")
        self.assertEqual(store.tasks, {})

    def test_batch_preview_reports_all_invalid_items(self) -> None:
        response = self.batch_preview(
            [
                {"instruction": "robot-01 로봇 이동해"},
                {"instruction": "robot-01 로봇을 A구역으로 이동할까?"},
            ]
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["valid_count"], 0)
        self.assertEqual(response.json()["invalid_count"], 2)
        self.assertTrue(all(not result["valid"] for result in response.json()["results"]))

    def test_batch_preview_enforces_one_to_fifty_items(self) -> None:
        item = {"instruction": "robot-01 로봇을 A구역으로 이동해"}

        empty = self.batch_preview([])
        maximum = self.batch_preview([item] * 50)
        oversized = self.batch_preview([item] * 51)

        self.assertEqual(empty.status_code, 422, empty.text)
        self.assertEqual(maximum.status_code, 200, maximum.text)
        self.assertEqual(maximum.json()["total"], 50)
        self.assertEqual(oversized.status_code, 422, oversized.text)
        self.assertEqual(store.tasks, {})


if __name__ == "__main__":
    unittest.main()
