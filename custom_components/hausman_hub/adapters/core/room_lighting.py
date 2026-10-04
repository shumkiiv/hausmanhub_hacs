"""HA-facing input adapter of the lighting path (S-01/S-02, HH-014.b).

Converts immutable Home Assistant state snapshots delivered by the
legacy runtime into normalized core observations bound to core resource
ids. The adapter owns entity bindings and property extraction; the core
never sees HA objects.
"""

from __future__ import annotations

import itertools
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass, replace
from typing import Mapping

from ...application.core.command_service import ClockPort
from ...domain.core.models import (
    Observation,
    ObservationAttribution,
    Evidence,
    Quality,
)


@dataclass(frozen=True, slots=True)
class EntityBinding:
    """One HA entity mapped to one core resource property."""

    resource_id: str
    property: str
    unit: str = "bool"


class RoomLightingInputAdapter:
    """Bind HA entities of one room profile to core resources."""

    def __init__(
        self,
        bindings: Mapping[str, EntityBinding | tuple[EntityBinding, ...]],
        clock: ClockPort,
        source_epoch: str,
        binding_revision: int = 1,
    ) -> None:
        self._bindings = {
            entity_id: value if isinstance(value, tuple) else (value,)
            for entity_id, value in bindings.items()
        }
        self._clock = clock
        self._source_epoch = source_epoch
        self._binding_revision = binding_revision
        self._report_sequence = itertools.count(1)
        # Observation ids are per-adapter sequential: stable, unique per
        # boot and free of HA entity ids (AUD-LP-ISO01 cycle 6: the core
        # sees resource ids only; HA stays inside the adapter).
        self._observation_seq = itertools.count(1)

    def observation(
        self,
        entity_id: str,
        old_state: object,
        new_state: object,
    ) -> Observation | None:
        observations = self.observations(entity_id, old_state, new_state)
        if not observations:
            return None
        observation = observations[0]
        # The legacy single-property port exposed integer percentages.  The
        # batch port deliberately keeps the precise Decimal needed by the
        # confirmation path; retaining this conversion keeps old callers
        # compatible without weakening the batch evidence.
        if observation.property == "brightness_pct":
            return replace(observation, value=round(observation.value))
        return observation

    def observations(
        self,
        entity_id: str,
        old_state: object,
        new_state: object,
        *,
        trusted_device_report: bool = False,
        source_sequence: int | None = None,
    ) -> tuple[Observation, ...]:
        bindings = self._bindings.get(entity_id)
        if bindings is None:
            return ()
        if source_sequence is None:
            source_sequence = next(self._report_sequence)
        if source_sequence <= 0:
            return ()
        now_utc = self._clock.now_utc_ms()
        now_mono = self._clock.now_mono_ms()
        evidence = Evidence.DEVICE_REPORT if trusted_device_report else Evidence.ADAPTER_STATE
        # A new runtime state is current knowledge even when its provenance
        # is merely the adapter.  It may satisfy a manual no-op, but never
        # becomes device-report evidence for confirmation or AUTO ownership.
        quality = (
            Quality.FRESH
            if trusted_device_report or old_state is not new_state
            else Quality.STALE
        )
        result: list[Observation] = []
        raw_state = getattr(new_state, "state", None)
        # An unknown/unavailable report of the switching state is never
        # an OFF: it becomes an explicit INVALID observation that
        # replaces the previous fact, so neither a manual no_op nor an
        # evidence read can mistake silence for a proven off
        # (SEC-S06-04).
        attributes = getattr(new_state, "attributes", {})
        if not isinstance(attributes, Mapping):
            attributes = {}
        for binding in bindings:
            invalid_state = binding.property in ("on", "occupancy") and raw_state not in (
                "on", "off",
            )
            value = (
                self._property_value(binding.property, new_state)
                if new_state is not None else None
            )
            attribute_present = (
                binding.property == "brightness_pct" and "brightness" in attributes
            ) or (
                binding.property == "color_temperature_k"
                and "color_temp_kelvin" in attributes
            )
            invalid_attribute = attribute_present and value is None
            if value is None and not invalid_state and not invalid_attribute:
                continue
            result.append(Observation(
                observation_id=f"obs-{binding.resource_id}-{next(self._observation_seq)}",
                resource_id=binding.resource_id,
                property=binding.property,
                value=value,
                unit=binding.unit,
                received_at_utc_ms=now_utc,
                received_at_mono_ms=now_mono,
                boot_id=self._clock.boot_id(),
                source_at_utc_ms=now_utc,
                source_sequence=source_sequence,
                source_id=f"ha_state:{binding.resource_id}",
                source_epoch=self._source_epoch,
                quality=(
                    Quality.INVALID
                    if invalid_state or invalid_attribute
                    else quality
                ),
                evidence_kind=evidence,
                binding_revision=self._binding_revision,
                observation_revision=now_mono,
                related_operation_id=None,
                attribution=ObservationAttribution.UNKNOWN,
            ))
        return tuple(result)

    @staticmethod
    def _property_value(property_name: str, state: State):
        if property_name == "on":
            return state.state == "on"
        if property_name == "occupancy":
            if state.state in ("on", "off"):
                return state.state == "on"
            return None
        if property_name == "brightness_pct":
            raw = state.attributes.get("brightness")
            if raw is None:
                return None
            try:
                raw_value = Decimal(str(raw))
                if (
                    not raw_value.is_finite()
                    or not Decimal(0) <= raw_value <= Decimal(255)
                ):
                    return None
                return raw_value * Decimal(100) / Decimal(255)
            except (InvalidOperation, TypeError, ValueError, OverflowError):
                return None
        if property_name == "color_temperature_k":
            raw = state.attributes.get("color_temp_kelvin")
            if isinstance(raw, bool):
                return None
            try:
                raw_value = Decimal(str(raw))
                if (
                    not raw_value.is_finite()
                    or not Decimal(1000) <= raw_value <= Decimal(10000)
                    or raw_value != raw_value.to_integral_value()
                ):
                    return None
            except (InvalidOperation, TypeError, ValueError, OverflowError):
                return None
            return int(raw_value)
        return None
