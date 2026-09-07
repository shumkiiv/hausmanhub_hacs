from __future__ import annotations

import json
from pathlib import Path

from custom_components.hausman_hub.application.scenario_consolidation_inventory import (
    ConsolidationOperation,
    NATIVE_AUTOMATIONS_TO_DISABLE,
    inventory_from_payload,
)
from custom_components.hausman_hub.domain.scenarios import ScenarioRegistry


EXPECTED_REPLACE_IDS = {
    "system-shower-comfort-controller",
    "system-small-corridor-light-controller",
    "system-tambur-adaptive-controller",
}
EXPECTED_UPDATE_IDS = {
    "scenario_manual_curtains_open",
    "scenario_manual_curtains_close",
}
EXPECTED_DISABLE_IDS = {
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
EXPECTED_PRESERVE_IDS = {
    "scenario-office-curtain-living-lights-close",
    "scenario-office-curtain-service-lights-close",
    "scenario_kitchen_ac_off_2200",
    "scenario_manual_ac_off",
    "scenario_manual_away",
    "scenario_manual_good_night",
    "scenario_manual_lights_off",
    "scenario_manual_water_close",
    "scenario_manual_water_open",
    "scenario_small_corridor_motion_after_sunset",
    "scenario_small_corridor_motion_low_light",
    "system-away-turn-off",
    "system-kitchen-curtains-open-weekday",
    "system-kitchen-curtains-open-weekend",
    "system-leak-bathroom-alert",
    "system-leak-extra-bathroom-alert",
    "system-leak-kitchen-alert",
    "system-leak-toilet-alert",
    "system-office-curtain-auto-close",
    "system-small-corridor-ambient-dark",
    "system-small-corridor-ambient-dark-bright",
    "system-small-corridor-ambient-dusk",
    "system-small-corridor-ambient-dusk-bright",
    "system-small-corridor-ambient-night-bright",
}
EXPECTED_CREATE_IDS = {
    "system-toilet-comfort-controller",
    "system-bathroom-exhaust-controller",
    "system-storage-light-controller",
    "system-cabinet-light-controller",
    "system-curtains-privacy-controller",
}


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
    expected = {
        **{scenario_id: "replace" for scenario_id in EXPECTED_REPLACE_IDS},
        **{scenario_id: "disable" for scenario_id in EXPECTED_DISABLE_IDS},
        **{scenario_id: "preserve" for scenario_id in EXPECTED_PRESERVE_IDS},
    }
    assert {scenario.id for scenario in registry.scenarios} == (
        EXPECTED_REPLACE_IDS
        | EXPECTED_UPDATE_IDS
        | EXPECTED_DISABLE_IDS
        | EXPECTED_PRESERVE_IDS
    )
    assert inventory.absent_managed_ids == EXPECTED_CREATE_IDS
    assert {
        scenario_id: disposition.value
        for scenario_id, disposition in classifications.items()
    } == expected | {
        scenario_id: "update"
        for scenario_id in EXPECTED_UPDATE_IDS
    }


def test_unknown_snapshot_id_is_rejected_instead_of_preserved() -> None:
    payload = json.loads(
        (
            Path(__file__).parents[1]
            / "fixtures/hausmanhub_scenario_consolidation_v1/inventory58.json"
        ).read_text()
    )
    inventory = inventory_from_payload(payload)

    try:
        inventory.classify_snapshot_scenario("scenario_foreign")
    except KeyError as error:
        assert error.args == ("scenario_foreign",)
    else:
        raise AssertionError("unknown scenario was silently preserved")


def test_immutable_native_snapshot_classifies_exactly_five_disables() -> None:
    fixture_root = Path(__file__).parents[1] / "fixtures" / "hausmanhub_scenario_consolidation_v1"
    inventory = inventory_from_payload(json.loads((fixture_root / "inventory58.json").read_text()))
    native = json.loads((fixture_root / "native18.json").read_text())
    automation_ids = tuple(item["definition"]["id"] for item in native["automations"])

    classifications = inventory.classify_native_automations(automation_ids)

    assert len(native["automations"]) == 18
    assert sum(item is ConsolidationOperation.DISABLE for item in classifications.values()) == 5
    assert sum(item is ConsolidationOperation.PRESERVE for item in classifications.values()) == 13
