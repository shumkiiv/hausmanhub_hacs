"""Validate one atomic Home Assistant area-assignment request."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..domain.climate import ClimateRegistry
from .climate_discovery import ClimateImportSnapshot
from .climate_setup import (
    JSON_SAFE_INTEGER_MAXIMUM,
    climate_setup_candidate_sources,
    climate_setup_options,
)


MAX_AREA_ASSIGNMENTS = 128
MAX_ASSIGNMENT_CANDIDATES = 32


class ClimateAreaAssignmentViolation(ValueError):
    """The requested Home Assistant area assignment is unsafe or stale."""

    def __init__(self, message: str, *, code: str = "invalid_request") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ClimateAreaAssignmentTarget:
    """Private entity targets resolved from response-local candidate ids."""

    room_id: str
    entity_ids: tuple[str, ...]


class ClimateAreaAssignmentPort(Protocol):
    """Outer boundary that updates Home Assistant registries atomically."""

    async def async_assign(
        self,
        targets: tuple[ClimateAreaAssignmentTarget, ...],
    ) -> dict[str, object]:
        """Assign all resolved targets or leave the registries unchanged."""


def climate_area_assignment_targets(
    registry: ClimateRegistry,
    snapshot: ClimateImportSnapshot,
    payload: object,
) -> tuple[ClimateAreaAssignmentTarget, ...]:
    """Resolve and validate one complete assignment batch without mutating HA."""

    if not isinstance(payload, dict) or set(payload) != {
        "snapshot_revision",
        "assignments",
    }:
        raise ClimateAreaAssignmentViolation("area assignment fields are invalid")
    options = climate_setup_options(registry, snapshot)
    requested_revision = payload["snapshot_revision"]
    if (
        type(requested_revision) is not int
        or not 0 <= requested_revision <= JSON_SAFE_INTEGER_MAXIMUM
    ):
        raise ClimateAreaAssignmentViolation("area assignment revision is invalid")
    if requested_revision != options["snapshot_revision"]:
        raise ClimateAreaAssignmentViolation(
            "climate inventory changed after it was opened",
            code="snapshot_changed",
        )
    assignments = payload["assignments"]
    if (
        not isinstance(assignments, list)
        or not 1 <= len(assignments) <= MAX_AREA_ASSIGNMENTS
    ):
        raise ClimateAreaAssignmentViolation("area assignments are invalid")

    rooms = {
        room["id"]
        for room in options["rooms"]
        if room["selectable"] is True
    }
    candidates = {candidate["candidate_id"]: candidate for candidate in options["devices"]}
    sources = climate_setup_candidate_sources(registry, snapshot)
    used_candidates: set[str] = set()
    used_groups: set[str] = set()
    targets: list[ClimateAreaAssignmentTarget] = []
    for value in assignments:
        if not isinstance(value, dict) or set(value) != {"candidate_ids", "room_id"}:
            raise ClimateAreaAssignmentViolation("area assignment item is invalid")
        room_id = value["room_id"]
        candidate_ids = value["candidate_ids"]
        if not isinstance(room_id, str) or room_id not in rooms:
            raise ClimateAreaAssignmentViolation("area assignment room is unavailable")
        if (
            not isinstance(candidate_ids, list)
            or not 1 <= len(candidate_ids) <= MAX_ASSIGNMENT_CANDIDATES
            or any(not isinstance(item, str) or not item for item in candidate_ids)
            or len(set(candidate_ids)) != len(candidate_ids)
        ):
            raise ClimateAreaAssignmentViolation("area assignment candidates are invalid")
        if used_candidates.intersection(candidate_ids):
            raise ClimateAreaAssignmentViolation("area assignment candidate is repeated")

        selected = []
        for candidate_id in candidate_ids:
            candidate = candidates.get(candidate_id)
            if (
                candidate is None
                or candidate["can_add"] is not True
                or candidate["room_id"] != ""
            ):
                raise ClimateAreaAssignmentViolation(
                    "area assignment candidate is unavailable",
                    code="snapshot_changed",
                )
            selected.append(candidate)
        group_ids = {candidate.get("device_group_id") for candidate in selected}
        if len(group_ids) != 1:
            raise ClimateAreaAssignmentViolation("physical device group is mixed")
        group_id = next(iter(group_ids))
        if group_id is None and len(selected) != 1:
            raise ClimateAreaAssignmentViolation("entity-only assignment must be singular")
        group_key = group_id or f"candidate:{candidate_ids[0]}"
        if group_key in used_groups:
            raise ClimateAreaAssignmentViolation("physical device group is repeated")

        entity_ids: list[str] = []
        for candidate_id in candidate_ids:
            source_id = sources.get(candidate_id)
            imported = None if source_id is None else snapshot.device(source_id)
            if imported is None:
                raise ClimateAreaAssignmentViolation(
                    "area assignment source is unavailable",
                    code="snapshot_changed",
                )
            resolved = (
                tuple(endpoint.entity_id for endpoint in imported.endpoints)
                if imported.endpoints
                else ((source_id,) if "." in source_id else ())
            )
            entity_ids.extend(resolved)
        unique_entity_ids = tuple(dict.fromkeys(entity_ids))
        if not unique_entity_ids:
            raise ClimateAreaAssignmentViolation("area assignment has no HA entities")
        used_candidates.update(candidate_ids)
        used_groups.add(group_key)
        targets.append(
            ClimateAreaAssignmentTarget(
                room_id=room_id,
                entity_ids=unique_entity_ids,
            )
        )
    return tuple(targets)
