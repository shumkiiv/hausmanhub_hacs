"""Safety policy for the approved HausmanHub integration modes.

The module intentionally has no Home Assistant imports. It is the innermost
layer and keeps general direct execution blocked. The optional control canary
may target only the separate domain model for one Home Assistant input helper.
"""

from __future__ import annotations

from dataclasses import dataclass

from .climate_bridge import ClimateControlMode, ClimateBridgeTarget
from .control import CanaryControlTarget
from .native_climate import NativeClimatePolicy


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
    """The complete validated HausmanHub configuration."""

    mode: str
    direct_execution_status: str = DIRECT_EXECUTION_BLOCKED
    local_summary_enabled: bool = True
    summary_update_interval: str = SUMMARY_UPDATE_INTERVAL_DEFAULT
    canary_control_enabled: bool = False
    canary_control_target: CanaryControlTarget | None = None
    climate_bridge_mode: ClimateControlMode = ClimateControlMode.DISABLED
    climate_bridge_target: ClimateBridgeTarget | None = None
    climate_canary_room_id: str | None = None
    native_climate_policy: NativeClimatePolicy = NativeClimatePolicy()
    connection_mode: str = "center"
    smart_home_center_url: str | None = None
    home_assistant_url: str | None = None


_CONNECTION_MODE_CENTER = "center"
_CONNECTION_MODE_HOME_ASSISTANT = "home_assistant"
APPROVED_CONNECTION_MODES = (_CONNECTION_MODE_CENTER, _CONNECTION_MODE_HOME_ASSISTANT)


def _connection_mode(value: object) -> str:
    """Return a validated connection mode or raise UnsafeModeError."""

    if not isinstance(value, str) or value not in APPROVED_CONNECTION_MODES:
        allowed = ", ".join(APPROVED_CONNECTION_MODES)
        raise UnsafeModeError(f"connection mode must be one of: {allowed}")
    return value


_connection_url_schemes = frozenset({"http", "https"})


def _connection_url(value: object) -> str | None:
    """Return a validated connection URL or None."""

    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise UnsafeModeError("connection URL must be a string")
    from urllib.parse import urlparse

    parsed = urlparse(value)
    if parsed.scheme not in _connection_url_schemes:
        raise UnsafeModeError("connection URL must use http or https")
    if not parsed.hostname:
        raise UnsafeModeError("connection URL must contain a host")
    return value


def configuration_for_mode(value: object) -> SafeConfiguration:
    """Create a configuration only for an explicitly approved mode."""

    if not isinstance(value, str) or value not in APPROVED_MODES:
        allowed = ", ".join(APPROVED_MODES)
        raise UnsafeModeError(f"mode must be one of: {allowed}")
    return SafeConfiguration(mode=value)
