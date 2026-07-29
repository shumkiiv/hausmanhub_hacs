"""Local admin configuration routes for mode, home signals, and room signals."""

from __future__ import annotations

import asyncio
from datetime import datetime
import importlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

from custom_components.hausman_hub.application.climate_registry import (
    ClimateRegistryViolation,
    registry_from_payload,
)
from custom_components.hausman_hub.application.climate_runtime import (
    ClimateRuntime,
    ClimateRuntimeUnavailable,
)
from custom_components.hausman_hub.application.climate_signal_settings import (
    CENTRAL_HEATING_DOMAINS,
    CENTRAL_HEATING_SIGNAL,
    OUTDOOR_TEMPERATURE_DOMAINS,
    PRESENCE_DOMAINS,
    ROOM_PRESENCE_DOMAINS,
    WINDOW_DOMAINS,
    WINDOW_SIGNAL,
    ClimateSignalSettingsViolation,
    signal_candidate_is_suitable,
    validate_climate_mode_update,
    validate_home_environment_update,
    validate_optional_signal_entity,
    validate_room_signal_update,
    validate_room_signal_updates,
    validate_room_window_update,
)
from custom_components.hausman_hub.domain.climate_bridge import ClimateControlMode
from custom_components.hausman_hub.domain.configuration import SafeConfiguration
from custom_components.hausman_hub.domain.contours import ContourRegistry
from tests.test_climate_runtime import MemoryContourStore, MemoryStore


ROOT = Path(__file__).resolve().parents[1]


def registry_with_rooms() -> object:
    """Build one schema-2 registry with two windowless rooms."""

    return registry_from_payload(
        {
            "version": 3,
            "home": {
                "outdoor_temperature_entity_id": None,
                "presence_entity_id": None,
                "central_heating_entity_id": None,
            },
            "rooms": [
                {"id": "living", "name": "Гостиная", "window_entity_id": None},
                {"id": "kids", "name": "Детская", "window_entity_id": None},
            ],
            "devices": [],
        }
    )


def configured_setup() -> tuple[object, ContourRegistry]:
    """Build one consistent registry and contour pair with a real device."""

    from custom_components.hausman_hub.application.contours import (
        build_climate_contour_setup,
    )
    from tests.climate_bridge_fixture import import_climate_state
    from tests.test_climate_import import source_payload

    snapshot = import_climate_state(source_payload())
    return build_climate_contour_setup(
        snapshot,
        room_ids=["living"],
        source_ids=["synthetic-ac-source-living"],
        name="Климат",
        mode="automatic",
        target_temperature=25.0,
        target_humidity=45,
        strategy="normal",
    )


class SignalStateView:
    """Bounded fake state view with the signal catalog extension."""

    def __init__(
        self,
        states: dict[str, tuple[str, str, str | None, str]] | None = None,
    ) -> None:
        self._states = dict(states or {})

    def entity_state(self, entity_id: str) -> object | None:
        item = self._states.get(entity_id)
        if item is None:
            return None
        return SimpleNamespace(entity_id=entity_id, state=item[0])

    def signal_entity_catalog(self, signal_kind: str) -> object:
        from custom_components.hausman_hub.application.climate_native_setup import (
            ClimateHaCatalogEntry,
            ClimateHaCatalogRoom,
            ClimateHaEntityCatalog,
        )
        from custom_components.hausman_hub.application.climate_signal_settings import (
            signal_candidate_is_suitable,
        )

        return ClimateHaEntityCatalog(
            entries=tuple(
                ClimateHaCatalogEntry(
                    entity_id=entity_id,
                    domain=entity_id.split(".", 1)[0],
                    state=state,
                    device_class=device_class,
                    supported_features=0,
                    friendly_name=name,
                    available=state not in {"", "unavailable", "unknown"},
                    last_updated_ms=0,
                    room_id=room_id,
                )
                for entity_id, (
                    state,
                    name,
                    device_class,
                    room_id,
                ) in sorted(self._states.items())
                if signal_candidate_is_suitable(
                    signal_kind,
                    domain=entity_id.split(".", 1)[0],
                    device_class=device_class,
                    entity_category=None,
                    attributes={},
                    entity_id=entity_id,
                    friendly_name=name,
                    room_name={
                        "kids": "Детская",
                        "living": "Гостиная",
                    }.get(room_id),
                )
            ),
            rooms=tuple(
                ClimateHaCatalogRoom(room_id=room_id, name=name)
                for room_id, name in (
                    ("kids", "Детская"),
                    ("living", "Гостиная"),
                )
                if any(item[3] == room_id for item in self._states.values())
            ),
        )


def build_runtime(
    registry: object,
    contours: ContourRegistry | None = None,
    state_view: object | None = None,
    mode: ClimateControlMode = ClimateControlMode.DISABLED,
) -> tuple[ClimateRuntime, MemoryStore, MemoryContourStore]:
    """Start one runtime against in-memory stores for admin route tests."""

    store = MemoryStore(registry)
    contour_store = MemoryContourStore(contours)
    runtime = ClimateRuntime(
        entry_id="synthetic-hausmanhub-entry",
        configuration=SafeConfiguration(
            mode="read-only",
            climate_bridge_mode=mode,
        ),
        registry_store=store,
        contour_store=contour_store,
        ha_state_view=state_view,
    )
    asyncio.run(runtime.async_start())
    return runtime, store, contour_store


