"""Light device adapter: typed targets to allowlist HA calls (S-02, S-03).

The adapter never decides rights and never picks targets. It prepares the
exact payload of one domain, performs a single transport call per permit
and normalizes raw device events into core observations. Light through
Zigbee2MQTT uses its HA MQTT entity; no Yandex transport exists here.
"""

from __future__ import annotations

from typing import Mapping

from ...application.core.command_service import (
    CapabilityProfile,
    PreparedCall,
    TransportOutcome,
)
from ...domain.core.models import (
    Evidence,
    LightStateTarget,
    Observation,
    ObservationAttribution,
    Quality,
    SwitchStateTarget,
    Target,
    ValidationIssue,
)

_RAW_ALLOWED_KEYS = frozenset(
    {
        "resource_id",
        "properties",
        "source_sequence",
        "received_at_mono_ms",
        "received_at_utc_ms",
        "source_id",
        "source_epoch",
        "evidence",
        "attribution",
    }
)

_EVIDENCE_BY_RAW = {
    "device_report": Evidence.DEVICE_REPORT,
    "optimistic": Evidence.OPTIMISTIC,
    "retained": Evidence.RESTORED,
    "snapshot": Evidence.RESTORED,
    "adapter_state": Evidence.ADAPTER_STATE,
}

_QUALITY_BY_RAW = {
    "device_report": Quality.FRESH,
    "optimistic": Quality.FRESH,
    "retained": Quality.STALE,
    "snapshot": Quality.STALE,
    "adapter_state": Quality.FRESH,
}


class LightDeviceAdapter:
    """FIRST adapter for the synthetic light stand (FIRST-LIGHT-1)."""

    def __init__(
        self,
        profiles_by_resource: Mapping[str, CapabilityProfile],
        binding_revisions: Mapping[str, int],
        boot_id: str,
        id_factory,
    ) -> None:
        self._profiles = dict(profiles_by_resource)
        self._binding_revisions = dict(binding_revisions)
        self._boot_id = boot_id
        self._id_factory = id_factory

    def capabilities(self, resource_id: str) -> CapabilityProfile:
        return self._profiles[resource_id]

    def prepare(
        self,
        target: Target,
        entity_id: str,
        profile: CapabilityProfile,
        operation_id: str,
        permit_id: str,
    ) -> PreparedCall | ValidationIssue:
        """Build the allowlist payload; no side effect happens here."""

        if isinstance(target, SwitchStateTarget):
            if "on" not in profile.properties:
                return ValidationIssue("unsupported_property", "on")
            return PreparedCall(
                domain="switch",
                service="turn_on" if target.on else "turn_off",
                entity_id=entity_id,
                data={},
                operation_id=operation_id,
                permit_id=permit_id,
                requested_properties=("on",),
            )
        if isinstance(target, LightStateTarget):
            properties = profile.properties
            if "on" not in properties:
                return ValidationIssue("unsupported_property", "on")
            data: dict[str, object] = {}
            requested: list[str] = ["on"]
            if target.on:
                if target.brightness_pct is not None:
                    rule = properties.get("brightness_pct")
                    if rule is None:
                        return ValidationIssue(
                            "unsupported_property", "brightness_pct"
                        )
                    value = int(target.brightness_pct)
                    if rule.min_value is not None and value < rule.min_value:
                        return ValidationIssue(
                            "value_out_of_range", str(value)
                        )
                    if rule.max_value is not None and value > rule.max_value:
                        return ValidationIssue(
                            "value_out_of_range", str(value)
                        )
                    if (
                        rule.step is not None
                        and rule.step > 0
                        and (target.brightness_pct - rule.min_value)
                        % rule.step
                        != 0
                    ):
                        return ValidationIssue(
                            "value_off_step", str(target.brightness_pct)
                        )
                    data["brightness_pct"] = value
                    requested.append("brightness_pct")
                if target.color_temperature_k is not None:
                    if "color_temperature_k" not in properties:
                        return ValidationIssue(
                            "unsupported_property", "color_temperature_k"
                        )
                    data["color_temperature_k"] = target.color_temperature_k
                    requested.append("color_temperature_k")
            return PreparedCall(
                domain="light",
                service="turn_on" if target.on else "turn_off",
                entity_id=entity_id,
                data=data,
                operation_id=operation_id,
                permit_id=permit_id,
                requested_properties=tuple(requested),
            )
        return ValidationIssue("unsupported_target", repr(target))

    def dispatch_once(self, prepared: PreparedCall, transport) -> TransportOutcome:
        """One application-level dispatch for one permit (K-08)."""

        return transport.call(prepared)

    def normalize(self, raw: Mapping[str, object]) -> tuple[Observation, ...]:
        unknown = set(raw) - _RAW_ALLOWED_KEYS
        if unknown:
            raise ValueError(f"unknown raw event fields: {sorted(unknown)}")
        resource_id = str(raw["resource_id"])
        evidence_raw = str(raw["evidence"])
        if evidence_raw not in _EVIDENCE_BY_RAW:
            raise ValueError(f"unknown evidence kind: {evidence_raw}")
        properties = raw["properties"]
        if not isinstance(properties, Mapping):
            raise ValueError("properties must be a mapping")
        sequence = raw.get("source_sequence")
        binding_revision = self._binding_revisions[resource_id]
        received_mono = int(raw["received_at_mono_ms"])  # type: ignore[arg-type]
        received_utc = int(raw.get("received_at_utc_ms", 0))  # type: ignore[arg-type]
        if "source_id" not in raw or "source_epoch" not in raw:
            raise ValueError("raw event must carry source_id and source_epoch")
        source_id = str(raw["source_id"])
        source_epoch = str(raw["source_epoch"])
        observations: list[Observation] = []
        for name in sorted(properties):
            observations.append(
                Observation(
                    observation_id=f"obs-{self._id_factory()}",
                    resource_id=resource_id,
                    property=name,
                    value=properties[name],
                    unit=self._unit_of(resource_id, name),
                    received_at_utc_ms=received_utc,
                    received_at_mono_ms=received_mono,
                    boot_id=self._boot_id,
                    source_at_utc_ms=None,
                    source_sequence=(
                        int(sequence) if sequence is not None else None  # type: ignore[arg-type]
                    ),
                    source_id=source_id,
                    source_epoch=source_epoch,
                    quality=_QUALITY_BY_RAW[evidence_raw],
                    evidence_kind=_EVIDENCE_BY_RAW[evidence_raw],
                    binding_revision=binding_revision,
                    observation_revision=received_mono,
                    related_operation_id=None,
                    attribution=self._attribution_of(raw),
                )
            )
        return tuple(observations)

    def _unit_of(self, resource_id: str, property_name: str) -> str:
        profile = self._profiles.get(resource_id)
        if profile is None:
            raise ValueError(f"no capability profile for {resource_id}")
        rule = profile.properties.get(property_name)
        if rule is None:
            raise ValueError(
                f"property {property_name} is outside the capability profile"
            )
        return rule.unit

    @staticmethod
    def _attribution_of(raw: Mapping[str, object]) -> ObservationAttribution:
        value = str(raw.get("attribution", "unknown"))
        if value == "manual":
            return ObservationAttribution.MANUAL
        if value == "automation":
            return ObservationAttribution.AUTOMATION
        return ObservationAttribution.UNKNOWN
