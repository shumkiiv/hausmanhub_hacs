"""Use case for the explicitly approved local aggregate summary access."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..domain.observation import HomeSummary
from .configuration import effective_configuration


def local_summary_snapshot(
    entry_data: Mapping[str, Any],
    options: Mapping[str, Any],
    home_summary: HomeSummary,
) -> dict[str, int]:
    """Return the fixed nine-count summary after rechecking safe configuration.

    This use case accepts no user, address, token, entity identifier, state
    value, or command. The outer Home Assistant adapter is responsible for
    authentication and local-network checks before it supplies this summary.
    """

    effective_configuration(entry_data, options)
    return home_summary_payload(home_summary)


def home_summary_payload(home_summary: HomeSummary) -> dict[str, int]:
    """Build the only permitted dynamic data shape from aggregate counts."""

    return {
        "areas_count": home_summary.areas_count,
        "devices_count": home_summary.devices_count,
        "entities_count": home_summary.entities_count,
        "sensors_count": home_summary.sensors_count,
        "available_entities_count": home_summary.available_entities_count,
        "unavailable_entities_count": home_summary.unavailable_entities_count,
        "unknown_entities_count": home_summary.unknown_entities_count,
        "not_reported_entities_count": home_summary.not_reported_entities_count,
        "disabled_entities_count": home_summary.disabled_entities_count,
    }
