"""Pure, per-component permission for a configured manual priority expiry."""
from dataclasses import dataclass
from collections.abc import Iterable
from ..domain.room_lighting import RoomLightingConfig, manual_priority_duration_ms


@dataclass(frozen=True, slots=True)
class ManualClaim:
    entity_id: str
    action: str
    observed_at_ms: int


@dataclass(frozen=True, slots=True)
class TimedManualReturn:
    due: bool
    reason: str
    expires_at_ms: int | None = None
    has_off: bool = False


def timed_manual_return(
    config: RoomLightingConfig, claims: Iterable[ManualClaim], now_ms: int,
    *, away_boundary_ms: int | None = None,
) -> TimedManualReturn:
    """Recompute from gesture time, never from the current lamp state.

    Independent journal generations are deliberately not compared. The latest
    proved timestamp per entity wins, and opposed equal-time evidence holds.
    A full linked room is released only when every latest claim is eligible.
    """
    latest: dict[str, ManualClaim] = {}
    for claim in claims:
        if (claim.action not in {"on", "off"} or type(claim.observed_at_ms) is not int
                or not 0 < claim.observed_at_ms <= now_ms):
            return TimedManualReturn(False, "attribution_uncertain")
        previous = latest.get(claim.entity_id)
        if previous is not None and previous.observed_at_ms == claim.observed_at_ms and previous.action != claim.action:
            return TimedManualReturn(False, "attribution_uncertain")
        if previous is None or claim.observed_at_ms > previous.observed_at_ms:
            latest[claim.entity_id] = claim
    if not latest:
        return TimedManualReturn(False, "no_manual_claim")
    has_off = any(claim.action == "off" for claim in latest.values())
    expiry = max(claim.observed_at_ms + manual_priority_duration_ms(config, claim.action)
                 for claim in latest.values())
    if (away_boundary_ms is not None and config.manual_control_return.clear_on_away
            and all(claim.observed_at_ms <= away_boundary_ms for claim in latest.values())):
        return TimedManualReturn(True, "away_return", expiry, has_off)
    return TimedManualReturn(now_ms >= expiry,
                             "timed_return" if now_ms >= expiry else "manual_priority_active", expiry, has_off)
