"""HA-backed clock adapter of the core (CA-02: time is an adapter detail).

The production clock is conservative: calendar time is trusted only when
the platform confirms synchronization, and the boot id is unique per
process so monotonic data of a dead process is never reused (K-10, S-05).
"""

from __future__ import annotations

import time
import uuid

from ...application.core.command_service import ClockPort
from ...domain.core.recovery import ClockTrust


class SystemClock(ClockPort):
    """Process clock with a per-process boot identifier.

    ``trusted`` is intentionally conservative: the shadow-only delivery
    never dispatches anyway, and proving platform time sync belongs to a
    later step (S-05).
    """

    def __init__(self, *, trusted: bool = False) -> None:
        self._trusted = trusted
        self._boot_id = f"boot-{uuid.uuid4().hex[:12]}"

    def now_mono_ms(self) -> int:
        return time.monotonic_ns() // 1_000_000

    def now_utc_ms(self) -> int:
        # HA's dt_util is imported lazily so the adapter stays importable
        # without Home Assistant installed; if unavailable, the standard
        # library UTC clock is used (identical epoch).
        try:
            from homeassistant.util import dt as dt_util

            return int(dt_util.utcnow().timestamp() * 1000)
        except ImportError:
            return int(time.time() * 1000)

    def boot_id(self) -> str:
        return self._boot_id

    def wall_clock_trust(self) -> ClockTrust:
        if self._trusted:
            return ClockTrust.trusted_clock()
        return ClockTrust(
            trusted=False,
            reason="clock_platform_sync_unconfirmed",
        )
