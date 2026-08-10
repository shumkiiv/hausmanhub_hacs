"""Detect external direct Wi-Fi shutdowns and persist room ownership."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from ..domain.climate import (
    ClimateControlChannel,
    ClimateDeviceKind,
    ClimateEndpointRole,
    ClimateRegistry,
)
from ..domain.climate_ha_calls import (
    ClimateHaHvacMode,
    ClimateHaService,
    ClimateHaServiceCall,
)
from ..domain.climate_manual import (
    CLIMATE_MANUAL_MEMORY_VERSION,
    ClimateDirectWifiPhase,
    ClimateDirectWifiState,
    ClimateManualMemory,
    ClimateManualViolation,
    empty_climate_manual_memory,
)
from ..domain.climate_observation import (
    ClimateDataStatus,
    ClimateDeviceActivity,
    ClimateDeviceAvailability,
    ClimateObservationSnapshot,
    ClimateRoomMode,
)


_ACTIVE_ACTIVITIES = frozenset(
    {
        ClimateDeviceActivity.RUNNING,
        ClimateDeviceActivity.IDLE,
        ClimateDeviceActivity.COOLING,
        ClimateDeviceActivity.HEATING,
    }
)
DIRECT_WIFI_COMMAND_ATTRIBUTION_MS = 5 * 60 * 1000


def reconcile_climate_manual_memory(
    memory: ClimateManualMemory,
    registry: ClimateRegistry,
    *,
    now_ms: int,
) -> tuple[ClimateManualMemory, bool]:
    """Drop removed rooms/devices and reset future-dated memory."""

    if not isinstance(memory, ClimateManualMemory):
        raise ClimateManualViolation("validated manual-control memory is required")
    if not isinstance(registry, ClimateRegistry):
        raise ClimateManualViolation("validated climate registry is required")
    _timestamp(now_ms, "manual-control reconciliation time")
    if memory.updated_at > now_ms:
        return empty_climate_manual_memory(updated_at=now_ms), True
    rooms = {room.room_id for room in registry.rooms}
    direct = {
        device.device_id: device
        for device in registry.devices
        if device.kind is ClimateDeviceKind.AIR_CONDITIONER
        and device.control_channel is ClimateControlChannel.DIRECT_WIFI
    }
    manual_room_ids = tuple(
        room.room_id for room in registry.rooms if room.room_id in memory.manual_room_ids
    )
    devices = tuple(
        state
        for device in registry.devices
        if (state := memory.device(device.device_id)) is not None
        and (configured := direct.get(state.device_id)) is not None
        and configured.room_id == state.room_id
        and state.room_id in rooms
        and (state.commanded_at is None or state.commanded_at <= now_ms)
        and state.observed_at <= now_ms
    )
    updated = ClimateManualMemory(
        updated_at=memory.updated_at,
        manual_room_ids=manual_room_ids,
        devices=devices,
    )
    if updated == memory:
        return memory, False
    return replace(updated, updated_at=now_ms), True


def update_direct_wifi_observation(
    memory: ClimateManualMemory,
    registry: ClimateRegistry,
    observation: ClimateObservationSnapshot,
) -> tuple[ClimateManualMemory, bool]:
    """Enter manual mode after an unattributed active-to-off transition."""

    if not isinstance(memory, ClimateManualMemory):
        raise ClimateManualViolation("validated manual-control memory is required")
    if not isinstance(registry, ClimateRegistry):
        raise ClimateManualViolation("validated climate registry is required")
    if not isinstance(observation, ClimateObservationSnapshot):
        raise ClimateManualViolation("validated climate observation is required")
    if memory.updated_at > observation.observed_at:
        raise ClimateManualViolation(
            "manual-control memory cannot be newer than the observation"
        )

    manual = set(memory.manual_room_ids)
    states: list[ClimateDirectWifiState] = []
    for configured in registry.devices:
        if (
            configured.kind is not ClimateDeviceKind.AIR_CONDITIONER
            or configured.control_channel is not ClimateControlChannel.DIRECT_WIFI
        ):
            continue
        previous = memory.device(configured.device_id)
        observed = observation.device(configured.device_id)
        phase = _observed_phase(observed)
        if (
            observation.data_status is not ClimateDataStatus.FRESH
            or phase is None
            or observed is None
            or observed.room_id != configured.room_id
        ):
            if previous is not None:
                states.append(previous)
            continue
        command_fresh = bool(
            previous is not None
            and previous.commanded_at is not None
            and previous.commanded_at >= previous.observed_at
            and previous.commanded_at <= observation.observed_at
            and observation.observed_at - previous.commanded_at
            <= DIRECT_WIFI_COMMAND_ATTRIBUTION_MS
        )
        own_off = bool(
            command_fresh
            and previous is not None
            and previous.commanded_phase is ClimateDirectWifiPhase.INACTIVE
        )
        if (
            previous is not None
            and previous.observed_phase is ClimateDirectWifiPhase.ACTIVE
            and phase is ClimateDirectWifiPhase.INACTIVE
            and not own_off
        ):
            manual.add(configured.room_id)
        phase_changed = previous is None or previous.observed_phase is not phase
        states.append(
            ClimateDirectWifiState(
                device_id=configured.device_id,
                room_id=configured.room_id,
                observed_phase=phase,
                observed_at=(
                    observation.observed_at
                    if phase_changed
                    else previous.observed_at
                ),
                commanded_phase=(
                    None
                    if own_off or not command_fresh
                    else None if previous is None else previous.commanded_phase
                ),
                commanded_at=(
                    None
                    if own_off or not command_fresh
                    else None if previous is None else previous.commanded_at
                ),
            )
        )
    ordered_manual = tuple(
        room.room_id for room in registry.rooms if room.room_id in manual
    )
    content_changed = (
        ordered_manual != memory.manual_room_ids or tuple(states) != memory.devices
    )
    updated = ClimateManualMemory(
        updated_at=observation.observed_at if content_changed else memory.updated_at,
        manual_room_ids=ordered_manual,
        devices=tuple(states),
    )
    return updated, updated != memory


def record_direct_wifi_commands(
    memory: ClimateManualMemory,
    registry: ClimateRegistry,
    calls: tuple[ClimateHaServiceCall, ...],
    *,
    executed_count: int,
    commanded_at: int,
) -> tuple[ClimateManualMemory, bool]:
    """Remember the successfully executed prefix of direct Wi-Fi power calls."""

    if not 0 <= executed_count <= len(calls):
        raise ClimateManualViolation("executed command count is invalid")
    _timestamp(commanded_at, "manual-control command time")
    by_entity = {
        endpoint.entity_id: device
        for device in registry.devices
        if device.kind is ClimateDeviceKind.AIR_CONDITIONER
        and device.control_channel is ClimateControlChannel.DIRECT_WIFI
        and (endpoint := device.endpoint(ClimateEndpointRole.CONTROL)) is not None
    }
    updates: dict[str, ClimateDirectWifiPhase] = {}
    for call in calls[:executed_count]:
        if call.service is not ClimateHaService.CLIMATE_SET_HVAC_MODE:
            continue
        if call.entity_id not in by_entity or call.hvac_mode is None:
            continue
        updates[by_entity[call.entity_id].device_id] = (
            ClimateDirectWifiPhase.INACTIVE
            if call.hvac_mode is ClimateHaHvacMode.OFF
            else ClimateDirectWifiPhase.ACTIVE
        )
    if not updates:
        return memory, False
    states = tuple(
        replace(
            state,
            commanded_phase=updates[state.device_id],
            commanded_at=commanded_at,
        )
        if state.device_id in updates
        else state
        for state in memory.devices
    )
    updated = ClimateManualMemory(
        updated_at=max(memory.updated_at, commanded_at),
        manual_room_ids=memory.manual_room_ids,
        devices=states,
    )
    return updated, updated != memory


def with_climate_room_mode(
    memory: ClimateManualMemory,
    registry: ClimateRegistry,
    *,
    room_id: str,
    manual: bool,
    updated_at: int,
) -> ClimateManualMemory:
    """Persist an explicit manual/automatic choice for one configured room."""

    _timestamp(updated_at, "manual-control update time")
    if registry.room(room_id) is None:
        raise ClimateManualViolation("manual-control room is not configured")
    selected = set(memory.manual_room_ids)
    if manual:
        selected.add(room_id)
    else:
        selected.discard(room_id)
    return ClimateManualMemory(
        updated_at=max(memory.updated_at, updated_at),
        manual_room_ids=tuple(
            room.room_id for room in registry.rooms if room.room_id in selected
        ),
        devices=memory.devices,
    )


def apply_manual_rooms(
    observation: ClimateObservationSnapshot,
    memory: ClimateManualMemory,
) -> ClimateObservationSnapshot:
    """Overlay durable manual ownership without changing physical facts."""

    manual = set(memory.manual_room_ids)
    return replace(
        observation,
        rooms=tuple(
            replace(
                room,
                mode=ClimateRoomMode.MANUAL,
                authority_eligible=False,
            )
            if room.room_id in manual
            else room
            for room in observation.rooms
        ),
    )


def climate_manual_to_payload(memory: ClimateManualMemory) -> dict[str, object]:
    """Return the exact private-binding-free storage payload."""

    if not isinstance(memory, ClimateManualMemory):
        raise ClimateManualViolation("validated manual-control memory is required")
    return {
        "version": memory.version,
        "updated_at": memory.updated_at,
        "manual_room_ids": list(memory.manual_room_ids),
        "devices": [
            {
                "device_id": state.device_id,
                "room_id": state.room_id,
                "observed_phase": state.observed_phase.value,
                "observed_at": state.observed_at,
                "commanded_phase": (
                    None if state.commanded_phase is None else state.commanded_phase.value
                ),
                "commanded_at": state.commanded_at,
            }
            for state in memory.devices
        ],
    }


def climate_manual_from_payload(payload: object) -> ClimateManualMemory:
    """Parse only the exact supported manual-control storage shape."""

    root = _mapping(payload, "manual-control memory")
    _exact_keys(
        root,
        {"version", "updated_at", "manual_room_ids", "devices"},
        "manual-control memory",
    )
    if (
        type(root["version"]) is not int
        or root["version"] != CLIMATE_MANUAL_MEMORY_VERSION
    ):
        raise ClimateManualViolation(
            "stored manual-control memory version is unsupported"
        )
    room_ids = _sequence(root["manual_room_ids"], "manual room ids")
    devices = _sequence(root["devices"], "manual-control devices")
    parsed: list[ClimateDirectWifiState] = []
    for raw in devices:
        item = _mapping(raw, "manual-control device")
        _exact_keys(
            item,
            {
                "device_id",
                "room_id",
                "observed_phase",
                "observed_at",
                "commanded_phase",
                "commanded_at",
            },
            "manual-control device",
        )
        try:
            observed_phase = ClimateDirectWifiPhase(item["observed_phase"])
            commanded_phase = (
                None
                if item["commanded_phase"] is None
                else ClimateDirectWifiPhase(item["commanded_phase"])
            )
        except (TypeError, ValueError) as error:
            raise ClimateManualViolation(
                "stored manual-control phase is unsupported"
            ) from error
        parsed.append(
            ClimateDirectWifiState(
                device_id=item["device_id"],  # type: ignore[arg-type]
                room_id=item["room_id"],  # type: ignore[arg-type]
                observed_phase=observed_phase,
                observed_at=item["observed_at"],  # type: ignore[arg-type]
                commanded_phase=commanded_phase,
                commanded_at=item["commanded_at"],  # type: ignore[arg-type]
            )
        )
    return ClimateManualMemory(
        updated_at=root["updated_at"],  # type: ignore[arg-type]
        manual_room_ids=tuple(room_ids),  # type: ignore[arg-type]
        devices=tuple(parsed),
    )


def _observed_phase(observed: object) -> ClimateDirectWifiPhase | None:
    if (
        observed is None
        or getattr(observed, "availability", None)
        is not ClimateDeviceAvailability.AVAILABLE
    ):
        return None
    activity = getattr(observed, "activity", None)
    if activity in _ACTIVE_ACTIVITIES:
        return ClimateDirectWifiPhase.ACTIVE
    if activity is ClimateDeviceActivity.STOPPED:
        return ClimateDirectWifiPhase.INACTIVE
    return None


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ClimateManualViolation(f"{label} must be an object")
    return value


def _sequence(value: object, label: str) -> Sequence[object]:
    if not isinstance(value, list):
        raise ClimateManualViolation(f"{label} must be a list")
    return value


def _exact_keys(
    value: Mapping[str, object], expected: set[str], label: str
) -> None:
    if set(value) != expected:
        raise ClimateManualViolation(f"{label} fields are invalid")


def _timestamp(value: object, label: str) -> None:
    if type(value) is not int or value < 0:
        raise ClimateManualViolation(f"{label} must be a non-negative integer")
