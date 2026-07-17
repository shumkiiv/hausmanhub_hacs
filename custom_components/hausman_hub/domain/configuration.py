"""Safety policy for the first HausMan Hub integration modes.

The module intentionally has no Home Assistant imports. It is the innermost
layer and knows only the two modes that are currently approved.
"""

from __future__ import annotations

from dataclasses import dataclass


READ_ONLY_MODE = "read-only"
SHADOW_MODE = "shadow"
APPROVED_MODES = (READ_ONLY_MODE, SHADOW_MODE)
DIRECT_EXECUTION_BLOCKED = "direct_execution_blocked"
SUMMARY_UPDATE_INTERVAL_DEFAULT = "5m"
APPROVED_SUMMARY_UPDATE_INTERVALS = (
    SUMMARY_UPDATE_INTERVAL_DEFAULT,
    "15m",
    "30m",
)


class UnsafeModeError(ValueError):
    """Raised when input attempts to leave the approved safety boundary."""


@dataclass(frozen=True, slots=True)
class SafeConfiguration:
    """The complete safe state of the first integration skeleton."""

    mode: str
    direct_execution_status: str = DIRECT_EXECUTION_BLOCKED
    local_summary_enabled: bool = True
    summary_update_interval: str = SUMMARY_UPDATE_INTERVAL_DEFAULT


def configuration_for_mode(value: object) -> SafeConfiguration:
    """Create a configuration only for an explicitly approved mode."""

    if not isinstance(value, str) or value not in APPROVED_MODES:
        allowed = ", ".join(APPROVED_MODES)
        raise UnsafeModeError(f"mode must be one of: {allowed}")
    return SafeConfiguration(mode=value)
