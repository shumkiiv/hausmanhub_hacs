"""Isolated tests for the authenticated local nine-count summary view."""

from __future__ import annotations

import asyncio
import copy
import importlib
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

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
    "homeassistant.components.frontend",
    "homeassistant.components.panel_custom",
    "homeassistant.const",
    "homeassistant.core",
    "homeassistant.helpers",
    "homeassistant.helpers.area_registry",
    "homeassistant.helpers.device_registry",
    "homeassistant.helpers.entity_registry",
    "homeassistant.helpers.event",
    "homeassistant.helpers.start",
    "homeassistant.helpers.storage",
    "homeassistant.util",
    "homeassistant.util.dt",
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
        self.static_paths: list[object] = []

    def register_view(self, view: object) -> None:
        self.views.append(view)

    async def async_register_static_paths(self, configs: list[object]) -> None:
        self.static_paths.extend(configs)


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
        self.updated: list[tuple[object, dict[str, object] | None]] = []

    def async_update_entry(
        self,
        entry: object,
        *,
        data: dict[str, object] | None = None,
        options: dict[str, object] | None = None,
        **_: object,
    ) -> None:
        self.updated.append((entry, data))
        if data is not None:
            entry.data = data
        if options is not None:
            entry.options = options

    def async_entries(self, domain: str) -> list[object]:
        """Return the synthetic saved entries for one integration domain."""

        return [entry for entry in self.entries if getattr(entry, "domain", None) == domain]

    def async_loaded_entries(self, domain: str) -> list[object]:
        """Return only the synthetic HausmanHub displays that are still running."""

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
    """Store synthetic states and record removal of an HausmanHub-owned state."""

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

    def async_all(self) -> list[SimpleNamespace]:
        """The native binding catalogue sees no synthetic HA entities here."""

        return []

    def async_remove(self, entity_id: str) -> None:
        self.removed.append(entity_id)
        self.values.pop(entity_id, None)


class FakeEntityRegistry:
    """Expose only the registry lookup used by the HausmanHub outer boundary."""

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
        self.executor_jobs: list[tuple[object, tuple[object, ...]]] = []
        self.http = FakeHttp()
        self.config_entries = FakeConfigEntries(unload_succeeds)
        self.area_registry = SimpleNamespace(areas={"synthetic-area": object()})
        self.device_registry = SimpleNamespace(
            devices={"synthetic-device-one": object(), "synthetic-device-two": object()}
        )
        self.entity_registry = FakeEntityRegistry()
        self.states = FakeStates()

    async def async_add_executor_job(self, target, *args):
        """Run a blocking helper outside the synthetic event-loop boundary."""

        self.executor_jobs.append((target, args))
        return target(*args)


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


class FakeJsonRequest(FakeRequest):
    """Add the bounded JSON request surface used by climate POST routes."""

    def __init__(
        self,
        remote: object,
        user: object,
        path: str,
        payload: object,
        *,
        content_type: str = "application/json",
        accept: str | None = None,
        raw_body: bytes | None = None,
    ) -> None:
        super().__init__(remote, user, path=path)
        self._payload = payload
        self._raw_body = raw_body or json.dumps(payload).encode("utf-8")
        self.content_type = content_type
        self.content_length = len(self._raw_body)
        self.headers = {} if accept is None else {"Accept": accept}

    async def json(self) -> object:
        return self._payload

    async def read(self) -> bytes:
        return self._raw_body


