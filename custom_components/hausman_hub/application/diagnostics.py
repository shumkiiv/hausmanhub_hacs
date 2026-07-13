"""Read-only diagnostics use case built from an explicit allow-list."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..domain.configuration import DIRECT_EXECUTION_BLOCKED
from .configuration import effective_configuration


def diagnostics_snapshot(
    entry_data: Mapping[str, Any], options: Mapping[str, Any]
) -> dict[str, object]:
    """Return a redacted snapshot without copying entry data or options.

    The function derives every value from the validated two-field safety model.
    This is stricter than removing known secret keys: arbitrary config data is
    never included in the first place.
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
        "redaction_report": {
            "status": "passed",
            "strategy": "allow_list_only",
        },
    }
