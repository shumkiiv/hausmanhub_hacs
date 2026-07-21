"""Contract tests for the native setup discovery foundation (36f1)."""

from __future__ import annotations

import unittest

from custom_components.hausman_hub.application.climate_native_setup import (
    ClimateHaCatalogEntry,
    ClimateHaEntityCatalog,
    ClimateNativeSetupViolation,
    build_native_climate_setup_snapshot,
)
from custom_components.hausman_hub.domain.climate import ClimateDeviceKind
from custom_components.hausman_hub.domain.climate_observation import (
    ClimateDataStatus,
)
from tests.test_climate_native_projections import _native_observation, _setup

GENERATED_AT = 1784280000000


def _catalog(entries: list[ClimateHaCatalogEntry]) -> ClimateHaEntityCatalog:
    return ClimateHaEntityCatalog(entries=tuple(entries))


def _entry(
    entity_id: str,
    *,
    state: str = "cool",
    device_class: str | None = None,
    supported_features: int = 0,
    friendly_name: str | None = None,
    available: bool = True,
) -> ClimateHaCatalogEntry:
    domain = entity_id.split(".", 1)[0]
    return ClimateHaCatalogEntry(
        entity_id=entity_id,
        domain=domain,
        state=state,
        device_class=device_class,
        supported_features=supported_features,
        friendly_name=friendly_name,
        available=available,
        last_updated_ms=GENERATED_AT,
    )


def _bound_catalog() -> ClimateHaEntityCatalog:
    return _catalog(
        [
            _entry("climate.living_air_conditioner", friendly_name="Living AC"),
            _entry(
                "sensor.living_temperature_observation",
                state="25.8",
                device_class="temperature",
            ),
            _entry(
                "sensor.living_humidity_observation",
                state="44.0",
                device_class="humidity",
            ),
        ]
    )


