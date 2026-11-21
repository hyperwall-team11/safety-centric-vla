import unittest
from datetime import timedelta
from threading import Event
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import main
from app.ctl_bridge import (
    CommandSerializationError,
    DispatchError,
    Ros2ActionDispatcher,
    build_approved_action_command,
    result_event_to_report,
    result_topic,
)
from app.main import app, seed_rules
from app.models import ActionProposal, AdapterAckEvent, Robot, Zone, utc_now
from app.store import store


def robot() -> Robot:
    return Robot.model_validate({
        "robot_id": "robot-01",
        "robot_type": "MOBILE",
        "control_adapter": "tiago-adapter",
        "status": "IDLE",
        "pose": {
            "frame_id": "world",
            "position": {"x": 0, "y": 0, "z": 0},
        },
        "velocity": {"x": 0, "y": 0, "z": 0},
        "motion_limits": {"max_speed": 0.3, "max_acceleration": 0.5},
    })


def proposal(target_pose: dict, speed: float = 0.4) -> ActionProposal:
    return ActionProposal.model_validate({
        "proposal_id": "proposal-1",
        "task_id": "task-1",
        "robot_id": "robot-01",
        "action_type": "MOVE_TO",
        "parameters": {"target_pose": target_pose, "speed_limit": speed},
        "trajectory": None,
        "frame_id": "world",
        "speed_limit": speed,
        "model_name": "rule-based-action-planner",
        "model_version": "0.2.0",
        "status": "APPROVED",
    })


class RecordingDispatcher:
    name = "recording"

    def __init__(self) -> None:
        self.commands = []

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def dispatch(self, proposal, robot, zones):
        command = build_approved_action_command(proposal, robot, zones)
        self.commands.append(command)
        return command


class FailingDispatcher(RecordingDispatcher):
    name = "failing"

    def dispatch(self, proposal, robot, zones):
        raise DispatchError("ROS 2 publish 실패")


class BackendCtlBridgeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.original_dispatcher = main.action_dispatcher

    def tearDown(self) -> None:
        main.action_dispatcher = self.original_dispatcher

    def test_coordinate_is_serialized_as_ctl_position_array(self) -> None:
        action = proposal({
            "type": "COORDINATE",
            "frame_id": "world",
            "position": {"x": 1.2, "y": -0.8, "z": 0},
        })

        command = build_approved_action_command(action, robot(), [])

        self.assertEqual(command["contract_version"], "backend-ctl/0.1")
        self.assertEqual(command["status"], "APPROVED")
        self.assertEqual(
            command["parameters"]["target_pose"],
            {"frame_id": "world", "position": [1.2, -0.8, 0.0]},
        )
        self.assertEqual(command["speed_limit"], 0.3)
        self.assertEqual(command["parameters"]["speed_limit"], 0.3)
        self.assertEqual(action.status, "APPROVED")
        self.assertIsInstance(action.parameters["target_pose"]["position"], dict)

    def test_zone_is_resolved_to_navigation_target(self) -> None:
        zone = Zone.model_validate({
            "zone_id": "A구역",
            "zone_type": "WORK",
            "geometry": {
                "type": "polygon",
                "points": [[0, 0], [1, 0], [1, 1]],
                "navigation_target": {"x": 0.5, "y": 0.5, "z": 0},
            },
            "frame_id": "map",
            "minimum_safety_distance": 1.0,
        })

        command = build_approved_action_command(
            proposal({"type": "ZONE", "zone_id": "A구역"}),
            robot(),
            [zone],
        )

        self.assertEqual(
            command["parameters"]["target_pose"],
            {"frame_id": "map", "position": [0.5, 0.5, 0.0]},
        )

    def test_zone_without_navigation_target_is_blocked(self) -> None:
        zone = Zone.model_validate({
            "zone_id": "A구역",
            "zone_type": "WORK",
            "geometry": {"type": "polygon", "points": [[0, 0], [1, 0], [1, 1]]},
            "frame_id": "map",
            "minimum_safety_distance": 1.0,
        })
        with self.assertRaises(CommandSerializationError):
            build_approved_action_command(
                proposal({"type": "ZONE", "zone_id": "A구역"}),
                robot(),
                [zone],
            )

    def test_ctl_failure_codes_are_normalized(self) -> None:
        report = result_event_to_report({
            "kind": "RESULT",
            "success": False,
            "reason_code": "NO_HEARTBEAT",
            "message": "검증 서버 하트비트 없음",
        })
        self.assertEqual(report.status, "FAILED")
        self.assertEqual(report.reason_code, "TIMEOUT")
        self.assertEqual(report.detail, "[NO_HEARTBEAT] 검증 서버 하트비트 없음")

    def test_success_result_is_converted(self) -> None:
        report = result_event_to_report({
            "kind": "RESULT",
            "success": True,
            "reason_code": None,
            "message": "목표 도달",
        })
        self.assertEqual(report.model_dump(exclude_none=True), {
            "status": "SUCCEEDED",
            "detail": "목표 도달",
        })

    def test_topic_segment_preserves_json_robot_id_rule(self) -> None:
        self.assertEqual(result_topic("robot-01"), "/adapter/robot_01/result")
        self.assertEqual(result_topic("01-arm"), "/adapter/r_01_arm/result")

    def test_ros_bridge_delivers_valid_ack_event(self) -> None:
        received = []
        delivered = Event()

        def record_ack(ack, _received_at):
            received.append(ack)
            delivered.set()

        dispatcher = Ros2ActionDispatcher(ack_event_handler=record_ack)
        dispatcher._proposal_robots["proposal-1"] = "robot-01"
        message = SimpleNamespace(data=(
            '{"kind":"ACK","robot_id":"robot-01","proposal_id":"proposal-1",'
            '"timestamp":"2026-11-15T00:00:00Z"}'
        ))

        dispatcher._on_result(message)

        self.assertTrue(delivered.wait(1))
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].proposal_id, "proposal-1")

    def test_ros_bridge_ignores_ack_with_wrong_robot_or_timestamp(self) -> None:
        received = []
        dispatcher = Ros2ActionDispatcher(
            ack_event_handler=lambda ack, _received_at: received.append(ack)
        )
        dispatcher._proposal_robots["proposal-1"] = "robot-01"

        dispatcher._on_result(SimpleNamespace(data=(
            '{"kind":"ACK","robot_id":"robot-02","proposal_id":"proposal-1",'
            '"timestamp":"2026-11-15T00:00:00Z"}'
        )))
        dispatcher._on_result(SimpleNamespace(data=(
            '{"kind":"ACK","robot_id":"robot-01","proposal_id":"proposal-1",'
            '"timestamp":"not-a-time"}'
        )))

        self.assertEqual(received, [])

    def test_publish_failure_does_not_transition_execution_state(self) -> None:
        store.reset()
        seed_rules()
        client = TestClient(app)
        self.assertEqual(
            client.post("/robots", json=robot().model_dump(mode="json")).status_code,
            201,
        )
        task = client.post(
            "/task-orders/from-natural-language",
            json={"instruction": "robot-01 로봇을 좌표 (1, 2, 0)로 이동해", "frame_id": "world"},
        ).json()
        action = client.post(
            f"/task-orders/{task['task_id']}/action-proposals",
            json={"speed_limit": 0.3},
        ).json()
        approved = client.post(f"/action-proposals/{action['proposal_id']}/evaluate")
        self.assertEqual(approved.json()["status"], "APPROVED")
        log_count = len(store.logs)
        main.action_dispatcher = FailingDispatcher()

        response = client.post(f"/action-proposals/{action['proposal_id']}/execute")

        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(store.proposals[action["proposal_id"]].status, "APPROVED")
        self.assertEqual(store.tasks[task["task_id"]].status, "PLANNING")
        self.assertEqual(store.robots["robot-01"].status, "IDLE")
        self.assertEqual(len(store.logs), log_count)
        self.assertNotIn(action["proposal_id"], store.proposal_acks)

    def test_execute_dispatches_approved_snapshot_before_state_change(self) -> None:
        store.reset()
        seed_rules()
        client = TestClient(app)
        self.assertEqual(
            client.post("/robots", json=robot().model_dump(mode="json")).status_code,
            201,
        )
        task = client.post(
            "/task-orders/from-natural-language",
            json={"instruction": "robot-01 로봇을 좌표 (1, 2, 0)로 이동해", "frame_id": "world"},
        ).json()
        action = client.post(
            f"/task-orders/{task['task_id']}/action-proposals",
            json={"speed_limit": 0.3},
        ).json()
        client.post(f"/action-proposals/{action['proposal_id']}/evaluate")
        dispatcher = RecordingDispatcher()
        main.action_dispatcher = dispatcher

        response = client.post(f"/action-proposals/{action['proposal_id']}/execute")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "EXECUTING")
        self.assertEqual(dispatcher.commands[0]["status"], "APPROVED")
        self.assertEqual(
            dispatcher.commands[0]["parameters"]["target_pose"]["position"],
            [1.0, 2.0, 0.0],
        )
        ack = client.get(f"/action-proposals/{action['proposal_id']}/ack")
        self.assertEqual(ack.status_code, 200, ack.text)
        self.assertEqual(ack.json()["status"], "PENDING")

    def test_ack_is_recorded_once_and_duplicate_count_is_incremented(self) -> None:
        store.reset()
        seed_rules()
        client = TestClient(app)
        self.assertEqual(
            client.post("/robots", json=robot().model_dump(mode="json")).status_code,
            201,
        )
        task = client.post(
            "/task-orders/from-natural-language",
            json={"instruction": "robot-01 로봇을 좌표 (1, 2, 0)로 이동해", "frame_id": "world"},
        ).json()
        action = client.post(
            f"/task-orders/{task['task_id']}/action-proposals",
            json={"speed_limit": 0.3},
        ).json()
        client.post(f"/action-proposals/{action['proposal_id']}/evaluate")
        main.action_dispatcher = RecordingDispatcher()
        client.post(f"/action-proposals/{action['proposal_id']}/execute")
        ack = AdapterAckEvent(
            proposal_id=action["proposal_id"],
            robot_id="robot-01",
            timestamp=utc_now(),
        )

        main.apply_ctl_ack_event(ack)
        first = client.get(f"/action-proposals/{action['proposal_id']}/ack").json()
        main.apply_ctl_ack_event(ack)
        duplicate = client.get(f"/action-proposals/{action['proposal_id']}/ack").json()

        self.assertEqual(first["status"], "ACKNOWLEDGED")
        self.assertIsNotNone(first["command_to_ack_ms"])
        self.assertIsNotNone(first["dispatch_to_ack_ms"])
        self.assertEqual(first["ack_received_at"], duplicate["ack_received_at"])
        self.assertEqual(duplicate["duplicate_count"], 1)

    def test_ack_query_marks_missing_ack_as_timed_out(self) -> None:
        store.reset()
        seed_rules()
        client = TestClient(app)
        self.assertEqual(
            client.post("/robots", json=robot().model_dump(mode="json")).status_code,
            201,
        )
        task = client.post(
            "/task-orders/from-natural-language",
            json={"instruction": "robot-01 로봇을 좌표 (1, 2, 0)로 이동해", "frame_id": "world"},
        ).json()
        action = client.post(
            f"/task-orders/{task['task_id']}/action-proposals",
            json={"speed_limit": 0.3},
        ).json()
        client.post(f"/action-proposals/{action['proposal_id']}/evaluate")
        main.action_dispatcher = RecordingDispatcher()
        client.post(f"/action-proposals/{action['proposal_id']}/execute")
        store.proposal_acks[action["proposal_id"]].timeout_at = utc_now() - timedelta(seconds=1)

        response = client.get(f"/action-proposals/{action['proposal_id']}/ack")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "TIMED_OUT")
        main.apply_ctl_ack_event(AdapterAckEvent(
            proposal_id=action["proposal_id"],
            robot_id="robot-01",
            timestamp=utc_now(),
        ))
        late_ack = client.get(f"/action-proposals/{action['proposal_id']}/ack").json()
        self.assertEqual(late_ack["status"], "TIMED_OUT")
        self.assertIsNotNone(late_ack["adapter_ack_at"])
        self.assertIsNotNone(late_ack["ack_received_at"])
        self.assertIsNotNone(late_ack["command_to_ack_ms"])
        self.assertIsNotNone(late_ack["dispatch_to_ack_ms"])

    def test_ctl_estop_event_holds_active_execution(self) -> None:
        store.reset()
        seed_rules()
        client = TestClient(app)
        self.assertEqual(
            client.post("/robots", json=robot().model_dump(mode="json")).status_code,
            201,
        )
        task = client.post(
            "/task-orders/from-natural-language",
            json={"instruction": "robot-01 로봇을 좌표 (1, 2, 0)로 이동해", "frame_id": "world"},
        ).json()
        action = client.post(
            f"/task-orders/{task['task_id']}/action-proposals",
            json={"speed_limit": 0.3},
        ).json()
        client.post(f"/action-proposals/{action['proposal_id']}/evaluate")
        main.action_dispatcher = RecordingDispatcher()
        client.post(f"/action-proposals/{action['proposal_id']}/execute")

        main.apply_ctl_safety_event({
            "kind": "ESTOP",
            "robot_id": "robot-01",
            "proposal_id": action["proposal_id"],
            "triggered_by": "OPERATOR",
        })

        self.assertEqual(store.proposals[action["proposal_id"]].status, "HELD")
        self.assertEqual(store.tasks[task["task_id"]].status, "HELD")
        self.assertEqual(store.robots["robot-01"].status, "ESTOP")
        self.assertEqual(store.logs[-1].decision, "ESTOP")
        self.assertEqual(store.logs[-1].triggered_by, "OPERATOR")


if __name__ == "__main__":
    unittest.main()
