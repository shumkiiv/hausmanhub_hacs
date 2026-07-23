"""Strict validation for the admin climate signal and mode settings.

This pure boundary validates the exact payloads of the local admin routes for
the climate control mode, the home environment signals, and the per-room
window binding. It never touches Home Assistant, storage, or commands: the
caller supplies an ``entity_known`` lookup, and the results are plain values
for the runtime's atomic write methods.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from math import isfinite

OUTDOOR_TEMPERATURE_DOMAINS = frozenset({"sensor"})
PRESENCE_DOMAINS = frozenset({"binary_sensor", "person", "device_tracker"})
CENTRAL_HEATING_DOMAINS = frozenset({"binary_sensor", "switch", "input_boolean"})
WINDOW_DOMAINS = frozenset({"binary_sensor"})
HEATING_LOCKOUT_MINIMUM = -40.0
HEATING_LOCKOUT_MAXIMUM = 60.0
CLIMATE_MODES = frozenset({"disabled", "managed"})
MAX_ENTITY_ID_LENGTH = 255

HOME_ENVIRONMENT_FIELDS = frozenset(
    {
        "outdoor_temperature_entity_id",
        "presence_entity_id",
        "central_heating_entity_id",
        "heating_lockout_high",
        "heating_lockout_low",
    }
)
ROOM_WINDOW_FIELDS = frozenset({"room_id", "window_entity_id"})
CLIMATE_MODE_FIELDS = frozenset({"mode", "expected_mode", "confirm"})


class ClimateSignalSettingsViolation(ValueError):
    """One bounded rejection of an admin signal or mode settings payload."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def validate_optional_signal_entity(
    value: object,
    *,
    allowed_domains: frozenset[str],
    entity_known: Callable[[str], bool],
) -> str | None:
    """Accept one optional entity id only when it exists and fits the domains."""

    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > MAX_ENTITY_ID_LENGTH:
        raise ClimateSignalSettingsViolation("invalid_entity")
    domain, separator, object_id = value.partition(".")
    if not separator or not domain or not object_id:
        raise ClimateSignalSettingsViolation("invalid_entity")
    if domain not in allowed_domains:
        raise ClimateSignalSettingsViolation("unsupported_entity_domain")
    if not entity_known(value):
        raise ClimateSignalSettingsViolation("unknown_entity")
    return value


def validate_home_environment_update(
    payload: object,
    *,
    entity_known: Callable[[str], bool],
) -> dict[str, object]:
    """Validate the exact home environment block for the atomic registry write."""

    if not isinstance(payload, Mapping) or set(payload.keys()) != set(
        HOME_ENVIRONMENT_FIELDS
    ):
        raise ClimateSignalSettingsViolation("invalid_home_environment")
    outdoor = validate_optional_signal_entity(
        payload["outdoor_temperature_entity_id"],
        allowed_domains=OUTDOOR_TEMPERATURE_DOMAINS,
        entity_known=entity_known,
    )
    presence = validate_optional_signal_entity(
        payload["presence_entity_id"],
        allowed_domains=PRESENCE_DOMAINS,
        entity_known=entity_known,
    )
    central_heating = validate_optional_signal_entity(
        payload["central_heating_entity_id"],
        allowed_domains=CENTRAL_HEATING_DOMAINS,
        entity_known=entity_known,
    )
    high = _lockout_threshold(payload["heating_lockout_high"])
    low = _lockout_threshold(payload["heating_lockout_low"])
    if low >= high:
        raise ClimateSignalSettingsViolation("invalid_lockout_order")
    return {
        "outdoor_temperature_entity_id": outdoor,
        "presence_entity_id": presence,
        "central_heating_entity_id": central_heating,
        "heating_lockout_high": high,
        "heating_lockout_low": low,
    }


def validate_room_window_update(
    payload: object,
    *,
    room_ids: frozenset[str],
    entity_known: Callable[[str], bool],
) -> tuple[str, str | None]:
    """Validate one room window binding replacement."""

    if not isinstance(payload, Mapping) or set(payload.keys()) != set(
        ROOM_WINDOW_FIELDS
    ):
        raise ClimateSignalSettingsViolation("invalid_room_window")
    room_id = payload["room_id"]
    if not isinstance(room_id, str) or not room_id or len(room_id) > 64:
        raise ClimateSignalSettingsViolation("invalid_room")
    if room_id not in room_ids:
        raise ClimateSignalSettingsViolation("unknown_room")
    entity_id = validate_optional_signal_entity(
        payload["window_entity_id"],
        allowed_domains=WINDOW_DOMAINS,
        entity_known=entity_known,
    )
    return room_id, entity_id


def validate_climate_mode_update(
    current_mode: str,
    payload: object,
) -> str:
    """Validate one explicit climate control mode transition."""

    if not isinstance(payload, Mapping) or set(payload.keys()) != set(
        CLIMATE_MODE_FIELDS
    ):
        raise ClimateSignalSettingsViolation("invalid_mode_update")
    mode = payload["mode"]
    if not isinstance(mode, str) or mode not in CLIMATE_MODES:
        raise ClimateSignalSettingsViolation("invalid_mode")
    if payload["expected_mode"] != current_mode:
        raise ClimateSignalSettingsViolation("mode_changed")
    if mode == "managed" and payload["confirm"] is not True:
        raise ClimateSignalSettingsViolation("confirmation_required")
    if payload["confirm"] is not None and type(payload["confirm"]) is not bool:
        raise ClimateSignalSettingsViolation("invalid_confirmation")
    return mode


def _lockout_threshold(value: object) -> float:
    """Accept one numeric heating lockout threshold inside the registry range."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ClimateSignalSettingsViolation("invalid_lockout_threshold")
    try:
        result = float(value)
    except OverflowError:
        raise ClimateSignalSettingsViolation("invalid_lockout_threshold") from None
    if not isfinite(result):
        raise ClimateSignalSettingsViolation("invalid_lockout_threshold")
    if not HEATING_LOCKOUT_MINIMUM <= result <= HEATING_LOCKOUT_MAXIMUM:
        raise ClimateSignalSettingsViolation("invalid_lockout_threshold")
    return result
