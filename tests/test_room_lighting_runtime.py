"""Runtime tests for the live room lighting state, executor and driver.

The tests use a synthetic Home Assistant shape, so no real Home Assistant and
no physical device is involved. The shadow-first guarantee is asserted
directly: with per-room ``commandsEnabled=false`` the executor is never called.
"""

from __future__ import annotations

import asyncio
import importlib
from datetime import datetime, time, timezone
import logging
import pytest
from types import SimpleNamespace
import threading
from uuid import uuid4


@pytest.fixture(autouse=True)
def _service_context_factory(monkeypatch):
    executor_module = importlib.import_module(
        "custom_components.hausman_hub.application.room_lighting_ha_executor"
    )
    monkeypatch.setattr(
        executor_module,
        "_new_service_context",
        lambda: SimpleNamespace(id=uuid4().hex),
    )

from custom_components.hausman_hub.application.room_lighting_ha_executor import (
    RoomLightingHaExecutor,
)
from custom_components.hausman_hub.application.room_lighting_ha_state import (
    build_context,
)
from custom_components.hausman_hub.application.room_lighting_runtime import (
    AWAY_ENTITY_ID,
    RoomLightingOwnershipJournal,
    RoomLightingRuntime,
)
from custom_components.hausman_hub.application.room_lighting_shadow import (
    RoomLightingShadowService,
)
from custom_components.hausman_hub.domain.room_lighting import (
    LightKind,
    LightTarget,
    SensorKind,
    config_from_payload,
)
from custom_components.hausman_hub.domain.room_lighting_engine import (
    LightAction,
    PlannedCommand,
    RoomLightingContext,
    SensorSnapshot,
    decision_target,
    evaluate_room_lighting,
)
from custom_components.hausman_hub.domain.room_lighting_ownership import (
    OwnershipSnapshot,
    OwnershipSource,
    SensorState,
    has_proven_auto_ownership,
)

_TZ = timezone.utc
_NOW_MS = int(datetime(2026, 9, 11, 10, 0, tzinfo=_TZ).timestamp() * 1000)
_NOW_DT = datetime.fromtimestamp(_NOW_MS / 1000, _TZ)
_ROOM_ID = "room_demo_entry"


def _config_payload(
    *,
    with_lux: bool = False,
    minimum_interval_seconds: int = 600,
    stable_absence_seconds: int = 30,
    commands_enabled: bool = False,
    power_switch_entity: str | None = None,
) -> dict[str, object]:
    sensors: list[dict[str, object]] = [
        {
            "id": "sensor_demo_presence",
            "name": "Присутствие",
            "kind": "presence",
            "entityId": "binary_sensor.demo_presence",
            "autoAdoptOverride": None,
        }
    ]
    if with_lux:
        sensors.append(
            {
                "id": "sensor_demo_lux",
                "name": "Освещённость",
                "kind": "illuminance",
                "entityId": "sensor.demo_lux",
                "autoAdoptOverride": None,
            }
        )
    payload: dict[str, object] = {
        "contract": {"name": "hausman-hub-room-lighting-config", "version": 1},
        "roomId": _ROOM_ID,
        "name": "Тамбур",
        "version": 1,
        "devices": {
            "sensors": sensors,
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
            "power_switch": (
                None
                if power_switch_entity is None
                else {
                    "id": "power",
                    "name": "Питание",
                    "entityId": power_switch_entity,
                    "autoAdoptOverride": None,
                }
            ),
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
                "targets": {
                    "lightTargets": ["light_main"],
                    "groupIds": [],
                    "roles": [],
                },
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
        "dimming": {
            "enabled": True,
            "onAbsence": True,
            "fadeSeconds": 20,
            "targetPercent": 0,
        },
        "manualOffProtection": {
            "enabled": True,
            "minimumIntervalSeconds": minimum_interval_seconds,
            "releaseMode": "timer_and_absence",
            "stableAbsenceSeconds": stable_absence_seconds,
            "priority": "manual_above_auto",
        },
        "awayBehavior": {"mode": "none"},
        "autoAdopt": True,
        "commandsEnabled": commands_enabled,
        "updatedAt": 1,
        "overrides": {},
    }
    if with_lux:
        payload["illumination"] = {
            "sensor": "sensor.demo_lux",
            "calibration": {"offset": 0, "multiplier": 1},
            "hysteresis": 5,
            "minLux": 0,
            "maxLux": 20000,
            "thresholds": [],
            "failClosed": True,
        }
    return payload


class _FakeState:
    def __init__(
        self,
        state: str,
        attributes: dict[str, object] | None = None,
        last_changed: datetime | None = None,
    ) -> None:
        self.state = state
        self.attributes = dict(attributes or {})
        self.last_changed = last_changed


class _FakeStates:
    def __init__(self) -> None:
        self.values: dict[str, _FakeState] = {}

    def get(self, entity_id: str) -> _FakeState | None:
        return self.values.get(entity_id)

    def set(
        self,
        entity_id: str,
        state: str,
        attributes: dict[str, object] | None = None,
        last_changed: datetime | None = None,
    ) -> None:
        self.values[entity_id] = _FakeState(state, attributes, last_changed)


class _FakeServices:
    """Apply real read-back semantics: the state changes after the call."""

    def __init__(self, hass: "_FakeHass") -> None:
        self._hass = hass
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    async def async_call(
        self,
        domain: str,
        service: str,
        data: dict[str, object],
        blocking: bool = True,
        context: object = None,
    ) -> None:
        del blocking
        self.calls.append((domain, service, dict(data)))
        entity_id = data.get("entity_id")
        if not isinstance(entity_id, str):
            return
        current = self._hass.states.get(entity_id)
        attributes = dict(current.attributes) if current is not None else {}
        if service == "turn_off":
            new_state = "off"
            attributes = {}
        elif service == "turn_on":
            new_state = "on"
            if "brightness_pct" in data:
                attributes["brightness"] = round(
                    int(data["brightness_pct"]) / 100 * 255
                )
            if "color_temp_kelvin" in data:
                attributes["color_temp_kelvin"] = int(data["color_temp_kelvin"])
        else:
            new_state = getattr(current, "state", "off")
        self._hass.states.set(entity_id, new_state, attributes)


class _FakeBus:
    def __init__(self) -> None:
        self.listeners: list[tuple[str, object]] = []

    def async_listen(self, event_type: str, callback: object) -> object:
        self.listeners.append((event_type, callback))
        return lambda: None


class _FakeConfig:
    def __init__(self, time_zone: str = "UTC") -> None:
        self.time_zone = time_zone


class _FakeHass:
    def __init__(self) -> None:
        self.states = _FakeStates()
        self.services = _FakeServices(self)
        self.config = _FakeConfig()
        self.bus = _FakeBus()
        self.tasks: list[object] = []

    def async_create_task(self, coroutine: object) -> object:
        task = asyncio.ensure_future(coroutine)  # type: ignore[arg-type]
        self.tasks.append(task)
        return task


class _MemoryShadowStore:
    def __init__(self) -> None:
        self.payload: object | None = None

    async def async_load(self) -> object | None:
        return self.payload

    async def async_save(self, payload: dict[str, object]) -> None:
        self.payload = payload


class _MemoryOwnershipStore:
    def __init__(self) -> None:
        self.payload: object | None = None
        self.saves = 0

    async def async_load(self) -> object | None:
        return self.payload

    async def async_save(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.saves += 1


class _ConfigService:
    def __init__(self, *configs: object) -> None:
        self._configs = {config.room_id: config for config in configs}  # type: ignore[attr-defined]

    async def async_list_configs(self) -> tuple[object, ...]:
        return tuple(self._configs.values())

    async def async_get_config(self, room_id: str) -> object | None:
        return self._configs.get(room_id)


class _SpyExecutor:
    def __init__(self, *, confirmed: bool = True) -> None:
        self.calls: list[object] = []
        self.confirmed = confirmed

    async def execute(self, hass: object, command: object) -> dict[str, object]:
        del hass
        self.calls.append(command)
        return {"confirmed": self.confirmed, "state_after": {"state": "on"}}


class _FakeDeviceAutomationApi:
    """Mimic the HA mqtt device-automation platform API boundary."""

    def __init__(self) -> None:
        self.attached: list[tuple[dict[str, object], object, dict[str, object]]] = []
        self.unsubscribed = 0

    async def async_attach_trigger(
        self,
        hass: object,
        config: dict[str, object],
        action: object,
        trigger_info: dict[str, object],
    ) -> object:
        del hass
        self.attached.append((dict(config), action, dict(trigger_info)))
        return self._unsubscribe

    def _unsubscribe(self) -> None:
        self.unsubscribed += 1


def _event(domain: str, service: str, entity_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        data={
            "domain": domain,
            "service": service,
            "service_data": {"entity_id": entity_id},
        }
    )


def _trigger_config_payload(*, action: str = "turn_on") -> dict[str, object]:
    payload = _config_payload()
    payload["devices"]["wireless_switches"].append(  # type: ignore[index]
        {
            "id": "sw_mirror",
            "name": "Зеркало",
            "deviceId": "device_demo_mirror",
            "triggerSubtypes": ["1_single", "2_single"],
            "buttons": ["left", "right"],
            "pressTypes": ["single", "double"],
        }
    )
    payload["switchBindings"].append(  # type: ignore[index]
        {
            "switchId": "sw_mirror",
            "triggerSubtype": "1_single",
            "action": action,
            "targets": {
                "lightTargets": ["light_main"],
                "groupIds": [],
                "roles": [],
            },
        }
    )
    return payload


def _make_runtime(
    hass: _FakeHass,
    *,
    commands_enabled: bool = False,
    executor: object | None = None,
    ownership_store: object | None = None,
    now_ms: object | None = None,
    payload: dict[str, object] | None = None,
    payloads: list[dict[str, object]] | None = None,
    device_automation_api: object | None = None,
    reserved_target_ids: tuple[str, ...] = (),
    reserved_entity_ids_provider: object | None = None,
) -> RoomLightingRuntime:
    shadow = RoomLightingShadowService(_MemoryShadowStore())
    if payloads is not None:
        configs = [config_from_payload(item) for item in payloads]
    else:
        room_payload = payload if payload is not None else _config_payload()
        if commands_enabled:
            room_payload = {**room_payload, "commandsEnabled": True}
        configs = [config_from_payload(room_payload)]
    service = _ConfigService(*configs)
    return RoomLightingRuntime(
        hass,
        service,
        shadow,
        executor=executor,
        ownership_store=ownership_store,
        now_ms=now_ms or (lambda: _NOW_MS),
        track_state_changes=lambda hass, entities, callback: (lambda: None),
        track_interval=lambda hass, callback, interval: (lambda: None),
        listen_bus=lambda hass, event_type, callback: (lambda: None),
        device_automation_api=device_automation_api,
        reserved_target_ids=reserved_target_ids,
        reserved_entity_ids_provider=reserved_entity_ids_provider,
    )


def _seed_presence_and_light(hass: _FakeHass) -> None:
    hass.states.set("binary_sensor.demo_presence", "on", last_changed=_NOW_DT)
    hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)


def _bathroom_payload() -> dict[str, object]:
    payload = _config_payload()
    payload["roomId"] = "room_demo_bathroom"
    payload["name"] = "Ванная"
    payload["devices"]["sensors"].append(  # type: ignore[index]
        {
            "id": "sensor_demo_humidity",
            "name": "Влажность",
            "kind": "humidity",
            "entityId": "sensor.demo_humidity",
            "autoAdoptOverride": None,
        }
    )
    payload["devices"]["light_targets"].append(  # type: ignore[index]
        {
            "id": "light_second",
            "name": "Свет второй",
            "kind": "switch",
            "entityId": "switch.demo_second",
            "role": None,
            "groupId": None,
            "brightness": False,
            "color_temperature": False,
            "autoAdoptOverride": None,
        }
    )
    payload["devices"]["auxiliaries"] = [  # type: ignore[index]
        {
            "id": "aux_fan",
            "name": "Вытяжка",
            "kind": "fan",
            "entityId": "switch.demo_fan",
            "autoAdoptOverride": None,
        }
    ]
    payload["auxiliary"] = {  # type: ignore[index]
        "fan": {
            "targetId": "aux_fan",
            "humidityThreshold": 65,
            "dayOffSeconds": 1800,
            "quietStart": "06:00",
            "dayStart": "08:00",
            "nightStart": "22:00",
        }
    }
    return payload


async def test_runtime_records_auxiliary_shadow_without_any_command() -> None:
    hass = _FakeHass()
    hass.states.set("light.demo_main", "on", last_changed=_NOW_DT)
    hass.states.set("switch.demo_second", "off", last_changed=_NOW_DT)
    hass.states.set("sensor.demo_humidity", "70", last_changed=_NOW_DT)
    hass.states.set("switch.demo_fan", "off", last_changed=_NOW_DT)
    config = config_from_payload(_bathroom_payload())
    shadow = RoomLightingShadowService(_MemoryShadowStore())
    runtime = RoomLightingRuntime(
        hass,
        _ConfigService(config),
        shadow,
        now_ms=lambda: _NOW_MS,
        track_state_changes=lambda hass, entities, callback: (lambda: None),
        track_interval=lambda hass, callback, interval: (lambda: None),
        listen_bus=lambda hass, event_type, callback: (lambda: None),
    )

    await runtime.start(hass, "entry")
    try:
        entries = shadow.journal_payload()["entries"]
        auxiliary_entries = [entry for entry in entries if "auxiliary" in entry]
        assert len(auxiliary_entries) == 1
        auxiliary = auxiliary_entries[0]["auxiliary"]
        # 10:00 UTC is the day band: a lit room above the humidity threshold
        # asks for the fan; the pure engine records it and nothing is sent.
        assert auxiliary["band"] == "day"
        assert auxiliary["lights"] == ["on", "off"]
        assert auxiliary["humidity"] == 70.0
        assert auxiliary["fan"] == "off"
        assert auxiliary["transition"] == "bathroom_hold"
        assert auxiliary["action"] == "turn_on"
        assert auxiliary["fanOwned"] is False
        assert hass.services.calls == []
    finally:
        await runtime.stop()


async def test_state_provider_builds_context_from_fake_hass() -> None:
    hass = _FakeHass()
    last = datetime(2026, 9, 11, 9, 59, tzinfo=_TZ)
    hass.states.set("binary_sensor.demo_presence", "on", last_changed=last)
    hass.states.set(
        "light.demo_main",
        "on",
        {"brightness": 153, "color_temp_kelvin": 3000},
        last_changed=last,
    )
    hass.states.set("sensor.demo_lux", "420.5", last_changed=last)
    hass.states.set(
        "sun.sun",
        "above_horizon",
        {
            "next_rising": "2026-09-11T05:12:00+00:00",
            "next_setting": "2026-09-11T17:40:00+00:00",
        },
    )
    config = config_from_payload(_config_payload(with_lux=True))

    context = await build_context(hass, config, _NOW_MS)

    presence = next(
        sensor
        for sensor in context.sensors
        if sensor.kind is SensorKind.PRESENCE
    )
    lux = next(
        sensor
        for sensor in context.sensors
        if sensor.kind is SensorKind.ILLUMINANCE
    )
    light = context.light("light_main")
    assert presence.state is SensorState.ON
    assert presence.last_changed == int(last.timestamp() * 1000)
    assert lux.lux == 420.5
    assert lux.lux_healthy is True
    assert light is not None
    assert light.state is SensorState.ON
    assert light.brightness == 60
    assert light.color_temperature == 3000
    assert context.sunrise == time(5, 12)
    assert context.sunset == time(17, 40)


async def test_state_provider_marks_unknown_and_unavailable() -> None:
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "unavailable")
    config = config_from_payload(_config_payload())

    context = await build_context(hass, config, _NOW_MS)

    presence = context.sensors[0]
    light = context.light("light_main")
    assert presence.state is SensorState.UNAVAILABLE
    # A missing light entity degrades to unknown instead of raising.
    assert light is not None
    assert light.state is SensorState.UNKNOWN


