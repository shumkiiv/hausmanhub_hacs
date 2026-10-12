"""Narrow runtime tests for the curveParams wiring (HH-007 phase 2a).

Covers the approved contract v1.0 (basis d16ab07): the two block forms
never mix, the new engine runs only for a day_curve room with a new-form
block, the legacy form and rooms without the block keep the legacy engine
byte-for-byte, and the occupancy semantics ride on the computed maximum
(riseSeconds up, dimming.targetPercent down).
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, time as dt_time, timezone

import pytest

from custom_components.hausman_hub.domain.room_lighting import (
    RoomLightingViolation,
    config_from_payload,
)
from custom_components.hausman_hub.domain.room_lighting_engine import (
    LightCurveDecision,
    LightCurveProfile,
    RoomLightingContext,
    RoomLightingEngineViolation,
    SensorKind,
    SensorSnapshot,
    LightSnapshot,
    curve_params_block,
    curve_profile_from_config,
    tambur_curve_light_decision,
)
from custom_components.hausman_hub.domain.room_lighting_ownership import (
    SensorState,
)
from custom_components.hausman_hub.domain.tambur_curve import (
    TamburCurveParams,
)


def _payload() -> dict[str, object]:
    return {
        "contract": {"name": "hausman-hub-room-lighting-config", "version": 1},
        "roomId": "room_demo_entry",
        "name": "Тамбур",
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
                    "id": "light_demo_main",
                    "name": "Люстра",
                    "kind": "light",
                    "entityId": "light.demo_main",
                    "role": "main",
                    "groupId": None,
                    "brightness": True,
                    "color_temperature": True,
                    "autoAdoptOverride": None,
                },
                {
                    "id": "light_demo_mirror",
                    "name": "Зеркало",
                    "kind": "light",
                    "entityId": "light.demo_mirror",
                    "role": "mirror",
                    "groupId": None,
                    "brightness": False,
                    "color_temperature": False,
                    "autoAdoptOverride": None,
                },
            ],
            "power_switch": {
                "id": "sw_demo_power",
                "name": "Питание",
                "entityId": "switch.demo_power",
                "autoAdoptOverride": None,
            },
            "wireless_switches": [],
            "selectAll": False,
        },
        "schedule": [],
        "switchBindings": [],
        "dimming": {
            "enabled": True,
            "onAbsence": True,
            "fadeSeconds": 5,
            "targetPercent": 0,
        },
        "manualOffProtection": {
            "enabled": True,
            "minimumIntervalSeconds": 600,
            "releaseMode": "timer_and_absence",
            "stableAbsenceSeconds": 30,
            "priority": "manual_above_auto",
        },
        "awayBehavior": {
            "mode": "room_off",
            "return": {"restore": "by_current_conditions"},
        },
        "autoAdopt": True,
        "updatedAt": 1757000000,
        "templateId": None,
        "overrides": {},
    }


FULL_CURVE = {
    "confirmedAbsenceSeconds": 600,
    "riseSeconds": 10,
    "presencePolicy": {
        "mirrorTransition": "wait_absence",
        "allOff": "wait_absence",
        "eveningDimming": "by_time",
    },
    "dayStart": "09:00",
    "morningRamp": {"startPercent": 20, "fullPercent": 100, "fullBy": "12:00"},
    "eveningDimming": {
        "targetPercent": 5,
        "targetBy": "21:00",
        "reserveStart": "20:00",
    },
}


def _config(payload: dict[str, object]):
    return config_from_payload(payload)


# ---------------------------------------------------------------------------
# Config block: forms, closedness, order rules


class TestCurveParamsConfig:
    def test_legacy_form_round_trips(self) -> None:
        payload = {**_payload(), "curveParams": {"eveningMaxPercent": 40}}
        config = _config(payload)
        assert config.curve is not None
        assert config.curve.evening_max_percent == 40
        assert config.curve.has_new_form is False
        encoded = config.to_dict()["curveParams"]
        assert encoded == {"eveningMaxPercent": 40}

    def test_new_form_round_trips(self) -> None:
        payload = {**_payload(), "curveParams": dict(FULL_CURVE)}
        config = _config(payload)
        assert config.curve is not None
        assert config.curve.has_new_form is True
        assert config.curve.evening_max_percent is None
        assert config.to_dict()["curveParams"] == FULL_CURVE

    def test_partial_new_form_keeps_defaults(self) -> None:
        payload = {**_payload(), "curveParams": {"riseSeconds": 30}}
        config = _config(payload)
        assert config.curve is not None
        assert config.curve.rise_seconds == 30
        assert config.curve.confirmed_absence_seconds is None
        encoded = config.to_dict()["curveParams"]
        assert encoded == {"riseSeconds": 30}

    def test_empty_block_is_valid_and_keeps_legacy(self) -> None:
        payload = {**_payload(), "curveParams": {}}
        config = _config(payload)
        assert config.curve is not None
        assert config.curve.has_new_form is False
        assert config.to_dict()["curveParams"] == {}

    def test_forms_never_mix(self) -> None:
        payload = {
            **_payload(),
            "curveParams": {"eveningMaxPercent": 40, "riseSeconds": 10},
        }
        with pytest.raises(RoomLightingViolation):
            _config(payload)

    def test_unknown_block_field_is_rejected(self) -> None:
        payload = {**_payload(), "curveParams": {"riseSeconds": 10, "x": 1}}
        with pytest.raises(RoomLightingViolation):
            _config(payload)

    def test_unknown_nested_field_is_rejected(self) -> None:
        for key in ("presencePolicy", "morningRamp", "eveningDimming"):
            inner = dict(FULL_CURVE[key])
            inner["unknown"] = 1
            payload = {
                **_payload(),
                "curveParams": {key: inner},
            }
            with pytest.raises(RoomLightingViolation):
                _config(payload)

    def test_order_rules_reject_inversions(self) -> None:
        bad = dict(FULL_CURVE)
        bad["dayStart"] = "13:00"
        bad["morningRamp"] = {"startPercent": 100, "fullPercent": 20, "fullBy": "09:00"}
        bad["eveningDimming"] = {
            "targetPercent": 5,
            "targetBy": "20:00",
            "reserveStart": "21:00",
        }
        payload = {**_payload(), "curveParams": bad}
        with pytest.raises(RoomLightingViolation):
            _config(payload)

    def test_equal_percents_are_allowed(self) -> None:
        payload = {
            **_payload(),
            "curveParams": {
                "morningRamp": {
                    "startPercent": 40,
                    "fullPercent": 40,
                    "fullBy": "12:00",
                }
            },
        }
        config = _config(payload)
        assert config.curve is not None
        assert config.curve.morning_start_percent == 40

    @pytest.mark.parametrize(
        "block",
        (
            {"confirmedAbsenceSeconds": 59},
            {"confirmedAbsenceSeconds": 86401},
            {"confirmedAbsenceSeconds": "600"},
            {"confirmedAbsenceSeconds": None},
            {"riseSeconds": -1},
            {"riseSeconds": 3601},
            {"riseSeconds": None},
            {"dayStart": "9:00"},
            {"dayStart": "24:00"},
            {"dayStart": None},
            {"morningRamp": None},
            {"presencePolicy": None},
            {"presencePolicy": {"mirrorTransition": None}},
            {"morningRamp": {"fullBy": None}},
            {"morningRamp": {"startPercent": 101}},
            {"eveningDimming": {"targetPercent": -1}},
            {"eveningDimming": {"targetPercent": None}},
            {"presencePolicy": {"mirrorTransition": "immediately"}},
        ),
    )
    def test_bounds_are_closed(self, block: dict[str, object]) -> None:
        payload = {**_payload(), "curveParams": block}
        with pytest.raises(RoomLightingViolation):
            _config(payload)

    @pytest.mark.parametrize(
        "block",
        (
            # One-sided values validate against the ENGINE DEFAULT of the
            # missing half before the document is ever saved.
            {"dayStart": "13:00"},
            {"dayStart": "12:00"},
            {"eveningDimming": {"reserveStart": "21:30"}},
            {"eveningDimming": {"reserveStart": "21:00"}},
            {"eveningDimming": {"targetPercent": 5, "targetBy": "19:00"}},
        ),
    )
    def test_one_sided_order_violations_are_rejected(self, block: dict) -> None:
        payload = {**_payload(), "curveParams": block}
        with pytest.raises(RoomLightingViolation):
            _config(payload)

    @pytest.mark.parametrize(
        "block",
        (
            {"dayStart": "11:00"},
            {"eveningDimming": {"reserveStart": "19:00"}},
            {"morningRamp": {"startPercent": 100}},
            {"dayStart": "08:59"},
        ),
    )
    def test_one_sided_order_compliant_values_are_accepted(self, block: dict) -> None:
        payload = {**_payload(), "curveParams": block}
        config = _config(payload)
        assert config.curve is not None


# ---------------------------------------------------------------------------
# Engine selection: legacy rooms stay legacy, new form runs the curve


def _context(now: float, *, away: bool = False, sun_healthy: bool = True) -> RoomLightingContext:
    base = datetime(2026, 10, 15, 0, 0, tzinfo=timezone.utc)
    moment = int(base.timestamp() * 1000) + int(now * 3600 * 1000)
    return RoomLightingContext(
        now=moment,
        timezone=timezone.utc,
        sunrise=dt_time(7, 0),
        sunset=dt_time(20, 0),
        sun_healthy=sun_healthy,
        away=away,
    )


def _day_curve_config(curve: dict[str, object] | None) -> object:
    payload = _payload()
    payload["profile"] = "day_curve"
    if curve is not None:
        payload["curveParams"] = curve
    return _config(payload)


class TestEngineSelection:
    def test_no_block_runs_legacy(self) -> None:
        config = _day_curve_config(None)
        assert curve_params_block(config) is None

    def test_legacy_form_runs_legacy(self) -> None:
        config = _day_curve_config({"eveningMaxPercent": 40})
        assert curve_params_block(config) is None
        profile = curve_profile_from_config(config)
        assert profile.evening_max_percent == 40

    def test_legacy_block_without_ceiling_keeps_default_profile(self) -> None:
        config = _day_curve_config({})
        assert curve_params_block(config) is None
        profile = curve_profile_from_config(config)
        assert profile == LightCurveProfile()

    def test_new_form_runs_the_curve(self) -> None:
        config = _day_curve_config(dict(FULL_CURVE))
        params = curve_params_block(config)
        assert isinstance(params, TamburCurveParams)
        assert params.day_start == dt_time(9, 0)
        assert params.confirmed_absence_seconds == 600
        assert params.rise_seconds == 10
        assert params.morning_full_by == dt_time(12, 0)
        assert params.evening_target_percent == 5

    def test_partial_form_uses_engine_defaults(self) -> None:
        config = _day_curve_config({"riseSeconds": 30})
        params = curve_params_block(config)
        assert isinstance(params, TamburCurveParams)
        assert params.rise_seconds == 30
        assert params.morning_start_percent == 20
        assert params.evening_target_by == dt_time(21, 0)

    def test_block_without_day_curve_profile_is_ignored(self) -> None:
        payload = _payload()
        payload["curveParams"] = dict(FULL_CURVE)
        config = _config(payload)
        assert curve_params_block(config) is None


# ---------------------------------------------------------------------------
# The decision adapter: occupancy rides on the computed maximum


def _decision(
    config,
    context: RoomLightingContext,
    *,
    presence: SensorState = SensorState.ON,
    confirmed: bool = True,
    absence_seconds: int | None = None,
    motion_preview: bool = False,
    motion_rejected: bool = False,
) -> LightCurveDecision:
    params = curve_params_block(config)
    assert isinstance(params, TamburCurveParams)
    return tambur_curve_light_decision(
        params,
        config,
        context,
        curve_profile_from_config(config),
        presence=presence,
        presence_confirmed=confirmed,
        absence_seconds=absence_seconds,
        motion_preview=motion_preview,
        motion_rejected=motion_rejected,
    )


class TestTamburDecisionAdapter:
    def test_morning_ramp_rises_to_computed_maximum(self) -> None:
        config = _day_curve_config(dict(FULL_CURVE))
        decision = _decision(config, _context(10.0))
        assert decision.chandelier_on is True
        assert decision.brightness_percent == 47  # 20 + 80*(60/180)
        assert decision.fade_seconds == 10  # riseSeconds
        assert decision.mirror_on is False

    def test_day_holds_the_maximum(self) -> None:
        config = _day_curve_config(dict(FULL_CURVE))
        decision = _decision(config, _context(14.0))
        assert decision.brightness_percent == 100

    def test_evening_decline_reaches_dimming_target(self) -> None:
        config = _day_curve_config(dict(FULL_CURVE))
        at_2030 = _decision(config, _context(20.5))
        assert 5 < at_2030.brightness_percent < 100
        at_2125 = _decision(config, _context(21.25))
        assert at_2125.brightness_percent == 5

    def test_unconfirmed_presence_holds_the_level(self) -> None:
        config = _day_curve_config(dict(FULL_CURVE))
        decision = _decision(
            config, _context(14.0), confirmed=False, absence_seconds=0
        )
        # An unconfirmed presence keeps the mode level (no invented raise).
        assert decision.brightness_percent == 100

    def test_confirmed_absence_declines_to_dimming_target(self) -> None:
        """The confirmed absence requires the CURRENT OFF reading: the
        occupant has left and the proven absence has passed the
        threshold."""
        config = _day_curve_config(dict(FULL_CURVE))
        decision = _decision(
            config, _context(14.0), confirmed=False, absence_seconds=700,
            presence=SensorState.OFF,
        )
        assert decision.reason.value == "absence"
        # dimming.fadeSeconds=5: the decline is complete at +5 s.
        assert decision.brightness_percent == 0

    def test_returning_occupant_voids_the_frozen_absence(self) -> None:
        """AUD-CURVE-02: the tracker freezes the pre-return absence; a
        live ON reading voids it — the room is held instead of declining
        or raising until the presence confirms."""
        config = _day_curve_config(dict(FULL_CURVE))
        decision = _decision(
            config, _context(14.0), confirmed=False, absence_seconds=700,
            presence=SensorState.ON,
        )
        assert decision.hold is True
        assert decision.brightness_percent is None

    def test_absence_below_the_confirmed_threshold_keeps_the_mode(self) -> None:
        config = _day_curve_config(dict(FULL_CURVE))
        decision = _decision(
            config, _context(14.0), confirmed=False, absence_seconds=599
        )
        assert decision.reason.value != "absence"
        assert decision.brightness_percent == 100

    def test_unknown_presence_holds_without_commands(self) -> None:
        config = _day_curve_config(dict(FULL_CURVE))
        decision = _decision(
            config, _context(14.0), presence=SensorState.UNKNOWN
        )
        assert decision.hold is True
        assert decision.brightness_percent is None

    def test_motion_preview_and_rejection(self) -> None:
        config = _day_curve_config(dict(FULL_CURVE))
        preview = _decision(
            config, _context(14.0), motion_preview=True, confirmed=False
        )
        assert preview.reason.value == "motion_preview"
        rejected = _decision(
            config, _context(14.0), motion_rejected=True, confirmed=False
        )
        assert rejected.reason.value == "motion_rejected"

    def test_wait_absence_mirror_holds_then_releases(self) -> None:
        config = _day_curve_config(dict(FULL_CURVE))
        held = _decision(config, _context(22.5), confirmed=False, absence_seconds=0)
        assert held.mirror_on is False
        assert held.hold is True
        released = _decision(
            config, _context(22.6), confirmed=False, absence_seconds=700,
            presence=SensorState.OFF,
        )
        assert released.mirror_on is True
        assert released.chandelier_on is False

    def test_by_time_mirror_switches_despite_presence(self) -> None:
        config = _day_curve_config(
            {
                "presencePolicy": {
                    "mirrorTransition": "by_time",
                    "allOff": "by_time",
                }
            }
        )
        decision = _decision(config, _context(22.5), confirmed=False, absence_seconds=0)
        assert decision.mirror_on is True
        assert decision.chandelier_on is False

    def test_all_off_after_23_once_the_room_is_empty(self) -> None:
        config = _day_curve_config(dict(FULL_CURVE))
        decision = _decision(
            config, _context(23.5), confirmed=False, absence_seconds=700,
            presence=SensorState.OFF,
        )
        assert decision.chandelier_on is False
        assert decision.mirror_on is False

    def test_all_off_after_midnight_still_holds_a_present_occupant(self) -> None:
        """AUD-CURVE-04: after midnight the wait_absence all-off keeps
        holding while the absence is not confirmed — the date change does
        not replace the proof."""
        config = _day_curve_config(dict(FULL_CURVE))
        decision = _decision(
            config, _context(0.5), confirmed=True, absence_seconds=0,
            presence=SensorState.ON,
        )
        assert decision.chandelier_on is True
        assert decision.transition_pending_hold is True if False else decision.hold is True
        # The engine-side pending flag:
        from custom_components.hausman_hub.domain.tambur_curve import (
            TamburCurveParams, TamburCurveSnapshot, evaluate_tambur_curve,
        )
        params = TamburCurveParams(
            all_off_policy=__import__(
                "custom_components.hausman_hub.domain.tambur_curve",
                fromlist=["PresencePolicy"],
            ).PresencePolicy.WAIT_ABSENCE,
        )
        snap = TamburCurveSnapshot(
            now_minutes=30, sunrise_minutes=None, sunset_minutes=1200,
            sun_healthy=False, outdoor_lux=None,
            presence_confirmed=True, absence_confirmed=False,
        )
        base = evaluate_tambur_curve(params, snap)
        assert base.transition_pending is True
        assert base.chandelier_on is True

    def test_away_overrides_everything(self) -> None:
        config = _day_curve_config(dict(FULL_CURVE))
        context = _context(14.0, away=True)
        decision = _decision(config, context)
        assert decision.chandelier_on is False
        assert decision.brightness_percent is None

    def test_unhealthy_sun_waits_for_proof(self) -> None:
        config = _day_curve_config(dict(FULL_CURVE))
        # 20:30 with no sun proof: no invented decline (L-07).
        decision = _decision(config, _context(20.5, sun_healthy=False))
        assert decision.reason.value != "evening"
        assert decision.brightness_percent == 100

    def test_late_sunset_reserve_declines_from_twenty(self) -> None:
        config = _day_curve_config(dict(FULL_CURVE))
        context = RoomLightingContext(
            now=_context(20.5).now,
            timezone=timezone.utc,
            sunrise=dt_time(7, 0),
            sunset=dt_time(22, 30),
            sun_healthy=True,
        )
        at_2030 = _decision(config, context)
        assert at_2030.brightness_percent is not None
        assert at_2030.brightness_percent < 100

    def test_early_sunset_freezes_the_growth_then_declines(self) -> None:
        """L-05 + §3.1.4: a sunset before fullBy stops the morning growth at
        the sunset level and the decline starts AT THE SUNSET, down to the
        target by targetBy; never rising again."""
        config = _day_curve_config(dict(FULL_CURVE))

        def at(hour: float) -> LightCurveDecision:
            context = RoomLightingContext(
                now=_context(hour).now,
                timezone=timezone.utc,
                sunrise=dt_time(7, 0),
                sunset=dt_time(11, 0),
                sun_healthy=True,
            )
            return _decision(config, context)

        held = at(11.5)
        # decline_start=11:00; ratio=(11:30-11:00)/(21:00-11:00)=0.05;
        # round(73 - 68*0.05) = 70.
        assert held.brightness_percent == 70
        day_decision = at(14.0)
        # ratio=(14:00-11:00)/6h=0.3; round(73 - 68*0.3) = 53.
        assert day_decision.brightness_percent == 53
        evening_decision = at(20.5)
        # ratio=19/20; round(73-64.6)=8.
        assert evening_decision.brightness_percent == 8
        at_target = at(21.25)
        assert at_target.brightness_percent == 5

    def test_regular_sunset_declines_from_the_sunset(self) -> None:
        """A sunset before targetBy declines AT THE SUNSET: 20:00 sunset
        gives 52% at 20:30 (ratio 0.5 of the 20:00→21:00 span)."""
        config = _day_curve_config(dict(FULL_CURVE))
        at_2030 = _decision(config, _context(20.5))
        assert at_2030.brightness_percent == 52
        # Without a confirmed presence the same minute is the mode level.
        at_2030_unconfirmed = _decision(
            config, _context(20.5), confirmed=False, absence_seconds=0
        )
        assert at_2030_unconfirmed.brightness_percent == 52
        assert at_2030_unconfirmed.reason.value == "evening"

    def test_configured_reserve_start_is_the_decline_start(self) -> None:
        """L-03 with a custom reserve: reserveStart=20:45, targetBy=21:00,
        sunset 21:30 — the decline starts at the owner's 20:45, not earlier."""
        from custom_components.hausman_hub.domain.tambur_curve import (
            PresencePolicy,
            TamburCurveParams,
            TamburCurveSnapshot,
            evaluate_tambur_curve,
        )
        params = TamburCurveParams(
            mirror_transition_policy=PresencePolicy.BY_TIME,
            all_off_policy=PresencePolicy.BY_TIME,
            evening_reserve_start=dt_time(20, 45),
        )

        def snap(minutes: int) -> TamburCurveSnapshot:
            return TamburCurveSnapshot(
                now_minutes=minutes,
                sunrise_minutes=420,
                sunset_minutes=21 * 60 + 30,
                sun_healthy=True,
                outdoor_lux=None,
                presence_confirmed=True,
                absence_confirmed=False,
            )

        at_2040 = evaluate_tambur_curve(params, snap(20 * 60 + 40))
        # Before the configured reserve: the day maximum holds (no early
        # sunset, so no L-05 ceiling).
        assert at_2040.required_percent == 100
        at_2050 = evaluate_tambur_curve(params, snap(20 * 60 + 50))
        # The decline runs from the configured 20:45 toward 5% by 21:00:
        # ratio=5/15; round(100 - 95/3) = 68.
        assert at_2050.required_percent == 68
        at_2100 = evaluate_tambur_curve(params, snap(21 * 60))
        assert at_2100.required_percent == 5

    def test_early_sunrise_lights_the_morning_mirror(self) -> None:
        """Plan §3.1: a proven early sunrise lights the mirror until
        day_start; the chandelier stays off."""
        config = _day_curve_config(dict(FULL_CURVE))
        context = RoomLightingContext(
            now=_context(8.0).now,
            timezone=timezone.utc,
            sunrise=dt_time(7, 0),
            sunset=dt_time(20, 0),
            sun_healthy=True,
        )
        decision = _decision(config, context)
        assert decision.mirror_on is True
        assert decision.chandelier_on is False

    def test_late_sunrise_keeps_pre_dawn_dark(self) -> None:
        """L-06: a sunrise at or after day_start is no morning-mirror
        proof — no mirror switch. With an empty room the pre-dawn side
        goes dark; with a present occupant (wait_absence all-off) the
        hold keeps the room instead of turning the light off overhead."""
        config = _day_curve_config(dict(FULL_CURVE))
        context = RoomLightingContext(
            now=_context(8.0).now,
            timezone=timezone.utc,
            sunrise=dt_time(9, 30),
            sunset=dt_time(20, 0),
            sun_healthy=True,
        )
        decision = _decision(
            config, context, presence=SensorState.OFF, absence_seconds=700,
        )
        assert decision.mirror_on is False
        assert decision.chandelier_on is False
        # Without a proven absence the hold keeps the room (fail-closed):
        # neither target is commanded — no mirror switch, no turn-off.
        held = _decision(config, context, presence=SensorState.ON)
        assert held.hold is True

    def test_sunset_at_target_by_holds_the_target(self) -> None:
        config = _day_curve_config(dict(FULL_CURVE))
        context = RoomLightingContext(
            now=_context(21.5).now,
            timezone=timezone.utc,
            sunrise=dt_time(7, 0),
            sunset=dt_time(21, 0),
            sun_healthy=True,
        )
        decision = _decision(config, context)
        assert decision.brightness_percent == 5

    def test_confirmed_presence_does_not_push_toward_pending_mirror(self) -> None:
        """P-02: while the wait_absence transition is pending, even a
        confirmed presence must not move the lights (no raise toward the
        pending state, no mirror switch)."""
        config = _day_curve_config(dict(FULL_CURVE))
        context = RoomLightingContext(
            now=_context(22.5).now,
            timezone=timezone.utc,
            sunrise=dt_time(7, 0),
            sunset=dt_time(20, 0),
            sun_healthy=True,
        )
        decision = _decision(
            config, context, confirmed=True, absence_seconds=0
        )
        assert decision.hold is True
        assert decision.brightness_percent is None
        assert decision.mirror_on is False

    def test_unknown_presence_during_pending_mirror_holds(self) -> None:
        config = _day_curve_config(dict(FULL_CURVE))
        context = RoomLightingContext(
            now=_context(22.5).now,
            timezone=timezone.utc,
            sunrise=dt_time(7, 0),
            sunset=dt_time(20, 0),
            sun_healthy=True,
        )
        decision = _decision(
            config, context, presence=SensorState.UNKNOWN
        )
        assert decision.hold is True
        assert decision.brightness_percent is None
        assert decision.mirror_on is False

    def test_confirmed_absence_after_pending_completes_the_mirror(self) -> None:
        config = _day_curve_config(dict(FULL_CURVE))
        context = RoomLightingContext(
            now=_context(22.5).now,
            timezone=timezone.utc,
            sunrise=dt_time(7, 0),
            sunset=dt_time(20, 0),
            sun_healthy=True,
        )
        decision = _decision(
            config, context, confirmed=False, absence_seconds=700,
            presence=SensorState.OFF,
        )
        assert decision.mirror_on is True
        assert decision.chandelier_on is False


