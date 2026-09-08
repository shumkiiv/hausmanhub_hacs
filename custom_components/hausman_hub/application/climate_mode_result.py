"""Typed private proof for a saved mode, which never commands hardware."""

from collections.abc import Mapping
from dataclasses import dataclass

from .contour_apply import ContourApplyStatus


@dataclass(frozen=True, slots=True)
class ClimateSavedModeResult:
    room_id: str
    modes: tuple[tuple[str, str], ...]
    observed_at: int
    status: ContourApplyStatus = ContourApplyStatus.CONFIRMED
    command_count: int = 0
    accepted_count: int = 0
    confirmed_room_count: int = 1
    device_outcomes: Mapping[str, Mapping[str, object]] | None = None
