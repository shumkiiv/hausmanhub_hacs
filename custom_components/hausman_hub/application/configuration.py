"""Use cases for storing and reading safe configuration values.

Only an approved mode and the hard direct-execution block are represented.
Both are required in saved entry data. Unknown values are rejected instead of
being copied into a config entry.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..domain.configuration import (
    DIRECT_EXECUTION_BLOCKED,
    SafeConfiguration,
    UnsafeModeError,
    configuration_for_mode,
)


MODE_FIELD = "mode"
DIRECT_EXECUTION_STATUS_FIELD = "direct_execution_status"


class ConfigurationViolation(ValueError):
    """A config-entry value does not satisfy the safety contract."""


def create_initial_entry(mode_value: object) -> dict[str, str]:
    """Return the only data shape permitted when an entry is created."""

    configuration = _configuration_for(mode_value)
    return {
        MODE_FIELD: configuration.mode,
        DIRECT_EXECUTION_STATUS_FIELD: configuration.direct_execution_status,
    }


def create_options(mode_value: object) -> dict[str, str]:
    """Return the only user-editable option: a safe read-only mode."""

    return {MODE_FIELD: _configuration_for(mode_value).mode}


def effective_configuration(
    entry_data: Mapping[str, Any], options: Mapping[str, Any]
) -> SafeConfiguration:
    """Validate persisted values and return their safe effective meaning."""

    _require_exact_keys(
        entry_data,
        {MODE_FIELD, DIRECT_EXECUTION_STATUS_FIELD},
        "entry data",
    )
    _require_allowed_keys(options, {MODE_FIELD}, "options")

    if entry_data.get(DIRECT_EXECUTION_STATUS_FIELD) != DIRECT_EXECUTION_BLOCKED:
        raise ConfigurationViolation("direct execution must remain blocked")

    mode_value = options.get(MODE_FIELD, entry_data.get(MODE_FIELD))
    return _configuration_for(mode_value)


def _configuration_for(mode_value: object) -> SafeConfiguration:
    try:
        return configuration_for_mode(mode_value)
    except UnsafeModeError as error:
        raise ConfigurationViolation(str(error)) from error


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
