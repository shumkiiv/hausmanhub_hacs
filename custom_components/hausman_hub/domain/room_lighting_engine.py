"""Deterministic room lighting engine.

Pure calculation: the engine only transforms (configuration + time + sensors +
lux + ownership + protection) into a desired state and a command plan. It never
imports Home Assistant and never sends a command; shadow mode keeps execution
disabled.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, time as dt_time, timedelta, tzinfo
from enum import StrEnum
import math

from .light_curve import (
    LightCurveContext,
    LightCurveDecision,
    LightCurveProfile,
    CurveReason,
    evaluate_light_curve,
    evening_mirror_window,
)
from .room_lighting import (
    AwayMode,
    LightRole,
    LightTarget,
    RoomLightingConfig,
    ScheduleEntry,
    ScheduleMode,
    SensorKind,
    resolve_anchor_time,
)
from .room_lighting_auxiliary import BathroomPolicy, BathroomTimes
from .room_lighting_ownership import (
    OwnershipSnapshot,
    OwnershipSource,
    SensorState,
    has_automatic_control,
    latest_ownership,
    release_expired_manual,
    resolve_manual_ownership,
    restore_after_restart,
)
from .tambur_curve import (
    LUX_SUNSET_THRESHOLD,
    PresencePolicy,
    TamburCurveParams,
    TamburCurveSnapshot,
    evaluate_tambur_curve,
)

MODE_SHADOW = "shadow"

# Saved dimming settings apply to legacy day-curve rooms too. This is the
# approved stable-absence window, not the separate manual-off protection.
LEGACY_CONFIGURED_ABSENCE_SECONDS = 600

_DAY_CODES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
# Remaining hardcoded boundary: before 23:00 the absence threshold is the
# longer one. Moving it into the schedule is a later step.
_EVENING_ABSENCE_BOUNDARY = dt_time(23, 0)
# The room lighting contract keeps ``timers.absence_seconds`` for
# compatibility with the initial draft; its default value is 30. A room that
# leaves it at that legacy default keeps the built-in 600/180 s thresholds,
# while any other value is an explicit uniform absence threshold for the room.
_LEGACY_TIMER_ABSENCE_SECONDS = 30


class LightAction(StrEnum):
    TURN_ON = "turn_on"
    TURN_OFF = "turn_off"
    SET_BRIGHTNESS = "set_brightness"
    SET_COLOR_TEMPERATURE = "set_color_temperature"


class DecisionReason(StrEnum):
    SCHEDULE = "schedule"
    PRESENCE = "presence"
    LUX = "lux"
    DIMMING = "dimming"
    NIGHT_LIGHT = "night_light"
    AWAY = "away"
    AWAY_RETURN = "away_return"
    RESTORE = "restore"


class SkipReason(StrEnum):
    QUIET_HOURS = "quiet_hours"
    MANUAL_OWNERSHIP = "manual_ownership"
    MANUAL_MODE = "manual_mode"
    MANUAL_PEER = "manual_peer"
    MANUAL_PROTECTION = "manual_protection"
    SENSOR_UNKNOWN = "sensor_unknown"
    SENSOR_STALE = "sensor_stale"
    ABSENCE_UNPROVEN = "absence_unproven"
    LUX_FAIL_CLOSED = "lux_fail_closed"
    IDEMPOTENT = "idempotent"
    NIGHT_MINIMUM = "night_minimum"
    NO_SCHEDULE = "no_schedule"
    UNOBSERVED = "unobserved"
    NO_AUTO_OWNERSHIP = "no_auto_ownership"


# A colour temperature round trip is Kelvin -> mired -> Kelvin and rounds to
# the nearest mired, so a desired 3000 K can read back as 3003 K. The engine
# must not chase that difference forever.
BRIGHTNESS_TOLERANCE = 2
COLOR_TEMPERATURE_TOLERANCE_KELVIN = 50


class RoomLightingEngineViolation(ValueError):
    """Engine input is malformed."""


@dataclass(frozen=True, slots=True)
class SensorSnapshot:
    sensor_id: str
    kind: SensorKind
    state: SensorState
    last_changed: int
    lux: float | None = None
    lux_healthy: bool = True
    continuous_absence_since: int | None = None
    observed_at_ms: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.sensor_id, str) or not self.sensor_id:
            raise RoomLightingEngineViolation("sensor id is required")
        if not isinstance(self.kind, SensorKind):
            raise RoomLightingEngineViolation("sensor kind is invalid")
        if not isinstance(self.state, SensorState):
            raise RoomLightingEngineViolation("sensor state is invalid")
        if type(self.last_changed) is not int or self.last_changed < 0:
            raise RoomLightingEngineViolation("sensor timestamp is invalid")
        if self.observed_at_ms is not None and (
            type(self.observed_at_ms) is not int or self.observed_at_ms < 0
        ):
            raise RoomLightingEngineViolation("sensor observation timestamp is invalid")
        if (
            self.continuous_absence_since is not None
            and (type(self.continuous_absence_since) is not int
                 or self.continuous_absence_since < 0)
        ):
            raise RoomLightingEngineViolation("continuous absence timestamp is invalid")


@dataclass(frozen=True, slots=True)
class LightSnapshot:
    target_id: str
    state: SensorState
    last_changed: int
    brightness: int | None = None
    color_temperature: int | None = None
    reported_state: SensorState | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.target_id, str) or not self.target_id:
            raise RoomLightingEngineViolation("light target id is required")
        if not isinstance(self.state, SensorState):
            raise RoomLightingEngineViolation("light state is invalid")
        if type(self.last_changed) is not int or self.last_changed < 0:
            raise RoomLightingEngineViolation("light timestamp is invalid")
        if self.reported_state is not None and not isinstance(
            self.reported_state, SensorState
        ):
            raise RoomLightingEngineViolation("reported light state is invalid")


@dataclass(frozen=True, slots=True)
class ProtectionSnapshot:
    active: bool = False
    started_at: int | None = None
    minimum_interval_seconds: int = 600
    stable_absence_seconds: int = 30
    release_mode: str = "timer_and_absence"
    reason: str = "none"
    absence_confirmed: bool = False
    absence_since: int | None = None
    # The moment presence returned, freezing the absence window end. While
    # the room is still empty this stays None and ``now`` is used instead:
    # post-return time must never stretch a shorter absence.
    absence_until: int | None = None
    # A completed stable absence has been proven since the last manual
    # action: the ownership release may fire on this latch even when the
    # LATEST absence window is shorter (short re-entries do not erase it).
    absence_latch: bool = False
    require_directed_off: bool = False

    def blocks_auto_on(
        self,
        *,
        now: int,
        absence_proven: bool,
        absence_since: int | None,
        absence_until: int | None = None,
        absence_latch: bool = False,
    ) -> bool:
        if not self.active:
            return False
        timer_ok = (
            self.started_at is not None
            and now - self.started_at >= self.minimum_interval_seconds * 1000
        )
        stable_end = absence_until if absence_until is not None else now
        absence_ok = (
            absence_proven
            and absence_since is not None
            # The absence must have started after the manual off: a historical
            # absence that predates the manual action must not release it.
            and self.started_at is not None
            and absence_since >= self.started_at
            and stable_end - absence_since >= self.stable_absence_seconds * 1000
        )
        # The proven stable-absence latch satisfies the absence condition on
        # its own (a completed >= stable window since the manual action). In
        # timer-including modes the minimum-interval timer stays mandatory;
        # an absence_only room releases on the absence per its own config and
        # never had a timer requirement.
        absence_ok = absence_ok or absence_latch
        if self.release_mode == "timer_only":
            return not timer_ok
        if self.release_mode == "absence_only":
            return not (absence_ok or absence_latch)
        return not (timer_ok and (absence_ok or absence_latch))


@dataclass(frozen=True, slots=True)
class RoomLightingContext:
    now: int
    timezone: tzinfo
    sunrise: dt_time
    sunset: dt_time
    sensors: tuple[SensorSnapshot, ...] = ()
    lights: tuple[LightSnapshot, ...] = ()
    ownership: tuple[OwnershipSnapshot, ...] = ()
    protection: ProtectionSnapshot = field(default_factory=ProtectionSnapshot)
    holiday: bool = False
    unobserved_since: int | None = None
    away: bool = False
    # Whether the sunrise/sunset evidence is provably from the sun service.
    # The default True keeps every legacy context unchanged; the parameterized
    # curve treats an unhealthy sun as no sunrise/sunset proof (L-06/L-07).
    sun_healthy: bool = True
    evening_handover_complete: bool = False
    # Runtime-local observation barrier, deliberately absent from saved state.
    observation_started_at: int | None = None

    def __post_init__(self) -> None:
        if type(self.now) is not int or self.now < 0:
            raise RoomLightingEngineViolation("context time is invalid")
        if not isinstance(self.timezone, tzinfo):
            raise RoomLightingEngineViolation("context timezone is required")
        if type(self.away) is not bool:
            raise RoomLightingEngineViolation("context away flag is invalid")
        if type(self.sun_healthy) is not bool:
            raise RoomLightingEngineViolation("context sun health is invalid")
        if type(self.evening_handover_complete) is not bool:
            raise RoomLightingEngineViolation("context evening handover flag is invalid")
        if self.observation_started_at is not None and (
            type(self.observation_started_at) is not int
            or self.observation_started_at < 0
        ):
            raise RoomLightingEngineViolation("context observation start is invalid")

    def light(self, target_id: str) -> LightSnapshot | None:
        for item in self.lights:
            if item.target_id == target_id:
                return item
        return None


@dataclass(frozen=True, slots=True)
class EnginePolicy:
    absence_seconds_before_night: int = 600
    absence_seconds_after_night: int = 180
    absence_seconds_uniform: int | None = None
    fade_seconds: int = 20
    sensor_freshness_seconds: int = 300
    staleness_seconds: int = 86400

    @classmethod
    def from_config(cls, config: RoomLightingConfig) -> "EnginePolicy":
        policy = cls()
        if config.dimming.enabled and config.dimming.fade_seconds > 0:
            policy = replace(policy, fade_seconds=config.dimming.fade_seconds)
        timers = config.timers
        if (
            timers is not None
            and timers.absence_seconds != _LEGACY_TIMER_ABSENCE_SECONDS
        ):
            # An explicit room value overrides both day and night thresholds.
            policy = replace(
                policy, absence_seconds_uniform=timers.absence_seconds
            )
        if config.manual_off_protection.enabled:
            minimum = config.manual_off_protection.minimum_interval_seconds
            policy = replace(
                policy,
                absence_seconds_before_night=max(
                    policy.absence_seconds_before_night, minimum
                ),
            )
        return policy


def _absence_threshold(policy: EnginePolicy, now_time: dt_time) -> int:
    """Return the proven-absence window before the automatic switch-off."""

    if policy.absence_seconds_uniform is not None:
        return policy.absence_seconds_uniform
    if now_time >= _EVENING_ABSENCE_BOUNDARY:
        return policy.absence_seconds_after_night
    return policy.absence_seconds_before_night


@dataclass(frozen=True, slots=True)
class PlannedCommand:
    target_id: str
    action: LightAction
    brightness: int | None = None
    color_temperature: int | None = None
    reason: DecisionReason = DecisionReason.SCHEDULE
    fade_seconds: int = 0


@dataclass(frozen=True, slots=True)
class Skip:
    target_id: str
    reason: SkipReason
    detail: str = ""


@dataclass(frozen=True, slots=True)
class TargetDecision:
    target_id: str
    desired_state: str
    desired_brightness: int | None
    desired_color_temperature: int | None
    commands: tuple[PlannedCommand, ...] = ()
    skips: tuple[Skip, ...] = ()


@dataclass(frozen=True, slots=True)
class RoomLightingDecision:
    room_id: str
    evaluated_at: int
    mode: str
    targets: tuple[TargetDecision, ...]
    commands_enabled: bool = False

    @property
    def commands(self) -> tuple[PlannedCommand, ...]:
        return tuple(
            command for target in self.targets for command in target.commands
        )

    @property
    def skips(self) -> tuple[Skip, ...]:
        return tuple(skip for target in self.targets for skip in target.skips)

    def to_payload(self) -> dict[str, object]:
        return {
            "roomId": self.room_id,
            "evaluatedAt": self.evaluated_at,
            "mode": self.mode,
            "commandsEnabled": self.commands_enabled,
            "targets": [
                {
                    "targetId": target.target_id,
                    "desiredState": target.desired_state,
                    "desiredBrightness": target.desired_brightness,
                    "desiredColorTemperature": target.desired_color_temperature,
                    "commands": [
                        {
                            "targetId": command.target_id,
                            "action": command.action.value,
                            "brightness": command.brightness,
                            "colorTemperature": command.color_temperature,
                            "reason": command.reason.value,
                            "fadeSeconds": command.fade_seconds,
                        }
                        for command in target.commands
                    ],
                    "skips": [
                        {
                            "targetId": skip.target_id,
                            "reason": skip.reason.value,
                            "detail": skip.detail,
                        }
                        for skip in target.skips
                    ],
                }
                for target in self.targets
            ],
        }


def evaluate_room_lighting(
    config: RoomLightingConfig,
    context: RoomLightingContext,
    *,
    policy: EnginePolicy | None = None,
    mode: str = MODE_SHADOW,
) -> RoomLightingDecision:
    """Compute the desired state and command plan without any side effect.

    When the context is away and the room uses ``room_off``, every target is
    switched off (reason ``away``), including ``autoControl=false`` manual-only
    targets, without requiring proven automatic ownership, because the
    deliberate departure is the evidence; nothing is turned on. Outside away,
    ``autoControl=false`` targets stay manual-only: the engine never commands
    them and only reports ``manual_mode``. On return (``away=False``) the
    normal schedule/presence evaluation simply resumes.
    """

    if not isinstance(config, RoomLightingConfig):
        raise RoomLightingEngineViolation("validated room lighting config is required")
    if not isinstance(context, RoomLightingContext):
        raise RoomLightingEngineViolation("validated room lighting context is required")
    policy = policy or EnginePolicy.from_config(config)

    now = context.now
    now_time = datetime.fromtimestamp(now / 1000, context.timezone).time()
    presence = _presence_state(context, policy)
    absence_proven, absence_since, absence_stale = _absence_evidence(context, policy)
    # A manual light owns the whole interchangeable source. If a person already
    # turned on one target of a group or schedule entry, automation must not
    # turn on a sibling source; it only observes until manual ownership clears.
    manual_targets = {
        target.id
        for target in config.devices.light_targets
        if (not target.auto_control and _light_on(context, target.id))
        or (
            resolve_manual_ownership(
                context.ownership, target.id, light_on=_light_on(context, target.id)
            )
            and not _manual_off_released(
                context, target.id, presence, absence_proven, absence_since
            )
        )
    }

    targets: list[TargetDecision] = []
    for target in config.devices.light_targets:
        schedule_entry = _active_schedule_entry(config, context, target)
        targets.append(
            _evaluate_target(
                target,
                config=config,
                context=context,
                policy=policy,
                now_time=now_time,
                presence=presence,
                absence_proven=absence_proven,
                absence_since=absence_since,
                absence_stale=absence_stale,
                schedule_entry=schedule_entry,
                manual_peer_blocked=_has_manual_peer(
                    config, target, schedule_entry, manual_targets
                ),
            )
        )
    return RoomLightingDecision(
        room_id=config.room_id,
        evaluated_at=now,
        mode=mode,
        targets=tuple(targets),
        commands_enabled=False,
    )


def bathroom_auxiliary_inputs(
    config: RoomLightingConfig,
) -> tuple[BathroomPolicy, BathroomTimes] | None:
    """Derive the pure bathroom exhaust inputs from one room configuration.

    Returns ``None`` when the room carries no auxiliary fan policy. Band
    boundaries are minute-of-day values, so the result stays free of any
    clock or timezone access; the runtime supplies the current minute and
    observations when it evaluates the auxiliary engine.
    """

    if not isinstance(config, RoomLightingConfig):
        raise RoomLightingEngineViolation("validated room lighting config is required")
    if config.auxiliary is None:
        return None
    fan = config.auxiliary.fan
    if fan is None:
        return None
    policy = BathroomPolicy(
        humidity_threshold=fan.humidity_threshold,
        day_off_seconds=fan.day_off_seconds,
    )
    times = BathroomTimes(
        quiet_start_minutes=_time_minutes(fan.quiet_start),
        day_start_minutes=_time_minutes(fan.day_start),
        night_start_minutes=_time_minutes(fan.night_start),
    )
    return policy, times


def _time_minutes(value: str) -> int:
    if not isinstance(value, str) or ":" not in value:
        raise RoomLightingEngineViolation("auxiliary band boundary must be HH:MM")
    hour, minute = (int(part) for part in value.split(":"))
    return hour * 60 + minute


def curve_profile_from_config(config: RoomLightingConfig) -> LightCurveProfile:
    """Day-curve profile for a room: engine defaults plus configured params.

    Rooms without a ``curveParams`` block keep the supplied document
    unchanged; configured values overlay only the evening ceiling and the
    room-local colour endpoints. Missing values keep their engine defaults.
    """

    if config.curve is None:
        return LightCurveProfile()
    defaults = LightCurveProfile()
    return LightCurveProfile(
        evening_max_percent=(
            config.curve.evening_max_percent
            if config.curve.evening_max_percent is not None
            else defaults.evening_max_percent
        ),
        neutral_kelvin=(
            config.curve.neutral_kelvin
            if config.curve.neutral_kelvin is not None
            else defaults.neutral_kelvin
        ),
        evening_warm_kelvin=config.curve.evening_warm_kelvin,
        presence_confirm_seconds=(config.curve.presence_confirm_seconds
                                  if config.curve.presence_confirm_seconds is not None
                                  else defaults.presence_confirm_seconds),
    )


def curve_params_block(config: RoomLightingConfig) -> TamburCurveParams | None:
    """The parameterized day curve of a room, or ``None`` for the legacy path.

    The new engine runs only for a ``day_curve`` room whose ``curveParams``
    block carries at least one v1.0 field; the legacy form
    (``eveningMaxPercent``) and rooms without the block keep the legacy
    engine byte-for-byte.
    """

    if config.profile != "day_curve" or config.curve is None:
        return None
    curve = config.curve
    if not curve.has_new_form:
        return None

    def _time(value: str | None, default: dt_time) -> dt_time:
        if value is None:
            return default
        hour, minute = (int(part) for part in value.split(":"))
        return dt_time(hour, minute)

    def _policy(value: str | None, default: PresencePolicy) -> PresencePolicy:
        if value is None:
            return default
        return PresencePolicy(value)

    return TamburCurveParams(
        day_start=_time(curve.day_start, dt_time(9, 0)),
        morning_start_percent=(
            20 if curve.morning_start_percent is None else curve.morning_start_percent
        ),
        morning_full_percent=(
            100 if curve.morning_full_percent is None else curve.morning_full_percent
        ),
        morning_full_by=_time(curve.morning_full_by, dt_time(12, 0)),
        evening_target_percent=(
            5 if curve.evening_target_percent is None else curve.evening_target_percent
        ),
        evening_target_by=_time(curve.evening_target_by, dt_time(21, 0)),
        evening_reserve_start=_time(curve.evening_reserve_start, dt_time(20, 0)),
        mirror_transition_policy=_policy(curve.mirror_transition, PresencePolicy.BY_TIME),
        all_off_policy=_policy(curve.all_off, PresencePolicy.BY_TIME),
        evening_dimming_policy=_policy(
            curve.evening_dimming_policy, PresencePolicy.BY_TIME
        ),
        confirmed_absence_seconds=(
            600 if curve.confirmed_absence_seconds is None
            else curve.confirmed_absence_seconds
        ),
        rise_seconds=(
            10 if curve.rise_seconds is None else curve.rise_seconds
        ),
    )


_TAMBUR_REASON_TO_CURVE = {
    "off": CurveReason.OFF,
    "night": CurveReason.NIGHT,
    "pre_dawn": CurveReason.OFF,
    "ramp": CurveReason.RAMP,
    "day": CurveReason.DAY,
    "evening_decline": CurveReason.EVENING,
    "evening_hold": CurveReason.LATE_EVENING,
    "reserve_decline": CurveReason.EVENING,
    "mirror": CurveReason.NIGHT,
    "all_off": CurveReason.NIGHT,
    "away": CurveReason.NIGHT,
    "wait_absence": CurveReason.UNKNOWN,
    "unknown": CurveReason.UNKNOWN,
}


def _tambur_kelvin(
    params: TamburCurveParams, now_minutes: int, sunset_minutes: int | None,
    profile: LightCurveProfile,
) -> int:
    """The agreed colour rule: neutral day, gentle evening warmth.

    The room profile supplies the neutral day (4000K by default), the
    unchanged morning endpoint (3400K), and an independent evening endpoint
    (3400K by default). The day holds neutral. The curve's §12.4 open question
    (the shape between the boundaries) stays untouched: the same smooth
    interpolation as the legacy curve is reused, not a new form.
    """

    full_by = params.morning_full_by.hour * 60 + params.morning_full_by.minute
    day_start = params.day_start.hour * 60 + params.day_start.minute
    target_by = params.evening_target_by.hour * 60 + params.evening_target_by.minute
    mirror_at = params.mirror_time.hour * 60 + params.mirror_time.minute
    if sunset_minutes is not None and now_minutes >= sunset_minutes:
        decline_start = min(
            day_start, params.evening_reserve_start.hour * 60 + params.evening_reserve_start.minute
        )
        start = decline_start if now_minutes >= decline_start else now_minutes
        ratio = _ratio_value(now_minutes, start, target_by)
        kelvin = profile.neutral_kelvin + (
            profile.evening_kelvin - profile.neutral_kelvin
        ) * ratio
        return round(kelvin)
    if now_minutes >= full_by:
        return profile.neutral_kelvin
    if now_minutes >= day_start:
        ratio = _ratio_value(now_minutes, day_start, full_by)
        kelvin = profile.warm_kelvin + (
            profile.neutral_kelvin - profile.warm_kelvin
        ) * ratio
        return round(kelvin)
    return profile.warm_kelvin


def _ratio_value(now: int, start: int, end: int) -> float:
    span = end - start
    if span <= 0 or now <= start:
        return 0.0
    if now >= end:
        return 1.0
    return (now - start) / span


def tambur_curve_light_decision(
    params: TamburCurveParams,
    config: RoomLightingConfig,
    context: RoomLightingContext,
    curve_profile: LightCurveProfile,
    *,
    presence: SensorState,
    presence_confirmed: bool,
    absence_seconds: int | None,
    motion_preview: bool = False,
    motion_rejected: bool = False,
    outdoor_lux: float | None = None,
) -> LightCurveDecision:
    """The parameterized curve decision in the shared decision shape.

    The pure ``evaluate_tambur_curve`` result supplies the time mode: the
    computed maximum, the chandelier/mirror order and the wait_absence
    holds. On top of it the shared occupancy semantics apply exactly like
    the legacy curve: a motion trial fades, an unknown presence holds, a
    confirmed presence raises the actual brightness to the computed
    maximum over ``riseSeconds``, and a confirmed absence starts the
    decline to ``dimming.targetPercent`` over the existing absence fade.
    """

    now_time = datetime.fromtimestamp(context.now / 1000, context.timezone).time()
    now_minutes = now_time.hour * 60 + now_time.minute
    sun_healthy = context.sun_healthy
    sunrise_minutes: int | None = (
        context.sunrise.hour * 60 + context.sunrise.minute if sun_healthy else None
    )
    sunset_minutes: int | None = (
        context.sunset.hour * 60 + context.sunset.minute if sun_healthy else None
    )
    if (
        not sun_healthy
        and outdoor_lux is not None
        and outdoor_lux < LUX_SUNSET_THRESHOLD
    ):
        # The documented outdoor-lux fallback (§10): an unavailable sun with
        # a dark outdoor reading proves the sunset; exactly 400 is not dark.
        sunset_minutes = now_minutes
    snapshot = TamburCurveSnapshot(
        now_minutes=now_minutes,
        sunrise_minutes=sunrise_minutes,
        sunset_minutes=sunset_minutes,
        sun_healthy=sun_healthy,
        outdoor_lux=outdoor_lux,
        presence_confirmed=presence_confirmed,
        absence_confirmed=(
            # The CONFIRMED absence is the CURRENT proven absence: the
            # tracker freezes the pre-return absence for the reduced-level
            # hold while a returning occupant is unconfirmed, and the
            # house rule is that NEW presence cancels the accrued absence.
            # A transition (mirror/all-off) or the evening decline may
            # therefore run only while the room is honestly empty — a
            # live ON reading voids it regardless of the frozen seconds
            # (AUD-CURVE-02).
            absence_seconds is not None
            and absence_seconds >= params.confirmed_absence_seconds
            and presence is SensorState.OFF
        ),
        away=context.away,
    )
    base = evaluate_tambur_curve(params, snapshot)
    reason = _TAMBUR_REASON_TO_CURVE[base.reason.value]
    kelvin = _tambur_kelvin(params, now_minutes, sunset_minutes, curve_profile)
    dimming = config.dimming
    mode_fade = curve_profile.mode_fade_seconds
    if not base.chandelier_on:
        if presence not in (SensorState.ON, SensorState.OFF):
            # A mode switch (mirror, all-off, morning mirror) must not
            # command the room while the presence evidence is unknown or
            # unavailable: the hold keeps the actual lights untouched
            # instead of turning the chandelier off over someone's head
            # (AUD-CURVE-05a).
            return LightCurveDecision(
                reason=CurveReason.UNKNOWN,
                chandelier_on=True,
                brightness_percent=None,
                color_temperature=None,
                fade_seconds=mode_fade,
                mirror_on=base.mirror_on,
                hold=True,
            )
        return LightCurveDecision(
            reason=reason,
            chandelier_on=False,
            brightness_percent=None,
            color_temperature=None,
            fade_seconds=mode_fade,
            mirror_on=base.mirror_on,
        )
    evening_wait_hold = (
        params.evening_dimming_policy is PresencePolicy.WAIT_ABSENCE
        and base.mode.value == "evening_decline"
        and not snapshot.absence_confirmed
    )
    if base.transition_pending or evening_wait_hold:
        # wait_absence holds: the 22:00/23:00 transition pending, or the
        # evening decline whose policy waits for the confirmed absence
        # (AUD-CURVE-03): the computed maximum keeps changing, but the
        # ACTUAL decline waits until the room is honestly empty. The
        # lights are left alone — no raise, no decline, no move toward
        # the mirror, no motion trial on top — until the caller confirms
        # the absence.
        return LightCurveDecision(
            reason=reason,
            chandelier_on=True,
            brightness_percent=None,
            color_temperature=None,
            fade_seconds=mode_fade,
            mirror_on=base.mirror_on,
            hold=True,
        )
    if (
        presence is SensorState.ON
        and not presence_confirmed
        and absence_seconds is not None
        and absence_seconds >= params.confirmed_absence_seconds
    ):
        # A returning occupant before the presence confirmation: the
        # tracker still reports the frozen pre-return absence (that hold
        # keeps the reduced level), so the curve neither declines further
        # nor raises to the computed maximum yet — the actual lights stay
        # exactly as they are until the presence confirms (AUD-CURVE-02).
        return LightCurveDecision(
            reason=reason,
            chandelier_on=True,
            brightness_percent=None,
            color_temperature=None,
            fade_seconds=mode_fade,
            mirror_on=base.mirror_on,
            hold=True,
        )
    if presence not in (SensorState.ON, SensorState.OFF):
        # An unknown or unavailable presence is not absence: the curve
        # holds without planning brightness moves (fail-closed on sensor
        # faults), and a motion trial cannot unlock it either.
        return LightCurveDecision(
            reason=CurveReason.UNKNOWN,
            chandelier_on=True,
            brightness_percent=None,
            color_temperature=kelvin,
            fade_seconds=mode_fade,
            mirror_on=base.mirror_on,
            hold=True,
        )
    if motion_rejected:
        return LightCurveDecision(
            reason=CurveReason.MOTION_REJECTED,
            chandelier_on=True,
            brightness_percent=min(
                curve_profile.evening_min_percent, base.required_percent
            ),
            color_temperature=kelvin,
            fade_seconds=curve_profile.motion_rejected_fade_seconds,
            mirror_on=base.mirror_on,
        )
    if motion_preview:
        return LightCurveDecision(
            reason=CurveReason.MOTION_PREVIEW,
            chandelier_on=True,
            brightness_percent=min(
                base.required_percent, curve_profile.motion_preview_percent
            ),
            color_temperature=kelvin,
            fade_seconds=curve_profile.motion_preview_fade_seconds,
            mirror_on=base.mirror_on,
        )
    if presence is SensorState.ON and presence_confirmed:
        return LightCurveDecision(
            reason=CurveReason.PRESENCE_RETURN,
            chandelier_on=True,
            brightness_percent=base.required_percent,
            color_temperature=kelvin,
            fade_seconds=params.rise_seconds,
            mirror_on=base.mirror_on,
        )
    if (
        absence_seconds is not None
        and absence_seconds >= params.confirmed_absence_seconds
        and dimming.enabled
        and dimming.on_absence
    ):
        # The confirmed-absence decline: the target stays dimming.targetPercent
        # (the approved decline goal), the fade duration is the existing
        # dimming.fadeSeconds. A room with dimming disabled keeps the mode
        # level — the same rule as the legacy curve's enabled flag. The
        # effective target never EXCEEDS the current computed maximum: the
        # computed maximum caps the actual brightness from above (spec
        # 4.2), so a saved 40% goal cannot RAISE an empty evening room
        # whose maximum has already declined to 5% (AUD-CURVE-06).
        target = min(dimming.target_percent, base.required_percent)
        progress = min(
            1.0,
            (absence_seconds - params.confirmed_absence_seconds)
            / max(1, dimming.fade_seconds),
        )
        level = base.required_percent - (
            base.required_percent - target
        ) * progress
        remaining = max(
            1,
            int(
                dimming.fade_seconds
                - (absence_seconds - params.confirmed_absence_seconds)
            ),
        )
        return LightCurveDecision(
            reason=CurveReason.ABSENCE,
            chandelier_on=True,
            brightness_percent=round(level),
            color_temperature=kelvin,
            fade_seconds=remaining if progress < 1.0 else 0,
            mirror_on=base.mirror_on,
        )
    return LightCurveDecision(
        reason=reason,
        chandelier_on=True,
        brightness_percent=base.required_percent,
        color_temperature=kelvin,
        fade_seconds=mode_fade,
        mirror_on=base.mirror_on,
    )


def corridor_outdoor_motion_gate(config: RoomLightingConfig, context: RoomLightingContext) -> bool:
    """Require fresh outdoor darkness and motion for corridor auto-on."""
    if config.room_id != "malyi_koridor":
        return True
    if config.behaviors is None or not config.behaviors.only_when_dark:
        return False
    if config.illumination is None or config.illumination.sensor != "sensor.0x00124b003571fd85_illuminance":
        return False
    configured_motion = {
        sensor.id for sensor in config.devices.sensors if sensor.kind is SensorKind.MOTION
    }
    configured_entities = {
        sensor.entity_id for sensor in config.devices.sensors if sensor.kind is SensorKind.MOTION
    }
    if len(configured_motion) != 2 or configured_entities != {
        "binary_sensor.0xf044d3fffebaf11b_occupancy",
        "binary_sensor.0xf044d3fffe11964d_occupancy",
    }:
        return False
    lux, failed = _lux_value(context, config, EnginePolicy())
    if failed or lux is None or not math.isfinite(lux) or not 0 <= lux < 400:
        return False
    return any(
        sensor.sensor_id in configured_motion and sensor.kind is SensorKind.MOTION
        and sensor.state is SensorState.ON
        and _is_fresh(sensor, context, EnginePolicy())
        for sensor in context.sensors
    )


def evaluate_curve_room(
    config: RoomLightingConfig,
    context: RoomLightingContext,
    *,
    presence_confirmed: bool,
    absence_seconds: int | None,
    presence: SensorState | None = None,
    motion_preview: bool = False,
    motion_rejected: bool = False,
    profile: LightCurveProfile | None = None,
    independent_targets: bool = False,
    target_protections: dict[str, ProtectionSnapshot] | None = None,
) -> RoomLightingDecision:
    target_ids = {target.id for target in config.devices.light_targets}
    scoped_protections = (target_protections if independent_targets
                          and isinstance(target_protections, dict)
                          and set(target_protections) == target_ids
                          and all(isinstance(value, ProtectionSnapshot)
                                  for value in target_protections.values()) else None)
    now_time = datetime.fromtimestamp(context.now / 1000, context.timezone).time()
    if config.room_id == "malyi_koridor" and (now_time >= dt_time(23, 30) or now_time < dt_time(9, 0)):
        off = LightCurveDecision(reason=CurveReason.NIGHT, chandelier_on=False)
        targets = tuple(
            _curve_main_decision(target, off, context)
            if has_automatic_control(context.ownership, target.id)
            else _unchanged(target.id, Skip(target.id, SkipReason.MANUAL_OWNERSHIP))
            for target in config.devices.light_targets
        )
        return RoomLightingDecision(room_id=config.room_id, evaluated_at=context.now,
                                    mode=MODE_SHADOW, targets=targets)
    if (not context.away and config.room_id == "malyi_koridor" and config.illumination is not None
            and config.illumination.sensor == "sensor.0x00124b003571fd85_illuminance"):
        lux, failed = _lux_value(context, config, EnginePolicy())
        if not failed and lux is not None and math.isfinite(lux) and lux > 400:
            blocked = curve_profile_block_reason(config, context)
            if blocked is not None:
                return RoomLightingDecision(room_id=config.room_id, evaluated_at=context.now,
                    mode=MODE_SHADOW, targets=tuple(
                        _unchanged(target.id, Skip(target.id, blocked))
                        for target in config.devices.light_targets))
            off = LightCurveDecision(reason=CurveReason.OFF, chandelier_on=False)
            def bright_target(target: object) -> TargetDecision:
                record = latest_ownership(context.ownership, target.id)
                light = context.light(target.id)
                if (not has_automatic_control(context.ownership, target.id)
                        or record is None or record.at > context.now
                        or (context.unobserved_since is not None and
                            record.at < context.unobserved_since)
                        or (light is not None and light.last_changed > record.at)):
                    return _unchanged(target.id, Skip(target.id, SkipReason.MANUAL_OWNERSHIP))
                result = _curve_main_decision(target, off, context)
                return replace(result, commands=tuple(
                    replace(command, reason=DecisionReason.LUX) for command in result.commands
                ))
            targets = tuple(bright_target(target) for target in config.devices.light_targets)
            return RoomLightingDecision(room_id=config.room_id, evaluated_at=context.now,
                                        mode=MODE_SHADOW, targets=targets)
    planning_context = (replace(context, protection=replace(context.protection, active=False))
                        if scoped_protections is not None and not context.away else context)
    decision = _evaluate_curve_room(
        config, planning_context, presence_confirmed=presence_confirmed,
        absence_seconds=absence_seconds, presence=presence,
        motion_preview=motion_preview, motion_rejected=motion_rejected,
        profile=profile, independent_targets=independent_targets,
    )
    if independent_targets and not (context.away and config.away_behavior.mode is AwayMode.ROOM_OFF):
        # The caller must also prove and enforce the physical-pair graph at
        # dispatch. This flag alone does not grant permission to send commands.
        def protect_target(target: TargetDecision) -> TargetDecision:
            target_context = (replace(context, protection=scoped_protections[target.target_id])
                              if scoped_protections is not None else context)
            reason = curve_target_block_reason(config, target_context, target.target_id)
            return (_unchanged(target.target_id, Skip(target.target_id, reason))
                    if reason is not None else target)
        decision = replace(decision, targets=tuple(protect_target(t) for t in decision.targets))
    if corridor_outdoor_motion_gate(config, context):
        return decision
    def gated_target(target: TargetDecision) -> TargetDecision:
        light = context.light(target.target_id)
        current = light.brightness if light is not None else None
        commands = tuple(command for command in target.commands if not (
            command.action is LightAction.TURN_ON or (
                command.action is LightAction.SET_BRIGHTNESS
                and (current is None or command.brightness is None or command.brightness > current)
            )
        ))
        if commands == target.commands:
            return target
        skip = Skip(target.target_id, SkipReason.LUX_FAIL_CLOSED,
                    "Ожидается движение и свежая наружная освещённость ниже 400 лк")
        return replace(target, commands=commands, skips=target.skips + (skip,))

    targets = tuple(gated_target(target) for target in decision.targets)
    return replace(decision, targets=targets)


def _evaluate_curve_room(
    config: RoomLightingConfig,
    context: RoomLightingContext,
    *,
    presence_confirmed: bool,
    absence_seconds: int | None,
    presence: SensorState | None = None,
    motion_preview: bool = False,
    motion_rejected: bool = False,
    profile: LightCurveProfile | None = None,
    independent_targets: bool = False,
) -> RoomLightingDecision:
    """Day-curve decisions for the room's main and mirror targets.

    The time of day, sunrise and sunset define the mode; presence only returns
    a reduced brightness. A confirmed manual action still blocks the target so
    a person keeps priority over the configured curve.
    """

    if not isinstance(config, RoomLightingConfig):
        raise RoomLightingEngineViolation("validated room lighting config is required")
    if not isinstance(context, RoomLightingContext):
        raise RoomLightingEngineViolation("validated room lighting context is required")
    if config.profile != "day_curve":
        raise RoomLightingEngineViolation("room does not use the day curve profile")

    # Explicit room-off on a confirmed departure is shared by every profile.
    # Delegate before the curve's manual hold, including accent/manual-only
    # lights. Dispatch still validates the departure and its manual boundary.
    if context.away and config.away_behavior.mode is AwayMode.ROOM_OFF:
        return evaluate_room_lighting(config, context)

    now_time = datetime.fromtimestamp(context.now / 1000, context.timezone).time()
    if presence is None:
        presence = _presence_state(context, EnginePolicy())
    main_target = next(
        (item for item in config.devices.light_targets if item.role is LightRole.MAIN),
        None,
    )
    mirror_target = next(
        (item for item in config.devices.light_targets if item.role is LightRole.MIRROR),
        None,
    )
    chandelier = context.light(main_target.id) if main_target is not None else None
    mirror = context.light(mirror_target.id) if mirror_target is not None else None
    blocked = curve_profile_block_reason(config, context)
    if independent_targets and blocked in {
        SkipReason.MANUAL_OWNERSHIP, SkipReason.MANUAL_MODE, SkipReason.UNOBSERVED,
    }:
        blocked = None
    if blocked is not None:
        return RoomLightingDecision(
            room_id=config.room_id, evaluated_at=context.now, mode=MODE_SHADOW,
            targets=tuple(
                _unchanged(item.id, Skip(item.id, blocked))
                for item in (main_target, mirror_target) if item is not None
            ),
        )
    curve_params = curve_params_block(config)
    if curve_params is not None:
        curve = tambur_curve_light_decision(
            curve_params,
            config,
            context,
            profile if profile is not None else curve_profile_from_config(config),
            presence=presence,
            presence_confirmed=presence_confirmed,
            absence_seconds=absence_seconds,
            motion_preview=motion_preview,
            motion_rejected=motion_rejected,
        )
        if curve.hold:
            # The parameterized hold (wait_absence pending, or an unknown
            # presence) leaves BOTH targets untouched: no turn-on without
            # brightness, no mirror handover — the transition completes
            # when the confirmed absence arrives (P-02/P-03). The legacy
            # engine path below is unchanged.
            return RoomLightingDecision(
                room_id=config.room_id, evaluated_at=context.now, mode=MODE_SHADOW,
                targets=tuple(
                    _unchanged(
                        item.id,
                        Skip(
                            item.id,
                            SkipReason.ABSENCE_UNPROVEN,
                            "Профиль держит свет: переход ожидает подтверждённое отсутствие",
                        ),
                    )
                    for item in (main_target, mirror_target) if item is not None
                ),
            )
    else:
        curve = evaluate_light_curve(
            profile if profile is not None else curve_profile_from_config(config),
            LightCurveContext(
                now=now_time,
                sunrise=context.sunrise,
                sunset=context.sunset,
                presence=presence,
                chandelier=(
                    chandelier.state if chandelier is not None else SensorState.UNKNOWN
                ),
                mirror=mirror.state if mirror is not None else SensorState.UNKNOWN,
                presence_confirmed=presence_confirmed,
                absence_seconds=absence_seconds,
                motion_preview=motion_preview,
                motion_rejected=motion_rejected,
                evening_handover_complete=context.evening_handover_complete,
            ),
            apply_absence_dimming=False,
        )
        # Keep every legacy time/source/colour decision. Only the absence
        # reduction uses the already saved settings, without migrating the
        # room to the parameterized profile. Send the final device goal once
        # with its transition, not intermediate goals with long transitions.
        absence_ready = (
            absence_seconds is not None
            and absence_seconds >= LEGACY_CONFIGURED_ABSENCE_SECONDS
            and config.dimming.enabled and config.dimming.on_absence
            and not motion_preview and not motion_rejected
            and curve.chandelier_on and not curve.hold
        )
        if absence_ready and presence is SensorState.ON and not presence_confirmed:
            curve = replace(curve, brightness_percent=None)
        elif (absence_ready and presence is SensorState.OFF
                and curve.brightness_percent is not None):
            curve = replace(
                curve, reason=CurveReason.ABSENCE,
                brightness_percent=min(config.dimming.target_percent, curve.brightness_percent),
                fade_seconds=config.dimming.fade_seconds,
            )

    if curve.hold and curve_params is None and evening_mirror_window(now_time):
        return RoomLightingDecision(
            room_id=config.room_id, evaluated_at=context.now, mode=MODE_SHADOW,
            targets=tuple(_unchanged(item.id, Skip(item.id, SkipReason.ABSENCE_UNPROVEN,
                          "Переход ожидает достоверное состояние света и датчиков"))
                          for item in config.devices.light_targets),
        )

    # A legacy all-off mode covers every configured light, not only the
    # first main/mirror pair. Keep the ordinary ownership/manual/unknown
    # gates and the mirror's existing no-fade OFF semantics. Parameterized
    # curves keep their own policy; this does not migrate their settings.
    if (curve_params is None and not curve.hold
            and not curve.chandelier_on and not curve.mirror_on):
        return RoomLightingDecision(
            room_id=config.room_id, evaluated_at=context.now, mode=MODE_SHADOW,
            targets=tuple(
                _curve_mirror_decision(item, curve, context)
                if item.role is LightRole.MIRROR
                else _curve_main_decision(item, curve, context)
                for item in config.devices.light_targets
            ),
        )

    targets: list[TargetDecision] = []
    early_mirror = (
        curve_params is None and evening_mirror_window(now_time)
        and curve.reason is CurveReason.NIGHT and curve.mirror_on
        and not curve.chandelier_on
    )
    if early_mirror:
        return _early_mirror_handover(
            config, context, curve, mirror_target,
            absence_proven=(
                presence is SensorState.OFF and absence_seconds is not None
                and not motion_preview
            ),
        )

    main_decision = None
    # When a manual hold exists for the room (a restored or fresh manual
    # record), the auto-on requires proven presence: a latched release
    # resumes automation, but the physical turn-on waits for the curve's
    # own presence confirmation (variant a): an UNKNOWN/unavailable sensor
    # keeps the room dark (fail-closed). The confirmation flag alone does
    # not pass the gate: a synthetic wake (an unlock entry wakeup) without
    # a valid sensor ON is not presence, so the gate also requires the
    # observed ON state. A motion preview is likewise not confirmation:
    # until the presence sensor itself reports ON (the promotion step of
    # the confirmation cycle), the preview must not light a held room. The
    # curve tracker and the motion promotion only confirm while the
    # aggregate reading is ON, so the honest paths are unaffected.
    manual_hold = any(
        _proven_manual_ownership(context.ownership, item.id)
        for item in (main_target, mirror_target) if item is not None
    )
    presence_gate = (
        manual_hold
        and not (presence_confirmed and presence is SensorState.ON)
    )
    if main_target is not None:
        main_decision = _curve_main_decision(
            main_target, curve, context, presence_gate=presence_gate
        )
        targets.append(main_decision)
    if mirror_target is not None:
        # Two observed phases: establish the main state and settings first,
        # then remove the automatically-owned mirror on a later evaluation.
        # A plan or an accepted command is not a confirmed replacement light.
        waiting_for_main = (
            main_target is not None
            and curve.chandelier_on and _light_on(context, mirror_target.id)
            and (
                not main_target.auto_control
                or not _light_on(context, main_target.id)
                or (
                    main_target.brightness
                    and (chandelier is None or chandelier.brightness is None
                         or chandelier.brightness <= 0)
                )
                or not has_automatic_control(context.ownership, main_target.id)
                or main_decision is None or bool(main_decision.commands)
                or any(skip.reason is not SkipReason.IDEMPOTENT for skip in main_decision.skips)
            )
        )
        targets.append(
            _unchanged(mirror_target.id, Skip(
                mirror_target.id, SkipReason.UNOBSERVED,
                "Ожидается подтверждение дневного источника и его настроек",
            )) if waiting_for_main else _curve_mirror_decision(
                mirror_target, curve, context, presence_gate=presence_gate
            )
        )
    return RoomLightingDecision(
        room_id=config.room_id,
        evaluated_at=context.now,
        mode=MODE_SHADOW,
        targets=tuple(targets),
    )


def _early_mirror_handover(
    config: RoomLightingConfig, context: RoomLightingContext,
    curve: LightCurveDecision, mirror_target: LightTarget | None,
    *, absence_proven: bool,
) -> RoomLightingDecision:
    """Confirm the replacement before removing any automatic main/accent."""
    mirror_ready = (
        mirror_target is not None and mirror_target.auto_control
        and _light_on(context, mirror_target.id)
        and has_automatic_control(context.ownership, mirror_target.id)
    )
    # Expiry or an absence latch alone must not relight a manual-OFF room.
    # This is a scheduled handover, not the confirmed-presence return path.
    manual_hold = any(
        _proven_manual_ownership(context.ownership, item.id)
        for item in config.devices.light_targets
    )
    targets: list[TargetDecision] = []
    for target in config.devices.light_targets:
        if target.role is LightRole.MIRROR:
            targets.append(_curve_mirror_decision(
                target, curve, context, presence_gate=manual_hold,
            ))
        elif target.role in (LightRole.MAIN, LightRole.ACCENT):
            targets.append(
                _curve_main_decision(target, curve, context) if mirror_ready and absence_proven
                else _unchanged(target.id, Skip(
                    target.id,
                    SkipReason.UNOBSERVED if not mirror_ready else SkipReason.ABSENCE_UNPROVEN,
                    "Ожидается подтверждение зеркала перед вечерним переключением"
                    if not mirror_ready else "Переключение ожидает подтверждённое отсутствие",
                ))
            )
    return RoomLightingDecision(
        room_id=config.room_id, evaluated_at=context.now,
        mode=MODE_SHADOW, targets=tuple(targets),
    )


def _effective_light_off(light, now: int, manual_at: int | None) -> bool:
    """Единое правило состояния света (иерархия доказательств, RP-10).

    Решения движка читают состояние цели только через эту функцию. Отчёт
    лампы «включена» доказывает текущее состояние лишь пока не опровергнут
    более сильным свидетельством:

    1. Доказанный отчёт «выключена» — сильнее любого «включена».
    2. Свежесть: отчёт «включена» старше окна свежести не доказывает
       настоящее (лампа могла потерять питание и не успеть отчитаться —
       клавиша выключателя обесточивает цепь).
    3. Приоритет человека: отчёт «включена», сделанный ДО последнего
       ручного действия, перекрыт этим действием.

    Слои применения одного правила: контекст рантайма (пара
    «питание комнаты → цели», `_apply_proven_power_pair`), решения
    движка (эта функция: `_curve_main_decision`, `_curve_mirror_decision`,
    `_curve_manual_blocked`, `_manual_off_released`), отправка (захваченное
    доказательство пары и `_untrusted_on_voided_by_manual_off` в диспетчере).
    Новое правило не добавляет исключений поверх старых — оно уточняет
    чтение состояния, которым все существующие защиты уже пользуется."""

    if light is None:
        return False
    if light.state is SensorState.OFF or light.reported_state is SensorState.OFF:
        return True
    return (
        light.state is SensorState.UNKNOWN
        and _reported_on_is_void(light, now, manual_at)
    )


def _reported_on_is_void(light, now: int, manual_at: int | None) -> bool:
    """Whether a reported ``on`` cannot prove a current selection.

    A bulb that lost power before it could report (the wall-key pair) keeps
    its last ``on`` report indefinitely. The report is void when it is
    older than the freshness window or when it predates the newest manual
    action: the person's own switch-off happened AFTER that report, so the
    light cannot honestly be on."""

    if light.reported_state is not SensorState.ON:
        return False
    if now - light.last_changed > EnginePolicy().sensor_freshness_seconds * 1000:
        return True
    return manual_at is not None and light.last_changed < manual_at


def _curve_manual_blocked(context: RoomLightingContext, target_id: str) -> bool:
    """Never expire manual ON; release OFF only by the configured evidence."""

    light = context.light(target_id)
    if light is not None and light.state is SensorState.ON:
        return not has_automatic_control(context.ownership, target_id)
    latest = latest_ownership(context.ownership, target_id)
    if latest is None or latest.source is not OwnershipSource.MANUAL:
        return False
    manual_at = latest.at
    light_off = _effective_light_off(light, context.now, manual_at)
    return not (
        light_off
        and release_expired_manual(
            context.ownership, target_id, now=context.now,
            minimum_interval_seconds=context.protection.minimum_interval_seconds,
            stable_absence_seconds=context.protection.stable_absence_seconds,
            absence_confirmed=context.protection.absence_confirmed,
            absence_since=context.protection.absence_since,
            absence_until=context.protection.absence_until,
            absence_latch=context.protection.absence_latch,
            release_mode=context.protection.release_mode,
            require_directed_off=context.protection.require_directed_off,
        )
    )


def curve_target_block_reason(
    config: RoomLightingConfig, context: RoomLightingContext, target_id: str,
) -> SkipReason | None:
    """Evaluate one target while retaining unresolved room-wide safeguards.

    This planning check does not replace the physical-pair authority or the
    final dispatch check. Callers must supply those independent safeguards.
    """
    target = config.devices.target(target_id)
    if target is None:
        return SkipReason.UNOBSERVED
    if context.away or context.protection.blocks_auto_on(
        now=context.now, absence_proven=context.protection.absence_confirmed,
        absence_since=context.protection.absence_since,
        absence_until=context.protection.absence_until,
        absence_latch=context.protection.absence_latch,
    ):
        return SkipReason.MANUAL_PROTECTION
    if _curve_manual_blocked(context, target.id):
        return SkipReason.MANUAL_OWNERSHIP
    light = context.light(target.id)
    if light is not None and light.state is SensorState.ON:
        if not target.auto_control:
            return SkipReason.MANUAL_MODE
        latest = latest_ownership(context.ownership, target.id)
        if (context.unobserved_since is not None and latest is not None
                and context.unobserved_since > latest.at):
            return SkipReason.UNOBSERVED
    return None


def curve_profile_block_reason(
    config: RoomLightingConfig, context: RoomLightingContext,
) -> SkipReason | None:
    """One manual source protects the whole interchangeable curve profile."""

    if context.away:
        return SkipReason.MANUAL_PROTECTION
    if context.protection.blocks_auto_on(
        now=context.now, absence_proven=context.protection.absence_confirmed,
        absence_since=context.protection.absence_since,
        absence_until=context.protection.absence_until,
        absence_latch=context.protection.absence_latch,
    ):
        return SkipReason.MANUAL_PROTECTION
    for target in config.devices.light_targets:
        if _curve_manual_blocked(context, target.id):
            return SkipReason.MANUAL_OWNERSHIP
        light = context.light(target.id)
        if light is not None and light.state is SensorState.ON:
            if not target.auto_control:
                return SkipReason.MANUAL_MODE
            latest = latest_ownership(context.ownership, target.id)
            if (
                context.unobserved_since is not None and latest is not None
                and context.unobserved_since > latest.at
            ):
                return SkipReason.UNOBSERVED
    return None


def _curve_main_decision(
    target: object,
    curve: object,
    context: RoomLightingContext,
    *,
    presence_gate: bool = False,
) -> TargetDecision:
    target_id = target.id  # type: ignore[attr-defined]
    light = context.light(target_id)
    reported = light.reported_state if light is not None else None
    latest_manual = latest_ownership(context.ownership, target_id)
    manual_at = (
        latest_manual.at
        if latest_manual is not None and latest_manual.source is OwnershipSource.MANUAL
        else None
    )
    light_off = _effective_light_off(light, context.now, manual_at)
    # Подтверждённое авто-владение — собственное доказательство автоматики:
    # пока нет противоречащего свежего свидетельства (свежий OFF, снятое
    # питание, ручное действие) и ненаблюдаемого разрыва, выключение по
    # расписанию опирается на подтверждённое включение, а не на свежесть
    # отчёта. Явный OFF сильнее владения: авто-запись создаётся после любой
    # подтверждённой команды, включая выключение.
    latest_record = latest_ownership(context.ownership, target_id)
    auto_believed_on = (
        light is not None
        and latest_record is not None
        and latest_record.confirmed
        and has_automatic_control(context.ownership, target_id)
        and light.state is not SensorState.OFF
        and light.reported_state is not SensorState.OFF
        and not (
            context.unobserved_since is not None
            and context.unobserved_since > latest_record.at
        )
    )
    light_on = light is not None and (
        (
            not light_off
            and (light.state is SensorState.ON or reported is SensorState.ON)
        )
        or auto_believed_on
    )
    if not target.auto_control:  # type: ignore[attr-defined]
        return _unchanged(target_id, Skip(target_id, SkipReason.MANUAL_MODE))
    if (
        light is not None
        and light.state is SensorState.UNKNOWN
        and latest_record is None
        and _reported_on_is_void(light, context.now, None)
    ):
        # A stale ON cannot prove OFF without separate evidence of power or
        # ownership; do not plan a command or claim it is already off.
        return _unchanged(target_id, Skip(target_id, SkipReason.SENSOR_UNKNOWN))
    if light is None or (
        light.state in (SensorState.UNKNOWN, SensorState.UNAVAILABLE)
        and reported not in (SensorState.ON, SensorState.OFF)
    ):
        # A missing, unavailable or restored target is never treated as off.
        # A stale but known on/off is still usable for schedule planning.
        return _unchanged(target_id, Skip(target_id, SkipReason.SENSOR_UNKNOWN))
    if _curve_manual_blocked(context, target_id):
        return _unchanged(target_id, Skip(target_id, SkipReason.MANUAL_OWNERSHIP))

    # When the manual protection is active and the presence is not
    # confirmed, the auto-on waits for a fresh confirmed presence reading:
    # a latched release resumes automation, but the physical turn-on still
    # requires valid presence evidence (fail-closed on sensor faults).
    if presence_gate and not light_on:
        return _unchanged(
            target_id, Skip(target_id, SkipReason.NIGHT_MINIMUM,
                            "защита активна: ожидается подтверждённое присутствие"),
        )
    fade = int(curve.fade_seconds)  # type: ignore[attr-defined]
    if not curve.chandelier_on:  # type: ignore[attr-defined]
        if not light_on:
            return _unchanged(target_id, Skip(target_id, SkipReason.IDEMPOTENT))
        return TargetDecision(
            target_id=target_id,
            desired_state="off",
            desired_brightness=None,
            desired_color_temperature=None,
            commands=(
                PlannedCommand(
                    target_id,
                    LightAction.TURN_OFF,
                    reason=DecisionReason.SCHEDULE,
                    fade_seconds=fade,
                ),
            ),
        )

    commands: list[PlannedCommand] = []
    brightness = curve.brightness_percent  # type: ignore[attr-defined]
    kelvin = curve.color_temperature  # type: ignore[attr-defined]
    if not light_on:
        # Включающая команда несёт целевые яркость и оттенок: светильник
        # включается сразу на уровне кривой, без отдельной регулировки к
        # уже включённому светильнику (защита от дубликатов её блокирует).
        commands.append(
            PlannedCommand(
                target_id,
                LightAction.TURN_ON,
                brightness=(
                    brightness
                    if target.brightness and brightness is not None
                    else None
                ),
                color_temperature=(
                    kelvin
                    if target.color_temperature and kelvin is not None
                    else None
                ),
                reason=DecisionReason.SCHEDULE,
                fade_seconds=fade,
            )
        )
    if target.brightness and brightness is not None:  # type: ignore[attr-defined]
        current = light.brightness if light is not None else None
        # Отдельная регулировка — только для уже включённого светильника:
        # после turn_on уровень задан самой включающей командой. Если текущая
        # яркость неизвестна (молчащий включённый светильник), планируем
        # абсолютную целевую команду: она несёт уровень и не является слепым
        # повтором, а dispatch ограничивает её одной попыткой на доказательства.
        if light_on and (
            current is None or abs(current - brightness) > BRIGHTNESS_TOLERANCE
        ):
            commands.append(
                PlannedCommand(
                    target_id,
                    LightAction.SET_BRIGHTNESS,
                    brightness=brightness,
                    reason=DecisionReason.SCHEDULE,
                    fade_seconds=fade,
                )
            )
    if target.color_temperature and kelvin is not None:  # type: ignore[attr-defined]
        # Оттенок к включённому светильнику — только как регулировка после
        # включения (turn_on уже несёт оттенок).
        current_kelvin = light.color_temperature if light is not None else None
        if light_on and (
            current_kelvin is None
            or abs(current_kelvin - kelvin) > COLOR_TEMPERATURE_TOLERANCE_KELVIN
        ):
            commands.append(
                PlannedCommand(
                    target_id,
                    LightAction.SET_COLOR_TEMPERATURE,
                    color_temperature=kelvin,
                    reason=DecisionReason.SCHEDULE,
                    fade_seconds=fade,
                )
            )
    if not commands:
        return _unchanged(target_id, Skip(target_id, SkipReason.IDEMPOTENT))
    return TargetDecision(
        target_id=target_id,
        desired_state="on",
        desired_brightness=brightness if target.brightness else None,  # type: ignore[attr-defined]
        desired_color_temperature=(
            kelvin if target.color_temperature else None  # type: ignore[attr-defined]
        ),
        commands=tuple(commands),
    )


def _curve_mirror_decision(
    target: object,
    curve: object,
    context: RoomLightingContext,
    *,
    presence_gate: bool = False,
) -> TargetDecision:
    target_id = target.id  # type: ignore[attr-defined]
    light = context.light(target_id)
    reported = light.reported_state if light is not None else None
    # One light-state rule for every engine decision (RP-10): a stale or
    # overridden reported ``on`` proves nothing, exactly like for the main
    # target (see _effective_light_off).
    latest_record = latest_ownership(context.ownership, target_id)
    manual_at = (
        latest_record.at
        if latest_record is not None and latest_record.source is OwnershipSource.MANUAL
        else None
    )
    light_off = _effective_light_off(light, context.now, manual_at)
    # Same rule as the main target: proven auto ownership is the
    # automation's own evidence — the scheduled handover off stays armed
    # until a fresh contradicting report arrives. Explicit OFF and an
    # unobserved gap are stronger than the ownership record.
    auto_believed_on = (
        light is not None
        and latest_record is not None
        and latest_record.confirmed
        and has_automatic_control(context.ownership, target_id)
        and light.state is not SensorState.OFF
        and light.reported_state is not SensorState.OFF
        and not (
            context.unobserved_since is not None
            and context.unobserved_since > latest_record.at
        )
    )
    light_on = light is not None and (
        (
            not light_off
            and (light.state is SensorState.ON or reported is SensorState.ON)
        )
        or auto_believed_on
    )
    if not target.auto_control:  # type: ignore[attr-defined]
        return _unchanged(target_id, Skip(target_id, SkipReason.MANUAL_MODE))
    if light is None or (
        light.state in (SensorState.UNKNOWN, SensorState.UNAVAILABLE)
        and reported not in (SensorState.ON, SensorState.OFF)
    ):
        return _unchanged(target_id, Skip(target_id, SkipReason.SENSOR_UNKNOWN))
    if _curve_manual_blocked(context, target_id):
        return _unchanged(target_id, Skip(target_id, SkipReason.MANUAL_OWNERSHIP))
    # The auto-on waits for a fresh confirmed presence when the manual
    # protection is in play (a latched release resumes automation, but the
    # physical turn-on waits for valid presence evidence — fail-closed on
    # sensor faults).
    if presence_gate and not light_on:
        return _unchanged(
            target_id, Skip(target_id, SkipReason.NIGHT_MINIMUM,
                            "защита активна: ожидается подтверждённое присутствие"),
        )
    if curve.mirror_on and not light_on:  # type: ignore[attr-defined]
        return TargetDecision(
            target_id=target_id,
            desired_state="on",
            desired_brightness=None,
            desired_color_temperature=None,
            commands=(
                PlannedCommand(
                    target_id,
                    LightAction.TURN_ON,
                    reason=DecisionReason.SCHEDULE,
                ),
            ),
        )
    if not curve.mirror_on and light_on:  # type: ignore[attr-defined]
        return TargetDecision(
            target_id=target_id,
            desired_state="off",
            desired_brightness=None,
            desired_color_temperature=None,
            commands=(
                PlannedCommand(
                    target_id,
                    LightAction.TURN_OFF,
                    reason=DecisionReason.SCHEDULE,
                ),
            ),
        )
    return _unchanged(target_id, Skip(target_id, SkipReason.IDEMPOTENT))


def _light_on(context: RoomLightingContext, target_id: str) -> bool:
    light = context.light(target_id)
    return light is not None and light.state is SensorState.ON


def _proven_manual_ownership(
    records: tuple[OwnershipSnapshot, ...], target_id: str
) -> bool:
    """Whether the newest record is a confirmed manual action."""

    latest = latest_ownership(records, target_id)
    return (
        latest is not None
        and latest.confirmed
        and latest.source is OwnershipSource.MANUAL
    )


def _manual_off_released(
    context: RoomLightingContext, target_id: str, presence: SensorState,
    absence_proven: bool, absence_since: int | None,
) -> bool:
    """Shared target/peer release: unknown is not a confirmed OFF state.

    A stale but known ``off`` counts as off: devices that report only on change
    would otherwise keep manual ownership forever and never release.
    """

    light = context.light(target_id)
    latest = latest_ownership(context.ownership, target_id)
    manual_at = latest.at if latest is not None else None
    light_off = _effective_light_off(light, context.now, manual_at)
    return (
        light_off
        and presence is SensorState.ON
        and release_expired_manual(
            context.ownership, target_id, now=context.now,
            minimum_interval_seconds=context.protection.minimum_interval_seconds,
            stable_absence_seconds=context.protection.stable_absence_seconds,
            absence_confirmed=context.protection.absence_confirmed or absence_proven,
            absence_since=(context.protection.absence_since
                           if context.protection.absence_since is not None else absence_since),
            absence_until=context.protection.absence_until,
            absence_latch=context.protection.absence_latch,
            release_mode=context.protection.release_mode,
            require_directed_off=context.protection.require_directed_off,
        )
    )


def _has_manual_peer(
    config: RoomLightingConfig,
    target: object,
    schedule_entry: ScheduleEntry | None,
    manual_targets: set[str],
) -> bool:
    """Whether a manually owned sibling blocks this target's automatic branch."""

    for other in config.devices.light_targets:
        if other.id == target.id or other.id not in manual_targets:
            continue
        if (
            target.group_id is not None  # type: ignore[attr-defined]
            and other.group_id == target.group_id  # type: ignore[attr-defined]
        ):
            return True
        if (
            target.role is not None  # type: ignore[attr-defined]
            and other.role == target.role  # type: ignore[attr-defined]
        ):
            return True
        if schedule_entry is not None and _entry_targets_target(schedule_entry, other):
            return True
    return False


