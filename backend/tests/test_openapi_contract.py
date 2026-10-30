import unittest

from app.main import app


class OpenApiContractTest(unittest.TestCase):
    def test_version_and_operations_match_backend_spec_v0_2(self) -> None:
        schema = app.openapi()
        actual_operations = {
            (method.upper(), path)
            for path, methods in schema["paths"].items()
            for method in methods
        }
        expected_operations = {
            ("GET", "/health"),
            ("POST", "/robots"),
            ("GET", "/robots"),
            ("POST", "/task-orders/preview"),
            ("POST", "/task-orders/from-natural-language"),
            ("GET", "/task-orders/{task_id}"),
            ("POST", "/task-orders/{task_id}/action-proposals"),
            ("GET", "/task-orders/{task_id}/action-proposals"),
            ("POST", "/action-proposals"),
            ("POST", "/action-proposals/{proposal_id}/evaluate"),
            ("POST", "/action-proposals/{proposal_id}/execute"),
            ("POST", "/hazards"),
            ("POST", "/hazards/{hazard_id}/resolve"),
            ("GET", "/safety-rules"),
            ("GET", "/execution-logs"),
        }

        self.assertEqual(schema["info"]["version"], "0.2.0")
        self.assertEqual(len(schema["paths"]), 13)
        self.assertEqual(actual_operations, expected_operations)


if __name__ == "__main__":
    unittest.main()
