"""Preserve a user's pre-existing light choice during sensor automation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from ..domain.device_power_dependencies import (
    DevicePowerDependency,
    effective_device_state,
)
from ..domain.scenarios import ScenarioAction, ScenarioActionType
from .scenarios import ScenarioCatalog, ScenarioDeviceEntry

MANUAL_LIGHT_PRIORITY_REASON = "manual_light_already_on"
_SENSOR_DOMAINS = frozenset({"binary_sensor", "sensor"})

_LIGHT_WORDS = (
    "light",
    "lamp",
    "chandelier",
    "mirror",
    "свет",
    "ламп",
    "люстр",
    "подсвет",
    "зеркал",
    "ночник",
)
_NON_LIGHT_SWITCH_WORDS = (
    "fan",
    "vent",
    "pump",
    "valve",
    "outlet",
    "socket",
    "heater",
    "humidifier",
    "siren",
    "lock",
    "boiler",
    "alarm",
    "вытяж",
    "вентил",
    "насос",
    "клапан",
    "розет",
    "обогрев",
    "увлажн",
    "сирен",
    "замок",
    "бойлер",
    "тревог",
    "кондиционер",
)


def _text(*values: object) -> str:
    return " ".join(str(value).casefold() for value in values if value)


def _contains_any(value: str, words: tuple[str, ...]) -> bool:
    return any(word in value for word in words)


def _state_revision(state: object | None) -> str | None:
    if state is None:
        return None
    changed = getattr(state, "last_changed", None)
    if isinstance(changed, datetime):
        return changed.isoformat()
    return None


def _sensor_triggered(
    trigger_context: Mapping[str, object] | None,
    catalog: ScenarioCatalog,
) -> bool:
    if not isinstance(trigger_context, Mapping):
        return False
    source = trigger_context.get("source")
    if source not in {"device_state", "nested"}:
        return False
    target_id = trigger_context.get("target_id")
    device = catalog.device(target_id) if isinstance(target_id, str) else None
    if device is None:
        return False
    domain = device.entity_id.split(".", 1)[0]
    return domain in _SENSOR_DOMAINS


@dataclass(frozen=True, slots=True)
class LightPriorityPlan:
    """One run-local classification and pre-command state snapshot."""

    light_action_ids: frozenset[str]
    light_target_ids: frozenset[str]
    guarded_target_ids: frozenset[str]
    manual_target_ids: frozenset[str]
    pre_states: Mapping[str, str | None]
    only_lighting_effects: bool

    @property
    def applied(self) -> bool:
        return bool(self.guarded_target_ids)


class LightAutomationPriority:
    """Track confirmed automatic light ownership for the current HA runtime."""

    def __init__(self) -> None:
        self._owned_revisions: dict[str, str | None] = {}

    def plan(
        self,
        actions: Sequence[ScenarioAction],
        catalog: ScenarioCatalog,
        hass: object,
        *,
        scenario_text: str,
        trigger_context: Mapping[str, object] | None,
        power_dependencies: Mapping[str, DevicePowerDependency],
    ) -> LightPriorityPlan:
        light_actions: list[tuple[ScenarioAction, ScenarioDeviceEntry]] = []
        has_non_lighting_effect = False
        for action in actions:
            classified = self._lighting_device(action, catalog, scenario_text)
            if classified is not None:
                light_actions.append((action, classified))
            elif action.type is not ScenarioActionType.DELAY:
                has_non_lighting_effect = True

        light_target_ids = frozenset(
            action.target_id
            for action, _device in light_actions
            if action.target_id is not None
        )
        light_action_ids = frozenset(action.id for action, _device in light_actions)
        pre_states = {
            device.entity_id: self._effective_state(
                device.entity_id, hass, power_dependencies
            )
            for _action, device in light_actions
        }
        self._discard_observed_off(pre_states)

        guarded_target_ids: frozenset[str] = frozenset()
        manual_target_ids: frozenset[str] = frozenset()
        has_activating_action = any(
            (allowed := device.action(action.action_id or "")) is not None
            and (allowed.service == "turn_on" or action.action_id == "toggle")
            for action, device in light_actions
        )
        if (
            light_actions
            and has_activating_action
            and _sensor_triggered(trigger_context, catalog)
        ):
            upstream_sources = {
                dependency.power_source_entity_id
                for _action, device in light_actions
                if (dependency := power_dependencies.get(device.entity_id)) is not None
            }
            visible = [
                (action, device)
                for action, device in light_actions
                if device.entity_id not in upstream_sources
            ]
            manual_target_ids = frozenset(
                action.target_id
                for action, device in visible
                if action.target_id is not None
                and pre_states.get(device.entity_id) == "on"
                and not self._is_owned(device.entity_id, hass)
            )
            if manual_target_ids:
                guarded_target_ids = light_target_ids

        return LightPriorityPlan(
            light_action_ids=light_action_ids,
            light_target_ids=light_target_ids,
            guarded_target_ids=guarded_target_ids,
            manual_target_ids=manual_target_ids,
            pre_states=pre_states,
            only_lighting_effects=bool(light_actions) and not has_non_lighting_effect,
        )

    def note_results(
        self,
        actions: Sequence[ScenarioAction],
        receipts: Sequence[Mapping[str, object]],
        plan: LightPriorityPlan,
        catalog: ScenarioCatalog,
        hass: object,
        *,
        automatic: bool,
        dry_run: bool,
    ) -> None:
        if dry_run:
            return
        by_action_id = {
            str(receipt.get("action_id")): receipt for receipt in receipts
        }
        for action in actions:
            if action.id not in plan.light_action_ids or action.target_id is None:
                continue
            receipt = by_action_id.get(action.id)
            if receipt is None or receipt.get("status") != "completed":
                continue
            if receipt.get("skipped") is True:
                continue
            device = catalog.device(action.target_id)
            allowed = device.action(action.action_id or "") if device else None
            if device is None or allowed is None:
                continue
            if allowed.service == "turn_off":
                self._owned_revisions.pop(device.entity_id, None)
                continue
            if allowed.service != "turn_on" and action.action_id != "toggle":
                continue
            if receipt.get("confirmed") is not True:
                continue
            if not automatic:
                self._owned_revisions.pop(device.entity_id, None)
            elif plan.pre_states.get(device.entity_id) != "on":
                self._owned_revisions[device.entity_id] = _state_revision(
                    self._state(hass, device.entity_id)
                )

    def note_direct_action(
        self,
        target_id: str,
        action_id: str,
        receipt: Mapping[str, object],
        catalog: ScenarioCatalog,
        hass: object,
    ) -> None:
        device = catalog.device(target_id)
        allowed = device.action(action_id) if device else None
        if (
            device is None
            or allowed is None
            or allowed.domain not in {"light", "switch"}
            or receipt.get("status") != "completed"
        ):
            return
        if allowed.service in {"turn_on", "turn_off"} or action_id == "toggle":
            self._owned_revisions.pop(device.entity_id, None)

    def _lighting_device(
        self,
        action: ScenarioAction,
        catalog: ScenarioCatalog,
        scenario_text: str,
    ) -> ScenarioDeviceEntry | None:
        if (
            action.type is not ScenarioActionType.DEVICE_ACTION
            or action.target_id is None
            or action.action_id is None
        ):
            return None
        device = catalog.device(action.target_id)
        allowed = device.action(action.action_id) if device else None
        if device is None or allowed is None:
            return None
        if allowed.domain == "light":
            return device
        if allowed.domain != "switch":
            return None
        target_text = _text(
            device.name,
            device.physical_name,
            device.capability_name,
            device.device_type_name,
        )
        if _contains_any(target_text, _LIGHT_WORDS):
            return device
        if _contains_any(target_text, _NON_LIGHT_SWITCH_WORDS):
            return None
        return device if _contains_any(scenario_text, _LIGHT_WORDS) else None

    def _is_owned(self, entity_id: str, hass: object) -> bool:
        if entity_id not in self._owned_revisions:
            return False
        recorded = self._owned_revisions[entity_id]
        current = _state_revision(self._state(hass, entity_id))
        if recorded is not None and current is not None and recorded != current:
            self._owned_revisions.pop(entity_id, None)
            return False
        return True

    def _discard_observed_off(self, pre_states: Mapping[str, str | None]) -> None:
        for entity_id, state in pre_states.items():
            if state != "on":
                self._owned_revisions.pop(entity_id, None)

    @staticmethod
    def _state(hass: object, entity_id: str) -> object | None:
        states = getattr(hass, "states", None)
        return states.get(entity_id) if states is not None else None

    @classmethod
    def _effective_state(
        cls,
        entity_id: str,
        hass: object,
        power_dependencies: Mapping[str, DevicePowerDependency],
    ) -> str | None:
        state, _status = effective_device_state(
            entity_id,
            power_dependencies,
            lambda requested: (
                str(getattr(current, "state", "unknown"))
                if (current := cls._state(hass, requested)) is not None
                else None
            ),
        )
        return state


def skipped_light_receipt(
    action: ScenarioAction,
    catalog: ScenarioCatalog,
    *,
    correlation_id: str,
    dry_run: bool,
) -> dict[str, object]:
    """Return a stable trace receipt without sending a physical command."""

    device = catalog.device(action.target_id or "")
    allowed = device.action(action.action_id or "") if device else None
    receipt: dict[str, object] = {
        "action_id": action.id,
        "correlation_id": correlation_id,
        "type": action.type,
        "status": "completed",
        "target_id": action.target_id,
        "skipped": True,
        "reason": MANUAL_LIGHT_PRIORITY_REASON,
    }
    if device is not None:
        receipt["entity_id"] = device.entity_id
    if allowed is not None:
        receipt["domain"] = allowed.domain
        receipt["service"] = allowed.service
    if dry_run:
        receipt["planned"] = True
        receipt["confirmed"] = None
    else:
        receipt["confirmed"] = True
        receipt["read_back"] = {
            "attempted": False,
            "matched": True,
            "observedAt": None,
            "observedState": "on",
            "attempts": 0,
        }
    return receipt
