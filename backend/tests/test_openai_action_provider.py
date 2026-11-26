import json
import os
import unittest
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError

from app.action_planner import RuleBasedActionPlanner, VlaActionPlanner
from app.models import ProposalGenerationRequest, Robot, TaskOrder
from app.openai_action_provider import (
    OpenAIActionProviderError,
    OpenAIResponsesActionProvider,
)
from app.planner_factory import action_planner_from_environment


ROBOT = Robot.model_validate({
    "robot_id": "robot-01",
    "robot_type": "MOBILE",
    "control_adapter": "tiago-adapter",
    "status": "IDLE",
    "pose": {
        "frame_id": "world",
        "position": {"x": 0, "y": 0, "z": 0},
        "orientation": {"x": 0, "y": 0, "z": 0, "w": 1},
    },
    "velocity": {"x": 0, "y": 0, "z": 0},
    "motion_limits": {"max_speed": 0.5, "max_acceleration": 0.5},
})

TASK = TaskOrder.model_validate({
    "task_id": "task-openai",
    "instruction": "robot-01 로봇을 좌표 (1, 0, 0)로 이동해",
    "target_robot_id": "robot-01",
    "target_object_id": None,
    "destination": {
        "type": "COORDINATE",
        "frame_id": "world",
        "position": {"x": 1, "y": 0, "z": 0},
    },
})


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class RawResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self.payload


class RecordingUrlopen:
    def __init__(self, output: dict | None = None, *, refusal: bool = False) -> None:
        self.requests = []
        content = (
            {"type": "refusal", "refusal": "no"}
            if refusal
            else {"type": "output_text", "text": json.dumps(output)}
        )
        self.response = {
            "status": "completed",
            "output": [{"type": "message", "content": [content]}],
        }

    def __call__(self, req, *, timeout):
        self.requests.append((req, timeout))
        return FakeResponse(self.response)


class OpenAIActionProviderTest(unittest.TestCase):
    def test_calls_responses_api_with_strict_schema_and_returns_action(self):
        output = {
            "action_type": "MOVE_TO",
            "parameters": {
                "target_pose": TASK.destination.model_dump(mode="json"),
                "speed_limit": 0.2,
            },
            "trajectory": None,
            "frame_id": "world",
            "speed_limit": 0.2,
        }
        transport = RecordingUrlopen(output)
        provider = OpenAIResponsesActionProvider(
            "test-key",
            model="gpt-test",
            timeout_seconds=7,
            urlopen=transport,
        )

        result = provider.generate(
            TASK,
            ROBOT,
            ProposalGenerationRequest(speed_limit=0.2),
        )

        self.assertEqual(result, output)
        req, timeout = transport.requests[0]
        payload = json.loads(req.data)
        self.assertEqual(req.full_url, "https://api.openai.com/v1/responses")
        self.assertEqual(timeout, 7)
        self.assertFalse(payload["store"])
        self.assertTrue(payload["text"]["format"]["strict"])
        self.assertEqual(
            payload["text"]["format"]["schema"]["properties"]["action_type"]["enum"],
            ["MOVE_TO"],
        )
        self.assertNotIn("test-key", json.dumps(payload))

    def test_refusal_fails_closed(self):
        provider = OpenAIResponsesActionProvider(
            "test-key",
            urlopen=RecordingUrlopen(refusal=True),
        )

        with self.assertRaisesRegex(OpenAIActionProviderError, "거부"):
            provider.generate(TASK, ROBOT, ProposalGenerationRequest())

    def test_incomplete_response_fails_closed(self):
        transport = RecordingUrlopen()
        transport.response = {"status": "incomplete", "output": []}
        provider = OpenAIResponsesActionProvider("test-key", urlopen=transport)

        with self.assertRaisesRegex(OpenAIActionProviderError, "완료되지 않았습니다"):
            provider.generate(TASK, ROBOT, ProposalGenerationRequest())

    def test_http_error_is_normalized(self):
        def raise_http_error(*_args, **_kwargs):
            raise HTTPError(
                "https://api.openai.com/v1/responses",
                429,
                "Too Many Requests",
                {},
                BytesIO(b'{"error":{"code":"rate_limit_exceeded"}}'),
            )

        provider = OpenAIResponsesActionProvider("test-key", urlopen=raise_http_error)

        with self.assertRaisesRegex(OpenAIActionProviderError, "HTTP 429"):
            provider.generate(TASK, ROBOT, ProposalGenerationRequest())

    def test_timeout_is_normalized(self):
        def raise_timeout(*_args, **_kwargs):
            raise TimeoutError

        provider = OpenAIResponsesActionProvider("test-key", urlopen=raise_timeout)

        with self.assertRaisesRegex(OpenAIActionProviderError, "연결에 실패했습니다"):
            provider.generate(TASK, ROBOT, ProposalGenerationRequest())

    def test_malformed_response_json_fails_closed(self):
        provider = OpenAIResponsesActionProvider(
            "test-key",
            urlopen=lambda *_args, **_kwargs: RawResponse(b"not-json"),
        )

        with self.assertRaisesRegex(OpenAIActionProviderError, "응답 JSON"):
            provider.generate(TASK, ROBOT, ProposalGenerationRequest())

    def test_malformed_structured_output_fails_closed(self):
        transport = RecordingUrlopen()
        transport.response = {
            "status": "completed",
            "output": [{
                "type": "message",
                "content": [{"type": "output_text", "text": "not-json"}],
            }],
        }
        provider = OpenAIResponsesActionProvider("test-key", urlopen=transport)

        with self.assertRaisesRegex(OpenAIActionProviderError, "구조화 출력"):
            provider.generate(TASK, ROBOT, ProposalGenerationRequest())

    def test_factory_defaults_to_rule_based(self):
        with patch.dict(os.environ, {}, clear=True):
            planner = action_planner_from_environment()
        self.assertIsInstance(planner, RuleBasedActionPlanner)

    def test_factory_selects_openai_and_requires_key(self):
        with patch.dict(
            os.environ,
            {"BACKEND_ACTION_PLANNER": "openai", "OPENAI_API_KEY": "test-key"},
            clear=True,
        ):
            planner = action_planner_from_environment()
        self.assertIsInstance(planner, VlaActionPlanner)
        self.assertEqual(planner.model_name, "openai-responses-action-provider")

    def test_factory_rejects_unknown_provider(self):
        with patch.dict(os.environ, {"BACKEND_ACTION_PLANNER": "unknown"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "rule-based 또는 openai"):
                action_planner_from_environment()


if __name__ == "__main__":
    unittest.main()
