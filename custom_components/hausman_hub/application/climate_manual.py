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
    ClimateManualAttribution,
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
from ..domain.contours import ContourDefinition


_ACTIVE_ACTIVITIES = frozenset(
    {
        ClimateDeviceActivity.RUNNING,
        ClimateDeviceActivity.IDLE,
        ClimateDeviceActivity.COOLING,
        ClimateDeviceActivity.HEATING,
        ClimateDeviceActivity.HUMIDIFYING,
    }
)
DIRECT_WIFI_COMMAND_ATTRIBUTION_MS = 5 * 60 * 1000
_MANUAL_OBSERVATION_KINDS = frozenset(
    {
        ClimateDeviceKind.AIR_CONDITIONER,
        ClimateDeviceKind.HUMIDIFIER,
        ClimateDeviceKind.FLOOR_HEATING,
        ClimateDeviceKind.RADIATOR_THERMOSTAT,
    }
)


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
    observed_devices = {
        device.device_id: device
        for device in registry.devices
        if device.kind in _MANUAL_OBSERVATION_KINDS
    }
    manual_room_ids = tuple(
        room.room_id for room in registry.rooms if room.room_id in memory.manual_room_ids
    )
    manual_device_ids = tuple(
        device.device_id
        for device in registry.devices
        if device.device_id in memory.manual_device_ids
    )
    devices = tuple(
        state
        for device in registry.devices
        if (state := memory.device(device.device_id)) is not None
        and (configured := observed_devices.get(state.device_id)) is not None
        and configured.room_id == state.room_id
        and state.room_id in rooms
        and (state.commanded_at is None or state.commanded_at <= now_ms)
        and state.observed_at <= now_ms
    )
    # Attribution is an ownership fact, not a direct-Wi-Fi observation. Do
    # not silently discard a valid manual exclusion merely because the device
    # has no direct-Wi-Fi state row.
    attributions = tuple(
        item for item in memory.attributions if item.device_id in observed_devices
    )
    updated = ClimateManualMemory(
        updated_at=memory.updated_at,
        manual_room_ids=manual_room_ids,
        manual_device_ids=manual_device_ids,
        devices=devices,
        attributions=attributions,
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

    manual_devices = set(memory.manual_device_ids)
    attributions = {item.device_id: item for item in memory.attributions}
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
            manual_devices.add(configured.device_id)
            attributions[configured.device_id] = ClimateManualAttribution(
                device_id=configured.device_id, reason="external_off",
                source="observation", changed_at=observation.observed_at,
            )
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
    ordered_manual_devices = tuple(
        device.device_id
        for device in registry.devices
        if device.device_id in manual_devices
    )
    content_changed = (
        ordered_manual_devices != memory.manual_device_ids
        or tuple(states) != memory.devices
    )
    updated = ClimateManualMemory(
        updated_at=observation.observed_at if content_changed else memory.updated_at,
        manual_room_ids=memory.manual_room_ids,
        manual_device_ids=ordered_manual_devices,
        devices=tuple(states),
        attributions=tuple(
            attributions[item.device_id] for item in registry.devices
            if item.device_id in attributions and item.device_id in manual_devices
        ),
    )
    return updated, updated != memory


def update_climate_manual_observation(
    memory: ClimateManualMemory,
    registry: ClimateRegistry,
    observation: ClimateObservationSnapshot,
    *,
    external_device_ids: Sequence[str] = (),
    context_by_device: Mapping[str, Mapping[str, object]] | None = None,
) -> tuple[ClimateManualMemory, bool]:
    """Record explicitly attributed external shutdowns for every actuator kind.

    A plain state transition is deliberately not enough for non direct-Wi-Fi
    devices: Home Assistant can publish it after a Hausman command.  The
    native bridge must provide an explicit external attribution (normally from
    an HA context that did not match a recent Hausman operation) before this
    function changes ownership.  This keeps unknown observations fail-closed.
    """

    updated, changed = update_direct_wifi_observation(memory, registry, observation)
    external = frozenset(external_device_ids)
    contexts = context_by_device or {}
    manual_devices = set(updated.manual_device_ids)
    attributions = {item.device_id: item for item in updated.attributions}
    states = {item.device_id: item for item in updated.devices}
    for device in registry.devices:
        if device.kind not in _MANUAL_OBSERVATION_KINDS:
            continue
        observed = observation.device(device.device_id)
        # Compare with the state that preceded this observation.  The direct
        # Wi-Fi compatibility pass above may already have advanced its copy.
        previous = memory.device(device.device_id)
        phase = _observed_phase(observed)
        if (
            observation.data_status is not ClimateDataStatus.FRESH
            or observed is None
            or observed.room_id != device.room_id
        ):
            continue
        if phase is not None and device.control_channel is not ClimateControlChannel.DIRECT_WIFI:
            states[device.device_id] = ClimateDirectWifiState(
                device_id=device.device_id, room_id=device.room_id,
                observed_phase=phase, observed_at=observation.observed_at,
            )
        if (
            device.device_id not in external
            or previous is None
            or previous.observed_phase is not ClimateDirectWifiPhase.ACTIVE
            or phase is not ClimateDirectWifiPhase.INACTIVE
        ):
            continue
        context = contexts.get(device.device_id, {})
        context_id = context.get("context_id")
        operation_id = context.get("operation_id")
        if context_id is not None and not isinstance(context_id, str):
            raise ClimateManualViolation("manual attribution context id is invalid")
        if operation_id is not None and not isinstance(operation_id, str):
            raise ClimateManualViolation("manual attribution operation id is invalid")
        manual_devices.add(device.device_id)
        attributions[device.device_id] = ClimateManualAttribution(
            device_id=device.device_id,
            reason="external_off",
            source="ha_context" if context_id else "direct_observation",
            changed_at=observation.observed_at,
            context_id=context_id,
            operation_id=operation_id,
        )
    result = replace(
        updated,
        updated_at=(observation.observed_at if manual_devices != set(updated.manual_device_ids) else updated.updated_at),
        manual_device_ids=tuple(device.device_id for device in registry.devices if device.device_id in manual_devices),
        devices=tuple(
            states[device.device_id] for device in registry.devices
            if device.device_id in states
        ),
        attributions=tuple(
            attributions[device.device_id] for device in registry.devices
            if device.device_id in manual_devices and device.device_id in attributions
        ),
    )
    return result, result != memory


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
        manual_device_ids=memory.manual_device_ids,
        devices=states,
        # Command attribution must not erase manual ownership already
        # established for unrelated leaves.
        attributions=memory.attributions,
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
        manual_device_ids=memory.manual_device_ids,
        devices=memory.devices,
        attributions=memory.attributions,
    )


