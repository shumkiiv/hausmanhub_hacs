"""Pure recovery rules of the core (K-10, S-05).

Classification of a restored operation and evaluation of calendar-clock
trust. Nothing here touches files, the system clock or Home Assistant:
every input is an explicit value so the same inputs give the same
decision.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import OperationPhase

CLOCK_DRIFT_LIMIT_MS = 2000
REASON_CLOCK_UNCERTAIN = "clock_uncertain"
REASON_RESTART_PENDING_CANCELLED = "restart_pending_cancelled"
REASON_RESTART_UNCERTAIN = "restart_uncertain"


@dataclass(frozen=True)
class ClockTrust:
    """Result of the S-05 calendar-clock trust evaluation."""

    trusted: bool
    reason: str | None = None

    @classmethod
    def trusted_clock(cls) -> "ClockTrust":
        return cls(trusted=True, reason=None)


def evaluate_clock_trust(
    platform_sync_confirmed: bool,
    previous_utc_ms: int | None,
    previous_mono_ms: int | None,
    current_utc_ms: int,
    current_mono_ms: int,
    max_drift_ms: int = CLOCK_DRIFT_LIMIT_MS,
) -> ClockTrust:
    """Trust the wall clock only with a confirmed platform sync and no
    UTC-vs-monotonic divergence beyond ``max_drift_ms`` inside the current
    process (S-05). Divergence in either direction counts."""

    if not platform_sync_confirmed:
        return ClockTrust(False, "clock_platform_sync_unconfirmed")
    if previous_utc_ms is not None and previous_mono_ms is not None:
        utc_delta = current_utc_ms - previous_utc_ms
        mono_delta = current_mono_ms - previous_mono_ms
        divergence = utc_delta - mono_delta
        if divergence < -max_drift_ms or divergence > max_drift_ms:
            return ClockTrust(False, "clock_uncertain")
    return ClockTrust(True, None)


@dataclass(frozen=True)
class RestoredOperationDecision:
    """What a persisted operation becomes after a restart (K-10)."""

    phase: OperationPhase
    reason_code: str


def classify_restored_operation(
    phase: OperationPhase,
) -> RestoredOperationDecision:
    """Pending work is never replayed after a restart, and a dispatched
    command without a durable outcome is unknown — a restart never
    upgrades or silently drops an outcome (S-04, K-10). Already terminal
    results restore as they are."""

    if phase in (
        OperationPhase.ACCEPTED,
        OperationPhase.QUEUED,
        OperationPhase.TAKEN,
    ):
        return RestoredOperationDecision(
            OperationPhase.CANCELLED,
            REASON_RESTART_PENDING_CANCELLED,
        )
    if phase in (OperationPhase.DISPATCHING, OperationPhase.OBSERVING):
        return RestoredOperationDecision(
            OperationPhase.UNKNOWN,
            REASON_RESTART_UNCERTAIN,
        )
    return RestoredOperationDecision(phase, "restored")
