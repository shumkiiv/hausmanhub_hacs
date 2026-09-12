"""Live Home Assistant state provider for room lighting.

The provider only reads ``hass.states`` and never calls a Home Assistant
service. Unknown, unavailable or missing entities degrade to ``unknown`` /
``unavailable`` snapshots instead of raising, so a room with a broken sensor
keeps producing a decision with an explicit skip reason.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, time as dt_time, timezone, tzinfo
import logging
from typing import TYPE_CHECKING

from ..domain.room_lighting import (
    LightKind,
    LightTarget,
    RoomLightingConfig,
    RoomSensor,
    SensorKind,
)
from ..domain.room_lighting_engine import (
    LightSnapshot,
    ProtectionSnapshot,
    RoomLightingContext,
    SensorSnapshot,
)
from ..domain.room_lighting_ownership import OwnershipSnapshot, SensorState
from .room_lighting_color import device_kelvin_bounds, reflect_inverted_kelvin

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

DEFAULT_SUNRISE = dt_time(7, 0)
DEFAULT_SUNSET = dt_time(19, 0)
SUN_ENTITY_ID = "sun.sun"
MAX_BRIGHTNESS_255 = 255.0

OwnershipProvider = Callable[[RoomLightingConfig], Sequence[OwnershipSnapshot]]
ProtectionProvider = Callable[[RoomLightingConfig], ProtectionSnapshot]
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
        ownership_provider: OwnershipProvider | None = None,
        protection_provider: ProtectionProvider | None = None,
        unobserved_since_provider: Callable[[], int | None] | None = None,
        sunrise: dt_time | None = None,
        sunset: dt_time | None = None,
    ) -> None:
        self._now_ms = now_ms or _default_now_ms
        self._ownership_provider = ownership_provider
        self._protection_provider = protection_provider
        self._unobserved_since_provider = unobserved_since_provider
        self._sunrise = sunrise
        self._sunset = sunset

    async def build_context(
        self,
        hass: HomeAssistant,
        config: RoomLightingConfig,
        now: int,
    ) -> RoomLightingContext:
        tz = _resolve_timezone(hass)
        sunrise, sunset = self._sun_times(hass, tz)
        power_on = self._room_power_on(hass, config)
        return RoomLightingContext(
            now=now,
            timezone=tz,
            sunrise=sunrise,
            sunset=sunset,
            sensors=tuple(
                self._sensor_snapshot(hass, sensor, now)
                for sensor in config.devices.sensors
            ),
            lights=tuple(
                self._light_snapshot(hass, target, now, power_on=power_on)
                for target in config.devices.light_targets
            ),
            ownership=self._ownership(config),
            protection=self._protection(config),
            unobserved_since=self._unobserved_since(),
        )

    def _room_power_on(self, hass: HomeAssistant, config: RoomLightingConfig) -> bool:
        """Whether the room's power switch, when configured, is provably on."""

        power = config.devices.power_switch
        if power is None or power.entity_id is None:
            return True
        state = hass.states.get(power.entity_id)
        return _state_of(getattr(state, "state", None)) is SensorState.ON

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
    ) -> tuple[dt_time, dt_time]:
        if self._sunrise is not None and self._sunset is not None:
            return self._sunrise, self._sunset
        sunrise, sunset = DEFAULT_SUNRISE, DEFAULT_SUNSET
        state = hass.states.get(SUN_ENTITY_ID)
        attributes = getattr(state, "attributes", None)
        if isinstance(attributes, dict):
            parsed_rising = _parse_sun_time(attributes.get("next_rising"), tz)
            parsed_setting = _parse_sun_time(attributes.get("next_setting"), tz)
            if parsed_rising is not None:
                sunrise = parsed_rising
            if parsed_setting is not None:
                sunset = parsed_setting
        return (
            self._sunrise or sunrise,
            self._sunset or sunset,
        )

    def _sensor_snapshot(
        self, hass: HomeAssistant, sensor: RoomSensor, now: int
    ) -> SensorSnapshot:
        state = hass.states.get(sensor.entity_id) if sensor.entity_id else None
        if state is None:
            return SensorSnapshot(
                sensor_id=sensor.id,
                kind=sensor.kind,
                state=SensorState.UNKNOWN,
                last_changed=now,
                lux_healthy=False,
            )
        raw = getattr(state, "state", None)
        last_changed = _last_changed_ms(state, now=now)
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
                )
            return SensorSnapshot(
                sensor_id=sensor.id,
                kind=sensor.kind,
                state=SensorState.ON,
                last_changed=last_changed,
                lux=lux,
                lux_healthy=True,
            )
        return SensorSnapshot(
            sensor_id=sensor.id,
            kind=sensor.kind,
            state=_state_of(raw),
            last_changed=last_changed,
        )

    def _light_snapshot(
        self,
        hass: HomeAssistant,
        target: LightTarget,
        now: int,
        *,
        power_on: bool = True,
    ) -> LightSnapshot:
        if not power_on:
            # An unpowered target cannot be on, regardless of the stale module
            # state: the engine must treat it as off and request a turn-on.
            return LightSnapshot(
                target_id=target.id,
                state=SensorState.OFF,
                last_changed=now,
            )
        state = hass.states.get(target.entity_id) if target.entity_id else None
        if state is None:
            return LightSnapshot(
                target_id=target.id,
                state=SensorState.UNKNOWN,
                last_changed=now,
            )
        raw = getattr(state, "state", None)
        light_state = _state_of(raw)
        last_changed = _last_changed_ms(state, now=now)
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
        return LightSnapshot(
            target_id=target.id,
            state=light_state,
            last_changed=last_changed,
            brightness=brightness,
            color_temperature=color_temperature,
        )


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
) -> RoomLightingContext:
    """Read the live state for one room and return a decision-ready context."""

    provider = RoomLightingHaStateProvider(
        ownership_provider=ownership_provider,
        protection_provider=protection_provider,
        unobserved_since_provider=unobserved_since_provider,
        sunrise=sunrise,
        sunset=sunset,
    )
    return await provider.build_context(hass, config, now)


__all__ = [
    "RoomLightingHaStateProvider",
    "build_context",
    "DEFAULT_SUNRISE",
    "DEFAULT_SUNSET",
]
