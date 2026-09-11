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
from ..domain.room_lighting_engine import LightAction, PlannedCommand
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
        domain, service, data = _service_call(command, target)
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


def _service_call(
    command: PlannedCommand, target: LightTarget
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
            data["kelvin"] = command.color_temperature
        if command.fade_seconds:
            data["transition"] = command.fade_seconds
    return domain, "turn_on", data


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
    return raw == "on"


__all__ = ["RoomLightingHaExecutor", "RoomLightingReceipt"]
