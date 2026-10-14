"""Runtime tests for the live room lighting state, executor and driver.

The tests use a synthetic Home Assistant shape, so no real Home Assistant and
no physical device is involved. The shadow-first guarantee is asserted
directly: with per-room ``commandsEnabled=false`` the executor is never called.
"""

from __future__ import annotations

import asyncio
import copy
import importlib
from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
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
    CURVE_SENSOR_FRESHNESS_MS,
    RoomLightingOwnershipJournal,
    RoomLightingRuntime,
    legacy_manual_authority_plan,
    CURVE_PRESENCE_CONFIRM_MS,
    PowerDeliveryEpisode,
    POWER_RETRY_INTERVAL_MS,
    POWER_RETRY_LIMIT_PER_REVISION,
)
from custom_components.hausman_hub.application.manual_light_authority import (
    ManualLightAuthority,
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
from custom_components.hausman_hub.domain.device_power_dependencies import (
    AUTO_TURN_ON_POLICY,
    DevicePowerDependency,
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

    async def async_save_if(self, payload, is_current):
        await asyncio.sleep(0)
        if not is_current():
            return False
        self.payload = copy.deepcopy(payload)
        self.saves += 1
        return True


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
    seed_away: bool = True,
    away_entity_id: str | None = AWAY_ENTITY_ID,
    light_priority: object | None = None,
    manual_light_authority: object | None = None,
) -> RoomLightingRuntime:
    # Live-mode tests must model the protected topology explicitly. Keep the
    # fixture home, unless an away transition test supplied its own state.
    if seed_away and away_entity_id and hass.states.get(away_entity_id) is None:
        hass.states.set(away_entity_id, "off", last_changed=_NOW_DT)
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
        away_entity_id=away_entity_id,
        light_priority=light_priority,
        manual_light_authority=manual_light_authority,
    )


def _seed_presence_and_light(hass: _FakeHass) -> None:
    hass.states.set("binary_sensor.demo_presence", "on", last_changed=_NOW_DT)
    hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)
    hass.states.set(AWAY_ENTITY_ID, "off", last_changed=_NOW_DT)


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
        assert actions == [LightAction.TURN_ON]
        entries = runtime._shadow.journal_payload()["entries"]  # type: ignore[attr-defined]
        operation_id = f"room-lighting-{_ROOM_ID}-{_NOW_MS}-1"
        decisions = [
            entry for entry in entries
            if entry.get("operationId") == operation_id and "commands" in entry
        ]
        assert decisions == [
            {
                "at": _NOW_MS,
                "roomId": _ROOM_ID,
                "mode": "live",
                "commandsEnabled": True,
                "operationId": operation_id,
                "configVersion": 1,
                "commands": [
                    {
                        "targetId": "light_main",
                        "action": LightAction.TURN_ON.value,
                        "brightness": 60,
                        "colorTemperature": 3000,
                        "reason": "presence",
                    }
                ],
                "skips": [],
            }
        ]
        dispatches = [
            entry for entry in entries
            if entry.get("operationId") == operation_id and "dispatch" in entry
        ]
        assert len(dispatches) == 1
        assert dispatches[0]["dispatch"][0]["operationId"] == operation_id
        # A confirmed receipt grants proven automatic ownership.
        ownership = runtime._ownership.snapshots_for(_ROOM_ID, {"light_main"})  # type: ignore[attr-defined]
        assert ownership
        assert ownership[-1].source is OwnershipSource.AUTO
        assert ownership[-1].confirmed is True
    finally:
        await runtime.stop()


async def test_live_commands_require_the_protected_away_role() -> None:
    """A missing protected topology is fail-closed, even with opt-in on."""

    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "on", last_changed=_NOW_DT)
    hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)
    executor = _SpyExecutor()
    runtime = RoomLightingRuntime(
        hass,
        _ConfigService(config_from_payload(_config_payload(commands_enabled=True))),
        RoomLightingShadowService(_MemoryShadowStore()),
        executor=executor,
        now_ms=lambda: _NOW_MS,
        track_state_changes=lambda hass, entities, callback: (lambda: None),
        track_interval=lambda hass, callback, interval: (lambda: None),
        listen_bus=lambda hass, event_type, callback: (lambda: None),
    )

    await runtime.start(hass, "entry")
    try:
        assert runtime.away_diagnostic == "protected_topology_missing"
        assert executor.calls == []
    finally:
        await runtime.stop()


@pytest.mark.parametrize("state", [None, "unknown", "unavailable", "invalid"])
async def test_automatic_commands_fail_closed_when_configured_away_is_untrusted(
    state: str | None,
) -> None:
    """A configured Away entity must still report a trusted on/off state."""

    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "on", last_changed=_NOW_DT)
    hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)
    if state is not None:
        hass.states.set(AWAY_ENTITY_ID, state, last_changed=_NOW_DT)
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass,
        commands_enabled=True,
        executor=executor,
        seed_away=False,
    )

    await runtime.start(hass, "entry")
    try:
        assert runtime.away_diagnostic == "away_sensor_unavailable"
        assert executor.calls == []
        assert runtime._ownership.snapshots_for(_ROOM_ID, {"light_main"}) == ()  # type: ignore[attr-defined]
        decision = runtime._shadow.journal_payload()["entries"][-1]  # type: ignore[attr-defined]
        assert decision["dispatchBlockReason"] == "away_sensor_unavailable"
    finally:
        await runtime.stop()


async def test_generic_command_fails_closed_when_configured_away_is_untrusted() -> None:
    """The generic path has no manual origin and is therefore automatic-safe."""

    hass = _FakeHass()
    hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass,
        commands_enabled=True,
        executor=executor,
        seed_away=False,
    )
    config = config_from_payload(_config_payload(commands_enabled=True))

    receipt = await runtime.async_execute_command(
        config, PlannedCommand("light_main", LightAction.TURN_ON)
    )

    assert receipt == {
        "confirmed": False,
        "blocked": True,
        "sent": False,
        "sendOutcome": "not_sent",
        "reason": "away_sensor_unavailable",
    }
    assert executor.calls == []


async def test_shared_manual_priority_blocks_room_runtime_dispatch() -> None:
    """A manual claim made by ScenarioExecutor closes the room-runtime route."""
    class _ManualPriority:
        @staticmethod
        def manual_claim_entity_ids(entity_ids: frozenset[str]) -> frozenset[str]:
            return entity_ids

    hass = _FakeHass()
    hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass,
        commands_enabled=True,
        executor=executor,
        seed_away=True,
        light_priority=_ManualPriority(),
    )
    config = config_from_payload(_config_payload(commands_enabled=True))

    receipt = await runtime.async_execute_command(
        config, PlannedCommand("light_main", LightAction.TURN_ON)
    )

    assert receipt["reason"] == "manual_light_priority"
    assert receipt["sent"] is False
    assert executor.calls == []


async def test_legacy_300_second_release_setting_cannot_bypass_shared_deny() -> None:
    from custom_components.hausman_hub.domain.scenario_controls import ScenarioControlPolicy

    assert ScenarioControlPolicy().manual_release_seconds == 300
    hass = _FakeHass()
    hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)
    authority = ManualLightAuthority()
    authority.capture("light.demo_main", "off", "service", "manual-off")
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=executor,
        manual_light_authority=authority, seed_away=True,
    )
    config = config_from_payload(_config_payload(commands_enabled=True))

    receipt = await runtime.async_execute_command(
        config, PlannedCommand("light_main", LightAction.TURN_ON)
    )

    assert authority.blocks("light.demo_main")
    assert receipt["reason"] == "manual_light_priority"
    assert executor.calls == []


async def test_runtime_start_publishes_related_graph_before_startup_release() -> None:
    from custom_components.hausman_hub.application.manual_light_authority import ManualLightAuthority
    hass = _FakeHass()
    payload = _config_payload(commands_enabled=False)
    payload["devices"]["light_targets"].append({
        "id": "related_switch", "name": "Связанный", "kind": "switch",
        "entityId": "switch.related", "role": "other", "groupId": None,
        "brightness": False, "color_temperature": False, "autoAdoptOverride": None,
    })
    authority = ManualLightAuthority()
    authority.begin_startup_observation()
    runtime = _make_runtime(hass, payload=payload, manual_light_authority=authority, seed_away=False)
    await runtime.start(hass, "entry")
    authority.capture("light.demo_main", "on", "service", "runtime-related")
    assert authority.blocks("switch.related")
    assert authority.unhealthy
    runtime.cancel()


@pytest.mark.parametrize("valid_state", ["off", "on"])
async def test_automatic_commands_stop_after_valid_away_becomes_untrusted(
    valid_state: str,
) -> None:
    """The permit is evaluated again after a previously valid Away reading."""

    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_NOW_DT)
    hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)
    hass.states.set(AWAY_ENTITY_ID, valid_state, last_changed=_NOW_DT)
    executor = _SpyExecutor()
    runtime = _make_runtime(hass, commands_enabled=True, executor=executor)

    await runtime.start(hass, "entry")
    try:
        assert runtime.away_diagnostic is None
        assert executor.calls == []
        hass.states.set(AWAY_ENTITY_ID, "unavailable", last_changed=_NOW_DT)
        hass.states.set("binary_sensor.demo_presence", "on", last_changed=_NOW_DT)
        await runtime.async_process()
        assert runtime.away_diagnostic == "away_sensor_unavailable"
        assert executor.calls == []
        assert runtime._ownership.snapshots_for(_ROOM_ID, {"light_main"}) == ()  # type: ignore[attr-defined]
    finally:
        await runtime.stop()


async def test_manual_switch_binding_stays_available_when_away_is_untrusted() -> None:
    """A physical press is a separate, explicitly manual command path."""

    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_NOW_DT)
    hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)
    hass.states.set(AWAY_ENTITY_ID, "unavailable", last_changed=_NOW_DT)
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass,
        commands_enabled=True,
        executor=executor,
        payload=_trigger_config_payload(action="turn_on"),
        seed_away=False,
    )

    await runtime.start(hass, "entry")
    try:
        await runtime._handle_device_trigger(
            runtime.configs()[0], "device_demo_mirror", "sw_mirror", "1_single"
        )
        assert [command.action for command in executor.calls] == [LightAction.TURN_ON]
    finally:
        await runtime.stop()


async def test_manual_switch_binding_stays_available_without_away_mapping() -> None:
    """A missing protected mapping blocks automation, not an attributable press."""

    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_NOW_DT)
    hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass,
        commands_enabled=True,
        executor=executor,
        payload=_trigger_config_payload(action="turn_on"),
        seed_away=False,
        away_entity_id=None,
    )

    await runtime.start(hass, "entry")
    try:
        assert runtime.away_diagnostic == "protected_topology_missing"
        assert executor.calls == []
        await runtime._handle_device_trigger(
            runtime.configs()[0], "device_demo_mirror", "sw_mirror", "1_single"
        )
        assert [command.action for command in executor.calls] == [LightAction.TURN_ON]
    finally:
        await runtime.stop()


async def test_automatic_dispatch_rechecks_away_after_pending_write(monkeypatch) -> None:
    """Away failure between intent and executor must close the automatic send."""

    hass = _FakeHass()
    _seed_presence_and_light(hass)
    executor = _SpyExecutor()
    runtime = _make_runtime(hass, commands_enabled=True, executor=executor)
    original = runtime._persist_pending_target_dispatch  # type: ignore[attr-defined]

    async def invalidate_away(**kwargs):
        saved = await original(**kwargs)
        hass.states.set(AWAY_ENTITY_ID, "unavailable", last_changed=_NOW_DT)
        return saved

    monkeypatch.setattr(runtime, "_persist_pending_target_dispatch", invalidate_away)
    await runtime.start(hass, "entry")
    try:
        assert runtime.away_diagnostic == "away_sensor_unavailable"
        assert executor.calls == []
        assert runtime._ownership.snapshots_for(_ROOM_ID, {"light_main"}) == ()  # type: ignore[attr-defined]
        dispatch = runtime._shadow.journal_payload()["entries"][-1]["dispatch"][0]  # type: ignore[attr-defined]
        assert dispatch["reason"] == "away_sensor_unavailable"
        assert dispatch["sent"] is False
    finally:
        await runtime.stop()


async def test_late_away_failure_blocks_each_remaining_automatic_target(monkeypatch) -> None:
    """A late Away failure cannot grant ownership to another planned target."""

    payload = _config_payload(commands_enabled=True)
    payload["devices"]["light_targets"].append(  # type: ignore[index]
        {
            "id": "light_second",
            "name": "Второй свет",
            "kind": "light",
            "entityId": "light.demo_second",
            "role": None,
            "groupId": None,
            "brightness": True,
            "color_temperature": True,
            "autoAdoptOverride": None,
        }
    )
    payload["schedule"][0]["targets"]["lightTargets"] = [  # type: ignore[index]
        "light_main", "light_second"
    ]
    hass = _FakeHass()
    _seed_presence_and_light(hass)
    hass.states.set("light.demo_second", "off", last_changed=_NOW_DT)
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=executor, payload=payload
    )
    original = runtime._persist_pending_target_dispatch  # type: ignore[attr-defined]

    async def invalidate_away(**kwargs):
        saved = await original(**kwargs)
        hass.states.set(AWAY_ENTITY_ID, "unavailable", last_changed=_NOW_DT)
        return saved

    monkeypatch.setattr(runtime, "_persist_pending_target_dispatch", invalidate_away)
    await runtime.start(hass, "entry")
    try:
        assert executor.calls == []
        assert runtime._ownership.snapshots_for(  # type: ignore[attr-defined]
            _ROOM_ID, {"light_main", "light_second"}
        ) == ()
        dispatches = [
            item["dispatch"][0]
            for item in runtime._shadow.journal_payload()["entries"]  # type: ignore[attr-defined]
            if "dispatch" in item
        ]
        assert {item["targetId"] for item in dispatches} == {
            "light_main", "light_second"
        }
        assert {item["reason"] for item in dispatches} == {"away_sensor_unavailable"}
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
        "sent": False,
        "sendOutcome": "not_sent",
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


def _now_ms(offset_ms: int = 0) -> int:
    import time as _time
    return int(_time.time() * 1000) + offset_ms


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


async def test_legacy_room_manual_record_migrates_before_dispatch() -> None:
    class _AuthorityStore(_MemoryOwnershipStore):
        recovered_previous = False

    hass = _FakeHass()
    _seed_presence_and_light(hass)
    ownership_store = _MemoryOwnershipStore()
    ownership_store.payload = {
        "version": 1,
        "records": [{
            "roomId": _ROOM_ID, "targetId": "light_main",
            "source": "manual", "confirmed": True, "at": _NOW_MS,
        }],
        "manualOff": [], "morning": [],
    }
    authority_store = _AuthorityStore()
    authority = ManualLightAuthority(authority_store)
    await authority.async_load()
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=executor,
        ownership_store=ownership_store, manual_light_authority=authority,
    )
    await runtime.start(hass, "entry")
    try:
        record = authority.record("light.demo_main")
        assert record is not None and record.legacy
        assert authority.blocks("light.demo_main")
        assert authority_store.payload["records"][0]["entityId"] == "light.demo_main"
        assert not executor.calls
    finally:
        await runtime.stop()


@pytest.mark.parametrize("store_state", ("new", "existing", "recovered"))
async def test_legacy_import_in_production_startup_order_preserves_old_payload(
    store_state: str,
) -> None:
    class _AuthorityStore(_MemoryOwnershipStore):
        recovered_previous = False

    hass = _FakeHass()
    _seed_presence_and_light(hass)
    ownership_store = _MemoryOwnershipStore()
    ownership_store.payload = {
        "version": 1,
        "records": [{"targetId": "light_main", "source": "manual", "confirmed": True, "at": 1}],
        "manualOff": [{"targetId": "light_main", "at": 1}],
        "morning": [],
    }
    before = copy.deepcopy(ownership_store.payload)
    authority_store = _AuthorityStore()
    if store_state != "new":
        authority_store.payload = {"version": 2, "records": [], "recentFingerprints": []}
    authority_store.recovered_previous = store_state == "recovered"
    authority = ManualLightAuthority(authority_store)
    authority.begin_startup_observation()
    await authority.async_load()
    assert authority.unhealthy and authority.persistence_ready
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=executor,
        ownership_store=ownership_store, manual_light_authority=authority,
    )
    await runtime.start(hass, "entry")
    try:
        assert authority.has_durable_import(("light.demo_main",))
        assert authority.record("light.demo_main").legacy
        assert authority.blocks("light.demo_main")
        assert authority.blocks("light.unrelated")
        await runtime.async_process([_ROOM_ID])
        assert executor.calls == []
        assert runtime._ownership.has_legacy()  # type: ignore[attr-defined]
        assert runtime._ownership.last_manual_off_at(_ROOM_ID, {"light_main"}) is None  # type: ignore[attr-defined]
    finally:
        await runtime.stop()
    assert ownership_store.payload == before
    assert ownership_store.saves == 0


async def test_legacy_manual_hold_without_authority_store_blocks_dispatch() -> None:
    hass = _FakeHass()
    _seed_presence_and_light(hass)
    ownership_store = _MemoryOwnershipStore()
    ownership_store.payload = {
        "version": 1,
        "records": [{
            "targetId": "light_main", "source": "manual",
            "confirmed": True, "at": 1,
        }],
        "manualOff": [{"targetId": "light_main", "at": 1}],
        "morning": [],
    }
    before = copy.deepcopy(ownership_store.payload)
    authority = ManualLightAuthority()
    await authority.async_drain()  # No store and no pending event: no-op.
    assert not authority.persistence_ready and not authority.unhealthy
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=executor,
        ownership_store=ownership_store, manual_light_authority=authority,
    )
    await runtime.start(hass, "entry")
    try:
        assert authority.unhealthy
        assert authority.blocks("light.demo_main")
        assert authority.blocks("light.unrelated")
        await runtime.async_process([_ROOM_ID])
        assert executor.calls == []
        assert hass.services.calls == []
    finally:
        await runtime.stop()
    assert ownership_store.payload == before
    assert ownership_store.saves == 0


async def test_noop_authority_drain_cannot_prove_legacy_import() -> None:
    class _AuthorityStore(_MemoryOwnershipStore):
        recovered_previous = False

    class _NoOpDrainAuthority(ManualLightAuthority):
        async def async_drain(self) -> None:
            return None

    hass = _FakeHass()
    _seed_presence_and_light(hass)
    ownership_store = _MemoryOwnershipStore()
    ownership_store.payload = {
        "version": 1,
        "records": [{
            "targetId": "light_main", "source": "manual",
            "confirmed": True, "at": 1,
        }],
        "manualOff": [], "morning": [],
    }
    before = copy.deepcopy(ownership_store.payload)
    authority_store = _AuthorityStore()
    authority = _NoOpDrainAuthority(authority_store)
    authority.begin_startup_observation()
    await authority.async_load()
    seed_writes = authority_store.saves
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=executor,
        ownership_store=ownership_store, manual_light_authority=authority,
    )
    await runtime.start(hass, "entry")
    try:
        authority.finish_startup_observation()
        assert authority.unhealthy
        assert not authority.has_durable_import(("light.demo_main",))
        assert authority.blocks("light.unrelated")
        await runtime.async_process([_ROOM_ID])
        assert executor.calls == []
        assert hass.services.calls == []
    finally:
        await runtime.stop()
    assert authority_store.saves == seed_writes
    assert ownership_store.payload == before
    assert ownership_store.saves == 0


async def test_ambiguous_legacy_room_record_closes_all_lights() -> None:
    class _AuthorityStore(_MemoryOwnershipStore):
        recovered_previous = False

    hass = _FakeHass()
    _seed_presence_and_light(hass)
    ownership_store = _MemoryOwnershipStore()
    ownership_store.payload = {
        "version": 1,
        "records": [{
            "targetId": "light_main", "source": "manual",
            "confirmed": True, "at": _NOW_MS,
        }],
        "manualOff": [], "morning": [],
    }
    before = dict(ownership_store.payload)
    second = _config_payload()
    second["roomId"] = "room_other"
    authority = ManualLightAuthority(_AuthorityStore())
    await authority.async_load()
    runtime = _make_runtime(
        hass, payloads=[_config_payload(), second],
        executor=_SpyExecutor(), ownership_store=ownership_store,
        manual_light_authority=authority,
    )
    await runtime.start(hass, "entry")
    try:
        assert authority.unhealthy
        assert authority.blocks("light.unrelated")
    finally:
        await runtime.stop()
    assert ownership_store.payload == before


async def test_legacy_journal_is_not_migrated_when_authority_save_fails() -> None:
    class _FailingAuthorityStore:
        recovered_previous = False

        async def async_load(self):
            return {"version": 2, "records": [], "recentFingerprints": []}

        async def async_save(self, _payload):
            raise RuntimeError("authority storage unavailable")

    hass = _FakeHass()
    _seed_presence_and_light(hass)
    ownership_store = _MemoryOwnershipStore()
    ownership_store.payload = {
        "version": 1,
        "records": [{
            "targetId": "light_main", "source": "manual",
            "confirmed": True, "at": _NOW_MS,
        }],
        "manualOff": [{"targetId": "light_main", "at": _NOW_MS}],
        "morning": [],
    }
    before = dict(ownership_store.payload)
    authority = ManualLightAuthority(_FailingAuthorityStore())
    authority.begin_startup_observation()
    await authority.async_load()
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=executor,
        ownership_store=ownership_store, manual_light_authority=authority,
    )
    await runtime.start(hass, "entry")
    try:
        assert authority.unhealthy
        assert not authority.persistence_ready
        assert not authority.has_durable_import(("light.demo_main",))
        assert authority.blocks("light.unrelated")
        assert executor.calls == []
        assert runtime._ownership.has_legacy()  # type: ignore[attr-defined]
    finally:
        await runtime.stop()
    assert ownership_store.payload == before


async def test_mixed_legacy_mapping_skips_all_journal_mutation_and_pruning() -> None:
    class _AuthorityStore(_MemoryOwnershipStore):
        recovered_previous = False

    hass = _FakeHass()
    _seed_presence_and_light(hass)
    ownership_store = _MemoryOwnershipStore()
    ownership_store.payload = {
        "version": 1,
        "records": [
            {"targetId": "light_main", "source": "manual", "confirmed": True, "at": 1},
            {"targetId": "missing", "source": "manual", "confirmed": True, "at": 1},
        ],
        "manualOff": [{"targetId": "light_main", "at": 1}], "morning": [],
    }
    before = dict(ownership_store.payload)
    authority_store = _AuthorityStore()
    authority = ManualLightAuthority(authority_store)
    authority.begin_startup_observation()
    await authority.async_load()
    seeded = copy.deepcopy(authority_store.payload)
    seed_writes = authority_store.saves
    runtime = _make_runtime(
        hass, ownership_store=ownership_store, manual_light_authority=authority,
    )
    await runtime.start(hass, "entry")
    try:
        assert authority.unhealthy
        assert authority.record("light.demo_main") is None
        assert authority_store.payload == seeded
        assert authority_store.saves == seed_writes
        assert runtime._ownership.has_legacy()  # type: ignore[attr-defined]
    finally:
        await runtime.stop()
    assert ownership_store.payload == before


async def test_failed_authority_preserves_modern_manual_hold_from_pruning() -> None:
    class _UnavailableAuthorityStore:
        recovered_previous = False

        async def async_load(self):
            return {"version": 2, "records": [], "recentFingerprints": []}

        async def async_save(self, _payload):
            raise RuntimeError("unavailable")

    hass = _FakeHass()
    _seed_presence_and_light(hass)
    hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)
    ownership_store = _MemoryOwnershipStore()
    ownership_store.payload = {
        "version": 1,
        "records": [{
            "roomId": _ROOM_ID, "targetId": "light_main", "source": "manual",
            "confirmed": True, "at": 0,
        }],
        "manualOff": [{"roomId": _ROOM_ID, "targetId": "light_main", "at": 0}],
        "morning": [],
    }
    before = dict(ownership_store.payload)
    authority = ManualLightAuthority(_UnavailableAuthorityStore())
    await authority.async_load()
    runtime = _make_runtime(
        hass, ownership_store=ownership_store, manual_light_authority=authority,
    )
    await runtime.start(hass, "entry")
    try:
        assert authority.unhealthy
        assert runtime._ownership.last_manual_off_at(_ROOM_ID, {"light_main"}) == 0  # type: ignore[attr-defined]
    finally:
        await runtime.stop()
    assert ownership_store.payload == before


async def test_successful_legacy_import_keeps_stale_old_manual_hold_for_rollback() -> None:
    class _AuthorityStore(_MemoryOwnershipStore):
        recovered_previous = False

    hass = _FakeHass()
    _seed_presence_and_light(hass)
    ownership_store = _MemoryOwnershipStore()
    ownership_store.payload = {
        "version": 1,
        "records": [{
            "roomId": _ROOM_ID, "targetId": "light_main", "source": "manual",
            "confirmed": True, "at": 0,
        }],
        "manualOff": [{"roomId": _ROOM_ID, "targetId": "light_main", "at": 0}],
        "morning": [],
    }
    before = copy.deepcopy(ownership_store.payload)
    authority = ManualLightAuthority(_AuthorityStore())
    authority.begin_startup_observation()
    await authority.async_load()
    runtime = _make_runtime(
        hass, ownership_store=ownership_store, manual_light_authority=authority,
    )
    await runtime.start(hass, "entry")
    try:
        assert authority.has_durable_import(("light.demo_main",))
        assert runtime._ownership.last_manual_off_at(_ROOM_ID, {"light_main"}) == 0  # type: ignore[attr-defined]
        assert runtime._ownership.snapshots_for(_ROOM_ID, {"light_main"})[-1].source is OwnershipSource.MANUAL  # type: ignore[attr-defined]
    finally:
        await runtime.stop()
    assert ownership_store.payload == before
    assert ownership_store.saves == 0


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


async def test_restart_keeps_manual_protection_without_observed_absence() -> None:
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

    # Restart after an unseen interval with the light off and nobody present.
    # The gap cannot prove sustained absence, so the manual hold survives.
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
        assert runtime2._ownership.last_manual_off_at(_ROOM_ID, {"light_main"}) == t0  # type: ignore[attr-defined]
        assert runtime2._ownership.snapshots_for(_ROOM_ID, {"light_main"})  # type: ignore[attr-defined]
        config = runtime2.configs()[0]
        assert runtime2._protection_for(config).active is True  # type: ignore[attr-defined]
    finally:
        await runtime2.stop()


async def test_restart_releases_manual_protection_after_new_observed_absence() -> None:
    """A post-restart off interval starts anew and can later release safely."""

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

    # A long gap is not absence. The fresh off state starts a new interval at
    # startup, which must still reach the configured stable duration.
    clock.value = t0 + 600_000
    hass2 = _FakeHass()
    hass2.states.set("binary_sensor.demo_presence", "off", last_changed=_dt(clock.value))
    hass2.states.set("light.demo_main", "off", last_changed=_dt(t0))
    executor = _SpyExecutor()
    runtime2 = _make_runtime(
        hass2,
        commands_enabled=True,
        executor=executor,
        ownership_store=ownership_store,
        now_ms=clock,
        payload=payload,
    )
    await runtime2.start(hass2, "entry")
    try:
        assert executor.calls == []
        clock.value += 5_000
        await runtime2.async_process()
        assert executor.calls == []
        clock.value += 1_000
        hass2.states.set("binary_sensor.demo_presence", "on", last_changed=_dt(clock.value))
        await runtime2.async_process()
        assert [command.action for command in executor.calls] == [LightAction.TURN_ON]
    finally:
        await runtime2.stop()


async def test_restart_keeps_manual_protection_without_presence_sensor() -> None:
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

    # With no configured presence sensor, a restart cannot fabricate absence
    # evidence or silently clear the user's manual decision.
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
        assert runtime2._ownership.last_manual_off_at(_ROOM_ID, {"light_main"}) == t0  # type: ignore[attr-defined]
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


@pytest.mark.parametrize("priority_seconds", [15, 3600, 7200, 86400])
async def test_device_press_manual_record_uses_room_priority_and_preserves_dedup(priority_seconds) -> None:
    class _AuthorityStore(_MemoryOwnershipStore):
        recovered_previous = False

    payload = _trigger_config_payload(action="turn_on")
    payload["manualControlReturn"] = {"prioritySeconds": priority_seconds}
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_NOW_DT)
    hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)
    api = _FakeDeviceAutomationApi()
    executor = _SpyExecutor()
    clock = _Clock(_NOW_MS)
    authority = ManualLightAuthority(_AuthorityStore())
    await authority.async_load()
    runtime = _make_runtime(hass, commands_enabled=False, payload=payload,
                            device_automation_api=api, executor=executor,
                            now_ms=clock, manual_light_authority=authority)
    await runtime.start(hass, "entry")
    try:
        _, action, _ = api.attached[0]
        await action({}, None)
        record = authority.record("light.demo_main")
        assert record is not None
        assert record.action == "on" and record.source == "device_trigger"
        assert record.deadline_ms - record.observed_at_ms == priority_seconds * 1000
        generation = record.generation
        await action({}, None)
        assert authority.record("light.demo_main").generation == generation
        assert authority.record("light.demo_main").deadline_ms == record.deadline_ms
        clock.value += 3000
        await action({}, None)
        renewed = authority.record("light.demo_main")
        assert renewed.observed_at_ms == clock.value
        assert renewed.deadline_ms == clock.value + priority_seconds * 1000
        assert renewed.generation > generation
        assert executor.calls == []
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


