"""Room-local daylight endpoint, with unchanged timing and evening endpoint."""

import json
from pathlib import Path
from dataclasses import replace

import pytest
from jsonschema import Draft202012Validator, ValidationError

from custom_components.hausman_hub.domain.room_lighting import config_from_payload, RoomLightingViolation
from custom_components.hausman_hub.domain.room_lighting_engine import curve_profile_from_config, curve_params_block
from custom_components.hausman_hub.domain.light_curve import evaluate_light_curve
from custom_components.hausman_hub.application.room_lighting_color import reflect_inverted_kelvin
from tests.test_room_lighting_curve_params_runtime import _payload, _day_curve_config, _context, _decision, FULL_CURVE
from tests.test_light_curve import _ctx


@pytest.mark.parametrize("curve", [{}, {"eveningMaxPercent": 40}, FULL_CURVE])
def test_neutral_roundtrip_and_engine_selection(curve):
    previous = _day_curve_config(dict(curve))
    configured = _day_curve_config({**curve, "neutralKelvin": 3534})
    assert configured.to_dict()["curveParams"] == {**curve, "neutralKelvin": 3534}
    assert configured.curve.has_new_form == previous.curve.has_new_form
    assert curve_params_block(configured) == curve_params_block(previous)
    assert curve_profile_from_config(configured) == replace(curve_profile_from_config(previous), neutral_kelvin=3534)
    assert curve_profile_from_config(previous).neutral_kelvin == 4000


@pytest.mark.parametrize("value", [True, False, None, 3534.5, "3534", 3400, 0, 6501])
def test_invalid_neutral_is_rejected_by_domain_and_contract(value):
    payload = {**_payload(), "curveParams": {"neutralKelvin": value}}
    with pytest.raises(RoomLightingViolation):
        config_from_payload(payload)
    schema = json.loads(Path("contract/schemas/v1/room-lighting-config.schema.json").read_text())
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(payload)


@pytest.mark.parametrize("value", [3401, 3534, 4000, 6500])
def test_contract_accepts_valid_neutral(value):
    payload = {**_payload(), "curveParams": {"neutralKelvin": value}}
    schema = json.loads(Path("contract/schemas/v1/room-lighting-config.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(payload)
    assert config_from_payload(payload).curve.neutral_kelvin == value


@pytest.mark.parametrize("new_form", [False, True])
def test_full_day_preserves_everything_except_colour(new_form):
    original = _day_curve_config(dict(FULL_CURVE) if new_form else {"eveningMaxPercent": 40})
    configured = replace(original, curve=replace(original.curve, neutral_kelvin=3534))
    old_profile = curve_profile_from_config(original)
    profile = curve_profile_from_config(configured)
    for minute in range(24 * 60):
        if new_form:
            old = _decision(original, _context(minute / 60))
            new = _decision(configured, _context(minute / 60))
        else:
            context = _ctx(minute // 60, minute % 60)
            old = evaluate_light_curve(old_profile, context)
            new = evaluate_light_curve(profile, context)
        assert replace(new, color_temperature=old.color_temperature) == old
        if new.color_temperature is not None:
            assert 3400 <= new.color_temperature <= 3534
    if new_form:
        noon = _decision(configured, _context(12))
        evening = _decision(configured, _context(21.5))
    else:
        noon = evaluate_light_curve(profile, _ctx(12, 0))
        evening = evaluate_light_curve(profile, _ctx(22, 30))
    assert noon.color_temperature == 3534
    assert evening.color_temperature == 3400
    assert reflect_inverted_kelvin(noon.color_temperature, 2000, 6535) == 2702
    assert 1_000_000 // 2702 == 370
    assert reflect_inverted_kelvin(evening.color_temperature, 2000, 6535) == 2786


def test_no_block_retains_default_profile():
    assert curve_profile_from_config(_day_curve_config(None)).neutral_kelvin == 4000


def test_contract_fixture_retains_legacy_form():
    schema = json.loads(Path("contract/schemas/v1/room-lighting-config.schema.json").read_text())
    payload = json.loads(Path("contract/fixtures/room-lighting-config.json").read_text())
    Draft202012Validator(schema).validate(payload)
    config = config_from_payload(payload)
    assert config.curve.neutral_kelvin == 3534
    assert not config.curve.has_new_form


@pytest.mark.asyncio
async def test_actual_storage_codec_survives_reload_without_other_config_changes():
    from custom_components.hausman_hub.application.room_lighting_storage import HomeAssistantRoomLightingStore

    class Backend:
        document = None

        async def async_load(self):
            return self.document

        async def async_save(self, payload):
            self.document = json.loads(json.dumps(payload))

    backend = Backend()
    initial = _day_curve_config({"eveningMaxPercent": 40, "neutralKelvin": 3534})
    original = HomeAssistantRoomLightingStore.__new__(HomeAssistantRoomLightingStore)
    original._store = backend
    await original.async_upsert(initial)
    restarted = HomeAssistantRoomLightingStore.__new__(HomeAssistantRoomLightingStore)
    restarted._store = backend
    assert await restarted.async_get(initial.room_id) == initial