async def test_runtime_shadow_mode_never_calls_executor() -> None:
    hass = _FakeHass()
    _seed_presence_and_light(hass)
    executor = _SpyExecutor()
    runtime = _make_runtime(hass, commands_enabled=False, executor=executor)

    await runtime.start(hass, "entry")
    try:
        assert runtime.configs()[0].commands_enabled is False
        assert executor.calls == []
        entries = runtime._shadow.journal_payload()["entries"]  # type: ignore[attr-defined]
        assert entries
        entry = entries[-1]
        assert entry["roomId"] == _ROOM_ID
        assert entry["commandsEnabled"] is False
        assert entry["mode"] == "shadow"
        assert entry["commands"][0]["action"] == LightAction.TURN_ON.value
    finally:
        await runtime.stop()


async def test_runtime_state_event_reaches_engine_and_shadow() -> None:
    hass = _FakeHass()
    _seed_presence_and_light(hass)
    runtime = _make_runtime(hass, commands_enabled=False)

    await runtime.start(hass, "entry")
    try:
        before = len(runtime._shadow.journal_payload()["entries"])  # type: ignore[attr-defined]
        runtime._state_event(
            SimpleNamespace(data={"entity_id": "binary_sensor.demo_presence"})
        )
        await asyncio.gather(*hass.tasks)
        after = len(runtime._shadow.journal_payload()["entries"])  # type: ignore[attr-defined]
        assert after == before + 1
    finally:
        await runtime.stop()


async def test_runtime_dispatches_plan_when_commands_enabled() -> None:
    hass = _FakeHass()
    _seed_presence_and_light(hass)
    executor = _SpyExecutor()
    runtime = _make_runtime(hass, commands_enabled=True, executor=executor)

    await runtime.start(hass, "entry")
    try:
        actions = [command.action for command in executor.calls]
        assert actions == [
            LightAction.TURN_ON,
            LightAction.SET_BRIGHTNESS,
            LightAction.SET_COLOR_TEMPERATURE,
        ]
        entries = runtime._shadow.journal_payload()["entries"]  # type: ignore[attr-defined]
        entry = entries[-1]
        assert entry["commandsEnabled"] is True
        assert entry["mode"] == "live"
        assert entry["commands"][0]["action"] == LightAction.TURN_ON.value
        # A confirmed receipt grants proven automatic ownership.
        ownership = runtime._ownership.snapshots_for(_ROOM_ID, {"light_main"})  # type: ignore[attr-defined]
        assert ownership
        assert ownership[-1].source is OwnershipSource.AUTO
        assert ownership[-1].confirmed is True
    finally:
        await runtime.stop()


async def test_reserved_target_blocks_direct_command_without_executor_call() -> None:
    hass = _FakeHass()
    _seed_presence_and_light(hass)
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass,
        commands_enabled=True,
        executor=executor,
        reserved_target_ids=("light_main",),
    )
    config = config_from_payload(_config_payload(commands_enabled=True))

    receipt = await runtime.async_execute_command(
        config, PlannedCommand("light_main", LightAction.TURN_ON)
    )

    assert receipt == {
        "confirmed": False,
        "blocked": True,
        "reason": "reserved_command_target",
    }
    assert executor.calls == []


async def test_reserved_entity_blocks_dispatch_and_binding_without_executor_call() -> None:
    hass = _FakeHass()
    _seed_presence_and_light(hass)
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass,
        commands_enabled=True,
        executor=executor,
        payload=_trigger_config_payload(),
        reserved_entity_ids_provider=lambda: ("light.demo_main",),
    )

    await runtime.start(hass, "entry")
    try:
        assert executor.calls == []
        await runtime._handle_device_trigger(
            runtime.configs()[0], "device_demo_mirror", "sw_mirror", "1_single"
        )
        assert executor.calls == []
    finally:
        await runtime.stop()


async def test_reserved_power_blocks_dispatch_before_power_or_light_call() -> None:
    hass = _FakeHass()
    _seed_presence_and_light(hass)
    hass.states.set(POWER_ENTITY, "off", last_changed=_NOW_DT)
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass,
        commands_enabled=True,
        executor=executor,
        payload=_config_payload(power_switch_entity=POWER_ENTITY),
        reserved_entity_ids_provider=lambda: (POWER_ENTITY,),
    )

    await runtime.start(hass, "entry")
    try:
        assert executor.calls == []
    finally:
        await runtime.stop()


async def test_per_room_commands_enabled_field_runs_executor() -> None:
    hass = _FakeHass()
    _seed_presence_and_light(hass)
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass,
        executor=executor,
        payload=_config_payload(commands_enabled=True),
    )

    await runtime.start(hass, "entry")
    try:
        assert runtime.configs()[0].commands_enabled is True
        assert executor.calls
        entry = runtime._shadow.journal_payload()["entries"][-1]  # type: ignore[attr-defined]
        assert entry["commandsEnabled"] is True
    finally:
        await runtime.stop()


async def test_per_room_commands_default_stays_shadow() -> None:
    hass = _FakeHass()
    _seed_presence_and_light(hass)
    executor = _SpyExecutor()
    runtime = _make_runtime(hass, executor=executor)

    await runtime.start(hass, "entry")
    try:
        assert runtime.configs()[0].commands_enabled is False
        assert executor.calls == []
        entry = runtime._shadow.journal_payload()["entries"][-1]  # type: ignore[attr-defined]
        assert entry["commandsEnabled"] is False
        assert entry["mode"] == "shadow"
    finally:
        await runtime.stop()


async def test_ownership_marks_foreign_manual_and_own_call_grants_no_auto() -> None:
    hass = _FakeHass()
    _seed_presence_and_light(hass)
    runtime = _make_runtime(hass, commands_enabled=False)

    await runtime.start(hass, "entry")
    try:
        runtime._service_event(_event("light", "turn_on", "light.demo_main"))
        manual = runtime._ownership.snapshots_for(_ROOM_ID, {"light_main"})[-1]  # type: ignore[attr-defined]
        assert manual.source is OwnershipSource.MANUAL
        assert manual.confirmed is True

        # Our own call_service event, before any read-back receipt, must not
        # grant automatic ownership.
        runtime._executing_actions["light.demo_main"] = "turn_on"
        runtime._service_event(_event("light", "turn_on", "light.demo_main"))
        records = runtime._ownership.snapshots_for(_ROOM_ID, {"light_main"})  # type: ignore[attr-defined]
        assert all(record.source is not OwnershipSource.AUTO for record in records)
    finally:
        await runtime.stop()


async def test_failed_receipt_does_not_grant_auto() -> None:
    hass = _FakeHass()
    _seed_presence_and_light(hass)
    executor = _SpyExecutor(confirmed=False)
    runtime = _make_runtime(hass, commands_enabled=True, executor=executor)

    await runtime.start(hass, "entry")
    try:
        assert executor.calls
        records = runtime._ownership.snapshots_for(_ROOM_ID, {"light_main"})  # type: ignore[attr-defined]
        assert all(record.source is not OwnershipSource.AUTO for record in records)
    finally:
        await runtime.stop()


async def test_foreign_off_while_own_command_in_flight_stays_manual() -> None:
    hass = _FakeHass()
    _seed_presence_and_light(hass)
    runtime = _make_runtime(hass, commands_enabled=False)

    await runtime.start(hass, "entry")
    try:
        # The runtime is mid turn_on on this entity: a foreign turn_off racing
        # it must still be attributed to the person, never to automation.
        runtime._executing_actions["light.demo_main"] = "turn_on"
        runtime._service_event(_event("light", "turn_off", "light.demo_main"))
        latest = runtime._ownership.snapshots_for(_ROOM_ID, {"light_main"})[-1]  # type: ignore[attr-defined]
        assert latest.source is OwnershipSource.MANUAL
        assert (
            runtime._ownership.last_manual_off_at(_ROOM_ID, {"light_main"})  # type: ignore[attr-defined]
            is not None
        )

        # The runtime's own matching call_service event grants no AUTO before
        # the executor confirms it through a read-back receipt.
        runtime._executing_actions["light.demo_main"] = "turn_on"
        runtime._service_event(_event("light", "turn_on", "light.demo_main"))
        latest = runtime._ownership.snapshots_for(_ROOM_ID, {"light_main"})[-1]  # type: ignore[attr-defined]
        assert latest.source is OwnershipSource.MANUAL
    finally:
        await runtime.stop()


async def test_repeat_without_state_change_does_not_resend() -> None:
    hass = _FakeHass()
    _seed_presence_and_light(hass)
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=RoomLightingHaExecutor()
    )

    await runtime.start(hass, "entry")
    try:
        first = len(hass.services.calls)
        assert first > 0
        await runtime.async_process([_ROOM_ID])
        assert len(hass.services.calls) == first
    finally:
        await runtime.stop()


class _Clock:
    def __init__(self, value: int) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value