async def test_device_trigger_return_to_auto_keeps_manual_hold_without_calling_light() -> None:
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
        assert runtime._ownership.last_manual_off_at(_ROOM_ID, {"light_main"}) == _NOW_MS  # type: ignore[attr-defined]
        ownership = runtime._ownership.snapshots_for(_ROOM_ID, {"light_main"})  # type: ignore[attr-defined]
        assert ownership[-1].source is OwnershipSource.MANUAL
    finally:
        await runtime.stop()


async def test_return_to_auto_noop_skips_evaluation_and_write_for_two_targets() -> None:
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_NOW_DT)
    hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)
    hass.states.set("switch.demo_second", "off", last_changed=_NOW_DT)
    api = _FakeDeviceAutomationApi()
    payload = _trigger_config_payload(action="return_to_auto")
    payload["devices"]["light_targets"].append({  # type: ignore[index]
        "id": "second", "name": "Второй свет", "kind": "switch",
        "entityId": "switch.demo_second", "role": "other", "groupId": None,
        "brightness": False, "color_temperature": False, "autoAdoptOverride": None,
    })
    ownership_store = _MemoryOwnershipStore()
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=_SpyExecutor(), payload=payload,
        device_automation_api=api, ownership_store=ownership_store,
    )
    evaluated: list[tuple[str, ...] | None] = []

    async def unexpected_process(room_ids=None):
        evaluated.append(room_ids)

    await runtime.start(hass, "entry")
    try:
        runtime.async_process = unexpected_process  # type: ignore[method-assign]
        runtime._ownership.record_manual(_ROOM_ID, "light_main", turned_off=True)  # type: ignore[attr-defined]
        runtime._ownership.record_manual(_ROOM_ID, "second", turned_off=True)  # type: ignore[attr-defined]
        _, action, _ = api.attached[0]
        await action({}, None)  # type: ignore[operator]
        assert evaluated == []
        assert ownership_store.payload is None
        records = runtime._ownership.snapshots_for(_ROOM_ID, {"light_main", "second"})  # type: ignore[attr-defined]
        assert all(record.source is OwnershipSource.MANUAL for record in records)
    finally:
        await runtime.stop()


async def test_explicit_return_removes_old_auto_history_before_evaluation() -> None:
    class _AuthorityStore(_MemoryOwnershipStore):
        recovered_previous = False

    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_NOW_DT)
    hass.states.set("light.demo_main", "on", last_changed=_NOW_DT)
    from tests.test_room_lighting_force_auto import seed_participants
    priority, _off_protection = await seed_participants(hass)
    authority_store = _AuthorityStore()
    authority = ManualLightAuthority(authority_store)
    await authority.async_load()
    ownership_store = _MemoryOwnershipStore()
    executor = _SpyExecutor()
    api = _FakeDeviceAutomationApi()
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=executor,
        payload=_trigger_config_payload(action="return_to_auto"),
        ownership_store=ownership_store, manual_light_authority=authority,
        device_automation_api=api,
        light_priority=priority,
    )
    await runtime.start(hass, "entry")
    try:
        runtime._ownership.record_auto(_ROOM_ID, "light_main", _NOW_MS - 1)  # type: ignore[attr-defined]
        runtime._ownership.record_manual(_ROOM_ID, "light_main", _NOW_MS)  # type: ignore[attr-defined]
        authority.capture("light.demo_main", "on", "device_trigger", "manual-on", observed_at_ms=_now_ms())
        await authority.async_drain()
        processed: list[object] = []

        async def unexpected_process(*_args, **_kwargs):
            assert authority.record("light.demo_main") is None
            assert ownership_store.payload["records"] == []
            processed.append(True)

        runtime.async_process = unexpected_process  # type: ignore[method-assign]
        _, action, _ = api.attached[0]
        returned = await action({}, None)  # type: ignore[operator]

        assert returned is None
        assert runtime._ownership.snapshots_for(_ROOM_ID, {"light_main"}) == ()  # type: ignore[attr-defined]
        assert authority.record("light.demo_main") is None
        assert not authority.blocks("light.demo_main")
        assert ownership_store.saves == 1
        assert processed == [True]
        assert executor.calls == []
    finally:
        await runtime.stop()


async def test_explicit_return_refuses_manual_off_journal_mismatch() -> None:
    class _AuthorityStore(_MemoryOwnershipStore):
        recovered_previous = False

    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_NOW_DT)
    hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)
    authority = ManualLightAuthority(_AuthorityStore())
    await authority.async_load()
    ownership_store = _MemoryOwnershipStore()
    runtime = _make_runtime(
        hass, ownership_store=ownership_store, manual_light_authority=authority,
    )
    await runtime.start(hass, "entry")
    try:
        runtime._ownership.record_manual(_ROOM_ID, "light_main", turned_off=True)  # type: ignore[attr-defined]
        authority.capture("light.demo_main", "on", "device_trigger", "common-on", observed_at_ms=_now_ms())
        await authority.async_drain()

        assert await runtime.async_return_room_to_automatic(_ROOM_ID) == ()
        assert authority.record("light.demo_main").action == "on"
        assert authority.blocks("light.demo_main")
        assert runtime._ownership.last_manual_off_at(_ROOM_ID, {"light_main"}) == _NOW_MS  # type: ignore[attr-defined]
        assert ownership_store.payload is None
    finally:
        await runtime.stop()


async def test_explicit_return_refuses_common_on_without_matching_room_manual_on() -> None:
    class _AuthorityStore(_MemoryOwnershipStore):
        recovered_previous = False

    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_NOW_DT)
    hass.states.set("light.demo_main", "on", last_changed=_NOW_DT)
    authority = ManualLightAuthority(_AuthorityStore())
    await authority.async_load()
    ownership_store = _MemoryOwnershipStore()
    runtime = _make_runtime(
        hass, ownership_store=ownership_store, manual_light_authority=authority,
    )
    await runtime.start(hass, "entry")
    try:
        runtime._ownership.record_auto(_ROOM_ID, "light_main", _NOW_MS)  # type: ignore[attr-defined]
        authority.capture("light.demo_main", "on", "service", "common-on", observed_at_ms=_now_ms())
        await authority.async_drain()

        assert await runtime.async_return_room_to_automatic(_ROOM_ID) == ()
        assert authority.record("light.demo_main").action == "on"
        assert authority.blocks("light.demo_main")
        assert ownership_store.payload is None
    finally:
        await runtime.stop()


def test_return_history_preserves_newer_manual_and_never_old_auto() -> None:
    journal = RoomLightingOwnershipJournal(now_ms=lambda: _NOW_MS)
    journal.record_auto(_ROOM_ID, "light_main", _NOW_MS - 2)
    journal.record_manual(_ROOM_ID, "light_main", _NOW_MS - 1)
    boundary = journal.manual_return_boundary()
    journal.record_manual(_ROOM_ID, "light_main", _NOW_MS)

    assert journal.release_room_manual_history_through(
        _ROOM_ID, {"light_main"}, {"light_main"}, through_generation=boundary
    ) == ("light_main",)
    records = journal.snapshots_for(_ROOM_ID, {"light_main"})
    assert len(records) == 1
    assert records[0] == OwnershipSnapshot(
        "light_main", OwnershipSource.MANUAL, True, _NOW_MS,
        turned_off=False, generation=2,
    )


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
        away_entity_id=AWAY_ENTITY_ID,
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
        # Only a light that this runtime demonstrably enabled may be switched
        # off by away automation. An on state alone is manual by default.
        runtime._ownership.record_auto(_ROOM_ID, "light_main", _NOW_MS, confirmed=True)

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
        away_entity_id=AWAY_ENTITY_ID,
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


def test_ownership_journal_migration_preserves_ambiguous_target_id() -> None:
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
    assert journal.has_legacy()
    assert journal.to_payload()["records"] == [{
        "targetId": "chandelier", "source": "manual",
        "confirmed": True, "at": 4_000,
        "turnedOff": False, "generation": 0,
    }]
    assert journal.to_payload()["manualOff"] == [{"targetId": "chandelier", "at": 4_000}]
    restored = RoomLightingOwnershipJournal()
    restored.restore(journal.to_payload())
    assert restored.has_legacy()
    assert restored.to_payload() == journal.to_payload()


def test_legacy_manual_authority_plan_requires_one_physical_light_mapping() -> None:
    def journal_for(*target_ids: str) -> RoomLightingOwnershipJournal:
        journal = RoomLightingOwnershipJournal()
        journal.restore({
            "version": 1,
            "records": [
                {"targetId": target_id, "source": "manual", "confirmed": True, "at": 1}
                for target_id in target_ids
            ],
            "manualOff": [],
        })
        return journal

    exact = SimpleNamespace(
        room_id="room", devices=SimpleNamespace(
            light_targets=(SimpleNamespace(id="main", entity_id="light.main"),),
            auxiliaries=(SimpleNamespace(id="fan", entity_id="switch.fan"),),
        ),
    )
    assert legacy_manual_authority_plan(journal_for("main"), (exact,)) == (
        ("light.main",), False,
    )
    assert legacy_manual_authority_plan(journal_for("missing"), (exact,)) == ((), True)
    assert legacy_manual_authority_plan(journal_for("fan"), (exact,)) == ((), False)

    duplicate = SimpleNamespace(
        room_id="room", devices=SimpleNamespace(
            light_targets=(
                SimpleNamespace(id="main", entity_id="light.main"),
                SimpleNamespace(id="alias", entity_id="light.main"),
            ),
            auxiliaries=(),
        ),
    )
    assert legacy_manual_authority_plan(journal_for("main", "alias"), (duplicate,)) == (
        ("light.main",), True,
    )

    room_a = SimpleNamespace(
        room_id="room_a", devices=SimpleNamespace(
            light_targets=(SimpleNamespace(id="shared", entity_id="light.a"),),
            auxiliaries=(),
        ),
    )
    room_b = SimpleNamespace(
        room_id="room_b", devices=SimpleNamespace(
            light_targets=(SimpleNamespace(id="shared", entity_id="light.b"),),
            auxiliaries=(),
        ),
    )
    fan_room = SimpleNamespace(
        room_id="fan_room", devices=SimpleNamespace(
            light_targets=(),
            auxiliaries=(SimpleNamespace(id="shared", entity_id="switch.fan"),),
        ),
    )
    assert legacy_manual_authority_plan(
        journal_for("shared"), (room_a, room_b, fan_room)
    ) == ((), True)

    scoped = RoomLightingOwnershipJournal()
    scoped.restore({
        "version": 1,
        "records": [{
            "roomId": "fan_room", "targetId": "shared", "source": "manual",
            "confirmed": True, "at": 1,
        }],
        "manualOff": [],
    })
    assert legacy_manual_authority_plan(scoped, (room_a, fan_room)) == ((), True)

    missing_room = RoomLightingOwnershipJournal()
    missing_room.restore({
        "version": 1,
        "records": [{
            "roomId": "missing", "targetId": "shared", "source": "manual",
            "confirmed": True, "at": 1,
        }],
        "manualOff": [],
    })
    assert legacy_manual_authority_plan(missing_room, (room_a, fan_room)) == ((), True)


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


@pytest.mark.parametrize("path", ("exact", "legacy"))
@pytest.mark.parametrize("away_transition", ("before_pending", "after_pending"))
async def test_untrusted_away_closes_only_saved_power_intent(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    away_transition: str,
) -> None:
    """An Away change cannot leave an unsent automatic power retry marker."""

    class PowerExecutor(_SpyExecutor):
        def __init__(self) -> None:
            super().__init__()
            self.power_calls: list[str] = []

        async def async_power_on(self, hass: object, entity_id: str) -> dict[str, object]:
            del hass
            self.power_calls.append(entity_id)
            return {"confirmed": True, "sent": True, "sendOutcome": "sent"}

    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_NOW_DT)
    hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)
    hass.states.set(AWAY_ENTITY_ID, "off", last_changed=_NOW_DT)
    hass.states.set(POWER_ENTITY, "off", last_changed=_NOW_DT)
    shadow = RoomLightingShadowService(_MemoryShadowStore())
    executor = PowerExecutor()
    runtime = RoomLightingRuntime(
        hass,
        _ConfigService(config_from_payload(_config_payload())),
        shadow,
        executor=executor,
        now_ms=lambda: _NOW_MS,
        track_state_changes=lambda hass, entities, callback: (lambda: None),
        track_interval=lambda hass, callback, interval: (lambda: None),
        listen_bus=lambda hass, event_type, callback: (lambda: None),
        electrical_breaker_resolver=(lambda _entity: False) if path == "exact" else None,
        away_entity_id=AWAY_ENTITY_ID,
    )

    await runtime.start(hass, "entry")
    try:
        if away_transition == "before_pending":
            hass.states.set(AWAY_ENTITY_ID, "unavailable", last_changed=_NOW_DT)
        else:
            original_persist = runtime._persist_pending_power_dispatch

            async def persist_then_invalidate_away(**kwargs: object) -> bool:
                persisted = await original_persist(**kwargs)
                hass.states.set(AWAY_ENTITY_ID, "unavailable", last_changed=_NOW_DT)
                return persisted

            monkeypatch.setattr(
                runtime,
                "_persist_pending_power_dispatch",
                persist_then_invalidate_away,
            )

        if path == "exact":
            powered, detail = await runtime._ensure_power_source_locked(
                DevicePowerDependency(
                    dependent_entity_id="light.demo_main",
                    power_source_entity_id=POWER_ENTITY,
                    policy=AUTO_TURN_ON_POLICY,
                    warmup_seconds=0,
                ),
                at=_NOW_MS,
                room_id=_ROOM_ID,
                operation_id="away-power-test",
                config_version=1,
command_action="turn_on",
            )
        else:
            powered, detail = await runtime._ensure_legacy_room_power_locked(
                POWER_ENTITY,
                at=_NOW_MS,
                room_id=_ROOM_ID,
                operation_id="away-power-test",
                config_version=1,
command_action="turn_on",
            )

        assert not powered
        assert detail["reason"] == "away_sensor_unavailable"
        assert detail["dispatchAttempt"] is False
        assert detail["sent"] is False
        assert detail["sendOutcome"] == "not_sent"
        assert executor.power_calls == []
        assert POWER_ENTITY not in runtime._power_failures
        failures = shadow.journal_payload().get("dispatchFailures", [])
        assert all(item.get("key") != f"power:{POWER_ENTITY}" for item in failures)

        if away_transition == "after_pending":
            receipts = [
                receipt
                for entry in shadow.journal_payload()["entries"]
                for receipt in entry.get("dispatch", [])
            ]
            assert {
                "sourceEntityId": POWER_ENTITY,
                "dispatchAttempt": False,
                "sent": False,
                "sendOutcome": "not_sent",
                "reason": "away_sensor_unavailable",
            }.items() <= receipts[-1]["power"].items()
    finally:
        await runtime.stop()


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


async def test_manual_on_survives_hour_and_restart_without_auto_off() -> None:
    """Luna 2026-10-12 ( сквозной критерий): a manual ON keeps its
    protection after the one-hour authority priority lapsed AND after a
    runtime restart: the automatic branch neither turns the light off nor
    re-arms it, and no automatic ownership is created."""
    payload = _config_payload(
        minimum_interval_seconds=600,
        stable_absence_seconds=30,
        commands_enabled=True,
    )
    ownership_store = _MemoryOwnershipStore()
    hass = _FakeHass()
    _seed_presence_and_light(hass)
    hass.states.set("light.demo_main", "on", last_changed=_NOW_DT)
    executor = RoomLightingHaExecutor()
    runtime = _make_runtime(
        hass,
        commands_enabled=True,
        executor=executor,
        payload=payload,
        ownership_store=ownership_store,
    )
    await runtime.start(hass, "entry")
    try:
        # A person turns the light on manually (no auto command ever ran).
        runtime._ownership.record_manual(_ROOM_ID, "light_main", _NOW_MS)  # type: ignore[attr-defined]
        await runtime._persist_ownership()  # type: ignore[attr-defined]
    finally:
        await runtime.stop()

    # A manual priority record exists too; one hour later it has EXPIRED.
    from custom_components.hausman_hub.application.manual_light_authority import (
        ManualLightAuthority,
    )
    authority = ManualLightAuthority()
    authority.set_related_sources(
        __import__(
            "custom_components.hausman_hub.application.related_light_sources",
            fromlist=["RelatedLightSources"],
        ).RelatedLightSources.from_configs(()),
    )
    # The manual priority was captured more than an hour before the
    # restart moment (the authority uses the real clock).
    authority.capture(
        "light.demo_main", "on", "device_trigger", "manual-on",
        observed_at_ms=int(datetime.now(timezone.utc).timestamp() * 1000)
        - 2 * 3_600_000,
    )
    await runtime.stop()

    # One hour passes (the authority priority has expired) and the runtime
    # restarts on the SAME ownership store.
    later = _NOW_MS + 3_600_000 + 5_000
    assert not authority.blocks("light.demo_main"), (
        "the one-hour priority must have expired"
    )
    hass2 = _FakeHass()
    hass2.states.set("binary_sensor.demo_presence", "on", last_changed=_dt(later))
    hass2.states.set("light.demo_main", "on", last_changed=_dt(later))
    hass2.states.set(AWAY_ENTITY_ID, "off", last_changed=_dt(later))
    executor2 = RoomLightingHaExecutor(manual_light_authority=authority)
    runtime2 = _make_runtime(
        hass2,
        commands_enabled=True,
        executor=executor2,
        payload=payload,
        ownership_store=ownership_store,
        now_ms=lambda: later,
        manual_light_authority=authority,
    )
    await runtime2.start(hass2, "entry")
    try:
        await asyncio.gather(*hass2.tasks)
        # NO automatic commands to the manual-on light at all: no turn_off,
        # no re-arming brightness/color changes.
        light_commands = [
            call for call in hass2.services.calls
            if call[2].get("entity_id") == "light.demo_main"
        ]
        assert not light_commands, light_commands
        # The MANUAL ownership evidence survived the restart in memory of
        # the same journal: the light still resolves as manual (no proven
        # auto ownership), and no AUTO record exists.
        snapshots = [
            snapshot
            for snapshot in runtime2._ownership.snapshots()  # type: ignore[attr-defined]
            if snapshot.target_id == "light_main"
        ]
        assert snapshots, "the manual ownership journal must survive the restart"
        assert any(
            snapshot.source.value == "manual" for snapshot in snapshots
        ), snapshots
        assert not any(
            snapshot.source.value == "auto" for snapshot in snapshots
        ), snapshots
    finally:
        await runtime2.stop()


async def test_expired_manual_off_on_sibling_target_allows_autolight() -> None:
    """Evening live test 2026-10-12: a manual OFF on one room target (the
    spots switch) blocked the WHOLE component forever — even the pair
    power preparation of the chandelier was denied with
    ``manual_light_priority`` long after the owner's one-hour priority had
    lapsed. After the expiry repair the automatic branch powers the pair
    and turns the chandelier on."""
    payload = _config_payload(
        minimum_interval_seconds=600,
        stable_absence_seconds=30,
        commands_enabled=True,
        power_switch_entity=POWER_ENTITY,
    )
    payload["devices"]["light_targets"].append({  # type: ignore[index]
        "id": "light_spots",
        "name": "Точки",
        "kind": "switch",
        "entityId": "switch.demo_spots",
        "role": "accent",
        "groupId": None,
        "brightness": False,
        "color_temperature": False,
        "autoAdoptOverride": None,
    })
    hass = _FakeHass()
    _seed_presence_and_light(hass)
    hass.states.set(POWER_ENTITY, "off", last_changed=_NOW_DT)
    hass.states.set("switch.demo_spots", "off", last_changed=_NOW_DT)

    class _AuthorityStore(_MemoryOwnershipStore):
        recovered_previous = False

    from custom_components.hausman_hub.application.manual_light_authority import (
        ManualLightAuthority,
    )
    authority = ManualLightAuthority(_AuthorityStore())
    await authority.async_load()
    # A person switched the spots off MORE than an hour ago.
    authority.capture(
        "switch.demo_spots", "off", "state", "spots-off",
        observed_at_ms=int(datetime.now(timezone.utc).timestamp() * 1000)
        - 2 * 3_600_000,
    )
    await authority.async_drain()
    executor = RoomLightingHaExecutor(manual_light_authority=authority)
    runtime = _make_runtime(
        hass,
        commands_enabled=True,
        executor=executor,
        payload=payload,
        manual_light_authority=authority,
    )
    await runtime.start(hass, "entry")
    try:
        entities = [call[2].get("entity_id") for call in hass.services.calls]
        assert POWER_ENTITY in entities, (
            "the expired priority must not deny the power preparation"
        )
        assert "light.demo_main" in entities
        assert entities.index(POWER_ENTITY) < entities.index("light.demo_main")
    finally:
        await runtime.stop()


async def test_active_manual_off_on_sibling_target_still_blocks_autolight() -> None:
    """The SAME scenario with a STILL-ACTIVE priority: no commands at all
    (deny-only, fail closed)."""
    payload = _config_payload(
        minimum_interval_seconds=600,
        stable_absence_seconds=30,
        commands_enabled=True,
        power_switch_entity=POWER_ENTITY,
    )
    payload["devices"]["light_targets"].append({  # type: ignore[index]
        "id": "light_spots",
        "name": "Точки",
        "kind": "switch",
        "entityId": "switch.demo_spots",
        "role": "accent",
        "groupId": None,
        "brightness": False,
        "color_temperature": False,
        "autoAdoptOverride": None,
    })
    hass = _FakeHass()
    _seed_presence_and_light(hass)
    hass.states.set(POWER_ENTITY, "off", last_changed=_NOW_DT)
    hass.states.set("switch.demo_spots", "off", last_changed=_NOW_DT)

    class _AuthorityStore(_MemoryOwnershipStore):
        recovered_previous = False

    from custom_components.hausman_hub.application.manual_light_authority import (
        ManualLightAuthority,
    )
    authority = ManualLightAuthority(_AuthorityStore())
    await authority.async_load()
    # The person switched the spots off five minutes ago: priority active.
    authority.capture(
        "switch.demo_spots", "off", "state", "spots-off",
        observed_at_ms=int(datetime.now(timezone.utc).timestamp() * 1000)
        - 5 * 60_000,
    )
    await authority.async_drain()
    executor = RoomLightingHaExecutor(manual_light_authority=authority)
    runtime = _make_runtime(
        hass,
        commands_enabled=True,
        executor=executor,
        payload=payload,
        manual_light_authority=authority,
    )
    await runtime.start(hass, "entry")
    try:
        entities = [call[2].get("entity_id") for call in hass.services.calls]
        assert POWER_ENTITY not in entities
        assert "light.demo_main" not in entities
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
    if hass.states.get(AWAY_ENTITY_ID) is None:
        hass.states.set(AWAY_ENTITY_ID, "off", last_changed=_NOW_DT)
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
        away_entity_id=AWAY_ENTITY_ID,
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


async def test_shower_exhaust_fails_closed_when_away_is_untrusted() -> None:
    """The automatic fan path shares the protected Away permit."""

    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_NOW_DT)
    hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)
    hass.states.set("sensor.demo_shower_humidity", "70", last_changed=_NOW_DT)
    hass.states.set("switch.demo_shower_fan", "off", last_changed=_NOW_DT)
    executor = _SpyExecutor()
    runtime, _shadow = _runtime_with_shadow(
        hass, _shower_payload(commands_enabled=True), executor=executor
    )
    hass.states.set(AWAY_ENTITY_ID, "unavailable", last_changed=_NOW_DT)

    await runtime.start(hass, "entry")
    try:
        assert runtime.away_diagnostic == "away_sensor_unavailable"
        assert executor.calls == []
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
        hass.states.set("binary_sensor.demo_presence", "off",
                        last_changed=datetime.fromtimestamp(clock[0] / 1000, _TZ))
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
    # HA semantics: last_changed stays at the ON transition while the sensor
    # holds ON; a moved last_changed would prove a new ON episode instead.
    context = RoomLightingContext(
        now=now[0], timezone=_TZ, sunrise=time(7, 0), sunset=time(19, 0),
        sensors=(SensorSnapshot("sensor_demo_presence", SensorKind.PRESENCE, SensorState.ON, now[0]),),
    )
    assert runtime._observe_curve_presence(config, context, now[0]) == (False, 0)  # type: ignore[attr-defined]
    now[0] += 15_000
    context = RoomLightingContext(
        now=now[0], timezone=_TZ, sunrise=time(7, 0), sunset=time(19, 0),
        sensors=(SensorSnapshot("sensor_demo_presence", SensorKind.PRESENCE, SensorState.ON, now[0] - 15_000),),
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

    def context(state: SensorState, moment: int, last_changed: int | None = None) -> RoomLightingContext:
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
                    last_changed if last_changed is not None else moment,
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
    # Presence observed again restarts the confirmation: the new episode
    # holds ON continuously (HA keeps last_changed at the transition).
    confirmed_now, _ = runtime._observe_curve_presence(  # type: ignore[attr-defined]
        config, context(SensorState.ON, base + 1_860_000), base + 1_860_000
    )
    assert confirmed_now is False
    confirmed_after, _ = runtime._observe_curve_presence(  # type: ignore[attr-defined]
        config, context(SensorState.ON, base + 1_875_000, base + 1_860_000),
        base + 1_875_000,
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


async def test_curve_stale_presence_with_fresh_motion_off_does_not_prove_absence() -> None:
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
    assert stale_on_and_motion_off is SensorState.UNKNOWN

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


async def test_curve_two_sensor_stale_on_never_accumulates_absence() -> None:
    payload = _curve_payload(commands_enabled=False)
    config = config_from_payload(payload)
    runtime = _make_runtime(_FakeHass(), payload=payload, now_ms=lambda: _NOW_MS)
    for delta in (0, 600_000, 1_200_000):
        now = _NOW_MS + delta
        context = RoomLightingContext(now=now, timezone=_TZ, sunrise=time(7), sunset=time(19), sensors=(
            SensorSnapshot("presence_demo", SensorKind.PRESENCE, SensorState.ON, _NOW_MS - 1_800_000),
            SensorSnapshot("motion_demo", SensorKind.MOTION, SensorState.OFF, now),
        ))
        assert runtime._observe_curve_presence(config, context, now) == (False, 0)


async def test_curve_stale_off_invalidates_old_manual_absence() -> None:
    payload = _curve_payload(commands_enabled=False)
    config = config_from_payload(payload)
    runtime = _make_runtime(_FakeHass(), payload=payload, now_ms=lambda: _NOW_MS)
    runtime._absence[config.room_id] = (True, _NOW_MS - 600_000)
    context = RoomLightingContext(now=_NOW_MS, timezone=_TZ, sunrise=time(7), sunset=time(19), sensors=(
        SensorSnapshot("presence_demo", SensorKind.PRESENCE, SensorState.OFF, _NOW_MS - 86_401_000),
        SensorSnapshot("motion_demo", SensorKind.MOTION, SensorState.OFF, _NOW_MS),
    ))
    runtime._observe_absence(config, context)
    assert config.room_id not in runtime._absence


async def test_curve_two_known_off_sensors_count_fresh_ten_minutes_after_fault() -> None:
    payload = _curve_payload(commands_enabled=False)
    config = config_from_payload(payload)
    runtime = _make_runtime(_FakeHass(), payload=payload, now_ms=lambda: _NOW_MS)

    def context(now, first_state=SensorState.OFF):
        return RoomLightingContext(now=now, timezone=_TZ, sunrise=time(7), sunset=time(19), sensors=(
            SensorSnapshot("presence_demo", SensorKind.PRESENCE, first_state, _NOW_MS),
            SensorSnapshot("motion_demo", SensorKind.MOTION, SensorState.OFF, _NOW_MS),
        ))

    assert runtime._observe_curve_presence(config, context(_NOW_MS), _NOW_MS) == (False, 0)
    assert runtime._observe_curve_presence(config, context(_NOW_MS + 599_000), _NOW_MS + 599_000) == (False, 599)
    assert runtime._observe_curve_presence(config, context(_NOW_MS + 600_000), _NOW_MS + 600_000) == (False, 600)
    assert runtime._observe_curve_presence(config, context(_NOW_MS + 610_000, SensorState.UNKNOWN), _NOW_MS + 610_000) == (False, 0)
    assert runtime._observe_curve_presence(config, context(_NOW_MS + 620_000), _NOW_MS + 620_000) == (False, 0)
    assert runtime._observe_curve_presence(config, context(_NOW_MS + 1_220_000), _NOW_MS + 1_220_000) == (False, 600)


@pytest.mark.parametrize("motion_state", ["unknown", "unavailable", "on"])
async def test_actual_curve_process_does_not_accumulate_absence_with_uncertain_motion(motion_state) -> None:
    payload = _curve_payload(commands_enabled=False)
    payload["devices"]["sensors"].append({
        "id": "sensor_demo_motion", "name": "Движение", "kind": "motion",
        "entityId": "binary_sensor.demo_motion", "autoAdoptOverride": None,
    })
    hass = _FakeHass()
    now = [_NOW_MS]
    hass.states.set(AWAY_ENTITY_ID, "off", last_changed=_NOW_DT)
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_NOW_DT)
    hass.states.set("binary_sensor.demo_motion", motion_state, last_changed=datetime.fromtimestamp((_NOW_MS - 700_000) / 1000, _TZ))
    hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)
    hass.states.set("light.demo_mirror", "off", last_changed=_NOW_DT)
    executor = _SpyExecutor()
    runtime = _make_runtime(hass, payload=payload, now_ms=lambda: now[0], executor=executor)
    await runtime.start(hass, "entry")
    try:
        now[0] += 600_000
        await runtime.async_process((_ROOM_ID,))
        assert runtime._curve_presence[_ROOM_ID].get("absence", 0) == 0
        assert runtime._curve_presence[_ROOM_ID]["last_seen"] is None
        recovered = datetime.fromtimestamp(now[0] / 1000, _TZ)
        hass.states.set("binary_sensor.demo_motion", "off", last_changed=recovered)
        await runtime.async_process((_ROOM_ID,))
        assert runtime._curve_presence[_ROOM_ID].get("absence", 0) == 0
        now[0] += 599_000
        await runtime.async_process((_ROOM_ID,))
        assert runtime._curve_presence[_ROOM_ID]["absence"] == 599
        now[0] += 1000
        await runtime.async_process((_ROOM_ID,))
        assert runtime._curve_presence[_ROOM_ID]["absence"] == 600
        assert executor.calls == []
    finally:
        await runtime.stop()


