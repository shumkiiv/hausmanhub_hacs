"""Translate source-private climate state into HausmanHub observations.

Only this adapter may use a registry source binding to find imported state.  Its
result contains stable HausmanHub ids and bounded facts, never HA entity ids,
service names, transport data, or executable commands.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from ..domain.climate import ClimateDeviceKind, ClimateRegistry
from ..domain.climate_observation import (
    ClimateControlObservation,
    ClimateDataStatus,
    ClimateDayPeriod,
    ClimateDelayedIntentState,
    ClimateDeviceActivity,
    ClimateDeviceAvailability,
    ClimateDeviceObservation,
    ClimateExecutionGuardState,
    ClimateFanMode,
    ClimateHomeObservation,
    ClimateObservationDeviceKind,
    ClimateObservationSnapshot,
    ClimateObservationViolation,
    ClimateOccupancyMode,
    ClimatePhysicalFeedback,
    ClimateRoomMode,
    ClimateRoomObservation,
    ClimateSeason,
    ClimateTemperatureQuality,
    ClimateWindowState,
)
from ..domain.climate_reference import load_climate_reference_suite
from .climate_import import ClimateImportSnapshot, ImportedClimateDevice


REFERENCE_OBSERVED_AT = 1_800_000_000_000
REFERENCE_ROOM_ID = "reference_room"
_DEVICE_KINDS = {
    kind: ClimateObservationDeviceKind(kind.value) for kind in ClimateDeviceKind
}
_REFERENCE_DEVICE_NAMES = {
    ClimateObservationDeviceKind.AIR_CONDITIONER: "Эталонный кондиционер",
    ClimateObservationDeviceKind.RADIATOR_THERMOSTAT: "Эталонная термоголовка",
    ClimateObservationDeviceKind.HUMIDIFIER: "Эталонный увлажнитель",
    ClimateObservationDeviceKind.FLOOR_HEATING: "Эталонный тёплый пол",
    ClimateObservationDeviceKind.TEMPERATURE_SENSOR: "Эталонный датчик температуры",
    ClimateObservationDeviceKind.HUMIDITY_SENSOR: "Эталонный датчик влажности",
    ClimateObservationDeviceKind.CURTAINS: "Эталонные шторы",
}


def build_climate_observation_snapshot(
    registry: ClimateRegistry,
    snapshot: ClimateImportSnapshot,
    *,
    observed_at: int | None = None,
) -> ClimateObservationSnapshot:
    """Build the algorithm boundary from configured rooms and bound devices."""

    if not isinstance(registry, ClimateRegistry):
        raise ClimateObservationViolation("a validated climate registry is required")
    if not isinstance(snapshot, ClimateImportSnapshot):
        raise ClimateObservationViolation("a validated climate import is required")
    status = (
        ClimateDataStatus.FRESH
        if snapshot.runtime_fresh
        else ClimateDataStatus.STALE
    )
    observation_time = snapshot.generated_at if observed_at is None else observed_at
    rooms: list[ClimateRoomObservation] = []
    for room in registry.rooms:
        imported = snapshot.room(room.room_id)
        if imported is None:
            rooms.append(_unavailable_room(room.room_id, room.name))
            continue
        rooms.append(
            ClimateRoomObservation(
                room_id=room.room_id,
                name=room.name,
                data_status=status,
                temperature=imported.temperature,
                humidity=imported.humidity,
                temperature_quality=(
                    ClimateTemperatureQuality.NORMAL
                    if imported.temperature is not None
                    else ClimateTemperatureQuality.UNKNOWN
                ),
                window=ClimateWindowState.UNKNOWN,
                mode=_room_mode(imported.mode),
                observed_target_temperature=imported.target_temperature,
                observed_target_humidity=imported.target_humidity,
                observed_target_strategy=imported.target_strategy,
                authority_eligible=(
                    imported.authority_eligible
                    and status is ClimateDataStatus.FRESH
                ),
            )
        )

    devices: list[ClimateDeviceObservation] = []
    for device in registry.devices:
        # The private source binding is deliberately consumed only by this lookup.
        imported = snapshot.device(device.source_id)
        if imported is None or imported.room_id != device.room_id:
            devices.append(
                _unavailable_device(
                    device.device_id,
                    device.name,
                    device.room_id,
                    device.kind,
                    ClimateDeviceAvailability.MISSING,
                )
            )
            continue
        if not imported.available:
            devices.append(
                _unavailable_device(
                    device.device_id,
                    device.name,
                    device.room_id,
                    device.kind,
                    ClimateDeviceAvailability.UNAVAILABLE,
                )
            )
            continue
        devices.append(
            ClimateDeviceObservation(
                device_id=device.device_id,
                name=device.name,
                room_id=device.room_id,
                kind=_DEVICE_KINDS[device.kind],
                availability=ClimateDeviceAvailability.AVAILABLE,
                activity=_device_activity(device.kind, imported),
            )
        )

    return ClimateObservationSnapshot(
        observed_at=observation_time,
        source_generated_at=snapshot.generated_at,
        data_status=status,
        home=ClimateHomeObservation(),
        control=ClimateControlObservation(),
        rooms=tuple(rooms),
        devices=tuple(devices),
    )


def unavailable_climate_observation_snapshot(
    registry: ClimateRegistry,
    *,
    observed_at: int,
) -> ClimateObservationSnapshot:
    """Represent a complete lack of source state without inventing values."""

    if not isinstance(registry, ClimateRegistry):
        raise ClimateObservationViolation("a validated climate registry is required")
    return ClimateObservationSnapshot(
        observed_at=observed_at,
        source_generated_at=None,
        data_status=ClimateDataStatus.UNAVAILABLE,
        home=ClimateHomeObservation(),
        control=ClimateControlObservation(),
        rooms=tuple(
            _unavailable_room(room.room_id, room.name) for room in registry.rooms
        ),
        devices=tuple(
            _unavailable_device(
                device.device_id,
                device.name,
                device.room_id,
                device.kind,
                ClimateDeviceAvailability.MISSING,
            )
            for device in registry.devices
        ),
    )


def _reference_case(case_id: str) -> Mapping[str, object]:
    if not isinstance(case_id, str) or not case_id:
        raise ClimateObservationViolation("reference case id is required")
    suite = load_climate_reference_suite()
    raw_cases = _sequence(suite.get("cases"), "reference cases")
    case = next(
        (
            item
            for item in raw_cases
            if isinstance(item, Mapping) and item.get("id") == case_id
        ),
        None,
    )
    if case is None:
        raise ClimateObservationViolation("reference case is unknown")
    return case


# The frozen suite only maintains through summer cooling; a winter maintain
# case must revisit this fixed post-decision activity mapping.
_MODULE_RUNNING_ACTIONS = {
    "cool": ClimateDeviceActivity.COOLING,
    "heat": ClimateDeviceActivity.HEATING,
    "maintain": ClimateDeviceActivity.COOLING,
}
_MODULE_STOP_ACTIONS = frozenset({"off", "safe_off"})


def climate_reference_module_observation(
    case_id: str,
    *,
    observed_at: int = REFERENCE_OBSERVED_AT,
) -> ClimateObservationSnapshot:
    """Express the frozen module decision as its post-decision observed state."""

    case = _reference_case(case_id)
    expected = _mapping(case.get("expected"), "reference expected")
    base = climate_reference_observation(case_id, observed_at=observed_at)
    devices = tuple(
        _module_decision_device(
            device,
            action=expected.get("action"),
            auxiliary=expected.get("auxiliary_action"),
            setpoint=_optional_number(expected.get("setpoint"), "expected setpoint"),
            fan_mode=_optional_enum(
                ClimateFanMode,
                expected.get("fan_mode"),
                "expected fan mode",
            ),
            quiet=_optional_bool(expected.get("quiet"), "expected quiet"),
        )
        for device in base.devices
    )
    return replace(base, devices=devices)


def _module_decision_device(
    device: ClimateDeviceObservation,
    *,
    action: object,
    auxiliary: object,
    setpoint: float | None,
    fan_mode: ClimateFanMode | None,
    quiet: bool | None,
) -> ClimateDeviceObservation:
    if device.kind is ClimateObservationDeviceKind.AIR_CONDITIONER:
        activity = _MODULE_RUNNING_ACTIONS.get(action)
        if activity is None:
            activity = (
                ClimateDeviceActivity.STOPPED
                if action in _MODULE_STOP_ACTIONS
                else device.activity
            )
        return replace(
            device,
            activity=activity,
            current_target_temperature=setpoint,
            fan_mode=fan_mode,
            quiet=quiet,
        )
    if device.kind is ClimateObservationDeviceKind.HUMIDIFIER:
        return replace(
            device,
            activity=(
                ClimateDeviceActivity.HUMIDIFYING
                if auxiliary == "humidifier_on"
                else ClimateDeviceActivity.STOPPED
            ),
        )
    return device


def climate_reference_observation(
    case_id: str,
    *,
    observed_at: int = REFERENCE_OBSERVED_AT,
) -> ClimateObservationSnapshot:
    """Express one frozen migration case through the new internal model."""

    case = _reference_case(case_id)
    values = _mapping(case.get("input"), "reference input")
    observation = _mapping(values.get("observation"), "reference observation")
    fresh = _bool(observation.get("state_fresh"), "reference state freshness")
    status = ClimateDataStatus.FRESH if fresh else ClimateDataStatus.STALE
    temperature = _optional_number(observation.get("temperature"), "temperature")
    room = ClimateRoomObservation(
        room_id=REFERENCE_ROOM_ID,
        name="Эталонная комната",
        data_status=status,
        temperature=temperature,
        humidity=_optional_number(observation.get("humidity"), "humidity"),
        temperature_quality=_enum(
            ClimateTemperatureQuality,
            observation.get("temperature_quality"),
            "temperature quality",
        ),
        window=_enum(ClimateWindowState, observation.get("window"), "window"),
        mode=_enum(ClimateRoomMode, values.get("room_mode"), "room mode"),
        observed_target_temperature=_optional_number(
            values.get("target_temperature"),
            "target temperature",
        ),
        hard_off_temperature=_optional_number(
            values.get("hard_off_temperature"),
            "hard-off temperature",
        ),
        observed_target_humidity=_optional_number(
            values.get("target_humidity"),
            "target humidity",
        ),
        authority_eligible=(
            fresh
            and values.get("execution_guard") != "authority_missing"
            and temperature is not None
        ),
        cooling_allowed=_optional_bool(
            observation.get("cooling_allowed"),
            "cooling permission",
        ),
        heating_allowed=_optional_bool(
            observation.get("heating_allowed"),
            "heating permission",
        ),
    )
    available_values = _sequence(
        values.get("available_devices"),
        "available reference devices",
    )
    available = tuple(
        _enum(
            ClimateObservationDeviceKind,
            value,
            "available device kind",
        )
        for value in available_values
    )
    if len(available) != len(set(available)):
        raise ClimateObservationViolation("available reference devices must be unique")
    devices = tuple(
        _reference_device(kind, observation, observed_at) for kind in available
    )
    return ClimateObservationSnapshot(
        observed_at=observed_at,
        source_generated_at=observed_at,
        data_status=status,
        home=ClimateHomeObservation(
            season=_enum(
                ClimateSeason,
                values.get("season", ClimateSeason.UNKNOWN.value),
                "season",
            ),
            period=_enum(
                ClimateDayPeriod,
                values.get("period", ClimateDayPeriod.UNKNOWN.value),
                "day period",
            ),
            outdoor_temperature=_optional_number(
                values.get("outdoor_temperature"),
                "outdoor temperature",
            ),
            heat_load_temperature=_optional_number(
                values.get("heat_load_temperature"),
                "outdoor heat-load temperature",
            ),
            central_heating_on=_optional_bool(
                observation.get("central_heating_on"),
                "central heating",
            ),
            occupancy=_enum(
                ClimateOccupancyMode,
                values.get("away", ClimateOccupancyMode.HOME.value),
                "occupancy",
            ),
        ),
        control=ClimateControlObservation(
            manual_request=(manual_request := _bool(
                values.get("manual_request", False),
                "manual request",
            )),
            manual_request_room_id=(REFERENCE_ROOM_ID if manual_request else None),
            delayed_intent=(delayed_intent := _enum(
                ClimateDelayedIntentState,
                observation.get(
                    "delayed_command",
                    ClimateDelayedIntentState.NONE.value,
                ),
                "delayed intent",
            )),
            delayed_intent_room_id=(
                REFERENCE_ROOM_ID
                if delayed_intent is not ClimateDelayedIntentState.NONE
                else None
            ),
            execution_guard=(execution_guard := _enum(
                ClimateExecutionGuardState,
                values.get(
                    "execution_guard",
                    ClimateExecutionGuardState.NONE.value,
                ),
                "execution guard",
            )),
            execution_guard_room_id=(
                REFERENCE_ROOM_ID
                if execution_guard is not ClimateExecutionGuardState.NONE
                else None
            ),
        ),
        rooms=(room,),
        devices=devices,
    )


def _unavailable_room(room_id: str, name: str) -> ClimateRoomObservation:
    return ClimateRoomObservation(
        room_id=room_id,
        name=name,
        data_status=ClimateDataStatus.UNAVAILABLE,
    )


def _unavailable_device(
    device_id: str,
    name: str,
    room_id: str,
    kind: ClimateDeviceKind,
    availability: ClimateDeviceAvailability,
) -> ClimateDeviceObservation:
    return ClimateDeviceObservation(
        device_id=device_id,
        name=name,
        room_id=room_id,
        kind=_DEVICE_KINDS[kind],
        availability=availability,
    )


def _room_mode(value: str | None) -> ClimateRoomMode:
    if value == "auto":
        return ClimateRoomMode.AUTO
    if value == "manual":
        return ClimateRoomMode.MANUAL
    return ClimateRoomMode.UNKNOWN


def _device_activity(
    kind: ClimateDeviceKind,
    imported: ImportedClimateDevice,
) -> ClimateDeviceActivity:
    state = imported.state.lower()
    if kind in {
        ClimateDeviceKind.TEMPERATURE_SENSOR,
        ClimateDeviceKind.HUMIDITY_SENSOR,
    }:
        return ClimateDeviceActivity.IDLE
    if state in {"off", "stopped"}:
        return ClimateDeviceActivity.STOPPED
    if state in {"cool", "cooling"}:
        return ClimateDeviceActivity.COOLING
    if state in {"heat", "heating"}:
        return ClimateDeviceActivity.HEATING
    if state in {"humidifying"}:
        return ClimateDeviceActivity.HUMIDIFYING
    if state in {"idle", "standby"}:
        return ClimateDeviceActivity.IDLE
    if state in {"on", "running"}:
        return (
            ClimateDeviceActivity.HUMIDIFYING
            if kind is ClimateDeviceKind.HUMIDIFIER
            else ClimateDeviceActivity.RUNNING
        )
    return ClimateDeviceActivity.UNKNOWN


def _reference_device(
    kind: ClimateObservationDeviceKind,
    observation: Mapping[str, object],
    observed_at: int,
) -> ClimateDeviceObservation:
    common: dict[str, object] = {
        "device_id": f"reference_{kind.value}",
        "name": _REFERENCE_DEVICE_NAMES[kind],
        "room_id": REFERENCE_ROOM_ID,
        "kind": kind,
        "availability": ClimateDeviceAvailability.AVAILABLE,
    }
    if kind is ClimateObservationDeviceKind.AIR_CONDITIONER:
        common.update(
            activity=_enum(
                ClimateDeviceActivity,
                observation.get("air_conditioner"),
                "air conditioner activity",
            ),
            current_target_temperature=_optional_number(
                observation.get("current_setpoint"),
                "current setpoint",
            ),
            fan_mode=_optional_enum(
                ClimateFanMode,
                observation.get("current_fan_mode"),
                "current fan mode",
            ),
            quiet=_optional_bool(observation.get("current_quiet"), "quiet state"),
            physical_feedback=_enum(
                ClimatePhysicalFeedback,
                observation.get(
                    "physical_feedback",
                    ClimatePhysicalFeedback.UNKNOWN.value,
                ),
                "physical feedback",
            ),
            last_started_at=_minutes_before(
                observed_at,
                observation.get("minutes_since_start"),
                "minutes since start",
            ),
            last_stopped_at=_minutes_before(
                observed_at,
                observation.get("minutes_since_stop"),
                "minutes since stop",
            ),
            cooling_evidence_confirmed=_bool(
                observation.get("cooling_evidence_confirmed"),
                "cooling evidence",
            ),
            cooling_rate_per_hour=_optional_number(
                observation.get("cooling_rate_per_hour"),
                "cooling rate",
            ),
        )
    elif kind is ClimateObservationDeviceKind.HUMIDIFIER:
        humidifier_on = _bool(observation.get("humidifier_on"), "humidifier state")
        common["activity"] = (
            ClimateDeviceActivity.HUMIDIFYING
            if humidifier_on
            else ClimateDeviceActivity.STOPPED
        )
    else:
        common["activity"] = ClimateDeviceActivity.UNKNOWN
    return ClimateDeviceObservation(**common)  # type: ignore[arg-type]


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ClimateObservationViolation(f"{label} must be an object")
    return value


def _sequence(value: object, label: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ClimateObservationViolation(f"{label} must be an array")
    return value


def _bool(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise ClimateObservationViolation(f"{label} must be boolean")
    return value


def _optional_bool(value: object, label: str) -> bool | None:
    if value is None:
        return None
    return _bool(value, label)


def _optional_number(value: object, label: str) -> float | None:
    if value is None:
        return None
    if type(value) not in {int, float}:
        raise ClimateObservationViolation(f"{label} must be numeric or unavailable")
    return float(value)


def _enum(expected: type, value: object, label: str):
    if not isinstance(value, str):
        raise ClimateObservationViolation(f"{label} must be approved")
    try:
        return expected(value)
    except ValueError as error:
        raise ClimateObservationViolation(f"{label} must be approved") from error


def _optional_enum(expected: type, value: object, label: str):
    if value is None:
        return None
    return _enum(expected, value, label)


def _minutes_before(observed_at: int, value: object, label: str) -> int | None:
    if value is None:
        return None
    if type(value) not in {int, float} or value < 0:
        raise ClimateObservationViolation(f"{label} must be non-negative")
    milliseconds = float(value) * 60_000
    if not milliseconds.is_integer() or milliseconds > observed_at:
        raise ClimateObservationViolation(f"{label} is outside the observation")
    return observed_at - int(milliseconds)
