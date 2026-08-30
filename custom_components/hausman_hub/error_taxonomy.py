"""Canonical safe API error policies pinned from HausmanHub contracts."""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


_TAXONOMY_PATH = Path(__file__).parent / "contracts" / "v1" / "error-taxonomy.json"
_CONTRACT = {"name": "hausman-hub-error-taxonomy", "version": 1}
_FALLBACK_CODE = "internal_error"


@lru_cache(maxsize=1)
def error_policies() -> dict[str, dict[str, Any]]:
    """Load and minimally verify the immutable packaged taxonomy once."""

    payload = json.loads(_TAXONOMY_PATH.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("contract") != _CONTRACT
        or payload.get("apiMajorVersion") != 1
        or not isinstance(payload.get("entries"), list)
    ):
        raise RuntimeError("HausmanHub error taxonomy contract is invalid")
    policies = {
        entry["code"]: entry
        for entry in payload["entries"]
        if isinstance(entry, dict) and isinstance(entry.get("code"), str)
    }
    if len(policies) != len(payload["entries"]) or _FALLBACK_CODE not in policies:
        raise RuntimeError("HausmanHub error taxonomy coverage is invalid")
    return policies


async def async_preload_error_policies(hass: HomeAssistant) -> None:
    """Load the packaged taxonomy outside the Home Assistant event loop."""

    await hass.async_add_executor_job(error_policies)


def error_policy(code: str) -> Mapping[str, Any]:
    """Return one known policy or the non-leaking internal fallback."""

    policies = error_policies()
    return policies.get(code, policies[_FALLBACK_CODE])


def api_error_payload(
    code: str,
    *,
    request_id: str | None = None,
    details: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build one strict API error without exposing caller-supplied text."""

    policy = error_policy(code)
    canonical_code = str(policy["code"])
    payload: dict[str, object] = {
        "contract": {"name": "hausman-hub-error", "version": 1},
        "code": canonical_code,
        "message": str(policy["safeMessage"]),
        "retryable": bool(policy["retryable"]),
    }
    if isinstance(request_id, str) and 0 < len(request_id) <= 128:
        payload["requestId"] = request_id
    if details:
        allowed = set(policy["allowedDetailKeys"])
        sanitized = {key: value for key, value in details.items() if key in allowed}
        if sanitized:
            payload["details"] = sanitized
    return payload


def api_error_status(code: str) -> int:
    """Return the canonical HTTP status, including for an unknown code."""

    return int(error_policy(code)["httpStatus"])
