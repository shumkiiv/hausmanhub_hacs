"""Local admin configuration routes for mode, home signals, and room windows."""

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
    OUTDOOR_TEMPERATURE_DOMAINS,
    PRESENCE_DOMAINS,
    WINDOW_DOMAINS,
    ClimateSignalSettingsViolation,
    validate_climate_mode_update,
    validate_home_environment_update,
    validate_optional_signal_entity,
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
            "version": 2,
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

    def __init__(self, states: dict[str, tuple[str, str]] | None = None) -> None:
        self._states = dict(states or {})

    def entity_state(self, entity_id: str) -> object | None:
        item = self._states.get(entity_id)
        if item is None:
            return None
        return SimpleNamespace(entity_id=entity_id, state=item[0])

    def signal_entity_catalog(self, allowed_domains: frozenset[str]) -> object:
        from custom_components.hausman_hub.application.climate_native_setup import (
            ClimateHaCatalogEntry,
            ClimateHaEntityCatalog,
        )

        return ClimateHaEntityCatalog(
            entries=tuple(
                ClimateHaCatalogEntry(
                    entity_id=entity_id,
                    domain=entity_id.split(".", 1)[0],
                    state=state,
                    device_class=None,
                    supported_features=0,
                    friendly_name=name,
                    available=state not in {"", "unavailable", "unknown"},
                    last_updated_ms=0,
                )
                for entity_id, (state, name) in sorted(self._states.items())
                if entity_id.split(".", 1)[0] in allowed_domains
            )
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
                "binary_sensor.living_window": ("off", "Окно гостиной"),
                "sensor.outdoor_temperature": ("4.5", "Улица"),
                "climate.living_ac": ("cool", "Кондиционер"),
            }
        )
        runtime, _, _ = build_runtime(registry_with_rooms(), state_view=view)
        self.assertTrue(runtime.signal_entity_known("binary_sensor.living_window"))
        self.assertFalse(runtime.signal_entity_known("binary_sensor.missing"))
        catalog = asyncio.run(runtime.async_signal_catalog(WINDOW_DOMAINS))
        self.assertEqual(
            [
                {
                    "entity_id": "binary_sensor.living_window",
                    "name": "Окно гостиной",
                    "available": True,
                }
            ],
            catalog,
        )

    def test_signal_catalog_fails_closed_without_the_extension(self) -> None:
        runtime, _, _ = build_runtime(registry_with_rooms(), state_view=object())
        with self.assertRaises(ClimateRuntimeUnavailable):
            asyncio.run(runtime.async_signal_catalog(WINDOW_DOMAINS))
        runtime_without_view, _, _ = build_runtime(registry_with_rooms())
        with self.assertRaises(ClimateRuntimeUnavailable):
            asyncio.run(runtime_without_view.async_signal_catalog(WINDOW_DOMAINS))
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

        def async_update_entry(entry: object, *, options: object = None) -> None:
            self.updated_options.append((entry, options))
            entry.options = dict(options)

        self.hass.config_entries.async_update_entry = async_update_entry
        from datetime import datetime as dt

        signal_states = {
            "sensor.synthetic_outdoor": SimpleNamespace(
                entity_id="sensor.synthetic_outdoor",
                state="4.5",
                attributes={"friendly_name": "Улица"},
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
                attributes={"friendly_name": "Окно"},
                last_updated=dt(2026, 7, 19, 12, 0),
            ),
        }
        self.hass.states.values = signal_states
        self.hass.states.async_all = lambda: list(signal_states.values())

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
                    "binary_sensor.synthetic_window": ("off", "Окно"),
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
            ["sensor.synthetic_outdoor"],
            [item["entity_id"] for item in candidate_groups["outdoor_temperature"]],
        )
        self.assertEqual(
            ["binary_sensor.synthetic_window", "person.synthetic_ivan"],
            [item["entity_id"] for item in candidate_groups["presence"]],
        )
        self.assertEqual(
            ["binary_sensor.synthetic_window", "switch.synthetic_central"],
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
                "outdoor_temperature_entity_id": None,
                "presence_entity_id": None,
                "central_heating_entity_id": None,
                "heating_lockout_high": 15,
                "heating_lockout_low": 16,
            },
        ):
            with self.subTest(payload=payload):
                self.assertEqual(400, self._post(path, self._admin(), payload).status)

    def test_room_signals_get_update_and_clear(self) -> None:
        path = "/api/hausman_hub/v1/admin/climate-room-signals"
        self._inject_runtime()
        response = self._get(path, self._admin())
        self.assertEqual(200, response.status)
        self.assertEqual(
            [
                {"id": "living", "name": "Гостиная", "window_entity_id": None},
                {"id": "kids", "name": "Детская", "window_entity_id": None},
            ],
            response.payload["rooms"],
        )
        self.assertEqual(
            ["binary_sensor.synthetic_window"],
            [item["entity_id"] for item in response.payload["candidates"]],
        )

        bound = self._post(
            path,
            self._admin(),
            {"room_id": "living", "window_entity_id": "binary_sensor.synthetic_window"},
        )
        self.assertEqual(200, bound.status)
        rooms = {room["id"]: room for room in bound.payload["rooms"]}
        self.assertEqual(
            "binary_sensor.synthetic_window",
            rooms["living"]["window_entity_id"],
        )
        self.assertIsNone(rooms["kids"]["window_entity_id"])

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

        for payload in (
            {"room_id": "attic", "window_entity_id": None},
            {"room_id": "living", "window_entity_id": "sensor.synthetic_outdoor"},
            {"room_id": "living", "window_entity_id": "binary_sensor.missing"},
            {"room_id": "living"},
        ):
            with self.subTest(payload=payload):
                self.assertEqual(400, self._post(path, self._admin(), payload).status)


if __name__ == "__main__":
    unittest.main()