class NativeSetupSnapshotTest(unittest.TestCase):
    """The native setup snapshot mirrors the wizard discovery contract."""

    def test_bound_devices_keep_private_identity_and_registry_room(self) -> None:
        registry, contours, _ = _setup()
        bound_registry, observation = _native_observation(registry, contours)

        snapshot = build_native_climate_setup_snapshot(
            bound_registry,
            observation,
            _bound_catalog(),
        )

        self.assertEqual(GENERATED_AT, snapshot.generated_at)
        self.assertTrue(snapshot.runtime_fresh)
        ac = snapshot.device("synthetic-ac-source-living")
        self.assertIsNotNone(ac)
        self.assertEqual("living", ac.room_id)
        self.assertEqual("Living AC", ac.name)
        self.assertEqual("climate", ac.domain)
        self.assertEqual("cool", ac.state)
        self.assertTrue(ac.available)
        self.assertEqual((ClimateDeviceKind.AIR_CONDITIONER,), ac.suggested_kinds)
        self.assertEqual(
            {
                "climate.turn_off",
                "climate.set_hvac_mode",
                "climate.set_temperature",
                "climate.set_fan_mode",
            },
            set(ac.command_types),
        )
        temperature = snapshot.device("synthetic-living_temperature_observation")
        self.assertEqual("25.8", temperature.state)
        self.assertEqual(
            (ClimateDeviceKind.TEMPERATURE_SENSOR,), temperature.suggested_kinds
        )

    def test_rooms_come_from_the_registry_and_native_observation(self) -> None:
        registry, contours, _ = _setup()
        bound_registry, observation = _native_observation(registry, contours)

        snapshot = build_native_climate_setup_snapshot(
            bound_registry,
            observation,
            _bound_catalog(),
        )

        living = snapshot.room("living")
        self.assertEqual("Living room", living.name)
        self.assertEqual(25.8, living.temperature)
        self.assertEqual(44.0, living.humidity)
        self.assertEqual(25.0, living.target_temperature)
        self.assertEqual("auto", living.mode)
        self.assertTrue(living.authority_eligible)
        self.assertIsNone(snapshot.room("kids"))

    def test_unbound_entities_become_candidates_with_entity_identity(self) -> None:
        registry, contours, _ = _setup()
        bound_registry, observation = _native_observation(registry, contours)
        catalog = _catalog(
            [
                *_bound_catalog().entries,
                _entry(
                    "climate.guest_ac",
                    state="heat",
                    supported_features=1 | 8 | 128,
                    friendly_name="Guest AC",
                ),
                _entry(
                    "sensor.guest_temperature",
                    state="21.5",
                    device_class="temperature",
                    friendly_name="Guest temperature",
                ),
                _entry("switch.guest_socket", state="on"),
            ]
        )

        snapshot = build_native_climate_setup_snapshot(
            bound_registry,
            observation,
            catalog,
        )

        guest_ac = snapshot.device("climate.guest_ac")
        self.assertEqual("", guest_ac.room_id)
        self.assertEqual("Guest AC", guest_ac.name)
        self.assertEqual("climate", guest_ac.domain)
        self.assertEqual("heat", guest_ac.state)
        self.assertTrue(guest_ac.available)
        self.assertEqual((ClimateDeviceKind.AIR_CONDITIONER,), guest_ac.suggested_kinds)
        self.assertEqual(
            {
                "climate.set_hvac_mode",
                "climate.turn_off",
                "climate.set_temperature",
                "climate.set_fan_mode",
            },
            set(guest_ac.command_types),
        )
        guest_sensor = snapshot.device("sensor.guest_temperature")
        self.assertEqual(
            (ClimateDeviceKind.TEMPERATURE_SENSOR,), guest_sensor.suggested_kinds
        )
        self.assertEqual((), guest_sensor.command_types)
        # The scanner itself filters domains; a stray switch passed directly
        # still receives no suggested kinds and no commands.
        stray = snapshot.device("switch.guest_socket")
        self.assertEqual((), stray.suggested_kinds)
        self.assertEqual((), stray.command_types)

    def test_missing_and_unavailable_entities_stay_fail_closed(self) -> None:
        registry, contours, _ = _setup()
        bound_registry, observation = _native_observation(registry, contours)
        catalog = _catalog(
            [
                _entry(
                    "climate.living_air_conditioner",
                    state="unavailable",
                    available=False,
                ),
                _entry(
                    "sensor.living_temperature_observation",
                    state="25.8",
                    device_class="temperature",
                ),
            ]
        )

        snapshot = build_native_climate_setup_snapshot(
            bound_registry,
            observation,
            catalog,
        )

        ac = snapshot.device("synthetic-ac-source-living")
        self.assertFalse(ac.available)
        self.assertEqual("unavailable", ac.state)
        humidity = snapshot.device("synthetic-living_humidity_observation")
        self.assertFalse(humidity.available)
        self.assertEqual("", humidity.state)

    def test_stale_observation_marks_the_snapshot_not_fresh(self) -> None:
        registry, contours, _ = _setup()
        bound_registry, observation = _native_observation(registry, contours)
        stale = type(observation)(
            observed_at=observation.observed_at,
            source_generated_at=observation.source_generated_at,
            data_status=ClimateDataStatus.STALE,
            home=observation.home,
            control=observation.control,
            rooms=observation.rooms,
            devices=observation.devices,
        )

        snapshot = build_native_climate_setup_snapshot(
            bound_registry,
            stale,
            _bound_catalog(),
        )

        self.assertFalse(snapshot.runtime_fresh)

    def test_catalog_rejects_duplicates_and_builder_rejects_bad_inputs(self) -> None:
        registry, contours, _ = _setup()
        bound_registry, observation = _native_observation(registry, contours)

        with self.assertRaises(ClimateNativeSetupViolation):
            _catalog(
                [
                    _entry("climate.guest_ac"),
                    _entry("climate.guest_ac", state="heat"),
                ]
            )
        with self.assertRaises(ClimateNativeSetupViolation):
            build_native_climate_setup_snapshot(None, observation, _bound_catalog())
        with self.assertRaises(ClimateNativeSetupViolation):
            build_native_climate_setup_snapshot(
                bound_registry, None, _bound_catalog()
            )
        with self.assertRaises(ClimateNativeSetupViolation):
            build_native_climate_setup_snapshot(bound_registry, observation, None)


class _FakeState:
    def __init__(
        self,
        entity_id: str,
        state: str,
        attributes: dict[str, object],
    ) -> None:
        self.entity_id = entity_id
        self.state = state
        self.attributes = attributes
        self.last_updated = self

    def timestamp(self) -> float:
        return GENERATED_AT / 1000


