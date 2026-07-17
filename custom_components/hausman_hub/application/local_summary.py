"""Use case for the explicitly approved local aggregate summary access."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ..domain.observation import HomeSummary
from .configuration import ConfigurationViolation, effective_configuration

HOME_SUMMARY_COUNT_KEYS = (
    "areas_count",
    "devices_count",
    "entities_count",
    "sensors_count",
    "available_entities_count",
    "unavailable_entities_count",
    "unknown_entities_count",
    "not_reported_entities_count",
    "disabled_entities_count",
)


def local_summary_snapshot(
    entry_data: Mapping[str, Any],
    options: Mapping[str, Any],
    home_summary_supplier: Callable[[], HomeSummary],
) -> dict[str, int]:
    """Validate first, then request and return the fixed nine-count summary.

    This use case accepts no user, address, token, entity identifier, state
    value, or command. The outer Home Assistant adapter is responsible for
    authentication and local-network checks before it supplies the reader.
    """

    configuration = effective_configuration(entry_data, options)
    if not configuration.local_summary_enabled:
        raise ConfigurationViolation("local summary page is disabled")
    return home_summary_payload(home_summary_supplier())


def home_summary_payload(home_summary: HomeSummary) -> dict[str, int]:
    """Build the only permitted dynamic data shape from aggregate counts."""

    return {
        key: getattr(home_summary, key)
        for key in HOME_SUMMARY_COUNT_KEYS
    }