class FakeEntry:
    """Minimal config entry shape used by the safe outer adapter."""

    def __init__(
        self,
        data: dict[str, object],
        options: dict[str, object],
        entry_id: str = "synthetic-hausmanhub-entry",
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

    class FakeStaticPathConfig:
        def __init__(self, url_path: str, path: str, cache_headers: bool) -> None:
            self.url_path = url_path
            self.path = path
            self.cache_headers = cache_headers

    http.StaticPathConfig = FakeStaticPathConfig  # type: ignore[attr-defined]
    frontend = ModuleType("homeassistant.components.frontend")
    frontend.async_remove_panel = lambda hass, url_path, *, warn_if_unknown=True: None  # type: ignore[attr-defined]
    frontend.async_panel_exists = lambda hass, url_path: False  # type: ignore[attr-defined]
    panel_custom = ModuleType("homeassistant.components.panel_custom")
    async def async_register_panel(hass, **kwargs):
        return None

    panel_custom.async_register_panel = async_register_panel  # type: ignore[attr-defined]
    const = ModuleType("homeassistant.const")
    const.STATE_UNAVAILABLE = "unavailable"  # type: ignore[attr-defined]
    const.STATE_UNKNOWN = "unknown"  # type: ignore[attr-defined]
    const.Platform = SimpleNamespace(SENSOR="sensor", SWITCH="switch")  # type: ignore[attr-defined]
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
    event = ModuleType("homeassistant.helpers.event")

    def async_track_time_interval(
        hass: object,
        action: object,
        interval: object,
    ) -> object:
        """Record no timer activity while returning the normal cancel callback."""

        del hass, action, interval
        return lambda: None

    event.async_track_time_interval = async_track_time_interval  # type: ignore[attr-defined]

    def async_track_time_change(
        hass: object,
        action: object,
        **time_match: object,
    ) -> object:
        """Record no clock activity while returning the normal cancel callback."""

        del hass, action, time_match
        return lambda: None

    event.async_track_time_change = async_track_time_change  # type: ignore[attr-defined]
    start = ModuleType("homeassistant.helpers.start")

    def async_at_started(hass: object, startup_callback: object) -> None:
        """Require the same loop-safe callback contract as Home Assistant."""

        if not getattr(startup_callback, "_hass_callback", False):
            raise RuntimeError("Home Assistant startup callbacks must be loop-safe")
        startup_callback(hass)

    start.async_at_started = async_at_started  # type: ignore[attr-defined]
    storage = ModuleType("homeassistant.helpers.storage")

    class FakeStore:
        """Keep the newly added disabled climate registry empty in memory."""

        def __class_getitem__(cls, _: object) -> type:
            return cls

        def __init__(
            self,
            hass: object,
            version: int,
            key: str,
            *,
            max_readable_version: int | None = None,
            atomic_writes: bool = False,
        ) -> None:
            self.hass = hass
            self.version = version
            self.key = key
            self.max_readable_version = max_readable_version
            self.atomic_writes = atomic_writes
            self.path = str(Path(tempfile.mkdtemp()) / key)

        async def async_load(self) -> object | None:
            path = Path(self.path)
            if not path.is_file():
                return None
            return json.loads(path.read_text(encoding="utf-8"))["data"]

        async def async_save(self, payload: object) -> None:
            Path(self.path).write_text(
                json.dumps(
                    {
                        "version": self.version,
                        "minor_version": 1,
                        "key": self.key,
                        "data": payload,
                    }
                ),
                encoding="utf-8",
            )

    storage.Store = FakeStore  # type: ignore[attr-defined]
    util = ModuleType("homeassistant.util")
    dt = ModuleType("homeassistant.util.dt")
    dt.now = lambda: datetime(2026, 7, 19, 12, 0)  # type: ignore[attr-defined]
    dt.parse_datetime = (  # type: ignore[attr-defined]
        lambda value: datetime.fromisoformat(value) if value else None
    )

    homeassistant.auth = auth  # type: ignore[attr-defined]
    homeassistant.components = components  # type: ignore[attr-defined]
    homeassistant.const = const  # type: ignore[attr-defined]
    homeassistant.core = core  # type: ignore[attr-defined]
    homeassistant.helpers = helpers  # type: ignore[attr-defined]
    auth.const = auth_const  # type: ignore[attr-defined]
    components.http = http  # type: ignore[attr-defined]
    components.frontend = frontend  # type: ignore[attr-defined]
    components.panel_custom = panel_custom  # type: ignore[attr-defined]
    helpers.area_registry = area_registry  # type: ignore[attr-defined]
    helpers.device_registry = device_registry  # type: ignore[attr-defined]
    helpers.entity_registry = entity_registry  # type: ignore[attr-defined]
    helpers.event = event  # type: ignore[attr-defined]
    helpers.start = start  # type: ignore[attr-defined]
    helpers.storage = storage  # type: ignore[attr-defined]
    homeassistant.util = util  # type: ignore[attr-defined]
    util.dt = dt  # type: ignore[attr-defined]

    return {
        "homeassistant": homeassistant,
        "homeassistant.auth": auth,
        "homeassistant.auth.const": auth_const,
        "homeassistant.components": components,
        "homeassistant.components.http": http,
        "homeassistant.components.frontend": frontend,
        "homeassistant.components.panel_custom": panel_custom,
        "homeassistant.const": const,
        "homeassistant.core": core,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.area_registry": area_registry,
        "homeassistant.helpers.device_registry": device_registry,
        "homeassistant.helpers.entity_registry": entity_registry,
        "homeassistant.helpers.event": event,
        "homeassistant.helpers.start": start,
        "homeassistant.helpers.storage": storage,
        "homeassistant.util": util,
        "homeassistant.util.dt": dt,
    }


class _ManagedRecipeStore:
    def __init__(self, value: object) -> None:
        self._value = value

    async def async_load(self):
        return self._value

    async def async_save(self, value):
        return None


class _ManagedRecipeBridge:
    def __init__(self, source: dict) -> None:
        self._source = source
        self.executed = []

    async def async_fetch_state(self):
        from tests.climate_bridge_fixture import (
            import_climate_state,
        )

        return import_climate_state(self._source)

    async def async_execute(self, plan):
        self.executed.append(plan)
        room = self._source["rooms"][0]
        if plan.action == "set_room_target_strategy":
            room["targets"]["targetStrategy"] = plan.backend_payload[
                "targetStrategy"
            ]
        elif plan.action == "set_room_target":
            room["targets"]["temperature"] = plan.backend_payload[
                "targetTemperature"
            ]
        elif plan.action == "set_room_mode":
            room["mode"] = plan.backend_payload["mode"]
        return {"ok": True}


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

    def assert_climate_route_payload_redacted(self, payload: object) -> None:
        serialized = json.dumps(payload, ensure_ascii=True, sort_keys=True)
        for forbidden in (
            '"entity_id"',
            '"entityId"',
            '"source_id"',
            '"sourceId"',
            '"service"',
            '"services"',
            '"call"',
            '"calls"',
            '"backend_payload"',
            '"backendPayload"',
            "synthetic-ac-source-living",
            "climate.synthetic_living_ac",
            "127.0.0.1:1880",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, serialized)

    def test_setup_resets_stale_history_when_keyring_is_replaced_after_marker(self) -> None:
        """The ConfigEntry marker cannot authorize records under a new keyring."""

        storage_module = importlib.import_module(
            "custom_components.hausman_hub.climate_operation_storage"
        )

        class StaticStore:
            backing: dict[str, object] = {
                "hausman_hub.climate_operations.synthetic-hausmanhub-entry": {
                    "version": 6,
                    "records": [{"forged": True}],
                    "recoveries": [{"forged": True}],
                    "control_revision": 77,
                    "desired_intents": {},
                    "direct_control_records": [{"forged": True}],
                },
                "hausman_hub.climate_operation_scopes.synthetic-hausmanhub-entry": {
                    "__tablet_state__": {"forged": True},
                    "forged-request": {"forged": True},
                },
            }

            def __class_getitem__(cls, _: object) -> type:
                return cls

            def __init__(self, hass: object, version: int, key: str, **_: object) -> None:
                self.key = key

            async def async_load(self) -> object | None:
                return self.backing.get(self.key)

            async def async_save(self, payload: object) -> None:
                self.backing[self.key] = payload

        with tempfile.TemporaryDirectory() as directory:
            keyring_path = Path(directory) / "replacement-keyring.json"
            keyring_path.write_text(
                json.dumps({"active_key_id": "replacement", "keys": {"replacement": "a" * 64}}),
                encoding="utf-8",
            )
            keyring_path.chmod(0o600)
            replacement_hass = FakeHomeAssistant()
            replacement_entry = FakeEntry(
                {
                    "mode": "read-only",
                    "direct_execution_status": "direct_execution_blocked",
                    "reliable_scope_external_keyring_initialized": True,
                    "reliable_scope_integrity_key": "do-not-read",
                },
                {},
            )
            replacement_hass.config_entries.entries = [replacement_entry]
            with patch.object(storage_module, "Store", StaticStore), patch.dict(
                os.environ,
                {"HAUSMAN_HUB_CLIMATE_LEDGER_KEYRING_PATH": str(keyring_path)},
            ):
                self.assertTrue(
                    asyncio.run(
                        self.integration.async_setup_entry(
                            replacement_hass, replacement_entry
                        )
                    )
                )

            main = StaticStore.backing[
                "hausman_hub.climate_operations.synthetic-hausmanhub-entry"
            ]
            self.assertEqual("hausman_climate_ledger_auth_v1", main["format"])
            self.assertEqual(0, main["payload"]["control_revision"])
            self.assertEqual([], main["payload"]["records"])
            self.assertEqual([], main["payload"]["direct_control_records"])
            self.assertNotIn("reliable_scope_integrity_key", replacement_entry.data)
            self.assertNotIn("reliable_scope_integrity_initialized", replacement_entry.data)
            self.assertTrue(
                replacement_entry.data["reliable_scope_external_keyring_initialized"]
            )
            self.assertEqual(
                {"__storage_state__"},
                set(StaticStore.backing[
                    "hausman_hub.climate_operation_scopes.synthetic-hausmanhub-entry"
                ]["payload"]),
            )
            self.assertIn(
                "synthetic-hausmanhub-entry",
                json.loads(keyring_path.read_text(encoding="utf-8"))["ledger_anchors"],
            )

    def test_setup_scrubs_retired_fields_without_a_usable_keyring(self) -> None:
        """Cleanup cannot depend on external keyring availability."""

        def entry_with_retired_fields() -> FakeEntry:
            return FakeEntry(
                {
                    "mode": "read-only",
                    "direct_execution_status": "direct_execution_blocked",
                    "reliable_scope_integrity_key": "legacy-secret",
                    "reliable_scope_integrity_initialized": True,
                },
                {
                    "reliable_scope_integrity_key": "legacy-option-secret",
                    "reliable_scope_integrity_initialized": True,
                },
            )

        with tempfile.TemporaryDirectory() as directory:
            unreadable = Path(directory) / "unreadable-keyring.json"
            unreadable.write_text(
                json.dumps({"active_key_id": "k1", "keys": {"k1": "a" * 64}}),
                encoding="utf-8",
            )
            unreadable.chmod(0o640)
            environments = ({}, {"HAUSMAN_HUB_CLIMATE_LEDGER_KEYRING_PATH": str(unreadable)})
            for environment in environments:
                with self.subTest(environment=environment):
                    hass = FakeHomeAssistant()
                    entry = entry_with_retired_fields()
                    hass.config_entries.entries = [entry]
                    with patch.dict(os.environ, environment, clear=True):
                        self.assertTrue(asyncio.run(self.integration.async_setup_entry(hass, entry)))
                    for values in (entry.data, entry.options):
                        self.assertNotIn("reliable_scope_integrity_key", values)
                        self.assertNotIn("reliable_scope_integrity_initialized", values)

    def test_setup_scrubs_retired_fields_before_configuration_validation_failure(self) -> None:
        """A rejected entry cannot retain an old local secret."""

        hass = FakeHomeAssistant()
        entry = FakeEntry(
            {
                "mode": "read-only",
                "direct_execution_status": "not_blocked",
                "reliable_scope_integrity_key": "legacy-secret",
                "reliable_scope_integrity_initialized": True,
            },
            {
                "reliable_scope_integrity_key": "legacy-option-secret",
                "reliable_scope_integrity_initialized": True,
            },
        )
        hass.config_entries.entries = [entry]
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(asyncio.run(self.integration.async_setup_entry(hass, entry)))
        for values in (entry.data, entry.options):
            self.assertNotIn("reliable_scope_integrity_key", values)
            self.assertNotIn("reliable_scope_integrity_initialized", values)

    def test_duplicate_setup_scrubs_retired_fields_before_rejecting_both_entries(self) -> None:
        """The duplicate guard stays closed without retaining old secrets."""

        def duplicate(entry_id: str) -> FakeEntry:
            return FakeEntry(
                {
                    "mode": "read-only",
                    "direct_execution_status": "direct_execution_blocked",
                    "reliable_scope_integrity_key": f"legacy-{entry_id}",
                    "reliable_scope_integrity_initialized": True,
                },
                {
                    "reliable_scope_integrity_key": f"legacy-option-{entry_id}",
                    "reliable_scope_integrity_initialized": True,
                },
                entry_id,
            )

        hass = FakeHomeAssistant()
        first, second = duplicate("first"), duplicate("second")
        hass.config_entries.entries = [first, second]
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(asyncio.run(self.integration.async_setup_entry(hass, first)))
            self.assertFalse(asyncio.run(self.integration.async_setup_entry(hass, second)))
        self.assertEqual([], hass.config_entries.forwarded)
        for entry in (first, second):
            for values in (entry.data, entry.options):
                self.assertNotIn("reliable_scope_integrity_key", values)
                self.assertNotIn("reliable_scope_integrity_initialized", values)

    def test_tablet_publishes_local_power_status_without_physical_commands(self) -> None:
        path = "/api/hausman_hub/v1/tablet-power-status"
        view = next(item for item in self.hass.http.views if item.url == path)
        payload = {
            "contract": {
                "name": "hausman-hub-tablet-power-status-request",
                "version": 1,
            },
            "correlationId": "tablet-power-test-39",
            "tabletId": "hall-tablet",
            "batteryPercent": 39,
            "charging": False,
            "powerSource": "battery",
            "batteryTemperatureC": 31.5,
            "reportedAt": time.time_ns() // 1_000_000,
        }

        response = asyncio.run(
            view.post(
                FakeJsonRequest(
                    "192.168.1.20",
                    reader_user("system-users"),
                    path,
                    payload,
                )
            )
        )

        self.assertEqual(200, response.status)
        self.assertEqual("turn_on", response.payload["chargingPolicy"])
        self.assertFalse(response.payload["physicalCommandsSent"])
        self.assertEqual("no-store", response.headers.get("Cache-Control"))
        status = self.hass.data["hausman_hub"]["tablet_power_service"].status
        self.assertEqual(39, status.battery_percent)
        journal = self.hass.data["hausman_hub"]["operation_journal"]
        records = journal.snapshot(correlation_id="tablet-power-test-39")["records"]
        self.assertEqual("tablet_power_update", records[0]["operation"])
        self.assertTrue(records[0]["confirmed"])

        remote = asyncio.run(
            view.post(
                FakeJsonRequest(
                    "203.0.113.7",
                    reader_user("system-users"),
                    path,
                    payload,
                )
            )
        )
        self.assertEqual(403, remote.status)
        malformed = asyncio.run(
            view.post(
                FakeJsonRequest(
                    "192.168.1.20",
                    reader_user("system-users"),
                    path,
                    {**payload, "private": "must-not-pass"},
                )
            )
        )
        self.assertEqual(400, malformed.status)

    def test_legacy_settings_preview_is_local_admin_only_and_read_only(self) -> None:
        path = "/api/hausman_hub/v1/admin/legacy-settings/preview"
        view = next(item for item in self.hass.http.views if item.url == path)
        payload = {
            "contract": {
                "name": "hausman-hub-legacy-settings-export",
                "version": 1,
            },
            "globals": {
                "home_target_temp": 25,
                "ac_pause_until": 123,
                "max_alert_user_ids": [12345],
            },
        }
        original_data = copy.deepcopy(self.entry.data)
        original_options = copy.deepcopy(self.entry.options)

        response = asyncio.run(
            view.post(
                FakeJsonRequest(
                    "127.0.0.1",
                    reader_user(admin=True),
                    path,
                    payload,
                )
            )
        )

        self.assertEqual(200, response.status)
        self.assertFalse(response.payload["write_performed"])
        self.assertEqual("no-store", response.headers["Cache-Control"])
        self.assertEqual(original_data, self.entry.data)
        self.assertEqual(original_options, self.entry.options)
        self.assertEqual(
            ["max_alert_user_ids"],
            response.payload["rejected_sensitive"],
        )

        for remote, user in (
            ("203.0.113.7", reader_user(admin=True)),
            ("127.0.0.1", reader_user("system-read-only")),
        ):
            with self.subTest(remote=remote, user=user):
                forbidden = asyncio.run(
                    view.post(FakeJsonRequest(remote, user, path, payload))
                )
                self.assertEqual(403, forbidden.status)

    def test_legacy_settings_preview_rejects_an_invalid_export(self) -> None:
        path = "/api/hausman_hub/v1/admin/legacy-settings/preview"
        view = next(item for item in self.hass.http.views if item.url == path)
        response = asyncio.run(
            view.post(
                FakeJsonRequest(
                    "127.0.0.1",
                    reader_user(admin=True),
                    path,
                    {"globals": {"home_target_temp": 25}},
                )
            )
        )

        self.assertEqual(400, response.status)
        self.assertEqual({"message"}, set(response.payload))

    def test_legacy_settings_apply_is_local_admin_only_and_rechecks_preview(self) -> None:
        path = "/api/hausman_hub/v1/admin/legacy-settings/apply"
        view = next(item for item in self.hass.http.views if item.url == path)
        payload = {
            "contract": {
                "name": "hausman-hub-legacy-settings-apply",
                "version": 1,
            },
            "preview_id": "0123456789abcdef",
            "confirm": True,
            "export": {
                "contract": {
                    "name": "hausman-hub-legacy-settings-export",
                    "version": 1,
                },
                "globals": {"home_target_temp": 25},
            },
            "room_mappings": [],
        }
        settings_service = self.hass.data["hausman_hub"]["settings_service"]
        climate_runtime = self.hass.data["hausman_hub"]["climate_runtime"]
        original_settings = settings_service.current
        original_contours = climate_runtime._contours

        conflict = asyncio.run(
            view.post(
                FakeJsonRequest(
                    "127.0.0.1",
                    reader_user(admin=True),
                    path,
                    payload,
                )
            )
        )

        self.assertEqual(409, conflict.status)
        self.assertEqual("no-store", conflict.headers["Cache-Control"])
        self.assertEqual(original_settings, settings_service.current)
        self.assertEqual(original_contours, climate_runtime._contours)
        for remote, user in (
            ("203.0.113.7", reader_user(admin=True)),
            ("127.0.0.1", reader_user("system-read-only")),
        ):
            with self.subTest(remote=remote, user=user):
                forbidden = asyncio.run(
                    view.post(FakeJsonRequest(remote, user, path, payload))
                )
                self.assertEqual(403, forbidden.status)

    def test_energy_settings_are_shared_persistent_and_local_admin_only(self) -> None:
        path = "/api/hausman_hub/v1/admin/energy-settings"
        view = next(item for item in self.hass.http.views if item.url == path)
        admin = reader_user(admin=True)
        initial = asyncio.run(view.get(FakeRequest("127.0.0.1", admin, path=path)))
        self.assertEqual(200, initial.status)
        self.assertEqual("watts", initial.payload["displayUnits"])
        payload = {
            "displayUnits": "both",
            "showVoltage": True,
            "aggregation": "separate",
            "useAllDevices": False,
            "selectedDeviceIds": ["device_0123456789abcdef"],
        }
        saved = asyncio.run(
            view.post(FakeJsonRequest("127.0.0.1", admin, path, payload))
        )
        self.assertEqual(200, saved.status)
        self.assertEqual(
            payload
            | {"anomalyPowerThresholdW": None, "anomalySustainMinutes": None},
            saved.payload,
        )
        preferences = self.hass.data["hausman_hub"]["tablet_preferences_service"]
        self.assertEqual(
            ["device_0123456789abcdef"],
            preferences.energy["settings"]["selectedDeviceIds"],
        )
        public_path = "/api/hausman_hub/v1/energy-settings"
        public_view = next(
            item for item in self.hass.http.views if item.url == public_path
        )
        public = asyncio.run(
            public_view.get(
                FakeRequest("127.0.0.1", reader_user("system-users"), path=public_path)
            )
        )
        self.assertEqual(1, public.payload["revision"])
        self.assertEqual(
            payload
            | {"anomalyPowerThresholdW": None, "anomalySustainMinutes": None},
            public.payload["settings"],
        )
        for remote, user in (
            ("203.0.113.7", admin),
            ("127.0.0.1", reader_user("system-read-only")),
        ):
            with self.subTest(remote=remote, user=user):
                response = asyncio.run(
                    view.post(FakeJsonRequest(remote, user, path, payload))
                )
                self.assertEqual(403, response.status)

    def test_device_power_dependencies_are_durable_atomic_and_admin_only(self) -> None:
        path = "/api/hausman_hub/v1/admin/device-power-dependencies"
        view = next(item for item in self.hass.http.views if item.url == path)
        admin = reader_user(admin=True)
        self.hass.states.values["light.synthetic_private_lamp"] = SimpleNamespace(
            state="on"
        )
        self.hass.states.values["switch.synthetic_private_light"] = SimpleNamespace(
            state="off"
        )
        initial = asyncio.run(view.get(FakeRequest("127.0.0.1", admin, path=path)))
        self.assertEqual(200, initial.status)
        self.assertEqual(0, initial.payload["revision"])
        self.assertEqual([], initial.payload["dependencies"])
        dependencies = [
            {
                "dependentEntityId": "light.synthetic_private_lamp",
                "powerSourceEntityId": "switch.synthetic_private_light",
                "policy": "auto_turn_on",
                "warmupSeconds": 2,
            }
        ]
        saved = asyncio.run(
            view.put(
                FakeJsonRequest(
                    "127.0.0.1",
                    admin,
                    path,
                    {"expectedRevision": 0, "dependencies": dependencies},
                )
            )
        )
        self.assertEqual(200, saved.status)
        self.assertEqual(1, saved.payload["revision"])
        self.assertEqual(dependencies, saved.payload["dependencies"])
        stale = asyncio.run(
            view.put(
                FakeJsonRequest(
                    "127.0.0.1",
                    admin,
                    path,
                    {"expectedRevision": 0, "dependencies": []},
                )
            )
        )
        self.assertEqual(409, stale.status)
        forbidden = asyncio.run(
            view.put(
                FakeJsonRequest(
                    "127.0.0.1",
                    reader_user("system-read-only"),
                    path,
                    {"expectedRevision": 1, "dependencies": []},
                )
            )
        )
        self.assertEqual(403, forbidden.status)

    def test_energy_meter_api_resets_cycle_without_resetting_ha_source(self) -> None:
        path = "/api/hausman_hub/v1/energy/meter"
        view = next(item for item in self.hass.http.views if item.url == path)

        async def dashboard_snapshot(*_args: object) -> dict[str, object]:
            return {"energy": {"totalKwh": 276.46}}

        globals_ = view.get.__func__.__globals__
        original = globals_["async_dashboard_snapshot"]
        globals_["async_dashboard_snapshot"] = dashboard_snapshot
        tablet = reader_user("system-users")
        try:
            initial = asyncio.run(view.get(FakeRequest("127.0.0.1", tablet, path=path)))
            self.assertEqual(200, initial.status)
            configured = asyncio.run(
                view.post(
                    FakeJsonRequest(
                        "127.0.0.1",
                        tablet,
                        path,
                        {
                            "expectedRevision": 0,
                            "action": "configure",
                            "settings": {
                                "enabled": True,
                                "submissionDayOfMonth": 25,
                                "reminderDaysBefore": 3,
                            },
                        },
                    )
                )
            )
            self.assertEqual(200, configured.status)
            submitted = asyncio.run(
                view.post(
                    FakeJsonRequest(
                        "127.0.0.1",
                        tablet,
                        path,
                        {"expectedRevision": 1, "action": "submit", "readingKwh": 18342.4},
                    )
                )
            )
            self.assertEqual(0.0, submitted.payload["cycle"]["consumptionKwh"])
            self.assertEqual(276.46, submitted.payload["source"]["currentTotalKwh"])
            stale = asyncio.run(
                view.post(
                    FakeJsonRequest(
                        "127.0.0.1",
                        tablet,
                        path,
                        {"expectedRevision": 1, "action": "correct", "readingKwh": 1},
                    )
                )
            )
            self.assertEqual(409, stale.status)
            forbidden = asyncio.run(
                view.get(FakeRequest("203.0.113.7", tablet, path=path))
            )
            self.assertEqual(403, forbidden.status)
        finally:
            globals_["async_dashboard_snapshot"] = original

    def test_energy_meter_api_binds_projection_to_one_energy_device(self) -> None:
        path = "/api/hausman_hub/v1/energy/meter"
        view = next(item for item in self.hass.http.views if item.url == path)
        source_id = "device_0123456789abcdef"

        async def dashboard_snapshot(*_args: object) -> dict[str, object]:
            return {
                "energy": {
                    "totalKwh": 366.65,
                    "sources": [
                        {
                            "id": source_id,
                            "name": "Вводной автомат",
                            "totalKwh": 51.03,
                        },
                        {
                            "id": "device_fedcba9876543210",
                            "name": "Резервный автомат",
                            "totalKwh": 315.62,
                        },
                    ],
                }
            }

        globals_ = view.get.__func__.__globals__
        original = globals_["async_dashboard_snapshot"]
        globals_["async_dashboard_snapshot"] = dashboard_snapshot
        tablet = reader_user("system-users")
        try:
            unknown = asyncio.run(
                view.post(
                    FakeJsonRequest(
                        "127.0.0.1",
                        tablet,
                        path,
                        {
                            "expectedRevision": 0,
                            "action": "configure",
                            "settings": {
                                "enabled": True,
                                "submissionDayOfMonth": 25,
                                "reminderDaysBefore": 3,
                                "sourceDeviceId": "device_aaaaaaaaaaaaaaaa",
                            },
                        },
                    )
                )
            )
            self.assertEqual(400, unknown.status)
            configured = asyncio.run(
                view.post(
                    FakeJsonRequest(
                        "127.0.0.1",
                        tablet,
                        path,
                        {
                            "expectedRevision": 0,
                            "action": "configure",
                            "settings": {
                                "enabled": True,
                                "submissionDayOfMonth": 25,
                                "reminderDaysBefore": 3,
                                "sourceDeviceId": source_id,
                            },
                        },
                    )
                )
            )
            self.assertEqual(200, configured.status)
            self.assertEqual(source_id, configured.payload["source"]["deviceId"])
            self.assertEqual("Вводной автомат", configured.payload["source"]["name"])
            self.assertEqual(51.03, configured.payload["source"]["currentTotalKwh"])
            submitted = asyncio.run(
                view.post(
                    FakeJsonRequest(
                        "127.0.0.1",
                        tablet,
                        path,
                        {"expectedRevision": 1, "action": "submit", "readingKwh": 900.0},
                    )
                )
            )
            self.assertEqual(source_id, submitted.payload["history"][0]["sourceDeviceId"])
        finally:
            globals_["async_dashboard_snapshot"] = original

    def test_energy_meters_api_keeps_named_meters_independent(self) -> None:
        path = "/api/hausman_hub/v1/energy/meters"
        view = next(item for item in self.hass.http.views if item.url == path)
        source_id = "device_0123456789abcdef"

        async def dashboard_snapshot(*_args: object) -> dict[str, object]:
            return {
                "energy": {
                    "totalKwh": 366.65,
                    "sources": [
                        {"id": source_id, "name": "Гараж", "totalKwh": 51.03},
                    ],
                }
            }

        globals_ = view.get.__func__.__globals__
        original = globals_["async_dashboard_snapshot"]
        globals_["async_dashboard_snapshot"] = dashboard_snapshot
        tablet = reader_user("system-users")
        try:
            initial = asyncio.run(view.get(FakeRequest("127.0.0.1", tablet, path=path)))
            self.assertEqual(200, initial.status)
            self.assertEqual(["meter_main"], [item["meterId"] for item in initial.payload["meters"]])

            configured = asyncio.run(
                view.post(
                    FakeJsonRequest(
                        "127.0.0.1",
                        tablet,
                        path,
                        {
                            "expectedRevision": 0,
                            "action": "upsert",
                            "meterId": "meter_garage",
                            "name": "Гараж",
                            "settings": {
                                "enabled": True,
                                "submissionDayOfMonth": 25,
                                "reminderDaysBefore": 3,
                                "sourceDeviceIds": [source_id],
                            },
                        },
                    )
                )
            )
            self.assertEqual(200, configured.status)
            self.assertEqual(
                ["meter_main", "meter_garage"],
                [item["meterId"] for item in configured.payload["meters"]],
            )
            garage = configured.payload["meters"][1]
            self.assertEqual(source_id, garage["source"]["deviceId"])
            self.assertEqual(51.03, garage["source"]["currentTotalKwh"])

            submitted = asyncio.run(
                view.post(
                    FakeJsonRequest(
                        "127.0.0.1",
                        tablet,
                        path,
                        {
                            "expectedRevision": 1,
                            "action": "submit",
                            "meterId": "meter_garage",
                            "readingKwh": 1200.5,
                        },
                    )
                )
            )
            self.assertEqual(200, submitted.status)
            self.assertEqual(1200.5, submitted.payload["meters"][1]["reading"]["currentKwh"])
            stale = asyncio.run(
                view.post(
                    FakeJsonRequest(
                        "127.0.0.1",
                        tablet,
                        path,
                        {"expectedRevision": 1, "action": "delete", "meterId": "meter_garage"},
                    )
                )
            )
            self.assertEqual(409, stale.status)
            primary = asyncio.run(
                view.post(
                    FakeJsonRequest(
                        "127.0.0.1",
                        tablet,
                        path,
                        {"expectedRevision": 2, "action": "delete", "meterId": "meter_main"},
                    )
                )
            )
            self.assertEqual(400, primary.status)
            self.assertEqual(
                403,
                asyncio.run(view.get(FakeRequest("203.0.113.7", tablet, path=path))).status,
            )
        finally:
            globals_["async_dashboard_snapshot"] = original

    def test_device_discovery_api_baselines_then_adds_energy_source(self) -> None:
        from custom_components.hausman_hub.application.device_discovery import (
            DiscoveredDevice,
            DiscoveryArea,
        )

        path = "/api/hausman_hub/v1/device-discovery"
        view = next(item for item in self.hass.http.views if item.url == path)
        devices = [
            DiscoveredDevice(
                private_device_id="private-existing",
                device_id="device_0000000000000001",
                title="Старое устройство",
                room_id="office",
                room_name="Кабинет",
                kind="physical",
                status="available",
                domains=("sensor",),
                manufacturer=None,
                model=None,
            )
        ]

        def snapshot(*_args: object, **_kwargs: object) -> tuple[object, object]:
            return tuple(devices), (DiscoveryArea("office", "Кабинет"),)

        globals_ = view.get.__func__.__globals__
        original = globals_["device_discovery_snapshot"]
        globals_["device_discovery_snapshot"] = snapshot
        tablet = reader_user("system-users")
        try:
            baseline = asyncio.run(view.get(FakeRequest("127.0.0.1", tablet, path=path)))
            self.assertEqual(0, baseline.payload["pendingCount"])
            devices.append(
                DiscoveredDevice(
                    private_device_id="private-new",
                    device_id="device_0000000000000002",
                    title="Новый счётчик",
                    room_id=None,
                    room_name=None,
                    kind="physical",
                    status="available",
                    domains=("sensor",),
                    manufacturer="Example",
                    model="EM-1",
                    energy_eligible=True,
                )
            )
            discovered = asyncio.run(view.get(FakeRequest("127.0.0.1", tablet, path=path)))
            self.assertEqual(1, discovered.payload["pendingCount"])
            notice = discovered.payload["notifications"][0]
            self.assertNotIn("privateDeviceId", notice)
            saved = asyncio.run(
                view.post(
                    FakeJsonRequest(
                        "127.0.0.1",
                        tablet,
                        path,
                        {
                            "expectedRevision": discovered.payload["revision"],
                            "action": "add_to_energy",
                            "notificationId": notice["id"],
                        },
                    )
                )
            )
            self.assertEqual(200, saved.status)
            self.assertEqual(0, saved.payload["pendingCount"])
            preferences = self.hass.data["hausman_hub"]["tablet_preferences_service"]
            self.assertEqual(
                ["device_0000000000000002"],
                preferences.energy["settings"]["selectedDeviceIds"],
            )
        finally:
            globals_["device_discovery_snapshot"] = original

    def test_tablet_profile_is_atomic_shared_and_rejects_stale_writes(self) -> None:
        path = "/api/hausman_hub/v1/tablet-profile"
        view = next(item for item in self.hass.http.views if item.url == path)
        tablet = reader_user("system-users")
        initial = asyncio.run(view.get(FakeRequest("127.0.0.1", tablet, path=path)))
        self.assertEqual(200, initial.status)
        self.assertEqual(0, initial.payload["revision"])
        settings = copy.deepcopy(initial.payload["settings"])
        settings["startScreen"]["mode"] = "kiosk"

        saved = asyncio.run(
            view.put(
                FakeJsonRequest(
                    "127.0.0.1",
                    tablet,
                    path,
                    {"expectedRevision": 0, "settings": settings},
                )
            )
        )
        self.assertEqual(200, saved.status)
        self.assertEqual(1, saved.payload["revision"])
        self.assertEqual("kiosk", saved.payload["settings"]["startScreen"]["mode"])

        stale = asyncio.run(
            view.put(
                FakeJsonRequest(
                    "127.0.0.1",
                    tablet,
                    path,
                    {"expectedRevision": 0, "settings": initial.payload["settings"]},
                )
            )
        )
        self.assertEqual(409, stale.status)
        current = asyncio.run(view.get(FakeRequest("127.0.0.1", tablet, path=path)))
        self.assertEqual(saved.payload, current.payload)

    def test_room_settings_apply_canonical_icon_with_registry_read_back(self) -> None:
        class AreaRegistry:
            def __init__(self) -> None:
                self.areas = {
                    "living": SimpleNamespace(
                        id="living", name="Гостиная", icon="mdi:sofa"
                    )
                }

            def async_list_areas(self) -> list[object]:
                return list(self.areas.values())

            def async_get_area(self, area_id: str) -> object | None:
                return self.areas.get(area_id)

            def async_update(self, area_id: str, *, icon: str | None) -> object:
                self.areas[area_id].icon = icon
                return self.areas[area_id]

        self.hass.area_registry = AreaRegistry()
        path = "/api/hausman_hub/v1/room-settings"
        view = next(item for item in self.hass.http.views if item.url == path)
        tablet = reader_user("system-users")
        initial = asyncio.run(view.get(FakeRequest("127.0.0.1", tablet, path=path)))
        self.assertEqual(200, initial.status)
        self.assertEqual(0, initial.payload["revision"])
        self.assertEqual("living", initial.payload["rooms"][0]["type"])

        changed = copy.deepcopy(initial.payload["rooms"])
        changed[0].update({"type": "office", "icon": "mdi:briefcase"})
        saved = asyncio.run(
            view.put(
                FakeJsonRequest(
                    "127.0.0.1",
                    tablet,
                    path,
                    {"expectedRevision": 0, "rooms": changed},
                )
            )
        )
        self.assertEqual(200, saved.status)
        self.assertEqual(1, saved.payload["revision"])
        self.assertEqual("mdi:briefcase", self.hass.area_registry.areas["living"].icon)

        stale = asyncio.run(
            view.put(
                FakeJsonRequest(
                    "127.0.0.1",
                    tablet,
                    path,
                    {"expectedRevision": 0, "rooms": changed},
                )
            )
        )
        self.assertEqual(409, stale.status)

    def test_dashboard_snapshot_is_available_to_local_tablet_and_admin(self) -> None:
        """The shared read model must feed both product surfaces without writes."""

        path = "/api/hausman_hub/v1/dashboard"
        view = next(item for item in self.hass.http.views if item.url == path)

        async def dashboard_snapshot(*_args: object) -> dict[str, object]:
            return {"energy": {}, "devices": []}

        method_globals = view.get.__func__.__globals__
        original_snapshot = method_globals["async_dashboard_snapshot"]
        method_globals["async_dashboard_snapshot"] = dashboard_snapshot
        try:
            for user in (
                reader_user("system-users"),
                reader_user("system-admin", admin=True),
            ):
                with self.subTest(user=user):
                    response = asyncio.run(
                        view.get(FakeRequest("127.0.0.1", user, path=path))
                    )
                    self.assertEqual(200, response.status)
                    self.assertEqual("no-store", response.headers["Cache-Control"])
                    self.assertIn("energy", response.payload)
                    self.assertIn("devices", response.payload)

            for remote, user in (
                ("203.0.113.7", reader_user("system-admin", admin=True)),
                ("127.0.0.1", reader_user("system-read-only")),
            ):
                with self.subTest(remote=remote, user=user):
                    response = asyncio.run(
                        view.get(FakeRequest(remote, user, path=path))
                    )
                    self.assertEqual(403, response.status)
        finally:
            method_globals["async_dashboard_snapshot"] = original_snapshot

    def test_energy_history_is_bounded_and_available_to_tablet_and_admin(self) -> None:
        path = "/api/hausman_hub/v1/energy/history"
        view = next(item for item in self.hass.http.views if item.url == path)

        class Query(dict[str, str]):
            def getall(self, key: str, default: list[str]) -> list[str]:
                return ["device_0123456789abcdef"] if key == "deviceId" else default

        async def dashboard_snapshot(*_args: object) -> dict[str, object]:
            return {"energy": {"sources": []}, "devices": []}

        async def energy_history(*_args: object, **kwargs: object) -> dict[str, object]:
            return {
                "contract": {"name": "hausman-hub-energy-history", "version": 1},
                "from": kwargs["start"].isoformat(),
                "to": kwargs["end"].isoformat(),
                "interval": kwargs["interval"],
                "series": [],
            }

        method_globals = view.get.__func__.__globals__
        original_dashboard = method_globals["async_dashboard_snapshot"]
        original_history = method_globals["async_energy_history"]
        method_globals["async_dashboard_snapshot"] = dashboard_snapshot
        method_globals["async_energy_history"] = energy_history
        try:
            for user in (
                reader_user("system-users"),
                reader_user("system-admin", admin=True),
            ):
                request = FakeRequest(
                    "127.0.0.1",
                    user,
                    path=path,
                    query_string="from=...",
                )
                request.query = Query(
                    {
                        "from": "2026-07-30T00:00:00+00:00",
                        "to": "2026-07-31T00:00:00+00:00",
                        "interval": "15m",
                    }
                )
                response = asyncio.run(view.get(request))
                self.assertEqual(200, response.status)
                self.assertEqual("15m", response.payload["interval"])
                self.assertEqual("no-store", response.headers["Cache-Control"])

            invalid = FakeRequest(
                "127.0.0.1",
                reader_user("system-users"),
                path=path,
                query_string="from=...",
            )
            invalid.query = Query(
                {
                    "from": "2025-07-29T00:00:00+00:00",
                    "to": "2026-07-31T00:00:00+00:00",
                    "interval": "15m",
                }
            )
            self.assertEqual(400, asyncio.run(view.get(invalid)).status)

            maximum_window = FakeRequest(
                "127.0.0.1",
                reader_user("system-users"),
                path=path,
                query_string="from=...",
            )
            maximum_window.query = Query(
                {
                    "from": "2026-06-30T00:00:00+00:00",
                    "to": "2026-07-31T00:00:00+00:00",
                    "interval": "1d",
                }
            )
            self.assertEqual(200, asyncio.run(view.get(maximum_window)).status)

            oversized_window = FakeRequest(
                "127.0.0.1",
                reader_user("system-users"),
                path=path,
                query_string="from=...",
            )
            oversized_window.query = Query(
                {
                    "from": "2025-08-01T00:00:00+00:00",
                    "to": "2026-07-31T00:00:00+00:00",
                    "interval": "1d",
                }
            )
            self.assertEqual(400, asyncio.run(view.get(oversized_window)).status)

            calendar_window = FakeRequest(
                "127.0.0.1",
                reader_user("system-users"),
                path=path,
                query_string="window=day",
            )
            calendar_window.query = Query(
                {"window": "day", "timezone": "Asia/Omsk", "interval": "1h"}
            )
            self.assertEqual(200, asyncio.run(view.get(calendar_window)).status)

            mixed_window = FakeRequest(
                "127.0.0.1",
                reader_user("system-users"),
                path=path,
                query_string="window=day",
            )
            mixed_window.query = Query(
                {
                    "window": "day",
                    "timezone": "Asia/Omsk",
                    "from": "2026-07-30T00:00:00+00:00",
                    "interval": "1h",
                }
            )
            self.assertEqual(400, asyncio.run(view.get(mixed_window)).status)
        finally:
            method_globals["async_dashboard_snapshot"] = original_dashboard
            method_globals["async_energy_history"] = original_history

    def test_climate_shadow_comparison_is_local_admin_only_and_read_only(self) -> None:
        path = "/api/hausman_hub/v1/admin/climate-shadow-comparison"
        view = next(item for item in self.hass.http.views if item.url == path)
        runtime = self.hass.data["hausman_hub"]["climate_runtime"]
        original_contours = runtime._contours

        unavailable = asyncio.run(
            view.get(
                FakeRequest(
                    "127.0.0.1",
                    reader_user(admin=True),
                    path=path,
                )
            )
        )

        self.assertEqual(503, unavailable.status)
        self.assertEqual("no-store", unavailable.headers["Cache-Control"])
        self.assertEqual(original_contours, runtime._contours)
        from custom_components.hausman_hub.application.climate_comparison import (
            climate_reference_comparison,
        )

        async def reference_comparison():
            return climate_reference_comparison("stopped_ac_starts_at_default_gap")

        runtime.async_native_climate_comparison = reference_comparison
        response = asyncio.run(
            view.get(
                FakeRequest(
                    "127.0.0.1",
                    reader_user(admin=True),
                    path=path,
                )
            )
        )
        self.assertEqual(200, response.status)
        self.assertFalse(response.payload["commands_enabled"])
        self.assertFalse(response.payload["physical_commands_sent"])
        self.assertFalse(response.payload["write_performed"])
        for remote, user in (
            ("203.0.113.7", reader_user(admin=True)),
            ("127.0.0.1", reader_user("system-read-only")),
        ):
            with self.subTest(remote=remote, user=user):
                forbidden = asyncio.run(
                    view.get(FakeRequest(remote, user, path=path))
                )
                self.assertEqual(403, forbidden.status)

    def test_climate_shadow_window_is_local_admin_only_and_command_free(self) -> None:
        path = "/api/hausman_hub/v1/admin/climate-shadow-window"
        view = next(item for item in self.hass.http.views if item.url == path)
        runtime = self.hass.data["hausman_hub"]["climate_runtime"]
        original_contours = runtime._contours

        response = asyncio.run(
            view.get(
                FakeRequest(
                    "127.0.0.1",
                    reader_user(admin=True),
                    path=path,
                )
            )
        )

        self.assertEqual(200, response.status)
        self.assertEqual("no-store", response.headers["Cache-Control"])
        self.assertEqual(0, response.payload["summary"]["sample_count"])
        self.assertTrue(response.payload["window"]["collection_active"])
        self.assertFalse(response.payload["commands_enabled"])
        self.assertFalse(response.payload["physical_commands_sent"])
        self.assertEqual(original_contours, runtime._contours)
        for remote, user in (
            ("203.0.113.7", reader_user(admin=True)),
            ("127.0.0.1", reader_user("system-read-only")),
        ):
            with self.subTest(remote=remote, user=user):
                forbidden = asyncio.run(
                    view.get(FakeRequest(remote, user, path=path))
                )
                self.assertEqual(403, forbidden.status)

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

    def test_disabled_climate_routes_separate_tablet_and_admin_roles(self) -> None:
        """Tablet and admin share typed climate control, not other role surfaces."""

        views = {view.url: view for view in self.hass.http.views}
        tablet = reader_user("system-users")
        admin = reader_user("system-admin", admin=True)
        read_only = reader_user("system-read-only")

        capabilities_path = "/api/hausman_hub/v1/capabilities"
        capabilities = views[capabilities_path]
        capabilities_response = asyncio.run(
            capabilities.get(
                FakeRequest(
                    "127.0.0.1",
                    tablet,
                    path=capabilities_path,
                )
            )
        )
        self.assertEqual(200, capabilities_response.status)
        self.assertEqual(
            {"name": "hausman-hub-capabilities", "version": 1},
            capabilities_response.payload["contract"],
        )
        self.assertEqual(
            7,
            capabilities_response.payload["capabilities"]["automatic_contours"][  # type: ignore[index]
                "response_contract"
            ]["version"],  # type: ignore[index]
        )
        self.assertEqual(
            {
                "available": True,
                "phase": "disabled",
                "commands_enabled": False,
            },
            {
                key: capabilities_response.payload["capabilities"][
                    "climate_runtime"
                ][key]
                for key in ("available", "phase", "commands_enabled")
            },
        )
        self.assertFalse(
            capabilities_response.payload["capabilities"][
                "climate_room_recovery_v2"
            ]["available"]
        )
        self.assertEqual("no-store", capabilities_response.headers.get("Cache-Control"))
        admin_capabilities_response = asyncio.run(
            capabilities.get(
                FakeRequest(
                    "127.0.0.1",
                    admin,
                    path=capabilities_path,
                )
            )
        )
        self.assertEqual(200, admin_capabilities_response.status)
        self.assertEqual("no-store", admin_capabilities_response.headers.get("Cache-Control"))
        self.assertEqual(
            capabilities_response.payload,
            admin_capabilities_response.payload,
        )
        self.assertEqual(
            404,
            asyncio.run(
                capabilities.get(
                    FakeRequest(
                        "127.0.0.1",
                        tablet,
                        path=capabilities_path,
                        query_string="unexpected=1",
                    )
                )
            ).status,
        )
        for remote, user in (
            ("203.0.113.7", admin),
            ("127.0.0.1", read_only),
        ):
            with self.subTest(capabilities_remote=remote, capabilities_user=user):
                self.assertEqual(
                    403,
                    asyncio.run(
                        capabilities.get(
                            FakeRequest(
                                remote,
                                user,
                                path=capabilities_path,
                            )
                        )
                    ).status,
                )

        runtime_path = "/api/hausman_hub/v1/climate/runtime"
        runtime_response = asyncio.run(
            views[runtime_path].get(
                FakeRequest("127.0.0.1", tablet, path=runtime_path)
            )
        )
        self.assertEqual(200, runtime_response.status)
        self.assertEqual("disabled", runtime_response.payload["phase"])
        self.assertFalse(runtime_response.payload["commands_enabled"])
        admin_runtime_response = asyncio.run(
            views[runtime_path].get(
                FakeRequest("127.0.0.1", admin, path=runtime_path)
            )
        )
        self.assertEqual(200, admin_runtime_response.status)
        self.assertEqual("disabled", admin_runtime_response.payload["phase"])

        action_path = "/api/hausman_hub/v1/climate/actions"
        disabled_action = {
            "contract": {
                "name": "hausman-hub-climate-action-request",
                "version": 1,
            },
            "request_id": "disabled-climate-action-1",
            "expected_state_revision": 0,
            "action": "set_room_target",
            "room_id": "living",
            "parameters": {"target_temperature": 23.5},
        }
        action_response = asyncio.run(
            views[action_path].post(
                FakeJsonRequest(
                    "127.0.0.1",
                    tablet,
                    action_path,
                    disabled_action,
                )
            )
        )
        self.assertEqual(409, action_response.status)
        self.assertEqual("climate_disabled", action_response.payload["code"])
        admin_action_response = asyncio.run(
            views[action_path].post(
                FakeJsonRequest(
                    "127.0.0.1",
                    admin,
                    action_path,
                    {**disabled_action, "request_id": "admin-disabled-climate-action-1"},
                )
            )
        )
        self.assertEqual(409, admin_action_response.status)
        self.assertEqual("climate_disabled", admin_action_response.payload["code"])

        operation_template = (
            "/api/hausman_hub/v1/climate/operations/{operation_id}"
        )
        operation_id = "f" * 32
        operation_response = asyncio.run(
            views[operation_template].get(
                FakeRequest(
                    "127.0.0.1",
                    tablet,
                    path=operation_template.replace("{operation_id}", operation_id),
                ),
                operation_id,
            )
        )
        self.assertEqual(404, operation_response.status)
        self.assertEqual(
            "climate_operation_not_found", operation_response.payload["code"]
        )

        home = views["/api/hausman_hub/v1/home"]
        self.assertEqual(
            503,
            asyncio.run(
                home.get(
                    FakeRequest(
                        "127.0.0.1",
                        tablet,
                        path="/api/hausman_hub/v1/home",
                    )
                )
            ).status,
        )
        for user in (admin, read_only):
            with self.subTest(user=user):
                response = asyncio.run(
                    home.get(
                        FakeRequest(
                            "127.0.0.1",
                            user,
                            path="/api/hausman_hub/v1/home",
                        )
                    )
                )
                self.assertEqual(403, response.status)

        contours = views["/api/hausman_hub/v1/contours"]
        contour_response = asyncio.run(
            contours.get(
                FakeRequest(
                    "127.0.0.1",
                    tablet,
                    path="/api/hausman_hub/v1/contours",
                )
            )
        )
        self.assertEqual(200, contour_response.status)
        self.assertEqual("hausman-hub-contours", contour_response.payload["contract"]["name"])
        self.assertEqual([], contour_response.payload["contours"])
        self.assertEqual(
            403,
            asyncio.run(
                contours.get(
                    FakeRequest(
                        "127.0.0.1",
                        admin,
                        path="/api/hausman_hub/v1/contours",
                    )
                )
            ).status,
        )
        temporary_path = "/api/hausman_hub/v1/contours/temporary-temperature"
        temporary_view = views[temporary_path]
        temporary_payload = {
            "request_id": "disabled-temporary-1",
            "contour_id": "climate",
            "room_id": "living",
            "action": "set",
            "target_temperature": 23.5,
            "confirm": True,
        }
        self.assertEqual(
            503,
            asyncio.run(
                temporary_view.post(
                    FakeJsonRequest(
                        "127.0.0.1",
                        tablet,
                        temporary_path,
                        temporary_payload,
                    )
                )
            ).status,
        )
        self.assertEqual(
            403,
            asyncio.run(
                temporary_view.post(
                    FakeJsonRequest(
                        "127.0.0.1",
                        admin,
                        temporary_path,
                        temporary_payload,
                    )
                )
            ).status,
        )

        apply_preview_path = "/api/hausman_hub/v1/contours/apply-preview"
        apply_preview = views[apply_preview_path]
        self.assertEqual(
            503,
            asyncio.run(
                apply_preview.get(
                    FakeRequest(
                        "127.0.0.1",
                        tablet,
                        path=apply_preview_path,
                    )
                )
            ).status,
        )
        self.assertEqual(
            403,
            asyncio.run(
                apply_preview.get(
                    FakeRequest(
                        "127.0.0.1",
                        admin,
                        path=apply_preview_path,
                    )
                )
            ).status,
        )
        apply_path = "/api/hausman_hub/v1/contours/apply"
        apply_view = views[apply_path]
        self.assertEqual(
            503,
            asyncio.run(
                apply_view.post(
                    FakeJsonRequest(
                        "127.0.0.1",
                        tablet,
                        apply_path,
                        {
                            "request_id": "disabled-apply-1",
                            "contour_id": "climate",
                            "confirm": True,
                        },
                    )
                )
            ).status,
        )
        self.assertEqual(
            403,
            asyncio.run(
                apply_view.post(
                    FakeJsonRequest(
                        "127.0.0.1",
                        admin,
                        apply_path,
                        {
                            "request_id": "admin-must-not-impersonate-tablet",
                            "contour_id": "climate",
                            "confirm": True,
                        },
                    )
                )
            ).status,
        )

        registry = views["/api/hausman_hub/v1/admin/climate-registry"]
        admin_response = asyncio.run(
            registry.get(
                FakeRequest(
                    "127.0.0.1",
                    admin,
                    path="/api/hausman_hub/v1/admin/climate-registry",
                )
            )
        )
        self.assertEqual(200, admin_response.status)
        self.assertEqual({"version": 3, "home": {"outdoor_temperature_entity_id": None, "presence_entity_id": None, "central_heating_entity_id": None}, "rooms": [], "devices": []}, admin_response.payload)
        tablet_response = asyncio.run(
            registry.get(
                FakeRequest(
                    "127.0.0.1",
                    tablet,
                    path="/api/hausman_hub/v1/admin/climate-registry",
                )
            )
        )
        self.assertEqual(403, tablet_response.status)

        bindings_path = "/api/hausman_hub/v1/admin/climate-device-bindings"
        bindings = views[bindings_path]
        bindings_response = asyncio.run(
            bindings.get(
                FakeRequest("127.0.0.1", admin, path=bindings_path)
            )
        )
        self.assertEqual(200, bindings_response.status)
        self.assertEqual(0, bindings_response.payload["summary"]["device_count"])
        self.assertEqual(
            403,
            asyncio.run(
                bindings.get(
                    FakeRequest("127.0.0.1", tablet, path=bindings_path)
                )
            ).status,
        )
        self.assertEqual(
            403,
            asyncio.run(
                bindings.post(
                    FakeJsonRequest("127.0.0.1", tablet, bindings_path, {})
                )
            ).status,
        )
        preview_path = f"{bindings_path}/preview"
        preview_view = views[preview_path]
        self.assertEqual(
            403,
            asyncio.run(
                preview_view.post(
                    FakeJsonRequest("127.0.0.1", tablet, preview_path, {})
                )
            ).status,
        )
        self.assertEqual(
            400,
            asyncio.run(
                preview_view.post(
                    FakeJsonRequest(
                        "127.0.0.1",
                        admin,
                        preview_path,
                        {
                            "snapshot_revision": bindings_response.payload[
                                "snapshot_revision"
                            ],
                            "bindings": [],
                        },
                    )
                )
            ).status,
        )
        stale_binding = {
            "snapshot_revision": bindings_response.payload["snapshot_revision"] + 1,
            "bindings": [
                {"device_id": "missing", "entity_id": "sensor.missing"}
            ],
        }
        self.assertEqual(
            409,
            asyncio.run(
                preview_view.post(
                    FakeJsonRequest(
                        "127.0.0.1", admin, preview_path, stale_binding
                    )
                )
            ).status,
        )
        self.assertEqual(
            409,
            asyncio.run(
                bindings.post(
                    FakeJsonRequest(
                        "127.0.0.1",
                        admin,
                        bindings_path,
                        {**stale_binding, "preview_revision": 1},
                    )
                )
            ).status,
        )

        draft_path = "/api/hausman_hub/v1/admin/climate-drafts"
        draft = views[draft_path]
        draft_request = {
            "snapshot_revision": 1,
            "name": "Климат",
            "mode": "automatic",
            "rooms": [],
        }
        self.assertEqual(
            409,
            asyncio.run(
                draft.post(
                    FakeJsonRequest(
                        "127.0.0.1",
                        admin,
                        draft_path,
                        draft_request,
                    )
                )
            ).status,
        )
        self.assertEqual(
            403,
            asyncio.run(
                draft.post(
                    FakeJsonRequest(
                        "127.0.0.1",
                        tablet,
                        draft_path,
                        draft_request,
                    )
                )
            ).status,
        )

        current_path = "/api/hausman_hub/v1/admin/climate-drafts/current"
        current_view = views[current_path]
        self.assertEqual(
            200,
            asyncio.run(
                current_view.get(
                    FakeRequest(
                        "127.0.0.1",
                        admin,
                        path=current_path,
                    )
                )
            ).status,
        )
        self.assertEqual(
            403,
            asyncio.run(
                current_view.get(
                    FakeRequest(
                        "127.0.0.1",
                        tablet,
                        path=current_path,
                    )
                )
            ).status,
        )

        save_path = "/api/hausman_hub/v1/admin/climate-drafts/save"
        save_view = views[save_path]
        self.assertEqual(
            400,
            asyncio.run(
                save_view.post(
                    FakeJsonRequest(
                        "127.0.0.1",
                        admin,
                        save_path,
                        draft_request,
                    )
                )
            ).status,
        )
        self.assertEqual(
            403,
            asyncio.run(
                save_view.post(
                    FakeJsonRequest(
                        "127.0.0.1",
                        tablet,
                        save_path,
                        draft_request,
                    )
                )
            ).status,
        )

        retired_path = "/api/hausman_hub/v1/admin/climate-canary-preflight"
        self.assertNotIn(retired_path, views)

    def test_tablet_climate_action_route_is_durably_idempotent(self) -> None:
        """One typed tablet request returns 202 and never executes twice."""

        from custom_components.hausman_hub.application.climate_tablet import (
            ClimateTabletService,
        )
        from tests.test_climate_tablet import (
            FakeRuntime,
            MemoryOperationStore,
            action_request,
            managed_home,
        )

        home = managed_home()
        runtime = FakeRuntime(home)
        service = ClimateTabletService(
            runtime,
            MemoryOperationStore(),
            operation_id_factory=iter(("6" * 32, "7" * 32)).__next__,
            now_ms=lambda: 1_785_949_320_000,
        )
        self.hass.data["hausman_hub"]["climate_tablet"] = service
        views = {view.url: view for view in self.hass.http.views}
        tablet = reader_user("system-users")
        hacs_admin = reader_user("system-admin", admin=True)
        capabilities_path = "/api/hausman_hub/v1/capabilities"
        runtime_path = "/api/hausman_hub/v1/climate/runtime"
        action_path = "/api/hausman_hub/v1/climate/actions"
        operation_template = (
            "/api/hausman_hub/v1/climate/operations/{operation_id}"
        )

        capabilities = asyncio.run(
            views[capabilities_path].get(
                FakeRequest("192.168.1.20", tablet, path=capabilities_path)
            )
        )
        # The ordinary in-memory operation store is enough to exercise the
        # typed action route and its idempotency, but it is not an external
        # authenticated ledger. Recovery dispatch must stay undiscoverable.
        self.assertFalse(
            capabilities.payload["capabilities"][
                "climate_room_recovery_v2"
            ]["available"]
        )

        snapshot = asyncio.run(
            views[runtime_path].get(
                FakeRequest("192.168.1.20", tablet, path=runtime_path)
            )
        )
        request = action_request(snapshot.payload["state_revision"], target=25.0)
        first = asyncio.run(
            views[action_path].post(
                FakeJsonRequest("192.168.1.20", tablet, action_path, request)
            )
        )
        duplicate = asyncio.run(
            views[action_path].post(
                FakeJsonRequest("192.168.1.20", tablet, action_path, request)
            )
        )

        self.assertEqual(202, first.status)
        self.assertEqual("confirmed", first.payload["status"])
        self.assertFalse(first.payload["duplicate"])
        self.assertEqual(202, duplicate.status)
        self.assertTrue(duplicate.payload["duplicate"])
        self.assertEqual(
            first.payload["operation_id"], duplicate.payload["operation_id"]
        )
        self.assertEqual(1, len(runtime.commands))
        lower_request = action_request(
            snapshot.payload["state_revision"],
            request_id="tablet.climate.lower-target",
            target=21.5,
        )
        lower = asyncio.run(
            views[action_path].post(
                FakeJsonRequest("127.0.0.1", hacs_admin, action_path, lower_request)
            )
        )
        lower_duplicate = asyncio.run(
            views[action_path].post(
                FakeJsonRequest("127.0.0.1", hacs_admin, action_path, lower_request)
            )
        )
        self.assertEqual(202, lower.status)
        self.assertEqual("confirmed", lower.payload["status"])
        self.assertTrue(lower_duplicate.payload["duplicate"])
        self.assertEqual(
            [25.0, 21.5],
            [command["target_temperature"] for command in runtime.commands],
        )

        operation_id = first.payload["operation_id"]
        operation = asyncio.run(
            views[operation_template].get(
                FakeRequest(
                    "192.168.1.20",
                    tablet,
                    path=operation_template.replace(
                        "{operation_id}", operation_id
                    ),
                ),
                operation_id,
            )
        )
        self.assertEqual(200, operation.status)
        self.assertEqual(operation_id, operation.payload["operation_id"])
        self.assertFalse(operation.payload["duplicate"])


    def test_legacy_home_targets_route_projects_a_reliable_typed_receipt(self) -> None:
        """The legacy request shape enters the typed coordinator without a runtime bypass."""

        from custom_components.hausman_hub.application.climate_tablet import (
            ClimateTabletService,
        )
        from tests.test_climate_tablet import FakeRuntime, MemoryOperationStore, managed_home

        captured: list[dict[str, object]] = []

        coordinator = ClimateTabletService(FakeRuntime(managed_home()), MemoryOperationStore())

        async def execute_legacy(**payload: object) -> dict[str, object]:
            captured.append(dict(payload))
            return {
                "contract": {
                    "name": "hausman-hub-climate-operation-receipt",
                    "version": 1,
                },
                "request_id": "tablet.climate.legacy-home.1",
                "status": "confirmed",
            }

        coordinator.async_execute_legacy_home_targets = execute_legacy
        self.hass.data["hausman_hub"]["climate_tablet"] = coordinator
        path = "/api/hausman_hub/v1/contours/home-targets"
        view = {item.url: item for item in self.hass.http.views}[path]
        response = asyncio.run(
            view.post(
                FakeJsonRequest(
                    "192.168.1.20",
                    reader_user("system-users"),
                    path,
                    {
                        "request_id": "tablet.climate.legacy-home.1",
                        "contour_id": "climate",
                        "target_temperature": 24.5,
                        "target_humidity": None,
                        "confirm": True,
                    },
                )
            )
        )

        self.assertEqual(200, response.status)
        self.assertEqual(
            {"name": "hausman-hub-climate-operation-receipt", "version": 1},
            response.payload["contract"],
        )
        self.assertEqual("tablet.climate.legacy-home.1", response.payload["request_id"])
        self.assertEqual("confirmed", response.payload["status"])
        self.assertEqual("tablet.climate.legacy-home.1", response.payload["correlation_id"])
        self.assertEqual("no-store", response.headers.get("Cache-Control"))
        self.assertEqual(1, len(captured))
        self.assertEqual("tablet.climate.legacy-home.1", captured[0]["request_id"])
        self.assertEqual("tablet.climate.legacy-home.1", captured[0]["correlation_id"])
        self.assertEqual({"target_temperature": 24.5}, captured[0]["parameters"])

    def test_legacy_home_target_receipt_preserves_typed_execution_facts(self) -> None:
        from custom_components.hausman_hub.climate_api import _legacy_home_target_receipt

        receipt = _legacy_home_target_receipt({
            "operation_id": "a" * 32,
            "request_id": "legacy.facts",
            "status": "partial",
            "message": "Часть команд не подтверждена.",
            "created_at": 10,
            "updated_at": 20,
            "room_count": 2,
            "command_count": 3,
            "accepted_count": 1,
            "confirmed_room_count": 1,
            "changes": {"temperature": 2, "strategy": 0, "automatic_mode": 0},
            "reasons": ["command_result_unavailable"],
            "read_back": {"attempted": True, "matched": False, "observed_at": 20,
                          "confirmed_room_count": 1},
        }, "corr.legacy.facts")

        self.assertEqual("hausman-hub-climate-control-receipt", receipt["contract"]["name"])
        self.assertEqual("partial", receipt["status"])
        self.assertEqual(3, receipt["command_count"])
        self.assertEqual(1, receipt["accepted_count"])
        self.assertTrue(receipt["read_back"]["attempted"])
        self.assertEqual(
            ["Не удалось надёжно узнать результат команды."],
            receipt["reason_names"],
        )

    def test_legacy_home_targets_route_uses_real_native_coordinator_and_replays_correlation(self) -> None:
        """The HTTP compatibility route has no private execution bypass."""
        from jsonschema import Draft202012Validator
        from custom_components.hausman_hub.application.climate_tablet import ClimateTabletService
        from tests.test_climate_tablet import native_home_target_runtime

        runtime, store, _contours, executor = native_home_target_runtime(
            include_humidifier=True,
        )
        asyncio.run(runtime.async_start())
        coordinator = ClimateTabletService(runtime, store, now_ms=lambda: 1784280004000)
        asyncio.run(coordinator.async_load())
        self.hass.data["hausman_hub"]["climate_tablet"] = coordinator
        path = "/api/hausman_hub/v1/contours/home-targets"
        view = {item.url: item for item in self.hass.http.views}[path]
        payload = {
            "request_id": "tablet.climate.route-real-a",
            "correlation_id": "corr.route-real",
            "contour_id": "climate",
            "target_temperature": 25.5,
            "target_humidity": 55,
            "confirm": True,
        }
        first = asyncio.run(view.post(FakeJsonRequest(
            "192.168.1.20", reader_user("system-users"), path, payload,
        )))
        from custom_components.hausman_hub.application.climate_tablet import _receipt_matches_request
        stored = coordinator._records_by_request["tablet.climate.route-real-a"]
        self.assertTrue(_receipt_matches_request(stored.receipt, stored.request), stored.receipt)
        sidecar = coordinator._legacy_home_execution_facts["corr.route-real"]
        self.assertEqual("confirmed", sidecar["status"])
        self.assertEqual(1, sidecar["room_count"])
        self.assertEqual(4, sidecar["command_count"])
        self.assertEqual(4, sidecar["accepted_count"])
        self.assertEqual(1, sidecar["confirmed_room_count"])
        self.assertEqual(1, sidecar["humidity_changes"])
        self.assertEqual(
            {"temperature": 1, "strategy": 0, "automatic_mode": 0},
            sidecar["changes"],
        )
        restarted = ClimateTabletService(runtime, store, now_ms=lambda: 1784280004000)
        asyncio.run(restarted.async_load())
        self.hass.data["hausman_hub"]["climate_tablet"] = restarted
        duplicate_payload = {**payload, "request_id": "tablet.climate.route-real-b"}
        duplicate = asyncio.run(view.post(FakeJsonRequest(
            "192.168.1.20", reader_user("system-users"), path, duplicate_payload,
        )))

        self.assertEqual(200, first.status)
        self.assertEqual("confirmed", first.payload["status"])
        self.assertGreater(
            first.payload["command_count"], 0,
            coordinator._records_by_request["tablet.climate.route-real-a"].receipt,
        )
        self.assertEqual(first.payload["command_count"], first.payload["accepted_count"])
        self.assertTrue(first.payload["read_back"]["matched"])
        self.assertEqual(first.payload["operation_id"], duplicate.payload["operation_id"])
        self.assertEqual(first.payload["command_count"], duplicate.payload["command_count"])
        self.assertEqual(4, len(executor.batches))
        schema = json.loads((ROOT / "custom_components" / "hausman_hub" / "contracts" / "v1" / "climate-control-receipt.schema.json").read_text(encoding="utf-8"))
        self.assertEqual([], list(Draft202012Validator(schema).iter_errors(first.payload)))

    def test_shadow_climate_route_returns_public_state_and_never_posts(self) -> None:
        """Exercise the native Android facade with an actual runtime."""

        from custom_components.hausman_hub.application.climate_runtime import (
            ClimateRuntime,
        )
        from custom_components.hausman_hub.application.contours import (
            build_climate_contour_setup,
            with_applied_climate_schedule_profile,
            with_climate_schedule,
        )
        from custom_components.hausman_hub.domain.climate_bridge import (
            ClimateControlMode,
        )
        from custom_components.hausman_hub.domain.configuration import SafeConfiguration
        from custom_components.hausman_hub.domain.contours import ClimateProfile
        from tests.climate_bridge_fixture import (
            import_climate_state,
        )
        from tests.test_climate_import import source_payload
        from tests.test_climate_runtime import (
            SnapshotStateView,
            with_native_observation_bindings,
        )

        snapshot = import_climate_state(source_payload())
        selected_registry, contours = build_climate_contour_setup(
            snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        contours = with_climate_schedule(
            contours,
            enabled=True,
            day_start="07:00",
            night_start="23:00",
        )
        contours = with_applied_climate_schedule_profile(
            contours,
            ClimateProfile.DAY,
        )
        selected_registry = with_native_observation_bindings(selected_registry)

        class Store:
            async def async_load(self):
                return selected_registry

            async def async_save(self, registry):
                return None

        class ContourStore:
            async def async_load(self):
                return contours

            async def async_save(self, registry):
                return None

        class Bridge:
            def __init__(self) -> None:
                self.executed = []
                self.snapshot = import_climate_state(source_payload())

            async def async_fetch_state(self):
                raise AssertionError("native facade must not read the bridge")

            async def async_execute(self, plan):
                self.executed.append(plan)
                return {"ok": True}

        bridge = Bridge()
        runtime = ClimateRuntime(
            entry_id=self.entry.entry_id,
            configuration=SafeConfiguration(
                mode="shadow",
                climate_bridge_mode=ClimateControlMode.MANAGED,
            ),
            registry_store=Store(),
            contour_store=ContourStore(),
            ha_state_view=SnapshotStateView(selected_registry, bridge),
            now_ms=lambda: 1784280005000,
            local_now=lambda: datetime(
                2026,
                8,
                11,
                12,
                0,
                tzinfo=timezone.utc,
            ),
        )
        asyncio.run(runtime.async_start())
        self.hass.data["hausman_hub"]["climate_runtime"] = runtime
        views = {view.url: view for view in self.hass.http.views}
        tablet = reader_user("system-users")

        home_response = asyncio.run(
            views["/api/hausman_hub/v1/home"].get(
                FakeRequest(
                    "192.168.1.20",
                    tablet,
                    path="/api/hausman_hub/v1/home",
                )
            )
        )
        self.assertEqual(200, home_response.status)
        self.assertEqual(12, home_response.payload["contract"]["version"])
        self.assertIs(type(home_response.payload["state_revision"]), int)
        self.assertEqual(
            "current",
            home_response.payload["rooms"][0]["actual"]["data_status"],
        )
        self.assertEqual(
            "climate",
            home_response.payload["contours"][0]["id"],
        )
        living_control = home_response.payload["rooms"][0]["control"]
        # Device commands remain in the managed loop. Public actions express
        # only room-level temperature and automatic/manual intents.
        self.assertTrue(living_control["enabled"])
        self.assertEqual(
            ["set_room_target", "set_room_mode"],
            living_control["actions"],
        )
        self.assertEqual(
            ["set_room_target", "set_room_mode"],
            living_control["allowed_actions"],
        )
        self.assertEqual(
            {
                "set_room_target": {
                    "allowed": True,
                    "blocked_reasons": [],
                }
            },
            living_control["action_availability"],
        )
        self.assertEqual(
            0.5,
            living_control["action_inputs"]["set_room_target"][
                "target_temperature"
            ]["step"],
        )
        self.assertEqual(
            "Установить температуру",
            living_control["action_presentations"]["set_room_target"][
                "title"
            ],
        )
        self.assertEqual([], living_control["blocked_reasons"])
        serialized = json.dumps(home_response.payload)
        self.assertNotIn("synthetic-ac-source-living", serialized)
        self.assertNotIn("entity_id", serialized)

        retired_paths = (
            "/api/hausman_hub/v1/actions",
            "/api/hausman_hub/v1/admin/climate-shadow-evidence",
            "/api/hausman_hub/v1/admin/climate-canary-preflight",
        )
        for retired in retired_paths:
            self.assertNotIn(retired, views)
        self.assertEqual([], bridge.executed)

    def test_local_admin_creates_unsaved_climate_draft_and_tablet_cannot(self) -> None:
        """The first setup POST returns only a draft and performs no write."""

        from custom_components.hausman_hub.application.climate_registry import (
            registry_from_payload,
        )
        from custom_components.hausman_hub.application.climate_runtime import (
            ClimateRuntime,
        )
        from custom_components.hausman_hub.domain.climate_bridge import (
            ClimateControlMode,
        )
        from custom_components.hausman_hub.domain.configuration import SafeConfiguration
        from custom_components.hausman_hub.domain.contours import ContourRegistry
        from tests.climate_bridge_fixture import (
            import_climate_state,
        )
        from tests.test_climate_import import source_payload

        registry = registry_from_payload({"version": 3, "home": {"outdoor_temperature_entity_id": None, "presence_entity_id": None, "central_heating_entity_id": None}, "rooms": [{"id": "living", "name": "Living room", "window_entity_id": None}, {"id": "kids", "name": "Kids", "window_entity_id": None}], "devices": []})

        class Store:
            def __init__(self) -> None:
                self.saved = []

            async def async_load(self):
                return registry

            async def async_save(self, value):
                self.saved.append(value)

        class ContourStore:
            def __init__(self) -> None:
                self.saved = []

            async def async_load(self):
                return ContourRegistry()

            async def async_save(self, value):
                self.saved.append(value)

        class Bridge:
            def __init__(self) -> None:
                self.executed = []
                self.snapshot = import_climate_state(source_payload())

            async def async_fetch_state(self):
                return self.snapshot

            async def async_execute(self, plan):
                self.executed.append(plan)
                return {"ok": True}

        from tests.test_climate_runtime import SnapshotStateView

        store = Store()
        contour_store = ContourStore()
        bridge = Bridge()
        runtime = ClimateRuntime(
            entry_id=self.entry.entry_id,
            configuration=SafeConfiguration(
                mode="shadow",
                climate_bridge_mode=ClimateControlMode.MANAGED,
            ),
            registry_store=store,
            contour_store=contour_store,
            ha_state_view=SnapshotStateView(registry, bridge),
        )
        asyncio.run(runtime.async_start())
        self.hass.data["hausman_hub"]["climate_runtime"] = runtime
        path = "/api/hausman_hub/v1/admin/climate-drafts"
        view = {item.url: item for item in self.hass.http.views}[path]
        owner = reader_user("system-admin", admin=True)
        options_response = asyncio.run(
            view.get(FakeRequest("192.168.1.20", owner, path=path))
        )
        self.assertEqual(200, options_response.status)
        self.assertTrue(options_response.payload["draft_creation_allowed"])
        self.assertEqual(
            "hausman-hub-climate-setup-options",
            options_response.payload["contract"]["name"],
        )
        revision = options_response.payload["snapshot_revision"]
        current_setup = asyncio.run(runtime.async_current_contour_setup())
        request = {
            "snapshot_revision": revision,
            "setup_revision": current_setup["setup_revision"],
            "name": "Климат",
            "mode": "automatic",
            "rooms": [
                {
                    "room_id": "living",
                    "target_temperature": 25.0,
                    "target_humidity": 45,
                    "strategy": "normal",
                    "devices": [
                        {
                            "candidate_id": "candidate_0002",
                            "type": "air_conditioner",
                        }
                    ],
                }
            ],
        }
        missing_setup_revision = dict(request)
        missing_setup_revision.pop("setup_revision")
        missing_setup_response = asyncio.run(
            view.post(
                FakeJsonRequest(
                    "192.168.1.20",
                    owner,
                    path,
                    missing_setup_revision,
                )
            )
        )
        self.assertEqual(409, missing_setup_response.status)
        self.assertEqual([], store.saved)
        self.assertEqual([], contour_store.saved)
        self.assertEqual([], bridge.executed)

        response = asyncio.run(
            view.post(
                FakeJsonRequest(
                    "192.168.1.20",
                    owner,
                    path,
                    request,
                )
            )
        )

        self.assertEqual(200, response.status)
        self.assertEqual("created", response.payload["status"])
        self.assertFalse(response.payload["save_allowed"])
        self.assertEqual("no-store", response.headers.get("Cache-Control"))
        self.assertEqual([], store.saved)
        self.assertEqual([], contour_store.saved)
        self.assertEqual([], bridge.executed)
        oversized_draft = FakeJsonRequest(
            "192.168.1.20",
            owner,
            path,
            request,
        )
        oversized_draft.content_length = 256 * 1024 + 1
        self.assertEqual(400, asyncio.run(view.post(oversized_draft)).status)
        self.assertNotIn(
            "/api/hausman_hub/v1/actions",
            {item.url: item for item in self.hass.http.views},
        )
        validation_path = "/api/hausman_hub/v1/admin/climate-drafts/validate"
        validation_view = {
            item.url: item for item in self.hass.http.views
        }[validation_path]
        validation_response = asyncio.run(
            validation_view.post(
                FakeJsonRequest(
                    "192.168.1.20",
                    owner,
                    validation_path,
                    response.payload,
                )
            )
        )
        self.assertEqual(200, validation_response.status)
        self.assertEqual("ready", validation_response.payload["status"])
        self.assertTrue(validation_response.payload["save_allowed"])
        self.assertFalse(validation_response.payload["command_allowed"])
        self.assertEqual([], store.saved)
        self.assertEqual([], contour_store.saved)
        self.assertEqual([], bridge.executed)
        changed_request = dict(request)
        changed_request["snapshot_revision"] = revision + 1
        changed_response = asyncio.run(
            view.post(
                FakeJsonRequest(
                    "192.168.1.20",
                    owner,
                    path,
                    changed_request,
                )
            )
        )
        self.assertEqual(409, changed_response.status)
        self.assertEqual([], store.saved)
        self.assertEqual([], contour_store.saved)
        self.assertEqual([], bridge.executed)
        stale_setup_request = dict(request)
        stale_setup_request["setup_revision"] = (
            current_setup["setup_revision"] + 1
        )
        stale_setup_response = asyncio.run(
            view.post(
                FakeJsonRequest(
                    "192.168.1.20",
                    owner,
                    path,
                    stale_setup_request,
                )
            )
        )
        self.assertEqual(409, stale_setup_response.status)
        self.assertEqual([], store.saved)
        self.assertEqual([], contour_store.saved)
        self.assertEqual([], bridge.executed)
        for remote, user in (
            ("192.168.1.20", reader_user("system-users")),
            ("8.8.8.8", reader_user("system-admin", admin=True)),
        ):
            with self.subTest(remote=remote):
                self.assertEqual(
                    403,
                    asyncio.run(
                        view.post(FakeJsonRequest(remote, user, path, request))
                    ).status,
                )

        save_path = "/api/hausman_hub/v1/admin/climate-drafts/save"
        save_view = {
            item.url: item for item in self.hass.http.views
        }[save_path]
        oversized_save = FakeJsonRequest(
            "192.168.1.20",
            owner,
            save_path,
            response.payload,
        )
        oversized_save.content_length = 256 * 1024 + 1
        self.assertEqual(400, asyncio.run(save_view.post(oversized_save)).status)
        stale_draft = dict(response.payload)
        stale_draft["snapshot_revision"] += 1
        stale_save = asyncio.run(
            save_view.post(
                FakeJsonRequest(
                    "192.168.1.20",
                    owner,
                    save_path,
                    stale_draft,
                )
            )
        )
        self.assertEqual(409, stale_save.status)
        self.assertEqual([], store.saved)
        self.assertEqual([], contour_store.saved)
        for remote, user in (
            ("192.168.1.20", reader_user("system-users")),
            ("8.8.8.8", reader_user("system-admin", admin=True)),
        ):
            with self.subTest(save_remote=remote):
                self.assertEqual(
                    403,
                    asyncio.run(
                        save_view.post(
                            FakeJsonRequest(
                                remote,
                                user,
                                save_path,
                                response.payload,
                            )
                        )
                    ).status,
                )
        save_response = asyncio.run(
            save_view.post(
                FakeJsonRequest(
                    "192.168.1.20",
                    owner,
                    save_path,
                    response.payload,
                )
            )
        )
        self.assertEqual(200, save_response.status)
        self.assertEqual("saved", save_response.payload["status"])
        self.assertFalse(save_response.payload["commands_sent"])
        self.assertFalse(save_response.payload["restart_required"])
        self.assertEqual(1, len(store.saved))
        self.assertEqual(1, len(contour_store.saved))
        self.assertEqual([], bridge.executed)
        serialized = json.dumps(save_response.payload, ensure_ascii=True)
        self.assertNotIn("synthetic-ac-source-living", serialized)
        current_path = "/api/hausman_hub/v1/admin/climate-drafts/current"
        current_view = {
            item.url: item for item in self.hass.http.views
        }[current_path]
        current_response = asyncio.run(
            current_view.get(
                FakeRequest(
                    "192.168.1.20",
                    owner,
                    path=current_path,
                )
            )
        )
        self.assertEqual(200, current_response.status)
        self.assertEqual("ready", current_response.payload["status"])
        self.assertTrue(current_response.payload["editing_allowed"])
        self.assertEqual("Климат", current_response.payload["name"])
        self.assertEqual(
            25.0,
            current_response.payload["rooms"][0]["profiles"]["day"][
                "target_temperature"
            ],
        )
        self.assertEqual(1, len(store.saved))
        self.assertEqual(1, len(contour_store.saved))
        self.assertEqual([], bridge.executed)
        for remote, user in (
            ("192.168.1.20", reader_user("system-users")),
            ("8.8.8.8", reader_user("system-admin", admin=True)),
        ):
            with self.subTest(current_remote=remote):
                self.assertEqual(
                    403,
                    asyncio.run(
                        current_view.get(
                            FakeRequest(remote, user, path=current_path)
                        )
                    ).status,
                )

    def test_local_admin_updates_profiles_without_sending_device_commands(self) -> None:
        """The strict profile route saves only current configured room profiles."""

        from custom_components.hausman_hub.application.climate_runtime import (
            ClimateRuntime,
        )
        from custom_components.hausman_hub.application.contours import (
            build_climate_contour_setup,
        )
        from custom_components.hausman_hub.domain.climate_bridge import (
            ClimateControlMode,
        )
        from tests.climate_bridge_fixture import (
            import_climate_state,
        )
        from tests.test_climate_import import source_payload
        from tests.test_climate_runtime import (
            ReflectingStrictExecutor,
            configuration,
            native_application_inputs,
        )

        snapshot = import_climate_state(source_payload())
        registry, contours = build_climate_contour_setup(
            snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        registry, state_view = native_application_inputs(registry)
        executor = ReflectingStrictExecutor(state_view)

        class Store:
            def __init__(self, value: object) -> None:
                self.value = value
                self.saved: list[object] = []

            async def async_load(self):
                return self.value

            async def async_save(self, value):
                self.value = value
                self.saved.append(value)

        class Bridge:
            def __init__(self) -> None:
                self.fetch_count = 0
                self.executed: list[object] = []

            async def async_fetch_state(self):
                self.fetch_count += 1
                return snapshot

            async def async_execute(self, plan):
                self.executed.append(plan)
                return {"ok": True}

        registry_store = Store(registry)
        contour_store = Store(contours)
        bridge = Bridge()
        runtime = ClimateRuntime(
            entry_id=self.entry.entry_id,
            configuration=configuration(ClimateControlMode.MANAGED),
            registry_store=registry_store,
            contour_store=contour_store,
            strict_ha_call_executor=executor,
            ha_state_view=state_view,
            now_ms=lambda: 1784512800000,
        )
        asyncio.run(runtime.async_start())
        current = asyncio.run(runtime.async_current_contour_setup())
        fetches_before = bridge.fetch_count
        self.hass.data["hausman_hub"]["climate_runtime"] = runtime
        path = "/api/hausman_hub/v1/admin/climate-profiles"
        view = {item.url: item for item in self.hass.http.views}[path]
        owner = reader_user("system-admin", admin=True)
        request = {
            "contract": {
                "name": "hausman-hub-climate-profile-update-request",
                "version": 1,
            },
            "setup_revision": current["setup_revision"],
            "rooms": [
                {
                    "room_id": "living",
                    "profiles": {
                        "day": {
                            "target_temperature": 24.5,
                            "target_humidity": 50,
                            "strategy": "soft",
                        },
                        "night": {
                            "target_temperature": 21.5,
                            "target_humidity": 45,
                            "strategy": "normal",
                        },
                    },
                }
            ],
        }

        response = asyncio.run(
            view.post(FakeJsonRequest("192.168.1.20", owner, path, request))
        )

        self.assertEqual(200, response.status)
        self.assertEqual("saved", response.payload["status"])
        self.assertFalse(response.payload["commands_sent"])
        self.assertEqual(fetches_before, bridge.fetch_count)
        self.assertEqual([], bridge.executed)
        self.assertEqual([], executor.batches)
        self.assertEqual(1, len(contour_store.saved))
        self.assertEqual("no-store", response.headers.get("Cache-Control"))
        self.assert_climate_route_payload_redacted(response.payload)
        self.assertEqual(
            409,
            asyncio.run(
                view.post(FakeJsonRequest("192.168.1.20", owner, path, request))
            ).status,
        )
        self.assertEqual(1, len(contour_store.saved))
        self.assertEqual(
            403,
            asyncio.run(
                view.post(
                    FakeJsonRequest(
                        "192.168.1.20",
                        reader_user("system-users"),
                        path,
                        request,
                    )
                )
            ).status,
        )
        oversized = FakeJsonRequest("192.168.1.20", owner, path, request)
        oversized.content_length = 256 * 1024 + 1
        self.assertEqual(400, asyncio.run(view.post(oversized)).status)
        self.assertEqual([], bridge.executed)
        self.assertEqual([], executor.batches)

    def test_local_admin_enables_schedule_without_sending_device_commands(self) -> None:
        """The strict schedule route needs consent and only persists the timer."""

        from custom_components.hausman_hub.application.climate_runtime import (
            ClimateRuntime,
        )
        from custom_components.hausman_hub.application.contours import (
            build_climate_contour_setup,
        )
        from custom_components.hausman_hub.domain.climate_bridge import (
            ClimateControlMode,
        )
        from tests.climate_bridge_fixture import (
            import_climate_state,
        )
        from tests.test_climate_import import source_payload
        from tests.test_climate_runtime import (
            ReflectingStrictExecutor,
            configuration,
            native_application_inputs,
        )

        snapshot = import_climate_state(source_payload())
        registry, contours = build_climate_contour_setup(
            snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        registry, state_view = native_application_inputs(registry)
        executor = ReflectingStrictExecutor(state_view)

        class Store:
            def __init__(self, value: object) -> None:
                self.value = value
                self.saved: list[object] = []

            async def async_load(self):
                return self.value

            async def async_save(self, value):
                self.value = value
                self.saved.append(value)

        class Bridge:
            def __init__(self) -> None:
                self.fetch_count = 0
                self.executed: list[object] = []

            async def async_fetch_state(self):
                self.fetch_count += 1
                return snapshot

            async def async_execute(self, plan):
                self.executed.append(plan)
                return {"ok": True}

        contour_store = Store(contours)
        bridge = Bridge()
        runtime = ClimateRuntime(
            entry_id=self.entry.entry_id,
            configuration=configuration(ClimateControlMode.MANAGED),
            registry_store=Store(registry),
            contour_store=contour_store,
            strict_ha_call_executor=executor,
            ha_state_view=state_view,
            now_ms=lambda: 1784512800000,
        )
        asyncio.run(runtime.async_start())
        current = asyncio.run(runtime.async_current_contour_setup())
        fetches_before = bridge.fetch_count
        self.hass.data["hausman_hub"]["climate_runtime"] = runtime
        path = "/api/hausman_hub/v1/admin/climate-schedule"
        view = {item.url: item for item in self.hass.http.views}[path]
        owner = reader_user("system-admin", admin=True)
        request = {
            "contract": {
                "name": "hausman-hub-climate-schedule-update-request",
                "version": 1,
            },
            "setup_revision": current["setup_revision"],
            "schedule": {
                "enabled": True,
                "day_start": "06:30",
                "night_start": "22:30",
            },
            "confirm_automatic_application": True,
        }

        response = asyncio.run(
            view.post(FakeJsonRequest("192.168.1.20", owner, path, request))
        )

        self.assertEqual(200, response.status)
        self.assertEqual("saved", response.payload["status"])
        self.assertTrue(response.payload["schedule"]["enabled"])
        self.assertTrue(response.payload["automatic_application_pending"])
        self.assertFalse(response.payload["commands_sent"])
        self.assertEqual(fetches_before, bridge.fetch_count)
        self.assertEqual([], bridge.executed)
        self.assertEqual([], executor.batches)
        self.assertEqual(1, len(contour_store.saved))
        self.assertEqual("no-store", response.headers.get("Cache-Control"))
        self.assert_climate_route_payload_redacted(response.payload)
        self.assertEqual(
            409,
            asyncio.run(
                view.post(FakeJsonRequest("192.168.1.20", owner, path, request))
            ).status,
        )
        unconfirmed = copy.deepcopy(request)
        unconfirmed["setup_revision"] = response.payload["setup_revision"]
        unconfirmed["confirm_automatic_application"] = False
        self.assertEqual(
            400,
            asyncio.run(
                view.post(FakeJsonRequest("192.168.1.20", owner, path, unconfirmed))
            ).status,
        )
        self.assertEqual(
            403,
            asyncio.run(
                view.post(
                    FakeJsonRequest(
                        "192.168.1.20",
                        reader_user("system-users"),
                        path,
                        request,
                    )
                )
            ).status,
        )
        oversized = FakeJsonRequest("192.168.1.20", owner, path, request)
        oversized.content_length = 256 * 1024 + 1
        self.assertEqual(400, asyncio.run(view.post(oversized)).status)
        self.assertEqual(1, len(contour_store.saved))
        self.assertEqual([], bridge.executed)
        self.assertEqual([], executor.batches)

    def _managed_climate_views(self):
        """Build the managed runtime recipe and return the registered views."""

        from custom_components.hausman_hub.application.climate_runtime import (
            ClimateRuntime,
        )
        from custom_components.hausman_hub.application.contours import (
            build_climate_contour_setup,
            with_applied_climate_schedule_profile,
            with_climate_schedule,
        )
        from custom_components.hausman_hub.domain.climate_bridge import (
            ClimateControlMode,
        )
        from custom_components.hausman_hub.domain.contours import ClimateProfile
        from tests.climate_bridge_fixture import (
            import_climate_state,
        )
        from tests.test_climate_import import source_payload
        from tests.test_climate_runtime import (
            ReflectingStrictExecutor,
            configuration,
            native_application_inputs,
        )

        source = source_payload()
        initial = import_climate_state(source)
        registry, contours = build_climate_contour_setup(
            initial,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        contours = with_climate_schedule(
            contours,
            enabled=True,
            day_start="07:00",
            night_start="23:00",
        )
        contours = with_applied_climate_schedule_profile(
            contours,
            ClimateProfile.DAY,
        )
        source["rooms"][0]["mode"] = "manual"
        source["rooms"][0]["targets"]["temperature"] = 26
        source["rooms"][0]["targets"]["targetStrategy"] = "soft"
        registry, state_view = native_application_inputs(registry)
        executor = ReflectingStrictExecutor(state_view)

        bridge = _ManagedRecipeBridge(source)
        runtime = ClimateRuntime(
            entry_id=self.entry.entry_id,
            configuration=configuration(ClimateControlMode.MANAGED),
            registry_store=_ManagedRecipeStore(registry),
            contour_store=_ManagedRecipeStore(contours),
            strict_ha_call_executor=executor,
            ha_state_view=state_view,
            operation_id_factory=iter(("4" * 32,)).__next__,
            now_ms=lambda: 1784280005000,
        )
        asyncio.run(runtime.async_start())
        self.hass.data["hausman_hub"]["climate_runtime"] = runtime
        return (
            {view.url: view for view in self.hass.http.views},
            bridge,
            executor,
            registry,
            contours,
        )

    def test_admin_panel_shows_disabled_readiness_without_a_snapshot(self) -> None:
        """The page remains useful before the climate contour is enabled."""

        views = {view.url: view for view in self.hass.http.views}
        admin = reader_user("system-admin", admin=True)
        panel_path = "/api/hausman_hub/v1/admin/panel"

        panel = asyncio.run(
            views[panel_path].get(
                FakeRequest("192.168.1.20", admin, path=panel_path)
            )
        )

        self.assertEqual(200, panel.status)
        self.assertEqual(
            {"name": "hausman-hub-admin-panel", "version": 2},
            panel.payload["contract"],
        )
        self.assertIsNone(panel.payload["snapshot"])
        self.assertEqual("disabled", panel.payload["readiness"]["status"])
        self.assertEqual(
            ["bridge_disabled"],
            panel.payload["readiness"]["reasons"],
        )
        self.assertEqual("no-store", panel.headers.get("Cache-Control"))

    def test_admin_panel_reports_the_installed_integration_version(self) -> None:
        """The header badge reads the version from the live panel payload."""

        views = {view.url: view for view in self.hass.http.views}
        admin = reader_user("system-admin", admin=True)
        panel_path = "/api/hausman_hub/v1/admin/panel"

        jobs_before = len(self.hass.executor_jobs)
        panel = asyncio.run(
            views[panel_path].get(
                FakeRequest("192.168.1.20", admin, path=panel_path)
            )
        )

        self.assertEqual(200, panel.status)
        self.assertEqual("1.52.209", panel.payload["integration_version"])
        self.assertEqual(jobs_before + 1, len(self.hass.executor_jobs))
        self.assertEqual(
            "_integration_version",
            self.hass.executor_jobs[-1][0].__name__,
        )

    def test_admin_panel_accepts_ipv6_link_local_admin_from_mdns(self) -> None:
        """A local admin may open the panel when mDNS selects IPv6 link-local."""

        views = {view.url: view for view in self.hass.http.views}
        admin = reader_user("system-admin", admin=True)
        panel_path = "/api/hausman_hub/v1/admin/panel"

        for remote in (
            "fe80::1",
            "fe80::1%9",
            "febf:ffff:ffff:ffff:ffff:ffff:ffff:ffff",
        ):
            with self.subTest(remote=remote):
                panel = asyncio.run(
                    views[panel_path].get(
                        FakeRequest(remote, admin, path=panel_path)
                    )
                )
                self.assertEqual(200, panel.status)
                self.assertEqual(
                    {"name": "hausman-hub-admin-panel", "version": 2},
                    panel.payload["contract"],
                )

        for remote in ("fec0::1", "2001:db8::1"):
            with self.subTest(remote=remote):
                panel = asyncio.run(
                    views[panel_path].get(
                        FakeRequest(remote, admin, path=panel_path)
                    )
                )
                self.assertEqual(403, panel.status)
                self.assertEqual(
                    {"contract", "code", "message", "retryable"},
                    set(panel.payload),
                )
                self.assertEqual("forbidden", panel.payload["code"])

        tablet = reader_user("system-users")
        tablet_path = "/api/hausman_hub/v1/capabilities"
        tablet_response = asyncio.run(
            views[tablet_path].get(
                FakeRequest("fe80::1%9", tablet, path=tablet_path)
            )
        )
        self.assertEqual(403, tablet_response.status)
        self.assertEqual(
            {"contract", "code", "message", "retryable"},
            set(tablet_response.payload),
        )
        self.assertEqual("forbidden", tablet_response.payload["code"])

    def test_admin_panel_shows_managed_unavailable_readiness_without_snapshot(
        self,
    ) -> None:
        """A safely unobservable managed contour remains an explainable state."""

        from dataclasses import replace

        from custom_components.hausman_hub.domain.climate_bridge import (
            ClimateControlMode,
        )

        runtime = self.hass.data["hausman_hub"]["climate_runtime"]
        runtime.configuration = replace(
            runtime.configuration,
            climate_bridge_mode=ClimateControlMode.MANAGED,
        )
        runtime._ha_state_view = None
        views = {view.url: view for view in self.hass.http.views}
        admin = reader_user("system-admin", admin=True)
        panel_path = "/api/hausman_hub/v1/admin/panel"

        panel = asyncio.run(
            views[panel_path].get(
                FakeRequest("192.168.1.20", admin, path=panel_path)
            )
        )

        self.assertEqual(200, panel.status)
        self.assertIsNone(panel.payload["snapshot"])
        self.assertEqual("unavailable", panel.payload["readiness"]["status"])

    def test_admin_panel_keeps_internal_runtime_failures_unavailable(self) -> None:
        """An internal runtime fault must not look like a normal empty panel."""

        from custom_components.hausman_hub.application.climate_runtime import (
            ClimateRuntimeUnavailable,
        )

        runtime = self.hass.data["hausman_hub"]["climate_runtime"]

        async def unavailable_readiness():
            return {
                "status": "unavailable",
                "bridge_mode": "managed",
                "reasons": [],
            }

        async def broken_snapshot():
            raise ClimateRuntimeUnavailable(
                "climate protection memory is unavailable"
            )

        runtime.async_readiness = unavailable_readiness
        runtime.async_public_snapshot = broken_snapshot
        views = {view.url: view for view in self.hass.http.views}
        admin = reader_user("system-admin", admin=True)
        panel_path = "/api/hausman_hub/v1/admin/panel"

        panel = asyncio.run(
            views[panel_path].get(
                FakeRequest("192.168.1.20", admin, path=panel_path)
            )
        )

        self.assertEqual(503, panel.status)

    def test_admin_panel_routes_serve_and_apply_for_a_local_admin(self) -> None:
        """The sidebar panel endpoints answer only to a local administrator."""

        views, bridge, executor, registry, contours = self._managed_climate_views()
        admin = reader_user("system-admin", admin=True)
        tablet = reader_user("system-users")

        panel_path = "/api/hausman_hub/v1/admin/panel"
        panel = asyncio.run(
            views[panel_path].get(FakeRequest("192.168.1.20", admin, path=panel_path))
        )
        self.assertEqual(200, panel.status)
        self.assertEqual(
            {"name": "hausman-hub-admin-panel", "version": 2},
            panel.payload["contract"],
        )
        self.assertEqual(
            "hausman-hub-home", panel.payload["snapshot"]["contract"]["name"]
        )
        self.assertEqual(
            "hausman-hub-climate-readiness",
            panel.payload["readiness"]["contract"]["name"],
        )
        # The synthetic recipe binds a humidity sensor without a state, so
        # native readiness honestly reports the unavailable device.
        self.assertEqual("not_ready", panel.payload["readiness"]["status"])
        self.assertEqual(["device_unavailable"], panel.payload["readiness"]["reasons"])
        self.assertEqual(403, asyncio.run(
            views[panel_path].get(
                FakeRequest("192.168.1.20", tablet, path=panel_path)
            )
        ).status)
        self.assertEqual(403, asyncio.run(
            views[panel_path].get(
                FakeRequest("192.168.1.20", reader_user("system-read-only"), path=panel_path)
            )
        ).status)

        apply_path = "/api/hausman_hub/v1/admin/panel/apply"
        apply_request = {
            "request_id": "admin-panel-apply-1",
            "contour_id": "climate",
            "confirm": True,
        }
        applied = asyncio.run(
            views[apply_path].post(
                FakeJsonRequest("192.168.1.20", admin, apply_path, apply_request)
            )
        )
        self.assertEqual(200, applied.status)
        self.assertEqual(
            "hausman-hub-climate-control-receipt",
            applied.payload["contract"]["name"],
        )
        self.assertEqual("confirmed", applied.payload["status"])
        self.assertEqual(403, asyncio.run(
            views[apply_path].post(
                FakeJsonRequest("192.168.1.20", tablet, apply_path, apply_request)
            )
        ).status)

        from custom_components.hausman_hub.application.climate_runtime import (
            ClimateRuntime,
        )
        from custom_components.hausman_hub.domain.climate_bridge import (
            ClimateControlMode,
        )
        from tests.test_climate_runtime import (
            ReflectingStrictExecutor,
            configuration,
            native_application_inputs,
        )

        registry, temporary_state_view = native_application_inputs(registry)
        temporary_runtime = ClimateRuntime(
            entry_id=self.entry.entry_id,
            configuration=configuration(ClimateControlMode.MANAGED),
            registry_store=_ManagedRecipeStore(registry),
            contour_store=_ManagedRecipeStore(contours),
            strict_ha_call_executor=ReflectingStrictExecutor(temporary_state_view),
            ha_state_view=temporary_state_view,
            operation_id_factory=iter(("6" * 32,)).__next__,
            now_ms=lambda: 1784280005000,
        )
        asyncio.run(temporary_runtime.async_start())
        self.hass.data["hausman_hub"]["climate_runtime"] = temporary_runtime

        temporary_path = "/api/hausman_hub/v1/admin/panel/temporary-temperature"
        temporary_request = {
            "request_id": "admin-panel-temp-1",
            "contour_id": "climate",
            "room_id": "living",
            "action": "set",
            "target_temperature": 23.5,
            "confirm": True,
        }
        temporary = asyncio.run(
            views[temporary_path].post(
                FakeJsonRequest("192.168.1.20", admin, temporary_path, temporary_request)
            )
        )
        self.assertEqual(200, temporary.status)
        self.assertEqual("confirmed", temporary.payload["status"])
        invalid = dict(temporary_request, request_id="admin-panel-temp-2", target_temperature=None)
        self.assertEqual(400, asyncio.run(
            views[temporary_path].post(
                FakeJsonRequest("192.168.1.20", admin, temporary_path, invalid)
            )
        ).status)
        malformed = FakeJsonRequest("192.168.1.20", admin, temporary_path, {})
        malformed.content_type = "text/plain"
        self.assertEqual(400, asyncio.run(
            views[temporary_path].post(malformed)
        ).status)
        malformed_apply = FakeJsonRequest("192.168.1.20", admin, apply_path, apply_request)
        malformed_apply.content_length = 0
        self.assertEqual(400, asyncio.run(
            views[apply_path].post(malformed_apply)
        ).status)
        self.assertEqual(403, asyncio.run(
            views[temporary_path].post(
                FakeJsonRequest("192.168.1.20", tablet, temporary_path, temporary_request)
            )
        ).status)
        self.assertEqual([], bridge.executed)

    def test_managed_contour_routes_apply_once_and_confirm_engine_state(self) -> None:
        """The tablet may apply only saved settings through the managed contour."""

        views, bridge, executor, registry, contours = self._managed_climate_views()
        from custom_components.hausman_hub.application.climate_runtime import (
            ClimateRuntime,
        )
        from custom_components.hausman_hub.domain.climate_bridge import (
            ClimateControlMode,
        )
        from tests.test_climate_runtime import (
            ReflectingStrictExecutor,
            configuration,
            native_application_inputs,
        )

        tablet = reader_user("system-users")

        preview_path = "/api/hausman_hub/v1/contours/apply-preview"
        preview = asyncio.run(
            views[preview_path].get(
                FakeRequest("192.168.1.20", tablet, path=preview_path)
            )
        )
        self.assertEqual(200, preview.status)
        # Native strict HA plan call count, formerly the bridge command count.
        self.assertEqual(1, preview.payload["command_count"])
        apply_path = "/api/hausman_hub/v1/contours/apply"
        request = {
            "request_id": "tablet-managed-contour-1",
            "contour_id": "climate",
            "confirm": True,
        }
        first = asyncio.run(
            views[apply_path].post(
                FakeJsonRequest("192.168.1.20", tablet, apply_path, request)
            )
        )
        duplicate = asyncio.run(
            views[apply_path].post(
                FakeJsonRequest("192.168.1.20", tablet, apply_path, request)
            )
        )

        self.assertEqual(200, first.status)
        self.assertEqual("confirmed", first.payload["status"])
        self.assertEqual(
            {
                "name": "hausman-hub-climate-control-receipt",
                "version": 1,
            },
            first.payload["contract"],
        )
        self.assertEqual(
            "apply_saved_settings",
            first.payload["action"]["code"],
        )
        self.assertEqual("Выполнено", first.payload["status_name"])
        self.assertEqual(first.payload, duplicate.payload)
        self.assertEqual([], bridge.executed)
        self.assertEqual(1, len(executor.batches))
        self.assert_climate_route_payload_redacted(first.payload)

        registry, temporary_state_view = native_application_inputs(registry)
        temporary_executor = ReflectingStrictExecutor(temporary_state_view)
        temporary_runtime = ClimateRuntime(
            entry_id=self.entry.entry_id,
            configuration=configuration(ClimateControlMode.MANAGED),
            registry_store=_ManagedRecipeStore(registry),
            contour_store=_ManagedRecipeStore(contours),
            strict_ha_call_executor=temporary_executor,
            ha_state_view=temporary_state_view,
            operation_id_factory=iter(("5" * 32,)).__next__,
            now_ms=lambda: 1784280005000,
        )
        asyncio.run(temporary_runtime.async_start())
        self.hass.data["hausman_hub"]["climate_runtime"] = temporary_runtime
        temporary_path = "/api/hausman_hub/v1/contours/temporary-temperature"
        invalid_temporary = asyncio.run(
            views[temporary_path].post(
                FakeJsonRequest(
                    "192.168.1.20",
                    tablet,
                    temporary_path,
                    {
                        "request_id": "tablet-invalid-temperature-1",
                        "contour_id": "climate",
                        "room_id": "living",
                        "action": "set",
                        "target_temperature": 23.2,
                        "confirm": True,
                    },
                )
            )
        )
        unknown_room = asyncio.run(
            views[temporary_path].post(
                FakeJsonRequest(
                    "192.168.1.20",
                    tablet,
                    temporary_path,
                    {
                        "request_id": "tablet-unknown-room-1",
                        "contour_id": "climate",
                        "room_id": "unknown",
                        "action": "set",
                        "target_temperature": 23.5,
                        "confirm": True,
                    },
                )
            )
        )
        self.assertEqual(400, invalid_temporary.status)
        self.assertEqual(409, unknown_room.status)
        temporary_response = asyncio.run(
            views[temporary_path].post(
                FakeJsonRequest(
                    "192.168.1.20",
                    tablet,
                    temporary_path,
                    {
                        "request_id": "tablet-temporary-temperature-1",
                        "contour_id": "climate",
                        "room_id": "living",
                        "action": "set",
                        "target_temperature": 22.5,
                        "confirm": True,
                    },
                )
            )
        )
        self.assertEqual(200, temporary_response.status)
        self.assertEqual("confirmed", temporary_response.payload["status"])
        self.assertEqual(1, temporary_response.payload["room_count"])
        self.assertEqual(
            "set_temporary_temperature",
            temporary_response.payload["action"]["code"],
        )
        self.assertEqual(
            "living",
            temporary_response.payload["action"]["room_id"],
        )
        self.assertEqual(
            22.5,
            temporary_response.payload["action"]["target_temperature"],
        )
        self.assertEqual([], bridge.executed)
        self.assertEqual(1, len(executor.batches))
        self.assertEqual(1, len(temporary_executor.batches))
        self.assert_climate_route_payload_redacted(temporary_response.payload)

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

    def test_public_scenario_routes_are_available_to_the_local_tablet(self) -> None:
        views = {view.url: view for view in self.hass.http.views}
        tablet = reader_user("system-users")
        scenarios_path = "/api/hausman_hub/v1/scenarios"
        catalog_path = "/api/hausman_hub/v1/scenarios/catalog"
        health_path = "/api/hausman_hub/v1/scenarios/health"
        action_path = "/api/hausman_hub/v1/scenarios/action"

        scenarios = asyncio.run(
            views[scenarios_path].get(
                FakeRequest("192.168.1.20", tablet, path=scenarios_path)
            )
        )
        health = asyncio.run(
            views[health_path].get(
                FakeRequest("192.168.1.20", tablet, path=health_path)
            )
        )
        service = self.hass.data["hausman_hub"]["scenario_service"]
        service._registry = SimpleNamespace(  # noqa: SLF001
            scenarios=(
                SimpleNamespace(
                    id="close_curtains",
                    title="Закрыть шторы",
                    icon="mdi:curtains",
                ),
                SimpleNamespace(
                    id="legacy_icon",
                    title="Старая иконка",
                    icon="unsafe-icon",
                ),
            )
        )
        catalog = asyncio.run(
            views[catalog_path].get(
                FakeRequest("192.168.1.20", tablet, path=catalog_path)
            )
        )
        unknown_action = asyncio.run(
            views[action_path].post(
                FakeJsonRequest(
                    "192.168.1.20",
                    tablet,
                    action_path,
                    {"action": "unsupported"},
                )
            )
        )
        executions: list[str] = []

        async def run_scenario(
            scenario_id: str,
            *,
            correlation_id: str | None = None,
            trigger_context: dict[str, object] | None = None,
        ) -> dict[str, object]:
            executions.append(scenario_id)
            return {
                "run_id": correlation_id or "run-close-curtains",
                "scenario_id": scenario_id,
                "status": "completed",
                "accepted": True,
                "confirmed": True,
                "receipts": [],
            }

        service.async_run_scenario = run_scenario
        run_action = asyncio.run(
            views[action_path].post(
                FakeJsonRequest(
                    "192.168.1.20",
                    tablet,
                    action_path,
                    {"action": "run_scenario", "scenarioId": "close_curtains"},
                )
            )
        )

        self.assertEqual(200, scenarios.status)
        self.assertIn("scenarios", scenarios.payload)
        self.assertEqual(
            {"name": "hausman-hub-scenario-list", "version": 1},
            scenarios.payload["contract"],
        )
        self.assertEqual(200, catalog.status)
        self.assertIn("devices", catalog.payload)
        self.assertIn("scenarios", catalog.payload)
        self.assertEqual("warming", catalog.payload["readiness"]["status"])
        self.assertEqual("initial_scan", catalog.payload["readiness"]["reason"])
        self.assertEqual(
            {
                "id": "close_curtains",
                "title": "Закрыть шторы",
                "icon": "mdi:curtains",
            },
            catalog.payload["scenarios"][0],
        )
        self.assertNotIn("icon", catalog.payload["scenarios"][1])
        self.assertEqual(200, health.status)
        self.assertEqual(
            {"name": "hausman-hub-scenario-health", "version": 1},
            health.payload["contract"],
        )
        self.assertEqual("healthy", health.payload["status"])
        self.assertEqual([], health.payload["violations"])
        self.assertEqual(400, unknown_action.status)
        self.assertEqual(200, run_action.status)
        self.assertTrue(run_action.payload["confirmed"])
        self.assertEqual(["close_curtains"], executions)

    def test_public_device_action_route_returns_confirmed_receipt(self) -> None:
        """Tablet and local admin commands cross the HTTP boundary with read-back evidence."""

        path = "/api/hausman_hub/v1/device-actions"
        view = next(item for item in self.hass.http.views if item.url == path)
        service = self.hass.data["hausman_hub"]["scenario_service"]
        executions: list[tuple[str, str, object]] = []

        async def execute_device_action(
            target_id: str,
            action_id: str,
            value: object,
            *,
            correlation_id: str | None = None,
        ) -> dict[str, object]:
            executions.append((target_id, action_id, value))
            return {
                "requestId": "request-1",
                "targetId": target_id,
                "accepted": True,
                "confirmed": True,
                "status": "confirmed",
                "statusName": "Выполнено",
                "observedState": "on",
            }

        service.async_execute_device_action = execute_device_action
        tablet = reader_user("system-users")
        response = asyncio.run(
            view.post(
                FakeJsonRequest(
                    "192.168.1.20",
                    tablet,
                    path,
                    {"targetId": "living-light", "actionId": "turn_on"},
                )
            )
        )

        self.assertEqual(200, response.status)
        self.assertEqual(
            {"name": "hausman-hub-device-action-receipt", "version": 1},
            response.payload["contract"],
        )
        self.assertTrue(response.payload["accepted"])
        self.assertTrue(response.payload["confirmed"])
        self.assertEqual("confirmed", response.payload["status"])
        self.assertEqual("no-store", response.headers.get("Cache-Control"))
        self.assertEqual([("living-light", "turn_on", None)], executions)

        admin = asyncio.run(
            view.post(
                FakeJsonRequest(
                    "192.168.1.20",
                    reader_user("system-admin", admin=True),
                    path,
                    {"targetId": "living-light", "actionId": "turn_on"},
                )
            )
        )
        self.assertEqual(200, admin.status)
        self.assertEqual(
            [("living-light", "turn_on", None), ("living-light", "turn_on", None)],
            executions,
        )
        forbidden = asyncio.run(
            view.post(
                FakeJsonRequest(
                    "192.168.1.20",
                    reader_user("system-read-only"),
                    path,
                    {"targetId": "living-light", "actionId": "turn_on"},
                )
            )
        )
        self.assertEqual(403, forbidden.status)

    def test_intercom_requires_confirmation_and_supports_command_free_dry_run(self) -> None:
        path = "/api/hausman_hub/v1/device-actions"
        view = next(item for item in self.hass.http.views if item.url == path)
        service = self.hass.data["hausman_hub"]["scenario_service"]
        executions: list[bool] = []

        async def is_intercom(target_id: str, action_id: str) -> bool:
            return target_id == "entry-intercom" and action_id in {
                "turn_on",
                "toggle",
            }

        async def execute_device_action(
            target_id: str,
            action_id: str,
            value: object,
            *,
            correlation_id: str | None = None,
            dry_run: bool = False,
        ) -> dict[str, object]:
            executions.append(dry_run)
            return {
                "requestId": "request-intercom",
                "targetId": target_id,
                "actionId": action_id,
                "accepted": True,
                "confirmed": not dry_run,
                "status": "confirmed" if not dry_run else "accepted",
            }

        service.async_is_intercom_action = is_intercom
        service.async_execute_device_action = execute_device_action
        tablet = reader_user("system-users")
        rejected = asyncio.run(
            view.post(
                FakeJsonRequest(
                    "192.168.1.20",
                    tablet,
                    path,
                    {"targetId": "entry-intercom", "actionId": "turn_on"},
                )
            )
        )
        self.assertEqual(403, rejected.status)
        self.assertEqual([], executions)

        rejected_toggle = asyncio.run(
            view.post(
                FakeJsonRequest(
                    "192.168.1.20",
                    tablet,
                    path,
                    {"targetId": "entry-intercom", "actionId": "toggle"},
                )
            )
        )
        self.assertEqual(403, rejected_toggle.status)
        self.assertEqual([], executions)

        dry_run = asyncio.run(
            view.post(
                FakeJsonRequest(
                    "192.168.1.20",
                    tablet,
                    path,
                    {
                        "targetId": "entry-intercom",
                        "actionId": "turn_on",
                        "dryRun": True,
                    },
                )
            )
        )
        self.assertEqual(200, dry_run.status)
        self.assertTrue(dry_run.payload["dryRun"])
        self.assertEqual([True], executions)

    def test_public_device_feature_matrix_is_read_only_and_local(self) -> None:
        path = "/api/hausman_hub/v1/device-features"
        view = next(item for item in self.hass.http.views if item.url == path)

        tablet = asyncio.run(
            view.get(
                FakeRequest(
                    "192.168.1.20",
                    reader_user("system-users"),
                    path=path,
                )
            )
        )
        self.assertEqual(200, tablet.status)
        self.assertEqual(
            {"name": "hausman-hub-device-feature-matrix", "version": 1},
            tablet.payload["contract"],
        )
        self.assertEqual("upper_bound", tablet.payload["authority"]["semantics"])
        self.assertFalse(
            tablet.payload["authority"]["clientMaySynthesizeActions"]
        )
        self.assertEqual(19, len(tablet.payload["deviceTypes"]))
        self.assertEqual("no-store", tablet.headers.get("Cache-Control"))

        admin = asyncio.run(
            view.get(
                FakeRequest(
                    "192.168.1.20",
                    reader_user("system-admin", admin=True),
                    path=path,
                )
            )
        )
        self.assertEqual(200, admin.status)
        forbidden = asyncio.run(
            view.get(
                FakeRequest(
                    "192.168.1.20",
                    reader_user("system-read-only"),
                    path=path,
                )
            )
        )
        self.assertEqual(403, forbidden.status)
        non_local = asyncio.run(
            view.get(
                FakeRequest(
                    "203.0.113.10",
                    reader_user("system-users"),
                    path=path,
                )
            )
        )
        self.assertEqual(403, non_local.status)
        wrong_path = asyncio.run(
            view.get(
                FakeRequest(
                    "192.168.1.20",
                    reader_user("system-users"),
                    path=f"{path}/",
                )
            )
        )
        self.assertEqual(404, wrong_path.status)

    def test_manual_ac_off_returns_ac_to_automatic_mode_after_command(self) -> None:
        path = "/api/hausman_hub/v1/device-actions"
        view = next(item for item in self.hass.http.views if item.url == path)
        service = self.hass.data["hausman_hub"]["scenario_service"]
        runtime = self.hass.data["hausman_hub"]["climate_runtime"]
        events: list[tuple[str, object]] = []

        async def resolve_device_action(
            target_id: str, action_id: str
        ) -> tuple[str, str]:
            events.append(("resolve", (target_id, action_id)))
            return "climate.office", "climate"

        async def set_mode(entity_id: object, mode: object) -> dict[str, object]:
            events.append(("mode", (entity_id, mode)))
            return {
                "entity_id": entity_id,
                "previous_mode": "automatic",
                "mode": mode,
                "changed": True,
            }

        async def execute_device_action(
            target_id: str,
            action_id: str,
            value: object,
            *,
            correlation_id: str | None = None,
        ) -> dict[str, object]:
            events.append(("execute", (target_id, action_id, value)))
            return {"accepted": True, "confirmed": True, "status": "confirmed"}

        service.async_resolve_device_action = resolve_device_action
        service.async_execute_device_action = execute_device_action
        runtime.async_set_device_mode_for_entity = set_mode
        response = asyncio.run(
            view.post(
                FakeJsonRequest(
                    "192.168.1.20",
                    reader_user("system-users"),
                    path,
                    {"targetId": "office-ac", "actionId": "turn_off"},
                )
            )
        )

        self.assertEqual(200, response.status)
        self.assertEqual("automatic", response.payload["climateMode"])
        self.assertEqual("Автоматический режим", response.payload["climateModeName"])
        self.assertEqual(
            [
                ("resolve", ("office-ac", "turn_off")),
                ("execute", ("office-ac", "turn_off", None)),
                ("mode", ("climate.office", "automatic")),
            ],
            events,
        )

    def test_rejected_manual_ac_off_keeps_existing_contour_ownership(self) -> None:
        path = "/api/hausman_hub/v1/device-actions"
        view = next(item for item in self.hass.http.views if item.url == path)
        service = self.hass.data["hausman_hub"]["scenario_service"]
        runtime = self.hass.data["hausman_hub"]["climate_runtime"]
        modes: list[tuple[object, object]] = []

        async def resolve_device_action(
            target_id: str, action_id: str
        ) -> tuple[str, str]:
            return "climate.office", "climate"

        async def set_mode(entity_id: object, mode: object) -> dict[str, object]:
            modes.append((entity_id, mode))
            return {
                "entity_id": entity_id,
                "previous_mode": "automatic" if mode == "manual" else "manual",
                "mode": mode,
                "changed": True,
            }

        async def execute_device_action(
            target_id: str,
            action_id: str,
            value: object,
            *,
            correlation_id: str | None = None,
        ) -> dict[str, object]:
            return {"accepted": False, "confirmed": False, "status": "rejected"}

        service.async_resolve_device_action = resolve_device_action
        service.async_execute_device_action = execute_device_action
        runtime.async_set_device_mode_for_entity = set_mode
        response = asyncio.run(
            view.post(
                FakeJsonRequest(
                    "192.168.1.20",
                    reader_user("system-users"),
                    path,
                    {"targetId": "office-ac", "actionId": "turn_off"},
                )
            )
        )

        self.assertEqual(409, response.status)
        self.assertNotIn("climateMode", response.payload)
        self.assertEqual([], modes)

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
        self.assertEqual(95, len(self.hass.http.views))
        self.assertEqual(
            1,
            sum(
                view.url == "/api/hausman_hub/local-summary"
                for view in self.hass.http.views
            ),
        )

    def test_local_admin_reads_and_updates_command_free_deviation_guard(self) -> None:
        self.assertTrue(asyncio.run(self.integration.async_setup_entry(self.hass, self.entry)))
        path = "/api/hausman_hub/v1/admin/climate-deviation-guard"
        view = {item.url: item for item in self.hass.http.views}[path]
        owner = reader_user("system-admin", admin=True)

        initial = asyncio.run(view.get(FakeRequest("192.168.1.20", owner, path=path)))
        self.assertEqual(200, initial.status)
        self.assertEqual(
            {"name": "hausman-hub-climate-deviation-guard", "version": 1},
            initial.payload["contract"],
        )
        self.assertEqual({"devices": []}, initial.payload["settings"])

        updated = asyncio.run(
            view.put(
                FakeJsonRequest(
                    "192.168.1.20",
                    owner,
                    path,
                    {"expectedRevision": 0, "settings": {"devices": []}},
                )
            )
        )
        self.assertEqual(200, updated.status)
        self.assertEqual(1, updated.payload["revision"])
        self.assertEqual(
            [
                (self.entry, ("sensor", "switch")),
                (self.entry, ("sensor", "switch")),
            ],
            self.hass.config_entries.forwarded,
        )

    def test_saved_setting_change_reloads_only_this_hausmanhub_entry(self) -> None:
        """A saved setting must ask Home Assistant to reload only HausmanHub."""

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

    def test_realtime_leak_alert_is_immediate_and_hides_entity_id(self) -> None:
        runtime = self.hass.data["hausman_hub"]["event_stream_runtime"]
        queue = runtime.broker.subscribe()
        runtime._publish_critical_alert(
            "binary_sensor.synthetic_private_leak",
            SimpleNamespace(state="off", attributes={}),
            SimpleNamespace(
                state="on",
                attributes={
                    "device_class": "moisture",
                    "friendly_name": "Датчик протечки",
                    "area_name": "Ванная",
                },
            ),
        )

        message = asyncio.run(queue.get())

        self.assertEqual("critical_alert", message["type"])
        self.assertEqual("leak", message["data"]["kind"])
        self.assertTrue(message["data"]["active"])
        self.assertEqual("Ванная", message["data"]["room"])
        self.assertNotIn("entity_id", json.dumps(message, ensure_ascii=False))

    def test_realtime_low_battery_alert_uses_profile_threshold_and_location(self) -> None:
        runtime = self.hass.data["hausman_hub"]["event_stream_runtime"]
        queue = runtime.broker.subscribe()
        runtime._publish_low_battery_alert(
            "sensor.synthetic_private_battery",
            SimpleNamespace(state="9", attributes={}),
            SimpleNamespace(
                state="7",
                attributes={
                    "device_class": "battery",
                    "friendly_name": "Датчик окна",
                    "area_name": "Гостиная",
                },
            ),
        )

        message = asyncio.run(queue.get())

        self.assertEqual("attention_alert", message["type"])
        self.assertEqual("low_battery", message["data"]["kind"])
        self.assertEqual("Датчик окна", message["data"]["device"])
        self.assertEqual("Гостиная", message["data"]["room"])
        self.assertEqual(7.0, message["data"]["value"])
        self.assertNotIn("entity_id", json.dumps(message, ensure_ascii=False))

    def test_realtime_battery_voltage_does_not_publish_a_percent_alert(self) -> None:
        runtime = self.hass.data["hausman_hub"]["event_stream_runtime"]
        queue = runtime.broker.subscribe()
        runtime._publish_low_battery_alert(
            "sensor.synthetic_private_battery_voltage",
            SimpleNamespace(state="3.1", attributes={}),
            SimpleNamespace(
                state="3.0",
                attributes={
                    "device_class": "voltage",
                    "unit_of_measurement": "V",
                    "friendly_name": "Напряжение батареи",
                },
            ),
        )

        self.assertTrue(queue.empty())

    def test_command_receipt_is_published_without_private_entity_id(self) -> None:
        from custom_components.hausman_hub.application.operation_journal import (
            OperationJournalService,
        )
        from custom_components.hausman_hub.realtime_api import publish_command_receipt

        runtime = self.hass.data["hausman_hub"]["event_stream_runtime"]
        queue = runtime.broker.subscribe()

        class Store:
            payload = None

            async def async_load(self):
                return self.payload

            async def async_save(self, payload):
                self.payload = payload

        journal = OperationJournalService(Store(), now_ms=lambda: 1786375200000)
        self.hass.data["hausman_hub"]["operation_journal"] = journal
        pending = []
        self.hass.async_create_task = pending.append

        publish_command_receipt(
            self.hass,
            {
                "requestId": "request-1",
                "correlationId": "corr.device-action.0001",
                "accepted": True,
                "confirmed": True,
                "targetId": "device_public_1",
                "message": "Устройство подтвердило новое состояние.",
            },
            operation="device_action",
        )
        asyncio.run(pending[0])
        message = asyncio.run(queue.get())

        self.assertEqual("command_receipt", message["type"])
        self.assertEqual("request-1", message["data"]["request_id"])
        self.assertEqual("corr.device-action.0001", message["correlation_id"])
        self.assertEqual("corr.device-action.0001", message["data"]["correlation_id"])
        self.assertEqual("confirmed", message["data"]["status"])
        self.assertNotIn("entity_id", json.dumps(message, ensure_ascii=False))
        record = journal.snapshot()["records"][0]
        self.assertEqual("corr.device-action.0001", record["correlation_id"])
        self.assertEqual("device", record["source"])
        self.assertNotIn("target_id", record)

    def test_device_action_batch_returns_each_target_receipt(self) -> None:
        views = {view.url: view for view in self.hass.http.views}
        path = "/api/hausman_hub/v1/device-actions/batch"
        tablet = reader_user("system-users")
        service = self.hass.data["hausman_hub"]["scenario_service"]
        calls: list[tuple[list[dict[str, object]], str]] = []

        async def execute_batch(
            actions: list[dict[str, object]],
            *,
            correlation_id: str,
        ) -> list[dict[str, object]]:
            calls.append((actions, correlation_id))
            return [
                {
                    "requestId": "batch-device-1",
                    "correlationId": correlation_id,
                    "targetId": "light_1",
                    "actionId": "turn_off",
                    "accepted": True,
                    "confirmed": True,
                    "status": "confirmed",
                },
                {
                    "requestId": "batch-device-2",
                    "correlationId": correlation_id,
                    "targetId": "light_2",
                    "actionId": "turn_off",
                    "accepted": False,
                    "confirmed": False,
                    "status": "failed",
                },
            ]

        service.async_execute_device_action_batch = execute_batch
        payload = {
            "contract": {
                "name": "hausman-hub-device-action-batch-request",
                "version": 1,
            },
            "correlationId": "room-off-1",
            "actions": [
                {"targetId": "light_1", "actionId": "turn_off"},
                {"targetId": "light_2", "actionId": "turn_off"},
            ],
        }

        response = asyncio.run(
            views[path].post(FakeJsonRequest("192.168.1.20", tablet, path, payload))
        )

        self.assertEqual(200, response.status)
        self.assertEqual("partial", response.payload["status"])
        self.assertEqual(2, response.payload["total"])
        self.assertEqual(1, response.payload["confirmedCount"])
        self.assertEqual(1, response.payload["failedCount"])
        self.assertEqual([("light_1", "light_2")], [
            tuple(item["targetId"] for item in actions) for actions, _ in calls
        ])

        duplicate = copy.deepcopy(payload)
        duplicate["actions"] = [payload["actions"][0], payload["actions"][0]]
        duplicate_response = asyncio.run(
            views[path].post(
                FakeJsonRequest("192.168.1.20", tablet, path, duplicate)
            )
        )
        self.assertEqual(400, duplicate_response.status)
        self.assertEqual(1, len(calls))

    def test_full_device_action_returns_evidence_aware_light_receipt(self) -> None:
        views = {view.url: view for view in self.hass.http.views}
        path = "/api/hausman_hub/v1/device-actions"
        tablet = reader_user("system-users")
        service = self.hass.data["hausman_hub"]["scenario_service"]
        entity_id = "light.synthetic_full_protocol"
        self.hass.states.values[entity_id] = SimpleNamespace(
            state="off",
            attributes={},
            last_updated=datetime.now(timezone.utc),
        )

        async def resolve_context(target_id: str, action_id: str):
            self.assertEqual(("light_full", "turn_on"), (target_id, action_id))
            return entity_id, "light", ("turn_on", "turn_off")

        async def execute_action(
            target_id: str,
            action_id: str,
            value: object,
            *,
            correlation_id: str | None = None,
            **_options: object,
        ) -> dict[str, object]:
            self.hass.states.values[entity_id] = SimpleNamespace(
                state="on",
                attributes={},
                last_updated=datetime.now(timezone.utc),
            )
            return {
                "correlationId": correlation_id,
                "requestId": _options["request_id"],
                "targetId": target_id,
                "actionId": action_id,
                "accepted": True,
                "confirmed": True,
                "status": "confirmed",
                "statusName": "Выполнено",
                "appliedAt": int(time.time() * 1000),
                "message": "Свет включён.",
                "confirmationWindowMs": 8000,
                "readBack": {
                    "attempted": True,
                    "matched": True,
                    "observedAt": int(time.time() * 1000),
                    "observedState": "on",
                    "attempts": 1,
                },
                "reason": None,
            }

        service.async_resolve_device_action_context = resolve_context
        service.async_execute_device_action = execute_action
        response = asyncio.run(
            views[path].post(
                FakeJsonRequest(
                    "192.168.1.20",
                    tablet,
                    path,
                    {
                        "contract": {
                            "name": "hausman-hub-device-action-request",
                            "version": 1,
                        },
                        "correlationId": "full.light.1",
                        "requestId": "full.light.request.1",
                        "targetId": "light_full",
                        "actionId": "turn_on",
                    },
                    content_type="application/vnd.hausmanhub.device-action-request.full+json",
                    accept="application/vnd.hausmanhub.device-action-receipt.full+json",
                )
            )
        )

        self.assertEqual(200, response.status)
        self.assertEqual("light", response.payload["targetType"])
        self.assertEqual("executed", response.payload["decision"])
        self.assertTrue(response.payload["commandSent"])
        self.assertEqual("manual", response.payload["ownership"])
        self.assertEqual(
            "application/vnd.hausmanhub.device-action-receipt.full+json",
            response.headers["Content-Type"],
        )

    def test_external_gate_requires_full_confirmation_and_fresh_state(self) -> None:
        path = "/api/hausman_hub/v1/device-actions"
        view = next(item for item in self.hass.http.views if item.url == path)
        service = self.hass.data["hausman_hub"]["scenario_service"]
        entity_id = "cover.synthetic_garage_gate"
        self.hass.states.values[entity_id] = SimpleNamespace(
            state="closed",
            attributes={},
            last_updated=datetime.now(timezone.utc),
        )
        executions = 0

        async def resolve_context(_target_id: str, _action_id: str):
            return entity_id, "cover", ("open_cover",), "open_cover"

        async def is_intercom(_target_id: str, _action_id: str) -> bool:
            return False

        async def execute_action(*_args: object, **_kwargs: object):
            nonlocal executions
            executions += 1
            raise AssertionError("blocked gate command reached the executor")

        service.async_resolve_device_action_context = resolve_context
        service.async_is_intercom_action = is_intercom
        service.is_contextually_dangerous_action = lambda *_args: True
        service.is_external_cover_action = lambda *_args: True
        service.async_execute_device_action = execute_action
        tablet = reader_user("system-users")

        legacy = asyncio.run(
            view.post(
                FakeJsonRequest(
                    "192.168.1.20",
                    tablet,
                    path,
                    {"targetId": "garage_gate", "actionId": "open_cover"},
                )
            )
        )
        self.assertEqual(403, legacy.status)

        self.hass.states.values[entity_id] = SimpleNamespace(
            state="closed",
            attributes={},
            last_updated=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        stale = asyncio.run(
            view.post(
                FakeJsonRequest(
                    "192.168.1.20",
                    tablet,
                    path,
                    {
                        "contract": {
                            "name": "hausman-hub-device-action-request",
                            "version": 1,
                        },
                        "correlationId": "garage.open.1",
                        "requestId": "garage.open.request.1",
                        "targetId": "garage_gate",
                        "actionId": "open_cover",
                        "confirmedByUser": True,
                        "idempotencyKey": "garage.open.key.1",
                    },
                    content_type="application/vnd.hausmanhub.device-action-request.full+json",
                    accept="application/vnd.hausmanhub.device-action-receipt.full+json",
                )
            )
        )
        self.assertEqual(409, stale.status)
        self.assertEqual(0, executions)

    def test_full_ordinary_action_replays_without_dispatch(self) -> None:
        views = {view.url: view for view in self.hass.http.views}
        path = "/api/hausman_hub/v1/device-actions"
        tablet = reader_user("system-users")
        service = self.hass.data["hausman_hub"]["scenario_service"]
        calls: list[str] = []

        async def resolve_context(_target_id: str, _action_id: str):
            return "switch.synthetic_ordinary", "switch", ("turn_on",)

        async def execute_action(
            target_id: str,
            action_id: str,
            value: object,
            *,
            correlation_id: str | None = None,
            **options: object,
        ) -> dict[str, object]:
            request_id = str(options["request_id"])
            calls.append(request_id)
            return {
                "correlationId": correlation_id,
                "requestId": request_id,
                "targetId": target_id,
                "actionId": action_id,
                "accepted": True,
                "confirmed": True,
                "status": "confirmed",
            }

        service.async_resolve_device_action_context = resolve_context
        service.async_execute_device_action = execute_action
        payload = {
            "contract": {
                "name": "hausman-hub-device-action-request",
                "version": 1,
            },
            "correlationId": "ordinary.full.1",
            "requestId": "ordinary.full.request.1",
            "targetId": "switch_ordinary",
            "actionId": "turn_on",
        }

        def send(current: dict[str, object]) -> FakeResponse:
            return asyncio.run(
                views[path].post(
                    FakeJsonRequest(
                        "192.168.1.20",
                        tablet,
                        path,
                        current,
                        content_type="application/vnd.hausmanhub.device-action-request.full+json",
                        accept="application/vnd.hausmanhub.device-action-receipt.full+json",
                    )
                )
            )

        first = send(payload)
        replay = send(copy.deepcopy(payload))

        self.assertEqual(200, first.status)
        self.assertEqual(first.payload, replay.payload)
        self.assertEqual([first.payload["requestId"]], calls)

    def test_full_ordinary_batch_replays_without_dispatch(self) -> None:
        views = {view.url: view for view in self.hass.http.views}
        path = "/api/hausman_hub/v1/device-actions/batch"
        tablet = reader_user("system-users")
        service = self.hass.data["hausman_hub"]["scenario_service"]
        calls: list[tuple[str, ...]] = []

        async def resolve_context(target_id: str, _action_id: str):
            return f"switch.synthetic_{target_id}", "switch", ("turn_off",)

        async def is_intercom(_target_id: str, _action_id: str) -> bool:
            return False

        async def execute_batch(
            actions: list[dict[str, object]],
            *,
            correlation_id: str,
            request_ids: tuple[str, ...],
            dispatch_contexts: tuple[object, ...],
        ) -> list[dict[str, object]]:
            self.assertEqual(2, len(dispatch_contexts))
            calls.append(request_ids)
            return [
                {
                    "correlationId": correlation_id,
                    "requestId": request_ids[index],
                    "targetId": str(item["targetId"]),
                    "actionId": str(item["actionId"]),
                    "accepted": True,
                    "confirmed": True,
                    "status": "confirmed",
                }
                for index, item in enumerate(actions)
            ]

        service.async_resolve_device_action_context = resolve_context
        service.async_is_intercom_action = is_intercom
        service.async_execute_device_action_batch = execute_batch
        payload = {
            "contract": {
                "name": "hausman-hub-device-action-batch-request",
                "version": 1,
            },
            "correlationId": "ordinary.batch.1",
            "requestId": "ordinary.batch.request.1",
            "actions": [
                {"targetId": "one", "actionId": "turn_off"},
                {"targetId": "two", "actionId": "turn_off"},
            ],
        }

        def send(current: dict[str, object]) -> FakeResponse:
            return asyncio.run(
                views[path].post(
                    FakeJsonRequest(
                        "192.168.1.20",
                        tablet,
                        path,
                        current,
                        content_type="application/vnd.hausmanhub.device-action-batch-request.full+json",
                        accept="application/vnd.hausmanhub.device-action-batch-receipt.full+json",
                    )
                )
            )

        first = send(payload)
        replay = send(copy.deepcopy(payload))

        self.assertEqual(200, first.status)
        self.assertEqual(first.payload, replay.payload)
        self.assertEqual(1, len(calls))
        self.assertEqual(2, len(calls[0]))

    def test_full_action_capacity_fails_closed_for_single_and_batch_after_reload(self) -> None:
        from jsonschema import Draft202012Validator

        from custom_components.hausman_hub.application.device_action_idempotency import (
            DangerousActionIdempotency,
            MAX_DANGEROUS_IDEMPOTENCY_RECORDS,
        )

        views = {view.url: view for view in self.hass.http.views}
        tablet = reader_user("system-users")
        service = self.hass.data["hausman_hub"]["scenario_service"]
        idempotency = self.hass.data["hausman_hub"]["device_action_idempotency"]
        dispatches: list[str] = []

        async def resolve_context(_target_id: str, _action_id: str):
            return "switch.synthetic_capacity", "switch", ("turn_on",), "turn_on"

        async def execute_action(
            target_id: str,
            action_id: str,
            _value: object,
            *,
            correlation_id: str | None = None,
            **options: object,
        ) -> dict[str, object]:
            request_id = str(options["request_id"])
            dispatches.append(request_id)
            return {
                "correlationId": correlation_id,
                "requestId": request_id,
                "targetId": target_id,
                "actionId": action_id,
                "accepted": True,
                "confirmed": True,
                "status": "confirmed",
            }

        service.async_resolve_device_action_context = resolve_context
        service.async_execute_device_action = execute_action
        reserve_calls = 0
        original_reserve = idempotency.async_reserve

        async def reserve(*args: object, **kwargs: object):
            nonlocal reserve_calls
            reserve_calls += 1
            return await original_reserve(*args, **kwargs)

        idempotency.async_reserve = reserve

        def single(index: int) -> FakeResponse:
            return asyncio.run(
                views["/api/hausman_hub/v1/device-actions"].post(
                    FakeJsonRequest(
                        "192.168.1.20",
                        tablet,
                        "/api/hausman_hub/v1/device-actions",
                        {
                            "contract": {"name": "hausman-hub-device-action-request", "version": 1},
                            "correlationId": f"capacity.single.{index}",
                            "requestId": f"capacity.single.request.{index}",
                            "targetId": "capacity_switch",
                            "actionId": "turn_on",
                        },
                        content_type="application/vnd.hausmanhub.device-action-request.full+json",
                        accept="application/vnd.hausmanhub.device-action-receipt.full+json",
                    )
                )
            )

        for index in range(MAX_DANGEROUS_IDEMPOTENCY_RECORDS):
            self.assertEqual(200, single(index).status)
        self.assertEqual(MAX_DANGEROUS_IDEMPOTENCY_RECORDS, len(dispatches))
        cached = single(0)
        self.assertEqual(200, cached.status)
        self.assertEqual(MAX_DANGEROUS_IDEMPOTENCY_RECORDS, len(dispatches))

        error_schema = json.loads(
            (ROOT / "custom_components/hausman_hub/contracts/v1/api-error.schema.json").read_text(encoding="utf-8")
        )
        single_full = single(MAX_DANGEROUS_IDEMPOTENCY_RECORDS)
        self.assertEqual(503, single_full.status)
        Draft202012Validator(error_schema).validate(single_full.payload)
        self.assertEqual(
            {
                "contract": {"name": "hausman-hub-error", "version": 1},
                "code": "unavailable",
                "message": "HausmanHub временно недоступен. Проверьте подключение и повторите позже.",
                "retryable": True,
                "requestId": single_full.payload["requestId"],
            },
            single_full.payload,
        )
        self.assertEqual(MAX_DANGEROUS_IDEMPOTENCY_RECORDS, len(dispatches))

        batch = asyncio.run(
            views["/api/hausman_hub/v1/device-actions/batch"].post(
                FakeJsonRequest(
                    "192.168.1.20",
                    tablet,
                    "/api/hausman_hub/v1/device-actions/batch",
                    {
                        "contract": {"name": "hausman-hub-device-action-batch-request", "version": 1},
                        "correlationId": "capacity.batch.new",
                        "requestId": "capacity.batch.request.new",
                        "actions": [{"targetId": "capacity_switch", "actionId": "turn_on"}],
                    },
                    content_type="application/vnd.hausmanhub.device-action-batch-request.full+json",
                    accept="application/vnd.hausmanhub.device-action-batch-receipt.full+json",
                )
            )
        )
        self.assertEqual(503, batch.status)
        Draft202012Validator(error_schema).validate(batch.payload)
        self.assertEqual("unavailable", batch.payload["code"])
        self.assertTrue(batch.payload["retryable"])
        self.assertNotIn("details", batch.payload)
        self.assertEqual(MAX_DANGEROUS_IDEMPOTENCY_RECORDS + 3, reserve_calls)
        self.assertEqual(MAX_DANGEROUS_IDEMPOTENCY_RECORDS, len(dispatches))

        reloaded = DangerousActionIdempotency(idempotency._store)  # noqa: SLF001
        asyncio.run(reloaded.async_load())
        self.hass.data["hausman_hub"]["device_action_idempotency"] = reloaded
        after_reload = single(MAX_DANGEROUS_IDEMPOTENCY_RECORDS + 1)
        self.assertEqual(503, after_reload.status)
        Draft202012Validator(error_schema).validate(after_reload.payload)
        self.assertEqual(MAX_DANGEROUS_IDEMPOTENCY_RECORDS, len(dispatches))

    def test_intercom_mark_dispatching_failure_cancels_only_prepared_obligation(self) -> None:
        views = {view.url: view for view in self.hass.http.views}
        path = "/api/hausman_hub/v1/device-actions"
        tablet = reader_user("system-users")
        service = self.hass.data["hausman_hub"]["scenario_service"]
        idempotency = self.hass.data["hausman_hub"]["device_action_idempotency"]
        prepared_requests: list[dict[str, object]] = []
        cancelled_requests: list[tuple[str, dict[str, object]]] = []
        executions: list[str] = []

        async def resolve_context(_target_id: str, _action_id: str):
            return "switch.synthetic_intercom", "switch", ("turn_on",), "turn_on"

        async def is_intercom(_target_id: str, _action_id: str) -> bool:
            return True

        async def prepare_release(
            _target_id: str,
            _action_id: str,
            **options: object,
        ) -> int:
            prepared_requests.append(options)
            return 5

        async def cancel_release(
            target_id: str,
            **options: object,
        ) -> bool:
            cancelled_requests.append((target_id, options))
            return True

        async def execute_action(
            target_id: str,
            action_id: str,
            value: object,
            *,
            correlation_id: str | None = None,
            **options: object,
        ) -> dict[str, object]:
            request_id = str(options["request_id"])
            executions.append(request_id)
            return {
                "correlationId": correlation_id,
                "requestId": request_id,
                "targetId": target_id,
                "actionId": action_id,
                "accepted": True,
                "confirmed": True,
                "status": "confirmed",
            }

        service.async_resolve_device_action_context = resolve_context
        service.async_is_intercom_action = is_intercom
        service.async_prepare_intercom_release = prepare_release
        service.async_cancel_intercom_release = cancel_release
        service.async_execute_device_action = execute_action
        original_mark_dispatching = idempotency.async_mark_dispatching

        async def fail_mark_dispatching(_key: str) -> None:
            raise OSError("mark dispatching failed")

        idempotency.async_mark_dispatching = fail_mark_dispatching
        payload = {
            "contract": {
                "name": "hausman-hub-device-action-request",
                "version": 1,
            },
            "correlationId": "intercom.failure.1",
            "requestId": "intercom.failure.request.1",
            "targetId": "intercom_failure",
            "actionId": "turn_on",
            "confirmedByUser": True,
            "idempotencyKey": "intercom.failure.key.1",
        }

        def send(current: dict[str, object]) -> FakeResponse:
            return asyncio.run(
                views[path].post(
                    FakeJsonRequest(
                        "192.168.1.20",
                        tablet,
                        path,
                        current,
                        content_type="application/vnd.hausmanhub.device-action-request.full+json",
                        accept="application/vnd.hausmanhub.device-action-receipt.full+json",
                    )
                )
            )

        try:
            failed = send(payload)
        finally:
            idempotency.async_mark_dispatching = original_mark_dispatching

        self.assertEqual(503, failed.status)
        self.assertEqual(1, len(prepared_requests))
        self.assertEqual(1, len(cancelled_requests))
        self.assertEqual("intercom_failure", cancelled_requests[0][0])
        self.assertEqual(
            prepared_requests[0]["request_id"],
            cancelled_requests[0][1]["expected_request_id"],
        )
        self.assertEqual(
            "switch.synthetic_intercom",
            cancelled_requests[0][1]["expected_entity_id"],
        )
        next_payload = {
            **payload,
            "correlationId": "intercom.failure.2",
            "requestId": "intercom.failure.request.2",
            "idempotencyKey": "intercom.failure.key.2",
        }
        response = send(next_payload)
        self.assertEqual(200, response.status)
        self.assertEqual(2, len(prepared_requests))
        self.assertEqual(1, len(executions))

    def test_intercom_returned_failed_receipt_cancels_unarmed_single_release(self) -> None:
        views = {view.url: view for view in self.hass.http.views}
        path = "/api/hausman_hub/v1/device-actions"
        tablet = reader_user("system-users")
        service = self.hass.data["hausman_hub"]["scenario_service"]
        prepared: list[dict[str, object]] = []
        cancelled: list[dict[str, object]] = []
        executions: list[str] = []

        async def resolve_context(_target_id: str, _action_id: str):
            return "switch.synthetic_intercom_failed", "switch", ("turn_on",), "turn_on"

        async def is_intercom(_target_id: str, _action_id: str) -> bool:
            return True

        async def prepare_release(_target_id: str, _action_id: str, **options: object) -> int:
            prepared.append(options)
            return 5

        async def cancel_release(_target_id: str, **options: object) -> bool:
            cancelled.append(options)
            return True

        async def execute_action(
            target_id: str,
            action_id: str,
            _value: object,
            *,
            correlation_id: str | None = None,
            **options: object,
        ) -> dict[str, object]:
            executions.append(str(options["request_id"]))
            return {
                "correlationId": correlation_id,
                "requestId": options["request_id"],
                "targetId": target_id,
                "actionId": action_id,
                "accepted": False,
                "confirmed": False,
                "status": "failed",
                "error": "device_action_failed",
            }

        service.async_resolve_device_action_context = resolve_context
        service.async_is_intercom_action = is_intercom
        service.async_prepare_intercom_release = prepare_release
        service.async_cancel_intercom_release = cancel_release
        service.async_execute_device_action = execute_action
        payload = {
            "contract": {
                "name": "hausman-hub-device-action-request",
                "version": 1,
            },
            "correlationId": "intercom.failed.single.1",
            "requestId": "intercom.failed.single.request.1",
            "targetId": "intercom_failed",
            "actionId": "turn_on",
            "confirmedByUser": True,
            "idempotencyKey": "intercom.failed.single.key.1",
        }

        def send(current: dict[str, object]) -> FakeResponse:
            return asyncio.run(
                views[path].post(
                    FakeJsonRequest(
                        "192.168.1.20",
                        tablet,
                        path,
                        current,
                        content_type="application/vnd.hausmanhub.device-action-request.full+json",
                        accept="application/vnd.hausmanhub.device-action-receipt.full+json",
                    )
                )
            )

        first = send(payload)
        second = send(
            {
                **payload,
                "correlationId": "intercom.failed.single.2",
                "requestId": "intercom.failed.single.request.2",
                "idempotencyKey": "intercom.failed.single.key.2",
            }
        )

        self.assertEqual(409, first.status)
        self.assertEqual(409, second.status)
        self.assertNotIn("releaseReceiptPending", first.payload)
        self.assertEqual(2, len(prepared))
        self.assertEqual(2, len(cancelled))
        self.assertEqual(2, len(executions))

    def test_intercom_returned_failed_receipt_cancels_unarmed_batch_release(self) -> None:
        views = {view.url: view for view in self.hass.http.views}
        path = "/api/hausman_hub/v1/device-actions/batch"
        tablet = reader_user("system-users")
        service = self.hass.data["hausman_hub"]["scenario_service"]
        prepared: list[dict[str, object]] = []
        cancelled: list[dict[str, object]] = []
        executions = 0

        async def resolve_context(target_id: str, action_id: str):
            if target_id == "intercom_batch":
                return "switch.synthetic_intercom_batch", "switch", (action_id,), "turn_on"
            return "switch.synthetic_batch_other", "switch", (action_id,), "turn_on"

        async def is_intercom(target_id: str, _action_id: str) -> bool:
            return target_id == "intercom_batch"

        async def prepare_release(_target_id: str, _action_id: str, **options: object) -> int:
            prepared.append(options)
            return 5

        async def cancel_release(_target_id: str, **options: object) -> bool:
            cancelled.append(options)
            return True

        async def execute_batch(
            actions: list[dict[str, object]],
            *,
            correlation_id: str,
            request_ids: tuple[str, ...],
            **_options: object,
        ) -> list[dict[str, object]]:
            nonlocal executions
            executions += 1
            return [
                {
                    "correlationId": correlation_id,
                    "requestId": request_ids[index],
                    "targetId": str(item["targetId"]),
                    "actionId": str(item["actionId"]),
                    "accepted": index != 0,
                    "confirmed": index != 0,
                    "status": "confirmed" if index != 0 else "failed",
                }
                for index, item in enumerate(actions)
            ]

        service.async_resolve_device_action_context = resolve_context
        service.async_is_intercom_action = is_intercom
        service.async_prepare_intercom_release = prepare_release
        service.async_cancel_intercom_release = cancel_release
        service.async_execute_device_action_batch = execute_batch
        payload = {
            "contract": {
                "name": "hausman-hub-device-action-batch-request",
                "version": 1,
            },
            "correlationId": "intercom.failed.batch.1",
            "requestId": "intercom.failed.batch.request.1",
            "actions": [
                {
                    "targetId": "intercom_batch",
                    "actionId": "turn_on",
                    "confirmedByUser": True,
                    "idempotencyKey": "intercom.failed.batch.key.1",
                },
                {"targetId": "batch_other", "actionId": "turn_on"},
            ],
        }

        def send(current: dict[str, object]) -> FakeResponse:
            return asyncio.run(
                views[path].post(
                    FakeJsonRequest(
                        "192.168.1.20",
                        tablet,
                        path,
                        current,
                        content_type="application/vnd.hausmanhub.device-action-batch-request.full+json",
                        accept="application/vnd.hausmanhub.device-action-batch-receipt.full+json",
                    )
                )
            )

        first = send(payload)
        second = send(
            {
                **payload,
                "correlationId": "intercom.failed.batch.2",
                "requestId": "intercom.failed.batch.request.2",
                "actions": [
                    {
                        "targetId": "intercom_batch",
                        "actionId": "turn_on",
                        "confirmedByUser": True,
                        "idempotencyKey": "intercom.failed.batch.key.2",
                    },
                    {"targetId": "batch_other", "actionId": "turn_on"},
                ],
            }
        )

        self.assertEqual(200, first.status)
        self.assertEqual(200, second.status)
        self.assertEqual(2, len(prepared))
        self.assertEqual(2, len(cancelled))
        self.assertEqual(2, executions)
        self.assertNotIn("releaseReceiptPending", first.payload)
        self.assertNotIn(
            "releaseReceiptPending", first.payload["receipts"][0]
        )

    def test_full_dangerous_action_replays_completed_receipt_without_dispatch(self) -> None:
        views = {view.url: view for view in self.hass.http.views}
        path = "/api/hausman_hub/v1/device-actions"
        tablet = reader_user("system-users")
        service = self.hass.data["hausman_hub"]["scenario_service"]
        calls: list[tuple[str, str]] = []

        async def resolve_context(target_id: str, action_id: str):
            return "button.synthetic_door", "button", ("press",)

        async def execute_action(
            target_id: str,
            action_id: str,
            value: object,
            *,
            correlation_id: str | None = None,
            dangerous_authorized: bool = False,
            **options: object,
        ) -> dict[str, object]:
            self.assertTrue(dangerous_authorized)
            self.assertTrue(str(options["request_id"]).startswith("dispatch."))
            calls.append((target_id, action_id))
            return {
                "correlationId": correlation_id,
                "requestId": options["request_id"],
                "targetId": target_id,
                "actionId": action_id,
                "accepted": True,
                "confirmed": True,
                "status": "confirmed",
                "statusName": "Выполнено",
                "appliedAt": int(time.time() * 1000),
                "message": "Кнопка нажата.",
                "confirmationWindowMs": 8000,
                "readBack": {
                    "attempted": True,
                    "matched": True,
                    "observedAt": int(time.time() * 1000),
                    "observedState": "on",
                    "attempts": 1,
                },
                "reason": None,
            }

        service.async_resolve_device_action_context = resolve_context
        service.async_execute_device_action = execute_action
        payload = {
            "contract": {
                "name": "hausman-hub-device-action-request",
                "version": 1,
            },
            "correlationId": "dangerous.press.1",
            "requestId": "dangerous.press.request.1",
            "targetId": "door_button",
            "actionId": "press",
            "confirmedByUser": True,
            "idempotencyKey": "dangerous.press.1",
        }

        def send(
            current: dict[str, object],
            *,
            accept: str = "application/vnd.hausmanhub.device-action-receipt.full+json",
        ) -> FakeResponse:
            return asyncio.run(
                views[path].post(
                    FakeJsonRequest(
                        "192.168.1.20",
                        tablet,
                        path,
                        current,
                        content_type="application/vnd.hausmanhub.device-action-request.full+json",
                        accept=accept,
                    )
                )
            )

        first = send(payload)
        replay = send(copy.deepcopy(payload), accept="application/json")
        conflict_payload = copy.deepcopy(payload)
        conflict_payload["correlationId"] = "dangerous.press.2"
        conflict = send(conflict_payload)

        self.assertEqual(200, first.status)
        self.assertEqual(first.payload, replay.payload)
        self.assertEqual(
            "application/vnd.hausmanhub.device-action-receipt.full+json",
            replay.headers["Content-Type"],
        )
        self.assertEqual(409, conflict.status)
        self.assertEqual("idempotency_key_conflict", conflict.payload["details"]["detailCode"])
        self.assertEqual([("door_button", "press")], calls)

    def test_light_reassert_requires_exact_evidence_and_replays_once(self) -> None:
        from custom_components.hausman_hub.application.device_action_receipts import (
            evidence_snapshot,
        )

        views = {view.url: view for view in self.hass.http.views}
        path = "/api/hausman_hub/v1/device-actions"
        service = self.hass.data["hausman_hub"]["scenario_service"]
        entity_id = "light.synthetic_stale"
        stale_state = SimpleNamespace(
            state="on",
            attributes={},
            last_updated=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        self.hass.states.values[entity_id] = stale_state
        evidence = evidence_snapshot(
            target_id="light_stale",
            state=stale_state,
            allowed_actions=("turn_on", "turn_off"),
        )
        calls: list[str] = []

        async def resolve_context(_target_id: str, _action_id: str):
            return entity_id, "light", ("turn_on", "turn_off")

        async def execute_action(
            target_id: str,
            action_id: str,
            value: object,
            *,
            correlation_id: str | None = None,
            **options: object,
        ) -> dict[str, object]:
            self.assertTrue(options["force_new_readback"])
            self.assertTrue(options["automatic_reassert"])
            self.assertEqual(
                evidence["evidenceRevision"],
                options["expected_evidence_revision"],
            )
            self.assertEqual(
                evidence["evidenceSequence"],
                options["expected_evidence_sequence"],
            )
            calls.append(correlation_id or "")
            return {
                "correlationId": correlation_id,
                "requestId": options["request_id"],
                "targetId": target_id,
                "actionId": action_id,
                "accepted": True,
                "confirmed": False,
                "status": "accepted",
                "statusName": "Проверяется",
                "appliedAt": int(time.time() * 1000),
                "message": "Состояние света подтверждено.",
                "confirmationWindowMs": 8000,
                "readBack": {
                    "attempted": True,
                    "matched": False,
                    "observedAt": int(time.time() * 1000),
                    "observedState": "on",
                    "attempts": 1,
                },
                "reason": None,
            }

        service.async_resolve_device_action_context = resolve_context
        service.async_execute_device_action = execute_action
        payload = {
            "contract": {
                "name": "hausman-hub-device-action-request",
                "version": 1,
            },
            "correlationId": "light.reassert.1",
            "requestId": "light.reassert.request.1",
            "targetId": "light_stale",
            "actionId": "turn_on",
            "reassertKey": "light.reassert.key.1",
            "expectedEvidenceRevision": evidence["evidenceRevision"],
            "expectedEvidenceSequence": evidence["evidenceSequence"],
        }

        def send(current: dict[str, object]) -> FakeResponse:
            return asyncio.run(
                views[path].post(
                    FakeJsonRequest(
                        "192.168.1.20",
                        reader_user("system-users"),
                        path,
                        current,
                        content_type="application/vnd.hausmanhub.device-action-request.full+json",
                        accept="application/vnd.hausmanhub.device-action-receipt.full+json",
                    )
                )
            )

        first = send(payload)
        replay = send(copy.deepcopy(payload))
        mismatch = copy.deepcopy(payload)
        mismatch["reassertKey"] = "light.reassert.key.2"
        rejected = send(mismatch)

        self.assertEqual(200, first.status)
        self.assertEqual("reasserted", first.payload["decision"])
        self.assertFalse(first.payload["confirmed"])
        self.assertEqual("automation", first.payload["commandSource"])
        self.assertEqual("unknown", first.payload["ownership"])
        self.assertFalse(first.payload["readBack"]["isNewEvidence"])
        self.assertEqual(first.payload, replay.payload)
        self.assertEqual(409, rejected.status)
        self.assertEqual(["light.reassert.1"], calls)

    def test_local_admin_reads_filtered_operation_journal(self) -> None:
        from custom_components.hausman_hub.application.operation_journal import (
            OperationJournalService,
        )
        from custom_components.hausman_hub.operation_journal_api import (
            ADMIN_OPERATION_JOURNAL_PATH,
            OperationJournalView,
        )

        class Store:
            async def async_load(self):
                return None

            async def async_save(self, payload):
                return None

        journal = OperationJournalService(Store(), now_ms=lambda: 1786375200000)
        asyncio.run(
            journal.async_append(
                {
                    "request_id": "climate-1",
                    "operation": "climate.tablet_action",
                    "accepted": True,
                    "confirmed": False,
                    "status": "accepted",
                    "reason": None,
                    "error_code": None,
                }
            )
        )
        self.hass.data["hausman_hub"]["operation_journal"] = journal
        request = FakeRequest(
            "192.168.1.20",
            reader_user(admin=True),
            path=ADMIN_OPERATION_JOURNAL_PATH,
            query_string="source=climate&limit=10",
        )
        request.query = {"source": "climate", "limit": "10"}

        response = asyncio.run(OperationJournalView(self.hass).get(request))

        self.assertEqual(200, response.status)
        self.assertEqual("hausman-hub-operation-journal", response.payload["contract"]["name"])
        self.assertEqual("climate-1", response.payload["records"][0]["correlation_id"])
        self.assertEqual("sequence_desc", response.payload["page"]["order"])
        self.assertEqual(512, response.payload["page"]["retention_limit"])

        cursor_request = FakeRequest(
            "192.168.1.20",
            reader_user(admin=True),
            path=ADMIN_OPERATION_JOURNAL_PATH,
            query_string="before_sequence=1",
        )
        cursor_request.query = {"before_sequence": "1"}
        cursor_response = asyncio.run(OperationJournalView(self.hass).get(cursor_request))
        self.assertEqual(200, cursor_response.status)
        self.assertEqual([], cursor_response.payload["records"])

        invalid_request = FakeRequest(
            "192.168.1.20",
            reader_user(admin=True),
            path=ADMIN_OPERATION_JOURNAL_PATH,
            query_string="before_sequence=01",
        )
        invalid_request.query = {"before_sequence": "01"}
        invalid_response = asyncio.run(OperationJournalView(self.hass).get(invalid_request))
        self.assertEqual(400, invalid_response.status)

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

    def test_view_does_not_read_home_when_a_stale_pointer_outlives_hausmanhub(self) -> None:
        """A retained runtime pointer must not outlive the loaded HausmanHub entry."""

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

    def test_view_fails_closed_if_a_second_saved_hausmanhub_entry_appears(self) -> None:
        """The retained view must not leak counts during a corrupt live pair."""

        self.hass.config_entries.entries.append(
            FakeEntry(
                {
                    "mode": "shadow",
                    "direct_execution_status": "direct_execution_blocked",
                },
                {},
                "synthetic-hausmanhub-second",
            )
        )

        response = asyncio.run(
            self.view.get(FakeRequest("127.0.0.1", reader_user("system-read-only")))
        )

        self.assertEqual(503, response.status)
        self.assertEqual({"message"}, set(response.payload))

    def test_unload_clears_only_hausmanhub_owned_state_values(self) -> None:
        """Turning HausmanHub off must not leave its old counts or touch another state."""

        hausmanhub_state = "sensor.hausman_hub_entities_count"
        self.hass.entity_registry.entities["hausmanhub-owned"] = SimpleNamespace(
            domain="sensor",
            entity_id=hausmanhub_state,
            config_entry_id=self.entry.entry_id,
            disabled_by=None,
        )
        self.hass.states.values[hausmanhub_state] = SimpleNamespace(state="7")

        self.assertTrue(asyncio.run(self.integration.async_unload_entry(self.hass, self.entry)))

        self.assertEqual([hausmanhub_state], self.hass.states.removed)
        self.assertNotIn(hausmanhub_state, self.hass.states.values)
        self.assertNotIn("settings_service", self.hass.data["hausman_hub"])
        self.assertIn("hausmanhub-owned", self.hass.entity_registry.entities)
        self.assertEqual([], self.hass.entity_registry.removed)
        self.assertIn("sensor.synthetic_private_temperature", self.hass.states.values)
        self.assertEqual(1, len(self.entry.update_listeners))

        self.entry.process_unload_callbacks()

        self.assertEqual([], self.entry.update_listeners)

    def test_failed_unload_keeps_the_current_hausmanhub_state_and_page(self) -> None:
        """A failed unload must not leave a half-cleared HausmanHub display behind."""

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

        hausmanhub_state = "sensor.hausman_hub_entities_count"
        failed_hass.entity_registry.entities["hausmanhub-owned"] = SimpleNamespace(
            domain="sensor",
            entity_id=hausmanhub_state,
            config_entry_id=failed_entry.entry_id,
            disabled_by=None,
        )
        failed_hass.states.values[hausmanhub_state] = SimpleNamespace(state="7")

        self.assertFalse(
            asyncio.run(self.integration.async_unload_entry(failed_hass, failed_entry))
        )

        self.assertEqual([], failed_hass.states.removed)
        self.assertIn(hausmanhub_state, failed_hass.states.values)
        self.assertIn("hausmanhub-owned", failed_hass.entity_registry.entities)
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
        """Closing the page must not remove the nine safe HausmanHub count sensors."""

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

        self.assertEqual(
            [(closed_entry, ("sensor", "switch"))],
            closed_hass.config_entries.forwarded,
        )
        self.assertEqual(94, len(closed_hass.http.views))
        self.assertEqual(
            {
                "/api/hausman_hub/v1/capabilities",
                "/api/hausman_hub/v1/dashboard",
                "/api/hausman_hub/v1/events",
                "/api/hausman_hub/v1/device-actions",
                "/api/hausman_hub/v1/device-actions/batch",
                "/api/hausman_hub/v1/device-features",
                "/api/hausman_hub/v1/device-property-names",
                "/api/hausman_hub/v1/energy/history",
                "/api/hausman_hub/v1/energy/meter",
                "/api/hausman_hub/v1/energy/meters",
                "/api/hausman_hub/v1/energy-settings",
                "/api/hausman_hub/v1/device-discovery",
                "/api/hausman_hub/v1/tablet-profile",
                "/api/hausman_hub/v1/tablet-power-status",
                "/api/hausman_hub/v1/room-settings",
                "/api/hausman_hub/v1/home",
                "/api/hausman_hub/v1/climate/runtime",
                "/api/hausman_hub/v1/climate/actions",
                "/api/hausman_hub/v1/climate-season-settings",
                "/api/hausman_hub/v1/climate/operations/{operation_id}",
                "/api/hausman_hub/v1/climate/control/operations/{operation_id}",
                "/api/hausman_hub/v1/climate/recovery/rooms/{room_id}",
                "/api/hausman_hub/v1/climate/recovery/rooms/{room_id}/preflight",
                "/api/hausman_hub/v1/climate/recovery/operations/{operation_id}",
                "/api/hausman_hub/v1/voice/yandex-greeting",
                "/api/hausman_hub/v1/voice/yandex-greeting/test",
                "/api/hausman_hub/v1/contours",
                "/api/hausman_hub/v1/contours/apply-preview",
                "/api/hausman_hub/v1/contours/apply",
                "/api/hausman_hub/v1/contours/temporary-temperature",
                "/api/hausman_hub/v1/contours/home-targets",
                "/api/hausman_hub/v1/admin/climate-import",
                "/api/hausman_hub/v1/admin/legacy-settings/preview",
                "/api/hausman_hub/v1/admin/legacy-settings/apply",
                "/api/hausman_hub/v1/admin/climate-shadow-comparison",
                "/api/hausman_hub/v1/admin/climate-shadow-window",
                "/api/hausman_hub/v1/admin/operations",
                "/api/hausman_hub/v1/admin/climate-drafts",
                "/api/hausman_hub/v1/admin/climate-drafts/current",
                "/api/hausman_hub/v1/admin/climate-drafts/validate",
                "/api/hausman_hub/v1/admin/climate-drafts/save",
                "/api/hausman_hub/v1/admin/device-area-assignments",
                "/api/hausman_hub/v1/admin/device-maintenance",
                "/api/hausman_hub/v1/admin/climate-device-bindings",
                "/api/hausman_hub/v1/admin/climate-device-bindings/preview",
                "/api/hausman_hub/v1/admin/climate-profiles",
                "/api/hausman_hub/v1/admin/climate-schedule",
                "/api/hausman_hub/v1/admin/climate-registry",
                "/api/hausman_hub/v1/admin/climate-registry-preview",
                "/api/hausman_hub/v1/admin/climate-readiness",
                "/api/hausman_hub/v1/admin/panel",
                "/api/hausman_hub/v1/admin/panel/apply",
                "/api/hausman_hub/v1/admin/panel/temporary-temperature",
                "/api/hausman_hub/v1/admin/climate-mode",
                "/api/hausman_hub/v1/admin/climate-deviation-guard",
                "/api/hausman_hub/v1/admin/home-environment",
                "/api/hausman_hub/v1/admin/climate-room-signals",
                "/api/hausman_hub/v1/admin/ai-assistant",
                "/api/hausman_hub/v1/admin/ai-assistant/settings",
                "/api/hausman_hub/v1/admin/ai-assistant/refresh",
                "/api/hausman_hub/v1/admin/connection-settings",
                "/api/hausman_hub/v1/admin/energy-settings",
                "/api/hausman_hub/v1/admin/device-power-dependencies",
                "/api/hausman_hub/v1/admin/water-safety",
                "/api/hausman_hub/v1/admin/water-safety/direction-test",
                "/api/hausman_hub/v1/admin/reset",
                "/api/hausman_hub/v1/admin/scenarios",
                "/api/hausman_hub/v1/admin/scenarios/action",
                "/api/hausman_hub/v1/admin/scenarios/ai-draft",
                "/api/hausman_hub/v1/admin/scenarios/catalog",
                "/api/hausman_hub/v1/admin/scenarios/health",
                "/api/hausman_hub/v1/admin/scenarios/node-red",
                "/api/hausman_hub/v1/admin/scenarios/node-red/source/{scenario_id}",
                "/api/hausman_hub/v1/admin/scenarios/delete",
                "/api/hausman_hub/v1/admin/scenarios/run",
                "/api/hausman_hub/v1/admin/scenarios/test",
                "/api/hausman_hub/v1/scenarios",
                "/api/hausman_hub/v1/scenarios/action",
                "/api/hausman_hub/v1/scenarios/ai-draft",
                "/api/hausman_hub/v1/scenarios/catalog",
                "/api/hausman_hub/v1/scenarios/health",
                "/api/hausman_hub/v1/scenarios/node-red",
                "/api/hausman_hub/v1/scenarios/node-red/source/{scenario_id}",
                "/api/hausman_hub/v1/scenarios/delete",
                "/api/hausman_hub/v1/scenarios/run",
                "/api/hausman_hub/v1/scenarios/test",
                "/api/hausman_hub/v1/scenarios/upcoming",
                "/api/hausman_hub/v1/scenarios/upcoming/cancel",
                "/api/hausman_hub/v1/admin/ir-codes",
                "/api/hausman_hub/v1/admin/ir-codes/scan",
                "/api/hausman_hub/v1/admin/ir-codes/bindings",
                "/api/hausman_hub/v1/admin/ir-codes/learn",
                "/api/hausman_hub/v1/admin/ir-codes/test",
                "/api/hausman_hub/v1/admin/ir-codes/delete",
            },
            {view.url for view in closed_hass.http.views},
        )
        self.assertNotIn("local_summary_active_entry", closed_hass.data["hausman_hub"])
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
                saved_hausmanhub_state = "sensor.hausman_hub_entities_count"
                unsafe_hass.entity_registry.entities["saved-hausmanhub"] = SimpleNamespace(
                    domain="sensor",
                    entity_id=saved_hausmanhub_state,
                    config_entry_id=unsafe_entry.entry_id,
                    disabled_by=None,
                )
                unsafe_hass.states.values[saved_hausmanhub_state] = SimpleNamespace(state="7")

                self.assertFalse(
                    asyncio.run(self.integration.async_setup_entry(unsafe_hass, unsafe_entry))
                )
                self.assertEqual({}, unsafe_hass.data)
                self.assertEqual([], unsafe_hass.http.views)
                self.assertEqual([], unsafe_hass.config_entries.forwarded)
                self.assertEqual([saved_hausmanhub_state], unsafe_hass.states.removed)
                self.assertNotIn(saved_hausmanhub_state, unsafe_hass.states.values)
                self.assertEqual([saved_hausmanhub_state], unsafe_hass.entity_registry.removed)
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
        """A corrupt pair of saved HausmanHub entries must not expose either display."""

        safe_data = {
            "mode": "read-only",
            "direct_execution_status": "direct_execution_blocked",
        }
        first_entry = FakeEntry(dict(safe_data), {}, "synthetic-hausmanhub-first")
        second_entry = FakeEntry(dict(safe_data), {}, "synthetic-hausmanhub-second")
        duplicate_hass = FakeHomeAssistant()
        duplicate_hass.config_entries.entries = [first_entry, second_entry]
        first_state = "sensor.hausman_hub_first_saved_count"
        second_state = "sensor.hausman_hub_second_saved_count"
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

    def test_second_saved_entry_closes_an_already_running_hausmanhub_display(self) -> None:
        """A live corrupt pair must close the existing display before cleanup."""

        first_state = "sensor.hausman_hub_first_running_count"
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
            "synthetic-hausmanhub-second",
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
