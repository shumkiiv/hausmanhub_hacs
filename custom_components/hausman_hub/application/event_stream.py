"""Framework-independent contract and fan-out for HausmanHub live events."""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
from itertools import count
from secrets import token_hex
from typing import Final

from ..correlation import new_correlation_id, validate_correlation_id


EVENT_CONTRACT_NAME: Final = "hausman-hub-event"
EVENT_CONTRACT_VERSION: Final = 1
EVENT_STREAM_HEARTBEAT_SECONDS: Final = 30
EVENT_STREAM_QUEUE_SIZE: Final = 32
EVENT_STREAM_REPLAY_SIZE: Final = 128
_SESSION_ONLY_EVENT_TYPES: Final = frozenset({"hello", "heartbeat"})


class EventStreamBroker:
    """Fan out bounded live events without letting a slow tablet block HA."""

    def __init__(self, *, stream_id: str | None = None) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, object] | None]] = set()
        self._sequence = count(1)
        self._replay: deque[dict[str, object]] = deque(maxlen=EVENT_STREAM_REPLAY_SIZE)
        self._stream_id = validate_correlation_id(stream_id or token_hex(8))
        self._closed = False

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    @property
    def stream_id(self) -> str:
        return self._stream_id

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

    def event(
        self,
        event_type: str,
        data: dict[str, object],
        *,
        correlation_id: str | None = None,
    ) -> dict[str, object]:
        sequence = next(self._sequence)
        resolved_correlation_id = (
            new_correlation_id()
            if correlation_id is None
            else validate_correlation_id(correlation_id)
        )
        return {
            "contract": {
                "name": EVENT_CONTRACT_NAME,
                "version": EVENT_CONTRACT_VERSION,
            },
            "id": f"evt-{self._stream_id}-{sequence}",
            "correlation_id": resolved_correlation_id,
            "type": event_type,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }

    def publish(
        self,
        event_type: str,
        data: dict[str, object],
        *,
        correlation_id: str | None = None,
    ) -> dict[str, object]:
        message = self.event(
            event_type,
            data,
            correlation_id=correlation_id,
        )
        if self._closed:
            return message
        if event_type not in _SESSION_ONLY_EVENT_TYPES:
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
                            "replay_status": "gap",
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
            "stream_id": broker.stream_id,
            "replay": {
                "strategy": "last_event_id",
                "request_header": "Last-Event-ID",
                "max_events": EVENT_STREAM_REPLAY_SIZE,
                "delivery_queue_limit": EVENT_STREAM_QUEUE_SIZE,
                "survives_restart": False,
            },
        },
    )


def heartbeat_event(
    broker: EventStreamBroker, sequence: int
) -> dict[str, object]:
    return broker.event("heartbeat", {"sequence": sequence})