class _FakeStates:
    def __init__(self, values: list[_FakeState]) -> None:
        self._values = {state.entity_id: state for state in values}

    def async_all(self) -> list[_FakeState]:
        return list(self._values.values())


class _FakeHass:
    def __init__(self, values: list[_FakeState]) -> None:
        self.states = _FakeStates(values)


class HomeAssistantEntityCatalogTest(unittest.TestCase):
    """The outer boundary enumerates only bounded climate-relevant entities."""

    def test_catalog_filters_domains_and_sensor_device_classes(self) -> None:
        from custom_components.hausman_hub.climate_ha_state_view import (
            HomeAssistantClimateStateView,
        )

        hass = _FakeHass(
            [
                _FakeState(
                    "climate.living_ac",
                    "cool",
                    {"friendly_name": "Living AC", "supported_features": 137},
                ),
                _FakeState(
                    "sensor.living_temperature",
                    "25.8",
                    {"device_class": "temperature"},
                ),
                _FakeState(
                    "sensor.living_humidity",
                    "44",
                    {"device_class": "humidity"},
                ),
                _FakeState("sensor.power_meter", "120", {"device_class": "power"}),
                _FakeState("switch.guest_socket", "on", {}),
                _FakeState("humidifier.kids", "off", {"friendly_name": "Kids"}),
                _FakeState("sensor.oversized", "x" * 65, {"device_class": "temperature"}),
            ]
        )
        view = HomeAssistantClimateStateView(hass)  # type: ignore[arg-type]

        catalog = view.entity_catalog()

        by_id = {entry.entity_id: entry for entry in catalog.entries}
        self.assertEqual(
            {
                "climate.living_ac",
                "sensor.living_temperature",
                "sensor.living_humidity",
                "humidifier.kids",
            },
            set(by_id),
        )
        ac = by_id["climate.living_ac"]
        self.assertEqual("climate", ac.domain)
        self.assertEqual(137, ac.supported_features)
        self.assertEqual("Living AC", ac.friendly_name)
        self.assertTrue(ac.available)
        self.assertEqual(GENERATED_AT, ac.last_updated_ms)
        unavailable = _FakeHass(
            [_FakeState("climate.living_ac", "unavailable", {})]
        )
        catalog = HomeAssistantClimateStateView(  # type: ignore[arg-type]
            unavailable
        ).entity_catalog()
        self.assertFalse(catalog.entries[0].available)


class NativeSetupWizardChainTest(unittest.TestCase):
    """The native snapshot drives the existing wizard builders unchanged."""

    def test_candidates_match_bound_devices_and_list_unbound_entities(self) -> None:
        from custom_components.hausman_hub.application.climate_setup import (
            climate_device_candidates,
        )

        registry, contours, _ = _setup()
        bound_registry, observation = _native_observation(registry, contours)
        catalog = _catalog(
            [
                *_bound_catalog().entries,
                _entry("climate.guest_ac", supported_features=129),
            ]
        )
        snapshot = build_native_climate_setup_snapshot(
            bound_registry,
            observation,
            catalog,
        )

        result = climate_device_candidates(bound_registry, snapshot)
        by_name = {
            candidate["configured_device_id"] or candidate["name"]: candidate
            for candidate in result["candidates"]
        }

        ac = by_name["living_air_conditioner"]
        self.assertEqual("already_configured", ac["status"])
        self.assertTrue(ac["configured"])
        self.assertEqual("air_conditioner", ac["configured_type"])
        guest = by_name["climate.guest_ac"]
        self.assertEqual("available", guest["status"])
        self.assertFalse(guest["configured"])
        self.assertEqual("", guest["room_id"])
        self.assertEqual(["air_conditioner"], guest["suggested_types"])

    def test_multi_endpoint_device_is_not_duplicated_as_unbound(self) -> None:
        from custom_components.hausman_hub.application.climate_setup import (
            climate_device_candidates,
        )

        registry, contours, _ = _setup()
        bound_registry, observation = _native_observation(registry, contours)
        catalog = _catalog(
            [
                _entry("climate.living_air_conditioner"),
                _entry(
                    "sensor.living_temperature_observation",
                    state="25.8",
                    device_class="temperature",
                ),
                _entry(
                    "sensor.living_humidity_observation",
                    state="44.0",
                    device_class="humidity",
                ),
            ]
        )
        snapshot = build_native_climate_setup_snapshot(
            bound_registry,
            observation,
            catalog,
        )

        result = climate_device_candidates(bound_registry, snapshot)
        unbound = [
            candidate
            for candidate in result["candidates"]
            if not candidate["configured"]
        ]

        self.assertEqual([], unbound)
        self.assertEqual(
            3, len(snapshot.devices)
        )


