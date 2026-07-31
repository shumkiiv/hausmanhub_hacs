"""Framework-independent contract and fan-out for HausmanHub live events."""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
from itertools import count
from typing import Final


EVENT_CONTRACT_NAME: Final = "hausman-hub-event"
EVENT_CONTRACT_VERSION: Final = 1
EVENT_STREAM_HEARTBEAT_SECONDS: Final = 30
EVENT_STREAM_QUEUE_SIZE: Final = 32
EVENT_STREAM_REPLAY_SIZE: Final = 128


class EventStreamBroker:
    """Fan out bounded live events without letting a slow tablet block HA."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, object] | None]] = set()
        self._sequence = count(1)
        self._replay: deque[dict[str, object]] = deque(maxlen=EVENT_STREAM_REPLAY_SIZE)
        self._closed = False

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def subscribe(
        self, after_event_id: str | None = None
    ) -> asyncio.Queue[dict[str, object] | None]:
        queue, _ = self.subscribe_with_resume(after_event_id)
        return queue

    def subscribe_with_resume(
        self, after_event_id: str | None = None
    ) -> tuple[asyncio.Queue[dict[str, object] | None], bool]:
        """Subscribe and decide replayability atomically in the event loop."""

        if self._closed:
            raise RuntimeError("event stream is closed")
        queue: asyncio.Queue[dict[str, object] | None] = asyncio.Queue(
            maxsize=EVENT_STREAM_QUEUE_SIZE
        )
        replay, resumable = self._resume_window(after_event_id)
        if resumable:
            for message in replay:
                queue.put_nowait(message)
        self._subscribers.add(queue)
        return queue, resumable

    def replay_after(self, event_id: str | None) -> tuple[dict[str, object], ...]:
        """Return retained events strictly after a known SSE event id."""

        replay, _ = self._resume_window(event_id)
        return replay

    def can_resume(self, event_id: str | None) -> bool:
        _, resumable = self._resume_window(event_id)
        return resumable

    def _resume_window(
        self, event_id: str | None
    ) -> tuple[tuple[dict[str, object], ...], bool]:
        if event_id is None:
            return (), True
        retained = tuple(self._replay)
        for index, message in enumerate(retained):
            if message.get("id") == event_id:
                replay = retained[index + 1 :]
                return replay, len(replay) <= EVENT_STREAM_QUEUE_SIZE
        return (), False

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
        self._replay.append(message)
        for queue in tuple(self._subscribers):
            if queue.full():
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(
                    {
                        **message,
                        "type": "snapshot_invalidated",
                        "data": {
                            "reason": "state_changed",
                            "state_revision": None,
                        },
                    }
                )
            else:
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
