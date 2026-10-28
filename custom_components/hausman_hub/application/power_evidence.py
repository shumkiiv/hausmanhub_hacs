"""Shared evidence rules for an upstream power source.

The scenario executor and the room lighting runtime both need the same answer
to one question: is this relay provably on *right now*? A state restored from
storage, a cached or assumed value, or a report older than the freshness window
is not proof. Keeping the rules in one module prevents the two dispatch paths
from drifting apart.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone

from .lighting_observation import ObservedLightState, physical_observed_ms

UNAVAILABLE_EVIDENCE = frozenset({"unknown", "unavailable"})
POWER_EVIDENCE_FRESH_SECONDS = 300
TARGET_STATE_FRESHNESS_MS = POWER_EVIDENCE_FRESH_SECONDS * 1000


def trusted_power_state(
    state: object | None,
    *,
    same_state_confirmation: bool = False,
    now_ms: int | None = None,
) -> str | None:
    """Return a power-source state only when its HA evidence is trustworthy.

    ``same_state_confirmation`` additionally accepts a fresh same-state report
    (``last_reported``/``last_updated``), which is how a relay answers an
    idempotent ``turn_on`` without moving ``last_changed``.
    """

    if isinstance(state, ObservedLightState):
        return trusted_target_state(
            state, now_ms=(now_ms if now_ms is not None
                           else int(datetime.now(timezone.utc).timestamp() * 1000))
        )
    if (
        state is None
        or not power_state_is_fresh(
            state, same_state_confirmation=same_state_confirmation
        )
        or state_is_restored_or_cached(state)
    ):
        return None
    value = str(getattr(state, "state", "unknown"))
    return None if value in UNAVAILABLE_EVIDENCE else value


def trusted_target_state(
    state: object | None,
    *,
    now_ms: int,
    freshness_ms: int = TARGET_STATE_FRESHNESS_MS,
) -> str | None:
    """Return ``on``/``off`` only from fresh target evidence.

    A light or switch can stay physically ON for longer than the freshness
    window while Home Assistant still receives same-state reports.  For target
    evidence the fresh report timestamp (``last_reported`` in newer HA,
    ``last_updated`` in older compatible versions) is therefore authoritative;
    a stale ``last_changed`` does not by itself make the stable target unknown.
    Missing, restored/cached, unavailable, stale and future timestamps fail
    closed.
    """

    if state is None or state_is_restored_or_cached(state):
        return None
    value = str(getattr(state, "state", "unknown")).lower()
    if value not in {"on", "off"}:
        return None
    if trusted_target_observed_ms(
        state, now_ms=now_ms, freshness_ms=freshness_ms
    ) is None:
        return None
    return value


def trusted_target_observed_ms(
    state: object | None,
    *,
    now_ms: int,
    freshness_ms: int = TARGET_STATE_FRESHNESS_MS,
) -> int | None:
    """Return the timestamp that made a target state trusted, if any."""

    if state is None:
        return None
    if isinstance(state, ObservedLightState):
        return physical_observed_ms(state, now_ms=now_ms, freshness_ms=freshness_ms)
    for name in ("last_reported", "last_updated", "last_changed"):
        observed = _timestamp_ms(getattr(state, name, None))
        if observed is None:
            continue
        age = now_ms - observed
        if 0 <= age <= freshness_ms:
            return observed
    return None


def _timestamp_ms(value: object) -> int | None:
    if isinstance(value, datetime):
        observed = value
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        return int(observed.astimezone(timezone.utc).timestamp() * 1000)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric = float(value)
        if not (numeric == numeric):
            return None
        return int(numeric if numeric > 1_000_000_000_000 else numeric * 1000)
    return None


def power_state_is_fresh(
    state: object | None,
    *,
    same_state_confirmation: bool = False,
) -> bool:
    """Trust a fresh same-state power report without rewriting manual history.

    ``last_changed`` intentionally remains the ownership boundary for lights.
    A relay that has stayed on for hours can nevertheless publish a new
    ``last_updated`` report after an idempotent ``turn_on``. Power readiness
    uses that observation while restored, cached and assumed states are still
    rejected separately.
    """

    if state is None:
        return False
    if isinstance(state, ObservedLightState):
        return physical_observed_ms(
            state,
            now_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
            freshness_ms=TARGET_STATE_FRESHNESS_MS,
        ) is not None
    observed = (
        getattr(state, "last_reported", None)
        or getattr(state, "last_updated", None)
        if same_state_confirmation
        else getattr(state, "last_changed", None)
    )
    if observed is None:
        observed = getattr(state, "last_updated", None)
    if not isinstance(observed, datetime):
        return True
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    age = (
        datetime.now(timezone.utc) - observed.astimezone(timezone.utc)
    ).total_seconds()
    return 0 <= age <= POWER_EVIDENCE_FRESH_SECONDS


def state_is_restored_or_cached(state: object | None) -> bool:
    """Whether a state came from restore/cache instead of a live report."""

    if state is None:
        return False
    if (
        getattr(state, "restored", False) is True
        or getattr(state, "is_restored", False) is True
        or getattr(state, "assumed_state", False) is True
        or getattr(state, "is_assumed_state", False) is True
    ):
        return True
    attributes = getattr(state, "attributes", {})
    if not isinstance(attributes, Mapping):
        return False
    return bool(
        attributes.get("restored") is True
        or attributes.get("assumed_state") is True
        or attributes.get("cached") is True
        or attributes.get("cache") is True
        or attributes.get("evidence_source") in {"restore", "cache"}
    )


__all__ = [
    "UNAVAILABLE_EVIDENCE",
    "POWER_EVIDENCE_FRESH_SECONDS",
    "TARGET_STATE_FRESHNESS_MS",
    "trusted_power_state",
    "trusted_target_state",
    "trusted_target_observed_ms",
    "power_state_is_fresh",
    "state_is_restored_or_cached",
]
