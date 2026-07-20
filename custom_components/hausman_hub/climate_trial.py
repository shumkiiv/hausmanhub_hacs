"""Home Assistant clock adapter for the one-room internal climate trial."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING

from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .application.climate_runtime import ClimateRuntime


_LOGGER = logging.getLogger(__name__)
_CHECK_INTERVAL = timedelta(minutes=1)


async def async_start_climate_trial(
    hass: HomeAssistant,
    entry: ConfigEntry,
    runtime: ClimateRuntime,
) -> None:
    """Run one gated trial check now and then once per minute."""

    async def async_check(now: datetime) -> None:
        try:
            await runtime.async_run_climate_trial()
            await runtime.async_run_climate_managed()
        except Exception as error:
            _LOGGER.warning(
                "HausmanHub could not run the internal climate trial: %s",
                type(error).__name__,
            )

    await async_check(dt_util.now())
    cancel = async_track_time_interval(hass, async_check, _CHECK_INTERVAL)
    entry.async_on_unload(cancel)
