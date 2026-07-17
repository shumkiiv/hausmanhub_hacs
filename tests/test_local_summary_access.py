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

    def __init__(
        self,
        payload: object,
        status: int,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.payload = payload
        self.status = status
        self.headers = dict(headers or {})


class FakeHomeAssistantView:
    """Expose only the JSON helpers used by the local summary view."""

    @staticmethod
    def json(
        payload: object,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> FakeResponse:
        return FakeResponse(payload, int(status_code), headers)

    def json_message(
        self,
        message: str,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> FakeResponse:
        return self.json({"message": message}, status_code, headers)


class FakeHttp:
    """Record registered views without starting an HTTP server."""

    def __init__(self) -> None:
        self.views: list[object] = []

    def register_view(self, view: object) -> None:
        self.views.append(view)


class FakeConfigEntries:
    """Record platform lifecycle requests without loading a platform."""

    def __init__(self, unload_succeeds: bool = True) -> None:
        self.entries: list[object] = []
        self.loaded_entries: list[object] = []
        self.forwarded: list[tuple[object, tuple[object, ...]]] = []
        self.manager_unloads: list[str] = []
        self.reloaded: list[str] = []
        self.unloaded: list[tuple[object, tuple[object, ...]]] = []
        self.unload_succeeds = unload_succeeds

    def async_entries(self, domain: str) -> list[object]:
        """Return the synthetic saved entries for one integration domain."""

        return [entry for entry in self.entries if getattr(entry, "domain", None) == domain]

    def async_loaded_entries(self, domain: str) -> list[object]:
        """Return only the synthetic HASC displays that are still running."""

        return [
            entry
            for entry in self.loaded_entries
            if getattr(entry, "domain", None) == domain
        ]

    async def async_forward_entry_setups(
        self,
        entry: object,
        platforms: tuple[object, ...],
    ) -> None:
        self.forwarded.append((entry, platforms))
        if entry not in self.loaded_entries:
            self.loaded_entries.append(entry)

    async def async_unload(self, entry_id: str) -> bool:
        """Stop one running synthetic entry through the manager boundary."""

        self.manager_unloads.append(entry_id)
        if not self.unload_succeeds:
            return False
        self.loaded_entries = [
            entry for entry in self.loaded_entries if getattr(entry, "entry_id", None) != entry_id
        ]
        return True

    async def async_reload(self, entry_id: str) -> bool:
        """Record a reload request without starting a real Home Assistant."""

        self.reloaded.append(entry_id)
        return True

    async def async_unload_platforms(
        self,
        entry: object,
        platforms: tuple[object, ...],
    ) -> bool:
        self.unloaded.append((entry, platforms))
        return self.unload_succeeds


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

    def __init__(self, unload_succeeds: bool = True) -> None:
        self.data: dict[str, dict[str, object]] = {}
        self.http = FakeHttp()
        self.config_entries = FakeConfigEntries(unload_succeeds)
        self.area_registry = SimpleNamespace(areas={"synthetic-area": object()})
        self.device_registry = SimpleNamespace(
            devices={"synthetic-device-one": object(), "synthetic-device-two": object()}
        )
        self.entity_registry = FakeEntityRegistry()
        self.states = FakeStates()


class FakeRequest(dict[str, object]):
    """Provide the authenticated user, source address, and route shape to the view."""

    def __init__(
        self,
        remote: object,
        user: object,
        path: str = "/api/hausman_hub/local-summary",
        query_string: str = "",
    ) -> None:
        super().__init__(hass_user=user)
        self.remote = remote
        self.path = path
        self.query_string = query_string


class FakeRequestWithoutUser(dict[str, object]):
    """Model a request that reached the view without an authenticated user."""

    def __init__(self, remote: object) -> None:
        super().__init__()
        self.remote = remote
        self.path = "/api/hausman_hub/local-summary"
        self.query_string = ""


class FakeEntry:
    """Minimal config entry shape used by the safe outer adapter."""

    def __init__(
        self,
        data: dict[str, object],
        options: dict[str, object],
        entry_id: str = "synthetic-hasc-entry",
    ) -> None:
        self.entry_id = entry_id
        self.domain = "hausman_hub"
        self.data = data
        self.options = options
        self.update_listeners: list[object] = []
        self.unload_callbacks: list[object] = []

    def add_update_listener(self, listener: object) -> object:
        """Register one synthetic saved-setting listener."""

        self.update_listeners.append(listener)

        def remove_listener() -> None:
            self.update_listeners.remove(listener)

        return remove_listener

    def async_on_unload(self, callback: object) -> None:
        """Keep the cleanup callback until the synthetic unload succeeds."""

        self.unload_callbacks.append(callback)

    def process_unload_callbacks(self) -> None:
        """Run the callbacks that Home Assistant normally runs after unload."""

        while self.unload_callbacks:
            callback = self.unload_callbacks.pop()
            callback()


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
        self.hass.config_entries.entries = [self.entry]
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
        self.assertEqual("no-store", response.headers.get("Cache-Control"))
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
                self.assertEqual("no-store", response.headers.get("Cache-Control"))

    def test_view_rejects_disallowed_origins_before_reading_the_home(self) -> None:
        """Only ordinary home-network source ranges may read the summary."""

        original_collect_home_summary = self.adapter.collect_home_summary

        def fail_if_home_is_read(*_: object, **__: object) -> object:
            raise AssertionError("a disallowed local summary origin must not read the home")

        self.adapter.collect_home_summary = fail_if_home_is_read
        try:
            for remote in (
                "0.0.0.0",
                "::",
                "::2",
                "::ffff:0.0.0.0",
                "126.255.255.255",
                "128.0.0.0",
                "9.255.255.255",
                "11.0.0.0",
                "172.15.255.255",
                "172.32.0.0",
                "192.167.255.255",
                "192.169.0.0",
                "192.0.2.1",
                "198.51.100.1",
                "203.0.113.1",
                "169.254.1.1",
                "100.64.0.1",
                "fbff:ffff:ffff:ffff:ffff:ffff:ffff:ffff",
                "fe00::",
                "fe80::1",
                "2001:db8::1",
                "::ffff:126.255.255.255",
                "::ffff:128.0.0.0",
                "::ffff:9.255.255.255",
                "::ffff:11.0.0.0",
                "::ffff:192.0.2.1",
                "::ffff:172.15.255.255",
                "::ffff:172.32.0.0",
                "::ffff:192.167.255.255",
                "::ffff:192.169.0.0",
            ):
                with self.subTest(remote=remote):
                    response = asyncio.run(
                        self.view.get(FakeRequest(remote, reader_user("system-read-only")))
                    )
                    self.assertEqual(403, response.status)
                    self.assertEqual({"message"}, set(response.payload))
                    self.assertEqual("no-store", response.headers.get("Cache-Control"))
        finally:
            self.adapter.collect_home_summary = original_collect_home_summary

    def test_view_rejects_changed_path_or_query_before_reading_the_home(self) -> None:
        """Only the exact route without extra query data may read the summary."""

        original_collect_home_summary = self.adapter.collect_home_summary

        def fail_if_home_is_read(*_: object, **__: object) -> object:
            raise AssertionError("an alternate local summary target must not read the home")

        rejected_requests = (
            FakeRequest(
                "127.0.0.1",
                reader_user("system-read-only"),
                path="/api/hausman_hub/local-summary/",
            ),
            FakeRequest(
                "127.0.0.1",
                reader_user("system-read-only"),
                query_string="unexpected=1",
            ),
        )
        self.adapter.collect_home_summary = fail_if_home_is_read
        try:
            for request in rejected_requests:
                with self.subTest(request=request):
                    response = asyncio.run(self.view.get(request))
                    self.assertEqual(404, response.status)
                    self.assertEqual({"message"}, set(response.payload))
                    self.assertEqual("no-store", response.headers.get("Cache-Control"))
        finally:
            self.adapter.collect_home_summary = original_collect_home_summary

    def test_view_accepts_only_approved_home_network_origins(self) -> None:
        """Allow loopback, RFC 1918 IPv4, ULA IPv6, and their safe mappings."""

        for remote in (
            "127.0.0.0",
            "127.255.255.255",
            "10.0.0.0",
            "10.255.255.255",
            "172.16.0.0",
            "172.31.255.255",
            "192.168.0.0",
            "192.168.255.255",
            "::1",
            "fc00::",
            "fdff:ffff:ffff:ffff:ffff:ffff:ffff:ffff",
            "::ffff:127.0.0.0",
            "::ffff:127.255.255.255",
            "::ffff:10.0.0.0",
            "::ffff:10.255.255.255",
            "::ffff:172.16.0.0",
            "::ffff:172.31.255.255",
            "::ffff:192.168.0.0",
            "::ffff:192.168.255.255",
        ):
            with self.subTest(remote=remote):
                response = asyncio.run(
                    self.view.get(FakeRequest(remote, reader_user("system-read-only")))
                )
                self.assertEqual(200, response.status)

    def test_local_address_policy_uses_explicit_home_network_ranges(self) -> None:
        """The adapter must not treat every Python-private address as home-local."""

        source = Path(self.adapter.__file__).read_text(encoding="utf-8")

        self.assertIn('IPv4Network("10.0.0.0/8")', source)
        self.assertIn('IPv4Network("172.16.0.0/12")', source)
        self.assertIn('IPv4Network("192.168.0.0/16")', source)
        self.assertIn('IPv6Network("fc00::/7")', source)
        self.assertIn("address.ipv4_mapped", source)
        self.assertNotIn("address.is_private", source)

    def test_view_has_only_get_http_method_and_registers_once(self) -> None:
        """The local page must have one URL and no alternative request method."""

        self.assertEqual("/api/hausman_hub/local-summary", self.view.url)
        self.assertEqual((), self.view.extra_urls)
        for method in ("post", "put", "patch", "delete", "head", "options"):
            with self.subTest(method=method):
                self.assertFalse(hasattr(self.view, method))

        self.assertTrue(asyncio.run(self.integration.async_setup_entry(self.hass, self.entry)))
        self.assertEqual(1, len(self.hass.http.views))
        self.assertEqual(
            [(self.entry, ("sensor",)), (self.entry, ("sensor",))],
            self.hass.config_entries.forwarded,
        )

    def test_saved_setting_change_reloads_only_this_hasc_entry(self) -> None:
        """A saved setting must ask Home Assistant to reload only HASC."""

        self.assertEqual(1, len(self.entry.update_listeners))
        listener = self.entry.update_listeners[0]

        asyncio.run(listener(self.hass, self.entry))

        self.assertEqual([self.entry.entry_id], self.hass.config_entries.reloaded)

    def test_turning_off_the_optional_page_closes_it_before_the_reload(self) -> None:
        """An old page address cannot read while the saved choice takes effect."""

        self.entry.options = {"local_summary_enabled": False}
        listener = self.entry.update_listeners[0]

        asyncio.run(listener(self.hass, self.entry))

        self.assertEqual([self.entry.entry_id], self.hass.config_entries.reloaded)
        self.assertIsNone(
            self.hass.data[self.adapter.DOMAIN].get(self.adapter.DATA_ACTIVE_ENTRY)
        )
        response = asyncio.run(
            self.view.get(FakeRequest("127.0.0.1", reader_user("system-read-only")))
        )
        self.assertEqual(503, response.status)
        self.assertEqual({"message"}, set(response.payload))

    def test_closed_optional_page_request_does_not_read_the_home(self) -> None:
        """The page request remains closed even with a stale runtime pointer."""

        self.entry.options = {"local_summary_enabled": False}
        original_collect_home_summary = self.adapter.collect_home_summary

        def fail_if_home_is_read(*_: object, **__: object) -> object:
            raise AssertionError("a closed optional local page request must not read the home")

        self.adapter.collect_home_summary = fail_if_home_is_read
        try:
            response = asyncio.run(
                self.view.get(FakeRequest("127.0.0.1", reader_user("system-read-only")))
            )
        finally:
            self.adapter.collect_home_summary = original_collect_home_summary

        self.assertEqual(503, response.status)
        self.assertEqual({"message"}, set(response.payload))

    def test_view_fails_closed_when_entry_is_unsafe_or_unloaded(self) -> None:
        self.entry.data["direct_execution_status"] = "not_blocked"
        unsafe_response = asyncio.run(
            self.view.get(FakeRequest("192.168.1.20", reader_user("system-read-only")))
        )
        self.assertEqual(503, unsafe_response.status)
        self.assertEqual("no-store", unsafe_response.headers.get("Cache-Control"))

        self.entry.data["direct_execution_status"] = "direct_execution_blocked"
        asyncio.run(self.integration.async_unload_entry(self.hass, self.entry))
        unloaded_response = asyncio.run(
            self.view.get(FakeRequest("192.168.1.20", reader_user("system-read-only")))
        )
        self.assertEqual(503, unloaded_response.status)
        self.assertEqual("no-store", unloaded_response.headers.get("Cache-Control"))

    def test_view_does_not_read_home_before_rejecting_an_unsafe_entry(self) -> None:
        """A running view must reject unsafe saved data before the only home read."""

        original_collect_home_summary = self.adapter.collect_home_summary

        def fail_if_home_is_read(*_: object, **__: object) -> object:
            raise AssertionError("an unsafe local summary must not read the home")

        self.adapter.collect_home_summary = fail_if_home_is_read
        try:
            self.entry.data["direct_execution_status"] = "not_blocked"
            response = asyncio.run(
                self.view.get(FakeRequest("192.168.1.20", reader_user("system-read-only")))
            )
        finally:
            self.adapter.collect_home_summary = original_collect_home_summary

        self.assertEqual(503, response.status)
        self.assertEqual({"message"}, set(response.payload))

    def test_view_fails_closed_when_the_home_summary_reader_raises(self) -> None:
        """An unexpected local observation failure must reveal no error details."""

        original_collect_home_summary = self.adapter.collect_home_summary

        def fail_home_summary_reader(*_: object, **__: object) -> object:
            raise RuntimeError("synthetic home summary reader failure")

        self.adapter.collect_home_summary = fail_home_summary_reader
        try:
            response = asyncio.run(
                self.view.get(FakeRequest("127.0.0.1", reader_user("system-read-only")))
            )
        finally:
            self.adapter.collect_home_summary = original_collect_home_summary

        self.assertEqual(503, response.status)
        self.assertEqual({"message": "The local summary is unavailable."}, response.payload)
        self.assertEqual("no-store", response.headers.get("Cache-Control"))
        self.assertNotIn("synthetic", json.dumps(response.payload))

    def test_view_does_not_swallow_cancelled_home_summary_read(self) -> None:
        """Cancellation must remain visible to Home Assistant's async framework."""

        original_collect_home_summary = self.adapter.collect_home_summary

        def cancel_home_summary_reader(*_: object, **__: object) -> object:
            raise asyncio.CancelledError

        self.adapter.collect_home_summary = cancel_home_summary_reader
        try:
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(
                    self.view.get(
                        FakeRequest("127.0.0.1", reader_user("system-read-only"))
                    )
                )
        finally:
            self.adapter.collect_home_summary = original_collect_home_summary

    def test_view_does_not_read_home_when_a_stale_pointer_outlives_hasc(self) -> None:
        """A retained runtime pointer must not outlive the loaded HASC entry."""

        self.assertEqual(
            [self.entry],
            self.hass.config_entries.async_loaded_entries(self.entry.domain),
        )
        self.assertIs(
            self.entry,
            self.hass.data[self.adapter.DOMAIN][self.adapter.DATA_ACTIVE_ENTRY],
        )
        self.hass.config_entries.loaded_entries.clear()

        original_collect_home_summary = self.adapter.collect_home_summary

        def fail_if_home_is_read(*_: object, **__: object) -> object:
            raise AssertionError("a stale local summary pointer must not read the home")

        self.adapter.collect_home_summary = fail_if_home_is_read
        try:
            response = asyncio.run(
                self.view.get(FakeRequest("192.168.1.20", reader_user("system-read-only")))
            )
        finally:
            self.adapter.collect_home_summary = original_collect_home_summary

        self.assertEqual(503, response.status)
        self.assertEqual({"message"}, set(response.payload))

    def test_view_fails_closed_if_a_second_saved_hasc_entry_appears(self) -> None:
        """The retained view must not leak counts during a corrupt live pair."""

        self.hass.config_entries.entries.append(
            FakeEntry(
                {
                    "mode": "shadow",
                    "direct_execution_status": "direct_execution_blocked",
                },
                {},
                "synthetic-hasc-second",
            )
        )

        response = asyncio.run(
            self.view.get(FakeRequest("127.0.0.1", reader_user("system-read-only")))
        )

        self.assertEqual(503, response.status)
        self.assertEqual({"message"}, set(response.payload))

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
        self.assertEqual(1, len(self.entry.update_listeners))

        self.entry.process_unload_callbacks()

        self.assertEqual([], self.entry.update_listeners)

    def test_failed_unload_keeps_the_current_hasc_state_and_page(self) -> None:
        """A failed unload must not leave a half-cleared HASC display behind."""

        failed_hass = FakeHomeAssistant(unload_succeeds=False)
        failed_entry = FakeEntry(
            {
                "mode": "read-only",
                "direct_execution_status": "direct_execution_blocked",
            },
            {},
        )
        failed_hass.config_entries.entries = [failed_entry]
        self.assertTrue(asyncio.run(self.integration.async_setup_entry(failed_hass, failed_entry)))

        hasc_state = "sensor.hausman_hub_hasc_entities_count"
        failed_hass.entity_registry.entities["hasc-owned"] = SimpleNamespace(
            domain="sensor",
            entity_id=hasc_state,
            config_entry_id=failed_entry.entry_id,
            disabled_by=None,
        )
        failed_hass.states.values[hasc_state] = SimpleNamespace(state="7")

        self.assertFalse(
            asyncio.run(self.integration.async_unload_entry(failed_hass, failed_entry))
        )

        self.assertEqual([], failed_hass.states.removed)
        self.assertIn(hasc_state, failed_hass.states.values)
        self.assertIn("hasc-owned", failed_hass.entity_registry.entities)
        self.assertEqual([], failed_hass.entity_registry.removed)
        self.assertEqual(1, len(failed_entry.update_listeners))
        response = asyncio.run(
            failed_hass.http.views[0].get(
                FakeRequest("127.0.0.1", reader_user("system-read-only"))
            )
        )
        self.assertEqual(200, response.status)

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
        unsafe_hass.config_entries.entries = [unsafe_entry]

        self.assertFalse(asyncio.run(self.integration.async_setup_entry(unsafe_hass, unsafe_entry)))
        self.assertEqual([], unsafe_hass.http.views)

    def test_setup_with_the_optional_page_closed_keeps_only_the_count_display(self) -> None:
        """Closing the page must not remove the nine safe HASC count sensors."""

        closed_hass = FakeHomeAssistant()
        closed_entry = FakeEntry(
            {
                "mode": "read-only",
                "direct_execution_status": "direct_execution_blocked",
            },
            {"local_summary_enabled": False},
        )
        closed_hass.config_entries.entries = [closed_entry]

        self.assertTrue(asyncio.run(self.integration.async_setup_entry(closed_hass, closed_entry)))

        self.assertEqual([(closed_entry, ("sensor",))], closed_hass.config_entries.forwarded)
        self.assertEqual([], closed_hass.http.views)
        self.assertEqual({}, closed_hass.data)
        self.assertEqual(1, len(closed_entry.update_listeners))

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
                unsafe_hass.config_entries.entries = [unsafe_entry]
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

    def test_setup_rejects_multiple_saved_entries_and_clears_only_their_records(self) -> None:
        """A corrupt pair of saved HASC entries must not expose either display."""

        safe_data = {
            "mode": "read-only",
            "direct_execution_status": "direct_execution_blocked",
        }
        first_entry = FakeEntry(dict(safe_data), {}, "synthetic-hasc-first")
        second_entry = FakeEntry(dict(safe_data), {}, "synthetic-hasc-second")
        duplicate_hass = FakeHomeAssistant()
        duplicate_hass.config_entries.entries = [first_entry, second_entry]
        first_state = "sensor.hausman_hub_hasc_first_saved_count"
        second_state = "sensor.hausman_hub_hasc_second_saved_count"
        duplicate_hass.entity_registry.entities["first-saved"] = SimpleNamespace(
            domain="sensor",
            entity_id=first_state,
            config_entry_id=first_entry.entry_id,
            disabled_by=None,
        )
        duplicate_hass.entity_registry.entities["second-saved"] = SimpleNamespace(
            domain="sensor",
            entity_id=second_state,
            config_entry_id=second_entry.entry_id,
            disabled_by="synthetic_configuration",
        )
        duplicate_hass.states.values[first_state] = SimpleNamespace(state="7")
        duplicate_hass.states.values[second_state] = SimpleNamespace(state="3")

        self.assertFalse(
            asyncio.run(self.integration.async_setup_entry(duplicate_hass, first_entry))
        )
        self.assertFalse(
            asyncio.run(self.integration.async_setup_entry(duplicate_hass, second_entry))
        )

        self.assertEqual([], duplicate_hass.http.views)
        self.assertEqual([], duplicate_hass.config_entries.forwarded)
        self.assertEqual(
            [first_entry, second_entry],
            duplicate_hass.config_entries.entries,
        )
        self.assertEqual([first_state, second_state], duplicate_hass.states.removed)
        self.assertNotIn(first_state, duplicate_hass.states.values)
        self.assertNotIn(second_state, duplicate_hass.states.values)
        self.assertEqual(
            [first_state, second_state],
            duplicate_hass.entity_registry.removed,
        )
        self.assertIn("synthetic-one", duplicate_hass.entity_registry.entities)
        self.assertIn(
            "sensor.synthetic_private_temperature",
            duplicate_hass.states.values,
        )

    def test_second_saved_entry_closes_an_already_running_hasc_display(self) -> None:
        """A live corrupt pair must close the existing display before cleanup."""

        first_state = "sensor.hausman_hub_hasc_first_running_count"
        self.hass.entity_registry.entities["first-running"] = SimpleNamespace(
            domain="sensor",
            entity_id=first_state,
            config_entry_id=self.entry.entry_id,
            disabled_by=None,
        )
        self.hass.states.values[first_state] = SimpleNamespace(state="7")
        second_entry = FakeEntry(
            {
                "mode": "read-only",
                "direct_execution_status": "direct_execution_blocked",
            },
            {},
            "synthetic-hasc-second",
        )
        self.hass.is_running = True
        self.hass.config_entries.entries.append(second_entry)

        self.assertFalse(
            asyncio.run(self.integration.async_setup_entry(self.hass, second_entry))
        )

        self.assertEqual([self.entry.entry_id], self.hass.config_entries.manager_unloads)
        self.assertEqual([], self.hass.config_entries.loaded_entries)
        self.assertEqual([first_state], self.hass.states.removed)
        self.assertNotIn(first_state, self.hass.states.values)
        self.assertEqual([first_state], self.hass.entity_registry.removed)
        self.assertIn("synthetic-one", self.hass.entity_registry.entities)
        response = asyncio.run(
            self.view.get(FakeRequest("127.0.0.1", reader_user("system-read-only")))
        )
        self.assertEqual(503, response.status)
        self.assertEqual({"message"}, set(response.payload))


if __name__ == "__main__":
    unittest.main()
