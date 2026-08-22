"""Failure isolation gates for optional media and voice vendors."""

from __future__ import annotations

import asyncio
import unittest

from custom_components.hausman_hub.application.vendor_resilience import (
    VendorCircuitBreaker,
    VendorServiceUnavailable,
)


class VendorCircuitBreakerTest(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_is_bounded_and_does_not_block_another_vendor(self) -> None:
        breaker = VendorCircuitBreaker(
            timeout_seconds=0.05,
            failure_threshold=1,
            cooldown_seconds=10,
        )

        async def slow() -> None:
            await asyncio.sleep(1)

        with self.assertRaisesRegex(VendorServiceUnavailable, "vendor_timeout"):
            await breaker.async_execute("yandex_station.play_media", slow)
        with self.assertRaisesRegex(VendorServiceUnavailable, "vendor_circuit_open"):
            await breaker.async_execute("yandex_station.play_media", slow)
        result = await breaker.async_execute(
            "home_assistant.conversation", lambda: asyncio.sleep(0, result="ok")
        )
        self.assertEqual("ok", result)
        self.assertEqual(
            "open", breaker.snapshot()["yandex_station.play_media"]["state"]
        )

    async def test_half_open_probe_closes_circuit_after_cooldown(self) -> None:
        now = [100.0]
        breaker = VendorCircuitBreaker(
            timeout_seconds=1,
            failure_threshold=1,
            cooldown_seconds=5,
            monotonic=lambda: now[0],
        )

        async def failed() -> None:
            raise RuntimeError("provider offline")

        with self.assertRaisesRegex(VendorServiceUnavailable, "vendor_error"):
            await breaker.async_execute("media_player.turn_on", failed)
        now[0] += 6
        result = await breaker.async_execute(
            "media_player.turn_on", lambda: asyncio.sleep(0, result="recovered")
        )
        self.assertEqual("recovered", result)
        self.assertEqual("closed", breaker.snapshot()["media_player.turn_on"]["state"])