def decision_target(
    decision: RoomLightingDecision, target_id: str
) -> TargetDecision | None:
    for target in decision.targets:
        if target.target_id == target_id:
            return target
    return None


def can_adjust_known_auto_on(context: RoomLightingContext, target_id: str) -> bool:
    """Planning permission only; a new command still needs fresh confirmation."""
    light = context.light(target_id)
    return (
        light is not None
        and light.state is SensorState.UNKNOWN
        and light.reported_state is SensorState.ON
        and context.unobserved_since is None
        and has_automatic_control(context.ownership, target_id)
    )


def _can_turn_off_silent_session_auto(
    context: RoomLightingContext, target_id: str
) -> bool:
    """Absence-only permission for an ON confirmed in this observation session."""
    light = context.light(target_id)
    record = latest_ownership(context.ownership, target_id)
    started = context.observation_started_at
    return (
        light is not None
        and light.state is SensorState.UNKNOWN
        and light.reported_state is SensorState.ON
        and context.unobserved_since is None
        and started is not None
        and started <= context.now
        and record is not None
        and record.source is OwnershipSource.AUTO
        and record.confirmed
        and started <= record.at <= context.now
        and has_automatic_control(context.ownership, target_id)
    )


def _evaluate_target(
    target: object,
    *,
    config: RoomLightingConfig,
    context: RoomLightingContext,
    policy: EnginePolicy,
    now_time: dt_time,
    presence: SensorState,
    absence_proven: bool,
    absence_since: int | None,
    absence_stale: bool,
    schedule_entry: ScheduleEntry | None,
    manual_peer_blocked: bool = False,
) -> TargetDecision:
    target_id = target.id  # type: ignore[attr-defined]
    light = context.light(target_id)
    light_on = light is not None and light.state is SensorState.ON
    light_brightness = light.brightness if light is not None else None
    light_color = light.color_temperature if light is not None else None

    # Away is a deliberate "nobody is home" safe-off. It outranks schedule,
    # presence, lux, protection, manual ownership and the per-target automatic
    # control flag: every target, including ``autoControl=false`` manual-only
    # ones, is switched off. This is the one path that intentionally commands a
    # target without proven automatic ownership, because the deliberate
    # departure itself is the evidence. Away never turns anything on.
    if context.away and config.away_behavior.mode is AwayMode.ROOM_OFF:
        if not light_on:
            return _unchanged(target_id, Skip(target_id, SkipReason.IDEMPOTENT))
        return TargetDecision(
            target_id=target_id,
            desired_state="off",
            desired_brightness=None,
            desired_color_temperature=None,
            commands=(
                PlannedCommand(
                    target_id,
                    LightAction.TURN_OFF,
                    reason=DecisionReason.AWAY,
                    fade_seconds=(
                        config.dimming.fade_seconds if config.dimming.enabled else 0
                    ),
                ),
            ),
        )

    # Outside away, ``autoControl=false`` is a manual-only target: the engine
    # never commands it and never publishes a desired state for it. It is
    # journaled only as a skip so the decision still shows why the target
    # stayed untouched.
    if not target.auto_control:  # type: ignore[attr-defined]
        return _unchanged(target_id, Skip(target_id, SkipReason.MANUAL_MODE))

    # A person already owns an interchangeable source of this profile. Leave
    # this target untouched instead of fighting the manual choice.
    if manual_peer_blocked:
        return _unchanged(target_id, Skip(target_id, SkipReason.MANUAL_PEER))

    manual = resolve_manual_ownership(context.ownership, target_id, light_on=light_on)
    # An explicit schedule "off" is a configured transition, not an absence
    # decision. It may switch an already-on target off even when automatic
    # ownership is unproven (for example right after a writer hand-over or a
    # restart). A confirmed manual action still outranks it and is checked
    # below. Absence-based switch-off and automatic turn-on stay strict.
    schedule_off_entry = (
        schedule_entry is not None and schedule_entry.how.mode is ScheduleMode.OFF
    )
    silent_absence_off = (
        not schedule_off_entry
        and _can_turn_off_silent_session_auto(context, target_id)
        and _turn_off_command(
            config, target, target_id, light_brightness, DecisionReason.DIMMING
        ).action is LightAction.TURN_OFF
    )
    # Absence that releases manual protection is historical: it is proven by a
    # completed stable absence, not by the presence event that starts a new
    # automatic turn-on. Prefer the protection snapshot so the same evidence
    # drives both the manual release and the protection gate.
    effective_absence = context.protection.absence_confirmed or absence_proven
    effective_absence_since = (
        context.protection.absence_since
        if context.protection.absence_since is not None
        else absence_since
    )
    if manual and _manual_on_absence_off_due(config, target, context, policy, now_time):
        return TargetDecision(
            target_id=target_id,
            desired_state="off",
            desired_brightness=None,
            desired_color_temperature=None,
            commands=(PlannedCommand(target_id, LightAction.TURN_OFF,
                                     reason=DecisionReason.DIMMING,
                                     fade_seconds=config.dimming.fade_seconds if config.dimming.enabled else 0),),
        )
    if manual:
        released = _manual_off_released(
            context, target_id, presence, absence_proven, absence_since
        )
        if not released and not (
            schedule_off_entry
            and not _proven_manual_ownership(context.ownership, target_id)
        ):
            return _unchanged(target_id, Skip(target_id, SkipReason.MANUAL_OWNERSHIP))

    # Both the adjust and the turn-off path must prove that automation owned the
    # light before the unobserved gap. A pre-restart AUTO record with a stale
    # read-back is not enough to command or turn off an already-on light.
    restored_auto = False
    if light_on:
        restored_auto = restore_after_restart(
            context.ownership,
            target_id,
            light_state=light.state if light is not None else SensorState.UNKNOWN,
            light_last_changed=light.last_changed if light is not None else None,
            unobserved_since=context.unobserved_since,
        )

    want_on = False
    reason = DecisionReason.SCHEDULE
    presence_required = False
    has_auto_source = schedule_entry is not None
    if schedule_entry is not None:
        mode = schedule_entry.how.mode
        if mode is ScheduleMode.OFF:
            want_on, reason = False, DecisionReason.SCHEDULE
        elif mode is ScheduleMode.ALWAYS:
            want_on, reason = True, DecisionReason.SCHEDULE
        elif mode is ScheduleMode.NIGHT_LIGHT:
            want_on = presence is SensorState.ON
            reason = DecisionReason.NIGHT_LIGHT
            presence_required = True
        else:
            want_on = presence is SensorState.ON
            reason = DecisionReason.PRESENCE
            presence_required = True

    brightness = schedule_entry.how.brightness if schedule_entry is not None else None
    color = schedule_entry.how.color_temperature if schedule_entry is not None else None
    lux_fail = False
    if config.illumination is not None:
        # Evaluate the illumination sensor for both branches: an unhealthy
        # sensor must block an automatic switch-off as well, so a broken lux
        # reading never leaves the room dark.
        lux, lux_fail = _lux_value(context, config, policy)
        if want_on and not lux_fail and lux is not None:
            brightness, color = _apply_lux(brightness, color, config, target, lux)
            reason = DecisionReason.LUX

    if not target.brightness:  # type: ignore[attr-defined]
        brightness = None
    if not target.color_temperature:  # type: ignore[attr-defined]
        color = None

    skips: list[Skip] = []
    if lux_fail:
        skips.append(Skip(target_id, SkipReason.LUX_FAIL_CLOSED))

    # This target is outside the active schedule entry: the schedule must not
    # command it, but absence may still dim an already auto-owned light.
    if not has_auto_source and not light_on and not silent_absence_off:
        return _unchanged(
            target_id,
            Skip(target_id, SkipReason.NO_SCHEDULE),
            extra_skips=skips,
        )

    if presence_required and presence is SensorState.UNKNOWN:
        return _unchanged(
            target_id,
            Skip(target_id, SkipReason.SENSOR_STALE if absence_stale else SkipReason.SENSOR_UNKNOWN),
            extra_skips=skips,
        )

    if want_on:
        if config.quiet_hours is not None and config.quiet_hours.contains(now_time):
            return _unchanged(target_id, Skip(target_id, SkipReason.QUIET_HOURS,
                              "Тихие часы: автоматическое включение приостановлено"),
                              extra_skips=skips)
        if context.protection.blocks_auto_on(
            now=context.now,
            absence_proven=effective_absence,
            absence_since=effective_absence_since,
            absence_until=context.protection.absence_until,
            absence_latch=context.protection.absence_latch,
        ):
            return _unchanged(
                target_id,
                Skip(target_id, SkipReason.MANUAL_PROTECTION),
                extra_skips=skips,
            )
        if light_on or can_adjust_known_auto_on(context, target_id):
            if context.unobserved_since is not None and not restored_auto:
                return _unchanged(
                    target_id,
                    Skip(target_id, SkipReason.UNOBSERVED),
                    extra_skips=skips,
                )
            if not has_automatic_control(context.ownership, target_id):
                return _unchanged(
                    target_id,
                    Skip(target_id, SkipReason.MANUAL_OWNERSHIP),
                    extra_skips=skips,
                )
            commands = _adjust_commands(target, target_id, brightness, color, light_brightness, light_color, reason)
            if not commands:
                return _unchanged(
                    target_id,
                    Skip(target_id, SkipReason.IDEMPOTENT),
                    extra_skips=skips,
                )
            return TargetDecision(
                target_id=target_id,
                desired_state="on",
                desired_brightness=brightness,
                desired_color_temperature=color,
                commands=tuple(commands),
                skips=tuple(skips),
            )
        # Включающая команда несёт целевые яркость и оттенок: светильник
        # включается сразу на уровне кривой, без отдельной регулировки к
        # уже включённому светильнику (защита от дубликатов её блокирует).
        commands = [
            PlannedCommand(
                target_id,
                LightAction.TURN_ON,
                brightness=brightness if target.brightness else None,
                color_temperature=color if target.color_temperature else None,
                reason=reason,
            )
        ]
        return TargetDecision(
            target_id=target_id,
            desired_state="on",
            desired_brightness=brightness,
            desired_color_temperature=color,
            commands=tuple(commands),
            skips=tuple(skips),
        )

    # Desired off.
    if not light_on and not silent_absence_off:
        return _unchanged(
            target_id,
            Skip(target_id, SkipReason.IDEMPOTENT),
            extra_skips=skips,
        )
    restored = restored_auto
    if not schedule_off_entry:
        if context.unobserved_since is not None and not restored:
            return _unchanged(
                target_id,
                Skip(target_id, SkipReason.UNOBSERVED),
                extra_skips=skips,
            )
        if not has_automatic_control(context.ownership, target_id):
            return _unchanged(
                target_id,
                Skip(target_id, SkipReason.NO_AUTO_OWNERSHIP),
                extra_skips=skips,
            )
    auto_record = latest_ownership(context.ownership, target_id)
    min_on_seconds = schedule_entry.how.min_on_seconds if schedule_entry is not None else 0
    if (
        schedule_entry is not None
        and schedule_entry.how.mode is ScheduleMode.NIGHT_LIGHT
        and min_on_seconds > 0
        and auto_record is not None
        and context.now - auto_record.at < min_on_seconds * 1000
    ):
        return _unchanged(
            target_id,
            Skip(target_id, SkipReason.NIGHT_MINIMUM),
            extra_skips=skips,
        )

    threshold = _absence_threshold(policy, now_time)
    absence_due = (
        absence_proven
        and absence_since is not None
        and context.now - absence_since >= threshold * 1000
    )
    schedule_off = schedule_entry is not None and schedule_entry.how.mode is ScheduleMode.OFF
    if lux_fail:
        # Owner rule: a faulty illumination sensor keeps the light instead of
        # turning it off. Away is handled earlier and still switches off.
        return _unchanged(
            target_id,
            Skip(target_id, SkipReason.LUX_FAIL_CLOSED),
            extra_skips=skips,
        )
    if absence_due:
        command = _turn_off_command(
            config, target, target_id, light_brightness, DecisionReason.DIMMING
        )
        return TargetDecision(
            target_id=target_id,
            desired_state="off",
            desired_brightness=None,
            desired_color_temperature=None,
            commands=(command,),
            skips=tuple(skips),
        )
    if schedule_off:
        command = _turn_off_command(
            config, target, target_id, light_brightness, DecisionReason.SCHEDULE
        )
        return TargetDecision(
            target_id=target_id,
            desired_state="off",
            desired_brightness=None,
            desired_color_temperature=None,
            commands=(command,),
            skips=tuple(skips),
        )
    if absence_stale:
        return _unchanged(
            target_id,
            Skip(target_id, SkipReason.SENSOR_STALE),
            extra_skips=skips,
        )
    return _unchanged(
        target_id,
        Skip(target_id, SkipReason.ABSENCE_UNPROVEN),
        extra_skips=skips,
    )


