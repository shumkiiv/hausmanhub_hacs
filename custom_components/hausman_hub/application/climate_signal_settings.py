"""Strict validation for the admin climate signal and mode settings.

This pure boundary validates the exact payloads of the local admin routes for
the climate control mode, the home environment signals, and the per-room
window and presence bindings. It never touches Home Assistant, storage, or
commands: the caller supplies an ``entity_known`` lookup, and the results are
plain values for the runtime's atomic write methods.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from math import isfinite

from ..domain.climate import MAX_ROOM_PRESENCE_ENTITIES

OUTDOOR_TEMPERATURE_DOMAINS = frozenset({"sensor", "weather"})
PRESENCE_DOMAINS = frozenset({"binary_sensor", "person", "device_tracker"})
CENTRAL_HEATING_DOMAINS = frozenset({"binary_sensor", "switch", "input_boolean"})
WINDOW_DOMAINS = frozenset({"binary_sensor"})
ROOM_PRESENCE_DOMAINS = frozenset({"binary_sensor"})
OUTDOOR_TEMPERATURE_SIGNAL = "outdoor_temperature"
PRESENCE_SIGNAL = "presence"
CENTRAL_HEATING_SIGNAL = "central_heating"
WINDOW_SIGNAL = "window"
ROOM_PRESENCE_SIGNAL = "room_presence"
SIGNAL_KINDS = frozenset(
    {
        OUTDOOR_TEMPERATURE_SIGNAL,
        PRESENCE_SIGNAL,
        CENTRAL_HEATING_SIGNAL,
        WINDOW_SIGNAL,
        ROOM_PRESENCE_SIGNAL,
    }
)
SIGNAL_DOMAINS_BY_KIND = {
    OUTDOOR_TEMPERATURE_SIGNAL: OUTDOOR_TEMPERATURE_DOMAINS,
    PRESENCE_SIGNAL: PRESENCE_DOMAINS,
    CENTRAL_HEATING_SIGNAL: CENTRAL_HEATING_DOMAINS,
    WINDOW_SIGNAL: WINDOW_DOMAINS,
    ROOM_PRESENCE_SIGNAL: ROOM_PRESENCE_DOMAINS,
}
PRESENCE_DEVICE_CLASSES = frozenset({"motion", "occupancy", "presence"})
WINDOW_DEVICE_CLASSES = frozenset({"window", "door", "opening", "garage_door"})
CENTRAL_HEATING_DEVICE_CLASSES = frozenset({"heat", "running", "power"})
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
ROOM_SIGNAL_FIELDS = frozenset(
    {"room_id", "window_entity_id", "presence_entity_ids"}
)
ROOM_SIGNAL_BATCH_FIELDS = frozenset({"rooms"})
MAX_ROOM_SIGNAL_UPDATES = 128
CLIMATE_MODE_FIELDS = frozenset({"mode", "expected_mode", "confirm"})


class ClimateSignalSettingsViolation(ValueError):
    """One bounded rejection of an admin signal or mode settings payload."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def signal_candidate_is_suitable(
    signal_kind: str,
    *,
    domain: str,
    device_class: str | None,
    entity_category: str | None,
    attributes: Mapping[str, object],
) -> bool:
    """Return whether one HA entity is semantically suitable for a binding."""

    if signal_kind not in SIGNAL_KINDS or entity_category == "diagnostic":
        return False
    if signal_kind == OUTDOOR_TEMPERATURE_SIGNAL:
        if domain == "sensor":
            return device_class == "temperature"
        return (
            domain == "weather"
            and _finite_signal_number(attributes.get("temperature")) is not None
        )
    if signal_kind == PRESENCE_SIGNAL:
        return domain in {"person", "device_tracker"} or (
            domain == "binary_sensor"
            and device_class in PRESENCE_DEVICE_CLASSES
        )
    if signal_kind == CENTRAL_HEATING_SIGNAL:
        return domain in {"switch", "input_boolean"} or (
            domain == "binary_sensor"
            and device_class in CENTRAL_HEATING_DEVICE_CLASSES
        )
    if signal_kind == WINDOW_SIGNAL:
        return domain == "binary_sensor" and device_class in WINDOW_DEVICE_CLASSES
    return (
        signal_kind == ROOM_PRESENCE_SIGNAL
        and domain == "binary_sensor"
        and device_class in PRESENCE_DEVICE_CLASSES
    )


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


