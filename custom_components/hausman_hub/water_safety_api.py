"""Local admin API for the fail-safe water safety policy."""

from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

from homeassistant.components.http import HomeAssistantView

from .application.water_safety import WaterSafetyService
from .climate_api import (
    DOMAIN,
    NO_STORE_HEADERS,
    _forbidden,
    _is_exact_request,
    _is_local_admin_request,
    _not_found,
    _request_json,
)

WATER_SAFETY_PATH = "/api/hausman_hub/v1/admin/water-safety"
WATER_DIRECTION_TEST_PATH = f"{WATER_SAFETY_PATH}/direction-test"
DATA_WATER_SAFETY = "water_safety_service"
DATA_WATER_SAFETY_VIEWS = "water_safety_views"


class WaterSafetyView(HomeAssistantView):
    requires_auth = True
    cors_allowed = False
    extra_urls: tuple[str, ...] = ()
    url = WATER_SAFETY_PATH
    name = "api:hausman_hub:water_safety"

    def __init__(self, hass: Any) -> None:
        self._hass = hass

    async def get(self, request: Any) -> Any:
        if not _is_exact_request(request, WATER_SAFETY_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        service = self._service()
        if service is None:
            return self.json_message(
                "The Hausman water safety service is unavailable.",
                HTTPStatus.SERVICE_UNAVAILABLE,
                headers=NO_STORE_HEADERS,
            )
        return self.json(service.snapshot(), headers=NO_STORE_HEADERS)

    async def put(self, request: Any) -> Any:
        if not _is_exact_request(request, WATER_SAFETY_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        service = self._service()
        if service is None:
            return self.json_message(
                "The Hausman water safety service is unavailable.",
                HTTPStatus.SERVICE_UNAVAILABLE,
                headers=NO_STORE_HEADERS,
            )
        try:
            payload = await _request_json(request)
            if not isinstance(payload, Mapping):
                raise TypeError
            expected_revision = payload.get("expectedRevision")
            if type(expected_revision) is not int or expected_revision < 0:
                raise ValueError
            if payload.get("action") == "clear_latch":
                if set(payload) != {"action", "expectedRevision", "confirmation"}:
                    raise ValueError
                result = await service.async_clear_latch(
                    expected_revision,
                    confirmation=payload.get("confirmation") is True,
                )
            else:
                if set(payload) != {"expectedRevision", "configuration"}:
                    raise ValueError
                result = await service.async_update(
                    expected_revision,
                    payload.get("configuration"),
                )
        except RuntimeError as error:
            if str(error) != "revision_conflict":
                raise
            return self.json_message(
                "The water safety policy changed. Refresh and retry.",
                HTTPStatus.CONFLICT,
                headers=NO_STORE_HEADERS,
            )
        except (KeyError, TypeError, ValueError):
            return self.json_message(
                "The water safety request is invalid.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        return self.json(result, headers=NO_STORE_HEADERS)

    def _service(self) -> WaterSafetyService | None:
        candidate = self._hass.data.get(DOMAIN, {}).get(DATA_WATER_SAFETY)
        return candidate if isinstance(candidate, WaterSafetyService) else None


class WaterDirectionTestView(HomeAssistantView):
    requires_auth = True
    cors_allowed = False
    extra_urls: tuple[str, ...] = ()
    url = WATER_DIRECTION_TEST_PATH
    name = "api:hausman_hub:water_direction_test"

    def __init__(self, hass: Any) -> None:
        self._hass = hass

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, WATER_DIRECTION_TEST_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        candidate = self._hass.data.get(DOMAIN, {}).get(DATA_WATER_SAFETY)
        if not isinstance(candidate, WaterSafetyService):
            return self.json_message(
                "The Hausman water safety service is unavailable.",
                HTTPStatus.SERVICE_UNAVAILABLE,
                headers=NO_STORE_HEADERS,
            )
        try:
            payload = await _request_json(request)
            if not isinstance(payload, Mapping) or set(payload) != {"entityId"}:
                raise ValueError
            entity_id = payload.get("entityId")
            if not isinstance(entity_id, str) or not entity_id:
                raise ValueError
            result = candidate.direction_test(entity_id)
        except KeyError:
            return self.json_message(
                "The configured water actuator was not found.",
                HTTPStatus.NOT_FOUND,
                headers=NO_STORE_HEADERS,
            )
        except (TypeError, ValueError):
            return self.json_message(
                "The water direction test request is invalid.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        return self.json(result, headers=NO_STORE_HEADERS)


def register_water_safety_api(hass: Any) -> None:
    data = hass.data.setdefault(DOMAIN, {})
    if DATA_WATER_SAFETY_VIEWS in data:
        return
    views = (WaterSafetyView(hass), WaterDirectionTestView(hass))
    for view in views:
        hass.http.register_view(view)
    data[DATA_WATER_SAFETY_VIEWS] = views


def clear_water_safety_api(hass: Any) -> None:
    data = hass.data.get(DOMAIN)
    if isinstance(data, dict):
        data.pop(DATA_WATER_SAFETY, None)
        data.pop(DATA_WATER_SAFETY_VIEWS, None)
