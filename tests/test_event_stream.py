"""Unit tests for the bounded HausmanHub real-time event fan-out."""

from __future__ import annotations

import asyncio
import unittest

from custom_components.hausman_hub.application.event_stream import (
    EVENT_STREAM_QUEUE_SIZE,
    EVENT_STREAM_REPLAY_SIZE,
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
        self.assertRegex(published["correlation_id"], r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
        broker.unsubscribe(second)
        self.assertEqual(1, broker.subscriber_count)

    async def test_slow_subscriber_receives_an_explicit_snapshot_gap(self) -> None:
        broker = EventStreamBroker()
        queue = broker.subscribe()

        for sequence in range(EVENT_STREAM_QUEUE_SIZE + 7):
            broker.publish("heartbeat", {"sequence": sequence})

        messages = [await queue.get() for _ in range(queue.qsize())]
        gap = next(message for message in messages if message["type"] == "snapshot_invalidated")
        self.assertEqual("state_changed", gap["data"]["reason"])
        self.assertLessEqual(len(messages), EVENT_STREAM_QUEUE_SIZE)

    async def test_close_releases_waiters_and_rejects_new_subscribers(self) -> None:
        broker = EventStreamBroker()
        queue = broker.subscribe()
        broker.close()

        self.assertIsNone(await queue.get())
        with self.assertRaisesRegex(RuntimeError, "closed"):
            broker.subscribe()

    async def test_reconnect_replays_only_events_after_last_known_id(self) -> None:
        broker = EventStreamBroker()
        first = broker.publish("snapshot_invalidated", {"reason": "state_changed"})
        expected = [
            broker.publish("heartbeat", {"sequence": sequence})
            for sequence in range(3)
        ]

        self.assertTrue(broker.can_resume(first["id"]))
        queue = broker.subscribe(first["id"])

        self.assertEqual(expected, [await queue.get() for _ in expected])

    async def test_reconnect_fails_closed_when_gap_exceeds_client_queue(self) -> None:
        broker = EventStreamBroker()
        first = broker.publish("snapshot_invalidated", {"reason": "state_changed"})
        for sequence in range(EVENT_STREAM_QUEUE_SIZE + 1):
            broker.publish("heartbeat", {"sequence": sequence})

        self.assertFalse(broker.can_resume(first["id"]))

        queue, resumable = broker.subscribe_with_resume(first["id"])

        self.assertFalse(resumable)
        self.assertTrue(queue.empty())

    async def test_replay_history_is_bounded(self) -> None:
        broker = EventStreamBroker()
        first = broker.publish("snapshot_invalidated", {"reason": "state_changed"})
        for sequence in range(EVENT_STREAM_REPLAY_SIZE):
            broker.publish("heartbeat", {"sequence": sequence})

        self.assertFalse(broker.can_resume(first["id"]))

    async def test_hello_and_heartbeat_have_explicit_payloads(self) -> None:
        broker = EventStreamBroker()

        hello = hello_event(broker)
        heartbeat = heartbeat_event(broker, 4)

        self.assertEqual("hello", hello["type"])
        self.assertEqual(30, hello["data"]["heartbeat_seconds"])
        self.assertEqual("heartbeat", heartbeat["type"])
        self.assertEqual(4, heartbeat["data"]["sequence"])
        self.assertRegex(hello["correlation_id"], r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
        self.assertRegex(heartbeat["correlation_id"], r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

    async def test_supplied_correlation_id_is_preserved(self) -> None:
        broker = EventStreamBroker()

        published = broker.publish(
            "command_receipt",
            {"request_id": "request-1"},
            correlation_id="corr.command.0001",
        )

        self.assertEqual("corr.command.0001", published["correlation_id"])
        with self.assertRaises(ValueError):
            broker.publish("heartbeat", {}, correlation_id="invalid value")


if __name__ == "__main__":
    unittest.main()
