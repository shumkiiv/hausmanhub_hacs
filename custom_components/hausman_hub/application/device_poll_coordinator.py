"""Local orchestration of saved poll preferences and a bounded runtime.

No transport discovery, public API, HA lifecycle or real-device writes.
"""
from __future__ import annotations

import asyncio
from collections.abc import Iterable

from .device_poll_runtime import DevicePollRuntime
from .device_poll_schedule import DevicePollSchedule, PollSettings
from .device_poll_settings_service import DevicePollSettingsService, PollSettingsConflict


async def _unused_poll(_device: str) -> bool:
    raise RuntimeError("scope validation must not poll")


class DevicePollCoordinator:
    def __init__(self, settings: DevicePollSettingsService, runtime: DevicePollRuntime,
                 devices: Iterable[str]) -> None:
        self._scope = tuple(devices)
        DevicePollSchedule(_unused_poll).configure(PollSettings(), self._scope)
        self._settings = settings
        self._runtime = runtime
        self._lock = asyncio.Lock()

    async def async_start(self) -> None:
        async with self._lock:
            await self._runtime.async_stop()
            await self._settings.async_load()
            _, settings = self._settings.current
            await self._runtime.async_configure(settings, self._scope)

    async def async_stop(self) -> None:
        async with self._lock:
            await self._runtime.async_stop()

    async def async_replace(self, revision: int, settings: PollSettings) -> int:
        async with self._lock:
            if type(revision) is not int or revision < 0 or not isinstance(settings, PollSettings):
                raise ValueError("invalid polling settings request")
            current_revision, current_settings = self._settings.current
            if current_revision != revision:
                raise PollSettingsConflict("polling settings changed")
            if settings == current_settings:
                expected_running = settings.enabled and bool(self._scope)
                if self._runtime.running != expected_running:
                    raise RuntimeError("poll lifecycle requires reload")
                return current_revision
            # Never leave old polling active during an interrupted save.
            await self._runtime.async_stop()
            new_revision = await self._settings.async_replace(revision, settings)
            try:
                await self._runtime.async_configure(settings, self._scope)
            except Exception:
                await self._runtime.async_stop()
                raise
            return new_revision
