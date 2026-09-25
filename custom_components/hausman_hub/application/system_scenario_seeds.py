"""Privacy-safe built-in system scenario bootstrap.

The public package intentionally ships no private home topology. Existing
installations keep their persisted scenario registry in Home Assistant storage;
a fresh installation starts without system scenario seeds until an admin imports
or provisions per-install scenarios.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .scenario_catalog import _stable_target_id_from_entity
from .scenarios import ScenarioCatalog

if TYPE_CHECKING:  # pragma: no cover
    from .scenario_service import ScenarioService

_LOGGER = logging.getLogger(__name__)


# --- Payload builders (wire format домена scenarios) ---


def _target(entity_id: str) -> str:
    """Стабильный targetId каталога для entity id (см. scenario_catalog)."""

    return _stable_target_id_from_entity(entity_id)


def _device_trigger(
    rule_id: str,
    entity_id: str,
    comparison: str,
    value: object | None = None,
    *,
    for_seconds: int = 0,
) -> dict[str, object]:
    trigger: dict[str, object] = {
        "id": rule_id,
        "type": "device_state",
        "targetId": _target(entity_id),
        "property": "state",
        "comparison": comparison,
    }
    if value is not None:
        trigger["value"] = value
    if for_seconds:
        trigger["forSeconds"] = for_seconds
    return trigger


def _device_condition(
    rule_id: str, entity_id: str, comparison: str, value: object
) -> dict[str, object]:
    return {
        "id": rule_id,
        "type": "device_state",
        "targetId": _target(entity_id),
        "property": "state",
        "comparison": comparison,
        "value": value,
    }


def _device_action(
    rule_id: str, entity_id: str, action_id: str, value: object | None = None
) -> dict[str, object]:
    action: dict[str, object] = {
        "id": rule_id,
        "type": "device_action",
        "targetId": _target(entity_id),
        "actionId": action_id,
    }
    if value is not None:
        action["value"] = value
    return action


def _delay(rule_id: str, seconds: int) -> dict[str, object]:
    return {"id": rule_id, "type": "delay", "delaySeconds": seconds}


def _notify(rule_id: str, message: str) -> dict[str, object]:
    return {"id": rule_id, "type": "notification", "message": message}


def _with_target_names(
    items: tuple[Mapping[str, object], ...] | list[Mapping[str, object]],
    catalog: ScenarioCatalog,
) -> list[Mapping[str, object]]:
    """Подставить targetName из живого каталога в шаги с targetId."""

    enriched: list[Mapping[str, object]] = []
    for item in items:
        target_id = item.get("targetId")
        if isinstance(target_id, str) and "targetName" not in item:
            device = catalog.device(target_id)
            if device is not None and device.name:
                item = {**item, "targetName": device.name}
        enriched.append(item)
    return enriched


AWAY_OFF_ENTITIES: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class SystemScenarioSeed:
    """Одно определение системного сценария."""

    scenario_id: str
    title: str
    description: str
    icon: str
    triggers: tuple[Mapping[str, object], ...]
    conditions: tuple[Mapping[str, object], ...]
    actions: tuple[Mapping[str, object], ...]
    execution_mode: str = "single"
    command_mode: str = "live"
    required_entities: tuple[str, ...] = ()
    optional_actions: tuple[tuple[str, str], ...] = ()

    def build_payload(self, catalog: ScenarioCatalog) -> dict[str, Any] | None:
        """Собрать payload для async_update_scenario или None, если рано."""

        for entity_id in self.required_entities:
            device = catalog.device(_target(entity_id))
            if device is None or device.entity_id != entity_id:
                _LOGGER.debug(
                    "system scenario %s skipped: required entity is not in catalog",
                    self.scenario_id,
                )
                return None
        actions: list[Mapping[str, object]] = []
        for index, (entity_id, action_id) in enumerate(self.optional_actions):
            device = catalog.device(_target(entity_id))
            if device is None or device.entity_id != entity_id:
                continue
            actions.append(_device_action(f"ax{index}", entity_id, action_id))
        actions.extend(self.actions)
        if self.optional_actions and not any(
            item.get("type") == "device_action" for item in actions
        ):
            return None
        return {
            "id": self.scenario_id,
            "title": self.title,
            "group": "system",
            "description": self.description,
            "icon": self.icon,
            "enabled": True,
            "triggerDescription": "",
            "conditionDescription": "",
            "actionDescription": "",
            "definition": {
                "version": 1,
                "executionMode": self.execution_mode,
                **(
                    {"commandMode": self.command_mode}
                    if self.command_mode != "live"
                    else {}
                ),
                "triggers": _with_target_names(self.triggers, catalog),
                "conditions": _with_target_names(self.conditions, catalog),
                "actions": _with_target_names(actions, catalog),
            },
        }


SYSTEM_SCENARIO_SEEDS: tuple[SystemScenarioSeed, ...] = ()


async def async_seed_system_scenarios(service: ScenarioService) -> tuple[str, ...]:
    """Создать отсутствующие системные сценарии. Идемпотентно."""

    catalog = service.current_catalog()
    existing = {scenario.id for scenario in await service.async_list_scenarios()}
    created: list[str] = []
    for seed in SYSTEM_SCENARIO_SEEDS:
        if seed.scenario_id in existing:
            continue
        payload = seed.build_payload(catalog)
        if payload is None:
            continue
        try:
            await service.async_update_scenario(payload)
        except Exception:
            _LOGGER.warning(
                "system scenario %s failed validation; will retry on next start",
                seed.scenario_id,
                exc_info=True,
            )
            continue
        created.append(seed.scenario_id)
    return tuple(created)
