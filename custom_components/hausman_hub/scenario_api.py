"""Authenticated admin and local-tablet HTTP views for HausmanHub scenarios."""

from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from homeassistant.components.http import HomeAssistantView

from .application.scenario_service import (
    ScenarioNotFoundError,
    ScenarioReferencedError,
    ScenarioService,
    ScenarioServiceError,
    ScenarioValidationError,
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
    SCENARIOS_CATALOG_PATH,
    SCENARIOS_DELETE_PATH,
    SCENARIOS_PATH,
    SCENARIOS_RUN_PATH,
    SCENARIOS_TEST_PATH,
)
from .domain.scenarios import ScenarioDefinition, _scenario_to_payload
from .realtime_api import publish_command_receipt

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


ADMIN_SCENARIOS_PATH = "/api/hausman_hub/v1/admin/scenarios"
ADMIN_SCENARIOS_CATALOG_PATH = f"{ADMIN_SCENARIOS_PATH}/catalog"
ADMIN_SCENARIOS_TEST_PATH = f"{ADMIN_SCENARIOS_PATH}/test"
ADMIN_SCENARIOS_DELETE_PATH = f"{ADMIN_SCENARIOS_PATH}/delete"
ADMIN_SCENARIOS_RUN_PATH = f"{ADMIN_SCENARIOS_PATH}/run"


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
        catalog = service._catalog  # noqa: SLF001
        devices = [
            {
                "target_id": device.target_id,
                "name": device.name,
                "entity_id": device.entity_id,
                "actions": [
                    {
                        "action_id": action.action_id,
                        "title": action.title,
                        "domain": action.domain,
                        "service": action.service,
                        "allowed_fields": sorted(action.allowed_fields),
                    }
                    for action in device.actions
                ],
            }
            for device in catalog.devices.values()
        ]
        return self.json(
            {"devices": sorted(devices, key=lambda item: item["name"])},
            headers=NO_STORE_HEADERS,
        )


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
        scenarios = await service.async_list_scenarios()
        return self.json(
            {"scenarios": [_scenario_to_payload(s) for s in scenarios]},
            headers=NO_STORE_HEADERS,
        )

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
                status=HTTPStatus.BAD_REQUEST,
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
                status=HTTPStatus.BAD_REQUEST,
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
        except ScenarioReferencedError as error:
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
            result = await service.async_run_scenario(scenario_id)
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
                "requestId": result.get("run_id"),
                "targetId": scenario_id,
                **response,
            },
            operation="scenario_run",
        )
        return self.json(
            response,
            status=HTTPStatus.OK if completed else HTTPStatus.CONFLICT,
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
            except ScenarioValidationError as error:
                return self.json(
                    _validation_error_payload(error),
                    status=HTTPStatus.BAD_REQUEST,
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
                    status=HTTPStatus.BAD_REQUEST,
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


class TabletScenarioCatalogView(_TabletScenarioAccess, ScenarioCatalogView):
    url = SCENARIOS_CATALOG_PATH
    name = "api:hausman_hub:tablet_scenarios_catalog"


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
    except ScenarioReferencedError as error:
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
        result = await service.async_run_scenario(scenario_id)
    except ScenarioNotFoundError as error:
        return view.json_message(error.message, HTTPStatus.NOT_FOUND, headers=NO_STORE_HEADERS)
    except ScenarioServiceError as error:
        return view.json_message(error.message, error.status, headers=NO_STORE_HEADERS)
    completed = result.get("status") == "completed"
    confirmed = completed and result.get("confirmed") is True
    response = {
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
            "requestId": result.get("run_id"),
            "targetId": scenario_id,
            **response,
        },
        operation="scenario_run",
    )
    return view.json(
        response,
        status=HTTPStatus.OK if completed else HTTPStatus.CONFLICT,
        headers=NO_STORE_HEADERS,
    )


def scenario_api_views(
    hass: HomeAssistant, service: ScenarioService
) -> tuple[HomeAssistantView, ...]:
    """Return all scenario admin views wired to one loaded service."""

    del service
    return (
        ScenarioCatalogView(hass),
        ScenariosView(hass),
        ScenarioTestView(hass),
        ScenarioDeleteView(hass),
        ScenarioRunView(hass),
        ScenarioActionView(hass),
        TabletScenarioCatalogView(hass),
        TabletScenariosView(hass),
        TabletScenarioTestView(hass),
        TabletScenarioDeleteView(hass),
        TabletScenarioRunView(hass),
        TabletScenarioActionView(hass),
    )
