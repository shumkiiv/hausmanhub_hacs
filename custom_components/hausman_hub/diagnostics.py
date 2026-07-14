"""Home Assistant adapter for a redacted, allow-list diagnostics snapshot."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .application.diagnostics import diagnostics_snapshot
from .home_observation import collect_home_summary

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, object]:
    """Return safety facts and count-only local inventory information."""

    return diagnostics_snapshot(
        entry.data,
        entry.options,
        collect_home_summary(hass),
    )
