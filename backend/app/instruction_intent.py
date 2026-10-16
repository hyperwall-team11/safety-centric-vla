from __future__ import annotations

import re

from .models import ActionType


class IntentDetectionError(ValueError):
    pass


NEGATIVE_PATTERN = re.compile(
    r"(?:지\s*마(?:세요)?|지\s*말(?:아|고|라|아줘)?|하지\s*마(?:세요)?|"
    r"하지\s*말(?:아|고|라)?|지\s*않|말고|"
    r"(?:서는|선)\s*안\s*(?:돼|됩니다?|된다)|"
    r"(?:하면|해도|해서는|해선)\s*안\s*(?:돼|됩니다?|된다))",
    re.IGNORECASE,
)
CONDITIONAL_PATTERN = re.compile(
    r"(?:하면|한다면|할\s*경우|했으면|라면|된다면)|"
    r"(?:[^\s,]+(?:으)?면)(?=\s|,)",
    re.IGNORECASE,
)
QUESTION_PATTERN = re.compile(
    r"(?:\?|(?:할|갈|옮길|보낼|이동할)까(?:요)?\s*[.!]?\s*$|"
    r"해도\s*(?:돼|되나|될까)(?:요)?\s*[.!]?\s*$|"
    r"(?:해야|해도)\s*(?:하나|되는지|좋을지)(?:요)?\s*[.!]?\s*$)",
    re.IGNORECASE,
)
PAST_STATEMENT_PATTERN = re.compile(
    r"(?:이동|운반|배달|이송|정지|중지|재개|시작)(?:했어|했다|했음|했어요)\s*[.!]?\s*$|"
    r"(?:갔어|갔다|옮겼어|옮겼다|보냈어|보냈다)\s*[.!]?\s*$",
    re.IGNORECASE,
)

CONTROL_RELEASE_PATTERN = re.compile(
    r"(?:일시\s*)?(?:정지|중지)\s*(?:상태|모드)?\s*(?:를|을)?\s*(?:해제|풀)",
    re.IGNORECASE,
)
STOP_PATTERN = re.compile(
    r"(?:일시\s*)?(?:정지|중지|중단)|멈춰|멈추(?:어|세요|라)?|스톱|\bstop\b",
    re.IGNORECASE,
)
RESUME_PATTERN = re.compile(
    r"(?:재개(?:해|하세요|해줘)?|계속\s*(?:해|진행(?:해|하세요)?)|"
    r"다시\s*시작(?:해|하세요|해줘)?|\bresume\b)",
    re.IGNORECASE,
)

MOVE_COMMAND_PATTERN = re.compile(
    r"(?:(?:이동|출발|주행)\s*(?:"
    r"해(?:\s*(?:줘|주세요))?|하세요|"
    r"시켜(?:\s*(?:줘|주세요))?|하라|해라)|"
    r"가(?:\s*(?:줘|주세요))?|가세요|가라)\s*[.!]?\s*$",
    re.IGNORECASE,
)
MANIPULATE_COMMAND_PATTERN = re.compile(
    r"(?:옮겨(?:\s*(?:줘|주세요))?|옮기(?:세요|라)|"
    r"(?:운반|이송|배달|적재|하역)\s*(?:"
    r"해(?:\s*(?:줘|주세요))?|하세요|하라)|"
    r"보내(?:\s*(?:줘|주세요|세요|라))?|"
    r"가져다\s*(?:놔|놓아|줘|주세요)|"
    r"가져가(?:\s*(?:줘|주세요|세요|라))?)\s*[.!]?\s*$",
    re.IGNORECASE,
)


def validate_executable_instruction(instruction: str) -> None:
    """Reject utterances that are unsafe to treat as an execution request."""

    if not instruction.strip():
        raise IntentDetectionError("작업 명령이 비어 있습니다.")
    if NEGATIVE_PATTERN.search(instruction):
        raise IntentDetectionError("부정형 또는 대안형 문장은 실행 명령으로 해석하지 않습니다.")
    if CONDITIONAL_PATTERN.search(instruction):
        raise IntentDetectionError("조건문은 실행 명령으로 해석하지 않습니다.")
    if QUESTION_PATTERN.search(instruction):
        raise IntentDetectionError("질문은 실행 명령으로 해석하지 않습니다.")
    if PAST_STATEMENT_PATTERN.search(instruction):
        raise IntentDetectionError("과거 작업 서술은 실행 명령으로 해석하지 않습니다.")


def detect_control_action(instruction: str) -> ActionType | None:
    """Return an explicit STOP/RESUME intent after ambiguity checks."""

    validate_executable_instruction(instruction)
    if CONTROL_RELEASE_PATTERN.search(instruction):
        return ActionType.RESUME

    has_stop = STOP_PATTERN.search(instruction) is not None
    has_resume = RESUME_PATTERN.search(instruction) is not None
    if has_stop and has_resume:
        raise IntentDetectionError("정지와 재개 의도가 한 명령에 함께 포함되어 있습니다.")
    if has_stop:
        return ActionType.STOP
    if has_resume:
        return ActionType.RESUME
    return None


def detect_requested_action(instruction: str, *, has_target_object: bool) -> ActionType:
    """Classify one supported executable command without guessing missing entities."""

    control_action = detect_control_action(instruction)
    if control_action is not None:
        return control_action

    has_move = MOVE_COMMAND_PATTERN.search(instruction) is not None
    has_manipulate = MANIPULATE_COMMAND_PATTERN.search(instruction) is not None
    if has_move and has_manipulate:
        raise IntentDetectionError("이동과 물체 조작이 한 문장에 함께 있어 단일 작업으로 해석할 수 없습니다.")

    if has_target_object:
        moves_object_explicitly = re.search(r"이동\s*시켜", instruction) is not None
        if has_manipulate or (has_move and moves_object_explicitly):
            return ActionType.MANIPULATE
        if has_move:
            raise IntentDetectionError("물체 대상에는 옮기기/운반하기 같은 조작 동사가 필요합니다.")
    else:
        if has_move or (has_manipulate and re.search(r"보내", instruction)):
            return ActionType.MOVE_TO
        if has_manipulate:
            raise IntentDetectionError("물체 조작 명령에는 대상 물체 ID가 필요합니다.")

    raise IntentDetectionError("지원하는 실행 동사를 식별할 수 없습니다.")