def with_climate_device_mode(
    memory: ClimateManualMemory,
    registry: ClimateRegistry,
    *,
    room_id: str,
    device_id: str,
    manual: bool,
    updated_at: int,
) -> ClimateManualMemory:
    """Persist an explicit manual/automatic choice for one climate device."""

    _timestamp(updated_at, "manual-control update time")
    device = registry.device(device_id)
    if device is None or device.room_id != room_id:
        raise ClimateManualViolation("manual-control device is not in the room")
    selected = set(memory.manual_device_ids)
    if manual:
        selected.add(device_id)
    else:
        selected.discard(device_id)
    attributions = {item.device_id: item for item in memory.attributions}
    if manual:
        attributions[device_id] = ClimateManualAttribution(
            device_id=device_id, reason="user_excluded", source="direct_observation",
            changed_at=updated_at,
        )
    else:
        attributions.pop(device_id, None)
    return ClimateManualMemory(
        updated_at=max(memory.updated_at, updated_at),
        manual_room_ids=memory.manual_room_ids,
        manual_device_ids=tuple(
            item.device_id for item in registry.devices if item.device_id in selected
        ),
        devices=memory.devices,
        attributions=tuple(
            attributions[item.device_id] for item in registry.devices
            if item.device_id in attributions and item.device_id in selected
        ),
    )