async def test_regular_profile_stale_on_clears_old_manual_absence() -> None:
    from custom_components.hausman_hub.domain.room_lighting_engine import LightSnapshot
    payload = _config_payload(commands_enabled=False)
    payload["devices"]["sensors"].append({
        "id": "sensor_demo_motion", "name": "Движение", "kind": "motion",
        "entityId": "binary_sensor.demo_motion", "autoAdoptOverride": None,
    })
    config = config_from_payload(payload)
    runtime = _make_runtime(_FakeHass(), payload=payload, now_ms=lambda: _NOW_MS)
    runtime._ownership.record_manual(config.room_id, "light_main", _NOW_MS - 700_000, confirmed=True, turned_off=True)
    runtime._absence[config.room_id] = (True, _NOW_MS - 600_000)
    context = RoomLightingContext(now=_NOW_MS, timezone=_TZ, sunrise=time(7), sunset=time(19), sensors=(
        SensorSnapshot("sensor_demo_presence", SensorKind.PRESENCE, SensorState.ON, _NOW_MS - 700_000),
        SensorSnapshot("sensor_demo_motion", SensorKind.MOTION, SensorState.ON, _NOW_MS),
    ), lights=(LightSnapshot("light_main", SensorState.OFF, _NOW_MS),))
    runtime._observe_absence(config, context)
    protection = runtime._protection_for(config)
    assert protection.active
    assert not protection.absence_confirmed
    from dataclasses import replace
    from custom_components.hausman_hub.domain.room_lighting_engine import evaluate_room_lighting
    checked = replace(context, protection=protection, ownership=runtime._ownership.snapshots_for(config.room_id, {"light_main"}))
    assert not evaluate_room_lighting(config, checked).commands


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
                assert executor.owns_service_context(context, data["entity_id"], "on")
                own_event = _event(domain, service, data["entity_id"])
                own_event.context = context
                runtime._service_event(own_event)
                assert not runtime._ownership.snapshots_for(_ROOM_ID, {"light_main"})
                child = SimpleNamespace(id="child-automation", parent_id=context.id)
                assert executor.owns_service_context(child, "light.demo_main", "on")
                assert not executor.owns_service_context(child, "light.demo_mirror", "on")
                mixed_targets = _event("light", "turn_on", "light.demo_main")
                mixed_targets.data["service_data"]["entity_id"] = [
                    "light.demo_main", "light.demo_mirror",
                ]
                mixed_targets.context = child
                runtime._service_event(mixed_targets)
                assert runtime._ownership.snapshots_for(_ROOM_ID, {"light_mirror"})
                assert not runtime._ownership.snapshots_for(_ROOM_ID, {"light_main"})

                opposite_action = _event("light", "turn_off", "light.demo_main")
                opposite_action.context = child
                assert not executor.owns_service_context(child, "light.demo_main", "off")
                runtime._service_event(opposite_action)
                assert runtime._ownership.snapshots_for(_ROOM_ID, {"light_main"})
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


@pytest.mark.parametrize("action", [LightAction.TURN_ON, LightAction.SET_BRIGHTNESS, LightAction.SET_COLOR_TEMPERATURE])
@pytest.mark.parametrize("inverted", [False, True])
@pytest.mark.parametrize("attributes, expected", [
    ({"brightness": 102, "color_temp_kelvin": 3000}, True),
    ({"brightness": 255, "color_temp_kelvin": 3000}, False),
    ({"brightness": 102, "color_temp_kelvin": 3500}, False),
    ({"brightness": 255, "color_temp_kelvin": 3500}, False),
    ({"color_temp_kelvin": 3000}, False),
    ({"brightness": 102}, False),
    ({"brightness": 107, "color_temp_kelvin": 3050}, True),
    ({"brightness": 110, "color_temp_kelvin": 3000}, False),
    ({"brightness": 102, "color_temp_kelvin": 3051}, False),
    ({"brightness": False, "color_temp_kelvin": 3000}, False),
    ({"brightness": 102, "color_temp_kelvin": "not-a-colour"}, False),
])
async def test_executor_receipt_requires_every_requested_property(action, inverted, attributes, expected):
    """No fabricated confirmed flag: production execute reads the fake device."""
    hass = _FakeHass()
    executor = _light_executor(hass, inverted=inverted)
    observed = dict(attributes)
    kelvin = observed.get("color_temp_kelvin")
    if inverted and type(kelvin) is int:
        observed["color_temp_kelvin"] = 6000 - kelvin
    observed.update(min_color_temp_kelvin=2000, max_color_temp_kelvin=6535)

    class ObservedServices(_FakeServices):
        async def async_call(self, domain, service, data, blocking=True, context=None):
            await super().async_call(domain, service, data, blocking, context)
            hass.states.set("light.demo_main", "on", observed, last_changed=_NOW_DT)

    hass.services = ObservedServices(hass)
    receipt = await executor.execute(hass, PlannedCommand("light_main", action, brightness=40, color_temperature=3000))
    assert receipt.sent and receipt.send_outcome == "sent"
    assert receipt.confirmed is expected
    assert receipt.to_payload()["confirmed"] is expected


async def test_executor_plain_switch_on_keeps_state_only_confirmation():
    hass = _FakeHass()
    hass.states.set("switch.demo_mirror", "off", last_changed=_NOW_DT)
    target = LightTarget(id="light_mirror", name="Зеркало", kind=LightKind.SWITCH,
                         brightness=False, color_temperature=False, entity_id="switch.demo_mirror")
    executor = RoomLightingHaExecutor({target.id: target})
    receipt = await executor.execute(hass, PlannedCommand(target.id, LightAction.TURN_ON))
    assert receipt.confirmed and receipt.sent
    assert hass.services.calls == [("switch", "turn_on", {"entity_id": "switch.demo_mirror"})]


@pytest.mark.parametrize("full_parameters", [False, True])
async def test_curve_real_executor_full_main_controls_mirror_handover(full_parameters):
    """Earn mirror ownership with real execute; wrong main params preserve it."""
    hass = _FakeHass()
    now = [int(datetime(2026, 10, 7, 7, 0, tzinfo=_TZ).timestamp() * 1000)]
    payload = _curve_payload(commands_enabled=True)
    config = config_from_payload(payload)
    executor = RoomLightingHaExecutor({target.id: target for target in config.devices.light_targets})
    for entity in ("light.demo_main", "light.demo_mirror"):
        hass.states.set(entity, "off", last_changed=datetime.fromtimestamp(now[0] / 1000, _TZ))
    hass.states.set("binary_sensor.demo_presence", "unknown", last_changed=datetime.fromtimestamp(now[0] / 1000, _TZ))

    class ReportServices(_FakeServices):
        async def async_call(self, domain, service, data, blocking=True, context=None):
            await super().async_call(domain, service, data, blocking, context)
            entity = data["entity_id"]
            state = hass.states.get(entity)
            attributes = dict(state.attributes)
            if entity == "light.demo_main" and not full_parameters:
                attributes.update(brightness=255, color_temp_kelvin=6500)
            hass.states.set(entity, state.state, attributes, last_changed=datetime.fromtimestamp(now[0] / 1000, _TZ))

    hass.services = ReportServices(hass)
    runtime = _make_runtime(hass, executor=executor, payload=payload, now_ms=lambda: now[0])
    await runtime.start(hass, "entry")
    try:
        assert any(c.target_id == "light_mirror" and c.action is LightAction.TURN_ON for c in executor.calls)
        assert has_proven_auto_ownership(runtime._ownership.snapshots_for(_ROOM_ID, {"light_mirror"}), "light_mirror")
        now[0] = int(datetime(2026, 10, 7, 10, 0, tzinfo=_TZ).timestamp() * 1000)
        await runtime.async_process()
        assert any(c.target_id == "light_main" for c in executor.calls)
        now[0] += 1000
        await runtime.async_process()
        off = [c for c in executor.calls if c.target_id == "light_mirror" and c.action is LightAction.TURN_OFF]
        assert bool(off) is full_parameters
        if not full_parameters:
            assert not has_proven_auto_ownership(runtime._ownership.snapshots_for(_ROOM_ID, {"light_main"}), "light_main")
            attempts = len([c for c in executor.calls if c.target_id == "light_main"])
            now[0] += 1000
            await runtime.async_process()
            assert len([c for c in executor.calls if c.target_id == "light_main"]) == attempts
    finally:
        await runtime.stop()


class _StubLightPriority:
    """Minimal shared light-priority source for away-release tests."""

    def __init__(self) -> None:
        self.records: dict[str, dict[str, object]] = {}

    def manual_claim_entity_ids(self, entity_ids: frozenset[str]) -> frozenset[str]:
        return frozenset(entity_ids & self.records.keys())

    async def async_release_manual_claims_before(
        self, boundary_ms: int
    ) -> frozenset[str]:
        released = frozenset(
            entity_id
            for entity_id, record in self.records.items()
            if isinstance(record.get("confirmedAt"), int)
            and record["confirmedAt"] < boundary_ms
        )
        for entity_id in released:
            self.records.pop(entity_id, None)
        return released


async def test_away_home_transition_releases_manual_protections() -> None:
    """Away→home releases manual protections across all three sources.

    Owner decision 2026-10-09: manual protections release when the away mode
    clears, coordinated through the room journal, the shared authority and
    the shared light priority. Manual actions observed after the return keep
    their normal priority.
    """

    class _AuthorityStore(_MemoryOwnershipStore):
        recovered_previous = False

    payload = _config_payload(minimum_interval_seconds=600, stable_absence_seconds=30)
    clock = _Clock(_NOW_MS)
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "on", last_changed=_dt(_NOW_MS))
    hass.states.set("light.demo_main", "on", last_changed=_dt(_NOW_MS))
    hass.states.set(AWAY_ENTITY_ID, "on", last_changed=_dt(_NOW_MS))
    executor = _SpyExecutor()
    from custom_components.hausman_hub.application.manual_light_authority import (
        ManualLightAuthority,
    )
    authority = ManualLightAuthority(_AuthorityStore())
    await authority.async_load()
    priority = _StubLightPriority()
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=executor, now_ms=clock,
        payload=payload, ownership_store=_MemoryOwnershipStore(),
        manual_light_authority=authority, light_priority=priority,
    )
    await runtime.start(hass, "entry")
    try:
        # A person switches the light off before leaving: room + shared holds.
        runtime._service_event(_event("light", "turn_off", "light.demo_main"))
        hass.states.set(
            "light.demo_main", "off", last_changed=_dt(clock.value)
        )
        authority.capture(
            "light.demo_main", "off", "device_trigger", "common-off",
            observed_at_ms=_now_ms(),
        )
        await authority.async_drain()
        priority.records["light.demo_main"] = {"confirmedAt": clock.value}
        await asyncio.gather(*hass.tasks)
        assert (
            runtime._ownership.last_manual_off_at(_ROOM_ID, {"light_main"})  # type: ignore[attr-defined]
            is not None
        )
        assert authority.blocks("light.demo_main")

        # The household returns: the away source clears.
        runtime._away = True  # type: ignore[attr-defined]
        clock.value = _NOW_MS + 5_000
        hass.states.set(AWAY_ENTITY_ID, "off", last_changed=_dt(clock.value))
        runtime._release_away()
        await asyncio.gather(*hass.tasks)

        assert (
            runtime._ownership.last_manual_off_at(_ROOM_ID, {"light_main"})  # type: ignore[attr-defined]
            is None
        ), "away→home must release manual-off anchors"
        assert not authority.blocks(
            "light.demo_main"
        ), "away→home must release pre-return shared authority claims"
        assert "light.demo_main" not in priority.manual_claim_entity_ids(
            frozenset({"light.demo_main"})
        ), "away→home must release pre-return priority claims"

        # The returning person walks in: automation resumes immediately.
        clock.value = _NOW_MS + 10_000
        hass.states.set(
            "binary_sensor.demo_presence", "on", last_changed=_dt(clock.value)
        )
        await runtime.async_process()
        assert executor.calls, "auto must resume after the away release"
    finally:
        await runtime.stop()


async def test_lock_unlock_alone_never_releases_manual_off_holds() -> None:
    """A real unlock event releases nothing: only the away lifecycle does.

    Owner decision 2026-10-09. The standing at-home release (minimum interval
    plus stable absence) keeps working and must not be weakened here.
    """

    payload = _config_payload(minimum_interval_seconds=600, stable_absence_seconds=30)
    payload["devices"]["sensors"].append(  # type: ignore[index]
        {
            "id": "entry_door",
            "name": "Замок",
            "kind": "entry",
            "entityId": "lock.demo_door",
            "autoAdoptOverride": None,
        }
    )
    clock = _Clock(_NOW_MS)
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_dt(_NOW_MS))
    hass.states.set("light.demo_main", "on", last_changed=_dt(_NOW_MS))
    hass.states.set("lock.demo_door", "locked", last_changed=_dt(_NOW_MS))
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=executor, now_ms=clock, payload=payload
    )
    await runtime.start(hass, "entry")
    try:
        runtime._service_event(_event("light", "turn_off", "light.demo_main"))
        hass.states.set(
            "light.demo_main", "off", last_changed=_dt(clock.value)
        )
        await asyncio.gather(*hass.tasks)
        anchor = runtime._ownership.last_manual_off_at(  # type: ignore[attr-defined]
            _ROOM_ID, {"light_main"}
        )
        assert anchor is not None

        # Somebody unlocks the door and steps in while the timer still runs:
        # the unlock travels through the real state-event handler.
        clock.value = _NOW_MS + 60_000
        hass.states.set("lock.demo_door", "unlocked", last_changed=_dt(clock.value))
        runtime._state_event(
            SimpleNamespace(
                data={
                    "entity_id": "lock.demo_door",
                    "new_state": SimpleNamespace(state="unlocked"),
                    "old_state": SimpleNamespace(state="locked"),
                }
            )
        )
        hass.states.set(
            "binary_sensor.demo_presence", "on", last_changed=_dt(clock.value)
        )
        await asyncio.gather(*hass.tasks)
        await runtime.async_process()
        assert executor.calls == [], "an unlock must not release the protection"
        assert (
            runtime._ownership.last_manual_off_at(_ROOM_ID, {"light_main"})  # type: ignore[attr-defined]
            == anchor
        ), "the hold must survive an unlock"

        # The standing at-home release still works: timer + stable absence.
        clock.value = _NOW_MS + 660_000
        hass.states.set(
            "binary_sensor.demo_presence", "off", last_changed=_dt(clock.value)
        )
        await runtime.async_process()
        clock.value = _NOW_MS + 720_000
        hass.states.set(
            "binary_sensor.demo_presence", "on", last_changed=_dt(clock.value)
        )
        await runtime.async_process()
        assert executor.calls, "the standing at-home release must keep working"
    finally:
        await runtime.stop()


async def test_away_home_release_bounds_absence_windows() -> None:
    """Absence observed before the return must not count after it.

    SEC-RP2-02: clearing the tracker dict is not enough — the away interval
    is unobserved, so a confirmed-absence window may only start at the return
    moment, the same fresh start a restart gets.
    """

    payload = _config_payload(minimum_interval_seconds=600, stable_absence_seconds=30)
    clock = _Clock(_NOW_MS)
    hass = _FakeHass()
    hass.states.set(
        "binary_sensor.demo_presence", "off", last_changed=_dt(_NOW_MS - 600_000)
    )
    hass.states.set("light.demo_main", "off", last_changed=_dt(_NOW_MS))
    hass.states.set(AWAY_ENTITY_ID, "on", last_changed=_dt(_NOW_MS))
    runtime = _make_runtime(hass, now_ms=clock, payload=payload)
    await runtime.start(hass, "entry")
    try:
        runtime._away = True  # type: ignore[attr-defined]
        # Presence returns BEFORE the release; the old window is kept while
        # the person is present, and must not survive the away→home release.
        clock.value = _NOW_MS + 500
        hass.states.set(
            "binary_sensor.demo_presence", "on", last_changed=_dt(clock.value)
        )
        await runtime.async_process()
        pre_return = runtime._absence.get(_ROOM_ID)  # type: ignore[attr-defined]
        assert pre_return is not None and pre_return[0] is True, (
            "the test requires an accumulated absence window before the return"
        )
        assert pre_return[1] < _NOW_MS + 1_000
        clock.value = _NOW_MS + 1_000
        hass.states.set(AWAY_ENTITY_ID, "off", last_changed=_dt(clock.value))
        floor = clock.value
        runtime._release_away()
        await asyncio.gather(*hass.tasks)
        await runtime.async_process()
        absence = runtime._absence.get(_ROOM_ID)  # type: ignore[attr-defined]
        assert absence is None or absence[1] >= floor, (
            "pre-return absence must not count after return"
        )
        assert pre_return is None or pre_return[1] < floor
        assert not runtime._curve_presence  # type: ignore[attr-defined]
        assert not runtime._curve_motion  # type: ignore[attr-defined]
    finally:
        await runtime.stop()


async def test_manual_action_after_return_survives_away_release() -> None:
    """A fresh manual action wins the race against the away→home release.

    The release is bounded by the return gesture: holds and claims observed
    after it keep their normal priority and still block automation.
    """

    class _AuthorityStore(_MemoryOwnershipStore):
        recovered_previous = False

    payload = _config_payload(minimum_interval_seconds=600, stable_absence_seconds=30)
    clock = _Clock(_NOW_MS)
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_dt(_NOW_MS))
    hass.states.set("light.demo_main", "off", last_changed=_dt(_NOW_MS))
    hass.states.set(AWAY_ENTITY_ID, "on", last_changed=_dt(_NOW_MS))
    executor = _SpyExecutor()
    from custom_components.hausman_hub.application.manual_light_authority import (
        ManualLightAuthority,
    )
    authority = ManualLightAuthority(_AuthorityStore())
    await authority.async_load()
    priority = _StubLightPriority()
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=executor, now_ms=clock,
        payload=payload, manual_light_authority=authority, light_priority=priority,
    )
    await runtime.start(hass, "entry")
    try:
        # A pre-return claim exists before the household comes back.
        priority.records["light.demo_main"] = {"confirmedAt": _NOW_MS + 500}
        runtime._away = True  # type: ignore[attr-defined]
        clock.value = _NOW_MS + 1_000
        hass.states.set(AWAY_ENTITY_ID, "off", last_changed=_dt(clock.value))
        runtime._release_away()
        # A person acts right at the return millisecond, while the
        # reconciliation is still pending, and refreshes the same claim.
        runtime._service_event(_event("light", "turn_off", "light.demo_main"))
        hass.states.set(
            "light.demo_main", "off", last_changed=_dt(clock.value)
        )
        authority.capture(
            "light.demo_main", "off", "device_trigger", "fresh-off",
            observed_at_ms=_now_ms(),
        )
        await authority.async_drain()
        priority.records["light.demo_main"] = {"confirmedAt": clock.value}
        await asyncio.gather(*hass.tasks)

        assert (
            runtime._ownership.last_manual_off_at(_ROOM_ID, {"light_main"})  # type: ignore[attr-defined]
            is not None
        ), "a fresh manual hold must survive the away release"
        assert authority.blocks("light.demo_main")
        assert "light.demo_main" in priority.manual_claim_entity_ids(
            frozenset({"light.demo_main"})
        ), "a refreshed claim must survive the away release"
        clock.value = _NOW_MS + 3_000
        hass.states.set(
            "binary_sensor.demo_presence", "on", last_changed=_dt(clock.value)
        )
        await runtime.async_process()
        assert executor.calls == [], "a fresh manual hold still blocks automation"
    finally:
        await runtime.stop()


async def test_away_authority_release_paused_save_keeps_fresh_capture() -> None:
    """A capture arriving during the release save wins over the doomed record.

    The pre-return pending claim is selected for release; while its save is
    paused, a fresh manual action replaces the pending entry. After the save
    only the exact frozen record object may be dropped — the fresh capture
    keeps blocking automation.
    """

    class _GateStore:
        def __init__(self) -> None:
            self.payload: object | None = None
            self.gate = asyncio.Event()
            self.recovered_previous = False
            self.paused = False

        async def async_load(self) -> object | None:
            return self.payload

        async def async_save(self, payload: dict[str, object]) -> None:
            if self.paused:
                await self.gate.wait()
            self.payload = payload

    from custom_components.hausman_hub.application.manual_light_authority import (
        ManualLightAuthority,
    )
    authority = ManualLightAuthority(_GateStore())
    await authority.async_load()
    assert authority.capture(
        "light.demo_main", "off", "device_trigger", "pre-return",
        observed_at_ms=_now_ms(),
    )
    boundary = authority.current_generation()
    authority._store.paused = True  # type: ignore[attr-defined]
    task = asyncio.ensure_future(
        authority.async_release_manual_claims_through(boundary)
    )
    await asyncio.sleep(0)
    # A fresh manual action replaces the pending record mid-save.
    assert authority.capture(
        "light.demo_main", "off", "device_trigger", "fresh-after-return",
        observed_at_ms=_now_ms() + 1,
    )
    authority._store.gate.set()  # type: ignore[attr-defined]
    released = await task
    assert released == ()
    record = authority.record("light.demo_main")
    assert record is not None
    assert record.fingerprint == "fresh-after-return", (
        "the fresh capture must survive the release save window"
    )
    assert authority.blocks("light.demo_main")


async def test_away_priority_release_failed_save_keeps_claim_blocking() -> None:
    """A failed priority-store save must keep the manual claim blocking.

    SEC-RP2-01/b: the reduced state is persisted before the claims leave
    memory; a rejecting store releases nothing and the deny boundary stays.
    Scope note: this covers the in-memory deny boundary for the FIRST save
    of the reduced snapshot (no claim replacement happens in this test —
    see test_away_priority_release_heal_save_failure_propagates for the
    convergence-failure path). All async full-snapshot writers are
    serialized through `_authority_lock`; the one synchronous mutating
    path (`plan()`, which cannot take an asyncio lock) only removes a
    claim on proven fresh off and never creates one (see
    evidence/HH-014.b-2026-10-09-away-release-rp3-callers.txt). Claim
    durability across a restart on a failing store is the store-wide
    persistence posture, not specific to this release path.
    """

    from custom_components.hausman_hub.application.scenario_light_priority import (
        LightAutomationPriority,
    )

    class _RejectingStore:
        def __init__(self) -> None:
            self.payload: object | None = None
            self.saves = 0

        async def async_load(self) -> object | None:
            return self.payload

        async def async_save(self, payload: dict[str, object]) -> None:
            self.saves += 1
            raise RuntimeError("storage unavailable")

    store = _RejectingStore()
    store.payload = {
        "version": 1,
        "records": [{
            "entityId": "light.demo_main",
            "targetId": "light_main",
            "ownership": "manual",
            "evidenceRevision": None,
            "confirmedAt": _NOW_MS,
        }],
    }
    priority = LightAutomationPriority(store)
    await priority.async_load()
    assert "light.demo_main" in priority.manual_claim_entity_ids(
        frozenset({"light.demo_main"})
    )
    # The release propagates the storage failure: the runtime logs it and the
    # deny boundary stays untouched — the claim keeps blocking.
    with pytest.raises(RuntimeError):
        await priority.async_release_manual_claims_before(_NOW_MS + 1_000)
    assert store.saves == 1, "the reduced state must be attempted before giving up"
    assert "light.demo_main" in priority.manual_claim_entity_ids(
        frozenset({"light.demo_main"})
    ), "the manual claim must keep blocking after a failed save"


async def test_away_priority_release_paused_save_keeps_claims_blocking() -> None:
    """Claims stay visible and blocking while the reduced save is in flight.

    SEC-RP2-01/b: the reduced snapshot is persisted without mutating live
    memory, so the dispatch read sees the claim during the save; a claim
    replaced during the save is a fresh manual action and survives; a
    cancelled save leaves the deny boundary untouched.
    """

    from custom_components.hausman_hub.application.scenario_light_priority import (
        LightAutomationPriority,
    )

    class _GateStore:
        def __init__(self) -> None:
            self.payload: object | None = None
            self.gate = asyncio.Event()
            self.recovered_previous = False
            self.paused = False

        async def async_load(self) -> object | None:
            return self.payload

        async def async_save(self, payload: dict[str, object]) -> None:
            if self.paused:
                await self.gate.wait()
            self.payload = payload

    store = _GateStore()
    store.payload = {
        "version": 1,
        "records": [{
            "entityId": "light.demo_main",
            "targetId": "light_main",
            "ownership": "manual",
            "evidenceRevision": None,
            "confirmedAt": _NOW_MS,
        }],
    }
    priority = LightAutomationPriority(store)
    await priority.async_load()
    claim = priority._manual_records["light.demo_main"]  # type: ignore[attr-defined]
    store.paused = True
    task = asyncio.ensure_future(
        priority.async_release_manual_claims_before(_NOW_MS + 1_000)
    )
    await asyncio.sleep(0)
    # The dispatch read must still see the claim while the save is in flight.
    assert "light.demo_main" in priority.manual_claim_entity_ids(
        frozenset({"light.demo_main"})
    )
    # A fresh manual action replaces the same claim mid-save.
    priority._manual_records["light.demo_main"] = {  # type: ignore[attr-defined]
        "entityId": "light.demo_main",
        "targetId": "light_main",
        "ownership": "manual",
        "evidenceRevision": None,
        "confirmedAt": _NOW_MS + 500,
    }
    store.gate.set()
    released = await task
    assert released == frozenset(), "the replaced claim must not be released"
    fresh = priority._manual_records.get("light.demo_main")  # type: ignore[attr-defined]
    assert fresh is not None and fresh["confirmedAt"] == _NOW_MS + 500, (
        "the fresh claim must survive the release save window"
    )

    # A cancelled release must leave the deny boundary untouched.
    store.paused = True
    store.gate.clear()
    cancelled = asyncio.ensure_future(
        priority.async_release_manual_claims_before(_NOW_MS + 100_000)
    )
    await asyncio.sleep(0)
    cancelled.cancel()
    try:
        await cancelled
    except asyncio.CancelledError:
        pass
    assert "light.demo_main" in priority.manual_claim_entity_ids(
        frozenset({"light.demo_main"})
    ), "a cancelled release must keep the manual claim blocking"


async def test_short_absence_return_does_not_stretch_release() -> None:
    """Post-return time must not stretch a shorter absence (audit scenario).

    Absent 10 s, then present: the stable-absence requirement measures the
    actual absence to the return moment, so the release must NOT fire after
    20 more seconds in the room. After a later absence of 30 s or more the
    release fires and the light turns on.
    """

    ownership_store = _MemoryOwnershipStore()
    ownership_store.payload = {
        "version": 1,
        "records": [{
            "roomId": _ROOM_ID,
            "targetId": "light_main",
            "source": "manual",
            "confirmed": True,
            "at": _NOW_MS - 7_200_000,
        }],
        "manualOff": [],
        "morning": [],
    }
    payload = _config_payload(minimum_interval_seconds=600, stable_absence_seconds=30)
    clock = _Clock(_NOW_MS)
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_dt(_NOW_MS))
    hass.states.set("light.demo_main", "off", last_changed=_dt(_NOW_MS))
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=executor, now_ms=clock,
        payload=payload, ownership_store=ownership_store,
    )
    await runtime.start(hass, "entry")
    try:
        # Confirm an ongoing absence first.
        await runtime.async_process()

        # Absent only 10 s, then present again.
        clock.value = _NOW_MS + 10_000
        hass.states.set(
            "binary_sensor.demo_presence", "on", last_changed=_dt(clock.value)
        )
        await runtime.async_process()
        # 20 more seconds in the room: the frozen window is only 10 s long.
        clock.value = _NOW_MS + 30_000
        await runtime.async_process()
        assert executor.calls == [], (
            "a 10 s absence must not be stretched into a release"
        )

        # Later: absent 40 s (fresh off, stable), then present — release.
        clock.value = _NOW_MS + 70_000
        hass.states.set(
            "binary_sensor.demo_presence", "off", last_changed=_dt(clock.value)
        )
        await runtime.async_process()
        clock.value = _NOW_MS + 110_000
        await runtime.async_process()
        clock.value = _NOW_MS + 115_000
        hass.states.set(
            "binary_sensor.demo_presence", "on", last_changed=_dt(clock.value)
        )
        await runtime.async_process()
        assert executor.calls, (
            "a stable absence of 30 s or more must release on the return"
        )
    finally:
        await runtime.stop()


