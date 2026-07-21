"""Read-only import of the existing HausMan Climate API v1 contract."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..domain.climate import ClimateDeviceKind, ClimateModelViolation, ClimateRoom


CLIMATE_API_CONTRACT_NAME = "hausman-climate"
CLIMATE_API_CONTRACT_VERSION = 1
MAX_CLIMATE_ROOMS = 128
MAX_CLIMATE_DEVICES = 512
MAX_CLIMATE_STATE_AGE_MS = 5 * 60 * 1000
MAX_FUTURE_SKEW_MS = 60 * 1000
SUPPORTED_BACKEND_COMMAND_TYPES = frozenset(
    {
        "climate.set_hvac_mode",
        "climate.set_temperature",
        "climate.set_fan_mode",
        "climate.turn_off",
        "humidifier.turn_on",
        "humidifier.turn_off",
        "humidifier.set_humidity",
        "trv.set_temperature",
        "switch.turn_on",
        "switch.turn_off",
    }
)


class ClimateImportViolation(ValueError):
    """The source snapshot is unsupported, incomplete, or unsafe."""


@dataclass(frozen=True, slots=True)
class ImportedClimateRoom:
    """One read-only room projection from the existing climate core."""

    room_id: str
    name: str
    temperature: float | None
    humidity: float | None
    target_temperature: float | None
    target_humidity: float | None
    target_strategy: str | None
    mode: str | None
    authority_eligible: bool


@dataclass(frozen=True, slots=True)
class ImportedClimateDevice:
    """A source device candidate that still requires explicit HausmanHub binding."""

    source_id: str
    name: str
    room_id: str
    domain: str
    category: str
    state: str
    available: bool
    command_types: tuple[str, ...]
    suggested_kinds: tuple[ClimateDeviceKind, ...]
    endpoints: tuple = ()


@dataclass(frozen=True, slots=True)
class ClimateImportSnapshot:
    """The bounded state accepted from Climate API v1."""

    generated_at: int
    runtime_fresh: bool
    rooms: tuple[ImportedClimateRoom, ...]
    devices: tuple[ImportedClimateDevice, ...]

    def room(self, room_id: str) -> ImportedClimateRoom | None:
        """Return one imported room by stable id."""

        return next((room for room in self.rooms if room.room_id == room_id), None)

    def device(self, source_id: str) -> ImportedClimateDevice | None:
        """Return one imported candidate by source-private id."""

        return next(
            (device for device in self.devices if device.source_id == source_id),
            None,
        )


def import_climate_state(
    payload: object,
    *,
    now_ms: int | None = None,
) -> ClimateImportSnapshot:
    """Validate and reduce Climate API v1 without mutating either system."""

    root = _mapping(payload, "climate state")
    contract = _mapping(root.get("contract"), "climate contract")
    if contract.get("name") != CLIMATE_API_CONTRACT_NAME:
        raise ClimateImportViolation("unsupported climate contract name")
    if (
        type(contract.get("version")) is not int
        or contract["version"] != CLIMATE_API_CONTRACT_VERSION
    ):
        raise ClimateImportViolation("unsupported climate contract version")
    generated_at = _timestamp(root.get("generatedAt"), "generatedAt")
    runtime_fresh = _runtime_is_fresh(root.get("runtimeHealth"), generated_at, now_ms)
    authority = _authority_by_room(root.get("authorityReadiness"))
    command_types = _command_types_by_device(root.get("capabilities"))
    rooms = _rooms(root.get("rooms"), authority)
    devices = _devices(root.get("devices"), command_types, {room.room_id for room in rooms})
    return ClimateImportSnapshot(
        generated_at=generated_at,
        runtime_fresh=runtime_fresh,
        rooms=rooms,
        devices=devices,
    )


def _rooms(
    value: object,
    authority: Mapping[str, bool],
) -> tuple[ImportedClimateRoom, ...]:
    values = _bounded_list(value, "rooms", MAX_CLIMATE_ROOMS)
    rooms: list[ImportedClimateRoom] = []
    for index, raw in enumerate(values):
        room = _mapping(raw, f"room {index}")
        room_id = _stable_id(room.get("id", room.get("roomId")), "room id")
        name = _name(room.get("name", room.get("roomName")), "room name")
        source = room.get("sourceData") if isinstance(room.get("sourceData"), Mapping) else {}
        controls = room.get("controls") if isinstance(room.get("controls"), Mapping) else {}
        targets = room.get("targets") if isinstance(room.get("targets"), Mapping) else {}
        rooms.append(
            ImportedClimateRoom(
                room_id=room_id,
                name=name,
                temperature=_number(
                    source.get("temperature", source.get("temp", room.get("temperature", room.get("temp"))))
                ),
                humidity=_number(source.get("humidity", room.get("humidity"))),
                target_temperature=_target_value(
                    controls.get("targetTemperature"),
                    targets.get("temperature", room.get("targetTemperature")),
                ),
                target_humidity=_target_value(
                    controls.get("targetHumidity"),
                    targets.get("humidity", room.get("targetHumidity")),
                ),
                target_strategy=_target_strategy(
                    controls.get("targetStrategy"),
                    targets.get(
                        "targetStrategy",
                        targets.get("coolingProfile", room.get("targetStrategy")),
                    ),
                ),
                mode=_room_mode(room),
                authority_eligible=authority.get(room_id, False),
            )
        )
    _require_unique((room.room_id for room in rooms), "imported room ids")
    return tuple(rooms)


def _devices(
    value: object,
    command_types: Mapping[str, tuple[str, ...]],
    room_ids: set[str],
) -> tuple[ImportedClimateDevice, ...]:
    values = _bounded_list(value, "devices", MAX_CLIMATE_DEVICES)
    devices: list[ImportedClimateDevice] = []
    for index, raw in enumerate(values):
        device = _mapping(raw, f"device {index}")
        source_id = _source_id(
            device.get("deviceId", device.get("id", device.get("entityId")))
        )
        room_id = _stable_id(device.get("roomId"), "device room id")
        if room_id not in room_ids:
            raise ClimateImportViolation("imported device references an unknown room")
        domain = _code(device.get("domain"), "device domain", allow_empty=True)
        category = _code(device.get("category"), "device category", allow_empty=True)
        state = _code(device.get("state"), "device state", allow_empty=True)
        supported = command_types.get(source_id, ())
        devices.append(
            ImportedClimateDevice(
                source_id=source_id,
                name=_name(device.get("name", device.get("title")), "device name"),
                room_id=room_id,
                domain=domain,
                category=category,
                state=state,
                available=(
                    type(device.get("unavailable")) is bool
                    and device.get("unavailable") is False
                )
                or (
                    device.get("unavailable") is None
                    and state != "unavailable"
                ),
                command_types=supported,
                suggested_kinds=_suggested_kinds(device, domain, category, supported),
            )
        )
    _require_unique((device.source_id for device in devices), "imported device ids")
    return tuple(devices)


def _command_types_by_device(value: object) -> dict[str, tuple[str, ...]]:
    values = _bounded_list(value, "capabilities", MAX_CLIMATE_DEVICES)
    result: dict[str, tuple[str, ...]] = {}
    for index, raw in enumerate(values):
        item = _mapping(raw, f"capability {index}")
        source_id = _source_id(item.get("deviceId", item.get("id")))
        raw_types = item.get("commandTypes", ())
        if not isinstance(raw_types, list):
            raise ClimateImportViolation("capability command types must be a list")
        types = tuple(raw_types)
        if any(not isinstance(value, str) or value not in SUPPORTED_BACKEND_COMMAND_TYPES for value in types):
            raise ClimateImportViolation("capability contains an unsupported command type")
        _require_unique(types, "capability command types")
        if source_id in result:
            raise ClimateImportViolation("capability device ids must be unique")
        result[source_id] = types
    return result


def _authority_by_room(value: object) -> dict[str, bool]:
    authority = _mapping(value, "authority readiness")
    rooms = _bounded_list(authority.get("rooms"), "authority rooms", MAX_CLIMATE_ROOMS)
    result: dict[str, bool] = {}
    for index, raw in enumerate(rooms):
        item = _mapping(raw, f"authority room {index}")
        room_id = _stable_id(item.get("roomId"), "authority room id")
        if room_id in result:
            raise ClimateImportViolation("authority room ids must be unique")
        result[room_id] = item.get("eligible") is True and not item.get("reasons")
    return result


def _runtime_is_fresh(value: object, generated_at: int, now_ms: int | None) -> bool:
    runtime = _mapping(value, "runtime health")
    if runtime.get("status") != "fresh":
        return False
    if now_ms is None:
        return True
    if type(now_ms) is not int or now_ms < 0:
        raise ClimateImportViolation("current time must be a non-negative integer")
    age = now_ms - generated_at
    return -MAX_FUTURE_SKEW_MS <= age <= MAX_CLIMATE_STATE_AGE_MS


def _suggested_kinds(
    device: Mapping[str, Any],
    domain: str,
    category: str,
    command_types: tuple[str, ...],
) -> tuple[ClimateDeviceKind, ...]:
    explicit = device.get("kind", device.get("deviceKind"))
    if isinstance(explicit, str):
        try:
            return (ClimateDeviceKind(explicit),)
        except ValueError:
            raise ClimateImportViolation("device contains an unsupported explicit kind") from None
    types = set(command_types)
    if domain == "humidifier" or any(value.startswith("humidifier.") for value in types):
        return (ClimateDeviceKind.HUMIDIFIER,)
    if "trv.set_temperature" in types:
        return (ClimateDeviceKind.RADIATOR_THERMOSTAT,)
    if domain == "climate" and category in {"heating", "floor_heating"}:
        return (
            ClimateDeviceKind.RADIATOR_THERMOSTAT,
            ClimateDeviceKind.FLOOR_HEATING,
        )
    if domain == "climate" or any(value.startswith("climate.") for value in types):
        return (ClimateDeviceKind.AIR_CONDITIONER,)
    if domain == "sensor" and category == "temperature":
        return (ClimateDeviceKind.TEMPERATURE_SENSOR,)
    if domain == "sensor" and category == "humidity":
        return (ClimateDeviceKind.HUMIDITY_SENSOR,)
    return ()


def _room_mode(room: Mapping[str, Any]) -> str | None:
    mode = room.get("mode")
    if mode in {"auto", "manual"}:
        return mode
    manual = room.get("manualControl")
    if isinstance(manual, Mapping) and type(manual.get("active")) is bool:
        return "manual" if manual["active"] else "auto"
    return None


def _target_value(control: object, fallback: object) -> float | None:
    if isinstance(control, Mapping):
        for key in ("value", "target", "current"):
            value = _number(control.get(key))
            if value is not None:
                return value
    return _number(fallback)


def _target_strategy(control: object, fallback: object) -> str | None:
    """Read only the three strategies already supported by climate-core."""

    if isinstance(control, Mapping):
        for key in ("value", "target", "current"):
            value = control.get(key)
            if isinstance(value, str) and value in {
                "soft",
                "normal",
                "aggressive",
            }:
                return value
    return (
        fallback
        if isinstance(fallback, str)
        and fallback in {"soft", "normal", "aggressive"}
        else None
    )


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ClimateImportViolation(f"{label} must be an object")
    return value


def _bounded_list(value: object, label: str, maximum: int) -> list[object]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ClimateImportViolation(f"{label} must be a bounded list")
    return value


def _stable_id(value: object, label: str) -> str:
    try:
        return ClimateRoom(value, "Temporary").room_id  # type: ignore[arg-type]
    except ClimateModelViolation as error:
        raise ClimateImportViolation(f"{label} must be a stable lowercase id") from error


def _source_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 255
    ):
        raise ClimateImportViolation("device source id is required")
    return value


def _name(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 120:
        raise ClimateImportViolation(f"{label} is invalid")
    return value


def _code(value: object, label: str, *, allow_empty: bool = False) -> str:
    if value is None and allow_empty:
        return ""
    if not isinstance(value, str) or len(value) > 80 or (not value and not allow_empty):
        raise ClimateImportViolation(f"{label} is invalid")
    return value


def _number(value: object) -> float | None:
    if type(value) not in {int, float}:
        return None
    number = float(value)
    return number if -50 <= number <= 100 else None


def _timestamp(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ClimateImportViolation(f"{label} must be a non-negative integer")
    return value


def _require_unique(values: object, label: str) -> None:
    items = tuple(values)  # type: ignore[arg-type]
    if len(items) != len(set(items)):
        raise ClimateImportViolation(f"{label} must be unique")
