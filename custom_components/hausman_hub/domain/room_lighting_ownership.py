"""Pure ownership rules for room lighting decisions.

Ownership is the evidence that automation, not a person, owns the current
light state. These helpers never read Home Assistant and never send commands.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Sequence


class OwnershipViolation(ValueError):
    """Ownership evidence is malformed."""


class OwnershipSource(StrEnum):
    MANUAL = "manual"
    AUTO = "auto"
    EXPLICIT_HANDOFF = "explicit_handoff"
    NONE = "none"
    UNKNOWN = "unknown"


class SensorState(StrEnum):
    ON = "on"
    OFF = "off"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ExplicitHandoffProof:
    """Owner intent and its observed scope, never a device ON receipt."""

    entity_id: str
    config_version: int
    operation_id: int
    manual_boundary: int
    observed_at: int
    observer_session: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.entity_id, str) or not self.entity_id:
            raise OwnershipViolation("handoff entity is required")
        if self.observer_session is not None and (not isinstance(self.observer_session, str) or not self.observer_session):
            raise OwnershipViolation("handoff observer session is invalid")
        for value, minimum in ((self.config_version, 1), (self.operation_id, 1),
                               (self.manual_boundary, 0), (self.observed_at, 0)):
            if type(value) is not int or value < minimum:
                raise OwnershipViolation("handoff evidence is invalid")


@dataclass(frozen=True, slots=True)
class OwnershipSnapshot:
    """One attribution record for one light target.

    ``turned_off`` and ``generation`` carry the record's direction and its
    place in the manual lineage (RP-10): the manual-off proof is validated
    against the specific record — its moment, its generation and its
    direction — not against a bare anchor. Both default to the unknown
    values so older documents and existing constructors stay valid."""

    target_id: str
    source: OwnershipSource
    confirmed: bool
    at: int
    turned_off: bool = False
    generation: int = 0
    handoff: ExplicitHandoffProof | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.target_id, str) or not self.target_id:
            raise OwnershipViolation("ownership target id is required")
        if not isinstance(self.source, OwnershipSource):
            raise OwnershipViolation("ownership source is invalid")
        if type(self.confirmed) is not bool:
            raise OwnershipViolation("ownership confirmation flag is invalid")
        if type(self.at) is not int or self.at < 0:
            raise OwnershipViolation("ownership time is invalid")
        if type(self.turned_off) is not bool:
            raise OwnershipViolation("ownership turn-off flag is invalid")
        if type(self.generation) is not int or self.generation < 0:
            raise OwnershipViolation("ownership generation is invalid")
        if self.source is OwnershipSource.EXPLICIT_HANDOFF:
            if not isinstance(self.handoff, ExplicitHandoffProof):
                raise OwnershipViolation("explicit handoff proof is required")
        elif self.handoff is not None:
            raise OwnershipViolation("handoff proof does not match its source")


def latest_ownership(
    records: Sequence[OwnershipSnapshot], target_id: str
) -> OwnershipSnapshot | None:
    """Return the newest record for one target, deterministically.

    A manual record wins a timestamp tie so a manual intervention is never
    lost behind an equally old automatic record.
    """

    latest: OwnershipSnapshot | None = None
    for record in records:
        if record.target_id != target_id:
            continue
        if latest is None or record.at > latest.at:
            latest = record
        elif record.at == latest.at and record.source is OwnershipSource.MANUAL:
            latest = record
    return latest


def manual_intervention_after(
    records: Sequence[OwnershipSnapshot], target_id: str, since: int
) -> bool:
    """Whether a manual record appeared strictly after one auto command."""

    for record in records:
        if record.target_id != target_id:
            continue
        if record.at > since and record.source is OwnershipSource.MANUAL:
            return True
    return False


def has_proven_auto_ownership(
    records: Sequence[OwnershipSnapshot], target_id: str
) -> bool:
    """Automation owns the target only with the newest confirmed auto record."""

    latest = latest_ownership(records, target_id)
    if latest is None or not latest.confirmed:
        return False
    if latest.source is not OwnershipSource.AUTO:
        return False
    return not manual_intervention_after(records, target_id, latest.at)


def has_automatic_control(
    records: Sequence[OwnershipSnapshot], target_id: str,
) -> bool:
    """Confirmed command or active explicit delegation permits control.

    The application projects pending, restored or invalidated delegations as
    UNKNOWN. The strict AUTO-command predicate remains unchanged.
    """
    latest = latest_ownership(records, target_id)
    return has_proven_auto_ownership(records, target_id) or bool(
        latest is not None and latest.confirmed
        and latest.source is OwnershipSource.EXPLICIT_HANDOFF
        and latest.handoff is not None
        and not manual_intervention_after(records, target_id, latest.at)
    )


def resolve_manual_ownership(
    records: Sequence[OwnershipSnapshot],
    target_id: str,
    *,
    light_on: bool,
) -> bool:
    """Manual ownership is attribution or an on light with no active owner."""

    latest = latest_ownership(records, target_id)
    if latest is not None and latest.source is OwnershipSource.MANUAL:
        return True
    if light_on and (
        latest is None
        or latest.source in {OwnershipSource.NONE, OwnershipSource.UNKNOWN}
        or (latest.source is OwnershipSource.EXPLICIT_HANDOFF and not latest.confirmed)
    ):
        return True
    return False


def release_expired_manual(
    records: Sequence[OwnershipSnapshot],
    target_id: str,
    *,
    now: int,
    minimum_interval_seconds: int,
    stable_absence_seconds: int,
    absence_confirmed: bool,
    absence_since: int | None,
    release_mode: str = "timer_and_absence",
    absence_until: int | None = None,
    absence_latch: bool = False,
    require_directed_off: bool = False,
) -> bool:
    """Whether an expired manual-off protection releases manual ownership.

    The transition only fires when the newest record is a confirmed manual
    action and the minimum interval elapsed. ``timer_only`` releases on the
    timer alone: the owner rule for such rooms is that a short protection
    interval returns everything to automatic after the light is switched off.
    Every other mode additionally requires a stable absence confirmed *after*
    the manual action; a historical absence that predates it must not release
    the protection. The stable duration is measured to ``absence_until`` —
    the moment presence returned — when provided, so post-return time never
    stretches a shorter absence. It is a pure predicate: the caller still
    requires a new event before turning light on.
    """

    latest = latest_ownership(records, target_id)
    if latest is None or latest.source is not OwnershipSource.MANUAL:
        return False
    if not latest.confirmed:
        return False
    if require_directed_off and (not latest.turned_off or latest.generation <= 0):
        return False
    if now - latest.at < minimum_interval_seconds * 1000:
        return False
    if release_mode == "timer_only":
        return True
    # The absence evidence is satisfied either by the proven stable-absence
    # latch (a completed >= stable window since the manual action — short
    # re-entries do not erase it) or by the current window measurement.
    if not absence_latch:
        if not absence_confirmed or absence_since is None:
            return False
        if absence_since < latest.at:
            return False
        end = absence_until if absence_until is not None else now
        if end - absence_since < stable_absence_seconds * 1000:
            return False
    return True


def restore_after_restart(
    records: Sequence[OwnershipSnapshot],
    target_id: str,
    *,
    light_state: SensorState,
    light_last_changed: int | None,
    unobserved_since: int | None,
) -> bool:
    """Restore only proven automation ownership with fresh state evidence."""

    latest = latest_ownership(records, target_id)
    if latest is None or not latest.confirmed or latest.source is not OwnershipSource.AUTO:
        return False
    if light_state is not SensorState.ON:
        return False
    if manual_intervention_after(records, target_id, latest.at):
        return False
    if unobserved_since is not None and unobserved_since > latest.at:
        return False
    if light_last_changed is not None and light_last_changed < latest.at:
        return False
    return True


def observed_absence(
    sensors: Sequence[tuple[SensorState, int]],
    *,
    now: int,
    freshness_ms: int,
) -> bool | None:
    """Return True only for continuous fresh off; None breaks the evidence.

    Unknown, unavailable or stale readings interrupt absence and must never be
    treated as an empty room.
    """

    if type(now) is not int or now < 0:
        raise OwnershipViolation("observation time is invalid")
    if type(freshness_ms) is not int or freshness_ms < 0:
        raise OwnershipViolation("freshness window is invalid")
    if not sensors:
        return None
    for state, last_changed in sensors:
        if not isinstance(state, SensorState):
            raise OwnershipViolation("sensor state is invalid")
        if type(last_changed) is not int or last_changed < 0:
            raise OwnershipViolation("sensor timestamp is invalid")
        if state in {SensorState.UNKNOWN, SensorState.UNAVAILABLE}:
            return None
        if now - last_changed > freshness_ms:
            return None
        if state is SensorState.ON:
            return False
    return True
