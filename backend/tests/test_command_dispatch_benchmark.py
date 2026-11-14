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
        self.calls: list[tuple[str, dict]] = []
        self.sequence = 0

    def post(self, path, body, headers=None):
        self.calls.append((path, body))
        if path == "/robots":
            return {"robot_id": "benchmark"}
        if path == "/task-orders/from-natural-language":
            self.sequence += 1
            return {"task_id": f"task-{self.sequence}"}
        if path.endswith("/action-proposals"):
            return {"proposal_id": f"proposal-{self.sequence}"}
        if path.endswith("/evaluate"):
            return {"status": "REJECTED" if self.reject else "APPROVED"}
        if path.endswith("/execute"):
            return {"status": "EXECUTING"}
        if path.endswith("/result"):
            return {"status": body["status"]}
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
        self.assertEqual(client.calls[-1][1]["status"], "SUCCEEDED")

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


if __name__ == "__main__":
    unittest.main()
