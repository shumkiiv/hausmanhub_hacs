"""Local-admin API for managed-switch migration receipt re-arm.

The read route previews the recomputed logical migration manifest and the
durable receipt revision without mutating anything.  The re-arm route replaces
one stale durable receipt with a ``superseded`` marker under an explicit
compare-and-set.  Both routes are local-admin only, never return private
target/entity ids and never send a physical command.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.http import HomeAssistantView

from .application.api_capabilities import API_BASE_PATH
from .application.managed_switch_migration_rearm import (
    ManagedSwitchMigrationRearmService,
    ManagedSwitchMigrationRearmViolation,
)
from .climate_api import (
    DOMAIN,
    NO_STORE_HEADERS,
    _api_error,
    _forbidden,
    _is_exact_request,
    _is_local_admin_request,
    _not_found,
    _request_json,
)

DATA_MANAGED_SWITCH_MIGRATION_REARM = "managed_switch_migration_rearm_service"
MANAGED_SWITCH_MIGRATION_PATH = f"{API_BASE_PATH}/admin/managed-switch-migration"
MANAGED_SWITCH_MIGRATION_REARM_PATH = f"{MANAGED_SWITCH_MIGRATION_PATH}/rearm"


def managed_switch_migration_rearm_service(
    hass: Any,
) -> ManagedSwitchMigrationRearmService | None:
    candidate = hass.data.get(DOMAIN, {}).get(DATA_MANAGED_SWITCH_MIGRATION_REARM)
    return (
        candidate
        if isinstance(candidate, ManagedSwitchMigrationRearmService)
        else None
    )


def register_managed_switch_migration_api(hass: Any) -> None:
    data = hass.data.setdefault(DOMAIN, {})
    if data.get("managed_switch_migration_api_registered") is True:
        return
    for view in (
        ManagedSwitchMigrationStatusView(hass),
        ManagedSwitchMigrationRearmView(hass),
    ):
        hass.http.register_view(view)
    data["managed_switch_migration_api_registered"] = True


class _ManagedSwitchMigrationView(HomeAssistantView):
    requires_auth = True
    cors_allowed = False

    def __init__(self, hass: Any) -> None:
        self._hass = hass

    def _service(self) -> ManagedSwitchMigrationRearmService | None:
        return managed_switch_migration_rearm_service(self._hass)


class ManagedSwitchMigrationStatusView(_ManagedSwitchMigrationView):
    """Read the durable receipt revision and the recomputed expectation."""

    url = MANAGED_SWITCH_MIGRATION_PATH
    name = "api:hausman_hub:managed_switch_migration"

    async def get(self, request: Any) -> Any:
        if not _is_exact_request(request, MANAGED_SWITCH_MIGRATION_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        service = self._service()
        if service is None:
            return _api_error(self, "unavailable")
        try:
            result = await service.async_status()
        except Exception:  # noqa: BLE001 - never leak storage details
            return _api_error(self, "internal_error")
        return self.json(result, headers=NO_STORE_HEADERS)


class ManagedSwitchMigrationRearmView(_ManagedSwitchMigrationView):
    """Supersede one stale durable receipt under an explicit CAS."""

    url = MANAGED_SWITCH_MIGRATION_REARM_PATH
    name = "api:hausman_hub:managed_switch_migration_rearm"

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, MANAGED_SWITCH_MIGRATION_REARM_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        service = self._service()
        if service is None:
            return _api_error(self, "unavailable")
        try:
            payload = await _request_json(request, maximum_bytes=8 * 1024)
        except (ValueError, TypeError):
            return _api_error(self, "invalid_request")
        try:
            result = await service.async_rearm(payload)
        except ManagedSwitchMigrationRearmViolation as error:
            if error.stale:
                return _api_error(self, "revision_conflict")
            if error.unprocessable:
                return _api_error(self, "unprocessable_entity")
            return _api_error(self, "invalid_request")
        except Exception:  # noqa: BLE001 - never leak storage details
            return _api_error(self, "internal_error")
        return self.json(result, headers=NO_STORE_HEADERS)


__all__ = [
    "DATA_MANAGED_SWITCH_MIGRATION_REARM",
    "MANAGED_SWITCH_MIGRATION_PATH",
    "MANAGED_SWITCH_MIGRATION_REARM_PATH",
    "managed_switch_migration_rearm_service",
    "register_managed_switch_migration_api",
]
