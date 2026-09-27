"""HA-facing input adapter of the lighting path (S-01/S-02, HH-014.b).

Converts immutable Home Assistant state snapshots delivered by the
legacy runtime into normalized core observations bound to core resource
ids. The adapter owns entity bindings and property extraction; the core
never sees HA objects.
"""

from __future__ import annotations

from dataclasses import dataclass
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
        bindings: Mapping[str, EntityBinding],
        clock: ClockPort,
        source_epoch: str,
    ) -> None:
        self._bindings = dict(bindings)
        self._clock = clock
        self._source_epoch = source_epoch

    def observation(
        self,
        entity_id: str,
        old_state: object,
        new_state: object,
    ) -> Observation | None:
        binding = self._bindings.get(entity_id)
        if binding is None or new_state is None:
            return None
        value = self._property_value(binding.property, new_state)
        if value is None:
            return None
        changed = old_state is None or old_state.state != new_state.state
        return Observation(
            observation_id=(
                f"obs-{entity_id}-{self._clock.now_mono_ms()}"
            ),
            resource_id=binding.resource_id,
            property=binding.property,
            value=value,
            unit=binding.unit,
            received_at_utc_ms=self._clock.now_utc_ms(),
            received_at_mono_ms=self._clock.now_mono_ms(),
            boot_id=self._clock.boot_id(),
            source_at_utc_ms=None,
            source_sequence=None,
            source_id=f"ha.{entity_id}",
            source_epoch=self._source_epoch,
            quality=Quality.FRESH if changed else Quality.STALE,
            evidence_kind=Evidence.DEVICE_REPORT,
            binding_revision=0,
            observation_revision=self._clock.now_mono_ms(),
            related_operation_id=None,
            attribution=ObservationAttribution.UNKNOWN,
        )

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
                return round(int(raw) * 100 / 255)
            except (TypeError, ValueError):
                return None
        return None
