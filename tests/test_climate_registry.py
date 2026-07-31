"""Pure tests for HausmanHub logical climate rooms and devices."""

from __future__ import annotations

import unittest

from custom_components.hausman_hub.application.climate_registry import (
    ClimateRegistryViolation,
    migrate_climate_registry_payload,
    registry_from_payload,
    registry_to_payload,
)
from custom_components.hausman_hub.domain.climate import (
    ClimateCapability,
    ClimateControlChannel,
    ClimateControlOwner,
    ClimateControlScope,
    ClimateDevice,
    ClimateDeviceKind,
    ClimateEndpoint,
    ClimateEndpointRole,
    ClimateHomeEnvironment,
    ClimateModelViolation,
    ClimateRegistry,
    ClimateRoom,
)


def air_conditioner(**changes: object) -> ClimateDevice:
    """Build one safe synthetic AC registration."""

    values: dict[str, object] = {
        "device_id": "living_ac",
        "name": "Living AC",
        "room_id": "living",
        "kind": ClimateDeviceKind.AIR_CONDITIONER,
        "source_id": "synthetic-climate-source-living",
        "control_scope": ClimateControlScope.CANARY,
        "control_owner": ClimateControlOwner.CLIMATE_CORE,
        "capabilities": (
            ClimateCapability.POWER,
            ClimateCapability.TARGET_TEMPERATURE,
            ClimateCapability.HVAC_MODE,
            ClimateCapability.FAN_MODE,
            ClimateCapability.PHYSICAL_FEEDBACK,
        ),
        "endpoints": (
            ClimateEndpoint(
                ClimateEndpointRole.CONTROL,
                "climate.synthetic_living_ac",
            ),
            ClimateEndpoint(
                ClimateEndpointRole.PHYSICAL_FEEDBACK,
                "binary_sensor.synthetic_living_ac_flap",
            ),
        ),
    }
    values.update(changes)
    return ClimateDevice(**values)  # type: ignore[arg-type]


