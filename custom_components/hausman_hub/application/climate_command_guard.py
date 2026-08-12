"""Filter satisfied climate calls and suppress repeated desired states."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
import hashlib
import json

from ..domain.climate import ClimateRegistry
from ..domain.climate_command_guard import (
    CLIMATE_COMMAND_GUARD_VERSION,
    ClimateCommandGuardMemory,
    ClimateCommandGuardViolation,
    ClimateGuardedCommand,
)
from ..domain.climate_comparison import (
    ClimateComparisonSnapshot,
    ClimateComparisonStatus,
)
from ..domain.climate_ha_calls import (
    ClimateHaCallPlanSnapshot,
    ClimateHaService,
    ClimateHaServiceCall,
)


@dataclass(frozen=True, slots=True)
class GuardedDeviceCalls:
    """One logical device batch reserved before physical execution."""

    device_id: str
    room_id: str
    calls: tuple[ClimateHaServiceCall, ...]
    fingerprint: str


@dataclass(frozen=True, slots=True)
class GuardedClimatePlan:
    """A narrowed call plan plus the device batches it contains."""

    call_plan: ClimateHaCallPlanSnapshot
    devices: tuple[GuardedDeviceCalls, ...]


def empty_climate_command_guard(*, updated_at: int) -> ClimateCommandGuardMemory:
    """Create an empty validated restart-safe command guard."""

    return ClimateCommandGuardMemory(updated_at=updated_at, commands=())


def reconcile_climate_command_guard(
    memory: ClimateCommandGuardMemory,
    registry: ClimateRegistry,
    *,
    now_ms: int,
) -> tuple[ClimateCommandGuardMemory, bool]:
    """Drop removed devices and future-dated memory after a clock change."""

    if not isinstance(memory, ClimateCommandGuardMemory):
        raise ClimateCommandGuardViolation("validated command guard is required")
    if not isinstance(registry, ClimateRegistry):
        raise ClimateCommandGuardViolation("validated climate registry is required")
    if type(now_ms) is not int or now_ms < 0:
        raise ClimateCommandGuardViolation("command guard reconciliation time is invalid")
    if memory.updated_at > now_ms:
        return empty_climate_command_guard(updated_at=now_ms), True
    known = {device.device_id for device in registry.devices}
    retained = tuple(
        command for command in memory.commands if command.device_id in known
    )
    if retained == memory.commands:
        return memory, False
    return replace(memory, updated_at=now_ms, commands=retained), True


def clear_aligned_climate_commands(
    memory: ClimateCommandGuardMemory,
    comparison: ClimateComparisonSnapshot,
    *,
    now_ms: int,
) -> tuple[ClimateCommandGuardMemory, bool]:
    """Rearm a device only after its observed state fully matches the plan."""

    aligned = {
        device.device_id
        for room in comparison.rooms
        for device in room.devices
        if device.status is ClimateComparisonStatus.ALIGNED
    }
    retained = tuple(
        command for command in memory.commands if command.device_id not in aligned
    )
    if retained == memory.commands:
        return memory, False
    return replace(memory, updated_at=now_ms, commands=retained), True


def guard_diverged_climate_calls(
    call_plan: ClimateHaCallPlanSnapshot,
    comparison: ClimateComparisonSnapshot,
    *,
    state_lookup: Callable[[str], object | None],
    memory: ClimateCommandGuardMemory,
) -> GuardedClimatePlan:
    """Keep only unsatisfied calls for diverged devices not already attempted."""

    if call_plan.observed_at != comparison.observed_at:
        raise ClimateCommandGuardViolation("guard inputs must share one observation")
    guarded_devices: list[GuardedDeviceCalls] = []
    guarded_rooms = []
    for room in call_plan.rooms:
        compared_room = comparison.room(room.room_id)
        compared_devices = {
            device.device_id: device
            for device in (() if compared_room is None else compared_room.devices)
        }
        narrowed = []
        for device in room.devices:
            compared = compared_devices.get(device.device_id)
            if (
                compared is None
                or compared.status is not ClimateComparisonStatus.DIVERGED
            ):
                continue
            if device.limits:
                narrowed.append(device)
                continue
            fingerprint = climate_calls_fingerprint(device.calls)
            previous = memory.command(device.device_id)
            if previous is not None and previous.fingerprint == fingerprint:
                continue
            calls = tuple(
                call
                for call in device.calls
                if not climate_call_is_satisfied(call, state_lookup)
            )
            if not calls:
                continue
            narrowed.append(replace(device, calls=calls))
            guarded_devices.append(
                GuardedDeviceCalls(
                    device.device_id,
                    device.room_id,
                    calls,
                    fingerprint,
                )
            )
        guarded_rooms.append(replace(room, devices=tuple(narrowed)))
    return GuardedClimatePlan(
        call_plan=replace(call_plan, rooms=tuple(guarded_rooms)),
        devices=tuple(guarded_devices),
    )


def full_climate_synchronization_plan(
    call_plan: ClimateHaCallPlanSnapshot,
    *,
    room_ids: Sequence[str],
) -> GuardedClimatePlan:
    """Select every completely translated automatic device for explicit sync."""

    selected = frozenset(room_ids)
    guarded_devices: list[GuardedDeviceCalls] = []
    guarded_rooms = []
    for room in call_plan.rooms:
        narrowed = []
        if room.room_id in selected:
            for device in room.devices:
                if not device.calls or device.limits:
                    continue
                fingerprint = climate_calls_fingerprint(device.calls)
                narrowed.append(device)
                guarded_devices.append(
                    GuardedDeviceCalls(
                        device.device_id,
                        device.room_id,
                        device.calls,
                        fingerprint,
                    )
                )
        guarded_rooms.append(replace(room, devices=tuple(narrowed)))
    return GuardedClimatePlan(
        call_plan=replace(call_plan, rooms=tuple(guarded_rooms)),
        devices=tuple(guarded_devices),
    )


def reserve_guarded_commands(
    memory: ClimateCommandGuardMemory,
    devices: Sequence[GuardedDeviceCalls],
    *,
    attempted_at: int,
) -> ClimateCommandGuardMemory:
    """Persist intent fingerprints before any physical service call."""

    by_device = {command.device_id: command for command in memory.commands}
    for device in devices:
        by_device[device.device_id] = ClimateGuardedCommand(
            device_id=device.device_id,
            fingerprint=device.fingerprint,
            attempted_at=attempted_at,
        )
    return replace(
        memory,
        updated_at=attempted_at,
        commands=tuple(by_device[key] for key in sorted(by_device)),
    )


def reserve_scheduled_synchronization(
    memory: ClimateCommandGuardMemory,
    *,
    slot: str,
    reserved_at: int,
) -> tuple[ClimateCommandGuardMemory, bool]:
    """Latch one local 10:00 or 22:00 slot before executing it."""

    candidate = replace(memory, updated_at=reserved_at, last_scheduled_slot=slot)
    if candidate.last_scheduled_slot == memory.last_scheduled_slot:
        return memory, False
    return candidate, True


def climate_calls_fingerprint(calls: Sequence[ClimateHaServiceCall]) -> str:
    """Hash the complete desired service batch without persisting private values."""

    payload = [
        {
            "service": call.service.value,
            "entity_id": call.entity_id,
            "hvac_mode": None if call.hvac_mode is None else call.hvac_mode.value,
            "temperature": call.temperature,
            "fan_mode": None if call.fan_mode is None else call.fan_mode.value,
            "humidity": call.humidity,
            "device": call.device,
            "command": call.command,
        }
        for call in calls
    ]
    encoded = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def climate_call_is_satisfied(
    call: ClimateHaServiceCall,
    state_lookup: Callable[[str], object | None],
) -> bool:
    """Compare one exact field with current HA state before sending it."""

    if call.service is ClimateHaService.REMOTE_SEND_COMMAND:
        return False
    state = state_lookup(call.entity_id)
    state_value = getattr(state, "state", None)
    attributes = getattr(state, "attributes", None)
    if not isinstance(state_value, str) or state_value in {
        "",
        "unknown",
        "unavailable",
    } or not isinstance(attributes, Mapping):
        return False
    if call.service is ClimateHaService.CLIMATE_SET_HVAC_MODE:
        return call.hvac_mode is not None and state_value == call.hvac_mode.value
    if call.service is ClimateHaService.CLIMATE_SET_TEMPERATURE:
        return _numbers_equal(attributes.get("temperature"), call.temperature)
    if call.service is ClimateHaService.CLIMATE_SET_FAN_MODE:
        return call.fan_mode is not None and attributes.get("fan_mode") == call.fan_mode.value
    if call.service is ClimateHaService.HUMIDIFIER_TURN_ON:
        return state_value == "on"
    if call.service is ClimateHaService.HUMIDIFIER_TURN_OFF:
        return state_value == "off"
    if call.service is ClimateHaService.HUMIDIFIER_SET_HUMIDITY:
        return _numbers_equal(attributes.get("humidity"), call.humidity)
    return False


def climate_command_guard_to_payload(
    memory: ClimateCommandGuardMemory,
) -> dict[str, object]:
    """Serialize the exact entity-free restart payload."""

    return {
        "version": memory.version,
        "updated_at": memory.updated_at,
        "last_scheduled_slot": memory.last_scheduled_slot,
        "commands": [
            {
                "device_id": command.device_id,
                "fingerprint": command.fingerprint,
                "attempted_at": command.attempted_at,
            }
            for command in memory.commands
        ],
    }


def climate_command_guard_from_payload(payload: object) -> ClimateCommandGuardMemory:
    """Restore only the exact supported command guard shape."""

    if not isinstance(payload, Mapping) or set(payload) != {
        "version",
        "updated_at",
        "last_scheduled_slot",
        "commands",
    }:
        raise ClimateCommandGuardViolation("stored command guard fields are invalid")
    raw_commands = payload.get("commands")
    if not isinstance(raw_commands, list):
        raise ClimateCommandGuardViolation("stored guarded commands are invalid")
    commands = []
    for raw in raw_commands:
        if not isinstance(raw, Mapping) or set(raw) != {
            "device_id",
            "fingerprint",
            "attempted_at",
        }:
            raise ClimateCommandGuardViolation("stored guarded command is invalid")
        commands.append(
            ClimateGuardedCommand(
                device_id=raw.get("device_id"),  # type: ignore[arg-type]
                fingerprint=raw.get("fingerprint"),  # type: ignore[arg-type]
                attempted_at=raw.get("attempted_at"),  # type: ignore[arg-type]
            )
        )
    return ClimateCommandGuardMemory(
        version=payload.get("version"),  # type: ignore[arg-type]
        updated_at=payload.get("updated_at"),  # type: ignore[arg-type]
        last_scheduled_slot=payload.get("last_scheduled_slot"),  # type: ignore[arg-type]
        commands=tuple(commands),
    )


def _numbers_equal(current: object, desired: int | float | None) -> bool:
    if desired is None or type(current) not in {int, float}:
        return False
    return abs(float(current) - float(desired)) < 0.05