class TestHoldCommandPlan:
    """The hold must reach the command plan: while the wait_absence
    transition is pending, NEITHER target gets a command — no turn-on
    without brightness, no mirror handover. The confirmed absence
    completes the transition."""

    def _room_context(
        self,
        *,
        chandelier: SensorState,
        mirror: SensorState,
        hour: float = 22.5,
        auto_owned_lit: bool = True,
    ) -> RoomLightingContext:
        from custom_components.hausman_hub.domain.room_lighting_ownership import (
            OwnershipSnapshot,
            OwnershipSource,
        )

        ownership: tuple = ()
        if auto_owned_lit:
            # A lit target without a proven auto record counts as a manual
            # selection (the house rule), so the tests that exercise the
            # curve itself prove the automation's own ownership instead.
            records = []
            if chandelier is SensorState.ON:
                records.append(
                    OwnershipSnapshot(
                        target_id="light_demo_main",
                        source=OwnershipSource.AUTO,
                        confirmed=True,
                        at=_context(hour).now - 60_000,
                    )
                )
            if mirror is SensorState.ON:
                records.append(
                    OwnershipSnapshot(
                        target_id="light_demo_mirror",
                        source=OwnershipSource.AUTO,
                        confirmed=True,
                        at=_context(hour).now - 60_000,
                    )
                )
            ownership = tuple(records)
        return RoomLightingContext(
            now=_context(hour).now,
            timezone=timezone.utc,
            sunrise=dt_time(7, 0),
            sunset=dt_time(20, 0),
            sensors=(),
            lights=(
                LightSnapshot("light_demo_main", chandelier, _context(hour).now),
                LightSnapshot("light_demo_mirror", mirror, _context(hour).now),
            ),
            ownership=ownership,
        )

    def _plan(self, config, context: RoomLightingContext, **kwargs):
        from custom_components.hausman_hub.domain.room_lighting_engine import (
            evaluate_curve_room,
        )

        return evaluate_curve_room(
            config,
            context,
            presence_confirmed=kwargs.get("presence_confirmed", True),
            absence_seconds=kwargs.get("absence_seconds", 0),
            presence=kwargs.get("presence", SensorState.ON),
            motion_preview=kwargs.get("motion_preview", False),
            motion_rejected=kwargs.get("motion_rejected", False),
        )

    def _target(self, decision, target_id: str):
        return next(item for item in decision.targets if item.target_id == target_id)

    def _assert_pending_held(self, decision, target_id: str) -> None:
        """One pending target: no commands, unchanged state, and the exact
        ABSENCE_UNPROVEN hold reason."""
        from custom_components.hausman_hub.domain.room_lighting_engine import (
            SkipReason,
        )

        item = self._target(decision, target_id)
        assert item.commands == (), target_id
        assert item.desired_state == "unchanged", target_id
        assert any(
            skip.reason is SkipReason.ABSENCE_UNPROVEN for skip in item.skips
        ), target_id

    def test_pending_keeps_lit_chandelier_and_mirror(self) -> None:
        """22:30, both lights on, person present: pending hold — no
        commands at all (no handover to the mirror)."""
        config = _day_curve_config(dict(FULL_CURVE))
        context = self._room_context(
            chandelier=SensorState.ON, mirror=SensorState.ON
        )
        decision = self._plan(config, context)
        for target_id in ("light_demo_main", "light_demo_mirror"):
            self._assert_pending_held(decision, target_id)

    def test_pending_does_not_turn_on_a_dark_room(self) -> None:
        """22:30, everything off, a proven presence (e.g. someone walked
        through): the pending transition must NOT produce a turn-on."""
        config = _day_curve_config(dict(FULL_CURVE))
        context = self._room_context(
            chandelier=SensorState.OFF, mirror=SensorState.OFF
        )
        decision = self._plan(config, context)
        for target_id in ("light_demo_main", "light_demo_mirror"):
            self._assert_pending_held(decision, target_id)

    def test_pending_does_not_switch_only_the_mirror_on(self) -> None:
        """22:30, mirror already on, chandelier off: the pending hold must
        not hand the room over to the mirror state either."""
        config = _day_curve_config(dict(FULL_CURVE))
        context = self._room_context(
            chandelier=SensorState.OFF, mirror=SensorState.ON
        )
        decision = self._plan(config, context)
        for target_id in ("light_demo_main", "light_demo_mirror"):
            self._assert_pending_held(decision, target_id)

    def test_pending_keeps_a_lit_chandelier_with_a_dark_mirror(self) -> None:
        """22:30, chandelier on, mirror off: the pending hold keeps the
        lit chandelier and does not command the dark mirror (the fourth
        pending state combination)."""
        config = _day_curve_config(dict(FULL_CURVE))
        context = self._room_context(
            chandelier=SensorState.ON, mirror=SensorState.OFF
        )
        decision = self._plan(config, context)
        for target_id in ("light_demo_main", "light_demo_mirror"):
            self._assert_pending_held(decision, target_id)

    def test_confirmed_absence_completes_the_handover(self) -> None:
        """22:30, chandelier on and the mirror still off, the absence has
        been confirmed (700 s ≥ the 600 s threshold): the transition
        executes — chandelier off, mirror on."""
        config = _day_curve_config(dict(FULL_CURVE))
        context = self._room_context(
            chandelier=SensorState.ON, mirror=SensorState.OFF
        )
        decision = self._plan(
            config,
            context,
            presence_confirmed=False,
            absence_seconds=700,
            presence=SensorState.OFF,
        )
        main = self._target(decision, "light_demo_main")
        assert any(
            command.action.value == "turn_off" for command in main.commands
        )
        mirror = self._target(decision, "light_demo_mirror")
        assert any(
            command.action.value == "turn_on" for command in mirror.commands
        )

    def test_unknown_presence_holds_the_full_plan(self) -> None:
        """14:00, unknown presence sensors: the hold leaves both targets
        untouched (fail-closed on sensor faults)."""
        config = _day_curve_config(dict(FULL_CURVE))
        context = self._room_context(
            chandelier=SensorState.OFF, mirror=SensorState.OFF, hour=14.0
        )
        decision = self._plan(
            config,
            context,
            presence=SensorState.UNKNOWN,
            presence_confirmed=False,
        )
        for target_id in ("light_demo_main", "light_demo_mirror"):
            self._assert_pending_held(decision, target_id)

    def test_motion_trials_cannot_bypass_the_pending_hold(self) -> None:
        """22:30 pending: a motion preview or a rejected motion must not
        unlock the hold — the dark chandelier gets NO turn-on and the lit
        one keeps its level (luna round 5)."""
        config = _day_curve_config(dict(FULL_CURVE))
        for kwargs in (
            {"motion_preview": True},
            {"motion_rejected": True},
        ):
            for lit in (SensorState.ON, SensorState.OFF):
                context = self._room_context(
                    chandelier=lit,
                    mirror=SensorState.OFF,
                )
                decision = self._plan(
                    config,
                    context,
                    presence_confirmed=False,
                    absence_seconds=0,
                    **kwargs,
                )
                for target_id in ("light_demo_main", "light_demo_mirror"):
                    self._assert_pending_held(decision, target_id)

    def test_motion_trials_cannot_bypass_the_unknown_hold(self) -> None:
        """14:00 unknown presence: a motion preview or rejection must not
        produce commands either."""
        config = _day_curve_config(dict(FULL_CURVE))
        for kwargs in (
            {"motion_preview": True},
            {"motion_rejected": True},
        ):
            context = self._room_context(
                chandelier=SensorState.OFF, mirror=SensorState.OFF, hour=14.0
            )
            decision = self._plan(
                config,
                context,
                presence=SensorState.UNKNOWN,
                presence_confirmed=False,
                **kwargs,
            )
            for target_id in ("light_demo_main", "light_demo_mirror"):
                self._assert_pending_held(decision, target_id)

    def test_motion_trials_still_work_outside_the_hold(self) -> None:
        """14:00, real presence state: the motion preview keeps its
        behaviour (the hold must not swallow the legitimate trial)."""
        config = _day_curve_config(dict(FULL_CURVE))
        decision = _decision(
            config,
            _context(14.0),
            confirmed=False,
            absence_seconds=0,
            motion_preview=True,
        )
        assert decision.reason.value == "motion_preview"
        assert decision.brightness_percent == 30
        rejected = _decision(
            config,
            _context(14.0),
            confirmed=False,
            absence_seconds=0,
            motion_rejected=True,
        )
        assert rejected.reason.value == "motion_rejected"