def _dt(milliseconds: int) -> datetime:
    return datetime.fromtimestamp(milliseconds / 1000, _TZ)


async def test_manual_off_protection_releases_after_timer_and_absence() -> None:
    payload = _config_payload(minimum_interval_seconds=60, stable_absence_seconds=5)
    clock = _Clock(_NOW_MS)
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_dt(_NOW_MS))
    hass.states.set("light.demo_main", "off", last_changed=_dt(_NOW_MS))
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass,
        commands_enabled=True,
        executor=executor,
        now_ms=clock,
        payload=payload,
    )
    await runtime.start(hass, "entry")
    try:
        runtime._service_event(_event("light", "turn_off", "light.demo_main"))
        await asyncio.gather(*hass.tasks)
        # The minimum interval has not elapsed yet.
        await runtime.async_process()
        assert executor.calls == []
        # Stable absence while the timer runs does not command by itself.
        clock.value = _NOW_MS + 70_000
        hass.states.set("binary_sensor.demo_presence", "off", last_changed=_dt(clock.value))
        await runtime.async_process()
        assert executor.calls == []
        # Expired protection + confirmed absence + new presence resume automation.
        clock.value = _NOW_MS + 80_000
        hass.states.set("binary_sensor.demo_presence", "on", last_changed=_dt(clock.value))
        await runtime.async_process()
        assert executor.calls
    finally:
        await runtime.stop()


async def test_manual_off_protection_survives_restart() -> None:
    payload = _config_payload(minimum_interval_seconds=60, stable_absence_seconds=5)
    ownership_store = _MemoryOwnershipStore()
    clock = _Clock(_NOW_MS)
    t0 = _NOW_MS

    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_dt(t0))
    hass.states.set("light.demo_main", "off", last_changed=_dt(t0))
    executor1 = _SpyExecutor()
    runtime1 = _make_runtime(
        hass,
        commands_enabled=True,
        executor=executor1,
        ownership_store=ownership_store,
        now_ms=clock,
        payload=payload,
    )
    await runtime1.start(hass, "entry")
    runtime1._service_event(_event("light", "turn_off", "light.demo_main"))
    await asyncio.gather(*hass.tasks)
    assert runtime1._ownership.last_manual_off_at(_ROOM_ID, {"light_main"}) == t0  # type: ignore[attr-defined]
    assert ownership_store.payload is not None
    await runtime1.stop()

    # "Restart": a fresh runtime and fresh HA state share the old Store.
    clock.value = t0 + 2_000
    hass2 = _FakeHass()
    hass2.states.set("binary_sensor.demo_presence", "on", last_changed=_dt(clock.value))
    hass2.states.set("light.demo_main", "off", last_changed=_dt(clock.value))
    executor2 = _SpyExecutor()
    runtime2 = _make_runtime(
        hass2,
        commands_enabled=True,
        executor=executor2,
        ownership_store=ownership_store,
        now_ms=clock,
        payload=payload,
    )
    await runtime2.start(hass2, "entry")
    try:
        config = runtime2.configs()[0]
        protection = runtime2._protection_for(config)  # type: ignore[attr-defined]
        assert protection.active is True
        assert protection.started_at == t0
        # Minimum interval not elapsed: automation must not turn the light on.
        assert executor2.calls == []
        clock.value = t0 + 90_000
        hass2.states.set("binary_sensor.demo_presence", "on", last_changed=_dt(clock.value))
        await runtime2.async_process()
        assert executor2.calls == []
        # Confirmed absence followed by a new presence releases the protection.
        clock.value = t0 + 100_000
        hass2.states.set("binary_sensor.demo_presence", "off", last_changed=_dt(clock.value))
        await runtime2.async_process()
        assert executor2.calls == []
        clock.value = t0 + 110_000
        hass2.states.set("binary_sensor.demo_presence", "on", last_changed=_dt(clock.value))
        await runtime2.async_process()
        assert executor2.calls
    finally:
        await runtime2.stop()


async def test_restart_keeps_manual_protection_while_presence_active() -> None:
    payload = _config_payload(minimum_interval_seconds=60, stable_absence_seconds=5)
    ownership_store = _MemoryOwnershipStore()
    clock = _Clock(_NOW_MS)
    t0 = _NOW_MS

    hass = _FakeHass()
    _seed_presence_and_light(hass)
    runtime1 = _make_runtime(
        hass,
        commands_enabled=False,
        ownership_store=ownership_store,
        now_ms=clock,
        payload=payload,
    )
    await runtime1.start(hass, "entry")
    runtime1._service_event(_event("light", "turn_off", "light.demo_main"))
    await asyncio.gather(*hass.tasks)
    assert runtime1._ownership.last_manual_off_at(_ROOM_ID, {"light_main"}) == t0  # type: ignore[attr-defined]
    await runtime1.stop()

    # Restart long after the interval with the light off, but the room's
    # presence sensor still reads on: the person is demonstrably there, so the
    # manual hold must survive until absence is confirmed again.
    clock.value = t0 + 600_000
    hass2 = _FakeHass()
    hass2.states.set("binary_sensor.demo_presence", "on", last_changed=_dt(clock.value))
    hass2.states.set("light.demo_main", "off", last_changed=_dt(t0))
    runtime2 = _make_runtime(
        hass2,
        commands_enabled=False,
        ownership_store=ownership_store,
        now_ms=clock,
        payload=payload,
    )
    await runtime2.start(hass2, "entry")
    try:
        assert runtime2._ownership.last_manual_off_at(_ROOM_ID, {"light_main"}) == t0  # type: ignore[attr-defined]
        config = runtime2.configs()[0]
        protection = runtime2._protection_for(config)  # type: ignore[attr-defined]
        assert protection.active is True
        assert protection.started_at == t0
    finally:
        await runtime2.stop()


async def test_restart_prunes_manual_protection_when_presence_not_active() -> None:
    payload = _config_payload(minimum_interval_seconds=60, stable_absence_seconds=5)
    ownership_store = _MemoryOwnershipStore()
    clock = _Clock(_NOW_MS)
    t0 = _NOW_MS

    hass = _FakeHass()
    _seed_presence_and_light(hass)
    runtime1 = _make_runtime(
        hass,
        commands_enabled=False,
        ownership_store=ownership_store,
        now_ms=clock,
        payload=payload,
    )
    await runtime1.start(hass, "entry")
    runtime1._service_event(_event("light", "turn_off", "light.demo_main"))
    await asyncio.gather(*hass.tasks)
    await runtime1.stop()

    # Restart after the interval with the light off and nobody present: the
    # stale hold is dropped so it cannot block automation forever.
    clock.value = t0 + 600_000
    hass2 = _FakeHass()
    hass2.states.set("binary_sensor.demo_presence", "off", last_changed=_dt(clock.value))
    hass2.states.set("light.demo_main", "off", last_changed=_dt(t0))
    runtime2 = _make_runtime(
        hass2,
        commands_enabled=False,
        ownership_store=ownership_store,
        now_ms=clock,
        payload=payload,
    )
    await runtime2.start(hass2, "entry")
    try:
        assert runtime2._ownership.last_manual_off_at(_ROOM_ID, {"light_main"}) is None  # type: ignore[attr-defined]
        assert runtime2._ownership.snapshots_for(_ROOM_ID, {"light_main"}) == ()  # type: ignore[attr-defined]
        config = runtime2.configs()[0]
        assert runtime2._protection_for(config).active is False  # type: ignore[attr-defined]
    finally:
        await runtime2.stop()


async def test_restart_prunes_manual_protection_without_presence_sensor() -> None:
    payload = _config_payload(minimum_interval_seconds=60, stable_absence_seconds=5)
    payload["devices"]["sensors"] = []  # type: ignore[index]
    ownership_store = _MemoryOwnershipStore()
    clock = _Clock(_NOW_MS)
    t0 = _NOW_MS

    hass = _FakeHass()
    hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)
    runtime1 = _make_runtime(
        hass,
        commands_enabled=False,
        ownership_store=ownership_store,
        now_ms=clock,
        payload=payload,
    )
    await runtime1.start(hass, "entry")
    runtime1._service_event(_event("light", "turn_off", "light.demo_main"))
    await asyncio.gather(*hass.tasks)
    await runtime1.stop()

    # No presence sensor can ever release the hold through the engine, so the
    # expired hold on an off light is dropped at startup.
    clock.value = t0 + 600_000
    hass2 = _FakeHass()
    hass2.states.set("light.demo_main", "off", last_changed=_dt(t0))
    runtime2 = _make_runtime(
        hass2,
        commands_enabled=False,
        ownership_store=ownership_store,
        now_ms=clock,
        payload=payload,
    )
    await runtime2.start(hass2, "entry")
    try:
        assert runtime2._ownership.last_manual_off_at(_ROOM_ID, {"light_main"}) is None  # type: ignore[attr-defined]
    finally:
        await runtime2.stop()



async def test_restart_sets_unobserved_and_keeps_persisted_auto_on() -> None:
    store = _MemoryOwnershipStore()
    store.payload = {
        "version": 1,
        "records": [
            {
                "targetId": "light_main",
                "source": "auto",
                "confirmed": True,
                "at": _NOW_MS - 600_000,
            }
        ],
        "manualOff": {},
    }
    hass = _FakeHass()
    hass.states.set(
        "binary_sensor.demo_presence", "off", last_changed=_dt(_NOW_MS - 900_000)
    )
    hass.states.set(
        "light.demo_main",
        "on",
        {"brightness": 60},
        last_changed=_dt(_NOW_MS - 600_000),
    )
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass,
        commands_enabled=True,
        executor=executor,
        ownership_store=store,
        now_ms=_Clock(_NOW_MS),
    )
    await runtime.start(hass, "entry")
    try:
        # The unobserved restart gap is not absence: no turn-off is dispatched.
        context = await runtime.async_context_for(runtime.configs()[0])
        assert context.unobserved_since == _NOW_MS
        assert executor.calls == []
    finally:
        await runtime.stop()


async def test_device_trigger_shadow_logs_intent_without_commands(caplog) -> None:
    caplog.set_level(
        logging.INFO,
        logger="custom_components.hausman_hub.application.room_lighting_runtime",
    )
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_NOW_DT)
    hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)
    api = _FakeDeviceAutomationApi()
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass,
        commands_enabled=False,
        executor=executor,
        payload=_trigger_config_payload(),
        device_automation_api=api,
    )

    await runtime.start(hass, "entry")
    try:
        assert len(api.attached) == 1
        trigger_config, action, trigger_info = api.attached[0]
        assert trigger_config == {
            "platform": "device",
            "device_id": "device_demo_mirror",
            "domain": "mqtt",
            "type": "action",
            "subtype": "1_single",
        }
        assert trigger_info["domain"] == "hausman_hub"
        assert trigger_info["name"] == "managed-room-lighting-runtime"
        assert str(trigger_info["trigger_data"]["id"]).startswith("room-lighting-")  # type: ignore[index]
        await action({}, None)  # type: ignore[operator]
        assert executor.calls == []
        ownership = runtime._ownership.snapshots_for(_ROOM_ID, {"light_main"})  # type: ignore[attr-defined]
        assert ownership
        assert ownership[-1].source is OwnershipSource.MANUAL
        assert "device trigger" in caplog.text
    finally:
        await runtime.stop()
    assert api.unsubscribed == 1


async def test_device_trigger_dispatches_manual_action_when_enabled() -> None:
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_NOW_DT)
    hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)
    api = _FakeDeviceAutomationApi()
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass,
        commands_enabled=True,
        executor=executor,
        payload=_trigger_config_payload(action="turn_on"),
        device_automation_api=api,
    )

    await runtime.start(hass, "entry")
    try:
        _, action, _ = api.attached[0]
        await action({}, None)  # type: ignore[operator]
        assert len(executor.calls) == 1
        assert executor.calls[0].action is LightAction.TURN_ON
        ownership = runtime._ownership.snapshots_for(_ROOM_ID, {"light_main"})  # type: ignore[attr-defined]
        assert ownership[-1].source is OwnershipSource.MANUAL
    finally:
        await runtime.stop()


