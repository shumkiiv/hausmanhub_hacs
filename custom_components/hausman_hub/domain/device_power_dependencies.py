"""Validated relationships between a device and its upstream power source."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable, Mapping


MAX_DEVICE_POWER_DEPENDENCIES = 128
POWER_DEPENDENCY_POLICY = "requires_on"
_ENTITY_ID = re.compile(r"^[a-z][a-z0-9_]*\.[a-z0-9_]+$")
_POWER_SOURCE_DOMAINS = frozenset({"light", "switch"})


class DevicePowerDependencyViolation(ValueError):
    """A power dependency document is malformed or unsafe."""


@dataclass(frozen=True, slots=True)
class DevicePowerDependency:
    """One device that can operate only while another entity is on."""

    dependent_entity_id: str
    power_source_entity_id: str
    policy: str = POWER_DEPENDENCY_POLICY

    def __post_init__(self) -> None:
        _entity_id(self.dependent_entity_id, "dependent entity")
        _entity_id(self.power_source_entity_id, "power source entity")
        if self.dependent_entity_id == self.power_source_entity_id:
            raise DevicePowerDependencyViolation(
                "a device cannot depend on its own power state"
            )
        if self.power_source_entity_id.split(".", 1)[0] not in _POWER_SOURCE_DOMAINS:
            raise DevicePowerDependencyViolation(
                "power source entity must be a switch or light"
            )
        if self.policy != POWER_DEPENDENCY_POLICY:
            raise DevicePowerDependencyViolation("power dependency policy is invalid")


@dataclass(frozen=True, slots=True)
class PowerDependencyStatus:
    """Effective state of one configured dependency."""

    source_entity_id: str
    state: str
    reason: str
    blocks_commands: bool


def validate_device_power_dependencies(
    value: object,
) -> tuple[DevicePowerDependency, ...]:
    """Validate a bounded, acyclic public dependency array."""

    if not isinstance(value, list) or len(value) > MAX_DEVICE_POWER_DEPENDENCIES:
        raise DevicePowerDependencyViolation("device power dependencies are invalid")
    dependencies: list[DevicePowerDependency] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {
            "dependentEntityId",
            "powerSourceEntityId",
            "policy",
        }:
            raise DevicePowerDependencyViolation("power dependency fields are invalid")
        dependencies.append(
            DevicePowerDependency(
                dependent_entity_id=item["dependentEntityId"],
                power_source_entity_id=item["powerSourceEntityId"],
                policy=item["policy"],
            )
        )
    dependent_ids = [item.dependent_entity_id for item in dependencies]
    if len(dependent_ids) != len(set(dependent_ids)):
        raise DevicePowerDependencyViolation(
            "each dependent entity must have one power source"
        )
    mapping = {
        item.dependent_entity_id: item.power_source_entity_id for item in dependencies
    }
    _reject_cycles(mapping)
    return tuple(dependencies)


def device_power_dependencies_to_payload(
    dependencies: tuple[DevicePowerDependency, ...],
) -> list[dict[str, str]]:
    """Encode canonical contract field names."""

    return [
        {
            "dependentEntityId": item.dependent_entity_id,
            "powerSourceEntityId": item.power_source_entity_id,
            "policy": item.policy,
        }
        for item in dependencies
    ]


def device_power_dependency_mapping(
    dependencies: tuple[DevicePowerDependency, ...],
) -> dict[str, str]:
    """Return the current dependent to source lookup."""

    return {
        item.dependent_entity_id: item.power_source_entity_id for item in dependencies
    }


def effective_device_state(
    entity_id: str,
    dependencies: Mapping[str, str],
    state_reader: Callable[[str], str | None],
) -> tuple[str, PowerDependencyStatus | None]:
    """Resolve a device state through an acyclic requires-on graph."""

    memo: dict[str, str] = {}

    def resolve(current_entity_id: str, visiting: frozenset[str]) -> str:
        if current_entity_id in memo:
            return memo[current_entity_id]
        if current_entity_id in visiting:
            return "unknown"
        reported = state_reader(current_entity_id)
        source_entity_id = dependencies.get(current_entity_id)
        if source_entity_id is None:
            result = reported if reported is not None else "unknown"
        else:
            source_state = resolve(source_entity_id, visiting | {current_entity_id})
            if source_state == "on":
                result = reported if reported is not None else "unknown"
            elif source_state == "off":
                result = "off"
            else:
                result = "unknown"
        memo[current_entity_id] = result
        return result

    effective = resolve(entity_id, frozenset())
    source_entity_id = dependencies.get(entity_id)
    if source_entity_id is None:
        return effective, None
    source_state = resolve(source_entity_id, frozenset({entity_id}))
    if source_state == "on":
        return effective, PowerDependencyStatus(
            source_entity_id=source_entity_id,
            state="powered",
            reason="power_source_on",
            blocks_commands=False,
        )
    if source_state == "off":
        return effective, PowerDependencyStatus(
            source_entity_id=source_entity_id,
            state="unpowered",
            reason="power_source_off",
            blocks_commands=True,
        )
    return effective, PowerDependencyStatus(
        source_entity_id=source_entity_id,
        state="unavailable",
        reason="power_source_unavailable",
        blocks_commands=True,
    )


def _entity_id(value: object, label: str) -> None:
    if not isinstance(value, str) or not _ENTITY_ID.fullmatch(value):
        raise DevicePowerDependencyViolation(f"{label} id is invalid")


def _reject_cycles(mapping: Mapping[str, str]) -> None:
    visited: set[str] = set()
    visiting: set[str] = set()

    def visit(entity_id: str) -> None:
        if entity_id in visited:
            return
        if entity_id in visiting:
            raise DevicePowerDependencyViolation(
                "device power dependencies must not contain a cycle"
            )
        visiting.add(entity_id)
        source_entity_id = mapping.get(entity_id)
        if source_entity_id is not None:
            visit(source_entity_id)
        visiting.remove(entity_id)
        visited.add(entity_id)

    for entity_id in mapping:
        visit(entity_id)
