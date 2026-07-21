"""Native Home Assistant climate projections with legacy payload parity.

Roadmap item 36 sub-steps 36e1/36e2. These pure builders produce the exact
external payload shapes previously derived from the external Climate API
bridge, but read only the native Home Assistant observation and the version-2
registry. The managed runtime serves all five projections through this module;
the shadow and canary modes deliberately keep the bridge-backed legacy paths
until their migration tooling is retired (36g).

Unknown or missing native facts always project to the same fail-closed values
the legacy builders produced for missing bridge facts: ``None``, ``unknown``,
``unavailable``, ``False`` or an empty list, never an optimistic value.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime

from ..domain.climate import (
    ClimateCapability,
    ClimateControlOwner,
    ClimateControlScope,
    ClimateDevice,
    ClimateDeviceKind,
    ClimateRegistry,
)
from ..domain.climate_bridge import ClimateBridgeMode
from ..domain.climate_observation import (
    ClimateDataStatus,
    ClimateDeviceActivity,
    ClimateDeviceAvailability,
    ClimateDeviceObservation,
    ClimateObservationSnapshot,
    ClimateRoomObservation,
)
from ..domain.contours import (
    CLIMATE_TARGET_TEMPERATURE_MAXIMUM,
    CLIMATE_TARGET_TEMPERATURE_MINIMUM,
    CLIMATE_TARGET_TEMPERATURE_STEP,
    ClimateContourRoom,
    ClimateProfile,
    ContourDefinition,
    ContourMode,
    ContourRegistry,
)
from .android_climate_values import (
    ANDROID_CLIMATE_CONTRACT_NAME,
    ANDROID_CLIMATE_CONTRACT_VERSION,
    ANDROID_ROOM_CONTROL_ACTIONS,
    ROOM_ACTION_COMMAND_TYPES,
    public_state_revision,
    room_action_availability,
    room_action_inputs,
    room_action_presentations,
    saved_profiles_by_room,
)
from .climate_application import build_climate_application_plan
from .climate_application_models import ClimateDesiredStateChanges
from .climate_import import SUPPORTED_BACKEND_COMMAND_TYPES
from .climate_registry import ClimateRegistryReconciliation
from .contour_apply import (
    CONTOUR_APPLY_CONTRACT_VERSION,
    CONTOUR_APPLY_PREVIEW_CONTRACT_NAME,
    ContourApplyViolation,
)
from .contours import (
    CONTOUR_CONTRACT_NAME,
    CONTOUR_CONTRACT_VERSION,
    _public_comfort_payload,
    _public_schedule,
    validate_contour_bindings,
)
from .public_climate_values import (
    public_climate_display_names,
    public_device_state,
    public_room_data_status,
    public_room_mode,
    public_strategy,
)

_COMMANDS_BY_KIND_CAPABILITY: dict[
    ClimateDeviceKind, dict[ClimateCapability, tuple[str, ...]]
] = {
    ClimateDeviceKind.AIR_CONDITIONER: {
        ClimateCapability.POWER: ("climate.turn_off",),
        ClimateCapability.HVAC_MODE: ("climate.set_hvac_mode",),
        ClimateCapability.TARGET_TEMPERATURE: ("climate.set_temperature",),
        ClimateCapability.FAN_MODE: ("climate.set_fan_mode",),
    },
    ClimateDeviceKind.RADIATOR_THERMOSTAT: {
        ClimateCapability.TARGET_TEMPERATURE: ("trv.set_temperature",),
    },
    ClimateDeviceKind.HUMIDIFIER: {
        ClimateCapability.POWER: ("humidifier.turn_on", "humidifier.turn_off"),
        ClimateCapability.TARGET_HUMIDITY: ("humidifier.set_humidity",),
    },
    ClimateDeviceKind.FLOOR_HEATING: {
        ClimateCapability.POWER: ("switch.turn_on", "switch.turn_off"),
        ClimateCapability.TARGET_TEMPERATURE: ("climate.set_temperature",),
    },
}

_WORKING_ACTIVITIES = frozenset(
    {
        ClimateDeviceActivity.RUNNING,
        ClimateDeviceActivity.COOLING,
        ClimateDeviceActivity.HEATING,
        ClimateDeviceActivity.HUMIDIFYING,
    }
)


def native_runtime_fresh(observation: ClimateObservationSnapshot) -> bool:
    """Return whether the native observation is fresh enough to act upon."""

    return observation.data_status is ClimateDataStatus.FRESH


def native_climate_reconciliation(
    registry: ClimateRegistry,
    observation: ClimateObservationSnapshot,
) -> ClimateRegistryReconciliation:
    """Compare registered devices against native observations by stable id."""

    matched: list[str] = []
    missing: list[str] = []
    room_mismatch: list[str] = []
    for device in registry.devices:
        observed = observation.device(device.device_id)
        if observed is None:
            missing.append(device.device_id)
        elif observed.room_id != device.room_id:
            room_mismatch.append(device.device_id)
        else:
            matched.append(device.device_id)
    return ClimateRegistryReconciliation(
        matched_device_ids=tuple(sorted(matched)),
        missing_device_ids=tuple(sorted(missing)),
        room_mismatch_device_ids=tuple(sorted(room_mismatch)),
        unregistered_source_ids=(),
    )


def native_device_command_types(device: ClimateDevice) -> tuple[str, ...]:
    """Derive supported command types from the validated registry binding."""

    mapping = _COMMANDS_BY_KIND_CAPABILITY.get(device.kind, {})
    commands: list[str] = []
    for capability in device.capabilities:
        for command in mapping.get(capability, ()):
            if command in SUPPORTED_BACKEND_COMMAND_TYPES and command not in commands:
                commands.append(command)
    return tuple(commands)


def _native_device_available(
    device: ClimateDevice,
    observed: ClimateDeviceObservation | None,
) -> bool:
    return (
        observed is not None
        and observed.room_id == device.room_id
        and observed.availability is ClimateDeviceAvailability.AVAILABLE
    )


_PASSIVE_KINDS = frozenset(
    {
        ClimateDeviceKind.TEMPERATURE_SENSOR,
        ClimateDeviceKind.HUMIDITY_SENSOR,
    }
)


def _legacy_device_state(
    device: ClimateDevice,
    observed: ClimateDeviceObservation | None,
) -> str | None:
    """Map native activity onto the legacy state vocabulary for parity."""

    if observed is None or device.kind in _PASSIVE_KINDS:
        # A passive sensor's bridge state was its numeric reading, which the
        # public vocabulary always normalized to "unknown".
        return None
    if observed.activity in _WORKING_ACTIVITIES:
        return "on"
    if observed.activity is ClimateDeviceActivity.IDLE:
        return "idle"
    if observed.activity is ClimateDeviceActivity.STOPPED:
        return "off"
    return None


def _legacy_room_mode(observed: ClimateRoomObservation | None) -> str | None:
    """Return the legacy room mode string; native values already match it."""

    return None if observed is None else observed.mode.value


def native_contour_snapshot(
    contours: ContourRegistry,
    climate_registry: ClimateRegistry,
    observation: ClimateObservationSnapshot | None,
    *,
    settings_apply_enabled: bool = False,
    local_now: datetime | None = None,
) -> dict[str, object]:
    """Project public contour status from the native observation."""

    validate_contour_bindings(contours, climate_registry)
    projection_time = local_now or datetime.now().astimezone()
    return {
        "contract": {
            "name": CONTOUR_CONTRACT_NAME,
            "version": CONTOUR_CONTRACT_VERSION,
        },
        "display_names": public_climate_display_names(
            include_room_data_statuses=False,
        ),
        "contours": [
            _native_contour_status(
                contour,
                climate_registry,
                observation,
                settings_apply_enabled=settings_apply_enabled,
                local_now=projection_time,
            )
            for contour in contours.contours
        ],
    }


def _native_contour_status(
    contour: ContourDefinition,
    climate_registry: ClimateRegistry,
    observation: ClimateObservationSnapshot | None,
    *,
    settings_apply_enabled: bool,
    local_now: datetime,
) -> dict[str, object]:
    schedule = _public_schedule(contour.schedule, local_now)
    schedule_profile = contour.schedule.last_applied_profile
    schedule_ready = (
        contour.mode is ContourMode.AUTOMATIC
        and contour.schedule.enabled
        and schedule_profile is not None
        and all(room.active_profile is schedule_profile for room in contour.rooms)
    )
    fresh = observation is not None and native_runtime_fresh(observation)
    temporary_temperature_available_by_room = {
        room.room_id: (
            schedule_ready
            and settings_apply_enabled
            and fresh
            and observation is not None
            and _native_room_settings_apply_available(
                room, climate_registry, observation
            )
        )
        for room in contour.rooms
    }
    room_results = [
        _native_room_status(
            room,
            climate_registry,
            observation,
            temporary_temperature_available=(
                temporary_temperature_available_by_room[room.room_id]
            ),
            next_schedule_change_at=schedule["next_change_at"],
        )
        for room in contour.rooms
    ]
    reasons = list(
        dict.fromkeys(
            reason
            for room in room_results
            for reason in room["reasons"]  # type: ignore[index]
            if isinstance(reason, str)
        )
    )
    automatic_active = bool(room_results) and all(
        room["automatic_active"] is True for room in room_results
    )
    if contour.mode is ContourMode.DISABLED:
        status = "disabled"
        automatic_active = False
    elif observation is None:
        status = "unavailable"
    elif not native_runtime_fresh(observation):
        status = "stale"
    elif contour.mode is ContourMode.AUTOMATIC and not automatic_active:
        status = "attention"
    elif any(room["status"] != "ready" for room in room_results):
        status = "attention"
    else:
        status = "ready"
    settings_apply_available = (
        settings_apply_enabled
        and contour.mode is ContourMode.AUTOMATIC
        and observation is not None
        and native_runtime_fresh(observation)
        and all(
            _native_room_settings_apply_available(room, climate_registry, observation)
            for room in contour.rooms
        )
    )
    return {
        "id": contour.contour_id,
        "name": contour.name,
        "kind": contour.kind.value,
        "mode": contour.mode.value,
        "status": status,
        "engine": {
            "name": "hausman-climate",
            "version": 1,
        },
        "schedule": schedule,
        "rooms": room_results,
        "execution": {
            "owner": contour.engine.value,
            "automatic_active": automatic_active,
            "hausmanhub_direct_commands": False,
            "settings_apply": {
                "available": settings_apply_available,
                "requires_confirmation": True,
                "parameters": {
                    "temperature": True,
                    "strategy": True,
                    "automatic_mode": True,
                    "humidity": False,
                },
            },
            "temporary_temperature": {
                "available": any(temporary_temperature_available_by_room.values()),
                "requires_confirmation": True,
                "minimum": CLIMATE_TARGET_TEMPERATURE_MINIMUM,
                "maximum": CLIMATE_TARGET_TEMPERATURE_MAXIMUM,
                "step": CLIMATE_TARGET_TEMPERATURE_STEP,
            },
        },
        "reasons": reasons,
    }


def _native_room_status(
    assignment: ClimateContourRoom,
    climate_registry: ClimateRegistry,
    observation: ClimateObservationSnapshot | None,
    *,
    temporary_temperature_available: bool,
    next_schedule_change_at: object,
) -> dict[str, object]:
    room = climate_registry.room(assignment.room_id)
    observed_room = (
        None if observation is None else observation.room(assignment.room_id)
    )
    observed_devices = [
        (
            device,
            None if observation is None else observation.device(device.device_id),
        )
        for device in (
            climate_registry.device(device_id) for device_id in assignment.device_ids
        )
        if device is not None
    ]
    fresh = observation is not None and native_runtime_fresh(observation)
    reasons: list[str] = []
    if observed_room is None:
        reasons.append("room_state_unavailable")
    if observation is not None and not native_runtime_fresh(observation):
        reasons.append("state_stale")
    if len(observed_devices) != len(assignment.device_ids) or any(
        not _native_device_available(device, observed)
        for device, observed in observed_devices
    ):
        reasons.append("device_unavailable")
    engine_automatic = observed_room is not None and observed_room.mode.value in {
        "auto",
        "forced_auto_only",
    }
    if observed_room is not None and not engine_automatic:
        reasons.append("engine_not_automatic")
    authority_ready = observed_room is not None and observed_room.authority_eligible
    if observed_room is not None and not authority_ready:
        reasons.append("authority_not_ready")
    temperature_matches = _same_number(
        None if observed_room is None else observed_room.observed_target_temperature,
        assignment.target_temperature,
    )
    humidity_matches = _optional_same_number(
        None if observed_room is None else observed_room.observed_target_humidity,
        assignment.target_humidity,
    )
    strategy_matches = (
        observed_room is not None
        and observed_room.observed_target_strategy is not None
        and observed_room.observed_target_strategy == assignment.strategy.value
    )
    if observed_room is not None and not temperature_matches:
        reasons.append("target_temperature_differs")
    if observed_room is not None and humidity_matches is False:
        reasons.append("target_humidity_differs")
    if observed_room is not None and observed_room.observed_target_strategy is None:
        reasons.append("target_strategy_unavailable")
    elif observed_room is not None and not strategy_matches:
        reasons.append("target_strategy_differs")
    available = (
        observed_room is not None
        and fresh
        and not any(reason in {"device_unavailable"} for reason in reasons)
    )
    targets_in_sync = (
        temperature_matches
        and (humidity_matches is True or humidity_matches is None)
        and strategy_matches
    )
    return {
        "id": assignment.room_id,
        "name": None if room is None else room.name,
        "status": "ready" if available else "unavailable",
        "current": {
            "temperature": (
                None if observed_room is None else observed_room.temperature
            ),
            "humidity": None if observed_room is None else observed_room.humidity,
        },
        "targets": {
            "temperature": assignment.target_temperature,
            "humidity": assignment.target_humidity,
            "strategy": assignment.strategy.value,
        },
        "comfort_profiles": {
            "active": assignment.active_profile.value,
            ClimateProfile.DAY.value: _public_comfort_payload(
                assignment.day_profile
            ),
            ClimateProfile.NIGHT.value: _public_comfort_payload(
                assignment.night_profile
            ),
        },
        "temporary_temperature": {
            "active": assignment.temporary_override is not None,
            "temperature": (
                None
                if assignment.temporary_override is None
                else assignment.temporary_override.target_temperature
            ),
            "ends": (
                None
                if assignment.temporary_override is None
                else "next_schedule_change"
            ),
            "ends_at": (
                None
                if assignment.temporary_override is None
                else next_schedule_change_at
            ),
            "available": temporary_temperature_available,
        },
        "device_count": len(assignment.device_ids),
        "engine_mode": public_room_mode(_legacy_room_mode(observed_room)),
        "engine_strategy": public_strategy(
            None if observed_room is None else observed_room.observed_target_strategy
        ),
        "authority_ready": authority_ready,
        "targets_in_sync": targets_in_sync,
        "automatic_active": (
            engine_automatic and authority_ready and available and targets_in_sync
        ),
        "reasons": reasons,
    }


def _native_room_settings_apply_available(
    assignment: ClimateContourRoom,
    climate_registry: ClimateRegistry,
    observation: ClimateObservationSnapshot,
) -> bool:
    """Mirror the typed room-command capability gate on native evidence."""

    observed_room = observation.room(assignment.room_id)
    if observed_room is None or not observed_room.authority_eligible:
        return False
    controlled_air_conditioners = []
    for device_id in assignment.device_ids:
        device = climate_registry.device(device_id)
        observed = None if device is None else observation.device(device.device_id)
        if (
            device is None
            or not _native_device_available(device, observed)
        ):
            return False
        if (
            device.kind is ClimateDeviceKind.AIR_CONDITIONER
            and device.control_owner is ClimateControlOwner.CLIMATE_CORE
            and device.control_scope is ClimateControlScope.MANAGED
            and "climate.set_temperature" in native_device_command_types(device)
        ):
            controlled_air_conditioners.append(device)
    return len(controlled_air_conditioners) == 1


def native_contour_apply_preview(
    contour: ContourDefinition,
    registry: ClimateRegistry,
    bridge_mode: ClimateBridgeMode,
    observation: ClimateObservationSnapshot,
    *,
    fingerprint: str,
) -> dict[str, object]:
    """Preview saved-contour changes against the native observation."""

    temperature_changes = 0
    strategy_changes = 0
    automatic_mode_changes = 0
    for room in contour.rooms:
        observed = observation.room(room.room_id)
        if observed is None:
            raise ContourApplyViolation("climate room state is unavailable")
        temperature_changes += (
            observed.observed_target_temperature != room.target_temperature
        )
        strategy_changes += observed.observed_target_strategy != room.strategy.value
        automatic_mode_changes += observed.mode.value != "auto"
    plan = build_climate_application_plan(
        contour,
        registry,
        bridge_mode,
        observation,
        fingerprint=fingerprint,
        target_room_ids=tuple(room.room_id for room in contour.rooms),
        desired_state_changes=ClimateDesiredStateChanges(0, 0, 0),
    )
    if plan.denial_reasons:
        raise ContourApplyViolation("climate contour is not ready to apply")
    command_count = len(plan.strict_calls)
    return {
        "contract": {
            "name": CONTOUR_APPLY_PREVIEW_CONTRACT_NAME,
            "version": CONTOUR_APPLY_CONTRACT_VERSION,
        },
        "contour_id": contour.contour_id,
        "status": "in_sync" if not command_count else "ready",
        "ready": True,
        "room_count": len(contour.rooms),
        "command_count": command_count,
        "changes": {
            "temperature": temperature_changes,
            "strategy": strategy_changes,
            "automatic_mode": automatic_mode_changes,
        },
        "requires_confirmation": True,
        "parameters": {
            "temperature": True,
            "strategy": True,
            "automatic_mode": True,
            "humidity": False,
        },
        "limitations": ["room_humidity_command_not_supported"],
    }


def native_climate_readiness(
    registry: ClimateRegistry,
    observation: ClimateObservationSnapshot | None,
    *,
    bridge_mode: ClimateBridgeMode,
) -> dict[str, object]:
    """Return redacted registry and observation readiness to a local admin."""

    if bridge_mode is ClimateBridgeMode.DISABLED:
        return _native_readiness_payload(
            registry,
            bridge_mode=bridge_mode,
            status="disabled",
            fresh=False,
            reconciliation=None,
            reasons=("bridge_disabled",),
        )
    if observation is None:
        return _native_readiness_payload(
            registry,
            bridge_mode=bridge_mode,
            status="unavailable",
            fresh=False,
            reconciliation=None,
            reasons=("climate_state_unavailable",),
        )
    reconciliation = native_climate_reconciliation(registry, observation)
    fresh = native_runtime_fresh(observation)
    reasons = _native_readiness_reasons(
        registry,
        observation,
        fresh=fresh,
        matches=reconciliation.matches,
    )
    return _native_readiness_payload(
        registry,
        bridge_mode=bridge_mode,
        status="ready" if not reasons else "not_ready",
        fresh=fresh,
        reconciliation=reconciliation,
        reasons=reasons,
    )


def _native_readiness_reasons(
    registry: ClimateRegistry,
    observation: ClimateObservationSnapshot,
    *,
    fresh: bool,
    matches: bool,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if not fresh:
        reasons.append("state_stale")
    if not registry.rooms:
        reasons.append("registry_has_no_rooms")
    if not registry.devices:
        reasons.append("registry_has_no_devices")
    if not matches or any(
        observation.room(room.room_id) is None for room in registry.rooms
    ):
        reasons.append("registry_mismatch")
    if any(
        not _native_device_available(device, observation.device(device.device_id))
        for device in registry.devices
    ):
        reasons.append("device_unavailable")
    return tuple(reasons)


def _native_readiness_payload(
    registry: ClimateRegistry,
    *,
    bridge_mode: ClimateBridgeMode,
    status: str,
    fresh: bool,
    reconciliation: ClimateRegistryReconciliation | None,
    reasons: tuple[str, ...],
) -> dict[str, object]:
    return {
        "contract": {
            "name": "hausman-hub-climate-readiness",
            "version": 1,
        },
        "bridge_mode": bridge_mode.value,
        "status": status,
        "ready": status == "ready",
        "fresh": fresh,
        "registry": {
            "room_count": len(registry.rooms),
            "device_count": len(registry.devices),
        },
        "reconciliation": _native_reconciliation_counts(reconciliation),
        "reasons": list(reasons),
    }


def _native_reconciliation_counts(
    reconciliation: ClimateRegistryReconciliation | None,
) -> dict[str, object] | None:
    if reconciliation is None:
        return None
    return {
        "matches": reconciliation.matches,
        "matched_device_count": len(reconciliation.matched_device_ids),
        "missing_device_count": len(reconciliation.missing_device_ids),
        "room_mismatch_device_count": len(reconciliation.room_mismatch_device_ids),
        "unregistered_source_count": len(reconciliation.unregistered_source_ids),
    }


def native_admin_climate_import_snapshot(
    registry: ClimateRegistry,
    observation: ClimateObservationSnapshot,
) -> dict[str, object]:
    """Build the local-administrator payload from the native observation."""

    reconciliation = native_climate_reconciliation(registry, observation)
    return {
        "generated_at": observation.observed_at,
        "fresh": native_runtime_fresh(observation),
        "rooms": [
            {
                "id": room.room_id,
                "name": room.name,
                "authority_eligible": room.authority_eligible,
            }
            for room in observation.rooms
        ],
        "candidates": [
            {
                "source_id": device.source_id,
                "name": device.name,
                "room_id": device.room_id,
                "available": _native_device_available(
                    device, observation.device(device.device_id)
                ),
                "command_types": list(native_device_command_types(device)),
                "suggested_kinds": [device.kind.value],
            }
            for device in registry.devices
        ],
        "reconciliation": {
            "matches": reconciliation.matches,
            "matched_device_ids": list(reconciliation.matched_device_ids),
            "missing_device_ids": list(reconciliation.missing_device_ids),
            "room_mismatch_device_ids": list(
                reconciliation.room_mismatch_device_ids
            ),
            "unregistered_source_ids": list(
                reconciliation.unregistered_source_ids
            ),
        },
    }


def native_android_climate_snapshot(
    registry: ClimateRegistry,
    observation: ClimateObservationSnapshot,
    *,
    contours: ContourRegistry | None = None,
    bridge_mode: ClimateBridgeMode,
    canary_room_id: str | None = None,
    candidate_ready: bool = False,
    pending_room_ids: Collection[str] = (),
    local_now: datetime | None = None,
) -> dict[str, object]:
    """Build the fixed tablet contract from the native observation only."""

    if not isinstance(bridge_mode, ClimateBridgeMode):
        raise ValueError("climate bridge mode must be approved")
    pending = frozenset(pending_room_ids)
    if any(registry.room(room_id) is None for room_id in pending):
        raise ValueError("pending climate rooms must be registered")
    reconciliation = native_climate_reconciliation(registry, observation)
    fresh = native_runtime_fresh(observation)
    devices_by_room: dict[str, list[dict[str, object]]] = {
        room.room_id: [] for room in registry.rooms
    }
    for device in registry.devices:
        observed = observation.device(device.device_id)
        available = _native_device_available(device, observed)
        devices_by_room[device.room_id].append(
            {
                "id": device.device_id,
                "name": device.name,
                "kind": device.kind.value,
                "control_scope": device.control_scope.value,
                "capabilities": [value.value for value in device.capabilities],
                "available": available,
                "state": public_device_state(
                    _legacy_device_state(device, observed),
                    available=available,
                ),
            }
        )

    public_contours = native_contour_snapshot(
        contours or ContourRegistry(),
        registry,
        observation,
        settings_apply_enabled=(bridge_mode is ClimateBridgeMode.MANAGED),
        local_now=local_now,
    )["contours"]
    room_saved_profiles = saved_profiles_by_room(public_contours)

    rooms: list[dict[str, object]] = []
    room_control_enabled = False
    for room in registry.rooms:
        observed_room = observation.room(room.room_id)
        public_mode = public_room_mode(_legacy_room_mode(observed_room))
        control = _native_room_control_projection(
            registry,
            observation,
            bridge_mode=bridge_mode,
            canary_room_id=canary_room_id,
            candidate_ready=candidate_ready,
            pending=room.room_id in pending,
            room_id=room.room_id,
        )
        room_control_enabled = room_control_enabled or control["enabled"] is True
        rooms.append(
            {
                "id": room.room_id,
                "name": room.name,
                "temperature": (
                    observed_room.temperature if observed_room else None
                ),
                "humidity": observed_room.humidity if observed_room else None,
                "target_temperature": (
                    observed_room.observed_target_temperature
                    if observed_room
                    else None
                ),
                "target_humidity": (
                    observed_room.observed_target_humidity if observed_room else None
                ),
                "mode": public_mode,
                "active_target": {
                    "temperature": (
                        observed_room.observed_target_temperature
                        if observed_room
                        else None
                    ),
                    "humidity": (
                        observed_room.observed_target_humidity
                        if observed_room
                        else None
                    ),
                    "strategy": public_strategy(
                        None
                        if observed_room is None
                        else observed_room.observed_target_strategy
                    ),
                },
                "saved_profiles": room_saved_profiles.get(
                    room.room_id,
                    {
                        "active": None,
                        "day": None,
                        "night": None,
                    },
                ),
                "actual": {
                    "data_status": public_room_data_status(
                        present=observed_room is not None,
                        fresh=fresh,
                    ),
                    "temperature": (
                        observed_room.temperature if observed_room else None
                    ),
                    "humidity": observed_room.humidity if observed_room else None,
                    "mode": public_mode,
                },
                "authority_eligible": bool(
                    observed_room is not None and observed_room.authority_eligible
                ),
                "control": control,
                "devices": devices_by_room[room.room_id],
            }
        )

    result: dict[str, object] = {
        "contract": {
            "name": ANDROID_CLIMATE_CONTRACT_NAME,
            "version": ANDROID_CLIMATE_CONTRACT_VERSION,
        },
        "generated_at": observation.observed_at,
        "climate": {
            "fresh": fresh,
            "commands_enabled": room_control_enabled,
        },
        "display_names": public_climate_display_names(),
        "rooms": rooms,
        "contours": public_contours,
        "reconciliation": {
            "matches": reconciliation.matches,
            "matched_device_ids": list(reconciliation.matched_device_ids),
            "missing_device_ids": list(reconciliation.missing_device_ids),
            "room_mismatch_device_ids": list(
                reconciliation.room_mismatch_device_ids
            ),
            "unregistered_device_count": len(
                reconciliation.unregistered_source_ids
            ),
        },
    }
    result["state_revision"] = public_state_revision(result)
    return result


def _native_room_control_projection(
    registry: ClimateRegistry,
    observation: ClimateObservationSnapshot,
    *,
    bridge_mode: ClimateBridgeMode,
    canary_room_id: str | None,
    candidate_ready: bool,
    pending: bool,
    room_id: str,
) -> dict[str, object]:
    """Project only coarse gates used to enable the first room controls."""

    reasons: list[str] = []
    if bridge_mode is ClimateBridgeMode.DISABLED:
        reasons.append("bridge_disabled")
    elif bridge_mode is ClimateBridgeMode.SHADOW:
        reasons.append("shadow_only")
    elif room_id != canary_room_id:
        reasons.append("room_not_selected")

    if not native_runtime_fresh(observation):
        reasons.append("state_stale")
    observed_room = observation.room(room_id)
    if observed_room is None:
        reasons.append("registry_mismatch")
    elif not observed_room.authority_eligible:
        reasons.append("authority_not_ready")

    controlled = [
        device
        for device in registry.devices
        if device.room_id == room_id
        and device.kind is ClimateDeviceKind.AIR_CONDITIONER
        and device.control_owner is ClimateControlOwner.CLIMATE_CORE
        and device.control_scope is not ClimateControlScope.OBSERVED
    ]
    actions: list[str] = []
    if len(controlled) != 1:
        reasons.append("actions_unsupported")
    else:
        observed_device = observation.device(controlled[0].device_id)
        if observed_device is None or observed_device.room_id != room_id:
            reasons.append("registry_mismatch")
        else:
            command_types = native_device_command_types(controlled[0])
            actions = [
                action
                for action in ANDROID_ROOM_CONTROL_ACTIONS
                if ROOM_ACTION_COMMAND_TYPES[action] in command_types
            ]
            if observed_device.availability is not ClimateDeviceAvailability.AVAILABLE:
                reasons.append("device_unavailable")
            if tuple(actions) != ANDROID_ROOM_CONTROL_ACTIONS:
                reasons.append("actions_unsupported")

    if (
        bridge_mode is ClimateBridgeMode.CANARY
        and room_id == canary_room_id
        and not candidate_ready
    ):
        reasons.append("evidence_not_ready")
    if pending:
        reasons.append("operation_pending")

    blocked_reasons = list(dict.fromkeys(reasons))
    allowed_actions = list(actions) if not blocked_reasons else []
    return {
        "enabled": bool(allowed_actions),
        "actions": actions,
        "allowed_actions": allowed_actions,
        "action_availability": room_action_availability(
            actions,
            allowed_actions,
            blocked_reasons,
        ),
        "action_inputs": room_action_inputs(actions),
        "action_presentations": room_action_presentations(actions),
        "blocked_reasons": blocked_reasons,
    }


def _same_number(left: object, right: object) -> bool:
    return (
        not isinstance(left, bool)
        and isinstance(left, (int, float))
        and not isinstance(right, bool)
        and isinstance(right, (int, float))
        and abs(float(left) - float(right)) < 0.01
    )


def _optional_same_number(left: object, right: object) -> bool | None:
    if left is None:
        return None
    return _same_number(left, right)
