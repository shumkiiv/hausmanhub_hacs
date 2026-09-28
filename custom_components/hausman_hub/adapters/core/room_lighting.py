"""HA-facing input adapter of the lighting path (S-01/S-02, HH-014.b).

Converts immutable Home Assistant state snapshots delivered by the
legacy runtime into normalized core observations bound to core resource
ids. The adapter owns entity bindings and property extraction; the core
never sees HA objects.
"""

from __future__ import annotations

import itertools
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
        binding = self._bindings.get(entity_id)
        if binding is None or new_state is None:
            return None
        raw_state = getattr(new_state, "state", None)
        # An unknown/unavailable report of the switching state is never
        # an OFF: it becomes an explicit INVALID observation that
        # replaces the previous fact, so neither a manual no_op nor an
        # evidence read can mistake silence for a proven off
        # (SEC-S06-04).
        invalid_state = binding.property == "on" and raw_state not in (
            "on",
            "off",
        )
        value = self._property_value(binding.property, new_state)
        if value is None and not invalid_state:
            return None
        changed = old_state is None or old_state.state != new_state.state
        if invalid_state:
            quality = Quality.INVALID
        else:
            quality = Quality.FRESH if changed else Quality.STALE
        return Observation(
            observation_id=(
                f"obs-{binding.resource_id}-{next(self._observation_seq)}"
            ),
            resource_id=binding.resource_id,
            property=binding.property,
            value=bool(value) if value is not None else False,
            unit=binding.unit,
            received_at_utc_ms=self._clock.now_utc_ms(),
            received_at_mono_ms=self._clock.now_mono_ms(),
            boot_id=self._clock.boot_id(),
            source_at_utc_ms=None,
            source_sequence=None,
            source_id=f"ha_state:{binding.resource_id}",
            source_epoch=self._source_epoch,
            quality=quality,
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
