"""Isolated tests for the authenticated local nine-count summary view."""

from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_MODULE = "custom_components.hausman_hub"
LOCAL_SUMMARY_MODULE = f"{PACKAGE_MODULE}.local_summary"
HOME_OBSERVATION_MODULE = f"{PACKAGE_MODULE}.home_observation"
FAKE_MODULE_NAMES = (
    "homeassistant",
    "homeassistant.auth",
    "homeassistant.auth.const",
    "homeassistant.components",
    "homeassistant.components.http",
    "homeassistant.const",
    "homeassistant.core",
    "homeassistant.helpers",
    "homeassistant.helpers.area_registry",
    "homeassistant.helpers.device_registry",
    "homeassistant.helpers.entity_registry",
    "homeassistant.helpers.start",
)


class FakeResponse:
    """Small stand-in for a Home Assistant JSON response."""

    def __init__(self, payload: object, status: int) -> None:
        self.payload = payload
        self.status = status


class FakeHomeAssistantView:
    """Expose only the JSON helpers used by the local summary view."""

    @staticmethod
    def json(payload: object, status_code: int = 200) -> FakeResponse:
        return FakeResponse(payload, int(status_code))

    def json_message(self, message: str, status_code: int = 200) -> FakeResponse:
        return self.json({"message": message}, status_code)


class FakeHttp:
    """Record registered views without starting an HTTP server."""

    def __init__(self) -> None:
        self.views: list[object] = []

    def register_view(self, view: object) -> None:
        self.views.append(view)


class FakeConfigEntries:
    """Record platform lifecycle requests without loading a platform."""

    def __init__(self) -> None:
        self.forwarded: list[tuple[object, tuple[object, ...]]] = []
        self.unloaded: list[tuple[object, tuple[object, ...]]] = []

    async def async_forward_entry_setups(
        self,
        entry: object,
        platforms: tuple[object, ...],
    ) -> None:
        self.forwarded.append((entry, platforms))

    async def async_unload_platforms(
        self,
        entry: object,
        platforms: tuple[object, ...],
    ) -> bool:
        self.unloaded.append((entry, platforms))
        return True


class FakeStates:
    """Store synthetic states and record removal of an HASC-owned state."""

    def __init__(self) -> None:
        self.values = {
            "sensor.synthetic_private_temperature": SimpleNamespace(state="21.5"),
            "switch.synthetic_private_light": SimpleNamespace(state="unavailable"),
            "sensor.synthetic_private_air": SimpleNamespace(state="unknown"),
            "switch.synthetic_private_disabled": SimpleNamespace(state="synthetic_active"),
        }
        self.removed: list[str] = []

    def get(self, entity_id: str) -> SimpleNamespace | None:
        return self.values.get(entity_id)

    def async_remove(self, entity_id: str) -> None:
        self.removed.append(entity_id)
        self.values.pop(entity_id, None)


class FakeEntityRegistry:
    """Expose only the registry lookup used by the HASC outer boundary."""

    def __init__(self) -> None:
        self.entities = {
            "synthetic-one": SimpleNamespace(
                domain="sensor",
                entity_id="sensor.synthetic_private_temperature",
                disabled_by=None,
            ),
            "synthetic-two": SimpleNamespace(
                domain="switch",
                entity_id="switch.synthetic_private_light",
                disabled_by=None,
            ),
            "synthetic-three": SimpleNamespace(
                domain="sensor",
                entity_id="sensor.synthetic_private_air",
                disabled_by=None,
            ),
            "synthetic-four": SimpleNamespace(
                domain="light",
                entity_id="light.synthetic_private_lamp",
                disabled_by=None,
            ),
            "synthetic-five": SimpleNamespace(
                domain="switch",
                entity_id="switch.synthetic_private_disabled",
                disabled_by="synthetic_configuration",
            ),
        }
        self.removed: list[str] = []

    def async_entries_for_config_entry(self, entry_id: str) -> list[object]:
        return [
            entity
            for entity in self.entities.values()
            if getattr(entity, "config_entry_id", None) == entry_id
        ]

    def async_remove(self, entity_id: str) -> None:
        self.removed.append(entity_id)
        for registry_id, entity in tuple(self.entities.items()):
            if entity.entity_id == entity_id:
                del self.entities[registry_id]
                return


class FakeHomeAssistant:
    """Minimal Home Assistant shape required by the local summary adapter."""

    def __init__(self) -> None:
        self.data: dict[str, dict[str, object]] = {}
        self.http = FakeHttp()
        self.config_entries = FakeConfigEntries()
        self.area_registry = SimpleNamespace(areas={"synthetic-area": object()})
        self.device_registry = SimpleNamespace(
            devices={"synthetic-device-one": object(), "synthetic-device-two": object()}
        )
        self.entity_registry = FakeEntityRegistry()
        self.states = FakeStates()


