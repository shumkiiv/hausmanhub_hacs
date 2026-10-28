"""Live Home Assistant state provider for room lighting.

The provider only reads ``hass.states`` and never calls a Home Assistant
service. Unknown, unavailable or missing entities degrade to ``unknown`` /
``unavailable`` snapshots instead of raising, so a room with a broken sensor
keeps producing a decision with an explicit skip reason.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, time as dt_time, timezone, tzinfo
import logging
import math
from typing import TYPE_CHECKING

from ..domain.device_power_dependencies import (
    DevicePowerDependency,
    effective_device_state,
)
from ..domain.room_lighting import (
    LightKind,
    LightRole,
    LightTarget,
    RoomLightingConfig,
    RoomSensor,
    SensorKind,
)
from ..domain.room_lighting_auxiliary import (
    BathroomObservation,
    bathroom_band,
)
from ..domain.room_lighting_engine import (
    LightSnapshot,
    ProtectionSnapshot,
    RoomLightingContext,
    SensorSnapshot,
    bathroom_auxiliary_inputs,
)
from ..domain.room_lighting_ownership import (
    OwnershipSnapshot,
    SensorState,
    has_proven_auto_ownership,
)
from .power_evidence import (
    state_is_restored_or_cached,
    trusted_power_state,
    trusted_target_state,
)
from .room_lighting_color import device_kelvin_bounds, reflect_inverted_kelvin
from .room_power_migration import (
    EXACT_POWER_TARGET_MANIFEST,
    decide_exact_power,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

DEFAULT_SUNRISE = dt_time(7, 0)
DEFAULT_SUNSET = dt_time(19, 0)
SUN_ENTITY_ID = "sun.sun"
MAX_BRIGHTNESS_255 = 255.0
LIGHT_STATE_FRESHNESS_MS = 300_000

OwnershipProvider = Callable[[RoomLightingConfig], Sequence[OwnershipSnapshot]]
ProtectionProvider = Callable[[RoomLightingConfig], ProtectionSnapshot]
PowerDependencyResolver = Callable[
    [], Mapping[str, DevicePowerDependency] | None
]
NowMs = Callable[[], int]


def _default_now_ms() -> int:
    import time as _time

    return int(_time.time() * 1000)


def _resolve_timezone(hass: object) -> tzinfo:
    config = getattr(hass, "config", None)
    name = getattr(config, "time_zone", None)
    if isinstance(name, str) and name:
        try:
            from zoneinfo import ZoneInfo

            return ZoneInfo(name)
        except Exception:  # noqa: BLE001 - an invalid zone must not crash a room
            _LOGGER.warning("room lighting could not resolve the home time zone")
    return timezone.utc


def _state_of(value: object) -> SensorState:
    raw = str(value).strip().lower() if value is not None else ""
    if raw == "on":
        return SensorState.ON
    if raw == "off":
        return SensorState.OFF
    if raw == "unavailable":
        return SensorState.UNAVAILABLE
    return SensorState.UNKNOWN


def _trusted_light_state(state: object, *, now: int) -> SensorState:
    """Return a target state only when it is fresh and not restored/cached."""

    value = trusted_target_state(
        state, now_ms=now, freshness_ms=LIGHT_STATE_FRESHNESS_MS
    )
    return _state_of(value)


def _planning_light_state(state: object) -> SensorState:
    """Return a known target ``on``/``off`` for command planning.

    A light or relay that reports only on change can keep a months-old
    ``last_reported`` while its Home Assistant state is still the best known
    truth.  Scheduling must not stall just because nothing changed recently;
    freshness stays required for confirming a command that was just sent
    (``_trusted_light_state``/``trusted_target_state``), while restored, cached
    and non-binary states still fail closed here.
    """

    if state is None or state_is_restored_or_cached(state):
        return SensorState.UNKNOWN
    return _state_of(getattr(state, "state", None))


def _last_changed_ms(state: object, *, now: int) -> int:
    value = getattr(state, "last_changed", None)
    if isinstance(value, datetime):
        return int(value.timestamp() * 1000)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric = float(value)
        # Tolerate both seconds and milliseconds from a synthetic test shim.
        if numeric > 1_000_000_000_000:
            return int(numeric)
        return int(numeric * 1000)
    return now


def _as_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _sensor_timestamp_ms(value: object, *, now: int) -> int | None:
    if isinstance(value, datetime):
        value = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
        numeric = value.timestamp() * 1000
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric = float(value)
        if numeric <= 1_000_000_000_000:
            numeric *= 1000
    else:
        return None
    if not math.isfinite(numeric) or numeric < 0 or numeric > now:
        return None
    return int(numeric)


def _sensor_observed_ms(state: object, *, now: int) -> int | None:
    """Read the newest available HA observation without hiding bad evidence."""
    for name in ("last_reported", "last_updated", "last_changed"):
        value = getattr(state, name, None)
        if value is not None:
            return _sensor_timestamp_ms(value, now=now)
    return None


def _brightness_percent(value: object) -> int | None:
    numeric = _as_float(value)
    if numeric is None:
        return None
    percent = numeric / MAX_BRIGHTNESS_255 * 100
    return max(0, min(100, int(round(percent))))


def _color_temperature_kelvin(attributes: object) -> int | None:
    if not isinstance(attributes, dict):
        return None
    kelvin = _as_float(attributes.get("color_temp_kelvin"))
    if kelvin is None:
        mireds = _as_float(attributes.get("color_temp"))
        if mireds and mireds > 0:
            kelvin = 1_000_000.0 / mireds
    if kelvin is None:
        return None
    return int(round(kelvin))


def _parse_sun_time(value: object, tz: tzinfo) -> dt_time | None:
    parsed: datetime | None = None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz).time()


class RoomLightingHaStateProvider:
    """Build a :class:`RoomLightingContext` from the live Home Assistant state."""

    def __init__(
        self,
        *,
        now_ms: NowMs | None = None,
        state_reader: Callable[[str], object | None] | None = None,
        ownership_provider: OwnershipProvider | None = None,
        protection_provider: ProtectionProvider | None = None,
        unobserved_since_provider: Callable[[], int | None] | None = None,
        sunrise: dt_time | None = None,
        sunset: dt_time | None = None,
        power_dependency_resolver: PowerDependencyResolver | None = None,
        power_migration_manifest: Mapping[str, str] | None = None,
    ) -> None:
        self._now_ms = now_ms or _default_now_ms
        self._state_reader = state_reader
        self._ownership_provider = ownership_provider
        self._protection_provider = protection_provider
        self._unobserved_since_provider = unobserved_since_provider
        self._sunrise = sunrise
        self._sunset = sunset
        self._power_dependency_resolver = power_dependency_resolver
        self._power_migration_manifest = (
            EXACT_POWER_TARGET_MANIFEST
            if power_migration_manifest is None
            else dict(power_migration_manifest)
        )

    def set_power_migration_manifest(self, manifest: Mapping[str, str]) -> None:
        """Update protected exact-power claims without rebuilding the provider."""

        self._power_migration_manifest = dict(manifest)

    def _read_light_state(self, hass: HomeAssistant, entity_id: str) -> object | None:
        if self._state_reader is not None:
            return self._state_reader(entity_id)
        return hass.states.get(entity_id)

    async def build_context(
        self,
        hass: HomeAssistant,
        config: RoomLightingConfig,
        now: int,
    ) -> RoomLightingContext:
        tz = _resolve_timezone(hass)
        sunrise, sunset, sun_healthy = self._sun_times(hass, tz)
        dependencies = self._power_dependencies()
        # Only a room with an explicit release-owned migration claim switches
        # to exact per-target semantics. Every other room keeps the legacy
        # whole-room power switch association, regardless of the live map.
        decision = decide_exact_power(
            tuple(
                (target.id, target.entity_id)
                for target in config.devices.light_targets
            ),
            dependencies,
            manifest=self._power_migration_manifest,
            room_id=config.room_id,
        )
        exact_dependencies = decision.projection if decision.active else None
        power_state = SensorState.ON if decision.active else self._room_power_state(hass, config)
        power_on = power_state is SensorState.ON
        legacy_power_off = not decision.active and power_state is SensorState.OFF
        paired_main = next((target.id for target in config.devices.light_targets
                            if target.role is LightRole.MAIN), None)
        # Only claimed logical targets whose own edge is missing/wrong become
        # unproven. Independent targets in the same claimed room keep their own
        # trusted state; extra live edges are ignored by the projection.
        unproven_targets = frozenset(decision.unproven_target_ids)
        return RoomLightingContext(
            now=now,
            timezone=tz,
            sunrise=sunrise,
            sunset=sunset,
            sun_healthy=sun_healthy,
            sensors=tuple(
                self._sensor_snapshot(hass, sensor, now)
                for sensor in config.devices.sensors
            ),
            lights=tuple(
                self._light_snapshot(
                    hass,
                    target,
                    now,
                    power_on=power_on,
                    dependencies=exact_dependencies,
                    unproven_targets=unproven_targets,
                    legacy_power_off=legacy_power_off and target.id == paired_main,
                )
                for target in config.devices.light_targets
            ),
            ownership=self._ownership(config),
            protection=self._protection(config),
            unobserved_since=self._unobserved_since(),
        )

    def _room_power_state(
        self, hass: HomeAssistant, config: RoomLightingConfig
    ) -> SensorState:
        """Trusted room power state; unknown is not a safe off proof."""

        power = config.devices.power_switch
        if power is None or power.entity_id is None:
            return SensorState.ON
        state = self._read_light_state(hass, power.entity_id)
        value = trusted_power_state(state, same_state_confirmation=True, now_ms=self._now_ms())
        return _state_of(value)

    def _power_dependencies(
        self,
    ) -> Mapping[str, DevicePowerDependency] | None:
        """Return the exact dependency map, or ``None`` when unavailable.

        A resolver that is absent keeps the legacy room-level association.
        A resolver that raises or returns a damaged document means the exact
        map cannot be trusted: the caller receives ``None`` and must fail
        closed instead of guessing the electrical circuit.
        """

        resolver = self._power_dependency_resolver
        if resolver is None:
            return None
        try:
            value = resolver()
        except Exception:  # noqa: BLE001 - a damaged map must not fail a room
            _LOGGER.warning("room lighting power dependency map is unavailable")
            return None
        if not isinstance(value, Mapping):
            _LOGGER.warning("room lighting power dependency map is damaged")
            return None
        dependencies: dict[str, DevicePowerDependency] = {}
        for entity_id, dependency in value.items():
            if not isinstance(entity_id, str) or not isinstance(
                dependency, DevicePowerDependency
            ):
                _LOGGER.warning("room lighting power dependency map is damaged")
                return None
            dependencies[entity_id] = dependency
        return dependencies

    def _unobserved_since(self) -> int | None:
        if self._unobserved_since_provider is None:
            return None
        return self._unobserved_since_provider()

    def _ownership(
        self, config: RoomLightingConfig
    ) -> tuple[OwnershipSnapshot, ...]:
        if self._ownership_provider is None:
            return ()
        return tuple(self._ownership_provider(config))

    def _protection(self, config: RoomLightingConfig) -> ProtectionSnapshot:
        if self._protection_provider is None:
            return ProtectionSnapshot()
        return self._protection_provider(config)

    def _sun_times(
        self, hass: HomeAssistant, tz: tzinfo
    ) -> tuple[dt_time, dt_time, bool]:
        """Sunrise/sunset plus the honest health of the sun evidence.

        The sunrise/sunset override (tests) is always healthy. A live read
        is healthy only when BOTH next_rising and next_setting parsed: a
        missing half must never fall back to the built-in default and be
        trusted as proof (L-06/L-07). The defaults still feed the legacy
        engine, which does not read the health flag.
        """

        if self._sunrise is not None and self._sunset is not None:
            return self._sunrise, self._sunset, True
        sunrise, sunset = DEFAULT_SUNRISE, DEFAULT_SUNSET
        healthy = False
        state = hass.states.get(SUN_ENTITY_ID)
        attributes = getattr(state, "attributes", None)
        if isinstance(attributes, dict):
            parsed_rising = _parse_sun_time(attributes.get("next_rising"), tz)
            parsed_setting = _parse_sun_time(attributes.get("next_setting"), tz)
            healthy = parsed_rising is not None and parsed_setting is not None
            if parsed_rising is not None:
                sunrise = parsed_rising
            if parsed_setting is not None:
                sunset = parsed_setting
        return (
            self._sunrise or sunrise,
            self._sunset or sunset,
            healthy,
        )

    def _sensor_snapshot(
        self, hass: HomeAssistant, sensor: RoomSensor, now: int
    ) -> SensorSnapshot:
        state = hass.states.get(sensor.entity_id) if sensor.entity_id else None
        observed = _sensor_observed_ms(state, now=now) if state is not None else None
        changed = _sensor_timestamp_ms(getattr(state, "last_changed", None), now=now)
        raw = getattr(state, "state", None)
        if (state is None or observed is None or changed is None or changed > observed
                or state_is_restored_or_cached(state)):
            return SensorSnapshot(
                sensor_id=sensor.id,
                kind=sensor.kind,
                state=SensorState.UNAVAILABLE if _state_of(raw) is SensorState.UNAVAILABLE else SensorState.UNKNOWN,
                last_changed=now,
                lux_healthy=False,
            )
        last_changed = changed
        if sensor.kind is SensorKind.ILLUMINANCE:
            lux = _as_float(raw)
            if lux is None:
                return SensorSnapshot(
                    sensor_id=sensor.id,
                    kind=sensor.kind,
                    state=_state_of(raw),
                    last_changed=last_changed,
                    lux=None,
                    lux_healthy=False,
                    observed_at_ms=observed,
                )
            return SensorSnapshot(
                sensor_id=sensor.id,
                kind=sensor.kind,
                state=SensorState.ON,
                last_changed=last_changed,
                lux=lux,
                lux_healthy=True,
                observed_at_ms=observed,
            )
        return SensorSnapshot(
            sensor_id=sensor.id,
            kind=sensor.kind,
            state=_state_of(raw),
            last_changed=last_changed,
            observed_at_ms=observed,
        )

    def _light_snapshot(
        self,
        hass: HomeAssistant,
        target: LightTarget,
        now: int,
        *,
        power_on: bool = True,
        dependencies: Mapping[str, DevicePowerDependency] | None = None,
        unproven_targets: frozenset[str] = frozenset(),
        legacy_power_off: bool = False,
    ) -> LightSnapshot:
        if target.id in unproven_targets:
            # The claimed topology of this room is not proven by the live map.
            # Never report the target as on: an unknown state fails closed and
            # the dispatch path blocks the command with a diagnostic reason.
            return LightSnapshot(
                target_id=target.id,
                state=SensorState.UNKNOWN,
                last_changed=now,
            )
        state = self._read_light_state(hass, target.entity_id) if target.entity_id else None
        trusted_target = _trusted_light_state(state, now=now)
        reported_target = _planning_light_state(state)
        if (
            dependencies is None
            and legacy_power_off
            and trusted_target is SensorState.UNKNOWN
            and str(getattr(state, "state", "")).lower() == "unavailable"
        ):
            # Narrow legacy room-power case: a freshly trusted OFF room relay can
            # explain an unavailable Zigbee target. This only lets the engine
            # plan the old two-step path; dispatch must re-read a trusted target
            # OFF after power is restored before sending the target command.
            return LightSnapshot(
                target_id=target.id,
                state=SensorState.OFF,
                last_changed=now,
            )
        if trusted_target is SensorState.UNKNOWN:
            brightness, color_temperature = self._light_parameters(target, state, reported_target)
            return LightSnapshot(
                target_id=target.id,
                state=SensorState.UNKNOWN,
                last_changed=now if state is None else _last_changed_ms(state, now=now),
                reported_state=reported_target,
                brightness=brightness,
                color_temperature=color_temperature,
            )
        # UNKNOWN power is not OFF; a fresh target ON contradicts a guessed
        # whole-room circuit. The runtime's proven pair covers only its main
        # target. Exact verified topology is resolved below.
        if dependencies is not None and target.entity_id is not None:
            # Exact dependency map: only a target with an upstream source
            # changes its effective state. Mirror and point switches keep
            # their actual reported state even when another circuit is off.
            light_state = self._effective_state(
                hass, target.entity_id, dependencies, now=now
            )
        else:
            light_state = trusted_target
        last_changed = _last_changed_ms(state, now=now)
        brightness, color_temperature = self._light_parameters(target, state, light_state)
        return LightSnapshot(
            target_id=target.id,
            state=light_state,
            last_changed=last_changed,
            brightness=brightness,
            color_temperature=color_temperature,
            reported_state=reported_target,
        )

    @staticmethod
    def _light_parameters(target: LightTarget, state: object, light_state: SensorState) -> tuple[int | None, int | None]:
        brightness: int | None = None
        color_temperature: int | None = None
        if target.kind is LightKind.LIGHT and light_state is SensorState.ON:
            attributes = getattr(state, "attributes", None)
            if isinstance(attributes, dict):
                brightness = _brightness_percent(attributes.get("brightness"))
                color_temperature = _color_temperature_kelvin(attributes)
                if color_temperature is not None and target.color_temp_inverted:
                    color_temperature = reflect_inverted_kelvin(
                        color_temperature, *device_kelvin_bounds(attributes)
                    )
        return brightness, color_temperature

    def _effective_state(
        self,
        hass: HomeAssistant,
        entity_id: str,
        dependencies: Mapping[str, DevicePowerDependency],
        *,
        now: int,
    ) -> SensorState:
        """Resolve one target through the exact dependency map.

        Power sources are read with the shared trusted-evidence rules, so a
        restored, cached or stale relay report is never treated as power.
        """

        source_ids = {
            dependency.power_source_entity_id
            for dependency in dependencies.values()
        }

        def read_state(requested_entity_id: str) -> str | None:
            state = self._read_light_state(hass, requested_entity_id)
            if requested_entity_id in source_ids:
                return trusted_power_state(state, same_state_confirmation=True, now_ms=now)
            trusted = _trusted_light_state(state, now=now)
            return None if trusted is SensorState.UNKNOWN else trusted.value

        effective, _status = effective_device_state(
            entity_id, dependencies, read_state
        )
        return _state_of(effective)

    def build_auxiliary_observation(
        self,
        hass: HomeAssistant,
        config: RoomLightingConfig,
        now: int,
    ) -> BathroomObservation | None:
        """Build the pure bathroom observation, or ``None`` when it cannot map.

        The fan follows the room's light targets in their declared order, which
        is exactly the legacy two-light contract. A room with a different light
        count, or a fan without an entity, is left unmapped rather than
        guessed. A missing, unavailable or non-finite humidity reading becomes
        ``None``; the engine then never treats it as dry air.
        """

        if config.auxiliary is None:
            return None
        fan_policy = config.auxiliary.fan
        if fan_policy is None:
            return None
        fan_target = config.devices.auxiliary(fan_policy.target_id)
        if fan_target is None or fan_target.entity_id is None:
            return None
        followed_lights = self._auxiliary_light_targets(config)
        if followed_lights is None:
            return None
        inputs = bathroom_auxiliary_inputs(config)
        if inputs is None:
            return None
        _policy, times = inputs
        tz = _resolve_timezone(hass)
        local = datetime.fromtimestamp(now / 1000, tz)
        humidity_sensor = next(
            (
                sensor
                for sensor in config.devices.sensors
                if sensor.kind is SensorKind.HUMIDITY
                and sensor.entity_id is not None
            ),
            None,
        )
        humidity: float | None = None
        if humidity_sensor is not None:
            state = hass.states.get(humidity_sensor.entity_id)
            raw = _as_float(getattr(state, "state", None))
            if raw is not None and math.isfinite(raw):
                humidity = raw
        return BathroomObservation(
            band=bathroom_band(local.hour * 60 + local.minute, times),
            lights=tuple(
                self._auxiliary_state(hass, target.entity_id)
                for target in followed_lights
            ),
            humidity=humidity,
            fan=self._auxiliary_state(hass, fan_target.entity_id),
            fan_owned=has_proven_auto_ownership(
                self._ownership(config), fan_policy.target_id
            ),
        )

    @staticmethod
    def _auxiliary_light_targets(
        config: RoomLightingConfig,
    ) -> tuple[LightTarget, ...] | None:
        """Return the ordered two lights the fan follows, or None when unmapped.

        The explicit policy list wins; otherwise the room light targets are
        used only when there are exactly two, because the legacy contract is a
        two-light controller.
        """

        fan = config.auxiliary.fan if config.auxiliary is not None else None
        if fan is None:
            return None
        if fan.light_targets:
            by_id = {target.id: target for target in config.devices.light_targets}
            selected = tuple(
                by_id[target_id]
                for target_id in fan.light_targets
                if target_id in by_id
            )
            if len(selected) != len(fan.light_targets):
                return None
            return selected
        if len(config.devices.light_targets) != 2:
            return None
        return tuple(config.devices.light_targets)

    @staticmethod
    def _auxiliary_state(hass: HomeAssistant, entity_id: str | None) -> SensorState:
        if entity_id is None:
            return SensorState.UNKNOWN
        state = hass.states.get(entity_id)
        return _state_of(getattr(state, "state", None))


async def build_context(
    hass: HomeAssistant,
    config: RoomLightingConfig,
    now: int,
    *,
    ownership_provider: OwnershipProvider | None = None,
    protection_provider: ProtectionProvider | None = None,
    unobserved_since_provider: Callable[[], int | None] | None = None,
    sunrise: dt_time | None = None,
    sunset: dt_time | None = None,
    power_dependency_resolver: PowerDependencyResolver | None = None,
    power_migration_manifest: Mapping[str, str] | None = None,
) -> RoomLightingContext:
    """Read the live state for one room and return a decision-ready context."""

    provider = RoomLightingHaStateProvider(
        ownership_provider=ownership_provider,
        protection_provider=protection_provider,
        unobserved_since_provider=unobserved_since_provider,
        sunrise=sunrise,
        sunset=sunset,
        power_dependency_resolver=power_dependency_resolver,
        power_migration_manifest=power_migration_manifest,
    )
    return await provider.build_context(hass, config, now)


__all__ = [
    "RoomLightingHaStateProvider",
    "build_context",
    "DEFAULT_SUNRISE",
    "DEFAULT_SUNSET",
]
