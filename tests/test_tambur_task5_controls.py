"""Regression tests for the bounded Tambur Task 5 controls."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.hausman_hub.application.manual_light_off_protection import (
    ManualLightOffProtectionCoordinator,
)
from custom_components.hausman_hub.application.scenario_executor import ScenarioExecutor
from custom_components.hausman_hub.application.scenarios import (
    ScenarioCatalog,
    ScenarioDeviceAction,
    ScenarioDeviceEntry,
)
from custom_components.hausman_hub.application.smart_switch_runtime import (
    MARMITEK_TRIGGER_CONFIGS,
    PASS_THROUGH_TRIGGER_CONFIGS,
    SmartSwitchTriggerAdapter,
    valid_smart_switch_dedup_payload,
)


class _MemoryStore:
    def __init__(self) -> None:
        self.payload: object | None = None
        self.recovered_previous = False

    async def async_load(self) -> object | None:
        return self.payload

    async def async_save(self, payload: dict[str, object]) -> None:
        self.payload = payload


def _state(
    value: str,
    observed: datetime,
    *,
    attributes: dict[str, object] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        state=value,
        attributes=attributes or {},
        last_changed=observed,
        last_updated=observed,
        last_reported=observed,
    )


@pytest.mark.asyncio
async def test_marmitek_scope_attaches_confirmed_left_and_right_gestures() -> None:
    requested: list[str] = []
    attached: list[tuple[dict[str, object], dict[str, object]]] = []

    async def get_triggers(_hass: object, device_id: str) -> list[dict[str, object]]:
        requested.append(device_id)
        configs = (
            PASS_THROUGH_TRIGGER_CONFIGS
            if device_id == PASS_THROUGH_TRIGGER_CONFIGS[0]["device_id"]
            else MARMITEK_TRIGGER_CONFIGS
        )
        return [{**item, "metadata": {}} for item in configs]

    async def attach(
        _hass: object,
        config: dict[str, object],
        _action: object,
        info: dict[str, object],
    ) -> object:
        attached.append((config, info))
        return lambda: None

    adapter = SmartSwitchTriggerAdapter(
        SimpleNamespace(),
        SimpleNamespace(async_run_typed_intent=lambda **_item: None),
        trigger_api=SimpleNamespace(
            async_get_triggers=get_triggers,
            async_attach_trigger=attach,
        ),
        state_store=_MemoryStore(),
        included_bindings=frozenset(
            {"tambur-light-group", "tambur-mirror-left", "tambur-master-off"}
        ),
    )

    await adapter.async_start()

    assert len(set(requested)) == 2
    assert [item[0]["subtype"] for item in attached] == [
        "on_down",
        "toggle_down",
        "off_up",
        "1_single",
        "1_double",
        "2_single",
        "2_double",
    ]
    assert [item[1]["trigger_data"]["id"] for item in attached[-4:]] == [
        "tambur-mirror-left-1_single",
        "tambur-mirror-left-1_double",
        "tambur-master-off-2_single",
        "tambur-master-off-2_double",
    ]


@pytest.mark.asyncio
async def test_marmitek_aliases_deduplicate_by_semantic_binding() -> None:
    intents: list[dict[str, object]] = []
    dispositions: list[dict[str, object]] = []
    adapter = SmartSwitchTriggerAdapter(
        SimpleNamespace(),
        SimpleNamespace(
            async_run_typed_intent=lambda **item: intents.append(item),
            async_record_typed_intent_disposition=lambda **item: dispositions.append(item),
        ),
        trigger_api=SimpleNamespace(),
        state_store=_MemoryStore(),
        wall_clock=lambda: 100.0,
        receipt_factory=iter(("m.1", "m.2", "m.3", "m.4")).__next__,
    )
    await adapter.async_load_state()

    assert await adapter.async_handle_trigger(MARMITEK_TRIGGER_CONFIGS[0], {})
    assert not await adapter.async_handle_trigger(MARMITEK_TRIGGER_CONFIGS[1], {})
    assert await adapter.async_handle_trigger(MARMITEK_TRIGGER_CONFIGS[2], {})
    assert not await adapter.async_handle_trigger(MARMITEK_TRIGGER_CONFIGS[3], {})

    assert [(item["binding"], item["action"]) for item in intents] == [
        ("tambur-mirror-left", "toggle"),
        ("tambur-master-off", "off"),
    ]
    assert [item["dedup_disposition"] for item in dispositions] == [
        "deduplicated",
        "deduplicated",
    ]
    assert valid_smart_switch_dedup_payload(adapter._store.payload)  # noqa: SLF001


@pytest.mark.asyncio
async def test_master_off_fences_three_lights_then_arms_then_dispatches_without_power() -> None:
    now = datetime.now(timezone.utc)
    target_entities = {
        "entity_71859313239a14e4": "light.tambur_chandelier",
        "entity_cd0098e5ff95da46": "switch.tambur_points",
        "entity_fbdf27871edb89bf": "switch.tambur_mirror",
        "entity_156050daca86aa6c": "binary_sensor.tambur_presence_1",
        "entity_402b26d150a1ef3f": "binary_sensor.tambur_presence_2",
        "entity_10b78187426f8485": "binary_sensor.tambur_motion",
    }
    states = {
        entity_id: _state("off" if entity_id.startswith("binary_sensor") else "on", now)
        for entity_id in target_entities.values()
    }
    order: list[object] = []
    service = _typed_service(target_entities, states)
    service._manual_action_batch_pre_admission = (  # noqa: SLF001
        lambda request_id, actions: order.append(("fence", request_id, actions))
    )

    async def arm(**item: object) -> dict[str, object]:
        order.append(("arm", item))
        return {"confirmed": True}

    async def execute(
        actions: list[dict[str, object]], **options: object
    ) -> list[dict[str, object]]:
        order.append(("dispatch", actions, options))
        return [
            {
                "targetId": item["targetId"],
                "actionId": "turn_off",
                "accepted": True,
                "confirmed": True,
                "status": "confirmed",
            }
            for item in actions
        ]

    service._manual_light_off_protection = SimpleNamespace(  # noqa: SLF001
        async_arm_release_owned_direct_off=arm
    )
    service.async_execute_device_action_batch = execute
    result = await service.async_run_typed_intent(
        binding="tambur-master-off",
        action="off",
        correlation_id="master.1",
        source="manual",
        trigger_id="2_single",
        intent_receipt_id="master.1",
        raw_subtype="2_single",
        dedup_disposition="accepted",
    )

    assert [item[0] for item in order] == ["fence", "arm", "dispatch"]
    fenced = order[0][2]
    assert {item["targetId"] for item in fenced} == {
        "entity_71859313239a14e4",
        "entity_cd0098e5ff95da46",
        "entity_fbdf27871edb89bf",
    }
    armed = order[1][1]
    assert set(armed["light_entity_ids"]) == {
        "light.tambur_chandelier",
        "switch.tambur_points",
        "switch.tambur_mirror",
    }
    assert set(armed["presence_sensor_entity_ids"]) == {
        "binary_sensor.tambur_presence_1",
        "binary_sensor.tambur_presence_2",
        "binary_sensor.tambur_motion",
    }
    assert all(item["targetId"] != "entity_b47991988cc6b9f3" for item in order[2][1])
    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_mirror_left_requires_fresh_state_and_dispatches_resolved_manual_action() -> None:
    now = datetime.now(timezone.utc)
    target_entities = {"entity_fbdf27871edb89bf": "switch.tambur_mirror"}
    service = _typed_service(
        target_entities,
        {"switch.tambur_mirror": _state("on", now)},
    )
    fenced: list[object] = []
    service._manual_action_batch_pre_admission = (  # noqa: SLF001
        lambda request_id, actions: fenced.append((request_id, actions))
    )
    service.async_execute_device_action_batch = AsyncMock(
        return_value=[
            {
                "targetId": "entity_fbdf27871edb89bf",
                "actionId": "turn_off",
                "accepted": True,
                "confirmed": True,
                "status": "confirmed",
            }
        ]
    )

    result = await service.async_run_typed_intent(
        binding="tambur-mirror-left",
        action="toggle",
        correlation_id="mirror.1",
        source="manual",
        trigger_id="1_double",
        intent_receipt_id="mirror.1",
        raw_subtype="1_double",
        dedup_disposition="accepted",
    )

    assert fenced[0][1] == (
        {
            "targetId": "entity_fbdf27871edb89bf",
            "actionId": "turn_off",
            "value": None,
        },
    )
    assert result["status"] == "completed"


def _typed_service(
    target_entities: dict[str, str], states: dict[str, object]
) -> object:
    from custom_components.hausman_hub.application.scenario_service import ScenarioService

    service = ScenarioService.__new__(ScenarioService)
    service._catalog = SimpleNamespace(  # noqa: SLF001
        device=lambda target_id: (
            SimpleNamespace(entity_id=target_entities[target_id])
            if target_id in target_entities
            else None
        )
    )
    service._hass = SimpleNamespace(states=SimpleNamespace(get=states.get))  # noqa: SLF001
    service._manual_light_off_protection = None  # noqa: SLF001
    service._smart_switch_receipt_consumer = SimpleNamespace(  # noqa: SLF001
        async_consume_intent_receipt=lambda **_item: True
    )
    service._operation_journal = None  # noqa: SLF001
    return service


@pytest.mark.asyncio
async def test_direct_off_requires_600_seconds_and_30_seconds_fresh_absence() -> None:
    now = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)
    coordinator = ManualLightOffProtectionCoordinator(_MemoryStore(), now=lambda: now)
    await coordinator.async_load()
    lights = (
        "light.tambur_chandelier",
        "switch.tambur_points",
        "switch.tambur_mirror",
    )
    sensors = (
        "binary_sensor.tambur_presence_1",
        "binary_sensor.tambur_presence_2",
        "binary_sensor.tambur_motion",
    )
    states = {sensor: _state("on", now) for sensor in sensors}
    await coordinator.async_arm_release_owned_direct_off(
        request_id="manual.600.30",
        light_entity_ids=lights,
        presence_sensor_entity_ids=sensors,
        sensor_states=states,
    )

    now += timedelta(seconds=600)
    for sensor in sensors:
        fresh_off = _state("off", now)
        await coordinator.async_note_state_transition(
            sensor, states[sensor], fresh_off, None
        )
        states[sensor] = fresh_off
    assert not (
        await coordinator.async_decide_entity(lights[0], automatic=True, dry_run=False)
    ).allowed
    now += timedelta(seconds=29)
    assert not (
        await coordinator.async_decide_entity(lights[1], automatic=True, dry_run=False)
    ).allowed
    now += timedelta(seconds=1)
    assert (
        await coordinator.async_decide_entity(lights[2], automatic=True, dry_run=False)
    ).allowed


@pytest.mark.asyncio
async def test_new_direct_off_extends_but_same_request_replays_without_extension() -> None:
    now = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)
    store = _MemoryStore()
    coordinator = ManualLightOffProtectionCoordinator(store, now=lambda: now)
    await coordinator.async_load()
    arguments = {
        "light_entity_ids": ("light.a", "light.b", "light.c"),
        "presence_sensor_entity_ids": ("binary_sensor.a", "binary_sensor.b", "binary_sensor.c"),
        "sensor_states": {},
    }
    first = await coordinator.async_arm_release_owned_direct_off(
        request_id="manual.extend.1", **arguments
    )
    first_deadline = store.payload["protections"][0]["notBefore"]
    now += timedelta(seconds=100)
    duplicate = await coordinator.async_arm_release_owned_direct_off(
        request_id="manual.extend.1", **arguments
    )
    assert duplicate == first
    assert store.payload["protections"][0]["notBefore"] == first_deadline

    await coordinator.async_arm_release_owned_direct_off(
        request_id="manual.extend.2", **arguments
    )
    assert store.payload["protections"][0]["notBefore"] == "2026-09-10T12:11:40Z"


@pytest.mark.asyncio
async def test_new_direct_off_never_shortens_a_longer_frozen_deadline() -> None:
    now = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)
    duration = 900
    store = _MemoryStore()
    coordinator = ManualLightOffProtectionCoordinator(store, now=lambda: now)
    coordinator.set_release_owned_block_seconds_provider(lambda: duration)
    await coordinator.async_load()
    arguments = {
        "light_entity_ids": ("light.a", "light.b", "light.c"),
        "presence_sensor_entity_ids": (
            "binary_sensor.a",
            "binary_sensor.b",
            "binary_sensor.c",
        ),
        "sensor_states": {},
    }
    await coordinator.async_arm_release_owned_direct_off(
        request_id="manual.long.1", **arguments
    )
    assert store.payload["protections"][0]["notBefore"] == "2026-09-10T12:15:00Z"

    now += timedelta(seconds=100)
    duration = 600
    await coordinator.async_arm_release_owned_direct_off(
        request_id="manual.long.2", **arguments
    )

    assert store.payload["protections"][0]["notBefore"] == "2026-09-10T12:15:00Z"


@pytest.mark.asyncio
async def test_zero_brightness_dispatches_turn_off_and_confirms_off() -> None:
    observed = datetime.now(timezone.utc)
    states = {
        "light.test": _state("on", observed, attributes={"brightness": 128})
    }
    services = SimpleNamespace(async_call=AsyncMock())

    async def apply(
        domain: str,
        service: str,
        data: dict[str, object],
        *,
        blocking: bool,
    ) -> None:
        assert (domain, service, data, blocking) == (
            "light",
            "turn_off",
            {"entity_id": "light.test"},
            True,
        )
        states["light.test"] = _state("off", observed + timedelta(seconds=1))

    services.async_call.side_effect = apply
    action = ScenarioDeviceAction(
        "set_brightness_percent",
        "Яркость",
        "light",
        "turn_on",
        frozenset({"value"}),
    )
    executor = ScenarioExecutor(
        SimpleNamespace(states=SimpleNamespace(get=states.get), services=services),
        ScenarioCatalog(
            devices={
                "light-target": ScenarioDeviceEntry(
                    "light-target", "Свет", "light.test", (action,)
                )
            },
            scenarios={},
        ),
        lambda *_args, **_kwargs: None,
        readback_window_seconds=0.02,
        readback_interval_seconds=0.01,
    )

    receipt = await executor.async_execute_device_action(
        "light-target", "set_brightness_percent", 0
    )

    assert receipt["actionId"] == "set_brightness_percent"
    assert receipt["confirmed"] is True
    services.async_call.assert_awaited_once()


@pytest.mark.asyncio
async def test_auto_power_reasserts_fresh_on_and_waits_for_new_report() -> None:
    observed = datetime.now(timezone.utc)
    states = {
        "light.test": _state("off", observed),
        "switch.power": _state("on", observed),
    }
    calls: list[tuple[str, str]] = []

    async def apply(
        domain: str,
        service: str,
        data: dict[str, object],
        *,
        blocking: bool,
    ) -> None:
        assert blocking
        entity_id = str(data["entity_id"])
        calls.append((entity_id, service))
        states[entity_id] = _state(
            "on",
            datetime.now(timezone.utc),
            attributes={"brightness": 102} if entity_id == "light.test" else {},
        )

    action = ScenarioDeviceAction(
        "set_brightness_percent", "Яркость", "light", "turn_on", frozenset({"value"})
    )
    executor = ScenarioExecutor(
        SimpleNamespace(
            states=SimpleNamespace(get=states.get),
            services=SimpleNamespace(async_call=apply),
        ),
        ScenarioCatalog(
            devices={
                "light-target": ScenarioDeviceEntry(
                    "light-target", "Свет", "light.test", (action,)
                )
            },
            scenarios={},
        ),
        lambda *_args, **_kwargs: None,
        readback_window_seconds=0.02,
        readback_interval_seconds=0.01,
        power_dependency_resolver=lambda: {
            "light.test": SimpleNamespace(
                power_source_entity_id="switch.power",
                policy="auto_turn_on",
                warmup_seconds=0,
            )
        },
        electrical_breaker_resolver=lambda _entity_id: False,
        command_guard=lambda _entity_id, _action_id, _automatic: None,
    )

    receipt = await executor.async_execute_device_action(
        "light-target", "set_brightness_percent", 40
    )

    assert receipt["confirmed"] is True
    assert calls == [("switch.power", "turn_on"), ("light.test", "turn_on")]
    assert receipt["power_precondition"]["sourceTurnedOn"] is True
    assert receipt["readBack"]["matched"] is True
    assert receipt["readBack"]["isNewEvidence"] is True
    assert isinstance(receipt["readBack"]["evidenceRevision"], str)


def test_ts0502b_template_is_ieee_scoped_and_preserves_base_capabilities() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    module = Path(__file__).parents[1] / "tools/zigbee2mqtt/ts0502b_cct_inversion_template.js"
    script = r"""
