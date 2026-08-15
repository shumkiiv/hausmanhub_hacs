"""Strict request model for one whole-home climate target change."""

from __future__ import annotations

from dataclasses import dataclass
import re

from ..correlation import CorrelationIdError, validate_correlation_id
from ..domain.contours import (
    CLIMATE_TARGET_HUMIDITY_MAXIMUM,
    CLIMATE_TARGET_HUMIDITY_MINIMUM,
    CLIMATE_TARGET_HUMIDITY_STEP,
    climate_target_temperature,
)


HOME_CLIMATE_TARGETS_REQUEST_CONTRACT_NAME = (
    "hausman-hub-home-climate-targets-request"
)
HOME_CLIMATE_TARGETS_REQUEST_CONTRACT_VERSION = 1
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")


class HomeClimateTargetsViolation(ValueError):
    """The whole-home target request is malformed or unsafe."""


@dataclass(frozen=True, slots=True)
class HomeClimateTargetsRequest:
    """One explicitly confirmed, idempotent whole-home target request."""

    request_id: str
    correlation_id: str | None
    target_temperature: float | None
    target_humidity: int | None


def parse_home_climate_targets_request(payload: object) -> HomeClimateTargetsRequest:
    """Validate an exact whole-home target payload without coercion."""

    required_fields = {
        "request_id",
        "contour_id",
        "target_temperature",
        "target_humidity",
        "confirm",
    }
    if not isinstance(payload, dict) or not required_fields <= set(
        payload
    ) <= required_fields | {"correlation_id"}:
        raise HomeClimateTargetsViolation("home climate target request is invalid")
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or _REQUEST_ID.fullmatch(request_id) is None:
        raise HomeClimateTargetsViolation("home climate target request id is invalid")
    if payload.get("contour_id") != "climate":
        raise HomeClimateTargetsViolation("home climate contour is unsupported")
    if payload.get("confirm") is not True:
        raise HomeClimateTargetsViolation(
            "home climate target requires explicit confirmation"
        )
    correlation_id = None
    if "correlation_id" in payload:
        try:
            correlation_id = validate_correlation_id(payload["correlation_id"])
        except CorrelationIdError as error:
            raise HomeClimateTargetsViolation("correlation id is invalid") from error
    temperature_value = payload.get("target_temperature")
    humidity_value = payload.get("target_humidity")
    if temperature_value is None and humidity_value is None:
        raise HomeClimateTargetsViolation("home climate target is empty")
    try:
        target_temperature = (
            None
            if temperature_value is None
            else climate_target_temperature(temperature_value)
        )
    except ValueError as error:
        raise HomeClimateTargetsViolation(str(error)) from error
    if humidity_value is None:
        target_humidity = None
    elif (
        type(humidity_value) is not int
        or not CLIMATE_TARGET_HUMIDITY_MINIMUM
        <= humidity_value
        <= CLIMATE_TARGET_HUMIDITY_MAXIMUM
        or humidity_value % CLIMATE_TARGET_HUMIDITY_STEP != 0
    ):
        raise HomeClimateTargetsViolation("home climate humidity is invalid")
    else:
        target_humidity = humidity_value
    return HomeClimateTargetsRequest(
        request_id=request_id,
        correlation_id=correlation_id,
        target_temperature=target_temperature,
        target_humidity=target_humidity,
    )
