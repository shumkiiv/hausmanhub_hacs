"""Read-only diagnostics use case built from an explicit allow-list."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..domain.observation import HomeSummary
from ..domain.configuration import DIRECT_EXECUTION_BLOCKED, SafeConfiguration
from ..domain.control import CANARY_CONTROL_SCOPE
from .configuration import effective_configuration
from .local_summary import home_summary_payload


def diagnostics_snapshot(
    entry_data: Mapping[str, Any],
    options: Mapping[str, Any],
    home_summary: HomeSummary,
) -> dict[str, object]:
    """Return a redacted snapshot without copying detailed home data.

    Safety values come from the validated effective configuration model. The
    only home data it accepts is a count-only domain object. This is stricter
    than removing known sensitive keys: names, identifiers, readings, history,
    and arbitrary config data never enter the export in the first place.
    """

    return diagnostics_snapshot_for_configuration(
        effective_configuration(entry_data, options),
        home_summary,
    )


def diagnostics_snapshot_for_configuration(
    configuration: SafeConfiguration,
    home_summary: HomeSummary,
) -> dict[str, object]:
    """Return the approved snapshot for an already-validated active setup."""

    return {
        "entry_summary": {
            "mode": configuration.mode,
            "local_summary_enabled": configuration.local_summary_enabled,
            "summary_update_interval": configuration.summary_update_interval,
            "canary_control_enabled": configuration.canary_control_enabled,
            "canary_control_scope": CANARY_CONTROL_SCOPE,
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
        "home_summary": home_summary_payload(home_summary),
        "redaction_report": {
            "status": "passed",
            "strategy": "allow_list_only_with_aggregate_home_summary",
        },
    }


def unavailable_diagnostics_snapshot() -> dict[str, str]:
    """Return the fixed report used without reading any home information."""

    return {"diagnostics_status": "unavailable"}
