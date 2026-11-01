"""Cancellable local lifecycle for explicitly configured read-only polls.

Not wired to Home Assistant: callers must supply trusted device callbacks and
must independently decide which physical observations are authoritative.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterable

from .device_poll_schedule import DevicePollSchedule, PollSettings


class DevicePollRuntime:
    def __init__(self, poll: Callable[[str], Awaitable[bool]], *,
                 now_ms: Callable[[], int] = lambda: int(time.monotonic() * 1000),
                 stop_timeout: float = 10.0) -> None:
        if not 0 < stop_timeout <= 30:
            raise ValueError("invalid stop timeout")
        self._poll = poll
        self._now_ms = now_ms
        self._stop_timeout = stop_timeout
        self._schedule = DevicePollSchedule(poll)
        self._task: asyncio.Task[None] | None = None
        self._configuration: tuple[PollSettings, tuple[str, ...]] | None = None
        self._last_result: tuple[int, dict[str, str]] | None = None
        self._lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def last_result(self) -> tuple[int, dict[str, str]] | None:
        if self._last_result is None:
            return None
        at, result = self._last_result
        return at, dict(result)

    async def _stop_locked(self) -> None:
        task = self._task
        if task is None:
            return
        self._schedule.configure(PollSettings(), ())
        self._configuration = None
        task.cancel()
        done, _ = await asyncio.wait({task}, timeout=self._stop_timeout)
        if not done:
            raise TimeoutError("poll did not stop; no replacement started")
        self._task = None
        try:
            task.result()
        except asyncio.CancelledError:
            pass

    async def async_stop(self) -> None:
        async with self._lock:
            self._last_result = None
            await self._stop_locked()
            self._configuration = None

    async def async_configure(self, settings: PollSettings, devices: Iterable[str]) -> None:
        scope = tuple(devices)
        candidate = DevicePollSchedule(self._poll)
        candidate.configure(settings, scope)  # Validate before stopping current reads.
        async with self._lock:
            if self._configuration == (settings, scope):
                return
            await self._stop_locked()
            self._last_result = None
            self._schedule = candidate
            self._configuration = (settings, scope)
            if settings.enabled and scope:
                self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while True:
            at = self._now_ms()
            result = await self._schedule.tick(at)
            if result:
                self._last_result = (at, result)
            await asyncio.sleep(1)