const assert = require('node:assert/strict');
const {buildTs0502bCctInversion} = require(process.argv[1]);
const from = {cluster: 'lightingColorCtrl', convert: () => ({color_temp: 153, state: 'ON'})};
const writes = [];
const set = {key: ['state', 'brightness', 'color_temp', 'color_temp_percent', 'effect'], convertSet: (_e, key, value) => {
  writes.push([key, value]);
  if (key === 'color_temp') return {state: {color_temp: value}};
  if (key === 'color_temp_percent') return {state: {color_temp: value === 100 ? 500 : 153}};
  return {state: {[key]: value}};
}};
const step = {key: ['brightness_step', 'color_temp_step'], convertSet: (_e, key, value) => { writes.push([key, value]); return undefined; }};
const move = {key: ['colortemp_move', 'color_temp_move'], convertSet: (_e, key, value) => { writes.push([key, value]); return undefined; }};
const dnd = {key: ['do_not_disturb'], convertSet: (_e, key, value) => ({state: {[key]: value}})};
const exposes = [{name: 'light', features: [{name: 'effect'}, {name: 'do_not_disturb'}]}];
const options = [{name: 'transition'}];
const meta = {applyRedFix: true};
const base = {zigbeeModel: ['TS0502B'], model: 'TS0502B', vendor: 'Tuya', description: 'base', fromZigbee: [from], toZigbee: [set, step, move, dnd], exposes, options, meta, configure: () => 'configured'};
const exact = buildTs0502bCctInversion(base, '0x00124b00000000aa');
assert.deepEqual(exact.fingerprint, [{modelID: 'TS0502B', manufacturerName: '_TZ3210_3wvqjh3q', ieeeAddr: '0x00124b00000000aa'}]);
assert.equal('zigbeeModel' in exact, false);
assert.equal(exact.exposes, exposes);
assert.equal(exact.options, options);
assert.equal(exact.meta, meta);
assert.equal(exact.configure, base.configure);
assert.equal(exact.fromZigbee.length, 1);
assert.equal(exact.toZigbee.length, 4);
assert.deepEqual(exact.toZigbee.map(item => item.key), base.toZigbee.map(item => item.key));
Promise.resolve(exact.fromZigbee[0].convert()).then(async inbound => {
  assert.equal(inbound.color_temp, 500);
  const color = await exact.toZigbee[0].convertSet(null, 'color_temp', 500, {});
  assert.equal(color.state.color_temp, 500);
  assert.deepEqual(writes.pop(), ['color_temp', 153]);
  const percent = await exact.toZigbee[0].convertSet(null, 'color_temp_percent', 0, {});
  assert.equal(percent.state.color_temp, 153);
  assert.deepEqual(writes.pop(), ['color_temp_percent', 100]);
  const effect = await exact.toZigbee[0].convertSet(null, 'effect', 'blink', {});
  assert.equal(effect.state.effect, 'blink');
  const movement = await exact.toZigbee[1].convertSet(null, 'color_temp_step', 25, {});
  assert.equal(movement, undefined);
  assert.deepEqual(writes.pop(), ['color_temp_step', -25]);
  await exact.toZigbee[2].convertSet(null, 'color_temp_move', 'up', {});
  assert.deepEqual(writes.pop(), ['color_temp_move', 'down']);
  await exact.toZigbee[2].convertSet(null, 'colortemp_move', {rate: 12, minimum: 200, maximum: 400}, {});
  assert.deepEqual(writes.pop(), ['colortemp_move', {rate: -12, minimum: 253, maximum: 453}]);
  const quiet = await exact.toZigbee[3].convertSet(null, 'do_not_disturb', true, {});
  assert.equal(quiet.state.do_not_disturb, true);
  assert.throws(() => buildTs0502bCctInversion(base, '0xprivate'), /IEEE/);
}).catch(error => { console.error(error); process.exitCode = 1; });
"""
    completed = subprocess.run(
        [node, "-e", script, str(module)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "0x00124b00000000aa" not in module.read_text(encoding="utf-8")
