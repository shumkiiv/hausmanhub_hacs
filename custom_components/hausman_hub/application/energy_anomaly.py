"""Fail-closed sustained energy anomaly observation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable


class EnergyAnomalyTracker:
    """Raise an anomaly only after power stays above a configured threshold."""

    def __init__(self, *, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._observed_since: datetime | None = None
        self._triggered_at: datetime | None = None
        self._policy: tuple[float, int] | None = None

    def observe(
        self,
        power_w: object,
        threshold_w: float | None,
        sustain_minutes: int | None,
    ) -> dict[str, object]:
        """Return public state without inventing an alert for missing data."""

        if threshold_w is None or sustain_minutes is None:
            self._reset(None)
            return self._document(None, None, None, False)
        policy = (float(threshold_w), int(sustain_minutes))
        if self._policy != policy:
            self._reset(policy)
        numeric_power = (
            float(power_w)
            if isinstance(power_w, (int, float)) and not isinstance(power_w, bool)
            else None
        )
        now = self._aware_now()
        if numeric_power is None or numeric_power <= policy[0]:
            self._observed_since = None
            self._triggered_at = None
            return self._document(policy[0], policy[1], numeric_power, False)
        if self._observed_since is None:
            self._observed_since = now
        active = now - self._observed_since >= timedelta(minutes=policy[1])
        if active and self._triggered_at is None:
            self._triggered_at = now
        return self._document(policy[0], policy[1], numeric_power, active)

    def _reset(self, policy: tuple[float, int] | None) -> None:
        self._policy = policy
        self._observed_since = None
        self._triggered_at = None

    def _aware_now(self) -> datetime:
        value = self._now()
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)

    @staticmethod
    def _iso(value: datetime | None) -> str | None:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value else None

    def _document(
        self,
        threshold_w: float | None,
        sustain_minutes: int | None,
        power_w: float | None,
        active: bool,
    ) -> dict[str, object]:
        return {
            "configured": threshold_w is not None and sustain_minutes is not None,
            "active": active,
            "thresholdW": threshold_w,
            "sustainMinutes": sustain_minutes,
            "observedPowerW": power_w,
            "observedSince": self._iso(self._observed_since),
            "triggeredAt": self._iso(self._triggered_at),
        }