async def test_device_trigger_duplicate_event_does_not_toggle_twice() -> None:
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_NOW_DT)
    hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)
    api = _FakeDeviceAutomationApi()
    executor = _SpyExecutor()
    clock = _Clock(_NOW_MS)
    runtime = _make_runtime(
        hass,
        commands_enabled=True,
        executor=executor,
        now_ms=clock,
        payload=_trigger_config_payload(action="turn_on"),
        device_automation_api=api,
    )

    await runtime.start(hass, "entry")
    try:
        _, action, _ = api.attached[0]
        await action({}, None)  # type: ignore[operator]
        await action({}, None)  # repeated MQTT delivery inside the window
        assert len(executor.calls) == 1
        clock.value = _NOW_MS + 3_000
        await action({}, None)  # a new press outside the window is delivered
        assert len(executor.calls) == 2
    finally:
        await runtime.stop()


async def test_device_trigger_sequence_selects_progressive_configured_steps() -> None:
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_NOW_DT)
    hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)
    api = _FakeDeviceAutomationApi()
    executor = _SpyExecutor()
    clock = _Clock(_NOW_MS)
    payload = _trigger_config_payload()
    payload["switchBindings"] = [  # type: ignore[index]
        {
            "switchId": "sw_mirror",
            "triggerSubtype": "1_single",
            "action": "turn_on",
            "sequenceIndex": 1,
            "sequenceWindowSeconds": 3,
            "targets": {"lightTargets": ["light_main"], "groupIds": [], "roles": []},
        },
        {
            "switchId": "sw_mirror",
            "triggerSubtype": "1_single",
            "action": "set_max",
            "brightness": 100,
            "colorTemperature": 4000,
            "sequenceIndex": 2,
            "sequenceWindowSeconds": 3,
            "targets": {"lightTargets": ["light_main"], "groupIds": [], "roles": []},
        },
    ]
    runtime = _make_runtime(
        hass,
        commands_enabled=True,
        executor=executor,
        now_ms=clock,
        payload=payload,
        device_automation_api=api,
    )

    await runtime.start(hass, "entry")
    try:
        _, action, _ = api.attached[0]
        await action({}, None)  # type: ignore[operator]
        clock.value += 500
        await action({}, None)  # type: ignore[operator]
        assert [call.action for call in executor.calls] == [
            LightAction.TURN_ON,
            LightAction.SET_BRIGHTNESS,
        ]
        assert executor.calls[-1].brightness == 100
        assert executor.calls[-1].color_temperature == 4000
        clock.value += 3_100
        await action({}, None)  # type: ignore[operator]
        assert executor.calls[-1].action is LightAction.TURN_ON
    finally:
        await runtime.stop()


async def test_device_trigger_return_to_auto_clears_manual_off_without_calling_light() -> None:
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_NOW_DT)
    hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)
    api = _FakeDeviceAutomationApi()
    executor = _SpyExecutor()
    payload = _trigger_config_payload(action="return_to_auto")
    runtime = _make_runtime(
        hass,
        commands_enabled=False,
        executor=executor,
        payload=payload,
        device_automation_api=api,
    )

    await runtime.start(hass, "entry")
    try:
        runtime._ownership.record_manual(  # type: ignore[attr-defined]
            _ROOM_ID, "light_main", _NOW_MS, confirmed=True, turned_off=True
        )
        _, action, _ = api.attached[0]
        await action({}, None)  # type: ignore[operator]
        assert executor.calls == []
        assert runtime._ownership.last_manual_off_at(_ROOM_ID, {"light_main"}) is None  # type: ignore[attr-defined]
        ownership = runtime._ownership.snapshots_for(_ROOM_ID, {"light_main"})  # type: ignore[attr-defined]
        assert ownership[-1].source is OwnershipSource.AUTO
    finally:
        await runtime.stop()


async def test_device_trigger_shadow_toggle_off_marks_manual_off() -> None:
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_NOW_DT)
    hass.states.set("light.demo_main", "on", last_changed=_NOW_DT)
    api = _FakeDeviceAutomationApi()
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass,
        commands_enabled=False,
        executor=executor,
        payload=_trigger_config_payload(action="toggle"),
        device_automation_api=api,
    )

    await runtime.start(hass, "entry")
    try:
        _, action, _ = api.attached[0]
        await action({}, None)  # type: ignore[operator]
        assert executor.calls == []
        assert (
            runtime._ownership.last_manual_off_at(_ROOM_ID, {"light_main"})  # type: ignore[attr-defined]
            is not None
        )
    finally:
        await runtime.stop()


async def test_device_trigger_attaches_after_running() -> None:
    class _ImmediateApi(_FakeDeviceAutomationApi):
        async def async_attach_trigger(self, hass, config, action, trigger_info):
            self.attached.append((dict(config), action, dict(trigger_info)))
            await action({}, None)
            return self._unsubscribe

    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_NOW_DT)
    hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)
    api = _ImmediateApi()
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass,
        commands_enabled=True,
        executor=executor,
        payload=_trigger_config_payload(action="turn_on"),
        device_automation_api=api,
    )

    await runtime.start(hass, "entry")
    try:
        # The press fired during attach, so the running guard was already open.
        assert len(executor.calls) == 1
    finally:
        await runtime.stop()


async def test_device_trigger_platform_unavailable_logs_warning(caplog) -> None:
    caplog.set_level(
        logging.WARNING,
        logger="custom_components.hausman_hub.application.room_lighting_runtime",
    )
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_NOW_DT)
    hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)
    runtime = _make_runtime(
        hass,
        commands_enabled=False,
        payload=_trigger_config_payload(),
        device_automation_api=None,
    )

    await runtime.start(hass, "entry")
    try:
        assert runtime.running is True
        assert "device triggers are disabled" in caplog.text
    finally:
        await runtime.stop()


async def test_device_trigger_attaches_for_each_room() -> None:
    first = config_from_payload(_trigger_config_payload())
    second_payload = _trigger_config_payload()
    second_payload["roomId"] = "room_demo_second"
    second = config_from_payload(second_payload)
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_NOW_DT)
    hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)
    api = _FakeDeviceAutomationApi()
    runtime = RoomLightingRuntime(
        hass,
        _ConfigService(first, second),
        RoomLightingShadowService(_MemoryShadowStore()),
        executor=_SpyExecutor(),
        now_ms=lambda: _NOW_MS,
        track_state_changes=lambda hass, entities, callback: (lambda: None),
        track_interval=lambda hass, callback, interval: (lambda: None),
        listen_bus=lambda hass, event_type, callback: (lambda: None),
        device_automation_api=api,
    )

    await runtime.start(hass, "entry")
    try:
        # The same physical device in two rooms must be attached twice.
        assert len(api.attached) == 2
        ids = {str(spec[2]["trigger_data"]["id"]) for spec in api.attached}  # type: ignore[index]
        assert ids == {
            "room-lighting-room_demo_entry-sw_mirror-1_single",
            "room-lighting-room_demo_second-sw_mirror-1_single",
        }
    finally:
        await runtime.stop()


async def test_registered_event_callbacks_are_ha_callbacks() -> None:
    hass = _FakeHass()
    _seed_presence_and_light(hass)
    captured: dict[str, object] = {}

    def track_state_changes(h, entities, callback):
        captured["state"] = callback
        return lambda: None

    def track_interval(h, callback, interval):
        captured["clock"] = callback
        return lambda: None

    def listen_bus(h, event_type, callback):
        captured["service"] = callback
        return lambda: None

    runtime = RoomLightingRuntime(
        hass,
        _ConfigService(config_from_payload(_config_payload())),
        RoomLightingShadowService(_MemoryShadowStore()),
        executor=_SpyExecutor(),
        now_ms=lambda: _NOW_MS,
        track_state_changes=track_state_changes,
        track_interval=track_interval,
        listen_bus=listen_bus,
    )
    await runtime.start(hass, "entry")
    try:
        # HA runs a callback on the event loop only when it carries this marker;
        # otherwise it is dispatched to an executor thread.
        for name in ("state", "clock", "service"):
            callback = captured[name]
            assert getattr(callback, "_hass_callback", False) is True, name
    finally:
        await runtime.stop()


async def test_create_task_from_worker_thread_uses_the_event_loop() -> None:
    loop = asyncio.get_running_loop()
    recorded: list[object] = []

    class _LoopHass:
        def __init__(self) -> None:
            self.loop = loop

        def async_create_task(self, coroutine):
            # Home Assistant refuses an off-loop call; emulate that strictness.
            if asyncio.get_running_loop() is not self.loop:
                raise RuntimeError("not on the event loop")
            task = asyncio.ensure_future(coroutine)
            recorded.append(task)
            return task

    runtime = RoomLightingRuntime(
        _LoopHass(),
        _ConfigService(config_from_payload(_config_payload())),
        RoomLightingShadowService(_MemoryShadowStore()),
        executor=_SpyExecutor(),
        now_ms=lambda: _NOW_MS,
        track_state_changes=lambda h, e, c: (lambda: None),
        track_interval=lambda h, c, i: (lambda: None),
        listen_bus=lambda h, e, c: (lambda: None),
    )

    thread = threading.Thread(
        target=lambda: runtime._create_task(asyncio.sleep(0))
    )
    thread.start()
    thread.join()
    await asyncio.sleep(0.05)
    assert len(recorded) == 1
    await asyncio.gather(*recorded)


def _light_executor(hass: _FakeHass, *, inverted: bool) -> RoomLightingHaExecutor:
    hass.states.set(
        "light.demo_main",
        "on",
        {
            "color_temp_kelvin": 3000,
            "min_color_temp_kelvin": 2000,
            "max_color_temp_kelvin": 6535,
        },
    )
    return RoomLightingHaExecutor(
        {
            "light_main": LightTarget(
                id="light_main",
                name="Люстра",
                kind=LightKind.LIGHT,
                brightness=True,
                color_temperature=True,
                entity_id="light.demo_main",
                color_temp_inverted=inverted,
            )
        }
    )


async def test_executor_reflects_inverted_kelvin_around_neutral() -> None:
    hass = _FakeHass()
    executor = _light_executor(hass, inverted=True)

    await executor.execute(
        hass,
        PlannedCommand(
            "light_main",
            LightAction.SET_COLOR_TEMPERATURE,
            color_temperature=2200,
        ),
    )
    assert hass.services.calls[-1][2]["color_temp_kelvin"] == 3800

    await executor.execute(
        hass,
        PlannedCommand(
            "light_main",
            LightAction.SET_COLOR_TEMPERATURE,
            color_temperature=3000,
        ),
    )
    assert hass.services.calls[-1][2]["color_temp_kelvin"] == 3000


async def test_executor_clamps_inverted_kelvin_to_device_bounds() -> None:
    hass = _FakeHass()
    executor = _light_executor(hass, inverted=True)

    await executor.execute(
        hass,
        PlannedCommand(
            "light_main",
            LightAction.SET_COLOR_TEMPERATURE,
            color_temperature=6500,
        ),
    )
    # 2 * 3000 - 6500 = -500, clamped to the device minimum of 2000 K.
    assert hass.services.calls[-1][2]["color_temp_kelvin"] == 2000


async def test_executor_keeps_kelvin_for_normal_target() -> None:
    hass = _FakeHass()
    executor = _light_executor(hass, inverted=False)

    await executor.execute(
        hass,
        PlannedCommand(
            "light_main",
            LightAction.SET_COLOR_TEMPERATURE,
            color_temperature=2200,
        ),
    )
    assert hass.services.calls[-1][2]["color_temp_kelvin"] == 2200


async def test_executor_never_sends_light_transition_to_switch() -> None:
    hass = _FakeHass()
    hass.states.set("switch.demo_spots", "on")
    executor = RoomLightingHaExecutor(
        {
            "spots": LightTarget(
                id="spots",
                name="Точки",
                kind=LightKind.SWITCH,
                brightness=False,
                color_temperature=False,
                entity_id="switch.demo_spots",
            )
        }
    )

    await executor.execute(
        hass,
        PlannedCommand("spots", LightAction.TURN_OFF, fade_seconds=20),
    )

    assert hass.services.calls[-1] == (
        "switch",
        "turn_off",
        {"entity_id": "switch.demo_spots"},
    )


