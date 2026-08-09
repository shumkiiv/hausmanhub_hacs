"""Home Assistant clock adapter for scheduled scenario triggers.

Arms exact daily callbacks (clock time, sunrise, sunset with offset) for
every enabled scenario trigger and re-arms when the registry changes.
Skip-once marks live in the scenario service; a cancelled occurrence is
consumed here instead of running the scenario.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING

from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .domain.scenarios import _offset_minutes

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .application.scenario_service import ScenarioService

_LOGGER = logging.getLogger(__name__)
_RECHECK_INTERVAL = timedelta(minutes=1)


async def async_start_scenario_schedule(
    hass: HomeAssistant,
    entry: ConfigEntry,
    service: ScenarioService,
) -> None:
    """Arm every enabled time/sun trigger and keep the arming in sync."""

    unsubs: list = []
    armed_signature: tuple = ()

    def _signature() -> tuple:
        return tuple(sorted(service.scheduled_trigger_items()))

    async def _async_run_due(
        scenario_id: str, trigger_id: str, _now: datetime
    ) -> None:
        day = dt_util.now().date().isoformat()
        if await service.async_consume_skip(scenario_id, trigger_id, day):
            _LOGGER.info(
                "scheduled run of %s (%s) skipped by user", scenario_id, trigger_id
            )
            return
        try:
            await service.async_run_scenario(scenario_id)
        except Exception as error:  # noqa: BLE001
            _LOGGER.warning(
                "scheduled run of scenario %s failed: %s",
                scenario_id,
                type(error).__name__,
            )

    def _rearm() -> None:
        nonlocal armed_signature
        for unsub in unsubs:
            unsub()
        unsubs.clear()
        for scenario_id, trigger_id, trigger_type, value in service.scheduled_trigger_items():
            if trigger_type == "time":
                from homeassistant.helpers.event import (  # noqa: PLC0415
                    async_track_time_change,
                )

                if not isinstance(value, str) or len(value) != 5:
                    continue
                try:
                    hour = int(value[:2])
                    minute = int(value[3:])
                except ValueError:
                    continue
                unsubs.append(
                    async_track_time_change(
                        hass,
                        lambda now, s=scenario_id, t=trigger_id: _async_run_due(s, t, now),
                        hour=hour,
                        minute=minute,
                        second=0,
                    )
                )
            elif trigger_type in ("sunrise", "sunset"):
                from homeassistant.helpers.event import (  # noqa: PLC0415
                    async_track_sunrise,
                    async_track_sunset,
                )

                offset = timedelta(
                    minutes=_offset_minutes(value, f"{trigger_type} trigger offset")
                )
                track = (
                    async_track_sunrise if trigger_type == "sunrise" else async_track_sunset
                )
                unsubs.append(
                    track(
                        hass,
                        lambda now, s=scenario_id, t=trigger_id: _async_run_due(s, t, now),
                        offset=offset,
                    )
                )
        armed_signature = _signature()

    async def _async_recheck(_now: datetime) -> None:
        try:
            if _signature() != armed_signature:
                _rearm()
        except Exception as error:  # noqa: BLE001
            _LOGGER.warning(
                "scenario schedule re-arm failed: %s", type(error).__name__
            )

    _rearm()
    cancel = async_track_time_interval(hass, _async_recheck, _RECHECK_INTERVAL)
    entry.async_on_unload(cancel)

    def _cancel_all() -> None:
        for unsub in unsubs:
            unsub()
        unsubs.clear()

    entry.async_on_unload(_cancel_all)
