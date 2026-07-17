"""Use cases for storing and reading safe configuration values.

Only an approved mode and the hard direct-execution block are represented in
saved entry data. Options add only the optional-page boolean and one fixed
same-or-slower count refresh choice. Unknown values are rejected instead of
being copied into a config entry.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..domain.configuration import (
    APPROVED_SUMMARY_UPDATE_INTERVALS,
    DIRECT_EXECUTION_BLOCKED,
    SafeConfiguration,
    SUMMARY_UPDATE_INTERVAL_DEFAULT,
    UnsafeModeError,
    configuration_for_mode,
)


MODE_FIELD = "mode"
DIRECT_EXECUTION_STATUS_FIELD = "direct_execution_status"
LOCAL_SUMMARY_ENABLED_FIELD = "local_summary_enabled"
LOCAL_SUMMARY_ENABLED_DEFAULT = True
SUMMARY_UPDATE_INTERVAL_FIELD = "summary_update_interval"


class ConfigurationViolation(ValueError):
    """A config-entry value does not satisfy the safety contract."""


def create_initial_entry(mode_value: object) -> dict[str, str]:
    """Return the only data shape permitted when an entry is created."""

    configuration = _configuration_for(mode_value)
    return {
        MODE_FIELD: configuration.mode,
        DIRECT_EXECUTION_STATUS_FIELD: configuration.direct_execution_status,
    }


def create_options(
    mode_value: object,
    local_summary_enabled_value: object = LOCAL_SUMMARY_ENABLED_DEFAULT,
    summary_update_interval_value: object = SUMMARY_UPDATE_INTERVAL_DEFAULT,
) -> dict[str, str | bool]:
    """Return only the safe mode, page choice, and fixed refresh choice."""

    configuration = _configuration_for(
        mode_value,
        local_summary_enabled_value,
        summary_update_interval_value,
    )
    return {
        MODE_FIELD: configuration.mode,
        LOCAL_SUMMARY_ENABLED_FIELD: configuration.local_summary_enabled,
        SUMMARY_UPDATE_INTERVAL_FIELD: configuration.summary_update_interval,
    }


def effective_configuration(
    entry_data: Mapping[str, Any], options: Mapping[str, Any]
) -> SafeConfiguration:
    """Validate persisted values and return their safe effective meaning."""

    _require_exact_keys(
        entry_data,
        {MODE_FIELD, DIRECT_EXECUTION_STATUS_FIELD},
        "entry data",
    )
    _require_allowed_keys(
        options,
        {
            MODE_FIELD,
            LOCAL_SUMMARY_ENABLED_FIELD,
            SUMMARY_UPDATE_INTERVAL_FIELD,
        },
        "options",
    )

    if entry_data.get(DIRECT_EXECUTION_STATUS_FIELD) != DIRECT_EXECUTION_BLOCKED:
        raise ConfigurationViolation("direct execution must remain blocked")

    mode_value = options.get(MODE_FIELD, entry_data.get(MODE_FIELD))
    local_summary_enabled_value = options.get(
        LOCAL_SUMMARY_ENABLED_FIELD,
        LOCAL_SUMMARY_ENABLED_DEFAULT,
    )
    summary_update_interval_value = options.get(
        SUMMARY_UPDATE_INTERVAL_FIELD,
        SUMMARY_UPDATE_INTERVAL_DEFAULT,
    )
    return _configuration_for(
        mode_value,
        local_summary_enabled_value,
        summary_update_interval_value,
    )


def _configuration_for(
    mode_value: object,
    local_summary_enabled_value: object = LOCAL_SUMMARY_ENABLED_DEFAULT,
    summary_update_interval_value: object = SUMMARY_UPDATE_INTERVAL_DEFAULT,
) -> SafeConfiguration:
    """Build the safe configuration after checking both optional choices."""

    if type(local_summary_enabled_value) is not bool:
        raise ConfigurationViolation("local summary setting must be true or false")
    if (
        not isinstance(summary_update_interval_value, str)
        or summary_update_interval_value not in APPROVED_SUMMARY_UPDATE_INTERVALS
    ):
        raise ConfigurationViolation(
            "summary update interval must be one of: "
            + ", ".join(APPROVED_SUMMARY_UPDATE_INTERVALS)
        )

    try:
        mode_configuration = configuration_for_mode(mode_value)
    except UnsafeModeError as error:
        raise ConfigurationViolation(str(error)) from error
    return SafeConfiguration(
        mode=mode_configuration.mode,
        direct_execution_status=mode_configuration.direct_execution_status,
        local_summary_enabled=local_summary_enabled_value,
        summary_update_interval=summary_update_interval_value,
    )


def _require_exact_keys(
    values: Mapping[str, Any], allowed: set[str], label: str
) -> None:
    """Require every fixed saved field and reject any hidden channel."""

    missing = allowed - set(values)
    if missing:
        names = ", ".join(sorted(missing))
        raise ConfigurationViolation(f"{label} is missing required fields: {names}")

    _require_allowed_keys(values, allowed, label)


def _require_allowed_keys(
    values: Mapping[str, Any], allowed: set[str], label: str
) -> None:
    """Reject unmodelled optional fields so they cannot become a hidden channel."""

    unexpected = set(values) - allowed
    if unexpected:
        names = ", ".join(sorted(unexpected))
        raise ConfigurationViolation(f"{label} contains unsupported fields: {names}")
