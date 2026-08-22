"""Authenticated local device-action boundary for the HausmanHub tablet."""

from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from homeassistant.components.http import HomeAssistantView

from .application.api_capabilities import (
    DEVICE_ACTIONS_BATCH_PATH,
    DEVICE_ACTIONS_PATH,
    DEVICE_FEATURES_PATH,
)
from .application.device_features import device_feature_matrix_snapshot
from .application.scenario_service import ScenarioService
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
from .correlation import CorrelationIdError, resolve_correlation_id
from .realtime_api import publish_command_receipt

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class DeviceFeatureMatrixView(HomeAssistantView):
    """Expose the authenticated, read-only device control upper bound."""

    requires_auth = True
    cors_allowed = False
    extra_urls: tuple[str, ...] = ()
    url = DEVICE_FEATURES_PATH
    name = "api:hausman_hub:device_features"

    async def get(self, request: Any) -> Any:
        if not _is_exact_request(request, DEVICE_FEATURES_PATH):
            return _not_found(self)
        if not (
            _is_local_tablet_request(request) or _is_local_admin_request(request)
        ):
            return _forbidden(self)
        return self.json(device_feature_matrix_snapshot(), headers=NO_STORE_HEADERS)


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
        if not (
            _is_local_tablet_request(request) or _is_local_admin_request(request)
        ):
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
        try:
            correlation_id = resolve_correlation_id(
                payload,
                field="correlationId",
            )
        except CorrelationIdError:
            return self.json_message(
                "correlationId is invalid.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        climate_mode_change: dict[str, object] | None = None
        climate_runtime = self._hass.data.get(DOMAIN, {}).get("climate_runtime")
        mode_writer = getattr(
            climate_runtime, "async_set_device_mode_for_entity", None
        )
        try:
            if action_id == "turn_off" and callable(mode_writer):
                resolved = await service.async_resolve_device_action(
                    target_id, action_id
                )
                if resolved is not None and resolved[1] == "climate":
                    climate_mode_change = await mode_writer(resolved[0], "manual")
            result = await service.async_execute_device_action(
                target_id,
                action_id,
                payload.get("value"),
                correlation_id=correlation_id,
            )
        except Exception:
            await self._async_restore_climate_mode(
                mode_writer, climate_mode_change
            )
            raise
        if result.get("accepted") is not True:
            await self._async_restore_climate_mode(mode_writer, climate_mode_change)
            climate_mode_change = None
        elif climate_mode_change is not None:
            result = {
                **result,
                "climateMode": "manual",
                "climateModeName": "Ручной режим",
            }
        release_seconds = None
        if result.get("accepted") is True:
            release_seconds = await service.async_schedule_intercom_release(
                target_id, action_id
            )
        response = {
            "contract": {
                "name": "hausman-hub-device-action-receipt",
                "version": 1,
            },
            **result,
        }
        if release_seconds is not None:
            response["autoReleaseSeconds"] = release_seconds
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

    @staticmethod
    async def _async_restore_climate_mode(
        mode_writer: object,
        change: dict[str, object] | None,
    ) -> None:
        """Undo only the manual exclusion introduced by this failed request."""

        if (
            change is None
            or change.get("changed") is not True
            or change.get("previous_mode") != "automatic"
            or not callable(mode_writer)
        ):
            return
        await mode_writer(change.get("entity_id"), "automatic")


class DeviceActionBatchView(HomeAssistantView):
    """Execute an ordered bounded batch and expose each target outcome."""

    requires_auth = True
    cors_allowed = False
    extra_urls: tuple[str, ...] = ()
    url = DEVICE_ACTIONS_BATCH_PATH
    name = "api:hausman_hub:device_actions_batch"

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, self.url):
            return _not_found(self)
        if not (
            _is_local_tablet_request(request) or _is_local_admin_request(request)
        ):
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
            payload = None
        if not isinstance(payload, Mapping) or payload.get("contract") != {
            "name": "hausman-hub-device-action-batch-request",
            "version": 1,
        }:
            return self.json_message(
                "The device action batch body is invalid.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        if not isinstance(payload.get("correlationId"), str):
            return self.json_message(
                "correlationId is required.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        actions = payload.get("actions")
        if not isinstance(actions, list) or not 1 <= len(actions) <= 64:
            return self.json_message(
                "actions must contain 1 to 64 items.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        normalized: list[Mapping[str, object]] = []
        action_keys: set[tuple[str, str]] = set()
        for item in actions:
            if (
                not isinstance(item, Mapping)
                or not {"targetId", "actionId"}.issubset(item)
                or not set(item).issubset({"targetId", "actionId", "value"})
                or not isinstance(item.get("targetId"), str)
                or not isinstance(item.get("actionId"), str)
            ):
                return self.json_message(
                    "A device action batch item is invalid.",
                    HTTPStatus.BAD_REQUEST,
                    headers=NO_STORE_HEADERS,
                )
            action_key = (item["targetId"], item["actionId"])
            if action_key in action_keys:
                return self.json_message(
                    "A target and action may appear only once in a batch.",
                    HTTPStatus.BAD_REQUEST,
                    headers=NO_STORE_HEADERS,
                )
            action_keys.add(action_key)
            normalized.append(item)
        try:
            correlation_id = resolve_correlation_id(payload, field="correlationId")
        except CorrelationIdError:
            return self.json_message(
                "correlationId is invalid.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        receipts = await service.async_execute_device_action_batch(
            normalized,
            correlation_id=correlation_id,
        )
        wrapped = [
            {
                "contract": {
                    "name": "hausman-hub-device-action-receipt",
                    "version": 1,
                },
                **item,
            }
            for item in receipts
        ]
        for receipt in wrapped:
            publish_command_receipt(self._hass, receipt, operation="device_action")
        accepted = sum(item.get("accepted") is True for item in wrapped)
        confirmed = sum(item.get("confirmed") is True for item in wrapped)
        failed = sum(item.get("status") == "failed" for item in wrapped)
        status = (
            "confirmed"
            if confirmed == len(wrapped)
            else "failed"
            if failed == len(wrapped)
            else "partial"
            if failed
            else "accepted"
        )
        return self.json(
            {
                "contract": {
                    "name": "hausman-hub-device-action-batch-receipt",
                    "version": 1,
                },
                "correlationId": correlation_id,
                "status": status,
                "total": len(wrapped),
                "acceptedCount": accepted,
                "confirmedCount": confirmed,
                "failedCount": failed,
                "receipts": wrapped,
            },
            headers=NO_STORE_HEADERS,
        )