async def test_away_priority_release_serialized_with_direct_action() -> None:
    """The away release and a fresh direct action never overwrite each other.

    Both writers serialize on the same lock: whichever lands second persists
    a snapshot that contains the fresher state, so a survived claim is
    durable in the store as well as in memory.
    """

    from custom_components.hausman_hub.application.scenario_light_priority import (
        LightAutomationPriority,
    )

    class _GateStore:
        def __init__(self) -> None:
            self.payload: object | None = None
            self.gate = asyncio.Event()
            self.recovered_previous = False
            self.paused = False

        async def async_load(self) -> object | None:
            return self.payload

        async def async_save(self, payload: dict[str, object]) -> None:
            if self.paused:
                await self.gate.wait()
            self.payload = payload

    class _Allowed:
        domain = "light"
        service = "turn_on"

    class _Device:
        entity_id = "light.demo_main"

        def action(self, action_id: str) -> _Allowed:
            return _Allowed()

    class _Catalog:
        def device(self, target_id: str) -> _Device:
            return _Device()

    store = _GateStore()
    store.payload = {
        "version": 1,
        "records": [{
            "entityId": "light.demo_main",
            "targetId": "light_main",
            "ownership": "manual",
            "evidenceRevision": None,
            "confirmedAt": _NOW_MS,
        }],
    }
    priority = LightAutomationPriority(store)
    await priority.async_load()
    store.paused = True
    direct = asyncio.ensure_future(
        priority.note_direct_action(
            "light_main",
            "turn_on",
            {"status": "completed"},
            _Catalog(),
            SimpleNamespace(states=SimpleNamespace(get=lambda entity_id: None)),
        )
    )
    await asyncio.sleep(0)
    release = asyncio.ensure_future(
        priority.async_release_manual_claims_before(_NOW_MS + 1_000)
    )
    await asyncio.sleep(0)
    # Both writers are serialized: the direct action holds the lock first,
    # the release waits behind it.
    store.paused = False
    store.gate.set()
    await asyncio.gather(direct, release)
    claim = priority._manual_records.get("light.demo_main")  # type: ignore[attr-defined]
    assert claim is not None and claim["ownership"] == "manual", (
        "the fresh direct-action claim must survive the away release"
    )
    saved_ids = {
        record.get("entityId")
        for record in (store.payload or {}).get("records", [])  # type: ignore[union-attr]
    }
    assert "light.demo_main" in saved_ids, (
        "the store must keep the survived claim durable"
    )
    reloaded = LightAutomationPriority(store)
    await reloaded.async_load()
    assert "light.demo_main" in reloaded.manual_claim_entity_ids(
        frozenset({"light.demo_main"})
    ), "a reload must restore the claim so it keeps blocking"


async def test_away_priority_release_heal_save_failure_propagates() -> None:
    """A failing convergence save keeps the claim and reports the failure.

    If an unsynchronized writer changed live state during the release save,
    the release converges the store to the live state. When that convergence
    save fails, the exception surfaces (the runtime logs it) and the
    artificially replaced claim keeps blocking in memory. The replaced claim
    is a direct stand-in for an unsynchronized writer, not the plan() path
    (plan() only removes claims on proven fresh off).
    """

    from custom_components.hausman_hub.application.scenario_light_priority import (
        LightAutomationPriority,
    )

    class _Store:
        def __init__(self) -> None:
            self.payload: object | None = None
            self.saves = 0
            self.fail_after = 1
            self.gate = asyncio.Event()
            self.entered = asyncio.Event()
            self.recovered_previous = False
            self.paused = False

        async def async_load(self) -> object | None:
            return self.payload

        async def async_save(self, payload: dict[str, object]) -> None:
            if self.paused:
                self.entered.set()
                await self.gate.wait()
            self.saves += 1
            if self.saves > self.fail_after:
                raise RuntimeError("convergence save failed")
            self.payload = payload

    store = _Store()
    store.payload = {
        "version": 1,
        "records": [{
            "entityId": "light.demo_main",
            "targetId": "light_main",
            "ownership": "manual",
            "evidenceRevision": None,
            "confirmedAt": _NOW_MS,
        }],
    }
    priority = LightAutomationPriority(store)
    await priority.async_load()
    store.paused = True
    release = asyncio.ensure_future(
        priority.async_release_manual_claims_before(_NOW_MS + 1_000)
    )
    # Deterministic: wait until the release save is inside the paused store.
    await store.entered.wait()
    # An unsynchronized writer replaces the claim while the release save is
    # paused: the fresh claim survives the identity check and the release
    # must converge the store to the live state — and report a convergence
    # save failure instead of silently dropping it.
    priority._manual_records["light.demo_main"] = {  # type: ignore[attr-defined]
        "entityId": "light.demo_main",
        "targetId": "light_main",
        "ownership": "manual",
        "evidenceRevision": None,
        "confirmedAt": _NOW_MS + 1_000,
    }
    store.paused = False
    store.gate.set()
    with pytest.raises(RuntimeError):
        await release
    claim = priority._manual_records.get("light.demo_main")  # type: ignore[attr-defined]
    assert claim is not None and claim["confirmedAt"] == _NOW_MS + 1_000, (
        "the surviving claim must stay in memory after a failed convergence"
    )
    assert "light.demo_main" in priority.manual_claim_entity_ids(
        frozenset({"light.demo_main"})
    )


async def test_restored_manual_record_releases_via_real_absence() -> None:
    """A restored manual record must release via the room's real absence.

    Live tambur failure 2026-10-09: after a restart the journal holds a
    manual record while the manual-off anchor is absent; the ownership
    release check was fed a default snapshot with absence_confirmed=False,
    so the record never expired and the room stayed dark on entry.
    """

    ownership_store = _MemoryOwnershipStore()
    ownership_store.payload = {
        "version": 1,
        "records": [{
            "roomId": _ROOM_ID,
            "targetId": "light_main",
            "source": "manual",
            "confirmed": True,
            "at": _NOW_MS - 7_200_000,
        }],
        "manualOff": [],
        "morning": [],
    }
    payload = _config_payload(minimum_interval_seconds=600, stable_absence_seconds=30)
    clock = _Clock(_NOW_MS)
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_dt(_NOW_MS))
    hass.states.set("light.demo_main", "off", last_changed=_dt(_NOW_MS))
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=executor, now_ms=clock,
        payload=payload, ownership_store=ownership_store,
    )
    await runtime.start(hass, "entry")
    try:
        # Absence confirms while the room is empty.
        clock.value = _NOW_MS + 60_000
        hass.states.set(
            "binary_sensor.demo_presence", "off", last_changed=_dt(clock.value)
        )
        await runtime.async_process()
        absence = runtime._absence.get(_ROOM_ID)  # type: ignore[attr-defined]
        assert absence is not None and absence[0] is True

        # The owner walks in: the restored manual record must release via the
        # real absence evidence and the light must turn on.
        clock.value = _NOW_MS + 120_000
        hass.states.set(
            "binary_sensor.demo_presence", "on", last_changed=_dt(clock.value)
        )
        await runtime.async_process()
        assert executor.calls, (
            "a restored manual record must release via the real absence "
            "evidence even without a manual-off anchor"
        )
    finally:
        await runtime.stop()


async def test_restored_manual_record_releases_via_real_absence_day_curve() -> None:
    """The same release on the day-curve profile (the tambur's own profile).

    The tambur runs `day_curve`; the release must work there too, including
    the curve presence confirmation on the tick after the walk-in.
    """

    ownership_store = _MemoryOwnershipStore()
    ownership_store.payload = {
        "version": 1,
        "records": [{
            "roomId": _ROOM_ID,
            "targetId": "light_main",
            "source": "manual",
            "confirmed": True,
            "at": _NOW_MS - 7_200_000,
        }],
        "manualOff": [],
        "morning": [],
    }
    payload = _config_payload(minimum_interval_seconds=600, stable_absence_seconds=30)
    payload["profile"] = "day_curve"
    clock = _Clock(_NOW_MS)
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_dt(_NOW_MS))
    hass.states.set("light.demo_main", "off", last_changed=_dt(_NOW_MS))
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=executor, now_ms=clock,
        payload=payload, ownership_store=ownership_store,
    )
    await runtime.start(hass, "entry")
    try:
        clock.value = _NOW_MS + 60_000
        hass.states.set(
            "binary_sensor.demo_presence", "off", last_changed=_dt(clock.value)
        )
        await runtime.async_process()
        absence = runtime._absence.get(_ROOM_ID)  # type: ignore[attr-defined]
        assert absence is not None and absence[0] is True

        clock.value = _NOW_MS + 120_000
        hass.states.set(
            "binary_sensor.demo_presence", "on", last_changed=_dt(clock.value)
        )
        await runtime.async_process()
        # Presence confirmation completes on the next tick: the sensor keeps
        # holding ON (last_changed stays at the T+120 transition).
        clock.value = _NOW_MS + 180_000
        await runtime.async_process()
        assert executor.calls, (
            "the day-curve profile must resume after the away-release fix"
        )
    finally:
        await runtime.stop()


async def test_restored_manual_record_holds_without_confirmed_absence() -> None:
    """Without confirmed absence the restored record keeps the room held.

    The release must fire only on real evidence: a room whose presence
    sensor has never been proven off stays held after a restart.
    """

    ownership_store = _MemoryOwnershipStore()
    ownership_store.payload = {
        "version": 1,
        "records": [{
            "roomId": _ROOM_ID,
            "targetId": "light_main",
            "source": "manual",
            "confirmed": True,
            "at": _NOW_MS - 7_200_000,
        }],
        "manualOff": [],
        "morning": [],
    }
    payload = _config_payload(minimum_interval_seconds=600, stable_absence_seconds=30)
    clock = _Clock(_NOW_MS)
    hass = _FakeHass()
    # The presence sensor was left ON by the previous run: absence is NOT
    # proven after the restart until a fresh off is observed.
    hass.states.set("binary_sensor.demo_presence", "on", last_changed=_dt(_NOW_MS))
    hass.states.set("light.demo_main", "off", last_changed=_dt(_NOW_MS))
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=executor, now_ms=clock,
        payload=payload, ownership_store=ownership_store,
    )
    await runtime.start(hass, "entry")
    try:
        clock.value = _NOW_MS + 120_000
        hass.states.set(
            "binary_sensor.demo_presence", "on", last_changed=_dt(clock.value)
        )
        await runtime.async_process()
        assert executor.calls == [], (
            "without confirmed absence the restored record must keep holding"
        )
    finally:
        await runtime.stop()


async def test_restored_manual_record_holds_on_stale_presence_sensor() -> None:
    """A stale presence sensor must not confirm the release either.

    Unknown, unavailable or stale readings break the absence evidence; the
    restored manual record keeps holding instead of silently releasing.
    """

    ownership_store = _MemoryOwnershipStore()
    ownership_store.payload = {
        "version": 1,
        "records": [{
            "roomId": _ROOM_ID,
            "targetId": "light_main",
            "source": "manual",
            "confirmed": True,
            "at": _NOW_MS - 7_200_000,
        }],
        "manualOff": [],
        "morning": [],
    }
    payload = _config_payload(minimum_interval_seconds=600, stable_absence_seconds=30)
    clock = _Clock(_NOW_MS)
    hass = _FakeHass()
    hass.states.set(
        "binary_sensor.demo_presence",
        "unavailable",
        last_changed=_dt(_NOW_MS - 600_000),
    )
    hass.states.set("light.demo_main", "off", last_changed=_dt(_NOW_MS))
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=executor, now_ms=clock,
        payload=payload, ownership_store=ownership_store,
    )
    await runtime.start(hass, "entry")
    try:
        clock.value = _NOW_MS + 120_000
        hass.states.set(
            "binary_sensor.demo_presence",
            "unavailable",
            last_changed=_dt(_NOW_MS - 600_000),
        )
        await runtime.async_process()
        absence = runtime._absence.get(_ROOM_ID)  # type: ignore[attr-defined]
        assert absence is None or absence[0] is not True, (
            "an unavailable sensor must not confirm absence"
        )
        assert executor.calls == []
    finally:
        await runtime.stop()


async def test_delayed_processing_freezes_end_at_sensor_return() -> None:
    """The frozen absence end is the sensor's return time, not processing time.

    Audit scenario: the absence started at T; the sensor flipped ON at T+10 s;
    the room evaluation ran late, at T+30 s. The stored window end must be
    T+10 s (the proven return), so the 10 s absence stays shorter than the
    30 s stable requirement and the room keeps holding.
    """

    ownership_store = _MemoryOwnershipStore()
    ownership_store.payload = {
        "version": 1,
        "records": [{
            "roomId": _ROOM_ID,
            "targetId": "light_main",
            "source": "manual",
            "confirmed": True,
            "at": _NOW_MS - 7_200_000,
        }],
        "manualOff": [],
        "morning": [],
    }
    payload = _config_payload(minimum_interval_seconds=600, stable_absence_seconds=30)
    clock = _Clock(_NOW_MS)
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_dt(_NOW_MS))
    hass.states.set("light.demo_main", "off", last_changed=_dt(_NOW_MS))
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=executor, now_ms=clock,
        payload=payload, ownership_store=ownership_store,
    )
    await runtime.start(hass, "entry")
    try:
        await runtime.async_process()
        # The sensor flipped ON at T+10 s; the room was evaluated only at T+30 s.
        sensor_return = _NOW_MS + 10_000
        hass.states.set(
            "binary_sensor.demo_presence", "on", last_changed=_dt(sensor_return)
        )
        clock.value = _NOW_MS + 30_000
        await runtime.async_process()
        absence = runtime._absence.get(_ROOM_ID)  # type: ignore[attr-defined]
        assert absence is not None and absence[0] is True
        assert absence[2] == sensor_return, (
            f"the frozen end must be the sensor return time, got {absence[2]}"
        )
        assert executor.calls == [], (
            "a 10 s absence must not be stretched into a release"
        )
    finally:
        await runtime.stop()


async def test_new_manual_off_after_completed_window_starts_fresh() -> None:
    """A manual off after a completed absence window starts a fresh window.

    Completed window → the release fired → the person switches the light off
    again: the new hold must anchor to the new manual action, not inherit the
    released window.
    """

    payload = _config_payload(minimum_interval_seconds=600, stable_absence_seconds=30)
    clock = _Clock(_NOW_MS)
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "on", last_changed=_dt(_NOW_MS))
    hass.states.set("light.demo_main", "on", last_changed=_dt(_NOW_MS))
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=executor, now_ms=clock, payload=payload
    )
    await runtime.start(hass, "entry")
    try:
        runtime._service_event(_event("light", "turn_off", "light.demo_main"))
        hass.states.set("light.demo_main", "off", last_changed=_dt(clock.value))
        clock.value = _NOW_MS + 660_000
        hass.states.set(
            "binary_sensor.demo_presence", "off", last_changed=_dt(clock.value)
        )
        await runtime.async_process()
        clock.value = _NOW_MS + 720_000
        hass.states.set(
            "binary_sensor.demo_presence", "on", last_changed=_dt(clock.value)
        )
        await runtime.async_process()
        assert executor.calls, "the completed window must release"

        # The person switches the light off again: a fresh hold anchors now.
        executor.calls.clear()
        runtime._service_event(_event("light", "turn_off", "light.demo_main"))
        hass.states.set(
            "light.demo_main", "off", last_changed=_dt(clock.value)
        )
        clock.value = _NOW_MS + 730_000
        hass.states.set(
            "binary_sensor.demo_presence", "off", last_changed=_dt(clock.value)
        )
        await runtime.async_process()
        anchor = runtime._ownership.last_manual_off_at(  # type: ignore[attr-defined]
            _ROOM_ID, {"light_main"}
        )
        assert anchor == _NOW_MS + 720_000, "the fresh hold must anchor to the new action"
        clock.value = _NOW_MS + 780_000
        hass.states.set(
            "binary_sensor.demo_presence", "on", last_changed=_dt(clock.value)
        )
        await runtime.async_process()
        assert executor.calls == [], (
            "the fresh hold must keep blocking before its own timer elapses"
        )
    finally:
        await runtime.stop()


def _restored_record_store() -> _MemoryOwnershipStore:
    store = _MemoryOwnershipStore()
    store.payload = {
        "version": 1,
        "records": [{
            "roomId": _ROOM_ID,
            "targetId": "light_main",
            "source": "manual",
            "confirmed": True,
            "at": _NOW_MS - 7_200_000,
        }],
        "manualOff": [],
        "morning": [],
    }
    return store


async def test_absence_latch_survives_short_reentries() -> None:
    """Short re-entries must not erase a proven stable absence (the tambur).

    Live scenario 2026-10-09: a restored manual record holds the room; the
    person moves in short in/out cycles (2-20 s windows), so the LATEST
    absence window never reaches the stable requirement — the earlier
    >= 30 s empty window must latch the release eligibility instead of
    being erased.
    """

    payload = _config_payload(minimum_interval_seconds=600, stable_absence_seconds=30)
    clock = _Clock(_NOW_MS)
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_dt(_NOW_MS))
    hass.states.set("light.demo_main", "off", last_changed=_dt(_NOW_MS))
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=executor, now_ms=clock,
        payload=payload, ownership_store=_restored_record_store(),
    )
    await runtime.start(hass, "entry")
    try:
        await runtime.async_process()
        assert runtime._absence_release_latch == set()  # type: ignore[attr-defined]

        # Short in/out cycles: windows of 2-20 s, never stable.
        moment = _NOW_MS + 1_000
        for cycle in range(3):
            moment += 20_000
            hass.states.set(
                "binary_sensor.demo_presence", "on", last_changed=_dt(moment)
            )
            clock.value = moment
            await runtime.async_process()
            moment += 5_000
            hass.states.set(
                "binary_sensor.demo_presence", "off", last_changed=_dt(moment)
            )
            clock.value = moment
            await runtime.async_process()
        assert executor.calls == [], (
            "short cycles before a stable window must keep the room held"
        )

        # One stable empty window (35 s) latches the release eligibility.
        moment += 35_000
        clock.value = moment
        await runtime.async_process()
        assert _ROOM_ID in runtime._absence_release_latch  # type: ignore[attr-defined]

        # The person walks in: the automation resumes and turns the light on.
        moment += 2_000
        hass.states.set(
            "binary_sensor.demo_presence", "on", last_changed=_dt(moment)
        )
        clock.value = moment
        await runtime.async_process()
        assert executor.calls, (
            "the latched release must resume automation on the return"
        )
    finally:
        await runtime.stop()


async def test_new_manual_action_clears_absence_latch() -> None:
    """A fresh manual action clears the latch and restarts the cycle."""

    payload = _config_payload(minimum_interval_seconds=600, stable_absence_seconds=30)
    clock = _Clock(_NOW_MS)
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_dt(_NOW_MS))
    hass.states.set("light.demo_main", "off", last_changed=_dt(_NOW_MS))
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=executor, now_ms=clock, payload=payload
    )
    await runtime.start(hass, "entry")
    try:
        # The empty-room window matures (30 s) and latches the eligibility:
        # the sensor stays off from the seed, only the clock advances.
        clock.value = _NOW_MS + 35_000
        await runtime.async_process()
        assert _ROOM_ID in runtime._absence_release_latch  # type: ignore[attr-defined]

        # A fresh manual action clears the latch: the hold restarts.
        runtime._service_event(_event("light", "turn_off", "light.demo_main"))
        await asyncio.gather(*hass.tasks)
        assert _ROOM_ID not in runtime._absence_release_latch  # type: ignore[attr-defined]

        # The person walks in right away: the fresh hold keeps blocking until
        # its own timer and a new stable absence.
        clock.value = _NOW_MS + 5_000
        hass.states.set(
            "binary_sensor.demo_presence", "on", last_changed=_dt(clock.value)
        )
        await runtime.async_process()
        assert executor.calls == []
    finally:
        await runtime.stop()


async def test_absence_latch_survives_unavailable_sensor() -> None:
    """A sensor dropout must not erase a proven stable absence.

    The latch is a historical fact (a completed >= stable empty window since
    the manual action); sensor faults clear the ongoing WINDOW but not the
    latch. The dropout also means no fresh evidence: no command fires while
    the sensor is unavailable.
    """

    payload = _config_payload(minimum_interval_seconds=600, stable_absence_seconds=30)
    clock = _Clock(_NOW_MS)
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_dt(_NOW_MS))
    hass.states.set("light.demo_main", "off", last_changed=_dt(_NOW_MS))
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=executor, now_ms=clock, payload=payload
    )
    await runtime.start(hass, "entry")
    try:
        # The empty-room window matures (30 s) and latches the eligibility.
        clock.value = _NOW_MS + 35_000
        await runtime.async_process()
        assert _ROOM_ID in runtime._absence_release_latch  # type: ignore[attr-defined]

        # A sensor dropout clears the ongoing WINDOW but not the latch.
        hass.states.set(
            "binary_sensor.demo_presence",
            "unavailable",
            last_changed=_dt(_NOW_MS + 36_000),
        )
        clock.value = _NOW_MS + 37_000
        await runtime.async_process()
        assert runtime._absence.get(_ROOM_ID) is None  # type: ignore[attr-defined]
        assert _ROOM_ID in runtime._absence_release_latch  # type: ignore[attr-defined]
        assert executor.calls == [], (
            "no commands on an unavailable sensor"
        )
    finally:
        await runtime.stop()


async def test_manual_off_protection_respects_absence_latch() -> None:
    """With an active anchor, the latch satisfies the absence condition.

    Live flow: manual off → a stable empty window latches the eligibility →
    short re-entries afterwards must not re-block: the release holds and the
    automation resumes on presence. The minimum interval stays mandatory.
    """

    payload = _config_payload(minimum_interval_seconds=600, stable_absence_seconds=30)
    clock = _Clock(_NOW_MS)
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_dt(_NOW_MS))
    hass.states.set("light.demo_main", "off", last_changed=_dt(_NOW_MS))
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=executor, now_ms=clock, payload=payload
    )
    await runtime.start(hass, "entry")
    try:
        # Manual off: the anchor arms; the empty window matures and latches.
        runtime._service_event(_event("light", "turn_off", "light.demo_main"))
        await asyncio.gather(*hass.tasks)
        clock.value = _NOW_MS + 35_000
        await runtime.async_process()
        assert _ROOM_ID in runtime._absence_release_latch  # type: ignore[attr-defined]

        # The minimum interval elapses while the room stays empty.
        clock.value = _NOW_MS + 610_000
        await runtime.async_process()

        # Short re-entries AFTER the latch: the release holds.
        clock.value = _NOW_MS + 615_000
        hass.states.set(
            "binary_sensor.demo_presence", "on", last_changed=_dt(clock.value)
        )
        await runtime.async_process()
        assert executor.calls, "the latched release must resume on presence"
        executor.calls.clear()

        clock.value = _NOW_MS + 620_000
        hass.states.set(
            "binary_sensor.demo_presence", "off", last_changed=_dt(clock.value)
        )
        clock.value = _NOW_MS + 625_000
        hass.states.set(
            "binary_sensor.demo_presence", "on", last_changed=_dt(clock.value)
        )
        await runtime.async_process()
        assert executor.calls, "the latched release must keep resuming"
    finally:
        await runtime.stop()


async def test_manual_off_protection_respects_absence_latch_day_curve() -> None:
    """The same latch release on the day-curve profile (the tambur's own)."""

    payload = _config_payload(minimum_interval_seconds=600, stable_absence_seconds=30)
    payload["profile"] = "day_curve"
    clock = _Clock(_NOW_MS)
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_dt(_NOW_MS))
    hass.states.set("light.demo_main", "off", last_changed=_dt(_NOW_MS))
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=executor, now_ms=clock, payload=payload
    )
    await runtime.start(hass, "entry")
    try:
        runtime._service_event(_event("light", "turn_off", "light.demo_main"))
        await asyncio.gather(*hass.tasks)
        clock.value = _NOW_MS + 35_000
        await runtime.async_process()
        assert _ROOM_ID in runtime._absence_release_latch  # type: ignore[attr-defined]
        clock.value = _NOW_MS + 610_000
        await runtime.async_process()
        clock.value = _NOW_MS + 615_000
        hass.states.set(
            "binary_sensor.demo_presence", "on", last_changed=_dt(clock.value)
        )
        await runtime.async_process()
        # The curve confirms presence on the next tick after the walk-in.
        clock.value = _NOW_MS + 645_000
        hass.states.set(
            "binary_sensor.demo_presence", "on", last_changed=_dt(clock.value)
        )
        await runtime.async_process()
        assert executor.calls, (
            "the latched release must resume on the day-curve profile"
        )
    finally:
        await runtime.stop()


async def test_power_manual_floor_blocks_latch_from_stale_sensor_off() -> None:
    """The room POWER manual path gets the same action floor as the light paths.

    Audit scenario (SEC-RP6-01), differentiating: the presence sensor stays
    OFF continuously from T+5 s; the room POWER is switched off manually at
    T+10 s — the floor restarts the absence evidence at the action. At T+35
    s (30 s since the sensor off, but only 25 s since the action) the latch
    must NOT set without the floor, and must NOT set with it either. At
    T+45 s (35 s after the action) the honest window latches. The power is
    restored and the person returns at T+50 s: the release fires and the
    light turns on.
    """

    payload = _config_payload(minimum_interval_seconds=600, stable_absence_seconds=30)
    payload["devices"]["power_switch"] = {
        "id": "power",
        "name": "Питание",
        "entityId": "switch.demo_power",
        "autoAdoptOverride": None,
    }
    clock = _Clock(_NOW_MS)
    hass = _FakeHass()
    # The presence sensor starts unknown at T0 and is proven off at T+5 s
    # (the evidence predates the action, but the sequence is monotonic).
    hass.states.set("binary_sensor.demo_presence", "unknown", last_changed=_dt(_NOW_MS))
    hass.states.set("light.demo_main", "off", last_changed=_dt(_NOW_MS))
    hass.states.set("switch.demo_power", "on", last_changed=_dt(_NOW_MS))
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=executor, now_ms=clock, payload=payload
    )
    await runtime.start(hass, "entry")
    try:
        # The presence sensor is proven off at T+5 s (fresh off evidence,
        # before the action).
        clock.value = _NOW_MS + 5_000
        hass.states.set(
            "binary_sensor.demo_presence", "off", last_changed=_dt(clock.value)
        )
        await runtime.async_process()

        # The room power is switched off manually at T+10 s: the floor lands.
        clock.value = _NOW_MS + 10_000
        hass.states.set("switch.demo_power", "off", last_changed=_dt(clock.value))
        runtime._observe_power_transition("switch.demo_power")
        await asyncio.gather(*hass.tasks)
        assert runtime._manual_absence_floor.get(_ROOM_ID) == _NOW_MS + 10_000  # type: ignore[attr-defined]

        # T+35 s: 30 s since the sensor off (T+5), but only 25 s since the
        # action (T+10). The floor makes the difference — the window starts
        # at the ACTION, so the latch must not set yet.
        clock.value = _NOW_MS + 35_000
        await runtime.async_process()
        assert _ROOM_ID not in runtime._absence_release_latch  # type: ignore[attr-defined]
        assert runtime._absence.get(_ROOM_ID) == (True, _NOW_MS + 10_000, None)  # type: ignore[attr-defined]

        # T+45 s: 35 s after the action — the honest window (action → now)
        # completes and latches.
        clock.value = _NOW_MS + 45_000
        await runtime.async_process()
        assert _ROOM_ID in runtime._absence_release_latch  # type: ignore[attr-defined]

        # At T+50 s (only 40 s after the action) the minimum-interval timer
        # still blocks — correctly: the fresh manual action's own 10-minute
        # protection is not bypassed by the latch.
        hass.states.set("switch.demo_power", "on", last_changed=_dt(clock.value))
        clock.value = _NOW_MS + 50_000
        hass.states.set(
            "binary_sensor.demo_presence", "on", last_changed=_dt(clock.value)
        )
        await runtime.async_process()
        assert executor.calls == [], (
            "the latch must not bypass the minimum-interval timer"
        )

        # The power is restored and the person returns after the minimum
        # interval: the release fires and the light turns on.
        clock.value = _NOW_MS + 615_000
        hass.states.set(
            "binary_sensor.demo_presence", "on", last_changed=_dt(clock.value)
        )
        await runtime.async_process()
        assert executor.calls, (
            "the honest post-action absence must release on the return"
        )
    finally:
        await runtime.stop()


