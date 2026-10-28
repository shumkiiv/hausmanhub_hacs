"""Consumer-owned physical evidence, separate from native HA state/history."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
import math


@dataclass(frozen=True, slots=True)
class PhysicalLightObservation:
    entity_id: str
    value: str
    observed_at_ms: int
    session: str
    sequence: int
    ieee: str
    endpoint: int


@dataclass(frozen=True, slots=True)
class ObservedLightState:
    """Read-only view; never manufacture last_changed/reported or context."""

    native: object
    observation: PhysicalLightObservation | None
    brightness_observation: PhysicalBrightnessObservation | None = None
    color_observation: PhysicalColorObservation | None = None

    def __getattr__(self, name: str) -> object:
        return getattr(self.native, name)


def physical_observed_ms(
    state: ObservedLightState, *, now_ms: int, freshness_ms: int
) -> int | None:
    observation = state.observation
    if observation is None:
        return None
    if (
        observation.entity_id != getattr(state.native, "entity_id", None)
        or observation.value not in {"on", "off"}
        or observation.value != str(getattr(state.native, "state", "unknown")).lower()
    ):
        return None
    age = now_ms - observation.observed_at_ms
    return observation.observed_at_ms if 0 <= age <= freshness_ms else None


def physical_evidence_token(state: object) -> str:
    if not isinstance(state, ObservedLightState) or state.observation is None:
        return ""
    report = state.observation
    return f"|physical:{report.session}:{report.sequence}"


@dataclass(frozen=True, slots=True)
class PhysicalBrightnessObservation:
    entity_id: str
    value: int
    observed_at_ms: int
    session: str
    sequence: int
    ieee: str
    endpoint: int


def physical_brightness_observed_ms(
    state: object, *, now_ms: int, freshness_ms: int = 300_000,
) -> int | None:
    """Validate level evidence without renewing or inferring ON/OFF evidence."""
    if not isinstance(state, ObservedLightState):
        return None
    report = state.brightness_observation
    if report is None or report.entity_id != getattr(state.native, "entity_id", None):
        return None
    attrs = getattr(state.native, "attributes", {})
    native_level = attrs.get("brightness") if isinstance(attrs, Mapping) else None
    if (
        type(report.value) is not int or not 0 <= report.value <= 254
        or type(native_level) not in (int, float)
        or not abs(native_level - report.value) <= 1
        or type(report.observed_at_ms) is not int
    ):
        return None
    age = now_ms - report.observed_at_ms
    return report.observed_at_ms if 0 <= age <= freshness_ms else None


def paired_recovery_brightness_observed_ms(
    state: object, *, now_ms: int, freshness_ms: int = 300_000,
) -> int | None:
    """Owner-approved dark-pair evidence, not the general brightness policy.

    An OFF light may hide its saved level in HA. It is still OFF, not an
    inferred zero. An ON light must agree on exact zero in both sources.
    """
    if not isinstance(state, ObservedLightState):
        return None
    # Import at the call boundary: power_evidence consumes our DTOs during
    # module loading, while its existing trust policy remains the sole owner.
    from .power_evidence import state_is_restored_or_cached
    if state_is_restored_or_cached(state):
        return None
    report, power = state.brightness_observation, state.observation
    if (report is None or power is None
            or report.entity_id != getattr(state.native, "entity_id", None)
            or report.session != power.session
            or type(report.value) is not int or not 0 <= report.value <= 254
            or type(report.observed_at_ms) is not int
            or physical_observed_ms(state, now_ms=now_ms, freshness_ms=freshness_ms) is None):
        return None
    attrs = getattr(state.native, "attributes", {})
    level = attrs.get("brightness") if isinstance(attrs, Mapping) else None
    if power.value == "on":
        if type(level) not in {int, float} or level != 0 or report.value != 0:
            return None
    elif power.value == "off":
        if level is not None and (type(level) not in {int, float}
                or not math.isfinite(level) or not 0 <= level <= 255
                or abs(level - report.value) > 1):
            return None
    else:
        return None
    age = now_ms - report.observed_at_ms
    return report.observed_at_ms if 0 <= age <= freshness_ms else None


@dataclass(frozen=True, slots=True)
class PhysicalColorObservation:
    entity_id: str
    value: int  # Device CCT in mired, before the room's inversion/calibration.
    observed_at_ms: int
    session: str
    sequence: int
    ieee: str
    endpoint: int


def physical_color_observed_ms(
    state: object, *, now_ms: int, freshness_ms: int = 300_000,
) -> int | None:
    """A CCT report proves colour only, never power, level or ownership."""
    if not isinstance(state, ObservedLightState):
        return None
    report = state.color_observation
    if report is None or report.entity_id != getattr(state.native, "entity_id", None):
        return None
    attrs = getattr(state.native, "attributes", {})
    kelvin = attrs.get("color_temp_kelvin") if isinstance(attrs, Mapping) else None
    if (type(report.value) is not int or not 153 <= report.value <= 500
        or type(kelvin) not in (int, float) or not math.isfinite(kelvin) or kelvin <= 0
        or abs(1_000_000 / kelvin - report.value) > 1
        or type(report.observed_at_ms) is not int):
        return None
    age = now_ms - report.observed_at_ms
    return report.observed_at_ms if 0 <= age <= freshness_ms else None