class ClimateSignalSettingsValidationTest(unittest.TestCase):
    """Lock the exact payload rules of the new admin configuration routes."""

    def test_signal_catalog_rejects_device_configuration_entities(self) -> None:
        self.assertFalse(
            signal_candidate_is_suitable(
                CENTRAL_HEATING_SIGNAL,
                domain="switch",
                device_class=None,
                entity_category="config",
                attributes={"friendly_name": "Увлажнитель детская Alarm"},
            )
        )
        self.assertTrue(
            signal_candidate_is_suitable(
                CENTRAL_HEATING_SIGNAL,
                domain="switch",
                device_class=None,
                entity_category=None,
                attributes={"friendly_name": "Центральное отопление"},
                friendly_name="Центральное отопление",
            )
        )

    def test_central_heating_requires_an_associative_identity(self) -> None:
        for domain, device_class, entity_id, friendly_name in (
            ("switch", None, "switch.living_left", "Выключатель гостиная"),
            ("switch", None, "switch.trv_child_lock", "Термоголовка блокировка"),
            ("switch", None, "switch.trv_heating", "TRV heating"),
            ("binary_sensor", "power", "binary_sensor.tv_power", "Телевизор питание"),
            ("input_boolean", None, "input_boolean.manual", "Ручной режим"),
        ):
            with self.subTest(entity_id=entity_id):
                self.assertFalse(
                    signal_candidate_is_suitable(
                        CENTRAL_HEATING_SIGNAL,
                        domain=domain,
                        device_class=device_class,
                        entity_category=None,
                        attributes={},
                        entity_id=entity_id,
                        friendly_name=friendly_name,
                    )
                )
        for domain, device_class, entity_id, friendly_name in (
            (
                "input_boolean",
                None,
                "input_boolean.central_heating",
                "Центральное отопление",
            ),
            (
                "binary_sensor",
                "running",
                "binary_sensor.heating_pump",
                "Насос отопления",
            ),
            (
                "switch",
                None,
                "switch.boiler",
                "Котёл",
            ),
        ):
            with self.subTest(entity_id=entity_id):
                self.assertTrue(
                    signal_candidate_is_suitable(
                        CENTRAL_HEATING_SIGNAL,
                        domain=domain,
                        device_class=device_class,
                        entity_category=None,
                        attributes={},
                        entity_id=entity_id,
                        friendly_name=friendly_name,
                    )
                )

    def test_outdoor_temperature_rejects_indoor_room_sensors(self) -> None:
        self.assertFalse(
            signal_candidate_is_suitable(
                "outdoor_temperature",
                domain="sensor",
                device_class="temperature",
                entity_category=None,
                attributes={},
                entity_id="sensor.deerma_temperature",
                friendly_name="Увлажнитель детская",
                room_name="Детская",
            )
        )
        self.assertTrue(
            signal_candidate_is_suitable(
                "outdoor_temperature",
                domain="sensor",
                device_class="temperature",
                entity_category=None,
                attributes={},
                entity_id="sensor.vneshnii_datchik_external_temperature",
                friendly_name="Внешний датчик температуры",
                room_name=None,
            )
        )

    def test_optional_signal_entity_accepts_none_and_known_ids(self) -> None:
        known = {"binary_sensor.living_window"}
        lookup = known.__contains__
        self.assertIsNone(
            validate_optional_signal_entity(
                None,
                allowed_domains=WINDOW_DOMAINS,
                entity_known=lookup,
            )
        )
        self.assertEqual(
            "binary_sensor.living_window",
            validate_optional_signal_entity(
                "binary_sensor.living_window",
                allowed_domains=WINDOW_DOMAINS,
                entity_known=lookup,
            ),
        )

    def test_optional_signal_entity_rejects_bounded_failures(self) -> None:
        lookup = {"binary_sensor.living_window"}.__contains__
        for value, code in (
            (42, "invalid_entity"),
            ("", "invalid_entity"),
            ("no-dot", "invalid_entity"),
            (".missingdomain", "invalid_entity"),
            ("sensor.living_window", "unsupported_entity_domain"),
            ("binary_sensor.unknown_window", "unknown_entity"),
        ):
            with self.subTest(value=value):
                with self.assertRaises(ClimateSignalSettingsViolation) as raised:
                    validate_optional_signal_entity(
                        value,
                        allowed_domains=WINDOW_DOMAINS,
                        entity_known=lookup,
                    )
                self.assertEqual(code, raised.exception.code)

    def test_home_environment_update_happy_path(self) -> None:
        known = {
            "sensor.outdoor_temperature",
            "weather.home",
            "person.ivan",
            "switch.central_heating",
        }
        result = validate_home_environment_update(
            {
                "outdoor_temperature_entity_id": "sensor.outdoor_temperature",
                "presence_entity_id": "person.ivan",
                "central_heating_entity_id": "switch.central_heating",
                "heating_lockout_high": 19,
                "heating_lockout_low": 15.5,
            },
            entity_known=known.__contains__,
        )
        self.assertEqual(
            {
                "outdoor_temperature_entity_id": "sensor.outdoor_temperature",
                "presence_entity_id": "person.ivan",
                "central_heating_entity_id": "switch.central_heating",
                "heating_lockout_high": 19.0,
                "heating_lockout_low": 15.5,
            },
            result,
        )
        weather = validate_home_environment_update(
            {
                "outdoor_temperature_entity_id": "weather.home",
                "presence_entity_id": "person.ivan",
                "central_heating_entity_id": "switch.central_heating",
                "heating_lockout_high": 19,
                "heating_lockout_low": 15.5,
            },
            entity_known=known.__contains__,
        )
        self.assertEqual(
            "weather.home",
            weather["outdoor_temperature_entity_id"],
        )

    def test_home_environment_update_rejects_bad_shapes_and_thresholds(self) -> None:
        lookup = {"sensor.outdoor_temperature"}.__contains__
        base = {
            "outdoor_temperature_entity_id": None,
            "presence_entity_id": None,
            "central_heating_entity_id": None,
            "heating_lockout_high": 18,
            "heating_lockout_low": 16,
        }
        for payload, code in (
            (None, "invalid_home_environment"),
            ({}, "invalid_home_environment"),
            ({**base, "extra": True}, "invalid_home_environment"),
            ({**base, "heating_lockout_high": True}, "invalid_lockout_threshold"),
            ({**base, "heating_lockout_high": "18"}, "invalid_lockout_threshold"),
            ({**base, "heating_lockout_high": 61}, "invalid_lockout_threshold"),
            ({**base, "heating_lockout_low": -41}, "invalid_lockout_threshold"),
            (
                {**base, "heating_lockout_high": 10**400},
                "invalid_lockout_threshold",
            ),
            (
                {**base, "heating_lockout_high": float("inf")},
                "invalid_lockout_threshold",
            ),
            (
                {**base, "heating_lockout_high": float("nan")},
                "invalid_lockout_threshold",
            ),
            (
                {**base, "heating_lockout_high": 16, "heating_lockout_low": 16},
                "invalid_lockout_order",
            ),
            (
                {**base, "outdoor_temperature_entity_id": "switch.central_heating"},
                "unsupported_entity_domain",
            ),
            (
                {**base, "outdoor_temperature_entity_id": "sensor.missing"},
                "unknown_entity",
            ),
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ClimateSignalSettingsViolation) as raised:
                    validate_home_environment_update(payload, entity_known=lookup)
                self.assertEqual(code, raised.exception.code)

    def test_room_window_update_happy_and_bounded_failures(self) -> None:
        known = {"binary_sensor.living_window"}
        room_ids = frozenset({"living", "kids"})
        self.assertEqual(
            ("living", "binary_sensor.living_window"),
            validate_room_window_update(
                {"room_id": "living", "window_entity_id": "binary_sensor.living_window"},
                room_ids=room_ids,
                entity_known=known.__contains__,
            ),
        )
        self.assertEqual(
            ("living", None),
            validate_room_window_update(
                {"room_id": "living", "window_entity_id": None},
                room_ids=room_ids,
                entity_known=known.__contains__,
            ),
        )
        for payload, code in (
            ({"room_id": "living"}, "invalid_room_window"),
            ({"room_id": 5, "window_entity_id": None}, "invalid_room"),
            ({"room_id": "attic", "window_entity_id": None}, "unknown_room"),
            (
                {"room_id": "living", "window_entity_id": "sensor.temperature"},
                "unsupported_entity_domain",
            ),
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ClimateSignalSettingsViolation) as raised:
                    validate_room_window_update(
                        payload,
                        room_ids=room_ids,
                        entity_known=known.__contains__,
                    )
                self.assertEqual(code, raised.exception.code)

    def test_room_signal_update_accepts_multiple_presence_sensors(self) -> None:
        known = {
            "binary_sensor.living_window",
            "binary_sensor.living_motion",
            "binary_sensor.living_occupancy",
        }
        room_ids = frozenset({"living", "kids"})
        self.assertEqual(
            (
                "living",
                "binary_sensor.living_window",
                (
                    "binary_sensor.living_motion",
                    "binary_sensor.living_occupancy",
                ),
            ),
            validate_room_signal_update(
                {
                    "room_id": "living",
                    "window_entity_id": "binary_sensor.living_window",
                    "presence_entity_ids": [
                        "binary_sensor.living_motion",
                        "binary_sensor.living_occupancy",
                    ],
                },
                room_ids=room_ids,
                entity_known=known.__contains__,
            ),
        )
        for payload, code in (
            (
                {
                    "room_id": "living",
                    "window_entity_id": None,
                    "presence_entity_ids": [
                        "binary_sensor.living_motion",
                        "binary_sensor.living_motion",
                    ],
                },
                "duplicate_room_presence",
            ),
            (
                {
                    "room_id": "living",
                    "window_entity_id": None,
                    "presence_entity_ids": ["person.ivan"],
                },
                "unsupported_entity_domain",
            ),
            (
                {
                    "room_id": "living",
                    "window_entity_id": None,
                    "presence_entity_ids": ["binary_sensor.missing"],
                },
                "unknown_entity",
            ),
            (
                {
                    "room_id": "living",
                    "window_entity_id": None,
                    "presence_entity_ids": "binary_sensor.living_motion",
                },
                "invalid_room_presence",
            ),
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ClimateSignalSettingsViolation) as raised:
                    validate_room_signal_update(
                        payload,
                        room_ids=room_ids,
                        entity_known=known.__contains__,
                    )
                self.assertEqual(code, raised.exception.code)
        self.assertEqual(frozenset({"binary_sensor"}), ROOM_PRESENCE_DOMAINS)

    def test_room_signal_batch_is_bounded_and_cross_room_unique(self) -> None:
        known = {
            "binary_sensor.living_motion",
            "binary_sensor.kids_motion",
        }
        room_ids = frozenset({"living", "kids"})
        self.assertEqual(
            (
                (
                    "living",
                    None,
                    ("binary_sensor.living_motion",),
                ),
                (
                    "kids",
                    None,
                    ("binary_sensor.kids_motion",),
                ),
            ),
            validate_room_signal_updates(
                {
                    "rooms": [
                        {
                            "room_id": "living",
                            "window_entity_id": None,
                            "presence_entity_ids": [
                                "binary_sensor.living_motion"
                            ],
                        },
                        {
                            "room_id": "kids",
                            "window_entity_id": None,
                            "presence_entity_ids": [
                                "binary_sensor.kids_motion"
                            ],
                        },
                    ]
                },
                room_ids=room_ids,
                entity_known=known.__contains__,
            ),
        )
        for payload, code in (
            ({"rooms": []}, "invalid_room_signal_batch"),
            (
                {
                    "rooms": [
                        {
                            "room_id": "living",
                            "window_entity_id": None,
                            "presence_entity_ids": [],
                        },
                        {
                            "room_id": "living",
                            "window_entity_id": None,
                            "presence_entity_ids": [],
                        },
                    ]
                },
                "duplicate_room_update",
            ),
            (
                {
                    "rooms": [
                        {
                            "room_id": "living",
                            "window_entity_id": None,
                            "presence_entity_ids": [
                                "binary_sensor.living_motion"
                            ],
                        },
                        {
                            "room_id": "kids",
                            "window_entity_id": None,
                            "presence_entity_ids": [
                                "binary_sensor.living_motion"
                            ],
                        },
                    ]
                },
                "duplicate_room_presence",
            ),
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ClimateSignalSettingsViolation) as raised:
                    validate_room_signal_updates(
                        payload,
                        room_ids=room_ids,
                        entity_known=known.__contains__,
                    )
                self.assertEqual(code, raised.exception.code)

    def test_climate_mode_update_rules(self) -> None:
        self.assertEqual(
            "disabled",
            validate_climate_mode_update(
                "managed",
                {"mode": "disabled", "expected_mode": "managed", "confirm": None},
            ),
        )
        self.assertEqual(
            "managed",
            validate_climate_mode_update(
                "disabled",
                {"mode": "managed", "expected_mode": "disabled", "confirm": True},
            ),
        )
        for current, payload, code in (
            (
                "disabled",
                {"mode": "managed", "expected_mode": "disabled", "confirm": None},
                "confirmation_required",
            ),
            (
                "disabled",
                {"mode": "managed", "expected_mode": "managed", "confirm": True},
                "mode_changed",
            ),
            (
                "disabled",
                {"mode": "shadow", "expected_mode": "disabled", "confirm": True},
                "invalid_mode",
            ),
            (
                "disabled",
                {"mode": "disabled", "expected_mode": "disabled"},
                "invalid_mode_update",
            ),
            (
                "disabled",
                {"mode": "disabled", "expected_mode": "disabled", "confirm": "yes"},
                "invalid_confirmation",
            ),
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ClimateSignalSettingsViolation) as raised:
                    validate_climate_mode_update(current, payload)
                self.assertEqual(code, raised.exception.code)


