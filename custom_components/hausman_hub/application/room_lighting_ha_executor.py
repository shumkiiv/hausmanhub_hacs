"""Home Assistant command executor for room lighting.

This is the only room lighting module that calls a Home Assistant service. The
runtime must never invoke it unless ``commands_enabled`` is explicitly true and
must not treat a 2xx call as success: every receipt carries the read-back
observation and a ``confirmed`` flag.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping

from ..domain.room_lighting import LightKind, LightTarget
from ..domain.room_lighting_engine import (
    COLOR_TEMPERATURE_TOLERANCE_KELVIN,
    LightAction,
    PlannedCommand,
)
from .room_lighting_color import (
    FALLBACK_MAX_KELVIN,
    FALLBACK_MIN_KELVIN,
    device_kelvin_bounds,
    reflect_inverted_kelvin,
)
from .room_lighting_ha_state import (
    _brightness_percent,
    _color_temperature_kelvin,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LIGHT_DOMAIN = "light"
_SWITCH_DOMAIN = "switch"


@dataclass(frozen=True, slots=True)
class RoomLightingReceipt:
    """Confirmed or unconfirmed result of one physical command."""

    target_id: str
    action: str
    confirmed: bool
    state_after: dict[str, object] | None
    service: str
    service_data: dict[str, object]

    def to_payload(self) -> dict[str, object]:
        return {
            "targetId": self.target_id,
            "action": self.action,
            "confirmed": self.confirmed,
            "stateAfter": self.state_after,
            "service": self.service,
            "serviceData": dict(self.service_data),
        }


class RoomLightingHaExecutor:
    """Dispatch a planned command and observe the physical result."""

    def __init__(self, targets: Mapping[str, LightTarget] | None = None) -> None:
        self._targets: dict[str, LightTarget] = dict(targets or {})

    def update_targets(self, targets: Mapping[str, LightTarget]) -> None:
        self._targets = dict(targets)

    @property
    def targets(self) -> dict[str, LightTarget]:
        return dict(self._targets)

    async def execute(
        self, hass: HomeAssistant, command: PlannedCommand
    ) -> RoomLightingReceipt:
        target = self._targets.get(command.target_id)
        if target is None or target.entity_id is None:
            return RoomLightingReceipt(
                target_id=command.target_id,
                action=command.action.value,
                confirmed=False,
                state_after=None,
                service="unknown",
                service_data={},
            )
        min_kelvin, max_kelvin = _device_kelvin_bounds(hass, target)
        domain, service, data = _service_call(
            command, target, min_kelvin=min_kelvin, max_kelvin=max_kelvin
        )
        await hass.services.async_call(domain, service, data, blocking=True)
        state_after = _read_back(hass, target)
        return RoomLightingReceipt(
            target_id=command.target_id,
            action=command.action.value,
            confirmed=_confirmed(command, state_after),
            state_after=state_after,
            service=f"{domain}.{service}",
            service_data=data,
        )

    async def async_power_on(self, hass: HomeAssistant, entity_id: str) -> bool:
        """Turn on a room power switch and confirm it through the read-back."""

        if not isinstance(entity_id, str) or not entity_id:
            return False
        try:
            await hass.services.async_call(
                _SWITCH_DOMAIN,
                "turn_on",
                {"entity_id": entity_id},
                blocking=True,
            )
        except Exception:  # noqa: BLE001 - a failed power call must not crash
            return False
        state = hass.states.get(entity_id)
        return state is not None and str(getattr(state, "state", "")).lower() == "on"


def _service_call(
    command: PlannedCommand,
    target: LightTarget,
    *,
    min_kelvin: int = FALLBACK_MIN_KELVIN,
    max_kelvin: int = FALLBACK_MAX_KELVIN,
) -> tuple[str, str, dict[str, object]]:
    domain = _LIGHT_DOMAIN if target.kind is LightKind.LIGHT else _SWITCH_DOMAIN
    entity_id = target.entity_id
    assert entity_id is not None  # narrowed by the caller
    data: dict[str, object] = {"entity_id": entity_id}
    if command.action is LightAction.TURN_OFF:
        if command.fade_seconds:
            data["transition"] = command.fade_seconds
        return domain, "turn_off", data
    if domain == _LIGHT_DOMAIN:
        if command.brightness is not None:
            data["brightness_pct"] = command.brightness
        if command.color_temperature is not None:
            kelvin = int(command.color_temperature)
            if target.color_temp_inverted:
                kelvin = reflect_inverted_kelvin(kelvin, min_kelvin, max_kelvin)
            # Home Assistant's light service accepts ``color_temp_kelvin``;
            # the legacy ``kelvin`` key is ignored by the entity.
            data["color_temp_kelvin"] = kelvin
        if command.fade_seconds:
            data["transition"] = command.fade_seconds
    return domain, "turn_on", data


def _device_kelvin_bounds(hass: HomeAssistant, target: LightTarget) -> tuple[int, int]:
    """Return the device kelvin bounds or the conservative fallback."""

    if target.entity_id is None:
        return FALLBACK_MIN_KELVIN, FALLBACK_MAX_KELVIN
    state = hass.states.get(target.entity_id)
    attributes = getattr(state, "attributes", None)
    return device_kelvin_bounds(attributes)


def _read_back(
    hass: HomeAssistant, target: LightTarget
) -> dict[str, object] | None:
    if target.entity_id is None:
        return None
    state = hass.states.get(target.entity_id)
    if state is None:
        return None
    observed: dict[str, object] = {"state": str(getattr(state, "state", "unknown"))}
    if target.kind is LightKind.LIGHT:
        attributes = getattr(state, "attributes", None)
        if isinstance(attributes, dict):
            brightness = _brightness_percent(attributes.get("brightness"))
            kelvin = _color_temperature_kelvin(attributes)
            if brightness is not None:
                observed["brightness"] = brightness
            if kelvin is not None:
                if target.color_temp_inverted:
                    # Report the logical colour so the read-back is comparable
                    # with the logical command the engine planned.
                    kelvin = reflect_inverted_kelvin(
                        kelvin, *_device_kelvin_bounds(hass, target)
                    )
                observed["color_temperature"] = kelvin
    return observed


def _confirmed(
    command: PlannedCommand, state_after: dict[str, object] | None
) -> bool:
    if state_after is None:
        return False
    raw = str(state_after.get("state", "unknown")).lower()
    if command.action is LightAction.TURN_OFF:
        return raw != "on"
    if raw != "on":
        return False
    # A colour-temperature command is only confirmed when the read-back
    # matches the requested logical colour; a 2xx call is not enough.
    if (
        command.action is LightAction.SET_COLOR_TEMPERATURE
        and command.color_temperature is not None
    ):
        observed = state_after.get("color_temperature")
        if not isinstance(observed, (int, float)) or isinstance(observed, bool):
            return False
        if (
            abs(int(observed) - int(command.color_temperature))
            > COLOR_TEMPERATURE_TOLERANCE_KELVIN
        ):
            return False
    return True


__all__ = ["RoomLightingHaExecutor", "RoomLightingReceipt"]
