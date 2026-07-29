"""Lifecycle and atomic persistence for native HausmanHub user settings."""

from __future__ import annotations

import asyncio

from ..domain.hub_settings import HausmanHubSettings


class SettingsServiceUnavailable(RuntimeError):
    """Settings were requested before the service completed loading."""


class HausmanHubSettingsService:
    def __init__(self, entry_id: str, store: object) -> None:
        self.entry_id = entry_id
        self._store = store
        self._settings: HausmanHubSettings | None = None
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        loaded = await self._store.async_load()
        if not isinstance(loaded, HausmanHubSettings):
            raise SettingsServiceUnavailable("settings store returned an invalid model")
        self._settings = loaded

    @property
    def current(self) -> HausmanHubSettings:
        if self._settings is None:
            raise SettingsServiceUnavailable("settings service is not loaded")
        return self._settings

    async def async_replace(self, settings: HausmanHubSettings) -> None:
        if not isinstance(settings, HausmanHubSettings):
            raise SettingsServiceUnavailable("validated settings are required")
        async with self._lock:
            await self._store.async_save(settings)
            self._settings = settings
