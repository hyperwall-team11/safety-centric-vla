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
    def __init__(self, _base_url: str = "", _timeout: float = 0, *, reject: bool = False) -> None:
        self.reject = reject
        self.timeout = _timeout
        self.calls: list[tuple[str, dict]] = []
        self.sequence = 0
        self.latest_proposal_id: str | None = None

    def post(self, path, body, headers=None):
        self.calls.append((path, body))
        if path == "/robots":
            return {"robot_id": "benchmark"}
        if path == "/task-orders/from-natural-language":
            self.sequence += 1
            return {"task_id": f"task-{self.sequence}"}
        if path.endswith("/action-proposals"):
            self.latest_proposal_id = f"proposal-{self.sequence}"
            return {"proposal_id": self.latest_proposal_id}
        if path.endswith("/evaluate"):
            return {"status": "REJECTED" if self.reject else "APPROVED"}
        if path.endswith("/execute"):
            return {"status": "EXECUTING"}
        raise AssertionError(f"unexpected path: {path}")

    def get(self, path):
        if path == "/robots":
            return []
        if path.endswith("/ack"):
            return {
                "status": "ACKNOWLEDGED",
                "command_to_ack_ms": 25.0,
            }
        if path.endswith("/action-proposals"):
            return [{
                "proposal_id": self.latest_proposal_id,
                "status": "COMPLETED",
            }]
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


if __name__ == "__main__":
    unittest.main()