def validate_room_signal_update(
    payload: object,
    *,
    room_ids: frozenset[str],
    entity_known: Callable[[str], bool],
) -> tuple[str, str | None, tuple[str, ...]]:
    """Validate one room's complete window and presence binding replacement."""

    if not isinstance(payload, Mapping) or set(payload.keys()) != set(
        ROOM_SIGNAL_FIELDS
    ):
        raise ClimateSignalSettingsViolation("invalid_room_signals")
    room_id = payload["room_id"]
    if not isinstance(room_id, str) or not room_id or len(room_id) > 64:
        raise ClimateSignalSettingsViolation("invalid_room")
    if room_id not in room_ids:
        raise ClimateSignalSettingsViolation("unknown_room")
    window_entity_id = validate_optional_signal_entity(
        payload["window_entity_id"],
        allowed_domains=WINDOW_DOMAINS,
        entity_known=entity_known,
    )
    raw_presence = payload["presence_entity_ids"]
    if (
        not isinstance(raw_presence, list)
        or len(raw_presence) > MAX_ROOM_PRESENCE_ENTITIES
    ):
        raise ClimateSignalSettingsViolation("invalid_room_presence")
    presence_entity_ids = tuple(
        validate_optional_signal_entity(
            entity_id,
            allowed_domains=ROOM_PRESENCE_DOMAINS,
            entity_known=entity_known,
        )
        for entity_id in raw_presence
    )
    if any(entity_id is None for entity_id in presence_entity_ids):
        raise ClimateSignalSettingsViolation("invalid_room_presence")
    normalized = tuple(
        entity_id
        for entity_id in presence_entity_ids
        if entity_id is not None
    )
    if len(set(normalized)) != len(normalized):
        raise ClimateSignalSettingsViolation("duplicate_room_presence")
    return room_id, window_entity_id, normalized


def validate_room_signal_updates(
    payload: object,
    *,
    room_ids: frozenset[str],
    entity_known: Callable[[str], bool],
) -> tuple[tuple[str, str | None, tuple[str, ...]], ...]:
    """Validate one bounded atomic batch of complete room signal replacements."""

    if not isinstance(payload, Mapping) or set(payload.keys()) != set(
        ROOM_SIGNAL_BATCH_FIELDS
    ):
        raise ClimateSignalSettingsViolation("invalid_room_signal_batch")
    rooms = payload["rooms"]
    if (
        not isinstance(rooms, list)
        or not rooms
        or len(rooms) > MAX_ROOM_SIGNAL_UPDATES
    ):
        raise ClimateSignalSettingsViolation("invalid_room_signal_batch")
    updates = tuple(
        validate_room_signal_update(
            room,
            room_ids=room_ids,
            entity_known=entity_known,
        )
        for room in rooms
    )
    updated_room_ids = tuple(update[0] for update in updates)
    if len(set(updated_room_ids)) != len(updated_room_ids):
        raise ClimateSignalSettingsViolation("duplicate_room_update")
    presence_entity_ids = tuple(
        entity_id
        for _, _, room_presence_entity_ids in updates
        for entity_id in room_presence_entity_ids
    )
    if len(set(presence_entity_ids)) != len(presence_entity_ids):
        raise ClimateSignalSettingsViolation("duplicate_room_presence")
    return updates


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


def _finite_signal_number(value: object) -> float | None:
    """Parse one finite non-boolean numeric signal attribute."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        result = float(value)
    except OverflowError:
        return None
    return result if isfinite(result) else None
