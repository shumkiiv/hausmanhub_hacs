"""Bounded correlation IDs shared by commands, events and notifications."""

from __future__ import annotations

from collections.abc import Mapping
import re
import uuid


CORRELATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class CorrelationIdError(ValueError):
    """Raised when a caller supplies an unsafe correlation ID."""


def new_correlation_id() -> str:
    """Create one opaque server correlation ID."""

    return uuid.uuid4().hex


def validate_correlation_id(value: object) -> str:
    """Return a valid correlation ID or fail closed."""

    if not isinstance(value, str) or CORRELATION_ID_PATTERN.fullmatch(value) is None:
        raise CorrelationIdError("correlation ID is invalid")
    return value


def resolve_correlation_id(
    payload: Mapping[str, object],
    *,
    field: str,
    fallback: object = None,
) -> str:
    """Preserve a caller ID, otherwise use a safe request ID or a new ID."""

    if field in payload:
        return validate_correlation_id(payload[field])
    if isinstance(fallback, str) and CORRELATION_ID_PATTERN.fullmatch(fallback):
        return fallback
    return new_correlation_id()


def receipt_correlation_id(receipt: Mapping[str, object]) -> str:
    """Resolve the canonical ID from a mixed-style public receipt."""

    for field in (
        "correlationId",
        "correlation_id",
        "requestId",
        "request_id",
        "commandId",
        "run_id",
    ):
        value = receipt.get(field)
        if isinstance(value, str) and CORRELATION_ID_PATTERN.fullmatch(value):
            return value
    return new_correlation_id()
