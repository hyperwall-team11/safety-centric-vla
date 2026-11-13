import sys
import unittest
from pathlib import Path


EVALUATION_DIR = Path(__file__).resolve().parents[1] / "evaluation"
sys.path.insert(0, str(EVALUATION_DIR))

from run_command_evaluation import DEFAULT_CASES, evaluate, load_cases, summary  # noqa: E402


class CommandEvaluationTest(unittest.TestCase):
    def test_reference_corpus_has_balanced_one_hundred_cases(self):
        cases = load_cases(DEFAULT_CASES)
        self.assertEqual(len(cases), 100)
        self.assertEqual(sum(case["valid"] for case in cases), 50)

    def test_reference_corpus_matches_current_parser_contract(self):
        cases = load_cases(DEFAULT_CASES)
        results = evaluate(cases)
        failures = [result for result in results if not result.passed]
        self.assertEqual(failures, [], "\n".join(f"{r.case_id}: {r.message}" for r in failures))
        self.assertEqual(summary(cases, results)["accuracy_percent"], 100.0)


if __name__ == "__main__":
    unittest.main()
