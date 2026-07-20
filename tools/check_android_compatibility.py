#!/usr/bin/env python3
"""Decode the current HASC fixture into Android-compatible climate models.

The check is deliberately self-contained. It reads only committed HASC schemas
and synthetic fixtures; the read-only Android repository is not required in CI.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
HOME_FIXTURE = ROOT / "fixtures" / "hasc_climate_v12" / "home.json"
HOME_SCHEMA = (
    ROOT
    / "custom_components"
    / "hausman_hub"
    / "contracts"
    / "v12"
    / "climate-home.schema.json"
)
ACTION_SCHEMA = (
    ROOT
    / "custom_components"
    / "hausman_hub"
    / "contracts"
    / "v1"
    / "climate-action-request.schema.json"
)
ANDROID_LONG_MAXIMUM = 9_223_372_036_854_775_807
JSON_SAFE_INTEGER_MAXIMUM = 9_007_199_254_740_991

_ANDROID_DOMAIN_BY_KIND = {
    "air_conditioner": "climate",
    "radiator_thermostat": "climate",
    "humidifier": "humidifier",
    "floor_heating": "climate",
    "temperature_sensor": "sensor",
    "humidity_sensor": "sensor",
}


class AndroidCompatibilityError(ValueError):
    """The HASC fixture cannot be represented by the audited Android models."""


@dataclass(frozen=True, slots=True)
class AndroidActionModel:
    """HomeAction-compatible fields plus HASC availability metadata."""

    action_id: str
    title: str
    description: str
    confirmation_required: bool
    allowed: bool
    blocked_reason_labels: tuple[str, ...]
    request: dict[str, object]


@dataclass(frozen=True, slots=True)
class AndroidDeviceModel:
    """Fields that map losslessly to the existing Android HomeDevice model."""

    device_id: str
    name: str
    room_id: str
    domain: str
    state: str
    unavailable: bool


@dataclass(frozen=True, slots=True)
class AndroidRoomModel:
    """Fields that map losslessly to the existing Android HomeRoom model."""

    room_id: str
    name: str
    temperature: float | None
    humidity: float | None
    target_temperature: float | None
    device_ids: tuple[str, ...]
    actions: tuple[AndroidActionModel, ...]


@dataclass(frozen=True, slots=True)
class AndroidHomeModel:
    """Synthetic HASC view using Kotlin-compatible scalar and collection types."""

    generated_at: int
    state_revision: int
    rooms: tuple[AndroidRoomModel, ...]
    devices: tuple[AndroidDeviceModel, ...]


def check_android_payload(
    payload: object,
    *,
    home_schema: object,
    action_schema: object,
) -> AndroidHomeModel:
    """Validate and decode one HASC home payload into audited Android types."""

    _validate_schema(payload, home_schema, "HASC home")
    root = _object(payload, "HASC home")
    contract = _object(root.get("contract"), "HASC home contract")
    if contract.get("name") != "hausman-hasc-home" or contract.get("version") != 12:
        raise AndroidCompatibilityError("Android check needs HASC home contract v12")

    generated_at = _android_long(root.get("generated_at"), "generated_at")
    state_revision = _android_long(root.get("state_revision"), "state_revision")
    if state_revision > JSON_SAFE_INTEGER_MAXIMUM:
        raise AndroidCompatibilityError("state_revision exceeds exact JSON number range")

    schema_device_kinds = _schema_device_kinds(home_schema)
    if schema_device_kinds != set(_ANDROID_DOMAIN_BY_KIND):
        raise AndroidCompatibilityError(
            "HASC device kinds and Android domain mappings are inconsistent"
        )

    display_names = _object(root.get("display_names"), "display_names")
    blocked_reason_names = _object(
        display_names.get("blocked_reasons"),
        "blocked reason names",
    )
    action_validator = _validator(action_schema, "climate action request")

    rooms: list[AndroidRoomModel] = []
    devices: list[AndroidDeviceModel] = []
    for room_index, room_value in enumerate(_array(root.get("rooms"), "rooms")):
        room = _object(room_value, f"room {room_index}")
        room_id = _text(room.get("id"), f"room {room_index} id")
        room_name = _text(room.get("name"), f"room {room_index} name")
        actual = _object(room.get("actual"), f"room {room_id} actual")
        active_target = _object(
            room.get("active_target"),
            f"room {room_id} active target",
        )
        control = _object(room.get("control"), f"room {room_id} control")
        action_codes = tuple(
            _text(value, f"room {room_id} action")
            for value in _array(control.get("actions"), f"room {room_id} actions")
        )
        allowed_actions = {
            _text(value, f"room {room_id} allowed action")
            for value in _array(
                control.get("allowed_actions"),
                f"room {room_id} allowed actions",
            )
        }
        availability = _object(
            control.get("action_availability"),
            f"room {room_id} action availability",
        )
        presentations = _object(
            control.get("action_presentations"),
            f"room {room_id} action presentations",
        )
        inputs = _object(
            control.get("action_inputs"),
            f"room {room_id} action inputs",
        )

        actions: list[AndroidActionModel] = []
        for action_index, action_code in enumerate(action_codes):
            status = _object(
                availability.get(action_code),
                f"room {room_id} action {action_code} status",
            )
            allowed = _boolean(
                status.get("allowed"),
                f"room {room_id} action {action_code} permission",
            )
            if allowed != (action_code in allowed_actions):
                raise AndroidCompatibilityError(
                    f"room {room_id} action {action_code} permission is inconsistent"
                )
            reason_codes = tuple(
                _text(value, f"room {room_id} action {action_code} reason")
                for value in _array(
                    status.get("blocked_reasons"),
                    f"room {room_id} action {action_code} reasons",
                )
            )
            reason_labels = tuple(
                _text(
                    blocked_reason_names.get(code),
                    f"Russian label for reason {code}",
                )
                for code in reason_codes
            )
            presentation = _object(
                presentations.get(action_code),
                f"room {room_id} action {action_code} presentation",
            )
            request = _android_action_request(
                room_id,
                action_code,
                action_index,
                inputs,
            )
            _validate_with(
                request,
                action_validator,
                f"room {room_id} action {action_code} request",
            )
            actions.append(
                AndroidActionModel(
                    action_id=action_code,
                    title=_text(
                        presentation.get("title"),
                        f"room {room_id} action {action_code} title",
                    ),
                    description=_text(
                        presentation.get("description"),
                        f"room {room_id} action {action_code} description",
                    ),
                    confirmation_required=_boolean(
                        presentation.get("confirmation_required"),
                        f"room {room_id} action {action_code} confirmation",
                    ),
                    allowed=allowed,
                    blocked_reason_labels=reason_labels,
                    request=request,
                )
            )

        room_devices: list[str] = []
        for device_index, device_value in enumerate(
            _array(room.get("devices"), f"room {room_id} devices")
        ):
            device = _object(device_value, f"room {room_id} device {device_index}")
            kind = _text(device.get("kind"), f"room {room_id} device kind")
            domain = _ANDROID_DOMAIN_BY_KIND.get(kind)
            if domain is None:
                raise AndroidCompatibilityError(
                    f"room {room_id} device kind {kind} has no Android domain"
                )
            device_id = _text(device.get("id"), f"room {room_id} device id")
            room_devices.append(device_id)
            devices.append(
                AndroidDeviceModel(
                    device_id=device_id,
                    name=_text(device.get("name"), f"device {device_id} name"),
                    room_id=room_id,
                    domain=domain,
                    state=_text(device.get("state"), f"device {device_id} state"),
                    unavailable=not _boolean(
                        device.get("available"),
                        f"device {device_id} availability",
                    ),
                )
            )

        rooms.append(
            AndroidRoomModel(
                room_id=room_id,
                name=room_name,
                temperature=_nullable_double(
                    actual.get("temperature"),
                    f"room {room_id} temperature",
                ),
                humidity=_nullable_double(
                    actual.get("humidity"),
                    f"room {room_id} humidity",
                ),
                target_temperature=_nullable_double(
                    active_target.get("temperature"),
                    f"room {room_id} target temperature",
                ),
                device_ids=tuple(room_devices),
                actions=tuple(actions),
            )
        )

    return AndroidHomeModel(
        generated_at=generated_at,
        state_revision=state_revision,
        rooms=tuple(rooms),
        devices=tuple(devices),
    )


def check_repository_fixture() -> AndroidHomeModel:
    """Run the Android model check against the current committed example."""

    return check_android_payload(
        _load_json(HOME_FIXTURE),
        home_schema=_load_json(HOME_SCHEMA),
        action_schema=_load_json(ACTION_SCHEMA),
    )


def _android_action_request(
    room_id: str,
    action: str,
    action_index: int,
    inputs: dict[str, Any],
) -> dict[str, object]:
    request: dict[str, object] = {
        "request_id": f"android-check-{action_index + 1}",
        "action": action,
        "room_id": room_id,
    }
    if action == "set_room_target":
        action_inputs = _object(inputs.get(action), f"action {action} inputs")
        temperature = _object(
            action_inputs.get("target_temperature"),
            "target temperature input",
        )
        request["target_temperature"] = _double(
            temperature.get("minimum"),
            "target temperature minimum",
        )
    elif action != "turn_room_off":
        raise AndroidCompatibilityError(f"Android action {action} is unsupported")
    return request


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _validator(schema: object, label: str) -> Draft202012Validator:
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _validate_schema(instance: object, schema: object, label: str) -> None:
    _validate_with(instance, _validator(schema, label), label)


def _validate_with(
    instance: object,
    validator: Draft202012Validator,
    label: str,
) -> None:
    error = next(iter(validator.iter_errors(instance)), None)
    if error is not None:
        raise AndroidCompatibilityError(f"{label} does not match its schema")


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise AndroidCompatibilityError(f"{label} must be a JSON object")
    return value


def _array(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise AndroidCompatibilityError(f"{label} must be a JSON array")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AndroidCompatibilityError(f"{label} must be non-empty text")
    return value


def _boolean(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise AndroidCompatibilityError(f"{label} must be boolean")
    return value


def _android_long(value: object, label: str) -> int:
    if type(value) is not int or not 0 <= value <= ANDROID_LONG_MAXIMUM:
        raise AndroidCompatibilityError(f"{label} does not fit Android Long")
    return value


def _double(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AndroidCompatibilityError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise AndroidCompatibilityError(f"{label} must be finite")
    return result


def _nullable_double(value: object, label: str) -> float | None:
    return None if value is None else _double(value, label)


def _schema_device_kinds(schema: object) -> set[str]:
    definitions = _object(_object(schema, "home schema").get("$defs"), "$defs")
    device = _object(definitions.get("device"), "device schema")
    properties = _object(device.get("properties"), "device properties")
    kind = _object(properties.get("kind"), "device kind schema")
    return {
        _text(value, "device kind code")
        for value in _array(kind.get("enum"), "device kind codes")
    }


def main() -> int:
    """Run the fixed local Android compatibility check."""

    try:
        model = check_repository_fixture()
    except (AndroidCompatibilityError, OSError, json.JSONDecodeError) as error:
        print(f"Android compatibility check failed: {error}")
        return 1
    action_count = sum(len(room.actions) for room in model.rooms)
    print(
        "Android compatibility check passed for "
        f"{len(model.rooms)} room(s), {len(model.devices)} device(s), and "
        f"{action_count} action model(s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
