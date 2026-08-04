"""Pure tests for explicit read-only candidate import into a draft."""

from __future__ import annotations

import copy
import json
import unittest

from custom_components.hausman_hub.application.climate_native_projections import (
    native_android_climate_snapshot,
)
from custom_components.hausman_hub.application.climate_native_setup import (
    ClimateHaCatalogEntry,
    ClimateHaCatalogRoom,
    ClimateHaEntityCatalog,
    build_native_climate_setup_snapshot,
)
from custom_components.hausman_hub.domain.climate_observation import (
    ClimateControlObservation,
    ClimateDataStatus,
    ClimateHomeObservation,
    ClimateObservationSnapshot,
    ClimateRoomObservation,
    ClimateRoomMode,
)
from tests.climate_bridge_fixture import (
    import_climate_state,
)
from custom_components.hausman_hub.application.climate_registry_import import (
    ClimateRegistryImportViolation,
    add_import_candidate_to_registry,
    candidate_control_domain,
    import_candidate_is_unchanged,
    import_managed_climate_selection,
)
from custom_components.hausman_hub.domain.climate import ClimateRegistry
from custom_components.hausman_hub.domain.climate_bridge import ClimateControlMode
from tests.test_climate_import import source_payload


class ClimateRegistryImportTest(unittest.TestCase):
    def test_native_entities_keep_endpoints_when_already_assigned_to_an_area(self) -> None:
        observation = ClimateObservationSnapshot(
            observed_at=1,
            source_generated_at=1,
            data_status=ClimateDataStatus.FRESH,
            home=ClimateHomeObservation(),
            control=ClimateControlObservation(),
            rooms=(
                ClimateRoomObservation(
                    room_id="living",
                    name="Гостиная",
                    data_status=ClimateDataStatus.FRESH,
                    mode=ClimateRoomMode.AUTO,
                ),
            ),
            devices=(),
        )
        catalog = ClimateHaEntityCatalog(
            rooms=(ClimateHaCatalogRoom(room_id="living", name="Гостиная"),),
            entries=(
                ClimateHaCatalogEntry(
                    entity_id="climate.living_ac",
                    domain="climate",
                    state="off",
                    device_class=None,
                    supported_features=385,
                    friendly_name="Кондиционер",
                    available=True,
                    last_updated_ms=1,
                    room_id="living",
                    hvac_modes=("off", "cool", "heat"),
                ),
                ClimateHaCatalogEntry(
                    entity_id="sensor.living_temperature",
                    domain="sensor",
                    state="25.0",
                    device_class="temperature",
                    supported_features=0,
                    friendly_name="Температура",
                    available=True,
                    last_updated_ms=1,
                    room_id="living",
                ),
            ),
        )
        snapshot = build_native_climate_setup_snapshot(
            ClimateRegistry(),
            observation,
            catalog,
        )

        result = import_managed_climate_selection(
            snapshot,
            room_ids=["living"],
            source_ids=["climate.living_ac", "sensor.living_temperature"],
            source_kinds={
                "climate.living_ac": "air_conditioner",
                "sensor.living_temperature": "temperature_sensor",
            },
        )

        by_kind = {device.kind.value: device for device in result.devices}
        self.assertEqual(
            "climate.living_ac",
            by_kind["air_conditioner"].endpoints[0].entity_id,
        )
        self.assertEqual(
            "sensor.living_temperature",
            by_kind["temperature_sensor"].endpoints[0].entity_id,
        )

    def test_managed_selection_keeps_an_explicit_supported_device_kind(self) -> None:
        source = source_payload()
        source["devices"][0]["category"] = "floor_heating"  # type: ignore[index]
        snapshot = import_climate_state(source)
        source_id = "synthetic-ac-source-living"

        registry = import_managed_climate_selection(
            snapshot,
            room_ids=["living"],
            source_ids=[source_id],
            source_kinds={source_id: "floor_heating"},
        )

        self.assertEqual("floor_heating", registry.devices[0].kind.value)
        with self.assertRaisesRegex(ClimateRegistryImportViolation, "exactly match"):
            import_managed_climate_selection(
                snapshot,
                room_ids=["living"],
                source_ids=[source_id],
                source_kinds={},
            )

    def test_explicit_candidate_adds_room_and_infers_only_supported_capabilities(
        self,
    ) -> None:
        snapshot = import_climate_state(source_payload(), now_ms=1784280005000)
        original = ClimateRegistry()

        registry = add_import_candidate_to_registry(
            original,
            snapshot,
            source_id="synthetic-ac-source-living",
            device_id="living_ac",
            device_name="Living AC",
            kind="air_conditioner",
            control_scope="canary",
            control_owner="climate_core",
            control_entity_id="climate.synthetic_living_ac",
        )

        self.assertEqual((), original.rooms)
        self.assertEqual(["living"], [room.room_id for room in registry.rooms])
        self.assertEqual("synthetic-ac-source-living", registry.devices[0].source_id)
        self.assertEqual(
            ["power", "target_temperature", "hvac_mode", "fan_mode"],
            [value.value for value in registry.devices[0].capabilities],
        )
        self.assertEqual("climate", candidate_control_domain("air_conditioner"))

        observation = ClimateObservationSnapshot(
            observed_at=snapshot.generated_at,
            source_generated_at=snapshot.generated_at,
            data_status=ClimateDataStatus.FRESH,
            home=ClimateHomeObservation(),
            control=ClimateControlObservation(),
            rooms=tuple(
                ClimateRoomObservation(
                    room_id=room.room_id,
                    name=room.name,
                    data_status=ClimateDataStatus.FRESH,
                    mode=ClimateRoomMode.AUTO,
                )
                for room in registry.rooms
            ),
            devices=(),
        )
        catalog = ClimateHaEntityCatalog(entries=())
        native_snapshot = build_native_climate_setup_snapshot(
            registry,
            observation,
            catalog,
        )
        public = json.dumps(
            native_android_climate_snapshot(
                registry,
                observation,
                bridge_mode=ClimateControlMode.MANAGED,
            ),
            sort_keys=True,
        )
        self.assertNotIn("synthetic-ac-source-living", public)
        self.assertNotIn("climate.synthetic_living_ac", public)

    def test_candidate_import_rejects_stale_drift_duplicates_and_bad_domains(
        self,
    ) -> None:
        snapshot = import_climate_state(source_payload(), now_ms=1784280005000)
        stale_source = source_payload()
        stale_source["runtimeHealth"]["status"] = "stale"  # type: ignore[index]
        stale = import_climate_state(stale_source)

        with self.assertRaisesRegex(ClimateRegistryImportViolation, "fresh"):
            add_import_candidate_to_registry(
                ClimateRegistry(),
                stale,
                source_id="synthetic-ac-source-living",
                device_id="living_ac",
                device_name="Living AC",
                kind="air_conditioner",
                control_scope="observed",
                control_owner="observed",
                control_entity_id="climate.synthetic_living_ac",
            )
        with self.assertRaisesRegex(ClimateRegistryImportViolation, "unavailable"):
            add_import_candidate_to_registry(
                ClimateRegistry(),
                snapshot,
                source_id="missing-source",
                device_id="living_ac",
                device_name="Living AC",
                kind="air_conditioner",
                control_scope="observed",
                control_owner="observed",
                control_entity_id="climate.synthetic_living_ac",
            )
        with self.assertRaisesRegex(ClimateRegistryImportViolation, "domain"):
            add_import_candidate_to_registry(
                ClimateRegistry(),
                snapshot,
                source_id="synthetic-ac-source-living",
                device_id="living_ac",
                device_name="Living AC",
                kind="air_conditioner",
                control_scope="observed",
                control_owner="observed",
                control_entity_id="switch.synthetic_living_ac",
            )

        registered = add_import_candidate_to_registry(
            ClimateRegistry(),
            snapshot,
            source_id="synthetic-ac-source-living",
            device_id="living_ac",
            device_name="Living AC",
            kind="air_conditioner",
            control_scope="observed",
            control_owner="observed",
            control_entity_id="climate.synthetic_living_ac",
        )
        with self.assertRaisesRegex(ClimateRegistryImportViolation, "already registered"):
            add_import_candidate_to_registry(
                registered,
                snapshot,
                source_id="synthetic-ac-source-living",
                device_id="another_id",
                device_name="Another AC",
                kind="air_conditioner",
                control_scope="observed",
                control_owner="observed",
                control_entity_id="climate.another_ac",
            )

    def test_candidate_kind_and_commands_must_support_registry_minimums(self) -> None:
        snapshot = import_climate_state(source_payload())
        with self.assertRaisesRegex(ClimateRegistryImportViolation, "not suggested"):
            add_import_candidate_to_registry(
                ClimateRegistry(),
                snapshot,
                source_id="synthetic-ac-source-living",
                device_id="living_humidifier",
                device_name="Wrong kind",
                kind="humidifier",
                control_scope="observed",
                control_owner="observed",
                control_entity_id="humidifier.wrong_kind",
            )

        insufficient_source = copy.deepcopy(source_payload())
        insufficient_source["capabilities"][0]["commandTypes"] = [  # type: ignore[index]
            "climate.set_temperature"
        ]
        insufficient = import_climate_state(insufficient_source)
        with self.assertRaisesRegex(ClimateRegistryImportViolation, "missing required"):
            add_import_candidate_to_registry(
                ClimateRegistry(),
                insufficient,
                source_id="synthetic-ac-source-living",
                device_id="living_ac",
                device_name="Living AC",
                kind="air_conditioner",
                control_scope="observed",
                control_owner="observed",
                control_entity_id="climate.synthetic_living_ac",
            )

    def test_repeat_read_allows_live_values_but_rejects_binding_drift(self) -> None:
        first_source = source_payload()
        first = import_climate_state(first_source)
        live_change_source = copy.deepcopy(first_source)
        live_change_source["rooms"][0]["sourceData"]["temperature"] = 26.5  # type: ignore[index]
        live_change_source["devices"][0]["state"] = "idle"  # type: ignore[index]
        live_change_source["devices"][0]["unavailable"] = True  # type: ignore[index]
        live_change = import_climate_state(live_change_source)

        self.assertTrue(
            import_candidate_is_unchanged(
                first,
                live_change,
                "synthetic-ac-source-living",
            )
        )

        binding_change_source = copy.deepcopy(first_source)
        binding_change_source["capabilities"][0]["commandTypes"].remove(  # type: ignore[index]
            "climate.set_fan_mode"
        )
        binding_change = import_climate_state(binding_change_source)
        self.assertFalse(
            import_candidate_is_unchanged(
                first,
                binding_change,
                "synthetic-ac-source-living",
            )
        )


if __name__ == "__main__":
    unittest.main()