def _manual_on_absence_off_due(config, target, context, policy, now_time) -> bool:
    """Permit only the selected manual-on target after newly proven absence."""
    if not target.auto_off_manual_on_absence or not target.auto_control:
        return False
    light = context.light(target.id)
    manual = latest_ownership(context.ownership, target.id)
    if (light is None or light.state is not SensorState.ON or light.last_changed > context.now
            or manual is None or manual.source is not OwnershipSource.MANUAL
            or not manual.confirmed or manual.turned_off or manual.at > context.now):
        return False
    configured = {item.id for item in config.devices.sensors
                  if item.kind in {SensorKind.PRESENCE, SensorKind.MOTION}}
    sensors = [item for item in context.sensors if item.sensor_id in configured]
    if not configured or {item.sensor_id for item in sensors} != configured:
        return False
    if any(item.state is not SensorState.OFF or not sensor_observation_is_fresh(
            item, context.now, policy.sensor_freshness_seconds * 1000) for item in sensors):
        return False
    floor = max(manual.at, context.unobserved_since or 0, context.observation_started_at or 0,
                *(max(item.last_changed, item.continuous_absence_since or 0)
                  for item in sensors))
    return context.now - floor >= _absence_threshold(policy, now_time) * 1000


def _adjust_commands(
    target: object,
    target_id: str,
    brightness: int | None,
    color: int | None,
    light_brightness: int | None,
    light_color: int | None,
    reason: DecisionReason,
) -> list[PlannedCommand]:
    commands: list[PlannedCommand] = []
    if (
        target.brightness  # type: ignore[attr-defined]
        and brightness is not None
        and not _within_tolerance(brightness, light_brightness, BRIGHTNESS_TOLERANCE)
    ):
        commands.append(
            PlannedCommand(
                target_id,
                LightAction.SET_BRIGHTNESS,
                brightness=brightness,
                reason=reason,
            )
        )
    if (
        target.color_temperature  # type: ignore[attr-defined]
        and color is not None
        and not _within_tolerance(
            color, light_color, COLOR_TEMPERATURE_TOLERANCE_KELVIN
        )
    ):
        commands.append(
            PlannedCommand(
                target_id,
                LightAction.SET_COLOR_TEMPERATURE,
                color_temperature=color,
                reason=reason,
            )
        )
    return commands


