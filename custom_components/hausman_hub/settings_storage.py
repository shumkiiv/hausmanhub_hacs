"""Versioned Home Assistant storage for native HausmanHub user settings."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .domain.hub_settings import (
    HUB_SETTINGS_VERSION,
    HausmanHubSettings,
    HausmanHubSettingsViolation,
    hub_settings_from_payload,
    hub_settings_to_payload,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class SettingsStorageError(RuntimeError):
    """Persisted HausmanHub settings are damaged or unavailable."""


class HomeAssistantSettingsStore:
    """Persist one complete user-settings document per config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        from homeassistant.helpers.storage import Store

        class _MigratingSettingsStore(Store[dict[str, object]]):  # type: ignore[type-arg]
            async def _async_migrate_func(
                self,
                old_major_version: int,
                old_minor_version: int,
                old_data: object,
            ) -> dict[str, object]:
                del old_major_version
                del old_minor_version
                del old_data
                return hub_settings_to_payload(HausmanHubSettings())

        self._store: Store[dict[str, object]] = _MigratingSettingsStore(
            hass,
            HUB_SETTINGS_VERSION,
            f"hausman_hub.settings.{entry_id}",
            max_readable_version=HUB_SETTINGS_VERSION,
        )

    async def async_load(self) -> HausmanHubSettings:
        """Return defaults only when no settings document exists yet."""

        payload = await self._store.async_load()
        if payload is None:
            return HausmanHubSettings()
        try:
            return hub_settings_from_payload(payload)
        except HausmanHubSettingsViolation as error:
            raise SettingsStorageError("stored HausmanHub settings are invalid") from error

    async def async_save(self, settings: HausmanHubSettings) -> None:
        """Persist only the exact validated document."""

        await self._store.async_save(hub_settings_to_payload(settings))
