from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from os import getenv
from typing import Any
from urllib import error, request as urllib_request

from .instruction_intent import IntentDetectionError, detect_requested_action
from .models import (
    ActionType,
    CoordinateDestination,
    ProposalGenerationRequest,
    Robot,
    TaskOrder,
    Zone,
)


OPENAI_ACTION_MODEL_ENV = "OPENAI_ACTION_MODEL"
OPENAI_ACTION_TIMEOUT_ENV = "OPENAI_ACTION_TIMEOUT_SECONDS"
OPENAI_BASE_URL_ENV = "OPENAI_BASE_URL"
DEFAULT_OPENAI_ACTION_MODEL = "gpt-4o-mini"


class OpenAIActionProviderError(RuntimeError):
    pass


def _positive_timeout(raw: str | None) -> float:
    if raw is None:
        return 30.0
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{OPENAI_ACTION_TIMEOUT_ENV}는 양수여야 합니다.") from exc
    if value <= 0:
        raise RuntimeError(f"{OPENAI_ACTION_TIMEOUT_ENV}는 양수여야 합니다.")
    return value


def _exact_destination_schema(task: TaskOrder) -> dict[str, Any]:
    destination = task.destination
    if isinstance(destination, CoordinateDestination):
        return {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["COORDINATE"]},
                "frame_id": {"type": "string", "enum": [destination.frame_id]},
                "position": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "number", "enum": [destination.position.x]},
                        "y": {"type": "number", "enum": [destination.position.y]},
                        "z": {"type": "number", "enum": [destination.position.z]},
                    },
                    "required": ["x", "y", "z"],
                    "additionalProperties": False,
                },
            },
            "required": ["type", "frame_id", "position"],
            "additionalProperties": False,
        }
    return {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["ZONE"]},
            "zone_id": {"type": "string", "enum": [destination.zone_id]},
        },
        "required": ["type", "zone_id"],
        "additionalProperties": False,
    }


def _output_schema(
    task: TaskOrder,
    robot: Robot,
    generation_request: ProposalGenerationRequest,
) -> tuple[ActionType, float, dict[str, Any]]:
    try:
        action_type = detect_requested_action(
            task.instruction,
            has_target_object=task.target_object_id is not None,
        )
    except IntentDetectionError as exc:
        raise OpenAIActionProviderError(str(exc)) from exc
    if action_type in {ActionType.STOP, ActionType.RESUME}:
        raise OpenAIActionProviderError("STOP·RESUME은 OpenAI 일반 행동 제안에서 생성하지 않습니다.")

    safe_speed = min(
        generation_request.speed_limit or 0.4,
        0.4,
        robot.motion_limits.max_speed,
    )
    destination_schema = _exact_destination_schema(task)
    if action_type == ActionType.MOVE_TO:
        if task.target_object_id is not None or generation_request.manipulation is not None:
            raise OpenAIActionProviderError("MOVE_TO 문맥에 물체 조작 요청이 포함되었습니다.")
        parameters_schema = {
            "type": "object",
            "properties": {
                "target_pose": destination_schema,
                "speed_limit": {"type": "number", "enum": [safe_speed]},
            },
            "required": ["target_pose", "speed_limit"],
            "additionalProperties": False,
        }
    else:
        if task.target_object_id is None or generation_request.manipulation is None:
            raise OpenAIActionProviderError(
                "MANIPULATE에는 대상 물체와 PICK 또는 PLACE 단계가 필요합니다."
            )
        parameters_schema = {
            "type": "object",
            "properties": {
                "manipulation": {
                    "type": "string",
                    "enum": [generation_request.manipulation.value],
                },
                "object_id": {"type": "string", "enum": [task.target_object_id]},
                "target_pose": destination_schema,
            },
            "required": ["manipulation", "object_id", "target_pose"],
            "additionalProperties": False,
        }

    frame_id = (
        task.destination.frame_id
        if isinstance(task.destination, CoordinateDestination)
        else robot.pose.frame_id
    )
    schema = {
        "type": "object",
        "properties": {
            "action_type": {"type": "string", "enum": [action_type.value]},
            "parameters": parameters_schema,
            "trajectory": {"type": "null"},
            "frame_id": {"type": "string", "enum": [frame_id]},
            "speed_limit": {"type": "number", "enum": [safe_speed]},
        },
        "required": [
            "action_type",
            "parameters",
            "trajectory",
            "frame_id",
            "speed_limit",
        ],
        "additionalProperties": False,
    }
    return action_type, safe_speed, schema