def _within_tolerance(
    desired: int, actual: int | None, tolerance: int
) -> bool:
    """Whether the observed value is close enough that no command is needed."""

    if actual is None:
        return False
    return abs(int(desired) - int(actual)) <= tolerance


def _turn_off_command(
    config: RoomLightingConfig,
    target: object,
    target_id: str,
    light_brightness: int | None,
    reason: DecisionReason,
) -> PlannedCommand:
    if config.dimming.enabled and config.dimming.on_absence and target.brightness:  # type: ignore[attr-defined]
        target_percent = config.dimming.target_percent
        current = light_brightness if light_brightness is not None else 100
        # Monotonic fade: a step may only lower brightness.
        if target_percent < current:
            return PlannedCommand(
                target_id,
                LightAction.SET_BRIGHTNESS,
                brightness=target_percent,
                reason=reason,
                fade_seconds=config.dimming.fade_seconds,
            )
    return PlannedCommand(
        target_id,
        LightAction.TURN_OFF,
        reason=reason,
        fade_seconds=config.dimming.fade_seconds,
    )


def _unchanged(
    target_id: str,
    skip: Skip,
    *,
    extra_skips: list[Skip] | None = None,
) -> TargetDecision:
    skips = [skip]
    if extra_skips:
        skips.extend(item for item in extra_skips if item != skip)
    return TargetDecision(
        target_id=target_id,
        desired_state="unchanged",
        desired_brightness=None,
        desired_color_temperature=None,
        skips=tuple(skips),
    )


