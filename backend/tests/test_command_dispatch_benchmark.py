import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from benchmark_command_to_dispatch import Trial, build_summary, percentile  # noqa: E402


class CommandDispatchBenchmarkTest(unittest.TestCase):
    def test_percentile_uses_observed_value(self):
        self.assertEqual(percentile([5, 1, 4, 2, 3], 0.95), 5)

    def test_summary_counts_threshold_passes(self):
        trials = [
            Trial(1, 400, "task-1", "proposal-1", True),
            Trial(2, 1200, "task-2", "proposal-2", False),
        ]
        report = build_summary(trials, 1000)
        self.assertEqual(report["passed"], 1)
        self.assertEqual(report["success_rate_percent"], 50.0)
        self.assertEqual(report["max_ms"], 1200)


if __name__ == "__main__":
    unittest.main()
