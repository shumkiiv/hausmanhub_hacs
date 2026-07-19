"""Validate one temporary room temperature change for a HASC climate schedule."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
import re

from ..domain.contours import climate_target_temperature


TEMPORARY_TEMPERATURE_REQUEST_CONTRACT_NAME = (
    "hausman-hasc-temporary-temperature-request"
)
TEMPORARY_TEMPERATURE_REQUEST_CONTRACT_VERSION = 1
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_STABLE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class TemporaryTemperatureViolation(ValueError):
    """A temporary room temperature request is unsafe or incomplete."""


class TemporaryTemperatureAction(StrEnum):
    """The two bounded temporary-temperature operations."""

    SET = "set"
    CLEAR = "clear"


@dataclass(frozen=True, slots=True)
class TemporaryTemperatureRequest:
    """One explicitly confirmed, idempotent room request."""

    request_id: str
    room_id: str
    action: TemporaryTemperatureAction
    target_temperature: float | None


def parse_temporary_temperature_request(
    payload: object,
) -> TemporaryTemperatureRequest:
    """Accept no backend command, private binding, duration, or arbitrary target."""

    if not isinstance(payload, Mapping) or any(
        not isinstance(key, str) for key in payload
    ):
        raise TemporaryTemperatureViolation(
            "temporary temperature request must be an object"
        )
    if set(payload) != {
        "request_id",
        "contour_id",
        "room_id",
        "action",
        "target_temperature",
        "confirm",
    }:
        raise TemporaryTemperatureViolation(
            "temporary temperature request fields are invalid"
        )
    request_id = payload.get("request_id")
    room_id = payload.get("room_id")
    if not isinstance(request_id, str) or _REQUEST_ID.fullmatch(request_id) is None:
        raise TemporaryTemperatureViolation("temporary request id is invalid")
    if payload.get("contour_id") != "climate":
        raise TemporaryTemperatureViolation("temporary request contour is invalid")
    if not isinstance(room_id, str) or _STABLE_ID.fullmatch(room_id) is None:
        raise TemporaryTemperatureViolation("temporary request room is invalid")
    if payload.get("confirm") is not True:
        raise TemporaryTemperatureViolation(
            "temporary temperature requires explicit confirmation"
        )
    try:
        action = TemporaryTemperatureAction(payload.get("action"))
    except (TypeError, ValueError) as error:
        raise TemporaryTemperatureViolation(
            "temporary temperature action is invalid"
        ) from error
    raw_temperature = payload.get("target_temperature")
    if action is TemporaryTemperatureAction.CLEAR:
        if raw_temperature is not None:
            raise TemporaryTemperatureViolation(
                "clearing temporary temperature cannot include a target"
            )
        target_temperature = None
    else:
        try:
            target_temperature = climate_target_temperature(raw_temperature)
        except ValueError as error:
            raise TemporaryTemperatureViolation(str(error)) from error
    return TemporaryTemperatureRequest(
        request_id=request_id,
        room_id=room_id,
        action=action,
        target_temperature=target_temperature,
    )