class TestDimmingEnabledFlag:
    """dimming.enabled=False keeps the mode level during the confirmed
    absence (the approved rule: the decline needs both flags)."""

    def _config_with_dimming(self, **dimming: object):
        payload = _payload()
        payload["profile"] = "day_curve"
        payload["curveParams"] = dict(FULL_CURVE)
        payload["dimming"] = {
            "enabled": dimming.get("enabled", True),
            "onAbsence": dimming.get("on_absence", True),
            "fadeSeconds": 5,
            "targetPercent": 5,
        }
        return _config(payload)

    def test_disabled_dimming_keeps_the_mode_level(self) -> None:
        config = self._config_with_dimming(enabled=False)
        decision = _decision(
            config,
            _context(14.0),
            confirmed=False,
            absence_seconds=700,
            presence=SensorState.OFF,
        )
        assert decision.reason.value != "absence"
        assert decision.brightness_percent == 100

    def test_on_absence_false_keeps_the_mode_level(self) -> None:
        config = self._config_with_dimming(on_absence=False)
        decision = _decision(
            config,
            _context(14.0),
            confirmed=False,
            absence_seconds=700,
            presence=SensorState.OFF,
        )
        assert decision.reason.value != "absence"
        assert decision.brightness_percent == 100

    def test_enabled_dimming_declines(self) -> None:
        config = self._config_with_dimming()
        decision = _decision(
            config,
            _context(14.0),
            confirmed=False,
            absence_seconds=700,
            presence=SensorState.OFF,
        )
        assert decision.reason.value == "absence"


