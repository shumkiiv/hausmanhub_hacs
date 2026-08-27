"""Authenticated admin and local-tablet HTTP views for HausmanHub scenarios."""

from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
import re
from typing import TYPE_CHECKING, Any

from homeassistant.components.http import HomeAssistantView

from .application.scenario_service import (
    ScenarioNotFoundError,
    ScenarioCatalogNotReadyError,
    ScenarioNodeRedSourceConflictError,
    ScenarioProtectedError,
    ScenarioReferencedError,
    ScenarioRevisionConflictError,
    ScenarioService,
    ScenarioServiceError,
    ScenarioValidationError,
)
from .application.scenario_ai import (
    ScenarioAiDraftService,
    ScenarioAiOutputInvalid,
    ScenarioAiRequestError,
    ScenarioAiUnavailable,
)
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
from .application.api_capabilities import (
    SCENARIOS_ACTION_PATH,
    SCENARIOS_AI_DRAFT_PATH,
    SCENARIOS_CATALOG_PATH,
    SCENARIOS_HEALTH_PATH,
    SCENARIOS_NODE_RED_PATH,
    SCENARIOS_NODE_RED_SOURCE_PATH,
    SCENARIOS_DELETE_PATH,
    SCENARIOS_PATH,
    SCENARIOS_RUN_PATH,
    SCENARIOS_TEST_PATH,
    SCENARIOS_UPCOMING_CANCEL_PATH,
    SCENARIOS_UPCOMING_PATH,
)
from .correlation import CorrelationIdError, resolve_correlation_id
from .realtime_api import publish_command_receipt

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


ADMIN_SCENARIOS_PATH = "/api/hausman_hub/v1/admin/scenarios"
ADMIN_SCENARIOS_CATALOG_PATH = f"{ADMIN_SCENARIOS_PATH}/catalog"
ADMIN_SCENARIOS_HEALTH_PATH = f"{ADMIN_SCENARIOS_PATH}/health"
ADMIN_SCENARIOS_NODE_RED_PATH = f"{ADMIN_SCENARIOS_PATH}/node-red"
ADMIN_SCENARIOS_NODE_RED_SOURCE_PATH = (
    f"{ADMIN_SCENARIOS_NODE_RED_PATH}/source/{{scenario_id}}"
)
ADMIN_SCENARIOS_TEST_PATH = f"{ADMIN_SCENARIOS_PATH}/test"
ADMIN_SCENARIOS_DELETE_PATH = f"{ADMIN_SCENARIOS_PATH}/delete"
ADMIN_SCENARIOS_RUN_PATH = f"{ADMIN_SCENARIOS_PATH}/run"
ADMIN_SCENARIOS_AI_DRAFT_PATH = f"{ADMIN_SCENARIOS_PATH}/ai-draft"
_MDI_ICON = re.compile(r"^mdi:[a-z0-9-]+$")


class _ScenarioView(HomeAssistantView):
    requires_auth = True
    cors_allowed = False
    extra_urls: tuple[str, ...] = ()

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    def _unavailable(self) -> Any:
        return self.json_message(
            "The HausmanHub scenario API is unavailable.",
            HTTPStatus.SERVICE_UNAVAILABLE,
            headers=NO_STORE_HEADERS,
        )

    def _service_ready(self) -> ScenarioService | None:
        service = self._hass.data.get(DOMAIN, {}).get("scenario_service")
        if not isinstance(service, ScenarioService):
            return None
        if service._registry is None:  # noqa: SLF001
            return None
        return service

    def _authorized(self, request: Any) -> bool:
        return _is_local_admin_request(request)


class ScenarioNodeRedView(_ScenarioView):
    """Expose Node-RED availability and managed-flow synchronization."""

    url = ADMIN_SCENARIOS_NODE_RED_PATH
    name = "api:hausman_hub:scenarios_node_red"

    async def get(self, request: Any) -> Any:
        if not _is_exact_request(request, self.url):
            return _not_found(self)
        if not self._authorized(request):
            return _forbidden(self)
        service = self._service_ready()
        if service is None:
            return self._unavailable()
        return self.json(
            await service.async_node_red_status(),
            status_code=HTTPStatus.OK,
            headers=NO_STORE_HEADERS,
        )


