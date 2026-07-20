"""Isolated tests for the authenticated local nine-count summary view."""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime
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


class FakeJsonRequest(FakeRequest):
    """Add the bounded JSON request surface used by climate POST routes."""

    def __init__(self, remote: object, user: object, path: str, payload: object) -> None:
        super().__init__(remote, user, path=path)
        self._payload = payload
        self.content_type = "application/json"
        self.content_length = len(json.dumps(payload).encode("utf-8"))

    async def json(self) -> object:
        return self._payload


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
        ) -> None:
            self.hass = hass
            self.version = version
            self.key = key
            self.max_readable_version = max_readable_version

        async def async_load(self) -> None:
            return None

        async def async_save(self, _: object) -> None:
            return None

    storage.Store = FakeStore  # type: ignore[attr-defined]
    util = ModuleType("homeassistant.util")
    dt = ModuleType("homeassistant.util.dt")
    dt.now = lambda: datetime(2026, 7, 19, 12, 0)  # type: ignore[attr-defined]

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

    def test_disabled_climate_routes_separate_tablet_and_admin_roles(self) -> None:
        """A normal tablet cannot administer, and an admin cannot impersonate it."""

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
        self.assertEqual("no-store", capabilities_response.headers.get("Cache-Control"))
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
        for user in (admin, read_only):
            with self.subTest(capabilities_user=user):
                self.assertEqual(
                    403,
                    asyncio.run(
                        capabilities.get(
                            FakeRequest(
                                "127.0.0.1",
                                user,
                                path=capabilities_path,
                            )
                        )
                    ).status,
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
        self.assertEqual({"version": 1, "rooms": [], "devices": []}, admin_response.payload)
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

        draft_path = "/api/hausman_hub/v1/admin/climate-drafts"
        draft = views[draft_path]
        draft_request = {
            "snapshot_revision": 1,
            "name": "Климат",
            "mode": "automatic",
            "rooms": [],
        }
        self.assertEqual(
            503,
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
            503,
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
            503,
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

        preflight_path = "/api/hausman_hub/v1/admin/climate-canary-preflight"
        preflight = views[preflight_path]
        invalid_admin_response = asyncio.run(
            preflight.post(
                FakeJsonRequest(
                    "127.0.0.1",
                    admin,
                    preflight_path,
                    {"room_id": "living"},
                )
            )
        )
        self.assertEqual(400, invalid_admin_response.status)
        forbidden_preflight = asyncio.run(
            preflight.post(
                FakeJsonRequest(
                    "127.0.0.1",
                    tablet,
                    preflight_path,
                    {"room_id": "living"},
                )
            )
        )
        self.assertEqual(403, forbidden_preflight.status)

    def test_shadow_climate_route_returns_public_state_and_never_posts(self) -> None:
        """Exercise the Android facade with an actual runtime and in-memory bridge."""

        from custom_components.hausman_hub.application.climate_import import (
            import_climate_state,
        )
        from custom_components.hausman_hub.application.climate_runtime import ClimateRuntime
        from custom_components.hausman_hub.application.contours import (
            build_climate_contour_setup,
        )
        from custom_components.hausman_hub.domain.climate_bridge import ClimateBridgeMode
        from custom_components.hausman_hub.domain.configuration import SafeConfiguration
        from tests.test_climate_import import source_payload

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

            async def async_fetch_state(self):
                return import_climate_state(source_payload())

            async def async_execute(self, plan):
                self.executed.append(plan)
                return {"ok": True}

        bridge = Bridge()
        runtime = ClimateRuntime(
            entry_id=self.entry.entry_id,
            configuration=SafeConfiguration(
                mode="shadow",
                climate_bridge_mode=ClimateBridgeMode.SHADOW,
            ),
            registry_store=Store(),
            contour_store=ContourStore(),
            bridge_client=bridge,
            now_ms=lambda: 1784280005000,
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
        self.assertFalse(living_control["enabled"])
        self.assertEqual(
            ["set_room_target", "turn_room_off"],
            living_control["actions"],
        )
        self.assertEqual([], living_control["allowed_actions"])
        self.assertEqual(
            ["shadow_only"],
            living_control["action_availability"]["set_room_target"][
                "blocked_reasons"
            ],
        )
        self.assertEqual(
            (18.0, 28.0, 0.5, "°C"),
            tuple(
                living_control["action_inputs"]["set_room_target"][
                    "target_temperature"
                ][key]
                for key in ("minimum", "maximum", "step", "unit")
            ),
        )
        self.assertEqual(
            ("Установить температуру", False),
            (
                living_control["action_presentations"]["set_room_target"]["title"],
                living_control["action_presentations"]["set_room_target"][
                    "confirmation_required"
                ],
            ),
        )
        self.assertEqual(
            ("Выключить климат", True),
            (
                living_control["action_presentations"]["turn_room_off"]["title"],
                living_control["action_presentations"]["turn_room_off"][
                    "confirmation_required"
                ],
            ),
        )
        self.assertEqual(["shadow_only"], living_control["blocked_reasons"])
        serialized = json.dumps(home_response.payload)
        self.assertNotIn("synthetic-ac-source-living", serialized)
        self.assertNotIn("entity_id", serialized)

        apply_preview_path = "/api/hausman_hub/v1/contours/apply-preview"
        apply_preview_response = asyncio.run(
            views[apply_preview_path].get(
                FakeRequest(
                    "192.168.1.20",
                    tablet,
                    path=apply_preview_path,
                )
            )
        )
        self.assertEqual(503, apply_preview_response.status)
        apply_path = "/api/hausman_hub/v1/contours/apply"
        apply_response = asyncio.run(
            views[apply_path].post(
                FakeJsonRequest(
                    "192.168.1.20",
                    tablet,
                    apply_path,
                    {
                        "request_id": "adapter-contour-apply-1",
                        "contour_id": "climate",
                        "confirm": True,
                    },
                )
            )
        )
        self.assertEqual(503, apply_response.status)
        self.assertEqual([], bridge.executed)

        action_response = asyncio.run(
            views["/api/hausman_hub/v1/actions"].post(
                FakeJsonRequest(
                    "192.168.1.20",
                    tablet,
                    "/api/hausman_hub/v1/actions",
                    {
                        "request_id": "adapter-shadow-1",
                        "action": "set_room_target",
                        "room_id": "living",
                        "target_temperature": 24.5,
                    },
                )
            )
        )
        self.assertEqual(200, action_response.status)
        self.assertEqual("accepted", action_response.payload["status"])
        self.assertEqual("shadow", action_response.payload["execution"])
        self.assertEqual("adapter-shadow-1", action_response.payload["request_id"])
        self.assertEqual([], bridge.executed)

        evidence_path = "/api/hausman_hub/v1/admin/climate-shadow-evidence"
        evidence_view = views[evidence_path]
        evidence_response = asyncio.run(
            evidence_view.post(
                FakeJsonRequest(
                    "192.168.1.20",
                    reader_user("system-admin", admin=True),
                    evidence_path,
                    {"room_id": "living"},
                )
            )
        )
        self.assertEqual(200, evidence_response.status)
        self.assertEqual("collecting", evidence_response.payload["candidate"]["status"])
        evidence_json = json.dumps(evidence_response.payload, sort_keys=True)
        self.assertNotIn("synthetic-ac-source-living", evidence_json)
        self.assertNotIn("entity_id", evidence_json)
        forbidden = asyncio.run(
            evidence_view.post(
                FakeJsonRequest(
                    "192.168.1.20",
                    tablet,
                    evidence_path,
                    {"room_id": "living"},
                )
            )
        )
        self.assertEqual(403, forbidden.status)

        preflight_path = "/api/hausman_hub/v1/admin/climate-canary-preflight"
        preflight_view = views[preflight_path]
        preflight_response = asyncio.run(
            preflight_view.post(
                FakeJsonRequest(
                    "192.168.1.20",
                    reader_user("system-admin", admin=True),
                    preflight_path,
                    {"room_id": "living"},
                )
            )
        )
        self.assertEqual(200, preflight_response.status)
        self.assertEqual("blocked", preflight_response.payload["status"])
        self.assertFalse(preflight_response.payload["ready_for_authorization"])
        self.assertTrue(preflight_response.payload["freshness"]["state_fresh"])
        self.assertFalse(preflight_response.payload["activation"]["allowed"])
        self.assertEqual(
            "no-store",
            preflight_response.headers.get("Cache-Control"),
        )
        preflight_json = json.dumps(preflight_response.payload, sort_keys=True)
        self.assertNotIn("synthetic-ac-source-living", preflight_json)
        self.assertNotIn("entity_id", preflight_json)
        forbidden_preflight = asyncio.run(
            preflight_view.post(
                FakeJsonRequest(
                    "192.168.1.20",
                    tablet,
                    preflight_path,
                    {"room_id": "living"},
                )
            )
        )
        self.assertEqual(403, forbidden_preflight.status)
        self.assertEqual([], bridge.executed)

    def test_local_admin_creates_unsaved_climate_draft_and_tablet_cannot(self) -> None:
        """The first setup POST returns only a draft and performs no write."""

        from custom_components.hausman_hub.application.climate_import import (
            import_climate_state,
        )
        from custom_components.hausman_hub.application.climate_registry import (
            registry_from_payload,
        )
        from custom_components.hausman_hub.application.climate_runtime import ClimateRuntime
        from custom_components.hausman_hub.domain.climate_bridge import ClimateBridgeMode
        from custom_components.hausman_hub.domain.configuration import SafeConfiguration
        from custom_components.hausman_hub.domain.contours import ContourRegistry
        from tests.test_climate_import import source_payload

        registry = registry_from_payload({"version": 1, "rooms": [], "devices": []})

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

            async def async_fetch_state(self):
                return import_climate_state(source_payload())

            async def async_execute(self, plan):
                self.executed.append(plan)
                return {"ok": True}

        store = Store()
        contour_store = ContourStore()
        bridge = Bridge()
        runtime = ClimateRuntime(
            entry_id=self.entry.entry_id,
            configuration=SafeConfiguration(
                mode="shadow",
                climate_bridge_mode=ClimateBridgeMode.MANAGED,
            ),
            registry_store=store,
            contour_store=contour_store,
            bridge_client=bridge,
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
        request = {
            "snapshot_revision": revision,
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
        oversized_action = FakeJsonRequest(
            "192.168.1.20",
            reader_user("system-users"),
            "/api/hausman_hub/v1/actions",
            {
                "request_id": "oversized-action",
                "action": "set_room_target",
                "room_id": "living",
                "target_temperature": 24.5,
            },
        )
        oversized_action.content_length = 16 * 1024 + 1
        actions_view = {
            item.url: item for item in self.hass.http.views
        }["/api/hausman_hub/v1/actions"]
        self.assertEqual(400, asyncio.run(actions_view.post(oversized_action)).status)
        self.assertEqual([], bridge.executed)
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

        from custom_components.hausman_hub.application.climate_import import (
            import_climate_state,
        )
        from custom_components.hausman_hub.application.climate_runtime import ClimateRuntime
        from custom_components.hausman_hub.application.contours import (
            build_climate_contour_setup,
        )
        from custom_components.hausman_hub.domain.climate_bridge import ClimateBridgeMode
        from custom_components.hausman_hub.domain.configuration import SafeConfiguration
        from tests.test_climate_import import source_payload

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
            configuration=SafeConfiguration(
                mode="shadow",
                climate_bridge_mode=ClimateBridgeMode.SHADOW,
            ),
            registry_store=registry_store,
            contour_store=contour_store,
            bridge_client=bridge,
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
        self.assertEqual(1, len(contour_store.saved))
        self.assertEqual("no-store", response.headers.get("Cache-Control"))
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

    def test_local_admin_enables_schedule_without_sending_device_commands(self) -> None:
        """The strict schedule route needs consent and only persists the timer."""

        from custom_components.hausman_hub.application.climate_import import (
            import_climate_state,
        )
        from custom_components.hausman_hub.application.climate_runtime import ClimateRuntime
        from custom_components.hausman_hub.application.contours import (
            build_climate_contour_setup,
        )
        from custom_components.hausman_hub.domain.climate_bridge import ClimateBridgeMode
        from custom_components.hausman_hub.domain.configuration import SafeConfiguration
        from tests.test_climate_import import source_payload

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
            configuration=SafeConfiguration(
                mode="shadow",
                climate_bridge_mode=ClimateBridgeMode.MANAGED,
            ),
            registry_store=Store(registry),
            contour_store=contour_store,
            bridge_client=bridge,
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
        self.assertEqual(1, len(contour_store.saved))
        self.assertEqual("no-store", response.headers.get("Cache-Control"))
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

    def test_managed_contour_routes_apply_once_and_confirm_engine_state(self) -> None:
        """The tablet may apply only saved settings through the managed contour."""

        from custom_components.hausman_hub.application.climate_import import (
            import_climate_state,
        )
        from custom_components.hausman_hub.application.climate_runtime import ClimateRuntime
        from custom_components.hausman_hub.application.contours import (
            build_climate_contour_setup,
            with_applied_climate_schedule_profile,
            with_climate_schedule,
        )
        from custom_components.hausman_hub.domain.climate_bridge import ClimateBridgeMode
        from custom_components.hausman_hub.domain.configuration import SafeConfiguration
        from custom_components.hausman_hub.domain.contours import ClimateProfile
        from tests.test_climate_import import source_payload

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

        class Store:
            async def async_load(self):
                return registry

            async def async_save(self, value):
                return None

        class ContourStore:
            async def async_load(self):
                return contours

            async def async_save(self, value):
                return None

        class Bridge:
            def __init__(self) -> None:
                self.executed = []

            async def async_fetch_state(self):
                return import_climate_state(source)

            async def async_execute(self, plan):
                self.executed.append(plan)
                room = source["rooms"][0]
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

        bridge = Bridge()
        runtime = ClimateRuntime(
            entry_id=self.entry.entry_id,
            configuration=SafeConfiguration(
                mode="shadow",
                climate_bridge_mode=ClimateBridgeMode.MANAGED,
            ),
            registry_store=Store(),
            contour_store=ContourStore(),
            bridge_client=bridge,
            operation_id_factory=iter(("4" * 32, "5" * 32)).__next__,
            now_ms=lambda: 1784280005000,
        )
        asyncio.run(runtime.async_start())
        self.hass.data["hausman_hub"]["climate_runtime"] = runtime
        views = {view.url: view for view in self.hass.http.views}
        tablet = reader_user("system-users")

        preview_path = "/api/hausman_hub/v1/contours/apply-preview"
        preview = asyncio.run(
            views[preview_path].get(
                FakeRequest("192.168.1.20", tablet, path=preview_path)
            )
        )
        self.assertEqual(200, preview.status)
        self.assertEqual(3, preview.payload["command_count"])
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
        self.assertEqual(first.payload, duplicate.payload)
        self.assertEqual(3, len(bridge.executed))
        serialized = json.dumps(first.payload, sort_keys=True)
        self.assertNotIn("synthetic-ac-source-living", serialized)
        self.assertNotIn("backend_payload", serialized)

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
                        "target_temperature": 23.5,
                        "confirm": True,
                    },
                )
            )
        )
        self.assertEqual(200, temporary_response.status)
        self.assertEqual("confirmed", temporary_response.payload["status"])
        self.assertEqual(1, temporary_response.payload["room_count"])
        self.assertEqual(4, len(bridge.executed))

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
        self.assertEqual(21, len(self.hass.http.views))
        self.assertEqual(
            1,
            sum(
                view.url == "/api/hausman_hub/local-summary"
                for view in self.hass.http.views
            ),
        )
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
        self.assertEqual(20, len(closed_hass.http.views))
        self.assertEqual(
            {
                "/api/hausman_hub/v1/capabilities",
                "/api/hausman_hub/v1/home",
                "/api/hausman_hub/v1/contours",
                "/api/hausman_hub/v1/contours/apply-preview",
                "/api/hausman_hub/v1/contours/apply",
                "/api/hausman_hub/v1/contours/temporary-temperature",
                "/api/hausman_hub/v1/actions",
                "/api/hausman_hub/v1/admin/climate-import",
                "/api/hausman_hub/v1/admin/climate-drafts",
                "/api/hausman_hub/v1/admin/climate-drafts/current",
                "/api/hausman_hub/v1/admin/climate-drafts/validate",
                "/api/hausman_hub/v1/admin/climate-drafts/save",
                "/api/hausman_hub/v1/admin/climate-profiles",
                "/api/hausman_hub/v1/admin/climate-schedule",
                "/api/hausman_hub/v1/admin/climate-registry",
                "/api/hausman_hub/v1/admin/climate-registry-preview",
                "/api/hausman_hub/v1/admin/climate-readiness",
                "/api/hausman_hub/v1/admin/climate-shadow-evidence",
                "/api/hausman_hub/v1/admin/climate-canary-preflight",
                "/api/hausman_hub/v1/operations",
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