class TestSunEvidenceHealth:
    """The sun health flag: a half-parsed live read is NOT proof."""

    @staticmethod
    def _provider():
        from custom_components.hausman_hub.application.room_lighting_ha_state import (
            RoomLightingHaStateProvider,
        )

        return RoomLightingHaStateProvider()

    @staticmethod
    def _hass(attributes: object):
        from types import SimpleNamespace

        state = SimpleNamespace(attributes=attributes) if attributes is not None else None
        return SimpleNamespace(states=SimpleNamespace(get=lambda entity_id: state))

    def test_both_times_prove_the_sun(self) -> None:
        from datetime import datetime, timezone as tz

        provider = self._provider()
        attributes = {
            "next_rising": datetime(2026, 10, 16, 7, 0, tzinfo=tz.utc).isoformat(),
            "next_setting": datetime(2026, 10, 15, 20, 0, tzinfo=tz.utc).isoformat(),
        }
        sunrise, sunset, healthy = provider._sun_times(self._hass(attributes), tz.utc)
        assert healthy is True
        assert (sunrise.hour, sunrise.minute) == (7, 0)
        assert (sunset.hour, sunset.minute) == (20, 0)

    def test_half_parsed_read_is_not_proof(self) -> None:
        from datetime import datetime, timezone as tz

        provider = self._provider()
        attributes = {
            "next_rising": datetime(2026, 10, 16, 7, 0, tzinfo=tz.utc).isoformat(),
        }
        _, _, healthy = provider._sun_times(self._hass(attributes), tz.utc)
        # The missing sunset must not fall back to the built-in default and
        # be trusted as proof (L-07).
        assert healthy is False

    def test_unreadable_sun_is_not_proof(self) -> None:
        from datetime import timezone as tz

        provider = self._provider()
        _, _, healthy = provider._sun_times(self._hass({"next_rising": "garbage"}), tz.utc)
        assert healthy is False

    def test_missing_sun_state_is_not_proof(self) -> None:
        from datetime import timezone as tz

        provider = self._provider()
        _, _, healthy = provider._sun_times(self._hass(None), tz.utc)
        assert healthy is False

    def test_overrides_are_always_healthy(self) -> None:
        from datetime import time as dt_time
        from datetime import timezone as tz

        from custom_components.hausman_hub.application.room_lighting_ha_state import (
            RoomLightingHaStateProvider,
        )

        provider = RoomLightingHaStateProvider(
            sunrise=dt_time(7, 0), sunset=dt_time(20, 0)
        )
        _, _, healthy = provider._sun_times(self._hass(None), tz.utc)
        assert healthy is True