def effective_manual_room_ids(
    memory: ClimateManualMemory,
    registry: ClimateRegistry,
) -> tuple[str, ...]:
    """Return explicit rooms plus rooms made unsafe by excluded primary sensors."""

    manual = set(memory.manual_room_ids)
    critical_kinds = {
        ClimateDeviceKind.TEMPERATURE_SENSOR,
        ClimateDeviceKind.HUMIDITY_SENSOR,
    }
    manual.update(
        device.room_id
        for device in registry.devices
        if device.device_id in memory.manual_device_ids
        and device.kind in critical_kinds
    )
    return tuple(room.room_id for room in registry.rooms if room.room_id in manual)


def contour_without_manual_devices(
    contour: ContourDefinition,
    memory: ClimateManualMemory,
) -> ContourDefinition:
    """Remove explicitly excluded devices from one immutable execution contour."""

    excluded = set(memory.manual_device_ids)
    if not excluded:
        return contour
    return replace(
        contour,
        rooms=tuple(
            replace(
                room,
                device_ids=tuple(
                    device_id for device_id in room.device_ids if device_id not in excluded
                ),
            )
            for room in contour.rooms
        ),
    )


def apply_manual_rooms(
    observation: ClimateObservationSnapshot,
    memory: ClimateManualMemory,
    registry: ClimateRegistry | None = None,
) -> ClimateObservationSnapshot:
    """Overlay durable manual ownership without changing physical facts."""

    manual = set(
        memory.manual_room_ids
        if registry is None
        else effective_manual_room_ids(memory, registry)
    )
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
        "manual_device_ids": list(memory.manual_device_ids),
        "attributions": [
            {"device_id": item.device_id, "reason": item.reason,
             "source": item.source, "changed_at": item.changed_at,
             "context_id": item.context_id, "operation_id": item.operation_id}
            for item in memory.attributions
        ],
        "hausman_context_ids": list(memory.hausman_context_ids),
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
    version = root.get("version")
    expected_keys = {"version", "updated_at", "manual_room_ids", "devices"}
    if version in {3, CLIMATE_MANUAL_MEMORY_VERSION}:
        expected_keys.update({"manual_device_ids", "attributions"})
    if version == CLIMATE_MANUAL_MEMORY_VERSION:
        expected_keys.add("hausman_context_ids")
    _exact_keys(root, expected_keys, "manual-control memory")
    if type(version) is not int or version not in {1, 2, 3, CLIMATE_MANUAL_MEMORY_VERSION}:
        raise ClimateManualViolation(
            "stored manual-control memory version is unsupported"
        )
    room_ids = _sequence(root["manual_room_ids"], "manual room ids")
    device_ids = _sequence(root.get("manual_device_ids", ()), "manual device ids")
    devices = _sequence(root["devices"], "manual-control devices")
    attribution_raw = _sequence(root.get("attributions", []), "manual-control attributions")
    attributions: list[ClimateManualAttribution] = []
    for raw in attribution_raw:
        item = _mapping(raw, "manual-control attribution")
        _exact_keys(item, {"device_id", "reason", "source", "changed_at", "context_id", "operation_id"}, "manual-control attribution")
        attributions.append(ClimateManualAttribution(
            device_id=item["device_id"], reason=item["reason"], source=item["source"],
            changed_at=item["changed_at"], context_id=item["context_id"], operation_id=item["operation_id"],
        ))
    context_ids = _sequence(root.get("hausman_context_ids", []), "Hausman context provenance")
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
        manual_device_ids=tuple(device_ids),  # type: ignore[arg-type]
        devices=tuple(parsed),
        attributions=tuple(attributions),
        hausman_context_ids=tuple(context_ids),
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
