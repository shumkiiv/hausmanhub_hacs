"""Time budget and recovery tests for external adapter calls."""

from __future__ import annotations

import asyncio
import unittest

from custom_components.hausman_hub.application.adapter_circuit_breaker import (
    AdapterCircuitBreaker,
    AdapterCircuitOpenError,
    AdapterTimeoutError,
)


class AdapterCircuitBreakerTest(unittest.IsolatedAsyncioTestCase):
    async def test_three_failures_open_then_one_probe_recovers(self) -> None:
        now = [100.0]
        calls = 0
        breaker = AdapterCircuitBreaker(monotonic=lambda: now[0])

        async def fail() -> None:
            nonlocal calls
            calls += 1
            raise RuntimeError("provider failed")

        for _ in range(3):
            with self.assertRaises(RuntimeError):
                await breaker.async_run("climate", fail)

        self.assertEqual("open", breaker.snapshot()[0]["state"])
        with self.assertRaises(AdapterCircuitOpenError):
            await breaker.async_run("climate", fail)
        self.assertEqual(3, calls)

        now[0] += 31.0

        async def recover() -> str:
            nonlocal calls
            calls += 1
            return "ok"

        self.assertEqual("ok", await breaker.async_run("climate", recover))
        health = breaker.snapshot()[0]
        self.assertEqual("closed", health["state"])
        self.assertEqual(0, health["consecutiveFailures"])
        self.assertEqual(1, health["openCount"])
        self.assertEqual(1, health["recoveryCount"])
        self.assertEqual(4, calls)

    async def test_timeout_is_counted_and_cancels_slow_call(self) -> None:
        breaker = AdapterCircuitBreaker(
            budget_seconds=0.001,
            failure_threshold=1,
        )

        async def slow() -> None:
            await asyncio.sleep(1)

        with self.assertRaises(AdapterTimeoutError):
            await breaker.async_run("media_player", slow)

        health = breaker.snapshot()[0]
        self.assertEqual("open", health["state"])
        self.assertEqual(1, health["timeoutCount"])
        self.assertEqual(1, health["openCount"])

    async def test_invalid_adapter_is_rejected_before_operation(self) -> None:
        breaker = AdapterCircuitBreaker()
        called = False

        async def operation() -> None:
            nonlocal called
            called = True

        with self.assertRaises(ValueError):
            await breaker.async_run("climate.private-id", operation)
        self.assertFalse(called)
