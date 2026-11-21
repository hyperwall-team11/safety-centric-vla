from __future__ import annotations

import asyncio
from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True)
class StateSubscription:
    subscription_id: int
    event: asyncio.Event


class StateBroadcaster:
    """Thread-safe notification boundary for async state subscribers."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._revision = 0
        self._next_subscription_id = 1
        self._subscribers: dict[
            int,
            tuple[asyncio.AbstractEventLoop, asyncio.Event],
        ] = {}

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    def subscribe(self) -> StateSubscription:
        loop = asyncio.get_running_loop()
        event = asyncio.Event()
        with self._lock:
            subscription_id = self._next_subscription_id
            self._next_subscription_id += 1
            self._subscribers[subscription_id] = (loop, event)
        return StateSubscription(subscription_id=subscription_id, event=event)

    def unsubscribe(self, subscription_id: int) -> None:
        with self._lock:
            self._subscribers.pop(subscription_id, None)

    def publish(self) -> int:
        with self._lock:
            self._revision += 1
            revision = self._revision
            subscribers = list(self._subscribers.values())
        for loop, event in subscribers:
            if loop.is_closed():
                continue
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:
                # 연결 종료와 publish가 경합하면 loop가 검사 직후 닫힐 수 있다.
                continue
        return revision


state_broadcaster = StateBroadcaster()