class ClimateRegistryTest(unittest.TestCase):
    """Keep configuration typed, explicit, and free of generic proxy fields."""

    def test_registry_groups_private_endpoints_under_one_logical_device(self) -> None:
        registry = ClimateRegistry(
            rooms=(ClimateRoom("living", "Living room"),),
            devices=(air_conditioner(),),
        )

        device = registry.device("living_ac")

        self.assertIsNotNone(device)
        assert device is not None
        self.assertTrue(device.supports(ClimateCapability.TARGET_TEMPERATURE))
        self.assertEqual(
            "binary_sensor.synthetic_living_ac_flap",
            device.endpoint(ClimateEndpointRole.PHYSICAL_FEEDBACK).entity_id,
        )

    def test_every_climate_kind_has_an_explicit_minimum_contract(self) -> None:
        devices = (
            air_conditioner(),
            air_conditioner(
                device_id="bedroom_trv",
                name="Bedroom TRV",
                room_id="bedroom",
                kind=ClimateDeviceKind.RADIATOR_THERMOSTAT,
                source_id="synthetic-trv-source-bedroom",
                capabilities=(ClimateCapability.TARGET_TEMPERATURE,),
                endpoints=(
                    ClimateEndpoint(
                        ClimateEndpointRole.CONTROL,
                        "climate.synthetic_bedroom_trv",
                    ),
                ),
            ),
            air_conditioner(
                device_id="kids_humidifier",
                name="Kids humidifier",
                room_id="kids",
                kind=ClimateDeviceKind.HUMIDIFIER,
                source_id="synthetic-humidifier-source-kids",
                capabilities=(
                    ClimateCapability.POWER,
                    ClimateCapability.TARGET_HUMIDITY,
                ),
                endpoints=(
                    ClimateEndpoint(
                        ClimateEndpointRole.CONTROL,
                        "humidifier.synthetic_kids",
                    ),
                ),
            ),
            air_conditioner(
                device_id="bathroom_floor",
                name="Bathroom floor",
                room_id="bathroom",
                kind=ClimateDeviceKind.FLOOR_HEATING,
                source_id="synthetic-floor-source-bathroom",
                capabilities=(
                    ClimateCapability.POWER,
                    ClimateCapability.TARGET_TEMPERATURE,
                ),
                endpoints=(
                    ClimateEndpoint(
                        ClimateEndpointRole.CONTROL,
                        "climate.synthetic_bathroom_floor",
                    ),
                ),
            ),
        )

        registry = ClimateRegistry(
            rooms=(
                ClimateRoom("living", "Living room"),
                ClimateRoom("bedroom", "Bedroom"),
                ClimateRoom("kids", "Kids"),
                ClimateRoom("bathroom", "Bathroom"),
            ),
            devices=devices,
        )

        self.assertEqual(4, len(registry.devices))

    def test_passive_sensor_cannot_gain_a_control_surface(self) -> None:
        with self.assertRaisesRegex(ClimateModelViolation, "must not have"):
            air_conditioner(
                device_id="living_temperature",
                name="Living temperature",
                kind=ClimateDeviceKind.TEMPERATURE_SENSOR,
                source_id="synthetic-temperature-source",
                capabilities=(),
                control_scope=ClimateControlScope.OBSERVED,
                control_owner=ClimateControlOwner.OBSERVED,
                endpoints=(
                    ClimateEndpoint(
                        ClimateEndpointRole.CONTROL,
                        "sensor.synthetic_living_temperature",
                    ),
                ),
            )

    def test_observed_device_cannot_hide_an_active_owner(self) -> None:
        with self.assertRaisesRegex(ClimateModelViolation, "observed ownership"):
            air_conditioner(
                control_scope=ClimateControlScope.OBSERVED,
                control_owner=ClimateControlOwner.CLIMATE_CORE,
            )

    def test_observed_device_cannot_have_a_control_channel(self) -> None:
        with self.assertRaisesRegex(ClimateModelViolation, "managed scope"):
            air_conditioner(
                control_scope=ClimateControlScope.OBSERVED,
                control_owner=ClimateControlOwner.OBSERVED,
                control_channel=ClimateControlChannel.UNIVERSAL_IR,
            )

    def test_registry_rejects_duplicate_sources_and_unknown_rooms(self) -> None:
        with self.assertRaisesRegex(ClimateModelViolation, "source ids"):
            ClimateRegistry(
                rooms=(ClimateRoom("living", "Living room"),),
                devices=(
                    air_conditioner(),
                    air_conditioner(device_id="second_ac", name="Second AC"),
                ),
            )

        with self.assertRaisesRegex(ClimateModelViolation, "unknown rooms"):
            ClimateRegistry(
                rooms=(ClimateRoom("living", "Living room"),),
                devices=(air_conditioner(room_id="missing"),),
            )

    def test_required_capabilities_and_endpoint_shape_fail_closed(self) -> None:
        with self.assertRaisesRegex(ClimateModelViolation, "required capabilities"):
            air_conditioner(capabilities=(ClimateCapability.POWER,))

        with self.assertRaisesRegex(ClimateModelViolation, "one control endpoint"):
            air_conditioner(endpoints=())

        with self.assertRaisesRegex(ClimateModelViolation, "Home Assistant entity"):
            ClimateEndpoint(ClimateEndpointRole.CONTROL, "not an entity")

    def test_room_window_binding_stays_optional_and_domain_strict(self) -> None:
        room = ClimateRoom(
            "living",
            "Living room",
            window_entity_id="binary_sensor.living_window",
        )
        self.assertEqual("binary_sensor.living_window", room.window_entity_id)
        self.assertIsNone(ClimateRoom("kids", "Kids").window_entity_id)

        with self.assertRaisesRegex(ClimateModelViolation, "window entity"):
            ClimateRoom("living", "Living room", window_entity_id="sensor.temperature")

        with self.assertRaisesRegex(ClimateModelViolation, "window entity"):
            ClimateRoom("living", "Living room", window_entity_id="not an entity")

    def test_room_accepts_multiple_unique_binary_presence_sensors(self) -> None:
        room = ClimateRoom(
            "living",
            "Living room",
            presence_entity_ids=(
                "binary_sensor.living_motion",
                "binary_sensor.living_occupancy",
            ),
        )
        self.assertEqual(
            (
                "binary_sensor.living_motion",
                "binary_sensor.living_occupancy",
            ),
            room.presence_entity_ids,
        )
        with self.assertRaisesRegex(ClimateModelViolation, "immutable"):
            ClimateRoom(
                "living",
                "Living room",
                presence_entity_ids=["binary_sensor.living_motion"],  # type: ignore[arg-type]
            )
        with self.assertRaisesRegex(ClimateModelViolation, "must be unique"):
            ClimateRoom(
                "living",
                "Living room",
                presence_entity_ids=(
                    "binary_sensor.living_motion",
                    "binary_sensor.living_motion",
                ),
            )
        with self.assertRaisesRegex(ClimateModelViolation, "room presence entity"):
            ClimateRoom(
                "living",
                "Living room",
                presence_entity_ids=("person.ivan",),
            )

    def test_one_room_presence_sensor_cannot_belong_to_two_rooms(self) -> None:
        with self.assertRaisesRegex(
            ClimateModelViolation,
            "room presence entities across rooms",
        ):
            ClimateRegistry(
                rooms=(
                    ClimateRoom(
                        "living",
                        "Living room",
                        presence_entity_ids=("binary_sensor.shared_motion",),
                    ),
                    ClimateRoom(
                        "kids",
                        "Kids",
                        presence_entity_ids=("binary_sensor.shared_motion",),
                    ),
                )
            )

    def test_home_environment_bindings_stay_optional_and_domain_strict(self) -> None:
        home = ClimateHomeEnvironment(
            outdoor_temperature_entity_id="weather.home",
            presence_entity_id="person.ivan",
            central_heating_entity_id="switch.central_heating",
        )
        registry = ClimateRegistry(home=home)

        self.assertIs(registry.home, home)
        self.assertEqual("weather.home", registry.home.outdoor_temperature_entity_id)
        self.assertEqual(
            ClimateHomeEnvironment(),
            ClimateRegistry().home,
        )
        prioritized = ClimateHomeEnvironment(
            outdoor_temperature_entity_id="sensor.outdoor_temperature",
            outdoor_temperature_entity_ids=(
                "sensor.outdoor_temperature",
                "weather.home",
            ),
        )
        self.assertEqual(
            ("sensor.outdoor_temperature", "weather.home"),
            prioritized.prioritized_outdoor_temperature_entity_ids,
        )

        with self.assertRaisesRegex(ClimateModelViolation, "outdoor temperature"):
            ClimateHomeEnvironment(
                outdoor_temperature_entity_id="binary_sensor.outdoor_temperature"
            )

        with self.assertRaisesRegex(ClimateModelViolation, "presence entity"):
            ClimateHomeEnvironment(presence_entity_id="sensor.presence")

        with self.assertRaisesRegex(ClimateModelViolation, "central heating entity"):
            ClimateHomeEnvironment(central_heating_entity_id="climate.heating")

        with self.assertRaisesRegex(ClimateModelViolation, "presence entity"):
            ClimateHomeEnvironment(presence_entity_id="not an entity")

    def test_passive_sensor_observation_endpoint_matches_its_kind(self) -> None:
        sensor = air_conditioner(
            device_id="living_temperature",
            name="Living temperature",
            kind=ClimateDeviceKind.TEMPERATURE_SENSOR,
            source_id="synthetic-temperature-source",
            capabilities=(),
            control_scope=ClimateControlScope.OBSERVED,
            control_owner=ClimateControlOwner.OBSERVED,
            endpoints=(
                ClimateEndpoint(
                    ClimateEndpointRole.TEMPERATURE,
                    "sensor.synthetic_living_temperature",
                ),
            ),
        )
        self.assertEqual(
            "sensor.synthetic_living_temperature",
            sensor.endpoint(ClimateEndpointRole.TEMPERATURE).entity_id,
        )

        with self.assertRaisesRegex(ClimateModelViolation, "must match its kind"):
            air_conditioner(
                device_id="living_temperature",
                name="Living temperature",
                kind=ClimateDeviceKind.TEMPERATURE_SENSOR,
                source_id="synthetic-temperature-source",
                capabilities=(),
                control_scope=ClimateControlScope.OBSERVED,
                control_owner=ClimateControlOwner.OBSERVED,
                endpoints=(
                    ClimateEndpoint(
                        ClimateEndpointRole.HUMIDITY,
                        "sensor.synthetic_living_humidity",
                    ),
                ),
            )

        with self.assertRaisesRegex(ClimateModelViolation, "observation entity"):
            air_conditioner(
                device_id="living_temperature",
                name="Living temperature",
                kind=ClimateDeviceKind.TEMPERATURE_SENSOR,
                source_id="synthetic-temperature-source",
                capabilities=(),
                control_scope=ClimateControlScope.OBSERVED,
                control_owner=ClimateControlOwner.OBSERVED,
                endpoints=(
                    ClimateEndpoint(
                        ClimateEndpointRole.TEMPERATURE,
                        "climate.synthetic_living_temperature",
                    ),
                ),
            )