class FakeRequest(dict[str, object]):
    """Provide only the authenticated user and remote address to the view."""

    def __init__(self, remote: object, user: object) -> None:
        super().__init__(hass_user=user)
        self.remote = remote


class FakeRequestWithoutUser(dict[str, object]):
    """Model a request that reached the view without an authenticated user."""

    def __init__(self, remote: object) -> None:
        super().__init__()
        self.remote = remote


class FakeEntry:
    """Minimal config entry shape used by the safe outer adapter."""

    def __init__(self, data: dict[str, object], options: dict[str, object]) -> None:
        self.entry_id = "synthetic-hasc-entry"
        self.data = data
        self.options = options


def reader_user(*group_ids: str, admin: bool = False, system_generated: bool = False) -> object:
    """Return a synthetic authenticated user with explicit group membership."""

    return SimpleNamespace(
        is_admin=admin,
        system_generated=system_generated,
        groups=tuple(SimpleNamespace(id=group_id) for group_id in group_ids),
    )


def fake_home_assistant_modules() -> dict[str, ModuleType]:
    """Build the exact small Home Assistant import surface used by this adapter."""

    homeassistant = ModuleType("homeassistant")
    auth = ModuleType("homeassistant.auth")
    auth_const = ModuleType("homeassistant.auth.const")
    auth_const.GROUP_ID_READ_ONLY = "system-read-only"  # type: ignore[attr-defined]
    components = ModuleType("homeassistant.components")
    http = ModuleType("homeassistant.components.http")
    http.HomeAssistantView = FakeHomeAssistantView  # type: ignore[attr-defined]
    const = ModuleType("homeassistant.const")
    const.STATE_UNAVAILABLE = "unavailable"  # type: ignore[attr-defined]
    const.STATE_UNKNOWN = "unknown"  # type: ignore[attr-defined]
    const.Platform = SimpleNamespace(SENSOR="sensor")  # type: ignore[attr-defined]
    core = ModuleType("homeassistant.core")
    core.HomeAssistant = FakeHomeAssistant  # type: ignore[attr-defined]

    def callback(function: object) -> object:
        """Mark a synthetic callback as safe for the Home Assistant loop."""

        setattr(function, "_hass_callback", True)
        return function

    core.callback = callback  # type: ignore[attr-defined]
    helpers = ModuleType("homeassistant.helpers")
    area_registry = ModuleType("homeassistant.helpers.area_registry")
    area_registry.async_get = lambda hass: hass.area_registry  # type: ignore[attr-defined]
    device_registry = ModuleType("homeassistant.helpers.device_registry")
    device_registry.async_get = lambda hass: hass.device_registry  # type: ignore[attr-defined]
    entity_registry = ModuleType("homeassistant.helpers.entity_registry")
    entity_registry.async_get = lambda hass: hass.entity_registry  # type: ignore[attr-defined]
    entity_registry.async_entries_for_config_entry = (  # type: ignore[attr-defined]
        lambda registry, entry_id: registry.async_entries_for_config_entry(entry_id)
    )
    start = ModuleType("homeassistant.helpers.start")

    def async_at_started(hass: object, startup_callback: object) -> None:
        """Require the same loop-safe callback contract as Home Assistant."""

        if not getattr(startup_callback, "_hass_callback", False):
            raise RuntimeError("Home Assistant startup callbacks must be loop-safe")
        startup_callback(hass)

    start.async_at_started = async_at_started  # type: ignore[attr-defined]

    homeassistant.auth = auth  # type: ignore[attr-defined]
    homeassistant.components = components  # type: ignore[attr-defined]
    homeassistant.const = const  # type: ignore[attr-defined]
    homeassistant.core = core  # type: ignore[attr-defined]
    homeassistant.helpers = helpers  # type: ignore[attr-defined]
    auth.const = auth_const  # type: ignore[attr-defined]
    components.http = http  # type: ignore[attr-defined]
    helpers.area_registry = area_registry  # type: ignore[attr-defined]
    helpers.device_registry = device_registry  # type: ignore[attr-defined]
    helpers.entity_registry = entity_registry  # type: ignore[attr-defined]
    helpers.start = start  # type: ignore[attr-defined]

    return {
        "homeassistant": homeassistant,
        "homeassistant.auth": auth,
        "homeassistant.auth.const": auth_const,
        "homeassistant.components": components,
        "homeassistant.components.http": http,
        "homeassistant.const": const,
        "homeassistant.core": core,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.area_registry": area_registry,
        "homeassistant.helpers.device_registry": device_registry,
        "homeassistant.helpers.entity_registry": entity_registry,
        "homeassistant.helpers.start": start,
    }


