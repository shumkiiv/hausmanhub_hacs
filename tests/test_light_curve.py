"""Tests for the pure tambur day-curve engine (owner xlsx algorithm)."""

from __future__ import annotations

from datetime import time

import pytest

from custom_components.hausman_hub.domain.light_curve import (
    CurveReason,
    LightCurveContext,
    LightCurveProfile,
    LightCurveViolation,
    evaluate_light_curve,
)
from custom_components.hausman_hub.domain.room_lighting_ownership import SensorState

PROFILE = LightCurveProfile()
SUNRISE = time(6, 30)
SUNSET = time(19, 0)


def _ctx(
    hour: int,
    minute: int,
    *,
    presence: SensorState = SensorState.OFF,
    presence_confirmed: bool = False,
    absence_seconds: int | None = None,
) -> LightCurveContext:
    return LightCurveContext(
        now=time(hour, minute),
        sunrise=SUNRISE,
        sunset=SUNSET,
        presence=presence,
        chandelier=SensorState.OFF,
        mirror=SensorState.OFF,
        presence_confirmed=presence_confirmed,
        absence_seconds=absence_seconds,
    )


def test_night_turns_chandelier_off_and_mirror_on() -> None:
    decision = evaluate_light_curve(PROFILE, _ctx(23, 30))
    assert decision.reason is CurveReason.NIGHT
    assert decision.chandelier_on is False
    assert decision.mirror_on is True

    after = evaluate_light_curve(PROFILE, _ctx(0, 15))
    assert after.mirror_on is True
    assert after.chandelier_on is False


def test_after_night_until_morning_both_off() -> None:
    for hour, minute in ((0, 45), (5, 0), (8, 59)):
        decision = evaluate_light_curve(PROFILE, _ctx(hour, minute))
        assert decision.chandelier_on is False, (hour, minute)
        assert decision.mirror_on is False, (hour, minute)


def test_morning_starts_at_five_percent() -> None:
    decision = evaluate_light_curve(PROFILE, _ctx(9, 0))
    assert decision.reason is CurveReason.RAMP
    assert decision.chandelier_on is True
    assert decision.brightness_percent == 5
    assert decision.color_temperature == PROFILE.warm_kelvin


def test_morning_ramp_reaches_full_at_noon() -> None:
    ten = evaluate_light_curve(PROFILE, _ctx(10, 0))
    assert ten.reason is CurveReason.RAMP
    assert ten.brightness_percent == 37  # 5 + 95 * 60/180

    noon = evaluate_light_curve(PROFILE, _ctx(12, 0))
    assert noon.reason is CurveReason.DAY
    assert noon.brightness_percent == 100
    assert noon.color_temperature == PROFILE.neutral_kelvin


def test_day_is_full_and_neutral_until_evening_lead() -> None:
    decision = evaluate_light_curve(PROFILE, _ctx(17, 0))  # 2h before sunset
    assert decision.reason is CurveReason.DAY
    assert decision.brightness_percent == 100
    assert decision.color_temperature == PROFILE.neutral_kelvin


def test_evening_curve_fades_and_warms_until_ten_pm() -> None:
    # 18:30 is 30 minutes into the four-hour sunset-60 .. 22:00 curve.
    decision = evaluate_light_curve(PROFILE, _ctx(18, 30))
    assert decision.reason is CurveReason.EVENING
    assert decision.brightness_percent == 88  # 100 - 95 * 30/240
    assert decision.color_temperature == 2900  # 3000 - 800 * 30/240

    after_sunset = evaluate_light_curve(PROFILE, _ctx(20, 30))
    assert after_sunset.reason is CurveReason.LATE_EVENING
    assert after_sunset.brightness_percent is not None
    assert 5 < after_sunset.brightness_percent < 100

    at_ten = evaluate_light_curve(PROFILE, _ctx(22, 0))
    assert at_ten.brightness_percent == 5
    assert at_ten.color_temperature == PROFILE.warm_kelvin


def test_late_evening_holds_five_percent_warm() -> None:
    decision = evaluate_light_curve(PROFILE, _ctx(22, 30))
    assert decision.chandelier_on is True
    assert decision.brightness_percent == 5
    assert decision.color_temperature == PROFILE.warm_kelvin


def test_absence_fades_toward_five_percent() -> None:
    # 7.5 minutes without presence: half of the five-minute fade remains.
    decision = evaluate_light_curve(PROFILE, _ctx(14, 0, absence_seconds=450))
    assert decision.reason is CurveReason.ABSENCE
    assert decision.brightness_percent == 52  # 100 - 95/2
    assert decision.fade_seconds == 150

    settled = evaluate_light_curve(PROFILE, _ctx(14, 0, absence_seconds=600))
    assert settled.brightness_percent == 5
    assert settled.fade_seconds == 0


def test_absence_before_five_minutes_holds_the_mode_level() -> None:
    decision = evaluate_light_curve(PROFILE, _ctx(14, 0, absence_seconds=120))
    assert decision.reason is CurveReason.DAY
    assert decision.brightness_percent == 100


def test_confirmed_presence_returns_to_mode_maximum_in_ten_seconds() -> None:
    decision = evaluate_light_curve(
        PROFILE,
        _ctx(20, 0, presence=SensorState.ON, presence_confirmed=True),
    )
    assert decision.reason is CurveReason.PRESENCE_RETURN
    assert decision.chandelier_on is True
    assert decision.fade_seconds == 10
    # The target is the maximum of the current time mode, not a fixed 5%.
    assert decision.brightness_percent == evaluate_light_curve(
        PROFILE, _ctx(20, 0)
    ).brightness_percent
    # Presence never changes the colour of the current mode.
    assert decision.color_temperature == evaluate_light_curve(
        PROFILE, _ctx(20, 0)
    ).color_temperature


def test_unconfirmed_presence_holds_the_reduced_level() -> None:
    decision = evaluate_light_curve(
        PROFILE,
        _ctx(
            20, 0,
            presence=SensorState.ON,
            presence_confirmed=False,
            absence_seconds=600,
        ),
    )
    assert decision.reason is CurveReason.ABSENCE
    assert decision.brightness_percent == 5


def test_profile_validation() -> None:
    with pytest.raises(LightCurveViolation):
        LightCurveProfile(morning_min_percent=100)
    with pytest.raises(LightCurveViolation):
        LightCurveProfile(warm_kelvin=3000, neutral_kelvin=2200)
    with pytest.raises(LightCurveViolation):
        LightCurveProfile(presence_confirm_seconds=-1)
    with pytest.raises(LightCurveViolation):
        evaluate_light_curve(object(), _ctx(12, 0))  # type: ignore[arg-type]


def test_decision_payload_is_stable() -> None:
    decision = evaluate_light_curve(PROFILE, _ctx(23, 30))
    assert decision.to_payload() == {
        "reason": "night",
        "chandelierOn": False,
        "brightnessPercent": None,
        "colorTemperature": None,
        "fadeSeconds": PROFILE.mode_fade_seconds,
        "mirrorOn": True,
    }
