"""Reconcile, update, serialize, and restore climate cycle protection."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from ..domain.climate import ClimateDeviceKind, ClimateRegistry
from ..domain.climate_observation import (
    ClimateDataStatus,
    ClimateDeviceActivity,
    ClimateDeviceAvailability,
    ClimateObservationDeviceKind,
    ClimateObservationSnapshot,
)
from ..domain.climate_protection import (
    CLIMATE_PROTECTION_MEMORY_VERSION,
    ClimateDeviceProtectionState,
    ClimateProtectionMemory,
    ClimateProtectionPhase,
    ClimateProtectionViolation,
    empty_climate_protection_memory,
)


_ACTIVE = frozenset(
    {
        ClimateDeviceActivity.RUNNING,
        ClimateDeviceActivity.COOLING,
    }
)
_INACTIVE = frozenset(
    {
        ClimateDeviceActivity.STOPPED,
        ClimateDeviceActivity.IDLE,
    }
)
_MAX_PROTECTED_DEVICES = 512


@dataclass(frozen=True, slots=True)
class ClimateProtectionUpdate:
    """One pure memory transition plus the enriched observation."""

    memory: ClimateProtectionMemory
    observation: ClimateObservationSnapshot
    changed: bool
    rearm_complete: bool

    def __post_init__(self) -> None:
        if not isinstance(self.memory, ClimateProtectionMemory):
            raise ClimateProtectionViolation("updated protection memory is required")
        if not isinstance(self.observation, ClimateObservationSnapshot):
            raise ClimateProtectionViolation("updated climate observation is required")
        if type(self.changed) is not bool or type(self.rearm_complete) is not bool:
            raise ClimateProtectionViolation("protection update flags must be boolean")
        if self.memory.updated_at > self.observation.observed_at:
            raise ClimateProtectionViolation(
                "protection update cannot be newer than its observation"
            )


def reconcile_climate_protection_memory(
    memory: ClimateProtectionMemory,
    registry: ClimateRegistry,
    *,
    now_ms: int,
) -> tuple[ClimateProtectionMemory, bool]:
    """Drop stale bindings and reset future-dated memory after a clock change."""

    if not isinstance(memory, ClimateProtectionMemory):
        raise ClimateProtectionViolation("validated protection memory is required")
    if not isinstance(registry, ClimateRegistry):
        raise ClimateProtectionViolation("validated climate registry is required")
    _timestamp(now_ms, "protection reconciliation time")
    if memory.updated_at > now_ms:
        return empty_climate_protection_memory(updated_at=now_ms), True
    allowed = {
        device.device_id: device
        for device in registry.devices
        if device.kind is ClimateDeviceKind.AIR_CONDITIONER
    }
    retained = tuple(
        state
        for state in memory.devices
        if (device := allowed.get(state.device_id)) is not None
        and device.room_id == state.room_id
    )
    if retained == memory.devices:
        return memory, False
    return ClimateProtectionMemory(updated_at=now_ms, devices=retained), True


def update_climate_protection(
    memory: ClimateProtectionMemory,
    registry: ClimateRegistry,
    observation: ClimateObservationSnapshot,
    *,
    restart_rearm_after: int | None,
) -> ClimateProtectionUpdate:
    """Restore transition facts and conservatively rearm after a restart."""

    if not isinstance(memory, ClimateProtectionMemory):
        raise ClimateProtectionViolation("validated protection memory is required")
    if not isinstance(registry, ClimateRegistry):
        raise ClimateProtectionViolation("validated climate registry is required")
    if not isinstance(observation, ClimateObservationSnapshot):
        raise ClimateProtectionViolation("validated climate observation is required")
    if restart_rearm_after is not None:
        _timestamp(restart_rearm_after, "restart rearm time")
        if restart_rearm_after > observation.observed_at:
            raise ClimateProtectionViolation(
                "restart rearm time cannot be newer than the observation"
            )
    if memory.updated_at > observation.observed_at:
        raise ClimateProtectionViolation(
            "protection memory cannot be newer than the observation"
        )

    tracked = tuple(
        device
        for device in registry.devices
        if device.kind is ClimateDeviceKind.AIR_CONDITIONER
    )
    states: list[ClimateDeviceProtectionState] = []
    enriched = list(observation.devices)
    for configured in tracked:
        observed = observation.device(configured.device_id)
        previous = memory.device(configured.device_id)
        if (
            observed is None
            or observed.room_id != configured.room_id
            or observed.kind is not ClimateObservationDeviceKind.AIR_CONDITIONER
            or observed.availability is not ClimateDeviceAvailability.AVAILABLE
            or observation.data_status is not ClimateDataStatus.FRESH
            or (phase := _phase(observed.activity)) is None
        ):
            if previous is not None:
                states.append(previous)
            continue
        state = _next_device_state(
            observed,
            previous,
            phase=phase,
            observed_at=observation.observed_at,
            rearm_after_restart=(
                restart_rearm_after is not None
                and (
                    previous is None
                    or previous.observed_at < restart_rearm_after
                )
            ),
        )
        states.append(state)
        enriched = [
            replace(
                device,
                last_started_at=state.last_started_at,
                last_stopped_at=state.last_stopped_at,
                confirmed_short_cycle_count=state.confirmed_short_cycle_count,
            )
            if device.device_id == state.device_id
            else device
            for device in enriched
        ]

    ordered_states = tuple(
        state
        for configured in tracked
        if (state := next(
            (item for item in states if item.device_id == configured.device_id),
            None,
        ))
        is not None
    )
    memory_changed = ordered_states != memory.devices
    updated_at = observation.observed_at if memory_changed else memory.updated_at
    updated_memory = ClimateProtectionMemory(
        updated_at=updated_at,
        devices=ordered_states,
    )
    updated_observation = replace(observation, devices=tuple(enriched))
    rearm_complete = restart_rearm_after is None or all(
        (state := updated_memory.device(configured.device_id)) is not None
        and state.observed_at >= restart_rearm_after
        for configured in tracked
    )
    return ClimateProtectionUpdate(
        memory=updated_memory,
        observation=updated_observation,
        changed=updated_memory != memory,
        rearm_complete=rearm_complete,
    )


def climate_protection_to_payload(
    memory: ClimateProtectionMemory,
) -> dict[str, object]:
    """Return the exact private-binding-free storage payload."""

    if not isinstance(memory, ClimateProtectionMemory):
        raise ClimateProtectionViolation("validated protection memory is required")
    return {
        "version": memory.version,
        "updated_at": memory.updated_at,
        "devices": [
            {
                "device_id": device.device_id,
                "room_id": device.room_id,
                "phase": device.phase.value,
                "observed_at": device.observed_at,
                "last_started_at": device.last_started_at,
                "last_stopped_at": device.last_stopped_at,
                "confirmed_short_cycle_count": device.confirmed_short_cycle_count,
            }
            for device in memory.devices
        ],
    }


def climate_protection_from_payload(payload: object) -> ClimateProtectionMemory:
    """Parse only the exact supported protection-storage shape."""

    root = _mapping(payload, "protection memory")
    _exact_keys(root, {"version", "updated_at", "devices"}, "protection memory")
    if (
        type(root["version"]) is not int
        or root["version"] != CLIMATE_PROTECTION_MEMORY_VERSION
    ):
        raise ClimateProtectionViolation(
            "stored protection memory version is unsupported"
        )
    devices = _sequence(root["devices"], "protection devices")
    parsed: list[ClimateDeviceProtectionState] = []
    for raw in devices:
        device = _mapping(raw, "protection device")
        _exact_keys(
            device,
            {
                "device_id",
                "room_id",
                "phase",
                "observed_at",
                "last_started_at",
                "last_stopped_at",
                "confirmed_short_cycle_count",
            },
            "protection device",
        )
        try:
            phase = ClimateProtectionPhase(device["phase"])
        except (TypeError, ValueError) as error:
            raise ClimateProtectionViolation(
                "stored protection phase is unsupported"
            ) from error
        parsed.append(
            ClimateDeviceProtectionState(
                device_id=device["device_id"],  # type: ignore[arg-type]
                room_id=device["room_id"],  # type: ignore[arg-type]
                phase=phase,
                observed_at=device["observed_at"],  # type: ignore[arg-type]
                last_started_at=device["last_started_at"],  # type: ignore[arg-type]
                last_stopped_at=device["last_stopped_at"],  # type: ignore[arg-type]
                confirmed_short_cycle_count=device[
                    "confirmed_short_cycle_count"
                ],  # type: ignore[arg-type]
            )
        )
    return ClimateProtectionMemory(
        updated_at=root["updated_at"],  # type: ignore[arg-type]
        devices=tuple(parsed),
        version=root["version"],  # type: ignore[arg-type]
    )


def _next_device_state(
    observed,
    previous: ClimateDeviceProtectionState | None,
    *,
    phase: ClimateProtectionPhase,
    observed_at: int,
    rearm_after_restart: bool,
) -> ClimateDeviceProtectionState:
    changed_phase = previous is None or previous.phase is not phase
    last_started_at = observed.last_started_at
    last_stopped_at = observed.last_stopped_at
    if phase is ClimateProtectionPhase.ACTIVE:
        if last_started_at is None:
            last_started_at = (
                observed_at
                if rearm_after_restart or changed_phase
                else previous.last_started_at
            )
        if last_stopped_at is None and previous is not None:
            last_stopped_at = previous.last_stopped_at
    else:
        if last_stopped_at is None:
            last_stopped_at = (
                observed_at
                if rearm_after_restart or changed_phase
                else previous.last_stopped_at
            )
        if last_started_at is None and previous is not None:
            last_started_at = previous.last_started_at
    short_cycles = (
        observed.confirmed_short_cycle_count
        if observed.confirmed_short_cycle_count is not None
        else (
            previous.confirmed_short_cycle_count if previous is not None else 0
        )
    )
    candidate = ClimateDeviceProtectionState(
        device_id=observed.device_id,
        room_id=observed.room_id,
        phase=phase,
        observed_at=observed_at,
        last_started_at=last_started_at,
        last_stopped_at=last_stopped_at,
        confirmed_short_cycle_count=short_cycles,
    )
    if (
        previous is not None
        and not rearm_after_restart
        and candidate.phase is previous.phase
        and candidate.last_started_at == previous.last_started_at
        and candidate.last_stopped_at == previous.last_stopped_at
        and candidate.confirmed_short_cycle_count
        == previous.confirmed_short_cycle_count
    ):
        return previous
    return candidate


def _phase(activity: ClimateDeviceActivity) -> ClimateProtectionPhase | None:
    if activity in _ACTIVE:
        return ClimateProtectionPhase.ACTIVE
    if activity in _INACTIVE:
        return ClimateProtectionPhase.INACTIVE
    return None


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(
        not isinstance(key, str) for key in value
    ):
        raise ClimateProtectionViolation(f"{label} must be an object")
    return value


def _sequence(value: object, label: str) -> Sequence[object]:
    if not isinstance(value, list) or len(value) > _MAX_PROTECTED_DEVICES:
        raise ClimateProtectionViolation(f"{label} must be a list")
    return value


def _exact_keys(
    value: Mapping[str, object],
    expected: set[str],
    label: str,
) -> None:
    if set(value) != expected:
        raise ClimateProtectionViolation(f"{label} fields are unsupported")


def _timestamp(value: object, label: str) -> None:
    if type(value) is not int or value < 0:
        raise ClimateProtectionViolation(f"{label} must be a non-negative integer")
