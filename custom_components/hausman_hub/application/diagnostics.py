"""Read-only diagnostics use case built from an explicit allow-list."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..domain.observation import HomeSummary
from ..domain.configuration import DIRECT_EXECUTION_BLOCKED
from .configuration import effective_configuration


def diagnostics_snapshot(
    entry_data: Mapping[str, Any],
    options: Mapping[str, Any],
    home_summary: HomeSummary,
) -> dict[str, object]:
    """Return a redacted snapshot without copying detailed home data.

    Safety values come from the validated two-field configuration model. The
    only home data it accepts is a count-only domain object. This is stricter
    than removing known sensitive keys: names, identifiers, readings, history,
    and arbitrary config data never enter the export in the first place.
    """

    configuration = effective_configuration(entry_data, options)
    return {
        "entry_summary": {
            "mode": configuration.mode,
            "single_config_entry": True,
        },
        "safety_model": {
            "device_authority": "not_granted",
            "direct_execution_status": DIRECT_EXECUTION_BLOCKED,
            "proxy_status": "not_approved",
        },
        "shadow_parity": {
            "parity_status": "unresolved",
            "evidence_status": "not_collected",
        },
        "repairs_summary": {
            "automatic_repairs": "disabled",
            "manual_guidance_only": True,
        },
        "home_summary": {
            "areas_count": home_summary.areas_count,
            "devices_count": home_summary.devices_count,
            "entities_count": home_summary.entities_count,
            "sensors_count": home_summary.sensors_count,
            "available_entities_count": home_summary.available_entities_count,
            "unavailable_entities_count": home_summary.unavailable_entities_count,
            "unknown_entities_count": home_summary.unknown_entities_count,
            "not_reported_entities_count": home_summary.not_reported_entities_count,
        },
        "redaction_report": {
            "status": "passed",
            "strategy": "allow_list_only_with_aggregate_home_summary",
        },
    }
