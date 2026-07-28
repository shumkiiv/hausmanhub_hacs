"""Authenticated admin HTTP views for HausmanHub IR command codes."""

from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from homeassistant.components.http import HomeAssistantView

from .application.ir_code_service import (
    IRCodeLearnRejectedError,
    IRCodeLearnTimeoutError,
    IRCodeNotFoundError,
    IRCodeSendError,
    IRCodeService,
    IRCodeServiceError,
)
from .application.climate_runtime import ClimateRuntime, ClimateRuntimeUnavailable
from .climate_api import (
    DOMAIN,
    NO_STORE_HEADERS,
    _forbidden,
    _is_exact_request,
    _is_local_admin_request,
    _not_found,
    _request_json,
)
from .domain.ir_codes import IRCodeSource, IRCodeViolation

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


ADMIN_IR_CODES_PATH = "/api/hausman_hub/v1/admin/ir-codes"
ADMIN_IR_CODES_SCAN_PATH = f"{ADMIN_IR_CODES_PATH}/scan"
ADMIN_IR_CODES_LEARN_PATH = f"{ADMIN_IR_CODES_PATH}/learn"
ADMIN_IR_CODES_TEST_PATH = f"{ADMIN_IR_CODES_PATH}/test"
ADMIN_IR_CODES_DELETE_PATH = f"{ADMIN_IR_CODES_PATH}/delete"
ADMIN_IR_CODES_BINDINGS_PATH = f"{ADMIN_IR_CODES_PATH}/bindings"


class _IrCodeView(HomeAssistantView):
    requires_auth = True
    cors_allowed = False
    extra_urls: tuple[str, ...] = ()

    def __init__(self, hass: HomeAssistant, service: IRCodeService) -> None:
        self._hass = hass
        self._service = service

    def _unavailable(self) -> Any:
        return self.json_message(
            "The HausmanHub IR code API is unavailable.",
            HTTPStatus.SERVICE_UNAVAILABLE,
            headers=NO_STORE_HEADERS,
        )

    def _service_ready(self) -> IRCodeService | None:
        if self._service.registry is None:
            return None
        return self._service

    def _runtime(self) -> ClimateRuntime | None:
        runtime = self._hass.data.get(DOMAIN, {}).get("climate_runtime")
        return runtime if isinstance(runtime, ClimateRuntime) else None