def _presence_state(context: RoomLightingContext, policy: EnginePolicy) -> SensorState:
    relevant = [
        sensor
        for sensor in context.sensors
        if sensor.kind in {SensorKind.PRESENCE, SensorKind.MOTION}
    ]
    if not relevant:
        return SensorState.UNKNOWN
    if any(
        sensor.state is SensorState.ON and _is_fresh(sensor, context, policy)
        for sensor in relevant
    ):
        return SensorState.ON
    if any(
        sensor.state in {SensorState.UNKNOWN, SensorState.UNAVAILABLE}
        or (sensor.state is SensorState.ON and not _is_fresh(sensor, context, policy))
        or (sensor.state is SensorState.OFF and not sensor_observation_is_fresh(
            sensor, context.now, policy.staleness_seconds * 1000
        ))
        for sensor in relevant
    ):
        return SensorState.UNKNOWN
    if all(sensor.state is SensorState.OFF for sensor in relevant):
        # Absence requires every participant to be known OFF; uncertainty may
        # not be discarded just because another sensor reports OFF.
        return SensorState.OFF
    return SensorState.UNKNOWN


def _absence_evidence(
    context: RoomLightingContext, policy: EnginePolicy
) -> tuple[bool, int | None, bool]:
    """Return (proven, absence_since, stale).

    Absence is proven only when every participating sensor is known OFF.
    Unknown, unavailable or a new ON interrupt it. An OFF reading older than
    the separate (much larger) staleness window is reported as stale instead
    of being silently treated as absence or as a generic unobserved period.
    """

    relevant = [
        sensor
        for sensor in context.sensors
        if sensor.kind in {SensorKind.PRESENCE, SensorKind.MOTION}
    ]
    if not relevant:
        return (False, None, False)
    off_times: list[int] = []
    for sensor in relevant:
        if sensor.state in {SensorState.UNKNOWN, SensorState.UNAVAILABLE}:
            return (False, None, False)
        if sensor.state is SensorState.ON:
            if _is_fresh(sensor, context, policy):
                return (False, None, False)
            return (False, None, True)
        if not sensor_observation_is_fresh(sensor, context.now, policy.staleness_seconds * 1000):
            return (False, None, True)
        off_times.append(
            sensor.continuous_absence_since
            if sensor.continuous_absence_since is not None
            else sensor.last_changed
        )
    if not off_times:
        return (False, None, False)
    absence_since = max(off_times)
    # An unobserved interval is not absence: a restart makes the confirmed
    # absence window start anew instead of inheriting a stale OFF timestamp.
    if context.unobserved_since is not None:
        absence_since = max(absence_since, context.unobserved_since)
    return (True, absence_since, False)


