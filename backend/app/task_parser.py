from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum

from .instruction_intent import (
    IntentDetectionError,
    detect_control_action,
    detect_requested_action,
    validate_executable_instruction,
)
from .models import ActionType, NaturalLanguageTaskRequest, TaskOrder, TaskOrderPreview


class TaskParseErrorCode(str, Enum):
    INVALID_INSTRUCTION = "INVALID_INSTRUCTION"
    UNSUPPORTED_CONTROL_ACTION = "UNSUPPORTED_CONTROL_ACTION"
    ROBOT_NOT_FOUND = "ROBOT_NOT_FOUND"
    ROBOT_AMBIGUOUS = "ROBOT_AMBIGUOUS"
    ROBOT_CONFLICT = "ROBOT_CONFLICT"
    ROBOT_REQUIRED = "ROBOT_REQUIRED"
    DESTINATION_INVALID = "DESTINATION_INVALID"
    DESTINATION_REQUIRED = "DESTINATION_REQUIRED"
    DESTINATION_AMBIGUOUS = "DESTINATION_AMBIGUOUS"
    OBJECT_AMBIGUOUS = "OBJECT_AMBIGUOUS"
    ACTION_INVALID = "ACTION_INVALID"
    ACTION_UNSUPPORTED = "ACTION_UNSUPPORTED"


class TaskParseError(ValueError):
    def __init__(self, message: str, code: TaskParseErrorCode) -> None:
        super().__init__(message)
        self.code = code


