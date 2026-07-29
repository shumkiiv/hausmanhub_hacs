"""Framework-independent contract and fan-out for HausmanHub live events."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from itertools import count
from typing import Final


EVENT_CONTRACT_NAME: Final = "hausman-hub-event"
EVENT_CONTRACT_VERSION: Final = 1
EVENT_STREAM_HEARTBEAT_SECONDS: Final = 30
EVENT_STREAM_QUEUE_SIZE: Final = 32


class EventStreamBroker:
    """Fan out bounded live events without letting a slow tablet block HA."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, object] | None]] = set()
        self._sequence = count(1)
        self._closed = False

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def subscribe(self) -> asyncio.Queue[dict[str, object] | None]:
        if self._closed:
            raise RuntimeError("event stream is closed")
        queue: asyncio.Queue[dict[str, object] | None] = asyncio.Queue(
            maxsize=EVENT_STREAM_QUEUE_SIZE
        )
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, object] | None]) -> None:
        self._subscribers.discard(queue)

    def event(self, event_type: str, data: dict[str, object]) -> dict[str, object]:
        sequence = next(self._sequence)
        return {
            "contract": {
                "name": EVENT_CONTRACT_NAME,
                "version": EVENT_CONTRACT_VERSION,
            },
            "id": f"evt-{sequence}",
            "type": event_type,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }

    def publish(self, event_type: str, data: dict[str, object]) -> dict[str, object]:
        message = self.event(event_type, data)
        if self._closed:
            return message
        for queue in tuple(self._subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(message)
        return message

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for queue in tuple(self._subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(None)
        self._subscribers.clear()


def hello_event(broker: EventStreamBroker) -> dict[str, object]:
    return broker.event(
        "hello",
        {
            "api_major_version": 1,
            "heartbeat_seconds": EVENT_STREAM_HEARTBEAT_SECONDS,
        },
    )


def heartbeat_event(
    broker: EventStreamBroker, sequence: int
) -> dict[str, object]:
    return broker.event("heartbeat", {"sequence": sequence})
