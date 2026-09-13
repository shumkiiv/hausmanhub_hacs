"""Deduplicating critical sensor notifications and pure health evaluators.

The service keeps one active entry per room and stable sensor id, keeps
``since`` fixed at the first fault, updates the same entry when the reason
changes and removes the entry only after a confirmed fresh reading. The light
and climate helpers reuse the existing decision freshness rules instead of
inventing a second health model. Nothing in this module reads Home Assistant
directly or sends a physical command.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace

from ..domain.climate import (
    ClimateDeviceKind,
    ClimateEndpointRole,
    ClimateRegistry,
)
from ..domain.critical_sensor_notification import (
    CriticalSensorHealth,
    CriticalSensorNotification,
    CriticalSensorReason,
    CriticalSensorRole,
    build_critical_sensor_notification,
)
from ..domain.room_lighting import RoomLightingConfig, SensorKind
from ..domain.room_lighting_engine import (
    EnginePolicy,
    RoomLightingContext,
    SensorSnapshot,
)
from ..domain.room_lighting_ownership import SensorState
from .climate_ha_observations import (
    ClimateHaEntityState,
    ClimateHaStateView,
    _is_room_sensor_fresh,
    _number,
)

StateLookup = Callable[[str], str | None]

_CLIMATE_SENSOR_ROLES = {
    ClimateDeviceKind.TEMPERATURE_SENSOR: ClimateEndpointRole.TEMPERATURE,
    ClimateDeviceKind.HUMIDITY_SENSOR: ClimateEndpointRole.HUMIDITY,
}
_WINDOW_HEALTHY_STATES = frozenset({"on", "off"})


class CriticalSensorNotificationServiceViolation(ValueError):
    """An evaluation input is malformed."""


class CriticalSensorNotificationService:
    """Active critical sensor faults with stable dedup and recovery keys."""

    def __init__(self) -> None:
        self._active: dict[tuple[str, str], CriticalSensorNotification] = {}

    def evaluate(
        self,
        healths: Sequence[CriticalSensorHealth],
        *,
        now: int,
    ) -> tuple[CriticalSensorNotification, ...]:
        """Apply one evaluation cycle and return the sorted active set.

        A faulted input creates an entry with ``since=now`` only when the
        (room, sensor) pair is not active yet. Repeating the same reason keeps
        the entry and its ``since``; a changed reason replaces the same entry
        without touching ``since``. A healthy input with ``reason=None`` is the
        only signal allowed to clear an entry, so a missing or unknown reading
        can never drop a fault by accident.
        """

        if type(now) is not int or now < 0:
            raise CriticalSensorNotificationServiceViolation(
                "evaluation time must be non-negative seconds"
            )
        for health in healths:
            if not isinstance(health, CriticalSensorHealth):
                raise CriticalSensorNotificationServiceViolation(
                    "validated critical sensor health is required"
                )
            key = health.key
            if health.reason is None:
                self._active.pop(key, None)
                continue
            existing = self._active.get(key)
            if existing is None:
                self._active[key] = build_critical_sensor_notification(
                    health, since=now
                )
                continue
            if (
                existing.reason is not health.reason
                or existing.role is not health.role
                or existing.room_name != health.room_name
                or existing.sensor_name != health.sensor_name
                or existing.entity_id != health.entity_id
            ):
                # ``replace`` deliberately keeps ``since``: a reason change is
                # the same fault, not a new one.
                self._active[key] = replace(
                    existing,
                    room_name=health.room_name,
                    role=health.role,
                    sensor_name=health.sensor_name,
                    entity_id=health.entity_id,
                    reason=health.reason,
                )
        return self.active()

    def active(self) -> tuple[CriticalSensorNotification, ...]:
        """Return active faults in a stable order: first fault, then ids."""

        return tuple(
            sorted(
                self._active.values(),
                key=lambda item: (item.since, item.room_id, item.sensor_id),
            )
        )

    def payloads(self) -> tuple[dict[str, object], ...]:
        """Return the contract documents of every active fault."""

        return tuple(item.to_payload() for item in self.active())

    def clear(self) -> None:
        self._active.clear()


def light_sensor_health_inputs(
    config: RoomLightingConfig,
    context: RoomLightingContext,
    *,
    policy: EnginePolicy | None = None,
    state_lookup: StateLookup | None = None,
) -> tuple[CriticalSensorHealth, ...]:
    """Evaluate only the sensors a light decision really consumes.

    Presence and motion sensors always take part in the presence decision. An
    illuminance sensor takes part only while it is the configured illumination
    source. The room power switch takes part in the effective-state decision
    when its state can be read. Every other configured device is not critical.
    """

    if not isinstance(config, RoomLightingConfig):
        raise CriticalSensorNotificationServiceViolation(
            "validated room lighting config is required"
        )
    if not isinstance(context, RoomLightingContext):
        raise CriticalSensorNotificationServiceViolation(
            "validated room lighting context is required"
        )
    engine_policy = policy or EnginePolicy.from_config(config)
    snapshots = {sensor.sensor_id: sensor for sensor in context.sensors}
    used_illumination = (
        config.illumination.sensor if config.illumination is not None else None
    )
    result: list[CriticalSensorHealth] = []
    for sensor in config.devices.sensors:
        if sensor.entity_id is None:
            continue
        if (
            sensor.kind is SensorKind.ILLUMINANCE
            and sensor.entity_id != used_illumination
        ):
            # A configured lux sensor that feeds no illumination block is not
            # part of the light decision and must never raise a critical fault.
            continue
        snapshot = snapshots.get(sensor.id)
        if snapshot is None:
            # Not observed this cycle: no claim can be made, so the coordinator
            # keeps any earlier fault instead of inventing a recovery.
            continue
        result.append(
            CriticalSensorHealth(
                room_id=config.room_id,
                room_name=config.name,
                role=CriticalSensorRole.LIGHT,
                sensor_id=sensor.id,
                sensor_name=sensor.name,
                entity_id=sensor.entity_id,
                reason=_light_sensor_reason(snapshot, context, engine_policy),
            )
        )
    power = config.devices.power_switch
    if (
        power is not None
        and power.entity_id is not None
        and state_lookup is not None
    ):
        result.append(
            CriticalSensorHealth(
                room_id=config.room_id,
                room_name=config.name,
                role=CriticalSensorRole.LIGHT,
                sensor_id=power.id,
                sensor_name=power.name,
                entity_id=power.entity_id,
                reason=_switch_reason(state_lookup(power.entity_id)),
            )
        )
    return tuple(result)


def _light_sensor_reason(
    snapshot: SensorSnapshot,
    context: RoomLightingContext,
    policy: EnginePolicy,
) -> CriticalSensorReason | None:
    """Reuse the engine freshness windows for one sensor snapshot."""

    state = snapshot.state
    if state is SensorState.UNAVAILABLE:
        return CriticalSensorReason.UNAVAILABLE
    if state is SensorState.UNKNOWN:
        return CriticalSensorReason.UNKNOWN
    age_ms = context.now - snapshot.last_changed
    if snapshot.kind is SensorKind.ILLUMINANCE:
        if snapshot.lux is None or not snapshot.lux_healthy:
            return CriticalSensorReason.UNHEALTHY
        if age_ms > policy.sensor_freshness_seconds * 1000:
            return CriticalSensorReason.STALE
        return None
    # Presence or motion: an OFF reading older than the absence staleness
    # window, or an ON reading older than the presence freshness window, no
    # longer proves what the engine needs.
    if age_ms > policy.staleness_seconds * 1000:
        return CriticalSensorReason.STALE
    if state is SensorState.ON and age_ms > policy.sensor_freshness_seconds * 1000:
        return CriticalSensorReason.STALE
    return None


def _switch_reason(raw: str | None) -> CriticalSensorReason | None:
    if raw is None:
        return CriticalSensorReason.UNKNOWN
    value = raw.strip().lower()
    if value == "unavailable":
        return CriticalSensorReason.UNAVAILABLE
    if value == "unknown":
        return CriticalSensorReason.UNKNOWN
    if value in {"on", "off"}:
        return None
    return CriticalSensorReason.UNHEALTHY


def climate_sensor_health_inputs(
    registry: ClimateRegistry,
    states: ClimateHaStateView,
    *,
    observed_at: int,
) -> tuple[CriticalSensorHealth, ...]:
    """Evaluate the climate sensors a room decision consumes.

    Room temperature and humidity sensors feed the room observation; the room
    window entity feeds the safety decision. Values reuse the native adapter
    freshness window and numeric parsing. Leak sensors are not part of the
    current registry model, so they cannot be evaluated here.
    """

    if not isinstance(registry, ClimateRegistry):
        raise CriticalSensorNotificationServiceViolation(
            "validated climate registry is required"
        )
    if type(observed_at) is not int or observed_at < 0:
        raise CriticalSensorNotificationServiceViolation(
            "observed_at must be non-negative milliseconds"
        )
    result: list[CriticalSensorHealth] = []
    for room in registry.rooms:
        for device in registry.devices:
            if device.room_id != room.room_id:
                continue
            role = _CLIMATE_SENSOR_ROLES.get(device.kind)
            if role is None:
                continue
            endpoint = device.endpoint(role)
            if endpoint is None:
                continue
            result.append(
                CriticalSensorHealth(
                    room_id=room.room_id,
                    room_name=room.name,
                    role=CriticalSensorRole.CLIMATE,
                    sensor_id=device.device_id,
                    sensor_name=device.name,
                    entity_id=endpoint.entity_id,
                    reason=_climate_value_reason(
                        states.entity_state(endpoint.entity_id), observed_at
                    ),
                )
            )
        if room.window_entity_id is not None:
            result.append(
                CriticalSensorHealth(
                    room_id=room.room_id,
                    room_name=room.name,
                    role=CriticalSensorRole.CLIMATE,
                    sensor_id=f"{room.room_id}_window",
                    sensor_name="Датчик открытия окна",
                    entity_id=room.window_entity_id,
                    reason=_climate_window_reason(
                        states.entity_state(room.window_entity_id)
                    ),
                )
            )
    return tuple(result)


def _climate_value_reason(
    state: ClimateHaEntityState | None,
    observed_at: int,
) -> CriticalSensorReason | None:
    if state is None:
        return CriticalSensorReason.UNKNOWN
    raw = state.state.strip().lower()
    if raw == "unavailable":
        return CriticalSensorReason.UNAVAILABLE
    if raw == "unknown":
        return CriticalSensorReason.UNKNOWN
    if _number(raw) is None:
        return CriticalSensorReason.UNHEALTHY
    if not _is_room_sensor_fresh(state, observed_at):
        return CriticalSensorReason.STALE
    return None


def _climate_window_reason(
    state: ClimateHaEntityState | None,
) -> CriticalSensorReason | None:
    if state is None:
        return CriticalSensorReason.UNKNOWN
    raw = state.state.strip().lower()
    if raw == "unavailable":
        return CriticalSensorReason.UNAVAILABLE
    if raw == "unknown":
        return CriticalSensorReason.UNKNOWN
    if raw in _WINDOW_HEALTHY_STATES:
        return None
    return CriticalSensorReason.UNKNOWN


__all__ = [
    "CriticalSensorNotificationService",
    "CriticalSensorNotificationServiceViolation",
    "StateLookup",
    "climate_sensor_health_inputs",
    "light_sensor_health_inputs",
]
