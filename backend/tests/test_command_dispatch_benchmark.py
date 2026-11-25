import contextlib
import io
import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from benchmark_command_to_dispatch import (  # noqa: E402
    Trial,
    build_summary,
    main,
    percentile,
    run_trial,
)


class FakeBackendClient:
    def __init__(
        self,
        _base_url: str = "",
        _timeout: float = 0,
        *,
        reject: bool = False,
        ack_status: str = "ACKNOWLEDGED",
    ) -> None:
        self.reject = reject
        self.ack_status = ack_status
        self.timeout = _timeout
        self.calls: list[tuple[str, dict]] = []
        self.get_calls: list[str] = []
        self.sequence = 0
        self.latest_proposal_id: str | None = None
        self.registered_robot_id: str | None = None
        self.latest_task_id: str | None = None

    def post(self, path, body, headers=None):
        self.calls.append((path, body))
        if path == "/robots":
            self.registered_robot_id = body["robot_id"]
            return {"robot_id": "benchmark"}
        if path == "/task-orders/from-natural-language":
            self.sequence += 1
            self.latest_task_id = f"task-{self.sequence}"
            return {"task_id": self.latest_task_id}
        if path.endswith("/action-proposals"):
            self.latest_proposal_id = f"proposal-{self.sequence}"
            return {
                "proposal_id": self.latest_proposal_id,
                "model_name": "test-model",
            }
        if path.endswith("/evaluate"):
            return {"status": "REJECTED" if self.reject else "APPROVED"}
        if path.endswith("/execute"):
            return {"status": "EXECUTING"}
        raise AssertionError(f"unexpected path: {path}")

    def get(self, path):
        self.get_calls.append(path)
        if path == "/robots":
            if self.registered_robot_id is None:
                if self.sequence == 0:
                    return []
                return [{"robot_id": "robot-01", "status": "IDLE"}]
            return [{"robot_id": self.registered_robot_id, "status": "IDLE"}]
        if path.endswith("/ack"):
            return {
                "status": self.ack_status,
                "command_to_ack_ms": 25.0 if self.ack_status == "ACKNOWLEDGED" else None,
            }
        if path.endswith("/action-proposals"):
            return [{
                "proposal_id": self.latest_proposal_id,
                "status": "SUCCEEDED",
            }]
        if path == f"/task-orders/{self.latest_task_id}":
            return {"task_id": self.latest_task_id, "status": "COMPLETED"}
        raise AssertionError(f"unexpected path: {path}")


class CommandDispatchBenchmarkTest(unittest.TestCase):
    def test_percentile_uses_observed_value(self):
        self.assertEqual(percentile([5, 1, 4, 2, 3], 0.95), 5)

    def test_summary_separates_execution_and_latency_rates(self):
        trials = [
            Trial(1, True, 400, "task-1", "proposal-1", True),
            Trial(2, True, 1200, "task-2", "proposal-2", False),
            Trial(3, False, None, "task-3", "proposal-3", False, "rejected"),
        ]
        report = build_summary(trials, 1000)
        self.assertEqual(report["execution_successes"], 2)
        self.assertEqual(report["execution_success_rate_percent"], 66.67)
        self.assertEqual(report["latency_passed"], 1)
        self.assertEqual(report["latency_pass_rate_percent"], 50.0)
        self.assertIsNone(report["p95_ms"])
        self.assertEqual(report["max_ms"], 1200)

    def test_run_trial_completes_one_successful_execution(self):
        client = FakeBackendClient()
        trial = run_trial(client, "robot-01", 0, 1000)
        self.assertTrue(trial.execution_succeeded)
        self.assertTrue(trial.latency_passed)
        self.assertEqual(trial.elapsed_ms, 25.0)
        self.assertFalse(any(path.endswith("/result") for path, _body in client.calls))

    def test_run_trial_records_rejection_without_aborting(self):
        client = FakeBackendClient(reject=True)
        trial = run_trial(client, "robot-01", 0, 1000)
        self.assertFalse(trial.execution_succeeded)
        self.assertIn("REJECTED", trial.error)
        self.assertFalse(any(path.endswith("/execute") for path, _body in client.calls))

    def test_run_trial_rejects_unexpected_action_model(self):
        client = FakeBackendClient()

        trial = run_trial(
            client,
            "robot-01",
            0,
            1000,
            expected_model_name="openai-responses-action-provider",
        )

        self.assertFalse(trial.execution_succeeded)
        self.assertIn("test-model != openai-responses-action-provider", trial.error)

    def test_ack_timeout_still_waits_for_execution_result(self):
        client = FakeBackendClient(ack_status="TIMED_OUT")

        trial = run_trial(client, "robot-01", 0, 1000)

        self.assertTrue(trial.execution_succeeded)
        self.assertFalse(trial.latency_passed)
        self.assertIn("TIMED_OUT", trial.error)
        self.assertTrue(any(
            path.endswith("/action-proposals")
            for path in client.get_calls
        ))

    def test_function_e2e_can_report_latency_without_using_it_as_exit_gate(self):
        client = FakeBackendClient(ack_status="TIMED_OUT")
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(
                ["--runs", "1", "--no-latency-gate", "--json"],
                lambda _base_url, _timeout: client,
            )

        self.assertEqual(code, 0)

    def test_trials_alternate_between_two_destinations(self):
        client = FakeBackendClient()

        run_trial(client, "robot-01", 0, 1000)
        run_trial(client, "robot-01", 1, 1000)

        instructions = [
            body["instruction"]
            for path, body in client.calls
            if path == "/task-orders/from-natural-language"
        ]
        self.assertIn("좌표 (1, 0, 0)", instructions[0])
        self.assertIn("좌표 (0, 0, 0)", instructions[1])

    def test_main_exit_code_reflects_scenario_a_acceptance(self):
        with contextlib.redirect_stdout(io.StringIO()):
            success_code = main(["--runs", "1", "--json"], FakeBackendClient)
            failure_code = main(
                ["--runs", "1", "--json"],
                lambda base_url, timeout: FakeBackendClient(base_url, timeout, reject=True),
            )
        self.assertEqual(success_code, 0)
        self.assertEqual(failure_code, 1)

    def test_main_uses_explicit_ctl_robot_id(self):
        client = FakeBackendClient()
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(
                ["--runs", "1", "--robot-id", "tiago-ctl", "--json"],
                lambda _base_url, _timeout: client,
            )
        self.assertEqual(code, 0)
        robot_call = next(body for path, body in client.calls if path == "/robots")
        self.assertEqual(robot_call["robot_id"], "tiago-ctl")
        task_call = next(
            body
            for path, body in client.calls
            if path == "/task-orders/from-natural-language"
        )
        self.assertIn("tiago-ctl", task_call["instruction"])

    def test_main_records_explicit_control_adapter(self):
        client = FakeBackendClient()
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(
                [
                    "--runs",
                    "1",
                    "--control-adapter",
                    "tiago-adapter",
                    "--json",
                ],
                lambda _base_url, _timeout: client,
            )
        self.assertEqual(code, 0)
        robot_call = next(body for path, body in client.calls if path == "/robots")
        self.assertEqual(robot_call["control_adapter"], "tiago-adapter")


if __name__ == "__main__":
    unittest.main()
