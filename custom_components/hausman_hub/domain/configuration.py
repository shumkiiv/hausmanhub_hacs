"""Safety policy for the approved HausMan Hub integration modes.

The module intentionally has no Home Assistant imports. It is the innermost
layer and keeps general direct execution blocked. The optional control canary
may target only the separate domain model for one Home Assistant input helper.
"""

from __future__ import annotations

from dataclasses import dataclass

from .control import CanaryControlTarget


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
    """The complete validated HASC configuration."""

    mode: str
    direct_execution_status: str = DIRECT_EXECUTION_BLOCKED
    local_summary_enabled: bool = True
    summary_update_interval: str = SUMMARY_UPDATE_INTERVAL_DEFAULT
    canary_control_enabled: bool = False
    canary_control_target: CanaryControlTarget | None = None


def configuration_for_mode(value: object) -> SafeConfiguration:
    """Create a configuration only for an explicitly approved mode."""

    if not isinstance(value, str) or value not in APPROVED_MODES:
        allowed = ", ".join(APPROVED_MODES)
        raise UnsafeModeError(f"mode must be one of: {allowed}")
    return SafeConfiguration(mode=value)
