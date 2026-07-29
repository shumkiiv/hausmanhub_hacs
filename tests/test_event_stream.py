"""Unit tests for the bounded HausmanHub real-time event fan-out."""

from __future__ import annotations

import asyncio
import unittest

from custom_components.hausman_hub.application.event_stream import (
    EVENT_STREAM_QUEUE_SIZE,
    EventStreamBroker,
    heartbeat_event,
    hello_event,
)


class EventStreamBrokerTest(unittest.IsolatedAsyncioTestCase):
    async def test_fans_out_contract_messages_and_unsubscribes(self) -> None:
        broker = EventStreamBroker()
        first = broker.subscribe()
        second = broker.subscribe()

        published = broker.publish(
            "snapshot_invalidated", {"reason": "state_changed"}
        )

        self.assertEqual(published, await first.get())
        self.assertEqual(published, await second.get())
        self.assertEqual("hausman-hub-event", published["contract"]["name"])
        self.assertEqual(1, published["contract"]["version"])
        broker.unsubscribe(second)
        self.assertEqual(1, broker.subscriber_count)

    async def test_slow_subscriber_is_bounded_to_fresh_events(self) -> None:
        broker = EventStreamBroker()
        queue = broker.subscribe()

        for sequence in range(EVENT_STREAM_QUEUE_SIZE + 7):
            broker.publish("heartbeat", {"sequence": sequence})

        self.assertEqual(EVENT_STREAM_QUEUE_SIZE, queue.qsize())
        oldest = await queue.get()
        self.assertEqual(7, oldest["data"]["sequence"])

    async def test_close_releases_waiters_and_rejects_new_subscribers(self) -> None:
        broker = EventStreamBroker()
        queue = broker.subscribe()
        broker.close()

        self.assertIsNone(await queue.get())
        with self.assertRaisesRegex(RuntimeError, "closed"):
            broker.subscribe()

    async def test_hello_and_heartbeat_have_explicit_payloads(self) -> None:
        broker = EventStreamBroker()

        hello = hello_event(broker)
        heartbeat = heartbeat_event(broker, 4)

        self.assertEqual("hello", hello["type"])
        self.assertEqual(30, hello["data"]["heartbeat_seconds"])
        self.assertEqual("heartbeat", heartbeat["type"])
        self.assertEqual(4, heartbeat["data"]["sequence"])


if __name__ == "__main__":
    unittest.main()
