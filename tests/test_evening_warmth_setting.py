"""Independent evening endpoint, with unchanged daylight, morning and brightness."""
from dataclasses import replace
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from custom_components.hausman_hub.domain.room_lighting import config_from_payload, RoomLightingViolation
from custom_components.hausman_hub.domain.room_lighting_engine import curve_profile_from_config, curve_params_block
from custom_components.hausman_hub.domain.light_curve import evaluate_light_curve
from tests.test_room_lighting_curve_params_runtime import _payload, _day_curve_config, _context, _decision, FULL_CURVE
from tests.test_light_curve import _ctx


def validator():
    schema = json.loads(Path('contract/schemas/v1/room-lighting-config.schema.json').read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@pytest.mark.parametrize('base', [{}, {'eveningMaxPercent':40}, FULL_CURVE])
def test_roundtrip_and_legacy_algorithm_selection(base):
    old = _day_curve_config({**base, 'neutralKelvin':3534})
    new = _day_curve_config({**base, 'neutralKelvin':3534, 'eveningWarmKelvin':3200})
    validator().validate(new.to_dict())
    assert new.curve.has_new_form == old.curve.has_new_form
    assert curve_params_block(old) == curve_params_block(new)
    assert config_from_payload(new.to_dict()) == new
    assert curve_profile_from_config(new) == replace(curve_profile_from_config(old), evening_warm_kelvin=3200)


@pytest.mark.parametrize('value', [None, True, False, 3200.5, '3200', 1499, 3401])
def test_bad_evening_values_rejected(value):
    payload = {**_payload(), 'curveParams':{'eveningWarmKelvin':value}}
    with pytest.raises(RoomLightingViolation):
        config_from_payload(payload)
    with pytest.raises(ValidationError):
        validator().validate(payload)


@pytest.mark.parametrize('value', [1500, 3200, 3400])
def test_valid_evening_values(value):
    payload = {**_payload(), 'curveParams':{'eveningWarmKelvin':value}}
    validator().validate(payload)
    assert config_from_payload(payload).curve.evening_warm_kelvin == value


@pytest.mark.parametrize('new_form', [False, True])
def test_entire_day_keeps_brightness_and_morning_unchanged(new_form):
    base = dict(FULL_CURVE) if new_form else {'eveningMaxPercent':40}
    old = _day_curve_config({**base, 'neutralKelvin':3534})
    new = _day_curve_config({**base, 'neutralKelvin':3534, 'eveningWarmKelvin':3200})
    old_profile, profile = curve_profile_from_config(old), curve_profile_from_config(new)
    changed = []
    for minute in range(1440):
        if new_form:
            before, after = _decision(old, _context(minute/60)), _decision(new, _context(minute/60))
        else:
            ctx = _ctx(minute//60, minute%60)
            before, after = evaluate_light_curve(old_profile, ctx), evaluate_light_curve(profile, ctx)
        assert replace(after, color_temperature=before.color_temperature) == before
        if minute < 17*60:
            assert after.color_temperature == before.color_temperature
        if after.color_temperature != before.color_temperature:
            changed.append(after.color_temperature)
            assert 3200 <= after.color_temperature <= 3534
    assert changed and 3200 in changed
    assert profile.warm_kelvin == 3400
    assert profile.neutral_kelvin == 3534
    assert curve_profile_from_config(_day_curve_config(None)).evening_kelvin == 3400


async def test_storage_roundtrip_and_old_client_omission():
    from custom_components.hausman_hub.application.room_lighting_storage import HomeAssistantRoomLightingStore
    from custom_components.hausman_hub.application.room_lighting_service import RoomLightingService, RoomLightingRevisionConflictError
    from tests.test_room_lighting_service import _MemoryStore
    initial = _day_curve_config({'eveningMaxPercent':40,'neutralKelvin':3534,'eveningWarmKelvin':3200})
    class Backend:
        document = None
        async def async_load(self): return self.document
        async def async_save(self, value): self.document = json.loads(json.dumps(value))
    backend = Backend()
    original = HomeAssistantRoomLightingStore.__new__(HomeAssistantRoomLightingStore)
    original._store = backend
    await original.async_upsert(initial)
    restarted = HomeAssistantRoomLightingStore.__new__(HomeAssistantRoomLightingStore)
    restarted._store = backend
    assert await restarted.async_get(initial.room_id) == initial
    store = _MemoryStore()
    await store.async_upsert(initial)
    service = RoomLightingService(store)
    old_client = replace(initial, curve=replace(initial.curve, neutral_kelvin=None, evening_warm_kelvin=None), name='New name')
    saved = await service.async_put_config(old_client, expected_version=initial.version,
        preserve_curve_neutral_kelvin=True, preserve_curve_evening_warm_kelvin=True)
    assert saved.curve == initial.curve
    with pytest.raises(RoomLightingRevisionConflictError):
        await service.async_put_config(old_client, expected_version=initial.version,
            preserve_curve_evening_warm_kelvin=True)
    reset = replace(saved, curve=replace(saved.curve, evening_warm_kelvin=3400))
    saved = await service.async_put_config(reset, expected_version=saved.version)
    assert saved.curve.evening_warm_kelvin == 3400
    assert saved.curve.neutral_kelvin == 3534


def test_approved_reference_fixture():
    from custom_components.hausman_hub.application.room_lighting_color import reflect_inverted_kelvin
    fixture = json.loads(Path('contract/fixtures/room-lighting-evening-colour.json').read_text())
    config = _day_curve_config(fixture['curveParams'])
    validator().validate(config.to_dict())
    profile = curve_profile_from_config(config)
    assert profile.evening_kelvin == 3200
    raw_day = reflect_inverted_kelvin(profile.neutral_kelvin,2000,6535)
    assert 1_000_000 // raw_day == fixture['daytimeReferenceMired'] == 370
    assert fixture['physicalColourAccepted'] is False
