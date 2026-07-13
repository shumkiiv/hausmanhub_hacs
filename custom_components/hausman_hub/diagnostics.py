"""Home Assistant adapter for a redacted, allow-list diagnostics snapshot."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .application.diagnostics import diagnostics_snapshot

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


async def async_get_config_entry_diagnostics(
    _hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, object]:
    """Return safety facts only; no entry value is copied to the export."""

    return diagnostics_snapshot(entry.data, entry.options)
