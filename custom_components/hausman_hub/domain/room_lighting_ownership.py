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
    NONE = "none"
    UNKNOWN = "unknown"


class SensorState(StrEnum):
    ON = "on"
    OFF = "off"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class OwnershipSnapshot:
    """One attribution record for one light target."""

    target_id: str
    source: OwnershipSource
    confirmed: bool
    at: int

    def __post_init__(self) -> None:
        if not isinstance(self.target_id, str) or not self.target_id:
            raise OwnershipViolation("ownership target id is required")
        if not isinstance(self.source, OwnershipSource):
            raise OwnershipViolation("ownership source is invalid")
        if type(self.confirmed) is not bool:
            raise OwnershipViolation("ownership confirmation flag is invalid")
        if type(self.at) is not int or self.at < 0:
            raise OwnershipViolation("ownership time is invalid")


def latest_ownership(
    records: Sequence[OwnershipSnapshot], target_id: str
) -> OwnershipSnapshot | None:
    """Return the newest record for one target, if any."""

    latest: OwnershipSnapshot | None = None
    for record in records:
        if record.target_id != target_id:
            continue
        if latest is None or record.at >= latest.at:
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
    ):
        return True
    return False


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
