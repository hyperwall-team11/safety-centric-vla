from __future__ import annotations

import re

from .models import NaturalLanguageTaskRequest, TaskOrder


class TaskParseError(ValueError):
    pass


COORDINATE_PATTERN = re.compile(
    r"(?:좌표|위치)\s*[\(\[]?\s*"
    r"(?:x\s*[:=]?\s*)?(?P<x>-?\d+(?:\.\d+)?)\s*[,\s]+"
    r"(?:y\s*[:=]?\s*)?(?P<y>-?\d+(?:\.\d+)?)"
    r"(?:\s*[,\s]+(?:z\s*[:=]?\s*)?(?P<z>-?\d+(?:\.\d+)?))?",
    re.IGNORECASE,
)
ROBOT_PATTERNS = (
    re.compile(r"(?P<id>[A-Za-z][A-Za-z0-9_-]*)\s*(?:번\s*)?로봇", re.IGNORECASE),
    re.compile(r"로봇\s*(?P<id>[A-Za-z0-9_-]+)", re.IGNORECASE),
)
ZONE_PATTERN = re.compile(
    r"(?P<name>[A-Za-z0-9가-힣_-]+)\s*"
    r"(?P<kind>구역|지점|창고|작업대|대기실)\s*(?:으로|로|까지)"
)
OBJECT_PATTERN = re.compile(
    r"(?P<object>obj[-_][A-Za-z0-9_-]+|(?:상자|물체|팔레트)\s*[A-Za-z0-9_-]+)",
    re.IGNORECASE,
)


def _extract_robot_id(instruction: str) -> str | None:
    for pattern in ROBOT_PATTERNS:
        match = pattern.search(instruction)
        if match:
            return match.group("id")
    return None


def _extract_destination(instruction: str, frame_id: str) -> dict[str, object] | None:
    coordinate = COORDINATE_PATTERN.search(instruction)
    if coordinate:
        return {
            "type": "COORDINATE",
            "frame_id": frame_id,
            "position": {
                "x": float(coordinate.group("x")),
                "y": float(coordinate.group("y")),
                "z": float(coordinate.group("z") or 0.0),
            },
        }

    zone = ZONE_PATTERN.search(instruction)
    if zone:
        return {"type": "ZONE", "zone_id": f"{zone.group('name')}{zone.group('kind')}"}
    return None


def parse_task_order(
    request: NaturalLanguageTaskRequest,
    known_robot_ids: list[str],
) -> TaskOrder:
    instruction = request.instruction.strip()
    robot_id = request.target_robot_id or _extract_robot_id(instruction)
    if robot_id is None and len(known_robot_ids) == 1:
        robot_id = known_robot_ids[0]
    if robot_id is None:
        raise TaskParseError("대상 로봇을 식별할 수 없습니다. target_robot_id를 지정해 주세요.")
    if robot_id not in known_robot_ids:
        raise TaskParseError(f"등록되지 않은 로봇입니다: {robot_id}")

    destination = _extract_destination(instruction, request.frame_id)
    if destination is None:
        raise TaskParseError("목적지를 식별할 수 없습니다. 구역/지점 또는 좌표를 포함해 주세요.")

    object_match = OBJECT_PATTERN.search(instruction)
    target_object_id = object_match.group("object").replace(" ", "") if object_match else None
    return TaskOrder(
        instruction=instruction,
        target_robot_id=robot_id,
        target_object_id=target_object_id,
        destination=destination,
    )
