"""Pure classification of the protected Away sensor for automatic commands."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProtectedAwayObservation:
    """Trusted Away value and the diagnostic relevant to automatic commands."""

    is_away: bool | None
    diagnostic: str | None

    @property
    def command_block_reason(self) -> str | None:
        """Return the explicit fail-closed reason, or ``None`` when trusted."""

        return self.diagnostic


def classify_protected_away(
    entity_id: str | None,
    raw_state: object,
) -> ProtectedAwayObservation:
    """Classify an HA state without inferring liveness from timestamps.

    Only the two state values the protected topology defines are trusted. A
    stable state is not treated as stale here: source-specific heartbeat proof
    belongs to a later policy decision.
    """

    if entity_id is None:
        return ProtectedAwayObservation(
            is_away=None,
            diagnostic="protected_topology_missing",
        )
    state = str(raw_state).lower()
    if state == "on":
        return ProtectedAwayObservation(is_away=True, diagnostic=None)
    if state == "off":
        return ProtectedAwayObservation(is_away=False, diagnostic=None)
    return ProtectedAwayObservation(
        is_away=None,
        diagnostic="away_sensor_unavailable",
    )
