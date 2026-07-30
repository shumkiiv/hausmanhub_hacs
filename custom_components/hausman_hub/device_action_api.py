"""Authenticated local device-action boundary for the HausmanHub tablet."""

from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from homeassistant.components.http import HomeAssistantView

from .application.api_capabilities import DEVICE_ACTIONS_PATH
from .application.scenario_service import ScenarioService
from .climate_api import (
    DOMAIN,
    NO_STORE_HEADERS,
    _forbidden,
    _is_exact_request,
    _is_local_tablet_request,
    _not_found,
    _request_json,
)
from .realtime_api import publish_command_receipt

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class DeviceActionView(HomeAssistantView):
    """Execute only catalog-resolved actions and return read-back evidence."""

    requires_auth = True
    cors_allowed = False
    extra_urls: tuple[str, ...] = ()
    url = DEVICE_ACTIONS_PATH
    name = "api:hausman_hub:device_actions"

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, DEVICE_ACTIONS_PATH):
            return _not_found(self)
        if not _is_local_tablet_request(request):
            return _forbidden(self)
        service = self._hass.data.get(DOMAIN, {}).get("scenario_service")
        if not isinstance(service, ScenarioService):
            return self.json_message(
                "The HausmanHub device action API is unavailable.",
                HTTPStatus.SERVICE_UNAVAILABLE,
                headers=NO_STORE_HEADERS,
            )
        try:
            payload = await _request_json(request)
        except ValueError:
            return self.json_message(
                "The device action body is invalid.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        if not isinstance(payload, Mapping):
            return self.json_message(
                "The device action body must be an object.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        target_id = payload.get("targetId")
        action_id = payload.get("actionId")
        if not isinstance(target_id, str) or not target_id:
            return self.json_message(
                "targetId is required.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        if not isinstance(action_id, str) or not action_id:
            return self.json_message(
                "actionId is required.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        result = await service.async_execute_device_action(
            target_id,
            action_id,
            payload.get("value"),
        )
        response = {
            "contract": {
                "name": "hausman-hub-device-action-receipt",
                "version": 1,
            },
            **result,
        }
        publish_command_receipt(self._hass, response, operation="device_action")
        return self.json(
            response,
            status_code=(
                HTTPStatus.OK
                if result.get("accepted") is True
                else HTTPStatus.CONFLICT
            ),
            headers=NO_STORE_HEADERS,
        )
