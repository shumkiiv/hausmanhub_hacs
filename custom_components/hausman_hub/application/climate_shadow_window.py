"""Collect and summarize bounded climate shadow evidence without commands."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Protocol

from ..domain.climate_comparison import (
    ClimateComparisonReason,
    ClimateComparisonSnapshot,
    ClimateComparisonStatus,
)
from ..domain.climate_shadow_window import (
    CLIMATE_SHADOW_MAX_SAMPLES,
    CLIMATE_SHADOW_WINDOW_VERSION,
    ClimateShadowRoomSample,
    ClimateShadowRoomVerdict,
    ClimateShadowSample,
    ClimateShadowVerdict,
    ClimateShadowWindowPolicy,
    ClimateShadowWindowReason,
    ClimateShadowWindowState,
    ClimateShadowWindowViolation,
)


class ClimateShadowWindowStore(Protocol):
    """Persistence boundary for redacted observation evidence."""

    async def async_load(self) -> ClimateShadowWindowState | None: ...

    async def async_save(self, state: ClimateShadowWindowState) -> None: ...


class ClimateShadowWindowService:
    """Serialize collection and persistence while exposing no command method."""

    def __init__(
        self,
        store: ClimateShadowWindowStore,
        *,
        policy: ClimateShadowWindowPolicy | None = None,
    ) -> None:
        self._store = store
        self._policy = policy or ClimateShadowWindowPolicy()
        self._state = ClimateShadowWindowState()
        self._loaded = False
        self._lock = asyncio.Lock()

    @property
    def state(self) -> ClimateShadowWindowState:
        return self._state

    async def async_load(self) -> None:
        async with self._lock:
            loaded = await self._store.async_load()
            self._state = loaded or ClimateShadowWindowState()
            self._loaded = True

    async def async_initialize_empty(self) -> None:
        """Recover from unreadable evidence without persisting unvalidated data."""

        async with self._lock:
            self._state = ClimateShadowWindowState()
            self._loaded = True

    async def async_record(
        self,
        comparison: ClimateComparisonSnapshot,
        *,
        collected_at: int,
    ) -> bool:
        """Persist one redacted sample; return whether storage changed."""

        sample = climate_shadow_sample_from_comparison(comparison)
        async with self._lock:
            updated = append_climate_shadow_sample(
                self._state,
                sample,
                collected_at=collected_at,
                policy=self._policy,
            )
            if updated == self._state:
                return False
            await self._store.async_save(updated)
            self._state = updated
            return True

    async def async_snapshot(self, *, generated_at: int) -> dict[str, object]:
        """Return contract payload without writing storage or sending commands."""

        async with self._lock:
            return climate_shadow_window_to_payload(
                self._state,
                generated_at=generated_at,
                policy=self._policy,
                collection_active=self._loaded,
            )


def climate_shadow_sample_from_comparison(
    comparison: ClimateComparisonSnapshot,
) -> ClimateShadowSample:
    """Discard device and binding details while preserving room evidence."""

    if not isinstance(comparison, ClimateComparisonSnapshot):
        raise ClimateShadowWindowViolation("validated climate comparison is required")
    return ClimateShadowSample(
        observed_at=comparison.observed_at,
        rooms=tuple(
            ClimateShadowRoomSample(
                room_id=room.room_id,
                status=room.status,
                reasons=room.reasons,
            )
            for room in sorted(comparison.rooms, key=lambda item: item.room_id)
        ),
    )


def append_climate_shadow_sample(
    state: ClimateShadowWindowState,
    sample: ClimateShadowSample,
    *,
    collected_at: int,
    policy: ClimateShadowWindowPolicy,
) -> ClimateShadowWindowState:
    """Add or replace one time-keyed sample and enforce the retention bound."""

    if not isinstance(state, ClimateShadowWindowState):
        raise ClimateShadowWindowViolation("validated shadow state is required")
    if not isinstance(sample, ClimateShadowSample):
        raise ClimateShadowWindowViolation("validated shadow sample is required")
    if type(collected_at) is not int or collected_at < 0:
        raise ClimateShadowWindowViolation("collection time is invalid")
    cutoff = max(0, collected_at - policy.retention_seconds * 1000)
    by_time = {
        existing.observed_at: existing
        for existing in state.samples
        if existing.observed_at >= cutoff
    }
    if sample.observed_at >= cutoff:
        by_time[sample.observed_at] = sample
    ordered = tuple(by_time[key] for key in sorted(by_time))[-CLIMATE_SHADOW_MAX_SAMPLES:]
    return ClimateShadowWindowState(samples=ordered)


def climate_shadow_window_to_payload(
    state: ClimateShadowWindowState,
    *,
    generated_at: int,
    policy: ClimateShadowWindowPolicy,
    collection_active: bool,
) -> dict[str, object]:
    """Build the strict local-admin contract from durable redacted evidence."""

    if type(generated_at) is not int or generated_at < 0:
        raise ClimateShadowWindowViolation("shadow generation time is invalid")
    verdicts = climate_shadow_room_verdicts(
        state,
        generated_at=generated_at,
        policy=policy,
    )
    first = state.samples[0].observed_at if state.samples else None
    latest = state.samples[-1].observed_at if state.samples else None
    return {
        "contract": {
            "name": "hausman-hub-climate-shadow-window",
            "version": 1,
        },
        "generated_at": generated_at,
        "window": {
            "collection_active": bool(collection_active),
            "sample_interval_seconds": policy.sample_interval_seconds,
            "retention_seconds": policy.retention_seconds,
            "minimum_sample_count": policy.minimum_sample_count,
            "minimum_span_seconds": policy.minimum_span_seconds,
            "freshness_seconds": policy.freshness_seconds,
            "minimum_alignment_ratio": float(policy.minimum_alignment_ratio),
        },
        "summary": {
            "sample_count": len(state.samples),
            "room_count": len(verdicts),
            "ready_room_count": sum(
                verdict.verdict is ClimateShadowVerdict.READY for verdict in verdicts
            ),
            "diverged_room_count": sum(
                verdict.verdict is ClimateShadowVerdict.DIVERGED for verdict in verdicts
            ),
            "insufficient_room_count": sum(
                verdict.verdict is ClimateShadowVerdict.INSUFFICIENT_DATA
                for verdict in verdicts
            ),
            "first_observed_at": first,
            "latest_observed_at": latest,
        },
        "rooms": [_room_verdict_payload(verdict) for verdict in verdicts],
        "samples": [
            {
                "observed_at": sample.observed_at,
                "rooms": [
                    {
                        "room_id": room.room_id,
                        "status": room.status.value,
                        "reasons": [reason.value for reason in room.reasons],
                    }
                    for room in sample.rooms
                ],
            }
            for sample in state.samples
        ],
        "commands_enabled": False,
        "physical_commands_sent": False,
    }


def climate_shadow_room_verdicts(
    state: ClimateShadowWindowState,
    *,
    generated_at: int,
    policy: ClimateShadowWindowPolicy,
) -> tuple[ClimateShadowRoomVerdict, ...]:
    """Aggregate each stable room independently across the retained window."""

    room_ids = sorted(
        {room.room_id for sample in state.samples for room in sample.rooms}
    )
    return tuple(
        _room_verdict(room_id, state.samples, generated_at, policy)
        for room_id in room_ids
    )


def climate_shadow_state_to_payload(
    state: ClimateShadowWindowState,
) -> dict[str, object]:
    """Encode only the bounded redacted storage document."""

    return {
        "version": CLIMATE_SHADOW_WINDOW_VERSION,
        "samples": [
            {
                "observed_at": sample.observed_at,
                "rooms": [
                    {
                        "room_id": room.room_id,
                        "status": room.status.value,
                        "reasons": [reason.value for reason in room.reasons],
                    }
                    for room in sample.rooms
                ],
            }
            for sample in state.samples
        ],
    }


def climate_shadow_state_from_payload(payload: object) -> ClimateShadowWindowState:
    """Decode only the exact current storage shape."""

    root = _exact_mapping(payload, {"version", "samples"}, "shadow storage")
    if root["version"] != CLIMATE_SHADOW_WINDOW_VERSION:
        raise ClimateShadowWindowViolation("shadow storage version is unsupported")
    raw_samples = root["samples"]
    if type(raw_samples) is not list:
        raise ClimateShadowWindowViolation("shadow samples must be a list")
    samples: list[ClimateShadowSample] = []
    for raw_sample in raw_samples:
        sample = _exact_mapping(raw_sample, {"observed_at", "rooms"}, "shadow sample")
        raw_rooms = sample["rooms"]
        if type(raw_rooms) is not list:
            raise ClimateShadowWindowViolation("shadow rooms must be a list")
        rooms: list[ClimateShadowRoomSample] = []
        for raw_room in raw_rooms:
            room = _exact_mapping(
                raw_room,
                {"room_id", "status", "reasons"},
                "shadow room",
            )
            raw_reasons = room["reasons"]
            if type(raw_reasons) is not list:
                raise ClimateShadowWindowViolation("shadow reasons must be a list")
            try:
                status = ClimateComparisonStatus(room["status"])
                reasons = tuple(ClimateComparisonReason(value) for value in raw_reasons)
            except (TypeError, ValueError) as error:
                raise ClimateShadowWindowViolation("shadow enum value is invalid") from error
            rooms.append(
                ClimateShadowRoomSample(
                    room_id=room["room_id"],
                    status=status,
                    reasons=reasons,
                )
            )
        samples.append(
            ClimateShadowSample(
                observed_at=sample["observed_at"],
                rooms=tuple(rooms),
            )
        )
    return ClimateShadowWindowState(samples=tuple(samples))


def _room_verdict(
    room_id: str,
    samples: tuple[ClimateShadowSample, ...],
    generated_at: int,
    policy: ClimateShadowWindowPolicy,
) -> ClimateShadowRoomVerdict:
    observations = tuple(
        room
        for sample in samples
        for room in sample.rooms
        if room.room_id == room_id
    )
    times = tuple(
        sample.observed_at
        for sample in samples
        if any(room.room_id == room_id for room in sample.rooms)
    )
    aligned = sum(room.status is ClimateComparisonStatus.ALIGNED for room in observations)
    diverged = sum(room.status is ClimateComparisonStatus.DIVERGED for room in observations)
    not_comparable = sum(
        room.status is ClimateComparisonStatus.NOT_COMPARABLE for room in observations
    )
    comparable = aligned + diverged
    ratio = aligned / comparable if comparable else None
    first = times[0] if times else None
    latest = times[-1] if times else None
    age = None if latest is None else generated_at - latest
    fresh = age is not None and 0 <= age <= policy.freshness_seconds * 1000
    reasons: set[ClimateShadowWindowReason] = set()
    if not observations:
        reasons.add(ClimateShadowWindowReason.NO_OBSERVATIONS)
    if diverged:
        reasons.add(ClimateShadowWindowReason.DIVERGENCE_OBSERVED)
        if ratio is not None and ratio < policy.minimum_alignment_ratio:
            reasons.add(ClimateShadowWindowReason.ALIGNMENT_BELOW_THRESHOLD)
        verdict = ClimateShadowVerdict.DIVERGED
    else:
        if comparable < policy.minimum_sample_count:
            reasons.add(ClimateShadowWindowReason.INSUFFICIENT_OBSERVATIONS)
        if first is None or latest is None or latest - first < policy.minimum_span_seconds * 1000:
            reasons.add(ClimateShadowWindowReason.INSUFFICIENT_TIMESPAN)
        if not fresh:
            reasons.add(ClimateShadowWindowReason.LATEST_OBSERVATION_STALE)
        if ratio is not None and ratio < policy.minimum_alignment_ratio:
            reasons.add(ClimateShadowWindowReason.ALIGNMENT_BELOW_THRESHOLD)
        if not_comparable and reasons:
            reasons.add(ClimateShadowWindowReason.NOT_COMPARABLE_OBSERVATIONS)
        verdict = (
            ClimateShadowVerdict.READY
            if not reasons
            else ClimateShadowVerdict.INSUFFICIENT_DATA
        )
    ordered_reasons = tuple(
        reason for reason in ClimateShadowWindowReason if reason in reasons
    )
    return ClimateShadowRoomVerdict(
        room_id=room_id,
        verdict=verdict,
        reasons=ordered_reasons,
        observation_count=len(observations),
        comparable_count=comparable,
        aligned_count=aligned,
        diverged_count=diverged,
        not_comparable_count=not_comparable,
        alignment_ratio=ratio,
        first_observed_at=first,
        latest_observed_at=latest,
        latest_status=observations[-1].status if observations else None,
        fresh=fresh,
    )


def _room_verdict_payload(verdict: ClimateShadowRoomVerdict) -> dict[str, object]:
    return {
        "room_id": verdict.room_id,
        "verdict": verdict.verdict.value,
        "reasons": [reason.value for reason in verdict.reasons],
        "observation_count": verdict.observation_count,
        "comparable_count": verdict.comparable_count,
        "aligned_count": verdict.aligned_count,
        "diverged_count": verdict.diverged_count,
        "not_comparable_count": verdict.not_comparable_count,
        "alignment_ratio": verdict.alignment_ratio,
        "first_observed_at": verdict.first_observed_at,
        "latest_observed_at": verdict.latest_observed_at,
        "latest_status": (
            None if verdict.latest_status is None else verdict.latest_status.value
        ),
        "fresh": verdict.fresh,
    }


def _exact_mapping(
    value: object,
    fields: set[str],
    label: str,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ClimateShadowWindowViolation(f"{label} fields are invalid")
    return value
