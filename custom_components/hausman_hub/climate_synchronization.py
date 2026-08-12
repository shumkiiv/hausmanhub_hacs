"""Run explicit full climate synchronization twice per local day."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import TYPE_CHECKING

from homeassistant.helpers.event import async_track_time_change

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .application.climate_runtime import ClimateRuntime


_LOGGER = logging.getLogger(__name__)
SYNCHRONIZATION_HOURS = (10, 22)


async def async_start_climate_synchronization(
    hass: HomeAssistant,
    entry: ConfigEntry,
    runtime: ClimateRuntime,
) -> None:
    """Schedule two exact local-time synchronizations without startup catch-up."""

    async def async_synchronize(now: datetime) -> None:
        try:
            await runtime.async_run_scheduled_climate_synchronization(now)
        except Exception as error:
            _LOGGER.warning(
                "HausmanHub could not synchronize climate devices: %s",
                type(error).__name__,
            )

    cancel = async_track_time_change(
        hass,
        async_synchronize,
        hour=SYNCHRONIZATION_HOURS,
        minute=0,
        second=0,
    )
    entry.async_on_unload(cancel)
