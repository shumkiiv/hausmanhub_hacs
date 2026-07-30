"""Versioned Home Assistant storage for bounded climate shadow evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.storage import Store

from .application.climate_shadow_window import (
    climate_shadow_state_from_payload,
    climate_shadow_state_to_payload,
)
from .domain.climate_shadow_window import (
    CLIMATE_SHADOW_WINDOW_VERSION,
    ClimateShadowWindowState,
    ClimateShadowWindowViolation,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class ClimateShadowStorageError(RuntimeError):
    """Stored shadow evidence is damaged or unavailable."""


class HomeAssistantClimateShadowStore:
    """Persist only redacted room-level comparison samples."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, object]] = Store(
            hass,
            CLIMATE_SHADOW_WINDOW_VERSION,
            f"hausman_hub.climate_shadow_window.{entry_id}",
        )

    async def async_load(self) -> ClimateShadowWindowState | None:
        payload = await self._store.async_load()
        if payload is None:
            return None
        try:
            return climate_shadow_state_from_payload(payload)
        except ClimateShadowWindowViolation as error:
            raise ClimateShadowStorageError(
                "stored climate shadow evidence is invalid"
            ) from error

    async def async_save(self, state: ClimateShadowWindowState) -> None:
        await self._store.async_save(climate_shadow_state_to_payload(state))
