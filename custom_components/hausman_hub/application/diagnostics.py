"""Read-only diagnostics use case built from an explicit allow-list."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..domain.observation import HomeSummary
from ..domain.configuration import DIRECT_EXECUTION_BLOCKED, SafeConfiguration
from ..domain.control import CANARY_CONTROL_SCOPE
from .configuration import effective_configuration
from .local_summary import home_summary_payload


CLIMATE_RUNTIME_STATUSES = frozenset(
    {"disabled", "unavailable", "not_refreshed", "fresh", "stale"}
)

CLIMATE_OUTDOOR_SOURCES = frozenset(
    {"none", "physical_sensor", "physical_sensor_stale", "weather_provider"}
)


@dataclass(frozen=True, slots=True)
class ClimateDiagnosticsSummary:
    """Validated coarse climate values that are safe to export."""

    runtime_status: str
    registry_rooms: int
    registry_devices: int
    outdoor_temperature_source: str = "none"
    outdoor_source_divergence_c: float | None = None

    def __post_init__(self) -> None:
        if self.runtime_status not in CLIMATE_RUNTIME_STATUSES:
            raise ValueError("climate diagnostics status is unsupported")
        if type(self.registry_rooms) is not int or not 0 <= self.registry_rooms <= 128:
            raise ValueError("climate diagnostics room count is invalid")
        if type(self.registry_devices) is not int or not 0 <= self.registry_devices <= 512:
            raise ValueError("climate diagnostics device count is invalid")
        if self.outdoor_temperature_source not in CLIMATE_OUTDOOR_SOURCES:
            raise ValueError("climate diagnostics outdoor source is unsupported")
        if self.outdoor_source_divergence_c is not None and (
            not isinstance(self.outdoor_source_divergence_c, (int, float))
            or isinstance(self.outdoor_source_divergence_c, bool)
            or not 0 <= self.outdoor_source_divergence_c <= 160
        ):
            raise ValueError("climate diagnostics outdoor divergence is invalid")


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
    climate_runtime_summary: ClimateDiagnosticsSummary | None = None,
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
            "device_authority": (
                "single_climate_canary"
                if configuration.climate_bridge_mode.value == "canary"
                else "not_granted"
            ),
            "direct_execution_status": DIRECT_EXECUTION_BLOCKED,
            "proxy_status": (
                "typed_climate_adapter_only"
                if configuration.climate_bridge_mode.value != "disabled"
                else "not_approved"
            ),
        },
        "climate_bridge": {
            "mode": configuration.climate_bridge_mode.value,
            "target_configured": configuration.climate_bridge_target is not None,
            "canary_scope": (
                "single_room"
                if configuration.climate_bridge_mode.value == "canary"
                else "none"
            ),
            "runtime_status": (
                climate_runtime_summary.runtime_status
                if climate_runtime_summary is not None
                else "unavailable"
            ),
            "registry_rooms": (
                climate_runtime_summary.registry_rooms
                if climate_runtime_summary is not None
                else 0
            ),
            "registry_devices": (
                climate_runtime_summary.registry_devices
                if climate_runtime_summary is not None
                else 0
            ),
            "outdoor_temperature_source": (
                climate_runtime_summary.outdoor_temperature_source
                if climate_runtime_summary is not None
                else "none"
            ),
            "outdoor_source_divergence_c": (
                climate_runtime_summary.outdoor_source_divergence_c
                if climate_runtime_summary is not None
                else None
            ),
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
