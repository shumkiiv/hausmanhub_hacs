"""Bound Home Assistant calls into potentially slow external adapters."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import re
import time
from typing import TypeVar


_Result = TypeVar("_Result")
_ADAPTER_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

DEFAULT_ADAPTER_BUDGET_SECONDS = 5.0
DEFAULT_FAILURE_THRESHOLD = 3
DEFAULT_RECOVERY_SECONDS = 30.0


class AdapterCircuitOpenError(RuntimeError):
    """The adapter circuit is open and no external call was attempted."""


class AdapterTimeoutError(TimeoutError):
    """The adapter exceeded its bounded wall-clock budget."""


@dataclass(slots=True)
class _AdapterState:
    state: str = "closed"
    consecutive_failures: int = 0
    timeout_count: int = 0
    open_count: int = 0
    recovery_count: int = 0
    opened_until: float = 0.0
    probe_in_flight: bool = False


class AdapterCircuitBreaker:
    """Apply one bounded circuit per public Home Assistant service domain."""

    def __init__(
        self,
        *,
        budget_seconds: float = DEFAULT_ADAPTER_BUDGET_SECONDS,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        recovery_seconds: float = DEFAULT_RECOVERY_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 0.001 <= budget_seconds <= 30.0:
            raise ValueError("adapter budget must be between 0.001 and 30 seconds")
        if not 1 <= failure_threshold <= 10:
            raise ValueError("adapter failure threshold must be between 1 and 10")
        if not 0.001 <= recovery_seconds <= 3600.0:
            raise ValueError("adapter recovery must be between 0.001 and 3600 seconds")
        self._budget_seconds = budget_seconds
        self._failure_threshold = failure_threshold
        self._recovery_seconds = recovery_seconds
        self._monotonic = monotonic
        self._states: dict[str, _AdapterState] = {}
        self._lock = asyncio.Lock()

    async def async_run(
        self,
        adapter: str,
        operation: Callable[[], Awaitable[_Result]],
    ) -> _Result:
        """Run one call within budget or reject it while the circuit is open."""

        if _ADAPTER_NAME.fullmatch(adapter) is None:
            raise ValueError("adapter name is invalid")
        probe = False
        async with self._lock:
            state = self._states.setdefault(adapter, _AdapterState())
            now = self._monotonic()
            if state.state == "open":
                if now < state.opened_until or state.probe_in_flight:
                    raise AdapterCircuitOpenError(f"{adapter} circuit is open")
                state.state = "half_open"
                state.probe_in_flight = True
                probe = True
            elif state.state == "half_open":
                if state.probe_in_flight:
                    raise AdapterCircuitOpenError(f"{adapter} circuit is open")
                state.probe_in_flight = True
                probe = True

        try:
            result = await asyncio.wait_for(
                operation(),
                timeout=self._budget_seconds,
            )
        except asyncio.CancelledError:
            if probe:
                await self._release_cancelled_probe(adapter)
            raise
        except TimeoutError as error:
            await self._record_failure(adapter, timed_out=True, probe=probe)
            raise AdapterTimeoutError(f"{adapter} call exceeded its budget") from error
        except Exception:
            await self._record_failure(adapter, timed_out=False, probe=probe)
            raise
        await self._record_success(adapter, probe=probe)
        return result

    async def _release_cancelled_probe(self, adapter: str) -> None:
        async with self._lock:
            state = self._states[adapter]
            state.probe_in_flight = False
            state.state = "open"

    async def _record_failure(
        self, adapter: str, *, timed_out: bool, probe: bool
    ) -> None:
        async with self._lock:
            state = self._states[adapter]
            state.probe_in_flight = False
            state.consecutive_failures += 1
            if timed_out:
                state.timeout_count += 1
            if probe or state.consecutive_failures >= self._failure_threshold:
                state.state = "open"
                state.opened_until = self._monotonic() + self._recovery_seconds
                state.open_count += 1

    async def _record_success(self, adapter: str, *, probe: bool) -> None:
        async with self._lock:
            state = self._states[adapter]
            recovered = probe or state.consecutive_failures > 0
            state.state = "closed"
            state.probe_in_flight = False
            state.opened_until = 0.0
            state.consecutive_failures = 0
            if recovered:
                state.recovery_count += 1

    def snapshot(self) -> list[dict[str, object]]:
        """Return bounded aggregate metrics without targets or provider data."""

        now = self._monotonic()
        payload: list[dict[str, object]] = []
        for adapter, state in sorted(self._states.items()):
            visible_state = state.state
            if visible_state == "open" and now >= state.opened_until:
                visible_state = "half_open"
            payload.append(
                {
                    "adapter": adapter,
                    "state": visible_state,
                    "budgetMs": int(self._budget_seconds * 1000),
                    "consecutiveFailures": state.consecutive_failures,
                    "timeoutCount": state.timeout_count,
                    "openCount": state.open_count,
                    "recoveryCount": state.recovery_count,
                }
            )
        return payload
