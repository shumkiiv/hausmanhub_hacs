"""Plan and record one-room ownership promotion into HausmanHub management."""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..domain.climate import (
    ClimateControlScope,
    ClimateDeviceKind,
    ClimateEndpointRole,
    ClimateRegistry,
)
from ..domain.climate_bridge import ClimateBridgeMode
from ..domain.climate_comparison import (
    ClimateComparisonReason,
    ClimateComparisonSnapshot,
    ClimateComparisonStatus,
)
from ..domain.climate_isolation import ClimateIsolationSnapshot, ClimateRoomIsolationStatus
from ..domain.climate_ownership import (
    ClimateOwnershipReason,
    ClimateOwnershipReceipt,
    ClimateOwnershipStatus,
    ClimateOwnershipViolation,
)
from ..domain.contours import ContourDefinition, ContourMode


_PASSIVE_KINDS = frozenset(
    {
        ClimateDeviceKind.TEMPERATURE_SENSOR,
        ClimateDeviceKind.HUMIDITY_SENSOR,
    }
)


@dataclass(frozen=True, slots=True)
class ClimateOwnershipDecision:
    """One gated promotion: a new registry or bounded deny reasons."""

    room_id: str
    registry: ClimateRegistry | None
    reasons: tuple[ClimateOwnershipReason, ...]
    device_count: int


def plan_room_promotion(
    room_id: str,
    *,
    bridge_mode: ClimateBridgeMode,
    contour: ContourDefinition,
    isolation: ClimateIsolationSnapshot,
    comparison: ClimateComparisonSnapshot,
    registry: ClimateRegistry,
) -> ClimateOwnershipDecision:
    """Evaluate every gate for promoting one verified room to management."""

    if not isinstance(room_id, str) or not room_id:
        raise ClimateOwnershipViolation("ownership room id is required")
    for value, model in (
        (contour, ContourDefinition),
        (isolation, ClimateIsolationSnapshot),
        (comparison, ClimateComparisonSnapshot),
        (registry, ClimateRegistry),
    ):
        if not isinstance(value, model):
            raise ClimateOwnershipViolation("ownership requires validated inputs")
    if comparison.observed_at != isolation.observed_at:
        raise ClimateOwnershipViolation("ownership inputs must share one observation")
    contour_room = next(
        (room for room in contour.rooms if room.room_id == room_id),
        None,
    )
    if contour_room is None:
        return _deny(room_id, ClimateOwnershipReason.ROOM_UNKNOWN, 0)
    registered = tuple(
        device
        for device in registry.devices
        if device.device_id in set(contour_room.device_ids)
    )
    actuators = tuple(
        device for device in registered if device.kind not in _PASSIVE_KINDS
    )
    device_count = len(actuators)
    if bridge_mode not in {ClimateBridgeMode.CANARY, ClimateBridgeMode.MANAGED}:
        return _deny(room_id, ClimateOwnershipReason.MODE_BLOCKED, device_count)
    if contour.mode is not ContourMode.AUTOMATIC:
        return _deny(
            room_id,
            ClimateOwnershipReason.CONTOUR_NOT_AUTOMATIC,
            device_count,
        )
    compared = comparison.room(room_id)
    if compared is not None and (
        ClimateComparisonReason.OBSERVATION_STALE in compared.reasons
    ):
        return _deny(room_id, ClimateOwnershipReason.OBSERVATION_STALE, device_count)
    isolated = isolation.room(room_id)
    if isolated is None or isolated.status is not ClimateRoomIsolationStatus.READY:
        return _deny(room_id, ClimateOwnershipReason.ROOM_NOT_READY, device_count)
    if compared is None or compared.status is not ClimateComparisonStatus.ALIGNED:
        return _deny(room_id, ClimateOwnershipReason.ROOM_NOT_VERIFIED, device_count)
    if not actuators:
        return _deny(
            room_id,
            ClimateOwnershipReason.DEVICE_BINDING_MISSING,
            device_count,
        )
    if any(
        device.endpoint(ClimateEndpointRole.CONTROL) is None
        for device in actuators
    ):
        return _deny(
            room_id,
            ClimateOwnershipReason.DEVICE_BINDING_MISSING,
            device_count,
        )
    scopes = {device.control_scope for device in actuators}
    if scopes == {ClimateControlScope.MANAGED}:
        return ClimateOwnershipDecision(
            room_id=room_id,
            registry=None,
            reasons=(),
            device_count=device_count,
        )
    if ClimateControlScope.MANAGED in scopes:
        return _deny(room_id, ClimateOwnershipReason.DEVICE_SCOPE_MIXED, device_count)
    promoted = tuple(
        replace(device, control_scope=ClimateControlScope.MANAGED)
        if device.room_id == room_id and device.kind not in _PASSIVE_KINDS
        else device
        for device in registry.devices
    )
    return ClimateOwnershipDecision(
        room_id=room_id,
        registry=ClimateRegistry(
            rooms=registry.rooms,
            devices=promoted,
        ),
        reasons=(),
        device_count=device_count,
    )


def climate_ownership_skip_receipt(
    decision: ClimateOwnershipDecision,
    *,
    observed_at: int,
) -> ClimateOwnershipReceipt:
    """Return the redacted receipt of one not-promoted decision."""

    if not isinstance(decision, ClimateOwnershipDecision):
        raise ClimateOwnershipViolation("validated ownership decision is required")
    if decision.registry is None and not decision.reasons:
        return ClimateOwnershipReceipt(
            room_id=decision.room_id,
            observed_at=observed_at,
            status=ClimateOwnershipStatus.ALREADY_MANAGED,
            reasons=(),
            device_count=decision.device_count,
            promoted_count=0,
        )
    return ClimateOwnershipReceipt(
        room_id=decision.room_id,
        observed_at=observed_at,
        status=ClimateOwnershipStatus.DENIED,
        reasons=decision.reasons,
        device_count=decision.device_count,
        promoted_count=0,
    )


def climate_ownership_promoted_receipt(
    decision: ClimateOwnershipDecision,
    *,
    observed_at: int,
) -> ClimateOwnershipReceipt:
    """Return the redacted receipt of one cleanly promoted room."""

    if not isinstance(decision, ClimateOwnershipDecision) or (
        decision.registry is None
    ):
        raise ClimateOwnershipViolation("promoted receipt requires a new registry")
    return ClimateOwnershipReceipt(
        room_id=decision.room_id,
        observed_at=observed_at,
        status=ClimateOwnershipStatus.PROMOTED,
        reasons=(),
        device_count=decision.device_count,
        promoted_count=decision.device_count,
    )


def climate_ownership_failure_receipt(
    decision: ClimateOwnershipDecision,
    *,
    observed_at: int,
) -> ClimateOwnershipReceipt:
    """Return the redacted receipt of one failed registry save."""

    if not isinstance(decision, ClimateOwnershipDecision) or (
        decision.registry is None
    ):
        raise ClimateOwnershipViolation("failure receipt requires a new registry")
    return ClimateOwnershipReceipt(
        room_id=decision.room_id,
        observed_at=observed_at,
        status=ClimateOwnershipStatus.FAILED,
        reasons=(ClimateOwnershipReason.REGISTRY_SAVE_FAILED,),
        device_count=decision.device_count,
        promoted_count=0,
    )


def _deny(
    room_id: str,
    reason: ClimateOwnershipReason,
    device_count: int,
) -> ClimateOwnershipDecision:
    return ClimateOwnershipDecision(
        room_id=room_id,
        registry=None,
        reasons=(reason,),
        device_count=device_count,
    )