class TestAuditCurves:
    """Closing the security audit findings (AUD-CURVE-01..06)."""

    def test_null_block_is_rejected_not_erasing(self) -> None:
        """AUD-CURVE-01: an explicit ``curveParams: null`` is a validation
        error — only the MISSING key triggers the preserve-merge."""
        payload = {**_payload(), "curveParams": None}
        with pytest.raises(RoomLightingViolation):
            _config(payload)

    def test_evening_wait_absence_holds_the_actual_level(self) -> None:
        """AUD-CURVE-03: eveningDimming=wait_absence — while the room is
        not honestly empty the computed decline does not move the actual
        lights (hold), and the plan commands nothing. The lit chandelier
        carries PROVEN auto ownership, so the manual-ownership gate does
        not stop the profile before the evening hold."""
        from custom_components.hausman_hub.domain.room_lighting_engine import (
            SkipReason,
            evaluate_curve_room,
        )
        from custom_components.hausman_hub.domain.room_lighting_ownership import (
            OwnershipSnapshot,
            OwnershipSource,
        )

        config = _day_curve_config(
            {
                "presencePolicy": {"eveningDimming": "wait_absence"},
                "eveningDimming": {
                    "targetPercent": 5,
                    "targetBy": "21:00",
                    "reserveStart": "20:00",
                },
            }
        )
        # 20:30, sunset 20:00, person present (confirmed): the computed
        # maximum is already declining (52%), but the actual decline waits.
        now = _context(20.5).now
        context = RoomLightingContext(
            now=now,
            timezone=timezone.utc,
            sunrise=dt_time(7, 0),
            sunset=dt_time(20, 0),
            sun_healthy=True,
            lights=(
                LightSnapshot("light_demo_main", SensorState.ON, now),
                LightSnapshot("light_demo_mirror", SensorState.OFF, now),
            ),
            ownership=(
                OwnershipSnapshot(
                    target_id="light_demo_main",
                    source=OwnershipSource.AUTO,
                    confirmed=True,
                    at=now - 60_000,
                ),
            ),
        )
        decision = evaluate_curve_room(
            config,
            context,
            presence_confirmed=True,
            absence_seconds=0,
            presence=SensorState.ON,
        )
        for target_id in ("light_demo_main", "light_demo_mirror"):
            item = next(x for x in decision.targets if x.target_id == target_id)
            assert item.commands == (), target_id
            assert item.desired_state == "unchanged", target_id
            assert any(
                skip.reason is SkipReason.ABSENCE_UNPROVEN for skip in item.skips
            ), target_id

    def test_evening_by_time_declines_despite_presence(self) -> None:
        """The default eveningDimming=by_time keeps declining at the wall
        time (presence does not stop the scheduled decline)."""
        config = _day_curve_config(dict(FULL_CURVE))
        decision = _decision(config, _context(20.5))
        assert decision.brightness_percent == 52

    def test_evening_wait_absence_executes_once_the_room_is_empty(self) -> None:
        """After the confirmed absence the evening decline executes: the
        actual level drops to the absence decline goal (dimming 0 in this
        document), which the computed maximum caps from above — never the
        reverse (AUD-CURVE-03 + AUD-CURVE-06)."""
        config = _day_curve_config(
            {
                "presencePolicy": {"eveningDimming": "wait_absence"},
                "eveningDimming": {
                    "targetPercent": 5,
                    "targetBy": "21:00",
                    "reserveStart": "20:00",
                },
            }
        )
        decision = _decision(
            config, _context(20.5), confirmed=False, absence_seconds=700,
            presence=SensorState.OFF,
        )
        assert decision.reason.value == "absence"
        assert decision.brightness_percent is not None
        assert decision.brightness_percent <= 52

    def test_absence_decline_never_raises_above_the_maximum(self) -> None:
        """AUD-CURVE-06: a saved 40% decline goal cannot raise an empty
        evening room whose computed maximum has already fallen to 5%."""
        payload = _payload()
        payload["profile"] = "day_curve"
        payload["curveParams"] = dict(FULL_CURVE)
        payload["dimming"] = {
            "enabled": True,
            "onAbsence": True,
            "fadeSeconds": 5,
            "targetPercent": 40,
        }
        config = _config(payload)
        decision = _decision(
            config, _context(21.25), confirmed=False, absence_seconds=700,
            presence=SensorState.OFF,
        )
        # The computed maximum at 21:25 is the 5% evening target; the
        # absence level must never exceed it.
        assert decision.brightness_percent is not None
        assert decision.brightness_percent <= 5

    def test_motion_rejected_respects_the_maximum(self) -> None:
        """AUD-CURVE-06: the rejected-motion floor also respects a lower
        computed maximum."""
        config = _day_curve_config(dict(FULL_CURVE))
        decision = _decision(
            config, _context(21.25), confirmed=False, absence_seconds=0,
            motion_rejected=True,
        )
        assert decision.brightness_percent is not None
        assert decision.brightness_percent <= 5

    def test_morning_mirror_holds_an_unknown_room(self) -> None:
        """AUD-CURVE-05a: the mirror/all-off mode switch must not command
        the room while the presence evidence is unknown."""
        config = _day_curve_config(dict(FULL_CURVE))
        context = RoomLightingContext(
            now=_context(8.0).now,
            timezone=timezone.utc,
            sunrise=dt_time(7, 0),
            sunset=dt_time(20, 0),
            sun_healthy=True,
        )
        decision = _decision(
            config, context, presence=SensorState.UNKNOWN,
        )
        assert decision.hold is True
        assert decision.brightness_percent is None