def _extract_output_text(response: Mapping[str, Any]) -> str:
    if response.get("status") not in {None, "completed"}:
        raise OpenAIActionProviderError(
            f"OpenAI 응답이 완료되지 않았습니다: {response.get('status')}"
        )
    for item in response.get("output", []):
        if not isinstance(item, Mapping) or item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if not isinstance(content, Mapping):
                continue
            if content.get("type") == "refusal":
                raise OpenAIActionProviderError("OpenAI 모델이 행동 생성을 거부했습니다.")
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                return content["text"]
    raise OpenAIActionProviderError("OpenAI 응답에 구조화 행동 출력이 없습니다.")


class OpenAIResponsesActionProvider:
    """OpenAI Responses API의 Structured Outputs를 VLA 출력 경계로 연결한다."""

    model_name = "openai-responses-action-provider"

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_OPENAI_ACTION_MODEL,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 30.0,
        urlopen: Callable[..., Any] = urllib_request.urlopen,
    ) -> None:
        if not api_key.strip():
            raise RuntimeError("OPENAI_API_KEY가 필요합니다.")
        if not model.strip():
            raise RuntimeError(f"{OPENAI_ACTION_MODEL_ENV}이 비어 있습니다.")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._urlopen = urlopen
        self.model_version = model

    @classmethod
    def from_environment(cls) -> OpenAIResponsesActionProvider:
        return cls(
            getenv("OPENAI_API_KEY", ""),
            model=getenv(OPENAI_ACTION_MODEL_ENV, DEFAULT_OPENAI_ACTION_MODEL),
            base_url=getenv(OPENAI_BASE_URL_ENV, "https://api.openai.com/v1"),
            timeout_seconds=_positive_timeout(getenv(OPENAI_ACTION_TIMEOUT_ENV)),
        )

    def generate(
        self,
        task: TaskOrder,
        robot: Robot,
        generation_request: ProposalGenerationRequest,
        *,
        destination_zone: Zone | None = None,
    ) -> Mapping[str, Any]:
        action_type, safe_speed, schema = _output_schema(
            task,
            robot,
            generation_request,
        )
        context = {
            "task_order": task.model_dump(mode="json"),
            "robot": robot.model_dump(mode="json"),
            "requested_manipulation": (
                None
                if generation_request.manipulation is None
                else generation_request.manipulation.value
            ),
            "destination_zone": (
                None if destination_zone is None else destination_zone.model_dump(mode="json")
            ),
            "required_action_type": action_type.value,
            "maximum_safe_speed": safe_speed,
        }
        payload = {
            "model": self.model_version,
            "store": False,
            "instructions": (
                "You generate one robot ActionProposal payload from trusted context. "
                "Never add IDs, change the target, change the coordinate frame, or create "
                "STOP/RESUME. Follow the JSON schema exactly. Safety evaluation happens "
                "after generation and may still reject the proposal."
            ),
            "input": json.dumps(context, ensure_ascii=False),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "robot_action_proposal",
                    "description": "One untrusted robot action candidate for backend validation",
                    "strict": True,
                    "schema": schema,
                }
            },
        }
        req = urllib_request.Request(
            f"{self._base_url}/responses",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with self._urlopen(req, timeout=self._timeout_seconds) as response:
                raw_response = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise OpenAIActionProviderError(
                f"OpenAI Responses API HTTP {exc.code}: {detail[:500]}"
            ) from exc
        except (error.URLError, TimeoutError) as exc:
            raise OpenAIActionProviderError("OpenAI Responses API 연결에 실패했습니다.") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OpenAIActionProviderError("OpenAI 응답 JSON을 해석할 수 없습니다.") from exc

        output_text = _extract_output_text(raw_response)
        try:
            output = json.loads(output_text)
        except json.JSONDecodeError as exc:
            raise OpenAIActionProviderError("OpenAI 구조화 출력이 JSON이 아닙니다.") from exc
        if not isinstance(output, Mapping):
            raise OpenAIActionProviderError("OpenAI 구조화 출력이 객체가 아닙니다.")
        return output
