"""HTTP boundary tests for room lighting config, status, templates and live tests.

Responses are validated against the contract schemas from the contract worktree.
"""

from __future__ import annotations

import asyncio
import copy
import importlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from jsonschema import Draft202012Validator

from tests.test_local_summary_access import (
    FAKE_MODULE_NAMES,
    FakeHomeAssistant,
    FakeJsonRequest,
    FakeRequest,
    fake_home_assistant_modules,
    reader_user,
)

PACKAGE_MODULE = "custom_components.hausman_hub"
API_MODULE = f"{PACKAGE_MODULE}.room_lighting_api"
_CONTRACT_DIR = Path(
    os.environ.get(
        "HAUSMANHUB_CONTRACT_DIR",
        "/home/ivsh/projects/HausmanHub/worktrees/"
        "codex-room-lighting-contract-2026-09-11/schemas/v1",
    )
)


def _validate(schema_name: str, payload: object) -> None:
    path = _CONTRACT_DIR / schema_name
    if not path.is_file():
        # A missing contract checkout must skip, not error. ``pytest.skip``
        # raises ``_pytest.outcomes.Skipped``, which unittest reports as an
        # error when raised inside ``asyncio.run``. ``unittest.SkipTest`` is
        # honoured by both runners.
        raise unittest.SkipTest(
            f"room lighting contract schema is not available: {schema_name}"
        )
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(payload)


def _config_payload(room_id: str = "room_demo_entry", name: str = "Тамбур") -> dict:
    return {
        "contract": {"name": "hausman-hub-room-lighting-config", "version": 1},
        "roomId": room_id,
        "name": name,
        "version": 1,
        "devices": {
            "sensors": [
                {
                    "id": "sensor_demo_presence",
                    "name": "Присутствие",
                    "kind": "presence",
                    "entityId": "binary_sensor.demo_presence",
                    "autoAdoptOverride": None,
                }
            ],
            "light_targets": [
                {
                    "id": "light_main",
                    "name": "Люстра",
                    "kind": "light",
                    "entityId": "light.demo_main",
                    "role": "main",
                    "groupId": None,
                    "brightness": True,
                    "color_temperature": True,
                    "autoAdoptOverride": None,
                }
            ],
            "power_switch": None,
            "wireless_switches": [],
            "selectAll": False,
        },
        "schedule": [
            {
                "id": "sch_day",
                "title": "День",
                "when": {
                    "daysOfWeek": "all",
                    "holiday": False,
                    "anchor": {"kind": "fixed", "time": "09:00", "offsetMinutes": 0},
                },
                "targets": {"lightTargets": ["light_main"], "groupIds": [], "roles": []},
                "how": {
                    "brightness": 60,
                    "colorTemperature": 3000,
                    "fade": True,
                    "mode": "on_presence",
                    "minOnSeconds": 0,
                },
            }
        ],
        "switchBindings": [],
        "illumination": None,
        "dimming": {
            "enabled": True,
            "onAbsence": True,
            "fadeSeconds": 20,
            "targetPercent": 0,
        },
        "manualOffProtection": {
            "enabled": True,
            "minimumIntervalSeconds": 600,
            "releaseMode": "timer_and_absence",
            "stableAbsenceSeconds": 30,
            "priority": "manual_above_auto",
        },
        "awayBehavior": {"mode": "none"},
        "autoAdopt": True,
        "updatedAt": 1,
        "overrides": {},
    }


def _absence_switch_payload(*, second=False):
    """A legal schedule room whose absence command is OFF, not a fade step."""
    payload = _config_payload()
    main = payload["devices"]["light_targets"][0]
    main.update(kind="switch", entityId="switch.demo_main",
        brightness=False, color_temperature=False)
    payload["schedule"][0]["how"].update(brightness=None, colorTemperature=None)
    payload["timers"] = {"absence_seconds": 300, "turn_off_seconds": 300}
    peer = copy.deepcopy(main)
    peer.update(id="light_peer", entityId="switch.demo_peer", role="other")
    payload["devices"]["light_targets"].append(peer)
    if second:
        payload["schedule"][0]["targets"]["lightTargets"].append("light_peer")
    return payload


class _MemoryStore:
    def __init__(self) -> None:
        self.configs: dict[str, object] = {}

    async def async_get(self, room_id: str):
        return self.configs.get(room_id)

    async def async_load_all(self):
        return tuple(self.configs.values())

    async def async_upsert(self, config) -> None:
        self.configs[config.room_id] = config

    async def async_delete(self, room_id: str) -> bool:
        return self.configs.pop(room_id, None) is not None


class _DelayedMemoryStore(_MemoryStore):
    """Hold one configuration save open to inspect the concurrent status read."""

    def __init__(self, config: object) -> None:
        super().__init__()
        self.configs[config.room_id] = config
        self.upsert_started = asyncio.Event()
        self.release = asyncio.Event()

    async def async_upsert(self, config) -> None:
        self.upsert_started.set()
        await self.release.wait()
        self.configs[config.room_id] = config


class _SpyExecutor:
    def __init__(self) -> None:
        self.calls: list[object] = []

    async def __call__(self, command: object) -> dict:
        self.calls.append(command)
        return {"confirmed": True}


def _request(path: str, **match: str) -> FakeRequest:
    request = FakeRequest("127.0.0.1", reader_user("admin", admin=True), path=path)
    request.match_info = match
    return request


def _json_request(path: str, payload: object, **match: str) -> FakeJsonRequest:
    request = FakeJsonRequest(
        "127.0.0.1", reader_user("admin", admin=True), path, payload
    )
    request.match_info = match
    return request


class _LiveConfigHarness:
    """Real API/service/storage/runtime, fake only HA and its storage backend."""

    def __init__(self, case):
        from tests.test_room_lighting_runtime import _FakeHass, _MemoryShadowStore
        from custom_components.hausman_hub.application import room_lighting_ha_executor as executor_module
        from custom_components.hausman_hub.application.room_lighting_runtime import RoomLightingRuntime
        from custom_components.hausman_hub.application.room_lighting_shadow import RoomLightingShadowService
        from custom_components.hausman_hub.application.room_lighting_service import RoomLightingService
        from custom_components.hausman_hub.application.room_lighting_storage import HomeAssistantRoomLightingStore
        from custom_components.hausman_hub.domain.room_lighting import ROOM_LIGHTING_STORAGE_VERSION

        class Backend:
            def __init__(self):
                self.payload = {"version": ROOM_LIGHTING_STORAGE_VERSION, "rooms": []}
                self.fail = False
                self.saves = 0

            async def async_load(self):
                return copy.deepcopy(self.payload)

            async def async_save(self, payload):
                self.saves += 1
                if self.fail:
                    return False
                self.payload = copy.deepcopy(payload)

        self.case = case
        self.now = int(datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc).timestamp() * 1000)
        self.backend = Backend()
        self.store = object.__new__(HomeAssistantRoomLightingStore)
        self.store._store = self.backend
        self.service = RoomLightingService(self.store)
        case.hass.data["hausman_hub"][case.api.DATA_ROOM_LIGHTING_SERVICE] = self.service
        self.hass = _FakeHass()
        self.hass._command_report_clock = lambda: self.now
        for entity, state in (("binary_sensor.a100_away", "off"),
                              ("binary_sensor.demo_presence", "off"),
                              ("light.demo_main", "off"), ("sun.sun", "above_horizon")):
            self.hass.states.set(entity, state, last_changed=self.dt())
        self.shadow_store = _MemoryShadowStore()
        self.shadow = RoomLightingShadowService(self.shadow_store)
        self.runtime = RoomLightingRuntime(
            self.hass, self.service, self.shadow, now_ms=lambda: self.now,
            executor=executor_module.RoomLightingHaExecutor(
                light_readback_window_seconds=0, now_ms=lambda: self.now),
            track_state_changes=lambda *_: lambda: None,
            track_interval=lambda *_: lambda: None,
            listen_bus=lambda *_: lambda: None,
            away_entity_id="binary_sensor.a100_away",
        )
        case.hass.data["hausman_hub"][case.api.DATA_ROOM_LIGHTING_RUNTIME] = self.runtime
        self.context_patch = patch.object(
            executor_module, "_new_service_context",
            lambda: SimpleNamespace(id=uuid4().hex, parent_id=None, user_id=None),
        )
        self.context_patch.start()

    def dt(self):
        return datetime.fromtimestamp(self.now / 1000, timezone.utc)

    async def put(self, payload):
        return await self.case.config_view.put(_json_request(
            self.case._config_path(payload["roomId"]), copy.deepcopy(payload), room_id=payload["roomId"]))

    async def start(self, payload=None):
        payload = copy.deepcopy(payload or _config_payload())
        payload.setdefault("commandsEnabled", True)
        created = await self.put(payload)
        self.case.assertEqual(200, created.status)
        self.saved = copy.deepcopy(created.payload)
        await self.runtime.start(self.hass, "active-config")
        self.case.assertEqual([], self.hass.services.calls, "startup must not send")
        return created

    async def presence(self, state="on"):
        await self.state_event("binary_sensor.demo_presence", state)

    async def state_event(self, entity, state):
        self.now += 1000
        old = self.hass.states.get(entity)
        self.hass.states.set(entity, state, last_changed=self.dt())
        first = len(self.hass.tasks)
        self.runtime._state_event(SimpleNamespace(data={
            "entity_id": entity, "old_state": old, "new_state": self.hass.states.get(entity)}))
        while first < len(self.hass.tasks):
            tasks = self.hass.tasks[first:]
            first += len(tasks)
            await asyncio.gather(*tasks)

    def receipts(self):
        return [receipt for entry in self.shadow.journal_payload()["entries"]
                for receipt in entry.get("dispatch", [])]

    async def power_setup(self, *, exact=False, warmup=0, payloads=None, local_power_switch=True):
        """Wire the real dependency provider to the fake HA power circuit."""
        from custom_components.hausman_hub.application.device_power_dependencies import DevicePowerDependencyService
        from custom_components.hausman_hub.application.room_power_migration import room_target_claim_key
        from tests.test_room_lighting_runtime import _MemoryShadowStore

        if not local_power_switch and not exact:
            raise ValueError("a shared global source needs exact dependencies")
        payloads = payloads or [_config_payload()]
        for payload in payloads:
            payload["commandsEnabled"] = True
            payload["devices"]["power_switch"] = {
                "id": "power", "name": "Питание", "entityId": "switch.demo_power",
                "autoAdoptOverride": None,
            } if local_power_switch else None
        self.hass.states.set("switch.demo_power", "off", last_changed=self.dt())
        if exact:
            provider = DevicePowerDependencyService(
                _MemoryShadowStore(), electrical_breaker_resolver=lambda _entity: False)
            await provider.async_load()
            await provider.async_replace(0, [{
                "dependentEntityId": payload["devices"]["light_targets"][0]["entityId"],
                "powerSourceEntityId": "switch.demo_power", "policy": "auto_turn_on",
                "warmupSeconds": warmup,
            } for payload in payloads])
            manifest = {
                room_target_claim_key(payload["roomId"], payload["devices"]["light_targets"][0]["id"]):
                "switch.demo_power" for payload in payloads
            }
            self.runtime._power_dependency_resolver = lambda: provider.mapping
            self.runtime._power_migration_manifest = manifest
            self.runtime._state_provider._power_dependency_resolver = lambda: provider.mapping
            self.runtime._state_provider._power_migration_manifest = manifest
            self.runtime._electrical_breaker_resolver = lambda _entity: False
        return payloads

    async def close(self):
        try:
            await self.runtime.stop()
        finally:
            self.context_patch.stop()


class _ManualCallbackHarness(_LiveConfigHarness):
    """Captured MQTT callback, real manual authority, API and HA executor."""

    def __init__(self, case):
        super().__init__(case)
        from tests.test_room_lighting_runtime import _FakeDeviceAutomationApi
        from custom_components.hausman_hub.application import manual_light_authority

        self.authority = manual_light_authority.ManualLightAuthority()
        self.runtime._manual_light_authority = self.authority
        self.runtime._executor._manual_light_authority = self.authority
        self.triggers = _FakeDeviceAutomationApi()
        self.runtime._device_automation_api = self.triggers
        self.results = []
        execute = self.runtime._executor.execute

        async def observe(*args, **kwargs):
            result = await execute(*args, **kwargs)
            self.results.append(result)
            return result

        self.runtime._executor.execute = observe
        self.clock_patch = patch.object(manual_light_authority, "time",
            SimpleNamespace(time=lambda: self.now / 1000))
        self.clock_patch.start()

    @staticmethod
    def payload(enabled=True, *, multiple=False, context_wait=False):
        payload = _config_payload()
        payload["commandsEnabled"] = enabled
        payload["devices"]["wireless_switches"] = [{
            "id": "sw_mirror", "name": "Кнопка", "deviceId": "device_demo_mirror",
            "triggerSubtypes": ["1_single", "2_single"],
            "buttons": ["left", "right"], "pressTypes": ["single", "double"],
        }]
        peer = copy.deepcopy(payload["devices"]["light_targets"][0])
        peer.update(id="light_peer", entityId="light.demo_peer", role="other")
        payload["devices"]["light_targets"].append(peer)
        payload["devices"]["auxiliaries"] = [{
            "id": "aux_fan", "name": "Вытяжка", "kind": "fan",
            "entityId": "switch.demo_fan", "autoAdoptOverride": None,
        }]
        payload["switchBindings"] = [{
            "switchId": "sw_mirror", "triggerSubtype": "1_single", "action": "turn_on",
            "targets": {"lightTargets": ["light_main", "light_peer"] if multiple else ["light_main"],
                        "groupIds": [], "roles": []},
        }]
        if context_wait:
            payload["profile"] = "day_curve"
            payload["devices"]["power_switch"] = {
                "id": "power", "name": "Питание", "entityId": "switch.demo_power",
                "autoAdoptOverride": None,
            }
        return payload

    async def start(self, payload=None):
        self.hass.states.set("light.demo_peer", "off", last_changed=self.dt())
        self.hass.states.set("switch.demo_fan", "off", last_changed=self.dt())
        self.hass.states.set("switch.demo_power", "on", last_changed=self.dt())
        result = await super().start(payload or self.payload())
        device = result.payload["devices"]["wireless_switches"][0]["deviceId"]
        matching = [action for config, action, _ in self.triggers.attached
                    if config["device_id"] == device]
        self.case.assertEqual(1, len(matching))
        self.callback = matching[0]
        return result

    def listen_to_real_manual_events(self):
        from custom_components.hausman_hub import manual_light_authority_events as events
        from custom_components.hausman_hub import manual_light_off_protection_events as off_events

        live = self
        class ClockMeta(type):
            def __instancecheck__(cls, value):
                return isinstance(value, datetime)
        class ClockDate(metaclass=ClockMeta):
            @classmethod
            def now(cls, tz=None):
                return live.dt().astimezone(tz or timezone.utc)

        self.listener_patches = [
            patch.object(events, "time", SimpleNamespace(time=lambda: self.now / 1000)),
            patch.object(events, "datetime", ClockDate),
            patch.object(off_events, "datetime", ClockDate),
        ]
        for item in self.listener_patches:
            item.start()
        self.listener = events.ManualLightAuthorityEvents(
            self.hass, self.authority,
            lambda: (target.entity_id for config in self.runtime.configs()
                     for target in config.devices.light_targets),
            None, lambda: self.runtime._executor)
        self.listener.start()
        self.emitted = []
        service = self.hass.services.async_call

        async def dispatch(domain, action, data, **kwargs):
            entity = data["entity_id"]
            old = self.hass.states.get(entity)
            context = kwargs.get("context")
            for kind, body in (("call_service", {"domain": domain, "service": action, "service_data": data}),):
                self.emitted.append(kind)
                for name, callback in tuple(self.hass.bus.listeners):
                    if name == kind:
                        callback(SimpleNamespace(data=body, context=context, time_fired=self.dt()))
            await service(domain, action, data, **kwargs)
            self.emitted.append("state_changed")
            for name, callback in tuple(self.hass.bus.listeners):
                if name == "state_changed":
                    callback(SimpleNamespace(data={"entity_id": entity, "old_state": old,
                        "new_state": self.hass.states.get(entity)}, context=context, time_fired=self.dt()))
        self.hass.services.async_call = dispatch

    @staticmethod
    def cabinet_payload(enabled=True):
        payload = _ManualCallbackHarness.payload(enabled)
        payload["devices"]["light_targets"] = [{
            "id": "cabinet", "name": "Шкаф", "kind": "switch", "entityId": "switch.demo_cabinet",
            "role": "main", "groupId": None, "brightness": False, "color_temperature": False,
            "autoAdoptOverride": None,
        }]
        payload["devices"]["wireless_switches"][0].update(
            id="sw_shower_b2", triggerSubtypes=["toggle_b2_down"], deviceId="device_demo_shower_b2")
        payload["switchBindings"][0].update(switchId="sw_shower_b2", triggerSubtype="toggle_b2_down", action="toggle")
        payload["switchBindings"][0]["targets"]["lightTargets"] = ["cabinet"]
        payload["schedule"][0]["targets"]["lightTargets"] = ["cabinet"]
        payload["schedule"][0]["how"].update(brightness=None, colorTemperature=None)
        return payload

    async def press(self, callback=None):
        self.now += 1000
        await (callback or self.callback)({}, None)
        if self.hass.tasks:
            await asyncio.gather(*tuple(self.hass.tasks))

    async def change(self, **changes):
        payload = copy.deepcopy(self.saved)
        payload.update(changes)
        response = await self.put(payload)
        self.case.assertEqual(200, response.status, response.payload)
        self.saved = copy.deepcopy(response.payload)
        return response

    async def close(self):
        try:
            if hasattr(self, "listener"):
                self.listener.cancel()
                await asyncio.sleep(0)
            await super().close()
        finally:
            for item in reversed(getattr(self, "listener_patches", [])):
                item.stop()
            self.clock_patch.stop()


class RoomLightingApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.previous_modules = {
            name: sys.modules.get(name)
            for name in (*FAKE_MODULE_NAMES, PACKAGE_MODULE, API_MODULE)
        }
        for name in (*FAKE_MODULE_NAMES, PACKAGE_MODULE, API_MODULE):
            sys.modules.pop(name, None)
        sys.modules.update(fake_home_assistant_modules())
        importlib.import_module(PACKAGE_MODULE)
        cls.api = importlib.import_module(API_MODULE)

    @classmethod
    def tearDownClass(cls) -> None:
        for name in (*FAKE_MODULE_NAMES, PACKAGE_MODULE, API_MODULE):
            sys.modules.pop(name, None)
        sys.modules.update(
            {
                name: module
                for name, module in cls.previous_modules.items()
                if module is not None
            }
        )

    def setUp(self) -> None:
        from datetime import datetime, timezone

        from custom_components.hausman_hub.application.room_lighting_service import (
            RoomLightingService,
        )

        now = int(datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc).timestamp() * 1000)

        def context_factory(_config):
            return self._context(now)

        def live_context_factory(_stage):
            return self._context(now)

        self.hass = FakeHomeAssistant()
        self.store = _MemoryStore()
        self.hass.data["hausman_hub"] = {
            self.api.DATA_ROOM_LIGHTING_SERVICE: RoomLightingService(self.store),
            self.api.DATA_ROOM_LIGHTING_LIVE_TESTS: {},
            self.api.DATA_ROOM_LIGHTING_CONTEXT: context_factory,
            self.api.DATA_ROOM_LIGHTING_LIVE_CONTEXT: live_context_factory,
            self.api.DATA_ROOM_LIGHTING_LIVE_TEST_SLEEP: lambda _seconds: asyncio.sleep(0),
        }
        self.config_view = self.api.RoomLightingConfigView(self.hass)
        self.status_view = self.api.RoomLightingStatusView(self.hass)
        self.templates_view = self.api.RoomLightingTemplatesView(self.hass)
        self.apply_view = self.api.RoomLightingTemplateApplyView(self.hass)
        self.live_view = self.api.RoomLightingLiveTestsView(self.hass)
        self.live_result_view = self.api.RoomLightingLiveTestView(self.hass)

    def test_manual_callback_startup_controls_and_dedup(self):
        async def flow(enabled, action):
            live = _ManualCallbackHarness(self)
            try:
                payload = live.payload(enabled)
                payload["switchBindings"][0]["action"] = action
                await live.start(payload)
                await live.press()
                self.assertEqual(int(enabled), len(live.hass.services.calls))
                record = live.authority.record("light.demo_main")
                self.assertEqual("device_trigger", record.source)
                self.assertEqual("off" if action == "turn_off" else "on", record.action)
                owner = live.runtime._ownership.snapshots_for(payload["roomId"], ("light_main",))
                self.assertEqual("manual", owner[-1].source.value)
                if enabled:
                    self.assertTrue(live.results[-1].sent)
                    self.assertTrue(live.results[-1].confirmed)
                    self.assertEqual(live.dt(), live.hass.states.get("light.demo_main").last_changed)
                await live.callback({}, None)  # Exact duplicate, same clock/fingerprint.
                self.assertEqual(record, live.authority.record("light.demo_main"))
                self.assertEqual(int(enabled), len(live.hass.services.calls))
                self.assertFalse(any(call[2]["entity_id"] == "switch.demo_fan"
                                     for call in live.hass.services.calls))
            finally:
                await live.close()
        for enabled in (False, True):
            for action in ("turn_on", "turn_off"):
                with self.subTest(enabled=enabled, action=action):
                    asyncio.run(flow(enabled, action))

    def test_manual_callback_false_to_true_uses_actual_put(self):
        async def flow():
            live = _ManualCallbackHarness(self)
            try:
                await live.start(live.payload(False))
                await live.press()
                self.assertEqual([], live.hass.services.calls)
                await live.change(commandsEnabled=True)
                self.assertEqual([], live.hass.services.calls, "PUT must not replay a press")
                await live.press()
                self.assertEqual(1, len(live.hass.services.calls))
                self.assertTrue(live.results[-1].confirmed)
            finally:
                await live.close()
        asyncio.run(flow())

    def test_manual_callback_true_to_false_uses_actual_put(self):
        async def flow():
            live = _ManualCallbackHarness(self)
            try:
                await live.start()
                await live.change(commandsEnabled=False)
                self.assertEqual([], live.hass.services.calls)
                await live.press()
                self.assertEqual([], live.hass.services.calls)
                self.assertIsNotNone(live.authority.record("light.demo_main"))
            finally:
                await live.close()
        asyncio.run(flow())

    def test_manual_callback_binding_lifecycle_and_put_controls(self):
        async def flow(mode):
            live = _ManualCallbackHarness(self)
            try:
                await live.start()
                original = live.runtime._configs[live.saved["roomId"]]
                if mode == "binding":
                    bindings = copy.deepcopy(live.saved["switchBindings"])
                    bindings[0]["targets"]["lightTargets"] = ["light_peer"]
                    await live.change(switchBindings=bindings)
                elif mode == "device":
                    devices = copy.deepcopy(live.saved["devices"])
                    devices["wireless_switches"][0]["deviceId"] = "replacement_device"
                    await live.change(devices=devices)
                elif mode == "delete":
                    self.assertTrue(await live.service.async_delete_config(live.saved["roomId"]))
                elif mode == "restart":
                    await live.runtime.stop()
                    live.now += 1000
                    await live.runtime.start(live.hass, "new-run")
                elif mode == "conflict":
                    bad = copy.deepcopy(live.saved)
                    bad.update(version=original.version + 1, commandsEnabled=False)
                    self.assertEqual(409, (await live.put(bad)).status)
                elif mode == "save_failure":
                    live.backend.fail = True
                    bad = copy.deepcopy(live.saved)
                    bad["commandsEnabled"] = False
                    self.assertEqual(503, (await live.put(bad)).status)
                elif mode == "noop":
                    response = await live.change()
                    self.assertEqual(original.version, response.payload["version"])
                else:  # A version-only policy change may be used by a NEW press.
                    await live.change(name="Новое имя")
                await live.press()
                expected = int(mode in {"conflict", "save_failure", "noop", "version"})
                self.assertEqual(expected, len(live.hass.services.calls))
                if mode == "restart":
                    await live.press(live.triggers.attached[-1][1])
                    self.assertEqual(1, len(live.hass.services.calls))
                if expected:
                    self.assertTrue(live.results[-1].confirmed)
            finally:
                await live.close()
        for mode in ("binding", "device", "delete", "restart", "conflict", "save_failure", "noop", "version"):
            with self.subTest(mode=mode):
                asyncio.run(flow(mode))

    def test_manual_callback_wait_boundaries_and_aba(self):
        async def flow(mode, stage):
            live = _ManualCallbackHarness(self)
            release, entered = asyncio.Event(), asyncio.Event()
            task = None
            gate_entries = 0
            delegated_context_calls = 0
            try:
                if stage == "context":
                    live.hass.states.set("light.demo_main", "on", {"brightness": 0}, last_changed=live.dt())
                await asyncio.wait_for(live.start(live.payload(context_wait=stage == "context")), 2)
                old_callback = live.callback
                old_epoch = live.runtime._return_intent_epoch
                attached_count = len(live.triggers.attached)
                if stage == "context":
                    original = live.runtime._state_provider.build_context
                    async def gate(*args, **kwargs):
                        nonlocal gate_entries, delegated_context_calls
                        if asyncio.current_task() is task and gate_entries == 0:
                            gate_entries += 1
                            entered.set()
                            await release.wait()
                        else:
                            delegated_context_calls += 1
                        return await original(*args, **kwargs)
                    live.runtime._state_provider.build_context = gate
                else:
                    original = live.runtime._executor._call
                    async def gate(*args, **kwargs):
                        nonlocal gate_entries
                        if asyncio.current_task() is task and gate_entries == 0:
                            gate_entries += 1
                            entered.set()
                            await release.wait()
                        return await original(*args, **kwargs)
                    live.runtime._executor._call = gate
                self.assertEqual([], live.hass.services.calls)
                task = asyncio.create_task(live.press())
                await asyncio.wait_for(entered.wait(), 2)
                self.assertEqual(1, gate_entries)
                self.assertEqual([], live.hass.services.calls)
                if mode == "control":
                    release.set()
                    await asyncio.wait_for(task, 2)
                    self.assertEqual(1, len(live.hass.services.calls))
                    domain, service, data = live.hass.services.calls[0]
                    self.assertEqual(("light", "turn_on", "light.demo_main"),
                                     (domain, service, data["entity_id"]))
                    if stage == "context":
                        self.assertGreater(data["brightness_pct"], 0)
                    self.assertEqual(1, len(live.results))
                    self.assertIs(True, live.results[0].sent)
                    self.assertIs(True, live.results[0].confirmed)
                    owner = live.runtime._ownership.snapshots_for(live.saved["roomId"], ("light_main",))[0]
                    self.assertEqual("manual", owner.source.value)
                    return
                newer = None
                if mode in {"false", "aba"}:
                    await asyncio.wait_for(live.change(commandsEnabled=False), 2)
                    if mode == "aba":
                        await asyncio.wait_for(live.change(commandsEnabled=True), 2)
                elif mode == "version":
                    await asyncio.wait_for(live.change(name="Изменено во время ожидания"), 2)
                elif mode == "stop":
                    await asyncio.wait_for(live.runtime.stop(), 2)
                elif mode == "restart":
                    await asyncio.wait_for(live.runtime.stop(), 2)
                    live.now += 1000
                    before_start_context_calls = delegated_context_calls
                    await asyncio.wait_for(live.runtime.start(live.hass, "new-run"), 2)
                    self.assertNotEqual(old_epoch, live.runtime._return_intent_epoch)
                    self.assertTrue(live.runtime._running)
                    self.assertGreater(len(live.triggers.attached), attached_count)
                    new_callback = live.triggers.attached[-1][1]
                    self.assertIsNot(old_callback, new_callback)
                    self.assertFalse(task.done())
                    self.assertEqual([], live.hass.services.calls)
                    if stage == "context":
                        self.assertGreater(delegated_context_calls, before_start_context_calls)
                elif mode == "reserved":
                    live.runtime._reserved_entity_ids_provider = lambda: ("light.demo_main",)
                elif mode == "dependencies":
                    live.runtime._power_dependency_resolver = lambda: {}
                else:
                    entity = "light.demo_peer" if mode == "related" else "light.demo_main"
                    live.now += 1
                    live.authority.capture(entity, "off", "physical", "newer-intervention",
                        observed_at_ms=live.now, duration_ms=7200000)
                    newer = live.authority.record(entity)
                release.set()
                await asyncio.wait_for(task, 2)
                self.assertEqual(1, gate_entries)
                self.assertEqual([], live.hass.services.calls)
                if newer is not None:
                    self.assertEqual(newer, live.authority.record(newer.entity_id))
                if mode == "aba":
                    await asyncio.wait_for(live.press(), 2)
                    self.assertEqual(1, len(live.hass.services.calls))
                if mode == "restart":
                    await asyncio.wait_for(live.press(new_callback), 2)
                    self.assertEqual(1, len(live.hass.services.calls))
                    self.assertEqual("light.demo_main", live.hass.services.calls[0][2]["entity_id"])
                    self.assertIs(True, live.results[-1].sent)
                    self.assertIs(True, live.results[-1].confirmed)
            finally:
                release.set()
                if task is not None:
                    if not task.done():
                        task.cancel()
                    await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 2)
                await asyncio.wait_for(live.close(), 2)
        for stage in ("executor", "context"):
            for mode in ("control", "false", "aba", "version", "stop", "restart", "manual", "related", "reserved", "dependencies"):
                with self.subTest(stage=stage, mode=mode):
                    asyncio.run(flow(mode, stage))

    def test_manual_callback_multitarget_late_send_truth(self):
        async def flow(phase, report):
            live = _ManualCallbackHarness(self)
            entered, release = asyncio.Event(), asyncio.Event()
            task = None
            try:
                await live.start(live.payload(multiple=True))
                if phase == "transport":
                    original = live.hass.services.async_call
                    async def gate(*args, **kwargs):
                        if report:
                            await original(*args, **kwargs)
                        else:
                            live.hass.services.calls.append((args[0], args[1], dict(args[2])))
                        entered.set()
                        await release.wait()
                    live.hass.services.async_call = gate
                else:
                    original = live.runtime._executor.execute
                    async def gate(*args, **kwargs):
                        result = await original(*args, **kwargs)
                        entered.set()
                        await release.wait()
                        return result
                    live.runtime._executor.execute = gate
                task = asyncio.create_task(live.press())
                await asyncio.wait_for(entered.wait(), 2)
                await live.change(commandsEnabled=False)
                release.set()
                await asyncio.wait_for(task, 2)
                self.assertEqual(1, len(live.hass.services.calls))
                self.assertEqual("light.demo_main", live.hass.services.calls[0][2]["entity_id"])
                self.assertEqual(1, len(live.results))
                self.assertTrue(live.results[0].sent)
                self.assertEqual("sent", live.results[0].send_outcome)
                self.assertIs(phase == "receipt", live.results[0].confirmed)
                owners = live.runtime._ownership.snapshots_for(live.saved["roomId"], ("light_main", "light_peer"))
                self.assertTrue(all(owner.source.value == "manual" for owner in owners))
            finally:
                release.set()
                if task is not None:
                    await asyncio.gather(task, return_exceptions=True)
                await live.close()
        for phase, report in (("transport", False), ("transport", True), ("receipt", True)):
            with self.subTest(phase=phase, report=report):
                asyncio.run(flow(phase, report))

    def test_manual_callback_quiet_away_other_room_and_unsupported_executor(self):
        async def flow(mode):
            live = _ManualCallbackHarness(self)
            try:
                payload = live.payload()
                if mode == "quiet":
                    payload["quietHours"] = {"start": "09:00", "end": "11:00"}
                if mode == "away":
                    live.hass.states.set("binary_sensor.a100_away", "on", last_changed=live.dt())
                await live.start(payload)
                unsupported_calls = []
                if mode == "unsupported":
                    class Unsupported:
                        async def execute(self, hass, command):
                            unsupported_calls.append("execute")
                            raise AssertionError("unguarded executor must never be invoked")
                        async def execute_manual(self, hass, command):
                            unsupported_calls.append("execute_manual")
                            raise AssertionError("manual fallback must never be invoked")
                    live.runtime._executor = Unsupported()
                if mode == "other_room":
                    other = live.payload(False)
                    other.update(roomId="room_other", name="Другая комната")
                    other["devices"]["sensors"][0]["entityId"] = "binary_sensor.other_presence"
                    for target in other["devices"]["light_targets"]:
                        target["entityId"] = target["entityId"].replace("demo", "other")
                    other["devices"]["auxiliaries"][0]["entityId"] = "switch.other_fan"
                    other["devices"]["wireless_switches"][0]["deviceId"] = "device_other"
                    self.assertEqual(200, (await live.put(other)).status)
                    await live.runtime.stop()
                    live.now += 1000
                    await live.runtime.start(live.hass, "two-rooms")
                    before = tuple(live.runtime._ownership.snapshots_for("room_other", ("light_main",)))
                    await live.change(name="Только первая комната")
                    callback = next(action for config, action, _info in live.triggers.attached[1:]
                                    if config["device_id"] == "device_demo_mirror")
                    await live.press(callback)
                    self.assertEqual(before, tuple(live.runtime._ownership.snapshots_for("room_other", ("light_main",))))
                    other_callback = next(action for config, action, _info in live.triggers.attached[1:]
                                          if config["device_id"] == "device_other")
                    await live.press(other_callback)
                else:
                    await live.press()
                self.assertEqual(0 if mode == "unsupported" else 1, len(live.hass.services.calls))
                self.assertEqual([], unsupported_calls)
                self.assertEqual([] if mode == "unsupported" else ["light.demo_main"],
                    [call[2]["entity_id"] for call in live.hass.services.calls])
                if mode in {"quiet", "away"}:
                    await live.runtime.async_process()
                    self.assertEqual(1, len(live.hass.services.calls), "manual ownership must still block automation")
            finally:
                await live.close()
        for mode in ("quiet", "away", "other_room", "unsupported"):
            with self.subTest(mode=mode):
                asyncio.run(flow(mode))

    def test_manual_callback_exact_cabinet_listener_controls(self):
        async def flow(mode):
            live = _ManualCallbackHarness(self)
            try:
                enabled = mode != "false"
                live.hass.states.set("switch.demo_cabinet", "off", last_changed=live.dt())
                await live.start(live.cabinet_payload(enabled))
                live.listen_to_real_manual_events()
                self.assertEqual(2, len(live.hass.bus.listeners))
                self.assertEqual("toggle_b2_down", live.triggers.attached[0][0]["subtype"])
                await live.press()
                self.assertEqual(int(enabled), len(live.hass.services.calls))
                first = live.authority.record("switch.demo_cabinet")
                self.assertEqual("device_trigger", first.source)
                self.assertEqual(1, first.generation, "own service/report must not create another manual action")
                await live.callback({}, None)
                self.assertEqual(first, live.authority.record("switch.demo_cabinet"))
                self.assertEqual(int(enabled), len(live.hass.services.calls))
                if enabled:
                    self.assertTrue(live.results[-1].confirmed)
                    await live.press()
                    self.assertEqual(["turn_on", "turn_off"], [call[1] for call in live.hass.services.calls])
                    self.assertTrue(live.results[-1].confirmed)
                    self.assertEqual(2, live.authority.record("switch.demo_cabinet").generation)
                    self.assertEqual(["call_service", "state_changed"] * 2, live.emitted)
                self.assertTrue(all(call[2]["entity_id"] == "switch.demo_cabinet" for call in live.hass.services.calls))
                self.assertTrue(all(record.source.value == "manual" for record in
                    live.runtime._ownership.snapshots_for(live.saved["roomId"], ("cabinet",))))
            finally:
                await live.close()
        for mode in ("true", "false"):
            with self.subTest(mode=mode):
                asyncio.run(flow(mode))

    def test_manual_callback_exact_cabinet_false_to_true(self):
        self._manual_callback_cabinet_transition(False)

    def test_manual_callback_exact_cabinet_true_to_false(self):
        self._manual_callback_cabinet_transition(True)

    def _manual_callback_cabinet_transition(self, enabled):
        async def flow():
            live = _ManualCallbackHarness(self)
            try:
                live.hass.states.set("switch.demo_cabinet", "off", last_changed=live.dt())
                await live.start(live.cabinet_payload(enabled))
                live.listen_to_real_manual_events()
                await live.change(commandsEnabled=not enabled)
                self.assertEqual([], live.hass.services.calls, "PUT cannot replay")
                await live.press()
                self.assertEqual([] if enabled else [("switch", "turn_on", {"entity_id": "switch.demo_cabinet"})],
                    live.hass.services.calls)
                record = live.authority.record("switch.demo_cabinet")
                self.assertEqual(("device_trigger", 1), (record.source, record.generation))
                if not enabled:
                    self.assertTrue(live.results[-1].confirmed)
                    self.assertEqual(["call_service", "state_changed"], live.emitted)
            finally:
                await live.close()
        asyncio.run(flow())

    def test_manual_callback_same_local_ids_both_room_orders(self):
        async def flow(reverse, action):
            live = _ManualCallbackHarness(self)
            try:
                payload = live.payload()
                if reverse:
                    payload["roomId"] = "room_z_primary"
                payload["switchBindings"][0]["action"] = action
                other = copy.deepcopy(payload)
                other.update(roomId="room_other", name="Другая комната")
                other["devices"]["sensors"][0]["entityId"] = "binary_sensor.other_presence"
                for target in other["devices"]["light_targets"]:
                    target["entityId"] = target["entityId"].replace("demo", "other")
                    live.hass.states.set(target["entityId"], "off", last_changed=live.dt())
                other["devices"]["auxiliaries"][0]["entityId"] = "switch.other_fan"
                other["devices"]["wireless_switches"][0]["deviceId"] = "device_other"
                if reverse:
                    self.assertEqual(200, (await live.put(other)).status)
                    await live.start(payload)
                else:
                    await live.start(payload)
                    self.assertEqual(200, (await live.put(other)).status)
                    await live.runtime.stop()
                    live.now += 1000
                    await live.runtime.start(live.hass, "both")
                self.assertEqual([], live.hass.services.calls)
                self.assertEqual(["room_other", "room_z_primary"] if reverse else ["room_demo_entry", "room_other"],
                    [config.room_id for config in live.runtime.configs()])
                for device, entity in (("device_demo_mirror", "light.demo_main"), ("device_other", "light.other_main")):
                    callback = next(action for config, action, _ in reversed(live.triggers.attached)
                                    if config["device_id"] == device)
                    before = len(live.hass.services.calls)
                    await live.press(callback)
                    self.assertEqual([( "light", action, {"entity_id": entity})], live.hass.services.calls[before:])
                    self.assertEqual("light_main", live.results[-1].target_id)
                    self.assertTrue(live.results[-1].confirmed)
            finally:
                await live.close()
        for reverse in (False, True):
            for action in ("turn_on", "turn_off"):
                with self.subTest(reverse=reverse, action=action):
                    asyncio.run(flow(reverse, action))

    def test_manual_callback_declared_but_ignored_guard_is_not_trusted(self):
        async def flow():
            live = _ManualCallbackHarness(self)
            calls = []
            class IgnoresGuard:
                async def execute(self, hass, command, *, automatic=True, dispatch_guard=None):
                    calls.append(command)
                    return {"confirmed": True}
            try:
                await live.start()
                live.runtime._executor = IgnoresGuard()
                await live.press()
                self.assertEqual([], calls)
                self.assertEqual([], live.hass.services.calls)
            finally:
                await live.close()
        asyncio.run(flow())

    def test_manual_callback_room_scope_automatic_and_auxiliary_paths(self):
        from custom_components.hausman_hub.domain.room_lighting_engine import PlannedCommand, LightAction

        async def flow(reverse, path):
            live = _ManualCallbackHarness(self)
            try:
                main = live.payload()
                if reverse:
                    main["roomId"] = "room_z_primary"
                other = copy.deepcopy(main)
                other.update(roomId="room_other", name="Другая комната")
                other["devices"]["sensors"][0]["entityId"] = "binary_sensor.other_presence"
                for item in other["devices"]["light_targets"]:
                    item["entityId"] = item["entityId"].replace("demo", "other")
                    live.hass.states.set(item["entityId"], "off", last_changed=live.dt())
                other["devices"]["auxiliaries"][0]["entityId"] = "switch.other_fan"
                other["devices"]["wireless_switches"][0]["deviceId"] = "device_other"
                live.hass.states.set("binary_sensor.other_presence", "off", last_changed=live.dt())
                live.hass.states.set("switch.other_fan", "off", last_changed=live.dt())
                if reverse:
                    self.assertEqual(200, (await live.put(other)).status)
                    await live.start(main)
                else:
                    await live.start(main)
                    self.assertEqual(200, (await live.put(other)).status)
                    await live.runtime.stop()
                    live.now += 1000
                    await live.runtime.start(live.hass, "two-auto")
                before = live.runtime._ownership.to_payload()
                self.assertEqual(["room_other", "room_z_primary"] if reverse else ["room_demo_entry", "room_other"],
                    [config.room_id for config in live.runtime.configs()])
                for room, prefix in ((main["roomId"], "demo"), (other["roomId"], "other")):
                    index = len(live.hass.services.calls)
                    if path == "automatic":
                        await live.state_event(f"binary_sensor.{prefix}_presence", "on")
                        self.assertEqual([f"light.{prefix}_main"],
                            [call[2]["entity_id"] for call in live.hass.services.calls[index:]])
                    else:
                        result = await live.runtime.async_execute_command(live.runtime._configs[room],
                            PlannedCommand("aux_fan", LightAction.TURN_ON))
                        self.assertTrue(result["sent"])
                        self.assertEqual([f"switch.{prefix}_fan"],
                            [call[2]["entity_id"] for call in live.hass.services.calls[index:]])
                if path == "auxiliary":
                    self.assertEqual(before, live.runtime._ownership.to_payload())
            finally:
                await live.close()
        for reverse in (False, True):
            for path in ("automatic", "auxiliary"):
                with self.subTest(reverse=reverse, path=path):
                    asyncio.run(flow(reverse, path))

    def test_manual_callback_store_read_fences(self):
        async def flow(mode):
            live = _ManualCallbackHarness(self)
            release, entered = asyncio.Event(), asyncio.Event()
            task = None
            try:
                await live.start()
                read = live.store.async_get
                async def gate(room):
                    result = await read(room)
                    entered.set()
                    await release.wait()
                    if mode == "failure":
                        raise OSError("configuration read failed")
                    return result
                live.store.async_get = gate
                task = asyncio.create_task(live.press())
                await asyncio.wait_for(entered.wait(), 2)
                live.store.async_get = read
                newer = None
                if mode == "change":
                    await live.change(commandsEnabled=False)
                elif mode == "delete":
                    await live.service.async_delete_config(live.saved["roomId"])
                elif mode in {"stop", "restart"}:
                    await live.runtime.stop()
                    if mode == "restart":
                        live.now += 1
                        await live.runtime.start(live.hass, "after-read")
                elif mode == "manual":
                    live.authority.capture("light.demo_main", "off", "physical", "during-read",
                        observed_at_ms=live.now, duration_ms=7200000)
                    newer = live.authority.record("light.demo_main")
                release.set()
                await asyncio.wait_for(task, 2)
                self.assertEqual(1 if mode == "control" else 0, len(live.hass.services.calls))
                if newer:
                    self.assertEqual(newer, live.authority.record("light.demo_main"))
            finally:
                release.set()
                if task is not None:
                    await asyncio.gather(task, return_exceptions=True)
                await live.close()
        for mode in ("control", "change", "delete", "stop", "restart", "manual", "failure"):
            with self.subTest(mode=mode):
                asyncio.run(flow(mode))

    def test_explicit_on_return_projects_active_delegation_as_auto(self) -> None:
        from copy import deepcopy
        from tests.test_room_lighting_force_auto import make_case, _ROOM_ID
        from custom_components.hausman_hub.domain.room_lighting_ownership import has_proven_auto_ownership
        from custom_components.hausman_hub.domain.room_lighting import config_from_payload
        from custom_components.hausman_hub.application.room_lighting_storage import HomeAssistantRoomLightingStore
        from custom_components.hausman_hub.application.room_lighting_service import RoomLightingService

        async def flow():
            case = await make_case("on")
            try:
                config = case.runtime._configs[_ROOM_ID]

                class Backend:
                    async def async_load(self):
                        return {"rooms": [deepcopy(config.to_dict())]}

                # Use the production storage parser and service. Only HA's
                # persistence backend is in memory; each read rebuilds objects.
                store = object.__new__(HomeAssistantRoomLightingStore)
                store._store = Backend()
                service = RoomLightingService(store)
                case.runtime._service = service
                result = await case.runtime.async_force_auto(_ROOM_ID, reason="owner")
                self.assertEqual("returned", result["outcome"])
                copies = (deepcopy(config), config_from_payload(config.to_dict()),
                          await service.async_get_config(_ROOM_ID))
                for loaded in copies:
                    self.assertEqual(config, loaded)
                    self.assertIsNot(config, loaded)
                    context = await self.api._status_context(
                        {self.api.DATA_ROOM_LIGHTING_RUNTIME: case.runtime}, loaded)
                    payload = self.api._status_payload(loaded, context, fresh=True)
                    self.assertEqual("auto", payload["sources"][0]["ownership"])
                    self.assertFalse(has_proven_auto_ownership(context.ownership, "light_main"))
                    _validate("room-lighting-status.schema.json", payload)
                self.assertEqual([], case.executor.calls)
            finally:
                await case.runtime.stop()

        asyncio.run(flow())

    def test_status_never_canonicalizes_different_settings(self) -> None:
        from dataclasses import replace
        from tests.test_room_lighting_force_auto import make_case, _ROOM_ID

        async def flow():
            case = await make_case("on")
            try:
                config = case.runtime._configs[_ROOM_ID]
                target = config.devices.light_targets[0]
                alternatives = (
                    replace(config, room_id="another_room"),
                    replace(config, version=config.version + 1),
                    replace(config, updated_at=config.updated_at + 1),
                    replace(config, name=config.name + " changed"),
                    replace(config, commands_enabled=not config.commands_enabled),
                    replace(config, profile="day_curve"),
                    replace(config, schedule=()),
                    replace(config, auto_adopt=not config.auto_adopt),
                    replace(config, devices=replace(config.devices, light_targets=(
                        replace(target, entity_id="light.other"),))),
                )
                seen = []

                async def build(candidate):
                    seen.append(candidate)
                    return None

                case.runtime._build_context = build
                for other in alternatives:
                    self.assertNotEqual(config, other)
                    await case.runtime.async_context_for(other)
                    self.assertIs(other, seen[-1])
                self.assertEqual([], case.executor.calls)
            finally:
                await case.runtime.stop()

        asyncio.run(flow())

    @staticmethod
    def _context(now: int):
        from datetime import time, timezone

        from custom_components.hausman_hub.domain.room_lighting import SensorKind
        from custom_components.hausman_hub.domain.room_lighting_engine import (
            LightSnapshot,
            RoomLightingContext,
            SensorSnapshot,
        )
        from custom_components.hausman_hub.domain.room_lighting_ownership import (
            SensorState,
        )

        return RoomLightingContext(
            now=now,
            timezone=timezone.utc,
            sunrise=time(7, 0),
            sunset=time(19, 0),
            sensors=(
                SensorSnapshot(
                    "sensor_demo_presence", SensorKind.PRESENCE, SensorState.ON, now
                ),
            ),
            lights=(LightSnapshot("light_main", SensorState.OFF, now),),
        )

    def _config_path(self, room_id: str = "room_demo_entry") -> str:
        return self.api.ROOM_LIGHTING_CONFIG_PATH.format(room_id=room_id)

    def test_force_auto_returns_real_coordinated_receipt(self) -> None:
        from tests.test_room_lighting_force_auto import make_case, _ROOM_ID

        async def flow() -> None:
            for mode in ("on", "off", "blocked", "incomplete"):
                case = await make_case("off" if mode != "on" else "on", protected=True)
                try:
                    self.store.configs[_ROOM_ID] = case.runtime._configs[_ROOM_ID]
                    self.hass.data["hausman_hub"][self.api.DATA_ROOM_LIGHTING_RUNTIME] = case.runtime
                    if mode == "blocked":
                        case.hass.states.set("binary_sensor.demo_presence", "unknown")
                    elif mode == "incomplete":
                        async def fail_save(_payload):
                            raise OSError("test save failure")
                        case.ownership_store.hook = fail_save
                    path = self.api.ROOM_LIGHTING_FORCE_AUTO_PATH.format(room_id=_ROOM_ID)
                    request = _json_request(path, {}, room_id=_ROOM_ID)
                    request.query = {}
                    response = await self.api.RoomLightingForceAutoView(self.hass).post(request)
                    self.assertEqual(200, response.status)
                    body = response.payload
                    self.assertEqual({"name": "hausman-hub-room-lighting-force-auto", "version": 1}, body["contract"])
                    self.assertEqual(_ROOM_ID, body["room"])
                    self.assertTrue(body["known"])
                    expected = mode if mode in ("blocked", "incomplete") else "returned"
                    self.assertEqual(expected, body["outcome"])
                    self.assertEqual(expected != "returned", case.authority.blocks("light.demo_main"))
                    self.assertFalse(case.executor.calls)
                finally:
                    await case.runtime.stop()
        asyncio.run(flow())

    def test_guarded_return_uses_original_intent_across_http_body_wait(self) -> None:
        from tests.test_guarded_lighting_return import case_with_intent, new_manual, drain_events
        from tests.test_room_lighting_force_auto import _ROOM_ID

        async def flow() -> None:
            for action in (None, "on", "off", "brightness", "color"):
                case = await case_with_intent()
                try:
                    self.hass.data["hausman_hub"][self.api.DATA_ROOM_LIGHTING_RUNTIME] = case.runtime
                    path = self.api.ROOM_LIGHTING_RETURN_INTENT_PATH.format(room_id=_ROOM_ID)
                    request = _request(path, room_id=_ROOM_ID)
                    request["hass_user"].id = "fixture-admin"
                    before = case.store.saves
                    response = await self.api.RoomLightingReturnIntentView(self.hass).get(request)
                    self.assertEqual(200, response.status)
                    self.assertEqual(before, case.store.saves)
                    self.assertEqual("no-store", response.headers["Cache-Control"])
                    _validate("room-lighting-return-intent.schema.json", response.payload)
                    token = response.payload["intentToken"]
                    path = self.api.ROOM_LIGHTING_GUARDED_RETURN_PATH.format(room_id=_ROOM_ID)
                    request = _json_request(path, {"intentToken": token}, room_id=_ROOM_ID)
                    request["hass_user"].id = "fixture-admin"
                    original_json = request.json

                    async def body():
                        if action:
                            new_manual(case, action)
                            await drain_events(case)
                        return await original_json()

                    request.json = body
                    view = self.api.RoomLightingGuardedReturnView(self.hass)
                    response = await view.post(request)
                    self.assertEqual(200, response.status)
                    self.assertEqual("no-store", response.headers["Cache-Control"])
                    _validate("room-lighting-guarded-return.schema.json", response.payload)
                    self.assertEqual("blocked" if action else "returned", response.payload["outcome"])
                    self.assertFalse(case.executor.calls)
                    if action:
                        self.assertIn("new-" + action, case.authority.record("light.demo_main").fingerprint)
                        self.assertTrue(case.authority.blocks("light.demo_main"))
                    request.json = original_json
                    replay = await view.post(request)
                    self.assertEqual("blocked", replay.payload["outcome"])
                finally:
                    await case.runtime.stop()
        asyncio.run(flow())

    def test_guarded_routes_reject_unauthorized_malformed_and_missing_runtime(self) -> None:
        async def flow() -> None:
            calls = []
            def forbidden_call(*args, **kwargs):
                calls.append((args, kwargs))
                raise AssertionError("rejected request reached runtime")
            self.hass.data["hausman_hub"][self.api.DATA_ROOM_LIGHTING_RUNTIME] = SimpleNamespace(
                issue_force_auto_intent=forbidden_call, async_force_auto_guarded=forbidden_call)
            for view_type, constant, method in (
                (self.api.RoomLightingReturnIntentView, self.api.ROOM_LIGHTING_RETURN_INTENT_PATH, "get"),
                (self.api.RoomLightingGuardedReturnView, self.api.ROOM_LIGHTING_GUARDED_RETURN_PATH, "post"),
            ):
                path = constant.format(room_id="room_demo_entry")
                for rejection in ("no-id", "reader", "external", "query", "wrong-path"):
                    request = _json_request(path, {"intentToken": "0" * 64}, room_id="room_demo_entry")
                    if rejection != "no-id": request["hass_user"].id = "fixture-admin"
                    if rejection == "reader": request["hass_user"].is_admin = False
                    elif rejection == "external": request.remote = "8.8.8.8"
                    elif rejection == "query": request.query_string = "retry=1"
                    elif rejection == "wrong-path": request.path += "/"
                    result = await getattr(view_type(self.hass), method)(request)
                    self.assertEqual(404 if rejection in ("query", "wrong-path") else 403, result.status)
            path = self.api.ROOM_LIGHTING_GUARDED_RETURN_PATH.format(room_id="room_demo_entry")
            for payload in ({}, {"intentToken": None}, {"intentToken": True}, {"intentToken": "x" * 64},
                            {"intentToken": "0" * 64, "retry": True}, [], "token"):
                request = _json_request(path, payload, room_id="room_demo_entry")
                request["hass_user"].id = "fixture-admin"
                self.assertEqual(400, (await self.api.RoomLightingGuardedReturnView(self.hass).post(request)).status)
            self.assertFalse(calls)
            self.hass.data["hausman_hub"][self.api.DATA_ROOM_LIGHTING_RUNTIME] = None
            request = _json_request(path, {"intentToken": "0" * 64}, room_id="room_demo_entry")
            request["hass_user"].id = "fixture-admin"
            self.assertEqual(503, (await self.api.RoomLightingGuardedReturnView(self.hass).post(request)).status)
        asyncio.run(flow())

    def test_force_auto_rejects_unauthorized_or_unknown_without_runtime_call(self) -> None:
        async def flow() -> None:
            calls = []
            async def force(*args, **kwargs):
                calls.append((args, kwargs))
                raise AssertionError("rejected request reached runtime")
            self.hass.data["hausman_hub"][self.api.DATA_ROOM_LIGHTING_RUNTIME] = SimpleNamespace(async_force_auto=force)
            view = self.api.RoomLightingForceAutoView(self.hass)
            path = self.api.ROOM_LIGHTING_FORCE_AUTO_PATH.format(room_id="room_demo_entry")
            request = _json_request(path, {}, room_id="room_demo_entry")
            request.query = {}
            self.assertEqual(404, (await view.post(request)).status)
            request["hass_user"] = reader_user("reader", admin=False)
            self.assertEqual(403, (await view.post(request)).status)
            self.assertFalse(calls)
        asyncio.run(flow())

    def test_config_round_trip_and_revision_conflict(self) -> None:
        async def flow() -> None:
            room_id = "room_demo_entry"
            missing = await self.config_view.get(
                _request(self._config_path(room_id), room_id=room_id)
            )
            self.assertEqual(404, missing.status)

            put = await self.config_view.put(
                _json_request(
                    self._config_path(room_id), _config_payload(room_id), room_id=room_id
                )
            )
            self.assertEqual(200, put.status)
            _validate("room-lighting-config.schema.json", put.payload)
            self.assertEqual(1, put.payload["version"])

            got = await self.config_view.get(
                _request(self._config_path(room_id), room_id=room_id)
            )
            self.assertEqual(200, got.status)
            _validate("room-lighting-config.schema.json", got.payload)
            self.assertEqual("Тамбур", got.payload["name"])

            changed = _config_payload(room_id, name="Другое")
            changed["version"] = 1
            updated = await self.config_view.put(
                _json_request(self._config_path(room_id), changed, room_id=room_id)
            )
            self.assertEqual(2, updated.payload["version"])

            conflict = _config_payload(room_id, name="Старое")
            conflict["version"] = 1
            stale = await self.config_view.put(
                _json_request(self._config_path(room_id), conflict, room_id=room_id)
            )
            self.assertEqual(409, stale.status)
            self.assertEqual("revision_conflict", stale.payload["code"])

        asyncio.run(flow())

    def test_durable_config_put_applies_the_saved_version_to_runtime_before_response(self) -> None:
        async def flow() -> None:
            payload = _config_payload()
            payload["commandsEnabled"] = True
            created = await self.config_view.put(
                _json_request(self._config_path(), payload, room_id=payload["roomId"])
            )
            self.assertEqual(200, created.status)
            applied: list[object] = []
            self.hass.data["hausman_hub"][self.api.DATA_ROOM_LIGHTING_RUNTIME] = SimpleNamespace(
                apply_config=applied.append
            )
            changed = dict(created.payload)
            changed["commandsEnabled"] = False
            response = await self.config_view.put(
                _json_request(self._config_path(), changed, room_id=changed["roomId"])
            )
            self.assertEqual(200, response.status)
            self.assertEqual(2, response.payload["version"])
            self.assertEqual(1, len(applied))
            self.assertEqual(2, applied[0].version)
            self.assertFalse(applied[0].commands_enabled)
        asyncio.run(flow())

    def test_put_false_updates_a_running_real_runtime_without_a_restart(self) -> None:
        async def flow() -> None:
            live = _LiveConfigHarness(self)
            try:
                created = await live.start()
                changed = copy.deepcopy(created.payload)
                changed["commandsEnabled"] = False
                response = await live.put(changed)
                # Exercise the event before asserting the active map: the old
                # implementation must prove a real unwanted service call.
                active = live.runtime._configs[changed["roomId"]]
                await live.presence()
                self.assertEqual(200, response.status)
                self.assertEqual(2, response.payload["version"])
                self.assertEqual([], live.hass.services.calls,
                                 f"active={active.to_dict()} receipts={live.receipts()}")
                self.assertFalse(active.commands_enabled)
                self.assertEqual([], live.receipts())
            finally:
                await live.close()
        asyncio.run(flow())

    def test_live_config_harness_positive_control_sends_and_confirms(self) -> None:
        async def flow():
            live = _LiveConfigHarness(self)
            try:
                await live.start()
                await live.presence()
                self.assertEqual(1, len(live.hass.services.calls))
                self.assertEqual(("light", "turn_on"), live.hass.services.calls[0][:2])
                receipts = live.receipts()
                self.assertTrue(receipts)
                self.assertTrue(receipts[-1]["sent"])
                self.assertTrue(receipts[-1]["confirmed"])
            finally:
                await live.close()
        asyncio.run(flow())

    def test_conflict_and_storage_failure_do_not_apply_an_unsaved_config(self) -> None:
        async def flow() -> None:
            live = _LiveConfigHarness(self)
            try:
                created = await live.start()
                v2 = copy.deepcopy(created.payload)
                v2["name"] = "Версия два"
                saved_v2 = await live.put(v2)
                self.assertEqual(200, saved_v2.status)
                active = live.runtime._configs[v2["roomId"]]
                document = copy.deepcopy(live.backend.payload)
                saves = live.backend.saves
                generation = live.service.configuration_generation
                stale = copy.deepcopy(created.payload)
                stale["commandsEnabled"] = False
                conflict = await live.put(stale)
                self.assertEqual(409, conflict.status)
                self.assertEqual(saves, live.backend.saves)
                self.assertEqual(generation, live.service.configuration_generation)
                self.assertIs(active, live.runtime._configs[v2["roomId"]])
                self.assertEqual(document, live.backend.payload)

                failed = copy.deepcopy(saved_v2.payload)
                failed["commandsEnabled"] = False
                live.backend.fail = True
                response = await live.put(failed)
                self.assertEqual(503, response.status)
                self.assertIs(active, live.runtime._configs[v2["roomId"]])
                self.assertEqual(document, live.backend.payload)
                self.assertEqual([], live.hass.services.calls)
                live.backend.fail = False
                recovered = await live.put(failed)
                self.assertEqual(200, recovered.status)
                self.assertEqual(3, recovered.payload["version"])
                await live.presence()
                self.assertEqual([], live.hass.services.calls)
                self.assertFalse(live.runtime._configs[v2["roomId"]].commands_enabled)
                self.assertFalse((await live.store.async_get(v2["roomId"])).commands_enabled)
            finally:
                await live.close()
        asyncio.run(flow())

    def test_absence_off_pending_and_context_wait_keep_all_final_guards(self) -> None:
        async def flow(boundary, mode):
            from custom_components.hausman_hub.application import manual_light_authority as authority_module
            from custom_components.hausman_hub.application import room_lighting_runtime as runtime_module
            from custom_components.hausman_hub.application.manual_light_authority import ManualLightAuthority
            from custom_components.hausman_hub.application.related_light_sources import RelatedLightSources
            from custom_components.hausman_hub.application.room_lighting_shadow import RoomLightingShadowService

            live = _LiveConfigHarness(self)
            entered, release = asyncio.Event(), asyncio.Event()
            process = None
            restart_task = None
            armed = False
            clock = patch.object(authority_module, "time",
                SimpleNamespace(time=lambda: live.now / 1000))
            clock.start()
            try:
                if mode == "restart":
                    from tests.test_room_lighting_runtime import _MemoryOwnershipStore
                    live.runtime._ownership_store = _MemoryOwnershipStore()
                for entity in ("switch.demo_main", "switch.demo_peer"):
                    live.hass.states.set(entity, "off", last_changed=live.dt())
                created = await live.start(_absence_switch_payload())
                room = created.payload["roomId"]
                authority = ManualLightAuthority()
                authority.set_related_sources(RelatedLightSources.from_configs(live.runtime.configs()))
                live.runtime._manual_light_authority = authority
                live.runtime._executor._manual_light_authority = authority
                await live.presence()
                self.assertTrue(live.receipts()[-1]["confirmed"])
                await live.presence("off")
                live.now += 300000
                first_call = len(live.hass.services.calls)
                save = live.shadow_store.async_save
                build = live.runtime._state_provider.build_context

                async def gated_save(document):
                    nonlocal armed
                    entries = document.get("entries", [])
                    pending = entries[-1].get("dispatchPending", {}) if entries else {}
                    if pending.get("action") == "turn_off" and not armed:
                        armed = True
                        if boundary == "pending":
                            entered.set()
                            await release.wait()
                        if mode == "pending_save_failure":
                            raise RuntimeError("test pending storage failure")
                    if mode == "closure_save_failure" and armed and entries and any(
                        receipt.get("sendOutcome") == "not_sent"
                        for receipt in entries[-1].get("dispatch", [])
                    ):
                        raise RuntimeError("test closure storage failure")
                    return await save(document)

                async def gated_build(*args):
                    if armed and mode == "provider_error":
                        raise RuntimeError("test provider failure")
                    context = await build(*args)
                    if armed and boundary == "context" and not entered.is_set():
                        entered.set()
                        await release.wait()
                    return context

                original_evaluate = runtime_module.evaluate_room_lighting

                def gated_evaluate(*args):
                    if armed and mode == "replan_error":
                        raise RuntimeError("test pure replan failure")
                    return original_evaluate(*args)

                live.shadow_store.async_save = gated_save
                live.runtime._state_provider.build_context = gated_build
                with patch.object(runtime_module, "evaluate_room_lighting", gated_evaluate):
                    process = asyncio.create_task(live.runtime.async_process((room,)))
                    await asyncio.wait_for(entered.wait(), 2)
                    if mode in {"false", "aba"}:
                        changed = copy.deepcopy(created.payload)
                        changed["commandsEnabled"] = False
                        disabled = await live.put(changed)
                        self.assertEqual(200, disabled.status)
                        if mode == "aba":
                            changed = copy.deepcopy(disabled.payload)
                            changed["commandsEnabled"] = True
                            self.assertEqual(200, (await live.put(changed)).status)
                    elif mode in {"manual_target", "manual_peer", "manual_off_latch"}:
                        entity = "switch.demo_peer" if mode == "manual_peer" else "switch.demo_main"
                        state = "off" if mode == "manual_off_latch" else "on"
                        self.assertTrue(authority.capture(entity, state, "manual", mode,
                            observed_at_ms=live.now))
                        self.assertTrue(authority.blocks("switch.demo_main"))
                        if state == "on":
                            record = authority.record(entity)
                            self.assertEqual(live.now + 3600000, record.deadline_ms)
                    elif mode == "reserved":
                        live.runtime._reserved_entity_ids_provider = lambda: {"switch.demo_main"}
                    elif mode in {"presence", "unknown", "unavailable", "stale"}:
                        sensor = live.hass.states.get("binary_sensor.demo_presence")
                        if mode == "stale":
                            sensor.last_changed = datetime.fromtimestamp(
                                (live.now - 86400001) / 1000, timezone.utc)
                            sensor.last_updated = sensor.last_reported = sensor.last_changed
                        else:
                            live.hass.states.set("binary_sensor.demo_presence",
                                "on" if mode == "presence" else mode, last_changed=live.dt())
                    elif mode in {"target_changed", "target_unknown", "target_unavailable", "restored", "cached"}:
                        value = "on" if mode in {"target_changed", "restored", "cached"} else mode.removeprefix("target_")
                        attrs = {mode: True} if mode in {"restored", "cached"} else None
                        live.hass.states.set("switch.demo_main", value, attrs, last_changed=live.dt())
                    elif mode == "stop":
                        await live.runtime.stop()
                    elif mode == "restart":
                        # pendingSaved=True, SENDcrossed=False. The old AUTO
                        # survives in the real store, but not as a command
                        # confirmed inside this new observation session.
                        previous_owner = live.runtime._ownership.snapshots_for(room, ("light_main",))
                        old_started = live.runtime._started_at
                        old_epoch = live.runtime._return_intent_epoch
                        pending_entries = [entry for entry in live.shadow_store.payload["entries"]
                            if entry.get("roomId") == room
                            and entry.get("dispatchPending", {}).get("action") == "turn_off"]
                        self.assertTrue(pending_entries)
                        old_operation = pending_entries[-1]["operationId"]
                        self.assertEqual(first_call, len(live.hass.services.calls))
                        await asyncio.wait_for(live.runtime.stop(), 2)
                        self.assertFalse(live.runtime._running)
                        live.now += 1000
                        restart_task = asyncio.create_task(live.runtime.start(
                            live.hass, "restart-during-off-context"))

                        async def wait_new_session():
                            while not (
                                live.runtime._started_at != old_started
                                and live.runtime._return_intent_epoch != old_epoch
                                and live.runtime._running
                                and live.runtime._ownership.snapshots_for(room, ("light_main",)) == previous_owner
                            ):
                                self.assertFalse(process.done())
                                self.assertFalse(restart_task.done())
                                await asyncio.sleep(0)

                        await asyncio.wait_for(wait_new_session(), 2)
                        self.assertFalse(process.done())
                        self.assertFalse(restart_task.done())
                        self.assertEqual(first_call, len(live.hass.services.calls))
                    elif mode == "closure_save_failure":
                        live.hass.states.set("binary_sensor.demo_presence", "on", last_changed=live.dt())
                    release.set()
                    if restart_task is None:
                        await asyncio.wait_for(process, 2)
                    else:
                        await asyncio.wait_for(asyncio.gather(process, restart_task), 2)
                late = live.hass.services.calls[first_call:]
                if mode == "control":
                    self.assertEqual([("switch", "turn_off", {"entity_id": "switch.demo_main"})], late)
                    self.assertTrue(live.receipts()[-1]["sent"])
                    self.assertTrue(live.receipts()[-1]["confirmed"])
                else:
                    self.assertEqual([], late)
                    if mode == "closure_save_failure":
                        self.assertIn((room, "light_main"), live.runtime._dispatch_failures)
                        preserved_memory = copy.deepcopy(live.runtime._dispatch_failures)
                        preserved_marker = copy.deepcopy(live.shadow_store.payload["dispatchFailures"])
                        preserved_revision = live.runtime._target_evidence_revision("switch.demo_main")
                        durable = RoomLightingShadowService(live.shadow_store)
                        await durable.async_load()
                        self.assertEqual(preserved_marker, durable.journal_payload()["dispatchFailures"])
                    else:
                        if mode == "restart":
                            old_receipts = [receipt
                                for entry in live.shadow.journal_payload()["entries"]
                                if entry.get("operationId") == old_operation
                                for receipt in entry.get("dispatch", [])
                                if receipt.get("targetId") == "light_main"]
                            self.assertEqual(1, len(old_receipts))
                            receipt = old_receipts[0]
                            self.assertEqual("superseded", receipt["reason"])
                            self.assertEqual(previous_owner,
                                live.runtime._ownership.snapshots_for(room, ("light_main",)))
                            self.assertEqual({}, live.runtime._dispatch_failures)
                            durable = RoomLightingShadowService(live.shadow_store)
                            await durable.async_load()
                            self.assertEqual([], durable.journal_payload()["dispatchFailures"])
                            context = await live.runtime._build_context(live.runtime._configs[room])
                            self.assertEqual(live.runtime._started_at, context.observation_started_at)
                        else:
                            receipt = live.receipts()[-1]
                        self.assertEqual((False, "not_sent", False, None, "none"), (
                            receipt["sent"], receipt["sendOutcome"], receipt["dispatchAttempt"],
                            receipt["confirmed"], receipt["ownershipChanged"]))
                        self.assertEqual([], live.shadow_store.payload["dispatchFailures"])
                        if mode == "pending_save_failure":
                            self.assertEqual("dispatch_pending_persistence_failed", receipt["reason"])
                            self.assertEqual({}, live.runtime._dispatch_failures)
                    if mode in {"unknown", "unavailable", "stale"}:
                        # A broken observation cannot be counted retroactively.
                        await live.presence("off")
                        live.now += 299000
                        await live.runtime.async_process((room,))
                        self.assertEqual(first_call, len(live.hass.services.calls))
                        live.now += 1000
                        await live.runtime.async_process((room,))
                        self.assertEqual("off", live.hass.states.get("switch.demo_main").state)
                    if mode in {"pending_save_failure", "closure_save_failure"}:
                        live.shadow_store.async_save = save
                        live.runtime._state_provider.build_context = build
                        if mode == "pending_save_failure":
                            # No pending marker reached storage and SEND was
                            # never crossed. A later healthy write may send once.
                            await live.runtime.async_process((room,))
                            self.assertEqual(first_call + 1, len(live.hass.services.calls))
                            self.assertEqual(("switch", "turn_off", {"entity_id": "switch.demo_main"}),
                                live.hass.services.calls[-1])
                            self.assertTrue(live.receipts()[-1]["sent"])
                            self.assertTrue(live.receipts()[-1]["confirmed"])
                            self.assertEqual([], live.shadow_store.payload["dispatchFailures"])
                            await live.runtime.async_process((room,))
                            self.assertEqual(first_call + 1, len(live.hass.services.calls))
                        else:
                            # pendingSaved=True, SENDcrossed=False,
                            # terminalSaved=False: retain the uncertain marker,
                            # even after a whole new observed absence window.
                            await live.presence("off")
                            live.now += 299000
                            await live.runtime.async_process((room,))
                            self.assertEqual(first_call, len(live.hass.services.calls))
                            live.now += 1000
                            await live.runtime.async_process((room,))
                            self.assertEqual(first_call, len(live.hass.services.calls))
                            receipt = live.receipts()[-1]
                            self.assertEqual(("unconfirmed_attempt", False, "not_sent", False, None), (
                                receipt["reason"], receipt["sent"], receipt["sendOutcome"],
                                receipt["dispatchAttempt"], receipt["confirmed"]))
                            self.assertEqual(preserved_revision,
                                live.runtime._target_evidence_revision("switch.demo_main"))
                            self.assertEqual(preserved_memory, live.runtime._dispatch_failures)
                            durable = RoomLightingShadowService(live.shadow_store)
                            await durable.async_load()
                            self.assertEqual(preserved_marker, durable.journal_payload()["dispatchFailures"])
                self.assertFalse(any(call[0] == "fan" or call[2].get("entity_id") == "switch.demo_power"
                    for call in live.hass.services.calls))
            finally:
                release.set()
                owned = [task for task in (process, restart_task) if task is not None]
                if owned:
                    try:
                        await asyncio.wait_for(asyncio.gather(*owned, return_exceptions=True), 2)
                    except asyncio.TimeoutError:
                        for task in owned:
                            task.cancel()
                        await asyncio.gather(*owned, return_exceptions=True)
                        raise
                await live.close()
                clock.stop()

        for boundary, modes in (
            ("pending", ("control", "presence", "unknown", "unavailable", "stale",
                "false", "aba", "manual_target", "manual_peer", "manual_off_latch",
                "reserved", "target_changed", "target_unknown", "target_unavailable",
                "restored", "cached", "stop", "provider_error", "replan_error",
                "pending_save_failure", "closure_save_failure")),
            ("context", ("false", "aba", "manual_target", "manual_peer", "reserved",
                "target_changed", "target_unknown", "target_unavailable", "restart")),
        ):
            for mode in modes:
                with self.subTest(boundary=boundary, mode=mode):
                    asyncio.run(flow(boundary, mode))

    def test_absence_off_restart_does_not_regrant_silent_old_auto(self) -> None:
        async def flow():
            from tests.test_room_lighting_runtime import _MemoryOwnershipStore
            live = _LiveConfigHarness(self)
            try:
                live.runtime._ownership_store = _MemoryOwnershipStore()
                for entity in ("switch.demo_main", "switch.demo_peer"):
                    live.hass.states.set(entity, "off", last_changed=live.dt())
                created = await live.start(_absence_switch_payload())
                room = created.payload["roomId"]
                await live.presence()
                self.assertTrue(live.receipts()[-1]["confirmed"])
                owner = live.runtime._ownership.snapshots_for(room, ("light_main",))[0]
                await live.presence("off")
                live.now += 100000
                await live.runtime.stop()
                live.now += 60000
                before = len(live.hass.services.calls)
                await live.runtime.start(live.hass, "restarted-absence")
                self.assertEqual(before, len(live.hass.services.calls))
                started = live.runtime._started_at
                self.assertGreater(started, owner.at)
                # The old OFF sensor timestamp cannot count a pre-start gap.
                context = await live.runtime._build_context(live.runtime._configs[room])
                self.assertEqual(started, context.observation_started_at)
                self.assertEqual(started, context.unobserved_since)
                await live.presence("off")
                live.now += 299000
                await live.runtime.async_process((room,))
                self.assertEqual(before, len(live.hass.services.calls))
                live.now += 1000
                await live.runtime.async_process((room,))
                self.assertEqual(before, len(live.hass.services.calls))
                context = await live.runtime._build_context(live.runtime._configs[room])
                self.assertIsNone(context.unobserved_since)
                self.assertEqual(started, context.observation_started_at)
                restored = live.runtime._ownership.snapshots_for(room, ("light_main",))[0]
                self.assertEqual(owner, restored)
                self.assertEqual("on", live.hass.states.get("switch.demo_main").state)
                # Separate existing positive: an actual fresh target report can
                # restore the old proof. This does not fabricate another AUTO.
                state = live.hass.states.get("switch.demo_main")
                state.last_reported = live.dt()
                context = await live.runtime._build_context(live.runtime._configs[room])
                self.assertEqual(owner, context.ownership[0])
                await live.runtime.async_process((room,))
                self.assertEqual(before + 1, len(live.hass.services.calls))
                self.assertTrue(live.receipts()[-1]["sent"])
                self.assertTrue(live.receipts()[-1]["confirmed"])
            finally:
                await live.close()
        asyncio.run(flow())

    def test_absence_off_first_actual_send_keeps_truth_and_cancels_second(self) -> None:
        async def flow(outcome):
            live = _LiveConfigHarness(self)
            try:
                for entity in ("switch.demo_main", "switch.demo_peer"):
                    live.hass.states.set(entity, "off", last_changed=live.dt())
                created = await live.start(_absence_switch_payload(second=True))
                room = created.payload["roomId"]
                await live.presence()
                self.assertEqual(2, len(live.hass.services.calls))
                self.assertTrue(all(receipt["confirmed"] for receipt in live.receipts()))
                await live.presence("off")
                live.now += 300000
                before = len(live.hass.services.calls)
                old_operations = {entry.get("operationId")
                    for entry in live.shadow.journal_payload()["entries"] if entry.get("roomId") == room}
                call = live.hass.services.async_call

                async def presence_after_actual_send(domain, service, data, **kwargs):
                    # pendingSaved=True; this lower boundary is the real SEND.
                    # A fresh ON is observed only after it has been crossed.
                    if outcome == "confirmed":
                        await call(domain, service, data, **kwargs)
                    else:
                        live.hass.services.calls.append((domain, service, copy.deepcopy(data)))
                    live.hass.states.set("binary_sensor.demo_presence", "on", last_changed=live.dt())
                    if outcome == "uncertain":
                        raise RuntimeError("transport failed after the actual lower call")

                live.hass.services.async_call = presence_after_actual_send
                await live.runtime.async_process((room,))
                self.assertEqual([("switch", "turn_off", {"entity_id": "switch.demo_main"})],
                    live.hass.services.calls[before:])
                entries = [entry for entry in live.shadow.journal_payload()["entries"]
                    if entry.get("roomId") == room and entry.get("operationId") not in old_operations
                    and entry.get("dispatch")]
                operations = {entry["operationId"] for entry in entries}
                self.assertEqual(1, len(operations))
                self.assertTrue(all(entry["configVersion"] == created.payload["version"] for entry in entries))
                receipts = [receipt for entry in entries for receipt in entry["dispatch"]]
                self.assertEqual(2, len(receipts))
                self.assertEqual({"light_main", "light_peer"}, {receipt["targetId"] for receipt in receipts})
                self.assertTrue(all(receipt["action"] == "turn_off" for receipt in receipts))
                durable_receipts = [receipt for entry in live.shadow_store.payload["entries"]
                    if entry.get("roomId") == room and entry.get("operationId") in operations
                    for receipt in entry.get("dispatch", [])]
                self.assertEqual(receipts, durable_receipts)
                by_target = {receipt["targetId"]: receipt for receipt in receipts}
                first, second = by_target["light_main"], by_target["light_peer"]
                expected = {
                    "confirmed": (True, True, "sent", True, "confirmed", "auto"),
                    "unconfirmed": (True, True, "sent", False, "unconfirmed", "none"),
                    "uncertain": (True, False, "uncertain", False, "dispatch_uncertain", "none"),
                }[outcome]
                self.assertEqual(expected, tuple(first[key] for key in (
                    "dispatchAttempt", "sent", "sendOutcome", "confirmed", "reason", "ownershipChanged")))
                self.assertIs(first["dispatchAttempt"], True)
                self.assertIs(first["sent"], outcome != "uncertain")
                self.assertIs(first["confirmed"], outcome == "confirmed")
                self.assertEqual(("light_peer", False, "not_sent", False, None, "none", "superseded"), (
                    second["targetId"], second["sent"], second["sendOutcome"],
                    second["dispatchAttempt"], second["confirmed"], second["ownershipChanged"], second["reason"]))
                self.assertEqual("on", live.hass.states.get("switch.demo_peer").state)
                failures = live.shadow_store.payload["dispatchFailures"]
                self.assertEqual(outcome != "confirmed", bool(failures))
                self.assertFalse(any(failure["targetId"] == "light_peer" for failure in failures))
                self.assertNotIn((room, "light_peer"), live.runtime._dispatch_failures)
                if outcome != "confirmed":
                    expected_failure = {"key": f"target:{room}:light_main", "kind": "target",
                        "roomId": room, "targetId": "light_main", "action": "turn_off",
                        "evidenceRevision": first["evidenceRevision"],
                        "blockedAfterMs": live.now, "attemptBoundary": True}
                    self.assertEqual([expected_failure], failures)
                    self.assertEqual(("turn_off", first["evidenceRevision"], live.now),
                        live.runtime._dispatch_failures[(room, "light_main")])
                    from custom_components.hausman_hub.application.room_lighting_shadow import RoomLightingShadowService
                    reloaded = RoomLightingShadowService(live.shadow_store)
                    await reloaded.async_load()
                    self.assertEqual(failures, reloaded.journal_payload()["dispatchFailures"])
                else:
                    self.assertNotIn((room, "light_main"), live.runtime._dispatch_failures)
            finally:
                await live.close()
        for outcome in ("confirmed", "unconfirmed", "uncertain"):
            with self.subTest(outcome=outcome):
                asyncio.run(flow(outcome))

    def test_absence_off_superseded_closure_preserves_loaded_failure_anchors(self) -> None:
        async def flow(known_anchor):
            from custom_components.hausman_hub.application.room_lighting_shadow import RoomLightingShadowService
            live = _LiveConfigHarness(self)
            pending, release = asyncio.Event(), asyncio.Event()
            process = None
            try:
                for entity in ("switch.demo_main", "switch.demo_peer"):
                    live.hass.states.set(entity, "off", last_changed=live.dt())
                created = await live.start(_absence_switch_payload())
                room = created.payload["roomId"]
                await live.presence()
                self.assertTrue(live.receipts()[-1]["confirmed"])
                await live.presence("off")
                live.now += 300000
                before = len(live.hass.services.calls)
                # A migrated backend record is loaded by the actual shadow and
                # runtime restore paths; no receipt or permission is replaced.
                previous = {"key": f"target:{room}:light_main", "kind": "target",
                    "roomId": room, "targetId": "light_main", "action": "turn_on",
                    "evidenceRevision": "legacy-unconfirmed-revision"}
                if known_anchor:
                    previous.update(blockedAfterMs=live.now, attemptBoundary=True)
                document = copy.deepcopy(live.shadow_store.payload)
                document["dispatchFailures"] = [previous]
                live.shadow_store.payload = document
                live.shadow = RoomLightingShadowService(live.shadow_store)
                await live.shadow.async_load()
                live.runtime._shadow = live.shadow
                live.runtime._restore_dispatch_failures()
                expected_memory = ("turn_on", "legacy-unconfirmed-revision",
                    live.now if known_anchor else None)
                self.assertEqual(expected_memory, live.runtime._dispatch_failures[(room, "light_main")])
                save = live.shadow_store.async_save

                async def pause(document):
                    entries = document.get("entries", [])
                    marker = entries[-1].get("dispatchPending", {}) if entries else {}
                    if marker.get("action") == "turn_off" and not pending.is_set():
                        pending.set()
                        await release.wait()
                    return await save(document)

                live.shadow_store.async_save = pause
                process = asyncio.create_task(live.runtime.async_process((room,)))
                await asyncio.wait_for(pending.wait(), 2)
                # pendingSaved completes after release, SEND is still uncrossed;
                # the fresh native ON makes this old absence intent superseded.
                live.hass.states.set("binary_sensor.demo_presence", "on", last_changed=live.dt())
                release.set()
                await asyncio.wait_for(process, 2)
                self.assertEqual(before, len(live.hass.services.calls))
                receipt = live.receipts()[-1]
                self.assertEqual(("superseded", False, "not_sent", False, None, "none"),
                    tuple(receipt[key] for key in ("reason", "sent", "sendOutcome",
                        "dispatchAttempt", "confirmed", "ownershipChanged")))
                self.assertEqual(expected_memory, live.runtime._dispatch_failures[(room, "light_main")])
                self.assertEqual([previous], live.shadow_store.payload["dispatchFailures"])
                reloaded = RoomLightingShadowService(live.shadow_store)
                await reloaded.async_load()
                self.assertEqual([previous], reloaded.journal_payload()["dispatchFailures"])
            finally:
                release.set()
                if process is not None:
                    await asyncio.gather(process, return_exceptions=True)
                await live.close()
        for known_anchor in (False, True):
            with self.subTest(known_anchor=known_anchor):
                asyncio.run(flow(known_anchor))

    def test_silent_absence_off_never_prepares_upstream_power(self) -> None:
        async def flow(exact):
            live = _LiveConfigHarness(self)
            try:
                payload = _absence_switch_payload()
                payload["devices"]["light_targets"] = payload["devices"]["light_targets"][:1]
                live.hass.states.set("switch.demo_main", "off", last_changed=live.dt())
                payload = (await live.power_setup(exact=exact, payloads=[payload]))[0]
                created = await live.start(payload)
                room = created.payload["roomId"]
                await live.presence()
                self.assertTrue(live.receipts()[-1]["confirmed"])
                self.assertEqual("on", live.hass.states.get("switch.demo_power").state)
                await live.presence("off")
                live.now += 300000
                before = len(live.hass.services.calls)
                # Loss of source evidence does not turn a safe target OFF into
                # a power-ON intent. Exact topology still has its real edge and
                # claim; the existing OFF exception remains necessary here.
                live.hass.states.set("switch.demo_power", "unavailable", last_changed=live.dt())
                await live.runtime.async_process((room,))
                self.assertEqual([("switch", "turn_off", {"entity_id": "switch.demo_main"})],
                    live.hass.services.calls[before:])
                self.assertTrue(live.receipts()[-1]["sent"])
                self.assertTrue(live.receipts()[-1]["confirmed"])
            finally:
                await live.close()
        for exact in (False, True):
            with self.subTest(exact=exact):
                asyncio.run(flow(exact))

    def test_power_original_config_permission_survives_pending_and_source_lock(self) -> None:
        async def flow(exact, mode):
            live = _LiveConfigHarness(self)
            entered, release = asyncio.Event(), asyncio.Event()
            task = None
            lock = None
            try:
                payload = (await live.power_setup(exact=exact))[0]
                if mode == "clock":
                    payload["quietHours"] = {"start": "10:01", "end": "11:00"}
                created = await live.start(payload)
                if mode == "control":
                    await live.presence()
                    self.assertEqual([("switch", "turn_on"), ("light", "turn_on")],
                                     [call[:2] for call in live.hass.services.calls])
                    self.assertTrue(live.receipts()[-1]["power"]["confirmed"] if
                                    "confirmed" in live.receipts()[-1]["power"] else
                                    live.receipts()[-1]["power"]["powerPreparation"]["confirmed"])
                    self.assertTrue(live.receipts()[-1]["confirmed"])
                    return
                save = live.shadow_store.async_save

                async def pause_pending(document):
                    if not entered.is_set() and any(
                        "dispatchPending" in entry for entry in document.get("entries", [])
                    ):
                        entered.set()
                        await release.wait()
                    return await save(document)

                if mode == "lock":
                    lock = live.runtime._power_source_lock("switch.demo_power")
                    await lock.acquire()
                    name = "_ensure_power_source" if exact else "_ensure_legacy_room_power"
                    ensure = getattr(live.runtime, name)

                    async def waiting(*args, **kwargs):
                        self.assertIsNotNone(kwargs["permission"])
                        entered.set()
                        return await ensure(*args, **kwargs)

                    setattr(live.runtime, name, waiting)
                else:
                    live.shadow_store.async_save = pause_pending
                task = asyncio.create_task(live.presence())
                await asyncio.wait_for(entered.wait(), 2)
                current = copy.deepcopy(created.payload)
                if mode == "clock":
                    live.now += 62000
                elif mode == "stop":
                    await live.runtime.stop()
                else:
                    if mode == "quiet":
                        current["quietHours"] = {"start": "09:30", "end": "11:00"}
                    elif mode == "enabled":
                        current["name"] = "Новая включённая версия"
                    else:
                        current["commandsEnabled"] = False
                    saved = await live.put(current)
                    self.assertEqual(200, saved.status)
                    self.assertEqual(2, saved.payload["version"])
                    current = saved.payload
                    if mode == "aba":
                        current = copy.deepcopy(current)
                        current["commandsEnabled"] = True
                        saved = await live.put(current)
                        self.assertEqual(200, saved.status)
                        self.assertEqual(3, saved.payload["version"])
                        current = saved.payload
                self.assertEqual([], live.hass.services.calls, "PUT itself cannot send")
                release.set()
                if lock is not None:
                    lock.release()
                    lock = None
                await asyncio.wait_for(task, 2)
                self.assertEqual([], live.hass.services.calls)
                receipt = live.receipts()[-1]
                power = receipt["power"]
                self.assertEqual("quiet_hours" if mode == "clock" else "dispatch_permission_stale", power["reason"])
                self.assertEqual((False, False, "not_sent"),
                                 (power["sent"], power["dispatchAttempt"], power["sendOutcome"]))
                self.assertNotIn("_attemptAt", power)
                self.assertNotIn("switch.demo_power", live.runtime._executing_actions)
                self.assertNotIn("switch.demo_power", live.runtime._command_grace)
                self.assertNotIn("switch.demo_power", live.runtime._power_delivery)
                self.assertEqual([], live.shadow_store.payload["dispatchFailures"])
                if mode in {"aba", "enabled"}:
                    await live.presence()
                    self.assertEqual([("switch", "turn_on"), ("light", "turn_on")],
                                     [call[:2] for call in live.hass.services.calls])
                    self.assertTrue(live.receipts()[-1]["confirmed"])
            finally:
                release.set()
                if lock is not None:
                    lock.release()
                if task is not None:
                    await asyncio.gather(task, return_exceptions=True)
                await live.close()
        for exact in (False, True):
            for mode in ("control", "false", "aba", "enabled", "lock", "quiet", "clock", "stop"):
                with self.subTest(exact=exact, mode=mode):
                    asyncio.run(flow(exact, mode))

    def test_power_pending_keeps_manual_reserved_and_away_refusals(self) -> None:
        async def flow(exact, mode):
            from custom_components.hausman_hub.application import manual_light_authority as authority_module
            from custom_components.hausman_hub.application.manual_light_authority import ManualLightAuthority
            from custom_components.hausman_hub.application.related_light_sources import RelatedLightSources
            live = _LiveConfigHarness(self)
            entered, release = asyncio.Event(), asyncio.Event()
            task = None
            authority_clock = patch.object(
                authority_module, "time", SimpleNamespace(time=lambda: live.now / 1000))
            authority_clock.start()
            try:
                payload = (await live.power_setup(exact=exact))[0]
                peer = copy.deepcopy(payload["devices"]["light_targets"][0])
                peer.update(id="light_peer", entityId="light.demo_peer", role="other")
                payload["devices"]["light_targets"].append(peer)
                live.hass.states.set("light.demo_peer", "off", last_changed=live.dt())
                await live.start(payload)
                authority = ManualLightAuthority()
                authority.set_related_sources(RelatedLightSources.from_configs(live.runtime.configs()))
                live.runtime._manual_light_authority = authority
                live.runtime._executor._manual_light_authority = authority
                save = live.shadow_store.async_save

                async def pause(document):
                    if not entered.is_set() and any(
                        "dispatchPending" in entry for entry in document.get("entries", [])
                    ):
                        entered.set()
                        await release.wait()
                    return await save(document)

                live.shadow_store.async_save = pause
                task = asyncio.create_task(live.presence())
                await asyncio.wait_for(entered.wait(), 2)
                manual_record = None
                if mode in {"target", "peer"}:
                    entity = "light.demo_main" if mode == "target" else "light.demo_peer"
                    self.assertTrue(authority.capture(
                        entity, "on", "manual", f"pending-{mode}", observed_at_ms=live.now))
                    manual_record = authority.record(entity)
                    self.assertEqual(live.now, manual_record.observed_at_ms)
                    self.assertEqual(live.now + 3600000, manual_record.deadline_ms)
                    self.assertTrue(authority.blocks(entity))
                    self.assertTrue(authority.blocks("light.demo_main"))
                elif mode == "reserved":
                    live.runtime._reserved_entity_ids_provider = lambda: {"switch.demo_power"}
                else:
                    old = live.hass.states.get("binary_sensor.a100_away")
                    live.hass.states.set("binary_sensor.a100_away", "unavailable", last_changed=live.dt())
                    live.runtime._away_event(SimpleNamespace(data={
                        "entity_id": "binary_sensor.a100_away", "old_state": old,
                        "new_state": live.hass.states.get("binary_sensor.a100_away")}))
                    self.assertEqual("away_sensor_unavailable", live.runtime._protected_away_observation().diagnostic)
                    self.assertEqual("away_sensor_unavailable", live.runtime._automatic_away_block_reason())
                release.set()
                await asyncio.wait_for(task, 2)
                self.assertEqual([], live.hass.services.calls)
                power = live.receipts()[-1]["power"]
                self.assertEqual((False, False, "not_sent"),
                                 (power["sent"], power["dispatchAttempt"], power["sendOutcome"]))
                if manual_record is not None:
                    self.assertIs(manual_record, authority.record(entity))
                    self.assertTrue(authority.blocks(entity))
                if mode == "untrusted_away":
                    self.assertEqual("away_sensor_unavailable", power["reason"])
                self.assertNotIn("switch.demo_power", live.runtime._command_grace)
                self.assertEqual([], live.shadow_store.payload["dispatchFailures"])
            finally:
                release.set()
                if task is not None:
                    await asyncio.gather(task, return_exceptions=True)
                await live.close()
                authority_clock.stop()
        for exact in (False, True):
            for mode in ("target", "peer", "reserved", "untrusted_away"):
                with self.subTest(exact=exact, mode=mode):
                    asyncio.run(flow(exact, mode))

    def test_shared_power_lock_keeps_room_permissions_and_terminal_save_separate(self) -> None:
        async def flow():
            from custom_components.hausman_hub.application.room_power_migration import room_target_claim_key
            live = _LiveConfigHarness(self)
            pending, release_pending = asyncio.Event(), asyncio.Event()
            terminal, release_terminal = asyncio.Event(), asyncio.Event()
            tasks = []
            try:
                first = _config_payload()
                other = _config_payload("room_other", "Другая комната")
                other["devices"]["sensors"][0].update(id="sensor_other_presence", entityId="binary_sensor.other")
                other["devices"]["light_targets"][0].update(id="light_other_main", entityId="light.other")
                other["schedule"][0]["targets"]["lightTargets"] = ["light_other_main"]
                first, other = await live.power_setup(
                    exact=True, payloads=[first, other], local_power_switch=False)
                live.hass.states.set("binary_sensor.other", "off", last_changed=live.dt())
                live.hass.states.set("light.other", "off", last_changed=live.dt())
                self.assertEqual(200, (await live.put(other)).status)
                created = await live.start(first)
                for payload in (first, other):
                    parsed = await live.store.async_get(payload["roomId"])
                    self.assertIsNone(parsed.devices.power_switch)
                    target = parsed.devices.light_targets[0]
                    edge = live.runtime._power_dependencies()[target.entity_id]
                    self.assertEqual("switch.demo_power", edge.power_source_entity_id)
                    self.assertEqual("switch.demo_power", live.runtime._power_migration_manifest[
                        room_target_claim_key(parsed.room_id, target.id)])
                    self.assertTrue(live.runtime._exact_power_decision(
                        parsed, live.runtime._power_dependencies()).active)
                save = live.shadow_store.async_save

                async def pause(document):
                    if not pending.is_set() and any(
                        "dispatchPending" in entry for entry in document.get("entries", [])
                    ):
                        pending.set()
                        await release_pending.wait()
                    if not terminal.is_set() and any(
                        receipt.get("power", {}).get("reason") == "dispatch_permission_stale"
                        for entry in document.get("entries", []) for receipt in entry.get("dispatch", [])
                    ):
                        terminal.set()
                        await release_terminal.wait()
                    return await save(document)

                live.shadow_store.async_save = pause
                tasks.append(asyncio.create_task(live.presence()))
                await asyncio.wait_for(pending.wait(), 2)
                changed = copy.deepcopy(created.payload)
                changed["commandsEnabled"] = False
                self.assertEqual(200, (await live.put(changed)).status)
                tasks.append(asyncio.create_task(live.state_event("binary_sensor.other", "on")))
                release_pending.set()
                await asyncio.wait_for(terminal.wait(), 2)
                await asyncio.sleep(0)
                self.assertTrue(live.runtime._power_source_lock("switch.demo_power").locked())
                self.assertEqual([], live.hass.services.calls)
                release_terminal.set()
                await asyncio.wait_for(asyncio.gather(*tasks), 2)
                self.assertEqual(["switch.demo_power", "light.other"],
                                 [call[2]["entity_id"] for call in live.hass.services.calls])
                self.assertFalse(live.runtime._configs[first["roomId"]].commands_enabled)
                self.assertTrue(live.runtime._configs[other["roomId"]].commands_enabled)
                first_receipts = [receipt for entry in live.shadow.journal_payload()["entries"]
                                  if entry.get("roomId") == first["roomId"]
                                  for receipt in entry.get("dispatch", []) if "targetId" in receipt]
                self.assertTrue(first_receipts)
                self.assertTrue(all(not receipt["sent"] for receipt in first_receipts))
                self.assertEqual([], live.shadow_store.payload["dispatchFailures"])
            finally:
                release_pending.set()
                release_terminal.set()
                await asyncio.gather(*tasks, return_exceptions=True)
                await live.close()
        asyncio.run(flow())

    def test_power_put_after_real_send_preserves_truth_and_blocks_old_target(self) -> None:
        async def flow(exact, outcome):
            live = _LiveConfigHarness(self)
            returned, release = asyncio.Event(), asyncio.Event()
            task = None
            try:
                payload = (await live.power_setup(exact=exact, warmup=1 if outcome == "warmup" else 0))[0]
                created = await live.start(payload)
                live.runtime._executor._power_readback_window = 0
                if outcome in {"uncertain", "unconfirmed"}:
                    async def unconfirmed(domain, service, data, **kwargs):
                        live.hass.services.calls.append((domain, service, copy.deepcopy(data)))
                        if outcome == "uncertain":
                            raise OSError("transport outcome unknown after entering HA")
                    live.hass.services.async_call = unconfirmed
                power_on = live.runtime._executor.async_power_on
                actual = []

                async def after_real_power(*args, **kwargs):
                    result = await power_on(*args, **kwargs)
                    actual.append(copy.deepcopy(result))
                    returned.set()
                    if outcome != "warmup":
                        await release.wait()
                    return result

                live.runtime._executor.async_power_on = after_real_power
                task = asyncio.create_task(live.presence())
                await asyncio.wait_for(returned.wait(), 2)
                if outcome == "warmup":
                    await asyncio.sleep(0)
                changed = copy.deepcopy(created.payload)
                changed["commandsEnabled"] = False
                self.assertEqual(200, (await live.put(changed)).status)
                release.set()
                await asyncio.wait_for(task, 3)
                self.assertEqual([("switch", "turn_on")], [call[:2] for call in live.hass.services.calls])
                receipt = live.receipts()[-1]
                self.assertFalse(receipt["sent"])
                power = receipt["power"]
                self.assertEqual(outcome != "uncertain", power["sent"])
                self.assertEqual("uncertain" if outcome == "uncertain" else "sent", power["sendOutcome"])
                self.assertTrue(power["dispatchAttempt"])
                self.assertEqual(actual[0], power["powerPreparation"])
                self.assertEqual(outcome in {"confirmed", "warmup"}, actual[0]["confirmed"])
                if outcome in {"uncertain", "unconfirmed"}:
                    self.assertIn("switch.demo_power", live.runtime._power_failures)
                    self.assertEqual(live.now, live.runtime._power_failure_at["switch.demo_power"])
                    self.assertEqual(1, len(live.shadow_store.payload["dispatchFailures"]))
                else:
                    self.assertEqual("dispatch_permission_stale", receipt["reason"])
                    self.assertEqual([], live.shadow_store.payload["dispatchFailures"])
            finally:
                release.set()
                if task is not None:
                    await asyncio.gather(task, return_exceptions=True)
                await live.close()
        for exact in (False, True):
            for outcome in (("confirmed", "unconfirmed", "uncertain", "warmup") if exact
                            else ("confirmed", "unconfirmed", "uncertain")):
                with self.subTest(exact=exact, outcome=outcome):
                    asyncio.run(flow(exact, outcome))

    def test_pending_config_fences_false_aba_and_quiet_changes(self) -> None:
        async def flow(mode):
            live = _LiveConfigHarness(self)
            release = asyncio.Event()
            pending = asyncio.Event()
            event_task = None
            try:
                created = await live.start()
                save = live.shadow_store.async_save

                async def pause_pending(document):
                    if not pending.is_set() and any(
                        "dispatchPending" in entry for entry in document.get("entries", [])
                    ):
                        pending.set()
                        await release.wait()
                    return await save(document)

                live.shadow_store.async_save = pause_pending
                event_task = asyncio.create_task(live.presence())
                await asyncio.wait_for(pending.wait(), 2)
                changed = copy.deepcopy(created.payload)
                if mode == "quiet":
                    changed["quietHours"] = {"start": "09:30", "end": "11:00"}
                else:
                    changed["commandsEnabled"] = False
                saved = await live.put(changed)
                self.assertEqual(200, saved.status)
                if mode == "aba":
                    enabled = copy.deepcopy(saved.payload)
                    enabled["commandsEnabled"] = True
                    self.assertEqual(200, (await live.put(enabled)).status)
                self.assertEqual([], live.hass.services.calls)
                release.set()
                await asyncio.wait_for(event_task, 2)
                self.assertEqual([], live.hass.services.calls)
                receipt = live.receipts()[-1]
                self.assertFalse(receipt["sent"])
                self.assertIsNone(receipt["confirmed"])
                self.assertEqual("not_sent", receipt["sendOutcome"])
                self.assertFalse(receipt["dispatchAttempt"])
                self.assertEqual("none", receipt["ownershipChanged"])
                self.assertEqual("quiet_hours" if mode == "quiet" else "permission_stale", receipt["reason"])
                self.assertEqual([], live.shadow.journal_payload().get("dispatchFailures", []))
                self.assertEqual([], live.shadow_store.payload.get("dispatchFailures", []))
                owners = live.runtime._ownership.snapshots_for(created.payload["roomId"], ("light_main",))
                self.assertFalse(any(owner.source.value == "auto" for owner in owners))
            finally:
                release.set()
                if event_task is not None:
                    await asyncio.gather(event_task, return_exceptions=True)
                await live.close()
        for mode in ("false", "aba", "quiet"):
            with self.subTest(mode=mode):
                asyncio.run(flow(mode))

    def test_put_true_requires_fresh_event_and_does_not_duplicate(self) -> None:
        async def flow():
            live = _LiveConfigHarness(self)
            try:
                payload = _config_payload()
                payload["commandsEnabled"] = False
                created = await live.start(payload)
                changed = copy.deepcopy(created.payload)
                changed["commandsEnabled"] = True
                saved = await live.put(changed)
                self.assertEqual(200, saved.status)
                self.assertEqual([], live.hass.services.calls)
                await live.presence()
                self.assertEqual(1, len(live.hass.services.calls))
                self.assertTrue(live.receipts()[-1]["confirmed"])
                await live.runtime.async_process((saved.payload["roomId"],))
                self.assertEqual(1, len(live.hass.services.calls))
                disabled = copy.deepcopy(saved.payload)
                disabled["commandsEnabled"] = False
                self.assertEqual(200, (await live.put(disabled)).status)
                await live.presence("off")
                await live.presence("on")
                self.assertEqual(1, len(live.hass.services.calls))
            finally:
                await live.close()
        asyncio.run(flow())

    def test_config_change_during_light_readback_keeps_sent_failure_and_manual_owner(self) -> None:
        async def flow():
            live = _LiveConfigHarness(self)
            try:
                created = await live.start()
                call = live.hass.services.async_call
                manual = None

                async def sent_then_change(*args, **kwargs):
                    nonlocal manual
                    await call(*args, **kwargs)
                    changed = copy.deepcopy(created.payload)
                    changed["commandsEnabled"] = False
                    self.assertEqual(200, (await live.put(changed)).status)
                    live.runtime._ownership.record_manual(created.payload["roomId"], "light_main", live.now)
                    manual = live.runtime._ownership.snapshots_for(created.payload["roomId"], ("light_main",))

                live.hass.services.async_call = sent_then_change
                await live.presence()
                self.assertEqual(1, len(live.hass.services.calls))
                receipt = live.receipts()[-1]
                self.assertTrue(receipt["sent"])
                self.assertIs(receipt["confirmed"], False)
                self.assertTrue(receipt["dispatchAttempt"])
                self.assertEqual("sent", receipt["sendOutcome"])
                self.assertEqual("none", receipt["ownershipChanged"])
                self.assertEqual("unconfirmed", receipt["reason"])
                self.assertEqual(manual, live.runtime._ownership.snapshots_for(
                    created.payload["roomId"], ("light_main",)))
                failures = live.shadow.journal_payload().get("dispatchFailures", [])
                self.assertTrue(failures)
                self.assertEqual(failures, live.shadow_store.payload["dispatchFailures"])
            finally:
                await live.close()
        asyncio.run(flow())

    def test_config_change_after_real_light_confirmation_never_grants_auto(self) -> None:
        async def flow(with_manual):
            live = _LiveConfigHarness(self)
            try:
                created = await live.start()
                execute = live.runtime._executor.execute
                manual = None

                async def confirmed_then_change(*args, **kwargs):
                    nonlocal manual
                    receipt = await execute(*args, **kwargs)
                    self.assertTrue(receipt.sent)
                    self.assertTrue(receipt.confirmed)
                    changed = copy.deepcopy(created.payload)
                    changed["commandsEnabled"] = False
                    self.assertEqual(200, (await live.put(changed)).status)
                    if with_manual:
                        live.runtime._ownership.record_manual(created.payload["roomId"], "light_main", live.now)
                        manual = live.runtime._ownership.snapshots_for(created.payload["roomId"], ("light_main",))
                    return receipt

                live.runtime._executor.execute = confirmed_then_change
                await live.presence()
                self.assertEqual(1, len(live.hass.services.calls))
                receipt = live.receipts()[-1]
                self.assertTrue(receipt["sent"])
                self.assertIs(receipt["confirmed"], True)
                self.assertEqual("sent", receipt["sendOutcome"])
                self.assertTrue(receipt["dispatchAttempt"])
                self.assertEqual("none", receipt["ownershipChanged"])
                self.assertEqual("dispatch_permission_stale", receipt["reason"])
                owners = live.runtime._ownership.snapshots_for(created.payload["roomId"], ("light_main",))
                self.assertFalse(any(owner.source.value == "auto" for owner in owners))
                if with_manual:
                    self.assertEqual(manual, owners)
                self.assertEqual([], live.shadow_store.payload.get("dispatchFailures", []))
            finally:
                await live.close()
        for with_manual in (False, True):
            with self.subTest(manual=with_manual):
                asyncio.run(flow(with_manual))

    def test_live_exhaust_config_fences_before_send_and_after_real_confirmation(self) -> None:
        async def flow(phase, change, with_manual=False):
            from tests.test_room_lighting_runtime import _shower_payload
            live = _LiveConfigHarness(self)
            captured = []
            try:
                payload = _shower_payload(commands_enabled=False)
                payload["roomId"] = "room_demo_entry"
                live.hass.states.set("sensor.demo_shower_humidity", "40", last_changed=live.dt())
                live.hass.states.set("switch.demo_shower_fan", "off", last_changed=live.dt())
                created = await live.start(payload)
                enabled = copy.deepcopy(created.payload)
                enabled["commandsEnabled"] = True
                saved = await live.put(enabled)
                self.assertEqual(200, saved.status)
                self.assertEqual([], live.hass.services.calls)
                execute = live.runtime._executor.execute
                manual = None

                async def change_config():
                    nonlocal manual
                    changed = copy.deepcopy(saved.payload)
                    if change == "false":
                        changed["commandsEnabled"] = False
                    elif change == "quiet":
                        changed["quietHours"] = {"start": "09:30", "end": "11:00"}
                    elif change == "remove":
                        del changed["auxiliary"]
                    result = await live.put(changed)
                    self.assertEqual(200, result.status)
                    if with_manual:
                        live.runtime._ownership.record_manual(payload["roomId"], "aux_shower_fan", live.now)
                        manual = live.runtime._ownership.snapshots_for(payload["roomId"], ("aux_shower_fan",))

                async def phase_boundary(hass, command, **kwargs):
                    if command.target_id != "aux_shower_fan":
                        return await execute(hass, command, **kwargs)
                    if phase == "before":
                        await change_config()
                    receipt = await execute(hass, command, **kwargs)
                    captured.append(receipt)
                    if phase == "after":
                        self.assertTrue(receipt.sent)
                        self.assertTrue(receipt.confirmed)
                        await change_config()
                    return receipt

                live.runtime._executor.execute = phase_boundary
                await live.state_event("sensor.demo_shower_humidity", "70")
                self.assertEqual(1, len(captured))
                receipt = captured[0]
                self.assertEqual(phase != "before", receipt.sent)
                self.assertIs(receipt.confirmed, phase != "before")
                self.assertEqual("not_sent" if phase == "before" else "sent", receipt.send_outcome)
                fan_calls = [call for call in live.hass.services.calls
                             if call[2].get("entity_id") == "switch.demo_shower_fan"]
                self.assertEqual(0 if phase == "before" else 1, len(fan_calls))
                owners = live.runtime._ownership.snapshots_for(payload["roomId"], ("aux_shower_fan",))
                if phase == "control":
                    self.assertTrue(any(owner.source.value == "auto" for owner in owners))
                else:
                    self.assertFalse(any(owner.source.value == "auto" for owner in owners))
                if with_manual:
                    self.assertEqual(manual, owners)
                if change == "remove":
                    self.assertIsNone(live.runtime._configs[payload["roomId"]].auxiliary)
                    stored = await live.store.async_get(payload["roomId"])
                    self.assertIsNone(stored.auxiliary)
                    self.assertEqual(enabled["devices"], stored.to_dict()["devices"])
                await live.state_event("sensor.demo_shower_humidity", "71")
                self.assertEqual(0 if phase == "before" else 1, len([
                    call for call in live.hass.services.calls
                    if call[2].get("entity_id") == "switch.demo_shower_fan"]))
            finally:
                await live.close()
        cases = [("control", "none", False)]
        cases += [("before", change, False) for change in ("false", "quiet", "remove")]
        cases += [("after", change, manual) for change in ("false", "remove") for manual in (False, True)]
        for phase, change, manual in cases:
            with self.subTest(phase=phase, change=change, manual=manual):
                asyncio.run(flow(phase, change, manual))

    def test_noop_old_version_and_other_room_preserve_active_state(self) -> None:
        async def flow():
            from dataclasses import replace
            from custom_components.hausman_hub.domain.room_lighting import config_from_payload
            live = _LiveConfigHarness(self)
            sleepers = []
            try:
                other = _config_payload("room_other", "Другая комната")
                other["commandsEnabled"] = True
                other["devices"]["sensors"][0]["id"] = "sensor_other_presence"
                other["devices"]["sensors"][0]["entityId"] = "binary_sensor.other"
                other["devices"]["light_targets"][0]["id"] = "light_other_main"
                other["devices"]["light_targets"][0]["entityId"] = "light.other"
                other["schedule"][0]["targets"]["lightTargets"] = ["light_other_main"]
                live.hass.states.set("binary_sensor.other", "off", last_changed=live.dt())
                live.hass.states.set("light.other", "off", last_changed=live.dt())
                self.assertEqual(200, (await live.put(other)).status)
                created = await live.start()
                room = created.payload["roomId"]
                first = live.runtime._configs[room]
                second = live.runtime._configs["room_other"]
                indices = copy.deepcopy(live.runtime._rooms_by_entity)
                for config in (first, second):
                    task = asyncio.create_task(asyncio.sleep(60))
                    sleepers.append(task)
                    live.runtime._legacy_curve_absence_tasks[config.room_id] = (config, live.now + 60000, task)
                    live.runtime._curve_presence[config.room_id] = {"deadline": live.now + 10000}
                    live.runtime._curve_motion[config.room_id] = {"deadline": live.now + 20000}
                    live.runtime._shower_exhaust[config.room_id] = {"presence_deadline": live.now + 30000}
                live.runtime._ownership.record_manual("room_other", "light_other_main", live.now)
                manual = live.runtime._ownership.snapshots_for("room_other", ("light_other_main",))
                noop = await live.put(created.payload)
                self.assertEqual(200, noop.status)
                self.assertEqual(created.payload["version"], noop.payload["version"])
                self.assertIs(first, live.runtime._configs[room])
                self.assertEqual(live.now + 10000, live.runtime._curve_presence[room]["deadline"])
                self.assertFalse(sleepers[0].cancelled())
                changed = copy.deepcopy(noop.payload)
                changed["commandsEnabled"] = False
                saved = await live.put(changed)
                self.assertEqual(200, saved.status)
                await asyncio.gather(sleepers[0], return_exceptions=True)
                self.assertTrue(sleepers[0].cancelled())
                self.assertIsNone(live.runtime._curve_presence[room]["deadline"])
                self.assertIsNone(live.runtime._curve_motion[room]["deadline"])
                self.assertNotIn(room, live.runtime._shower_exhaust)
                self.assertIs(second, live.runtime._configs["room_other"])
                self.assertFalse(sleepers[1].cancelled())
                self.assertEqual(live.now + 10000, live.runtime._curve_presence["room_other"]["deadline"])
                self.assertEqual(live.now + 30000, live.runtime._shower_exhaust["room_other"]["presence_deadline"])
                self.assertEqual(manual, live.runtime._ownership.snapshots_for("room_other", ("light_other_main",)))
                current = live.runtime._configs[room]
                live.runtime.apply_config(first)
                self.assertIs(current, live.runtime._configs[room])
                changed_devices = copy.deepcopy(saved.payload)
                changed_devices["devices"]["light_targets"][0]["entityId"] = "light.unbound"
                candidate = replace(config_from_payload(changed_devices), version=current.version + 1)
                live.runtime.apply_config(candidate)
                self.assertIs(current, live.runtime._configs[room])
                self.assertEqual(indices, live.runtime._rooms_by_entity)
                self.assertEqual([], live.hass.services.calls)
            finally:
                for task in sleepers:
                    task.cancel()
                await asyncio.gather(*sleepers, return_exceptions=True)
                await live.close()
        asyncio.run(flow())

    def test_config_change_does_not_clear_or_retry_an_unconfirmed_attempt(self) -> None:
        async def flow():
            live = _LiveConfigHarness(self)
            try:
                created = await live.start()

                async def call_without_report(domain, service, data, **kwargs):
                    live.hass.services.calls.append((domain, service, copy.deepcopy(data)))

                live.hass.services.async_call = call_without_report
                await live.presence()
                self.assertEqual(1, len(live.hass.services.calls))
                self.assertTrue(live.receipts()[-1]["sent"])
                self.assertFalse(live.receipts()[-1]["confirmed"])
                failures = copy.deepcopy(live.shadow.journal_payload()["dispatchFailures"])
                self.assertTrue(failures)
                memory = copy.deepcopy(live.runtime._dispatch_failures)
                changed = copy.deepcopy(created.payload)
                changed["name"] = "Новое название без разрешения повтора"
                self.assertEqual(200, (await live.put(changed)).status)
                self.assertEqual(memory, live.runtime._dispatch_failures)
                self.assertEqual(failures, live.shadow.journal_payload()["dispatchFailures"])
                self.assertEqual(failures, live.shadow_store.payload["dispatchFailures"])
                await live.presence()
                self.assertEqual(1, len(live.hass.services.calls))
                self.assertEqual("unconfirmed_attempt", live.receipts()[-1]["reason"])
                self.assertEqual(failures, live.shadow.journal_payload()["dispatchFailures"])
                self.assertEqual(failures, live.shadow_store.payload["dispatchFailures"])
            finally:
                await live.close()
        asyncio.run(flow())

    def test_pending_config_change_preserves_superseded_legacy_failure(self) -> None:
        async def flow(with_anchor):
            from custom_components.hausman_hub.domain.room_lighting_engine import (
                LightAction, PlannedCommand, RoomLightingDecision, TargetDecision,
            )
            live = _LiveConfigHarness(self)
            pending = asyncio.Event()
            release = asyncio.Event()
            task = None
            try:
                created = await live.start()
                await live.presence()
                self.assertTrue(live.receipts()[-1]["confirmed"])
                self.assertEqual(1, len(live.hass.services.calls))
                config = live.runtime._configs[created.payload["roomId"]]
                # A migrated crash record can lack a precise attempt anchor.
                # The stale-on safe-OFF path must retain that older protection
                # if its superseding OFF is stopped by the new config.
                live.now += 700000
                anchor = live.now if with_anchor else None
                previous = ("turn_on", "old-revision", anchor)
                key = (config.room_id, "light_main")
                live.runtime._dispatch_failures[key] = previous
                save = live.shadow_store.async_save

                async def pause_pending(document):
                    entries = document.get("entries", [])
                    if not pending.is_set() and entries and entries[-1].get("dispatchPending", {}).get("action") == "turn_off":
                        pending.set()
                        await release.wait()
                    return await save(document)

                live.shadow_store.async_save = pause_pending
                command = PlannedCommand("light_main", LightAction.TURN_OFF)
                decision = RoomLightingDecision(
                    room_id=config.room_id, evaluated_at=live.now, mode="live",
                    targets=(TargetDecision("light_main", "off", None, None, (command,)),),
                    commands_enabled=True,
                )
                task = asyncio.create_task(live.runtime._dispatch(
                    config, decision, live.now, permission=live.runtime._dispatch_permission_token(config)))
                await asyncio.wait_for(pending.wait(), 2)
                changed = copy.deepcopy(created.payload)
                changed["commandsEnabled"] = False
                self.assertEqual(200, (await live.put(changed)).status)
                release.set()
                receipts = await asyncio.wait_for(task, 2)
                self.assertEqual(1, len(live.hass.services.calls))
                self.assertEqual("permission_stale", receipts[0]["reason"])
                self.assertFalse(receipts[0]["sent"])
                self.assertIsNone(receipts[0]["confirmed"])
                self.assertEqual("not_sent", receipts[0]["sendOutcome"])
                self.assertFalse(receipts[0]["dispatchAttempt"])
                self.assertEqual("none", receipts[0]["ownershipChanged"])
                self.assertEqual(previous, live.runtime._dispatch_failures[key])
                failure = live.shadow_store.payload["dispatchFailures"][0]
                self.assertEqual("turn_on", failure["action"])
                self.assertEqual("old-revision", failure["evidenceRevision"])
                if with_anchor:
                    self.assertEqual(anchor, failure["blockedAfterMs"])
                else:
                    self.assertNotIn("blockedAfterMs", failure)
                expected = {
                    "key": f"target:{config.room_id}:light_main", "kind": "target",
                    "roomId": config.room_id, "targetId": "light_main",
                    "action": "turn_on", "evidenceRevision": "old-revision",
                }
                if with_anchor:
                    expected["blockedAfterMs"] = anchor
                    expected["attemptBoundary"] = True
                self.assertEqual([expected], live.shadow_store.payload["dispatchFailures"])
                self.assertEqual([expected], live.shadow.journal_payload()["dispatchFailures"])
                from custom_components.hausman_hub.application.room_lighting_shadow import RoomLightingShadowService
                stored = copy.deepcopy(live.shadow_store.payload)
                reloaded = RoomLightingShadowService(live.shadow_store)
                await reloaded.async_load()
                self.assertEqual([expected], reloaded.journal_payload()["dispatchFailures"])
                self.assertEqual(stored, live.shadow_store.payload)
                persisted_receipts = [receipt for entry in stored["entries"]
                                      for receipt in entry.get("dispatch", [])]
                self.assertIsNone(persisted_receipts[-1]["confirmed"])
            finally:
                release.set()
                if task is not None:
                    await asyncio.gather(task, return_exceptions=True)
                await live.close()
        for with_anchor in (False, True):
            with self.subTest(anchor=with_anchor):
                asyncio.run(flow(with_anchor))

    def test_legacy_put_preserves_existing_manual_priority(self) -> None:
        async def flow() -> None:
            payload = _config_payload()
            payload["manualControlReturn"] = {"prioritySeconds": 7200}
            created = await self.config_view.put(
                _json_request(self._config_path(), payload, room_id=payload["roomId"])
            )
            self.assertEqual(created.status, 200)
            legacy = _config_payload(name="Новое название")
            updated = await self.config_view.put(
                _json_request(self._config_path(), legacy, room_id=legacy["roomId"])
            )
            self.assertEqual(updated.status, 200)
            self.assertEqual(updated.payload["name"], "Новое название")
            self.assertEqual(updated.payload["manualControlReturn"]["prioritySeconds"], 7200)
        asyncio.run(flow())

    def test_quiet_hours_survive_legacy_put_and_conflict_and_explicit_null_clears(self) -> None:
        async def flow() -> None:
            payload = _config_payload()
            payload['quietHours'] = {'start': '00:30', 'end': '07:00'}
            created = await self.config_view.put(
                _json_request(self._config_path(), payload, room_id=payload['roomId'])
            )
            self.assertEqual(200, created.status)
            _validate('room-lighting-config.schema.json', created.payload)
            legacy = dict(created.payload)
            del legacy['quietHours']
            legacy['name'] = 'Сохранение старым планшетом'
            updated = await self.config_view.put(
                _json_request(self._config_path(), legacy, room_id=payload['roomId'])
            )
            self.assertEqual(200, updated.status)
            self.assertEqual(payload['quietHours'], updated.payload['quietHours'])
            stale = await self.config_view.put(
                _json_request(self._config_path(), legacy, room_id=payload['roomId'])
            )
            self.assertEqual(409, stale.status)
            reset = dict(updated.payload)
            reset['quietHours'] = None
            cleared = await self.config_view.put(
                _json_request(self._config_path(), reset, room_id=payload['roomId'])
            )
            self.assertEqual(200, cleared.status)
            self.assertNotIn('quietHours', cleared.payload)
            _validate('room-lighting-config.schema.json', cleared.payload)
        asyncio.run(flow())

    def test_old_client_curve_save_preserves_neutral_and_explicit_reset(self) -> None:
        async def flow() -> None:
            payload = _config_payload()
            payload["profile"] = "day_curve"
            payload["curveParams"] = {"eveningMaxPercent": 40, "neutralKelvin": 3534, "eveningWarmKelvin": 3200}
            created = await self.config_view.put(
                _json_request(self._config_path(), payload, room_id=payload["roomId"])
            )
            self.assertEqual(created.status, 200)
            _validate("room-lighting-config.schema.json", created.payload)
            update = dict(created.payload)
            update["curveParams"] = {"eveningMaxPercent": 40}
            update["name"] = "Новое название"
            updated = await self.config_view.put(
                _json_request(self._config_path(), update, room_id=payload["roomId"])
            )
            self.assertEqual(updated.status, 200)
            self.assertEqual(updated.payload["curveParams"], payload["curveParams"])
            stale = await self.config_view.put(
                _json_request(self._config_path(), update, room_id=payload["roomId"])
            )
            self.assertEqual(stale.status, 409)
            whole_block_omitted = dict(updated.payload)
            del whole_block_omitted["curveParams"]
            whole_block_omitted["name"] = "Сохранение старым планшетом"
            preserved = await self.config_view.put(
                _json_request(self._config_path(), whole_block_omitted, room_id=payload["roomId"])
            )
            self.assertEqual(preserved.status, 200)
            self.assertEqual(preserved.payload["curveParams"], payload["curveParams"])
            reset = dict(preserved.payload)
            reset["curveParams"] = {"eveningMaxPercent": 40, "neutralKelvin": 4000}
            result = await self.config_view.put(
                _json_request(self._config_path(), reset, room_id=payload["roomId"])
            )
            self.assertEqual(result.status, 200)
            self.assertEqual(result.payload["curveParams"]["neutralKelvin"], 4000)
            self.assertEqual(result.payload["curveParams"]["eveningWarmKelvin"], 3200)
            reset_evening = dict(result.payload)
            reset_evening["curveParams"] = {"eveningMaxPercent": 40, "eveningWarmKelvin": 3400}
            result = await self.config_view.put(
                _json_request(self._config_path(), reset_evening, room_id=payload["roomId"])
            )
            self.assertEqual(result.status, 200)
            self.assertEqual(result.payload["curveParams"]["neutralKelvin"], 4000)
            self.assertEqual(result.payload["curveParams"]["eveningWarmKelvin"], 3400)
        asyncio.run(flow())

    def test_curve_params_creation_normalizes_dimming_target_to_five(self) -> None:
        async def flow() -> None:
            payload = _config_payload()
            payload["profile"] = "day_curve"
            payload["curveParams"] = {
                "confirmedAbsenceSeconds": 600,
                "riseSeconds": 10,
                "dayStart": "09:00",
                "morningRamp": {
                    "startPercent": 20,
                    "fullPercent": 100,
                    "fullBy": "12:00",
                },
                "eveningDimming": {
                    "targetPercent": 5,
                    "targetBy": "21:00",
                    "reserveStart": "20:00",
                },
            }
            del payload["dimming"]["targetPercent"]  # omitted, not zero
            created = await self.config_view.put(
                _json_request(self._config_path(), payload, room_id=payload["roomId"])
            )
            self.assertEqual(created.status, 200)
            # The approved creation rule: a curve room created without an
            # explicit dimming target gets the documented decline goal 5.
            self.assertEqual(created.payload["dimming"]["targetPercent"], 5)
            self.assertEqual(
                created.payload["curveParams"],
                payload["curveParams"],
            )

            # An update that omits the whole block keeps the saved curve.
            update = _config_payload(name="Новое название")
            update["version"] = created.payload["version"]
            del update["dimming"]["targetPercent"]
            updated = await self.config_view.put(
                _json_request(self._config_path(), update, room_id=update["roomId"])
            )
            self.assertEqual(updated.status, 200)
            self.assertEqual(updated.payload["curveParams"], payload["curveParams"])
            self.assertEqual(updated.payload["dimming"]["targetPercent"], 5)
        asyncio.run(flow())

    def test_curve_params_creation_respects_explicit_zero_target(self) -> None:
        async def flow() -> None:
            payload = _config_payload()
            payload["profile"] = "day_curve"
            payload["curveParams"] = {"riseSeconds": 10}
            payload["dimming"]["targetPercent"] = 0
            created = await self.config_view.put(
                _json_request(self._config_path(), payload, room_id=payload["roomId"])
            )
            self.assertEqual(created.status, 200)
            self.assertEqual(created.payload["dimming"]["targetPercent"], 0)
        asyncio.run(flow())

    def test_curve_params_forms_never_mix_over_http(self) -> None:
        async def flow() -> None:
            payload = _config_payload()
            payload["profile"] = "day_curve"
            payload["curveParams"] = {"eveningMaxPercent": 40, "riseSeconds": 10}
            rejected = await self.config_view.put(
                _json_request(self._config_path(), payload, room_id=payload["roomId"])
            )
            self.assertEqual(rejected.status, 400)
            self.assertIn("curveParams", str(rejected.payload))
        asyncio.run(flow())

    def test_curve_params_explicit_null_is_rejected_over_http(self) -> None:
        """AUD-CURVE-01: an explicit ``curveParams: null`` must be a 400 —
        only the MISSING key preserves a saved block, so accepting null
        would silently erase the saved ceiling (the live tambur document
        carries the legacy evening maximum)."""
        async def flow() -> None:
            payload = _config_payload()
            payload["profile"] = "day_curve"
            payload["curveParams"] = {"eveningMaxPercent": 40}
            created = await self.config_view.put(
                _json_request(self._config_path(), payload, room_id=payload["roomId"])
            )
            self.assertEqual(created.status, 200)
            update = _config_payload(name="Новое название")
            update["version"] = created.payload["version"]
            update["curveParams"] = None
            rejected = await self.config_view.put(
                _json_request(self._config_path(), update, room_id=update["roomId"])
            )
            self.assertEqual(rejected.status, 400)
            self.assertIn("curveParams", str(rejected.payload))
            got = await self.config_view.get(
                _request(self._config_path(), room_id=payload["roomId"])
            )
            self.assertEqual(got.status, 200)
            self.assertEqual(got.payload["curveParams"], {"eveningMaxPercent": 40})
        asyncio.run(flow())

    def test_put_rejects_revision_changed_by_other_service_writer(self) -> None:
        async def flow() -> None:
            from custom_components.hausman_hub.application.room_lighting_service import RoomLightingService
            from custom_components.hausman_hub.domain.room_lighting import config_from_payload

            lock = asyncio.Lock()
            service = RoomLightingService(self.store, mutation_lock=lock)
            self.hass.data["hausman_hub"][self.api.DATA_ROOM_LIGHTING_SERVICE] = service
            original = _config_payload()
            await service.async_put_config(config_from_payload(original))
            await lock.acquire()
            fresh = _config_payload(name="Изменено другим действием")
            direct = asyncio.create_task(service.async_put_config(config_from_payload(fresh)))
            old = _config_payload(name="Устаревшее изменение клиента")
            http = asyncio.create_task(self.config_view.put(
                _json_request(self._config_path(), old, room_id=old["roomId"])
            ))
            await asyncio.sleep(0)
            lock.release()
            await direct
            response = await http
            self.assertEqual(response.status, 409)
            self.assertEqual(response.payload["code"], "revision_conflict")
            self.assertEqual(response.payload["details"]["actualRevision"], 2)
            stored = await service.async_get_config(old["roomId"])
            self.assertEqual(stored.name, "Изменено другим действием")
            self.assertEqual(stored.version, 2)
        asyncio.run(flow())

    def test_manual_priority_explicit_update_legacy_create_and_noop(self) -> None:
        async def flow() -> None:
            payload = _config_payload()
            created = await self.config_view.put(
                _json_request(self._config_path(), payload, room_id=payload["roomId"])
            )
            self.assertEqual(created.status, 200)
            self.assertEqual(created.payload["manualControlReturn"]["prioritySeconds"], 3600)
            payload["manualControlReturn"] = {"prioritySeconds": 7200}
            changed = await self.config_view.put(
                _json_request(self._config_path(), payload, room_id=payload["roomId"])
            )
            self.assertEqual(changed.status, 200)
            self.assertEqual(changed.payload["version"], 2)
            legacy = _config_payload()
            legacy["version"] = 2
            unchanged = await self.config_view.put(
                _json_request(self._config_path(), legacy, room_id=legacy["roomId"])
            )
            self.assertEqual(unchanged.status, 200)
            self.assertEqual(unchanged.payload, changed.payload)
            payload["version"] = 2
            payload["manualControlReturn"] = {"prioritySeconds": 5400}
            explicit = await self.config_view.put(
                _json_request(self._config_path(), payload, room_id=payload["roomId"])
            )
            self.assertEqual(explicit.status, 200)
            self.assertEqual(explicit.payload["manualControlReturn"]["prioritySeconds"], 5400)
            self.assertEqual(explicit.payload["version"], 3)
        asyncio.run(flow())

    def test_guarded_put_keeps_manual_graph_closed_until_durable_save(self) -> None:
        """CRUD safety only; status contract validation is a separate known gap."""
        async def flow() -> None:
            from custom_components.hausman_hub.application.manual_light_authority import ManualLightAuthority
            from custom_components.hausman_hub.application.related_light_sources import RelatedLightSources
            from custom_components.hausman_hub.application.room_lighting_service import RoomLightingService
            from custom_components.hausman_hub.domain.room_lighting import config_from_payload

            initial = config_from_payload(_config_payload())
            store = _DelayedMemoryStore(initial)
            authority = ManualLightAuthority()
            authority.set_related_sources(RelatedLightSources.from_configs((initial,)))
            service = RoomLightingService(store, topology_invalidator=authority.invalidate_related_sources)
            self.hass.data["hausman_hub"][self.api.DATA_ROOM_LIGHTING_SERVICE] = service
            self.hass.data["hausman_hub"]["manual_light_authority"] = authority
            changed = _config_payload(name="Изменённая конфигурация")
            changed["devices"]["light_targets"][0]["entityId"] = "light.demo_changed"
            pending = asyncio.create_task(self.config_view.put(
                _json_request(self._config_path(), changed, room_id=changed["roomId"])
            ))
            await store.upsert_started.wait()
            try:
                authority.set_related_sources(RelatedLightSources.from_configs((initial,)))
                self.assertEqual(authority.barrier_reason_for("light.demo_main"), "attribution_uncertain")
                self.assertEqual(store.configs[initial.room_id], initial)
                status = await self.status_view.get(_request(
                    self.api.ROOM_LIGHTING_STATUS_PATH.format(room_id=initial.room_id), room_id=initial.room_id,
                ))
                self.assertEqual(status.status, 200)
                self.assertTrue(status.payload["manual_control_return"]["active"])
                self.assertEqual(status.payload["manual_control_return"]["reason"], "attribution_uncertain")
            finally:
                store.release.set()
                response = await pending
            self.assertEqual(response.status, 200)
            self.assertEqual(response.payload["version"], 2)
            self.assertEqual(store.configs[initial.room_id].devices.light_targets[0].entity_id, "light.demo_changed")
        asyncio.run(flow())

    def test_invalid_config_returns_readable_400_without_details(self) -> None:
        async def flow() -> None:
            room_id = "room_demo_entry"
            payload = _config_payload(room_id)
            payload["schedule"][0]["how"]["brightness"] = 101
            response = await self.config_view.put(
                _json_request(self._config_path(room_id), payload, room_id=room_id)
            )
            self.assertEqual(400, response.status)
            self.assertEqual("invalid_request", response.payload["code"])
            self.assertIn("Конфигурация освещения", response.payload["message"])
            details = response.payload.get("details", {})
            self.assertNotIn("violations", details)

        asyncio.run(flow())

    def test_status_matches_contract_schema(self) -> None:
        async def flow() -> None:
            room_id = "room_demo_entry"
            await self.config_view.put(
                _json_request(
                    self._config_path(room_id), _config_payload(room_id), room_id=room_id
                )
            )
            status = await self.status_view.get(
                _request(
                    self.api.ROOM_LIGHTING_STATUS_PATH.format(room_id=room_id),
                    room_id=room_id,
                )
            )
            self.assertEqual(200, status.status)
            _validate("room-lighting-status.schema.json", status.payload)
            self.assertNotIn("commandsEnabled", status.payload)
            self.assertNotIn("shadowDecision", status.payload)

        asyncio.run(flow())

    def test_status_reports_each_manual_return_source_without_false_countdown(self) -> None:
        from custom_components.hausman_hub.application.manual_light_authority import (
            ManualLightAuthority,
        )

        async def flow() -> None:
            room_id = "room_demo_entry"
            config = _config_payload(room_id)
            config["devices"]["light_targets"].append({
                "id": "switch_lamp", "name": "Ручной свет", "kind": "switch",
                "entityId": "switch.demo_lamp", "role": None, "groupId": None,
                "brightness": False, "color_temperature": False,
                "autoControl": False, "autoAdoptOverride": None,
            })
            response = await self.config_view.put(
                _json_request(self._config_path(room_id), config, room_id=room_id)
            )
            self.assertEqual(200, response.status)
            authority = ManualLightAuthority()
            authority.capture(
                "light.demo_main", "on", "service", "context-1",
                observed_at_ms=int(time.time() * 1000),
            )
            self.hass.data["hausman_hub"]["manual_light_authority"] = authority
            request = _request(
                self.api.ROOM_LIGHTING_STATUS_PATH.format(room_id=room_id),
                room_id=room_id,
            )
            status = await self.status_view.get(request)
            self.assertEqual(200, status.status)
            returned = status.payload["manual_control_return"]
            self.assertTrue(returned["active"])
            self.assertEqual("manual_only_target", returned["reason"])
            self.assertEqual("blocked", returned["scope"])
            self.assertIsNone(returned["expires_at"])
            self.assertIsNone(returned["remaining_seconds"])
            self.assertIsNone(returned["since"])
            self.assertEqual({
                "light.demo_main": "manual_on_wait",
                "switch.demo_lamp": "manual_only_target",
            }, {source["entityId"]: source["reason"] for source in returned["sources"]})
            self.assertTrue(all(source["scope"] == "blocked" for source in returned["sources"]))
            _validate("room-lighting-status.schema.json", status.payload)

            authority.mark_unhealthy()
            failed = (await self.status_view.get(request)).payload["manual_control_return"]
            self.assertEqual("storage_unavailable", failed["reason"])
            self.assertTrue(all(
                source["reason"] == "storage_unavailable"
                for source in failed["sources"]
            ))
            self.assertIsNone(failed["remaining_seconds"])
            _validate("room-lighting-status.schema.json", {
                **status.payload, "manual_control_return": failed,
            })

            class _RestoredStore:
                recovered_previous = False

                async def async_load(self):
                    return {"version": 2, "records": [], "recentFingerprints": []}

                async def async_save(self, _payload):
                    return None

            restored = ManualLightAuthority(_RestoredStore())
            await restored.async_load()
            self.hass.data["hausman_hub"]["manual_light_authority"] = restored
            restart = (await self.status_view.get(request)).payload
            self.assertEqual(
                "restart_uncertain", restart["manual_control_return"]["reason"]
            )
            self.assertIsNone(
                restart["manual_control_return"]["remaining_seconds"]
            )
            _validate("room-lighting-status.schema.json", restart)

        asyncio.run(flow())

    def test_status_manual_off_and_inactive_return_sources(self) -> None:
        from custom_components.hausman_hub.application.manual_light_authority import (
            ManualLightAuthority,
        )

        async def flow() -> None:
            room_id = "room_demo_entry"
            response = await self.config_view.put(
                _json_request(
                    self._config_path(room_id), _config_payload(room_id),
                    room_id=room_id,
                )
            )
            self.assertEqual(200, response.status)
            authority = ManualLightAuthority()
            self.hass.data["hausman_hub"]["manual_light_authority"] = authority
            request = _request(
                self.api.ROOM_LIGHTING_STATUS_PATH.format(room_id=room_id),
                room_id=room_id,
            )
            inactive = (await self.status_view.get(request)).payload
            self.assertEqual({
                "active": False, "reason": "none", "expires_at": None,
                "scope": "normal", "remaining_seconds": 0, "since": None,
                "sources": [{
                    "entityId": "light.demo_main", "active": False,
                    "reason": "none", "scope": "normal",
                }],
            }, inactive["manual_control_return"])
            _validate("room-lighting-status.schema.json", inactive)

            observed = int(time.time() * 1000)
            authority.capture(
                "light.demo_main", "off", "service", "context-off",
                observed_at_ms=observed,
            )
            manual_off = (await self.status_view.get(request)).payload
            returned = manual_off["manual_control_return"]
            self.assertTrue(returned["active"])
            self.assertEqual("manual_off_wait", returned["reason"])
            self.assertEqual("blocked", returned["scope"])
            self.assertEqual(observed // 1000, returned["since"])
            self.assertIsNone(returned["remaining_seconds"])
            self.assertEqual("manual_off_wait", returned["sources"][0]["reason"])
            self.assertEqual("blocked", returned["sources"][0]["scope"])
            _validate("room-lighting-status.schema.json", manual_off)

            initial = ManualLightAuthority()
            initial.capture_legacy(
                "light.demo_main", source="startup_unobserved"
            )
            self.hass.data["hausman_hub"]["manual_light_authority"] = initial
            startup = (await self.status_view.get(request)).payload
            uncertain = startup["manual_control_return"]
            self.assertTrue(uncertain["active"])
            self.assertEqual("restart_uncertain", uncertain["reason"])
            self.assertEqual(
                "startup_unobserved", uncertain["sources"][0]["source"]
            )
            self.assertIsNone(uncertain["since"])
            self.assertIsNone(uncertain["remaining_seconds"])
            _validate("room-lighting-status.schema.json", startup)

        asyncio.run(flow())

    def test_status_deduplicates_aliases_and_fails_closed_on_conflict(self) -> None:
        from custom_components.hausman_hub.application.manual_light_authority import (
            ManualLightAuthority,
        )

        async def flow() -> None:
            room_id = "room_demo_entry"
            config = _config_payload(room_id)
            alias = {
                "id": "light_demo_main_alias", "name": "Люстра, второй ID",
                "kind": "light", "entityId": "light.demo_main", "role": "other",
                "groupId": None, "brightness": True, "color_temperature": True,
                "autoControl": True, "autoAdoptOverride": None,
            }
            config["devices"]["light_targets"].append(alias)
            response = await self.config_view.put(
                _json_request(self._config_path(room_id), config, room_id=room_id)
            )
            self.assertEqual(200, response.status)
            authority = ManualLightAuthority()
            observed = int(time.time() * 1000)
            authority.capture("light.demo_main", "on", "service", "alias", observed_at_ms=observed)
            self.hass.data["hausman_hub"]["manual_light_authority"] = authority
            request = _request(
                self.api.ROOM_LIGHTING_STATUS_PATH.format(room_id=room_id), room_id=room_id
            )
            deduplicated = (await self.status_view.get(request)).payload
            returned = deduplicated["manual_control_return"]
            self.assertEqual(["light.demo_main"], [
                source["entityId"] for source in returned["sources"]
            ])
            self.assertEqual("manual_on_wait", returned["reason"])
            self.assertEqual(observed // 1000, returned["since"])
            _validate("room-lighting-status.schema.json", deduplicated)

            alias["autoControl"] = False
            response = await self.config_view.put(
                _json_request(self._config_path(room_id), config, room_id=room_id)
            )
            self.assertEqual(200, response.status)
            conflicted = (await self.status_view.get(request)).payload
            returned = conflicted["manual_control_return"]
            self.assertEqual("attribution_uncertain", returned["reason"])
            self.assertEqual("blocked", returned["scope"])
            self.assertIsNone(returned["since"])
            self.assertEqual("attribution_uncertain", returned["sources"][0]["reason"])
            _validate("room-lighting-status.schema.json", conflicted)

        asyncio.run(flow())

    def test_status_aggregate_since_comes_from_selected_reason_source(self) -> None:
        from custom_components.hausman_hub.application.manual_light_authority import (
            ManualLightAuthority,
        )

        async def flow() -> None:
            room_id = "room_demo_entry"
            config = _config_payload(room_id)
            config["devices"]["light_targets"].append({
                "id": "light_demo_secondary", "name": "Второй свет", "kind": "light",
                "entityId": "light.demo_secondary", "role": "other", "groupId": None,
                "brightness": True, "color_temperature": False,
                "autoControl": True, "autoAdoptOverride": None,
            })
            response = await self.config_view.put(
                _json_request(self._config_path(room_id), config, room_id=room_id)
            )
            self.assertEqual(200, response.status)
            authority = ManualLightAuthority()
            authority.capture_legacy("light.demo_main", source="startup_unobserved")
            authority.capture(
                "light.demo_secondary", "off", "service", "manual-off", observed_at_ms=int(time.time() * 1000)
            )
            self.hass.data["hausman_hub"]["manual_light_authority"] = authority
            request = _request(
                self.api.ROOM_LIGHTING_STATUS_PATH.format(room_id=room_id), room_id=room_id
            )
            status = (await self.status_view.get(request)).payload
            returned = status["manual_control_return"]
            self.assertEqual("restart_uncertain", returned["reason"])
            self.assertEqual("blocked", returned["scope"])
            self.assertIsNone(returned["since"])
            self.assertIsNone(returned["remaining_seconds"])
            _validate("room-lighting-status.schema.json", status)

        asyncio.run(flow())

    def test_status_projects_related_sibling_manual_evidence(self) -> None:
        from custom_components.hausman_hub.application.manual_light_authority import ManualLightAuthority
        from custom_components.hausman_hub.application.related_light_sources import RelatedLightSources

        async def flow() -> None:
            room_id = "room_demo_entry"
            config = _config_payload(room_id)
            config["devices"]["light_targets"].append({
                "id": "switch_related", "name": "Связанный свет", "kind": "switch",
                "entityId": "switch.demo_related", "role": "other", "groupId": None,
                "brightness": False, "color_temperature": False,
                "autoControl": True, "autoAdoptOverride": None,
            })
            response = await self.config_view.put(_json_request(self._config_path(room_id), config, room_id=room_id))
            self.assertEqual(200, response.status)
            authority = ManualLightAuthority()
            graph = RelatedLightSources.from_configs((
                SimpleNamespace(room_id=room_id, devices=SimpleNamespace(light_targets=(
                    SimpleNamespace(entity_id="light.demo_main"), SimpleNamespace(entity_id="switch.demo_related"),
                ))),
            ))
            authority.set_related_sources(graph)
            observed = int(time.time() * 1000)
            authority.capture("light.demo_main", "on", "service", "related-api", observed_at_ms=observed)
            self.hass.data["hausman_hub"]["manual_light_authority"] = authority
            request = _request(self.api.ROOM_LIGHTING_STATUS_PATH.format(room_id=room_id), room_id=room_id)
            status = (await self.status_view.get(request)).payload
            returned = status["manual_control_return"]
            self.assertEqual(
                ("blocked", "manual_on_wait", observed // 1000),
                (returned["scope"], returned["reason"], returned["since"]),
            )
            sibling = next(item for item in returned["sources"] if item["entityId"] == "switch.demo_related")
            self.assertEqual((True, "blocked", "manual_on_wait"), (sibling["active"], sibling["scope"], sibling["reason"]))
            _validate("room-lighting-status.schema.json", status)

        asyncio.run(flow())

    def test_status_projects_selector_uncertainty_to_related_sibling(self) -> None:
        from custom_components.hausman_hub.application.manual_light_authority import ManualLightAuthority
        from custom_components.hausman_hub.application.related_light_sources import RelatedLightSources

        async def flow() -> None:
            room_id = "room_demo_entry"
            config = _config_payload(room_id)
            config["devices"]["light_targets"].append({
                "id": "switch_related", "name": "Связанный свет", "kind": "switch",
                "entityId": "switch.demo_related", "role": "other", "groupId": None,
                "brightness": False, "color_temperature": False,
                "autoControl": True, "autoAdoptOverride": None,
            })
            response = await self.config_view.put(_json_request(self._config_path(room_id), config, room_id=room_id))
            self.assertEqual(200, response.status)
            authority = ManualLightAuthority()
            authority.set_related_sources(RelatedLightSources.from_configs((
                SimpleNamespace(room_id=room_id, devices=SimpleNamespace(light_targets=(
                    SimpleNamespace(entity_id="light.demo_main"), SimpleNamespace(entity_id="switch.demo_related"),
                ))),
            )))
            authority.mark_selector_uncertain("light")
            self.hass.data["hausman_hub"]["manual_light_authority"] = authority
            request = _request(self.api.ROOM_LIGHTING_STATUS_PATH.format(room_id=room_id), room_id=room_id)
            status = (await self.status_view.get(request)).payload
            returned = status["manual_control_return"]
            self.assertEqual((True, "selector_uncertain", "blocked"), (
                returned["active"], returned["reason"], returned["scope"],
            ))
            self.assertEqual({
                "light.demo_main": "selector_uncertain",
                "switch.demo_related": "selector_uncertain",
            }, {source["entityId"]: source["reason"] for source in returned["sources"]})
            _validate("room-lighting-status.schema.json", status)

        asyncio.run(flow())

    def test_registered_service_closes_fresh_config_until_runtime_rebuilds_graph(self) -> None:
        """A saved room must not inherit the previous topology's authority."""
        from custom_components.hausman_hub.application.manual_light_authority import (
            ManualLightAuthority,
        )
        from custom_components.hausman_hub.application.related_light_sources import (
            RelatedLightSources,
        )
        from custom_components.hausman_hub.domain.room_lighting import (
            config_from_payload,
        )

        async def flow() -> None:
            hass = FakeHomeAssistant()
            authority = ManualLightAuthority()
            hass.data["hausman_hub"] = {"manual_light_authority": authority}
            self.api.register_room_lighting_api(hass, "entry_graph_invalidation")
            service = hass.data["hausman_hub"][self.api.DATA_ROOM_LIGHTING_SERVICE]
            self.assertIsNotNone(service)

            config = _config_payload("room_graph_invalidation")
            saved = await service.async_put_config(config_from_payload(config))
            authority.set_related_sources(RelatedLightSources.from_configs((saved,)))
            self.assertEqual(
                "attribution_uncertain",
                authority.barrier_reason_for("light.demo_main"),
            )

            status = await self.api.RoomLightingStatusView(hass).get(
                _request(
                    self.api.ROOM_LIGHTING_STATUS_PATH.format(
                        room_id="room_graph_invalidation"
                    ),
                    room_id="room_graph_invalidation",
                )
            )
            self.assertEqual(200, status.status)
            returned = status.payload["manual_control_return"]
            self.assertEqual("attribution_uncertain", returned["reason"])
            self.assertEqual("attribution_uncertain", returned["sources"][0]["reason"])
            _validate("room-lighting-status.schema.json", status.payload)

        asyncio.run(flow())

    def test_pending_changed_config_cannot_reopen_stale_manual_graph(self) -> None:
        """Status stays fail-closed while a changed topology is being saved."""
        from custom_components.hausman_hub.application.manual_light_authority import (
            ManualLightAuthority,
        )
        from custom_components.hausman_hub.application.related_light_sources import (
            RelatedLightSources,
        )
        from custom_components.hausman_hub.application.room_lighting_service import (
            RoomLightingService,
        )
        from custom_components.hausman_hub.domain.room_lighting import (
            config_from_payload,
        )

        async def flow() -> None:
            room_id = "room_demo_entry"
            initial = config_from_payload(_config_payload(room_id))
            store = _DelayedMemoryStore(initial)
            authority = ManualLightAuthority()
            authority.set_related_sources(RelatedLightSources.from_configs((initial,)))
            self.hass.data["hausman_hub"][self.api.DATA_ROOM_LIGHTING_SERVICE] = (
                RoomLightingService(
                    store,
                    topology_invalidator=authority.invalidate_related_sources,
                )
            )
            self.hass.data["hausman_hub"]["manual_light_authority"] = authority

            changed = _config_payload(room_id)
            changed["devices"]["light_targets"].append({
                "id": "light_secondary", "name": "Второй свет", "kind": "light",
                "entityId": "light.demo_secondary", "role": "other", "groupId": None,
                "brightness": True, "color_temperature": False,
                "autoControl": True, "autoAdoptOverride": None,
            })
            put = asyncio.create_task(self.config_view.put(
                _json_request(self._config_path(room_id), changed, room_id=room_id)
            ))
            await store.upsert_started.wait()

            authority.set_related_sources(RelatedLightSources.from_configs((initial,)))
            self.assertEqual(
                "attribution_uncertain", authority.barrier_reason_for("light.demo_main")
            )
            status = await self.status_view.get(
                _request(
                    self.api.ROOM_LIGHTING_STATUS_PATH.format(room_id=room_id),
                    room_id=room_id,
                )
            )
            self.assertEqual(200, status.status)
            returned = status.payload["manual_control_return"]
            self.assertEqual("attribution_uncertain", returned["reason"])
            self.assertTrue(returned["active"])
            self.assertEqual("attribution_uncertain", returned["sources"][0]["reason"])
            _validate("room-lighting-status.schema.json", status.payload)

            store.release.set()
            saved = await put
            self.assertEqual(200, saved.status)

        asyncio.run(flow())

    def test_status_uses_the_running_runtime_context(self) -> None:
        from datetime import datetime, timezone

        now = int(datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc).timestamp() * 1000)
        calls: list[object] = []

        class _Runtime:
            running = True

            async def async_context_for(self, config):
                calls.append(config.room_id)
                return RoomLightingApiTest._context(now)

        async def flow() -> None:
            room_id = "room_demo_entry"
            await self.config_view.put(
                _json_request(
                    self._config_path(room_id), _config_payload(room_id), room_id=room_id
                )
            )
            self.hass.data["hausman_hub"][
                self.api.DATA_ROOM_LIGHTING_RUNTIME
            ] = _Runtime()
            status = await self.status_view.get(
                _request(
                    self.api.ROOM_LIGHTING_STATUS_PATH.format(room_id=room_id),
                    room_id=room_id,
                )
            )
            self.assertEqual(200, status.status)
            _validate("room-lighting-status.schema.json", status.payload)
            self.assertTrue(status.payload["fresh"])
            self.assertEqual("idle", status.payload["phase"])
            self.assertEqual([room_id], calls)

        asyncio.run(flow())

    def test_status_away_uses_runtime_flag(self) -> None:
        from datetime import datetime, timezone

        now = int(datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc).timestamp() * 1000)

        class _Runtime:
            running = True
            away = True

            async def async_context_for(self, config):
                return RoomLightingApiTest._context(now)

        async def flow() -> None:
            room_id = "room_demo_entry"
            await self.config_view.put(
                _json_request(
                    self._config_path(room_id), _config_payload(room_id), room_id=room_id
                )
            )
            self.hass.data["hausman_hub"][
                self.api.DATA_ROOM_LIGHTING_RUNTIME
            ] = _Runtime()
            status = await self.status_view.get(
                _request(
                    self.api.ROOM_LIGHTING_STATUS_PATH.format(room_id=room_id),
                    room_id=room_id,
                )
            )
            self.assertEqual(200, status.status)
            self.assertTrue(status.payload["away"])

            # Without a running runtime the config fallback applies.
            self.hass.data["hausman_hub"].pop(
                self.api.DATA_ROOM_LIGHTING_RUNTIME, None
            )
            fallback = await self.status_view.get(
                _request(
                    self.api.ROOM_LIGHTING_STATUS_PATH.format(room_id=room_id),
                    room_id=room_id,
                )
            )
            self.assertEqual(200, fallback.status)
            self.assertFalse(fallback.payload["away"])

        asyncio.run(flow())

    def test_templates_are_full_contract_documents(self) -> None:
        async def flow() -> None:
            room_id = "room_demo_entry"
            templates = await self.templates_view.get(
                _request(
                    self.api.ROOM_LIGHTING_TEMPLATES_PATH.format(room_id=room_id),
                    room_id=room_id,
                )
            )
            self.assertEqual(200, templates.status)
            self.assertTrue(templates.payload["templates"])
            for document in templates.payload["templates"]:
                _validate("room-lighting-template.schema.json", document)

        asyncio.run(flow())

    def test_apply_template_returns_valid_config_without_null_entity(self) -> None:
        async def flow() -> None:
            room_id = "room_demo_entry"
            response = await self.apply_view.post(
                _json_request(
                    self.api.ROOM_LIGHTING_TEMPLATE_APPLY_PATH.format(room_id=room_id),
                    {"templateId": "day_profile", "keepDevices": True},
                    room_id=room_id,
                )
            )
            self.assertEqual(200, response.status)
            _validate("room-lighting-config.schema.json", response.payload)
            for target in response.payload["devices"]["light_targets"]:
                if "entityId" in target:
                    self.assertIsNotNone(target["entityId"])

        asyncio.run(flow())

    def test_unbound_template_target_omits_optional_entity_id(self) -> None:
        async def flow() -> None:
            room_id = "room_demo_entry"
            response = await self.apply_view.post(_json_request(
                self.api.ROOM_LIGHTING_TEMPLATE_APPLY_PATH.format(room_id=room_id),
                {"templateId": "day_profile", "keepDevices": True}, room_id=room_id,
            ))
            self.assertEqual(response.status, 200)
            _validate("room-lighting-config.schema.json", response.payload)
            self.assertTrue(response.payload["devices"]["light_targets"])
            for target in response.payload["devices"]["light_targets"]:
                self.assertNotIn("entityId", target)
        asyncio.run(flow())

    def test_live_test_safe_is_background_and_command_free(self) -> None:
        async def flow() -> None:
            room_id = "room_demo_entry"
            spy = _SpyExecutor()
            self.hass.data["hausman_hub"][self.api.DATA_ROOM_LIGHTING_EXECUTOR] = spy
            await self.config_view.put(
                _json_request(
                    self._config_path(room_id), _config_payload(room_id), room_id=room_id
                )
            )
            response = await self.live_view.post(
                _json_request(
                    self.api.ROOM_LIGHTING_LIVE_TESTS_PATH.format(room_id=room_id),
                    {"mode": "safe", "correlationId": "live-api-safe-1"},
                    room_id=room_id,
                )
            )
            self.assertEqual(202, response.status)
            _validate("room-lighting-live-test.schema.json", response.payload)
            self.assertEqual("request", response.payload["kind"])

            registry = self.hass.data["hausman_hub"][
                self.api.DATA_ROOM_LIGHTING_LIVE_TESTS
            ]
            await registry["live-api-safe-1"]["task"]

            path = self.api.ROOM_LIGHTING_LIVE_TEST_PATH.format(
                room_id=room_id, correlation_id="live-api-safe-1"
            )
            result = await self.live_result_view.get(
                _request(path, room_id=room_id, correlation_id="live-api-safe-1")
            )
            self.assertEqual(200, result.status)
            _validate("room-lighting-live-test.schema.json", result.payload)
            self.assertEqual("result", result.payload["kind"])
            self.assertEqual(0, result.payload["result"]["commands_sent"])
            self.assertEqual([], spy.calls)

        asyncio.run(flow())

    def test_live_test_real_without_executor_is_capability_unavailable(self) -> None:
        async def flow() -> None:
            room_id = "room_demo_entry"
            await self.config_view.put(
                _json_request(
                    self._config_path(room_id), _config_payload(room_id), room_id=room_id
                )
            )
            response = await self.live_view.post(
                _json_request(
                    self.api.ROOM_LIGHTING_LIVE_TESTS_PATH.format(room_id=room_id),
                    {"mode": "real", "correlationId": "live-api-real-1"},
                    room_id=room_id,
                )
            )
            self.assertEqual(409, response.status)
            self.assertEqual("capability_unavailable", response.payload["code"])

        asyncio.run(flow())

    def test_live_test_real_with_executor_records_receipts(self) -> None:
        async def flow() -> None:
            room_id = "room_demo_entry"
            spy = _SpyExecutor()
            self.hass.data["hausman_hub"][self.api.DATA_ROOM_LIGHTING_EXECUTOR] = spy
            await self.config_view.put(
                _json_request(
                    self._config_path(room_id), _config_payload(room_id), room_id=room_id
                )
            )
            response = await self.live_view.post(
                _json_request(
                    self.api.ROOM_LIGHTING_LIVE_TESTS_PATH.format(room_id=room_id),
                    {"mode": "real", "correlationId": "live-api-real-2"},
                    room_id=room_id,
                )
            )
            self.assertEqual(202, response.status)
            registry = self.hass.data["hausman_hub"][
                self.api.DATA_ROOM_LIGHTING_LIVE_TESTS
            ]
            await registry["live-api-real-2"]["task"]
            path = self.api.ROOM_LIGHTING_LIVE_TEST_PATH.format(
                room_id=room_id, correlation_id="live-api-real-2"
            )
            result = await self.live_result_view.get(
                _request(path, room_id=room_id, correlation_id="live-api-real-2")
            )
            self.assertEqual(200, result.status)
            _validate("room-lighting-live-test.schema.json", result.payload)
            self.assertTrue(result.payload["result"]["commands_sent"] > 0)
            self.assertTrue(spy.calls)

        asyncio.run(flow())

    def test_live_test_real_away_block_is_failed_and_never_counts_a_command(self) -> None:
        """A runtime pre-executor Away block is a terminal real-test failure."""

        class _AwayBlockedExecutor:
            def __init__(self) -> None:
                self.calls: list[object] = []

            async def __call__(self, command: object) -> dict[str, object]:
                self.calls.append(command)
                return {
                    "blocked": True,
                    "confirmed": False,
                    "sent": False,
                    "sendOutcome": "not_sent",
                    "reason": "away_sensor_unavailable",
                }

        async def flow() -> None:
            room_id = "room_demo_entry"
            executor = _AwayBlockedExecutor()
            self.hass.data["hausman_hub"][self.api.DATA_ROOM_LIGHTING_EXECUTOR] = executor
            await self.config_view.put(
                _json_request(
                    self._config_path(room_id), _config_payload(room_id), room_id=room_id
                )
            )
            response = await self.live_view.post(
                _json_request(
                    self.api.ROOM_LIGHTING_LIVE_TESTS_PATH.format(room_id=room_id),
                    {"mode": "real", "correlationId": "live-api-away-block-1"},
                    room_id=room_id,
                )
            )
            self.assertEqual(202, response.status)
            registry = self.hass.data["hausman_hub"][
                self.api.DATA_ROOM_LIGHTING_LIVE_TESTS
            ]
            await registry["live-api-away-block-1"]["task"]
            path = self.api.ROOM_LIGHTING_LIVE_TEST_PATH.format(
                room_id=room_id, correlation_id="live-api-away-block-1"
            )
            result = await self.live_result_view.get(
                _request(path, room_id=room_id, correlation_id="live-api-away-block-1")
            )
            self.assertEqual(200, result.status)
            _validate("room-lighting-live-test.schema.json", result.payload)
            trace = result.payload["result"]
            self.assertEqual("failed", trace["status"])
            self.assertEqual("sensor_unavailable", trace["reason"])
            self.assertEqual(0, trace["commands_sent"])
            self.assertEqual(
                "away_sensor_unavailable", result.payload["steps"][-1]["detail"]
            )
            self.assertEqual(1, len(executor.calls))

        asyncio.run(flow())

    def test_live_test_background_exception_publishes_terminal_failed_trace(self) -> None:
        """Unexpected runner failures must not leave a polling client pending."""

        async def broken_run(*_args, **_kwargs):
            raise RuntimeError("unexpected runner failure")

        async def flow() -> None:
            room_id = "room_demo_entry"
            await self.config_view.put(
                _json_request(
                    self._config_path(room_id), _config_payload(room_id), room_id=room_id
                )
            )
            original = self.api.RoomLightingLiveTestRunner.run
            self.api.RoomLightingLiveTestRunner.run = broken_run
            try:
                response = await self.live_view.post(
                    _json_request(
                        self.api.ROOM_LIGHTING_LIVE_TESTS_PATH.format(room_id=room_id),
                        {"mode": "safe", "correlationId": "live-api-failure-1"},
                        room_id=room_id,
                    )
                )
                self.assertEqual(202, response.status)
                registry = self.hass.data["hausman_hub"][
                    self.api.DATA_ROOM_LIGHTING_LIVE_TESTS
                ]
                await registry["live-api-failure-1"]["task"]
            finally:
                self.api.RoomLightingLiveTestRunner.run = original

            path = self.api.ROOM_LIGHTING_LIVE_TEST_PATH.format(
                room_id=room_id, correlation_id="live-api-failure-1"
            )
            result = await self.live_result_view.get(
                _request(path, room_id=room_id, correlation_id="live-api-failure-1")
            )
            self.assertEqual(200, result.status)
            _validate("room-lighting-live-test.schema.json", result.payload)
            trace = result.payload["result"]
            self.assertEqual("failed", trace["status"])
            self.assertEqual("unknown", trace["reason"])
            self.assertEqual(0, trace["commands_sent"])

        asyncio.run(flow())

    def test_live_test_cancel(self) -> None:
        async def flow() -> None:
            room_id = "room_demo_entry"
            await self.config_view.put(
                _json_request(
                    self._config_path(room_id), _config_payload(room_id), room_id=room_id
                )
            )
            started = await self.live_view.post(
                _json_request(
                    self.api.ROOM_LIGHTING_LIVE_TESTS_PATH.format(room_id=room_id),
                    {"mode": "safe", "correlationId": "live-api-cancel-1"},
                    room_id=room_id,
                )
            )
            self.assertEqual(202, started.status)
            registry = self.hass.data["hausman_hub"][
                self.api.DATA_ROOM_LIGHTING_LIVE_TESTS
            ]
            entry = registry["live-api-cancel-1"]
            await asyncio.sleep(0)

            path = self.api.ROOM_LIGHTING_LIVE_TEST_PATH.format(
                room_id=room_id, correlation_id="live-api-cancel-1"
            )
            cancelled = await self.live_result_view.post(
                _request(path, room_id=room_id, correlation_id="live-api-cancel-1")
            )
            self.assertEqual(200, cancelled.status)
            _validate("room-lighting-live-test.schema.json", cancelled.payload)
            self.assertEqual("cancelled", cancelled.payload["result"]["status"])

            await entry["task"]
            result = await self.live_result_view.get(
                _request(path, room_id=room_id, correlation_id="live-api-cancel-1")
            )
            self.assertEqual("cancelled", result.payload["result"]["status"])

        asyncio.run(flow())

    def test_day_presence_old_client_preserves_effective_pair(self) -> None:
        async def flow() -> None:
            room_id = "room_demo_entry"
            payload = _config_payload(room_id)
            payload["profile"] = "day_curve"
            payload["curveParams"] = {"neutralKelvin": 3278, "eveningWarmKelvin": 3003,
                                      "presenceConfirmSeconds": 3}
            path = self._config_path(room_id)
            first = await self.config_view.put(_json_request(path, payload, room_id=room_id))
            self.assertEqual(200, first.status)
            old_client = dict(first.payload)
            old_client["name"] = "Новое имя"
            old_client["curveParams"] = {"neutralKelvin": 3278}
            saved = await self.config_view.put(_json_request(path, old_client, room_id=room_id))
            self.assertEqual(200, saved.status)
            self.assertEqual(first.payload["curveParams"], saved.payload["curveParams"])
            _validate("room-lighting-config.schema.json", saved.payload)
            stale = await self.config_view.put(_json_request(path, old_client, room_id=room_id))
            self.assertEqual(409, stale.status)
            invalid = dict(saved.payload)
            invalid["curveParams"] = {"eveningWarmKelvin": 3400}
            rejected = await self.config_view.put(_json_request(path, invalid, room_id=room_id))
            self.assertEqual(400, rejected.status)
        asyncio.run(flow())

    def test_unauthorized_is_forbidden(self) -> None:
        async def flow() -> None:
            room_id = "room_demo_entry"
            path = self._config_path(room_id)
            request = FakeRequest("8.8.8.8", reader_user("guest"), path=path)
            request.match_info = {"room_id": room_id}
            response = await self.config_view.get(request)
            self.assertEqual(403, response.status)

        asyncio.run(flow())

    def test_config_omits_absent_entity_id(self) -> None:
        async def flow() -> None:
            room_id = "room_demo_entry"
            payload = _config_payload(room_id)
            payload["devices"]["sensors"] = []
            payload["devices"]["light_targets"][0].pop("entityId")
            put = await self.config_view.put(
                _json_request(self._config_path(room_id), payload, room_id=room_id)
            )
            self.assertEqual(200, put.status)
            _validate("room-lighting-config.schema.json", put.payload)
            self.assertNotIn("entityId", put.payload["devices"]["light_targets"][0])

            got = await self.config_view.get(
                _request(self._config_path(room_id), room_id=room_id)
            )
            _validate("room-lighting-config.schema.json", got.payload)
            self.assertNotIn("entityId", got.payload["devices"]["light_targets"][0])

        asyncio.run(flow())

    def test_status_sensor_state_enum_and_since_seconds(self) -> None:
        from datetime import datetime, time as dt_time, timezone

        from custom_components.hausman_hub.domain.room_lighting import SensorKind
        from custom_components.hausman_hub.domain.room_lighting_engine import (
            LightSnapshot,
            ProtectionSnapshot,
            RoomLightingContext,
            SensorSnapshot,
        )
        from custom_components.hausman_hub.domain.room_lighting_ownership import (
            SensorState,
        )

        async def flow() -> None:
            room_id = "room_demo_entry"
            now_ms = int(datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc).timestamp() * 1000)
            payload = _config_payload(room_id)
            payload["devices"]["sensors"].append(
                {
                    "id": "sensor_demo_lux",
                    "name": "Освещённость",
                    "kind": "illuminance",
                    "entityId": "sensor.demo_lux",
                    "autoAdoptOverride": None,
                }
            )
            payload["illumination"] = {
                "sensor": "sensor.demo_lux",
                "calibration": {"offset": 0, "multiplier": 1},
                "hysteresis": 5,
                "minLux": 0,
                "maxLux": 20000,
                "thresholds": [],
                "failClosed": True,
            }
            await self.config_view.put(
                _json_request(self._config_path(room_id), payload, room_id=room_id)
            )
            status_path = self.api.ROOM_LIGHTING_STATUS_PATH.format(room_id=room_id)

            def provider(lux_healthy: bool, state: SensorState):
                def factory(_config):
                    return RoomLightingContext(
                        now=now_ms,
                        timezone=timezone.utc,
                        sunrise=dt_time(7, 0),
                        sunset=dt_time(19, 0),
                        sensors=(
                            SensorSnapshot(
                                "sensor_demo_lux",
                                SensorKind.ILLUMINANCE,
                                state,
                                now_ms,
                                lux=120.0,
                                lux_healthy=lux_healthy,
                            ),
                        ),
                        lights=(LightSnapshot("light_main", SensorState.OFF, now_ms),),
                        protection=ProtectionSnapshot(
                            active=True,
                            started_at=now_ms,
                            minimum_interval_seconds=600,
                        ),
                    )

                return factory

            self.hass.data["hausman_hub"][
                self.api.DATA_ROOM_LIGHTING_CONTEXT
            ] = provider(True, SensorState.OFF)
            response = await self.status_view.get(
                _request(status_path, room_id=room_id)
            )
            _validate("room-lighting-status.schema.json", response.payload)
            self.assertEqual("ok", response.payload["illumination"]["sensorState"])
            self.assertEqual(
                now_ms // 1000, response.payload["manual_protection"]["since"]
            )

            self.hass.data["hausman_hub"][
                self.api.DATA_ROOM_LIGHTING_CONTEXT
            ] = provider(False, SensorState.OFF)
            stale = await self.status_view.get(_request(status_path, room_id=room_id))
            _validate("room-lighting-status.schema.json", stale.payload)
            self.assertEqual("stale", stale.payload["illumination"]["sensorState"])

        asyncio.run(flow())

    def test_config_rejects_entity_shared_with_another_room(self) -> None:
        async def flow() -> None:
            first = await self.config_view.put(
                _json_request(
                    self._config_path("room_a"),
                    _config_payload("room_a"),
                    room_id="room_a",
                )
            )
            self.assertEqual(200, first.status)
            second = await self.config_view.put(
                _json_request(
                    self._config_path("room_b"),
                    _config_payload("room_b"),
                    room_id="room_b",
                )
            )
            self.assertEqual(400, second.status)
            self.assertEqual("invalid_request", second.payload["code"])

        asyncio.run(flow())

    def test_status_remaining_seconds_is_the_actual_remainder(self) -> None:
        from datetime import datetime, time as dt_time, timezone

        from custom_components.hausman_hub.domain.room_lighting_engine import (
            LightSnapshot,
            ProtectionSnapshot,
            RoomLightingContext,
        )
        from custom_components.hausman_hub.domain.room_lighting_ownership import (
            SensorState,
        )

        async def flow() -> None:
            room_id = "room_demo_entry"
            now_ms = int(
                datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc).timestamp() * 1000
            )
            await self.config_view.put(
                _json_request(
                    self._config_path(room_id),
                    _config_payload(room_id),
                    room_id=room_id,
                )
            )

            def provider(_config):
                return RoomLightingContext(
                    now=now_ms,
                    timezone=timezone.utc,
                    sunrise=dt_time(7, 0),
                    sunset=dt_time(19, 0),
                    lights=(LightSnapshot("light_main", SensorState.OFF, now_ms),),
                    protection=ProtectionSnapshot(
                        active=True,
                        started_at=now_ms - 100_000,
                        minimum_interval_seconds=600,
                    ),
                )

            self.hass.data["hausman_hub"][
                self.api.DATA_ROOM_LIGHTING_CONTEXT
            ] = provider
            response = await self.status_view.get(
                _request(
                    self.api.ROOM_LIGHTING_STATUS_PATH.format(room_id=room_id),
                    room_id=room_id,
                )
            )
            self.assertEqual(200, response.status)
            _validate("room-lighting-status.schema.json", response.payload)
            self.assertEqual(
                500, response.payload["manual_protection"]["remaining_seconds"]
            )

        asyncio.run(flow())

if __name__ == "__main__":
    unittest.main()
