"""Runtime tests for the live room lighting state, executor and driver.

The tests use a synthetic Home Assistant shape, so no real Home Assistant and
no physical device is involved. The shadow-first guarantee is asserted
directly: with ``commands_enabled=False`` the executor is never called.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, time, timezone
import logging
from types import SimpleNamespace

from custom_components.hausman_hub.application.room_lighting_ha_executor import (
    RoomLightingHaExecutor,
)
from custom_components.hausman_hub.application.room_lighting_ha_state import (
    build_context,
)
from custom_components.hausman_hub.application.room_lighting_runtime import (
    RoomLightingRuntime,
)
from custom_components.hausman_hub.application.room_lighting_shadow import (
    RoomLightingShadowService,
)
from custom_components.hausman_hub.domain.room_lighting import (
    SensorKind,
    config_from_payload,
)
from custom_components.hausman_hub.domain.room_lighting_engine import LightAction
from custom_components.hausman_hub.domain.room_lighting_ownership import (
    OwnershipSource,
    SensorState,
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
            if "kelvin" in data:
                attributes["color_temp_kelvin"] = int(data["kelvin"])
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
    device_automation_api: object | None = None,
) -> RoomLightingRuntime:
    shadow = RoomLightingShadowService(_MemoryShadowStore())
    service = _ConfigService(
        config_from_payload(payload if payload is not None else _config_payload())
    )
    return RoomLightingRuntime(
        hass,
        service,
        shadow,
        commands_enabled=commands_enabled,
        executor=executor,
        ownership_store=ownership_store,
        now_ms=now_ms or (lambda: _NOW_MS),
        track_state_changes=lambda hass, entities, callback: (lambda: None),
        track_interval=lambda hass, callback, interval: (lambda: None),
        listen_bus=lambda hass, event_type, callback: (lambda: None),
        device_automation_api=device_automation_api,
    )


def _seed_presence_and_light(hass: _FakeHass) -> None:
    hass.states.set("binary_sensor.demo_presence", "on", last_changed=_NOW_DT)
    hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)


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
        assert runtime.commands_enabled is False
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
        ownership = runtime._ownership.snapshots_for({"light_main"})  # type: ignore[attr-defined]
        assert ownership
        assert ownership[-1].source is OwnershipSource.AUTO
        assert ownership[-1].confirmed is True
    finally:
        await runtime.stop()


async def test_ownership_marks_foreign_manual_and_own_auto() -> None:
    hass = _FakeHass()
    _seed_presence_and_light(hass)
    runtime = _make_runtime(hass, commands_enabled=False)

    await runtime.start(hass, "entry")
    try:
        runtime._service_event(_event("light", "turn_on", "light.demo_main"))
        manual = runtime._ownership.snapshots_for({"light_main"})[-1]  # type: ignore[attr-defined]
        assert manual.source is OwnershipSource.MANUAL
        assert manual.confirmed is True

        runtime._executing_entities.add("light.demo_main")
        runtime._service_event(_event("light", "turn_on", "light.demo_main"))
        automatic = runtime._ownership.snapshots_for({"light_main"})[-1]  # type: ignore[attr-defined]
        assert automatic.source is OwnershipSource.AUTO
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
    assert runtime1._ownership.last_manual_off_at({"light_main"}) == t0  # type: ignore[attr-defined]
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
        ownership = runtime._ownership.snapshots_for({"light_main"})  # type: ignore[attr-defined]
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
        ownership = runtime._ownership.snapshots_for({"light_main"})  # type: ignore[attr-defined]
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
            runtime._ownership.last_manual_off_at({"light_main"})  # type: ignore[attr-defined]
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
        commands_enabled=False,
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