class ClimateAdminRuntimeSupportTest(unittest.TestCase):
    """Lock the new runtime seams behind the admin configuration routes."""

    def test_configured_setup_serializes_null_bounds_and_control_channel(self) -> None:
        from custom_components.hausman_hub.application.climate_registry import (
            registry_to_payload,
        )
        from custom_components.hausman_hub.application.contours import (
            contour_registry_to_payload,
        )

        registry, contours = configured_setup()
        registry_payload = registry_to_payload(registry)  # type: ignore[arg-type]
        contour_payload = contour_registry_to_payload(contours)

        self.assertIsNone(registry_payload["devices"][0]["control_channel"])  # type: ignore[index]
        room = contour_payload["contours"][0]["rooms"][0]  # type: ignore[index]
        self.assertIsNone(room["min_temperature"])
        self.assertIsNone(room["max_temperature"])

    def test_update_room_window_rewrites_one_room_atomically(self) -> None:
        runtime, store, _ = build_runtime(registry_with_rooms())
        payload = asyncio.run(
            runtime.async_update_room_window("living", "binary_sensor.living_window")
        )
        rooms = {room["id"]: room for room in payload["rooms"]}
        self.assertEqual(
            "binary_sensor.living_window",
            rooms["living"]["window_entity_id"],
        )
        self.assertIsNone(rooms["kids"]["window_entity_id"])
        self.assertEqual(1, len(store.saved))

    def test_update_room_window_rejects_an_unknown_room_without_write(self) -> None:
        runtime, store, _ = build_runtime(registry_with_rooms())
        with self.assertRaises(ClimateRegistryViolation):
            asyncio.run(runtime.async_update_room_window("attic", None))
        self.assertEqual([], store.saved)

    def test_update_room_signals_rewrites_multiple_presence_bindings(self) -> None:
        runtime, store, _ = build_runtime(registry_with_rooms())
        payload = asyncio.run(
            runtime.async_update_room_signals(
                "living",
                "binary_sensor.living_window",
                (
                    "binary_sensor.living_motion",
                    "binary_sensor.living_occupancy",
                ),
            )
        )
        living = next(room for room in payload["rooms"] if room["id"] == "living")
        self.assertEqual(
            [
                "binary_sensor.living_motion",
                "binary_sensor.living_occupancy",
            ],
            living["presence_entity_ids"],
        )
        self.assertEqual(1, len(store.saved))

        moved = asyncio.run(
            runtime.async_update_room_signal_batch(
                (
                    (
                        "living",
                        "binary_sensor.living_window",
                        ("binary_sensor.living_occupancy",),
                    ),
                    (
                        "kids",
                        None,
                        ("binary_sensor.living_motion",),
                    ),
                )
            )
        )
        moved_rooms = {room["id"]: room for room in moved["rooms"]}
        self.assertEqual(
            ["binary_sensor.living_occupancy"],
            moved_rooms["living"]["presence_entity_ids"],
        )
        self.assertEqual(
            ["binary_sensor.living_motion"],
            moved_rooms["kids"]["presence_entity_ids"],
        )
        self.assertEqual(2, len(store.saved))

        with self.assertRaises(ClimateRegistryViolation):
            asyncio.run(
                runtime.async_update_room_signal_batch(
                    (
                        (
                            "living",
                            None,
                            ("binary_sensor.shared_motion",),
                        ),
                        (
                            "kids",
                            None,
                            ("binary_sensor.shared_motion",),
                        ),
                    )
                )
            )
        self.assertEqual(2, len(store.saved))

    def test_climate_mode_status_reflects_mode_and_contour(self) -> None:
        runtime, _, _ = build_runtime(registry_with_rooms())
        self.assertEqual(
            {"mode": "disabled", "contour_configured": False},
            asyncio.run(runtime.async_climate_mode_status()),
        )
        registry, contours = configured_setup()
        managed_runtime, _, _ = build_runtime(
            registry,
            contours=contours,
            mode=ClimateControlMode.MANAGED,
        )
        self.assertEqual(
            {"mode": "managed", "contour_configured": True},
            asyncio.run(managed_runtime.async_climate_mode_status()),
        )

    def test_signal_catalog_and_entity_lookup(self) -> None:
        view = SignalStateView(
            {
                "binary_sensor.living_window": (
                    "off",
                    "Окно гостиной",
                    "window",
                    "living",
                ),
                "sensor.outdoor_temperature": (
                    "4.5",
                    "Улица",
                    "temperature",
                    "",
                ),
                "climate.living_ac": ("cool", "Кондиционер", None, "living"),
            }
        )
        runtime, _, _ = build_runtime(registry_with_rooms(), state_view=view)
        self.assertTrue(runtime.signal_entity_known("binary_sensor.living_window"))
        self.assertFalse(runtime.signal_entity_known("binary_sensor.missing"))
        catalog = asyncio.run(runtime.async_signal_catalog(WINDOW_SIGNAL))
        self.assertEqual(
            [
                {
                    "entity_id": "binary_sensor.living_window",
                    "name": "Окно гостиной",
                    "available": True,
                    "domain": "binary_sensor",
                    "room_id": "living",
                    "device_class": "window",
                    "room_name": "Гостиная",
                }
            ],
            catalog,
        )

    def test_signal_catalog_fails_closed_without_the_extension(self) -> None:
        runtime, _, _ = build_runtime(registry_with_rooms(), state_view=object())
        with self.assertRaises(ClimateRuntimeUnavailable):
            asyncio.run(runtime.async_signal_catalog(WINDOW_SIGNAL))
        runtime_without_view, _, _ = build_runtime(registry_with_rooms())
        with self.assertRaises(ClimateRuntimeUnavailable):
            asyncio.run(runtime_without_view.async_signal_catalog(WINDOW_SIGNAL))
        self.assertFalse(runtime_without_view.signal_entity_known("sensor.any"))


