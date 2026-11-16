import sys
import unittest
from pathlib import Path


EVALUATION_DIR = Path(__file__).resolve().parents[1] / "evaluation"
sys.path.insert(0, str(EVALUATION_DIR))

from run_command_evaluation import (  # noqa: E402
    DEFAULT_CASES,
    evaluate,
    load_cases,
    summary,
    validate_case,
)


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

    def test_valid_case_requires_every_expected_field(self):
        with self.assertRaisesRegex(ValueError, "기대 필드 누락"):
            validate_case({"id": "V", "instruction": "명령", "valid": True})

    def test_rejected_case_requires_known_error_code(self):
        with self.assertRaisesRegex(ValueError, "expected_error_code 누락"):
            validate_case({"id": "X", "instruction": "명령", "valid": False})
        with self.assertRaisesRegex(ValueError, "알 수 없는 expected_error_code"):
            validate_case({
                "id": "X",
                "instruction": "명령",
                "valid": False,
                "expected_error_code": "UNKNOWN",
            })


if __name__ == "__main__":
    unittest.main()