async def test_manual_floor_blocks_latch_from_stale_sensor_off() -> None:
    """The manual-action floor blocks a premature latch after the action.

    The sensor goes off 5 s after the start; the manual action lands 10 s
    after the start (the absence floor restarts the evidence at the action).
    Without the floor the latch would set 30 s after the sensor-off — only
    25 s after the action. With the floor it sets no earlier than 30 s after
    the action itself.
    """

    payload = _config_payload(minimum_interval_seconds=600, stable_absence_seconds=30)
    clock = _Clock(_NOW_MS)
    hass = _FakeHass()
    # The presence sensor starts unknown at T0 and is proven off at T+5 s.
    hass.states.set("binary_sensor.demo_presence", "unknown", last_changed=_dt(_NOW_MS))
    hass.states.set("light.demo_main", "off", last_changed=_dt(_NOW_MS))
    hass.states.set("switch.demo_power", "on", last_changed=_dt(_NOW_MS))
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=executor, now_ms=clock, payload=payload
    )
    await runtime.start(hass, "entry")
    try:
        # The sensor goes off 5 s after the start (fresh off evidence).
        clock.value = _NOW_MS + 5_000
        hass.states.set(
            "binary_sensor.demo_presence", "off", last_changed=_dt(clock.value)
        )
        await runtime.async_process()
        assert _ROOM_ID not in runtime._absence_release_latch  # type: ignore[attr-defined]

        # The manual action at T+10 s restarts the absence evidence.
        clock.value = _NOW_MS + 10_000
        runtime._service_event(_event("light", "turn_off", "light.demo_main"))
        await asyncio.gather(*hass.tasks)
        assert _ROOM_ID not in runtime._absence_release_latch  # type: ignore[attr-defined]

        # 25 s after the action (30 s after the sensor-off): no latch — the
        # floor requires the stable window to be 30 s after the ACTION.
        clock.value = _NOW_MS + 35_000
        await runtime.async_process()
        assert _ROOM_ID not in runtime._absence_release_latch  # type: ignore[attr-defined]

        # 35 s after the action: the genuine window latches.
        clock.value = _NOW_MS + 45_000
        await runtime.async_process()
        assert _ROOM_ID in runtime._absence_release_latch  # type: ignore[attr-defined]
    finally:
        await runtime.stop()


async def test_sensor_failure_during_absence_self_heals() -> None:
    """A sensor failure during the empty period: the latch survives (a
    historical fact), the ongoing window is dropped, and the person's
    presence is invisible (no reading — no auto-on, fail-closed). When the
    sensor recovers with a valid fresh ON reading, the latch plus the valid
    presence resume the automation.
    """

    ownership_store = _MemoryOwnershipStore()
    ownership_store.payload = {
        "version": 1,
        "records": [{
            "roomId": _ROOM_ID,
            "targetId": "light_main",
            "source": "manual",
            "confirmed": True,
            "at": _NOW_MS - 7_200_000,
        }],
        "manualOff": [],
        "morning": [],
    }
    payload = _config_payload(minimum_interval_seconds=600, stable_absence_seconds=30)
    clock = _Clock(_NOW_MS)
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_dt(_NOW_MS))
    hass.states.set("light.demo_main", "off", last_changed=_dt(_NOW_MS))
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=executor, now_ms=clock,
        payload=payload, ownership_store=ownership_store,
    )
    await runtime.start(hass, "entry")
    try:
        # The room is empty; the window matures and latches.
        clock.value = _NOW_MS + 35_000
        await runtime.async_process()
        assert _ROOM_ID in runtime._absence_release_latch  # type: ignore[attr-defined]

        # The sensor fails (unavailable): the latch survives, the person's
        # presence is invisible.
        hass.states.set(
            "binary_sensor.demo_presence",
            "unavailable",
            last_changed=_dt(_NOW_MS + 36_000),
        )
        clock.value = _NOW_MS + 37_000
        await runtime.async_process()
        clock.value = _NOW_MS + 40_000
        await runtime.async_process()
        assert _ROOM_ID in runtime._absence_release_latch  # type: ignore[attr-defined]
        assert executor.calls == [], (
            "no auto-on while the presence reading is unavailable"
        )

        # The sensor recovers with a valid fresh ON reading: the latch
        # (proven earlier) + the valid presence → the release.
        hass.states.set(
            "binary_sensor.demo_presence", "on", last_changed=_dt(_NOW_MS + 50_000)
        )
        clock.value = _NOW_MS + 50_000
        await runtime.async_process()
        clock.value = _NOW_MS + 65_000
        await runtime.async_process()
        assert executor.calls, (
            "the recovery with valid presence resumes the automation"
        )
    finally:
        await runtime.stop()




async def test_day_curve_no_anchor_unknown_presence_stays_dark() -> None:
    """Day curve, restored manual record, NO anchor: UNKNOWN presence →
    the auto-on waits; a fresh valid ON reading resumes it."""

    ownership_store = _MemoryOwnershipStore()
    ownership_store.payload = {
        "version": 1,
        "records": [{
            "roomId": _ROOM_ID,
            "targetId": "light_main",
            "source": "manual",
            "confirmed": True,
            "at": _NOW_MS - 7_200_000,
        }],
        "manualOff": [],
        "morning": [],
    }
    payload = _config_payload(minimum_interval_seconds=600, stable_absence_seconds=30)
    payload["profile"] = "day_curve"
    clock = _Clock(_NOW_MS)
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_dt(_NOW_MS))
    hass.states.set("light.demo_main", "off", last_changed=_dt(_NOW_MS))
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=executor, now_ms=clock,
        payload=payload, ownership_store=ownership_store,
    )
    await runtime.start(hass, "entry")
    try:
        # The empty room matures the window; the latch sets (via the real
        # absence evidence with working sensors).
        clock.value = _NOW_MS + 35_000
        await runtime.async_process()
        assert _ROOM_ID in runtime._absence_release_latch  # type: ignore[attr-defined]

        # The sensor becomes UNKNOWN (unreliable): the auto-on must wait.
        hass.states.set(
            "binary_sensor.demo_presence", "unknown", last_changed=_dt(_NOW_MS + 36_000)
        )
        clock.value = _NOW_MS + 40_000
        await runtime.async_process()
        assert executor.calls == [], (
            "no auto-on with an unreliable presence reading"
        )

        # A fresh valid ON reading: the automation resumes.
        hass.states.set(
            "binary_sensor.demo_presence", "on", last_changed=_dt(_NOW_MS + 50_000)
        )
        clock.value = _NOW_MS + 65_000
        await runtime.async_process()
        assert executor.calls, "the automation resumes on the valid return"
    finally:
        await runtime.stop()


async def test_entry_wakeup_wake_does_not_bypass_manual_presence_gate() -> None:
    """A synthetic unlock wake (the presence_confirmed flag) is not presence.

    With a proven manual record the unlock wakeup alone keeps the room dark
    (fail-closed): the flag does not pass the presence gate without a valid
    ON sensor reading. The light resumes when the sensor itself proves
    presence (a fresh ON held for the confirmation floor).
    """

    ownership_store = _MemoryOwnershipStore()
    ownership_store.payload = {
        "version": 1,
        "records": [{
            "roomId": _ROOM_ID,
            "targetId": "light_main",
            "source": "manual",
            "confirmed": True,
            "at": _NOW_MS - 7_200_000,
        }],
        "manualOff": [],
        "morning": [],
    }
    payload = _config_payload(minimum_interval_seconds=600, stable_absence_seconds=30)
    payload["profile"] = "day_curve"
    clock = _Clock(_NOW_MS)
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_dt(_NOW_MS))
    hass.states.set("light.demo_main", "off", last_changed=_dt(_NOW_MS))
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=executor, now_ms=clock,
        payload=payload, ownership_store=ownership_store,
    )
    await runtime.start(hass, "entry")
    try:
        # The empty room matures the window; the latch sets.
        clock.value = _NOW_MS + 35_000
        await runtime.async_process()
        assert _ROOM_ID in runtime._absence_release_latch  # type: ignore[attr-defined]

        # The entry unlocks: the wakeup window is active while the presence
        # reading is unreliable. The synthetic confirmation flag must not
        # light the room.
        runtime._entry_wakeup_until[_ROOM_ID] = _NOW_MS + 120_000  # type: ignore[attr-defined]
        hass.states.set(
            "binary_sensor.demo_presence", "unknown", last_changed=_dt(_NOW_MS + 36_000)
        )
        clock.value = _NOW_MS + 40_000
        await runtime.async_process()
        assert executor.calls == [], (
            "the unlock wake must not bypass the presence gate"
        )

        # The sensor proves presence (a fresh ON held for the confirmation
        # floor): the gate passes on sensor evidence.
        hass.states.set(
            "binary_sensor.demo_presence", "on", last_changed=_dt(_NOW_MS + 50_000)
        )
        clock.value = _NOW_MS + 65_000
        await runtime.async_process()
        assert executor.calls, "the light resumes on the sensor's own evidence"
    finally:
        await runtime.stop()


def test_new_on_episode_restarts_curve_confirmation() -> None:
    """A moved last_changed proves a new ON episode: the floor restarts.

    A delayed processing pass must not confirm presence across a hidden
    OFF: the sensor went off and on again between two observations, so the
    continuous ON duration follows the newest ON transition, not the first
    episode start (SEC-RP9-PRESENCE-01).
    """

    hass = _FakeHass()
    runtime = _make_runtime(
        hass, payload=_curve_payload(commands_enabled=False), now_ms=lambda: _NOW_MS
    )
    config = config_from_payload(_curve_payload(commands_enabled=False))

    def context(moment: int, last_changed: int) -> RoomLightingContext:
        return RoomLightingContext(
            now=moment, timezone=_TZ, sunrise=time(7, 0), sunset=time(19, 0),
            sensors=(SensorSnapshot(
                "sensor_demo_presence", SensorKind.PRESENCE, SensorState.ON, last_changed,
            ),),
        )

    start = _NOW_MS
    # The first ON episode begins; the processing pass is on time.
    assert runtime._observe_curve_presence(  # type: ignore[attr-defined]
        config, context(start, start), start
    ) == (False, 0)
    # Hidden OFF at start+11s, a new ON at start+12s; the next processing
    # pass at start+15s only sees the current ON (last_changed=start+12s).
    # Three seconds of continuous ON must not confirm presence.
    confirmed, _ = runtime._observe_curve_presence(  # type: ignore[attr-defined]
        config, context(start + 15_000, start + 12_000), start + 15_000
    )
    assert confirmed is False
    # The new episode holds for its own confirmation floor.
    confirmed, _ = runtime._observe_curve_presence(  # type: ignore[attr-defined]
        config, context(start + 27_000, start + 12_000), start + 27_000
    )
    assert confirmed is True


async def test_manual_power_on_resets_absence_evidence() -> None:
    """A manual power restore is an action boundary for the absence proof.

    The latch proven before the action must not release the manual hold on
    old evidence: after the owner powers the room back, a fresh stable
    absence is required before the automation may light the room again.
    """

    ownership_store = _MemoryOwnershipStore()
    ownership_store.payload = {
        "version": 1,
        "records": [{
            "roomId": _ROOM_ID,
            "targetId": "light_main",
            "source": "manual",
            "confirmed": True,
            "at": _NOW_MS - 7_200_000,
        }],
        "manualOff": [],
        "morning": [],
    }
    payload = _config_payload(minimum_interval_seconds=600, stable_absence_seconds=30)
    payload["devices"]["power_switch"] = {
        "id": "power",
        "name": "Питание",
        "entityId": "switch.demo_power",
        "autoAdoptOverride": None,
    }
    clock = _Clock(_NOW_MS)
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_dt(_NOW_MS))
    hass.states.set("light.demo_main", "off", last_changed=_dt(_NOW_MS))
    hass.states.set("switch.demo_power", "off", last_changed=_dt(_NOW_MS))
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=executor, now_ms=clock,
        payload=payload, ownership_store=ownership_store,
    )
    await runtime.start(hass, "entry")
    try:
        # The empty room matures the window; the latch sets.
        clock.value = _NOW_MS + 35_000
        await runtime.async_process()
        assert _ROOM_ID in runtime._absence_release_latch  # type: ignore[attr-defined]

        # The owner restores the room power: the proven latch and the
        # absence window are stale from this action onwards.
        clock.value = _NOW_MS + 40_000
        runtime._service_event(_event("switch", "turn_on", "switch.demo_power"))
        hass.states.set("switch.demo_power", "on", last_changed=_dt(clock.value))
        await asyncio.gather(*hass.tasks)
        assert _ROOM_ID not in runtime._absence_release_latch  # type: ignore[attr-defined]
        assert runtime._manual_absence_floor.get(_ROOM_ID) == _NOW_MS + 40_000  # type: ignore[attr-defined]

        # The person returns: presence is confirmed, but the hold must not
        # release on the pre-action evidence.
        clock.value = _NOW_MS + 50_000
        hass.states.set(
            "binary_sensor.demo_presence", "on", last_changed=_dt(clock.value)
        )
        await runtime.async_process()
        clock.value = _NOW_MS + 65_000
        await runtime.async_process()
        assert executor.calls == [], (
            "the stale latch must not release after a manual power action"
        )

        # A fresh stable absence after the action restores the honest path.
        clock.value = _NOW_MS + 70_000
        hass.states.set(
            "binary_sensor.demo_presence", "off", last_changed=_dt(clock.value)
        )
        await runtime.async_process()
        # The sensor keeps holding OFF (last_changed stays at T+70).
        clock.value = _NOW_MS + 105_000
        await runtime.async_process()
        assert _ROOM_ID in runtime._absence_release_latch  # type: ignore[attr-defined]
        clock.value = _NOW_MS + 110_000
        hass.states.set(
            "binary_sensor.demo_presence", "on", last_changed=_dt(clock.value)
        )
        await runtime.async_process()
        # The sensor keeps holding ON (last_changed stays at T+110); the
        # confirmation floor completes 15 s into the return.
        clock.value = _NOW_MS + 125_000
        await runtime.async_process()
        assert executor.calls, "the fresh absence releases the hold on the return"
    finally:
        await runtime.stop()


async def test_morning_door_wake_does_not_release_manual_holds() -> None:
    """An unlock releases nothing (owner rule 2026-10-09).

    During the morning wait the room keeps its night state: the unlock wake
    does not start the day and must not delete the manual holds
    (SEC-RP9-MORNING-02). Only a trusted presence reading starts the day,
    releasing the holds dated before the boundary; a manual action made
    after the boundary keeps its ordinary hold.
    """

    ownership_store = _MemoryOwnershipStore()
    ownership_store.payload = {
        "version": 1,
        "records": [
            {
                "roomId": _ROOM_ID,
                "targetId": "light_main",
                "source": "manual",
                "confirmed": True,
                # 06:00 UTC: dated before the 07:00 boundary.
                "at": _NOW_MS - 14_400_000,
            },
            {
                "roomId": _ROOM_ID,
                "targetId": "light_main",
                "source": "manual",
                "confirmed": True,
                "turnedOff": True,
                # 08:00 UTC: dated after the 07:00 boundary.
                "at": _NOW_MS - 7_200_000,
            },
        ],
        "manualOff": [],
        "morning": [],
    }
    payload = _config_payload(minimum_interval_seconds=600, stable_absence_seconds=30)
    payload["profile"] = "day_curve"
    payload["morningBoundary"] = {
        "enabled": True, "time": "07:00", "presenceGate": True,
    }
    clock = _Clock(_NOW_MS)
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "unknown", last_changed=_dt(_NOW_MS))
    hass.states.set("light.demo_main", "off", last_changed=_dt(_NOW_MS))
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=executor, now_ms=clock,
        payload=payload, ownership_store=ownership_store,
    )
    await runtime.start(hass, "entry")
    try:
        # A morning wait is pending since the 07:00 boundary (matching the
        # wall time); nobody trusted has been seen yet.
        runtime._ownership.mark_morning_waiting(  # type: ignore[attr-defined]
            _ROOM_ID, "2026-09-11", _NOW_MS - 10_800_000, _NOW_MS - 10_800_000
        )
        config = runtime._configs[_ROOM_ID]

        # The entry unlocks while the presence reading stays unreliable:
        # the room keeps its night state (the curve is not evaluated during
        # the wait), no commands are sent, both holds survive.
        runtime._entry_wakeup_until[_ROOM_ID] = _NOW_MS + 60_000  # type: ignore[attr-defined]
        await runtime.async_process()
        assert (
            runtime._morning_gate(config, config.morning, clock.value)  # type: ignore[attr-defined]
            == "wait"
        )
        assert executor.calls == [], "the wake must not light a waiting room"
        assert len(runtime._ownership.snapshots_for(  # type: ignore[attr-defined]
            _ROOM_ID, ("light_main",)
        )) == 2, "an unlock must not delete either manual hold"

        # A trusted presence reading starts the day (the processing pass
        # itself transitions): the pre-boundary hold is released, the
        # post-boundary hold keeps its ordinary protection.
        hass.states.set(
            "binary_sensor.demo_presence", "on", last_changed=_dt(clock.value)
        )
        await runtime.async_process()
        entry = runtime._ownership.morning_entry(_ROOM_ID)  # type: ignore[attr-defined]
        assert entry is not None and not entry.get("waiting"), (
            "the trusted presence starts the day"
        )
        remaining = runtime._ownership.snapshots_for(  # type: ignore[attr-defined]
            _ROOM_ID, ("light_main",)
        )
        assert not [
            snapshot for snapshot in remaining
            if snapshot.source is OwnershipSource.MANUAL
            and snapshot.at < _NOW_MS - 10_800_000
        ], "the morning transition releases the pre-boundary hold"
        assert any(
            snapshot.source is OwnershipSource.MANUAL
            and snapshot.at == _NOW_MS - 7_200_000
            for snapshot in remaining
        ), "the post-boundary manual action keeps its ordinary hold"
    finally:
        await runtime.stop()


async def test_proven_power_pair_plans_relight_after_release() -> None:
    """The wall key and the chandelier are one pair (owner rule 2026-10-10).

    While the room power switch proves the circuit off, the pair reads the
    room target off (a bulb that lost power cannot report; its stale
    ``on`` is voided) and the released hold plans the relight. The dispatch
    order (power first) and the send-gate proof are pinned by
    test_dispatch_switches_power_on_before_the_target and
    test_turn_on_matrix_honors_captured_pair_proof.
    """

    payload = _config_payload(
        minimum_interval_seconds=30, stable_absence_seconds=30,
        power_switch_entity='switch.demo_power',
    )
    payload['profile'] = 'day_curve'
    clock = _Clock(_NOW_MS)
    hass = _FakeHass()
    # The bulb was on long ago; the wall key cut the circuit at T0, so the
    # report is stale (beyond the freshness window) and HA still says "on".
    # The tambur's own profile (day_curve) runs this path.
    hass.states.set(
        'light.demo_main', 'on', last_changed=_dt(_NOW_MS - 600_000)
    )
    hass.states.set('switch.demo_power', 'off', last_changed=_dt(_NOW_MS))
    hass.states.set('binary_sensor.demo_presence', 'off', last_changed=_dt(_NOW_MS))
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=executor,
        now_ms=clock, payload=payload,
    )
    await runtime.start(hass, 'entry')
    try:
        # The manual switch-off lands at T0 (the wall key act, attributed).
        clock.value = _NOW_MS
        runtime._service_event(_event('light', 'turn_off', 'light.demo_main'))
        await asyncio.gather(*hass.tasks)

        # The empty room matures the window; the latch sets (the presence
        # sensor keeps holding OFF from T0). While the power is proven off,
        # the pair reads the room target off (state OFF, the stale report
        # voided).
        clock.value = _NOW_MS + 35_000
        await runtime.async_process()
        assert _ROOM_ID in runtime._absence_release_latch  # type: ignore[attr-defined]
        context = await runtime.async_context_for(runtime._configs[_ROOM_ID])
        assert context.light('light_main').state is SensorState.OFF and (
            context.light('light_main').reported_state is None
        ), 'the proven-off power must read the room target off (the pair)'

        # The person returns; the curve confirms the presence and the
        # released hold plans the relight (turn_on: the pair view reads the
        # target off).
        clock.value = _NOW_MS + 40_000
        hass.states.set(
            'binary_sensor.demo_presence', 'on', last_changed=_dt(clock.value)
        )
        await runtime.async_process()
        clock.value = _NOW_MS + 55_000
        hass.states.set(
            'binary_sensor.demo_presence', 'on', last_changed=_dt(_NOW_MS + 40_000)
        )
        await runtime.async_process()
        from custom_components.hausman_hub.domain.room_lighting_engine import (
            decision_target,
            evaluate_curve_room,
        )
        from custom_components.hausman_hub.domain.room_lighting_ownership import SensorState as SS
        context = await runtime.async_context_for(runtime._configs[_ROOM_ID])
        assert context.light('light_main').state is SensorState.OFF
        decision = evaluate_curve_room(
            runtime._configs[_ROOM_ID], context,
            presence=SS.ON, presence_confirmed=True, absence_seconds=None,
        )
        main = decision_target(decision, 'light_main')
        assert main is not None, "the held room must still be evaluated"
        assert any(
            command.action is LightAction.TURN_ON for command in main.commands
        ), (
            "the relight must be planned: the stale reported on must not "
            f"keep the room dark (skips={[s.reason.value for s in main.skips]})"
        )
        assert all(
            skip.reason is not SkipReason.MANUAL_OWNERSHIP for skip in main.skips
        ), "the manual hold must release on the proven-off power"
    finally:
        await runtime.stop()


async def test_pair_proof_survives_repower_and_delivers() -> None:
    """The delivery chain through the REAL dispatch: the pair proof is
    captured BEFORE the power preparation, survives the confirmed re-power
    and the final check, and the target command fires (the stale ``on``
    must not block the send)."""

    payload = _config_payload(
        minimum_interval_seconds=30, stable_absence_seconds=30,
        power_switch_entity='switch.demo_power',
    )
    payload['profile'] = 'day_curve'
    clock = _Clock(_NOW_MS)
    hass = _FakeHass()
    hass.states.set(
        'light.demo_main', 'on', last_changed=_dt(_NOW_MS - 600_000)
    )
    hass.states.set('switch.demo_power', 'off', last_changed=_dt(_NOW_MS))
    hass.states.set('binary_sensor.demo_presence', 'off', last_changed=_dt(_NOW_MS))
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=RoomLightingHaExecutor(),
        now_ms=clock, payload=payload,
    )
    await runtime.start(hass, 'entry')
    try:
        clock.value = _NOW_MS
        runtime._service_event(_event('light', 'turn_off', 'light.demo_main'))
        await asyncio.gather(*hass.tasks)
        clock.value = _NOW_MS + 35_000
        await runtime.async_process()
        clock.value = _NOW_MS + 40_000
        hass.states.set(
            'binary_sensor.demo_presence', 'on', last_changed=_dt(clock.value)
        )
        await runtime.async_process()
        await asyncio.gather(*hass.tasks)
        # The relay confirms the re-power: the deferred target command
        # delivers through the final check.
        hass.states.set('switch.demo_power', 'on', last_changed=_dt(_NOW_MS + 41_000))
        clock.value = _NOW_MS + 42_000
        await runtime.async_process()
        await asyncio.gather(*hass.tasks)
        entities = [call[2].get('entity_id') for call in hass.services.calls]
        assert 'switch.demo_power' in entities, 'the pair must re-power first'
        assert 'light.demo_main' in entities, (
            'the captured pair proof must survive the re-power and deliver '
            'the target command'
        )
        assert entities.index('switch.demo_power') < entities.index('light.demo_main')
    finally:
        await runtime.stop()


async def test_untrusted_on_voided_by_newest_manual_off_delivers_without_pair() -> None:
    """The dispatch send-gate without a captured pair proof (SEC-RP10-01).

    The bulb's raw HA state is a stale ``on`` (it lost power before it
    could report and no pair is bound), and the newest manual action for
    the target is the turn-off. The released hold plans the relight; the
    send-gate must read the voided report off (the journal's own
    manual-off evidence, not a snapshot field) and deliver the additive
    turn_on — not crash on a missing attribute and not answer
    ``already_on`` from the voided report."""

    payload = _config_payload(
        minimum_interval_seconds=30, stable_absence_seconds=30,
    )
    payload['profile'] = 'day_curve'
    clock = _Clock(_NOW_MS)
    hass = _FakeHass()
    # A stale raw "on": the trusted context view is unknown+reported-on,
    # and the dispatch evidence stays untrusted with last_known "on".
    hass.states.set(
        'light.demo_main', 'on', last_changed=_dt(_NOW_MS - 600_000)
    )
    hass.states.set('binary_sensor.demo_presence', 'off', last_changed=_dt(_NOW_MS))
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=executor,
        now_ms=clock, payload=payload,
    )
    await runtime.start(hass, 'entry')
    try:
        # The newest manual action for the target is the turn-off.
        clock.value = _NOW_MS
        runtime._service_event(_event('light', 'turn_off', 'light.demo_main'))
        await asyncio.gather(*hass.tasks)

        # The empty room matures the window and the latch sets; the engine
        # releases the hold on the voided stale report (no pair involved).
        clock.value = _NOW_MS + 35_000
        await runtime.async_process()
        assert _ROOM_ID in runtime._absence_release_latch  # type: ignore[attr-defined]

        # The person returns; the curve plans the relight and the dispatch
        # delivers it through the manual-off void (no pair proof).
        clock.value = _NOW_MS + 40_000
        hass.states.set(
            'binary_sensor.demo_presence', 'on', last_changed=_dt(clock.value)
        )
        await runtime.async_process()
        clock.value = _NOW_MS + 55_000
        await runtime.async_process()
        assert any(
            call.target_id == 'light_main' and call.action is LightAction.TURN_ON
            for call in executor.calls
        ), (
            "the voided stale on must deliver the relight through the "
            f"send-gate without a pair proof (calls={executor.calls})"
        )
    finally:
        await runtime.stop()


