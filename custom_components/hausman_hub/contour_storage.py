"""Versioned Home Assistant storage adapter for HASC contour definitions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.storage import Store

from .application.contours import (
    ContourRegistryViolation,
    contour_registry_from_payload,
    contour_registry_to_payload,
)
from .domain.contours import CONTOUR_REGISTRY_VERSION, ContourRegistry

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class ContourStorageError(RuntimeError):
    """Persisted contour data is damaged or unavailable."""


class HomeAssistantContourStore:
    """Persist one complete contour registry per HASC config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, object]] = Store(
            hass,
            CONTOUR_REGISTRY_VERSION,
            f"hausman_hub.contours.{entry_id}",
        )

    async def async_load(self) -> ContourRegistry:
        """Return an empty registry only before the first contour is saved."""

        payload = await self._store.async_load()
        if payload is None:
            return ContourRegistry()
        try:
            return contour_registry_from_payload(payload)
        except ContourRegistryViolation as error:
            raise ContourStorageError("stored contour registry is invalid") from error

    async def async_save(self, registry: ContourRegistry) -> None:
        """Save only the exact validated contour payload."""

        await self._store.async_save(contour_registry_to_payload(registry))
