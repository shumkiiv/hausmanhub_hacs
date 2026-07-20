"""Frozen, command-free reference cases from the working climate module.

The source module is intentionally not imported.  HausmanHub owns a redacted
copy of its observable inputs and expected decisions so the native algorithm
can be migrated and compared without reading a live home or sending commands.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
from pathlib import Path
import re
from typing import Final


CLIMATE_REFERENCE_CONTRACT: Final = "hausman-hub-climate-reference-suite"
CLIMATE_REFERENCE_VERSION: Final = 1
CLIMATE_REFERENCE_SHA256: Final = (
    "93dd2717a1043e1e71b36244e6e5c7be5f04afdef8dee44e3dbe82b579fa74c9"
)
CLIMATE_REFERENCE_PATH: Final = (
    Path(__file__).resolve().parents[1]
    / "reference"
    / "v1"
    / "climate-reference-suite.json"
)

REFERENCE_POLICY_PRIORITY: Final = (
    "away",
    "safety_lockout",
    "freshness_guard",
    "forced_auto_only",
    "manual",
    "auto",
    "direct_device_command",
)

REFERENCE_PROTECTION_CODES: Final = frozenset(
    {
        "away",
        "forced_auto_only",
        "window",
        "critical_sensor",
        "cooling_blocked",
        "heating_blocked",
        "central_heating_off",
        "device_unavailable",
        "stale_state",
        "temperature_jump",
        "stale_delayed_command",
        "minimum_off_pause",
        "minimum_run_hold",
        "execution_disabled",
        "morning_approval_required",
        "room_not_allowed",
        "type_not_allowed",
        "entity_not_allowed",
        "hvac_auto_forbidden",
        "invalid_service_command",
        "physical_feedback_unconfirmed",
        "cooldown",
        "duplicate",
        "awaiting_physical_transition",
        "physical_transition_timeout",
        "authority_not_granted",
        "manual_no_automatic_plan",
        "blocked_policy_non_actuating",
        "shadow_plan_not_executed",
        "physical_shutter_edge_driven",
        "redundant_off_suppression",
    }
)

REFERENCE_CATEGORIES: Final = frozenset(
    {
        "cooling",
        "heating",
        "humidity",
        "priority",
        "freshness",
        "timing",
        "availability",
        "execution_guard",
        "limitation",
    }
)

_FORBIDDEN_PRIVATE_KEYS: Final = frozenset(
    {
        "address",
        "device_id",
        "entity_id",
        "service",
        "source_id",
        "token",
    }
)
_ENTITY_VALUE = re.compile(
    r"^(?:binary_sensor|button|climate|humidifier|remote|sensor|switch)\.[a-z0-9_]"
)
_PRIVATE_IPV4 = re.compile(
    r"^(?:127\.|10\.|192\.168\.|172\.(?:1[6-9]|2[0-9]|3[01])\.)"
)


class ClimateReferenceViolation(ValueError):
    """The frozen reference suite is incomplete, changed, or unsafe."""


class DuplicateReferenceKey(ValueError):
    """A repeated JSON key would make the frozen evidence ambiguous."""


def load_climate_reference_suite() -> dict[str, object]:
    """Load and validate the packaged reference suite without side effects."""

    raw = CLIMATE_REFERENCE_PATH.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != CLIMATE_REFERENCE_SHA256:
        raise ClimateReferenceViolation(
            "climate reference suite changed without a new reviewed fingerprint"
        )
    try:
        payload = json.loads(raw, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, UnicodeDecodeError, DuplicateReferenceKey) as error:
        raise ClimateReferenceViolation(
            "climate reference suite must be unambiguous UTF-8 JSON"
        ) from error
    if not isinstance(payload, dict):
        raise ClimateReferenceViolation("climate reference suite must be an object")
    validate_climate_reference_suite(payload)
    return payload


def validate_climate_reference_suite(payload: Mapping[str, object]) -> None:
    """Check migration-specific invariants beyond the published JSON schema."""

    contract = _mapping(payload.get("contract"), "contract")
    if contract != {
        "name": CLIMATE_REFERENCE_CONTRACT,
        "version": CLIMATE_REFERENCE_VERSION,
    }:
        raise ClimateReferenceViolation("climate reference contract is unsupported")

    source = _mapping(payload.get("source"), "source")
    if source.get("read_only") is not True:
        raise ClimateReferenceViolation("reference source must remain read-only")
    source_files = _sequence(source.get("files"), "source.files")
    source_paths = {
        _mapping(item, "source.files item").get("path") for item in source_files
    }
    if None in source_paths or len(source_paths) != len(source_files):
        raise ClimateReferenceViolation("reference source files must be unique")

    execution = _mapping(payload.get("execution"), "execution")
    if execution != {"mode": "reference_only", "commands_enabled": False}:
        raise ClimateReferenceViolation("reference suite must never enable commands")

    priority = tuple(_sequence(payload.get("policy_priority"), "policy_priority"))
    if priority != REFERENCE_POLICY_PRIORITY:
        raise ClimateReferenceViolation("climate policy priority changed")

    protections = _sequence(payload.get("protections"), "protections")
    protection_codes: set[object] = set()
    for item in protections:
        protection = _mapping(item, "protection")
        code = protection.get("code")
        if code in protection_codes:
            raise ClimateReferenceViolation("reference protection codes must be unique")
        protection_codes.add(code)
        if protection.get("source_file") not in source_paths:
            raise ClimateReferenceViolation(
                "every reference protection must name a captured source file"
            )
    if protection_codes != REFERENCE_PROTECTION_CODES:
        raise ClimateReferenceViolation("reference protection coverage changed")

    cases = _sequence(payload.get("cases"), "cases")
    case_ids: set[object] = set()
    categories: set[object] = set()
    for item in cases:
        case = _mapping(item, "case")
        case_id = case.get("id")
        if case_id in case_ids:
            raise ClimateReferenceViolation("reference case ids must be unique")
        case_ids.add(case_id)
        categories.add(case.get("category"))
        source_reference = _mapping(case.get("source_reference"), "source_reference")
        if source_reference.get("file") not in source_paths:
            raise ClimateReferenceViolation(
                "every reference case must name a captured source file"
            )
        expected = _mapping(case.get("expected"), "expected")
        blockers = set(_sequence(expected.get("blockers"), "expected.blockers"))
        active = set(
            _sequence(
                expected.get("active_protections"),
                "expected.active_protections",
            )
        )
        if not blockers <= active:
            raise ClimateReferenceViolation(
                "every blocked reference result must activate the same protection"
            )
        if not active <= protection_codes:
            raise ClimateReferenceViolation(
                "reference case uses an unknown protection code"
            )
        _validate_available_device_intents(case)

    if categories != REFERENCE_CATEGORIES:
        raise ClimateReferenceViolation("reference scenario coverage changed")
    _reject_private_runtime_values(payload)


def _validate_available_device_intents(case: Mapping[str, object]) -> None:
    input_payload = _mapping(case.get("input"), "input")
    available = set(
        _sequence(input_payload.get("available_devices"), "available_devices")
    )
    expected = _mapping(case.get("expected"), "expected")
    intents = set(_sequence(expected.get("planned_intents"), "planned_intents"))
    requirements = {
        "air_conditioner": {
            "start_air_conditioner",
            "set_air_conditioner_temperature",
            "set_air_conditioner_fan",
            "stop_air_conditioner",
        },
        "humidifier": {"start_humidifier", "stop_humidifier"},
        "radiator_thermostat": {"set_radiator_temperature"},
    }
    for device_kind, device_intents in requirements.items():
        if intents & device_intents and device_kind not in available:
            raise ClimateReferenceViolation(
                "reference intent requires an explicitly available device"
            )


def _reject_private_runtime_values(value: object) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in _FORBIDDEN_PRIVATE_KEYS:
                raise ClimateReferenceViolation(
                    "reference suite must not contain private runtime keys"
                )
            _reject_private_runtime_values(item)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _reject_private_runtime_values(item)
        return
    if isinstance(value, str) and (
        _ENTITY_VALUE.match(value) is not None or _PRIVATE_IPV4.match(value) is not None
    ):
        raise ClimateReferenceViolation(
            "reference suite must not contain entity ids or private addresses"
        )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateReferenceKey(key)
        result[key] = value
    return result


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ClimateReferenceViolation(f"{name} must be an object")
    return value


def _sequence(value: object, name: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ClimateReferenceViolation(f"{name} must be an array")
    return value