def test_newest_manual_action_is_off_generation_semantics() -> None:
    """The journal's own proof (SEC-RP10-01): the newest manual action is
    the off exactly when the manual-off generation matches the newest
    manual record generation; a later manual turn-on clears the answer,
    and targets without manual records prove nothing."""

    from custom_components.hausman_hub.application.room_lighting_runtime import (
        RoomLightingOwnershipJournal,
    )

    journal = RoomLightingOwnershipJournal(now_ms=lambda: 1_000)
    assert journal.newest_manual_action_is_off('room', 'light_main') is False

    journal.record_manual('room', 'light_main', 1_000, turned_off=True)
    assert journal.newest_manual_action_is_off('room', 'light_main') is True

    journal.record_manual('room', 'light_main', 2_000, turned_off=False)
    assert journal.newest_manual_action_is_off('room', 'light_main') is False, (
        "a newer manual turn-on must clear the turn-off answer"
    )

    journal.record_manual('room', 'light_main', 3_000, turned_off=True)
    assert journal.newest_manual_action_is_off('room', 'light_main') is True

    journal.record_auto('room', 'light_main', 4_000)
    assert journal.newest_manual_action_is_off('room', 'light_main') is True, (
        "an automatic record does not change the manual lineage"
    )

    # SEC-RP10B-01: the generations must survive a save/restore round-trip;
    # a journal restored without them must answer False (safe), never
    # mistake the restored zero generations for a proof of the turn-off.
    payload = journal.to_payload()
    restored = RoomLightingOwnershipJournal(now_ms=lambda: 5_000)
    restored.restore(payload)
    assert restored.newest_manual_action_is_off('room', 'light_main') is True, (
        "the round-trip must keep the turn-off distinguishable"
    )

    switched = RoomLightingOwnershipJournal(now_ms=lambda: 1_000)
    switched.record_manual('room', 'light_main', 1_000, turned_off=True)
    switched.record_manual('room', 'light_main', 2_000, turned_off=False)
    restored_switched = RoomLightingOwnershipJournal(now_ms=lambda: 5_000)
    restored_switched.restore(switched.to_payload())
    assert restored_switched.newest_manual_action_is_off(
        'room', 'light_main'
    ) is False, (
        "after restore a newer manual turn-on must still clear the answer"
    )

    legacy = dict(payload)
    legacy.pop('manualRecordGenerations', None)
    legacy.pop('manualOffGenerations', None)
    restored_legacy = RoomLightingOwnershipJournal(now_ms=lambda: 5_000)
    restored_legacy.restore(legacy)
    assert restored_legacy.newest_manual_action_is_off(
        'room', 'light_main'
    ) is False, (
        "an old payload without the generation maps must answer False, "
        "never a false proof of the turn-off"
    )

    # SEC-RP10B-01 (second round): a NEW manual action after the restore
    # must take a strictly fresh generation — the restored turn-off proof
    # must not swallow it. Exact audit sequence: the restored off holds
    # generation 1; without the counter bump the next on would reuse 1.
    single = RoomLightingOwnershipJournal(now_ms=lambda: 1_000)
    single.record_manual('room', 'light_main', 1_000, turned_off=True)
    assert single.newest_manual_action_is_off('room', 'light_main') is True
    after_single = RoomLightingOwnershipJournal(now_ms=lambda: 6_000)
    after_single.restore(single.to_payload())
    after_single.record_manual('room', 'light_main', 6_000, turned_off=False)
    assert after_single.newest_manual_action_is_off(
        'room', 'light_main'
    ) is False, (
        "a manual turn-on after the restore must clear the restored "
        "turn-off answer (no generation reuse)"
    )
    after = RoomLightingOwnershipJournal(now_ms=lambda: 6_000)
    after.restore(payload)
    after.record_manual('room', 'light_main', 6_000, turned_off=False)
    assert after.newest_manual_action_is_off('room', 'light_main') is False, (
        "a manual turn-on after the restore must clear the restored "
        "turn-off answer (no generation reuse)"
    )
    after_off = RoomLightingOwnershipJournal(now_ms=lambda: 7_000)
    after_off.restore(payload)
    after_off.record_manual('room', 'light_main', 7_000, turned_off=True)
    assert after_off.newest_manual_action_is_off(
        'room', 'light_main'
    ) is True, "a manual turn-off after the restore must prove the off"

    # SEC-RP10B-04: generations without their own evidence (no manual-off
    # anchor / no manual record) must not fabricate the proof; a damaged
    # document stays fail-safe.
    damaged = {
        'records': [
            {'roomId': 'room', 'targetId': 'light_main',
             'source': 'manual', 'confirmed': True, 'at': 1_000},
        ],
        'manualOff': [],
        'manualRecordGenerations': [
            {'roomId': 'room', 'targetId': 'light_main', 'generation': 5},
        ],
        'manualOffGenerations': [
            {'roomId': 'room', 'targetId': 'light_main', 'generation': 5},
        ],
    }
    damaged_journal = RoomLightingOwnershipJournal(now_ms=lambda: 5_000)
    damaged_journal.restore(damaged)
    assert damaged_journal.newest_manual_action_is_off(
        'room', 'light_main'
    ) is False, (
        "equal positive generations without a manual-off anchor must not "
        "prove the turn-off"
    )
    orphan = {
        'records': [],
        'manualOff': [],
        'manualRecordGenerations': [
            {'roomId': 'room', 'targetId': 'light_main', 'generation': 5},
        ],
        'manualOffGenerations': [
            {'roomId': 'room', 'targetId': 'light_main', 'generation': 5},
        ],
    }
    orphan_journal = RoomLightingOwnershipJournal(now_ms=lambda: 5_000)
    orphan_journal.restore(orphan)
    assert orphan_journal.newest_manual_action_is_off(
        'room', 'light_main'
    ) is False, "generations without any manual record prove nothing"

    # SEC-RP10B-05: the restored maps are bounded; the next save stays
    # bounded too (a damaged document cannot grow the journal). The flood
    # carries CONSISTENT counterparts for every pair (a manual record, an
    # off anchor at the same moment, equal generations), so only the cap
    # can bound the maps — and the bound is checked on their FULL size.
    flooded = {
        'records': [
            {'roomId': f'room{n}', 'targetId': 'light_main',
             'source': 'manual', 'confirmed': True, 'at': 1_000}
            for n in range(5000)
        ],
        'manualOff': [
            {'roomId': f'room{n}', 'targetId': 'light_main', 'at': 1_000}
            for n in range(5000)
        ],
        'manualRecordGenerations': [
            {'roomId': f'room{n}', 'targetId': 'light_main', 'generation': 1}
            for n in range(5000)
        ],
        'manualOffGenerations': [
            {'roomId': f'room{n}', 'targetId': 'light_main', 'generation': 1}
            for n in range(5000)
        ],
    }
    flooded_journal = RoomLightingOwnershipJournal(now_ms=lambda: 5_000)
    flooded_journal.restore(flooded)
    # No zero placeholders: the maps hold only accepted entries, and the
    # bound covers their FULL size (both maps, at the cap boundary).
    assert len(flooded_journal._manual_record_generation) == 4096
    assert len(flooded_journal._manual_off_generation) == 4096
    assert not any(
        value <= 0
        for value in flooded_journal._manual_record_generation.values()
    )
    assert not any(
        value <= 0
        for value in flooded_journal._manual_off_generation.values()
    )
    resaved = flooded_journal.to_payload()
    # No growth on the re-save: the maps stay within the bound.
    assert len(resaved['manualRecordGenerations']) <= 4096
    assert len(resaved['manualOffGenerations']) <= 4096
    resaved_journal = RoomLightingOwnershipJournal(now_ms=lambda: 6_000)
    resaved_journal.restore(resaved)
    assert len(resaved_journal._manual_record_generation) <= 4096
    assert len(resaved_journal._manual_off_generation) <= 4096

    # Conflicting duplicates: the first valid entry wins, deterministically.
    duplicated = {
        'records': [
            {'roomId': 'room', 'targetId': 'light_main',
             'source': 'manual', 'confirmed': True, 'at': 1_000,
             'turnedOff': True, 'generation': 2},
        ],
        'manualOff': [
            {'roomId': 'room', 'targetId': 'light_main', 'at': 1_000},
        ],
        'manualRecordGenerations': [
            {'roomId': 'room', 'targetId': 'light_main', 'generation': 2},
            {'roomId': 'room', 'targetId': 'light_main', 'generation': 9},
        ],
        'manualOffGenerations': [
            {'roomId': 'room', 'targetId': 'light_main', 'generation': 2},
            {'roomId': 'room', 'targetId': 'light_main', 'generation': 9},
        ],
    }
    duplicated_journal = RoomLightingOwnershipJournal(now_ms=lambda: 5_000)
    duplicated_journal.restore(duplicated)
    assert duplicated_journal._manual_record_generation[
        ('room', 'light_main')
    ] == 2, "the first valid generation entry must win"
    assert duplicated_journal.newest_manual_action_is_off(
        'room', 'light_main'
    ) is True

    # SEC-RP10B-04 (fourth round, the exact audit sequence): a damaged
    # document bumped the off generation (1 → 2) to equal the newer manual
    # record's generation, but the anchor still points at the older action
    # — the contradiction must answer False, never a forged off proof.
    real = RoomLightingOwnershipJournal(now_ms=lambda: 1_000)
    real.record_manual('room', 'light_main', 1_000, turned_off=True)
    real.record_manual('room', 'light_main', 2_000, turned_off=False)
    damaged_off = real.to_payload()
    for entry in damaged_off['manualOffGenerations']:
        if entry['targetId'] == 'light_main':
            entry['generation'] = 2
    restored_damaged = RoomLightingOwnershipJournal(now_ms=lambda: 5_000)
    restored_damaged.restore(damaged_off)
    assert restored_damaged.newest_manual_action_is_off(
        'room', 'light_main'
    ) is False, (
        "the anchor at 1000 contradicts the newest manual record at 2000: "
        "the bumped generation must not forge the off proof"
    )
    # The honest document keeps the distinguishable answers.
    restored_real = RoomLightingOwnershipJournal(now_ms=lambda: 5_000)
    restored_real.restore(real.to_payload())
    assert restored_real.newest_manual_action_is_off(
        'room', 'light_main'
    ) is False, "the honest OFF→ON document must restore the False answer"

    # SEC-RP10B-04 (fifth round, equal timestamps): OFF@1000 gen1 and
    # ON@1000 gen2 share the moment — a document that bumps the off
    # generation to 2 is indistinguishable from the honest one, so the
    # ambiguous match must answer False, never resolve the command.
    same_ms = RoomLightingOwnershipJournal(now_ms=lambda: 1_000)
    same_ms.record_manual('room', 'light_main', 1_000, turned_off=True)
    same_ms.record_manual('room', 'light_main', 1_000, turned_off=False)
    damaged_same = same_ms.to_payload()
    for entry in damaged_same['manualOffGenerations']:
        if entry['targetId'] == 'light_main':
            entry['generation'] = 2
    restored_same = RoomLightingOwnershipJournal(now_ms=lambda: 5_000)
    restored_same.restore(damaged_same)
    assert restored_same.newest_manual_action_is_off(
        'room', 'light_main'
    ) is False, (
        "two manual actions share the anchor moment: the bumped generation "
        "must not resolve the ambiguity into an off proof"
    )

    # SEC-RP10B-04 (sixth round): the bucket trimming must not turn the
    # ambiguous document into an unambiguous one — the check reads the RAW
    # document history, not the trimmed 500-record bucket.
    trimmed_history = {
        'records': (
            [{'roomId': 'room', 'targetId': 'light_main',
              'source': 'manual', 'confirmed': True, 'at': 1_000}]
            + [{'roomId': 'room', 'targetId': 'light_main',
                'source': 'auto', 'confirmed': True, 'at': 2_000 + n}
               for n in range(499)]
            + [{'roomId': 'room', 'targetId': 'light_main',
                'source': 'manual', 'confirmed': True, 'at': 1_000}]
        ),
        'manualOff': [
            {'roomId': 'room', 'targetId': 'light_main', 'at': 1_000},
        ],
        'manualRecordGenerations': [
            {'roomId': 'room', 'targetId': 'light_main', 'generation': 2},
        ],
        'manualOffGenerations': [
            {'roomId': 'room', 'targetId': 'light_main', 'generation': 2},
        ],
    }
    assert len(trimmed_history['records']) > 500, "the trim must be exercised"
    trimmed_journal = RoomLightingOwnershipJournal(now_ms=lambda: 5_000)
    trimmed_journal.restore(trimmed_history)
    assert trimmed_journal.newest_manual_action_is_off(
        'room', 'light_main'
    ) is False, (
        "the trimmed bucket hides the second same-moment manual action: "
        "the ambiguous document must stay False"
    )
    resaved_trimmed = trimmed_journal.to_payload()
    resaved_trimmed_journal = RoomLightingOwnershipJournal(now_ms=lambda: 6_000)
    resaved_trimmed_journal.restore(resaved_trimmed)
    assert resaved_trimmed_journal.newest_manual_action_is_off(
        'room', 'light_main'
    ) is False, "the re-saved document must keep the safe answer"

    # SEC-RP10B-04 (seventh round, the LIVE journal sequence): the trimming
    # happens inside the running journal too — the off record is gone from
    # the saved document before any restore. The proof must stay False
    # through the full save → damage → restore → re-save → restore cycle.
    live = RoomLightingOwnershipJournal(now_ms=lambda: 1_000)
    live.record_manual('room', 'light_main', 1_000, turned_off=True)
    for n in range(499):
        live.record_auto('room', 'light_main', 2_000 + n)
    live.record_manual('room', 'light_main', 1_000, turned_off=False)
    live_payload = live.to_payload()
    assert len(live_payload['records']) == 500, "the trim must be exercised"
    for entry in live_payload['manualOffGenerations']:
        if entry['targetId'] == 'light_main':
            entry['generation'] = 2
    live_restored = RoomLightingOwnershipJournal(now_ms=lambda: 5_000)
    live_restored.restore(live_payload)
    assert live_restored.newest_manual_action_is_off(
        'room', 'light_main'
    ) is False, (
        "the live-trimmed document cannot prove the trimmed turn-off: "
        "the bumped generation must not resolve into an off proof"
    )
    live_resaved = live_restored.to_payload()
    live_again = RoomLightingOwnershipJournal(now_ms=lambda: 6_000)
    live_again.restore(live_resaved)
    assert live_again.newest_manual_action_is_off(
        'room', 'light_main'
    ) is False, "the re-saved trimmed document must keep the safe answer"

    # The honest limited history keeps the positive answer: the newest
    # manual action IS the turn-off and its record survived the bound.
    honest = RoomLightingOwnershipJournal(now_ms=lambda: 1_000)
    honest.record_manual('room', 'light_main', 1_000, turned_off=True)
    for n in range(400):
        honest.record_auto('room', 'light_main', 2_000 + n)
    honest_restored = RoomLightingOwnershipJournal(now_ms=lambda: 5_000)
    honest_restored.restore(honest.to_payload())
    assert honest_restored.newest_manual_action_is_off(
        'room', 'light_main'
    ) is True, (
        "an honest bounded history with the turn-off as the newest manual "
        "action must keep the off proof after a round-trip"
    )

    # The call-time validation is the last line of defence: even a forged
    # off-generation in the maps cannot match the anchor record's own
    # generation and direction.
    forged = RoomLightingOwnershipJournal(now_ms=lambda: 1_000)
    forged.record_manual('room', 'light_main', 1_000, turned_off=True)
    forged.record_manual('room', 'light_main', 2_000, turned_off=False)
    forged._manual_off_generation[('room', 'light_main')] = 2
    assert forged.newest_manual_action_is_off(
        'room', 'light_main'
    ) is False, (
        "the anchor record (moment 1000) carries generation 1: a forged "
        "off-generation of 2 must not match it"
    )

    # SEC-RP10B-04 (eighth round): a later manual record whose generation
    # was DAMAGED to zero (a string payload value parses to 0) still vetoes
    # the proof — its later moment proves a newer manual action.
    damaged_generation = RoomLightingOwnershipJournal(now_ms=lambda: 1_000)
    damaged_generation.record_manual(
        'room', 'light_main', 1_000, turned_off=True
    )
    damaged_generation.record_manual(
        'room', 'light_main', 2_000, turned_off=False
    )
    payload_damaged = damaged_generation.to_payload()
    payload_damaged['records'][1]['generation'] = "2"
    restored_damaged_generation = RoomLightingOwnershipJournal(now_ms=lambda: 5_000)
    restored_damaged_generation.restore(payload_damaged)
    assert restored_damaged_generation.newest_manual_action_is_off(
        'room', 'light_main'
    ) is False, (
        "the later manual ON keeps its later moment even when its "
        "generation was damaged: it must veto the off proof"
    )

    # (eighth round): duplicate positive generations — two manual records
    # claiming the same place in the lineage make the newest action
    # ambiguous, even when one of them matches the anchor.
    duplicated_generation = {
        'records': [
            {'roomId': 'room', 'targetId': 'light_main',
             'source': 'manual', 'confirmed': True, 'at': 1_000,
             'turnedOff': False, 'generation': 2},
            {'roomId': 'room', 'targetId': 'light_main',
             'source': 'manual', 'confirmed': True, 'at': 2_000,
             'turnedOff': True, 'generation': 2},
        ],
        'manualOff': [
            {'roomId': 'room', 'targetId': 'light_main', 'at': 2_000},
        ],
        'manualRecordGenerations': [
            {'roomId': 'room', 'targetId': 'light_main', 'generation': 2},
        ],
        'manualOffGenerations': [
            {'roomId': 'room', 'targetId': 'light_main', 'generation': 2},
        ],
    }
    duplicated_gen_journal = RoomLightingOwnershipJournal(now_ms=lambda: 5_000)
    duplicated_gen_journal.restore(duplicated_generation)
    assert duplicated_gen_journal.newest_manual_action_is_off(
        'room', 'light_main'
    ) is False, (
        "two manual records with the same generation make the newest "
        "action ambiguous: no off proof"
    )

    # (ninth round): the honest same-moment sequence (an older turn-on and
    # a newer turn-off, distinct valid generations) keeps its positive
    # proof across a round-trip — the restore must not reject it just
    # because two manual actions share the moment.
    same_ms_honest = RoomLightingOwnershipJournal(now_ms=lambda: 1_000)
    same_ms_honest.record_manual('room', 'light_main', 1_000, turned_off=False)
    same_ms_honest.record_manual('room', 'light_main', 1_000, turned_off=True)
    assert same_ms_honest.newest_manual_action_is_off(
        'room', 'light_main'
    ) is True
    same_ms_restored = RoomLightingOwnershipJournal(now_ms=lambda: 5_000)
    same_ms_restored.restore(same_ms_honest.to_payload())
    assert same_ms_restored.newest_manual_action_is_off(
        'room', 'light_main'
    ) is True, (
        "the honest same-moment ON-then-OFF with distinct generations must "
        "keep the off proof after a round-trip"
    )

    # (ninth round): the mirror case — a same-moment turn-on whose
    # generation was DAMAGED cannot prove its order, and the uncertainty
    # must not turn into a positive off proof.
    same_ms_damaged = same_ms_honest.to_payload()
    for entry in same_ms_damaged['records']:
        if entry.get('source') == 'manual' and not entry.get('turnedOff'):
            entry['generation'] = 0
    same_ms_damaged_restored = RoomLightingOwnershipJournal(now_ms=lambda: 5_000)
    same_ms_damaged_restored.restore(same_ms_damaged)
    assert same_ms_damaged_restored.newest_manual_action_is_off(
        'room', 'light_main'
    ) is False, (
        "a same-moment turn-on with a lost generation cannot prove its "
        "order: the uncertainty must not become an off proof"
    )

    # (tenth round): the mirrored honest sequence (an older turn-off and a
    # newer same-moment turn-on) with the ON's generation damaged DOWN to
    # the off's generation — equal generations at the same moment are
    # ambiguous too: False, in the live journal and after a round-trip.
    off_then_on = RoomLightingOwnershipJournal(now_ms=lambda: 1_000)
    off_then_on.record_manual('room', 'light_main', 1_000, turned_off=True)
    off_then_on.record_manual('room', 'light_main', 1_000, turned_off=False)
    assert off_then_on.newest_manual_action_is_off(
        'room', 'light_main'
    ) is False, "the live journal knows the turn-on was newest"
    damaged_equal = off_then_on.to_payload()
    for entry in damaged_equal['records']:
        if entry.get('source') == 'manual' and not entry.get('turnedOff'):
            entry['generation'] = 1
    for entry in damaged_equal.get('manualRecordGenerations', []):
        entry['generation'] = 1
    for entry in damaged_equal.get('manualOffGenerations', []):
        entry['generation'] = 1
    damaged_equal_restored = RoomLightingOwnershipJournal(now_ms=lambda: 5_000)
    damaged_equal_restored.restore(damaged_equal)
    assert damaged_equal_restored.newest_manual_action_is_off(
        'room', 'light_main'
    ) is False, (
        "equal generations at the same moment are ambiguous: the damaged "
        "turn-on must veto the off proof"
    )


def test_apply_proven_power_pair_unit() -> None:
    """The pair override: proven-off power reads the PAIRED chandelier off
    and voids its stale report; the pair binds the chandelier only — an
    independent room target keeps its own evidence even when stale. A
    fresh ON target (independent circuit or newer report), a non-config
    target, power on, unknown power and no power switch leave the context
    untouched."""

    payload = _config_payload(power_switch_entity='switch.demo_power')
    payload['devices']['light_targets'].append({
        'id': 'light_second',
        'name': 'Вторая',
        'kind': 'light',
        'role': 'accent',
        'groupId': None,
        'brightness': False,
        'color_temperature': False,
        'entityId': 'light.demo_second',
        'autoAdoptOverride': None,
    })
    hass = _FakeHass()
    from custom_components.hausman_hub.domain.room_lighting import config_from_payload
    from custom_components.hausman_hub.domain.room_lighting_engine import (
        LightSnapshot,
        RoomLightingContext,
    )
    config = config_from_payload(payload)
    runtime = _make_runtime(
        hass, payload=payload, now_ms=lambda: _NOW_MS
    )
    lights = (
        # The paired chandelier with a stale on: overridden.
        LightSnapshot(target_id='light_main', state=SensorState.UNKNOWN,
                      last_changed=_NOW_MS - 600_000, reported_state=SensorState.ON),
        # A fresh ON target of the room: contradicts the proven-off power
        # (an independent circuit) — untouched.
        LightSnapshot(target_id='light_second', state=SensorState.ON,
                      last_changed=_NOW_MS, reported_state=SensorState.ON),
        # An INDEPENDENT room target with an unknown state and a stale
        # report: whether it shares the chandelier circuit is an open
        # wiring question, so the pair must NOT silently override it.
        LightSnapshot(target_id='light_second', state=SensorState.UNKNOWN,
                      last_changed=_NOW_MS - 600_000, reported_state=SensorState.ON),
        # A foreign (not configured) stale target: untouched even though it
        # is stale — the pair covers only its own paired target.
        LightSnapshot(target_id='foreign', state=SensorState.UNKNOWN,
                      last_changed=_NOW_MS - 600_000, reported_state=SensorState.ON),
    )

    def ctx(lights_tuple):
        return RoomLightingContext(
            now=_NOW_MS, timezone=_TZ, sunrise=time(7, 0), sunset=time(19, 0),
            sensors=(), lights=lights_tuple,
        )

    hass.states.set('switch.demo_power', 'off', last_changed=_dt(_NOW_MS))
    paired = runtime._apply_proven_power_pair(ctx(lights), config)  # type: ignore[attr-defined]
    assert paired.lights[0].state is SensorState.OFF
    assert paired.lights[0].reported_state is None
    assert paired.lights[1] == lights[1], (
        'a fresh ON target of the room is untouched'
    )
    assert paired.lights[2] == lights[2], (
        'an independent stale target keeps its own evidence: the pair '
        'binds the chandelier only'
    )
    assert paired.lights[3] == lights[3], 'a foreign stale target is untouched'

    # Unknown power: fail-closed, no override.
    hass.states.set('switch.demo_power', 'unknown', last_changed=_dt(_NOW_MS))
    unknown = runtime._apply_proven_power_pair(ctx(lights), config)  # type: ignore[attr-defined]
    assert unknown.lights == lights

    hass.states.set('switch.demo_power', 'on', last_changed=_dt(_NOW_MS))
    untouched = runtime._apply_proven_power_pair(ctx(lights), config)  # type: ignore[attr-defined]
    assert untouched.lights == lights

    payload_bare = _config_payload()
    config_bare = config_from_payload(payload_bare)
    runtime_bare = _make_runtime(
        _FakeHass(), payload=payload_bare, now_ms=lambda: _NOW_MS
    )
    bare = runtime_bare._apply_proven_power_pair(ctx(lights), config_bare)  # type: ignore[attr-defined]
    assert bare.lights == lights


async def test_room_power_off_proof_binds_the_chandelier_only() -> None:
    """The room power action blocks every target, but its positive
    manual-off proof binds the chandelier only (SEC-RP10B-06): an
    independent target that stays on and silent must not get its stale
    report voided by the room-wide record."""

    payload = _config_payload(power_switch_entity='switch.demo_power')
    payload['devices']['light_targets'].append({
        'id': 'light_second',
        'name': 'Вторая',
        'kind': 'light',
        'role': 'accent',
        'groupId': None,
        'brightness': False,
        'color_temperature': False,
        'entityId': 'light.demo_second',
        'autoAdoptOverride': None,
    })
    hass = _FakeHass()
    hass.states.set('switch.demo_power', 'on', last_changed=_dt(_NOW_MS))
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=_SpyExecutor(),
        now_ms=lambda: _NOW_MS, payload=payload,
    )
    await runtime.start(hass, 'entry')
    try:
        # The person cuts the room power (the chandelier's wall key).
        runtime._record_power_manual(_ROOM_ID, 'switch.demo_power', 'turn_off')
        await asyncio.gather(*hass.tasks)
        config = runtime._configs[_ROOM_ID]

        # The paired chandelier keeps the full off-proof.
        assert runtime._ownership.newest_manual_action_is_off(
            _ROOM_ID, 'light_main'
        ) is True
        assert runtime._untrusted_on_voided_by_manual_off(
            config, 'light_main'
        ) is True

        # The independent target keeps the blocking manual hold but no
        # positive off-proof: its stale report cannot be voided by the
        # room-wide power record.
        assert any(
            snapshot.source is OwnershipSource.MANUAL
            for snapshot in runtime._ownership.snapshots_for(
                _ROOM_ID, ('light_second',)
            )
        ), 'the blocking protection must stay for every target'
        assert runtime._ownership.newest_manual_action_is_off(
            _ROOM_ID, 'light_second'
        ) is False, (
            'the room power record must not serve as the independent '
            "target's manual-off evidence"
        )
        assert runtime._untrusted_on_voided_by_manual_off(
            config, 'light_second'
        ) is False, (
            'the send-gate must refuse to void the independent target on '
            'the room-wide record'
        )

        # A DIFFERENT power source (not the configured pair switch — e.g. an
        # exact-map relay of the mirror) must not grant the chandelier its
        # off-proof either: its own circuit is a different one.
        hass.states.set('switch.other_source', 'on', last_changed=_dt(_NOW_MS))
        runtime._record_power_manual(
            _ROOM_ID, 'switch.other_source', 'turn_off'
        )
        await asyncio.gather(*hass.tasks)
        assert runtime._ownership.newest_manual_action_is_off(
            _ROOM_ID, 'light_main'
        ) is False, (
            "a non-pair power source must not prove the chandelier's "
            "manual off"
        )
        assert runtime._untrusted_on_voided_by_manual_off(
            config, 'light_main'
        ) is False, (
            'the send-gate must refuse the pair void on a non-pair source'
        )
    finally:
        await runtime.stop()


async def test_completed_absence_latches_on_frozen_return() -> None:
    """The latch sets when the window completes at the frozen return.

    If no evaluation runs between the completion moment and the return, the
    proven stable absence must still latch: the next short window must not
    erase it (Sol scenario, RP-9). OFF at T0, return at T+35 (35 s proven,
    frozen at the sensor's own return time), leave at T+40, return at T+45:
    the release fires on the latch, and the light turns on after the curve
    confirms the presence.
    """

    ownership_store = _MemoryOwnershipStore()
    ownership_store.payload = {
        "version": 1,
        "records": [{
            "roomId": _ROOM_ID,
            "targetId": "light_main",
            "source": "manual",
            "confirmed": True,
            "at": _NOW_MS - 7_200_000,
        }],
        "manualOff": [],
        "morning": [],
    }
    payload = _config_payload(minimum_interval_seconds=600, stable_absence_seconds=30)
    payload["profile"] = "day_curve"
    clock = _Clock(_NOW_MS)
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_dt(_NOW_MS))
    hass.states.set("light.demo_main", "off", last_changed=_dt(_NOW_MS))
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=executor, now_ms=clock,
        payload=payload, ownership_store=ownership_store,
    )
    await runtime.start(hass, "entry")
    try:
        # The window opens at T0 and is not evaluated again until the
        # return: no processing pass observes the 30 s completion moment.
        clock.value = _NOW_MS
        await runtime.async_process()

        # The person returns at T+35: the window freezes at the sensor's
        # own return time — and the completed 35 s absence latches.
        clock.value = _NOW_MS + 35_000
        hass.states.set(
            "binary_sensor.demo_presence", "on", last_changed=_dt(clock.value)
        )
        await runtime.async_process()
        absence = runtime._absence.get(_ROOM_ID)  # type: ignore[attr-defined]
        assert absence is not None and absence[2] == _NOW_MS + 35_000, (
            "the window end freezes at the return"
        )
        assert _ROOM_ID in runtime._absence_release_latch, (  # type: ignore[attr-defined]
            "the completed window latches at the frozen return"
        )

        # A short second window starts: the latch (the historical fact)
        # survives it.
        clock.value = _NOW_MS + 40_000
        hass.states.set(
            "binary_sensor.demo_presence", "off", last_changed=_dt(clock.value)
        )
        await runtime.async_process()
        assert _ROOM_ID in runtime._absence_release_latch  # type: ignore[attr-defined]

        # The person returns again after 5 s: the release fires on the
        # latch, and the light turns on once the curve confirms presence.
        clock.value = _NOW_MS + 45_000
        hass.states.set(
            "binary_sensor.demo_presence", "on", last_changed=_dt(clock.value)
        )
        await runtime.async_process()
        assert executor.calls == [], (
            "no light before the curve confirms the returned presence"
        )
        clock.value = _NOW_MS + 60_000
        await runtime.async_process()
        assert executor.calls, "the latch releases the hold on the return"
    finally:
        await runtime.stop()


async def test_mirror_night_turn_on_gated_by_unreliable_presence() -> None:
    """The night mirror auto-on waits for confirmed presence while a manual
    hold exists (the restored record; the pipeline view — the engine-level
    differentiators cover the gate semantics directly)."""

    ownership_store = _MemoryOwnershipStore()
    ownership_store.payload = {
        "version": 1,
        "records": [{
            "roomId": _ROOM_ID,
            "targetId": "light_main",
            "source": "manual",
            "confirmed": True,
            "at": _NOW_MS - 7_200_000,
        }],
        "manualOff": [],
        "morning": [],
    }
    payload = _config_payload(minimum_interval_seconds=600, stable_absence_seconds=30)
    payload["profile"] = "day_curve"
    payload["devices"]["light_targets"].append({
        "id": "light_mirror",
        "name": "Зеркало",
        "kind": "switch",
        "role": "mirror",
        "groupId": None,
        "brightness": False,
        "color_temperature": False,
        "entityId": "switch.demo_mirror",
        "autoAdoptOverride": None,
    })
    clock = _Clock(_NOW_MS)
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_dt(_NOW_MS))
    hass.states.set("light.demo_main", "off", last_changed=_dt(_NOW_MS))
    hass.states.set("switch.demo_mirror", "off", last_changed=_dt(_NOW_MS))
    executor = _SpyExecutor()
    runtime = _make_runtime(
        hass, commands_enabled=True, executor=executor, now_ms=clock,
        payload=payload, ownership_store=ownership_store,
    )
    await runtime.start(hass, "entry")
    try:
        runtime._service_event(_event("light", "turn_off", "light.demo_main"))
        hass.states.set("light.demo_main", "off", last_changed=_dt(clock.value))
        await asyncio.gather(*hass.tasks)

        # Night: the mirror's schedule wants on; the presence is unreliable.
        clock.value = _NOW_MS + 660_000
        hass.states.set(
            "binary_sensor.demo_presence", "unknown", last_changed=_dt(clock.value)
        )
        await runtime.async_process()
        mirror_calls = [
            c for c in executor.calls
            if getattr(c, "target_id", "") == "light_mirror"
            and getattr(c, "action", None) is LightAction.TURN_ON
        ]
        assert mirror_calls == [], (
            "the mirror auto-on must wait for confirmed presence"
        )
    finally:
        await runtime.stop()