class ScenarioNodeRedSourceView(_ScenarioView):
    """Read, validate and save one managed Node-RED function."""

    url = ADMIN_SCENARIOS_NODE_RED_SOURCE_PATH
    name = "api:hausman_hub:scenario_node_red_source"

    def _scenario_id(self, request: Any) -> str | None:
        scenario_id = getattr(request, "match_info", {}).get("scenario_id")
        if (
            not isinstance(scenario_id, str)
            or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", scenario_id) is None
            or not _is_exact_request(
                request, self.url.format(scenario_id=scenario_id)
            )
        ):
            return None
        return scenario_id

    async def get(self, request: Any) -> Any:
        scenario_id = self._scenario_id(request)
        if scenario_id is None:
            return _not_found(self)
        if not self._authorized(request):
            return _forbidden(self)
        service = self._service_ready()
        if service is None:
            return self._unavailable()
        try:
            payload = await service.async_node_red_source(scenario_id)
        except ScenarioServiceError as error:
            return self.json_message(
                error.message, error.status, headers=NO_STORE_HEADERS
            )
        return self.json(payload, headers=NO_STORE_HEADERS)

    async def put(self, request: Any) -> Any:
        scenario_id = self._scenario_id(request)
        if scenario_id is None:
            return _not_found(self)
        if not self._authorized(request):
            return _forbidden(self)
        service = self._service_ready()
        if service is None:
            return self._unavailable()
        try:
            payload = await _request_json(request)
        except ValueError as error:
            return self.json_message(
                str(error), HTTPStatus.BAD_REQUEST, headers=NO_STORE_HEADERS
            )
        if not isinstance(payload, Mapping):
            return self.json_message(
                "Request body must be a JSON object.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        contract = payload.get("contract")
        if (
            not isinstance(contract, Mapping)
            or contract.get("name")
            != "hausman-hub-scenario-node-red-source-update-request"
            or contract.get("version") != 1
            or payload.get("scenarioId") != scenario_id
        ):
            return self.json_message(
                "Node-RED source update contract or scenarioId is invalid.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        try:
            receipt = await service.async_update_node_red_source(
                scenario_id, payload
            )
        except ScenarioRevisionConflictError as error:
            return _revision_conflict_response(self, error)
        except ScenarioNodeRedSourceConflictError as error:
            return self.json(
                {
                    "ok": False,
                    "status": "conflict",
                    "error": "source_conflict",
                    "message": (
                        "Алгоритм изменён в другом редакторе. "
                        "Перечитайте его перед сохранением."
                    ),
                    "scenarioId": scenario_id,
                    "expectedSourceHash": error.expected_hash,
                    "currentSourceHash": error.current_hash,
                },
                status_code=HTTPStatus.CONFLICT,
                headers=NO_STORE_HEADERS,
            )
        except ScenarioValidationError as error:
            return self.json(
                _validation_error_payload(error),
                status_code=HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        except ScenarioServiceError as error:
            return self.json_message(
                error.message, error.status, headers=NO_STORE_HEADERS
            )
        return self.json(receipt, headers=NO_STORE_HEADERS)


class ScenarioCatalogView(_ScenarioView):
    """Expose the live device/action catalog for the scenario editor."""

    url = ADMIN_SCENARIOS_CATALOG_PATH
    name = "api:hausman_hub:scenarios_catalog"

    async def get(self, request: Any) -> Any:
        if not _is_exact_request(request, self.url):
            return _not_found(self)
        if not self._authorized(request):
            return _forbidden(self)
        service = self._service_ready()
        if service is None:
            return self._unavailable()
        catalog = service.current_catalog()
        devices = []
        for device in catalog.devices.values():
            item = {
                "target_id": device.target_id,
                "name": device.name,
                "entity_id": device.entity_id,
                "properties": [
                    {
                        "property_id": prop.property_id,
                        "label": prop.label,
                        "value_type": prop.value_type,
                        "comparisons": list(prop.comparisons),
                        "source": prop.source,
                        "availability_policy": prop.availability_policy,
                        **({"unit": prop.unit} if prop.unit is not None else {}),
                        **(
                            {
                                "options": [
                                    {"value": option.value, "label": option.label}
                                    for option in prop.options
                                ]
                            }
                            if prop.options
                            else {}
                        ),
                    }
                    for prop in device.properties
                ],
                "actions": [
                    {
                        "action_id": action.action_id,
                        "title": action.title,
                        "domain": action.domain,
                        "service": action.service,
                        "allowed_fields": sorted(action.allowed_fields),
                        **(
                            {"value_policy": dict(action.value_policy)}
                            if action.value_policy is not None
                            else {}
                        ),
                    }
                    for action in device.actions
                ],
            }
            for key in (
                "physical_id",
                "physical_name",
                "room_id",
                "room_name",
                "device_type",
                "device_type_name",
                "capability_name",
            ):
                value = getattr(device, key)
                if value is not None:
                    item[key] = value
            devices.append(item)
        saved_scenarios = await service.async_list_scenarios()
        scenarios = [
            _scenario_catalog_summary(scenario)
            for scenario in saved_scenarios
        ]
        return self.json(
            {
                "devices": sorted(devices, key=lambda item: item["name"]),
                "scenarios": scenarios,
                "readiness": service.catalog_readiness,
            },
            headers=NO_STORE_HEADERS,
        )


class ScenarioHealthView(_ScenarioView):
    """Return redacted live-catalog problems without editing or running scenarios."""

    url = ADMIN_SCENARIOS_HEALTH_PATH
    name = "api:hausman_hub:scenarios_health"

    async def get(self, request: Any) -> Any:
        if not _is_exact_request(request, self.url):
            return _not_found(self)
        if not self._authorized(request):
            return _forbidden(self)
        service = self._service_ready()
        if service is None:
            return self._unavailable()
        try:
            payload = await service.async_scenario_health()
        except ScenarioServiceError as error:
            return self.json_message(
                error.message, error.status, headers=NO_STORE_HEADERS
            )
        return self.json(payload, headers=NO_STORE_HEADERS)


def _scenario_catalog_summary(scenario: object) -> dict[str, str]:
    """Return the additive scenario metadata approved by catalog contract v1."""

    summary = {
        "id": str(getattr(scenario, "id")),
        "title": str(getattr(scenario, "title")),
    }
    icon = getattr(scenario, "icon", None)
    if (
        isinstance(icon, str)
        and len(icon) <= 80
        and _MDI_ICON.fullmatch(icon) is not None
    ):
        summary["icon"] = icon
    return summary


class ScenariosView(_ScenarioView):
    """List all scenarios or atomically create/update one."""

    url = ADMIN_SCENARIOS_PATH
    name = "api:hausman_hub:scenarios"

    async def get(self, request: Any) -> Any:
        if not _is_exact_request(request, self.url):
            return _not_found(self)
        if not self._authorized(request):
            return _forbidden(self)
        service = self._service_ready()
        if service is None:
            return self._unavailable()
        revision = await service.async_scenario_content_revision()
        etag = f'"scenario-{revision}"'
        headers = {**NO_STORE_HEADERS, "ETag": etag}
        request_headers = getattr(request, "headers", {})
        if _if_none_match_matches(request_headers.get("If-None-Match"), etag):
            from aiohttp import web  # noqa: PLC0415

            return web.Response(status=HTTPStatus.NOT_MODIFIED, headers=headers)
        payload = await service.async_scenario_list_payload()
        payload_revision = payload["contentRevision"]
        if isinstance(payload_revision, str) and payload_revision != revision:
            etag = f'"scenario-{payload_revision}"'
            headers = {**NO_STORE_HEADERS, "ETag": etag}
        return self.json(payload, headers=headers)

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, self.url):
            return _not_found(self)
        if not self._authorized(request):
            return _forbidden(self)
        service = self._service_ready()
        if service is None:
            return self._unavailable()
        try:
            payload = await _request_json(request)
        except ValueError:
            return self.json_message(
                "The scenario body is invalid.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        try:
            scenario = await service.async_update_scenario(payload)
        except ScenarioCatalogNotReadyError as error:
            return self.json(
                {
                    "ok": False,
                    "status": "failed",
                    "error": "scenario_catalog_not_ready",
                    "message": error.message,
                    "readiness": error.readiness,
                },
                status_code=HTTPStatus.CONFLICT,
                headers=NO_STORE_HEADERS,
            )
        except ScenarioRevisionConflictError as error:
            return _revision_conflict_response(self, error)
        except ScenarioValidationError as error:
            return self.json(
                {
                    "ok": False,
                    "status": "failed",
                    "error": "scenario_validation_failed",
                    "message": error.message,
                    "violations": [
                        {
                            "message": str(v),
                            "path": v.path,
                            "code": v.code,
                        }
                        for v in error.violations
                    ],
                },
                status_code=HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        except ScenarioServiceError as error:
            return self.json_message(
                error.message,
                error.status,
                headers=NO_STORE_HEADERS,
            )
        return self.json(
            {
                "ok": True,
                "status": "success",
                "scenario_id": scenario.id,
                "updated_at": scenario.updated_at,
            },
            headers=NO_STORE_HEADERS,
        )


class ScenarioTestView(_ScenarioView):
    """Dry-run a scenario definition without saving or executing commands."""

    url = ADMIN_SCENARIOS_TEST_PATH
    name = "api:hausman_hub:scenario_test"

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, self.url):
            return _not_found(self)
        if not self._authorized(request):
            return _forbidden(self)
        service = self._service_ready()
        if service is None:
            return self._unavailable()
        try:
            payload = await _request_json(request)
        except ValueError:
            return self.json_message(
                "The scenario test body is invalid.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        try:
            result = await service.async_test_scenario(payload)
        except ScenarioCatalogNotReadyError as error:
            return self.json(
                {
                    "ok": False,
                    "status": "failed",
                    "error": "scenario_catalog_not_ready",
                    "message": error.message,
                    "readiness": error.readiness,
                },
                status_code=HTTPStatus.CONFLICT,
                headers=NO_STORE_HEADERS,
            )
        except ScenarioValidationError as error:
            return self.json(
                {
                    "ok": False,
                    "status": "failed",
                    "error": "scenario_validation_failed",
                    "message": error.message,
                    "violations": [
                        {
                            "message": str(v),
                            "path": v.path,
                            "code": v.code,
                        }
                        for v in error.violations
                    ],
                },
                status_code=HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        except ScenarioServiceError as error:
            return self.json_message(
                error.message,
                error.status,
                headers=NO_STORE_HEADERS,
            )
        return self.json(
            {
                "ok": True,
                "status": "success",
                "result": result,
            },
            headers=NO_STORE_HEADERS,
        )


class ScenarioAiDraftView(_ScenarioView):
    """Generate a validated, disabled draft without persistence or commands."""

    url = ADMIN_SCENARIOS_AI_DRAFT_PATH
    name = "api:hausman_hub:scenario_ai_draft"

    def _ai_service(self) -> ScenarioAiDraftService | None:
        candidate = self._hass.data.get(DOMAIN, {}).get("scenario_ai_draft_service")
        return candidate if isinstance(candidate, ScenarioAiDraftService) else None

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, self.url):
            return _not_found(self)
        if not self._authorized(request):
            return _forbidden(self)
        service = self._ai_service()
        if service is None or not service.available:
            return self.json(
                {
                    "ok": False,
                    "status": "failed",
                    "error": "scenario_ai_unavailable",
                    "message": "Подключите и включите нейросеть в настройках Hausman.",
                },
                status_code=HTTPStatus.SERVICE_UNAVAILABLE,
                headers=NO_STORE_HEADERS,
            )
        try:
            payload = await _request_json(request)
            result = await service.async_generate(payload)
        except (ValueError, ScenarioAiRequestError):
            return self.json(
                {
                    "ok": False,
                    "status": "failed",
                    "error": "invalid_scenario_ai_request",
                    "message": "Описание или упоминание устройства заполнено неверно.",
                },
                status_code=HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        except ScenarioCatalogNotReadyError as error:
            return self.json(
                {
                    "ok": False,
                    "status": "failed",
                    "error": "scenario_catalog_not_ready",
                    "message": error.message,
                    "readiness": error.readiness,
                },
                status_code=HTTPStatus.CONFLICT,
                headers=NO_STORE_HEADERS,
            )
        except ScenarioAiUnavailable:
            return self.json(
                {
                    "ok": False,
                    "status": "failed",
                    "error": "scenario_ai_unavailable",
                    "message": "Нейросеть не ответила. Попробуйте ещё раз.",
                },
                status_code=HTTPStatus.SERVICE_UNAVAILABLE,
                headers=NO_STORE_HEADERS,
            )
        except ScenarioAiOutputInvalid:
            return self.json(
                {
                    "ok": False,
                    "status": "failed",
                    "error": "scenario_ai_output_invalid",
                    "message": "Черновик не прошёл проверку Hausman. Уточните описание и повторите.",
                },
                status_code=HTTPStatus.BAD_GATEWAY,
                headers=NO_STORE_HEADERS,
            )
        return self.json(result, headers=NO_STORE_HEADERS)


class ScenarioDeleteView(_ScenarioView):
    """Delete a scenario unless it is referenced by another scenario."""

    url = ADMIN_SCENARIOS_DELETE_PATH
    name = "api:hausman_hub:scenario_delete"

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, self.url):
            return _not_found(self)
        if not self._authorized(request):
            return _forbidden(self)
        service = self._service_ready()
        if service is None:
            return self._unavailable()
        try:
            payload = await _request_json(request)
        except ValueError:
            return self.json_message(
                "The scenario delete body is invalid.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        scenario_id = payload.get("scenario_id") if isinstance(payload, Mapping) else None
        if not isinstance(scenario_id, str) or not scenario_id:
            return self.json_message(
                "scenario_id is required.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        try:
            await service.async_delete_scenario(scenario_id)
        except (ScenarioProtectedError, ScenarioReferencedError) as error:
            return self.json_message(
                error.message,
                HTTPStatus.CONFLICT,
                headers=NO_STORE_HEADERS,
            )
        except ScenarioNotFoundError as error:
            return self.json_message(
                error.message,
                HTTPStatus.NOT_FOUND,
                headers=NO_STORE_HEADERS,
            )
        except ScenarioServiceError as error:
            return self.json_message(
                error.message,
                error.status,
                headers=NO_STORE_HEADERS,
            )
        return self.json(
            {"ok": True, "status": "success", "scenario_id": scenario_id},
            headers=NO_STORE_HEADERS,
        )


class ScenarioRunView(_ScenarioView):
    """Execute a saved scenario and return confirmed receipts."""

    url = ADMIN_SCENARIOS_RUN_PATH
    name = "api:hausman_hub:scenario_run"

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, self.url):
            return _not_found(self)
        if not self._authorized(request):
            return _forbidden(self)
        service = self._service_ready()
        if service is None:
            return self._unavailable()
        try:
            payload = await _request_json(request)
        except ValueError:
            return self.json_message(
                "The scenario run body is invalid.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        scenario_id = payload.get("scenario_id") if isinstance(payload, Mapping) else None
        if not isinstance(scenario_id, str) or not scenario_id:
            return self.json_message(
                "scenario_id is required.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        try:
            correlation_id = resolve_correlation_id(payload, field="correlationId")
            result = await service.async_run_scenario(
                scenario_id,
                correlation_id=correlation_id,
                trigger_context={
                    "source": "manual",
                    "trigger_id": None,
                    "recovery": False,
                },
            )
        except CorrelationIdError:
            return self.json_message(
                "correlationId is invalid.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        except ScenarioNotFoundError as error:
            return self.json_message(
                error.message,
                HTTPStatus.NOT_FOUND,
                headers=NO_STORE_HEADERS,
            )
        except ScenarioServiceError as error:
            return self.json_message(
                error.message,
                error.status,
                headers=NO_STORE_HEADERS,
            )
        completed = result.get("status") == "completed"
        confirmed = completed and result.get("confirmed") is True
        response = {
            "correlationId": correlation_id,
            "ok": completed,
            "accepted": completed,
            "confirmed": confirmed,
            "status": "success" if completed else "failed",
            "message": (
                "Сценарий выполнен и подтверждён."
                if confirmed
                else "Сценарий выполнен, но не все устройства подтвердили состояние."
                if completed
                else "Сценарий не выполнен."
            ),
            "result": result,
        }
        publish_command_receipt(
            self._hass,
            {
                "requestId": correlation_id,
                "targetId": scenario_id,
                **response,
            },
            operation="scenario_run",
            persist_journal=False,
        )
        return self.json(
            response,
            status_code=HTTPStatus.OK if completed else HTTPStatus.CONFLICT,
            headers=NO_STORE_HEADERS,
        )


class ScenarioActionView(_ScenarioView):
    """Tablet-compatible action dispatch for scenarios.

    Accepts the same action-oriented contract the Android Smart Home Center
    editor uses, routing ``update_scenario``, ``test_scenario``,
    ``delete_scenario`` and ``run_scenario`` to the matching REST handlers.
    """

    url = f"{ADMIN_SCENARIOS_PATH}/action"
    name = "api:hausman_hub:scenario_action"

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, self.url):
            return _not_found(self)
        if not self._authorized(request):
            return _forbidden(self)
        service = self._service_ready()
        if service is None:
            return self._unavailable()
        try:
            payload = await _request_json(request)
        except ValueError:
            return self.json_message(
                "The scenario action body is invalid.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        if not isinstance(payload, Mapping):
            return self.json_message(
                "The scenario action body must be an object.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        action = payload.get("action")
        if action == "update_scenario":
            try:
                scenario = await service.async_update_scenario(dict(payload))
            except ScenarioRevisionConflictError as error:
                return _revision_conflict_response(self, error)
            except ScenarioValidationError as error:
                return self.json(
                    _validation_error_payload(error),
                    status_code=HTTPStatus.BAD_REQUEST,
                    headers=NO_STORE_HEADERS,
                )
            except ScenarioServiceError as error:
                return self.json_message(error.message, error.status, headers=NO_STORE_HEADERS)
            return self.json(
                {"ok": True, "status": "success", "scenario_id": scenario.id, "updated_at": scenario.updated_at},
                headers=NO_STORE_HEADERS,
            )
        if action == "test_scenario":
            try:
                result = await service.async_test_scenario(dict(payload))
            except ScenarioValidationError as error:
                return self.json(
                    _validation_error_payload(error),
                    status_code=HTTPStatus.BAD_REQUEST,
                    headers=NO_STORE_HEADERS,
                )
            except ScenarioServiceError as error:
                return self.json_message(error.message, error.status, headers=NO_STORE_HEADERS)
            return self.json({"ok": True, "status": "success", "result": result}, headers=NO_STORE_HEADERS)
        if action == "delete_scenario":
            return await _delete_scenario(self, service, payload)
        if action == "run_scenario":
            return await _run_scenario(self, service, payload)
        return self.json_message(
            "Unknown scenario action.",
            HTTPStatus.BAD_REQUEST,
            headers=NO_STORE_HEADERS,
        )


class _TabletScenarioAccess:
    """Allow authenticated local tablet users on the public scenario surface."""

    def _authorized(self, request: Any) -> bool:
        return _is_local_tablet_request(request)


class ScenarioUpcomingView(_ScenarioView):
    """List upcoming scheduled scenario runs for the panel and the tablet."""

    url = SCENARIOS_UPCOMING_PATH
    name = "api:hausman_hub:scenario_upcoming"

    def _authorized(self, request: Any) -> bool:
        return _is_local_tablet_request(request) or _is_local_admin_request(request)

    async def get(self, request: Any) -> Any:
        if not _is_exact_request(request, self.url):
            return _not_found(self)
        if not self._authorized(request):
            return _forbidden(self)
        service = self._service_ready()
        if service is None:
            return self._unavailable()
        return self.json(
            await service.async_list_upcoming_events(),
            headers=NO_STORE_HEADERS,
        )


class ScenarioUpcomingCancelView(_ScenarioView):
    """Skip one concrete scheduled run (skip-once)."""

    url = SCENARIOS_UPCOMING_CANCEL_PATH
    name = "api:hausman_hub:scenario_upcoming_cancel"

    def _authorized(self, request: Any) -> bool:
        return _is_local_tablet_request(request) or _is_local_admin_request(request)

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, self.url):
            return _not_found(self)
        if not self._authorized(request):
            return _forbidden(self)
        service = self._service_ready()
        if service is None:
            return self._unavailable()
        try:
            payload = await _request_json(request)
        except ValueError as error:
            return self.json_message(
                str(error), HTTPStatus.BAD_REQUEST, headers=NO_STORE_HEADERS
            )
        if not isinstance(payload, Mapping):
            return self.json_message(
                "Request body must be a JSON object.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        scenario_id = payload.get("scenarioId")
        trigger_id = payload.get("triggerId")
        run_at = payload.get("runAt")
        if not all(
            isinstance(value, str) and value
            for value in (scenario_id, trigger_id, run_at)
        ):
            return self.json_message(
                "scenarioId, triggerId and runAt are required.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        try:
            correlation_id = resolve_correlation_id(payload, field="correlationId")
            receipt = await service.async_cancel_upcoming(
                scenario_id, trigger_id, run_at
            )
        except CorrelationIdError:
            return self.json_message(
                "correlationId is invalid.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        except ScenarioServiceError as error:
            return self.json_message(
                error.message, error.status, headers=NO_STORE_HEADERS
            )
        response = {**receipt, "correlationId": correlation_id}
        publish_command_receipt(
            self._hass,
            {
                **response,
                "requestId": correlation_id,
                "targetId": scenario_id,
            },
            operation="scenario_upcoming_cancel",
        )
        return self.json(response, headers=NO_STORE_HEADERS)


class TabletScenarioCatalogView(_TabletScenarioAccess, ScenarioCatalogView):
    url = SCENARIOS_CATALOG_PATH
    name = "api:hausman_hub:tablet_scenarios_catalog"


class TabletScenarioHealthView(_TabletScenarioAccess, ScenarioHealthView):
    url = SCENARIOS_HEALTH_PATH
    name = "api:hausman_hub:tablet_scenarios_health"


class TabletScenarioNodeRedView(_TabletScenarioAccess, ScenarioNodeRedView):
    url = SCENARIOS_NODE_RED_PATH
    name = "api:hausman_hub:tablet_scenarios_node_red"


class TabletScenarioNodeRedSourceView(
    _TabletScenarioAccess, ScenarioNodeRedSourceView
):
    url = SCENARIOS_NODE_RED_SOURCE_PATH
    name = "api:hausman_hub:tablet_scenario_node_red_source"


class TabletScenariosView(_TabletScenarioAccess, ScenariosView):
    url = SCENARIOS_PATH
    name = "api:hausman_hub:tablet_scenarios"


class TabletScenarioTestView(_TabletScenarioAccess, ScenarioTestView):
    url = SCENARIOS_TEST_PATH
    name = "api:hausman_hub:tablet_scenario_test"


class TabletScenarioDeleteView(_TabletScenarioAccess, ScenarioDeleteView):
    url = SCENARIOS_DELETE_PATH
    name = "api:hausman_hub:tablet_scenario_delete"


class TabletScenarioRunView(_TabletScenarioAccess, ScenarioRunView):
    url = SCENARIOS_RUN_PATH
    name = "api:hausman_hub:tablet_scenario_run"


class TabletScenarioActionView(_TabletScenarioAccess, ScenarioActionView):
    url = SCENARIOS_ACTION_PATH
    name = "api:hausman_hub:tablet_scenario_action"


class TabletScenarioAiDraftView(_TabletScenarioAccess, ScenarioAiDraftView):
    url = SCENARIOS_AI_DRAFT_PATH
    name = "api:hausman_hub:tablet_scenario_ai_draft"


def _validation_error_payload(error: ScenarioValidationError) -> dict[str, object]:
    return {
        "ok": False,
        "status": "failed",
        "error": "scenario_validation_failed",
        "message": error.message,
        "violations": [
            {"message": str(item), "path": item.path, "code": item.code}
            for item in error.violations
        ],
    }


def _revision_conflict_payload(
    error: ScenarioRevisionConflictError,
) -> dict[str, object]:
    """Return only the revision required to reload one stale editor draft."""

    return {
        "ok": False,
        "status": "conflict",
        "error": "revision_conflict",
        "message": "Сценарий изменён на другом устройстве. Перечитайте его перед сохранением.",
        "scenarioId": error.scenario_id,
        "expectedRevision": error.expected_revision,
        "currentRevision": error.current_revision,
        "changedFields": list(error.changed_fields),
        "currentRoomIds": list(error.current_room_ids),
        "currentActionIds": list(error.current_action_ids),
    }


def _revision_conflict_response(
    view: _ScenarioView, error: ScenarioRevisionConflictError
) -> Any:
    """Keep all scenario mutation routes on one stale-draft response."""

    return view.json(
        _revision_conflict_payload(error),
        status_code=HTTPStatus.CONFLICT,
        headers=NO_STORE_HEADERS,
    )


def _if_none_match_matches(value: object, etag: str) -> bool:
    """Perform weak comparison for a comma-separated If-None-Match header."""

    if not isinstance(value, str):
        return False
    for candidate in value.split(","):
        token = candidate.strip()
        if token == "*":
            return True
        if token.startswith("W/"):
            token = token[2:].strip()
        if token == etag:
            return True
    return False


async def _delete_scenario(
    view: _ScenarioView,
    service: ScenarioService,
    payload: Mapping[str, object],
) -> Any:
    scenario_id = payload.get("scenario_id") or payload.get("scenarioId")
    if not isinstance(scenario_id, str) or not scenario_id:
        return view.json_message("scenario_id is required.", HTTPStatus.BAD_REQUEST, headers=NO_STORE_HEADERS)
    try:
        await service.async_delete_scenario(scenario_id)
    except (ScenarioProtectedError, ScenarioReferencedError) as error:
        return view.json_message(error.message, HTTPStatus.CONFLICT, headers=NO_STORE_HEADERS)
    except ScenarioNotFoundError as error:
        return view.json_message(error.message, HTTPStatus.NOT_FOUND, headers=NO_STORE_HEADERS)
    except ScenarioServiceError as error:
        return view.json_message(error.message, error.status, headers=NO_STORE_HEADERS)
    return view.json({"ok": True, "status": "success", "scenario_id": scenario_id}, headers=NO_STORE_HEADERS)


async def _run_scenario(
    view: _ScenarioView,
    service: ScenarioService,
    payload: Mapping[str, object],
) -> Any:
    scenario_id = payload.get("scenario_id") or payload.get("scenarioId")
    if not isinstance(scenario_id, str) or not scenario_id:
        return view.json_message("scenario_id is required.", HTTPStatus.BAD_REQUEST, headers=NO_STORE_HEADERS)
    try:
        correlation_id = resolve_correlation_id(payload, field="correlationId")
        result = await service.async_run_scenario(
            scenario_id,
            correlation_id=correlation_id,
            trigger_context={
                "source": "manual",
                "trigger_id": None,
                "recovery": False,
            },
        )
    except CorrelationIdError:
        return view.json_message(
            "correlationId is invalid.", HTTPStatus.BAD_REQUEST, headers=NO_STORE_HEADERS
        )
    except ScenarioNotFoundError as error:
        return view.json_message(error.message, HTTPStatus.NOT_FOUND, headers=NO_STORE_HEADERS)
    except ScenarioServiceError as error:
        return view.json_message(error.message, error.status, headers=NO_STORE_HEADERS)
    completed = result.get("status") == "completed"
    confirmed = completed and result.get("confirmed") is True
    response = {
        "correlationId": correlation_id,
        "ok": completed,
        "accepted": completed,
        "confirmed": confirmed,
        "status": "success" if completed else "failed",
        "message": (
            "Сценарий выполнен и подтверждён."
            if confirmed
            else "Сценарий выполнен, но не все устройства подтвердили состояние."
            if completed
            else "Сценарий не выполнен."
        ),
        "result": result,
    }
    publish_command_receipt(
        view._hass,
        {
            "requestId": correlation_id,
            "targetId": scenario_id,
            **response,
        },
        operation="scenario_run",
        persist_journal=False,
    )
    return view.json(
        response,
        status_code=HTTPStatus.OK if completed else HTTPStatus.CONFLICT,
        headers=NO_STORE_HEADERS,
    )


def scenario_api_views(
    hass: HomeAssistant, service: ScenarioService
) -> tuple[HomeAssistantView, ...]:
    """Return all scenario admin views wired to one loaded service."""

    del service
    return (
        ScenarioCatalogView(hass),
        ScenarioHealthView(hass),
        ScenarioNodeRedView(hass),
        ScenarioNodeRedSourceView(hass),
        ScenariosView(hass),
        ScenarioTestView(hass),
        ScenarioAiDraftView(hass),
        ScenarioDeleteView(hass),
        ScenarioRunView(hass),
        ScenarioActionView(hass),
        TabletScenarioCatalogView(hass),
        TabletScenarioHealthView(hass),
        TabletScenarioNodeRedView(hass),
        TabletScenarioNodeRedSourceView(hass),
        TabletScenariosView(hass),
        TabletScenarioTestView(hass),
        TabletScenarioAiDraftView(hass),
        TabletScenarioDeleteView(hass),
        TabletScenarioRunView(hass),
        TabletScenarioActionView(hass),
        ScenarioUpcomingView(hass),
        ScenarioUpcomingCancelView(hass),
    )
