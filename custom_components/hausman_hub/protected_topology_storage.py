"""Home Assistant storage adapter for per-install protected topology."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .application.protected_topology import PROTECTED_TOPOLOGY_VERSION

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class HomeAssistantProtectedTopologyStore:
    """Persist private role bindings outside the public HACS package."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        from homeassistant.helpers.storage import Store

        self._store: Store[dict[str, object]] = Store(
            hass,
            PROTECTED_TOPOLOGY_VERSION,
            f"hausman_hub.protected_topology.{entry_id}",
            atomic_writes=True,
        )

    async def async_load(self) -> object | None:
        return await self._store.async_load()

    async def async_save(self, payload: dict[str, object]) -> bool | None:
        return await self._store.async_save(payload)


__all__ = ["HomeAssistantProtectedTopologyStore"]
