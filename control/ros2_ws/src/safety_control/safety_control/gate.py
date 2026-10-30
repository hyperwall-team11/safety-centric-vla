"""HOLD / E-Stop 판단. 어댑터 종류와 무관하게 공통으로 쓴다 (스키마 v1.1 9장, 개발계획서 5-6장).

- E-Stop: /safety/estop의 active 값을 그대로 따른다. 해석할 수 없는 메시지는 정지로 본다 (Fail-Closed).
- 하트비트: 검증 서버가 5Hz로 보내며, 1초(5주기) 동안 없으면 HOLD 대상이다.
"""
from __future__ import annotations

from datetime import datetime, timezone

HEARTBEAT_TIMEOUT_S = 1.0


def utc_iso(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat()


def parse_iso(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


class SafetyGate:
    def __init__(self, heartbeat_timeout: float = HEARTBEAT_TIMEOUT_S):
        self.heartbeat_timeout = heartbeat_timeout
        self.estop_active = False
        self.last_heartbeat: float | None = None

    def on_heartbeat(self, now: float) -> None:
        self.last_heartbeat = now

    def heartbeat_lost(self, now: float) -> bool:
        """하트비트를 받은 적이 없거나 제한 시간을 넘겼으면 True."""
        return self.last_heartbeat is None or now - self.last_heartbeat > self.heartbeat_timeout

    def heartbeat_expired(self, now: float) -> bool:
        """한 번 이상 받은 뒤 끊긴 경우만 True. 자율 HOLD 진입 조건."""
        return self.last_heartbeat is not None and self.heartbeat_lost(now)

    def on_estop(self, msg: dict, now: float) -> dict | None:
        """E-Stop 상태를 갱신한다. 정지 요청이면 발원지와 지연(ms)을, 해제면 None을 돌려준다."""
        self.estop_active = bool(msg.get("active", True))
        if not self.estop_active:
            return None
        latency_ms = None
        if msg.get("issued_at"):
            latency_ms = round((now - parse_iso(msg["issued_at"])) * 1000, 2)
        return {"triggered_by": msg.get("triggered_by", "RULE_ENGINE"), "latency_ms": latency_ms}

    def resume_blocker(self, now: float) -> str | None:
        """RESUME을 막는 사유 코드. 복귀 가능하면 None."""
        if self.estop_active:
            return "ROBOT_HELD"
        if self.heartbeat_lost(now):
            return "NO_HEARTBEAT"
        return None
