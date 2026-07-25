from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING

from homeassistant.helpers.event import async_track_time_interval

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .application.ai_assistant import AiAssistantService


_LOGGER = logging.getLogger(__name__)
_REFRESH_INTERVAL = timedelta(hours=2)


async def async_start_ai_assistant_schedule(
    hass: HomeAssistant,
    entry: ConfigEntry,
    service: AiAssistantService,
) -> None:
    async def async_refresh(_: datetime) -> None:
        try:
            await service.async_refresh()
        except Exception as error:  # noqa: BROAD_EXCEPT_OK
            _LOGGER.warning(
                "HausmanHub AI advisory refresh failed: %s",
                type(error).__name__,
            )

    cancel = async_track_time_interval(hass, async_refresh, _REFRESH_INTERVAL)
    entry.async_on_unload(cancel)