class NativeReimportPreservationTest(unittest.TestCase):
    """Re-saving a contour must never strip native HA bindings (review 36f2)."""

    def test_second_import_preserves_bound_device_endpoints(self) -> None:
        from custom_components.hausman_hub.application.climate_registry_import import (
            import_managed_climate_selection,
        )

        registry, contours, _ = _setup()
        bound_registry, observation = _native_observation(registry, contours)
        catalog = _bound_catalog()
        snapshot = build_native_climate_setup_snapshot(
            bound_registry,
            observation,
            catalog,
        )
        first = import_managed_climate_selection(
            snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
        )
        second = import_managed_climate_selection(
            snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
        )

        ac = second.device("living_air_conditioner")
        self.assertEqual(1, len(ac.endpoints))
        self.assertEqual("control", ac.endpoints[0].role.value)
        self.assertEqual(
            "climate.living_air_conditioner", ac.endpoints[0].entity_id
        )
        self.assertEqual(first, second)

    def test_overrides_are_rejected_for_bound_candidates(self) -> None:
        from custom_components.hausman_hub.application.climate_registry_import import (
            ClimateRegistryImportViolation,
            add_import_candidate_to_registry,
        )
        from custom_components.hausman_hub.domain.climate import ClimateRegistry

        registry, contours, _ = _setup()
        bound_registry, observation = _native_observation(registry, contours)
        snapshot = build_native_climate_setup_snapshot(
            bound_registry,
            observation,
            _bound_catalog(),
        )

        with self.assertRaises(ClimateRegistryImportViolation):
            add_import_candidate_to_registry(
                ClimateRegistry(rooms=bound_registry.rooms),
                snapshot,
                source_id="synthetic-ac-source-living",
                device_id="other_ac",
                device_name="Other AC",
                kind="air_conditioner",
                control_scope="managed",
                control_owner="climate_core",
                room_id_override="living",
            )
        with self.assertRaises(ClimateRegistryImportViolation):
            add_import_candidate_to_registry(
                ClimateRegistry(rooms=bound_registry.rooms),
                snapshot,
                source_id="synthetic-ac-source-living",
                device_id="other_ac",
                device_name="Other AC",
                kind="air_conditioner",
                control_scope="managed",
                control_owner="climate_core",
                registry_source_id="attacker-chosen-id",
            )

    def test_native_candidate_receives_derived_private_source_id(self) -> None:
        from custom_components.hausman_hub.application.climate_registry_import import (
            add_import_candidate_to_registry,
        )
        from custom_components.hausman_hub.domain.climate import (
            ClimateRegistry,
            ClimateRoom,
        )

        registry, contours, _ = _setup()
        _, observation = _native_observation(registry, contours)
        catalog = _catalog(
            [_entry("climate.guest_ac", supported_features=137)]
        )
        snapshot = build_native_climate_setup_snapshot(
            ClimateRegistry(rooms=(ClimateRoom("guest", "Guest"),)),
            observation,
            catalog,
        )

        result = add_import_candidate_to_registry(
            ClimateRegistry(rooms=(ClimateRoom("guest", "Guest"),)),
            snapshot,
            source_id="climate.guest_ac",
            device_id="guest_ac",
            device_name="Guest AC",
            kind="air_conditioner",
            control_scope="managed",
            control_owner="climate_core",
            room_id_override="guest",
        )

        device = result.devices[0]
        self.assertEqual(
            "hausmanhub-native-climate.guest_ac", device.source_id
        )
        self.assertEqual("guest", device.room_id)
        self.assertEqual("climate.guest_ac", device.endpoints[0].entity_id)
