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

from .room_lighting import (
    AwayMode,
    RoomLightingConfig,
    ScheduleEntry,
    ScheduleMode,
    SensorKind,
    resolve_anchor_time,
)
from .room_lighting_ownership import (
    OwnershipSnapshot,
    SensorState,
    has_proven_auto_ownership,
    latest_ownership,
    release_expired_manual,
    resolve_manual_ownership,
    restore_after_restart,
)

MODE_SHADOW = "shadow"

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

    def __post_init__(self) -> None:
        if not isinstance(self.sensor_id, str) or not self.sensor_id:
            raise RoomLightingEngineViolation("sensor id is required")
        if not isinstance(self.kind, SensorKind):
            raise RoomLightingEngineViolation("sensor kind is invalid")
        if not isinstance(self.state, SensorState):
            raise RoomLightingEngineViolation("sensor state is invalid")
        if type(self.last_changed) is not int or self.last_changed < 0:
            raise RoomLightingEngineViolation("sensor timestamp is invalid")


@dataclass(frozen=True, slots=True)
class LightSnapshot:
    target_id: str
    state: SensorState
    last_changed: int
    brightness: int | None = None
    color_temperature: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.target_id, str) or not self.target_id:
            raise RoomLightingEngineViolation("light target id is required")
        if not isinstance(self.state, SensorState):
            raise RoomLightingEngineViolation("light state is invalid")
        if type(self.last_changed) is not int or self.last_changed < 0:
            raise RoomLightingEngineViolation("light timestamp is invalid")


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

    def blocks_auto_on(
        self,
        *,
        now: int,
        absence_proven: bool,
        absence_since: int | None,
    ) -> bool:
        if not self.active:
            return False
        timer_ok = (
            self.started_at is not None
            and now - self.started_at >= self.minimum_interval_seconds * 1000
        )
        absence_ok = (
            absence_proven
            and absence_since is not None
            and now - absence_since >= self.stable_absence_seconds * 1000
        )
        if self.release_mode == "timer_only":
            return not timer_ok
        if self.release_mode == "absence_only":
            return not absence_ok
        return not (timer_ok and absence_ok)


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

    def __post_init__(self) -> None:
        if type(self.now) is not int or self.now < 0:
            raise RoomLightingEngineViolation("context time is invalid")
        if not isinstance(self.timezone, tzinfo):
            raise RoomLightingEngineViolation("context timezone is required")
        if type(self.away) is not bool:
            raise RoomLightingEngineViolation("context away flag is invalid")

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
        or resolve_manual_ownership(
            context.ownership,
            target.id,
            light_on=_light_on(context, target.id),
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


def _light_on(context: RoomLightingContext, target_id: str) -> bool:
    light = context.light(target_id)
    return light is not None and light.state is SensorState.ON


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
    if manual:
        released = (
            not light_on
            and presence is SensorState.ON
            and release_expired_manual(
                context.ownership,
                target_id,
                now=context.now,
                minimum_interval_seconds=context.protection.minimum_interval_seconds,
                stable_absence_seconds=context.protection.stable_absence_seconds,
                absence_confirmed=effective_absence,
                absence_since=effective_absence_since,
            )
        )
        if not released:
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
    if want_on and config.illumination is not None:
        lux, lux_fail = _lux_value(context, config, policy)
        if not lux_fail and lux is not None:
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
    if not has_auto_source and not light_on:
        return _unchanged(
            target_id,
            Skip(target_id, SkipReason.NO_SCHEDULE),
            extra_skips=skips,
        )

    if presence_required and presence is SensorState.UNKNOWN:
        return _unchanged(
            target_id,
            Skip(target_id, SkipReason.SENSOR_UNKNOWN),
            extra_skips=skips,
        )

    if want_on:
        if context.protection.blocks_auto_on(
            now=context.now,
            absence_proven=effective_absence,
            absence_since=effective_absence_since,
        ):
            return _unchanged(
                target_id,
                Skip(target_id, SkipReason.MANUAL_PROTECTION),
                extra_skips=skips,
            )
        if light_on:
            if context.unobserved_since is not None and not restored_auto:
                return _unchanged(
                    target_id,
                    Skip(target_id, SkipReason.UNOBSERVED),
                    extra_skips=skips,
                )
            if not has_proven_auto_ownership(context.ownership, target_id):
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
        commands = [PlannedCommand(target_id, LightAction.TURN_ON, reason=reason)]
        if target.brightness and brightness is not None:  # type: ignore[attr-defined]
            commands.append(
                PlannedCommand(
                    target_id,
                    LightAction.SET_BRIGHTNESS,
                    brightness=brightness,
                    reason=reason,
                )
            )
        if target.color_temperature and color is not None:  # type: ignore[attr-defined]
            commands.append(
                PlannedCommand(
                    target_id,
                    LightAction.SET_COLOR_TEMPERATURE,
                    color_temperature=color,
                    reason=reason,
                )
            )
        return TargetDecision(
            target_id=target_id,
            desired_state="on",
            desired_brightness=brightness,
            desired_color_temperature=color,
            commands=tuple(commands),
            skips=tuple(skips),
        )

    # Desired off.
    if not light_on:
        return _unchanged(
            target_id,
            Skip(target_id, SkipReason.IDEMPOTENT),
            extra_skips=skips,
        )
    restored = restored_auto
    if context.unobserved_since is not None and not restored:
        return _unchanged(
            target_id,
            Skip(target_id, SkipReason.UNOBSERVED),
            extra_skips=skips,
        )
    if not has_proven_auto_ownership(context.ownership, target_id):
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
    if all(sensor.state is SensorState.OFF for sensor in relevant):
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
            return (False, None, False)
        if context.now - sensor.last_changed > policy.staleness_seconds * 1000:
            return (False, None, True)
        off_times.append(sensor.last_changed)
    absence_since = max(off_times)
    # An unobserved interval is not absence: a restart makes the confirmed
    # absence window start anew instead of inheriting a stale OFF timestamp.
    if context.unobserved_since is not None:
        absence_since = max(absence_since, context.unobserved_since)
    return (True, absence_since, False)


def _is_fresh(sensor: SensorSnapshot, context: RoomLightingContext, policy: EnginePolicy) -> bool:
    if sensor.state in {SensorState.UNKNOWN, SensorState.UNAVAILABLE}:
        return False
    return context.now - sensor.last_changed <= policy.sensor_freshness_seconds * 1000


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
