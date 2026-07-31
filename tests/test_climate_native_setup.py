"""Contract tests for the native setup discovery foundation (36f1)."""

from __future__ import annotations

import hashlib
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from custom_components.hausman_hub.application.climate_native_setup import (
    ClimateHaCatalogEntry,
    ClimateHaCatalogRoom,
    ClimateHaEntityCatalog,
    ClimateNativeSetupViolation,
    build_native_climate_setup_snapshot,
)
from custom_components.hausman_hub.domain.climate import (
    ClimateDeviceKind,
    ClimateRegistry,
)
from custom_components.hausman_hub.domain.climate_observation import (
    ClimateDataStatus,
)
from tests.test_climate_native_projections import _native_observation, _setup

GENERATED_AT = 1784280000000


def _catalog(
    entries: list[ClimateHaCatalogEntry],
    rooms: list[ClimateHaCatalogRoom] | None = None,
) -> ClimateHaEntityCatalog:
    return ClimateHaEntityCatalog(
        entries=tuple(entries),
        rooms=tuple(rooms or ()),
    )


def _entry(
    entity_id: str,
    *,
    state: str = "cool",
    device_class: str | None = None,
    supported_features: int = 0,
    friendly_name: str | None = None,
    available: bool = True,
    room_id: str = "",
    device_group_id: str | None = None,
    device_name: str | None = None,
    manufacturer: str | None = None,
    model: str | None = None,
    image_url: str | None = None,
    hvac_modes: tuple[str, ...] = (),
    entity_category: str | None = None,
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
        room_id=room_id,
        device_group_id=device_group_id,
        device_name=device_name,
        manufacturer=manufacturer,
        model=model,
        image_url=image_url,
        hvac_modes=hvac_modes,
        entity_category=entity_category,
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

    def test_unbound_climate_kinds_use_hvac_modes_then_trv_markers(self) -> None:
        registry, contours, _ = _setup()
        _, observation = _native_observation(registry, contours)
        catalog = _catalog(
            [
                _entry(
                    "climate.sonoff_trvzb",
                    supported_features=1,
                    model="Zigbee thermostatic radiator valve",
                    hvac_modes=("off", "auto", "heat"),
                ),
                _entry(
                    "climate.office_ac",
                    hvac_modes=("off", "cool"),
                ),
                _entry(
                    "climate.air_purifier",
                    hvac_modes=("fan_only", "off"),
                ),
                _entry(
                    "climate.marker_trv",
                    friendly_name="Термоголовка спальни",
                ),
                _entry(
                    "climate.legacy_facade",
                    friendly_name="Климат гостиной",
                ),
                _entry(
                    "climate.bath_floor",
                    friendly_name="Тёплый пол ванной",
                    hvac_modes=("off", "heat"),
                ),
                _entry(
                    "climate.generic_heater",
                    friendly_name="Термостат",
                    hvac_modes=("off", "heat"),
                ),
            ]
        )

        snapshot = build_native_climate_setup_snapshot(
            ClimateRegistry(),
            observation,
            catalog,
        )

        self.assertEqual(
            (ClimateDeviceKind.RADIATOR_THERMOSTAT,),
            snapshot.device("climate.sonoff_trvzb").suggested_kinds,
        )
        self.assertEqual(
            (ClimateDeviceKind.AIR_CONDITIONER,),
            snapshot.device("climate.office_ac").suggested_kinds,
        )
        self.assertEqual(
            (),
            snapshot.device("climate.air_purifier").suggested_kinds,
        )
        self.assertEqual(
            (ClimateDeviceKind.RADIATOR_THERMOSTAT,),
            snapshot.device("climate.marker_trv").suggested_kinds,
        )
        self.assertEqual(
            (),
            snapshot.device("climate.legacy_facade").suggested_kinds,
        )
        self.assertEqual(
            (ClimateDeviceKind.FLOOR_HEATING,),
            snapshot.device("climate.bath_floor").suggested_kinds,
        )
        self.assertEqual(
            (),
            snapshot.device("climate.generic_heater").suggested_kinds,
        )

    def test_uninformative_hvac_modes_still_fall_back_to_trv_markers(self) -> None:
        registry, contours, _ = _setup()
        _, observation = _native_observation(registry, contours)
        catalog = _catalog(
            [
                _entry(
                    "climate.named_trv",
                    friendly_name="Термоголовка ванной",
                    hvac_modes=("off", "auto"),
                ),
                _entry(
                    "climate.heat_cool_combo",
                    hvac_modes=("heat", "cool"),
                ),
                _entry(
                    "climate.unknown_box",
                    hvac_modes=("off", "auto"),
                ),
            ]
        )

        snapshot = build_native_climate_setup_snapshot(
            ClimateRegistry(),
            observation,
            catalog,
        )

        self.assertEqual(
            (ClimateDeviceKind.RADIATOR_THERMOSTAT,),
            snapshot.device("climate.named_trv").suggested_kinds,
        )
        self.assertEqual(
            (ClimateDeviceKind.AIR_CONDITIONER,),
            snapshot.device("climate.heat_cool_combo").suggested_kinds,
        )
        self.assertEqual(
            (),
            snapshot.device("climate.unknown_box").suggested_kinds,
        )

    def test_missing_hvac_modes_recognize_all_trv_markers(self) -> None:
        registry, contours, _ = _setup()
        _, observation = _native_observation(registry, contours)
        marker_entries = [
            _entry("climate.russian_head", friendly_name="Термоголовка"),
            _entry("climate.russian_regulator", device_name="Терморегулятор"),
            _entry(
                "climate.radiator_valve",
                model="Thermostatic Radiator Valve",
            ),
            _entry("climate.sonoff", model="TRVZB"),
            _entry("climate.token", device_name="Bedroom TRV"),
            _entry("climate.not_token", device_name="TRVending gateway"),
        ]

        snapshot = build_native_climate_setup_snapshot(
            ClimateRegistry(),
            observation,
            _catalog(marker_entries),
        )

        for entity_id in (
            "climate.russian_head",
            "climate.russian_regulator",
            "climate.radiator_valve",
            "climate.sonoff",
            "climate.token",
        ):
            with self.subTest(entity_id=entity_id):
                self.assertEqual(
                    (ClimateDeviceKind.RADIATOR_THERMOSTAT,),
                    snapshot.device(entity_id).suggested_kinds,
                )
        self.assertEqual(
            (),
            snapshot.device("climate.not_token").suggested_kinds,
        )

    def test_configuration_entities_never_become_device_choices(self) -> None:
        registry, contours, _ = _setup()
        _, observation = _native_observation(registry, contours)
        snapshot = build_native_climate_setup_snapshot(
            ClimateRegistry(),
            observation,
            _catalog(
                [
                    _entry(
                        "climate.calibration",
                        friendly_name="Кондиционер: калибровка",
                        hvac_modes=("cool", "off"),
                        entity_category="config",
                    ),
                    _entry(
                        "sensor.temperature_offset",
                        device_class="temperature",
                        friendly_name="Поправка температуры",
                        entity_category="config",
                    ),
                ]
            ),
        )

        self.assertEqual(
            (),
            snapshot.device("climate.calibration").suggested_kinds,
        )
        self.assertEqual(
            (),
            snapshot.device("sensor.temperature_offset").suggested_kinds,
        )

    def test_trv_candidate_creates_a_ready_draft(self) -> None:
        from custom_components.hausman_hub.application.climate_setup import (
            create_climate_contour_draft,
            climate_setup_options,
            validate_climate_contour_draft,
        )

        registry, contours, _ = _setup()
        _, observation = _native_observation(registry, contours)
        snapshot = build_native_climate_setup_snapshot(
            ClimateRegistry(),
            observation,
            _catalog(
                [
                    _entry(
                        "climate.sonoff_trvzb",
                        supported_features=1,
                        model="Zigbee thermostatic radiator valve",
                        hvac_modes=("off", "auto", "heat"),
                        room_id="living",
                    )
                ],
                [ClimateHaCatalogRoom("living", "Гостиная")],
            ),
        )
        options = climate_setup_options(ClimateRegistry(), snapshot)
        trv = options["devices"][0]

        draft = create_climate_contour_draft(
            ClimateRegistry(),
            snapshot,
            {
                "snapshot_revision": options["snapshot_revision"],
                "name": "Климат",
                "mode": "automatic",
                "rooms": [
                    {
                        "room_id": "living",
                        "target_temperature": 22.0,
                        "target_humidity": 45,
                        "strategy": "normal",
                        "devices": [
                            {
                                "candidate_id": trv["candidate_id"],
                                "type": "radiator_thermostat",
                            }
                        ],
                    }
                ],
            },
        )
        validation = validate_climate_contour_draft(
            ClimateRegistry(),
            snapshot,
            draft,
        )

        self.assertEqual(["radiator_thermostat"], trv["suggested_types"])
        self.assertTrue(validation["save_allowed"])

    def test_ha_areas_bootstrap_an_empty_registry_and_enable_page_drafts(self) -> None:
        from custom_components.hausman_hub.application.climate_setup import (
            climate_setup_options,
        )

        registry, contours, _ = _setup()
        _, observation = _native_observation(registry, contours)
        catalog = _catalog(
            [
                _entry(
                    "climate.living_ac",
                    supported_features=129,
                    friendly_name="Кондиционер",
                    room_id="living",
                ),
                _entry(
                    "sensor.kids_temperature",
                    state="21.5",
                    device_class="temperature",
                    friendly_name="Температура детской",
                    room_id="kids",
                ),
            ],
            [
                ClimateHaCatalogRoom("living", "Гостиная"),
                ClimateHaCatalogRoom("kids", "Детская"),
            ],
        )

        snapshot = build_native_climate_setup_snapshot(
            ClimateRegistry(),
            observation,
            catalog,
        )
        options = climate_setup_options(ClimateRegistry(), snapshot)

        self.assertEqual(
            [("living", "Гостиная"), ("kids", "Детская")],
            [(room.room_id, room.name) for room in snapshot.rooms],
        )
        self.assertEqual("living", snapshot.device("climate.living_ac").room_id)
        self.assertEqual(
            "kids",
            snapshot.device("sensor.kids_temperature").room_id,
        )
        self.assertTrue(options["draft_creation_allowed"])
        self.assertEqual(2, len(options["rooms"]))
        self.assertEqual(2, sum(device["can_add"] is True for device in options["devices"]))
        self.assertEqual(
            ["universal_ir", "yandex_remote", "direct_wifi"],
            options["control_channels"],
        )

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
            _catalog([_entry("climate.guest_ac", room_id="missing")])
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

    def test_catalog_accepts_intflag_supported_features(self) -> None:
        """Real HA stores climate supported_features as IntFlag, not plain int."""
        from enum import IntFlag

        from custom_components.hausman_hub.climate_ha_state_view import (
            HomeAssistantClimateStateView,
        )

        class _ClimateFeature(IntFlag):
            TARGET_TEMPERATURE = 1
            FAN_MODE = 8
            TURN_OFF = 128
            TURN_ON = 256

        hass = _FakeHass(
            [
                _FakeState(
                    "climate.living_ac",
                    "off",
                    {
                        "friendly_name": "Living AC",
                        "supported_features": _ClimateFeature(393),
                    },
                ),
            ]
        )
        view = HomeAssistantClimateStateView(hass)  # type: ignore[arg-type]

        catalog = view.entity_catalog()

        self.assertEqual(393, catalog.entries[0].supported_features)
        self.assertIs(int, type(catalog.entries[0].supported_features))

    def test_catalog_accepts_only_bounded_hvac_mode_lists(self) -> None:
        from custom_components.hausman_hub.climate_ha_state_view import (
            HomeAssistantClimateStateView,
        )

        hass = _FakeHass(
            [
                _FakeState(
                    "climate.valid",
                    "cool",
                    {"hvac_modes": ["cool", "off"]},
                ),
                _FakeState(
                    "climate.not_a_sequence",
                    "off",
                    {"hvac_modes": "cool"},
                ),
                _FakeState(
                    "climate.too_many",
                    "off",
                    {"hvac_modes": ["off"] * 17},
                ),
                _FakeState(
                    "climate.invalid_member",
                    "off",
                    {"hvac_modes": ["off", 1]},
                ),
                _FakeState(
                    "climate.too_long",
                    "off",
                    {"hvac_modes": ["x" * 33]},
                ),
            ]
        )

        catalog = HomeAssistantClimateStateView(hass).entity_catalog()  # type: ignore[arg-type]
        by_id = {entry.entity_id: entry for entry in catalog.entries}

        self.assertEqual(("cool", "off"), by_id["climate.valid"].hvac_modes)
        for entity_id in (
            "climate.not_a_sequence",
            "climate.too_many",
            "climate.invalid_member",
            "climate.too_long",
        ):
            with self.subTest(entity_id=entity_id):
                self.assertEqual((), by_id[entity_id].hvac_modes)

    def test_signal_catalog_filters_by_purpose_and_preserves_device_class(self) -> None:
        from custom_components.hausman_hub.climate_ha_state_view import (
            HomeAssistantClimateStateView,
        )
        from custom_components.hausman_hub.application.climate_signal_settings import (
            ROOM_PRESENCE_SIGNAL,
            WINDOW_SIGNAL,
        )

        hass = _FakeHass(
            [
                _FakeState(
                    "binary_sensor.living_motion",
                    "off",
                    {
                        "device_class": "motion",
                        "friendly_name": "Движение гостиной",
                    },
                ),
                _FakeState(
                    "binary_sensor.living_window",
                    "off",
                    {"device_class": "window"},
                ),
            ]
        )

        view = HomeAssistantClimateStateView(  # type: ignore[arg-type]
            hass
        )
        presence = view.signal_entity_catalog(ROOM_PRESENCE_SIGNAL)
        windows = view.signal_entity_catalog(WINDOW_SIGNAL)
        by_id = {entry.entity_id: entry for entry in presence.entries}

        self.assertEqual("motion", by_id["binary_sensor.living_motion"].device_class)
        self.assertNotIn("binary_sensor.living_window", by_id)
        self.assertEqual(
            ["binary_sensor.living_window"],
            [entry.entity_id for entry in windows.entries],
        )

    def test_ir_remote_catalog_filters_domains_and_binds_rooms(self) -> None:
        from custom_components.hausman_hub.climate_ha_state_view import (
            HomeAssistantClimateStateView,
        )

        hass = _FakeHass(
            [
                _FakeState(
                    "remote.living_broadlink",
                    "on",
                    {"friendly_name": "Пульт гостиной"},
                ),
                _FakeState("remote.roomless", "unavailable", {}),
                _FakeState("climate.living_ac", "cool", {}),
                _FakeState("remote.oversized", "x" * 65, {}),
            ]
        )
        hass.area_registry = SimpleNamespace(
            async_list_areas=lambda: [
                SimpleNamespace(id="living", name="Гостиная"),
            ]
        )
        entity_entries = {
            "remote.living_broadlink": SimpleNamespace(
                area_id=None,
                device_id="device_living_remote",
            ),
        }
        hass.entity_registry = SimpleNamespace(
            async_get=lambda entity_id: entity_entries.get(entity_id)
        )
        hass.device_registry = SimpleNamespace(
            async_get=lambda device_id: {
                "device_living_remote": SimpleNamespace(
                    area_id="living",
                    identifiers=set(),
                    name="Broadlink RM4",
                    manufacturer="Broadlink",
                    model="RM4 mini",
                    model_id=None,
                ),
            }.get(device_id)
        )

        homeassistant = ModuleType("homeassistant")
        helpers = ModuleType("homeassistant.helpers")
        area_module = ModuleType("homeassistant.helpers.area_registry")
        device_module = ModuleType("homeassistant.helpers.device_registry")
        entity_module = ModuleType("homeassistant.helpers.entity_registry")
        area_module.async_get = lambda value: value.area_registry  # type: ignore[attr-defined]
        device_module.async_get = lambda value: value.device_registry  # type: ignore[attr-defined]
        entity_module.async_get = lambda value: value.entity_registry  # type: ignore[attr-defined]
        homeassistant.helpers = helpers  # type: ignore[attr-defined]
        helpers.area_registry = area_module  # type: ignore[attr-defined]
        helpers.device_registry = device_module  # type: ignore[attr-defined]
        helpers.entity_registry = entity_module  # type: ignore[attr-defined]
        fake_modules = {
            "homeassistant": homeassistant,
            "homeassistant.helpers": helpers,
            "homeassistant.helpers.area_registry": area_module,
            "homeassistant.helpers.device_registry": device_module,
            "homeassistant.helpers.entity_registry": entity_module,
        }

        with patch.dict(sys.modules, fake_modules):
            catalog = HomeAssistantClimateStateView(  # type: ignore[arg-type]
                hass
            ).ir_remote_catalog()

        by_id = {entry.entity_id: entry for entry in catalog.entries}
        self.assertEqual(
            {"remote.living_broadlink", "remote.roomless"},
            set(by_id),
        )
        living = by_id["remote.living_broadlink"]
        self.assertEqual("remote", living.domain)
        self.assertEqual("Пульт гостиной", living.friendly_name)
        self.assertEqual("living", living.room_id)
        self.assertEqual("Broadlink RM4", living.device_name)
        self.assertTrue(living.available)
        self.assertIsNone(living.image_url)
        roomless = by_id["remote.roomless"]
        self.assertEqual("", roomless.room_id)
        self.assertFalse(roomless.available)

    def test_catalog_reads_ha_areas_and_inherits_device_assignment(self) -> None:
        from custom_components.hausman_hub.climate_ha_state_view import (
            HomeAssistantClimateStateView,
        )

        hass = _FakeHass(
            [
                _FakeState("climate.living_ac", "cool", {}),
                _FakeState(
                    "sensor.kids_temperature",
                    "21.5",
                    {"device_class": "temperature"},
                ),
                _FakeState(
                    "sensor.kids_humidity",
                    "45",
                    {"device_class": "humidity"},
                ),
                _FakeState(
                    "sensor.lock_device_temperature",
                    "38",
                    {"device_class": "temperature"},
                ),
                _FakeState(
                    "weather.home",
                    "sunny",
                    {"temperature": 6.5, "friendly_name": "Погода дома"},
                ),
                _FakeState("humidifier.mobile", "off", {}),
            ]
        )
        hass.area_registry = SimpleNamespace(
            async_list_areas=lambda: [
                SimpleNamespace(id="living", name="Гостиная"),
                SimpleNamespace(id="kids", name="Детская"),
            ]
        )
        entity_entries = {
            "climate.living_ac": SimpleNamespace(
                area_id="living",
                device_id="device_living",
            ),
            "sensor.kids_temperature": SimpleNamespace(
                area_id=None,
                device_id="device_kids",
            ),
            "sensor.kids_humidity": SimpleNamespace(
                area_id=None,
                device_id="device_kids",
            ),
            "sensor.lock_device_temperature": SimpleNamespace(
                area_id="living",
                device_id="device_living",
                entity_category="diagnostic",
            ),
        }
        hass.entity_registry = SimpleNamespace(
            async_get=lambda entity_id: entity_entries.get(entity_id)
        )
        hass.device_registry = SimpleNamespace(
            async_get=lambda device_id: {
                "device_living": SimpleNamespace(
                    area_id="kids",
                    identifiers=set(),
                ),
                "device_kids": SimpleNamespace(
                    area_id="kids",
                    identifiers={
                        ("mqtt", "zigbee2mqtt_0xa4c1389ecebec375")
                    },
                    name_by_user="Климат детская",
                    name="Climate sensor",
                    manufacturer="KOJIMA",
                    model="Temperature and humidity sensor",
                    model_id="KOJIMA-THS-ZG-LCD",
                ),
            }.get(device_id)
        )

        homeassistant = ModuleType("homeassistant")
        helpers = ModuleType("homeassistant.helpers")
        area_module = ModuleType("homeassistant.helpers.area_registry")
        device_module = ModuleType("homeassistant.helpers.device_registry")
        entity_module = ModuleType("homeassistant.helpers.entity_registry")
        area_module.async_get = lambda value: value.area_registry  # type: ignore[attr-defined]
        device_module.async_get = lambda value: value.device_registry  # type: ignore[attr-defined]
        entity_module.async_get = lambda value: value.entity_registry  # type: ignore[attr-defined]
        homeassistant.helpers = helpers  # type: ignore[attr-defined]
        helpers.area_registry = area_module  # type: ignore[attr-defined]
        helpers.device_registry = device_module  # type: ignore[attr-defined]
        helpers.entity_registry = entity_module  # type: ignore[attr-defined]
        fake_modules = {
            "homeassistant": homeassistant,
            "homeassistant.helpers": helpers,
            "homeassistant.helpers.area_registry": area_module,
            "homeassistant.helpers.device_registry": device_module,
            "homeassistant.helpers.entity_registry": entity_module,
        }

        with patch.dict(sys.modules, fake_modules):
            view = HomeAssistantClimateStateView(hass)  # type: ignore[arg-type]
            catalog = view.entity_catalog()
            from custom_components.hausman_hub.application.climate_signal_settings import (
                OUTDOOR_TEMPERATURE_SIGNAL,
            )

            outdoor = view.signal_entity_catalog(OUTDOOR_TEMPERATURE_SIGNAL)

        self.assertEqual(
            [("kids", "Детская"), ("living", "Гостиная")],
            [(room.room_id, room.name) for room in catalog.rooms],
        )
        by_id = {entry.entity_id: entry for entry in catalog.entries}
        self.assertNotIn("sensor.lock_device_temperature", by_id)
        self.assertEqual(
            {"weather.home"},
            {entry.entity_id for entry in outdoor.entries},
        )
        self.assertEqual("living", by_id["climate.living_ac"].room_id)
        self.assertEqual("kids", by_id["sensor.kids_temperature"].room_id)
        self.assertEqual("kids", by_id["sensor.kids_humidity"].room_id)
        self.assertEqual("", by_id["humidifier.mobile"].room_id)
        expected_group = "device_" + hashlib.sha256(
            b"device:device_kids"
        ).hexdigest()[:16]
        for entity_id in (
            "sensor.kids_temperature",
            "sensor.kids_humidity",
        ):
            entry = by_id[entity_id]
            self.assertEqual(expected_group, entry.device_group_id)
            self.assertEqual("Климат детская", entry.device_name)
            self.assertEqual("KOJIMA", entry.manufacturer)
            self.assertEqual("Temperature and humidity sensor", entry.model)
            self.assertEqual(
                (
                    "https://www.zigbee2mqtt.io/images/devices/"
                    "KOJIMA-THS-ZG-LCD.png"
                ),
                entry.image_url,
            )
        self.assertIsNone(by_id["climate.living_ac"].image_url)


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

    def test_setup_options_group_physical_zigbee2mqtt_sensor_entities(self) -> None:
        from custom_components.hausman_hub.application.climate_setup import (
            climate_setup_options,
        )

        registry, contours, _ = _setup()
        _, observation = _native_observation(registry, contours)
        presentation = {
            "device_group_id": "device_0123456789abcdef",
            "device_name": "Климат детская",
            "manufacturer": "KOJIMA",
            "model": "Temperature and humidity sensor",
            "image_url": (
                "https://www.zigbee2mqtt.io/images/devices/"
                "KOJIMA-THS-ZG-LCD.png"
            ),
        }
        catalog = _catalog(
            [
                _entry(
                    "sensor.kids_temperature",
                    state="21.5",
                    device_class="temperature",
                    friendly_name="Климат детская Температура",
                    room_id="kids",
                    **presentation,
                ),
                _entry(
                    "sensor.kids_humidity",
                    state="45",
                    device_class="humidity",
                    friendly_name="Климат детская Влажность",
                    room_id="kids",
                    **presentation,
                ),
            ],
            [ClimateHaCatalogRoom("kids", "Детская")],
        )

        snapshot = build_native_climate_setup_snapshot(
            ClimateRegistry(),
            observation,
            catalog,
        )
        options = climate_setup_options(ClimateRegistry(), snapshot)
        devices = options["devices"]

        self.assertEqual(2, len(devices))
        self.assertEqual(
            {"temperature_sensor", "humidity_sensor"},
            {device["recommended_type"] for device in devices},
        )
        for device in devices:
            for field, expected in presentation.items():
                self.assertEqual(expected, device[field])

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
