"""Home Assistant command executor for room lighting.

This is the only room lighting module that calls a Home Assistant service. The
runtime must never invoke it unless ``commands_enabled`` is explicitly true and
must not treat a 2xx call as success: every receipt carries the read-back
observation and a ``confirmed`` flag.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping

from ..domain.room_lighting import LightKind, LightTarget
from ..domain.room_lighting_engine import (
    COLOR_TEMPERATURE_TOLERANCE_KELVIN,
    LightAction,
    PlannedCommand,
)
from .power_evidence import trusted_power_state
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
DEFAULT_POWER_READBACK_WINDOW_SECONDS = 8.0
DEFAULT_POWER_READBACK_INTERVAL_SECONDS = 0.25


@dataclass(frozen=True, slots=True)
class RoomLightingReceipt:
    """Confirmed or unconfirmed result of one physical command."""

    target_id: str
    action: str
    confirmed: bool
    state_after: dict[str, object] | None
    service: str
    service_data: dict[str, object]
    sent: bool = True
    send_outcome: str = "sent"

    def to_payload(self) -> dict[str, object]:
        return {
            "targetId": self.target_id,
            "action": self.action,
            "sent": self.sent,
            "sendOutcome": self.send_outcome,
            "confirmed": self.confirmed,
            "stateAfter": self.state_after,
            "service": self.service,
            "serviceData": dict(self.service_data),
        }


class RoomLightingHaExecutor:
    """Dispatch a planned command and observe the physical result."""

    def __init__(
        self,
        targets: Mapping[str, LightTarget] | None = None,
        *,
        power_readback_window_seconds: float = DEFAULT_POWER_READBACK_WINDOW_SECONDS,
        power_readback_interval_seconds: float = DEFAULT_POWER_READBACK_INTERVAL_SECONDS,
        manual_light_authority: object | None = None,
    ) -> None:
        self._targets: dict[str, LightTarget] = dict(targets or {})
        self._manual_light_authority = manual_light_authority
        self._active_context_ids: set[str] = set()
        self._recent_context_ids: dict[str, tuple[float, str, str]] = {}
        # Bounded diagnostic trace used by narrow runtime tests and local
        # troubleshooting. It does not participate in command decisions.
        self.calls: list[PlannedCommand] = []
        self._power_readback_window = max(
            0.0, float(power_readback_window_seconds)
        )
        self._power_readback_interval = max(
            0.01, float(power_readback_interval_seconds)
        )

    def owns_service_context(
        self, context: object, entity_id: str, action: str
    ) -> bool:
        """Trust a context lineage only for its exact physical light action."""
        now = time.monotonic()
        self._recent_context_ids = {
            key: evidence for key, evidence in self._recent_context_ids.items()
            if evidence[0] > now
        }
        context_id = getattr(context, "id", None)
        for candidate_id in (context_id, getattr(context, "parent_id", None)):
            if not isinstance(candidate_id, str):
                continue
            evidence = self._recent_context_ids.get(candidate_id)
            if evidence is not None and evidence[1:] == (entity_id, action):
                return True
        return False

    async def _call(
        self, hass, domain: str, service: str, data: dict,
        *, protected_entities: tuple[str, ...] = (),
    ) -> None:
        authority = self._manual_light_authority
        if authority is not None and any(
            authority.blocks(entity_id) for entity_id in protected_entities
        ):
            raise ManualLightAuthorityDenied("manual_light_priority")
        context = _new_service_context()
        self._active_context_ids.add(context.id)
        entity_id = data.get("entity_id")
        action = {"turn_on": "on", "turn_off": "off"}.get(service)
        if isinstance(entity_id, str) and action is not None:
            self._recent_context_ids[context.id] = (
                time.monotonic() + 30.0, entity_id, action
            )
        try:
            await hass.services.async_call(
                domain, service, data, blocking=True, context=context
            )
        finally:
            self._active_context_ids.discard(context.id)

    def update_targets(self, targets: Mapping[str, LightTarget]) -> None:
        self._targets = dict(targets)

    @property
    def targets(self) -> dict[str, LightTarget]:
        return dict(self._targets)

    async def execute(
        self, hass: HomeAssistant, command: PlannedCommand,
        *, automatic: bool = True,
    ) -> RoomLightingReceipt:
        self.calls.append(command)
        del self.calls[:-256]
        target = self._targets.get(command.target_id)
        if target is None or target.entity_id is None:
            return RoomLightingReceipt(
                target_id=command.target_id,
                action=command.action.value,
                confirmed=False,
                state_after=None,
                service="unknown",
                service_data={},
                sent=False,
                send_outcome="not_sent",
            )
        if automatic and isinstance(target, LightTarget) and not target.auto_control:
            return RoomLightingReceipt(
                target_id=command.target_id, action=command.action.value,
                confirmed=False, state_after=None, service="manual_only",
                service_data={}, sent=False, send_outcome="not_sent",
            )
        min_kelvin, max_kelvin = _device_kelvin_bounds(hass, target)
        domain, service, data = _service_call(
            command, target, min_kelvin=min_kelvin, max_kelvin=max_kelvin
        )
        protected = (
            (target.entity_id,)
            if automatic and isinstance(target, LightTarget)
            and target.entity_id.startswith(("light.", "switch."))
            else ()
        )
        try:
            await self._call(hass, domain, service, data,
                             protected_entities=protected)
        except ManualLightAuthorityDenied:
            return RoomLightingReceipt(
                target_id=command.target_id, action=command.action.value,
                confirmed=False, state_after=None,
                service=f"{domain}.{service}", service_data=data,
                sent=False, send_outcome="not_sent",
            )
        state_after = _read_back(hass, target)
        return RoomLightingReceipt(
            target_id=command.target_id,
            action=command.action.value,
            confirmed=_confirmed(command, state_after),
            state_after=state_after,
            service=f"{domain}.{service}",
            service_data=data,
        )

    async def execute_manual(
        self, hass: HomeAssistant, command: PlannedCommand
    ) -> RoomLightingReceipt:
        """Physical binding presses remain directly executable by the user."""
        return await self.execute(hass, command, automatic=False)

    async def async_power_on(
        self, hass: HomeAssistant, entity_id: str,
        *, protected_entities: tuple[str, ...] = (),
    ) -> dict[str, object]:
        """Turn on a power source and confirm it through fresh read-back.

        A single immediate read is not proof: a relay can report a few seconds
        after the call. The method therefore polls for a fresh, non-restored
        ``on`` report and returns an explicit receipt. An already provable
        ``on`` source is not commanded again.
        """

        receipt: dict[str, object] = {
            "entityId": entity_id,
            "sent": False,
            "sendOutcome": "not_sent",
            "confirmed": False,
            "stateBefore": None,
            "stateAfter": None,
            "waitedSeconds": 0.0,
        }
        if not isinstance(entity_id, str) or not entity_id:
            receipt["reason"] = "power_source_invalid"
            return receipt
        before = hass.states.get(entity_id)
        receipt["stateBefore"] = (
            None if before is None else str(getattr(before, "state", "unknown"))
        )
        if trusted_power_state(before, same_state_confirmation=True) == "on":
            receipt.update(
                confirmed=True,
                stateAfter="on",
                reason="power_source_on",
            )
            return receipt
        started = asyncio.get_running_loop().time()
        domain = entity_id.split(".", 1)[0]
        if domain not in (_LIGHT_DOMAIN, _SWITCH_DOMAIN):
            receipt["reason"] = "power_source_invalid"
            return receipt
        try:
            await self._call(
                hass,
                domain,
                "turn_on",
                {"entity_id": entity_id},
                protected_entities=protected_entities,
            )
        except ManualLightAuthorityDenied:
            receipt["reason"] = "manual_light_priority"
            return receipt
        except Exception:  # noqa: BLE001 - a failed power call must not crash
            # The exception may have happened before, during or after the
            # Home Assistant boundary: the command outcome is unknown, never a
            # confirmed send.
            receipt.update(
                sendOutcome="uncertain",
                reason="command_uncertain",
            )
            return receipt
        receipt["sent"] = True
        receipt["sendOutcome"] = "sent"
        deadline = started + self._power_readback_window
        while True:
            state = hass.states.get(entity_id)
            if trusted_power_state(state, same_state_confirmation=True) == "on":
                receipt.update(
                    confirmed=True,
                    stateAfter="on",
                    waitedSeconds=round(
                        asyncio.get_running_loop().time() - started, 3
                    ),
                    reason="power_source_on",
                )
                return receipt
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(self._power_readback_interval, remaining))
        state = hass.states.get(entity_id)
        receipt.update(
            stateAfter=None if state is None else str(getattr(state, "state", "unknown")),
            waitedSeconds=round(asyncio.get_running_loop().time() - started, 3),
            reason="power_source_unconfirmed",
        )
        return receipt


class ManualLightAuthorityDenied(RuntimeError):
    """Physical call was refused before reaching Home Assistant."""


def _new_service_context():
    from homeassistant.core import Context

    return Context()


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
        # ``transition`` belongs to the light service schema.  Passing it to
        # switch.turn_off makes Home Assistant reject the whole command, so a
        # non-dimmable target is always switched immediately.
        if domain == _LIGHT_DOMAIN and command.fade_seconds:
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
