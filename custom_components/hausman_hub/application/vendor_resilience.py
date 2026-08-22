"""Bounded timeout and circuit breaker for optional vendor services."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import time
from typing import TypeVar


T = TypeVar("T")


class VendorServiceUnavailable(RuntimeError):
    """A vendor call was rejected, timed out, or failed in isolation."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class VendorCircuitBreaker:
    """Fail fast after repeated vendor failures and probe after cooldown."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 8.0,
        failure_threshold: int = 3,
        cooldown_seconds: float = 60.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 0.05 <= timeout_seconds <= 30:
            raise ValueError("vendor timeout must be between 0.05 and 30 seconds")
        if not 1 <= failure_threshold <= 10:
            raise ValueError("vendor failure threshold must be between 1 and 10")
        if not 1 <= cooldown_seconds <= 900:
            raise ValueError("vendor cooldown must be between 1 and 900 seconds")
        self.timeout_seconds = timeout_seconds
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._monotonic = monotonic
        self._states: dict[str, dict[str, object]] = {}
        self._lock = asyncio.Lock()

    async def async_execute(
        self,
        service_key: str,
        operation: Callable[[], Awaitable[T]],
    ) -> T:
        """Execute one bounded call without letting vendor latency spread."""

        if not service_key or len(service_key) > 128:
            raise ValueError("vendor service key is invalid")
        async with self._lock:
            state = self._states.setdefault(
                service_key,
                {"failures": 0, "opened_until": 0.0, "probe_running": False},
            )
            now = self._monotonic()
            opened_until = float(state["opened_until"])
            if opened_until > now:
                raise VendorServiceUnavailable("vendor_circuit_open")
            if opened_until and state["probe_running"] is True:
                raise VendorServiceUnavailable("vendor_circuit_open")
            if opened_until:
                state["probe_running"] = True
        try:
            result = await asyncio.wait_for(
                operation(), timeout=self.timeout_seconds
            )
        except asyncio.CancelledError:
            await self._finish_probe(service_key)
            raise
        except TimeoutError as error:
            await self._record_failure(service_key)
            raise VendorServiceUnavailable("vendor_timeout") from error
        except Exception as error:  # noqa: BLE001 - adapter boundary
            await self._record_failure(service_key)
            raise VendorServiceUnavailable("vendor_error") from error
        async with self._lock:
            state = self._states[service_key]
            state.update(failures=0, opened_until=0.0, probe_running=False)
        return result

    async def _record_failure(self, service_key: str) -> None:
        async with self._lock:
            state = self._states[service_key]
            failures = int(state["failures"]) + 1
            state["failures"] = failures
            state["probe_running"] = False
            if failures >= self.failure_threshold:
                state["opened_until"] = self._monotonic() + self.cooldown_seconds

    async def _finish_probe(self, service_key: str) -> None:
        async with self._lock:
            self._states[service_key]["probe_running"] = False

    def snapshot(self) -> dict[str, dict[str, object]]:
        """Return redacted diagnostics keyed by logical service, never entity ID."""

        now = self._monotonic()
        return {
            key: {
                "state": (
                    "open"
                    if float(value["opened_until"]) > now
                    else "half_open"
                    if value["probe_running"] is True
                    else "closed"
                ),
                "failureCount": int(value["failures"]),
                "retryAfterSeconds": max(
                    0, int(round(float(value["opened_until"]) - now))
                ),
            }
            for key, value in sorted(self._states.items())
        }
