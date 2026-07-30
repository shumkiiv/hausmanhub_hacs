"""Bounded command-free evidence collected from climate comparisons."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re

from .climate_comparison import ClimateComparisonReason, ClimateComparisonStatus


CLIMATE_SHADOW_WINDOW_VERSION = 1
CLIMATE_SHADOW_SAMPLE_INTERVAL_SECONDS = 5 * 60
CLIMATE_SHADOW_RETENTION_SECONDS = 7 * 24 * 60 * 60
CLIMATE_SHADOW_MINIMUM_SAMPLE_COUNT = 24
CLIMATE_SHADOW_MINIMUM_SPAN_SECONDS = 6 * 60 * 60
CLIMATE_SHADOW_FRESHNESS_SECONDS = 10 * 60
CLIMATE_SHADOW_MINIMUM_ALIGNMENT_RATIO = 1.0
CLIMATE_SHADOW_MAX_SAMPLES = (
    CLIMATE_SHADOW_RETENTION_SECONDS // CLIMATE_SHADOW_SAMPLE_INTERVAL_SECONDS
)
_STABLE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class ClimateShadowWindowViolation(ValueError):
    """Stored or calculated shadow evidence is invalid."""


class ClimateShadowVerdict(StrEnum):
    """Whether one room has enough evidence for a later canary decision."""

    READY = "ready"
    DIVERGED = "diverged"
    INSUFFICIENT_DATA = "insufficient_data"


class ClimateShadowWindowReason(StrEnum):
    """Bounded explanation for a room-level window verdict."""

    NO_OBSERVATIONS = "no_observations"
    INSUFFICIENT_OBSERVATIONS = "insufficient_observations"
    INSUFFICIENT_TIMESPAN = "insufficient_timespan"
    LATEST_OBSERVATION_STALE = "latest_observation_stale"
    DIVERGENCE_OBSERVED = "divergence_observed"
    ALIGNMENT_BELOW_THRESHOLD = "alignment_below_threshold"
    NOT_COMPARABLE_OBSERVATIONS = "not_comparable_observations"


@dataclass(frozen=True, slots=True)
class ClimateShadowWindowPolicy:
    """Explicit evidence thresholds; none of them can authorize commands."""

    sample_interval_seconds: int = CLIMATE_SHADOW_SAMPLE_INTERVAL_SECONDS
    retention_seconds: int = CLIMATE_SHADOW_RETENTION_SECONDS
    minimum_sample_count: int = CLIMATE_SHADOW_MINIMUM_SAMPLE_COUNT
    minimum_span_seconds: int = CLIMATE_SHADOW_MINIMUM_SPAN_SECONDS
    freshness_seconds: int = CLIMATE_SHADOW_FRESHNESS_SECONDS
    minimum_alignment_ratio: float = CLIMATE_SHADOW_MINIMUM_ALIGNMENT_RATIO

    def __post_init__(self) -> None:
        for value, minimum, maximum, label in (
            (self.sample_interval_seconds, 60, 3600, "sample interval"),
            (self.retention_seconds, 3600, 2592000, "retention"),
            (self.minimum_sample_count, 2, CLIMATE_SHADOW_MAX_SAMPLES, "sample count"),
            (self.minimum_span_seconds, 60, 2592000, "minimum span"),
            (self.freshness_seconds, 60, 86400, "freshness"),
        ):
            if type(value) is not int or not minimum <= value <= maximum:
                raise ClimateShadowWindowViolation(f"{label} is outside the safe range")
        if (
            not isinstance(self.minimum_alignment_ratio, (int, float))
            or isinstance(self.minimum_alignment_ratio, bool)
            or not 0 <= float(self.minimum_alignment_ratio) <= 1
        ):
            raise ClimateShadowWindowViolation("alignment ratio is outside the safe range")


@dataclass(frozen=True, slots=True)
class ClimateShadowRoomSample:
    """Redacted result for one room at one observation time."""

    room_id: str
    status: ClimateComparisonStatus
    reasons: tuple[ClimateComparisonReason, ...]

    def __post_init__(self) -> None:
        _stable_id(self.room_id, "shadow room id")
        if not isinstance(self.status, ClimateComparisonStatus):
            raise ClimateShadowWindowViolation("shadow status must be approved")
        if type(self.reasons) is not tuple or any(
            not isinstance(reason, ClimateComparisonReason) for reason in self.reasons
        ):
            raise ClimateShadowWindowViolation("shadow reasons must be typed")
        if len(self.reasons) != len(set(self.reasons)):
            raise ClimateShadowWindowViolation("shadow reasons must be unique")
        if self.reasons != tuple(
            reason for reason in ClimateComparisonReason if reason in self.reasons
        ):
            raise ClimateShadowWindowViolation("shadow reasons must use fixed order")


@dataclass(frozen=True, slots=True)
class ClimateShadowSample:
    """One immutable, private-binding-free comparison sample."""

    observed_at: int
    rooms: tuple[ClimateShadowRoomSample, ...]

    def __post_init__(self) -> None:
        if type(self.observed_at) is not int or self.observed_at < 0:
            raise ClimateShadowWindowViolation("shadow observation time is invalid")
        if type(self.rooms) is not tuple or not self.rooms or any(
            not isinstance(room, ClimateShadowRoomSample) for room in self.rooms
        ):
            raise ClimateShadowWindowViolation("shadow rooms must be a non-empty typed tuple")
        room_ids = tuple(room.room_id for room in self.rooms)
        if len(room_ids) != len(set(room_ids)) or room_ids != tuple(sorted(room_ids)):
            raise ClimateShadowWindowViolation("shadow room ids must be unique and sorted")


@dataclass(frozen=True, slots=True)
class ClimateShadowWindowState:
    """Durable bounded evidence; it contains no HA entity or command payload."""

    samples: tuple[ClimateShadowSample, ...] = ()
    version: int = CLIMATE_SHADOW_WINDOW_VERSION

    def __post_init__(self) -> None:
        if self.version != CLIMATE_SHADOW_WINDOW_VERSION:
            raise ClimateShadowWindowViolation("shadow storage version is unsupported")
        if type(self.samples) is not tuple or any(
            not isinstance(sample, ClimateShadowSample) for sample in self.samples
        ):
            raise ClimateShadowWindowViolation("shadow samples must be typed")
        times = tuple(sample.observed_at for sample in self.samples)
        if times != tuple(sorted(set(times))):
            raise ClimateShadowWindowViolation("shadow samples must be unique and ordered")
        if len(times) > CLIMATE_SHADOW_MAX_SAMPLES:
            raise ClimateShadowWindowViolation("shadow window exceeds the retention bound")


@dataclass(frozen=True, slots=True)
class ClimateShadowRoomVerdict:
    """Aggregated evidence for one stable room id."""

    room_id: str
    verdict: ClimateShadowVerdict
    reasons: tuple[ClimateShadowWindowReason, ...]
    observation_count: int
    comparable_count: int
    aligned_count: int
    diverged_count: int
    not_comparable_count: int
    alignment_ratio: float | None
    first_observed_at: int | None
    latest_observed_at: int | None
    latest_status: ClimateComparisonStatus | None
    fresh: bool

    def __post_init__(self) -> None:
        _stable_id(self.room_id, "shadow verdict room id")
        if not isinstance(self.verdict, ClimateShadowVerdict):
            raise ClimateShadowWindowViolation("shadow verdict must be approved")
        if type(self.reasons) is not tuple or any(
            not isinstance(reason, ClimateShadowWindowReason) for reason in self.reasons
        ):
            raise ClimateShadowWindowViolation("shadow verdict reasons must be typed")
        if self.reasons != tuple(
            reason for reason in ClimateShadowWindowReason if reason in self.reasons
        ):
            raise ClimateShadowWindowViolation("shadow verdict reasons must use fixed order")
        counts = (
            self.observation_count,
            self.comparable_count,
            self.aligned_count,
            self.diverged_count,
            self.not_comparable_count,
        )
        if any(type(value) is not int or value < 0 for value in counts):
            raise ClimateShadowWindowViolation("shadow verdict counts are invalid")
        if self.aligned_count + self.diverged_count != self.comparable_count:
            raise ClimateShadowWindowViolation("comparable count is inconsistent")
        if self.comparable_count + self.not_comparable_count != self.observation_count:
            raise ClimateShadowWindowViolation("observation count is inconsistent")
        if self.alignment_ratio is not None and not 0 <= self.alignment_ratio <= 1:
            raise ClimateShadowWindowViolation("shadow alignment ratio is invalid")


def _stable_id(value: object, label: str) -> None:
    if not isinstance(value, str) or not _STABLE_ID.fullmatch(value):
        raise ClimateShadowWindowViolation(f"{label} must be a stable HausmanHub id")
