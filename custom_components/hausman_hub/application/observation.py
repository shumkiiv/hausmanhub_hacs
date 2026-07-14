"""Create a safe aggregate summary from already-redacted inventory facts."""

from __future__ import annotations

from collections.abc import Iterable

from ..domain.observation import HomeSummary, RegisteredEntity


def create_home_summary(
    areas_count: int,
    devices_count: int,
    entities: Iterable[RegisteredEntity],
) -> HomeSummary:
    """Count inventory facts without retaining names, identifiers, or values."""

    availability_counts = {
        "available": 0,
        "unavailable": 0,
        "unknown": 0,
        "not_reported": 0,
    }
    entities_count = 0
    sensors_count = 0
    for entity in entities:
        entities_count += 1
        if entity.domain == "sensor":
            sensors_count += 1
        availability_counts[entity.availability] += 1

    return HomeSummary(
        areas_count=areas_count,
        devices_count=devices_count,
        entities_count=entities_count,
        sensors_count=sensors_count,
        available_entities_count=availability_counts["available"],
        unavailable_entities_count=availability_counts["unavailable"],
        unknown_entities_count=availability_counts["unknown"],
        not_reported_entities_count=availability_counts["not_reported"],
    )
