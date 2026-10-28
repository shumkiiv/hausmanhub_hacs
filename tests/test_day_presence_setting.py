"""Approved warmer daytime and room-local, deadline-driven presence."""
import json
import asyncio
from datetime import time
from dataclasses import replace
from pathlib import Path
import pytest
from jsonschema import Draft202012Validator, ValidationError
from custom_components.hausman_hub.domain.room_lighting import config_from_payload, RoomLightingViolation
from custom_components.hausman_hub.domain.room_lighting_engine import curve_profile_from_config, RoomLightingContext, SensorSnapshot, SensorKind
from custom_components.hausman_hub.domain.room_lighting_ownership import SensorState
from custom_components.hausman_hub.domain.light_curve import evaluate_light_curve
from custom_components.hausman_hub.application.room_lighting_color import reflect_inverted_kelvin
from tests.test_room_lighting_curve_params_runtime import _day_curve_config, FULL_CURVE, _context, _decision
from tests.test_light_curve import _ctx
from tests.test_room_lighting_runtime import _make_runtime, _FakeHass, _curve_payload, _NOW_MS, _TZ


@pytest.mark.parametrize("base", [{"eveningMaxPercent": 40}, FULL_CURVE])
def test_new_colour_all_day_preserves_non_colour_behaviour(base):
    old = _day_curve_config({**base, "neutralKelvin": 3534, "eveningWarmKelvin": 3003})
    new = replace(old, curve=replace(old.curve, neutral_kelvin=3278, presence_confirm_seconds=3))
    schema = json.loads(Path("contract/schemas/v1/room-lighting-config.schema.json").read_text())
    Draft202012Validator(schema).validate(new.to_dict())
    assert config_from_payload(new.to_dict()) == new
    assert new.curve.has_new_form == old.curve.has_new_form
    profile = curve_profile_from_config(new)
    assert (profile.warm_kelvin, profile.neutral_kelvin, profile.evening_kelvin, profile.presence_confirm_seconds) == (3400, 3278, 3003, 3)
    assert reflect_inverted_kelvin(3278, 2000, 6535) == 2874
    for minute in range(1440):
        if old.curve.has_new_form:
            before, after = _decision(old, _context(minute / 60)), _decision(new, _context(minute / 60))
        else:
            ctx = _ctx(minute // 60, minute % 60)
            before, after = evaluate_light_curve(curve_profile_from_config(old), ctx), evaluate_light_curve(profile, ctx)
        assert replace(after, color_temperature=before.color_temperature) == before
    assert evaluate_light_curve(profile, _ctx(12, 0)).color_temperature == 3278


@pytest.mark.parametrize("value", [None, True, False, 0, 16, 3.5, "3"])
def test_bad_presence_setting_rejected_by_contract_and_domain(value):
    payload = _day_curve_config({}).to_dict()
    payload["curveParams"] = {"presenceConfirmSeconds": value}
    with pytest.raises(RoomLightingViolation):
        config_from_payload(payload)
    schema = json.loads(Path("contract/schemas/v1/room-lighting-config.schema.json").read_text())
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(payload)


def test_day_requires_a_warmer_evening():
    for evening in (None, 3278, 3300):
        params = {"neutralKelvin": 3278}
        if evening is not None:
            params["eveningWarmKelvin"] = evening
        with pytest.raises(RoomLightingViolation):
            _day_curve_config(params)


def presence_context(moment, state=SensorState.ON, started=_NOW_MS):
    return RoomLightingContext(now=moment, timezone=_TZ, sunrise=time(7), sunset=time(19),
        sensors=(SensorSnapshot("sensor_demo_presence", SensorKind.PRESENCE, state, started),))


async def test_presence_threshold_deadline_and_unknown_reset(monkeypatch):
    payload = _curve_payload(commands_enabled=False)
    payload["curveParams"] = {"presenceConfirmSeconds": 3}
    config = config_from_payload(payload)
    now = [_NOW_MS]
    runtime = _make_runtime(_FakeHass(), payload=payload, now_ms=lambda: now[0])
    tasks = []
    monkeypatch.setattr(runtime, "_create_task", lambda coro: tasks.append(coro))
    assert runtime._observe_curve_presence(config, presence_context(now[0]), now[0])[0] is False
    assert len(tasks) == 1
    now[0] += 2999
    assert runtime._observe_curve_presence(config, presence_context(now[0]), now[0])[0] is False
    assert len(tasks) == 1
    now[0] += 1
    assert runtime._observe_curve_presence(config, presence_context(now[0]), now[0])[0] is True
    processed = []
    async def process(rooms): processed.append(rooms)
    sleeps = []
    async def sleep(delay): sleeps.append(delay)
    monkeypatch.setattr(runtime, "async_process", process)
    monkeypatch.setattr(asyncio, "sleep", sleep)
    runtime._running = True
    await tasks.pop()
    assert processed == [(config.room_id,)]
    now[0] += 1000
    runtime._observe_curve_presence(config, presence_context(now[0], SensorState.UNKNOWN), now[0])
    assert runtime._curve_presence[config.room_id]["since"] is None
    now[0] += 1000
    assert runtime._observe_curve_presence(config, presence_context(now[0], started=now[0]), now[0])[0] is False
    runtime._observe_curve_presence(config, presence_context(now[0], SensorState.OFF), now[0])
    await tasks.pop()
    assert len(processed) == 1
    assert sleeps == [0, 3]
    assert runtime._presence_confirm_ms(config_from_payload(_curve_payload(commands_enabled=False))) == 15000


async def test_storage_and_old_client_atomic_preservation():
    from custom_components.hausman_hub.application.room_lighting_service import RoomLightingService
    from custom_components.hausman_hub.application.room_lighting_storage import HomeAssistantRoomLightingStore
    from tests.test_room_lighting_service import _MemoryStore
    initial = _day_curve_config({"neutralKelvin": 3278, "eveningWarmKelvin": 3003, "presenceConfirmSeconds": 3})
    class Backend:
        document = None
        async def async_load(self): return self.document
        async def async_save(self, value): self.document = json.loads(json.dumps(value))
    backend = Backend()
    storage = HomeAssistantRoomLightingStore.__new__(HomeAssistantRoomLightingStore)
    storage._store = backend
    await storage.async_upsert(initial)
    restarted = HomeAssistantRoomLightingStore.__new__(HomeAssistantRoomLightingStore)
    restarted._store = backend
    assert await restarted.async_get(initial.room_id) == initial
    store = _MemoryStore()
    await store.async_upsert(initial)
    service = RoomLightingService(store)
    old_client = replace(initial, curve=replace(initial.curve, neutral_kelvin=None, evening_warm_kelvin=None, presence_confirm_seconds=None))
    saved = await service.async_put_config(old_client, expected_version=initial.version,
        preserve_curve_neutral_kelvin=True, preserve_curve_evening_warm_kelvin=True, preserve_curve_presence_confirm_seconds=True)
    assert saved.curve == initial.curve
