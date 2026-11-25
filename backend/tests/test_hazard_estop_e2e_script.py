import contextlib
import io
import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from verify_hazard_estop import (  # noqa: E402
    main,
    recover_hazard_scenario,
    run_hazard_scenario,
)


class FakeBackendClient:
    def __init__(self, _base_url: str = "", _timeout: float = 0) -> None:
        self.timeout = _timeout
        self.calls: list[tuple[str, dict]] = []
        self.hazard_posted = False

    def post(self, path, body, headers=None):
        self.calls.append((path, body))
        if path == "/robots":
            return body
        if path == "/zones":
            return body
        if path == "/task-orders/from-natural-language":
            return {"task_id": "task-e2e"}
        if path.endswith("/action-proposals"):
            return {"proposal_id": "proposal-e2e"}
        if path.endswith("/evaluate"):
            return {"status": "APPROVED"}
        if path.endswith("/execute"):
            return {"status": "EXECUTING"}
        if path == "/hazards":
            self.hazard_posted = True
            return {"hazard_id": "hazard-e2e"}
        raise AssertionError(f"unexpected path: {path}")

    def get(self, path):
        status = "ESTOP" if self.hazard_posted else "MOVING"
        if path == "/robots":
            return [] if not self.calls else [{"robot_id": "robot-01", "status": status}]
        if path == "/zones":
            return []
        if path.endswith("/ack"):
            return {"status": "ACKNOWLEDGED", "command_to_ack_ms": 25.0}
        if path == "/task-orders/task-e2e":
            return {"task_id": "task-e2e", "status": "HELD" if self.hazard_posted else "RUNNING"}
        if path == "/task-orders/task-e2e/action-proposals":
            return [{
                "proposal_id": "proposal-e2e",
                "status": "HELD" if self.hazard_posted else "EXECUTING",
            }]
        raise AssertionError(f"unexpected path: {path}")


class FakeAdapterEventMonitor:
    def __init__(self, topic: str, _discovery_timeout_seconds: float) -> None:
        self.topic = topic
        self.closed = False

    def wait_for_estop(self, robot_id: str, _timeout_seconds: float):
        return {
            "kind": "ESTOP",
            "robot_id": robot_id,
            "triggered_by": "RULE_ENGINE",
            "latency_ms": 12.5,
        }

    def close(self):
        self.closed = True


class FakeRecoveryClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.hazard_status = "OPEN"

    def put(self, path, body):
        self.calls.append((path, body))
        return body

    def post(self, path, body, headers=None):
        self.calls.append((path, body))
        if path.endswith("/clearance"):
            self.hazard_status = "ACKNOWLEDGED"
            return {"hazard_id": "hazard-e2e"}
        if path.endswith("/resolve"):
            self.hazard_status = "RESOLVED"
            return {"hazard_id": "hazard-e2e", "status": "RESOLVED"}
        if path.endswith("/resume-proposal"):
            return {"proposal_id": "proposal-resume", "action_type": "RESUME"}
        if path.endswith("/evaluate"):
            return {"status": "APPROVED"}
        if path.endswith("/execute"):
            return {"status": "EXECUTING"}
        raise AssertionError(f"unexpected path: {path}")

    def get(self, path):
        if path == "/hazards":
            return [{"hazard_id": "hazard-e2e", "status": self.hazard_status}]
        if path.endswith("/ack"):
            return {"status": "ACKNOWLEDGED", "command_to_ack_ms": 7.5}
        if path == "/task-orders/task-e2e/action-proposals":
            return [{"proposal_id": "proposal-resume", "status": "SUCCEEDED"}]
        if path == "/task-orders/task-e2e":
            return {"task_id": "task-e2e", "status": "PLANNING"}
        if path == "/robots":
            return [{"robot_id": "robot-01", "status": "IDLE"}]
        raise AssertionError(f"unexpected path: {path}")


class HazardEStopE2EScriptTest(unittest.TestCase):
    def test_scenario_registers_critical_hazard_and_waits_for_held_state(self):
        client = FakeBackendClient()

        report = run_hazard_scenario(
            client,
            "robot-01",
            "tiago-adapter",
            "world",
            3,
            5,
            0,
            estop_event_waiter=FakeAdapterEventMonitor("unused", 1).wait_for_estop,
            sleep_fn=lambda _seconds: None,
        )

        hazard = next(body for path, body in client.calls if path == "/hazards")
        robot = next(body for path, body in client.calls if path == "/robots")
        self.assertEqual(hazard["severity"], "CRITICAL")
        self.assertEqual(hazard["related_robot_ids"], ["robot-01"])
        self.assertEqual(robot["control_adapter"], "tiago-adapter")
        self.assertEqual(report["robot_status"], "ESTOP")
        self.assertEqual(report["task_status"], "HELD")
        self.assertEqual(report["proposal_status"], "HELD")
        self.assertEqual(report["adapter_estop_latency_ms"], 12.5)
        self.assertEqual(report["adapter_estop_triggered_by"], "RULE_ENGINE")

    def test_main_returns_success_for_completed_estop_roundtrip(self):
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(
                ["--hazard-delay", "0", "--json"],
                FakeBackendClient,
                FakeAdapterEventMonitor,
            )
        self.assertEqual(code, 0)

    def test_scenario_rejects_hazard_after_motion_already_completed(self):
        client = FakeBackendClient()
        original_get = client.get

        def get_with_completed_proposal(path):
            result = original_get(path)
            if path == "/task-orders/task-e2e/action-proposals" and not client.hazard_posted:
                result[0]["status"] = "SUCCEEDED"
            return result

        client.get = get_with_completed_proposal
        with self.assertRaisesRegex(RuntimeError, "실행 중이 아닙니다"):
            run_hazard_scenario(
                client,
                "robot-01",
                "tiago-adapter",
                "world",
                3,
                5,
                0,
                estop_event_waiter=FakeAdapterEventMonitor("unused", 1).wait_for_estop,
                sleep_fn=lambda _seconds: None,
            )

    def test_recovery_submits_cv_clearance_and_waits_for_resume_result(self):
        client = FakeRecoveryClient()

        report = recover_hazard_scenario(
            client,
            {
                "robot_id": "robot-01",
                "task_id": "task-e2e",
                "proposal_id": "proposal-e2e",
                "hazard_id": "hazard-e2e",
            },
            "world",
            3,
            5,
            sleep_fn=lambda _seconds: None,
        )

        self.assertEqual(report["hazard_status_after_clearance"], "ACKNOWLEDGED")
        self.assertEqual(report["hazard_status"], "RESOLVED")
        self.assertEqual(report["resume_proposal_status"], "SUCCEEDED")
        self.assertEqual(report["resumed_task_status"], "PLANNING")
        self.assertEqual(report["resumed_robot_status"], "IDLE")
        self.assertIn(("/workers/snapshot", client.calls[1][1]), client.calls)


if __name__ == "__main__":
    unittest.main()