class ClimateRegistryPayloadTest(unittest.TestCase):
    """Keep the stored shape exact and the legacy migration fail-closed."""

    def test_version_three_payload_round_trips_native_bindings(self) -> None:
        registry = ClimateRegistry(
            rooms=(
                ClimateRoom(
                    "living",
                    "Living room",
                    window_entity_id="binary_sensor.living_window",
                    presence_entity_ids=(
                        "binary_sensor.living_motion",
                        "binary_sensor.living_occupancy",
                    ),
                ),
            ),
            devices=(
                air_conditioner(),
                air_conditioner(
                    device_id="living_temperature",
                    name="Living temperature",
                    kind=ClimateDeviceKind.TEMPERATURE_SENSOR,
                    source_id="synthetic-temperature-source",
                    capabilities=(),
                    control_scope=ClimateControlScope.OBSERVED,
                    control_owner=ClimateControlOwner.OBSERVED,
                    endpoints=(
                        ClimateEndpoint(
                            ClimateEndpointRole.TEMPERATURE,
                            "sensor.synthetic_living_temperature",
                        ),
                    ),
                ),
            ),
            home=ClimateHomeEnvironment(
                outdoor_temperature_entity_id="sensor.outdoor_temperature",
                presence_entity_id="binary_sensor.home_occupied",
                central_heating_entity_id="input_boolean.central_heating",
            ),
        )

        restored = registry_from_payload(registry_to_payload(registry))

        self.assertEqual(registry, restored)

    def test_managed_control_channels_round_trip(self) -> None:
        for channel in ClimateControlChannel:
            with self.subTest(channel=channel):
                registry = ClimateRegistry(
                    rooms=(ClimateRoom("living", "Living room"),),
                    devices=(
                        air_conditioner(
                            control_scope=ClimateControlScope.MANAGED,
                            control_channel=channel,
                        ),
                    ),
                )

                payload = registry_to_payload(registry)
                restored = registry_from_payload(payload)

                self.assertEqual(channel.value, payload["devices"][0]["control_channel"])  # type: ignore[index]
                self.assertEqual(channel, restored.devices[0].control_channel)

    def test_absent_bindings_round_trip_as_absent(self) -> None:
        registry = ClimateRegistry(
            rooms=(ClimateRoom("living", "Living room"),),
            devices=(air_conditioner(),),
        )

        payload = registry_to_payload(registry)

        self.assertEqual(
            {
                "outdoor_temperature_entity_id": None,
                "presence_entity_id": None,
                "central_heating_entity_id": None,
            },
            payload["home"],
        )
        self.assertIsNone(payload["rooms"][0]["window_entity_id"])  # type: ignore[index]
        self.assertNotIn("presence_entity_ids", payload["rooms"][0])  # type: ignore[operator,index]
        self.assertEqual(registry, registry_from_payload(payload))

    def test_payload_rejects_unknown_fields_and_wrong_version(self) -> None:
        payload = registry_to_payload(ClimateRegistry())

        with self.assertRaisesRegex(ClimateRegistryViolation, "fixed fields"):
            registry_from_payload({**payload, "extra": True})

        with self.assertRaisesRegex(ClimateRegistryViolation, "unsupported"):
            registry_from_payload({**payload, "version": 1})

        with self.assertRaisesRegex(ClimateRegistryViolation, "fixed fields"):
            registry_from_payload(
                {**payload, "rooms": [{"id": "living", "name": "Living room"}]}
            )

    def test_legacy_version_one_payload_migrates_with_absent_bindings(self) -> None:
        legacy = {
            "version": 1,
            "rooms": [{"id": "living", "name": "Living room"}],
            "devices": [
                {
                    "id": "living_ac",
                    "name": "Living AC",
                    "room_id": "living",
                    "kind": "air_conditioner",
                    "source_id": "synthetic-ac-source-living",
                    "control_scope": "canary",
                    "control_owner": "climate_core",
                    "capabilities": ["power", "target_temperature"],
                    "endpoints": [
                        {
                            "role": "control",
                            "entity_id": "climate.synthetic_living_ac",
                        }
                    ],
                }
            ],
        }

        migrated = migrate_climate_registry_payload(1, legacy)
        restored = registry_from_payload(migrated)

        self.assertEqual(3, migrated["version"])
        self.assertEqual(
            ClimateHomeEnvironment(),
            restored.home,
        )
        self.assertIsNone(restored.rooms[0].window_entity_id)
        self.assertEqual((), restored.rooms[0].presence_entity_ids)
        self.assertEqual("living_ac", restored.devices[0].device_id)
        self.assertIsNone(restored.devices[0].control_channel)

    def test_version_two_payload_migrates_with_an_absent_control_channel(self) -> None:
        registry = ClimateRegistry(
            rooms=(ClimateRoom("living", "Living room"),),
            devices=(air_conditioner(),),
        )
        version_two = registry_to_payload(registry)
        version_two["version"] = 2
        version_two["devices"][0].pop("control_channel")  # type: ignore[index]

        migrated = migrate_climate_registry_payload(2, version_two)
        restored = registry_from_payload(migrated)

        self.assertEqual(3, migrated["version"])
        self.assertIsNone(restored.devices[0].control_channel)
        self.assertIsNone(migrated["devices"][0]["control_channel"])  # type: ignore[index]

    def test_migration_rejects_unknown_storage_version_and_shape(self) -> None:
        with self.assertRaisesRegex(ClimateRegistryViolation, "unsupported"):
            migrate_climate_registry_payload(0, {})

        with self.assertRaisesRegex(ClimateRegistryViolation, "unsupported"):
            migrate_climate_registry_payload(4, {})

        with self.assertRaisesRegex(ClimateRegistryViolation, "does not match"):
            migrate_climate_registry_payload(
                1,
                {"version": 2, "rooms": [], "devices": []},
            )

        with self.assertRaisesRegex(ClimateRegistryViolation, "fixed fields"):
            migrate_climate_registry_payload(
                1,
                {"version": 1, "rooms": [], "devices": [], "home": {}},
            )

    def test_current_version_migration_is_an_exact_round_trip(self) -> None:
        registry = ClimateRegistry(
            rooms=(ClimateRoom("living", "Living room"),),
            devices=(air_conditioner(),),
        )
        payload = registry_to_payload(registry)

        self.assertEqual(payload, migrate_climate_registry_payload(3, payload))


if __name__ == "__main__":
    unittest.main()
