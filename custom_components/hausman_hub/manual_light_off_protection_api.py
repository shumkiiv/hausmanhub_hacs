"""Local, authenticated boundary for manual light-off protection."""

from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
import re
from typing import Any

from homeassistant.components.http import HomeAssistantView

from .application.api_capabilities import MANUAL_LIGHT_OFF_PROTECTION_PATH
from .application.manual_light_off_protection import ManualLightOffProtectionCoordinator
from .climate_api import (
    DOMAIN,
    NO_STORE_HEADERS,
    _forbidden,
    _is_exact_request,
    _is_local_admin_request,
    _is_local_tablet_request,
    _not_found,
    _request_json,
)

MANUAL_LIGHT_OFF_PROTECTION_RELEASE_PATH = f"{MANUAL_LIGHT_OFF_PROTECTION_PATH}/release"
DATA_MANUAL_LIGHT_OFF_PROTECTION = "manual_light_off_protection"
DATA_MANUAL_LIGHT_OFF_PROTECTION_VIEWS = "manual_light_off_protection_views"
MAX_MANUAL_LIGHT_OFF_PROTECTION_BODY_BYTES = 16 * 1024
_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\\Z")


class _BaseView(HomeAssistantView):
    requires_auth = True
    cors_allowed = False
    extra_urls: tuple[str, ...] = ()

    def __init__(self, hass: Any) -> None:
        self._hass = hass

    def _coordinator(self) -> ManualLightOffProtectionCoordinator | None:
        candidate = self._hass.data.get(DOMAIN, {}).get(DATA_MANUAL_LIGHT_OFF_PROTECTION)
        return candidate if isinstance(candidate, ManualLightOffProtectionCoordinator) else None

    def _unavailable(self) -> Any:
        return self.json_message("The manual light-off protection service is unavailable.", HTTPStatus.SERVICE_UNAVAILABLE, headers=NO_STORE_HEADERS)


class ManualLightOffProtectionView(_BaseView):
    url = MANUAL_LIGHT_OFF_PROTECTION_PATH
    name = "api:hausman_hub:manual_light_off_protection"

    async def get(self, request: Any) -> Any:
        if not _is_exact_request(request, self.url):
            return _not_found(self)
        if not _is_local_tablet_request(request):
            return _forbidden(self)
        coordinator = self._coordinator()
        return self._unavailable() if coordinator is None else self.json(coordinator.snapshot(), headers=NO_STORE_HEADERS)

    async def put(self, request: Any) -> Any:
        if not _is_exact_request(request, self.url):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        coordinator = self._coordinator()
        if coordinator is None:
            return self._unavailable()
        try:
            payload = await _request_json(request, maximum_bytes=MAX_MANUAL_LIGHT_OFF_PROTECTION_BODY_BYTES)
            if not isinstance(payload, Mapping) or set(payload) != {"contract", "requestId", "expectedRevision", "settings"}:
                raise ValueError
            contract = payload["contract"]
            if contract != {"name": "hausman-hub-manual-light-off-protection-settings-request", "version": 1}:
                raise ValueError
            request_id = payload["requestId"]
            expected_revision = payload["expectedRevision"]
            settings = payload["settings"]
            if (
                not isinstance(request_id, str)
                or _REQUEST_ID.fullmatch(request_id) is None
                or type(expected_revision) is not int
                or not isinstance(settings, Mapping)
            ):
                raise ValueError
            receipt = await coordinator.async_replace_settings(
                request_id, expected_revision, settings
            )
        except ValueError as error:
            status = HTTPStatus.CONFLICT if "revision conflict" in str(error) else HTTPStatus.BAD_REQUEST
            return self.json_message("The manual light-off protection request is invalid.", status, headers=NO_STORE_HEADERS)
        except (KeyError, TypeError):
            return self.json_message("The manual light-off protection request is invalid.", HTTPStatus.BAD_REQUEST, headers=NO_STORE_HEADERS)
        return self.json(receipt, headers=NO_STORE_HEADERS)


class ManualLightOffProtectionReleaseView(_BaseView):
    url = MANUAL_LIGHT_OFF_PROTECTION_RELEASE_PATH
    name = "api:hausman_hub:manual_light_off_protection_release"

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, self.url):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        coordinator = self._coordinator()
        if coordinator is None:
            return self._unavailable()
        try:
            payload = await _request_json(request, maximum_bytes=MAX_MANUAL_LIGHT_OFF_PROTECTION_BODY_BYTES)
            if not isinstance(payload, Mapping) or set(payload) != {"contract", "requestId", "roomId", "profileId"}:
                raise ValueError
            if payload["contract"] != {"name": "hausman-hub-manual-light-off-protection-release-request", "version": 1}:
                raise ValueError
            request_id = payload["requestId"]
            room_id = payload["roomId"]
            profile_id = payload["profileId"]
            if (
                not all(isinstance(value, str) for value in (request_id, room_id, profile_id))
                or _REQUEST_ID.fullmatch(request_id) is None
            ):
                raise ValueError
            receipt = await coordinator.async_release_profile(request_id, room_id, profile_id)
        except ValueError as error:
            status = HTTPStatus.CONFLICT if "conflict" in str(error) else HTTPStatus.BAD_REQUEST
            return self.json_message("The manual light-off protection release is invalid.", status, headers=NO_STORE_HEADERS)
        except (KeyError, TypeError):
            return self.json_message("The manual light-off protection release is invalid.", HTTPStatus.BAD_REQUEST, headers=NO_STORE_HEADERS)
        return self.json(receipt, headers=NO_STORE_HEADERS)


def register_manual_light_off_protection_api(hass: Any) -> None:
    if hass.data.setdefault(DOMAIN, {}).get(DATA_MANUAL_LIGHT_OFF_PROTECTION_VIEWS):
        return
    views = (ManualLightOffProtectionView(hass), ManualLightOffProtectionReleaseView(hass))
    for view in views:
        hass.http.register_view(view)
    hass.data[DOMAIN][DATA_MANUAL_LIGHT_OFF_PROTECTION_VIEWS] = views


def clear_manual_light_off_protection_api(hass: Any) -> None:
    hass.data.get(DOMAIN, {}).pop(DATA_MANUAL_LIGHT_OFF_PROTECTION_VIEWS, None)
