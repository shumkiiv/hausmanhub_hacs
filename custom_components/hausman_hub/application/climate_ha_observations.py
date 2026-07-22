"""Build HausmanHub climate observations from Home Assistant states.

This adapter is the only place that reads native entity states through the
private registry bindings.  It consumes an abstract immutable state view, so
the pure application layer never imports Home Assistant.  Missing, stale, or
contradictory values stay honestly unknown; they never become permissive
defaults.  The result contains stable HausmanHub ids only, never entity ids,
service names, transports, or commands.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
from typing import Protocol

from ..domain.climate import (
    ClimateDevice,
    ClimateDeviceKind,
    ClimateEndpointRole,
    ClimateRegistry,
)
from ..domain.climate_observation import (
    ClimateDataStatus,
    ClimateDeviceActivity,
    ClimateDeviceAvailability,
    ClimateDeviceObservation,
    ClimateFanMode,
    ClimateHomeObservation,
    ClimateControlObservation,
    ClimateDayPeriod,
    ClimateOccupancyMode,
    ClimateObservationDeviceKind,
    ClimateObservationSnapshot,
    ClimatePhysicalFeedback,
    ClimateRoomMode,
    ClimateRoomObservation,
    ClimateSeason,
    ClimateTemperatureQuality,
    ClimateWindowState,
)
from ..domain.climate_protection import ClimateProtectionMemory
from ..domain.contours import ContourDefinition, ContourMode

MAX_NATIVE_STATE_AGE_MS = 5 * 60 * 1000
MAX_STATE_LENGTH = 64
MAX_ATTRIBUTES = 64
_OBSERVATION_DEVICE_KINDS = {
    kind: ClimateObservationDeviceKind(kind.value) for kind in ClimateDeviceKind
}
_PASSIVE_KINDS = frozenset(
    {
        ClimateDeviceKind.TEMPERATURE_SENSOR,
        ClimateDeviceKind.HUMIDITY_SENSOR,
    }
)
_SENSOR_ROLES = {
    ClimateDeviceKind.TEMPERATURE_SENSOR: ClimateEndpointRole.TEMPERATURE,
    ClimateDeviceKind.HUMIDITY_SENSOR: ClimateEndpointRole.HUMIDITY,
}
_UNAVAILABLE_STATES = frozenset({"unavailable", "unknown"})


class ClimateHaObservationViolation(ValueError):
    """A native Home Assistant state input is unsafe or contradictory."""


@dataclass(frozen=True, slots=True)
class ClimateHaEntityState:
    """One immutable bounded entity state accepted by the native adapter."""

    entity_id: str
    state: str
    attributes: Mapping[str, object]
    last_updated_ms: int

    def __post_init__(self) -> None:
        if not isinstance(self.entity_id, str) or "." not in self.entity_id:
            raise ClimateHaObservationViolation("entity id is invalid")
        if not isinstance(self.state, str) or len(self.state) > MAX_STATE_LENGTH:
            raise ClimateHaObservationViolation("entity state is invalid")
        if not isinstance(self.attributes, Mapping) or any(
            not isinstance(key, str) for key in self.attributes
        ):
            raise ClimateHaObservationViolation("entity attributes must be an object")
        if len(self.attributes) > MAX_ATTRIBUTES:
            raise ClimateHaObservationViolation("entity attributes are unbounded")
        if type(self.last_updated_ms) is not int or self.last_updated_ms < 0:
            raise ClimateHaObservationViolation(
                "entity update time must be a non-negative integer"
            )


class ClimateHaStateView(Protocol):
    """Minimal read-only native state boundary used by the adapter."""

    def entity_state(self, entity_id: str) -> ClimateHaEntityState | None:
        """Return one current entity state, or None when it does not exist."""


def build_native_ha_climate_observation(
    registry: ClimateRegistry,
    contour: ContourDefinition | None,
    states: ClimateHaStateView,
    *,
    observed_at: int,
    protection: ClimateProtectionMemory,
    local_time: tuple[int, int] | None = None,
    previous_weather_lockout: bool | None = None,
) -> ClimateObservationSnapshot:
    """Build one complete observation from registry bindings and HA states."""

    if not isinstance(registry, ClimateRegistry):
        raise ClimateHaObservationViolation("a validated climate registry is required")
    if contour is not None and not isinstance(contour, ContourDefinition):
        raise ClimateHaObservationViolation("a validated climate contour is required")
    if not isinstance(protection, ClimateProtectionMemory):
        raise ClimateHaObservationViolation("validated protection memory is required")
    if type(observed_at) is not int or observed_at < 0:
        raise ClimateHaObservationViolation("observation time must be non-negative")
    if local_time is not None and (
        type(local_time) is not tuple
        or len(local_time) != 2
        or any(type(value) is not int for value in local_time)
    ):
        raise ClimateHaObservationViolation("local time must be an hour/minute pair")

    devices = tuple(
        _device_observation(device, states, protection, observed_at)
        for device in registry.devices
    )
    rooms = tuple(
        _room_observation(room, registry, contour, devices, states, observed_at)
        for room in registry.rooms
    )
    return ClimateObservationSnapshot(
        observed_at=observed_at,
        source_generated_at=observed_at,
        data_status=ClimateDataStatus.FRESH,
        home=_home_observation(
            registry,
            contour,
            states,
            observed_at,
            local_time,
            previous_weather_lockout,
        ),
        control=ClimateControlObservation(),
        rooms=rooms,
        devices=devices,
    )


def _room_observation(
    room,
    registry: ClimateRegistry,
    contour: ContourDefinition | None,
    devices: tuple[ClimateDeviceObservation, ...],
    states: ClimateHaStateView,
    observed_at: int,
) -> ClimateRoomObservation:
    bound_entity_ids = _room_bound_entity_ids(registry, room)
    present = [
        state
        for entity_id in bound_entity_ids
        if (state := states.entity_state(entity_id)) is not None
    ]
    if bound_entity_ids and not present:
        return ClimateRoomObservation(
            room_id=room.room_id,
            name=room.name,
            data_status=ClimateDataStatus.UNAVAILABLE,
        )
    temperature, temperature_fresh = _room_sensor_number(
        registry,
        room.room_id,
        ClimateDeviceKind.TEMPERATURE_SENSOR,
        states,
        observed_at,
    )
    if temperature is None:
        temperature, temperature_fresh = _room_climate_current_temperature(
            registry,
            room.room_id,
            devices,
            states,
            observed_at,
        )
    humidity, humidity_fresh = _room_sensor_number(
        registry,
        room.room_id,
        ClimateDeviceKind.HUMIDITY_SENSOR,
        states,
        observed_at,
    )
    window = _window_state(room.window_entity_id, states)
    contour_room = (
        None
        if contour is None
        else next(
            (item for item in contour.rooms if item.room_id == room.room_id),
            None,
        )
    )
    stale = (temperature is not None and not temperature_fresh) or (
        humidity is not None and not humidity_fresh
    )
    return ClimateRoomObservation(
        room_id=room.room_id,
        name=room.name,
        data_status=(
            ClimateDataStatus.STALE if stale else ClimateDataStatus.FRESH
        ),
        temperature=temperature,
        humidity=humidity,
        temperature_quality=(
            ClimateTemperatureQuality.NORMAL
            if temperature is not None
            else ClimateTemperatureQuality.UNKNOWN
        ),
        window=window,
        mode=_room_mode(contour),
        observed_target_temperature=(
            None
            if contour_room is None
            else contour_room.active_settings.target_temperature
        ),
        observed_target_humidity=(
            None
            if contour_room is None
            else float(contour_room.active_settings.target_humidity)
        ),
        observed_target_strategy=(
            None
            if contour_room is None
            else contour_room.active_settings.strategy.value
        ),
        authority_eligible=(
            not stale
            and temperature is not None
            and contour is not None
            and contour.mode is ContourMode.AUTOMATIC
        ),
    )


def _room_bound_entity_ids(registry: ClimateRegistry, room) -> tuple[str, ...]:
    entity_ids: list[str] = []
    if room.window_entity_id is not None:
        entity_ids.append(room.window_entity_id)
    for device in registry.devices:
        if device.room_id != room.room_id:
            continue
        entity_ids.extend(endpoint.entity_id for endpoint in device.endpoints)
    return tuple(entity_ids)


def _room_sensor_number(
    registry: ClimateRegistry,
    room_id: str,
    kind: ClimateDeviceKind,
    states: ClimateHaStateView,
    observed_at: int,
) -> tuple[float | None, bool]:
    role = _SENSOR_ROLES[kind]
    for device in registry.devices:
        if device.room_id != room_id or device.kind is not kind:
            continue
        endpoint = device.endpoint(role)
        if endpoint is None:
            continue
        state = states.entity_state(endpoint.entity_id)
        if state is None or state.state in _UNAVAILABLE_STATES:
            continue
        value = _number(state.state)
        if value is None:
            continue
        return value, _is_fresh(state, observed_at)
    return None, True


def _is_fresh(state: ClimateHaEntityState, observed_at: int) -> bool:
    age = max(0, observed_at - state.last_updated_ms)
    return age <= MAX_NATIVE_STATE_AGE_MS


def _room_climate_current_temperature(
    registry: ClimateRegistry,
    room_id: str,
    devices: tuple[ClimateDeviceObservation, ...],
    states: ClimateHaStateView,
    observed_at: int,
) -> tuple[float | None, bool]:
    for device in registry.devices:
        if device.room_id != room_id or device.kind in _PASSIVE_KINDS:
            continue
        observed = next(
            (item for item in devices if item.device_id == device.device_id),
            None,
        )
        if observed is None or observed.availability is not (
            ClimateDeviceAvailability.AVAILABLE
        ):
            continue
        endpoint = device.endpoint(ClimateEndpointRole.CONTROL)
        if endpoint is None:
            continue
        state = states.entity_state(endpoint.entity_id)
        if state is None:
            continue
        value = _number(state.attributes.get("current_temperature"))
        if value is not None:
            return value, _is_fresh(state, observed_at)
    return None, True


def _window_state(
    window_entity_id: str | None,
    states: ClimateHaStateView,
) -> ClimateWindowState:
    if window_entity_id is None:
        return ClimateWindowState.NOT_CONFIGURED
    state = states.entity_state(window_entity_id)
    if state is None or state.state in _UNAVAILABLE_STATES:
        return ClimateWindowState.UNKNOWN
    if state.state == "on":
        return ClimateWindowState.OPEN
    if state.state == "off":
        return ClimateWindowState.CLOSED
    return ClimateWindowState.UNKNOWN


def _room_mode(contour: ContourDefinition | None) -> ClimateRoomMode:
    if contour is not None and contour.mode is ContourMode.AUTOMATIC:
        return ClimateRoomMode.AUTO
    return ClimateRoomMode.UNKNOWN


def _home_observation(
    registry: ClimateRegistry,
    contour: ContourDefinition | None,
    states: ClimateHaStateView,
    observed_at: int,
    local_time: tuple[int, int] | None,
    previous_weather_lockout: bool | None,
) -> ClimateHomeObservation:
    home = registry.home
    outdoor = _home_number(home.outdoor_temperature_entity_id, states)
    heat_load = _heat_load_temperature(
        home.outdoor_temperature_entity_id,
        states,
        observed_at,
    )
    return ClimateHomeObservation(
        season=ClimateSeason.UNKNOWN,
        period=_day_period(contour, local_time),
        outdoor_temperature=outdoor,
        # The heat-load rule accepts the outdoor reading only when the
        # configured sensor is fresh, numeric, and inside a plausible range.
        heat_load_temperature=heat_load,
        heating_lockout_high=home.heating_lockout_high,
        heating_lockout_low=home.heating_lockout_low,
        central_heating_on=_home_switch(home.central_heating_entity_id, states),
        central_heating_configured=home.central_heating_entity_id is not None,
        weather_heating_lockout=_weather_heating_lockout(
            heat_load,
            home.heating_lockout_high,
            home.heating_lockout_low,
            previous_weather_lockout,
        ),
        occupancy=_occupancy(home.presence_entity_id, states),
    )


def _weather_heating_lockout(
    heat_load_temperature: float | None,
    high: float,
    low: float,
    previous: bool | None,
) -> bool:
    if heat_load_temperature is None:
        return False
    if heat_load_temperature >= high:
        return True
    if heat_load_temperature <= low:
        return False
    # Inside the hysteresis band the previous explicit permission wins;
    # a first observation there fails closed instead of inventing permission.
    return previous if previous is not None else True


def _day_period(
    contour: ContourDefinition | None,
    local_time: tuple[int, int] | None,
) -> ClimateDayPeriod:
    if contour is None or not contour.schedule.enabled or local_time is None:
        return ClimateDayPeriod.UNKNOWN
    hour, minute = local_time
    return ClimateDayPeriod(
        contour.schedule.profile_at(hour=hour, minute=minute).value
    )


def _home_number(
    entity_id: str | None,
    states: ClimateHaStateView,
) -> float | None:
    if entity_id is None:
        return None
    state = states.entity_state(entity_id)
    if state is None or state.state in _UNAVAILABLE_STATES:
        return None
    value = _number(state.state)
    # An implausible outdoor reading is unusable, never a crash or a default.
    if value is None or not -80.0 <= value <= 80.0:
        return None
    return value


def _heat_load_temperature(
    entity_id: str | None,
    states: ClimateHaStateView,
    observed_at: int,
) -> float | None:
    if entity_id is None:
        return None
    state = states.entity_state(entity_id)
    if state is None or state.state in _UNAVAILABLE_STATES:
        return None
    if not _is_fresh(state, observed_at):
        return None
    value = _number(state.state)
    if value is None or not -80.0 <= value <= 100.0:
        return None
    return value


def _home_switch(
    entity_id: str | None,
    states: ClimateHaStateView,
) -> bool | None:
    if entity_id is None:
        return None
    state = states.entity_state(entity_id)
    if state is None or state.state in _UNAVAILABLE_STATES:
        return None
    if state.state == "on":
        return True
    if state.state == "off":
        return False
    return None


def _occupancy(
    entity_id: str | None,
    states: ClimateHaStateView,
) -> ClimateOccupancyMode:
    if entity_id is None:
        return ClimateOccupancyMode.HOME
    state = states.entity_state(entity_id)
    if state is None or state.state in _UNAVAILABLE_STATES:
        # A configured but unobserved presence forbids auto-humidifying
        # without inventing an away setback.
        return ClimateOccupancyMode.UNKNOWN
    if state.state in {"off", "not_home"}:
        return ClimateOccupancyMode.AWAY_SETBACK
    if state.state in {"on", "home"}:
        return ClimateOccupancyMode.HOME
    # A custom person/device_tracker zone is deliberately treated as home:
    # an unfamiliar zone label must not invent an away policy.
    return ClimateOccupancyMode.HOME


def _device_observation(
    device: ClimateDevice,
    states: ClimateHaStateView,
    protection: ClimateProtectionMemory,
    observed_at: int,
) -> ClimateDeviceObservation:
    kind = _OBSERVATION_DEVICE_KINDS[device.kind]
    if device.kind in _PASSIVE_KINDS:
        return _passive_device_observation(device, kind, states)
    endpoint = device.endpoint(ClimateEndpointRole.CONTROL)
    if endpoint is None:
        return ClimateDeviceObservation(
            device_id=device.device_id,
            name=device.name,
            room_id=device.room_id,
            kind=kind,
            availability=ClimateDeviceAvailability.MISSING,
        )
    state = states.entity_state(endpoint.entity_id)
    if state is None:
        return ClimateDeviceObservation(
            device_id=device.device_id,
            name=device.name,
            room_id=device.room_id,
            kind=kind,
            availability=ClimateDeviceAvailability.MISSING,
        )
    if state.state in _UNAVAILABLE_STATES:
        return ClimateDeviceObservation(
            device_id=device.device_id,
            name=device.name,
            room_id=device.room_id,
            kind=kind,
            availability=ClimateDeviceAvailability.UNAVAILABLE,
        )
    transitions = _protection_transitions(device, protection, observed_at)
    return ClimateDeviceObservation(
        device_id=device.device_id,
        name=device.name,
        room_id=device.room_id,
        kind=kind,
        availability=ClimateDeviceAvailability.AVAILABLE,
        activity=_device_activity(device.kind, state),
        current_target_temperature=_bounded_number(
            state.attributes.get("temperature"),
            10,
            35,
        ),
        current_target_humidity=_bounded_number(
            state.attributes.get("humidity"),
            0,
            100,
        ),
        fan_mode=_fan_mode(state.attributes.get("fan_mode")),
        physical_feedback=_physical_feedback(device, states, observed_at),
        last_started_at=transitions[0],
        last_stopped_at=transitions[1],
        confirmed_short_cycle_count=transitions[2],
    )


def _passive_device_observation(
    device: ClimateDevice,
    kind: ClimateObservationDeviceKind,
    states: ClimateHaStateView,
) -> ClimateDeviceObservation:
    role = _SENSOR_ROLES[device.kind]
    endpoint = device.endpoint(role)
    state = None if endpoint is None else states.entity_state(endpoint.entity_id)
    if state is None:
        return ClimateDeviceObservation(
            device_id=device.device_id,
            name=device.name,
            room_id=device.room_id,
            kind=kind,
            availability=ClimateDeviceAvailability.MISSING,
        )
    if state.state in _UNAVAILABLE_STATES:
        return ClimateDeviceObservation(
            device_id=device.device_id,
            name=device.name,
            room_id=device.room_id,
            kind=kind,
            availability=ClimateDeviceAvailability.UNAVAILABLE,
        )
    return ClimateDeviceObservation(
        device_id=device.device_id,
        name=device.name,
        room_id=device.room_id,
        kind=kind,
        availability=ClimateDeviceAvailability.AVAILABLE,
        activity=ClimateDeviceActivity.IDLE,
    )


def _device_activity(
    kind: ClimateDeviceKind,
    state: ClimateHaEntityState,
) -> ClimateDeviceActivity:
    if kind is ClimateDeviceKind.HUMIDIFIER:
        if state.state == "on":
            return ClimateDeviceActivity.HUMIDIFYING
        if state.state == "off":
            return ClimateDeviceActivity.STOPPED
        return ClimateDeviceActivity.UNKNOWN
    action = state.attributes.get("hvac_action")
    if isinstance(action, str):
        if action == "cooling":
            return ClimateDeviceActivity.COOLING
        if action == "heating":
            return ClimateDeviceActivity.HEATING
        if action == "idle":
            return ClimateDeviceActivity.IDLE
        if action == "off":
            return ClimateDeviceActivity.STOPPED
        if action in {"fan", "drying"}:
            return ClimateDeviceActivity.RUNNING
    if state.state == "off":
        return ClimateDeviceActivity.STOPPED
    if state.state == "cool":
        return ClimateDeviceActivity.COOLING
    if state.state == "heat":
        return ClimateDeviceActivity.HEATING
    if state.state in {"heat_cool", "auto", "dry", "fan_only"}:
        return ClimateDeviceActivity.IDLE
    return ClimateDeviceActivity.UNKNOWN


def _physical_feedback(
    device: ClimateDevice,
    states: ClimateHaStateView,
    observed_at: int,
) -> ClimatePhysicalFeedback:
    endpoint = device.endpoint(ClimateEndpointRole.PHYSICAL_FEEDBACK)
    if endpoint is None:
        return ClimatePhysicalFeedback.UNKNOWN
    state = states.entity_state(endpoint.entity_id)
    if state is None or state.state in _UNAVAILABLE_STATES:
        return ClimatePhysicalFeedback.UNKNOWN
    if not _is_fresh(state, observed_at):
        return ClimatePhysicalFeedback.STALE
    return ClimatePhysicalFeedback.CONFIRMED


def _protection_transitions(
    device: ClimateDevice,
    protection: ClimateProtectionMemory,
    observed_at: int,
) -> tuple[int | None, int | None, int | None]:
    if device.kind is not ClimateDeviceKind.AIR_CONDITIONER:
        return None, None, None
    memory = protection.device(device.device_id)
    if memory is None or memory.observed_at > observed_at:
        return None, None, None
    last_started = (
        memory.last_started_at
        if memory.last_started_at is not None
        and memory.last_started_at <= observed_at
        else None
    )
    last_stopped = (
        memory.last_stopped_at
        if memory.last_stopped_at is not None
        and memory.last_stopped_at <= observed_at
        else None
    )
    return last_started, last_stopped, memory.confirmed_short_cycle_count


def _fan_mode(value: object) -> ClimateFanMode | None:
    if not isinstance(value, str):
        return None
    try:
        return ClimateFanMode(value)
    except ValueError:
        return None


def _number(value: object) -> float | None:
    number: float | None = None
    if type(value) in {int, float}:
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return None
    if number is None or not math.isfinite(number):
        return None
    return number


def _bounded_number(
    value: object,
    minimum: float,
    maximum: float,
) -> float | None:
    number = _number(value)
    if number is None or number < minimum or number > maximum:
        return None
    return number
