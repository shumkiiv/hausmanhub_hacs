"""Atomic storage service for the configurable away-mode settings."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from typing import Callable

from ..domain.away_settings import (
    AwaySettings,
    AwaySettingsViolation,
    away_settings_to_payload,
    validate_away_settings,
)


AWAY_SETTINGS_CONTRACT = "hausman-hub-away-settings"


class AwaySettingsServiceViolation(ValueError):
    """An away-settings update is malformed, stale or references missing entities."""

    def __init__(self, message: str, *, stale: bool = False) -> None:
        super().__init__(message)
        self.stale = stale


class AwaySettingsService:
    """Load and atomically replace one away-mode document per config entry."""

    def __init__(
        self,
        store: object,
        *,
        entity_id_validator: Callable[[str], bool] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._entity_id_validator = entity_id_validator
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._revision = 0
        self._updated_at = ""
        self._settings = AwaySettings()
        self._loaded = False
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        loaded = await self._store.async_load()
        if loaded is None:
            self._revision = 0
            self._updated_at = self._timestamp()
            self._settings = AwaySettings()
            self._loaded = True
            return
        if not isinstance(loaded, dict) or set(loaded) != {
            "revision",
            "updatedAt",
            "settings",
        }:
            raise AwaySettingsServiceViolation(
                "stored away settings are invalid"
            )
        revision = loaded.get("revision")
        updated_at = loaded.get("updatedAt")
        if type(revision) is not int or revision < 0 or not isinstance(updated_at, str):
            raise AwaySettingsServiceViolation(
                "stored away settings metadata is invalid"
            )
        try:
            settings = validate_away_settings(loaded["settings"])
        except AwaySettingsViolation as error:
            raise AwaySettingsServiceViolation(str(error)) from error
        self._revision = revision
        self._updated_at = updated_at
        self._settings = settings
        self._loaded = True

    @property
    def settings(self) -> AwaySettings:
        self._require_loaded()
        return self._settings

    @property
    def document(self) -> dict[str, object]:
        self._require_loaded()
        return {
            "contract": {
                "name": AWAY_SETTINGS_CONTRACT,
                "version": 1,
            },
            "revision": self._revision,
            "updatedAt": self._updated_at,
            "settings": away_settings_to_payload(self._settings),
        }

    async def async_replace(
        self, expected_revision: object, settings: object
    ) -> dict[str, object]:
        self._require_loaded()
        if type(expected_revision) is not int or expected_revision < 0:
            raise AwaySettingsServiceViolation(
                "expected away settings revision is invalid"
            )
        try:
            validated = validate_away_settings(settings)
        except AwaySettingsViolation as error:
            raise AwaySettingsServiceViolation(str(error)) from error
        if self._entity_id_validator is not None and any(
            not self._entity_id_validator(item.entity_id)
            for item in validated.triggers
        ):
            raise AwaySettingsServiceViolation(
                "away trigger references an unavailable entity"
            )
        async with self._lock:
            if expected_revision != self._revision:
                raise AwaySettingsServiceViolation(
                    "away settings revision is stale", stale=True
                )
            next_revision = self._revision + 1
            updated_at = self._timestamp()
            stored = {
                "revision": next_revision,
                "updatedAt": updated_at,
                "settings": away_settings_to_payload(validated),
            }
            await self._store.async_save(stored)
            self._revision = next_revision
            self._updated_at = updated_at
            self._settings = validated
        return self.document

    async def async_reset(self) -> dict[str, object]:
        """Disarm the configurable engine using the same atomic write."""

        return await self.async_replace(self._revision, _EMPTY_SETTINGS)

    def _timestamp(self) -> str:
        value = self._now()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def _require_loaded(self) -> None:
        if not self._loaded:
            raise AwaySettingsServiceViolation("away settings service is not loaded")


_EMPTY_SETTINGS = deepcopy(
    {"triggers": [], "awayActions": [], "returnActions": []}
)
