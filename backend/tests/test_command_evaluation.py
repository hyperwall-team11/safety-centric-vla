import sys
import tempfile
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

    def test_case_metadata_requires_expected_types(self):
        with self.assertRaisesRegex(ValueError, "id는 비어 있지 않은 문자열"):
            validate_case({"id": "", "instruction": "명령", "valid": False})
        with self.assertRaisesRegex(ValueError, "valid는 boolean"):
            validate_case({"id": "X", "instruction": "명령", "valid": "false"})

    def test_duplicate_case_ids_are_rejected(self):
        content = (
            '{"id":"X","instruction":"명령","valid":false,'
            '"expected_error_code":"INVALID_INSTRUCTION"}\n'
            '{"id":"X","instruction":"다른 명령","valid":false,'
            '"expected_error_code":"INVALID_INSTRUCTION"}\n'
        )
        with tempfile.TemporaryDirectory() as directory:
            cases_path = Path(directory) / "cases.jsonl"
            cases_path.write_text(content, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "중복 평가 ID"):
                load_cases(cases_path)

    def test_summary_supports_a_single_case_class(self):
        cases = [{
            "id": "V",
            "instruction": "robot-01 로봇을 A구역으로 이동해",
            "valid": True,
            "robot_id": "robot-01",
            "action_type": "MOVE_TO",
            "destination_type": "ZONE",
            "destination_value": "A구역",
            "object_id": None,
        }]
        report = summary(cases, evaluate(cases))
        self.assertEqual(report["valid_accuracy_percent"], 100.0)
        self.assertIsNone(report["rejection_accuracy_percent"])


if __name__ == "__main__":
    unittest.main()
