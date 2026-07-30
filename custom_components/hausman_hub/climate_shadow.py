"""Home Assistant clock adapter for command-free climate shadow collection."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING

from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .application.climate_shadow_window import ClimateShadowWindowService
from .climate_shadow_storage import (
    ClimateShadowStorageError,
    HomeAssistantClimateShadowStore,
)
from .domain.climate_shadow_window import CLIMATE_SHADOW_SAMPLE_INTERVAL_SECONDS

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .application.climate_runtime import ClimateRuntime


_LOGGER = logging.getLogger(__name__)
_CHECK_INTERVAL = timedelta(seconds=CLIMATE_SHADOW_SAMPLE_INTERVAL_SECONDS)


async def async_start_climate_shadow(
    hass: HomeAssistant,
    entry: ConfigEntry,
    runtime: ClimateRuntime,
) -> ClimateShadowWindowService:
    """Restore evidence, collect once now, then sample every five minutes."""

    service = ClimateShadowWindowService(
        HomeAssistantClimateShadowStore(hass, entry.entry_id)
    )
    try:
        await service.async_load()
    except ClimateShadowStorageError as error:
        _LOGGER.warning(
            "HausmanHub ignored invalid climate shadow evidence: %s",
            type(error).__name__,
        )
        await service.async_initialize_empty()

    async def async_collect(now: datetime) -> None:
        try:
            comparison = await runtime.async_native_climate_comparison()
            if comparison is not None:
                await service.async_record(
                    comparison,
                    collected_at=int(now.timestamp() * 1000),
                )
        except Exception as error:
            _LOGGER.warning(
                "HausmanHub could not collect climate shadow evidence: %s",
                type(error).__name__,
            )

    await async_collect(dt_util.now())
    cancel = async_track_time_interval(hass, async_collect, _CHECK_INTERVAL)
    entry.async_on_unload(cancel)
    return service