async def test_inverted_target_observation_is_logical_and_idempotent() -> None:
    payload = _config_payload()
    payload["devices"]["light_targets"][0]["colorTempInverted"] = True  # type: ignore[index]
    payload["schedule"][0]["how"]["colorTemperature"] = 2200  # type: ignore[index]
    config = config_from_payload(payload)

    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "on", last_changed=_NOW_DT)
    hass.states.set(
        "light.demo_main",
        "on",
        {
            "brightness": 153,  # 60 %
            "color_temp_kelvin": 3800,  # raw command that is physically 2200 K
            "min_color_temp_kelvin": 2000,
            "max_color_temp_kelvin": 6535,
        },
        last_changed=_NOW_DT,
    )
    context = await build_context(
        hass,
        config,
        _NOW_MS,
        ownership_provider=lambda _config: (
            OwnershipSnapshot(
                "light_main", OwnershipSource.AUTO, True, _NOW_MS - 1000
            ),
        ),
    )
    light = context.light("light_main")
    assert light is not None
    # The provider reflects the raw 3800 K back to the logical 2200 K.
    assert light.color_temperature == 2200

    decision = evaluate_room_lighting(config, context)
    main = decision_target(decision, "light_main")
    assert main is not None
    assert LightAction.SET_COLOR_TEMPERATURE not in [
        command.action for command in main.commands
    ]


def _away_runtime(
    hass: _FakeHass,
    *,
    commands_enabled: bool,
    executor: object,
    captured: dict[str, object],
) -> RoomLightingRuntime:
    payload = _config_payload(commands_enabled=commands_enabled)
    payload["awayBehavior"] = {
        "mode": "room_off",
        "return": {"restore": "by_current_conditions"},
    }

    def track_state_changes(h, entities, callback):
        if AWAY_ENTITY_ID in entities:
            captured["away"] = callback
        else:
            captured["room"] = callback
        return lambda: None

    return RoomLightingRuntime(
        hass,
        _ConfigService(config_from_payload(payload)),
        RoomLightingShadowService(_MemoryShadowStore()),
        executor=executor,
        now_ms=lambda: _NOW_MS,
        track_state_changes=track_state_changes,
        track_interval=lambda h, c, i: (lambda: None),
        listen_bus=lambda h, e, c: (lambda: None),
        away_debounce_seconds=0,
    )


async def test_away_source_turns_context_away_and_switches_off_light() -> None:
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_NOW_DT)
    hass.states.set("light.demo_main", "on", {"brightness": 153}, last_changed=_NOW_DT)
    hass.states.set(AWAY_ENTITY_ID, "off", last_changed=_NOW_DT)
    captured: dict[str, object] = {}
    executor = _SpyExecutor()
    runtime = _away_runtime(
        hass, commands_enabled=True, executor=executor, captured=captured
    )

    await runtime.start(hass, "entry")
    try:
        assert runtime.away is False
        before = await runtime.async_context_for(runtime.configs()[0])
        assert before.away is False

        # The Matter A100 Away source turns on: the runtime debounces, marks
        # the room context away and switches the automatic light off.
        hass.states.set(AWAY_ENTITY_ID, "on", last_changed=_NOW_DT)
        captured["away"](SimpleNamespace(data={"entity_id": AWAY_ENTITY_ID}))  # type: ignore[operator]
        await asyncio.gather(*hass.tasks)

        assert runtime.away is True
        during = await runtime.async_context_for(runtime.configs()[0])
        assert during.away is True
        assert any(
            command.action is LightAction.TURN_OFF
            and command.target_id == "light_main"
            for command in executor.calls
        )

        # Return home: the source clears and the normal evaluation resumes.
        executor.calls.clear()
        hass.states.set(AWAY_ENTITY_ID, "off", last_changed=_NOW_DT)
        captured["away"](SimpleNamespace(data={"entity_id": AWAY_ENTITY_ID}))  # type: ignore[operator]
        await asyncio.gather(*hass.tasks)

        assert runtime.away is False
        home = await runtime.async_context_for(runtime.configs()[0])
        assert home.away is False
    finally:
        await runtime.stop()


async def test_away_in_shadow_only_journals_without_commands() -> None:
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_NOW_DT)
    hass.states.set("light.demo_main", "on", {"brightness": 153}, last_changed=_NOW_DT)
    hass.states.set(AWAY_ENTITY_ID, "off", last_changed=_NOW_DT)
    captured: dict[str, object] = {}
    executor = _SpyExecutor()
    runtime = _away_runtime(
        hass, commands_enabled=False, executor=executor, captured=captured
    )

    await runtime.start(hass, "entry")
    try:
        hass.states.set(AWAY_ENTITY_ID, "on", last_changed=_NOW_DT)
        captured["away"](SimpleNamespace(data={"entity_id": AWAY_ENTITY_ID}))  # type: ignore[operator]
        await asyncio.gather(*hass.tasks)

        assert runtime.away is True
        assert executor.calls == []
    finally:
        await runtime.stop()


def test_ownership_journal_isolates_same_target_id_across_rooms() -> None:
    journal = RoomLightingOwnershipJournal(now_ms=lambda: 5_000)
    journal.record_manual("room_a", "main", 4_000, turned_off=True)

    assert journal.snapshots_for("room_a", {"main"})
    assert journal.snapshots_for("room_b", {"main"}) == ()
    assert journal.last_manual_off_at("room_a", {"main"}) == 4_000
    assert journal.last_manual_off_at("room_b", {"main"}) is None


def test_ownership_journal_migrates_legacy_target_only_payload() -> None:
    journal = RoomLightingOwnershipJournal(now_ms=lambda: 5_000)
    journal.restore(
        {
            "version": 1,
            "records": [
                {
                    "targetId": "main",
                    "source": "manual",
                    "confirmed": True,
                    "at": 4_000,
                }
            ],
            "manualOff": {"main": 4_000},
        }
    )
    assert journal.has_legacy()
    assert journal.snapshots_for("room_a", {"main"}) == ()

    journal.migrate_legacy({"room_a": ("main",), "room_b": ("other",)})

    assert journal.snapshots_for("room_a", {"main"})
    assert journal.snapshots_for("room_b", {"main"}) == ()
    assert journal.last_manual_off_at("room_a", {"main"}) == 4_000
    assert journal.last_manual_off_at("room_b", {"main"}) is None
    assert not journal.has_legacy()


async def test_service_event_ownership_is_scoped_to_room() -> None:
    hass = _FakeHass()
    second = _config_payload()
    second["roomId"] = "room_demo_second"
    second["devices"]["light_targets"][0]["entityId"] = "light.demo_second"  # type: ignore[index]

    runtime = RoomLightingRuntime(
        hass,
        _ConfigService(
            config_from_payload(_config_payload()),
            config_from_payload(second),
        ),
        RoomLightingShadowService(_MemoryShadowStore()),
        now_ms=lambda: _NOW_MS,
        track_state_changes=lambda hass, entities, callback: (lambda: None),
        track_interval=lambda hass, callback, interval: (lambda: None),
        listen_bus=lambda hass, event_type, callback: (lambda: None),
    )
    await runtime.start(hass, "entry")
    try:
        # Both rooms reuse the same target id "light_main"; a manual call on
        # the first room's entity must not grant anything to the second room.
        runtime._service_event(_event("light", "turn_on", "light.demo_main"))
        assert runtime._ownership.snapshots_for(_ROOM_ID, {"light_main"})  # type: ignore[attr-defined]
        assert (
            runtime._ownership.snapshots_for("room_demo_second", {"light_main"})  # type: ignore[attr-defined]
            == ()
        )
        assert (
            runtime._ownership.last_manual_off_at(  # type: ignore[attr-defined]
                "room_demo_second", {"light_main"}
            )
            is None
        )
    finally:
        await runtime.stop()


def test_ownership_journal_migration_skips_ambiguous_target_id() -> None:
    journal = RoomLightingOwnershipJournal(now_ms=lambda: 5_000)
    journal.restore(
        {
            "version": 1,
            "records": [
                {
                    "targetId": "chandelier",
                    "source": "manual",
                    "confirmed": True,
                    "at": 4_000,
                }
            ],
            "manualOff": {"chandelier": 4_000},
        }
    )
    journal.migrate_legacy(
        {"room_a": ("chandelier",), "room_b": ("chandelier", "mirror")}
    )

    assert journal.snapshots_for("room_a", {"chandelier"}) == ()
    assert journal.snapshots_for("room_b", {"chandelier"}) == ()
    assert journal.last_manual_off_at("room_a", {"chandelier"}) is None
    assert journal.last_manual_off_at("room_b", {"chandelier"}) is None
    assert not journal.has_legacy()


def test_ownership_journal_prunes_stale_manual_only_when_light_off() -> None:
    journal = RoomLightingOwnershipJournal(now_ms=lambda: 5_000)
    journal.record_manual("room_a", "main", 4_000, turned_off=True)
    journal.record_manual("room_b", "main", 4_000, turned_off=True)

    journal.prune_stale_manual(
        now=4_000 + 600_000,
        minimum_interval_seconds={"room_a": 600, "room_b": 600},
        light_on={("room_a", "main"): False, ("room_b", "main"): True},
        presence_active={},
    )

    assert journal.snapshots_for("room_a", {"main"}) == ()
    assert journal.last_manual_off_at("room_a", {"main"}) is None
    assert journal.snapshots_for("room_b", {"main"})
    assert journal.last_manual_off_at("room_b", {"main"}) == 4_000


def test_ownership_journal_keeps_stale_manual_while_presence_active() -> None:
    journal = RoomLightingOwnershipJournal(now_ms=lambda: 5_000)
    journal.record_manual("room_a", "main", 4_000, turned_off=True)

    journal.prune_stale_manual(
        now=4_000 + 600_000,
        minimum_interval_seconds={"room_a": 600},
        light_on={("room_a", "main"): False},
        presence_active={"room_a": True},
    )

    assert journal.snapshots_for("room_a", {"main"})
    assert journal.last_manual_off_at("room_a", {"main"}) == 4_000


POWER_ENTITY = "switch.demo_power"


async def test_power_switch_state_event_recomputes_the_room() -> None:
    hass = _FakeHass()
    _seed_presence_and_light(hass)
    hass.states.set(POWER_ENTITY, "off", last_changed=_NOW_DT)
    runtime = _make_runtime(
        hass, payload=_config_payload(power_switch_entity=POWER_ENTITY)
    )

    await runtime.start(hass, "entry")
    try:
        before = len(runtime._shadow.journal_payload()["entries"])  # type: ignore[attr-defined]
        hass.states.set(POWER_ENTITY, "on", last_changed=_NOW_DT)
        runtime._state_event(SimpleNamespace(data={"entity_id": POWER_ENTITY}))
        await asyncio.gather(*hass.tasks)
        after = len(runtime._shadow.journal_payload()["entries"])  # type: ignore[attr-defined]
        assert after > before
    finally:
        await runtime.stop()


async def test_unpowered_target_reads_off_and_engine_plans_turn_on() -> None:
    config = config_from_payload(
        _config_payload(power_switch_entity=POWER_ENTITY)
    )
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "on", last_changed=_NOW_DT)
    hass.states.set("light.demo_main", "on", {"brightness": 153}, last_changed=_NOW_DT)
    hass.states.set(POWER_ENTITY, "off", last_changed=_NOW_DT)

    context = await build_context(hass, config, _NOW_MS)
    light = context.light("light_main")
    assert light is not None
    assert light.state is SensorState.OFF

    decision = evaluate_room_lighting(config, context)
    main = decision_target(decision, "light_main")
    assert main is not None
    assert any(command.action is LightAction.TURN_ON for command in main.commands)


async def test_dispatch_switches_power_on_before_the_target() -> None:
    hass = _FakeHass()
    _seed_presence_and_light(hass)
    hass.states.set(POWER_ENTITY, "off", last_changed=_NOW_DT)
    runtime = _make_runtime(
        hass,
        commands_enabled=True,
        executor=RoomLightingHaExecutor(),
        payload=_config_payload(power_switch_entity=POWER_ENTITY),
    )

    await runtime.start(hass, "entry")
    try:
        entities = [call[2].get("entity_id") for call in hass.services.calls]
        assert POWER_ENTITY in entities
        assert "light.demo_main" in entities
        assert entities.index(POWER_ENTITY) < entities.index("light.demo_main")
    finally:
        await runtime.stop()