PACKAGE_MODULE = "custom_components.hausman_hub"
CLIMATE_API_MODULE = f"{PACKAGE_MODULE}.climate_api"


class ClimateAdminConfigurationRoutesTest(unittest.TestCase):
    """Exercise the new admin routes through the guarded HTTP boundary."""

    @classmethod
    def setUpClass(cls) -> None:
        from tests.test_local_summary_access import (
            FAKE_MODULE_NAMES,
            fake_home_assistant_modules,
        )

        cls.fake_module_names = FAKE_MODULE_NAMES
        cls.original_sys_path = sys.path[:]
        cls.previous_modules = {
            name: sys.modules.get(name)
            for name in (*FAKE_MODULE_NAMES, PACKAGE_MODULE, CLIMATE_API_MODULE)
        }
        for name in (*FAKE_MODULE_NAMES, PACKAGE_MODULE, CLIMATE_API_MODULE):
            sys.modules.pop(name, None)
        sys.modules.update(fake_home_assistant_modules())
        cls.integration = importlib.import_module(PACKAGE_MODULE)

    @classmethod
    def tearDownClass(cls) -> None:
        for name in (*cls.fake_module_names, PACKAGE_MODULE, CLIMATE_API_MODULE):
            sys.modules.pop(name, None)
        sys.modules.update(
            {
                name: module
                for name, module in cls.previous_modules.items()
                if module is not None
            }
        )
        sys.path[:] = cls.original_sys_path

    def setUp(self) -> None:
        from tests.test_local_summary_access import FakeEntry, FakeHomeAssistant

        self.hass = FakeHomeAssistant()
        self.entry = FakeEntry(
            {
                "mode": "read-only",
                "direct_execution_status": "direct_execution_blocked",
            },
            {},
        )
        self.hass.config_entries.entries = [self.entry]
        self.assertTrue(
            asyncio.run(self.integration.async_setup_entry(self.hass, self.entry))
        )
        self.views = {view.url: view for view in self.hass.http.views}
        self.updated_options: list[tuple[object, object]] = []

        def async_update_entry(
            entry: object,
            *,
            data: object = None,
            options: object = None,
        ) -> None:
            if data is not None:
                entry.data = dict(data)
            if options is not None:
                self.updated_options.append((entry, options))
                entry.options = dict(options)

        self.hass.config_entries.async_update_entry = async_update_entry
        from datetime import datetime as dt

        signal_states = {
            "sensor.synthetic_outdoor": SimpleNamespace(
                entity_id="sensor.synthetic_outdoor",
                state="4.5",
                attributes={
                    "friendly_name": "Улица",
                    "device_class": "temperature",
                },
                last_updated=dt(2026, 7, 19, 12, 0),
            ),
            "weather.synthetic_home": SimpleNamespace(
                entity_id="weather.synthetic_home",
                state="sunny",
                attributes={
                    "friendly_name": "Погода дома",
                    "temperature": 6.5,
                },
                last_updated=dt(2026, 7, 19, 12, 0),
            ),
            "sensor.synthetic_lock_temperature": SimpleNamespace(
                entity_id="sensor.synthetic_lock_temperature",
                state="37.0",
                attributes={
                    "friendly_name": "Температура замка",
                    "device_class": "temperature",
                },
                last_updated=dt(2026, 7, 19, 12, 0),
            ),
            "sensor.synthetic_kids_temperature": SimpleNamespace(
                entity_id="sensor.synthetic_kids_temperature",
                state="23.5",
                attributes={
                    "friendly_name": "Температура детской",
                    "device_class": "temperature",
                },
                last_updated=dt(2026, 7, 19, 12, 0),
            ),
            "person.synthetic_ivan": SimpleNamespace(
                entity_id="person.synthetic_ivan",
                state="home",
                attributes={"friendly_name": "Иван"},
                last_updated=dt(2026, 7, 19, 12, 0),
            ),
            "switch.synthetic_central": SimpleNamespace(
                entity_id="switch.synthetic_central",
                state="off",
                attributes={"friendly_name": "Центральное отопление"},
                last_updated=dt(2026, 7, 19, 12, 0),
            ),
            "binary_sensor.synthetic_window": SimpleNamespace(
                entity_id="binary_sensor.synthetic_window",
                state="off",
                attributes={"friendly_name": "Окно", "device_class": "window"},
                last_updated=dt(2026, 7, 19, 12, 0),
            ),
            "binary_sensor.synthetic_motion": SimpleNamespace(
                entity_id="binary_sensor.synthetic_motion",
                state="off",
                attributes={"friendly_name": "Движение", "device_class": "motion"},
                last_updated=dt(2026, 7, 19, 12, 0),
            ),
            "binary_sensor.synthetic_occupancy": SimpleNamespace(
                entity_id="binary_sensor.synthetic_occupancy",
                state="on",
                attributes={
                    "friendly_name": "Присутствие",
                    "device_class": "occupancy",
                },
                last_updated=dt(2026, 7, 19, 12, 0),
            ),
        }
        self.hass.states.values = signal_states
        self.hass.states.async_all = lambda: list(signal_states.values())
        self.hass.area_registry = SimpleNamespace(
            async_list_areas=lambda: [
                SimpleNamespace(id="kids", name="Детская"),
                SimpleNamespace(id="living", name="Гостиная"),
            ]
        )
        for index, (entity_id, room_id) in enumerate(
            (
                ("binary_sensor.synthetic_motion", "living"),
                ("binary_sensor.synthetic_occupancy", "living"),
                ("binary_sensor.synthetic_window", "living"),
                ("sensor.synthetic_outdoor", ""),
                ("weather.synthetic_home", ""),
                ("sensor.synthetic_lock_temperature", "living"),
                ("sensor.synthetic_kids_temperature", "kids"),
            )
        ):
            self.hass.entity_registry.entities[f"signal-{index}"] = SimpleNamespace(
                entity_id=entity_id,
                area_id=room_id or None,
                device_id=None,
                entity_category=(
                    "diagnostic"
                    if entity_id == "sensor.synthetic_lock_temperature"
                    else None
                ),
            )

    def _admin(self) -> object:
        from tests.test_local_summary_access import reader_user

        return reader_user("system-admin", admin=True)

    def _tablet(self) -> object:
        from tests.test_local_summary_access import reader_user

        return reader_user("system-users")

    def _inject_runtime(
        self,
        configured: bool = False,
    ) -> None:
        if configured:
            registry, contours = configured_setup()
        else:
            registry, contours = registry_with_rooms(), None
        runtime, store, contour_store = build_runtime(
            registry,
            contours=contours,
            state_view=SignalStateView(
                {
                    "binary_sensor.synthetic_window": (
                        "off",
                        "Окно",
                        "window",
                        "living",
                    ),
                    "binary_sensor.synthetic_motion": (
                        "off",
                        "Движение",
                        "motion",
                        "living",
                    ),
                    "binary_sensor.synthetic_occupancy": (
                        "on",
                        "Присутствие",
                        "occupancy",
                        "living",
                    ),
                    "binary_sensor.synthetic_kids_motion": (
                        "off",
                        "Движение детской",
                        "motion",
                        "kids",
                    ),
                }
            ),
        )
        self.runtime_store = store
        self.runtime_contour_store = contour_store
        self.hass.data["hausman_hub"]["climate_runtime"] = runtime

    def _get(self, path: str, user: object, remote: str = "192.168.1.20") -> object:
        from tests.test_local_summary_access import FakeRequest

        return asyncio.run(self.views[path].get(FakeRequest(remote, user, path=path)))

    def _post(
        self,
        path: str,
        user: object,
        payload: object,
        remote: str = "192.168.1.20",
    ) -> object:
        from tests.test_local_summary_access import FakeJsonRequest

        return asyncio.run(
            self.views[path].post(FakeJsonRequest(remote, user, path, payload))
        )

    def test_routes_registered_and_guarded(self) -> None:
        from tests.test_local_summary_access import FakeRequest, reader_user

        for path in (
            "/api/hausman_hub/v1/admin/climate-mode",
            "/api/hausman_hub/v1/admin/home-environment",
            "/api/hausman_hub/v1/admin/climate-room-signals",
        ):
            self.assertIn(path, self.views)
            with self.subTest(path=path):
                self.assertEqual(
                    404,
                    asyncio.run(
                        self.views[path].get(
                            FakeRequest(
                                "192.168.1.20",
                                self._admin(),
                                path=path,
                                query_string="unexpected=1",
                            )
                        )
                    ).status,
                )
                for user, remote in (
                    (self._tablet(), "192.168.1.20"),
                    (reader_user("system-read-only"), "192.168.1.20"),
                    (self._admin(), "8.8.8.8"),
                ):
                    with self.subTest(user=user, remote=remote):
                        self.assertEqual(
                            403,
                            asyncio.run(
                                self.views[path].get(
                                    FakeRequest(remote, user, path=path)
                                )
                            ).status,
                        )

    def test_post_guards_and_runtime_unavailability(self) -> None:
        from tests.test_local_summary_access import FakeJsonRequest, reader_user

        mode_path = "/api/hausman_hub/v1/admin/climate-mode"
        system_generated_admin = reader_user(
            "system-admin",
            admin=True,
            system_generated=True,
        )
        payload = {"mode": "disabled", "expected_mode": "disabled", "confirm": None}
        for path in (
            mode_path,
            "/api/hausman_hub/v1/admin/home-environment",
            "/api/hausman_hub/v1/admin/climate-room-signals",
        ):
            with self.subTest(path=path):
                self.assertEqual(
                    403,
                    self._post(path, system_generated_admin, payload).status,
                )
                self.assertEqual(
                    403,
                    self._post(path, self._tablet(), payload).status,
                )
                self.assertEqual(
                    403,
                    self._post(path, self._admin(), payload, remote="8.8.8.8").status,
                )
                with_query = FakeJsonRequest(
                    "192.168.1.20",
                    self._admin(),
                    path,
                    payload,
                )
                with_query.query_string = "unexpected=1"
                self.assertEqual(
                    404,
                    asyncio.run(self.views[path].post(with_query)).status,
                )
        oversized = self._post(
            mode_path,
            self._admin(),
            {
                "mode": "x" * 20000,
                "expected_mode": "disabled",
                "confirm": None,
            },
        )
        self.assertEqual(400, oversized.status)

        del self.hass.data["hausman_hub"]["climate_runtime"]
        self.assertEqual(503, self._get(mode_path, self._admin()).status)
        self.assertEqual(503, self._post(mode_path, self._admin(), payload).status)

    def test_mode_second_stale_post_loses_with_409(self) -> None:
        path = "/api/hausman_hub/v1/admin/climate-mode"
        self._inject_runtime(configured=True)
        enabled = self._post(
            path,
            self._admin(),
            {"mode": "managed", "expected_mode": "disabled", "confirm": True},
        )
        self.assertEqual(200, enabled.status)
        stale = self._post(
            path,
            self._admin(),
            {"mode": "disabled", "expected_mode": "disabled", "confirm": None},
        )
        self.assertEqual(409, stale.status)
        self.assertEqual(1, len(self.updated_options))

    def test_home_environment_error_payload_never_writes(self) -> None:
        path = "/api/hausman_hub/v1/admin/home-environment"
        self._inject_runtime()
        rejected = self._post(
            path,
            self._admin(),
            {
                "outdoor_temperature_entity_id": "sensor.missing_outdoor",
                "presence_entity_id": None,
                "central_heating_entity_id": None,
                "heating_lockout_high": 18,
                "heating_lockout_low": 16,
            },
        )
        self.assertEqual(400, rejected.status)
        self.assertEqual([], self.runtime_store.saved)

    def test_climate_mode_get_and_disabled_repost(self) -> None:
        path = "/api/hausman_hub/v1/admin/climate-mode"
        response = self._get(path, self._admin())
        self.assertEqual(200, response.status)
        self.assertEqual(
            {"mode": "disabled", "contour_configured": False},
            response.payload,
        )
        repost = self._post(
            path,
            self._admin(),
            {"mode": "disabled", "expected_mode": "disabled", "confirm": None},
        )
        self.assertEqual(200, repost.status)
        self.assertEqual(1, len(self.updated_options))
        _, options = self.updated_options[0]
        self.assertEqual("disabled", options["climate_bridge_mode"])
        self.assertIsNone(options.get("climate_bridge_target"))
        self.assertIsNone(options.get("climate_canary_room_id"))

    def test_climate_mode_managed_requires_contour_and_consent(self) -> None:
        path = "/api/hausman_hub/v1/admin/climate-mode"
        without_contour = self._post(
            path,
            self._admin(),
            {"mode": "managed", "expected_mode": "disabled", "confirm": True},
        )
        self.assertEqual(409, without_contour.status)
        without_consent = self._post(
            path,
            self._admin(),
            {"mode": "managed", "expected_mode": "disabled", "confirm": None},
        )
        self.assertEqual(400, without_consent.status)
        stale = self._post(
            path,
            self._admin(),
            {"mode": "disabled", "expected_mode": "managed", "confirm": None},
        )
        self.assertEqual(409, stale.status)
        malformed = self._post(
            path,
            self._admin(),
            {"mode": "managed"},
        )
        self.assertEqual(400, malformed.status)
        self.assertEqual([], self.updated_options)

        self._inject_runtime(configured=True)
        enabled = self._post(
            path,
            self._admin(),
            {"mode": "managed", "expected_mode": "disabled", "confirm": True},
        )
        self.assertEqual(200, enabled.status)
        self.assertEqual(
            {"mode": "managed", "contour_configured": True},
            enabled.payload,
        )
        _, options = self.updated_options[0]
        self.assertEqual("managed", options["climate_bridge_mode"])

    def test_home_environment_get_and_update(self) -> None:
        path = "/api/hausman_hub/v1/admin/home-environment"
        response = self._get(path, self._admin())
        self.assertEqual(200, response.status)
        self.assertEqual(
            {
                "outdoor_temperature_entity_id": None,
                "presence_entity_id": None,
                "central_heating_entity_id": None,
            },
            {
                key: response.payload["home"].get(key)
                for key in (
                    "outdoor_temperature_entity_id",
                    "presence_entity_id",
                    "central_heating_entity_id",
                )
            },
        )
        candidate_groups = response.payload["candidates"]
        self.assertEqual(
            ["sensor.synthetic_outdoor", "weather.synthetic_home"],
            [item["entity_id"] for item in candidate_groups["outdoor_temperature"]],
        )
        self.assertEqual(
            [
                "binary_sensor.synthetic_motion",
                "binary_sensor.synthetic_occupancy",
                "person.synthetic_ivan",
            ],
            [item["entity_id"] for item in candidate_groups["presence"]],
        )
        self.assertEqual(
            [
                "switch.synthetic_central",
            ],
            [item["entity_id"] for item in candidate_groups["central_heating"]],
        )

        saved = self._post(
            path,
            self._admin(),
            {
                "outdoor_temperature_entity_id": "sensor.synthetic_outdoor",
                "presence_entity_id": "person.synthetic_ivan",
                "central_heating_entity_id": "switch.synthetic_central",
                "heating_lockout_high": 19,
                "heating_lockout_low": 15,
            },
        )
        self.assertEqual(200, saved.status)
        self.assertEqual(
            "sensor.synthetic_outdoor",
            saved.payload["home"]["outdoor_temperature_entity_id"],
        )
        self.assertEqual(19.0, saved.payload["home"]["heating_lockout_high"])
        self.assertIsInstance(saved.payload["setup_revision"], int)

        weather_saved = self._post(
            path,
            self._admin(),
            {
                "outdoor_temperature_entity_id": "weather.synthetic_home",
                "presence_entity_id": "person.synthetic_ivan",
                "central_heating_entity_id": "switch.synthetic_central",
                "heating_lockout_high": 19,
                "heating_lockout_low": 15,
            },
        )
        self.assertEqual(200, weather_saved.status)
        self.assertEqual(
            "weather.synthetic_home",
            weather_saved.payload["home"]["outdoor_temperature_entity_id"],
        )
        self.assertNotEqual(
            saved.payload["setup_revision"],
            weather_saved.payload["setup_revision"],
        )

        for payload in (
            {
                "outdoor_temperature_entity_id": "binary_sensor.synthetic_window",
                "presence_entity_id": None,
                "central_heating_entity_id": None,
                "heating_lockout_high": 18,
                "heating_lockout_low": 16,
            },
            {
                "outdoor_temperature_entity_id": "sensor.missing_outdoor",
                "presence_entity_id": None,
                "central_heating_entity_id": None,
                "heating_lockout_high": 18,
                "heating_lockout_low": 16,
            },
            {
                "outdoor_temperature_entity_id": "sensor.synthetic_lock_temperature",
                "presence_entity_id": None,
                "central_heating_entity_id": None,
                "heating_lockout_high": 18,
                "heating_lockout_low": 16,
            },
            {
                "outdoor_temperature_entity_id": None,
                "presence_entity_id": None,
                "central_heating_entity_id": None,
                "heating_lockout_high": 15,
                "heating_lockout_low": 16,
            },
        ):
            with self.subTest(payload=payload):
                self.assertEqual(400, self._post(path, self._admin(), payload).status)

    def test_full_reset_requires_confirmation_and_preserves_ha_inventory(self) -> None:
        path = "/api/hausman_hub/v1/admin/reset"
        self.assertIn(path, self.views)
        self._inject_runtime(configured=True)
        self.assertEqual(400, self._post(path, self._admin(), {}).status)
        self.assertEqual(
            403,
            self._post(
                path,
                self._tablet(),
                {"confirmation": "RESET_HAUSMANHUB"},
            ).status,
        )
        response = self._post(
            path,
            self._admin(),
            {"confirmation": "RESET_HAUSMANHUB"},
        )
        self.assertEqual(200, response.status)
        self.assertEqual("reset", response.payload["status"])
        self.assertEqual(
            ["home_assistant_areas", "home_assistant_devices"],
            response.payload["preserved"],
        )
        runtime = self.hass.data["hausman_hub"]["climate_runtime"]
        registry = asyncio.run(runtime.async_registry_payload())
        contours = asyncio.run(runtime.async_contour_registry_payload())
        self.assertEqual([], registry["rooms"])
        self.assertEqual([], registry["devices"])
        self.assertEqual([], contours["contours"])
        self.assertEqual("read-only", self.entry.options["mode"])
        self.assertNotIn("smart_home_center_url", self.entry.options)
        self.assertEqual(2, len(self.hass.area_registry.async_list_areas()))

    def test_device_area_assignment_route_is_explicit_and_guarded(self) -> None:
        from tests.test_local_summary_access import FakeJsonRequest, reader_user

        path = "/api/hausman_hub/v1/admin/device-area-assignments"
        self.assertIn(path, self.views)
        payload = {"snapshot_revision": 0, "assignments": []}
        for user, remote in (
            (self._tablet(), "192.168.1.20"),
            (reader_user("system-read-only"), "192.168.1.20"),
            (self._admin(), "8.8.8.8"),
        ):
            with self.subTest(user=user, remote=remote):
                self.assertEqual(403, self._post(path, user, payload, remote).status)
        with_query = FakeJsonRequest(
            "192.168.1.20",
            self._admin(),
            path,
            payload,
        )
        with_query.query_string = "unexpected=1"
        self.assertEqual(404, asyncio.run(self.views[path].post(with_query)).status)
        self.assertEqual(409, self._post(path, self._admin(), payload).status)

    def test_room_signals_get_update_and_clear(self) -> None:
        path = "/api/hausman_hub/v1/admin/climate-room-signals"
        self._inject_runtime()
        response = self._get(path, self._admin())
        self.assertEqual(200, response.status)
        self.assertEqual(
            [
                {
                    "id": "living",
                    "name": "Гостиная",
                    "window_entity_id": None,
                    "presence_entity_ids": [],
                },
                {
                    "id": "kids",
                    "name": "Детская",
                    "window_entity_id": None,
                    "presence_entity_ids": [],
                },
            ],
            response.payload["rooms"],
        )
        self.assertEqual(
            ["binary_sensor.synthetic_window"],
            [item["entity_id"] for item in response.payload["candidates"]],
        )
        self.assertEqual(
            [
                "binary_sensor.synthetic_kids_motion",
                "binary_sensor.synthetic_motion",
                "binary_sensor.synthetic_occupancy",
            ],
            [
                item["entity_id"]
                for item in response.payload["presence_candidates"]
            ],
        )

        bound = self._post(
            path,
            self._admin(),
            {
                "rooms": [
                    {
                        "room_id": "living",
                        "window_entity_id": "binary_sensor.synthetic_window",
                        "presence_entity_ids": [
                            "binary_sensor.synthetic_motion",
                            "binary_sensor.synthetic_occupancy",
                        ],
                    }
                ],
            },
        )
        self.assertEqual(200, bound.status)
        rooms = {room["id"]: room for room in bound.payload["rooms"]}
        self.assertEqual(
            "binary_sensor.synthetic_window",
            rooms["living"]["window_entity_id"],
        )
        self.assertIsNone(rooms["kids"]["window_entity_id"])
        self.assertEqual(
            [
                "binary_sensor.synthetic_motion",
                "binary_sensor.synthetic_occupancy",
            ],
            rooms["living"]["presence_entity_ids"],
        )

        moved = self._post(
            path,
            self._admin(),
            {
                "rooms": [
                    {
                        "room_id": "living",
                        "window_entity_id": "binary_sensor.synthetic_window",
                        "presence_entity_ids": [
                            "binary_sensor.synthetic_occupancy"
                        ],
                    },
                    {
                        "room_id": "kids",
                        "window_entity_id": None,
                        "presence_entity_ids": [
                            "binary_sensor.synthetic_kids_motion"
                        ],
                    },
                ],
            },
        )
        self.assertEqual(200, moved.status)
        moved_rooms = {room["id"]: room for room in moved.payload["rooms"]}
        self.assertEqual(
            ["binary_sensor.synthetic_occupancy"],
            moved_rooms["living"]["presence_entity_ids"],
        )
        self.assertEqual(
            ["binary_sensor.synthetic_kids_motion"],
            moved_rooms["kids"]["presence_entity_ids"],
        )

        cleared = self._post(
            path,
            self._admin(),
            {"room_id": "living", "window_entity_id": None},
        )
        self.assertEqual(200, cleared.status)
        self.assertIsNone(
            {room["id"]: room for room in cleared.payload["rooms"]}["living"][
                "window_entity_id"
            ]
        )
        self.assertEqual(
            ["binary_sensor.synthetic_occupancy"],
            {room["id"]: room for room in cleared.payload["rooms"]}["living"][
                "presence_entity_ids"
            ],
        )

        for payload in (
            {"room_id": "attic", "window_entity_id": None},
            {"room_id": "living", "window_entity_id": "sensor.synthetic_outdoor"},
            {"room_id": "living", "window_entity_id": "binary_sensor.missing"},
            {"room_id": "living"},
            {
                "room_id": "kids",
                "window_entity_id": None,
                "presence_entity_ids": [
                    "binary_sensor.synthetic_occupancy",
                ],
            },
            {
                "room_id": "kids",
                "window_entity_id": None,
                "presence_entity_ids": [
                    "binary_sensor.synthetic_motion",
                    "binary_sensor.synthetic_motion",
                ],
            },
            {
                "rooms": [
                    {
                        "room_id": "living",
                        "window_entity_id": None,
                        "presence_entity_ids": [
                            "binary_sensor.synthetic_motion"
                        ],
                    },
                    {
                        "room_id": "kids",
                        "window_entity_id": None,
                        "presence_entity_ids": [
                            "binary_sensor.synthetic_motion"
                        ],
                    },
                ]
            },
        ):
            with self.subTest(payload=payload):
                self.assertEqual(400, self._post(path, self._admin(), payload).status)


if __name__ == "__main__":
    unittest.main()