class LocalSummaryAccessTest(unittest.TestCase):
    """Prove the inbound adapter fails closed and returns counts only."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.original_sys_path = sys.path[:]
        sys.path.insert(0, str(ROOT))
        cls.previous_modules = {
            name: sys.modules.get(name)
            for name in (*FAKE_MODULE_NAMES, PACKAGE_MODULE, LOCAL_SUMMARY_MODULE, HOME_OBSERVATION_MODULE)
        }
        for name in (*FAKE_MODULE_NAMES, PACKAGE_MODULE, LOCAL_SUMMARY_MODULE, HOME_OBSERVATION_MODULE):
            sys.modules.pop(name, None)
        sys.modules.update(fake_home_assistant_modules())
        cls.integration = importlib.import_module(PACKAGE_MODULE)
        cls.adapter = importlib.import_module(LOCAL_SUMMARY_MODULE)

    @classmethod
    def tearDownClass(cls) -> None:
        for name in (*FAKE_MODULE_NAMES, PACKAGE_MODULE, LOCAL_SUMMARY_MODULE, HOME_OBSERVATION_MODULE):
            sys.modules.pop(name, None)
        sys.modules.update(
            {name: module for name, module in cls.previous_modules.items() if module is not None}
        )
        sys.path[:] = cls.original_sys_path

    def setUp(self) -> None:
        self.hass = FakeHomeAssistant()
        self.entry = FakeEntry(
            {
                "mode": "read-only",
                "direct_execution_status": "direct_execution_blocked",
            },
            {},
        )
        self.assertTrue(asyncio.run(self.integration.async_setup_entry(self.hass, self.entry)))
        self.view = self.hass.http.views[0]

    def test_view_returns_exactly_nine_counts_for_a_local_read_only_user(self) -> None:
        response = asyncio.run(
            self.view.get(FakeRequest("127.0.0.1", reader_user("system-read-only")))
        )

        self.assertEqual(200, response.status)
        self.assertEqual(
            {
                "areas_count",
                "devices_count",
                "entities_count",
                "sensors_count",
                "available_entities_count",
                "unavailable_entities_count",
                "unknown_entities_count",
                "not_reported_entities_count",
                "disabled_entities_count",
            },
            set(response.payload),
        )
        self.assertEqual(5, response.payload["entities_count"])
        self.assertEqual(1, response.payload["disabled_entities_count"])
        serialized = json.dumps(response.payload)
        for forbidden_value in ("synthetic_private", "21.5", "token", "command"):
            self.assertNotIn(forbidden_value, serialized)

    def test_view_rejects_admin_mixed_group_system_and_public_requests(self) -> None:
        rejected_requests = (
            FakeRequest("127.0.0.1", reader_user("system-admin", admin=True)),
            FakeRequest("127.0.0.1", reader_user("system-read-only", "system-users")),
            FakeRequest("127.0.0.1", reader_user("system-read-only", system_generated=True)),
            FakeRequest("8.8.8.8", reader_user("system-read-only")),
            FakeRequest("::ffff:8.8.8.8", reader_user("system-read-only")),
            FakeRequest(None, reader_user("system-read-only")),
            FakeRequestWithoutUser("127.0.0.1"),
        )

        for request in rejected_requests:
            with self.subTest(request=request):
                response = asyncio.run(self.view.get(request))
                self.assertEqual(403, response.status)
                self.assertEqual({"message"}, set(response.payload))

    def test_view_accepts_loopback_and_private_ipv6_origins(self) -> None:
        """Accept private IPv6 forms while rejecting public mapped addresses."""

        for remote in ("::1", "fd00::20", "::ffff:192.168.1.20"):
            with self.subTest(remote=remote):
                response = asyncio.run(
                    self.view.get(FakeRequest(remote, reader_user("system-read-only")))
                )
                self.assertEqual(200, response.status)

    def test_view_has_no_mutating_http_method_and_registers_once(self) -> None:
        self.assertFalse(hasattr(self.view, "post"))
        self.assertFalse(hasattr(self.view, "put"))
        self.assertFalse(hasattr(self.view, "patch"))
        self.assertFalse(hasattr(self.view, "delete"))

        next_entry = FakeEntry(dict(self.entry.data), {})
        self.assertTrue(asyncio.run(self.integration.async_setup_entry(self.hass, next_entry)))
        self.assertEqual(1, len(self.hass.http.views))
        self.assertEqual(
            [(self.entry, ("sensor",)), (next_entry, ("sensor",))],
            self.hass.config_entries.forwarded,
        )

    def test_view_fails_closed_when_entry_is_unsafe_or_unloaded(self) -> None:
        self.entry.data["direct_execution_status"] = "not_blocked"
        unsafe_response = asyncio.run(
            self.view.get(FakeRequest("192.168.1.20", reader_user("system-read-only")))
        )
        self.assertEqual(503, unsafe_response.status)

        self.entry.data["direct_execution_status"] = "direct_execution_blocked"
        asyncio.run(self.integration.async_unload_entry(self.hass, self.entry))
        unloaded_response = asyncio.run(
            self.view.get(FakeRequest("192.168.1.20", reader_user("system-read-only")))
        )
        self.assertEqual(503, unloaded_response.status)

    def test_unload_clears_only_hasc_owned_state_values(self) -> None:
        """Turning HASC off must not leave its old counts or touch another state."""

        hasc_state = "sensor.hausman_hub_hasc_entities_count"
        self.hass.entity_registry.entities["hasc-owned"] = SimpleNamespace(
            domain="sensor",
            entity_id=hasc_state,
            config_entry_id=self.entry.entry_id,
            disabled_by=None,
        )
        self.hass.states.values[hasc_state] = SimpleNamespace(state="7")

        self.assertTrue(asyncio.run(self.integration.async_unload_entry(self.hass, self.entry)))

        self.assertEqual([hasc_state], self.hass.states.removed)
        self.assertNotIn(hasc_state, self.hass.states.values)
        self.assertIn("hasc-owned", self.hass.entity_registry.entities)
        self.assertEqual([], self.hass.entity_registry.removed)
        self.assertIn("sensor.synthetic_private_temperature", self.hass.states.values)

    def test_setup_rejects_an_unsafe_entry_before_registering_the_view(self) -> None:
        """A rejected entry must not open even the local count-only path."""

        unsafe_hass = FakeHomeAssistant()
        unsafe_entry = FakeEntry(
            {
                "mode": "shadow",
                "direct_execution_status": "not_blocked",
            },
            {},
        )

        self.assertFalse(asyncio.run(self.integration.async_setup_entry(unsafe_hass, unsafe_entry)))
        self.assertEqual([], unsafe_hass.http.views)

    def test_setup_rejects_invalid_saved_configuration_before_loading(self) -> None:
        """Stored unsafe values must not open sensors, runtime data, or the page."""

        safe_data = {
            "mode": "read-only",
            "direct_execution_status": "direct_execution_blocked",
        }
        invalid_configurations = (
            ({**safe_data, "mode": "proxy"}, {}),
            ({**safe_data, "direct_execution_status": "allowed"}, {}),
            (
                {"direct_execution_status": "direct_execution_blocked"},
                {"mode": "shadow"},
            ),
            ({**safe_data, "synthetic_extra": "ignored"}, {}),
            (safe_data, {"mode": "proxy"}),
            (safe_data, {"mode": "read-only", "synthetic_extra": "ignored"}),
        )

        for data, options in invalid_configurations:
            with self.subTest(data=data, options=options):
                unsafe_hass = FakeHomeAssistant()
                unsafe_entry = FakeEntry(dict(data), dict(options))
                saved_hasc_state = "sensor.hausman_hub_hasc_entities_count"
                unsafe_hass.entity_registry.entities["saved-hasc"] = SimpleNamespace(
                    domain="sensor",
                    entity_id=saved_hasc_state,
                    config_entry_id=unsafe_entry.entry_id,
                    disabled_by=None,
                )
                unsafe_hass.states.values[saved_hasc_state] = SimpleNamespace(state="7")

                self.assertFalse(
                    asyncio.run(self.integration.async_setup_entry(unsafe_hass, unsafe_entry))
                )
                self.assertEqual({}, unsafe_hass.data)
                self.assertEqual([], unsafe_hass.http.views)
                self.assertEqual([], unsafe_hass.config_entries.forwarded)
                self.assertEqual([saved_hasc_state], unsafe_hass.states.removed)
                self.assertNotIn(saved_hasc_state, unsafe_hass.states.values)
                self.assertEqual([saved_hasc_state], unsafe_hass.entity_registry.removed)
                self.assertEqual(
                    [],
                    unsafe_hass.entity_registry.async_entries_for_config_entry(
                        unsafe_entry.entry_id
                    ),
                )
                self.assertIn("synthetic-one", unsafe_hass.entity_registry.entities)
                self.assertIn(
                    "sensor.synthetic_private_temperature",
                    unsafe_hass.states.values,
                )


if __name__ == "__main__":
    unittest.main()
