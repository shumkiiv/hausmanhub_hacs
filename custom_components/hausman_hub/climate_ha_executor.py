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
            try:
                await self._hass.services.async_call(
                    domain,
                    service,
                    data,
                    blocking=True,
                )
            except Exception as error:
                raise ClimateHaExecutionError(completed) from error
            completed += 1
        return completed
