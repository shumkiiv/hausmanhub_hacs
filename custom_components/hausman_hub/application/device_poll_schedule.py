"""Bounded scheduling policy for explicitly registered read-only refreshers.

This module does not discover devices, publish transport messages, persist
settings, start background tasks, or create evidence of physical state.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PollSettings:
    enabled: bool = False
    interval_minutes: int = 15

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("enabled must be boolean")
        if type(self.interval_minutes) is not int or not 1 <= self.interval_minutes <= 1440:
            raise ValueError("interval must be between 1 and 1440 minutes")


class DevicePollSchedule:
    """Serialize bounded polling of a caller-supplied, trusted device scope."""

    def __init__(self, poll: Callable[[str], Awaitable[bool]]) -> None:
        self._poll = poll
        self._settings = PollSettings()
        self._devices: tuple[str, ...] = ()
        self._next: dict[str, int] = {}
        self._last_tick: int | None = None
        self._generation = 0
        self._lock = asyncio.Lock()

    def configure(self, settings: PollSettings, devices: Iterable[str]) -> None:
        if not isinstance(settings, PollSettings):
            raise ValueError("validated settings required")
        scope = tuple(devices)
        if (len(scope) > 128
                or any(not isinstance(device, str) or not device for device in scope)
                or len(set(scope)) != len(scope)):
            raise ValueError("invalid device scope")
        if settings == self._settings and scope == self._devices:
            return
        self._settings, self._devices = settings, scope
        self._generation += 1
        self._next.clear()

    async def tick(self, now_ms: int) -> dict[str, str]:
        """Run due reads once; `confirmed` depends on the trusted callback."""
        if type(now_ms) is not int or now_ms < 0:
            raise ValueError("monotonic time required")
        async with self._lock:
            if self._last_tick is not None and now_ms < self._last_tick:
                raise ValueError("monotonic time moved backwards")
            self._last_tick = now_ms
            if not self._settings.enabled:
                return {}
            generation = self._generation
            semaphore = asyncio.Semaphore(4)

            async def read(device: str) -> tuple[str, str] | None:
                async with semaphore:
                    if generation != self._generation or not self._settings.enabled:
                        return None
                    if now_ms < self._next.get(device, 0):
                        return None
                    self._next[device] = now_ms + self._settings.interval_minutes * 60_000
                    try:
                        confirmed = await asyncio.wait_for(self._poll(device), timeout=8)
                    except Exception:
                        status = "failed"
                    else:
                        status = "confirmed" if confirmed is True else "unconfirmed"
                    return device, status

            values = await asyncio.gather(*(read(device) for device in self._devices))
            if generation != self._generation or not self._settings.enabled:
                return {}
            return dict(value for value in values if value is not None)