async def test_manual_power_off_is_not_immediately_overridden() -> None:
    """Core race guard: a manual switch-off must win over the automatic branch.

    The room power switch is not a configured light target, so without an
    explicit attribution the runtime used to re-power the room right after a
    person switched it off. A manual power-off now owns every target of the
    room and blocks the automatic re-power until the protection releases.
    """

    hass = _FakeHass()
    _seed_presence_and_light(hass)
    hass.states.set(POWER_ENTITY, "on", last_changed=_NOW_DT)
    runtime = _make_runtime(
        hass,
        commands_enabled=True,
        executor=RoomLightingHaExecutor(),
        payload=_config_payload(power_switch_entity=POWER_ENTITY),
    )

    await runtime.start(hass, "entry")
    try:
        hass.services.calls.clear()
        # A person turns the room power off: the state changes, the switch
        # reports off and Home Assistant publishes the service call.
        hass.states.set(POWER_ENTITY, "off", last_changed=_NOW_DT)
        runtime._service_event(
            SimpleNamespace(
                data={
                    "domain": "switch",
                    "service": "turn_off",
                    "service_data": {"entity_id": POWER_ENTITY},
                }
            )
        )
        hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)
        runtime._state_event(SimpleNamespace(data={"entity_id": POWER_ENTITY}))
        await asyncio.gather(*hass.tasks)

        assert (
            runtime._ownership.last_manual_off_at(_ROOM_ID, {"light_main"})
            is not None
        )
        power_on_calls = [
            call
            for call in hass.services.calls
            if call[0] == "switch"
            and call[1] == "turn_on"
            and call[2].get("entity_id") == POWER_ENTITY
        ]
        assert power_on_calls == []
    finally:
        await runtime.stop()


def test_explicit_return_to_auto_keeps_manual_off_protection() -> None:
    journal = RoomLightingOwnershipJournal(now_ms=lambda: _NOW_MS)
    journal.record_manual(_ROOM_ID, "ordinary", turned_off=False)
    journal.record_manual(_ROOM_ID, "protected", turned_off=True)

    assert journal.clear_explicit_manual_ownership(
        _ROOM_ID, {"ordinary", "protected"}
    ) == ("ordinary",)
    assert journal.snapshots_for(_ROOM_ID, {"ordinary"}) == ()
    assert journal.last_manual_off_at(_ROOM_ID, {"protected"}) == _NOW_MS


async def test_external_power_on_does_not_claim_all_room_targets_as_manual() -> None:
    """An unattributed power-on must not suppress the next presence decision."""

    hass = _FakeHass()
    _seed_presence_and_light(hass)
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_NOW_DT)
    hass.states.set(POWER_ENTITY, "off", last_changed=_NOW_DT)
    runtime = _make_runtime(
        hass,
        commands_enabled=True,
        executor=RoomLightingHaExecutor(),
        payload=_config_payload(power_switch_entity=POWER_ENTITY),
    )

    await runtime.start(hass, "entry")
    try:
        hass.services.calls.clear()
        hass.states.set(POWER_ENTITY, "on", last_changed=_NOW_DT)
        runtime._service_event(
            SimpleNamespace(
                data={
                    "domain": "switch",
                    "service": "turn_on",
                    "service_data": {"entity_id": POWER_ENTITY},
                }
            )
        )

        assert runtime._ownership.snapshots_for(_ROOM_ID, {"light_main"}) == ()

        hass.states.set("binary_sensor.demo_presence", "on", last_changed=_NOW_DT)
        runtime._state_event(
            SimpleNamespace(data={"entity_id": "binary_sensor.demo_presence"})
        )
        await asyncio.gather(*hass.tasks)

        assert runtime._ownership.snapshots_for(
            _ROOM_ID, {"light_main"}
        )[-1].source is OwnershipSource.AUTO
        assert any(
            call[0] == "light"
            and call[1] == "turn_on"
            and call[2].get("entity_id") == "light.demo_main"
            for call in hass.services.calls
        )
    finally:
        await runtime.stop()


async def test_external_light_off_starts_protection_and_blocks_auto_on() -> None:
    """A wall switch without a Home Assistant call still counts as manual off."""

    hass = _FakeHass()
    _seed_presence_and_light(hass)
    hass.states.set(
        "light.demo_main", "on", {"brightness": 153}, last_changed=_NOW_DT
    )
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=RoomLightingHaExecutor()
    )

    await runtime.start(hass, "entry")
    try:
        hass.services.calls.clear()
        # The physical wall switch cuts the light; Home Assistant only sees the
        # state change, there is no ``call_service`` event for it.
        hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)
        runtime._state_event(SimpleNamespace(data={"entity_id": "light.demo_main"}))
        await asyncio.gather(*hass.tasks)

        assert (
            runtime._ownership.last_manual_off_at(_ROOM_ID, {"light_main"})
            is not None
        )
        turn_on_calls = [
            call
            for call in hass.services.calls
            if call[0] == "light" and call[1] == "turn_on"
        ]
        assert turn_on_calls == []
    finally:
        await runtime.stop()


async def test_own_command_state_report_is_not_attributed_as_manual() -> None:
    """The delayed device report of our own command is not a manual action."""

    hass = _FakeHass()
    _seed_presence_and_light(hass)
    hass.states.set(
        "light.demo_main", "on", {"brightness": 153}, last_changed=_NOW_DT
    )
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=RoomLightingHaExecutor()
    )

    await runtime.start(hass, "entry")
    try:
        runtime._command_grace["light.demo_main"] = ("turn_off", runtime._now_ms())
        hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)
        runtime._state_event(SimpleNamespace(data={"entity_id": "light.demo_main"}))

        assert runtime._ownership.last_manual_off_at(_ROOM_ID, {"light_main"}) is None
    finally:
        await runtime.stop()


async def test_automatic_turn_on_does_not_mask_a_manual_off() -> None:
    """A recent automatic on must not swallow the person's switch-off."""

    hass = _FakeHass()
    _seed_presence_and_light(hass)
    hass.states.set("light.demo_main", "on", {"brightness": 153}, last_changed=_NOW_DT)
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=RoomLightingHaExecutor()
    )

    await runtime.start(hass, "entry")
    try:
        # Our own command was turn_on; the person then switches the light off.
        runtime._command_grace["light.demo_main"] = ("turn_on", runtime._now_ms())
        hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)
        runtime._state_event(SimpleNamespace(data={"entity_id": "light.demo_main"}))

        assert (
            runtime._ownership.last_manual_off_at(_ROOM_ID, {"light_main"})
            is not None
        )
    finally:
        await runtime.stop()


async def test_executor_confirms_colour_only_on_matching_read_back() -> None:
    hass = _FakeHass()
    executor = _light_executor(hass, inverted=True)

    receipt = await executor.execute(
        hass,
        PlannedCommand(
            "light_main",
            LightAction.SET_COLOR_TEMPERATURE,
            color_temperature=2200,
        ),
    )
    assert hass.services.calls[-1][2]["color_temp_kelvin"] == 3800
    assert receipt.confirmed is True
    # The read-back is reported in the logical space.
    assert receipt.state_after is not None
    assert receipt.state_after["color_temperature"] == 2200

    # A device that accepts the call without applying the colour must not be
    # reported as a confirmed colour command.
    class _StaleServices:
        def __init__(self, hass: _FakeHass) -> None:
            self._hass = hass

        async def async_call(
            self, domain: str, service: str, data: dict[str, object], blocking: bool = True,
            context: object = None,
        ) -> None:
            del domain, service, blocking
            entity_id = data.get("entity_id")
            if isinstance(entity_id, str):
                current = self._hass.states.get(entity_id)
                attributes = dict(current.attributes) if current is not None else {}
                self._hass.states.set(entity_id, "on", attributes)

    hass.states.set(
        "light.demo_main",
        "on",
        {
            "color_temp_kelvin": 2000,
            "min_color_temp_kelvin": 2000,
            "max_color_temp_kelvin": 6535,
        },
    )
    hass.services = _StaleServices(hass)  # type: ignore[assignment]
    failed = await executor.execute(
        hass,
        PlannedCommand(
            "light_main",
            LightAction.SET_COLOR_TEMPERATURE,
            color_temperature=2200,
        ),
    )
    assert failed.confirmed is False


async def test_duplicate_entity_across_rooms_logs_a_warning(caplog) -> None:
    hass = _FakeHass()
    _seed_presence_and_light(hass)
    first = _config_payload()
    second = _config_payload()
    second["roomId"] = "room_other"
    second["name"] = "Другая"
    runtime = _make_runtime(hass, payloads=[first, second])

    with caplog.at_level(logging.WARNING):
        await runtime.start(hass, "entry")
    try:
        messages = [record.getMessage() for record in caplog.records]
        assert any("entity collision" in message for message in messages)
    finally:
        await runtime.stop()


async def test_stale_turn_off_grace_does_not_mask_a_later_manual_off() -> None:
    """The own-turn-off marker explains one report and is then consumed."""

    hass = _FakeHass()
    _seed_presence_and_light(hass)
    hass.states.set("light.demo_main", "on", {"brightness": 153}, last_changed=_NOW_DT)
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=RoomLightingHaExecutor()
    )

    await runtime.start(hass, "entry")
    try:
        runtime._command_grace["light.demo_main"] = ("turn_off", runtime._now_ms())
        # Our own off report consumes the marker.
        hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)
        runtime._state_event(SimpleNamespace(data={"entity_id": "light.demo_main"}))
        # A person turns it on and off again inside the old window.
        hass.states.set("light.demo_main", "on", last_changed=_NOW_DT)
        runtime._state_event(SimpleNamespace(data={"entity_id": "light.demo_main"}))
        hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)
        runtime._state_event(SimpleNamespace(data={"entity_id": "light.demo_main"}))

        assert (
            runtime._ownership.last_manual_off_at(_ROOM_ID, {"light_main"})
            is not None
        )
    finally:
        await runtime.stop()


async def test_physical_power_switch_off_blocks_re_power_and_turn_on() -> None:
    """A wall switch cutting the room power counts as a manual off."""

    hass = _FakeHass()
    _seed_presence_and_light(hass)
    hass.states.set(POWER_ENTITY, "on", last_changed=_NOW_DT)
    hass.states.set("light.demo_main", "on", {"brightness": 153}, last_changed=_NOW_DT)
    runtime = _make_runtime(
        hass,
        commands_enabled=True,
        executor=RoomLightingHaExecutor(),
        payload=_config_payload(power_switch_entity=POWER_ENTITY),
    )

    await runtime.start(hass, "entry")
    try:
        hass.services.calls.clear()
        # The physical relay changes state without a Home Assistant call.
        hass.states.set(POWER_ENTITY, "off", last_changed=_NOW_DT)
        runtime._state_event(SimpleNamespace(data={"entity_id": POWER_ENTITY}))
        await asyncio.gather(*hass.tasks)
        assert (
            runtime._ownership.last_manual_off_at(_ROOM_ID, {"light_main"})
            is not None
        )
        # The light reports off because the power is gone; nothing may restore it.
        hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)
        runtime._state_event(SimpleNamespace(data={"entity_id": "light.demo_main"}))
        await asyncio.gather(*hass.tasks)
        power_on_calls = [
            call
            for call in hass.services.calls
            if call[0] == "switch"
            and call[1] == "turn_on"
            and call[2].get("entity_id") == POWER_ENTITY
        ]
        assert power_on_calls == []
    finally:
        await runtime.stop()


def _three_light_bathroom_payload(*, explicit_lights: bool) -> dict[str, object]:
    payload = _bathroom_payload()
    payload["devices"]["light_targets"].append(  # type: ignore[index]
        {
            "id": "light_mirror",
            "name": "Зеркало",
            "kind": "light",
            "entityId": "light.demo_mirror",
            "role": "mirror",
            "groupId": None,
            "brightness": False,
            "color_temperature": False,
            "autoAdoptOverride": None,
        }
    )
    if explicit_lights:
        payload["auxiliary"]["fan"]["lightTargets"] = ["light_main", "light_second"]  # type: ignore[index]
    return payload


def _runtime_with_shadow(
    hass: _FakeHass,
    payload: dict[str, object],
    *,
    executor: object | None = None,
    now_ms: object | None = None,
) -> tuple[RoomLightingRuntime, RoomLightingShadowService]:
    shadow = RoomLightingShadowService(_MemoryShadowStore())
    runtime = RoomLightingRuntime(
        hass,
        _ConfigService(config_from_payload(payload)),
        shadow,
        executor=executor,
        now_ms=now_ms or (lambda: _NOW_MS),
        track_state_changes=lambda hass, entities, callback: (lambda: None),
        track_interval=lambda hass, callback, interval: (lambda: None),
        listen_bus=lambda hass, event_type, callback: (lambda: None),
    )
    return runtime, shadow


