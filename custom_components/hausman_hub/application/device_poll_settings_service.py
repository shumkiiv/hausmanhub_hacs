"""Versioned, fail-closed persistence for device polling preferences."""
from __future__ import annotations

import asyncio

from .device_poll_schedule import PollSettings


class PollSettingsConflict(ValueError):
    """The saved settings were changed by another client."""


class DevicePollSettingsService:
    def __init__(self, store: object) -> None:
        self._store = store
        self._current: tuple[int, PollSettings] | None = None
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        async with self._lock:
            self._current = None
            value = await self._store.async_load()
            if value is None:
                self._current = (0, PollSettings())
                return
            if (not isinstance(value, dict) or set(value) != {"version", "revision", "settings"}
                    or type(value["version"]) is not int or value["version"] != 1
                    or type(value["revision"]) is not int or value["revision"] < 0
                    or not isinstance(value["settings"], dict)
                    or set(value["settings"]) != {"enabled", "intervalMinutes"}):
                self._current = None
                raise ValueError("invalid polling settings document")
            try:
                settings = PollSettings(value["settings"]["enabled"], value["settings"]["intervalMinutes"])
            except ValueError:
                self._current = None
                raise
            self._current = (value["revision"], settings)

    @property
    def current(self) -> tuple[int, PollSettings]:
        if self._current is None:
            raise RuntimeError("polling settings not loaded")
        return self._current

    async def async_replace(self, expected_revision: int, settings: PollSettings) -> int:
        if type(expected_revision) is not int or expected_revision < 0 or not isinstance(settings, PollSettings):
            raise ValueError("invalid polling settings request")
        async with self._lock:
            revision, _ = self.current
            if revision != expected_revision:
                raise PollSettingsConflict("polling settings changed")
            next_revision = revision + 1
            await self._store.async_save({"version": 1, "revision": next_revision,
                "settings": {"enabled": settings.enabled, "intervalMinutes": settings.interval_minutes}})
            self._current = (next_revision, settings)
            return next_revision
