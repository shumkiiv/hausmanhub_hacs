"""Immutable migration inventory and safety classifications for scenario consolidation."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class ConsolidationOperation(StrEnum):
    """Exactly one disposition for each inventory record."""

    CREATE = "create"
    REPLACE = "replace"
    DISABLE = "disable"
    PRESERVE = "preserve"


NATIVE_AUTOMATIONS_TO_DISABLE = frozenset(
    {
        "hausman_night_absence_tambur_light_off",
        "hausman_night_absence_small_corridor_light_off",
        "hausman_shower_cabinet_off_absence_failsafe",
        "hausman_shower_fan_humidity_on_failsafe",
        "hausman_shower_fan_off_absence_normal_humidity",
    }
)
PROTECTED_WATER_TARGET_IDS = frozenset(
    {"entity_e3941765c1f247c5", "entity_cadf80670b42abc3"}
)

# Every existing entry point whose old generation can overlap the consolidated
# controllers.  The two curtain wrappers keep their IDs but are also closed
# while their definitions are replaced in the later manifest phase.
REGISTRY_SCENARIO_IDS_TO_DRAIN = frozenset(
    {
        "system-shower-comfort-controller",
        "system-small-corridor-light-controller",
        "system-tambur-adaptive-controller",
        "scenario_manual_curtains_open",
        "scenario_manual_curtains_close",
        "system-bathroom-fan-off-night",
        "system-bathroom-fan-off-day-sustained",
        "system-bathroom-fan-day-light-1",
        "system-bathroom-fan-day-light-2",
        "system-bathroom-fan-night-light-1",
        "system-bathroom-fan-night-light-2",
        "system-bathroom-fan-morning-light-1",
        "system-bathroom-fan-morning-quiet",
        "system-office-evening-bright",
        "system-office-evening-dark",
        "system-office-evening-medium",
        "system-office-day-medium",
        "system-office-day-bright",
        "system-office-night-light",
        "system-office-day-low",
        "system-storage-light-on-presence",
        "system-storage-light-off-timer",
        "system-storage-light-off-confirmed",
        "system-toilet-fan-off-delay",
        "system-toilet-fan-day",
        "system-toilet-light-motion-night",
        "system-toilet-light-motion-evening",
        "system-toilet-light-motion",
        "scenario_msirqdih",
        "system-twilight-curtains-close",
        "scenario_kitchen_curtains_sunrise",
        "scenario_curtains_lights_living",
        "scenario_curtains_lights_service",
        "scenario_mssvmo6v",
    }
)


@dataclass(frozen=True, slots=True)
class ManagedSnapshot:
    """Minimal, non-secret CAS evidence extracted from the read-only snapshot."""

    scenario_id: str
    revision: int
    enabled: bool
    flow_id: str
    source_hash: str
    topology: str
    endpoint: str
    input_target_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ConsolidationInventory:
    """Independent baseline used to reject a manifest that invents live state."""

    scenario_count: int
    enabled_count: int
    managed: tuple[ManagedSnapshot, ...]
    snapshot_scenario_ids: frozenset[str]
    absent_managed_ids: frozenset[str]
    native_disable_ids: frozenset[str]
    native_preserve_count: int

    def __post_init__(self) -> None:
        if self.scenario_count != 58 or self.enabled_count != 46:
            raise ValueError("scenario consolidation inventory no longer matches the verified snapshot")
        if len(self.managed) != 3 or len({item.scenario_id for item in self.managed}) != 3:
            raise ValueError("inventory must contain exactly three managed controllers")
        if len(self.snapshot_scenario_ids) != self.scenario_count:
            raise ValueError("inventory must contain every verified scenario id")
        if not {item.scenario_id for item in self.managed} <= self.snapshot_scenario_ids:
            raise ValueError("managed controller is missing from the source inventory")
        if len(self.absent_managed_ids) != 5:
            raise ValueError("inventory must contain exactly five absent controllers")
        if self.native_disable_ids != NATIVE_AUTOMATIONS_TO_DISABLE:
            raise ValueError("native automation disposition changed without review")
        if self.native_preserve_count != 13:
            raise ValueError("native preserve count changed without review")

    def operation_for(self, scenario_id: str) -> ConsolidationOperation:
        if scenario_id in self.absent_managed_ids:
            return ConsolidationOperation.CREATE
        if scenario_id in {item.scenario_id for item in self.managed}:
            return ConsolidationOperation.REPLACE
        raise KeyError(scenario_id)

    def classify_snapshot_scenario(
        self, scenario_id: str
    ) -> ConsolidationOperation:
        """Classify every scenario captured in the immutable source registry."""

        if scenario_id in {item.scenario_id for item in self.managed}:
            return ConsolidationOperation.REPLACE
        return ConsolidationOperation.PRESERVE

    def classify_snapshot_registry(
        self, scenario_ids: tuple[str, ...]
    ) -> dict[str, ConsolidationOperation]:
        """Fail closed unless the source registry is the verified 58-record set."""

        if frozenset(scenario_ids) != self.snapshot_scenario_ids:
            raise ValueError("scenario registry does not match the verified snapshot")
        return {
            scenario_id: self.classify_snapshot_scenario(scenario_id)
            for scenario_id in scenario_ids
        }

    def classify_native_automations(
        self, automation_ids: tuple[str, ...]
    ) -> dict[str, ConsolidationOperation]:
        """Keep every native rule unless its exact immutable ID is replaced."""

        if len(automation_ids) != 18 or len(set(automation_ids)) != 18:
            raise ValueError("native automation inventory does not match the verified snapshot")
        if not self.native_disable_ids <= set(automation_ids):
            raise ValueError("native automation selected for disable is absent")
        return {
            automation_id: (
                ConsolidationOperation.DISABLE
                if automation_id in self.native_disable_ids
                else ConsolidationOperation.PRESERVE
            )
            for automation_id in automation_ids
        }


def inventory_from_payload(value: object) -> ConsolidationInventory:
    """Decode only the non-secret fixture schema used by migration tests."""

    if not isinstance(value, Mapping) or not isinstance(value.get("managed"), list):
        raise ValueError("scenario consolidation inventory is invalid")
    managed: list[ManagedSnapshot] = []
    for item in value["managed"]:
        if not isinstance(item, Mapping):
            raise ValueError("managed scenario snapshot is invalid")
        inputs = item.get("inputTargetIds")
        if not isinstance(inputs, list) or not all(isinstance(value, str) for value in inputs):
            raise ValueError("managed scenario input targets are invalid")
        fields = ("id", "flowId", "sourceHash", "topology", "endpoint")
        if not all(isinstance(item.get(field), str) and item[field] for field in fields):
            raise ValueError("managed scenario CAS evidence is invalid")
        if not isinstance(item.get("revision"), int) or not isinstance(item.get("enabled"), bool):
            raise ValueError("managed scenario revision evidence is invalid")
        managed.append(ManagedSnapshot(
            scenario_id=item["id"], revision=item["revision"], enabled=item["enabled"],
            flow_id=item["flowId"], source_hash=item["sourceHash"],
            topology=item["topology"], endpoint=item["endpoint"],
            input_target_ids=tuple(inputs),
        ))
    native = value.get("native")
    absent = value.get("absentManagedIds")
    snapshot_ids = value.get("snapshotScenarioIds")
    if not isinstance(native, Mapping) or not isinstance(absent, list) or not isinstance(snapshot_ids, list):
        raise ValueError("scenario consolidation disposition is invalid")
    disable = native.get("disable")
    if not isinstance(disable, list) or not all(isinstance(item, str) for item in disable):
        raise ValueError("native disable disposition is invalid")
    if not all(isinstance(item, str) for item in absent):
        raise ValueError("absent controller disposition is invalid")
    if not all(isinstance(item, str) and item for item in snapshot_ids):
        raise ValueError("scenario inventory ids are invalid")
    return ConsolidationInventory(
        scenario_count=value.get("scenarioCount"), enabled_count=value.get("enabledCount"),
        managed=tuple(managed), snapshot_scenario_ids=frozenset(snapshot_ids),
        absent_managed_ids=frozenset(absent),
        native_disable_ids=frozenset(disable), native_preserve_count=native.get("preserveCount"),
    )
