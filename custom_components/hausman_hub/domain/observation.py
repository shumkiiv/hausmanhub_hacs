"""Pure data rules for a read-only aggregate home summary.

The domain model deliberately represents only category and availability totals.
It has no field for an entity, device, area, address, measurement, or history
value, so those details cannot cross the boundary into diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


Availability = Literal["available", "unavailable", "unknown", "not_reported"]
VALID_AVAILABILITY = frozenset(
    {"available", "unavailable", "unknown", "not_reported"}
)


@dataclass(frozen=True, slots=True)
class RegisteredEntity:
    """One registered item reduced to the two facts this summary may use."""

    domain: str
    availability: Availability

    def __post_init__(self) -> None:
        """Reject an unmodelled category before aggregation begins."""

        if self.availability not in VALID_AVAILABILITY:
            raise ValueError("entity availability must be an approved category")


@dataclass(frozen=True, slots=True)
class HomeSummary:
    """An identifier-free count of the locally visible Home Assistant inventory."""

    areas_count: int
    devices_count: int
    entities_count: int
    sensors_count: int
    available_entities_count: int
    unavailable_entities_count: int
    unknown_entities_count: int
    not_reported_entities_count: int

    def __post_init__(self) -> None:
        """Reject impossible aggregate values before they can be exported."""

        values = (
            self.areas_count,
            self.devices_count,
            self.entities_count,
            self.sensors_count,
            self.available_entities_count,
            self.unavailable_entities_count,
            self.unknown_entities_count,
            self.not_reported_entities_count,
        )
        if any(type(value) is not int or value < 0 for value in values):
            raise ValueError("home summary counts must be non-negative integers")
        if self.sensors_count > self.entities_count:
            raise ValueError("sensor count must not exceed entity count")
        if (
            self.available_entities_count
            + self.unavailable_entities_count
            + self.unknown_entities_count
            + self.not_reported_entities_count
            != self.entities_count
        ):
            raise ValueError("availability counts must equal entity count")
