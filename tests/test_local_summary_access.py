"""Isolated tests for the authenticated local nine-count summary view."""

from __future__ import annotations

import asyncio
import copy
import gc
import importlib
import json
import os
import sys
import tempfile
import time
import unittest
import weakref
from dataclasses import replace
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
    "homeassistant.exceptions",
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

    async def dispatch(self, method: str, request: object) -> FakeResponse:
        """Dependency-free equivalent of HA's registered-view dispatcher."""
        path = getattr(request, "path", None)
        view = next((item for item in self.views if getattr(item, "url", None) == path), None)
        if view is None:
            raise LookupError(path)
        handler = getattr(view, method.casefold(), None)
        if handler is None:
            raise LookupError(method)
        return await handler(request)

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
        self.content = self
        self.content_type = content_type
        self.content_length = len(self._raw_body)
        self.headers = {} if accept is None else {"Accept": accept}

    async def json(self) -> object:
        return self._payload

    async def read(self, size: int = -1) -> bytes:
        return self._raw_body if size < 0 else self._raw_body[:size]


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

    exceptions = ModuleType("homeassistant.exceptions")

    class FakeHomeAssistantError(Exception):
        """Match Home Assistant's base integration exception."""

    exceptions.HomeAssistantError = FakeHomeAssistantError  # type: ignore[attr-defined]

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
    temporary_directories: weakref.WeakSet[tempfile.TemporaryDirectory] = (
        weakref.WeakSet()
    )

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
            self._temporary_directory = tempfile.TemporaryDirectory()
            temporary_directories.add(self._temporary_directory)
            self.path = str(Path(self._temporary_directory.name) / key)

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

    def cleanup_temporary_directories() -> None:
        for temporary_directory in list(temporary_directories):
            temporary_directory.cleanup()
        temporary_directories.clear()

    storage.Store = FakeStore  # type: ignore[attr-defined]
    storage.cleanup_temporary_directories = (  # type: ignore[attr-defined]
        cleanup_temporary_directories
    )
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
    homeassistant.exceptions = exceptions  # type: ignore[attr-defined]
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
        "homeassistant.exceptions": exceptions,
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


