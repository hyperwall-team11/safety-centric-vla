import unittest

from fastapi.testclient import TestClient

from app.main import app, seed_rules
from app.store import store


def robot_payload(
    robot_id: str,
    *,
    max_speed: float = 1.0,
) -> dict:
    return {
        "robot_id": robot_id,
        "robot_type": "MOBILE",
        "control_adapter": "mock-mobile-adapter",
        "status": "IDLE",
        "pose": {
            "frame_id": "map",
            "position": {"x": 0, "y": 0, "z": 0},
            "orientation": {"x": 0, "y": 0, "z": 0, "w": 1},
        },
        "velocity": {"x": 0, "y": 0, "z": 0},
        "motion_limits": {"max_speed": max_speed, "max_acceleration": 0.5},
    }


class ActionPlannerApiTest(unittest.TestCase):
    def setUp(self) -> None:
        store.reset()
        seed_rules()
        self.client = TestClient(app)
        response = self.client.post("/robots", json=robot_payload("robot-01"))
        self.assertEqual(response.status_code, 201, response.text)

    def create_task(
        self,
        instruction: str = "robot-01 로봇을 A구역으로 이동해",
        **request_fields,
    ) -> dict:
        response = self.client.post(
            "/task-orders/from-natural-language",
            json={"instruction": instruction, **request_fields},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def generate(
        self,
        task_id: str,
        body: dict | None = None,
        idempotency_key: str | None = None,
    ):
        headers = (
            {"Idempotency-Key": idempotency_key}
            if idempotency_key is not None
            else None
        )
        return self.client.post(
            f"/task-orders/{task_id}/action-proposals",
            json=body or {},
            headers=headers,
        )

    def test_generates_move_to_and_transitions_task_to_planning(self) -> None:
        task = self.create_task()
        response = self.generate(task["task_id"])
        self.assertEqual(response.status_code, 201, response.text)

        proposal = response.json()
        self.assertEqual(proposal["task_id"], task["task_id"])
        self.assertEqual(proposal["robot_id"], "robot-01")
        self.assertEqual(proposal["action_type"], "MOVE_TO")
        self.assertEqual(proposal["status"], "PROPOSED")
        self.assertTrue(proposal["proposal_id"].startswith("proposal-"))
        self.assertTrue(proposal["proposed_at"].endswith("Z"))
        self.assertIsNone(proposal["trajectory"])
        self.assertEqual(proposal["speed_limit"], 0.4)
        self.assertEqual(proposal["model_name"], "rule-based-action-planner")
        self.assertEqual(proposal["model_version"], "0.2.0")
        self.assertEqual(
            proposal["parameters"],
            {
                "target_pose": {"type": "ZONE", "zone_id": "A구역"},
                "speed_limit": 0.4,
            },
        )
        stored_task = self.client.get(f"/task-orders/{task['task_id']}").json()
        self.assertEqual(stored_task["status"], "PLANNING")
        self.assertEqual(store.logs, [])

    def test_coordinate_frame_and_explicit_speed_are_preserved(self) -> None:
        task = self.create_task(
            "robot-01 로봇을 좌표 (1, 2, 0)로 이동해",
            frame_id="odom",
        )
        response = self.generate(task["task_id"], {"speed_limit": 0.3})
        self.assertEqual(response.status_code, 201, response.text)
        proposal = response.json()
        self.assertEqual(proposal["frame_id"], "odom")
        self.assertEqual(proposal["speed_limit"], 0.3)
        self.assertEqual(proposal["parameters"]["speed_limit"], 0.3)
        self.assertEqual(
            proposal["parameters"]["target_pose"]["position"],
            {"x": 1.0, "y": 2.0, "z": 0.0},
        )

    def test_generates_contract_aligned_manipulate_proposal(self) -> None:
        task = self.create_task("robot-01 로봇으로 상자 3을 B 작업대로 옮겨")
        response = self.generate(task["task_id"], {"manipulation": "PLACE"})
        self.assertEqual(response.status_code, 201, response.text)
        proposal = response.json()
        self.assertEqual(proposal["action_type"], "MANIPULATE")
        self.assertEqual(
            proposal["parameters"],
            {
                "manipulation": "PLACE",
                "object_id": "상자3",
                "target_pose": {"type": "ZONE", "zone_id": "B작업대"},
            },
        )

    def test_requires_explicit_manipulation_for_object_task(self) -> None:
        task = self.create_task("robot-01 로봇으로 상자 3을 B 작업대로 옮겨")
        response = self.generate(task["task_id"])
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("PICK 또는 PLACE", response.json()["detail"])
        self.assertEqual(store.tasks[task["task_id"]].status, "CREATED")
        self.assertEqual(store.proposals, {})

    def test_rejects_manipulation_for_move_task(self) -> None:
        task = self.create_task()
        response = self.generate(task["task_id"], {"manipulation": "PICK"})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(store.tasks[task["task_id"]].status, "CREATED")
        self.assertEqual(store.proposals, {})

    def test_default_speed_is_capped_by_robot_motion_limit(self) -> None:
        store.reset()
        seed_rules()
        self.assertEqual(
            self.client.post(
                "/robots",
                json=robot_payload("robot-01", max_speed=0.2),
            ).status_code,
            201,
        )
        task = self.create_task()
        response = self.generate(task["task_id"])
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["speed_limit"], 0.2)
        self.assertEqual(response.json()["parameters"]["speed_limit"], 0.2)

    def test_idempotent_replay_returns_same_proposal(self) -> None:
        task = self.create_task()
        first = self.generate(task["task_id"], {"speed_limit": 0.4}, "request-1")
        self.assertEqual(first.status_code, 201, first.text)
        replay = self.generate(task["task_id"], {"speed_limit": 0.4}, "request-1")
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["proposal_id"], first.json()["proposal_id"])
        self.assertEqual(len(store.proposals), 1)

        changed_body = self.generate(task["task_id"], {"speed_limit": 0.3}, "request-1")
        self.assertEqual(changed_body.status_code, 409)
        self.assertEqual(len(store.proposals), 1)

    def test_rejects_invalid_idempotency_key(self) -> None:
        task = self.create_task()
        self.assertEqual(self.generate(task["task_id"], idempotency_key="   ").status_code, 422)
        self.assertEqual(self.generate(task["task_id"], idempotency_key="x" * 129).status_code, 422)
        self.assertEqual(store.tasks[task["task_id"]].status, "CREATED")
        self.assertEqual(store.proposals, {})

    def test_rejects_second_active_proposal(self) -> None:
        task = self.create_task()
        self.assertEqual(self.generate(task["task_id"]).status_code, 201)
        duplicate = self.generate(task["task_id"])
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(len(store.proposals), 1)

    def test_planning_failure_leaves_state_unchanged(self) -> None:
        task = self.create_task()
        stored_task = store.tasks[task["task_id"]]
        stored_task.instruction = "robot-01 로봇을 A구역으로 사진 찍어"
        before_logs = list(store.logs)

        response = self.generate(task["task_id"])
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(stored_task.status, "CREATED")
        self.assertEqual(store.proposals, {})
        self.assertEqual(store.logs, before_logs)

    def test_lists_only_proposals_for_requested_task(self) -> None:
        first_task = self.create_task("robot-01 로봇을 A구역으로 이동해")
        second_task = self.create_task("robot-01 로봇을 B구역으로 이동해")
        first = self.generate(first_task["task_id"])
        second = self.generate(second_task["task_id"])
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)

        listed = self.client.get(
            f"/task-orders/{first_task['task_id']}/action-proposals"
        )
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()), 1)
        self.assertEqual(listed.json()[0]["proposal_id"], first.json()["proposal_id"])

    def test_manual_proposal_rejects_task_robot_mismatch(self) -> None:
        self.assertEqual(
            self.client.post("/robots", json=robot_payload("robot-02")).status_code,
            201,
        )
        task = self.create_task()
        response = self.client.post(
            "/action-proposals",
            json={
                "task_id": task["task_id"],
                "robot_id": "robot-02",
                "action_type": "MOVE_TO",
                "parameters": {
                    "target_pose": {"type": "ZONE", "zone_id": "A구역"},
                    "speed_limit": 0.5,
                },
                "trajectory": None,
                "frame_id": "map",
                "speed_limit": 0.5,
                "model_name": "external-vla",
                "model_version": "0.1.0",
            },
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(store.tasks[task["task_id"]].status, "CREATED")
        self.assertEqual(store.proposals, {})

    def test_rejects_invalid_action_parameter_contracts(self) -> None:
        task = self.create_task()
        base = {
            "task_id": task["task_id"],
            "robot_id": "robot-01",
            "trajectory": None,
            "frame_id": "map",
            "speed_limit": 0.5,
            "model_name": "external-vla",
            "model_version": "0.1.0",
        }
        missing_target = self.client.post(
            "/action-proposals",
            json={**base, "action_type": "MOVE_TO", "parameters": {"speed_limit": 0.5}},
        )
        self.assertEqual(missing_target.status_code, 422, missing_target.text)
        mismatched_speed = self.client.post(
            "/action-proposals",
            json={
                **base,
                "action_type": "MOVE_TO",
                "parameters": {
                    "target_pose": {"type": "ZONE", "zone_id": "A구역"},
                    "speed_limit": 0.4,
                },
            },
        )
        self.assertEqual(mismatched_speed.status_code, 422, mismatched_speed.text)
        invalid_manipulate = self.client.post(
            "/action-proposals",
            json={**base, "action_type": "MANIPULATE", "parameters": {}},
        )
        self.assertEqual(invalid_manipulate.status_code, 422, invalid_manipulate.text)
        invalid_safety_priority = self.client.post(
            "/action-proposals",
            json={
                **base,
                "action_type": "MOVE_TO",
                "parameters": {
                    "target_pose": {"type": "ZONE", "zone_id": "A구역"},
                    "speed_limit": 0.5,
                    "priority": "SAFETY",
                },
            },
        )
        self.assertEqual(
            invalid_safety_priority.status_code,
            422,
            invalid_safety_priority.text,
        )
        self.assertEqual(store.tasks[task["task_id"]].status, "CREATED")
        self.assertEqual(store.proposals, {})

    def test_accepts_normal_priority_for_move_to(self) -> None:
        task = self.create_task()
        response = self.client.post(
            "/action-proposals",
            json={
                "task_id": task["task_id"],
                "robot_id": "robot-01",
                "action_type": "MOVE_TO",
                "parameters": {
                    "target_pose": {"type": "ZONE", "zone_id": "A구역"},
                    "speed_limit": 0.4,
                    "priority": "NORMAL",
                },
                "trajectory": None,
                "frame_id": "map",
                "speed_limit": 0.4,
                "model_name": "external-vla",
                "model_version": "0.1.0",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["parameters"]["priority"], "NORMAL")

    def test_invalid_generation_request_does_not_mutate_state(self) -> None:
        task = self.create_task()
        response = self.generate(task["task_id"], {"speed_limit": 0})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(store.tasks[task["task_id"]].status, "CREATED")
        self.assertEqual(store.proposals, {})

    def test_rejects_frame_override_without_coordinate_transform(self) -> None:
        task = self.create_task(
            "robot-01 로봇을 좌표 (1, 2)로 이동해",
            frame_id="map",
        )
        response = self.generate(task["task_id"], {"frame_id": "odom"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(store.tasks[task["task_id"]].status, "CREATED")
        self.assertEqual(store.proposals, {})

    def test_rejects_blank_coordinate_frame(self) -> None:
        response = self.client.post(
            "/task-orders/from-natural-language",
            json={
                "instruction": "robot-01 로봇을 좌표 (1, 2)로 이동해",
                "frame_id": "   ",
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(store.tasks, {})

    def test_held_task_requires_separate_resume_flow(self) -> None:
        task = self.create_task()
        store.tasks[task["task_id"]].status = "HELD"
        response = self.generate(task["task_id"])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(store.tasks[task["task_id"]].status, "HELD")
        self.assertEqual(store.proposals, {})

    def test_generated_proposal_passes_existing_evaluate_execute_flow(self) -> None:
        task = self.create_task()
        generated = self.generate(task["task_id"])
        self.assertEqual(generated.status_code, 201, generated.text)
        proposal_id = generated.json()["proposal_id"]

        evaluated = self.client.post(f"/action-proposals/{proposal_id}/evaluate")
        self.assertEqual(evaluated.status_code, 200, evaluated.text)
        self.assertEqual(evaluated.json()["status"], "APPROVED")
        executed = self.client.post(f"/action-proposals/{proposal_id}/execute")
        self.assertEqual(executed.status_code, 200, executed.text)
        self.assertEqual(executed.json()["status"], "EXECUTING")

    def test_replans_after_rejected_proposal(self) -> None:
        task = self.create_task()
        unsafe = self.generate(task["task_id"], {"speed_limit": 1.5})
        self.assertEqual(unsafe.status_code, 201, unsafe.text)
        evaluated = self.client.post(
            f"/action-proposals/{unsafe.json()['proposal_id']}/evaluate"
        )
        self.assertEqual(evaluated.status_code, 200, evaluated.text)
        self.assertEqual(evaluated.json()["status"], "REJECTED")

        replanned = self.generate(task["task_id"], {"speed_limit": 0.4})
        self.assertEqual(replanned.status_code, 201, replanned.text)
        self.assertNotEqual(
            replanned.json()["proposal_id"],
            unsafe.json()["proposal_id"],
        )
        self.assertEqual(len(store.proposals), 2)


if __name__ == "__main__":
    unittest.main()
