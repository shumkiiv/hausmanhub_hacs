"""Authenticated local API for wall-tablet power telemetry."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from homeassistant.components.http import HomeAssistantView

from .application.operation_journal import OperationJournalService
from .application.tablet_power import (
    TABLET_BATTERY_ENTITY_ID,
    TABLET_POWER_CONTRACT_VERSION,
    TABLET_POWER_RECEIPT_CONTRACT,
    TABLET_POWER_ENTITY_ID,
    TabletPowerService,
    TabletPowerViolation,
    charging_policy_decision,
)
from .climate_api import (
    DOMAIN,
    NO_STORE_HEADERS,
    _forbidden,
    _is_exact_request,
    _is_local_dashboard_request,
    _not_found,
    _request_json,
)
from .operation_journal_api import DATA_OPERATION_JOURNAL

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

TABLET_POWER_PATH = "/api/hausman_hub/v1/tablet-power-status"
DATA_TABLET_POWER = "tablet_power_service"
DATA_TABLET_POWER_VIEW = "tablet_power_view"


def tablet_power_service(hass: HomeAssistant) -> TabletPowerService:
    data = hass.data.setdefault(DOMAIN, {})
    service = data.get(DATA_TABLET_POWER)
    if not isinstance(service, TabletPowerService):
        service = TabletPowerService()
        data[DATA_TABLET_POWER] = service
    return service


class TabletPowerView(HomeAssistantView):
    requires_auth = True
    cors_allowed = False
    extra_urls: tuple[str, ...] = ()
    url = TABLET_POWER_PATH
    name = "api:hausman_hub:tablet_power_status"

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, TABLET_POWER_PATH):
            return _not_found(self)
        if not _is_local_dashboard_request(request):
            return _forbidden(self)
        try:
            payload = await _request_json(request, maximum_bytes=4 * 1024)
            status = tablet_power_service(self._hass).update(payload)
        except (TabletPowerViolation, ValueError):
            return self.json_message(
                "Состояние питания планшета не принято.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        journal = self._hass.data.get(DOMAIN, {}).get(DATA_OPERATION_JOURNAL)
        if isinstance(journal, OperationJournalService):
            await journal.async_append(
                {
                    "correlation_id": status.correlation_id,
                    "operation": "tablet_power_update",
                    "accepted": True,
                    "confirmed": True,
                    "status": "confirmed",
                    "reason": f"battery_{status.battery_percent}_{status.power_source}",
                }
            )
        policy = charging_policy_decision(status.battery_percent)
        return self.json(
            {
                "contract": {
                    "name": TABLET_POWER_RECEIPT_CONTRACT,
                    "version": TABLET_POWER_CONTRACT_VERSION,
                },
                "correlationId": status.correlation_id,
                "accepted": True,
                "confirmed": True,
                "status": "confirmed",
                "batterySensorEntityId": TABLET_BATTERY_ENTITY_ID,
                "powerSensorEntityId": TABLET_POWER_ENTITY_ID,
                "chargingPolicy": policy,
                "physicalCommandsSent": False,
                "message": "Состояние питания планшета опубликовано в Home Assistant.",
            },
            headers=NO_STORE_HEADERS,
        )


def register_tablet_power_api(hass: HomeAssistant) -> None:
    data = hass.data.setdefault(DOMAIN, {})
    tablet_power_service(hass)
    if DATA_TABLET_POWER_VIEW in data:
        return
    view = TabletPowerView(hass)
    hass.http.register_view(view)
    data[DATA_TABLET_POWER_VIEW] = view


def clear_tablet_power_api(hass: HomeAssistant) -> None:
    data = hass.data.get(DOMAIN)
    if isinstance(data, dict):
        data.pop(DATA_TABLET_POWER, None)