NUMBER = r"[+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[eE][+-]?\d+)?"
SEPARATOR = r"(?:\s*,\s*|\s+)"
BRACKETED_COORDINATE_PATTERN = re.compile(
    rf"(?P<open>[\(\[])\s*"
    rf"(?:x\s*[:=]\s*)?(?P<x>{NUMBER}){SEPARATOR}"
    rf"(?:y\s*[:=]\s*)?(?P<y>{NUMBER})"
    rf"(?:{SEPARATOR}(?:z\s*[:=]\s*)?(?P<z>{NUMBER}))?\s*"
    rf"(?P<close>[\)\]])",
    re.IGNORECASE,
)
PLAIN_COORDINATE_PATTERN = re.compile(
    rf"(?:좌표|위치)\s*:?\s*(?![\(\[])"
    rf"(?:x\s*[:=]\s*)?(?P<x>{NUMBER}){SEPARATOR}"
    rf"(?:y\s*[:=]\s*)?(?P<y>{NUMBER})"
    rf"(?:{SEPARATOR}(?:z\s*[:=]\s*)?(?P<z>{NUMBER}))?"
    rf"(?=\s*(?:좌표|위치)?\s*(?:(?:으로|로)(?!부터)|까지|에(?!서))|"
    rf"\s*[.!]?\s*$)",
    re.IGNORECASE,
)
ZONE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9가-힣_-])"
    r"(?P<name>[A-Za-z0-9가-힣][A-Za-z0-9가-힣_-]{0,63})\s*"
    r"(?P<kind>구역|지점|창고|작업대|대기실)\s*"
    r"(?P<particle>(?:으로|로)(?!부터)|까지|에(?!서))"
)
QUOTED_ZONE_PATTERN = re.compile(
    r"[\"'“](?P<label>[^\"'”\n]{1,64})[\"'”]\s*"
    r"(?:(?:으로|로)(?!부터)|까지|에(?!서))"
)
CANONICAL_OBJECT_PATTERN = re.compile(
    r"(?P<object>(?:obj|box)[-_][A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
LABEL_OBJECT_PATTERN = re.compile(
    r"(?P<label>상자|박스|물체|팔레트)\s*(?:번호\s*)?"
    r"(?P<id>[A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
NUMBERED_OBJECT_PATTERN = re.compile(
    r"(?P<id>[A-Za-z0-9_-]+)\s*번\s*(?P<label>상자|박스|물체|팔레트)",
    re.IGNORECASE,
)
STRUCTURED_ROBOT_PATTERNS = (
    re.compile(
        r"(?<![A-Za-z0-9가-힣_-])(?P<id>[A-Za-z0-9][A-Za-z0-9_-]*)\s*"
        r"(?:번|호)?\s*로봇",
        re.IGNORECASE,
    ),
    re.compile(
        r"로봇\s*(?P<id>[A-Za-z0-9][A-Za-z0-9_-]*)"
        r"(?=(?:은|는|이|가|을|를)?(?:\s|$|[,.;:!?]))",
        re.IGNORECASE,
    ),
)

ZONE_PREFIX_PARTICLES = (
    "을",
    "를",
    "이",
    "가",
    "은",
    "는",
    "에",
    "에서",
    "에게",
    "와",
    "과",
)
ZONE_PREFIX_ADVERBS = {"빨리", "즉시", "지금", "먼저", "천천히", "안전하게"}
ZONE_PREFIX_SYNTAX_SUFFIXES = (
    *ZONE_PREFIX_PARTICLES,
    "으로",
    "로",
    "까지",
    "에서",
    "부터",
    "해",
    "하고",
    "해서",
    "한",
    "후",
    "뒤",
)


@dataclass(frozen=True)
class DestinationCandidate:
    value: dict[str, object]
    span: tuple[int, int]


def _robot_is_mentioned(instruction: str, robot_id: str) -> bool:
    escaped = re.escape(robot_id)
    before_robot = re.compile(
        rf"(?<![A-Za-z0-9가-힣_-]){escaped}\s*(?:번|호)?\s*로봇",
        re.IGNORECASE,
    )
    after_robot = re.compile(
        rf"로봇\s*(?:ID\s*[:=]?\s*)?{escaped}"
        rf"(?=(?:은|는|이|가|을|를)?(?:\s|$|[,.;:!?]))",
        re.IGNORECASE,
    )
    if before_robot.search(instruction) or after_robot.search(instruction):
        return True

    # Conventional robot IDs are unambiguous enough to accept without the noun.
    if re.match(r"^(?:robot|bot)[-_]", robot_id, re.IGNORECASE):
        standalone = re.compile(
            rf"(?<![A-Za-z0-9_-]){escaped}(?![A-Za-z0-9_-])",
            re.IGNORECASE,
        )
        return standalone.search(instruction) is not None
    return False


def _extract_structured_robot_ids(instruction: str) -> set[str]:
    return {
        match.group("id")
        for pattern in STRUCTURED_ROBOT_PATTERNS
        for match in pattern.finditer(instruction)
    }


def _select_robot_id(
    request: NaturalLanguageTaskRequest,
    instruction: str,
    known_robot_ids: list[str],
) -> str:
    known_ids = set(known_robot_ids)
    known_ids_by_casefold = {robot_id.casefold(): robot_id for robot_id in known_ids}
    mentioned_known = {
        robot_id
        for robot_id in known_ids
        if _robot_is_mentioned(instruction, robot_id)
    }
    structured_mentions = _extract_structured_robot_ids(instruction)
    unknown_mentions = {
        mention
        for mention in structured_mentions
        if mention.casefold() not in known_ids_by_casefold
    }
    if unknown_mentions:
        unknown = ", ".join(sorted(unknown_mentions))
        raise TaskParseError(
            f"등록되지 않은 로봇이 명령에 포함되어 있습니다: {unknown}",
            TaskParseErrorCode.ROBOT_NOT_FOUND,
        )
    if len(mentioned_known) > 1:
        raise TaskParseError(
            "여러 로봇이 언급되어 대상 로봇을 하나로 결정할 수 없습니다.",
            TaskParseErrorCode.ROBOT_AMBIGUOUS,
        )

    explicit_robot_id = request.target_robot_id
    if explicit_robot_id is not None:
        if explicit_robot_id not in known_ids:
            raise TaskParseError(
                f"등록되지 않은 로봇입니다: {explicit_robot_id}",
                TaskParseErrorCode.ROBOT_NOT_FOUND,
            )
        if mentioned_known and explicit_robot_id not in mentioned_known:
            mentioned = next(iter(mentioned_known))
            raise TaskParseError(
                f"target_robot_id({explicit_robot_id})와 문장 속 로봇({mentioned})이 다릅니다.",
                TaskParseErrorCode.ROBOT_CONFLICT,
            )
        return explicit_robot_id

    if mentioned_known:
        return next(iter(mentioned_known))
    if len(known_ids) == 1:
        return next(iter(known_ids))
    raise TaskParseError(
        "대상 로봇을 식별할 수 없습니다. target_robot_id를 지정해 주세요.",
        TaskParseErrorCode.ROBOT_REQUIRED,
    )


def _brackets_are_balanced(text: str) -> bool:
    pairs = {")": "(", "]": "["}
    stack: list[str] = []
    for character in text:
        if character in "([":
            stack.append(character)
        elif character in ")]":
            if not stack or stack.pop() != pairs[character]:
                return False
    return not stack


def _to_finite_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise TaskParseError(
            "좌표 숫자를 해석할 수 없습니다.",
            TaskParseErrorCode.DESTINATION_INVALID,
        ) from exc
    if not math.isfinite(parsed):
        raise TaskParseError(
            "좌표에는 유한한 숫자만 사용할 수 있습니다.",
            TaskParseErrorCode.DESTINATION_INVALID,
        )
    return parsed


def _coordinate_value(match: re.Match[str], frame_id: str) -> dict[str, object]:
    return {
        "type": "COORDINATE",
        "frame_id": frame_id,
        "position": {
            "x": _to_finite_float(match.group("x")),
            "y": _to_finite_float(match.group("y")),
            "z": _to_finite_float(match.group("z")) if match.group("z") else 0.0,
        },
    }


def _extract_coordinate_candidates(
    instruction: str,
    frame_id: str,
) -> list[DestinationCandidate]:
    bracket_cue = re.search(
        r"(?:좌표|위치)\s*:?\s*[\(\[]|[\(\[][^\n]{0,100}(?:좌표|위치)",
        instruction,
        re.IGNORECASE,
    )
    if bracket_cue and not _brackets_are_balanced(instruction):
        raise TaskParseError(
            "좌표 괄호가 닫히지 않았거나 서로 맞지 않습니다.",
            TaskParseErrorCode.DESTINATION_INVALID,
        )

    candidates: list[DestinationCandidate] = []
    for match in BRACKETED_COORDINATE_PATTERN.finditer(instruction):
        if (match.group("open"), match.group("close")) not in {("(", ")"), ("[", "]")}:
            continue
        before = instruction[max(0, match.start() - 16) : match.start()]
        after = instruction[match.end() :]
        has_prefix = (
            re.search(r"(?:좌표|위치)\s*:?\s*$", before, re.IGNORECASE)
            or "x" in match.group(0).lower()
            or "y" in match.group(0).lower()
        )
        has_destination_suffix = re.match(
            r"\s*(?:좌표|위치)?\s*(?:(?:으로|로)(?!부터)|까지|에(?!서))",
            after,
            re.IGNORECASE,
        )
        ends_instruction = re.fullmatch(r"\s*[.!]?\s*", after) is not None
        if (has_prefix or has_destination_suffix) and (
            has_destination_suffix or ends_instruction
        ):
            candidates.append(
                DestinationCandidate(_coordinate_value(match, frame_id), match.span())
            )

    for match in PLAIN_COORDINATE_PATTERN.finditer(instruction):
        if any(start <= match.start() < end for start, end in (c.span for c in candidates)):
            continue
        candidates.append(DestinationCandidate(_coordinate_value(match, frame_id), match.span()))

    if bracket_cue and not candidates:
        raise TaskParseError(
            "좌표 형식이 올바르지 않습니다. x, y와 선택적인 z 값을 확인해 주세요.",
            TaskParseErrorCode.DESTINATION_INVALID,
        )
    return candidates


def _extract_zone_candidates(instruction: str) -> list[DestinationCandidate]:
    candidates: list[DestinationCandidate] = []
    for match in QUOTED_ZONE_PATTERN.finditer(instruction):
        zone_id = "".join(match.group("label").split())
        candidates.append(
            DestinationCandidate({"type": "ZONE", "zone_id": zone_id}, match.span())
        )

    for match in ZONE_PATTERN.finditer(instruction):
        if any(start <= match.start() < end for start, end in (c.span for c in candidates)):
            continue
        name = match.group("name")
        has_latin = re.search(r"[A-Za-z]", name) is not None
        has_hangul = re.search(r"[가-힣]", name) is not None
        has_glued_entity = re.match(
            r"(?:로봇|상자|박스|물체|팔레트)(?:은|는|이|가|을|를).+",
            name,
        )
        has_glued_adverb = any(
            name.startswith(adverb) and name != adverb
            for adverb in ZONE_PREFIX_ADVERBS
        )
        if (has_latin and has_hangul) or has_glued_entity or has_glued_adverb:
            raise TaskParseError(
                "구역명 형식이 모호합니다. 조사·부사와 구역명 사이에 공백을 넣어 주세요.",
                TaskParseErrorCode.DESTINATION_INVALID,
            )

        prefix = instruction[: match.start()].rstrip()
        prefix_match = re.search(r"([A-Za-z0-9가-힣_-]+)$", prefix)
        if prefix_match:
            prefix_token = prefix_match.group(1)
            is_syntax_token = prefix_token.endswith(ZONE_PREFIX_SYNTAX_SUFFIXES)
            if not is_syntax_token and prefix_token not in ZONE_PREFIX_ADVERBS:
                raise TaskParseError(
                    "공백이 포함된 구역명은 따옴표로 묶어 주세요. 예: \"조립 라인 구역\"으로",
                    TaskParseErrorCode.DESTINATION_INVALID,
                )
        zone_id = f"{name}{match.group('kind')}"
        candidates.append(
            DestinationCandidate({"type": "ZONE", "zone_id": zone_id}, match.span())
        )
    return candidates


def _extract_destination(instruction: str, frame_id: str) -> DestinationCandidate:
    candidates = _extract_coordinate_candidates(instruction, frame_id)
    candidates.extend(_extract_zone_candidates(instruction))
    if not candidates:
        raise TaskParseError(
            "목적지를 식별할 수 없습니다. 구역/지점 또는 좌표를 포함해 주세요.",
            TaskParseErrorCode.DESTINATION_REQUIRED,
        )
    if len(candidates) > 1:
        raise TaskParseError(
            "목적지가 여러 개이므로 단일 작업으로 안전하게 해석할 수 없습니다.",
            TaskParseErrorCode.DESTINATION_AMBIGUOUS,
        )
    return candidates[0]


def _extract_object_id(
    instruction: str,
    destination_span: tuple[int, int],
) -> str | None:
    start, end = destination_span
    masked = instruction[:start] + " " * (end - start) + instruction[end:]
    object_ids = {
        "".join(match.group("object").split())
        for match in CANONICAL_OBJECT_PATTERN.finditer(masked)
    }
    for pattern in (LABEL_OBJECT_PATTERN, NUMBERED_OBJECT_PATTERN):
        for match in pattern.finditer(masked):
            object_ids.add(f"{match.group('label')}{match.group('id')}")
    if len(object_ids) > 1:
        raise TaskParseError(
            "대상 물체가 여러 개이므로 단일 작업으로 해석할 수 없습니다.",
            TaskParseErrorCode.OBJECT_AMBIGUOUS,
        )
    return next(iter(object_ids), None)


def parse_task_order_preview(
    request: NaturalLanguageTaskRequest,
    known_robot_ids: list[str],
) -> TaskOrderPreview:
    instruction = request.instruction.strip()
    try:
        validate_executable_instruction(instruction)
        control_action = detect_control_action(instruction)
    except IntentDetectionError as exc:
        raise TaskParseError(
            str(exc),
            TaskParseErrorCode.INVALID_INSTRUCTION,
        ) from exc
    if control_action is not None:
        raise TaskParseError(
            f"{control_action.value} 명령은 현재 TaskOrder v1 자동 변환에서 지원하지 않습니다.",
            TaskParseErrorCode.UNSUPPORTED_CONTROL_ACTION,
        )

    robot_id = _select_robot_id(request, instruction, known_robot_ids)
    destination = _extract_destination(instruction, request.frame_id)
    target_object_id = _extract_object_id(instruction, destination.span)

    try:
        action_type = detect_requested_action(
            instruction,
            has_target_object=target_object_id is not None,
        )
    except IntentDetectionError as exc:
        raise TaskParseError(
            str(exc),
            TaskParseErrorCode.ACTION_INVALID,
        ) from exc
    if action_type not in {ActionType.MOVE_TO, ActionType.MANIPULATE}:
        raise TaskParseError(
            f"현재 지원하지 않는 작업 유형입니다: {action_type.value}",
            TaskParseErrorCode.ACTION_UNSUPPORTED,
        )

    return TaskOrderPreview(
        instruction=instruction,
        target_robot_id=robot_id,
        target_object_id=target_object_id,
        destination=destination.value,
        action_type=action_type,
    )


def parse_task_order(
    request: NaturalLanguageTaskRequest,
    known_robot_ids: list[str],
) -> TaskOrder:
    preview = parse_task_order_preview(request, known_robot_ids)
    return TaskOrder(
        instruction=preview.instruction,
        target_robot_id=preview.target_robot_id,
        target_object_id=preview.target_object_id,
        destination=preview.destination,
    )
