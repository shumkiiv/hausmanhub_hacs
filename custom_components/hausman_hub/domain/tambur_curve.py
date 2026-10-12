"""Parameterized tambur day-curve engine (HH-007 curveParams, v1).

The owner-approved plan (HH-007, ред. 1.0; contracts
room-lighting-config.schema.json curveParams block) defines the tambur
lighting by explicit parameters instead of the legacy fixed schedule:

* ``day_start`` (09:00) — the mirror-to-day boundary. Before it the room is
  dark unless the sunrise is early enough for the morning mirror; with a
  late/unavailable sunrise the day chandelier mode starts at day_start with
  no morning mirror by guesswork (L-06).
* ``morning_ramp`` — the computed maximum grows linearly from
  ``start_percent`` (20) to ``full_percent`` (100) reaching it at ``full_by``
  (12:00). An early sunset before full_by stops the growth (L-05): the
  ceiling is capped by the evening decline.
* ``evening_dimming`` — the computed maximum declines to ``target_percent``
  (5) by ``target_by`` (21:00). ``reserve_start`` (20:00) begins a reserve
  decline ONLY for a genuinely late sunset (L-03/L-04): the decline then
  runs evenly toward the target by target_by; an already lower brightness is
  never raised. An unavailable sun is NOT replaced by the reserve (L-07) —
  the outdoor-lux rule (§10) governs that case in the caller.
* Presence never changes the time mode; arrival raises the actual brightness
  to the current computed maximum over ``rise_seconds`` (10), and the
  confirmed absence starts the decline to the dimming target over the
  existing absence fade (the caller's fade logic).
* Mode transitions: mirror at ``mirror_time`` (22:00) and all-off at
  ``all_off_time`` (23:00) follow the presence policy: ``by_time`` executes
  at the wall time despite presence; ``wait_absence`` holds while a person
  is present and executes when the caller confirms the absence (P-02/P-03).

The engine is pure: one immutable snapshot in, one decision out. The
wait-for-absence delay is expressed as a HOLD decision; the caller tracks
the pending transition and completes it when the confirmed absence arrives.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time as dt_time
from enum import StrEnum

from .room_lighting_ownership import SensorState


class TamburCurveViolation(ValueError):
    """Parameterized tambur curve input is malformed."""


class PresencePolicy(StrEnum):
    BY_TIME = "by_time"
    WAIT_ABSENCE = "wait_absence"


class TamburMode(StrEnum):
    NIGHT = "night_mirror"      # 23:00..day_start: dark, mirror allowed at wake
    PRE_DAY = "night_off"       # sunrise..day_start: dark (late-s sunrise, L-06)
    RAMP = "day"                # day_start..full_by: computed max growing
    DAY = "day"                 # full_by..evening decline start
    EVENING_DECLINE = "evening_decline"
    EVENING_HOLD = "evening_decline"  # target reached, holding to mirror time
    MIRROR = "mirror"           # 22:00..all_off: mirror on, rest off
    ALL_OFF = "night_off"       # 23:00..: everything off
    AWAY = "away"
    UNKNOWN = "unknown"


class TamburReason(StrEnum):
    OFF = "off"
    NIGHT = "night"
    PRE_DAWN = "pre_dawn"
    RAMP = "ramp"
    DAY = "day"
    EVENING_DECLINE = "evening_decline"
    EVENING_HOLD = "evening_hold"
    RESERVE_DECLINE = "reserve_decline"
    MIRROR = "mirror"
    ALL_OFF = "all_off"
    AWAY = "away"
    ABSENCE_DIMMING = "absence_dimming"
    PRESENCE_RETURN = "presence_return"
    WAIT_ABSENCE = "wait_absence"
    UNKNOWN = "unknown"


def _minutes(value: dt_time) -> int:
    return value.hour * 60 + value.minute


def _ratio(now: int, start: int, end: int) -> float:
    span = end - start
    if span <= 0 or now <= start:
        return 0.0
    if now >= end:
        return 1.0
    return (now - start) / span


@dataclass(frozen=True, slots=True)
class TamburCurveParams:
    """The curveParams contract block, validated (approved spec v1.0)."""

    day_start: dt_time = dt_time(9, 0)
    morning_start_percent: int = 20
    morning_full_percent: int = 100
    morning_full_by: dt_time = dt_time(12, 0)
    evening_target_percent: int = 5
    evening_target_by: dt_time = dt_time(21, 0)
    evening_reserve_start: dt_time = dt_time(20, 0)
    mirror_time: dt_time = dt_time(22, 0)
    all_off_time: dt_time = dt_time(23, 0)
    mirror_transition_policy: PresencePolicy = PresencePolicy.BY_TIME
    all_off_policy: PresencePolicy = PresencePolicy.BY_TIME
    evening_dimming_policy: PresencePolicy = PresencePolicy.BY_TIME
    confirmed_absence_seconds: int = 600
    rise_seconds: int = 10

    def __post_init__(self) -> None:
        for label, value in (
            ("dayStart", self.day_start),
            ("morningRamp.fullBy", self.morning_full_by),
            ("eveningDimming.targetBy", self.evening_target_by),
            ("eveningDimming.reserveStart", self.evening_reserve_start),
            ("mirrorTime", self.mirror_time),
            ("allOffTime", self.all_off_time),
        ):
            if not isinstance(value, dt_time):
                raise TamburCurveViolation(f"tambur curve {label} is invalid")
        for label, value in (
            ("morningRamp.startPercent", self.morning_start_percent),
            ("morningRamp.fullPercent", self.morning_full_percent),
            ("eveningDimming.targetPercent", self.evening_target_percent),
        ):
            if type(value) is not int or not 0 <= value <= 100:
                raise TamburCurveViolation(
                    f"tambur curve {label} must be 0..100"
                )
        if self.morning_start_percent > self.morning_full_percent:
            raise TamburCurveViolation(
                "tambur curve morningRamp must grow (startPercent <= fullPercent)"
            )
        for label, value in (
            ("confirmedAbsenceSeconds", self.confirmed_absence_seconds),
            ("riseSeconds", self.rise_seconds),
        ):
            if type(value) is not int or value < 0:
                raise TamburCurveViolation(f"tambur curve {label} is invalid")
        for value in (
            self.mirror_transition_policy,
            self.all_off_policy,
            self.evening_dimming_policy,
        ):
            if not isinstance(value, PresencePolicy):
                raise TamburCurveViolation(
                    "tambur curve presencePolicy is invalid"
                )
        if _minutes(self.day_start) >= _minutes(self.morning_full_by):
            raise TamburCurveViolation(
                "tambur curve dayStart must be before morningRamp.fullBy "
                "(server-side order rule)"
            )
        if _minutes(self.evening_reserve_start) >= _minutes(
            self.evening_target_by
        ):
            raise TamburCurveViolation(
                "tambur curve reserveStart must be before eveningDimming."
                "targetBy (server-side order rule)"
            )


@dataclass(frozen=True, slots=True)
class TamburCurveSnapshot:
    """One immutable evaluation snapshot (pure engine input)."""

    now_minutes: int                 # minutes since midnight
    sunrise_minutes: int | None      # None/unavailable = no sunrise proof
    sunset_minutes: int | None       # None/unavailable = no sunset proof
    sun_healthy: bool                # sun service healthy (vs lux fallback)
    outdoor_lux: float | None        # the outdoor lux reading, if any
    presence_confirmed: bool         # the current presence is confirmed
    absence_confirmed: bool          # the confirmed absence is active now
    away: bool = False

    def __post_init__(self) -> None:
        for label, value in (
            ("now", self.now_minutes),
            ("sunrise", self.sunrise_minutes),
            ("sunset", self.sunset_minutes),
        ):
            if value is not None and (
                type(value) is not int or not 0 <= value < 1440
            ):
                raise TamburCurveViolation(f"tambur curve {label} is invalid")
        if type(self.sun_healthy) is not bool:
            raise TamburCurveViolation("tambur curve sun_healthy is invalid")
        if self.outdoor_lux is not None and not isinstance(self.outdoor_lux, (int, float)):
            raise TamburCurveViolation("tambur curve outdoor lux is invalid")
        for label, value in (
            ("presence_confirmed", self.presence_confirmed),
            ("absence_confirmed", self.absence_confirmed),
            ("away", self.away),
        ):
            if type(value) is not bool:
                raise TamburCurveViolation(f"tambur curve {label} is invalid")


@dataclass(frozen=True, slots=True)
class TamburCurveDecision:
    """The computed maximum and the transition orders for one evaluation.

    ``mode`` is the current profile mode; ``required_percent`` is the
    computed maximum (the actual brightness is the caller's fade business);
    ``mirror_on``/``chandelier_on`` are the ORDERED states for the mode
    (the caller applies presence raises, absence declines and protections);
    ``transition_pending`` flags a wait_absence transition whose wall time
    has arrived but which waits for the confirmed absence (P-02/P-03).
    """

    mode: TamburMode
    reason: TamburReason
    required_percent: int
    chandelier_on: bool
    mirror_on: bool
    transition_pending: bool = False
    sun_source: str = "sun_service"   # sun_service | outdoor_lux | unknown

    def to_payload(self) -> dict[str, object]:
        return {
            "mode": self.mode.value,
            "reason": self.reason.value,
            "requiredPercent": self.required_percent,
            "chandelierOn": self.chandelier_on,
            "mirrorOn": self.mirror_on,
            "transitionPending": self.transition_pending,
            "sunSource": self.sun_source,
        }


LUX_SUNSET_THRESHOLD = 400.0  # ровно 400 — не закат (план §10)


def _effective_sunset(
    params: TamburCurveParams, snapshot: TamburCurveSnapshot
) -> tuple[int | None, str]:
    """Resolve the effective sunset per §3.1/§10.

    A healthy sun service is authoritative. An unhealthy service falls back
    to the outdoor lux (<400 = sunset; exactly 400 is NOT sunset). An
    unavailable sun with no lux proof returns None: the reserve never
    substitutes for an unavailable sun (L-07), and the evening decline waits
    for proof.
    """

    if snapshot.sun_healthy and snapshot.sunset_minutes is not None:
        return snapshot.sunset_minutes, "sun_service"
    if (
        snapshot.outdoor_lux is not None
        and snapshot.outdoor_lux < LUX_SUNSET_THRESHOLD
    ):
        return snapshot.now_minutes, "outdoor_lux"
    return None, "unknown"


def evaluate_tambur_curve(
    params: TamburCurveParams, snapshot: TamburCurveSnapshot
) -> TamburCurveDecision:
    """Pure parameterized evaluation (owner plan §3/§3.1/§5, matrix D/P/L)."""

    if snapshot.away:
        return TamburCurveDecision(
            TamburMode.AWAY, TamburReason.AWAY, 0, False, False,
            sun_source="unknown",
        )
    now = snapshot.now_minutes
    sunset, sun_source = _effective_sunset(params, snapshot)
    day_start = _minutes(params.day_start)
    full_by = _minutes(params.morning_full_by)
    target_by = _minutes(params.evening_target_by)
    reserve_start = _minutes(params.evening_reserve_start)
    mirror_at = _minutes(params.mirror_time)
    all_off_at = _minutes(params.all_off_time)
    target = params.evening_target_percent

    # --- morning mirror window (plan §3.1: sunrise..day_start) ------------
    # An EARLY, PROVEN sunrise (before day_start) lights the mirror from
    # the sunrise minute until day_start; the chandelier stays off and the
    # day_start boundary hands over to the day mode. A late (>= day_start)
    # or unavailable sunrise is NO proof: no morning mirror by guesswork
    # (L-06), the pre-dawn side stays dark.
    if (
        snapshot.sun_healthy
        and snapshot.sunrise_minutes is not None
        and snapshot.sunrise_minutes < day_start
        and snapshot.sunrise_minutes <= now < day_start
    ):
        return TamburCurveDecision(
            TamburMode.MIRROR, TamburReason.MIRROR, 0, False, True,
            sun_source=sun_source,
        )

    # --- 23:00 all off (P-03) — checked before the mirror window ----------
    if now >= all_off_at or now < day_start:
        # P-03: the wait_absence policy gates the all-off INCLUDING after
        # midnight: the calendar date change does not replace the proof of
        # the confirmed absence, so while a person is present the lights
        # stay held (transition pending) past 00:00 as well; the confirmed
        # absence completes the all-off (AUD-CURVE-04).
        if (
            params.all_off_policy is PresencePolicy.WAIT_ABSENCE
            and not snapshot.absence_confirmed
        ):
            return TamburCurveDecision(
                TamburMode.MIRROR, TamburReason.WAIT_ABSENCE,
                target, True, True, transition_pending=True,
                sun_source=sun_source,
            )
        return TamburCurveDecision(
            TamburMode.ALL_OFF, TamburReason.ALL_OFF, 0, False, False,
            sun_source=sun_source,
        )

    # --- 22:00 mirror transition (P-01/P-02) ------------------------------
    if now >= mirror_at:
        if (
            params.mirror_transition_policy is PresencePolicy.WAIT_ABSENCE
            and not snapshot.absence_confirmed
        ):
            # P-02: hold the current lights and flag the pending transition:
            # the caller completes it when the absence confirms.
            return TamburCurveDecision(
                TamburMode.MIRROR, TamburReason.WAIT_ABSENCE,
                target, True, False, transition_pending=True,
                sun_source=sun_source,
            )
        return TamburCurveDecision(
            TamburMode.MIRROR, TamburReason.MIRROR, target, False, True,
            sun_source=sun_source,
        )

    # --- day ceiling (L-05) and evening decline (sunset-lead..mirror) -----
    # The early-sunset ceiling (L-05): an early sunset (after day_start, no
    # later than full_by) stops the morning growth — the computed maximum
    # freezes at the ramp level of the sunset minute for the rest of the
    # day. A later or unavailable sunset keeps the full day maximum.
    early_ceiling = params.morning_full_percent
    if sunset is not None and day_start < sunset <= full_by:
        early_ratio = _ratio(sunset, day_start, full_by)
        early_ceiling = round(
            params.morning_start_percent
            + (params.morning_full_percent - params.morning_start_percent)
            * early_ratio
        )
    # The computed maximum declines toward the target, reaching it by
    # target_by (21:00). A genuinely LATE sunset (after target_by) starts
    # the decline at the CONFIGURED reserve start (L-03) — the owner's
    # setting is the decline start, it is never shifted by a fixed lead; an
    # already lower brightness is never raised (L-03, and the L-05 ceiling
    # keeps the decline below the frozen level). A sunset at or before
    # target_by starts the decline AT THE SUNSET (plan §3.1.4: the sunset
    # is authoritative). An unavailable sun is not replaced by the reserve
    # (L-07): the decline waits for proof.
    if sunset is not None:
        if sunset > target_by:
            decline_start = reserve_start
        else:
            decline_start = sunset
        if now >= decline_start:
            if now < target_by:
                ratio = _ratio(now, decline_start, target_by)
                computed = round(
                    early_ceiling - (early_ceiling - target) * ratio
                )
                computed = max(computed, target)
                return TamburCurveDecision(
                    TamburMode.EVENING_DECLINE,
                    TamburReason.RESERVE_DECLINE
                    if sunset > target_by
                    else TamburReason.EVENING_DECLINE,
                    computed, True, False,
                    sun_source=sun_source,
                )
            # target_by reached: hold at the target until the mirror time.
            return TamburCurveDecision(
                TamburMode.EVENING_HOLD, TamburReason.EVENING_HOLD,
                target, True, False,
                sun_source=sun_source,
            )

    # --- day ramp and day hold (before the evening decline) ---------------
    if now >= day_start:
        if now < full_by:
            ratio = _ratio(now, day_start, full_by)
            computed = round(
                params.morning_start_percent
                + (params.morning_full_percent - params.morning_start_percent)
                * ratio
            )
            computed = min(computed, early_ceiling)
            return TamburCurveDecision(
                TamburMode.RAMP, TamburReason.RAMP, computed, True, False,
                sun_source=sun_source,
            )
        return TamburCurveDecision(
            TamburMode.DAY, TamburReason.DAY,
            early_ceiling, True, False,
            sun_source=sun_source,
        )

    # --- pre-dawn (sunrise..day_start): dark day side (L-06) --------------
    # No morning mirror by guesswork for a late/unavailable sunrise.
    return TamburCurveDecision(
        TamburMode.PRE_DAY, TamburReason.PRE_DAWN, 0, False, False,
        sun_source=sun_source,
    )


__all__ = [
    "LUX_SUNSET_THRESHOLD",
    "PresencePolicy",
    "TamburCurveDecision",
    "TamburCurveParams",
    "TamburCurveSnapshot",
    "TamburCurveViolation",
    "TamburMode",
    "TamburReason",
    "evaluate_tambur_curve",
]