def _is_fresh(sensor: SensorSnapshot, context: RoomLightingContext, policy: EnginePolicy) -> bool:
    return sensor_observation_is_fresh(sensor, context.now, policy.sensor_freshness_seconds * 1000)


def sensor_observation_is_fresh(sensor: SensorSnapshot, now: int, freshness_ms: int) -> bool:
    """Fresh reports prove observation, not a new state transition."""
    observed = sensor.observed_at_ms
    if observed is None:
        observed = sensor.last_changed  # Preserve existing snapshot callers.
    return (
        sensor.state not in {SensorState.UNKNOWN, SensorState.UNAVAILABLE}
        and sensor.last_changed <= observed <= now
        and now - observed <= freshness_ms
    )


def _lux_value(
    context: RoomLightingContext,
    config: RoomLightingConfig,
    policy: EnginePolicy,
) -> tuple[float | None, bool]:
    illumination = config.illumination
    if illumination is None:
        return (None, False)
    configured_ids = {
        sensor.id
        for sensor in config.devices.sensors
        if sensor.kind is SensorKind.ILLUMINANCE
        and sensor.entity_id == illumination.sensor
    }
    if not configured_ids:
        return (None, True)
    for sensor in context.sensors:
        if sensor.kind is SensorKind.ILLUMINANCE and sensor.sensor_id in configured_ids:
            fresh = _is_fresh(sensor, context, policy)
            if not fresh or not sensor.lux_healthy or sensor.lux is None:
                return (None, True)
            return (sensor.lux, False)
    return (None, True)