def _shower_payload(*, commands_enabled: bool = False) -> dict[str, object]:
    payload = _config_payload(commands_enabled=commands_enabled)
    payload["roomId"] = "room_demo_shower"
    payload["devices"]["sensors"].append(  # type: ignore[index]
        {
            "id": "sensor_demo_shower_humidity",
            "name": "Влажность душевой",
            "kind": "humidity",
            "entityId": "sensor.demo_shower_humidity",
            "autoAdoptOverride": None,
        }
    )
    payload["devices"]["auxiliaries"] = [  # type: ignore[index]
        {
            "id": "aux_shower_fan",
            "name": "Вытяжка душевой",
            "kind": "fan",
            "entityId": "switch.demo_shower_fan",
            "autoAdoptOverride": None,
        }
    ]
    payload["auxiliary"] = {  # type: ignore[index]
        "exhaust": {
            "targetId": "aux_shower_fan",
            "humidityThreshold": 55,
            "presenceRunSeconds": 120,
            "absenceSeconds": 300,
            "fanOffSeconds": 300,
        }
    }
    return payload


async def test_shower_exhaust_turns_the_fan_on_with_humid_air() -> None:
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_NOW_DT)
    hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)
    hass.states.set("sensor.demo_shower_humidity", "70", last_changed=_NOW_DT)
    hass.states.set("switch.demo_shower_fan", "off", last_changed=_NOW_DT)
    executor = _SpyExecutor()
    runtime, _shadow = _runtime_with_shadow(
        hass, _shower_payload(commands_enabled=True), executor=executor
    )

    await runtime.start(hass, "entry")
    try:
        actions = [(command.target_id, command.action) for command in executor.calls]
        assert ("aux_shower_fan", LightAction.TURN_ON) in actions
    finally:
        await runtime.stop()


async def test_shower_exhaust_shadow_journals_without_command() -> None:
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_NOW_DT)
    hass.states.set("sensor.demo_shower_humidity", "70", last_changed=_NOW_DT)
    hass.states.set("switch.demo_shower_fan", "off", last_changed=_NOW_DT)
    executor = _SpyExecutor()
    runtime, shadow = _runtime_with_shadow(
        hass, _shower_payload(commands_enabled=False), executor=executor
    )

    await runtime.start(hass, "entry")
    try:
        assert executor.calls == []
        entries = shadow.journal_payload()["entries"]
        exhaust = [entry for entry in entries if "showerExhaust" in entry]
        assert exhaust
        assert exhaust[-1]["showerExhaust"]["action"] == "turn_on"
        assert exhaust[-1]["mode"] == "shadow"
    finally:
        await runtime.stop()


async def test_shower_exhaust_full_timer_chain() -> None:
    """Dry presence runs the fan after 120 s; absence stops it after 300 s."""

    hass = _FakeHass()
    clock = [_NOW_MS]
    hass.states.set("binary_sensor.demo_presence", "on", last_changed=_NOW_DT)
    hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)
    hass.states.set("sensor.demo_shower_humidity", "40", last_changed=_NOW_DT)
    hass.states.set("switch.demo_shower_fan", "off", last_changed=_NOW_DT)
    runtime, _shadow = _runtime_with_shadow(
        hass,
        _shower_payload(commands_enabled=True),
        now_ms=lambda: clock[0],
    )

    def fan_services() -> list[str]:
        return [
            service
            for _domain, service, data in hass.services.calls
            if data.get("entity_id") == "switch.demo_shower_fan"
        ]

    await runtime.start(hass, "entry")
    try:
        # The room lights are separately controlled; the fan waits 120 s of
        # dry presence before it is switched on.
        assert fan_services() == []
        clock[0] += 120_000
        await runtime.async_process()
        assert fan_services()[-1] == "turn_on"

        # Presence is gone: the owned, dry fan stops only after 300 s.
        hass.states.set("binary_sensor.demo_presence", "off")
        await runtime.async_process()
        clock[0] += 300_000
        await runtime.async_process()
        assert fan_services()[-1] == "turn_off"
    finally:
        await runtime.stop()


async def test_auxiliary_shadow_uses_explicit_fan_lights_with_three_room_lights() -> None:
    hass = _FakeHass()
    hass.states.set("light.demo_main", "on", last_changed=_NOW_DT)
    hass.states.set("switch.demo_second", "off", last_changed=_NOW_DT)
    hass.states.set("light.demo_mirror", "on", last_changed=_NOW_DT)
    hass.states.set("sensor.demo_humidity", "70", last_changed=_NOW_DT)
    hass.states.set("switch.demo_fan", "off", last_changed=_NOW_DT)
    runtime, shadow = _runtime_with_shadow(
        hass, _three_light_bathroom_payload(explicit_lights=True)
    )

    await runtime.start(hass, "entry")
    try:
        entries = shadow.journal_payload()["entries"]
        auxiliary = [entry for entry in entries if "auxiliary" in entry]
        assert len(auxiliary) == 1
        payload = auxiliary[0]["auxiliary"]
        # The fan follows the two named lights, not the mirror.
        assert payload["lights"] == ["on", "off"]
        assert payload["band"] == "day"
        assert payload["action"] == "turn_on"
        assert hass.services.calls == []
    finally:
        await runtime.stop()


async def test_auxiliary_shadow_skips_unmapped_three_light_room() -> None:
    hass = _FakeHass()
    hass.states.set("light.demo_main", "on", last_changed=_NOW_DT)
    hass.states.set("switch.demo_second", "off", last_changed=_NOW_DT)
    hass.states.set("light.demo_mirror", "on", last_changed=_NOW_DT)
    hass.states.set("sensor.demo_humidity", "70", last_changed=_NOW_DT)
    hass.states.set("switch.demo_fan", "off", last_changed=_NOW_DT)
    runtime, shadow = _runtime_with_shadow(
        hass, _three_light_bathroom_payload(explicit_lights=False)
    )

    await runtime.start(hass, "entry")
    try:
        entries = shadow.journal_payload()["entries"]
        assert not [entry for entry in entries if "auxiliary" in entry]
    finally:
        await runtime.stop()


def _curve_payload(*, commands_enabled: bool) -> dict[str, object]:
    payload = _config_payload(commands_enabled=commands_enabled)
    payload["profile"] = "day_curve"
    payload["devices"]["light_targets"].append(  # type: ignore[index]
        {
            "id": "light_mirror",
            "name": "Зеркало",
            "kind": "light",
            "entityId": "light.demo_mirror",
            "role": "mirror",
            "groupId": None,
            "brightness": False,
            "color_temperature": False,
            "autoAdoptOverride": None,
        }
    )
    return payload


async def test_curve_room_dispatches_mirror_on_at_night() -> None:
    hass = _FakeHass()
    night_ms = int(datetime(2026, 9, 11, 23, 30, tzinfo=_TZ).timestamp() * 1000)
    night_dt = datetime.fromtimestamp(night_ms / 1000, _TZ)
    hass.states.set("light.demo_main", "off", last_changed=night_dt)
    hass.states.set("light.demo_mirror", "off", last_changed=night_dt)
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass,
        commands_enabled=True,
        executor=executor,
        payload=_curve_payload(commands_enabled=True),
        now_ms=lambda: night_ms,
    )

    await runtime.start(hass, "entry")
    try:
        actions = [
            (command.target_id, command.action)
            for command in executor.calls
        ]
        assert ("light_mirror", LightAction.TURN_ON) in actions
    finally:
        await runtime.stop()


async def test_curve_room_in_shadow_dispatches_nothing() -> None:
    hass = _FakeHass()
    night_ms = int(datetime(2026, 9, 11, 23, 30, tzinfo=_TZ).timestamp() * 1000)
    night_dt = datetime.fromtimestamp(night_ms / 1000, _TZ)
    hass.states.set("light.demo_main", "off", last_changed=night_dt)
    hass.states.set("light.demo_mirror", "off", last_changed=night_dt)
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass,
        commands_enabled=False,
        executor=executor,
        payload=_curve_payload(commands_enabled=False),
        now_ms=lambda: night_ms,
    )

    await runtime.start(hass, "entry")
    try:
        assert executor.calls == []
    finally:
        await runtime.stop()


async def test_curve_room_unlock_event_wakes_profile_immediately() -> None:
    hass = _FakeHass()
    now = [int(datetime(2026, 9, 11, 12, 30, tzinfo=_TZ).timestamp() * 1000)]
    now_dt = datetime.fromtimestamp(now[0] / 1000, _TZ)
    hass.states.set("light.demo_main", "on", {"brightness": 13}, last_changed=now_dt)
    hass.states.set("light.demo_mirror", "off", last_changed=now_dt)
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=now_dt)
    hass.states.set("lock.demo_entry", "locked", last_changed=now_dt)
    payload = _curve_payload(commands_enabled=False)
    payload["devices"]["sensors"].append(  # type: ignore[index]
        {
            "id": "sensor_demo_lock",
            "name": "Умный замок",
            "kind": "entry",
            "entityId": "lock.demo_entry",
            "autoAdoptOverride": None,
        }
    )
    runtime = _make_runtime(
        hass,
        payload=payload,
        now_ms=lambda: now[0],
    )
    await runtime.start(hass, "entry")
    try:
        runtime._ownership.record_auto(_ROOM_ID, "light_main", now[0], confirmed=True)  # type: ignore[attr-defined]
        runtime._curve_presence[_ROOM_ID] = {"since": None, "last_seen": now[0] - 301_000}  # type: ignore[attr-defined]
        await runtime.async_process()
        hass.states.set("lock.demo_entry", "unlocked", last_changed=now_dt)
        runtime._state_event(
            SimpleNamespace(
                data={
                    "entity_id": "lock.demo_entry",
                    "old_state": SimpleNamespace(state="locked"),
                    "new_state": SimpleNamespace(state="unlocked"),
                }
            )
        )
        await asyncio.gather(*hass.tasks)
        entry = runtime._shadow.journal_payload()["entries"][-1]  # type: ignore[attr-defined]
        assert any(
            command["targetId"] == "light_main"
            and command["action"] == LightAction.SET_BRIGHTNESS.value
            and command["brightness"] == 100
            for command in entry["commands"]
        )
    finally:
        await runtime.stop()


async def test_curve_presence_confirms_after_fifteen_seconds() -> None:
    hass = _FakeHass()
    now = [_NOW_MS]
    runtime = _make_runtime(hass, payload=_curve_payload(commands_enabled=False), now_ms=lambda: now[0])
    config = config_from_payload(_curve_payload(commands_enabled=False))
    context = RoomLightingContext(
        now=now[0], timezone=_TZ, sunrise=time(7, 0), sunset=time(19, 0),
        sensors=(SensorSnapshot("sensor_demo_presence", SensorKind.PRESENCE, SensorState.ON, now[0]),),
    )
    assert runtime._observe_curve_presence(config, context, now[0]) == (False, 0)  # type: ignore[attr-defined]
    now[0] += 15_000
    context = RoomLightingContext(
        now=now[0], timezone=_TZ, sunrise=time(7, 0), sunset=time(19, 0),
        sensors=(SensorSnapshot("sensor_demo_presence", SensorKind.PRESENCE, SensorState.ON, now[0]),),
    )
    assert runtime._observe_curve_presence(config, context, now[0]) == (True, 0)  # type: ignore[attr-defined]


def test_curve_motion_preview_requires_presence_within_twenty_seconds() -> None:
    payload = _curve_payload(commands_enabled=False)
    payload["devices"]["sensors"].append(  # type: ignore[index]
        {
            "id": "motion_demo",
            "name": "Движение",
            "kind": "motion",
            "entityId": "binary_sensor.demo_motion",
            "autoAdoptOverride": None,
        }
    )
    now = [_NOW_MS]
    runtime = _make_runtime(
        _FakeHass(), payload=payload, now_ms=lambda: now[0]
    )
    runtime._hass = None  # type: ignore[attr-defined]  # no real-time task in this unit test
    config = config_from_payload(payload)

    def context(presence: SensorState) -> RoomLightingContext:
        return RoomLightingContext(
            now=now[0], timezone=_TZ, sunrise=time(7, 0), sunset=time(19, 0),
            sensors=(
                SensorSnapshot("presence_demo", SensorKind.PRESENCE, presence, now[0]),
                SensorSnapshot("motion_demo", SensorKind.MOTION, SensorState.ON, now[0]),
            ),
        )

    assert runtime._curve_motion_phase(  # type: ignore[attr-defined]
        config, context(SensorState.OFF), now[0], SensorState.OFF
    ) == (True, False, False)
    now[0] += 15_000
    assert runtime._curve_motion_phase(  # type: ignore[attr-defined]
        config, context(SensorState.ON), now[0], SensorState.ON
    ) == (False, False, True)

    other = _make_runtime(_FakeHass(), payload=payload, now_ms=lambda: now[0])
    other._hass = None  # type: ignore[attr-defined]
    now[0] = _NOW_MS
    assert other._curve_motion_phase(  # type: ignore[attr-defined]
        config, context(SensorState.OFF), now[0], SensorState.OFF
    ) == (True, False, False)
    now[0] += 20_000
    assert other._curve_motion_phase(  # type: ignore[attr-defined]
        config, context(SensorState.OFF), now[0], SensorState.OFF
    ) == (False, True, False)