class TestPowerSourceDeliveryEpisode:
    """One confirmed presence opens ONE bounded, spaced delivery episode
    for an unconfirmed power-on (live test 2026-10-12: a single lost
    Zigbee packet must not lock the scenario until a manual
    intervention). Each attempt still needs its own confirmation; the
    spacing counts from the ORIGINAL failed attempt (AUD-POWER-02)."""

    POWER = "switch.demo_power"
    ROOM = "room_demo_entry"

    def _runtime(self, clock: dict[str, int]):
        hass = _FakeHass()
        hass.states.set(self.POWER, "off", last_changed=_dt(_NOW_MS))

        class _RetryExecutor(_SpyExecutor):
            async def async_power_on(
                self, hass: object, entity_id: str
            ) -> dict[str, object]:
                del hass, entity_id
                return {"confirmed": True, "sent": True, "sendOutcome": "sent"}

        runtime = _make_runtime(
            hass,
            commands_enabled=True,
            executor=_RetryExecutor(),
            now_ms=lambda: clock["value"],
        )
        config = config_from_payload(_config_payload())
        runtime._configs = {config.room_id: config}
        hass.states.set("binary_sensor.demo_presence", "on", last_changed=_dt(_NOW_MS))
        return runtime, hass


    def _simulated_retry_blocked(
        self, runtime, entity_id, *, room_id=None, command_action="turn_on"
    ):
        """Unit policy driver: explicitly account for a simulated unconfirmed send.

        Production admission is now read-only. Full executor/storage paths
        are exercised separately by the lifecycle matrix below.
        """
        revision = runtime._power_evidence_revision(entity_id)
        runtime._release_power_failure_on_fresh_evidence(entity_id, revision)
        plan = runtime._plan_power_attempt(
            entity_id, room_id=room_id, command_action=command_action
        )
        if plan is None:
            return True
        if plan.retry_episode is not None:
            runtime._commit_power_attempt(entity_id, plan, runtime._now_ms())
        return False

    @staticmethod
    async def _cancel_when_started(task, started):
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def _confirmed_presence(self, runtime, since: int) -> None:
        runtime._curve_presence[self.ROOM] = {
            "since": since,
            "last_seen": since,
            "absence": 0,
        }

    def _latch(self, runtime, failed_at: int) -> None:
        """Simulate the ORIGINAL unconfirmed attempt at ``failed_at``."""
        revision = runtime._power_evidence_revision(self.POWER)
        runtime._power_failures[self.POWER] = ("turn_on", revision)
        runtime._power_failure_at[self.POWER] = failed_at

    def test_episode_opens_and_is_spaced_from_the_original(self) -> None:
        clock = {"value": _NOW_MS}
        runtime, _hass = self._runtime(clock)
        self._confirmed_presence(runtime, _NOW_MS - 20_000)
        self._latch(runtime, _NOW_MS)
        # Inside the spacing window from the ORIGINAL attempt: suppressed,
        # and NO episode is opened yet (nothing to count).
        clock["value"] = _NOW_MS + POWER_RETRY_INTERVAL_MS - 1_000
        assert self._simulated_retry_blocked(runtime,self.POWER, room_id=self.ROOM) is True
        assert self.POWER not in runtime._power_delivery
        # After the spacing: the episode opens with retry 1.
        clock["value"] = _NOW_MS + POWER_RETRY_INTERVAL_MS + 1_000
        assert self._simulated_retry_blocked(runtime,self.POWER, room_id=self.ROOM) is False
        assert runtime._power_delivery[self.POWER].attempts == 1
        # Spacing from the last attempt: suppressed, then retry 2.
        clock["value"] = _NOW_MS + 2 * POWER_RETRY_INTERVAL_MS
        assert self._simulated_retry_blocked(runtime,self.POWER, room_id=self.ROOM) is True
        clock["value"] = _NOW_MS + 2 * POWER_RETRY_INTERVAL_MS + 1_000
        assert self._simulated_retry_blocked(runtime,self.POWER, room_id=self.ROOM) is False
        assert runtime._power_delivery[self.POWER].attempts == 2

    def test_episode_is_capped(self) -> None:
        clock = {"value": _NOW_MS + 60_000}
        runtime, _hass = self._runtime(clock)
        self._confirmed_presence(runtime, _NOW_MS - 20_000)
        self._latch(runtime, _NOW_MS)
        for attempt in range(POWER_RETRY_LIMIT_PER_REVISION):
            clock["value"] = _NOW_MS + (attempt + 1) * (
                POWER_RETRY_INTERVAL_MS + 1_000
            )
            assert (
                self._simulated_retry_blocked(runtime,self.POWER, room_id=self.ROOM)
                is False
            ), attempt
        # The episode is exhausted: suppressed until a new episode opens.
        clock["value"] = _NOW_MS + 5 * (POWER_RETRY_INTERVAL_MS + 1_000)
        assert self._simulated_retry_blocked(runtime,self.POWER, room_id=self.ROOM) is True

    def test_new_presence_episode_never_refreshes_the_budget(self) -> None:
        """AUD-POWER-03/06: the delivery episode BELONGS TO THE RELAY. A
        new confirmed-presence episode (even a genuine return) does not
        refresh an exhausted budget — only fresh trusted relay evidence
        does."""
        clock = {"value": _NOW_MS}
        runtime, _hass = self._runtime(clock)
        self._confirmed_presence(runtime, _NOW_MS - 20_000)
        self._latch(runtime, _NOW_MS)
        for attempt in range(POWER_RETRY_LIMIT_PER_REVISION):
            clock["value"] = _NOW_MS + (attempt + 1) * (
                POWER_RETRY_INTERVAL_MS + 1_000
            )
            self._simulated_retry_blocked(runtime,self.POWER, room_id=self.ROOM)
        # The episode is exhausted.
        clock["value"] = _NOW_MS + 5 * (POWER_RETRY_INTERVAL_MS + 1_000)
        assert self._simulated_retry_blocked(runtime,self.POWER, room_id=self.ROOM) is True
        # The occupant leaves and returns: a NEW confirmed-presence
        # episode must NOT refresh the exhausted budget.
        new_since = _NOW_MS + 6 * (POWER_RETRY_INTERVAL_MS + 1_000)
        clock["value"] = new_since + CURVE_PRESENCE_CONFIRM_MS + 1_000
        self._confirmed_presence(runtime, new_since)
        assert self._simulated_retry_blocked(runtime,self.POWER, room_id=self.ROOM) is True

    def test_shared_relay_budget_is_not_refreshed_by_another_room(self) -> None:
        """AUD-POWER-06: two rooms share one relay. Room A exhausts the
        episode; room B (a different, confirmed presence pointer) must
        not open a fresh budget for the same relay."""
        clock = {"value": _NOW_MS}
        runtime, _hass = self._runtime(clock)
        # Room A confirms at T-20s, room B confirms at T-10s.
        self._confirmed_presence(runtime, _NOW_MS - 20_000)
        runtime._curve_presence["room_other"] = {
            "since": _NOW_MS - 10_000,
            "last_seen": _NOW_MS - 10_000,
            "absence": 0,
        }
        self._latch(runtime, _NOW_MS)
        for attempt in range(POWER_RETRY_LIMIT_PER_REVISION):
            clock["value"] = _NOW_MS + (attempt + 1) * (
                POWER_RETRY_INTERVAL_MS + 1_000
            )
            self._simulated_retry_blocked(runtime,self.POWER, room_id="room_demo_entry")
        # Exhausted by room A.
        clock["value"] = _NOW_MS + 5 * (POWER_RETRY_INTERVAL_MS + 1_000)
        assert self._simulated_retry_blocked(runtime,
            self.POWER, room_id="room_demo_entry"
        ) is True
        # Room B asks with ITS (different, newer) presence pointer: the
        # shared relay's budget is still exhausted.
        assert self._simulated_retry_blocked(runtime,
            self.POWER, room_id="room_other"
        ) is True

    def test_two_retries_can_never_run_back_to_back(self) -> None:
        """AUD-POWER-04: an allowed attempt stamps its OWN moment, so a
        second attempt epsilon after the first is still spaced out."""
        clock = {"value": _NOW_MS + 60_000}
        runtime, _hass = self._runtime(clock)
        self._confirmed_presence(runtime, _NOW_MS - 20_000)
        # The original attempt happened 9s ago: the spacing has elapsed.
        self._latch(runtime, _NOW_MS - 9_000)
        assert self._simulated_retry_blocked(runtime,self.POWER, room_id=self.ROOM) is False
        # Epsilon later: suppressed — the allowed attempt stamped NOW.
        clock["value"] = clock["value"] + 100
        assert self._simulated_retry_blocked(runtime,self.POWER, room_id=self.ROOM) is True
        # Only after the full interval from the STAMPED moment: allowed.
        clock["value"] = clock["value"] + POWER_RETRY_INTERVAL_MS
        assert self._simulated_retry_blocked(runtime,self.POWER, room_id=self.ROOM) is False

    async def test_exact_dispatch_delay_stamps_the_failure_moment(self) -> None:
        """AUD-POWER-04: if the pre-send bookkeeping (persist, lock wait)
        takes 9 seconds, the failure is stamped at the ACTUAL send moment
        — the first retry is still a full interval away from it."""
        clock = {"value": _NOW_MS}
        runtime, hass = self._runtime(clock)
        runtime._electrical_breaker_resolver = lambda _entity: False
        self._confirmed_presence(runtime, _NOW_MS - 20_000)
        # The original attempt happened 9s ago: the spacing has elapsed.
        self._latch(runtime, _NOW_MS - 9_000)
        # SEC-02: the retry carries the turn_on plan and a LIVE confirmed
        # presence (the send-boundary re-check requires both).
        runtime._configs = {  # type: ignore[attr-defined]
            config_from_payload(_config_payload()).room_id: config_from_payload(
                _config_payload()
            )
        }
        hass.states.set(
            "binary_sensor.demo_presence", "on", last_changed=_dt(_NOW_MS)
        )

        class _UnconfirmedExecutor(_SpyExecutor):
            async def async_power_on(
                self, hass: object, entity_id: str, **kwargs: object
            ) -> dict[str, object]:
                return {"confirmed": False, "sent": True, "sendOutcome": "sent"}

        runtime._executor = _UnconfirmedExecutor()

        # The pre-send bookkeeping (persist + lock wait) takes 9 seconds.
        original_persist = runtime._persist_pending_power_dispatch

        async def slow_persist(**kwargs: object) -> bool:
            persisted = await original_persist(**kwargs)
            clock["value"] = clock["value"] + 9_000
            return persisted

        runtime._persist_pending_power_dispatch = slow_persist
        powered, detail = await runtime._ensure_power_source_locked(
            DevicePowerDependency(
                dependent_entity_id="light.demo_main",
                power_source_entity_id=self.POWER,
                policy="auto_turn_on",
                warmup_seconds=0,
            ),
            at=clock["value"],
            room_id=self.ROOM,
            operation_id="op-delayed",
            config_version=1,
            command_action="turn_on",
        )
        assert powered is False
        assert detail["reason"] == "power_source_unconfirmed"
        assert detail.get("suppressed") is not True
        # The failure is stamped at the ACTUAL send moment (after the 9s
        # bookkeeping): an immediate re-ask is still spaced out.
        stamped_at = runtime._power_failure_at[self.POWER]
        assert stamped_at == _NOW_MS + 9_000
        assert self._simulated_retry_blocked(runtime,self.POWER, room_id=self.ROOM) is True
        # After the full interval from the stamped moment: allowed.
        clock["value"] = stamped_at + POWER_RETRY_INTERVAL_MS + 1_000
        assert self._simulated_retry_blocked(runtime,self.POWER, room_id=self.ROOM) is False

    def test_untrusted_revision_never_refreshes_the_budget(self) -> None:
        """AUD-POWER-01: an untrusted revision (the relay going
        unavailable) is the LOSS of evidence — it must not open a new
        delivery episode or reset the exhausted one."""
        clock = {"value": _NOW_MS + 60_000}
        runtime, hass = self._runtime(clock)
        self._confirmed_presence(runtime, _NOW_MS - 20_000)
        self._latch(runtime, _NOW_MS)
        for attempt in range(POWER_RETRY_LIMIT_PER_REVISION):
            clock["value"] = _NOW_MS + (attempt + 1) * (
                POWER_RETRY_INTERVAL_MS + 1_000
            )
            self._simulated_retry_blocked(runtime,self.POWER, room_id=self.ROOM)
        clock["value"] = _NOW_MS + 5 * (POWER_RETRY_INTERVAL_MS + 1_000)
        assert self._simulated_retry_blocked(runtime,self.POWER, room_id=self.ROOM) is True
        hass.states.set(self.POWER, "unknown", last_changed=_dt(clock["value"]))
        assert self._simulated_retry_blocked(runtime,self.POWER, room_id=self.ROOM) is True

    def test_untrusted_off_relay_opens_spaced_retries_for_turn_on_plan(self) -> None:
        """Evening live test 2026-10-12: a relay that LOST the packet keeps
        reporting ``untrusted:off`` (silent). With an explicit turn_on plan
        and a currently confirmed presence the SAME bounded episode budget
        applies: spaced from the ORIGINAL attempt, capped, own
        confirmation."""
        clock = {"value": _NOW_MS}
        runtime, _hass = self._runtime_untrusted(clock)
        self._confirmed_presence(runtime, _NOW_MS - 20_000)
        self._latch(runtime, _NOW_MS)
        revision = runtime._power_evidence_revision(self.POWER)
        assert revision == "untrusted:off"
        # Inside the spacing window from the ORIGINAL attempt: suppressed.
        clock["value"] = _NOW_MS + POWER_RETRY_INTERVAL_MS - 1_000
        assert self._simulated_retry_blocked(runtime,
            self.POWER, room_id=self.ROOM, command_action="turn_on"
        ) is True
        # After the spacing: retry 1 is allowed for the turn_on plan.
        clock["value"] = _NOW_MS + POWER_RETRY_INTERVAL_MS + 1_000
        assert self._simulated_retry_blocked(runtime,
            self.POWER, room_id=self.ROOM, command_action="turn_on"
        ) is False
        assert runtime._power_delivery[self.POWER].attempts == 1
        assert runtime._power_delivery[self.POWER].revision == "untrusted:off"

    def test_untrusted_off_requires_the_turn_on_plan(self) -> None:
        """The silent-relay exception is ONLY for an explicit turn_on plan:
        other actions and plan-less callers stay suppressed."""
        clock = {"value": _NOW_MS + 60_000}
        runtime, _hass = self._runtime_untrusted(clock)
        self._confirmed_presence(runtime, _NOW_MS - 20_000)
        self._latch(runtime, _NOW_MS)
        for action in (None, "turn_off", "set_brightness", "set_color_temperature"):
            assert self._simulated_retry_blocked(runtime,
                self.POWER, room_id=self.ROOM, command_action=action
            ) is True, action

    def test_untrusted_off_rechecks_presence_on_every_retry(self) -> None:
        """Every untrusted retry re-checks the confirmed presence."""
        clock = {"value": _NOW_MS + 60_000}
        runtime, _hass = self._runtime_untrusted(clock)
        self._confirmed_presence(runtime, _NOW_MS - 20_000)
        self._latch(runtime, _NOW_MS - 9_000)
        assert self._simulated_retry_blocked(runtime,
            self.POWER, room_id=self.ROOM, command_action="turn_on"
        ) is False
        assert runtime._power_delivery[self.POWER].attempts == 1
        # The presence pointer faults: the next retry stays suppressed.
        runtime._curve_presence.pop(self.ROOM)
        clock["value"] += POWER_RETRY_INTERVAL_MS + 1_000
        assert self._simulated_retry_blocked(runtime,
            self.POWER, room_id=self.ROOM, command_action="turn_on"
        ) is True
        # Presence is confirmed again: the SAME remaining budget applies.
        self._confirmed_presence(runtime, clock["value"] - 20_000)
        clock["value"] += POWER_RETRY_INTERVAL_MS + 1_000
        assert self._simulated_retry_blocked(runtime,
            self.POWER, room_id=self.ROOM, command_action="turn_on"
        ) is False
        assert runtime._power_delivery[self.POWER].attempts == 2

    def test_untrusted_unknown_and_missing_stay_blocked(self) -> None:
        """Only ``untrusted:off`` is the silent-relay class; unknown and
        missing evidence never allow a retry."""
        clock = {"value": _NOW_MS + 60_000}
        runtime, hass = self._runtime_untrusted(clock)
        self._confirmed_presence(runtime, _NOW_MS - 20_000)
        self._latch(runtime, _NOW_MS)
        stale = datetime.now(_TZ) - timedelta(seconds=600)
        hass.states.set(self.POWER, "unknown", last_changed=stale)
        assert self._simulated_retry_blocked(runtime,
            self.POWER, room_id=self.ROOM, command_action="turn_on"
        ) is True
        hass.states.pop(self.POWER, None) if hasattr(hass.states, "pop") else None
        runtime._entity_state = lambda entity_id: None  # type: ignore[method-assign]
        assert self._simulated_retry_blocked(runtime,
            self.POWER, room_id=self.ROOM, command_action="turn_on"
        ) is True

    def test_untrusted_revision_change_never_refreshes_budget(self) -> None:
        """A change between untrusted labels is not evidence: the budget
        belongs to the original revision only."""
        clock = {"value": _NOW_MS + 60_000}
        runtime, hass = self._runtime_untrusted(clock)
        self._confirmed_presence(runtime, _NOW_MS - 20_000)
        self._latch(runtime, _NOW_MS)
        # One retry is spent on the untrusted:off revision.
        assert self._simulated_retry_blocked(runtime,
            self.POWER, room_id=self.ROOM, command_action="turn_on"
        ) is False
        assert runtime._power_delivery[self.POWER].attempts == 1
        # The relay reports a DIFFERENT untrusted revision: suppressed.
        stale = datetime.now(_TZ) - timedelta(seconds=600)
        hass.states.set(self.POWER, "unknown", last_changed=stale)
        clock["value"] += POWER_RETRY_INTERVAL_MS + 1_000
        assert self._simulated_retry_blocked(runtime,
            self.POWER, room_id=self.ROOM, command_action="turn_on"
        ) is True

    def test_exhausted_untrusted_budget_stays_exhausted(self) -> None:
        clock = {"value": _NOW_MS + 60_000}
        runtime, _hass = self._runtime_untrusted(clock)
        self._confirmed_presence(runtime, _NOW_MS - 20_000)
        self._latch(runtime, _NOW_MS)
        for attempt in range(POWER_RETRY_LIMIT_PER_REVISION):
            clock["value"] = _NOW_MS + (attempt + 1) * (
                POWER_RETRY_INTERVAL_MS + 1_000
            )
            assert self._simulated_retry_blocked(runtime,
                self.POWER, room_id=self.ROOM, command_action="turn_on"
            ) is False, attempt
        clock["value"] = _NOW_MS + 5 * (POWER_RETRY_INTERVAL_MS + 1_000)
        assert self._simulated_retry_blocked(runtime,
            self.POWER, room_id=self.ROOM, command_action="turn_on"
        ) is True

    def test_unknown_original_attempt_boundary_stays_blocked(self) -> None:
        """A restored latch has no provable original attempt moment: the
        spacing cannot be honoured, so the retry stays suppressed."""
        clock = {"value": _NOW_MS + 60_000}
        runtime, _hass = self._runtime_untrusted(clock)
        self._confirmed_presence(runtime, _NOW_MS - 20_000)
        revision = runtime._power_evidence_revision(self.POWER)
        runtime._power_failures[self.POWER] = ("turn_on", revision)
        # No _power_failure_at entry: the boundary is unknown.
        assert self._simulated_retry_blocked(runtime,
            self.POWER, room_id=self.ROOM, command_action="turn_on"
        ) is True

    def _runtime_untrusted(self, clock: dict[str, int]):
        """Like _runtime, but the relay's last report is STALE: the evidence
        revision is genuinely ``untrusted:off`` (silent relay)."""
        hass = _FakeHass()
        stale = datetime.now(_TZ) - timedelta(seconds=600)
        hass.states.values[self.POWER] = SimpleNamespace(
            state="off", attributes={}, restored=False,
            last_changed=stale, last_reported=stale, last_updated=stale,
        )
        # A live presence sensor: the send-boundary re-check
        # (_confirmed_presence_live) must find it trusted and on.
        hass.states.set(
            "binary_sensor.demo_presence", "on", last_changed=_dt(_NOW_MS)
        )

        class _RetryExecutor(_SpyExecutor):
            async def async_power_on(
                self, hass: object, entity_id: str
            ) -> dict[str, object]:
                del hass, entity_id
                return {"confirmed": True, "sent": True, "sendOutcome": "sent"}

        runtime = _make_runtime(
            hass,
            commands_enabled=True,
            executor=_RetryExecutor(),
            now_ms=lambda: clock["value"],
        )
        config = config_from_payload(_config_payload())
        runtime._configs = {config.room_id: config}
        hass.states.set("binary_sensor.demo_presence", "on", last_changed=_dt(_NOW_MS))
        return runtime, hass

    async def test_legacy_power_retry_honours_the_plan_action(self) -> None:
        """The untrusted:off retry is bound to the explicit turn_on plan:
        the same outstanding latch suppresses any other plan entirely."""
        clock = {"value": _NOW_MS + 60_000}
        runtime, hass = self._runtime_untrusted(clock)
        self._confirmed_presence(runtime, _NOW_MS - 20_000)
        self._latch(runtime, _NOW_MS - 9_000)
        # The send-boundary live re-check reads the room config (sensors):
        # seed the config WITHOUT starting the room processing, so the
        # start-time dispatch does not spend the episode budget before the
        # scenario under test.
        runtime._configs = {  # type: ignore[attr-defined]
            config_from_payload(_config_payload()).room_id: config_from_payload(
                _config_payload()
            )
        }
        powered, detail = await runtime._ensure_legacy_room_power(
            self.POWER,
            at=clock["value"],
            room_id=self.ROOM,
            operation_id="op-plan-on",
            config_version=1,
            command_action="turn_on",
        )
        assert powered is True
        assert detail["reason"] == "power_source_on"
        # The latch is outstanding again with the SAME untrusted:off
        # revision, but the plan is NOT a turn_on: suppressed, no attempt.
        clock["value"] += POWER_RETRY_INTERVAL_MS + 1_000
        runtime._power_failures[self.POWER] = (
            "turn_on", runtime._power_evidence_revision(self.POWER),
        )
        runtime._power_failure_at[self.POWER] = _NOW_MS
        runtime._power_delivery.pop(self.POWER, None)
        powered, detail = await runtime._ensure_legacy_room_power(
            self.POWER,
            at=clock["value"],
            room_id=self.ROOM,
            operation_id="op-plan-brightness",
            config_version=1,
            command_action="set_brightness",
        )
        assert powered is False
        assert detail["reason"] == "power_source_unconfirmed"
        assert detail.get("suppressed") is True

    def test_fresh_trusted_revision_closes_a_stale_exhausted_episode(self) -> None:
        """Luna 2026-10-12 (finding 3): fresh TRUSTED evidence of a NEW
        revision closes the stale episode in the no-failure-record branch
        too; a later unconfirmed attempt on the NEW revision opens a NEW
        budget instead of being suppressed by the old exhausted one."""
        clock = {"value": _NOW_MS + 60_000}
        runtime, hass = self._runtime(clock)
        self._confirmed_presence(runtime, _NOW_MS - 20_000)
        self._latch(runtime, _NOW_MS)
        for attempt in range(POWER_RETRY_LIMIT_PER_REVISION):
            clock["value"] = _NOW_MS + (attempt + 1) * (
                POWER_RETRY_INTERVAL_MS + 1_000
            )
            self._simulated_retry_blocked(runtime,self.POWER, room_id=self.ROOM)
        # The failure record was already closed by a not-sent closure: the
        # episode (old revision) stays in the no-failure-record branch with
        # its whole budget spent (exhausted, cap reached).
        runtime._power_failures.pop(self.POWER, None)
        runtime._power_delivery[self.POWER] = replace(
            runtime._power_delivery[self.POWER], exhausted=True
        )
        # Fresh trusted relay evidence: a NEW revision. The stale episode
        # must be closed by the fresh TRUSTED revision (AUD-POWER-09, the
        # no-failure-record branch), NOT suppress the new revision.
        hass.states.set(self.POWER, "off", last_changed=_dt(clock["value"]))
        revision = runtime._power_evidence_revision(self.POWER)
        assert runtime._trusted_evidence_revision(revision)
        assert self._simulated_retry_blocked(runtime,
            self.POWER, room_id=self.ROOM, command_action="turn_on"
        ) is False
        assert self.POWER not in runtime._power_delivery, (
            "the stale exhausted episode must be closed by fresh trusted evidence"
        )
        # A later unconfirmed attempt on the NEW revision opens a NEW
        # budget (not blocked by the old exhausted episode).
        runtime._power_failures[self.POWER] = ("turn_on", revision)
        runtime._power_failure_at[self.POWER] = clock["value"]
        clock["value"] += POWER_RETRY_INTERVAL_MS + 1_000
        assert self._simulated_retry_blocked(runtime,
            self.POWER, room_id=self.ROOM, command_action="turn_on"
        ) is False
        assert runtime._power_delivery[self.POWER].revision == revision
        assert runtime._power_delivery[self.POWER].attempts == 1

    async def test_not_sent_closure_keeps_the_open_episode_durable(self) -> None:
        """Luna 2026-10-12 (finding 2): a not-sent closure must NOT wipe the
        durable latch while the episode is open; a restart restores the
        budget fail-closed (exhausted) until fresh trusted evidence."""
        clock = {"value": _NOW_MS + 60_000}
        runtime, hass = self._runtime_untrusted(clock)
        shadow_store = runtime._shadow._store  # type: ignore[attr-defined]
        self._confirmed_presence(runtime, _NOW_MS - 20_000)
        self._latch(runtime, _NOW_MS)
        # One retry is spent: the episode is open and still has budget.
        assert self._simulated_retry_blocked(runtime,
            self.POWER, room_id=self.ROOM, command_action="turn_on"
        ) is False
        closed = await runtime._persist_not_sent_power_dispatch_closure(
            at=clock["value"],
            room_id=self.ROOM,
            source_entity_id=self.POWER,
            evidence_revision=runtime._power_evidence_revision(self.POWER),
            reason="away_block",
            operation_id="op-not-sent",
            config_version=1,
        )
        assert closed is True
        payload = shadow_store.payload
        document = payload.get("data", payload) if isinstance(payload, dict) else {}
        failures = document["dispatchFailures"]
        assert any(
            entry.get("kind") == "power" and entry.get("entityId") == self.POWER
            for entry in failures
        ), failures
        # A restart on the SAME shadow store restores the latch
        # fail-closed (exhausted).
        from custom_components.hausman_hub.application.room_lighting_shadow import (
            RoomLightingShadowService,
        )
        shadow2 = RoomLightingShadowService(shadow_store)
        await shadow2.async_load()
        runtime2 = RoomLightingRuntime(
            hass,
            _ConfigService(config_from_payload(_config_payload())),
            shadow2,
            executor=_SpyExecutor(),
            ownership_store=_MemoryOwnershipStore(),
            now_ms=lambda: clock["value"],
            track_state_changes=lambda hass, entities, callback: (lambda: None),
            track_interval=lambda hass, callback, interval: (lambda: None),
            listen_bus=lambda hass, event_type, callback: (lambda: None),
        )
        runtime2._restore_dispatch_failures()
        assert runtime2._power_attempt_blocked(
            self.POWER, room_id=self.ROOM, command_action="turn_on"
        ) is True

    async def test_send_boundary_refusal_rolls_back_the_episode(self) -> None:
        """Luna third round (AUD-POWER-12): a refusal at the SEND boundary
        (after the awaited persistence) must leave the budget exactly as it
        was BEFORE the gate: the attempt is rolled back, the relay is not
        called."""
        clock = {"value": _NOW_MS + 60_000}
        runtime, hass = self._runtime_untrusted(clock)
        self._confirmed_presence(runtime, _NOW_MS - 20_000)
        self._latch(runtime, _NOW_MS - 9_000)
        runtime._configs = {  # type: ignore[attr-defined]
            config_from_payload(_config_payload()).room_id: config_from_payload(
                _config_payload()
            )
        }

        relay_calls: list[str] = []

        class _CountingExecutor(_SpyExecutor):
            async def async_power_on(self, hass: object, entity_id: str):
                relay_calls.append(entity_id)
                return {"confirmed": True, "sent": True, "sendOutcome": "sent"}

        runtime._executor = _CountingExecutor()  # type: ignore[assignment]

        original_persist = runtime._persist_pending_power_dispatch

        async def persist_then_presence_dies(**kwargs: object) -> bool:
            persisted = await original_persist(**kwargs)
            # The occupant leaves WHILE the intent write is awaited: the
            # current sensor state is a fresh OFF — the live presence
            # re-check must refuse the send.
            hass.states.set(
                "binary_sensor.demo_presence",
                "off",
                last_changed=_dt(clock["value"]),
            )
            return persisted

        runtime._persist_pending_power_dispatch = persist_then_presence_dies  # type: ignore[assignment]

        powered, detail = await runtime._ensure_legacy_room_power(
            self.POWER,
            at=clock["value"],
            room_id=self.ROOM,
            operation_id="op-boundary-refusal",
            config_version=1,
            command_action="turn_on",
        )
        assert powered is False
        assert detail["reason"] == "power_source_unconfirmed"
        assert relay_calls == [], "the relay must not be called after the refusal"
        # The budget was NOT spent: the gate's stamp is rolled back to the
        # pre-gate snapshot (no episode existed before the gate).
        assert self.POWER not in runtime._power_delivery, (
            runtime._power_delivery
        )
        # The latch (unconfirmed attempt record) is still in place.
        assert runtime._power_failures.get(self.POWER) is not None

    def test_no_confirmed_presence_opens_no_episode(self) -> None:
        clock = {"value": _NOW_MS}
        runtime, _hass = self._runtime(clock)
        self._confirmed_presence(runtime, _NOW_MS - 1_000)
        self._latch(runtime, _NOW_MS)
        assert self._simulated_retry_blocked(runtime,self.POWER, room_id=self.ROOM) is True
        assert self.POWER not in runtime._power_delivery
        runtime._curve_presence.pop(self.ROOM, None)
        assert self._simulated_retry_blocked(runtime,self.POWER, room_id=self.ROOM) is True

    def test_fresh_relay_evidence_closes_the_episode(self) -> None:
        clock = {"value": _NOW_MS}
        runtime, hass = self._runtime(clock)
        self._confirmed_presence(runtime, _NOW_MS - 20_000)
        old_revision = runtime._power_evidence_revision(self.POWER)
        runtime._power_failures[self.POWER] = ("turn_on", old_revision)
        runtime._power_failure_at[self.POWER] = _NOW_MS
        runtime._power_delivery[self.POWER] = PowerDeliveryEpisode(
            revision=old_revision,
            presence_since=_NOW_MS - 20_000,
            attempts=POWER_RETRY_LIMIT_PER_REVISION,
            last_attempt_ms=_NOW_MS,
        )
        assert self._simulated_retry_blocked(runtime,self.POWER, room_id=self.ROOM) is True
        clock["value"] = _NOW_MS + 30_000
        hass.states.set(
            self.POWER,
            "off",
            last_changed=datetime.fromtimestamp(
                clock["value"] / 1000, tz=timezone.utc
            ),
        )
        assert self._simulated_retry_blocked(runtime,self.POWER, room_id=self.ROOM) is False
        assert self.POWER not in runtime._power_failures
        assert self.POWER not in runtime._power_delivery
        runtime._power_failures[self.POWER] = (
            "turn_on",
            runtime._power_evidence_revision(self.POWER),
        )
        runtime._power_failure_at[self.POWER] = clock["value"]
        assert self._simulated_retry_blocked(runtime,self.POWER, room_id=self.ROOM) is True
        clock["value"] = _NOW_MS + 30_000 + POWER_RETRY_INTERVAL_MS + 1_000
        assert self._simulated_retry_blocked(runtime,self.POWER, room_id=self.ROOM) is False
        assert runtime._power_delivery[self.POWER].attempts == 1

    async def test_exact_success_clears_the_episode(self) -> None:
        clock = {"value": _NOW_MS + 60_000}
        runtime, hass = self._runtime(clock)
        runtime._electrical_breaker_resolver = lambda _entity: False
        self._confirmed_presence(runtime, _NOW_MS - 20_000)
        self._latch(runtime, _NOW_MS - 9_000)
        power_entity_id = self.POWER

        class _ReadBackExecutor(_SpyExecutor):
            async def async_power_on(
                self, hass: object, entity_id: str, **kwargs: object
            ) -> dict[str, object]:
                hass.states.set(
                    power_entity_id,
                    "on",
                    last_changed=datetime.fromtimestamp(
                        clock["value"] / 1000, tz=timezone.utc
                    ),
                )
                return {"confirmed": True, "sent": True, "sendOutcome": "sent"}

        runtime._executor = _ReadBackExecutor()
        powered, detail = await runtime._ensure_power_source_locked(
            DevicePowerDependency(
                dependent_entity_id="light.demo_main",
                power_source_entity_id=self.POWER,
                policy="auto_turn_on",
                warmup_seconds=0,
            ),
            at=clock["value"],
            room_id=self.ROOM,
            operation_id="op-exact-success",
            config_version=1,
            command_action="turn_on",
        )
        assert powered is True, detail
        assert self.POWER not in runtime._power_failures
        assert self.POWER not in runtime._power_delivery

    async def test_exact_warmup_failure_keeps_the_spacing(self) -> None:
        clock = {"value": _NOW_MS + 60_000}
        runtime, hass = self._runtime(clock)
        runtime._electrical_breaker_resolver = lambda _entity: False
        self._confirmed_presence(runtime, _NOW_MS - 20_000)
        self._latch(runtime, _NOW_MS - 9_000)
        # SEC-02: a retry needs the plan action and a LIVE confirmed
        # presence — seed the room config (sensors) and the sensor state.
        runtime._configs = {  # type: ignore[attr-defined]
            config_from_payload(_config_payload()).room_id: config_from_payload(
                _config_payload()
            )
        }
        hass.states.set(  # type: ignore[attr-defined]
            "binary_sensor.demo_presence", "on", last_changed=_dt(_NOW_MS)
        )


        class _NoReadBackExecutor(_SpyExecutor):
            async def async_power_on(
                self, hass: object, entity_id: str, **kwargs: object
            ) -> dict[str, object]:
                return {"confirmed": True, "sent": True, "sendOutcome": "sent"}

        runtime._executor = _NoReadBackExecutor()
        powered, detail = await runtime._ensure_power_source_locked(
            DevicePowerDependency(
                dependent_entity_id="light.demo_main",
                power_source_entity_id=self.POWER,
                policy="auto_turn_on",
                warmup_seconds=1,
            ),
            at=clock["value"],
            room_id=self.ROOM,
            operation_id="op-exact-warmup-fail",
            config_version=1,
            command_action="turn_on",
        )
        assert powered is False
        assert detail["reason"] == "power_source_unconfirmed"
        assert self.POWER in runtime._power_failures
        assert self._simulated_retry_blocked(runtime,self.POWER, room_id=self.ROOM) is True

    def test_legacy_confirmed_success_clears_the_episode(self) -> None:
        clock = {"value": _NOW_MS + 60_000}
        runtime, hass = self._runtime(clock)

        class _ConfirmExecutor(_SpyExecutor):
            async def async_power_on(
                self, hass: object, entity_id: str
            ) -> dict[str, object]:
                del hass, entity_id
                return {"confirmed": True, "sent": True, "sendOutcome": "sent"}

        runtime._executor = _ConfirmExecutor()
        self._confirmed_presence(runtime, _NOW_MS - 20_000)
        self._latch(runtime, _NOW_MS - 9_000)
        # SEC-02: a retry needs the plan action and a LIVE confirmed
        # presence — seed the room config (sensors) and the sensor state.
        runtime._configs = {  # type: ignore[attr-defined]
            config_from_payload(_config_payload()).room_id: config_from_payload(
                _config_payload()
            )
        }
        hass.states.set(  # type: ignore[attr-defined]
            "binary_sensor.demo_presence", "on", last_changed=_dt(_NOW_MS)
        )
        powered, detail = asyncio.run(
            runtime._ensure_legacy_room_power_locked(
                self.POWER,
                at=clock["value"],
                room_id=self.ROOM,
                operation_id="op-success",
                config_version=1,
                command_action="turn_on",
            )
        )
        assert powered is True
        assert self.POWER not in runtime._power_failures
        assert self.POWER not in runtime._power_delivery

    def test_restored_latch_from_durable_record_arrives_exhausted(self) -> None:
        from types import SimpleNamespace

        clock = {"value": _NOW_MS + 60_000}
        runtime, hass = self._runtime(clock)
        self._confirmed_presence(runtime, _NOW_MS - 20_000)
        evidence_revision = runtime._power_evidence_revision(self.POWER)
        runtime._shadow = SimpleNamespace(
            journal_payload=lambda: {
                "dispatchFailures": [
                    {
                        "kind": "power",
                        "entityId": self.POWER,
                        "action": "turn_on",
                        "evidenceRevision": evidence_revision,
                    }
                ]
            }
        )
        runtime._restore_dispatch_failures()
        assert runtime._power_failures[self.POWER] == ("turn_on", evidence_revision)
        restored_episode = runtime._power_delivery[self.POWER]
        assert restored_episode.exhausted is True
        assert restored_episode.attempts == POWER_RETRY_LIMIT_PER_REVISION
        assert self._simulated_retry_blocked(runtime,self.POWER, room_id=self.ROOM) is True
        assert runtime._power_delivery[self.POWER].exhausted is True
        clock["value"] = _NOW_MS + 90_000
        hass.states.set(
            self.POWER,
            "off",
            last_changed=datetime.fromtimestamp(
                clock["value"] / 1000, tz=timezone.utc
            ),
        )
        assert self._simulated_retry_blocked(runtime,self.POWER, room_id=self.ROOM) is False
        assert self.POWER not in runtime._power_failures
        assert self.POWER not in runtime._power_delivery

    def test_legacy_journal_migration_arrives_exhausted(self) -> None:
        from types import SimpleNamespace

        clock = {"value": _NOW_MS + 60_000}
        runtime, _hass = self._runtime(clock)
        self._confirmed_presence(runtime, _NOW_MS - 20_000)
        evidence_revision = runtime._power_evidence_revision(self.POWER)
        runtime._shadow = SimpleNamespace(
            journal_payload=lambda: {
                "entries": [
                    {
                        "at": _NOW_MS - 5_000,
                        "roomId": self.ROOM,
                        "dispatch": [
                            {
                                "targetId": "light_demo_main",
                                "action": "turn_on",
                                "evidenceRevision": evidence_revision,
                                "power": {
                                    "sourceEntityId": self.POWER,
                                    "evidenceRevision": evidence_revision,
                                    "dispatchAttempt": True,
                                    "sendOutcome": "sent",
                                    "confirmed": False,
                                    "reason": "power_source_unconfirmed",
                                },
                            }
                        ],
                    }
                ]
            }
        )
        runtime._restore_dispatch_failures()
        assert runtime._power_failures[self.POWER] == ("turn_on", evidence_revision)
        restored_episode = runtime._power_delivery[self.POWER]
        assert restored_episode.exhausted is True
        assert restored_episode.attempts == POWER_RETRY_LIMIT_PER_REVISION
        assert self._simulated_retry_blocked(runtime,self.POWER, room_id=self.ROOM) is True

    def test_stale_or_faulted_presence_backing_is_rejected(self) -> None:
        clock = {"value": _NOW_MS + 60_000}
        runtime, _hass = self._runtime(clock)
        runtime._curve_presence[self.ROOM] = {
            "since": _NOW_MS - 20_000,
            "last_seen": None,
            "absence": 0,
        }
        assert runtime._confirmed_curve_presence_since(self.ROOM) is None
        stale_seen = _NOW_MS - CURVE_SENSOR_FRESHNESS_MS - 1_000
        runtime._curve_presence[self.ROOM] = {
            "since": _NOW_MS - 20_000,
            "last_seen": stale_seen,
            "absence": 0,
        }
        assert runtime._confirmed_curve_presence_since(self.ROOM) is None

    async def test_not_sent_closure_with_open_episode_keeps_failure(self) -> None:
        """AUD-POWER-PERSIST-01 (real closure path): calling the not-sent
        closure with an open delivery episode does NOT clear the failure
        record."""
        clock = {"value": _NOW_MS + 60_000}
        runtime, _hass = self._runtime(clock)
        self._confirmed_presence(runtime, _NOW_MS - 20_000)
        revision = runtime._power_evidence_revision(self.POWER)
        runtime._power_failures[self.POWER] = ("turn_on", revision)
        runtime._power_delivery[self.POWER] = PowerDeliveryEpisode(
            revision=revision,
            presence_since=_NOW_MS - 20_000,
            attempts=1,
            last_attempt_ms=clock["value"] - POWER_RETRY_INTERVAL_MS - 1_000,
        )
        closed = await runtime._persist_not_sent_power_dispatch_closure(
            at=clock["value"],
            room_id=self.ROOM,
            source_entity_id=self.POWER,
            evidence_revision=revision,
            reason="away_mode",
            operation_id="op-test",
            config_version=1,
        )
        assert closed is True
        # The failure record SURVIVES the not-sent closure because the
        # delivery episode is still open.
        assert self.POWER in runtime._power_failures

    def test_not_sent_closure_keeps_the_episode_limits(self) -> None:
        """AUD-POWER-08: a not-sent closure of a NEW attempt must not
        clear the outstanding previous unconfirmed delivery — the live
        episode keeps limiting (cap and spacing)."""
        clock = {"value": _NOW_MS + 60_000}
        runtime, _hass = self._runtime(clock)
        self._confirmed_presence(runtime, _NOW_MS - 20_000)
        # An open episode from an earlier unconfirmed delivery.
        revision = runtime._power_evidence_revision(self.POWER)
        runtime._power_delivery[self.POWER] = PowerDeliveryEpisode(
            revision=revision,
            presence_since=_NOW_MS - 20_000,
            attempts=1,
            last_attempt_ms=clock["value"] - 1_000,
        )
        # A newer attempt was closed as NOT SENT (no failure record).
        assert self.POWER not in runtime._power_failures
        # The live episode still limits: spacing from the last allowed
        # attempt has NOT elapsed.
        assert self._simulated_retry_blocked(runtime,self.POWER, room_id=self.ROOM) is True
        # After the spacing from the last allowed attempt: a bounded
        # attempt is allowed (attempt 2 of the episode).
        clock["value"] = clock["value"] + POWER_RETRY_INTERVAL_MS + 1_000
        assert self._simulated_retry_blocked(runtime,self.POWER, room_id=self.ROOM) is False
        assert runtime._power_delivery[self.POWER].attempts == 2

    async def test_exact_cancellation_during_send_keeps_the_sent_at_stamp(
        self,
    ) -> None:
        """AUD-POWER-09 (exact path): a task cancelled DURING the
        executor call keeps the sent_at stamp — the spacing still
        applies."""
        clock = {"value": _NOW_MS + 60_000}
        runtime, hass = self._runtime(clock)
        runtime._electrical_breaker_resolver = lambda _entity: False
        self._confirmed_presence(runtime, _NOW_MS - 20_000)
        self._latch(runtime, _NOW_MS - 9_000)
        # SEC-02: a retry needs the plan action and a LIVE confirmed
        # presence — seed the room config (sensors) and the sensor state.
        runtime._configs = {  # type: ignore[attr-defined]
            config_from_payload(_config_payload()).room_id: config_from_payload(
                _config_payload()
            )
        }
        hass.states.set(  # type: ignore[attr-defined]
            "binary_sensor.demo_presence", "on", last_changed=_dt(_NOW_MS)
        )
        started = asyncio.Event()

        class _SlowUnconfirmedExecutor(_SpyExecutor):
            async def async_power_on(
                self, hass: object, entity_id: str, **kwargs: object
            ) -> dict[str, object]:
                started.set()
                await asyncio.sleep(5)
                return {"confirmed": False, "sent": True, "sendOutcome": "sent"}

        runtime._executor = _SlowUnconfirmedExecutor()
        task = asyncio.ensure_future(
            runtime._ensure_power_source_locked(
                DevicePowerDependency(
                    dependent_entity_id="light.demo_main",
                    power_source_entity_id=self.POWER,
                    policy="auto_turn_on",
                    warmup_seconds=0,
                ),
                at=clock["value"],
                room_id=self.ROOM,
                operation_id="op-exact-cancelled-send",
                config_version=1,
                command_action="turn_on",
            )
        )
        await self._cancel_when_started(task, started)
        # The sent_at stamp (NOW at cancellation time) is kept.
        stamped_at = runtime._power_failure_at[self.POWER]
        assert stamped_at == clock["value"]
        assert self._simulated_retry_blocked(runtime,self.POWER, room_id=self.ROOM) is True
        # After the spacing from the sent moment: the bounded retry runs.
        clock["value"] = clock["value"] + POWER_RETRY_INTERVAL_MS + 1_000
        assert self._simulated_retry_blocked(runtime,self.POWER, room_id=self.ROOM) is False

    def test_not_sent_closure_plus_untrusted_revision_blocks(self) -> None:
        """AUD-POWER-08 (companion): after a not-sent closure the live
        episode remains; if the relay then goes unavailable (untrusted
        revision), the attempt stays suppressed — the loss of evidence
        must not refresh the budget."""
        clock = {"value": _NOW_MS + 60_000}
        runtime, hass = self._runtime(clock)
        self._confirmed_presence(runtime, _NOW_MS - 20_000)
        # An open episode from an earlier unconfirmed delivery.
        revision = runtime._power_evidence_revision(self.POWER)
        runtime._power_delivery[self.POWER] = PowerDeliveryEpisode(
            revision=revision,
            presence_since=_NOW_MS - 20_000,
            attempts=1,
            last_attempt_ms=clock["value"] - POWER_RETRY_INTERVAL_MS - 1_000,
        )
        # The not-sent closure popped the failure record (the episode
        # remains).
        runtime._power_failures.pop(self.POWER, None)
        # The relay goes unavailable: the revision turns untrusted.
        hass.states.set(self.POWER, "unknown", last_changed=_dt(clock["value"]))
        assert self._simulated_retry_blocked(runtime,self.POWER, room_id=self.ROOM) is True

    async def test_exact_warmup_cancellation_keeps_the_sent_at_stamp(
        self,
    ) -> None:
        """AUD-POWER-10: a task cancelled DURING the warm-up wait (after
        the confirmed receipt) keeps the sent_at stamp — the spacing
        still applies."""
        clock = {"value": _NOW_MS + 60_000}
        runtime, hass = self._runtime(clock)
        runtime._electrical_breaker_resolver = lambda _entity: False
        self._confirmed_presence(runtime, _NOW_MS - 20_000)
        self._latch(runtime, _NOW_MS - 9_000)
        # SEC-02: a retry needs the plan action and a LIVE confirmed
        # presence — seed the room config (sensors) and the sensor state.
        runtime._configs = {  # type: ignore[attr-defined]
            config_from_payload(_config_payload()).room_id: config_from_payload(
                _config_payload()
            )
        }
        hass.states.set(
            "binary_sensor.demo_presence", "on", last_changed=_dt(_NOW_MS)
        )

        started = asyncio.Event()

        class _ConfirmThenWarmupExecutor(_SpyExecutor):
            async def async_power_on(
                self, hass: object, entity_id: str, **kwargs: object
            ) -> dict[str, object]:
                started.set()
                return {"confirmed": True, "sent": True, "sendOutcome": "sent"}

        runtime._executor = _ConfirmThenWarmupExecutor()
        task = asyncio.ensure_future(
            runtime._ensure_power_source_locked(
                DevicePowerDependency(
                    dependent_entity_id="light.demo_main",
                    power_source_entity_id=self.POWER,
                    policy="auto_turn_on",
                    warmup_seconds=5,
                ),
                at=clock["value"],
                room_id=self.ROOM,
                operation_id="op-warmup-cancelled",
                config_version=1,
                command_action="turn_on",
            )
        )
        await self._cancel_when_started(task, started)
        # The sent_at stamp (NOW at cancellation time) is kept: the
        # spacing still applies.
        stamped_at = runtime._power_failure_at[self.POWER]
        assert stamped_at == clock["value"]
        assert self._simulated_retry_blocked(runtime,self.POWER, room_id=self.ROOM) is True

    async def test_legacy_cancellation_during_send_keeps_the_sent_at_stamp(
        self,
    ) -> None:
        """AUD-POWER-09 (legacy path): a task cancelled DURING the
        executor call keeps the sent_at stamp — the spacing still
        applies."""
        clock = {"value": _NOW_MS + 60_000}
        runtime, hass = self._runtime(clock)
        self._confirmed_presence(runtime, _NOW_MS - 20_000)
        self._latch(runtime, _NOW_MS - 9_000)
        # SEC-02: a retry needs the plan action and a LIVE confirmed
        # presence — seed the room config (sensors) and the sensor state.
        runtime._configs = {  # type: ignore[attr-defined]
            config_from_payload(_config_payload()).room_id: config_from_payload(
                _config_payload()
            )
        }
        hass.states.set(
            "binary_sensor.demo_presence", "on", last_changed=_dt(_NOW_MS)
        )
        started = asyncio.Event()

        class _SlowUnconfirmedExecutor(_SpyExecutor):
            async def async_power_on(
                self, hass: object, entity_id: str, **kwargs: object
            ) -> dict[str, object]:
                started.set()
                await asyncio.sleep(5)
                return {"confirmed": False, "sent": True, "sendOutcome": "sent"}

        runtime._executor = _SlowUnconfirmedExecutor()
        task = asyncio.ensure_future(
            runtime._ensure_legacy_room_power_locked(
                self.POWER,
                at=clock["value"],
                room_id=self.ROOM,
                operation_id="op-legacy-cancelled-send",
                config_version=1,
                command_action="turn_on",
            )
        )
        await self._cancel_when_started(task, started)
        # The sent_at stamp (NOW at cancellation time) is kept.
        stamped_at = runtime._power_failure_at[self.POWER]
        assert stamped_at == clock["value"]
        assert self._simulated_retry_blocked(runtime,self.POWER, room_id=self.ROOM) is True
        # After the spacing from the sent moment: the bounded retry runs.
        clock["value"] = clock["value"] + POWER_RETRY_INTERVAL_MS + 1_000
        assert self._simulated_retry_blocked(runtime,self.POWER, room_id=self.ROOM) is False

    async def test_not_sent_closure_restart_persistence(self) -> None:
        """AUD-POWER-PERSIST-01 (end-to-end): pending → not_sent closure →
        shadow store → NEW runtime restore → the latch is properly
        cleared for a room without a delivery episode (legacy behavior),
        and the away intent is properly closed."""
        from custom_components.hausman_hub.application.room_lighting_shadow import (
            RoomLightingShadowService,
        )

        shadow_store = _MemoryShadowStore()
        shadow = RoomLightingShadowService(shadow_store)

        clock = {"value": _NOW_MS + 60_000}
        runtime, _hass = self._runtime(clock)
        runtime._shadow = shadow
        self._confirmed_presence(runtime, _NOW_MS - 20_000)

        revision = runtime._power_evidence_revision(self.POWER)

        # Step 1: persist the pending dispatch.
        persisted = await runtime._persist_pending_power_dispatch(
            at=_NOW_MS,
            room_id=self.ROOM,
            source_entity_id=self.POWER,
            evidence_revision=revision,
            operation_id="op-persist-test",
            config_version=1,
        )
        assert persisted is True

        # Step 2: not-sent closure — the attempt was blocked (away, etc.)
        # so no actual send happened. This is a room WITHOUT a delivery
        # episode, so the latch is cleared (legacy behavior).
        closed = await runtime._persist_not_sent_power_dispatch_closure(
            at=_NOW_MS + 1_000,
            room_id=self.ROOM,
            source_entity_id=self.POWER,
            evidence_revision=revision,
            reason="away_mode",
            operation_id="op-persist-test",
            config_version=1,
        )
        assert closed is True
        assert self.POWER not in runtime._power_failures

        # Step 3: create a NEW runtime from the SAME shadow store and
        # restore — simulating a restart.
        runtime2 = RoomLightingRuntime(
            _FakeHass(),
            _ConfigService(config_from_payload(_config_payload())),
            shadow,
            executor=_SpyExecutor(),
            now_ms=lambda: clock["value"],
            track_state_changes=lambda hass, entities, callback: (lambda: None),
            track_interval=lambda hass, callback, interval: (lambda: None),
            listen_bus=lambda hass, event_type, callback: (lambda: None),
            away_entity_id=AWAY_ENTITY_ID,
        )
        runtime2._restore_dispatch_failures()

        # Step 4: for a room without a delivery episode, the latch is
        # properly cleared after restart (legacy behavior).
        assert self.POWER not in runtime2._power_failures

    async def test_shared_not_sent_closure_keeps_open_failure(self) -> None:
        """AUD-POWER-PERSIST-01 (real closure path): calling the not-sent
        closure with an open delivery episode does NOT clear the failure
        record."""
        clock = {"value": _NOW_MS + 60_000}
        runtime, _hass = self._runtime(clock)
        self._confirmed_presence(runtime, _NOW_MS - 20_000)
        revision = runtime._power_evidence_revision(self.POWER)
        runtime._power_failures[self.POWER] = ("turn_on", revision)
        runtime._power_delivery[self.POWER] = PowerDeliveryEpisode(
            revision=revision,
            presence_since=_NOW_MS - 20_000,
            attempts=1,
            last_attempt_ms=clock["value"] - POWER_RETRY_INTERVAL_MS - 1_000,
        )
        closed = await runtime._persist_not_sent_power_dispatch_closure(
            at=clock["value"],
            room_id=self.ROOM,
            source_entity_id=self.POWER,
            evidence_revision=revision,
            reason="away_mode",
            operation_id="op-test",
            config_version=1,
        )
        assert closed is True
        # The failure record SURVIVES the not-sent closure because the
        # delivery episode is still open.
        assert self.POWER in runtime._power_failures

    async def test_not_sent_closure_restart_persistence_end_to_end(self) -> None:
        """A refused retry preserves the original boundary across a fresh load."""
        clock = {"value": _NOW_MS + 60_000}
        runtime, hass = self._runtime(clock)
        self._confirmed_presence(runtime, _NOW_MS - 20_000)
        self._latch(runtime, _NOW_MS - 9_000)
        revision = runtime._power_evidence_revision(self.POWER)
        assert await runtime._persist_pending_power_dispatch(
            at=clock["value"], room_id=self.ROOM, source_entity_id=self.POWER,
            evidence_revision=revision, operation_id="op-persist-e2e", config_version=1,
        )
        assert await runtime._persist_not_sent_power_dispatch_closure(
            at=clock["value"], room_id=self.ROOM, source_entity_id=self.POWER,
            evidence_revision=revision, reason="away_mode",
            operation_id="op-persist-e2e", config_version=1,
        )
        shadow = RoomLightingShadowService(runtime._shadow._store)
        await shadow.async_load()
        restored = _make_runtime(hass, commands_enabled=True, now_ms=lambda: clock["value"])
        restored._shadow = shadow
        restored._configs = dict(runtime._configs)
        self._confirmed_presence(restored, _NOW_MS - 20_000)
        restored._restore_dispatch_failures()
        assert restored._power_attempt_blocked(
            self.POWER, room_id=self.ROOM, command_action="turn_on"
        ) is True
        assert restored._power_delivery[self.POWER].exhausted
        assert shadow.journal_payload()["dispatchFailures"][0]["blockedAfterMs"] == _NOW_MS - 9_000
        hass.states.set(self.POWER, "off", last_changed=_dt(clock["value"] + 1))
        assert restored._power_attempt_blocked(
            self.POWER, room_id=self.ROOM, command_action="turn_on"
        ) is False

    async def test_cancellation_during_warmup_keeps_latch_and_episode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A task cancelled DURING the warm-up sleep must leave BOTH the
        latch and the episode intact (luna round 3, MEDIUM): the limits
        keep applying after the cancellation."""
        clock = {"value": _NOW_MS + 60_000}
        runtime, _hass = self._runtime(clock)
        runtime._electrical_breaker_resolver = lambda _entity: False
        self._confirmed_presence(runtime, _NOW_MS - 20_000)
        self._latch(runtime, _NOW_MS)

        class _InstantConfirmExecutor(_SpyExecutor):
            async def async_power_on(
                self, hass: object, entity_id: str, **kwargs: object
            ) -> dict[str, object]:
                return {"confirmed": True, "sent": True, "sendOutcome": "sent"}

        runtime._executor = _InstantConfirmExecutor()

        # Track the entry into the ENGINE warm-up sleep (not the executor):
        # the cancellation must happen inside that specific wait.
        import custom_components.hausman_hub.application.room_lighting_runtime as runtime_module

        warmup_started = asyncio.Event()
        real_sleep = asyncio.sleep

        async def tracked_sleep(delay: object, *args: object, **kwargs: object):
            if delay == 5:
                warmup_started.set()
            return await real_sleep(delay, *args, **kwargs)

        monkeypatch.setattr(runtime_module.asyncio, "sleep", tracked_sleep)

        task = asyncio.ensure_future(
            runtime._ensure_power_source_locked(
                DevicePowerDependency(
                    dependent_entity_id="light.demo_main",
                    power_source_entity_id=self.POWER,
                    policy="auto_turn_on",
                    warmup_seconds=5,
                ),
                at=clock["value"],
                room_id=self.ROOM,
                operation_id="op-cancelled",
                config_version=1,
                command_action="turn_on",
            )
        )
        await self._cancel_when_started(task, warmup_started)
        monkeypatch.undo()
        # The cleanup line was never reached: the latch AND the episode
        # survive the cancellation, so the episode limits keep applying.
        assert self.POWER in runtime._power_failures
        assert runtime._power_delivery[self.POWER].attempts == 1
        assert not runtime._power_delivery[self.POWER].exhausted
        # A later retry is still a normal spaced episode attempt (the
        # spacing from the original attempt has elapsed by now).
        clock["value"] = clock["value"] + POWER_RETRY_INTERVAL_MS + 1_000
        assert self._simulated_retry_blocked(runtime,self.POWER, room_id=self.ROOM) is False
        assert runtime._power_delivery[self.POWER].attempts == 2
