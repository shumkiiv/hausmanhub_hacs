from __future__ import annotations

import json
from pathlib import Path

from custom_components.hausman_hub.application.scenario_consolidation_inventory import (
    ConsolidationOperation,
    NATIVE_AUTOMATIONS_TO_DISABLE,
    inventory_from_payload,
)
from custom_components.hausman_hub.domain.scenarios import ScenarioRegistry


def test_independent_fixture58_describes_three_existing_and_five_absent_controllers() -> None:
    payload = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "hausmanhub_scenario_consolidation_v1" / "inventory58.json").read_text()
    )

    inventory = inventory_from_payload(payload)

    assert {(item.scenario_id, item.revision) for item in inventory.managed} == {
        ("system-shower-comfort-controller", 4),
        ("system-small-corridor-light-controller", 3),
        ("system-tambur-adaptive-controller", 8),
    }
    assert inventory.operation_for("system-storage-light-controller") is ConsolidationOperation.CREATE
    assert inventory.operation_for("system-tambur-adaptive-controller") is ConsolidationOperation.REPLACE
    assert inventory.native_disable_ids == NATIVE_AUTOMATIONS_TO_DISABLE


def test_immutable_snapshot_registry_parses_all_58_real_definitions() -> None:
    fixture_root = Path(__file__).parents[1] / "fixtures" / "hausmanhub_scenario_consolidation_v1"
    inventory = inventory_from_payload(json.loads((fixture_root / "inventory58.json").read_text()))
    registry = ScenarioRegistry.from_storage(json.loads((fixture_root / "scenarios58.json").read_text()))

    assert len(registry.scenarios) == 58
    assert sum(scenario.enabled for scenario in registry.scenarios) == 46
    assert registry.scenario("scenario_manual_away").definition.actions
    assert registry.scenario("system-bathroom-fan-off-night").definition.triggers
    classifications = inventory.classify_snapshot_registry(
        tuple(scenario.id for scenario in registry.scenarios)
    )
    assert set(classifications.values()) == {
        ConsolidationOperation.REPLACE, ConsolidationOperation.PRESERVE
    }
    assert sum(item is ConsolidationOperation.REPLACE for item in classifications.values()) == 3
    assert sum(item is ConsolidationOperation.PRESERVE for item in classifications.values()) == 55


def test_immutable_native_snapshot_classifies_exactly_five_disables() -> None:
    fixture_root = Path(__file__).parents[1] / "fixtures" / "hausmanhub_scenario_consolidation_v1"
    inventory = inventory_from_payload(json.loads((fixture_root / "inventory58.json").read_text()))
    native = json.loads((fixture_root / "native18.json").read_text())
    automation_ids = tuple(item["definition"]["id"] for item in native["automations"])

    classifications = inventory.classify_native_automations(automation_ids)

    assert len(native["automations"]) == 18
    assert sum(item is ConsolidationOperation.DISABLE for item in classifications.values()) == 5
    assert sum(item is ConsolidationOperation.PRESERVE for item in classifications.values()) == 13