def _apply_lux(
    brightness: int | None,
    color: int | None,
    config: RoomLightingConfig,
    target: object,
    lux: float,
) -> tuple[int | None, int | None]:
    illumination = config.illumination
    if illumination is None or not illumination.thresholds:
        return brightness, color
    matched = None
    for threshold in sorted(illumination.thresholds, key=lambda item: item.lux):
        if float(threshold.lux) <= float(lux):
            matched = threshold
    if matched is None:
        return brightness, color
    if target.brightness and matched.brightness is not None:  # type: ignore[attr-defined]
        brightness = matched.brightness
    if target.color_temperature and matched.color_temperature is not None:  # type: ignore[attr-defined]
        color = matched.color_temperature
    return brightness, color


def _active_schedule_entry(
    config: RoomLightingConfig,
    context: RoomLightingContext,
    target: object,
) -> ScheduleEntry | None:
    """Latest entry that names this target; other targets are never commanded."""

    local_now = datetime.fromtimestamp(context.now / 1000, context.timezone)
    best: ScheduleEntry | None = None
    best_time: datetime | None = None
    for entry in config.schedule:
        if not _entry_targets_target(entry, target):
            continue
        for day_offset in (0, -1):
            day = local_now.date() + timedelta(days=day_offset)
            if not _day_matches(entry.when.days_of_week, day, context.holiday):
                continue
            resolved = resolve_anchor_time(
                entry.when.anchor,
                day=day,
                sunrise=context.sunrise,
                sunset=context.sunset,
                home_timezone=context.timezone,
            )
            if resolved > local_now:
                continue
            if best_time is None or resolved > best_time:
                best, best_time = entry, resolved
    return best


def _entry_targets_target(entry: ScheduleEntry, target: object) -> bool:
    targets = entry.targets
    if not targets.light_targets and not targets.group_ids and not targets.roles:
        return True
    if target.id in targets.light_targets:  # type: ignore[attr-defined]
        return True
    group_id = target.group_id  # type: ignore[attr-defined]
    if group_id is not None and group_id in targets.group_ids:
        return True
    role = target.role  # type: ignore[attr-defined]
    return role is not None and role in targets.roles


def _day_matches(days: object, day: object, holiday: bool) -> bool:
    del holiday
    weekday = day.weekday()  # type: ignore[attr-defined]
    if isinstance(days, str):
        if days == "weekdays":
            return weekday < 5
        if days == "weekend":
            return weekday >= 5
        return True
    return _DAY_CODES[weekday] in days  # type: ignore[operator]
