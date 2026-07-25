from __future__ import annotations

from ..domain.climate_observation import ClimateObservationSnapshot
from ..domain.contours import ContourRegistry
from ..domain.ai_assistant_json import AiJsonObject
from .contours import CLIMATE_CONTOUR_ID


_MAX_EVIDENCE_ROOMS = 64
_MAX_ROOM_REASONS = 8


def ai_evidence_from_observation(
    observation: ClimateObservationSnapshot,
    contours: ContourRegistry,
) -> AiJsonObject:
    contour = contours.contour(CLIMATE_CONTOUR_ID)
    assignments = {} if contour is None else {
        room.room_id: room for room in contour.rooms
    }
    rooms: list[AiJsonObject] = []
    mismatch_room_ids: list[str] = []
    for room in observation.rooms[:_MAX_EVIDENCE_ROOMS]:
        assignment = assignments.get(room.room_id)
        target_temperature = (
            None if assignment is None else assignment.target_temperature
        )
        target_humidity = None if assignment is None else assignment.target_humidity
        reasons = _room_reasons(room, target_temperature, target_humidity)
        if reasons:
            mismatch_room_ids.append(room.room_id)
        rooms.append(
            {
                "id": room.room_id,
                "data_status": room.data_status.value,
                "temperature": room.temperature,
                "humidity": room.humidity,
                "target_temperature": target_temperature,
                "target_humidity": target_humidity,
                "observed_target_temperature": room.observed_target_temperature,
                "observed_target_humidity": room.observed_target_humidity,
                "window": room.window.value,
                "temperature_quality": room.temperature_quality.value,
                "reasons": reasons,
            }
        )
    return {
        "version": 1,
        "data_status": observation.data_status.value,
        "rooms": rooms,
        "mismatch_room_ids": mismatch_room_ids,
        "diagnostics": {
            "season": observation.home.season.value,
            "occupancy": observation.home.occupancy.value,
            "central_heating_on": observation.home.central_heating_on,
            "weather_heating_lockout": observation.home.weather_heating_lockout,
        },
        "outdoor_temperature": observation.home.outdoor_temperature,
    }


def _room_reasons(
    room,
    target_temperature: float | None,
    target_humidity: int | None,
) -> list[str]:
    reasons: list[str] = []
    if room.data_status.value != "fresh":
        reasons.append("room_state_stale")
    if room.observed_target_temperature != target_temperature:
        reasons.append("target_temperature_differs")
    if target_humidity is not None and room.observed_target_humidity != target_humidity:
        reasons.append("target_humidity_differs")
    if room.window.value != "closed":
        reasons.append("window_not_closed")
    return reasons[:_MAX_ROOM_REASONS]
