"""Strict HTTP protocol helpers for physical device commands."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from typing import Any

MAX_DEVICE_ACTION_BODY_BYTES = 256 * 1024
LEGACY_REQUEST_MEDIA_TYPE = "application/json"
FULL_SINGLE_REQUEST_MEDIA_TYPE = (
    "application/vnd.hausmanhub.device-action-request.full+json"
)
FULL_SINGLE_RESPONSE_MEDIA_TYPE = (
    "application/vnd.hausmanhub.device-action-receipt.full+json"
)
FULL_BATCH_REQUEST_MEDIA_TYPE = (
    "application/vnd.hausmanhub.device-action-batch-request.full+json"
)
FULL_BATCH_RESPONSE_MEDIA_TYPE = (
    "application/vnd.hausmanhub.device-action-batch-receipt.full+json"
)
DANGEROUS_ACTION_IDS = frozenset({"press", "unlock", "open_valve"})
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_VALUELESS_ACTIONS = frozenset(
    {
        "press", "turn_on", "turn_off", "open_cover", "close_cover", "toggle",
        "lock", "unlock", "media_play", "media_pause", "start", "pause", "stop",
        "return_home", "open_valve", "close_valve",
    }
)


class StrictJsonError(ValueError):
    """The request body is not bounded strict JSON."""


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise StrictJsonError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_non_finite(value: object) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise StrictJsonError("non-finite JSON number")
    if isinstance(value, Mapping):
        for nested in value.values():
            _reject_non_finite(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_non_finite(nested)


async def strict_request_json(
    request: Any,
    *,
    allowed_media_types: frozenset[str],
) -> object:
    """Read bounded raw bytes before parsing and reject ambiguous JSON."""

    length = getattr(request, "content_length", None)
    if type(length) is not int or not 0 < length <= MAX_DEVICE_ACTION_BODY_BYTES:
        raise StrictJsonError("request body size is invalid")
    content_type = str(getattr(request, "content_type", "")).casefold()
    if content_type not in allowed_media_types:
        raise StrictJsonError("request body media type is invalid")

    raw_reader = getattr(request, "read", None)
    if callable(raw_reader):
        raw = await raw_reader()
        if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_DEVICE_ACTION_BODY_BYTES:
            raise StrictJsonError("request body size is invalid")
        try:
            decoded = raw.decode("utf-8")
            payload = json.loads(
                decoded,
                object_pairs_hook=_unique_object,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    StrictJsonError(f"non-finite JSON number: {value}")
                ),
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise StrictJsonError("request body is invalid JSON") from error
    else:
        try:
            payload = await request.json()
        except (TypeError, ValueError) as error:
            raise StrictJsonError("request body is invalid JSON") from error
    _reject_non_finite(payload)
    return payload


def request_is_full(request: Any, *, batch: bool) -> bool:
    expected = FULL_BATCH_REQUEST_MEDIA_TYPE if batch else FULL_SINGLE_REQUEST_MEDIA_TYPE
    return str(getattr(request, "content_type", "")).casefold() == expected


def negotiated_response_media_type(request: Any, *, batch: bool) -> str | None:
    """Select legacy or full response using a small standard Accept parser."""

    full = FULL_BATCH_RESPONSE_MEDIA_TYPE if batch else FULL_SINGLE_RESPONSE_MEDIA_TYPE
    headers = getattr(request, "headers", None)
    accept = headers.get("Accept") if isinstance(headers, Mapping) else None
    if not isinstance(accept, str) or not accept.strip():
        return LEGACY_REQUEST_MEDIA_TYPE

    legacy_quality = -1.0
    full_quality = -1.0
    for part in accept.split(","):
        tokens = [token.strip() for token in part.split(";")]
        media_type = tokens[0].casefold()
        quality = 1.0
        for token in tokens[1:]:
            if token.casefold().startswith("q="):
                try:
                    quality = float(token[2:])
                except ValueError:
                    quality = 0.0
        if not 0.0 <= quality <= 1.0:
            quality = 0.0
        if media_type == full:
            full_quality = max(full_quality, quality)
        if media_type in {LEGACY_REQUEST_MEDIA_TYPE, "application/*", "*/*"}:
            legacy_quality = max(legacy_quality, quality)
    if full_quality > 0 and full_quality > legacy_quality:
        return full
    if legacy_quality > 0:
        return LEGACY_REQUEST_MEDIA_TYPE
    if full_quality > 0:
        return full
    return None


def canonical_request_fingerprint(payload: Mapping[str, object]) -> str:
    """Hash the complete immutable request with deterministic JSON encoding."""

    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    import hashlib

    return hashlib.sha256(encoded).hexdigest()


def validate_single_request(payload: object, *, full: bool) -> Mapping[str, object]:
    """Validate the complete legacy or full single-action envelope."""

    if not isinstance(payload, Mapping):
        raise StrictJsonError("device action body must be an object")
    allowed = {
        "contract", "correlationId", "targetId", "actionId", "value",
        "confirmedByUser", "dryRun",
    }
    if full:
        allowed |= {
            "idempotencyKey", "reassertKey", "expectedEvidenceRevision",
            "expectedEvidenceSequence",
            "requestId",
        }
    if not set(payload).issubset(allowed):
        raise StrictJsonError("device action body contains unknown fields")
    contract = payload.get("contract")
    if contract is not None and contract != {
        "name": "hausman-hub-device-action-request",
        "version": 1,
    }:
        raise StrictJsonError("device action contract is invalid")
    _validate_action(payload, full=full)
    correlation_id = payload.get("correlationId")
    if correlation_id is not None and (
        not isinstance(correlation_id, str)
        or _IDENTIFIER.fullmatch(correlation_id) is None
    ):
        raise StrictJsonError("correlationId is invalid")
    if full and payload.get("dryRun") is not True and "requestId" not in payload:
        raise StrictJsonError("requestId is required for a physical full action")
    if full and "requestId" in payload and (
        not isinstance(payload["requestId"], str)
        or _IDENTIFIER.fullmatch(payload["requestId"]) is None
    ):
        raise StrictJsonError("requestId is invalid")
    return payload


def validate_batch_request(payload: object, *, full: bool) -> list[Mapping[str, object]]:
    """Validate all batch items before the first physical dispatch."""

    required_fields = {"contract", "correlationId", "actions"}
    allowed_fields = required_fields | ({"requestId"} if full else set())
    if (
        not isinstance(payload, Mapping)
        or not required_fields.issubset(payload)
        or not set(payload).issubset(allowed_fields)
    ):
        raise StrictJsonError("device action batch body is invalid")
    if payload.get("contract") != {
        "name": "hausman-hub-device-action-batch-request",
        "version": 1,
    }:
        raise StrictJsonError("device action batch contract is invalid")
    correlation_id = payload.get("correlationId")
    if not isinstance(correlation_id, str) or _IDENTIFIER.fullmatch(correlation_id) is None:
        raise StrictJsonError("correlationId is invalid")
    if full and "requestId" in payload and (
        not isinstance(payload["requestId"], str)
        or _IDENTIFIER.fullmatch(payload["requestId"]) is None
    ):
        raise StrictJsonError("requestId is invalid")
    actions = payload.get("actions")
    if not isinstance(actions, list) or not 1 <= len(actions) <= 64:
        raise StrictJsonError("actions must contain 1 to 64 items")
    normalized: list[Mapping[str, object]] = []
    action_keys: set[tuple[str, str]] = set()
    dangerous = 0
    for item in actions:
        if not isinstance(item, Mapping):
            raise StrictJsonError("device action batch item is invalid")
        allowed = {"targetId", "actionId", "value"}
        if full:
            allowed |= {
                "dryRun", "confirmedByUser", "idempotencyKey", "reassertKey",
                "expectedEvidenceRevision", "expectedEvidenceSequence",
            }
        if not set(item).issubset(allowed):
            raise StrictJsonError("device action batch item contains unknown fields")
        _validate_action(item, full=full)
        action_key = (str(item["targetId"]), str(item["actionId"]))
        if action_key in action_keys:
            raise StrictJsonError("target and action may appear only once in a batch")
        action_keys.add(action_key)
        if item["actionId"] in DANGEROUS_ACTION_IDS and item.get("dryRun") is not True:
            dangerous += 1
        normalized.append(item)
    if full and any(item.get("dryRun") is not True for item in normalized):
        if "requestId" not in payload:
            raise StrictJsonError("requestId is required for a physical full batch")
    if full and dangerous > 1:
        raise StrictJsonError("a full batch may contain one dangerous physical action")
    return normalized


def _validate_action(payload: Mapping[str, object], *, full: bool) -> None:
    target_id = payload.get("targetId")
    action_id = payload.get("actionId")
    if not isinstance(target_id, str) or not 1 <= len(target_id) <= 128:
        raise StrictJsonError("targetId is invalid")
    if not isinstance(action_id, str) or not 1 <= len(action_id) <= 128:
        raise StrictJsonError("actionId is invalid")
    if full and action_id == "toggle":
        raise StrictJsonError("toggle is not supported by the full physical API")
    for name in ("confirmedByUser", "dryRun"):
        if name in payload and type(payload[name]) is not bool:
            raise StrictJsonError(f"{name} must be boolean")
    if action_id in _VALUELESS_ACTIONS and "value" in payload:
        raise StrictJsonError("action does not accept value")
    if "value" in payload:
        _validate_action_value(payload["value"], depth=0)
        if not full and isinstance(payload["value"], (list, Mapping)):
            raise StrictJsonError("legacy action value must be scalar")
    if full:
        _validate_action_specific_value(action_id, payload)
    if not full:
        return
    reassert_key = payload.get("reassertKey")
    evidence_revision = payload.get("expectedEvidenceRevision")
    evidence_sequence = payload.get("expectedEvidenceSequence")
    if any(value is not None for value in (reassert_key, evidence_revision, evidence_sequence)):
        if (
            action_id != "turn_on"
            or payload.get("dryRun") is True
            or not isinstance(reassert_key, str)
            or _IDEMPOTENCY_KEY.fullmatch(reassert_key) is None
            or not isinstance(evidence_revision, str)
            or _IDENTIFIER.fullmatch(evidence_revision) is None
            or type(evidence_sequence) is not int
            or evidence_sequence < 0
        ):
            raise StrictJsonError("reassert evidence identity is invalid")
    if action_id in DANGEROUS_ACTION_IDS and payload.get("dryRun") is not True:
        key = payload.get("idempotencyKey")
        if (
            payload.get("confirmedByUser") is not True
            or not isinstance(key, str)
            or _IDEMPOTENCY_KEY.fullmatch(key) is None
        ):
            raise StrictJsonError("dangerous action confirmation is invalid")


def _validate_action_value(value: object, *, depth: int) -> None:
    if value is None or type(value) is bool:
        return
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(float(value)) or not -1_000_000 <= value <= 1_000_000:
            raise StrictJsonError("action value number is invalid")
        return
    if isinstance(value, str):
        if len(value) > 256:
            raise StrictJsonError("action value string is too long")
        return
    if depth >= 3:
        raise StrictJsonError("action value nesting is too deep")
    if isinstance(value, list):
        if len(value) > 32:
            raise StrictJsonError("action value array is too large")
        for nested in value:
            _validate_action_value(nested, depth=depth + 1)
        return
    if isinstance(value, Mapping):
        if len(value) > 32 or any(
            not isinstance(key, str) or len(key) > 256 for key in value
        ):
            raise StrictJsonError("action value object is too large")
        for nested in value.values():
            _validate_action_value(nested, depth=depth + 1)
        return
    raise StrictJsonError("action value type is invalid")


def _validate_action_specific_value(
    action_id: str, payload: Mapping[str, object]
) -> None:
    numeric_ranges = {
        "set_position": (0, 100),
        "set_brightness": (0, 255),
        "set_adaptive_brightness": (1, 100),
        "set_brightness_percent": (0, 100),
        "set_color_temperature": (1000, 10000),
        "set_night_light": (1, 30),
    }
    if action_id in {"set_temperature", "set_humidity", "set_value"}:
        numeric_ranges[action_id] = (-1_000_000, 1_000_000)
    if action_id in numeric_ranges:
        value = payload.get("value")
        minimum, maximum = numeric_ranges[action_id]
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not minimum <= value <= maximum
        ):
            raise StrictJsonError("numeric action value is invalid")
    if action_id in {"set_hvac_mode", "set_fan_mode", "set_operation_mode"}:
        if not isinstance(payload.get("value"), str):
            raise StrictJsonError("string action value is required")
    if action_id == "set_rgb_color":
        value = payload.get("value")
        if not isinstance(value, str) or re.fullmatch(r"#[0-9A-Fa-f]{6}", value) is None:
            raise StrictJsonError("RGB action value is invalid")
