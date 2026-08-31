"""Single strict climate-call executor for trial, managed ticks, and settings application."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .domain.climate_ha_calls import ClimateHaService, ClimateHaServiceCall

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class ClimateHaExecutionError(RuntimeError):
    """One strict call failed; completed count is preserved for the receipt."""

    def __init__(self, completed: int) -> None:
        super().__init__("strict climate call failed")
        self.completed = completed


class HomeAssistantClimateCallExecutor:
    """Execute the single strict climate call boundary through Home Assistant services only."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._recent_context_ids: list[str] = []

    def recent_context_ids(self) -> tuple[str, ...]:
        """Return bounded contexts created by Hausman climate commands."""
        return tuple(self._recent_context_ids)

    async def async_execute(self, calls: tuple[ClimateHaServiceCall, ...]) -> int:
        """Run the strict calls in order and stop at the first failure."""

        completed = 0
        for call in calls:
            domain, service = call.service.value.split(".", 1)
            data: dict[str, object] = {"entity_id": call.entity_id}
            if call.service is ClimateHaService.CLIMATE_SET_HVAC_MODE:
                data["hvac_mode"] = call.hvac_mode.value  # type: ignore[union-attr]
            elif call.service is ClimateHaService.CLIMATE_SET_TEMPERATURE:
                data["temperature"] = call.temperature
            elif call.service is ClimateHaService.CLIMATE_SET_FAN_MODE:
                data["fan_mode"] = call.fan_mode.value  # type: ignore[union-attr]
            elif call.service is ClimateHaService.HUMIDIFIER_SET_HUMIDITY:
                data["humidity"] = call.humidity
            elif call.service is ClimateHaService.REMOTE_SEND_COMMAND:
                data["device"] = call.device
                data["command"] = call.command
            try:
                try:
                    from homeassistant.core import Context  # noqa: PLC0415
                    context = Context()
                except ModuleNotFoundError:
                    # Pure application tests intentionally run without HA.
                    # Production always provides Context and its attribution.
                    context = None
                context_id = getattr(context, "id", None)
                if isinstance(context_id, str) and context_id:
                    self._recent_context_ids.append(context_id)
                    del self._recent_context_ids[:-128]
                await self._hass.services.async_call(
                    domain,
                    service,
                    data,
                    blocking=True,
                    **({"context": context} if context is not None else {}),
                )
            except Exception as error:
                raise ClimateHaExecutionError(completed) from error
            completed += 1
        return completed

    async def async_execute_isolated(
        self, calls: tuple[ClimateHaServiceCall, ...]
    ) -> tuple[bool, ...]:
        """Execute independent device leaves without hiding a later result.

        The historic strict batch method deliberately stops on the first
        exception.  Receipt-producing callers use this sibling method so one
        unavailable device cannot turn unattempted neighbours into a claimed
        failure or trigger their redispatch on replay.
        """

        results: list[bool] = []
        for call in calls:
            try:
                await self.async_execute((call,))
            except ClimateHaExecutionError:
                results.append(False)
            else:
                results.append(True)
        return tuple(results)