class IrCodesView(_IrCodeView):
    """List all IR codes or import one from an external source."""

    url = ADMIN_IR_CODES_PATH
    name = "api:hausman_hub:ir_codes"

    async def get(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_IR_CODES_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        service = self._service_ready()
        if service is None:
            return self._unavailable()
        codes = service.all_codes()
        return self.json(
            {
                "codes": [
                    {
                        "code_id": code.code_id,
                        "device_id": code.device_id,
                        "remote_entity_id": code.remote_entity_id,
                        "command_name": code.command_name,
                        "code_data": code.code_data,
                        "source": code.source.value,
                        "created_at": code.created_at,
                    }
                    for code in codes
                ]
            },
            headers=NO_STORE_HEADERS,
        )

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_IR_CODES_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        service = self._service_ready()
        if service is None:
            return self._unavailable()
        try:
            payload = await _request_json(request)
        except ValueError:
            return self.json_message(
                "The IR code import body is invalid.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        if not isinstance(payload, Mapping):
            return self.json_message(
                "The IR code import body must be an object.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        device_id = payload.get("device_id")
        remote_entity_id = payload.get("remote_entity_id")
        command_name = payload.get("command_name")
        code_data = payload.get("code_data")
        source_str = payload.get("source", "manual")
        replace = payload.get("replace", False)
        if (
            not isinstance(device_id, str) or not device_id
            or not isinstance(remote_entity_id, str) or not remote_entity_id
            or not isinstance(command_name, str) or not command_name
            or not isinstance(code_data, str) or not code_data
        ):
            return self.json_message(
                "device_id, remote_entity_id, command_name, and code_data are required.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        if type(replace) is not bool:
            return self.json_message(
                "replace must be boolean.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        try:
            source = IRCodeSource(source_str)
        except ValueError:
            return self.json_message(
                "source must be smartir, broadlink, or manual.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        try:
            code = await service.async_import_code(
                device_id=device_id,
                remote_entity_id=remote_entity_id,
                command_name=command_name,
                code_data=code_data,
                source=source,
                replace=replace,
            )
        except (IRCodeServiceError, IRCodeViolation) as error:
            return self.json_message(
                (
                    error.message
                    if isinstance(error, IRCodeServiceError)
                    else "IR code import data is invalid."
                ),
                (
                    error.status
                    if isinstance(error, IRCodeServiceError)
                    else HTTPStatus.BAD_REQUEST
                ),
                headers=NO_STORE_HEADERS,
            )
        return self.json(
            {
                "ok": True,
                "code_id": code.code_id,
            },
            headers=NO_STORE_HEADERS,
        )


class IrCodesScanView(_IrCodeView):
    """Scan SmartIR and Broadlink storage for available IR codes."""

    url = ADMIN_IR_CODES_SCAN_PATH
    name = "api:hausman_hub:ir_codes_scan"

    async def get(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_IR_CODES_SCAN_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        service = self._service_ready()
        if service is None:
            return self._unavailable()
        try:
            catalog = await service.async_scan_catalog()
        except Exception:
            return self._unavailable()
        return self.json(
            {
                **catalog,
            },
            headers=NO_STORE_HEADERS,
        )


class IrCodeBindingsView(_IrCodeView):
    """Expose raw-remote bindings only to a local authenticated administrator."""

    url = ADMIN_IR_CODES_BINDINGS_PATH
    name = "api:hausman_hub:ir_code_bindings"

    async def get(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_IR_CODES_BINDINGS_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        if self._service_ready() is None:
            return self._unavailable()
        runtime = self._runtime()
        if runtime is None:
            return self._unavailable()
        try:
            bindings = await runtime.async_ir_code_bindings()
        except ClimateRuntimeUnavailable:
            return self._unavailable()
        except Exception:
            return self._unavailable()
        return self.json(bindings, headers=NO_STORE_HEADERS)


class IrCodeLearnView(_IrCodeView):
    """Trigger IR learning on a remote entity and persist the result."""

    url = ADMIN_IR_CODES_LEARN_PATH
    name = "api:hausman_hub:ir_code_learn"

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_IR_CODES_LEARN_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        service = self._service_ready()
        if service is None:
            return self._unavailable()
        try:
            payload = await _request_json(request)
        except ValueError:
            return self.json_message(
                "The IR learn body is invalid.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        if not isinstance(payload, Mapping):
            return self.json_message(
                "The IR learn body must be an object.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        device_id = payload.get("device_id")
        remote_entity_id = payload.get("remote_entity_id")
        command_name = payload.get("command_name")
        timeout_seconds = payload.get("timeout_seconds", 30.0)
        replace = payload.get("replace", False)
        if (
            not isinstance(device_id, str) or not device_id
            or not isinstance(remote_entity_id, str) or not remote_entity_id
            or not isinstance(command_name, str) or not command_name
        ):
            return self.json_message(
                "device_id, remote_entity_id, and command_name are required.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        if not isinstance(timeout_seconds, (int, float)) or timeout_seconds < 5:
            timeout_seconds = 30.0
        if type(replace) is not bool:
            return self.json_message(
                "replace must be boolean.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        try:
            code = await service.async_learn_code(
                device_id=device_id,
                remote_entity_id=remote_entity_id,
                command_name=command_name,
                timeout_seconds=timeout_seconds,
                replace=replace,
            )
        except IRCodeLearnTimeoutError:
            return self.json_message(
                "IR learning timed out. Make sure the remote is in learning mode.",
                HTTPStatus.REQUEST_TIMEOUT,
                headers=NO_STORE_HEADERS,
            )
        except IRCodeLearnRejectedError as error:
            return self.json_message(
                error.message, error.status, headers=NO_STORE_HEADERS,
            )
        except IRCodeServiceError as error:
            return self.json_message(
                error.message, error.status, headers=NO_STORE_HEADERS,
            )
        return self.json(
            {
                "ok": True,
                "code_id": code.code_id,
                "source": code.source.value,
            },
            headers=NO_STORE_HEADERS,
        )


class IrCodeTestView(_IrCodeView):
    """Send one IR code via a remote entity."""

    url = ADMIN_IR_CODES_TEST_PATH
    name = "api:hausman_hub:ir_code_test"

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_IR_CODES_TEST_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        service = self._service_ready()
        if service is None:
            return self._unavailable()
        try:
            payload = await _request_json(request)
        except ValueError:
            return self.json_message(
                "The IR test body is invalid.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        if not isinstance(payload, Mapping):
            return self.json_message(
                "The IR test body must be an object.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        code_id = payload.get("code_id")
        remote_entity_id = payload.get("remote_entity_id")
        device_id = payload.get("device_id")
        if isinstance(code_id, str) and code_id:
            code = service.code_by_id(code_id)
            if code is None:
                return self.json_message(
                    f"IR code {code_id!r} not found.",
                    HTTPStatus.NOT_FOUND,
                    headers=NO_STORE_HEADERS,
                )
            code_data = code.code_data
            device_id = code.device_id
            if not isinstance(remote_entity_id, str) or not remote_entity_id:
                remote_entity_id = code.remote_entity_id
        else:
            code_data = payload.get("code_data")
            if (
                not isinstance(remote_entity_id, str)
                or not remote_entity_id
                or not isinstance(device_id, str)
                or not device_id
            ):
                return self.json_message(
                    "code_id or device_id + remote_entity_id + code_data are required.",
                    HTTPStatus.BAD_REQUEST,
                    headers=NO_STORE_HEADERS,
                )
        if not isinstance(code_data, str) or not code_data:
            return self.json_message(
                "code_data is required.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        try:
            await service.async_test_send(
                remote_entity_id=remote_entity_id,
                device_id=device_id,
                code_data=code_data,
            )
        except IRCodeSendError as error:
            return self.json_message(
                error.message, error.status, headers=NO_STORE_HEADERS,
            )
        except IRCodeServiceError as error:
            return self.json_message(
                error.message, error.status, headers=NO_STORE_HEADERS,
            )
        return self.json(
            {"ok": True}, headers=NO_STORE_HEADERS,
        )


class IrCodeDeleteView(_IrCodeView):
    """Delete one IR code or all codes for a device."""

    url = ADMIN_IR_CODES_DELETE_PATH
    name = "api:hausman_hub:ir_code_delete"

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_IR_CODES_DELETE_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        service = self._service_ready()
        if service is None:
            return self._unavailable()
        try:
            payload = await _request_json(request)
        except ValueError:
            return self.json_message(
                "The IR delete body is invalid.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        if not isinstance(payload, Mapping):
            return self.json_message(
                "The IR delete body must be an object.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        code_id = payload.get("code_id")
        device_id = payload.get("device_id")
        if isinstance(code_id, str) and code_id:
            try:
                await service.async_delete_code(code_id)
            except IRCodeNotFoundError as error:
                return self.json_message(
                    error.message, error.status, headers=NO_STORE_HEADERS,
                )
            except IRCodeServiceError as error:
                return self.json_message(
                    error.message, error.status, headers=NO_STORE_HEADERS,
                )
            return self.json(
                {"ok": True, "code_id": code_id}, headers=NO_STORE_HEADERS,
            )
        if isinstance(device_id, str) and device_id:
            removed = await service.async_delete_device_codes(device_id)
            return self.json(
                {"ok": True, "device_id": device_id, "removed": removed},
                headers=NO_STORE_HEADERS,
            )
        return self.json_message(
            "code_id or device_id is required.",
            HTTPStatus.BAD_REQUEST,
            headers=NO_STORE_HEADERS,
        )


def ir_code_api_views(
    hass: HomeAssistant, service: IRCodeService
) -> tuple[HomeAssistantView, ...]:
    """Return all IR code admin views wired to one loaded service."""

    return (
        IrCodesView(hass, service),
        IrCodesScanView(hass, service),
        IrCodeBindingsView(hass, service),
        IrCodeLearnView(hass, service),
        IrCodeTestView(hass, service),
        IrCodeDeleteView(hass, service),
    )