class FakeStoreLifecycleTest(unittest.TestCase):
    """Keep test-only Home Assistant storage from leaking temporary paths."""

    def test_temporary_storage_directory_is_removed_with_store(self) -> None:
        storage = fake_home_assistant_modules()["homeassistant.helpers.storage"]
        store = storage.Store(object(), 1, "hausman_hub.cleanup")
        directory = Path(store.path).parent
        self.assertTrue(directory.is_dir())

        del store
        gc.collect()

        self.assertFalse(directory.exists())

    def test_storage_cleanup_removes_live_store_directory(self) -> None:
        storage = fake_home_assistant_modules()["homeassistant.helpers.storage"]
        store = storage.Store(object(), 1, "hausman_hub.cleanup")
        directory = Path(store.path).parent
        self.assertTrue(directory.is_dir())

        storage.cleanup_temporary_directories()

        self.assertFalse(directory.exists())


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
        fake_modules = fake_home_assistant_modules()
        cls.fake_storage = fake_modules["homeassistant.helpers.storage"]
        sys.modules.update(fake_modules)
        cls.integration = importlib.import_module(PACKAGE_MODULE)
        cls.adapter = importlib.import_module(LOCAL_SUMMARY_MODULE)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fake_storage.cleanup_temporary_directories()
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

    def tearDown(self) -> None:
        self.fake_storage.cleanup_temporary_directories()

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

    def _manual_protection_views(self) -> tuple[object, object]:
        settings = next(
            view for view in self.hass.http.views
            if getattr(view, "name", "") == "api:hausman_hub:manual_light_off_protection"
        )
        release = next(
            view for view in self.hass.http.views
            if getattr(view, "name", "") == "api:hausman_hub:manual_light_off_protection_release"
        )
        return settings, release

    def _install_monotonic_contextual_action_stack(self) -> SimpleNamespace:
        """Wire the real API service and executor around a scripted classifier."""

        from custom_components.hausman_hub.application.scenario_executor import (
            ScenarioExecutor,
        )
        from custom_components.hausman_hub.application.scenario_service import (
            ScenarioService,
        )
        from custom_components.hausman_hub.application.scenarios import (
            ScenarioCatalog,
            ScenarioDeviceAction,
            ScenarioDeviceEntry,
        )

        entity_id = "switch.contextual_action"
        catalog = ScenarioCatalog(
            devices={
                "contextual_switch": ScenarioDeviceEntry(
                    target_id="contextual_switch",
                    name="Контекстный выключатель",
                    entity_id=entity_id,
                    actions=(
                        ScenarioDeviceAction(
                            action_id="turn_on",
                            title="Включить",
                            domain="switch",
                            service="turn_on",
                            allowed_fields=frozenset(),
                        ),
                    ),
                    device_type="switch",
                )
            },
            scenarios={},
        )

        class Store:
            async def async_load(self) -> None:
                return None

            async def async_save(self, _value: object) -> None:
                return None

        state = SimpleNamespace(
            refreshes=0,
            outcomes=[],
            classifications=[],
            direct_markers=0,
            batch_markers=0,
            service_calls=[],
        )

        async def load_catalog() -> object:
            state.refreshes += 1
            return catalog

        def classify(target_id: str, action_id: str) -> bool:
            self.assertEqual(("contextual_switch", "turn_on"), (target_id, action_id))
            if not state.outcomes:
                raise AssertionError("unexpected contextual danger classification")
            outcome = state.outcomes.pop(0)
            state.classifications.append(
                (
                    state.refreshes,
                    type(outcome).__name__
                    if isinstance(outcome, Exception)
                    else outcome,
                )
            )
            if isinstance(outcome, Exception):
                raise outcome
            return bool(outcome)

        service = ScenarioService(
            self.hass,
            Store(),
            catalog,
            catalog_loader=load_catalog,
        )
        executor = ScenarioExecutor(
            self.hass,
            catalog,
            service.async_run_scenario,
            contextual_dangerous_resolver=classify,
        )
        service.set_executor(executor)
        service.is_contextually_dangerous_action = classify

        original_direct = service.async_execute_device_action

        async def execute_direct(
            target_id: str,
            action_id: str,
            value: object,
            *,
            dispatch_marker=None,
            **options: object,
        ) -> dict[str, object]:
            external_request_id = options.get("request_id")
            if external_request_id is not None:
                options["request_id"] = "contextual_direct_internal"
            tracked_marker = None
            if dispatch_marker is not None:

                def tracked_marker() -> None:
                    state.direct_markers += 1
                    dispatch_marker()

            receipt = await original_direct(
                target_id,
                action_id,
                value,
                dispatch_marker=tracked_marker,
                **options,
            )
            if external_request_id is not None:
                receipt["requestId"] = external_request_id
            return receipt

        original_batch = service.async_execute_device_action_batch

        async def execute_batch(
            actions: list[dict[str, object]],
            *,
            dispatch_marker=None,
            dispatch_markers=None,
            **options: object,
        ) -> list[dict[str, object]]:
            external_request_ids = options.get("request_ids")
            if isinstance(external_request_ids, tuple):
                options["request_ids"] = tuple(
                    f"contextual_batch_internal_{index}"
                    for index in range(len(external_request_ids))
                )
            tracked_markers = None
            if dispatch_markers is not None:

                def tracked(index: int):
                    def mark() -> None:
                        state.batch_markers += 1
                        dispatch_markers[index]()

                    return mark

                tracked_markers = tuple(
                    tracked(index) for index in range(len(dispatch_markers))
                )
            receipts = await original_batch(
                actions,
                dispatch_marker=dispatch_marker,
                dispatch_markers=tracked_markers,
                **options,
            )
            if isinstance(external_request_ids, tuple):
                for index, receipt in enumerate(receipts):
                    receipt["requestId"] = external_request_ids[index]
            return receipts

        service.async_execute_device_action = execute_direct
        service.async_execute_device_action_batch = execute_batch
        self.hass.data["hausman_hub"]["scenario_service"] = service

        self.hass.states.values[entity_id] = SimpleNamespace(
            state="off",
            attributes={},
            last_updated=datetime.now(timezone.utc),
        )

        class Services:
            async def async_call(
                inner_self,
                domain: str,
                action: str,
                service_data: dict[str, object],
                *,
                blocking: bool,
                **options: object,
            ) -> None:
                state.service_calls.append(
                    (domain, action, dict(service_data), blocking, dict(options))
                )
                self.hass.states.values[entity_id] = SimpleNamespace(
                    state="on" if action == "turn_on" else "off",
                    attributes={},
                    last_updated=datetime.now(timezone.utc) + timedelta(microseconds=1),
                )

        self.hass.services = Services()
        state.service = service
        state.executor = executor
        state.entity_id = entity_id
        return state

    def _install_slow_safe_climate_action_stack(
        self,
        *,
        service_entered: asyncio.Event,
        release_service: asyncio.Event,
    ) -> SimpleNamespace:
        """Wire real service/executor objects around one deliberately slow HA call."""

        from custom_components.hausman_hub.application.scenario_executor import (
            ScenarioExecutor,
        )
        from custom_components.hausman_hub.application.scenario_service import (
            ScenarioService,
        )
        from custom_components.hausman_hub.application.scenarios import (
            ScenarioCatalog,
            ScenarioDeviceAction,
            ScenarioDeviceEntry,
        )

        climate_entity_id = "climate.synthetic_slow_smartir"
        trv_entity_id = "climate.synthetic_slow_trv"
        humidifier_entity_id = "humidifier.synthetic_slow"
        catalog = ScenarioCatalog(
            devices={
                "slow_smartir": ScenarioDeviceEntry(
                    target_id="slow_smartir",
                    name="Медленный кондиционер",
                    entity_id=climate_entity_id,
                    actions=(
                        ScenarioDeviceAction(
                            action_id="set_temperature",
                            title="Температура",
                            domain="climate",
                            service="set_temperature",
                            allowed_fields=frozenset({"value"}),
                        ),
                        ScenarioDeviceAction(
                            action_id="turn_off",
                            title="Выключить",
                            domain="climate",
                            service="turn_off",
                            allowed_fields=frozenset(),
                        ),
                    ),
                    device_type="climate",
                ),
                "slow_trv": ScenarioDeviceEntry(
                    target_id="slow_trv",
                    name="Медленный термостат",
                    entity_id=trv_entity_id,
                    actions=(
                        ScenarioDeviceAction(
                            action_id="set_temperature",
                            title="Температура",
                            domain="climate",
                            service="set_temperature",
                            allowed_fields=frozenset({"value"}),
                        ),
                    ),
                    device_type="climate",
                ),
                "slow_humidifier": ScenarioDeviceEntry(
                    target_id="slow_humidifier",
                    name="Медленный увлажнитель",
                    entity_id=humidifier_entity_id,
                    actions=(
                        ScenarioDeviceAction(
                            action_id="set_humidity",
                            title="Влажность",
                            domain="humidifier",
                            service="set_humidity",
                            allowed_fields=frozenset({"value"}),
                        ),
                    ),
                    device_type="humidifier",
                ),
            },
            scenarios={},
        )

        class Store:
            async def async_load(self) -> None:
                return None

            async def async_save(self, _value: object) -> None:
                return None

        async def load_catalog() -> object:
            return catalog

        state = SimpleNamespace(service_calls=[])
        service = ScenarioService(
            self.hass,
            Store(),
            catalog,
            catalog_loader=load_catalog,
        )
        executor = ScenarioExecutor(
            self.hass,
            catalog,
            service.async_run_scenario,
            readback_interval_seconds=0.01,
        )
        service.set_executor(executor)
        self.hass.data["hausman_hub"]["scenario_service"] = service
        self.hass.states.values[climate_entity_id] = SimpleNamespace(
            state="cool",
            attributes={
                "temperature": 21,
                "target_temperature": 21,
                "min_temp": 16,
                "max_temp": 30,
                "target_temp_step": 1,
            },
            last_updated=datetime.now(timezone.utc),
        )
        self.hass.states.values[humidifier_entity_id] = SimpleNamespace(
            state="on",
            attributes={
                "humidity": 40,
                "target_humidity": 40,
                "min_humidity": 30,
                "max_humidity": 80,
                "target_humidity_step": 1,
            },
            last_updated=datetime.now(timezone.utc),
        )
        self.hass.states.values[trv_entity_id] = SimpleNamespace(
            state="heat",
            attributes={
                "temperature": 20,
                "target_temperature": 20,
                "min_temp": 5,
                "max_temp": 30,
                "target_temp_step": 0.5,
            },
            last_updated=datetime.now(timezone.utc),
        )
        self.hass.entity_registry.entities[climate_entity_id] = SimpleNamespace(
            entity_id=climate_entity_id,
            platform="smartir",
            disabled_by=None,
        )

        class Services:
            async def async_call(
                inner_self,
                domain: str,
                action: str,
                service_data: dict[str, object],
                *,
                blocking: bool,
                **options: object,
            ) -> None:
                state.service_calls.append(
                    (domain, action, dict(service_data), blocking, dict(options))
                )
                entity_id = service_data.get("entity_id")
                if (
                    domain == "climate"
                    and action == "set_temperature"
                    and isinstance(entity_id, str)
                ):
                    current = self.hass.states.values[entity_id]
                    self.hass.states.values[entity_id] = SimpleNamespace(
                        state=current.state,
                        attributes={
                            **current.attributes,
                            "temperature": service_data["temperature"],
                            "target_temperature": service_data["temperature"],
                        },
                        last_updated=datetime.now(timezone.utc),
                    )
                if (
                    domain == "humidifier"
                    and action == "set_humidity"
                    and isinstance(entity_id, str)
                ):
                    current = self.hass.states.values[entity_id]
                    self.hass.states.values[entity_id] = SimpleNamespace(
                        state=current.state,
                        attributes={
                            **current.attributes,
                            "humidity": service_data["humidity"],
                            "target_humidity": service_data["humidity"],
                        },
                        last_updated=datetime.now(timezone.utc),
                    )
                service_entered.set()
                await release_service.wait()

        self.hass.services = Services()
        state.service = service
        state.executor = executor
        return state

    @staticmethod
    def _manual_settings_request(request_id: str, interval: int = 30) -> dict[str, object]:
        return {
            "contract": {"name": "hausman-hub-manual-light-off-protection-settings-request", "version": 1},
            "requestId": request_id, "expectedRevision": 0,
            "settings": {"globalPolicy": {"enabled": True, "minimumIntervalSeconds": interval, "releaseMode": "timer_only", "stableAbsenceSeconds": 5, "extendOnRepeatedManualOff": True, "noSensorFallback": "timer_only", "protectedScope": "profile", "allowManualRelease": True}, "roomOverrides": {}, "profileOverrides": {}, "profiles": []},
        }

    @staticmethod
    def _manual_release_request(
        request_id: str,
        room_id: str = "room",
        profile_id: str = "profile",
        expected_protection_revision: int = 0,
    ) -> dict[str, object]:
        return {
            "contract": {
                "name": "hausman-hub-manual-light-off-protection-release-request",
                "version": 1,
            },
            "requestId": request_id,
            "roomId": room_id,
            "profileId": profile_id,
            "expectedProtectionRevision": expected_protection_revision,
        }

    def test_manual_protection_http_replay_conflicts_and_no_store(self) -> None:
        # The synthetic read-only catalog has no first-wave devices. This API
        # boundary test replaces its intentionally fail-closed setup state.
        self.hass.data["hausman_hub"]["manual_light_off_protection"].set_catalog_coverage_healthy(True)
        view, release = self._manual_protection_views()
        admin = reader_user("system-admin", admin=True)
        request = self._manual_settings_request("replay.1")
        first = asyncio.run(self.hass.http.dispatch("PUT", FakeJsonRequest("127.0.0.1", admin, view.url, request)))
        same = asyncio.run(self.hass.http.dispatch("PUT", FakeJsonRequest("127.0.0.1", admin, view.url, request)))
        changed = self._manual_settings_request("replay.1", 31)
        conflict = asyncio.run(self.hass.http.dispatch("PUT", FakeJsonRequest("127.0.0.1", admin, view.url, changed)))
        cross = asyncio.run(self.hass.http.dispatch("POST", FakeJsonRequest("127.0.0.1", admin, release.url, self._manual_release_request("replay.1"))))
        self.assertEqual(200, first.status)
        self.assertEqual(first.payload, same.payload)
        for response in (conflict, cross):
            self.assertEqual(409, response.status)
            self.assertEqual("hausman-hub-error", response.payload["contract"]["name"])
            self.assertEqual("no-store", response.headers["Cache-Control"])

    def test_manual_protection_dispatcher_returns_canonical_no_store_405(self) -> None:
        view, release = self._manual_protection_views()
        admin = reader_user("system-admin", admin=True)
        for method, path in (("POST", view.url), ("GET", release.url), ("PUT", release.url), ("PATCH", view.url), ("DELETE", view.url), ("HEAD", release.url)):
            response = asyncio.run(self.hass.http.dispatch(method, FakeJsonRequest("127.0.0.1", admin, path, {})))
            self.assertEqual(405, response.status)
            self.assertEqual("hausman-hub-error", response.payload["contract"]["name"])
            self.assertEqual("method_not_allowed", response.payload["code"])
            self.assertEqual("GET, PUT" if path == view.url else "POST", response.headers["Allow"])
            self.assertEqual("no-store", response.headers["Cache-Control"])

    def test_manual_protection_release_replays_through_dispatcher(self) -> None:
        from custom_components.hausman_hub.application.manual_light_off_protection import ManualLightOffProtectionCoordinator

        class Store:
            payload = None
            saves = 0

            async def async_load(self): return copy.deepcopy(self.payload)
            async def async_save(self, payload):
                self.saves += 1
                self.payload = copy.deepcopy(payload)

        store = Store()
        coordinator = ManualLightOffProtectionCoordinator(store)
        asyncio.run(coordinator.async_load())
        self.hass.data["hausman_hub"]["manual_light_off_protection"] = coordinator
        view, release = self._manual_protection_views()
        admin = reader_user("system-admin", admin=True)
        request = self._manual_settings_request("seed.release")
        request["settings"]["profiles"] = [{"roomId": "room", "profileId": "profile", "lightIds": ["light.one"], "presenceSensorIds": []}]
        asyncio.run(self.hass.http.dispatch("PUT", FakeJsonRequest("127.0.0.1", admin, view.url, request)))
        asyncio.run(coordinator.async_note_state_transition("light.one", SimpleNamespace(state="on"), SimpleNamespace(state="off"), None))
        payload = self._manual_release_request("release.replay")
        first = asyncio.run(self.hass.http.dispatch("POST", FakeJsonRequest("127.0.0.1", admin, release.url, payload)))
        saves_after_first_release = store.saves
        again = asyncio.run(self.hass.http.dispatch("POST", FakeJsonRequest("127.0.0.1", admin, release.url, payload)))
        reverse = asyncio.run(self.hass.http.dispatch(
            "PUT",
            FakeJsonRequest(
                "127.0.0.1",
                admin,
                view.url,
                self._manual_settings_request("release.replay"),
            ),
        ))
        self.assertEqual(200, first.status)
        self.assertEqual(first.payload, again.payload)
        self.assertEqual(saves_after_first_release, store.saves)
        self.assertEqual(409, reverse.status)
        self.assertEqual("conflict", reverse.payload["code"])
        self.assertEqual("no-store", reverse.headers["Cache-Control"])
        self.assertEqual(saves_after_first_release, store.saves)

    def test_manual_protection_release_request_id_conflicts_for_every_payload_change(self) -> None:
        self.hass.data["hausman_hub"]["manual_light_off_protection"].set_catalog_coverage_healthy(True)
        view, release = self._manual_protection_views()
        admin = reader_user("system-admin", admin=True)
        settings = self._manual_settings_request("seed.release.conflicts")
        settings["settings"]["profiles"] = [{"roomId": "room", "profileId": "profile", "lightIds": ["light.one"], "presenceSensorIds": []}]
        asyncio.run(self.hass.http.dispatch("PUT", FakeJsonRequest("127.0.0.1", admin, view.url, settings)))
        coordinator = self.hass.data["hausman_hub"]["manual_light_off_protection"]
        asyncio.run(coordinator.async_note_state_transition("light.one", SimpleNamespace(state="on"), SimpleNamespace(state="off"), None))
        original = self._manual_release_request("release.conflict")
        first = asyncio.run(self.hass.http.dispatch("POST", FakeJsonRequest("127.0.0.1", admin, release.url, original)))
        self.assertEqual(200, first.status)
        for changed in (
            self._manual_release_request("release.conflict", room_id="other-room"),
            self._manual_release_request("release.conflict", profile_id="other-profile"),
            self._manual_release_request("release.conflict", expected_protection_revision=1),
        ):
            response = asyncio.run(self.hass.http.dispatch("POST", FakeJsonRequest("127.0.0.1", admin, release.url, changed)))
            self.assertEqual(409, response.status)
            self.assertEqual("no-store", response.headers["Cache-Control"])

    def test_manual_protection_unload_and_healthy_reload_retain_two_route_objects(self) -> None:
        admin = reader_user("system-admin", admin=True)
        view, _ = self._manual_protection_views()
        before = tuple(item for item in self.hass.http.views if "manual_light_off_protection" in getattr(item, "name", ""))
        old_coordinator = self.hass.data["hausman_hub"]["manual_light_off_protection"]
        self.assertTrue(asyncio.run(self.integration.async_unload_entry(self.hass, self.entry)))
        unavailable = asyncio.run(self.hass.http.dispatch("PUT", FakeJsonRequest("127.0.0.1", admin, view.url, self._manual_settings_request("unload.1"))))
        self.assertEqual(503, unavailable.status)
        from custom_components.hausman_hub.application.scenario_catalog import ScenarioCatalog, ScenarioDeviceAction, ScenarioDeviceEntry
        from custom_components.hausman_hub.application.system_light_profiles import FIRST_WAVE_AUTO_ON_TARGETS
        import custom_components.hausman_hub.application.scenario_catalog as scenario_catalog

        devices = {
            target.target_id: ScenarioDeviceEntry(
                target.target_id,
                target.target_id,
                target.entity_id or "light.unpinned_catalog_target",
                (ScenarioDeviceAction("turn_on", "On", (target.entity_id or "light.unpinned_catalog_target").partition(".")[0], "turn_on", frozenset()),),
            )
            for target in FIRST_WAVE_AUTO_ON_TARGETS
        }
        # This is the actual small-corridor controller composition. Only its
        # chandelier belongs to the first-wave auto-on registry; the local
        # bright/dark input, motion trigger and relay stay observable inputs.
        devices.update({
            "entity_c9d6bc67f172f30d": ScenarioDeviceEntry("entity_c9d6bc67f172f30d", "Local light", "sensor.small_corridor_local_light", ()),
            "entity_90417aada6a33491": ScenarioDeviceEntry("entity_90417aada6a33491", "Motion", "binary_sensor.small_corridor_motion", ()),
            "entity_ff0244d6b760be7e": ScenarioDeviceEntry("entity_ff0244d6b760be7e", "Relay", "switch.small_corridor_relay", (ScenarioDeviceAction("turn_on", "On", "switch", "turn_on", frozenset()),)),
        })
        catalog = ScenarioCatalog(devices, {})
        async def healthy_catalog(_hass): return catalog
        with patch.object(scenario_catalog, "async_build_scenario_catalog", healthy_catalog):
            self.assertTrue(asyncio.run(self.integration.async_setup_entry(self.hass, self.entry)))
        after = tuple(item for item in self.hass.http.views if "manual_light_off_protection" in getattr(item, "name", ""))
        self.assertEqual(before, after)
        self.assertEqual(2, len(after))
        self.assertIsNot(old_coordinator, self.hass.data["hausman_hub"]["manual_light_off_protection"])
        self.assertFalse(self.hass.data["hausman_hub"]["manual_light_off_protection"].unhealthy)
        restored = asyncio.run(self.hass.http.dispatch("PUT", FakeJsonRequest("127.0.0.1", admin, view.url, self._manual_settings_request("reload.healthy"))))
        self.assertEqual(200, restored.status)

    def test_manual_protection_failed_unload_keeps_old_coordinator(self) -> None:
        self.hass.config_entries.unload_succeeds = False
        old = self.hass.data["hausman_hub"]["manual_light_off_protection"]
        old.set_catalog_coverage_healthy(True)
        view, _ = self._manual_protection_views()
        self.assertFalse(asyncio.run(self.integration.async_unload_entry(self.hass, self.entry)))
        self.assertIs(old, self.hass.data["hausman_hub"]["manual_light_off_protection"])
        response = asyncio.run(self.hass.http.dispatch("PUT", FakeJsonRequest("127.0.0.1", reader_user("system-admin", admin=True), view.url, self._manual_settings_request("failed-unload.1"))))
        self.assertEqual(200, response.status)

    def test_setup_marks_the_coordinator_unhealthy_when_the_real_auto_on_is_missing(self) -> None:
        from custom_components.hausman_hub.application.scenario_catalog import ScenarioCatalog, ScenarioDeviceAction, ScenarioDeviceEntry
        from custom_components.hausman_hub.application.system_light_profiles import FIRST_WAVE_AUTO_ON_TARGETS
        import custom_components.hausman_hub.application.scenario_catalog as scenario_catalog

        hass = FakeHomeAssistant()
        entry = FakeEntry({"mode": "read-only", "direct_execution_status": "direct_execution_blocked"}, {})
        hass.config_entries.entries = [entry]
        catalog = ScenarioCatalog(
            {
                target.target_id: ScenarioDeviceEntry(
                    target.target_id,
                    target.target_id,
                    target.entity_id or "light.unpinned_catalog_target",
                    () if target.target_id == "entity_9ed909332fdaa8fd" else (ScenarioDeviceAction("turn_on", "On", (target.entity_id or "light.unpinned_catalog_target").partition(".")[0], "turn_on", frozenset()),),
                )
                for target in FIRST_WAVE_AUTO_ON_TARGETS
            },
            {},
        )

        healthy_catalog = ScenarioCatalog(
            {
                target.target_id: ScenarioDeviceEntry(
                    target.target_id,
                    target.target_id,
                    target.entity_id or "light.unpinned_catalog_target",
                    (
                        ScenarioDeviceAction(
                            "turn_on",
                            "On",
                            (target.entity_id or "light.unpinned_catalog_target").partition(".")[0],
                            "turn_on",
                            frozenset(),
                        ),
                    ),
                )
                for target in FIRST_WAVE_AUTO_ON_TARGETS
            },
            {},
        )
        catalogs = iter((catalog, healthy_catalog))

        async def warming_catalog(_hass):
            return next(catalogs)

        with patch.object(scenario_catalog, "async_build_scenario_catalog", warming_catalog):
            self.assertTrue(asyncio.run(self.integration.async_setup_entry(hass, entry)))
            coordinator = hass.data["hausman_hub"]["manual_light_off_protection"]
            self.assertTrue(coordinator.unhealthy)
            asyncio.run(hass.data["hausman_hub"]["scenario_service"].async_refresh_catalog())
            self.assertFalse(coordinator.unhealthy)

    def test_manual_protection_http_failed_save_cannot_replay_success(self) -> None:
        from custom_components.hausman_hub.application.manual_light_off_protection import ManualLightOffProtectionCoordinator
        import copy

        class Store:
            payload = None
            fail = True
            saves = 0
            async def async_load(self): return copy.deepcopy(self.payload)
            async def async_save(self, payload):
                self.saves += 1
                if self.fail: raise OSError("private storage failure")
                self.payload = copy.deepcopy(payload)

        store = Store()
        failed = ManualLightOffProtectionCoordinator(store)
        asyncio.run(failed.async_load())
        self.hass.data["hausman_hub"]["manual_light_off_protection"] = failed
        view, _ = self._manual_protection_views()
        admin = reader_user("system-admin", admin=True)
        request = self._manual_settings_request("failure.1")
        response = asyncio.run(self.hass.http.dispatch("PUT", FakeJsonRequest("127.0.0.1", admin, view.url, request)))
        self.assertIn(response.status, {500, 503})
        self.assertEqual("hausman-hub-error", response.payload["contract"]["name"])
        self.assertEqual("no-store", response.headers["Cache-Control"])
        store.fail = False
        restarted = ManualLightOffProtectionCoordinator(store)
        asyncio.run(restarted.async_load())
        self.hass.data["hausman_hub"]["manual_light_off_protection"] = restarted
        retried = asyncio.run(self.hass.http.dispatch("PUT", FakeJsonRequest("127.0.0.1", admin, view.url, request)))
        self.assertEqual(200, retried.status)
        self.assertEqual(2, store.saves)

    def test_manual_protection_http_restart_replays_only_same_payload(self) -> None:
        from custom_components.hausman_hub.application.manual_light_off_protection import ManualLightOffProtectionCoordinator
        import copy

        class Store:
            payload = None
            saves = 0
            async def async_load(self): return copy.deepcopy(self.payload)
            async def async_save(self, payload): self.saves += 1; self.payload = copy.deepcopy(payload)

        store = Store()
        coordinator = ManualLightOffProtectionCoordinator(store)
        asyncio.run(coordinator.async_load())
        self.hass.data["hausman_hub"]["manual_light_off_protection"] = coordinator
        view, _ = self._manual_protection_views()
        admin = reader_user("system-admin", admin=True)
        request = self._manual_settings_request("restart.1")
        original = asyncio.run(self.hass.http.dispatch("PUT", FakeJsonRequest("127.0.0.1", admin, view.url, request)))
        restarted = ManualLightOffProtectionCoordinator(store)
        asyncio.run(restarted.async_load())
        self.hass.data["hausman_hub"]["manual_light_off_protection"] = restarted
        replay = asyncio.run(self.hass.http.dispatch("PUT", FakeJsonRequest("127.0.0.1", admin, view.url, request)))
        changed = asyncio.run(self.hass.http.dispatch("PUT", FakeJsonRequest("127.0.0.1", admin, view.url, self._manual_settings_request("restart.1", 31))))
        self.assertEqual(original.payload, replay.payload)
        self.assertEqual(1, store.saves)
        self.assertEqual(409, changed.status)
        self.assertEqual("no-store", changed.headers["Cache-Control"])

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


    def test_climate_receipt_routes_project_legacy_deferred_text_without_changing_history(self) -> None:
        """A verified old receipt stays valid on GET, alias GET and duplicate POST."""
        from tests.test_climate_tablet import contract_validator

        canonical = json.loads((ROOT / "fixtures/hausmanhub_climate_reliability_v1/climate-operation-partial-intent.json").read_text())
        operation_id = canonical["operation_id"]
        views = {view.url: view for view in self.hass.http.views}
        routes = (
            ("/api/hausman_hub/v1/climate/operations/{operation_id}", "_climate_tablet", "get"),
            ("/api/hausman_hub/v1/climate/control/operations/{operation_id}", "_runtime", "get"),
            ("/api/hausman_hub/v1/climate/actions", "_climate_tablet", "post"),
        )
        for template, accessor, method in routes:
            with self.subTest(route=template):
                stored = copy.deepcopy(canonical)
                stored["outcomes"]["rooms"]["bedroom"]["devices"]["bedroom_ac"]["message"] = "Цель сохранена, устройство недоступно."
                original = copy.deepcopy(stored)

                async def trusted_receipt(*args):
                    return stored

                service = SimpleNamespace(async_operation=trusted_receipt,
                                          async_control_operation=trusted_receipt,
                                          async_submit=trusted_receipt)
                view = views[template]
                path = template.replace("{operation_id}", operation_id)
                with patch.object(view, accessor, return_value=service):
                    if method == "get":
                        response = asyncio.run(view.get(FakeRequest("127.0.0.1", reader_user("system-users"), path=path), operation_id))
                    else:
                        response = asyncio.run(view.post(FakeJsonRequest("127.0.0.1", reader_user("system-users"), path, {})))
                self.assertEqual(200 if method == "get" else 202, response.status)
                contract_validator("climate-operation-receipt.schema.json").validate(response.payload)
                self.assertEqual(canonical, response.payload)
                self.assertEqual(original, stored, "Presentation must not rewrite authenticated history")

    def test_climate_receipt_text_projection_does_not_repair_other_leaf_fields(self) -> None:
        """Legacy wording is not permission to hide malformed execution evidence."""
        canonical = json.loads((ROOT / "fixtures/hausmanhub_climate_reliability_v1/climate-operation-partial-intent.json").read_text())
        template = "/api/hausman_hub/v1/climate/operations/{operation_id}"
        view = {item.url: item for item in self.hass.http.views}[template]
        mutations = ({"status": "manual"}, {"reason": "configuration_error"},
                     {"message_code": "pending"}, {"command_count": True},
                     {"accepted_count": 1}, {"execution_state": "pending_dispatch"},
                     {"message": "Unknown old wording"})
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                stored = copy.deepcopy(canonical)
                leaf = stored["outcomes"]["rooms"]["bedroom"]["devices"]["bedroom_ac"]
                leaf["message"] = "Цель сохранена, устройство недоступно."
                leaf.update(mutation)
                original = copy.deepcopy(stored)

                async def trusted_receipt(*args):
                    return stored

                with patch.object(view, "_climate_tablet", return_value=SimpleNamespace(async_operation=trusted_receipt)):
                    operation_id = stored["operation_id"]
                    response = asyncio.run(view.get(FakeRequest("127.0.0.1", reader_user("system-users"), path=template.replace("{operation_id}", operation_id)), operation_id))
                self.assertEqual(original, response.payload)
                self.assertEqual(original, stored)

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
        self.assertEqual("1.52.239", panel.payload["integration_version"])
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
            contextually_dangerous: bool = False,
        ) -> dict[str, object]:
            self.assertTrue(contextually_dangerous)
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

    def test_manual_ac_off_keeps_ac_in_manual_mode_after_command(self) -> None:
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
        self.assertEqual("manual", response.payload["climateMode"])
        self.assertEqual("Ручной режим", response.payload["climateModeName"])
        self.assertEqual(
            [
                ("resolve", ("office-ac", "turn_off")),
                ("execute", ("office-ac", "turn_off", None)),
                ("mode", ("climate.office", "manual")),
            ],
            events,
        )

    def test_manual_power_on_returns_each_climate_domain_to_automatic(self) -> None:
        path = "/api/hausman_hub/v1/device-actions"
        view = next(item for item in self.hass.http.views if item.url == path)
        service = self.hass.data["hausman_hub"]["scenario_service"]
        runtime = self.hass.data["hausman_hub"]["climate_runtime"]
        for domain in ("climate", "humidifier", "switch"):
            with self.subTest(domain=domain):
                modes = []

                async def resolve(target_id, action_id):
                    return f"{domain}.office", domain

                async def execute(target_id, action_id, value, *, correlation_id=None):
                    return {"accepted": True, "confirmed": True, "status": "confirmed"}

                async def mode_writer(entity_id, mode):
                    modes.append((entity_id, mode))
                    return {"mode": mode, "changed": True}

                service.async_resolve_device_action = resolve
                service.async_execute_device_action = execute
                runtime.async_set_device_mode_for_entity = mode_writer
                response = asyncio.run(view.post(FakeJsonRequest(
                    "192.168.1.20", reader_user("system-users"), path,
                    {"targetId": "office-device", "actionId": "turn_on"},
                )))
                self.assertEqual(200, response.status)
                self.assertEqual([(f"{domain}.office", "automatic")], modes)
                self.assertEqual("automatic", response.payload["climateMode"])

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
        self.assertEqual(99, len(self.hass.http.views))
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
            initial_contextually_dangerous: frozenset[
                tuple[str, str]
            ] = frozenset(),
        ) -> list[dict[str, object]]:
            self.assertEqual(frozenset(), initial_contextually_dangerous)
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

    def test_full_curtain_replay_keeps_original_observation_after_policy_change(
        self,
    ) -> None:
        """A completed replay never recalculates the applied curtain position."""

        path = "/api/hausman_hub/v1/device-actions"
        view = next(item for item in self.hass.http.views if item.url == path)
        tablet = reader_user("system-users")
        service = self.hass.data["hausman_hub"]["scenario_service"]
        entity_id = "cover.synthetic_kitchen"
        self.hass.states.values[entity_id] = SimpleNamespace(
            state="closed",
            attributes={"current_position": 0},
            last_updated=datetime.now(timezone.utc),
        )
        cap = {"value": 80}
        executions = 0

        async def resolve_context(target_id: str, action_id: str):
            self.assertEqual(
                ("entity_2da2065add6e2168", "set_position"),
                (target_id, action_id),
            )
            return (
                entity_id,
                "cover",
                ("open_cover", "close_cover", "set_position"),
                "set_cover_position",
            )

        async def execute_action(
            target_id: str,
            action_id: str,
            value: object,
            *,
            correlation_id: str | None = None,
            **options: object,
        ) -> dict[str, object]:
            nonlocal executions
            executions += 1
            options["dispatch_marker"]()
            observed = min(int(value), cap["value"])
            observed_at = int(time.time() * 1000)
            self.hass.states.values[entity_id] = SimpleNamespace(
                state="open",
                attributes={"current_position": observed},
                last_updated=datetime.now(timezone.utc),
            )
            return {
                "correlationId": correlation_id,
                "requestId": options["request_id"],
                "targetId": target_id,
                "actionId": action_id,
                "accepted": True,
                "confirmed": True,
                "status": "confirmed",
                "statusName": "Выполнено",
                "appliedAt": observed_at,
                "message": "Позиция шторы подтверждена.",
                "confirmationWindowMs": 8000,
                "readBack": {
                    "attempted": True,
                    "matched": True,
                    "observedAt": observed_at,
                    "observedState": "open",
                    "observedValue": observed,
                    "attempts": 1,
                    "isNewEvidence": True,
                    "evidenceRevision": f"cover.synthetic.{observed}",
                    "evidenceSequence": observed_at,
                },
                "reason": "curtain_position_limited",
            }

        service.async_resolve_device_action_context = resolve_context
        service.async_execute_device_action = execute_action
        payload = {
            "contract": {
                "name": "hausman-hub-device-action-request",
                "version": 1,
            },
            "correlationId": "curtain.full.1",
            "requestId": "curtain.full.request.1",
            "idempotencyKey": "curtain.full.key.1",
            "targetId": "entity_2da2065add6e2168",
            "actionId": "set_position",
            "value": 100,
        }

        def send(body: dict[str, object]) -> FakeResponse:
            return asyncio.run(
                view.post(
                    FakeJsonRequest(
                        "192.168.1.20",
                        tablet,
                        path,
                        copy.deepcopy(body),
                        content_type=(
                            "application/vnd.hausmanhub.device-action-request."
                            "full+json"
                        ),
                        accept=(
                            "application/vnd.hausmanhub.device-action-receipt."
                            "full+json"
                        ),
                    )
                )
            )

        first = send(payload)
        cap["value"] = 70
        replay = send(payload)
        conflict = send({**payload, "value": 90})

        self.assertEqual(200, first.status)
        self.assertEqual(200, replay.status)
        self.assertEqual(409, conflict.status)
        self.assertEqual(100, first.payload["actionValue"])
        self.assertEqual(80, first.payload["readBack"]["observedValue"])
        self.assertLessEqual(
            first.payload["commandSentAt"],
            first.payload["readBack"]["observedAt"],
        )
        self.assertTrue(
            str(first.payload["readBack"]["commandRequestId"]).startswith(
                "dispatch."
            )
        )
        self.assertEqual(first.payload, replay.payload)
        self.assertEqual(
            "idempotency_key_conflict",
            conflict.payload["details"]["detailCode"],
        )
        self.assertEqual(1, executions)

    def test_full_curtain_request_uses_real_executor_readback_and_replays_once(
        self,
    ) -> None:
        """HTTP retains the actual HA position returned by the shared executor."""

        from custom_components.hausman_hub.application.curtain_command_policy import (
            KITCHEN_CURTAIN_TARGET,
            CurtainCommandPolicy,
        )
        from custom_components.hausman_hub.application.scenarios import (
            ScenarioCatalog,
            ScenarioDeviceAction,
            ScenarioDeviceEntry,
        )

        path = "/api/hausman_hub/v1/device-actions"
        view = next(item for item in self.hass.http.views if item.url == path)
        service = self.hass.data["hausman_hub"]["scenario_service"]
        control_service = self.hass.data["hausman_hub"][
            "scenario_control_policy_service"
        ]
        executor = service._executor
        entity_id = "cover.synthetic_kitchen_actual"
        actions = (
            ScenarioDeviceAction(
                action_id="open_cover",
                title="Открыть",
                domain="cover",
                service="open_cover",
                allowed_fields=frozenset(),
            ),
            ScenarioDeviceAction(
                action_id="close_cover",
                title="Закрыть",
                domain="cover",
                service="close_cover",
                allowed_fields=frozenset(),
            ),
            ScenarioDeviceAction(
                action_id="set_position",
                title="Положение",
                domain="cover",
                service="set_cover_position",
                allowed_fields=frozenset({"value"}),
            ),
        )
        catalog = ScenarioCatalog(
            devices={
                KITCHEN_CURTAIN_TARGET: ScenarioDeviceEntry(
                    target_id=KITCHEN_CURTAIN_TARGET,
                    name="Шторы кухня",
                    entity_id=entity_id,
                    actions=actions,
                    device_type="cover",
                )
            },
            scenarios={},
        )
        service._catalog = catalog
        service._catalog_loader = None
        executor.replace_catalog(catalog)
        executor._curtain_command_policy = CurtainCommandPolicy.with_confirmed_scales(
            {KITCHEN_CURTAIN_TARGET},
            control_document_provider=lambda: control_service.current,
        )
        before_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.hass.states.values[entity_id] = SimpleNamespace(
            state="closed",
            attributes={"current_position": 0},
            last_updated=before_at,
        )
        service_calls: list[tuple[str, str, dict[str, object], bool]] = []

        class Services:
            async def async_call(
                inner_self,
                domain: str,
                action: str,
                service_data: dict[str, object],
                *,
                blocking: bool,
                **_options: object,
            ) -> None:
                service_calls.append(
                    (domain, action, dict(service_data), blocking)
                )
                self.hass.states.values[entity_id] = SimpleNamespace(
                    state="open",
                    attributes={"current_position": service_data["position"]},
                    last_updated=datetime.now(timezone.utc),
                )

        self.hass.services = Services()
        payload = {
            "contract": {
                "name": "hausman-hub-device-action-request",
                "version": 1,
            },
            "correlationId": "curtain.actual.1",
            "requestId": "curtain.actual.request.1",
            "idempotencyKey": "curtain.actual.key.1",
            "targetId": KITCHEN_CURTAIN_TARGET,
            "actionId": "set_position",
            "value": 100,
        }

        def send(body: dict[str, object]) -> FakeResponse:
            return asyncio.run(
                view.post(
                    FakeJsonRequest(
                        "192.168.1.20",
                        reader_user("system-users"),
                        path,
                        copy.deepcopy(body),
                        content_type=(
                            "application/vnd.hausmanhub.device-action-request."
                            "full+json"
                        ),
                        accept=(
                            "application/vnd.hausmanhub.device-action-receipt."
                            "full+json"
                        ),
                    )
                )
            )

        first = send(payload)
        current = control_service.current
        asyncio.run(
            control_service.async_replace(
                current.policy_revision,
                replace(current.policy, kitchen_cover_cap_percent=70),
            )
        )
        replay = send(payload)
        conflict = send({**payload, "value": 90})

        self.assertEqual(200, first.status)
        self.assertEqual(100, first.payload["actionValue"])
        self.assertFalse(first.payload["confirmed"])
        self.assertEqual(
            "curtain_position_provenance_unverified", first.payload["reason"]
        )
        self.assertNotIn("observedValue", first.payload["readBack"])
        self.assertLessEqual(
            first.payload["commandSentAt"],
            first.payload["readBack"]["observedAt"],
        )
        self.assertTrue(
            str(first.payload["readBack"]["commandRequestId"]).startswith(
                "dispatch."
            )
        )
        self.assertEqual(first.payload, replay.payload)
        self.assertEqual(409, conflict.status)
        self.assertEqual(
            "idempotency_key_conflict",
            conflict.payload["details"]["detailCode"],
        )
        self.assertEqual(
            [
                (
                    "cover",
                    "set_cover_position",
                    {"entity_id": entity_id, "position": 80},
                    True,
                )
            ],
            service_calls,
        )

    def test_device_action_response_sets_negotiated_type_after_ha_json_creation(self) -> None:
        """HA must receive no Content-Type header while it creates JSON."""

        views = {view.url: view for view in self.hass.http.views}
        path = "/api/hausman_hub/v1/device-actions"
        view = views[path]
        service = self.hass.data["hausman_hub"]["scenario_service"]
        tablet = reader_user("system-users")
        execution_accepts = True
        execution_failure: str | None = None
        execution_calls: list[str] = []

        def strict_ha_json(
            payload: object,
            status_code: int = 200,
            headers: dict[str, str] | None = None,
        ) -> FakeResponse:
            response_headers = dict(headers or {})
            if any(name.casefold() == "content-type" for name in response_headers):
                raise ValueError(
                    "passing both Content-Type header and content_type or charset params is forbidden"
                )
            response_headers["Content-Type"] = "application/json; charset=utf-8"
            return FakeResponse(payload, int(status_code), response_headers)

        async def resolve_context(_target_id: str, _action_id: str):
            return "switch.synthetic_response", "switch", ("turn_on",), "turn_on"

        async def execute_action(
            target_id: str,
            action_id: str,
            _value: object,
            *,
            correlation_id: str | None = None,
            **options: object,
        ) -> dict[str, object]:
            execution_calls.append(target_id)
            if execution_failure is not None:
                if execution_failure.startswith("post_"):
                    dispatch_marker = options.get("dispatch_marker")
                    self.assertTrue(callable(dispatch_marker))
                    dispatch_marker()
                if execution_failure.endswith("timeout"):
                    raise asyncio.TimeoutError("synthetic executor timeout")
                raise RuntimeError("synthetic executor failure")
            return {
                "correlationId": correlation_id,
                "requestId": options.get("request_id", "legacy.response.request"),
                "targetId": target_id,
                "actionId": action_id,
                "accepted": execution_accepts,
                "confirmed": execution_accepts,
                "status": "confirmed" if execution_accepts else "failed",
                "reason": None if execution_accepts else "device_unavailable",
            }

        view.json = strict_ha_json
        service.async_resolve_device_action_context = resolve_context
        service.async_execute_device_action = execute_action

        cases = (
            (
                "legacy-confirmed",
                True,
                {
                    "correlationId": "legacy.confirmed.1",
                    "targetId": "response_target",
                    "actionId": "turn_on",
                },
                "application/json",
                None,
                200,
                "application/json",
            ),
            (
                "legacy-failed",
                False,
                {
                    "correlationId": "legacy.failed.1",
                    "targetId": "response_target",
                    "actionId": "turn_on",
                },
                "application/json",
                "application/json",
                409,
                "application/json",
            ),
            (
                "legacy-pre-dispatch-exception",
                "pre_dispatch",
                {
                    "correlationId": "legacy.pre.exception.1",
                    "targetId": "response_target",
                    "actionId": "turn_on",
                },
                "application/json",
                None,
                503,
                "application/json",
            ),
            (
                "legacy-post-dispatch-exception",
                "post_dispatch",
                {
                    "correlationId": "legacy.post.exception.1",
                    "targetId": "response_target",
                    "actionId": "turn_on",
                },
                "application/json",
                None,
                409,
                "application/json",
            ),
            (
                "legacy-pre-dispatch-timeout",
                "pre_timeout",
                {
                    "correlationId": "legacy.pre.timeout.1",
                    "targetId": "response_target",
                    "actionId": "turn_on",
                },
                "application/json",
                None,
                503,
                "application/json",
            ),
            (
                "legacy-post-dispatch-timeout",
                "post_timeout",
                {
                    "correlationId": "legacy.post.timeout.1",
                    "targetId": "response_target",
                    "actionId": "turn_on",
                },
                "application/json",
                None,
                409,
                "application/json",
            ),
            (
                "full-confirmed",
                True,
                {
                    "contract": {
                        "name": "hausman-hub-device-action-request",
                        "version": 1,
                    },
                    "correlationId": "full.confirmed.1",
                    "requestId": "full.confirmed.request.1",
                    "targetId": "response_target",
                    "actionId": "turn_on",
                },
                "application/vnd.hausmanhub.device-action-request.full+json",
                "application/vnd.hausmanhub.device-action-receipt.full+json",
                200,
                "application/vnd.hausmanhub.device-action-receipt.full+json",
            ),
            (
                "full-failed",
                False,
                {
                    "contract": {
                        "name": "hausman-hub-device-action-request",
                        "version": 1,
                    },
                    "correlationId": "full.failed.1",
                    "requestId": "full.failed.request.1",
                    "targetId": "response_target",
                    "actionId": "turn_on",
                },
                "application/vnd.hausmanhub.device-action-request.full+json",
                "application/vnd.hausmanhub.device-action-receipt.full+json",
                409,
                "application/vnd.hausmanhub.device-action-receipt.full+json",
            ),
            (
                "full-pre-dispatch-exception",
                "pre_dispatch",
                {
                    "contract": {
                        "name": "hausman-hub-device-action-request",
                        "version": 1,
                    },
                    "correlationId": "full.pre.exception.1",
                    "requestId": "full.pre.exception.request.1",
                    "targetId": "response_target",
                    "actionId": "turn_on",
                },
                "application/vnd.hausmanhub.device-action-request.full+json",
                "application/vnd.hausmanhub.device-action-receipt.full+json",
                503,
                "application/vnd.hausmanhub.device-action-receipt.full+json",
            ),
            (
                "full-post-dispatch-exception",
                "post_dispatch",
                {
                    "contract": {
                        "name": "hausman-hub-device-action-request",
                        "version": 1,
                    },
                    "correlationId": "full.post.exception.1",
                    "requestId": "full.post.exception.request.1",
                    "targetId": "response_target",
                    "actionId": "turn_on",
                },
                "application/vnd.hausmanhub.device-action-request.full+json",
                "application/vnd.hausmanhub.device-action-receipt.full+json",
                409,
                "application/vnd.hausmanhub.device-action-receipt.full+json",
            ),
        )
        for (
            label,
            outcome,
            payload,
            content_type,
            accept,
            expected_status,
            expected_media_type,
        ) in cases:
            with self.subTest(label=label):
                execution_failure = (
                    outcome if isinstance(outcome, str) else None
                )
                execution_accepts = outcome is True
                before_calls = len(execution_calls)
                response = asyncio.run(
                    view.post(
                        FakeJsonRequest(
                            "192.168.1.20",
                            tablet,
                            path,
                            payload,
                            content_type=content_type,
                            accept=accept,
                        )
                    )
                )
                self.assertEqual(expected_status, response.status)
                self.assertEqual(expected_media_type, response.headers["Content-Type"])
                self.assertEqual("no-store", response.headers["Cache-Control"])
                if execution_failure is not None:
                    self.assertEqual(before_calls + 1, len(execution_calls))
                    self.assertEqual(
                        "unavailable" if expected_status == 503 else "conflict",
                        response.payload["code"],
                    )
                    if expected_status == 409:
                        self.assertEqual(
                            "dispatch_unknown", response.payload["details"]["state"]
                        )
                        self.assertFalse(
                            response.payload["details"]["automaticRetryAllowed"]
                        )

    def test_legacy_single_and_batch_failures_use_dispatch_marker(self) -> None:
        """Legacy clients receive a safe unknown result only after physical send."""

        views = {view.url: view for view in self.hass.http.views}
        single_path = "/api/hausman_hub/v1/device-actions"
        batch_path = "/api/hausman_hub/v1/device-actions/batch"
        service = self.hass.data["hausman_hub"]["scenario_service"]
        tablet = reader_user("system-users")
        mode = "safe_negative"

        async def resolve_context(target_id: str, _action_id: str):
            return f"switch.synthetic_{target_id}", "switch", ("turn_on",), "turn_on"

        async def execute_single(
            target_id: str,
            action_id: str,
            _value: object,
            **options: object,
        ) -> object:
            self.assertNotIn("dangerous_authorized", options)
            if mode.startswith("crossed_"):
                marker = options.get("dispatch_marker")
                self.assertTrue(callable(marker))
                marker()
            if mode.endswith("timeout"):
                raise asyncio.TimeoutError("synthetic legacy single timeout")
            if mode.endswith("exception"):
                raise RuntimeError("synthetic legacy single failure")
            if mode.endswith("malformed"):
                return []
            return {
                "correlationId": options.get("correlation_id"),
                "requestId": "legacy.single.receipt",
                "targetId": target_id,
                "actionId": action_id,
                "accepted": False,
                "confirmed": False,
                "status": "failed",
            }

        async def execute_batch(
            actions: list[dict[str, object]],
            **options: object,
        ) -> list[dict[str, object]]:
            self.assertNotIn("dangerous_authorized", options)
            if mode.startswith("crossed_"):
                markers = options.get("dispatch_markers")
                self.assertIsInstance(markers, tuple)
                markers[0]()
            if mode.endswith("timeout"):
                raise asyncio.TimeoutError("synthetic legacy batch timeout")
            if mode.endswith("exception"):
                raise RuntimeError("synthetic legacy batch failure")
            if mode.endswith("malformed"):
                return []
            if mode == "crossed_other_negative":
                return [
                    {
                        "correlationId": options.get("correlation_id"),
                        "requestId": "legacy.batch.receipt.accepted",
                        "targetId": actions[0]["targetId"],
                        "actionId": actions[0]["actionId"],
                        "accepted": True,
                        "confirmed": True,
                        "status": "confirmed",
                    },
                    {
                        "correlationId": options.get("correlation_id"),
                        "requestId": "legacy.batch.receipt.failed",
                        "targetId": actions[1]["targetId"],
                        "actionId": actions[1]["actionId"],
                        "accepted": False,
                        "confirmed": False,
                        "status": "failed",
                    },
                ]
            item = actions[0]
            return [{
                "correlationId": options.get("correlation_id"),
                "requestId": "legacy.batch.receipt",
                "targetId": item["targetId"],
                "actionId": item["actionId"],
                "accepted": False,
                "confirmed": False,
                "status": "failed",
            }]

        service.async_resolve_device_action_context = resolve_context
        service.async_execute_device_action = execute_single
        service.async_execute_device_action_batch = execute_batch

        def send_single(suffix: str) -> FakeResponse:
            return asyncio.run(
                views[single_path].post(
                    FakeJsonRequest(
                        "192.168.1.20",
                        tablet,
                        single_path,
                        {
                            "correlationId": f"legacy.single.{suffix}",
                            "targetId": "single_target",
                            "actionId": "turn_on",
                        },
                    )
                )
            )

        def send_batch(suffix: str, *, two_items: bool = False) -> FakeResponse:
            actions = [{"targetId": "batch_target", "actionId": "turn_on"}]
            if two_items:
                actions.append(
                    {"targetId": "batch_target_2", "actionId": "turn_on"}
                )
            return asyncio.run(
                views[batch_path].post(
                    FakeJsonRequest(
                        "192.168.1.20",
                        tablet,
                        batch_path,
                        {
                            "contract": {
                                "name": "hausman-hub-device-action-batch-request",
                                "version": 1,
                            },
                            "correlationId": f"legacy.batch.{suffix}",
                            "actions": actions,
                        },
                    )
                )
            )

        for receipt_kind in ("negative", "malformed", "timeout", "exception"):
            with self.subTest(receipt_kind=receipt_kind, boundary="before"):
                mode = f"safe_{receipt_kind}"
                safe_single = send_single(mode)
                safe_batch = send_batch(mode)
                if receipt_kind == "negative":
                    self.assertEqual(409, safe_single.status)
                    self.assertFalse(safe_single.payload["accepted"])
                    self.assertEqual(200, safe_batch.status)
                    self.assertEqual("failed", safe_batch.payload["status"])
                elif receipt_kind in {"malformed", "timeout", "exception"}:
                    self.assertEqual(503, safe_single.status)
                    self.assertEqual(503, safe_batch.status)

            with self.subTest(receipt_kind=receipt_kind, boundary="after"):
                mode = f"crossed_{receipt_kind}"
                for response in (send_single(mode), send_batch(mode)):
                    self.assertEqual(409, response.status)
                    self.assertEqual(
                        "dispatch_unknown", response.payload["details"]["state"]
                    )
                    self.assertFalse(
                        response.payload["details"]["automaticRetryAllowed"]
                    )

        mode = "crossed_other_negative"
        mixed_batch = send_batch(mode, two_items=True)
        self.assertEqual(409, mixed_batch.status)
        self.assertEqual(
            "dispatch_unknown", mixed_batch.payload["details"]["state"]
        )
        self.assertFalse(mixed_batch.payload["details"]["automaticRetryAllowed"])

    def test_full_batch_execution_exceptions_keep_negotiated_media_type(self) -> None:
        views = {view.url: view for view in self.hass.http.views}
        path = "/api/hausman_hub/v1/device-actions/batch"
        view = views[path]
        service = self.hass.data["hausman_hub"]["scenario_service"]
        tablet = reader_user("system-users")
        calls: list[list[dict[str, object]]] = []
        dispatch_crossed = False

        async def resolve_context(_target_id: str, _action_id: str):
            return "switch.synthetic_response", "switch", ("turn_on",), "turn_on"

        async def execute_batch(
            actions: list[dict[str, object]], **options: object
        ) -> list[dict[str, object]]:
            calls.append(actions)
            if dispatch_crossed:
                marker = options.get("dispatch_marker")
                self.assertTrue(callable(marker))
                marker()
            raise RuntimeError("synthetic batch executor failure")

        service.async_resolve_device_action_context = resolve_context
        service.async_execute_device_action_batch = execute_batch
        for index, (crossed, status, code) in enumerate(
            ((False, 503, "unavailable"), (True, 409, "conflict"))
        ):
            with self.subTest(dispatch_crossed=crossed):
                dispatch_crossed = crossed
                response = asyncio.run(
                    view.post(
                        FakeJsonRequest(
                            "192.168.1.20",
                            tablet,
                            path,
                            {
                                "contract": {
                                    "name": "hausman-hub-device-action-batch-request",
                                    "version": 1,
                                },
                                "correlationId": f"full.batch.exception.{index}",
                                "requestId": f"full.batch.exception.request.{index}",
                                "actions": [
                                    {"targetId": "response_target", "actionId": "turn_on"}
                                ],
                            },
                            content_type="application/vnd.hausmanhub.device-action-batch-request.full+json",
                            accept="application/vnd.hausmanhub.device-action-batch-receipt.full+json",
                        )
                    )
                )
                self.assertEqual(status, response.status)
                self.assertEqual(code, response.payload["code"])
                self.assertEqual(
                    "application/vnd.hausmanhub.device-action-batch-receipt.full+json",
                    response.headers["Content-Type"],
                )
                self.assertEqual(index + 1, len(calls))

    def test_full_power_source_dispatch_unknown_is_not_replayed(self) -> None:
        """A source command that crossed dispatch keeps both journals pending."""

        views = {view.url: view for view in self.hass.http.views}
        tablet = reader_user("system-users")
        service = self.hass.data["hausman_hub"]["scenario_service"]
        single_calls = 0
        batch_calls = 0

        async def resolve_context(_target_id: str, _action_id: str):
            return "switch.synthetic_power_source", "switch", ("turn_on",), "turn_on"

        async def is_intercom(_target_id: str, _action_id: str) -> bool:
            return True

        async def prepare_release(*_args: object, **_options: object) -> int:
            return 5

        async def cancel_release(*_args: object, **_options: object) -> bool:
            return True

        async def execute_single(*_args: object, **options: object) -> dict[str, object]:
            nonlocal single_calls
            single_calls += 1
            marker = options.get("dispatch_marker")
            self.assertTrue(callable(marker))
            marker()
            raise RuntimeError("power source command outcome is unknown")

        async def execute_batch(*_args: object, **options: object) -> list[dict[str, object]]:
            nonlocal batch_calls
            batch_calls += 1
            marker = options.get("dispatch_marker")
            self.assertTrue(callable(marker))
            marker()
            raise RuntimeError("power source confirmation timed out")

        service.async_resolve_device_action_context = resolve_context
        service.async_is_intercom_action = is_intercom
        service.async_prepare_intercom_release = prepare_release
        service.async_cancel_intercom_release = cancel_release
        service.async_execute_device_action = execute_single
        service.async_execute_device_action_batch = execute_batch

        def send(path: str, payload: dict[str, object]) -> FakeResponse:
            media = (
                "application/vnd.hausmanhub.device-action-receipt.full+json"
                if path.endswith("device-actions")
                else "application/vnd.hausmanhub.device-action-batch-receipt.full+json"
            )
            request_media = media.replace("receipt", "request")
            return asyncio.run(
                views[path].post(
                    FakeJsonRequest(
                        "192.168.1.20", tablet, path, payload,
                        content_type=request_media, accept=media,
                    )
                )
            )

        single_path = "/api/hausman_hub/v1/device-actions"
        single_payload = {
            "contract": {"name": "hausman-hub-device-action-request", "version": 1},
            "correlationId": "power.source.single.1",
            "requestId": "power.source.single.request.1",
            "targetId": "power_source_single",
            "actionId": "turn_on",
            "confirmedByUser": True,
            "idempotencyKey": "power.source.single.key.1",
        }
        first_single = send(single_path, single_payload)
        replay_single = send(single_path, copy.deepcopy(single_payload))
        self.assertEqual(409, first_single.status)
        self.assertEqual(409, replay_single.status)
        self.assertEqual("dispatch_unknown", replay_single.payload["details"]["state"])
        self.assertEqual(1, single_calls)
        self.assertEqual(
            "application/vnd.hausmanhub.device-action-receipt.full+json",
            first_single.headers["Content-Type"],
        )

        batch_path = "/api/hausman_hub/v1/device-actions/batch"
        batch_payload = {
            "contract": {"name": "hausman-hub-device-action-batch-request", "version": 1},
            "correlationId": "power.source.batch.1",
            "requestId": "power.source.batch.request.1",
            "actions": [{
                "targetId": "power_source_batch",
                "actionId": "turn_on",
                "confirmedByUser": True,
                "idempotencyKey": "power.source.batch.key.1",
            }],
        }
        first_batch = send(batch_path, batch_payload)
        replay_batch = send(batch_path, copy.deepcopy(batch_payload))
        self.assertEqual(409, first_batch.status)
        self.assertEqual(409, replay_batch.status)
        self.assertEqual("dispatch_unknown", replay_batch.payload["details"]["state"])
        self.assertEqual(1, batch_calls)
        self.assertEqual(
            "application/vnd.hausmanhub.device-action-batch-receipt.full+json",
            first_batch.headers["Content-Type"],
        )

    def test_full_action_mark_pending_failure_is_cleaned_and_same_request_can_retry(self) -> None:
        """A failed pending save must not leak a reservation or dispatch a command."""

        path = "/api/hausman_hub/v1/device-actions"
        view = next(item for item in self.hass.http.views if item.url == path)
        service = self.hass.data["hausman_hub"]["scenario_service"]
        coordinator = self.hass.data["hausman_hub"]["device_action_idempotency"]
        tablet = reader_user("system-users")
        executions = 0

        async def resolve_context(_target_id: str, _action_id: str):
            return "switch.synthetic_pending", "switch", ("turn_on",), "turn_on"

        async def execute_action(
            target_id: str,
            action_id: str,
            _value: object,
            *,
            correlation_id: str,
            **options: object,
        ) -> dict[str, object]:
            nonlocal executions
            executions += 1
            marker = options["dispatch_marker"]
            marker()
            return {
                "correlationId": correlation_id,
                "requestId": options["request_id"],
                "targetId": target_id,
                "actionId": action_id,
                "accepted": True,
                "confirmed": True,
                "status": "confirmed",
            }

        service.async_resolve_device_action_context = resolve_context
        service.async_execute_device_action = execute_action
        original = coordinator.async_mark_pending
        failures = 1

        async def fail_once(key: str) -> None:
            nonlocal failures
            if failures:
                failures -= 1
                raise OSError("pending save failed")
            await original(key)

        coordinator.async_mark_pending = fail_once
        payload = {
            "contract": {"name": "hausman-hub-device-action-request", "version": 1},
            "correlationId": "pending.failure.1",
            "requestId": "pending.failure.request.1",
            "targetId": "pending_switch",
            "actionId": "turn_on",
        }

        def send() -> FakeResponse:
            return asyncio.run(
                view.post(
                    FakeJsonRequest(
                        "192.168.1.20",
                        tablet,
                        path,
                        copy.deepcopy(payload),
                        content_type="application/vnd.hausmanhub.device-action-request.full+json",
                        accept="application/vnd.hausmanhub.device-action-receipt.full+json",
                    )
                )
            )

        first = send()
        second = send()

        self.assertEqual(503, first.status)
        self.assertEqual(200, second.status)
        self.assertEqual(1, executions)

    def test_full_action_reservation_save_failure_returns_negotiated_503_without_dispatch(self) -> None:
        """An arbitrary reserve-store error is a fail-closed API response."""

        path = "/api/hausman_hub/v1/device-actions"
        view = next(item for item in self.hass.http.views if item.url == path)
        service = self.hass.data["hausman_hub"]["scenario_service"]
        coordinator = self.hass.data["hausman_hub"]["device_action_idempotency"]
        tablet = reader_user("system-users")
        executions = 0

        async def resolve_context(_target_id: str, _action_id: str):
            return "switch.synthetic_reserve", "switch", ("turn_on",), "turn_on"

        async def execute(*_args: object, **options: object) -> dict[str, object]:
            nonlocal executions
            executions += 1
            options["dispatch_marker"]()
            return {
                "correlationId": "reserve.failure.1",
                "requestId": options["request_id"],
                "targetId": "reserve_switch",
                "actionId": "turn_on",
                "accepted": True,
                "confirmed": True,
                "status": "confirmed",
            }

        service.async_resolve_device_action_context = resolve_context
        service.async_execute_device_action = execute
        original = coordinator.async_reserve
        failures = 1

        async def fail_once(*args: object, **kwargs: object):
            nonlocal failures
            if failures:
                failures -= 1
                raise OSError("reserve save failed")
            return await original(*args, **kwargs)

        coordinator.async_reserve = fail_once
        body = {
            "contract": {"name": "hausman-hub-device-action-request", "version": 1},
            "correlationId": "reserve.failure.1",
            "requestId": "reserve.failure.request.1",
            "targetId": "reserve_switch",
            "actionId": "turn_on",
        }

        def send() -> FakeResponse:
            return asyncio.run(
                view.post(
                    FakeJsonRequest(
                        "192.168.1.20",
                        tablet,
                        path,
                        copy.deepcopy(body),
                        content_type="application/vnd.hausmanhub.device-action-request.full+json",
                        accept="application/vnd.hausmanhub.device-action-receipt.full+json",
                    )
                )
            )

        first = send()
        second = send()
        self.assertEqual(503, first.status)
        self.assertEqual(
            "application/vnd.hausmanhub.device-action-receipt.full+json",
            first.headers["Content-Type"],
        )
        self.assertEqual(200, second.status)
        self.assertEqual(1, executions)

    def test_intercom_prepare_exception_and_none_cancel_and_release_reservation(self) -> None:
        """Every attempted prepare gets exact unarmed cleanup, even after partial save."""

        path = "/api/hausman_hub/v1/device-actions"
        view = next(item for item in self.hass.http.views if item.url == path)
        service = self.hass.data["hausman_hub"]["scenario_service"]
        tablet = reader_user("system-users")
        outcomes: list[object] = [RuntimeError("partial prepare"), 15, None, 15]
        cancels: list[dict[str, object]] = []
        executions = 0

        async def resolve_context(_target_id: str, _action_id: str):
            return "switch.synthetic_intercom_prepare", "switch", ("turn_on",), "turn_on"

        async def is_intercom(_target_id: str, _action_id: str) -> bool:
            return True

        async def prepare(*_args: object, **_options: object) -> int | None:
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        async def cancel(_target_id: str, **options: object) -> bool:
            cancels.append(options)
            return True

        async def execute(
            target_id: str,
            action_id: str,
            _value: object,
            *,
            correlation_id: str,
            **options: object,
        ) -> dict[str, object]:
            nonlocal executions
            executions += 1
            options["dispatch_marker"]()
            return {
                "correlationId": correlation_id,
                "requestId": options["request_id"],
                "targetId": target_id,
                "actionId": action_id,
                "accepted": True,
                "confirmed": True,
                "status": "confirmed",
            }

        service.async_resolve_device_action_context = resolve_context
        service.async_is_intercom_action = is_intercom
        service.async_prepare_intercom_release = prepare
        service.async_cancel_intercom_release = cancel
        service.async_execute_device_action = execute

        def payload(suffix: str) -> dict[str, object]:
            return {
                "contract": {"name": "hausman-hub-device-action-request", "version": 1},
                "correlationId": f"prepare.{suffix}",
                "requestId": f"prepare.request.{suffix}",
                "targetId": "intercom_prepare",
                "actionId": "turn_on",
                "confirmedByUser": True,
                "idempotencyKey": f"prepare.key.{suffix}",
            }

        def send(body: dict[str, object]) -> FakeResponse:
            return asyncio.run(
                view.post(
                    FakeJsonRequest(
                        "192.168.1.20",
                        tablet,
                        path,
                        copy.deepcopy(body),
                        content_type="application/vnd.hausmanhub.device-action-request.full+json",
                        accept="application/vnd.hausmanhub.device-action-receipt.full+json",
                    )
                )
            )

        exception_payload = payload("exception")
        self.assertEqual(503, send(exception_payload).status)
        self.assertEqual(200, send(exception_payload).status)
        none_payload = payload("none")
        self.assertEqual(503, send(none_payload).status)
        self.assertEqual(200, send(none_payload).status)

        self.assertEqual(2, executions)
        self.assertEqual(2, len(cancels))
        self.assertTrue(all(item.get("unarmed_only") is True for item in cancels))

    def test_post_dispatch_rejected_receipt_is_unknown_and_never_replayed(self) -> None:
        """A negative receipt after a physical marker cannot become a completed replay."""

        path = "/api/hausman_hub/v1/device-actions"
        view = next(item for item in self.hass.http.views if item.url == path)
        service = self.hass.data["hausman_hub"]["scenario_service"]
        tablet = reader_user("system-users")
        executions = 0

        async def resolve_context(_target_id: str, _action_id: str):
            return "switch.synthetic_rejected", "switch", ("turn_on",), "turn_on"

        async def execute(
            target_id: str,
            action_id: str,
            _value: object,
            *,
            correlation_id: str,
            **options: object,
        ) -> dict[str, object]:
            nonlocal executions
            executions += 1
            options["dispatch_marker"]()
            return {
                "correlationId": correlation_id,
                "requestId": options["request_id"],
                "targetId": target_id,
                "actionId": action_id,
                "accepted": False,
                "confirmed": False,
                "status": "failed",
            }

        service.async_resolve_device_action_context = resolve_context
        service.async_execute_device_action = execute
        body = {
            "contract": {"name": "hausman-hub-device-action-request", "version": 1},
            "correlationId": "rejected.after.dispatch.1",
            "requestId": "rejected.after.dispatch.request.1",
            "targetId": "rejected_switch",
            "actionId": "turn_on",
        }

        def send() -> FakeResponse:
            return asyncio.run(
                view.post(
                    FakeJsonRequest(
                        "192.168.1.20",
                        tablet,
                        path,
                        copy.deepcopy(body),
                        content_type="application/vnd.hausmanhub.device-action-request.full+json",
                        accept="application/vnd.hausmanhub.device-action-receipt.full+json",
                    )
                )
            )

        first = send()
        replay = send()

        self.assertEqual(409, first.status)
        self.assertEqual("dispatch_unknown", first.payload["details"]["state"])
        self.assertEqual(409, replay.status)
        self.assertEqual("dispatch_unknown", replay.payload["details"]["state"])
        self.assertEqual(
            "application/vnd.hausmanhub.device-action-receipt.full+json",
            replay.headers["Content-Type"],
        )
        self.assertEqual(1, executions)

    def test_climate_mode_postprocessing_failure_keeps_completed_device_receipt(self) -> None:
        """A mode bookkeeping failure cannot repeat an already dispatched climate command."""

        path = "/api/hausman_hub/v1/device-actions"
        view = next(item for item in self.hass.http.views if item.url == path)
        service = self.hass.data["hausman_hub"]["scenario_service"]
        tablet = reader_user("system-users")
        entity_id = "climate.synthetic_postprocess"
        self.hass.states.values[entity_id] = SimpleNamespace(
            state="off", attributes={}, last_updated=datetime.now(timezone.utc)
        )
        executions = 0

        async def resolve_context(_target_id: str, _action_id: str):
            return entity_id, "climate", ("turn_off",), "turn_off"

        async def resolve(_target_id: str, _action_id: str):
            return entity_id, "climate"

        async def execute(
            target_id: str,
            action_id: str,
            _value: object,
            *,
            correlation_id: str,
            **options: object,
        ) -> dict[str, object]:
            nonlocal executions
            executions += 1
            options["dispatch_marker"]()
            return {
                "correlationId": correlation_id,
                "requestId": options["request_id"],
                "targetId": target_id,
                "actionId": action_id,
                "accepted": True,
                "confirmed": True,
                "status": "confirmed",
            }

        mode_outcomes: list[object] = [OSError("mode journal unavailable"), None]

        async def fail_mode(*_args: object) -> object:
            outcome = mode_outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        service.async_resolve_device_action_context = resolve_context
        service.async_resolve_device_action = resolve
        service.async_execute_device_action = execute
        self.hass.data["hausman_hub"]["climate_runtime"] = SimpleNamespace(
            async_set_device_mode_for_entity=fail_mode
        )
        def body(suffix: str) -> dict[str, object]:
            return {
                "contract": {
                    "name": "hausman-hub-device-action-request",
                    "version": 1,
                },
                "correlationId": f"climate.postprocess.{suffix}",
                "requestId": f"climate.postprocess.request.{suffix}",
                "targetId": "climate_postprocess",
                "actionId": "turn_off",
            }

        def send(payload: dict[str, object]) -> FakeResponse:
            return asyncio.run(
                view.post(
                    FakeJsonRequest(
                        "192.168.1.20",
                        tablet,
                        path,
                        copy.deepcopy(payload),
                        content_type="application/vnd.hausmanhub.device-action-request.full+json",
                        accept="application/vnd.hausmanhub.device-action-receipt.full+json",
                    )
                )
            )

        raised = body("raised")
        first = send(raised)
        replay = send(raised)
        malformed = body("malformed")
        malformed_first = send(malformed)
        malformed_replay = send(malformed)

        self.assertEqual(200, first.status)
        self.assertEqual(first.payload, replay.payload)
        self.assertEqual(200, malformed_first.status)
        self.assertEqual(malformed_first.payload, malformed_replay.payload)
        self.assertEqual(2, executions)

    def test_complete_failure_after_dispatch_is_unknown_and_publish_failure_is_nonfatal(self) -> None:
        """Persistence owns retry safety; later publication is best effort."""

        path = "/api/hausman_hub/v1/device-actions"
        view = next(item for item in self.hass.http.views if item.url == path)
        service = self.hass.data["hausman_hub"]["scenario_service"]
        coordinator = self.hass.data["hausman_hub"]["device_action_idempotency"]
        tablet = reader_user("system-users")
        executions = 0

        async def resolve_context(_target_id: str, _action_id: str):
            return "switch.synthetic_complete", "switch", ("turn_on",), "turn_on"

        async def execute(
            target_id: str,
            action_id: str,
            _value: object,
            *,
            correlation_id: str,
            **options: object,
        ) -> dict[str, object]:
            nonlocal executions
            executions += 1
            options["dispatch_marker"]()
            return {
                "correlationId": correlation_id,
                "requestId": options["request_id"],
                "targetId": target_id,
                "actionId": action_id,
                "accepted": True,
                "confirmed": True,
                "status": "confirmed",
            }

        service.async_resolve_device_action_context = resolve_context
        service.async_execute_device_action = execute

        def body(suffix: str) -> dict[str, object]:
            return {
                "contract": {"name": "hausman-hub-device-action-request", "version": 1},
                "correlationId": f"complete.{suffix}",
                "requestId": f"complete.request.{suffix}",
                "targetId": "complete_switch",
                "actionId": "turn_on",
            }

        def send(payload: dict[str, object]) -> FakeResponse:
            return asyncio.run(
                view.post(
                    FakeJsonRequest(
                        "192.168.1.20",
                        tablet,
                        path,
                        copy.deepcopy(payload),
                        content_type="application/vnd.hausmanhub.device-action-request.full+json",
                        accept="application/vnd.hausmanhub.device-action-receipt.full+json",
                    )
                )
            )

        original_complete = coordinator.async_complete

        async def fail_complete(*_args: object, **_options: object) -> None:
            raise OSError("complete save failed")

        coordinator.async_complete = fail_complete
        failed_body = body("failure")
        first = send(failed_body)
        coordinator.async_complete = original_complete
        replay = send(failed_body)
        self.assertEqual(409, first.status)
        self.assertEqual("dispatch_unknown", first.payload["details"]["state"])
        self.assertEqual(409, replay.status)
        self.assertEqual(1, executions)

        published_body = body("publish")
        with patch(
            "custom_components.hausman_hub.device_action_api.publish_command_receipt",
            side_effect=RuntimeError("broker unavailable"),
        ):
            published = send(published_body)
        published_replay = send(published_body)
        self.assertEqual(200, published.status)
        self.assertEqual(published.payload, published_replay.payload)
        self.assertEqual(2, executions)

    def test_complete_failure_before_dispatch_abandons_and_allows_exact_retry(self) -> None:
        """A failed terminal save can be retried only when no physical marker crossed."""

        path = "/api/hausman_hub/v1/device-actions"
        view = next(item for item in self.hass.http.views if item.url == path)
        service = self.hass.data["hausman_hub"]["scenario_service"]
        coordinator = self.hass.data["hausman_hub"]["device_action_idempotency"]
        tablet = reader_user("system-users")
        executions = 0

        async def resolve_context(_target_id: str, _action_id: str):
            return "switch.synthetic_safe_complete", "switch", ("turn_on",), "turn_on"

        async def execute(
            target_id: str,
            action_id: str,
            _value: object,
            *,
            correlation_id: str,
            **options: object,
        ) -> dict[str, object]:
            nonlocal executions
            executions += 1
            return {
                "correlationId": correlation_id,
                "requestId": options["request_id"],
                "targetId": target_id,
                "actionId": action_id,
                "accepted": False,
                "confirmed": False,
                "status": "failed",
            }

        service.async_resolve_device_action_context = resolve_context
        service.async_execute_device_action = execute
        original_complete = coordinator.async_complete
        failures = 1

        async def fail_once(*args: object, **kwargs: object) -> None:
            nonlocal failures
            if failures:
                failures -= 1
                raise OSError("safe complete failed")
            await original_complete(*args, **kwargs)

        coordinator.async_complete = fail_once
        body = {
            "contract": {"name": "hausman-hub-device-action-request", "version": 1},
            "correlationId": "safe.complete.1",
            "requestId": "safe.complete.request.1",
            "targetId": "safe_complete",
            "actionId": "turn_on",
        }

        def send() -> FakeResponse:
            return asyncio.run(
                view.post(
                    FakeJsonRequest(
                        "192.168.1.20",
                        tablet,
                        path,
                        copy.deepcopy(body),
                        content_type="application/vnd.hausmanhub.device-action-request.full+json",
                        accept="application/vnd.hausmanhub.device-action-receipt.full+json",
                    )
                )
            )

        first = send()
        second = send()
        replay = send()
        self.assertEqual(503, first.status)
        self.assertEqual(409, second.status)
        self.assertEqual(409, replay.status)
        self.assertEqual(second.payload, replay.payload)
        self.assertEqual(2, executions)

    def test_full_receipt_builder_failure_after_dispatch_is_unknown(self) -> None:
        """Receipt construction cannot erase an already crossed dispatch boundary."""

        path = "/api/hausman_hub/v1/device-actions"
        view = next(item for item in self.hass.http.views if item.url == path)
        service = self.hass.data["hausman_hub"]["scenario_service"]
        tablet = reader_user("system-users")
        executions = 0

        async def resolve_context(_target_id: str, _action_id: str):
            return "switch.synthetic_builder", "switch", ("turn_on",), "turn_on"

        async def execute(
            target_id: str,
            action_id: str,
            _value: object,
            *,
            correlation_id: str,
            **options: object,
        ) -> dict[str, object]:
            nonlocal executions
            executions += 1
            options["dispatch_marker"]()
            return {
                "correlationId": correlation_id,
                "requestId": options["request_id"],
                "targetId": target_id,
                "actionId": action_id,
                "accepted": True,
                "confirmed": True,
                "status": "confirmed",
            }

        service.async_resolve_device_action_context = resolve_context
        service.async_execute_device_action = execute
        body = {
            "contract": {"name": "hausman-hub-device-action-request", "version": 1},
            "correlationId": "builder.failure.1",
            "requestId": "builder.failure.request.1",
            "targetId": "builder_switch",
            "actionId": "turn_on",
        }

        def send() -> FakeResponse:
            return asyncio.run(
                view.post(
                    FakeJsonRequest(
                        "192.168.1.20",
                        tablet,
                        path,
                        copy.deepcopy(body),
                        content_type="application/vnd.hausmanhub.device-action-request.full+json",
                        accept="application/vnd.hausmanhub.device-action-receipt.full+json",
                    )
                )
            )

        with patch(
            "custom_components.hausman_hub.device_action_api.full_action_receipt",
            side_effect=TypeError("receipt builder failed"),
        ):
            first = send()
        replay = send()
        self.assertEqual(409, first.status)
        self.assertEqual("dispatch_unknown", first.payload["details"]["state"])
        self.assertEqual(409, replay.status)
        self.assertEqual(1, executions)

    def test_full_dry_run_postprocessing_failures_are_negotiated_without_reservation(self) -> None:
        """Command-free full requests still return a structured pre-dispatch error."""

        views = {item.url: item for item in self.hass.http.views}
        service = self.hass.data["hausman_hub"]["scenario_service"]
        tablet = reader_user("system-users")
        single_path = "/api/hausman_hub/v1/device-actions"
        batch_path = "/api/hausman_hub/v1/device-actions/batch"

        async def resolve_context(_target_id: str, _action_id: str):
            return "switch.synthetic_dry", "switch", ("turn_on",), "turn_on"

        async def execute_single(
            target_id: str,
            action_id: str,
            _value: object,
            *,
            correlation_id: str,
            **_options: object,
        ) -> dict[str, object]:
            return {
                "correlationId": correlation_id,
                "requestId": "dry.single.dispatch",
                "targetId": target_id,
                "actionId": action_id,
                "accepted": True,
                "confirmed": None,
                "status": "planned",
            }

        async def malformed_batch(*_args: object, **_options: object) -> list[object]:
            return []

        service.async_resolve_device_action_context = resolve_context
        service.async_execute_device_action = execute_single
        service.async_execute_device_action_batch = malformed_batch
        single_body = {
            "contract": {"name": "hausman-hub-device-action-request", "version": 1},
            "correlationId": "dry.postprocess.single.1",
            "requestId": "dry.postprocess.single.request.1",
            "targetId": "dry_switch",
            "actionId": "turn_on",
            "dryRun": True,
        }
        with patch(
            "custom_components.hausman_hub.device_action_api.full_action_receipt",
            side_effect=TypeError("dry receipt failed"),
        ):
            single = asyncio.run(
                views[single_path].post(
                    FakeJsonRequest(
                        "192.168.1.20",
                        tablet,
                        single_path,
                        single_body,
                        content_type="application/vnd.hausmanhub.device-action-request.full+json",
                        accept="application/vnd.hausmanhub.device-action-receipt.full+json",
                    )
                )
            )
        batch_body = {
            "contract": {
                "name": "hausman-hub-device-action-batch-request",
                "version": 1,
            },
            "correlationId": "dry.postprocess.batch.1",
            "requestId": "dry.postprocess.batch.request.1",
            "actions": [
                {"targetId": "dry_switch", "actionId": "turn_on", "dryRun": True}
            ],
        }
        batch = asyncio.run(
            views[batch_path].post(
                FakeJsonRequest(
                    "192.168.1.20",
                    tablet,
                    batch_path,
                    batch_body,
                    content_type="application/vnd.hausmanhub.device-action-batch-request.full+json",
                    accept="application/vnd.hausmanhub.device-action-batch-receipt.full+json",
                )
            )
        )

        self.assertEqual(503, single.status)
        self.assertEqual(
            "application/vnd.hausmanhub.device-action-receipt.full+json",
            single.headers["Content-Type"],
        )
        self.assertEqual(503, batch.status)
        self.assertEqual(
            "application/vnd.hausmanhub.device-action-batch-receipt.full+json",
            batch.headers["Content-Type"],
        )

    def test_cancelled_device_action_is_not_swallowed_and_replay_is_unknown(self) -> None:
        """Task cancellation propagates while the durable dispatch fence remains."""

        path = "/api/hausman_hub/v1/device-actions"
        view = next(item for item in self.hass.http.views if item.url == path)
        service = self.hass.data["hausman_hub"]["scenario_service"]
        tablet = reader_user("system-users")
        executions = 0

        async def resolve_context(_target_id: str, _action_id: str):
            return "switch.synthetic_cancelled", "switch", ("turn_on",), "turn_on"

        async def execute(*_args: object, **options: object) -> dict[str, object]:
            nonlocal executions
            executions += 1
            options["dispatch_marker"]()
            raise asyncio.CancelledError

        service.async_resolve_device_action_context = resolve_context
        service.async_execute_device_action = execute
        body = {
            "contract": {"name": "hausman-hub-device-action-request", "version": 1},
            "correlationId": "cancelled.action.1",
            "requestId": "cancelled.action.request.1",
            "targetId": "cancelled_switch",
            "actionId": "turn_on",
        }

        def request() -> FakeJsonRequest:
            return FakeJsonRequest(
                "192.168.1.20",
                tablet,
                path,
                copy.deepcopy(body),
                content_type="application/vnd.hausmanhub.device-action-request.full+json",
                accept="application/vnd.hausmanhub.device-action-receipt.full+json",
            )

        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(view.post(request()))
        replay = asyncio.run(view.post(request()))

        self.assertEqual(409, replay.status)
        self.assertEqual("dispatch_unknown", replay.payload["details"]["state"])
        self.assertEqual(1, executions)

    def test_batch_tracks_dispatch_per_item_and_rejects_malformed_receipt_count(self) -> None:
        """Only a failed item with its own side effect makes the whole batch unknown."""

        path = "/api/hausman_hub/v1/device-actions/batch"
        view = next(item for item in self.hass.http.views if item.url == path)
        service = self.hass.data["hausman_hub"]["scenario_service"]
        tablet = reader_user("system-users")
        mode = "known_partial"
        executions = 0

        async def resolve_context(target_id: str, _action_id: str):
            return f"switch.synthetic_{target_id}", "switch", ("turn_on",), "turn_on"

        async def execute(
            actions: list[dict[str, object]],
            *,
            correlation_id: str,
            request_ids: tuple[str, ...],
            **options: object,
        ) -> list[dict[str, object]]:
            nonlocal executions
            executions += 1
            markers = options["dispatch_markers"]
            if mode == "known_partial":
                markers[0]()
                accepted = (True, False)
            elif mode == "unknown_second":
                markers[1]()
                accepted = (True, False)
            elif mode == "unattributed_unknown":
                options["dispatch_marker"]()
                accepted = (True, False)
            elif mode == "count_mismatch_safe":
                return []
            else:
                markers[0]()
                return []
            return [
                {
                    "correlationId": correlation_id,
                    "requestId": request_ids[index],
                    "targetId": str(item["targetId"]),
                    "actionId": str(item["actionId"]),
                    "accepted": accepted[index],
                    "confirmed": accepted[index],
                    "status": "confirmed" if accepted[index] else "failed",
                }
                for index, item in enumerate(actions)
            ]

        service.async_resolve_device_action_context = resolve_context
        service.async_execute_device_action_batch = execute

        def body(suffix: str) -> dict[str, object]:
            return {
                "contract": {
                    "name": "hausman-hub-device-action-batch-request",
                    "version": 1,
                },
                "correlationId": f"batch.items.{suffix}",
                "requestId": f"batch.items.request.{suffix}",
                "actions": [
                    {"targetId": "first", "actionId": "turn_on"},
                    {"targetId": "second", "actionId": "turn_on"},
                ],
            }

        def send(payload: dict[str, object]) -> FakeResponse:
            return asyncio.run(
                view.post(
                    FakeJsonRequest(
                        "192.168.1.20",
                        tablet,
                        path,
                        copy.deepcopy(payload),
                        content_type="application/vnd.hausmanhub.device-action-batch-request.full+json",
                        accept="application/vnd.hausmanhub.device-action-batch-receipt.full+json",
                    )
                )
            )

        known = body("known")
        first_known = send(known)
        replay_known = send(known)
        self.assertEqual(200, first_known.status)
        self.assertEqual("partial", first_known.payload["status"])
        self.assertEqual(first_known.payload, replay_known.payload)
        self.assertEqual(1, executions)

        mode = "unknown_second"
        unknown = body("unknown")
        first_unknown = send(unknown)
        replay_unknown = send(unknown)
        self.assertEqual(409, first_unknown.status)
        self.assertEqual("dispatch_unknown", first_unknown.payload["details"]["state"])
        self.assertEqual(409, replay_unknown.status)
        self.assertEqual(
            "application/vnd.hausmanhub.device-action-batch-receipt.full+json",
            replay_unknown.headers["Content-Type"],
        )
        self.assertEqual(2, executions)

        mode = "unattributed_unknown"
        unattributed = body("unattributed")
        first_unattributed = send(unattributed)
        replay_unattributed = send(unattributed)
        self.assertEqual(409, first_unattributed.status)
        self.assertEqual(
            "dispatch_unknown", first_unattributed.payload["details"]["state"]
        )
        self.assertEqual(409, replay_unattributed.status)
        self.assertEqual(3, executions)

        mode = "count_mismatch_safe"
        safe_malformed = body("safe-malformed")
        first_safe_malformed = send(safe_malformed)
        mode = "known_partial"
        retried_safe_malformed = send(safe_malformed)
        self.assertEqual(503, first_safe_malformed.status)
        self.assertEqual(200, retried_safe_malformed.status)
        self.assertEqual(5, executions)

        mode = "count_mismatch"
        malformed = body("malformed")
        first_malformed = send(malformed)
        replay_malformed = send(malformed)
        self.assertEqual(409, first_malformed.status)
        self.assertEqual("dispatch_unknown", first_malformed.payload["details"]["state"])
        self.assertEqual(409, replay_malformed.status)
        self.assertEqual(6, executions)

    def test_batch_pending_and_intercom_prepare_failures_cleanup_before_retry(self) -> None:
        """The batch path uses the same pre-dispatch cleanup lifecycle as single."""

        path = "/api/hausman_hub/v1/device-actions/batch"
        view = next(item for item in self.hass.http.views if item.url == path)
        service = self.hass.data["hausman_hub"]["scenario_service"]
        coordinator = self.hass.data["hausman_hub"]["device_action_idempotency"]
        tablet = reader_user("system-users")
        executions = 0

        async def resolve_context(target_id: str, _action_id: str):
            return f"switch.synthetic_{target_id}", "switch", ("turn_on",), "turn_on"

        async def is_intercom(target_id: str, _action_id: str) -> bool:
            return target_id == "batch_intercom"

        prepare_failures = 1
        cancel_calls: list[dict[str, object]] = []

        async def prepare(*_args: object, **_options: object) -> int:
            nonlocal prepare_failures
            if prepare_failures:
                prepare_failures -= 1
                raise OSError("partial intercom prepare")
            return 15

        async def cancel(_target_id: str, **options: object) -> bool:
            cancel_calls.append(options)
            return True

        async def execute(
            actions: list[dict[str, object]],
            *,
            correlation_id: str,
            request_ids: tuple[str, ...],
            **options: object,
        ) -> list[dict[str, object]]:
            nonlocal executions
            executions += 1
            options["dispatch_markers"][0]()
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
        service.async_prepare_intercom_release = prepare
        service.async_cancel_intercom_release = cancel
        service.async_execute_device_action_batch = execute

        def body(suffix: str, *, intercom: bool) -> dict[str, object]:
            action: dict[str, object] = {
                "targetId": "batch_intercom" if intercom else "batch_switch",
                "actionId": "turn_on",
            }
            if intercom:
                action.update(
                    confirmedByUser=True,
                    idempotencyKey=f"batch.cleanup.key.{suffix}",
                )
            return {
                "contract": {
                    "name": "hausman-hub-device-action-batch-request",
                    "version": 1,
                },
                "correlationId": f"batch.cleanup.{suffix}",
                "requestId": f"batch.cleanup.request.{suffix}",
                "actions": [action],
            }

        def send(payload: dict[str, object]) -> FakeResponse:
            return asyncio.run(
                view.post(
                    FakeJsonRequest(
                        "192.168.1.20",
                        tablet,
                        path,
                        copy.deepcopy(payload),
                        content_type="application/vnd.hausmanhub.device-action-batch-request.full+json",
                        accept="application/vnd.hausmanhub.device-action-batch-receipt.full+json",
                    )
                )
            )

        original_pending = coordinator.async_mark_pending
        pending_failures = 1

        async def fail_pending_once(key: str) -> None:
            nonlocal pending_failures
            if pending_failures:
                pending_failures -= 1
                raise OSError("batch pending save failed")
            await original_pending(key)

        coordinator.async_mark_pending = fail_pending_once
        pending_body = body("pending", intercom=False)
        self.assertEqual(503, send(pending_body).status)
        self.assertEqual(200, send(pending_body).status)

        prepare_body = body("prepare", intercom=True)
        self.assertEqual(503, send(prepare_body).status)
        self.assertEqual(200, send(prepare_body).status)

        self.assertEqual(2, executions)
        self.assertEqual(1, len(cancel_calls))
        self.assertIs(cancel_calls[0]["unarmed_only"], True)

    def test_batch_late_intercom_failure_cancels_unarmed_after_earlier_dispatch(self) -> None:
        """An earlier batch side effect does not suppress exact intercom cleanup."""

        path = "/api/hausman_hub/v1/device-actions/batch"
        view = next(item for item in self.hass.http.views if item.url == path)
        service = self.hass.data["hausman_hub"]["scenario_service"]
        tablet = reader_user("system-users")
        cancels: list[dict[str, object]] = []
        executions = 0

        async def resolve_context(target_id: str, _action_id: str):
            return f"switch.synthetic_{target_id}", "switch", ("turn_on",), "turn_on"

        async def is_intercom(target_id: str, _action_id: str) -> bool:
            return target_id == "late_intercom"

        async def prepare(*_args: object, **_options: object) -> int:
            return 15

        async def cancel(_target_id: str, **options: object) -> bool:
            cancels.append(options)
            return True

        async def execute(*_args: object, **options: object) -> list[dict[str, object]]:
            nonlocal executions
            executions += 1
            options["dispatch_markers"][0]()
            raise OSError("late intercom failed before arm")

        service.async_resolve_device_action_context = resolve_context
        service.async_is_intercom_action = is_intercom
        service.async_prepare_intercom_release = prepare
        service.async_cancel_intercom_release = cancel
        service.async_execute_device_action_batch = execute
        body = {
            "contract": {
                "name": "hausman-hub-device-action-batch-request",
                "version": 1,
            },
            "correlationId": "batch.late.intercom.1",
            "requestId": "batch.late.intercom.request.1",
            "actions": [
                {"targetId": "first_switch", "actionId": "turn_on"},
                {
                    "targetId": "late_intercom",
                    "actionId": "turn_on",
                    "confirmedByUser": True,
                    "idempotencyKey": "batch.late.intercom.key.1",
                },
            ],
        }

        def send() -> FakeResponse:
            return asyncio.run(
                view.post(
                    FakeJsonRequest(
                        "192.168.1.20",
                        tablet,
                        path,
                        copy.deepcopy(body),
                        content_type="application/vnd.hausmanhub.device-action-batch-request.full+json",
                        accept="application/vnd.hausmanhub.device-action-batch-receipt.full+json",
                    )
                )
            )

        first = send()
        replay = send()
        self.assertEqual(409, first.status)
        self.assertEqual("dispatch_unknown", first.payload["details"]["state"])
        self.assertEqual(409, replay.status)
        self.assertEqual(1, executions)
        self.assertEqual(1, len(cancels))
        self.assertIs(cancels[0]["unarmed_only"], True)

    def test_cleanup_failures_are_independent_and_batch_complete_is_unknown(self) -> None:
        """Cancel and abandon both run; a dispatched batch never retries after save loss."""

        views = {item.url: item for item in self.hass.http.views}
        single_path = "/api/hausman_hub/v1/device-actions"
        batch_path = "/api/hausman_hub/v1/device-actions/batch"
        service = self.hass.data["hausman_hub"]["scenario_service"]
        coordinator = self.hass.data["hausman_hub"]["device_action_idempotency"]
        tablet = reader_user("system-users")
        cancel_calls = 0
        abandon_calls = 0
        batch_executions = 0

        async def resolve_context(target_id: str, _action_id: str):
            return f"switch.synthetic_{target_id}", "switch", ("turn_on",), "turn_on"

        async def is_intercom(target_id: str, _action_id: str) -> bool:
            return target_id == "cleanup_intercom"

        async def prepare(*_args: object, **_options: object) -> None:
            raise OSError("prepare partially saved an unarmed record")

        async def cancel(*_args: object, **options: object) -> bool:
            nonlocal cancel_calls
            cancel_calls += 1
            self.assertIs(options["unarmed_only"], True)
            raise OSError("cancel store failed")

        async def abandon(_key: str) -> None:
            nonlocal abandon_calls
            abandon_calls += 1
            raise OSError("idempotency cleanup failed")

        service.async_resolve_device_action_context = resolve_context
        service.async_is_intercom_action = is_intercom
        service.async_prepare_intercom_release = prepare
        service.async_cancel_intercom_release = cancel
        original_abandon = coordinator.async_abandon_pre_dispatch
        coordinator.async_abandon_pre_dispatch = abandon
        single_body = {
            "contract": {"name": "hausman-hub-device-action-request", "version": 1},
            "correlationId": "cleanup.independent.1",
            "requestId": "cleanup.independent.request.1",
            "targetId": "cleanup_intercom",
            "actionId": "turn_on",
            "confirmedByUser": True,
            "idempotencyKey": "cleanup.independent.key.1",
        }
        single = asyncio.run(
            views[single_path].post(
                FakeJsonRequest(
                    "192.168.1.20",
                    tablet,
                    single_path,
                    single_body,
                    content_type="application/vnd.hausmanhub.device-action-request.full+json",
                    accept="application/vnd.hausmanhub.device-action-receipt.full+json",
                )
            )
        )
        self.assertEqual(503, single.status)
        self.assertEqual(1, cancel_calls)
        self.assertEqual(1, abandon_calls)

        coordinator.async_abandon_pre_dispatch = original_abandon

        async def no_intercom(_target_id: str, _action_id: str) -> bool:
            return False

        async def execute_batch(
            actions: list[dict[str, object]],
            *,
            correlation_id: str,
            request_ids: tuple[str, ...],
            **options: object,
        ) -> list[dict[str, object]]:
            nonlocal batch_executions
            batch_executions += 1
            options["dispatch_markers"][0]()
            return [
                {
                    "correlationId": correlation_id,
                    "requestId": request_ids[0],
                    "targetId": str(actions[0]["targetId"]),
                    "actionId": str(actions[0]["actionId"]),
                    "accepted": True,
                    "confirmed": True,
                    "status": "confirmed",
                }
            ]

        service.async_is_intercom_action = no_intercom
        service.async_execute_device_action_batch = execute_batch
        original_complete = coordinator.async_complete

        async def fail_complete(*_args: object, **_options: object) -> None:
            raise OSError("batch completion save failed")

        coordinator.async_complete = fail_complete
        batch_body = {
            "contract": {
                "name": "hausman-hub-device-action-batch-request",
                "version": 1,
            },
            "correlationId": "batch.complete.failure.1",
            "requestId": "batch.complete.failure.request.1",
            "actions": [{"targetId": "batch_complete", "actionId": "turn_on"}],
        }

        def send_batch() -> FakeResponse:
            return asyncio.run(
                views[batch_path].post(
                    FakeJsonRequest(
                        "192.168.1.20",
                        tablet,
                        batch_path,
                        copy.deepcopy(batch_body),
                        content_type="application/vnd.hausmanhub.device-action-batch-request.full+json",
                        accept="application/vnd.hausmanhub.device-action-batch-receipt.full+json",
                    )
                )
            )

        failed = send_batch()
        coordinator.async_complete = original_complete
        replay = send_batch()
        self.assertEqual(409, failed.status)
        self.assertEqual("dispatch_unknown", failed.payload["details"]["state"])
        self.assertEqual(409, replay.status)
        self.assertEqual(1, batch_executions)

    def test_single_and_batch_arbitrary_execution_exceptions_are_negotiated(self) -> None:
        """Expected runtime exceptions map to 503/409 without swallowing BaseException."""

        from homeassistant.exceptions import HomeAssistantError

        views = {item.url: item for item in self.hass.http.views}
        single_path = "/api/hausman_hub/v1/device-actions"
        batch_path = "/api/hausman_hub/v1/device-actions/batch"
        service = self.hass.data["hausman_hub"]["scenario_service"]
        tablet = reader_user("system-users")
        exception_types = (
            HomeAssistantError,
            OSError,
            ConnectionError,
            KeyError,
            TypeError,
            RuntimeError,
            TimeoutError,
            ValueError,
        )
        current_exception: type[Exception] = RuntimeError
        current_crossed = False

        async def resolve_context(_target_id: str, _action_id: str):
            return "switch.synthetic_exception", "switch", ("turn_on",), "turn_on"

        async def execute_single(*_args: object, **options: object) -> dict[str, object]:
            if current_crossed:
                options["dispatch_marker"]()
            raise current_exception("single failure")

        async def execute_batch(*_args: object, **options: object) -> list[dict[str, object]]:
            if current_crossed:
                options["dispatch_markers"][0]()
            raise current_exception("batch failure")

        service.async_resolve_device_action_context = resolve_context
        service.async_execute_device_action = execute_single
        service.async_execute_device_action_batch = execute_batch

        for route, media in (
            (single_path, "device-action"),
            (batch_path, "device-action-batch"),
        ):
            for crossed in (False, True):
                current_crossed = crossed
                for index, error_type in enumerate(exception_types):
                    current_exception = error_type
                    suffix = f"{media}.{int(crossed)}.{index}"
                    if route == single_path:
                        body = {
                            "contract": {
                                "name": "hausman-hub-device-action-request",
                                "version": 1,
                            },
                            "correlationId": f"exceptions.{suffix}",
                            "requestId": f"exceptions.request.{suffix}",
                            "targetId": "exception_switch",
                            "actionId": "turn_on",
                        }
                        request_media = "application/vnd.hausmanhub.device-action-request.full+json"
                        response_media = "application/vnd.hausmanhub.device-action-receipt.full+json"
                    else:
                        body = {
                            "contract": {
                                "name": "hausman-hub-device-action-batch-request",
                                "version": 1,
                            },
                            "correlationId": f"exceptions.{suffix}",
                            "requestId": f"exceptions.request.{suffix}",
                            "actions": [
                                {"targetId": "exception_switch", "actionId": "turn_on"}
                            ],
                        }
                        request_media = "application/vnd.hausmanhub.device-action-batch-request.full+json"
                        response_media = "application/vnd.hausmanhub.device-action-batch-receipt.full+json"
                    with self.subTest(
                        route=route, crossed=crossed, error=error_type.__name__
                    ):
                        response = asyncio.run(
                            views[route].post(
                                FakeJsonRequest(
                                    "192.168.1.20",
                                    tablet,
                                    route,
                                    body,
                                    content_type=request_media,
                                    accept=response_media,
                                )
                            )
                        )
                        self.assertEqual(409 if crossed else 503, response.status)
                        self.assertEqual(response_media, response.headers["Content-Type"])

    def test_single_and_batch_do_not_swallow_process_control_exceptions(self) -> None:
        """Cancellation, keyboard interruption and process exit stay outside API mapping."""

        views = {item.url: item for item in self.hass.http.views}
        service = self.hass.data["hausman_hub"]["scenario_service"]
        tablet = reader_user("system-users")

        async def resolve_context(_target_id: str, _action_id: str):
            return "switch.synthetic_base_exception", "switch", ("turn_on",), "turn_on"

        service.async_resolve_device_action_context = resolve_context
        exception_types = (asyncio.CancelledError, KeyboardInterrupt, SystemExit)
        for route, batch in (
            ("/api/hausman_hub/v1/device-actions", False),
            ("/api/hausman_hub/v1/device-actions/batch", True),
        ):
            for index, error_type in enumerate(exception_types):
                async def execute(*_args: object, **options: object):
                    marker = (
                        options["dispatch_markers"][0]
                        if batch
                        else options["dispatch_marker"]
                    )
                    marker()
                    raise error_type

                if batch:
                    service.async_execute_device_action_batch = execute
                    body = {
                        "contract": {
                            "name": "hausman-hub-device-action-batch-request",
                            "version": 1,
                        },
                        "correlationId": f"base.batch.{index}",
                        "requestId": f"base.batch.request.{index}",
                        "actions": [
                            {"targetId": "base_switch", "actionId": "turn_on"}
                        ],
                    }
                    request_media = "application/vnd.hausmanhub.device-action-batch-request.full+json"
                    response_media = "application/vnd.hausmanhub.device-action-batch-receipt.full+json"
                else:
                    service.async_execute_device_action = execute
                    body = {
                        "contract": {
                            "name": "hausman-hub-device-action-request",
                            "version": 1,
                        },
                        "correlationId": f"base.single.{index}",
                        "requestId": f"base.single.request.{index}",
                        "targetId": "base_switch",
                        "actionId": "turn_on",
                    }
                    request_media = "application/vnd.hausmanhub.device-action-request.full+json"
                    response_media = "application/vnd.hausmanhub.device-action-receipt.full+json"
                request = FakeJsonRequest(
                    "192.168.1.20",
                    tablet,
                    route,
                    body,
                    content_type=request_media,
                    accept=response_media,
                )
                with self.subTest(route=route, error=error_type.__name__):
                    with self.assertRaises(error_type):
                        asyncio.run(views[route].post(request))

    def test_setup_persists_pending_power_source_before_entity_registration(self) -> None:
        from custom_components.hausman_hub import device_power_dependency_storage

        dependent = "light.0xa4c138d69d102803"
        old_source = "switch.0x603d61fffe75c334_1"
        new_source = "switch.0x603d61fffe759363_1"
        stored = {
            "revision": 12,
            "updatedAt": "2026-09-06T06:00:00Z",
            "dependencies": [
                {
                    "dependentEntityId": dependent,
                    "powerSourceEntityId": old_source,
                    "policy": "requires_on",
                },
                {
                    "dependentEntityId": "light.owner_selected",
                    "powerSourceEntityId": "switch.owner_selected",
                    "policy": "auto_turn_on",
                    "warmupSeconds": 4,
                },
            ],
        }

        class PowerStore:
            def __init__(self) -> None:
                self.value = copy.deepcopy(stored)
                self.saved: list[dict[str, object]] = []

            async def async_load(self) -> dict[str, object]:
                return copy.deepcopy(self.value)

            async def async_save(self, value: dict[str, object]) -> None:
                self.value = copy.deepcopy(value)
                self.saved.append(copy.deepcopy(value))

        power_store = PowerStore()
        hass = FakeHomeAssistant()
        hass.states.values[dependent] = SimpleNamespace(state="off", attributes={})
        self.assertNotIn(new_source, hass.states.values)
        entry = FakeEntry(
            {
                "mode": "read-only",
                "direct_execution_status": "direct_execution_blocked",
            },
            {},
            entry_id="power-source-migration-entry",
        )
        hass.config_entries.entries = [entry]

        with patch.object(
            device_power_dependency_storage,
            "HomeAssistantDevicePowerDependencyStore",
            lambda _hass, _entry_id: power_store,
        ):
            self.assertTrue(asyncio.run(self.integration.async_setup_entry(hass, entry)))

        document = hass.data["hausman_hub"][
            "device_power_dependency_service"
        ].document
        self.assertEqual(13, document["revision"])
        self.assertEqual(
            [
                {
                    "dependentEntityId": dependent,
                    "powerSourceEntityId": new_source,
                    "policy": "requires_on",
                },
                stored["dependencies"][1],
            ],
            document["dependencies"],
        )
        self.assertEqual(
            [
                {
                    "revision": 13,
                    "updatedAt": document["updatedAt"],
                    "dependencies": document["dependencies"],
                }
            ],
            power_store.saved,
        )

    def test_direct_contextual_danger_stays_true_across_refreshes(self) -> None:
        path = "/api/hausman_hub/v1/device-actions"
        view = next(item for item in self.hass.http.views if item.url == path)
        tablet = reader_user("system-users")

        legacy_stack = self._install_monotonic_contextual_action_stack()
        legacy_stack.outcomes.extend([True])
        legacy = asyncio.run(
            view.post(
                FakeJsonRequest(
                    "192.168.1.20",
                    tablet,
                    path,
                    {"targetId": "contextual_switch", "actionId": "turn_on"},
                )
            )
        )
        self.assertEqual(403, legacy.status)
        self.assertEqual([(2, True)], legacy_stack.classifications)
        self.assertEqual(0, legacy_stack.direct_markers)
        self.assertEqual([], legacy_stack.service_calls)

        unconfirmed_stack = self._install_monotonic_contextual_action_stack()
        unconfirmed_stack.outcomes.extend([True])
        unconfirmed_payload = {
            "contract": {
                "name": "hausman-hub-device-action-request",
                "version": 1,
            },
            "correlationId": "contextual.direct.unconfirmed.1",
            "requestId": "contextual.direct.unconfirmed.request.1",
            "targetId": "contextual_switch",
            "actionId": "turn_on",
            "idempotencyKey": "contextual.direct.unconfirmed.key.1",
        }
        unconfirmed = asyncio.run(
            view.post(
                FakeJsonRequest(
                    "192.168.1.20",
                    tablet,
                    path,
                    unconfirmed_payload,
                    content_type="application/vnd.hausmanhub.device-action-request.full+json",
                    accept="application/vnd.hausmanhub.device-action-receipt.full+json",
                )
            )
        )
        self.assertEqual(409, unconfirmed.status)
        self.assertEqual([(2, True)], unconfirmed_stack.classifications)
        self.assertEqual(0, unconfirmed_stack.direct_markers)
        self.assertEqual([], unconfirmed_stack.service_calls)

        stack = self._install_monotonic_contextual_action_stack()
        stack.outcomes.extend([True, False, False])
        payload = {
            **unconfirmed_payload,
            "correlationId": "contextual.direct.confirmed.1",
            "requestId": "contextual.direct.confirmed.request.1",
            "idempotencyKey": "contextual.direct.confirmed.key.1",
            "confirmedByUser": True,
        }
        response = asyncio.run(
            view.post(
                FakeJsonRequest(
                    "192.168.1.20",
                    tablet,
                    path,
                    payload,
                    content_type="application/vnd.hausmanhub.device-action-request.full+json",
                    accept="application/vnd.hausmanhub.device-action-receipt.full+json",
                )
            )
        )

        self.assertEqual(200, response.status)
        self.assertTrue(response.payload["confirmed"])
        self.assertEqual(8000, response.payload["confirmationWindowMs"])
        self.assertEqual([(2, True), (3, False), (3, False)], stack.classifications)
        self.assertEqual([], stack.outcomes)
        self.assertEqual(1, stack.direct_markers)
        self.assertEqual(
            [
                (
                    "switch",
                    "turn_on",
                    {"entity_id": "switch.contextual_action"},
                    True,
                    {},
                )
            ],
            stack.service_calls,
        )
        record = self.hass.data["hausman_hub"]["device_action_idempotency"]._records[
            "contextual.direct.confirmed.key.1"
        ]
        self.assertEqual("completed", record["state"])
        self.assertEqual(8000, record["receipt"]["confirmationWindowMs"])

    def test_direct_contextual_reclassification_error_fails_before_dispatch(self) -> None:
        path = "/api/hausman_hub/v1/device-actions"
        view = next(item for item in self.hass.http.views if item.url == path)
        stack = self._install_monotonic_contextual_action_stack()
        stack.outcomes.extend([True, RuntimeError("classification unavailable")])
        key = "contextual.direct.failure.key.1"
        response = asyncio.run(
            view.post(
                FakeJsonRequest(
                    "192.168.1.20",
                    reader_user("system-users"),
                    path,
                    {
                        "contract": {
                            "name": "hausman-hub-device-action-request",
                            "version": 1,
                        },
                        "correlationId": "contextual.direct.failure.1",
                        "requestId": "contextual.direct.failure.request.1",
                        "targetId": "contextual_switch",
                        "actionId": "turn_on",
                        "confirmedByUser": True,
                        "idempotencyKey": key,
                    },
                    content_type="application/vnd.hausmanhub.device-action-request.full+json",
                    accept="application/vnd.hausmanhub.device-action-receipt.full+json",
                )
            )
        )

        self.assertEqual(503, response.status)
        self.assertEqual(
            [(2, True), (3, "RuntimeError")],
            stack.classifications,
        )
        self.assertEqual([], stack.outcomes)
        self.assertEqual(0, stack.direct_markers)
        self.assertEqual([], stack.service_calls)
        self.assertNotIn(
            key,
            self.hass.data["hausman_hub"]["device_action_idempotency"]._records,
        )

    def test_batch_contextual_danger_stays_true_across_refreshes(self) -> None:
        path = "/api/hausman_hub/v1/device-actions/batch"
        view = next(item for item in self.hass.http.views if item.url == path)

        stack = self._install_monotonic_contextual_action_stack()
        stack.outcomes.extend([True, False, False])
        key = "contextual.batch.confirmed.key.1"
        response = asyncio.run(
            view.post(
                FakeJsonRequest(
                    "192.168.1.20",
                    reader_user("system-users"),
                    path,
                    {
                        "contract": {
                            "name": "hausman-hub-device-action-batch-request",
                            "version": 1,
                        },
                        "correlationId": "contextual.batch.confirmed.1",
                        "requestId": "contextual.batch.confirmed.request.1",
                        "actions": [
                            {
                                "targetId": "contextual_switch",
                                "actionId": "turn_on",
                                "confirmedByUser": True,
                                "idempotencyKey": key,
                            }
                        ],
                    },
                    content_type="application/vnd.hausmanhub.device-action-batch-request.full+json",
                    accept="application/vnd.hausmanhub.device-action-batch-receipt.full+json",
                )
            )
        )

        self.assertEqual(200, response.status)
        self.assertEqual("confirmed", response.payload["status"])
        self.assertEqual(8000, response.payload["receipts"][0]["confirmationWindowMs"])
        self.assertEqual([(2, True), (3, False), (3, False)], stack.classifications)
        self.assertEqual([], stack.outcomes)
        self.assertEqual(1, stack.batch_markers)
        self.assertEqual(
            [
                (
                    "switch",
                    "turn_on",
                    {"entity_id": "switch.contextual_action"},
                    True,
                    {},
                )
            ],
            stack.service_calls,
        )
        record = self.hass.data["hausman_hub"]["device_action_idempotency"]._records[key]
        self.assertEqual("completed", record["state"])
        self.assertEqual(
            8000,
            record["receipt"]["receipts"][0]["confirmationWindowMs"],
        )
        self.assertEqual(8000, record["itemJournal"][0]["confirmationWindowMs"])

    def test_batch_contextual_reclassification_error_fails_before_dispatch(self) -> None:
        path = "/api/hausman_hub/v1/device-actions/batch"
        view = next(item for item in self.hass.http.views if item.url == path)
        stack = self._install_monotonic_contextual_action_stack()
        stack.outcomes.extend([True, RuntimeError("classification unavailable")])
        key = "contextual.batch.failure.key.1"
        response = asyncio.run(
            view.post(
                FakeJsonRequest(
                    "192.168.1.20",
                    reader_user("system-users"),
                    path,
                    {
                        "contract": {
                            "name": "hausman-hub-device-action-batch-request",
                            "version": 1,
                        },
                        "correlationId": "contextual.batch.failure.1",
                        "requestId": "contextual.batch.failure.request.1",
                        "actions": [
                            {
                                "targetId": "contextual_switch",
                                "actionId": "turn_on",
                                "confirmedByUser": True,
                                "idempotencyKey": key,
                            }
                        ],
                    },
                    content_type="application/vnd.hausmanhub.device-action-batch-request.full+json",
                    accept="application/vnd.hausmanhub.device-action-batch-receipt.full+json",
                )
            )
        )

        self.assertEqual(503, response.status)
        self.assertEqual(
            [(2, True), (3, "RuntimeError")],
            stack.classifications,
        )
        self.assertEqual([], stack.outcomes)
        self.assertEqual(0, stack.batch_markers)
        self.assertEqual([], stack.service_calls)
        self.assertNotIn(
            key,
            self.hass.data["hausman_hub"]["device_action_idempotency"]._records,
        )

    def test_safe_climate_http_returns_before_slow_real_service_finishes(self) -> None:
        path = "/api/hausman_hub/v1/device-actions"
        view = next(item for item in self.hass.http.views if item.url == path)

        async def exercise() -> tuple[FakeResponse, float, SimpleNamespace]:
            service_entered = asyncio.Event()
            release_service = asyncio.Event()
            stack = self._install_slow_safe_climate_action_stack(
                service_entered=service_entered,
                release_service=release_service,
            )
            request = FakeJsonRequest(
                "192.168.1.20",
                reader_user("system-users"),
                path,
                {
                    "contract": {
                        "name": "hausman-hub-device-action-request",
                        "version": 1,
                    },
                    "correlationId": "safe.climate.slow.1",
                    "requestId": "safe.climate.slow.request.1",
                    "targetId": "slow_smartir",
                    "actionId": "set_temperature",
                    "value": 23,
                },
                content_type="application/vnd.hausmanhub.device-action-request.full+json",
                accept="application/vnd.hausmanhub.device-action-receipt.full+json",
            )
            started = time.monotonic()
            try:
                response = await asyncio.wait_for(view.post(request), timeout=2.5)
                elapsed = time.monotonic() - started
                self.assertTrue(service_entered.is_set(), response.payload)
            finally:
                release_service.set()
            coordinator = self.hass.data["hausman_hub"].get(
                "safe_device_command_lifecycle"
            )
            if coordinator is not None:
                await coordinator.async_close()
            return response, elapsed, stack

        response, elapsed, stack = asyncio.run(exercise())

        self.assertEqual(200, response.status)
        self.assertLess(elapsed, 2.5)
        self.assertTrue(response.payload["accepted"])
        self.assertFalse(response.payload["confirmed"])
        self.assertEqual("accepted", response.payload["status"])
        self.assertEqual("executed", response.payload["decision"])
        self.assertTrue(response.payload["commandSent"])
        self.assertTrue(response.payload["readBack"]["attempted"])
        self.assertFalse(response.payload["readBack"]["matched"])
        self.assertNotIn("observedValue", response.payload["readBack"])
        self.assertEqual(1, len(stack.service_calls))

    def test_safe_deadline_does_not_capture_an_ordinary_switch_action(self) -> None:
        """Only exact climate descriptors may opt into the shortened HTTP path."""

        api = importlib.import_module(
            "custom_components.hausman_hub.device_action_api"
        )
        path = "/api/hausman_hub/v1/device-actions"
        view = next(item for item in self.hass.http.views if item.url == path)
        service = self.hass.data["hausman_hub"]["scenario_service"]
        entity_id = "switch.synthetic_ordinary_deadline"
        self.hass.states.values[entity_id] = SimpleNamespace(
            state="off",
            attributes={},
            last_updated=datetime.now(timezone.utc),
        )
        action = SimpleNamespace(domain="switch", service="turn_on")
        device = SimpleNamespace(
            entity_id=entity_id,
            action=lambda action_id: action if action_id == "turn_on" else None,
        )
        service.current_catalog = lambda: SimpleNamespace(
            device=lambda target_id: device if target_id == "ordinary_switch" else None
        )
        calls = 0

        async def resolve_context(_target_id: str, _action_id: str):
            await asyncio.sleep(0.03)
            return entity_id, "switch", ("turn_on",), "turn_on"

        async def no_intercom(_target_id: str, _action_id: str) -> bool:
            return False

        async def execute(
            target_id: str,
            action_id: str,
            _value: object,
            *,
            correlation_id: str,
            **options: object,
        ) -> dict[str, object]:
            nonlocal calls
            calls += 1
            return {
                "correlationId": correlation_id,
                "requestId": str(options.get("request_id") or "ordinary.request.1"),
                "targetId": target_id,
                "actionId": action_id,
                "accepted": True,
                "confirmed": False,
                "status": "accepted",
            }

        service.async_resolve_device_action_context = resolve_context
        service.async_is_intercom_action = no_intercom
        service.is_contextually_dangerous_action = lambda *_args: False
        service.is_external_cover_action = lambda *_args: False
        service.async_execute_device_action = execute

        def short_deadline():
            started = time.monotonic()
            return api.CommandDeadline(started, started + 0.01)

        with patch.object(api.CommandDeadline, "start", side_effect=short_deadline):
            response = asyncio.run(
                view.post(
                    FakeJsonRequest(
                        "192.168.1.20",
                        reader_user("system-users"),
                        path,
                        {"targetId": "ordinary_switch", "actionId": "turn_on"},
                    )
                )
            )

        self.assertEqual(200, response.status)
        self.assertEqual(1, calls)

    def test_safe_climate_batch_blocks_unstarted_item_before_slow_call_finishes(self) -> None:
        path = "/api/hausman_hub/v1/device-actions/batch"
        view = next(item for item in self.hass.http.views if item.url == path)

        async def exercise() -> tuple[FakeResponse, FakeResponse, float, SimpleNamespace]:
            service_entered = asyncio.Event()
            release_service = asyncio.Event()
            stack = self._install_slow_safe_climate_action_stack(
                service_entered=service_entered,
                release_service=release_service,
            )
            payload = {
                    "contract": {
                        "name": "hausman-hub-device-action-batch-request",
                        "version": 1,
                    },
                    "correlationId": "safe.climate.batch.slow.1",
                    "requestId": "safe.climate.batch.slow.request.1",
                    "actions": [
                        {
                            "targetId": "slow_smartir",
                            "actionId": "set_temperature",
                            "value": 23,
                        },
                        {
                            "targetId": "slow_humidifier",
                            "actionId": "set_humidity",
                            "value": 45,
                        },
                    ],
                }

            def request() -> FakeJsonRequest:
                return FakeJsonRequest(
                    "192.168.1.20",
                    reader_user("system-users"),
                    path,
                    copy.deepcopy(payload),
                    content_type="application/vnd.hausmanhub.device-action-batch-request.full+json",
                    accept="application/vnd.hausmanhub.device-action-batch-receipt.full+json",
                )
            started = time.monotonic()
            try:
                response = await asyncio.wait_for(view.post(request()), timeout=2.5)
                elapsed = time.monotonic() - started
                self.assertTrue(service_entered.is_set(), response.payload)
                replay = await asyncio.wait_for(view.post(request()), timeout=0.5)
            finally:
                release_service.set()
            coordinator = self.hass.data["hausman_hub"].get(
                "safe_device_command_lifecycle"
            )
            if coordinator is not None:
                await coordinator.async_close()
            return response, replay, elapsed, stack

        response, replay, elapsed, stack = asyncio.run(exercise())

        self.assertEqual(200, response.status)
        self.assertLess(elapsed, 2.5)
        self.assertEqual(response.payload, replay.payload)
        self.assertEqual("partial", response.payload["status"])
        first, second = response.payload["receipts"]
        self.assertTrue(first["accepted"])
        self.assertFalse(first["confirmed"])
        self.assertTrue(first["commandSent"])
        self.assertEqual("executed", first["decision"])
        self.assertFalse(second["accepted"])
        self.assertFalse(second["confirmed"])
        self.assertFalse(second["commandSent"])
        self.assertEqual("blocked", second["decision"])
        self.assertTrue(second["decisionTerminal"])
        self.assertEqual(1, len(stack.service_calls))

    def test_safe_trv_and_humidifier_slow_calls_use_one_bounded_path(self) -> None:
        path = "/api/hausman_hub/v1/device-actions"
        view = next(item for item in self.hass.http.views if item.url == path)

        async def exercise() -> list[tuple[str, FakeResponse, float, int]]:
            outcomes: list[tuple[str, FakeResponse, float, int]] = []
            for target_id, action_id, value in (
                ("slow_trv", "set_temperature", 21.5),
                ("slow_humidifier", "set_humidity", 45),
            ):
                service_entered = asyncio.Event()
                release_service = asyncio.Event()
                stack = self._install_slow_safe_climate_action_stack(
                    service_entered=service_entered,
                    release_service=release_service,
                )
                request_id = f"safe.slow.{target_id}.1"
                request = FakeJsonRequest(
                    "192.168.1.20",
                    reader_user("system-users"),
                    path,
                    {
                        "contract": {
                            "name": "hausman-hub-device-action-request",
                            "version": 1,
                        },
                        "correlationId": f"{request_id}.correlation",
                        "requestId": request_id,
                        "targetId": target_id,
                        "actionId": action_id,
                        "value": value,
                    },
                    content_type="application/vnd.hausmanhub.device-action-request.full+json",
                    accept="application/vnd.hausmanhub.device-action-receipt.full+json",
                )
                started = time.monotonic()
                try:
                    response = await asyncio.wait_for(view.post(request), timeout=2.5)
                    elapsed = time.monotonic() - started
                    self.assertTrue(service_entered.is_set())
                finally:
                    release_service.set()
                outcomes.append(
                    (target_id, response, elapsed, len(stack.service_calls))
                )
            coordinator = self.hass.data["hausman_hub"].get(
                "safe_device_command_lifecycle"
            )
            if coordinator is not None:
                await coordinator.async_close()
            return outcomes

        outcomes = asyncio.run(exercise())

        for target_id, response, elapsed, calls in outcomes:
            self.assertEqual(200, response.status, target_id)
            self.assertLess(elapsed, 2.5, target_id)
            self.assertTrue(response.payload["accepted"], target_id)
            self.assertFalse(response.payload["confirmed"], target_id)
            self.assertTrue(response.payload["commandSent"], target_id)
            self.assertTrue(response.payload["readBack"]["attempted"], target_id)
            self.assertEqual(1, calls, target_id)

    def test_safe_turn_off_returns_before_mode_writer_and_cas_preserves_manual_choice(self) -> None:
        path = "/api/hausman_hub/v1/device-actions"
        view = next(item for item in self.hass.http.views if item.url == path)

        async def exercise() -> tuple[FakeResponse, float, list[dict[str, object]], str]:
            service_entered = asyncio.Event()
            release_service = asyncio.Event()
            release_service.set()
            stack = self._install_slow_safe_climate_action_stack(
                service_entered=service_entered,
                release_service=release_service,
            )
            writer_entered = asyncio.Event()
            release_writer = asyncio.Event()
            writer_done = asyncio.Event()
            mode_state: dict[str, object] = {"revision": 1, "mode": "manual"}
            writes: list[dict[str, object]] = []

            def snapshot(_entity_id: str) -> dict[str, object]:
                return dict(mode_state)

            async def write_mode(
                _entity_id: str,
                mode: str,
                *,
                expected_revision: object | None = None,
                expected_mode: object | None = None,
            ) -> dict[str, object]:
                writer_entered.set()
                await release_writer.wait()
                writes.append(
                    {
                        "mode": mode,
                        "expected_revision": expected_revision,
                        "expected_mode": expected_mode,
                    }
                )
                if (
                    expected_revision != mode_state["revision"]
                    or expected_mode != mode_state["mode"]
                ):
                    writer_done.set()
                    return {
                        "mode": mode_state["mode"],
                        "changed": False,
                        "skipped": True,
                        "reason": "manual_mode_changed",
                    }
                mode_state["mode"] = mode
                writer_done.set()
                return {"mode": mode, "changed": True}

            self.hass.data["hausman_hub"]["climate_runtime"] = SimpleNamespace(
                device_mode_snapshot_for_entity=snapshot,
                async_set_device_mode_for_entity=write_mode,
            )
            request = FakeJsonRequest(
                "192.168.1.20",
                reader_user("system-users"),
                path,
                {
                    "contract": {
                        "name": "hausman-hub-device-action-request",
                        "version": 1,
                    },
                    "correlationId": "safe.turn-off.mode-writer.1",
                    "requestId": "safe.turn-off.mode-writer.request.1",
                    "targetId": "slow_smartir",
                    "actionId": "turn_off",
                },
                content_type="application/vnd.hausmanhub.device-action-request.full+json",
                accept="application/vnd.hausmanhub.device-action-receipt.full+json",
            )
            started = time.monotonic()
            response = await asyncio.wait_for(view.post(request), timeout=2.5)
            elapsed = time.monotonic() - started
            self.assertTrue(service_entered.is_set())
            self.assertTrue(writer_entered.is_set())
            mode_state["revision"] = 2
            release_writer.set()
            await asyncio.wait_for(writer_done.wait(), timeout=1)
            await asyncio.sleep(0)
            coordinator = self.hass.data["hausman_hub"].get(
                "safe_device_command_lifecycle"
            )
            if coordinator is not None:
                await coordinator.async_close()
            self.assertEqual(1, len(stack.service_calls))
            return response, elapsed, writes, str(mode_state["mode"])

        response, elapsed, writes, final_mode = asyncio.run(exercise())

        self.assertEqual(200, response.status)
        self.assertLess(elapsed, 2.5)
        self.assertTrue(response.payload["accepted"])
        self.assertFalse(response.payload["confirmed"])
        self.assertTrue(response.payload["commandSent"])
        self.assertEqual(
            [{"mode": "manual", "expected_revision": 1, "expected_mode": "manual"}],
            writes,
        )
        self.assertEqual("manual", final_mode)

    def test_safe_slow_replay_and_supported_legacy_negotiations_do_not_redispatch(self) -> None:
        path = "/api/hausman_hub/v1/device-actions"
        view = next(item for item in self.hass.http.views if item.url == path)

        async def exercise() -> tuple[FakeResponse, FakeResponse, FakeResponse, FakeResponse, list[int]]:
            call_counts: list[int] = []

            first_entered = asyncio.Event()
            first_release = asyncio.Event()
            first_stack = self._install_slow_safe_climate_action_stack(
                service_entered=first_entered,
                release_service=first_release,
            )
            full_payload = {
                "contract": {
                    "name": "hausman-hub-device-action-request",
                    "version": 1,
                },
                "correlationId": "safe.replay.1",
                "requestId": "safe.replay.request.1",
                "targetId": "slow_smartir",
                "actionId": "set_temperature",
                "value": 23,
            }
            full_request = lambda accept: FakeJsonRequest(
                "192.168.1.20",
                reader_user("system-users"),
                path,
                copy.deepcopy(full_payload),
                content_type="application/vnd.hausmanhub.device-action-request.full+json",
                accept=accept,
            )
            first = await asyncio.wait_for(
                view.post(
                    full_request(
                        "application/vnd.hausmanhub.device-action-receipt.full+json"
                    )
                ),
                timeout=2.5,
            )
            replay = await asyncio.wait_for(
                view.post(
                    full_request(
                        "application/vnd.hausmanhub.device-action-receipt.full+json"
                    )
                ),
                timeout=0.5,
            )
            call_counts.append(len(first_stack.service_calls))
            first_release.set()

            legacy_response_entered = asyncio.Event()
            legacy_response_release = asyncio.Event()
            legacy_response_stack = self._install_slow_safe_climate_action_stack(
                service_entered=legacy_response_entered,
                release_service=legacy_response_release,
            )
            full_legacy_payload = {
                **full_payload,
                "correlationId": "safe.full-legacy.1",
                "requestId": "safe.full-legacy.request.1",
                "targetId": "slow_trv",
                "value": 21.5,
            }
            full_legacy = await asyncio.wait_for(
                view.post(
                    FakeJsonRequest(
                        "192.168.1.20",
                        reader_user("system-users"),
                        path,
                        full_legacy_payload,
                        content_type="application/vnd.hausmanhub.device-action-request.full+json",
                        accept="application/json",
                    )
                ),
                timeout=2.5,
            )
            call_counts.append(len(legacy_response_stack.service_calls))
            legacy_response_release.set()

            legacy_entered = asyncio.Event()
            legacy_release = asyncio.Event()
            legacy_stack = self._install_slow_safe_climate_action_stack(
                service_entered=legacy_entered,
                release_service=legacy_release,
            )
            legacy = await asyncio.wait_for(
                view.post(
                    FakeJsonRequest(
                        "192.168.1.20",
                        reader_user("system-users"),
                        path,
                        {
                            "targetId": "slow_humidifier",
                            "actionId": "set_humidity",
                            "value": 45,
                        },
                    )
                ),
                timeout=2.5,
            )
            call_counts.append(len(legacy_stack.service_calls))
            legacy_release.set()
            await asyncio.sleep(0)
            coordinator = self.hass.data["hausman_hub"].get(
                "safe_device_command_lifecycle"
            )
            if coordinator is not None:
                await coordinator.async_close()
            return first, replay, full_legacy, legacy, call_counts

        first, replay, full_legacy, legacy, call_counts = asyncio.run(exercise())

        self.assertEqual(200, first.status)
        self.assertEqual(first.payload, replay.payload)
        self.assertEqual(
            "application/vnd.hausmanhub.device-action-receipt.full+json",
            replay.headers["Content-Type"],
        )
        self.assertEqual(200, full_legacy.status)
        self.assertEqual("application/json", full_legacy.headers["Content-Type"])
        self.assertEqual(200, legacy.status)
        self.assertEqual("application/json", legacy.headers["Content-Type"])
        self.assertEqual([1, 1, 1], call_counts)

    def test_safe_late_readback_distinguishes_smartir_echo_trv_and_failure(self) -> None:
        path = "/api/hausman_hub/v1/device-actions"
        view = next(item for item in self.hass.http.views if item.url == path)

        async def exercise() -> dict[str, dict[str, object]]:
            late: dict[str, dict[str, object]] = {}
            for target_id, value, fail_after_call in (
                ("slow_smartir", 23, False),
                ("slow_trv", 21.5, False),
                ("slow_humidifier", 45, True),
            ):
                service_entered = asyncio.Event()
                release_service = asyncio.Event()
                stack = self._install_slow_safe_climate_action_stack(
                    service_entered=service_entered,
                    release_service=release_service,
                )
                action_id = (
                    "set_humidity"
                    if target_id == "slow_humidifier"
                    else "set_temperature"
                )
                if fail_after_call:
                    original_call = self.hass.services.async_call

                    async def failing_call(*args, **kwargs):
                        await original_call(*args, **kwargs)
                        raise RuntimeError("synthetic post-dispatch failure")

                    self.hass.services.async_call = failing_call
                request_id = f"safe.late.{target_id}.1"
                response = await asyncio.wait_for(
                    view.post(
                        FakeJsonRequest(
                            "192.168.1.20",
                            reader_user("system-users"),
                            path,
                            {
                                "contract": {
                                    "name": "hausman-hub-device-action-request",
                                    "version": 1,
                                },
                                "correlationId": f"{request_id}.correlation",
                                "requestId": request_id,
                                "targetId": target_id,
                                "actionId": action_id,
                                "value": value,
                            },
                            content_type="application/vnd.hausmanhub.device-action-request.full+json",
                            accept="application/vnd.hausmanhub.device-action-receipt.full+json",
                        )
                    ),
                    timeout=2.5,
                )
                self.assertTrue(response.payload["accepted"])
                self.assertFalse(response.payload["confirmed"])
                release_service.set()
                coordinator = self.hass.data["hausman_hub"][
                    "safe_device_command_lifecycle"
                ]
                record = None
                for _attempt in range(200):
                    record = next(
                        (
                            item
                            for item in coordinator._payload["operations"]
                            if item["targetId"] == target_id
                        ),
                        None,
                    )
                    if record is not None and record["lateReceipt"] is not None:
                        break
                    await asyncio.sleep(0.01)
                self.assertIsNotNone(record)
                self.assertIsNotNone(record["lateReceipt"])
                self.assertEqual(1, len(stack.service_calls))
                late[target_id] = copy.deepcopy(record["lateReceipt"])
            await self.hass.data["hausman_hub"][
                "safe_device_command_lifecycle"
            ].async_close()
            return late

        late = asyncio.run(exercise())

        smartir = late["slow_smartir"]
        self.assertFalse(smartir["confirmed"])
        self.assertNotIn("observedValue", smartir["readBack"])
        trv = late["slow_trv"]
        self.assertTrue(trv["confirmed"])
        self.assertEqual(21.5, trv["readBack"]["observedValue"])
        failure = late["slow_humidifier"]
        self.assertTrue(failure["accepted"])
        self.assertFalse(failure["confirmed"])
        self.assertEqual("dispatch_result_unknown", failure["reason"])

    def test_safe_trv_preexisting_match_without_new_evidence_is_not_confirmed(self) -> None:
        path = "/api/hausman_hub/v1/device-actions"
        view = next(item for item in self.hass.http.views if item.url == path)

        async def exercise() -> tuple[FakeResponse, int]:
            service_entered = asyncio.Event()
            release_service = asyncio.Event()
            stack = self._install_slow_safe_climate_action_stack(
                service_entered=service_entered,
                release_service=release_service,
            )
            stack.executor._readback_window_seconds = 0.05

            async def no_new_state(
                domain: str,
                action: str,
                service_data: dict[str, object],
                *,
                blocking: bool,
                **options: object,
            ) -> None:
                stack.service_calls.append(
                    (domain, action, dict(service_data), blocking, dict(options))
                )
                service_entered.set()

            self.hass.services.async_call = no_new_state
            response = await view.post(
                FakeJsonRequest(
                    "192.168.1.20",
                    reader_user("system-users"),
                    path,
                    {
                        "contract": {
                            "name": "hausman-hub-device-action-request",
                            "version": 1,
                        },
                        "correlationId": "safe.trv.prematch.1",
                        "requestId": "safe.trv.prematch.request.1",
                        "targetId": "slow_trv",
                        "actionId": "set_temperature",
                        "value": 20,
                    },
                    content_type="application/vnd.hausmanhub.device-action-request.full+json",
                    accept="application/vnd.hausmanhub.device-action-receipt.full+json",
                )
            )
            await self.hass.data["hausman_hub"][
                "safe_device_command_lifecycle"
            ].async_close()
            return response, len(stack.service_calls)

        response, calls = asyncio.run(exercise())

        self.assertEqual(200, response.status)
        self.assertTrue(response.payload["accepted"])
        self.assertFalse(response.payload["confirmed"])
        self.assertFalse(response.payload["commandSent"])
        self.assertEqual("skipped", response.payload["decision"])
        self.assertNotIn("readBack", response.payload)
        self.assertEqual(0, calls)

    def test_safe_climate_slow_idempotency_phases_are_bounded_and_never_dispatch(self) -> None:
        path = "/api/hausman_hub/v1/device-actions"
        view = next(item for item in self.hass.http.views if item.url == path)

        async def exercise() -> list[tuple[str, int, float, int]]:
            results: list[tuple[str, int, float, int]] = []
            idempotency = self.hass.data["hausman_hub"][
                "device_action_idempotency"
            ]
            coordinator = self.hass.data["hausman_hub"].get(
                "safe_device_command_lifecycle"
            )
            for phase, method_name in (
                ("reserve", "async_reserve"),
                ("pending", "async_mark_pending"),
                ("dispatching", "async_mark_dispatching"),
            ):
                service_entered = asyncio.Event()
                release_service = asyncio.Event()
                stack = self._install_slow_safe_climate_action_stack(
                    service_entered=service_entered,
                    release_service=release_service,
                )
                release_storage = asyncio.Event()
                original = getattr(idempotency, method_name)

                async def delayed(*args, _original=original, **kwargs):
                    try:
                        await release_storage.wait()
                    except asyncio.CancelledError:
                        await release_storage.wait()
                    return await _original(*args, **kwargs)

                setattr(idempotency, method_name, delayed)
                request_id = f"safe.storage.{phase}.1"
                request = FakeJsonRequest(
                    "192.168.1.20",
                    reader_user("system-users"),
                    path,
                    {
                        "contract": {
                            "name": "hausman-hub-device-action-request",
                            "version": 1,
                        },
                        "correlationId": f"{request_id}.correlation",
                        "requestId": request_id,
                        "targetId": "slow_smartir",
                        "actionId": "set_temperature",
                        "value": 23,
                    },
                    content_type="application/vnd.hausmanhub.device-action-request.full+json",
                    accept="application/vnd.hausmanhub.device-action-receipt.full+json",
                )
                started = time.monotonic()
                response = await asyncio.wait_for(view.post(request), timeout=2.5)
                elapsed = time.monotonic() - started
                setattr(idempotency, method_name, original)
                release_storage.set()
                for _attempt in range(100):
                    if f"request:{request_id}" not in idempotency._records:
                        break
                    await asyncio.sleep(0.01)
                results.append(
                    (phase, response.status, elapsed, len(stack.service_calls))
                )
                release_service.set()
            if coordinator is not None:
                await coordinator.async_close()
            return results

        results = asyncio.run(exercise())

        self.assertEqual(
            ["reserve", "pending", "dispatching"],
            [item[0] for item in results],
        )
        for _phase, status, elapsed, service_calls in results:
            self.assertEqual(503, status)
            self.assertLess(elapsed, 2.5)
            self.assertEqual(0, service_calls)

    def test_safe_climate_invalid_or_unavailable_values_never_dispatch(self) -> None:
        path = "/api/hausman_hub/v1/device-actions"
        view = next(item for item in self.hass.http.views if item.url == path)

        async def exercise() -> list[tuple[str, FakeResponse, int]]:
            outcomes: list[tuple[str, FakeResponse, int]] = []
            cases = (
                ("bool", True, False),
                ("nan", float("nan"), False),
                ("inf", float("inf"), False),
                ("range", 31, False),
                ("step", 22.5, False),
                ("unavailable", 23, True),
            )
            for name, value, unavailable in cases:
                service_entered = asyncio.Event()
                release_service = asyncio.Event()
                stack = self._install_slow_safe_climate_action_stack(
                    service_entered=service_entered,
                    release_service=release_service,
                )
                if unavailable:
                    state = self.hass.states.values[
                        "climate.synthetic_slow_smartir"
                    ]
                    self.hass.states.values[
                        "climate.synthetic_slow_smartir"
                    ] = SimpleNamespace(
                        state="unavailable",
                        attributes=state.attributes,
                        last_updated=datetime.now(timezone.utc),
                    )
                request_id = f"safe.invalid.{name}.1"
                response = await view.post(
                    FakeJsonRequest(
                        "192.168.1.20",
                        reader_user("system-users"),
                        path,
                        {
                            "contract": {
                                "name": "hausman-hub-device-action-request",
                                "version": 1,
                            },
                            "correlationId": f"{request_id}.correlation",
                            "requestId": request_id,
                            "targetId": "slow_smartir",
                            "actionId": "set_temperature",
                            "value": value,
                        },
                        content_type="application/vnd.hausmanhub.device-action-request.full+json",
                        accept="application/vnd.hausmanhub.device-action-receipt.full+json",
                    )
                )
                outcomes.append((name, response, len(stack.service_calls)))
                release_service.set()
            coordinator = self.hass.data["hausman_hub"].get(
                "safe_device_command_lifecycle"
            )
            if coordinator is not None:
                await coordinator.async_close()
            return outcomes

        outcomes = asyncio.run(exercise())

        for name, response, service_calls in outcomes:
            self.assertIn(response.status, {200, 400, 409}, name)
            if response.status == 200:
                self.assertFalse(response.payload["accepted"], name)
                self.assertFalse(response.payload["commandSent"], name)
            self.assertEqual(0, service_calls, name)

    def test_safe_batch_invalid_item_blocks_every_item_before_dispatch(self) -> None:
        path = "/api/hausman_hub/v1/device-actions/batch"
        view = next(item for item in self.hass.http.views if item.url == path)

        async def exercise() -> tuple[FakeResponse, int]:
            service_entered = asyncio.Event()
            release_service = asyncio.Event()
            stack = self._install_slow_safe_climate_action_stack(
                service_entered=service_entered,
                release_service=release_service,
            )
            response = await view.post(
                FakeJsonRequest(
                    "192.168.1.20",
                    reader_user("system-users"),
                    path,
                    {
                        "contract": {
                            "name": "hausman-hub-device-action-batch-request",
                            "version": 1,
                        },
                        "correlationId": "safe.batch.invalid.1",
                        "requestId": "safe.batch.invalid.request.1",
                        "actions": [
                            {
                                "targetId": "slow_smartir",
                                "actionId": "set_temperature",
                                "value": 23,
                            },
                            {
                                "targetId": "slow_humidifier",
                                "actionId": "set_humidity",
                                "value": 45.5,
                            },
                        ],
                    },
                    content_type="application/vnd.hausmanhub.device-action-batch-request.full+json",
                    accept="application/vnd.hausmanhub.device-action-batch-receipt.full+json",
                )
            )
            await self.hass.data["hausman_hub"][
                "safe_device_command_lifecycle"
            ].async_close()
            return response, len(stack.service_calls)

        response, calls = asyncio.run(exercise())

        self.assertEqual(200, response.status)
        self.assertEqual("failed", response.payload["status"])
        self.assertEqual(0, response.payload["acceptedCount"])
        self.assertEqual(2, response.payload["failedCount"])
        self.assertTrue(
            all(not item["commandSent"] for item in response.payload["receipts"])
        )
        self.assertEqual(0, calls)

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

    def test_energy_breaker_requires_full_confirmation_and_replays_durably(self) -> None:
        from custom_components.hausman_hub.application.scenarios import (
            ScenarioCatalog,
            ScenarioDeviceAction,
            ScenarioDeviceEntry,
        )

        path = "/api/hausman_hub/v1/device-actions"
        view = next(item for item in self.hass.http.views if item.url == path)
        service = self.hass.data["hausman_hub"]["scenario_service"]
        tablet = reader_user("system-users")
        physical_id = "device_5555555555555555"
        action = ScenarioDeviceAction(
            action_id="turn_on",
            title="Включить",
            domain="switch",
            service="turn_on",
            allowed_fields=frozenset(),
        )
        service._catalog = ScenarioCatalog(
            devices={
                "main_breaker": ScenarioDeviceEntry(
                    target_id="main_breaker",
                    name="Вводной автомат · Реле",
                    entity_id="switch.main_breaker",
                    actions=(action,),
                    physical_id=physical_id,
                    physical_name="Вводной автомат",
                    device_type="switch",
                )
            },
            scenarios={},
        )
        service._electrical_breaker_device_ids_resolver = lambda: (physical_id,)
        self.hass.states.values["switch.main_breaker"] = SimpleNamespace(
            state="on",
            attributes={},
            last_updated=datetime.now(timezone.utc),
        )
        executions = 0

        async def resolve_context(_target_id: str, _action_id: str):
            return "switch.main_breaker", "switch", ("turn_on",), "turn_on"

        async def is_intercom(_target_id: str, _action_id: str) -> bool:
            return False

        async def execute_action(
            target_id: str,
            action_id: str,
            _value: object,
            *,
            correlation_id: str,
            request_id: str,
            dispatch_marker,
            **_options: object,
        ) -> dict[str, object]:
            nonlocal executions
            executions += 1
            dispatch_marker()
            self.hass.states.values["switch.main_breaker"] = SimpleNamespace(
                state="on",
                attributes={},
                last_updated=datetime.now(timezone.utc),
            )
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
        service.async_execute_device_action = execute_action

        legacy = asyncio.run(
            view.post(
                FakeJsonRequest(
                    "192.168.1.20",
                    tablet,
                    path,
                    {"targetId": "main_breaker", "actionId": "turn_on"},
                )
            )
        )
        self.assertEqual(403, legacy.status)

        base = {
            "contract": {
                "name": "hausman-hub-device-action-request",
                "version": 1,
            },
            "correlationId": "breaker.on.1",
            "requestId": "breaker.on.request.1",
            "targetId": "main_breaker",
            "actionId": "turn_on",
            "idempotencyKey": "breaker.on.key.1",
        }

        def send(payload: dict[str, object]) -> FakeResponse:
            return asyncio.run(
                view.post(
                    FakeJsonRequest(
                        "192.168.1.20",
                        tablet,
                        path,
                        payload,
                        content_type="application/vnd.hausmanhub.device-action-request.full+json",
                        accept="application/vnd.hausmanhub.device-action-receipt.full+json",
                    )
                )
            )

        unconfirmed = send(copy.deepcopy(base))
        self.assertEqual(409, unconfirmed.status)
        self.assertEqual(0, executions)

        confirmed_payload = {**base, "confirmedByUser": True}
        confirmed = send(confirmed_payload)
        self.assertIn(
            "breaker.on.key.1",
            self.hass.data["hausman_hub"]["device_action_idempotency"]._records,
        )
        self.assertTrue(
            service.is_contextually_dangerous_action(
                "main_breaker", "turn_on"
            )
        )
        replay = send(copy.deepcopy(confirmed_payload))
        self.assertEqual(200, confirmed.status)
        self.assertEqual(confirmed.payload, replay.payload)
        self.assertEqual(1, executions)

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
            initial_contextually_dangerous: frozenset[
                tuple[str, str]
            ] = frozenset(),
        ) -> list[dict[str, object]]:
            self.assertEqual(2, len(dispatch_contexts))
            self.assertEqual(frozenset(), initial_contextually_dangerous)
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

    def test_operation_journal_admin_posts_are_strictly_bounded_and_no_store(self) -> None:
        from custom_components.hausman_hub.operation_journal_api import (
            OperationJournalArchiveView,
            OperationJournalResetView,
        )

        user = reader_user(admin=True)
        archive_body = {
            "contract": {
                "name": "hausman-hub-operation-journal-archive-request",
                "version": 1,
            }
        }

        class NeverReadRequest(FakeJsonRequest):
            def __init__(self, path, declared):
                super().__init__("192.168.1.20", user, path, archive_body)
                self.content_length = declared
                self.read_calls = 0

            async def read(self, size=-1):
                self.read_calls += 1
                raise AssertionError("oversized or missing request must not be read")

        for view, path in (
            (OperationJournalArchiveView(self.hass), "/api/hausman_hub/v1/admin/operations/archive"),
            (OperationJournalResetView(self.hass), "/api/hausman_hub/v1/admin/operations/reset"),
        ):
            oversized = NeverReadRequest(path, 4097)
            response = asyncio.run(view.post(oversized))
            self.assertEqual(413, response.status)
            self.assertEqual("invalid_request", response.payload["code"])
            self.assertEqual(0, oversized.read_calls)
            self.assertEqual("no-store", response.headers["Cache-Control"])
            self.assertEqual("no-cache", response.headers["Pragma"])

            missing = NeverReadRequest(path, None)
            response = asyncio.run(view.post(missing))
            self.assertEqual(400, response.status)
            self.assertEqual(0, missing.read_calls)

        mismatch = FakeJsonRequest(
            "192.168.1.20",
            user,
            "/api/hausman_hub/v1/admin/operations/archive",
            archive_body,
        )
        mismatch.content_length += 1
        response = asyncio.run(OperationJournalArchiveView(self.hass).post(mismatch))
        self.assertEqual(400, response.status)
        self.assertEqual("invalid_request", response.payload["code"])

    def test_operation_journal_rate_and_opaque_token_errors_match_contract(self) -> None:
        from custom_components.hausman_hub.application.operation_journal import (
            OperationJournalService,
        )
        from custom_components.hausman_hub.application.operation_journal_admin import (
            OperationJournalArchiveService,
        )
        from custom_components.hausman_hub.operation_journal_api import (
            OperationJournalArchiveView,
            OperationJournalResetView,
        )

        class Store:
            def __init__(self):
                self.payload = None

            async def async_load(self):
                return self.payload

            async def async_save(self, payload):
                self.payload = payload

        class Keyring:
            active_key_id = "external-test-key"
            keys = {active_key_id: bytes.fromhex("55" * 32)}
            active_key = keys[active_key_id]
            backup_separated = True

            def key_for(self, key_id):
                return self.keys.get(key_id)

        journal = OperationJournalService(Store(), now_ms=lambda: 100)
        service = OperationJournalArchiveService(
            journal, Store(), keyring=Keyring(), now_ms=lambda: 100
        )
        self.hass.data["hausman_hub"]["operation_journal_archive"] = service
        user = reader_user(admin=True)
        archive_body = {
            "contract": {
                "name": "hausman-hub-operation-journal-archive-request",
                "version": 1,
            }
        }
        archive_path = "/api/hausman_hub/v1/admin/operations/archive"
        for _ in range(2):
            response = asyncio.run(
                OperationJournalArchiveView(self.hass).post(
                    FakeJsonRequest("192.168.1.20", user, archive_path, archive_body)
                )
            )
            self.assertEqual(201, response.status)
            self.assertFalse(response.payload["physicalCommandsSent"])
        archived_receipt = response.payload
        limited = asyncio.run(
            OperationJournalArchiveView(self.hass).post(
                FakeJsonRequest("192.168.1.20", user, archive_path, archive_body)
            )
        )
        self.assertEqual(429, limited.status)
        self.assertEqual(
            {
                "contract": {"name": "hausman-hub-error", "version": 1},
                "code": "rate_limited",
                "message": "Слишком много запросов. Подождите перед повтором.",
                "retryable": True,
                "details": {"retryAfterSeconds": 60},
            },
            limited.payload,
        )
        self.assertEqual("60", limited.headers["Retry-After"])

        reset_path = "/api/hausman_hub/v1/admin/operations/reset"
        invalid = asyncio.run(
            OperationJournalResetView(self.hass).post(
                FakeJsonRequest(
                    "192.168.1.20",
                    user,
                    reset_path,
                    {
                        "contract": {
                            "name": "hausman-hub-operation-journal-reset-request",
                            "version": 1,
                        },
                        "archiveToken": "A" * 43,
                        "expectedGeneration": 1,
                        "expectedSequence": 0,
                        "expectedRevision": "0" * 16,
                    },
                )
            )
        )
        fixture = json.loads(
            (
                ROOT
                / "custom_components/hausman_hub/contracts/v1/fixtures/operation-journal-reset-token-error.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(409, invalid.status)
        self.assertEqual(fixture, invalid.payload)
        self.assertEqual("no-store", invalid.headers["Cache-Control"])
        self.assertEqual("no-cache", invalid.headers["Pragma"])

        asyncio.run(
            journal.async_append(
                {
                    "request_id": "journal-cas-change",
                    "operation": "device_action",
                    "accepted": True,
                    "confirmed": True,
                    "status": "confirmed",
                    "reason": None,
                    "error_code": None,
                }
            )
        )
        reset_body = {
            "contract": {
                "name": "hausman-hub-operation-journal-reset-request",
                "version": 1,
            },
            "archiveToken": archived_receipt["archiveToken"],
            "expectedGeneration": archived_receipt["snapshot"]["generation"],
            "expectedSequence": archived_receipt["snapshot"]["sequence"],
            "expectedRevision": archived_receipt["snapshot"]["revision"],
        }
        conflict = asyncio.run(
            OperationJournalResetView(self.hass).post(
                FakeJsonRequest("192.168.1.20", user, reset_path, reset_body)
            )
        )
        self.assertEqual(409, conflict.status)
        self.assertEqual("conflict", conflict.payload["code"])
        self.assertEqual("conflict", conflict.payload["category"])
        self.assertFalse(conflict.payload["retryable"])
        self.assertEqual(
            "Журнал изменился. Создайте новый архив перед повторным сбросом.",
            conflict.payload["message"],
        )
        details = conflict.payload["details"]
        self.assertEqual("reset_precondition_conflict", details["detailCode"])
        self.assertTrue(details["newArchiveRequired"])
        self.assertTrue(details["archivePreserved"])
        self.assertFalse(details["consumedByThisAttempt"])
        self.assertFalse(details["physicalCommandsSent"])
        self.assertFalse(
            service._archives[archived_receipt["archivedSnapshotId"]]["consumed"]
        )

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

        with patch.object(
            self.integration.asyncio,
            "run",
            side_effect=AssertionError("unload callback must not create a private loop"),
        ):
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

    def test_missing_bindings_prevent_tambur_migration_and_subscription(self) -> None:
        from custom_components.hausman_hub.application.managed_switch_migration import (
            ManagedSwitchMigration,
        )
        from custom_components.hausman_hub.application.smart_switch_runtime import (
            SmartSwitchTriggerAdapter,
        )
        from custom_components.hausman_hub.application.tambur_room_migration import (
            TamburRoomStartupCoordinator,
        )

        async def unexpected_global_migration(_migration: object) -> str:
            raise AssertionError("global migration must stay deferred in room mode")

        def unexpected_adapter(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("missing bindings must not construct an adapter")

        def unexpected_room_startup(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("missing bindings must not start room migration")

        hass = FakeHomeAssistant()
        entry = FakeEntry(
            {
                "mode": "read-only",
                "direct_execution_status": "direct_execution_blocked",
            },
            {},
            "synthetic-switch-cleanup-failure",
        )
        hass.config_entries.entries = [entry]

        with (
            patch.object(
                ManagedSwitchMigration,
                "async_apply",
                unexpected_global_migration,
            ),
            patch.object(
                SmartSwitchTriggerAdapter,
                "__init__",
                unexpected_adapter,
            ),
            patch.object(
                TamburRoomStartupCoordinator,
                "__init__",
                unexpected_room_startup,
            ),
        ):
            self.assertTrue(asyncio.run(self.integration.async_setup_entry(hass, entry)))

        self.assertEqual(
            {
                "state": "deferred",
                "reason": "tambur_room_only",
            },
            hass.data["hausman_hub"]["managed_switch_migration"],
        )
        self.assertEqual(
            {"state": "blocked", "stage": "bindings_unavailable"},
            hass.data["hausman_hub"]["tambur_room_migration"],
        )
        self.assertEqual(
            {
                "state": "unavailable",
                "reason": "bindings_unavailable",
            },
            hass.data["hausman_hub"]["smart_switch_runtime"],
        )

    def test_setup_defers_incomplete_global_controller_content_in_room_mode(self) -> None:
        from custom_components.hausman_hub.application import (
            managed_switch_migration as migration_module,
        )

        hass = FakeHomeAssistant()
        entry = FakeEntry(
            {
                "mode": "read-only",
                "direct_execution_status": "direct_execution_blocked",
            },
            {},
            "synthetic-switch-catalog-warmup",
        )
        hass.config_entries.entries = [entry]
        incomplete_manifest = tuple(
            replace(item, activation_ready=False)
            for item in migration_module.FULL_MIGRATION_MANIFEST
        )

        with patch.object(
            migration_module,
            "FULL_MIGRATION_MANIFEST",
            incomplete_manifest,
        ):
            self.assertTrue(asyncio.run(self.integration.async_setup_entry(hass, entry)))

        self.assertEqual(
            {
                "state": "deferred",
                "reason": "tambur_room_only",
            },
            hass.data["hausman_hub"]["managed_switch_migration"],
        )
        self.assertEqual(
            {"state": "blocked", "stage": "bindings_unavailable"},
            hass.data["hausman_hub"]["tambur_room_migration"],
        )
        self.assertEqual(
            {
                "state": "unavailable",
                "reason": "bindings_unavailable",
            },
            hass.data["hausman_hub"]["smart_switch_runtime"],
        )

    def test_archive_store_load_failure_does_not_abort_climate_setup(self) -> None:
        from custom_components.hausman_hub.application.operation_journal_admin import (
            JournalArchiveError,
        )
        from custom_components.hausman_hub.operation_journal_storage import (
            HomeAssistantOperationJournalArchiveStore,
        )

        async def archive_load_failure(_store: object) -> object:
            raise OSError("private archive storage detail")

        hass = FakeHomeAssistant()
        entry = FakeEntry(
            {
                "mode": "read-only",
                "direct_execution_status": "direct_execution_blocked",
            },
            {},
            "synthetic-archive-load-failure",
        )
        hass.config_entries.entries = [entry]

        with patch.object(
            HomeAssistantOperationJournalArchiveStore,
            "async_load",
            archive_load_failure,
        ):
            self.assertTrue(asyncio.run(self.integration.async_setup_entry(hass, entry)))

        self.assertIn("climate_runtime", hass.data["hausman_hub"])
        archive_service = hass.data["hausman_hub"]["operation_journal_archive"]
        with self.assertRaisesRegex(JournalArchiveError, "archive_storage_unavailable"):
            asyncio.run(archive_service.async_archive("admin"))

    def test_public_manual_off_cancels_tambur_auto_on_during_power_warmup(self) -> None:
        """The real public path fences auto work before waiting for light authority."""

        from custom_components.hausman_hub.application.scenario_decision_bridge import (
            ScenarioDecisionBridge,
        )
        from custom_components.hausman_hub.application.scenario_executor import (
            ScenarioExecutor,
        )
        from custom_components.hausman_hub.application.scenario_service import (
            ScenarioService,
        )
        from custom_components.hausman_hub.application.scenarios import (
            ScenarioCatalog,
            ScenarioDeviceAction,
            ScenarioDeviceEntry,
        )
        from custom_components.hausman_hub.domain.device_power_dependencies import (
            DevicePowerDependency,
        )

        target_id = "lamp_chandelier_demo"
        entity_id = "light.tambur_chandelier"
        power_entity_id = "switch.tambur_power"
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

        class Store:
            value = None

            async def async_load(inner_self):
                return copy.deepcopy(inner_self.value)

            async def async_save(inner_self, value):
                inner_self.value = copy.deepcopy(value)

        catalog = ScenarioCatalog(
            devices={
                target_id: ScenarioDeviceEntry(
                    target_id=target_id,
                    name="Люстра тамбура",
                    entity_id=entity_id,
                    actions=(
                        ScenarioDeviceAction(
                            "turn_on", "Включить", "light", "turn_on", frozenset()
                        ),
                        ScenarioDeviceAction(
                            "turn_off", "Выключить", "light", "turn_off", frozenset()
                        ),
                    ),
                )
            },
            scenarios={},
        )
        stamp = datetime.now(timezone.utc)
        self.hass.states.values[entity_id] = SimpleNamespace(
            state="off", attributes={}, last_changed=stamp,
            last_updated=stamp, last_reported=stamp,
        )
        self.hass.states.values[power_entity_id] = SimpleNamespace(
            state="off", attributes={}, last_changed=stamp,
            last_updated=stamp, last_reported=stamp,
        )
        power_on = asyncio.Event()
        calls: list[tuple[str, str, str]] = []

        class Services:
            async def async_call(
                inner_self,
                domain: str,
                action_id: str,
                data: dict[str, object],
                **_kwargs: object,
            ) -> None:
                current_entity = str(data["entity_id"])
                calls.append((domain, action_id, current_entity))
                observed = datetime.now(timezone.utc)
                self.hass.states.values[current_entity] = SimpleNamespace(
                    state="on" if action_id == "turn_on" else "off",
                    attributes={}, last_changed=observed,
                    last_updated=observed, last_reported=observed,
                )
                if current_entity == power_entity_id and action_id == "turn_on":
                    power_on.set()

        self.hass.services = Services()
        service = ScenarioService(self.hass, Store(), catalog)
        executor = ScenarioExecutor(
            self.hass,
            catalog,
            service.async_run_scenario,
            power_dependency_resolver=lambda: {
                entity_id: DevicePowerDependency(
                    entity_id, power_entity_id, "auto_turn_on", 30
                )
            },
            command_guard=lambda *_args: None,
            electrical_breaker_resolver=lambda _entity: False,
            readback_window_seconds=0.05,
            readback_interval_seconds=0.01,
        )
        service.set_executor(executor)

        async def source(_scenario_id, event, observation_epoch):
            return {
                "settingsRevision": 7,
                "issuedAtMs": now_ms,
                "expiresAtMs": now_ms + 60_000,
                "event": event,
                "clock": {
                    "nowMs": now_ms,
                    "timezone": "Asia/Omsk",
                    "localDate": "2027-01-15",
                    "minutesOfDay": 660,
                    "sunsetAtMs": now_ms + 20_000_000,
                },
                "bindings": {
                    "chandelier": target_id,
                    "points": "lamp_points_demo",
                    "mirror": "lamp_mirror_demo",
                    "power": "power_demo",
                    "presenceSensors": ["sensor_demo"],
                },
                "settings": {
                    "morningStart": "09:00", "morningEnd": "10:00",
                    "eveningLatestStart": "21:00", "mainOff": "23:00",
                    "mirrorOff": "01:00", "minPercent": 5, "maxPercent": 80,
                    "dayKelvin": 3000, "eveningKelvin": 2200,
                    "absenceDaySeconds": 600, "absenceNightSeconds": 180,
                    "fadeSeconds": 20, "manualOffMinSeconds": 600,
                    "manualOffAbsenceSeconds": 30, "manualOnHoldSeconds": 3600,
                },
                "observations": {
                    target_id: {"state": "off", "revision": 11, "observedAtMs": now_ms, "fresh": True, "continuityEpoch": observation_epoch},
                    "lamp_points_demo": {"state": "off", "revision": 12, "observedAtMs": now_ms, "fresh": True, "continuityEpoch": observation_epoch},
                    "lamp_mirror_demo": {"state": "off", "revision": 13, "observedAtMs": now_ms, "fresh": True, "continuityEpoch": observation_epoch},
                    "sensor_demo": {"state": "on", "revision": 20, "observedAtMs": now_ms, "fresh": True, "continuityEpoch": observation_epoch},
                },
                "authority": {
                    target_id: {"owner": "none", "generation": 2, "protectionActive": False},
                    "lamp_points_demo": {"owner": "none", "generation": 3, "protectionActive": False},
                    "lamp_mirror_demo": {"owner": "none", "generation": 4, "protectionActive": False},
                },
            }

        async def authority(_target_id):
            return {"generation": 2, "observedRevision": 11, "observedAtMs": now_ms, "observationEpoch": 1, "fresh": True, "owner": "none", "protectionActive": False}

        bridge_store = Store()
        bridge = ScenarioDecisionBridge(
            bridge_store,
            snapshot_provider=source,
            authority_provider=authority,
            now_ms=lambda: now_ms,
            executor=executor,
        )
        service.set_manual_action_pre_admission(bridge.async_register_manual_intent)
        self.hass.data["hausman_hub"]["scenario_service"] = service
        device_view = next(
            view for view in self.hass.http.views
            if view.url == "/api/hausman_hub/v1/device-actions"
        )

        async def run_race() -> tuple[dict[str, object], object]:
            await bridge.async_recover()
            request = await bridge.async_snapshot(
                "system-tambur-adaptive-controller",
                {"id": "presence.1", "kind": "sensor", "observedAtMs": now_ms, "targetId": "sensor_demo"},
            )
            decision = {
                "contract": {"name": "hausman-node-red-decision", "version": 1},
                "correlationId": request["correlationId"],
                "scenarioId": "system-tambur-adaptive-controller",
                "planId": request["correlationId"],
                "controllerVersion": 1, "settingsRevision": 7,
                "baseRevision": request["durable"]["revision"],
                "snapshotRevision": request["snapshotRevision"],
                "observationEpoch": request["observationEpoch"],
                "expiresAtMs": request["expiresAtMs"],
                "status": "decided", "reasonCode": "presence_day", "trace": [],
                "nextState": {"phase": "occupied", "phaseStartedAtMs": now_ms, "absenceSinceMs": None, "absenceEpoch": None, "fadeStartPercent": None, "fadeStartedAtMs": None, "fadeReason": None},
                "wakeups": [],
                "action": {"id": f"{str(request['correlationId'])[:119]}.act", "targetId": target_id, "actionId": "turn_on", "authorityGeneration": 2, "observedRevision": 11},
            }
            automatic = asyncio.create_task(
                executor.async_execute_tambur_decision(decision, bridge)
            )
            await asyncio.wait_for(power_on.wait(), 1)
            manual = asyncio.create_task(
                device_view.post(
                    FakeJsonRequest(
                        "192.168.1.20", reader_user("system-users"),
                        "/api/hausman_hub/v1/device-actions",
                        {"targetId": target_id, "actionId": "turn_off"},
                    )
                )
            )
            return await automatic, await manual

        automatic_result, manual_response = asyncio.run(run_race())

        self.assertIn(automatic_result["status"], {"failed", "uncertain"})
        self.assertEqual(200, manual_response.status)
        self.assertTrue(bridge_store.value["manualIntents"])
        self.assertEqual(
            [("switch", "turn_on", power_entity_id)],
            [call for call in calls if call[1] == "turn_on"],
        )
        self.assertNotIn(("light", "turn_on", entity_id), calls)
        self.assertNotIn(("switch", "turn_off", power_entity_id), calls)
        self.assertFalse(executor._light_priority.authority_lock().locked())  # noqa: SLF001

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
        self.assertEqual(98, len(closed_hass.http.views))
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
                "/api/hausman_hub/v1/admin/operations/archive",
                "/api/hausman_hub/v1/admin/operations/reset",
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
                "/api/hausman_hub/v1/lighting/manual-off-protection",
                "/api/hausman_hub/v1/lighting/manual-off-protection/release",
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