@pytest.mark.parametrize("confirmed", [False, True])
async def test_curve_day_handover_waits_for_observed_confirmed_main(confirmed) -> None:
    hass = _FakeHass()
    now = [_NOW_MS]
    hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)
    hass.states.set("light.demo_mirror", "on", last_changed=_NOW_DT)
    hass.states.set("binary_sensor.demo_presence", "unknown", last_changed=_NOW_DT)

    class Executor:
        def __init__(self):
            self.calls = []

        async def execute(self, hass, command):
            self.calls.append(command)
            if not confirmed:
                return {"confirmed": False}
            entity = "light.demo_main" if command.target_id == "light_main" else "light.demo_mirror"
            state = hass.states.get(entity)
            attributes = dict(state.attributes)
            if command.brightness is not None:
                attributes["brightness"] = round(command.brightness * 255 / 100)
            if command.color_temperature is not None:
                attributes["color_temp_kelvin"] = command.color_temperature
            hass.states.set(entity, "off" if command.action is LightAction.TURN_OFF else "on",
                            attributes, last_changed=datetime.fromtimestamp(now[0] / 1000, _TZ))
            return {"confirmed": True}

    executor = Executor()
    runtime = _make_runtime(hass, executor=executor,
                            payload=_curve_payload(commands_enabled=True), now_ms=lambda: now[0])
    await runtime.start(hass, "entry")
    try:
        # Establish mirror ownership during this observed session, not across restart.
        runtime._ownership.record_auto(_ROOM_ID, "light_mirror", now[0], confirmed=True)
        now[0] += 1000
        await runtime.async_process()
        assert any(command.action is LightAction.TURN_ON for command in executor.calls)
        assert hass.states.get("light.demo_mirror").state == "on"
        assert not any(command.target_id == "light_mirror" for command in executor.calls)
        now[0] += 1000
        await runtime.async_process()
        mirror_off = [command for command in executor.calls
                      if command.target_id == "light_mirror" and command.action is LightAction.TURN_OFF]
        assert bool(mirror_off) is confirmed
        assert hass.states.get("light.demo_mirror").state == ("off" if confirmed else "on")
    finally:
        await runtime.stop()


@pytest.mark.parametrize("manual_service", ["turn_on", "turn_off"])
async def test_curve_manual_action_during_main_confirmation_cancels_remaining_steps(manual_service) -> None:
    hass = _FakeHass()
    now = [_NOW_MS]
    hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)
    hass.states.set("light.demo_mirror", "off", last_changed=_NOW_DT)
    hass.states.set("binary_sensor.demo_presence", "on", last_changed=_NOW_DT)

    class Executor:
        def __init__(self):
            self.calls = []

        async def execute(self, hass, command):
            self.calls.append(command)
            # A person intervenes while read-back is awaited.
            now[0] += 1
            runtime._service_event(_event("light", manual_service, "light.demo_main"))
            return {"confirmed": True}

    executor = Executor()
    runtime = _make_runtime(hass, executor=executor,
                            payload=_curve_payload(commands_enabled=True), now_ms=lambda: now[0])
    await runtime.start(hass, "entry")
    try:
        assert len(executor.calls) == 1
        assert executor.calls[0].action is LightAction.TURN_ON
        records = runtime._ownership.snapshots_for(_ROOM_ID, {"light_main"})
        assert not has_proven_auto_ownership(records, "light_main")
    finally:
        await runtime.stop()


async def test_curve_unknown_presence_does_not_accumulate_absence() -> None:
    hass = _FakeHass()
    runtime = _make_runtime(
        hass,
        payload=_curve_payload(commands_enabled=False),
        now_ms=lambda: _NOW_MS,
    )
    config = config_from_payload(_curve_payload(commands_enabled=False))

    def context(state: SensorState, moment: int) -> RoomLightingContext:
        return RoomLightingContext(
            now=moment,
            timezone=_TZ,
            sunrise=time(7, 0),
            sunset=time(19, 0),
            sensors=(
                SensorSnapshot(
                    "sensor_demo_presence",
                    SensorKind.PRESENCE,
                    state,
                    last_changed=moment,
                ),
            ),
        )

    base = _NOW_MS
    assert runtime._observe_curve_presence(  # type: ignore[attr-defined]
        config, context(SensorState.OFF, base), base
    ) == (False, 0)
    _, absence = runtime._observe_curve_presence(  # type: ignore[attr-defined]
        config, context(SensorState.OFF, base + 600_000), base + 600_000
    )
    assert absence == 600
    # A lost sensor is not absence: the gap is dropped, not inherited.
    assert runtime._observe_curve_presence(  # type: ignore[attr-defined]
        config, context(SensorState.UNKNOWN, base + 1_200_000), base + 1_200_000
    ) == (False, 0)
    _, restarted = runtime._observe_curve_presence(  # type: ignore[attr-defined]
        config, context(SensorState.OFF, base + 1_800_000), base + 1_800_000
    )
    assert restarted == 0
    # Presence observed again restarts the 12-second confirmation.
    confirmed_now, _ = runtime._observe_curve_presence(  # type: ignore[attr-defined]
        config, context(SensorState.ON, base + 1_860_000), base + 1_860_000
    )
    assert confirmed_now is False
    confirmed_after, _ = runtime._observe_curve_presence(  # type: ignore[attr-defined]
        config, context(SensorState.ON, base + 1_875_000), base + 1_875_000
    )
    assert confirmed_after is True


async def test_curve_stale_presence_is_not_presence() -> None:
    hass = _FakeHass()
    runtime = _make_runtime(
        hass,
        payload=_curve_payload(commands_enabled=False),
        now_ms=lambda: _NOW_MS,
    )

    def context(state: SensorState, age_ms: int) -> RoomLightingContext:
        return RoomLightingContext(
            now=_NOW_MS,
            timezone=_TZ,
            sunrise=time(7, 0),
            sunset=time(19, 0),
            sensors=(
                SensorSnapshot(
                    "sensor_demo_presence",
                    SensorKind.PRESENCE,
                    state,
                    last_changed=_NOW_MS - age_ms,
                ),
            ),
        )

    fresh = runtime._curve_presence_state(  # type: ignore[attr-defined]
        context(SensorState.ON, 10_000)
    )
    stale = runtime._curve_presence_state(  # type: ignore[attr-defined]
        context(SensorState.ON, 3_600_000)
    )
    assert fresh is SensorState.ON
    assert stale is SensorState.UNKNOWN


async def test_curve_stale_presence_with_fresh_motion_off_proves_absence() -> None:
    hass = _FakeHass()
    runtime = _make_runtime(
        hass,
        payload=_curve_payload(commands_enabled=False),
        now_ms=lambda: _NOW_MS,
    )

    def context(sensors: tuple[SensorSnapshot, ...]) -> RoomLightingContext:
        return RoomLightingContext(
            now=_NOW_MS,
            timezone=_TZ,
            sunrise=time(7, 0),
            sunset=time(19, 0),
            sensors=sensors,
        )

    def sensor(state: SensorState, age_ms: int, kind: SensorKind) -> SensorSnapshot:
        return SensorSnapshot(
            f"{kind.value}_demo", kind, state, last_changed=_NOW_MS - age_ms
        )

    stale_on_and_motion_off = runtime._curve_presence_state(  # type: ignore[attr-defined]
        context(
            (
                sensor(SensorState.ON, 1_800_000, SensorKind.PRESENCE),
                sensor(SensorState.OFF, 200_000, SensorKind.MOTION),
            )
        )
    )
    assert stale_on_and_motion_off is SensorState.OFF

    stale_on_and_unknown = runtime._curve_presence_state(  # type: ignore[attr-defined]
        context(
            (
                sensor(SensorState.ON, 1_800_000, SensorKind.PRESENCE),
                sensor(SensorState.UNKNOWN, 10_000, SensorKind.MOTION),
            )
        )
    )
    assert stale_on_and_unknown is SensorState.UNKNOWN

    fresh_on = runtime._curve_presence_state(  # type: ignore[attr-defined]
        context(
            (
                sensor(SensorState.ON, 10_000, SensorKind.PRESENCE),
                sensor(SensorState.OFF, 10_000, SensorKind.MOTION),
            )
        )
    )
    assert fresh_on is SensorState.ON


@pytest.mark.parametrize("state", ["unknown", "unavailable", "on"])
async def test_curve_lost_presence_invalidates_completed_manual_protection_absence(state):
    hass = _FakeHass()
    now = [_NOW_MS]
    hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)
    hass.states.set("light.demo_mirror", "off", last_changed=_NOW_DT)
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_NOW_DT)
    executor = _SpyExecutor()
    runtime = _make_runtime(hass, executor=executor, now_ms=lambda: now[0],
                            payload=_curve_payload(commands_enabled=False))
    await runtime.start(hass, "entry")
    try:
        runtime._ownership.record_manual(_ROOM_ID, "light_main", now[0],
                                         confirmed=True, turned_off=True)
        now[0] += 60_000
        config = runtime._configs[_ROOM_ID]
        await runtime._build_context(config)
        assert runtime._absence[_ROOM_ID][0]
        hass.states.set("binary_sensor.demo_presence", state, last_changed=_NOW_DT)
        now[0] += 600_000
        context = await runtime._build_context(config)
        assert not context.protection.absence_confirmed
        assert _ROOM_ID not in runtime._absence
        from custom_components.hausman_hub.domain.room_lighting_engine import evaluate_curve_room
        assert not evaluate_curve_room(
            config, context, presence=SensorState.UNKNOWN,
            presence_confirmed=False, absence_seconds=None,
        ).commands
    finally:
        await runtime.stop()


async def test_executor_context_does_not_hide_concurrent_identical_manual_call():
    hass = _FakeHass()
    now = [_NOW_MS]
    _seed_presence_and_light(hass)
    hass.states.set("light.demo_mirror", "off", last_changed=_NOW_DT)
    executor = RoomLightingHaExecutor()
    runtime = _make_runtime(hass, executor=executor, now_ms=lambda: now[0],
                            payload=_curve_payload(commands_enabled=False))
    await runtime.start(hass, "entry")
    try:
        class Services(_FakeServices):
            async def async_call(self, domain, service, data, blocking=True, context=None):
                assert executor.owns_service_context(context)
                own_event = _event(domain, service, data["entity_id"])
                own_event.context = context
                runtime._service_event(own_event)
                assert not runtime._ownership.snapshots_for(_ROOM_ID, {"light_main"})
                now[0] += 1
                runtime._service_event(_event(domain, service, data["entity_id"]))
                await super().async_call(domain, service, data, blocking, context)

        hass.services = Services(hass)
        await executor.execute(hass, PlannedCommand("light_main", LightAction.TURN_ON))
        records = runtime._ownership.snapshots_for(_ROOM_ID, {"light_main"})
        assert records[-1].source is OwnershipSource.MANUAL
        assert not executor._active_context_ids
    finally:
        await runtime.stop()


@pytest.mark.parametrize("reserved_entity", ["light.demo_main", "switch.demo_power"])
async def test_curve_reservation_acquired_during_power_on_blocks_target(reserved_entity):
    hass = _FakeHass()
    _seed_presence_and_light(hass)
    hass.states.set("light.demo_mirror", "off", last_changed=_NOW_DT)
    hass.states.set("switch.demo_power", "off", last_changed=_NOW_DT)
    reserved = []

    class Executor(_SpyExecutor):
        async def async_power_on(self, hass, entity_id):
            reserved.append(reserved_entity)
            hass.states.set(entity_id, "on", last_changed=_NOW_DT)
            return True

    executor = Executor()
    payload = _curve_payload(commands_enabled=True)
    payload["devices"]["power_switch"] = _config_payload(
        power_switch_entity="switch.demo_power"
    )["devices"]["power_switch"]
    runtime = _make_runtime(hass, executor=executor, payload=payload,
                            reserved_entity_ids_provider=lambda: reserved)
    await runtime.start(hass, "entry")
    try:
        assert reserved == [reserved_entity]
        assert not executor.calls
    finally:
        await runtime.stop()
