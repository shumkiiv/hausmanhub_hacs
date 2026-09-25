"""Local-admin API for protected topology provisioning/readiness."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from homeassistant.components.http import HomeAssistantView

from .application.api_capabilities import API_BASE_PATH
from .application.protected_topology import (
    PROTECTED_TOPOLOGY_READINESS_CONTRACT,
    PROTECTED_TOPOLOGY_REPLACE_CONTRACT,
    ProtectedTopologyService,
    ProtectedTopologyViolation,
    _valid_role,
)
from .climate_api import (
    DOMAIN,
    NO_STORE_HEADERS,
    _forbidden,
    _is_exact_request,
    _is_local_admin_request,
    _not_found,
    _request_json,
)


DATA_PROTECTED_TOPOLOGY = "protected_topology_service"
PROTECTED_TOPOLOGY_PATH = f"{API_BASE_PATH}/admin/protected-topology"
PROTECTED_TOPOLOGY_READINESS_PATH = f"{PROTECTED_TOPOLOGY_PATH}/readiness"


def protected_topology_service(hass: Any) -> ProtectedTopologyService | None:
    candidate = hass.data.get(DOMAIN, {}).get(DATA_PROTECTED_TOPOLOGY)
    return candidate if isinstance(candidate, ProtectedTopologyService) else None


def register_protected_topology_api(hass: Any) -> None:
    data = hass.data.setdefault(DOMAIN, {})
    if data.get("protected_topology_api_registered") is True:
        return
    for view in (
        ProtectedTopologyView(hass),
        ProtectedTopologyReadinessView(hass),
    ):
        hass.http.register_view(view)
    data["protected_topology_api_registered"] = True


class _ProtectedTopologyView(HomeAssistantView):
    requires_auth = True
    cors_allowed = False

    def __init__(self, hass: Any) -> None:
        self._hass = hass

    def _service(self) -> ProtectedTopologyService | None:
        return protected_topology_service(self._hass)

    def _unavailable(self) -> Any:
        return self.json_message(
            "Protected topology is unavailable.",
            HTTPStatus.SERVICE_UNAVAILABLE,
            headers=NO_STORE_HEADERS,
        )


class ProtectedTopologyView(_ProtectedTopologyView):
    """Read redacted topology status or replace local private bindings."""

    url = PROTECTED_TOPOLOGY_PATH
    name = "api:hausman_hub:protected_topology"

    async def get(self, request: Any) -> Any:
        if not _is_exact_request(request, PROTECTED_TOPOLOGY_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        service = self._service()
        if service is None:
            return self._unavailable()
        return self.json(service.redacted_document(), headers=NO_STORE_HEADERS)

    async def put(self, request: Any) -> Any:
        if not _is_exact_request(request, PROTECTED_TOPOLOGY_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        service = self._service()
        if service is None:
            return self._unavailable()
        try:
            payload = await _request_json(request, maximum_bytes=32 * 1024)
            if not isinstance(payload, dict) or not {
                "contract", "expectedRevision", "bindings",
            } <= set(payload) or set(payload) - {
                "contract", "expectedRevision", "bindings", "migrationExpectations",
            }:
                raise ProtectedTopologyViolation("protected topology body is invalid")
            contract = payload.get("contract")
            if not isinstance(contract, dict) or contract != {
                "name": PROTECTED_TOPOLOGY_REPLACE_CONTRACT,
                "version": 1,
            }:
                raise ProtectedTopologyViolation("protected topology body is invalid")
            result = await service.async_replace(
                payload["expectedRevision"],
                payload["bindings"],
                payload.get("migrationExpectations"),
            )
        except ProtectedTopologyViolation as error:
            if error.storage:
                return self.json_message(
                    "Protected topology was not saved.",
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    headers=NO_STORE_HEADERS,
                )
            return self.json_message(
                "Protected topology revision is stale."
                if error.stale
                else "Protected topology body is invalid.",
                HTTPStatus.CONFLICT if error.stale else HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        return self.json(result, headers=NO_STORE_HEADERS)


class ProtectedTopologyReadinessView(_ProtectedTopologyView):
    """Return only logical role readiness, never private ids."""

    url = PROTECTED_TOPOLOGY_READINESS_PATH
    name = "api:hausman_hub:protected_topology_readiness"

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, PROTECTED_TOPOLOGY_READINESS_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        service = self._service()
        if service is None:
            return self._unavailable()
        try:
            payload = await _request_json(request, maximum_bytes=16 * 1024)
            contract = payload.get("contract") if isinstance(payload, dict) else None
            if contract != {
                "name": PROTECTED_TOPOLOGY_READINESS_CONTRACT,
                "version": 1,
            }:
                raise ValueError
            roles = payload.get("roles") if isinstance(payload, dict) else None
            if (
                not isinstance(roles, list)
                or not roles
                or len(roles) > 64
                or len(roles) != len(set(roles))
            ):
                raise ValueError
            tuple(_valid_role(role) for role in roles)
        except (ValueError, TypeError, ProtectedTopologyViolation):
            return self.json_message(
                "Protected topology readiness request is invalid.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        return self.json(service.readiness(tuple(roles)), headers=NO_STORE_HEADERS)


__all__ = [
    "DATA_PROTECTED_TOPOLOGY",
    "PROTECTED_TOPOLOGY_PATH",
    "PROTECTED_TOPOLOGY_READINESS_PATH",
    "protected_topology_service",
    "register_protected_topology_api",
]
