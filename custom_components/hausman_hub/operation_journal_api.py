"""Local admin boundary for the durable cross-domain operation journal."""

from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from homeassistant.components.http import HomeAssistantView

from .application.operation_journal import (
    MAX_OPERATION_JOURNAL_RECORDS,
    OperationJournalService,
)
from .climate_api import (
    DOMAIN,
    NO_STORE_HEADERS,
    _forbidden,
    _is_local_admin_request,
    _not_found,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


ADMIN_OPERATION_JOURNAL_PATH = "/api/hausman_hub/v1/admin/operations"
DATA_OPERATION_JOURNAL = "operation_journal"
DATA_OPERATION_JOURNAL_VIEW = "operation_journal_view"


class OperationJournalView(HomeAssistantView):
    """Serve a filtered journal without sending or repeating any command."""

    requires_auth = True
    cors_allowed = False
    extra_urls: tuple[str, ...] = ()
    url = ADMIN_OPERATION_JOURNAL_PATH
    name = "api:hausman_hub:operation_journal"

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def get(self, request: Any) -> Any:
        if getattr(request, "path", None) != ADMIN_OPERATION_JOURNAL_PATH:
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        query = getattr(request, "query", None)
        if not isinstance(query, Mapping) or not set(query) <= {
            "limit",
            "source",
            "correlation_id",
        }:
            return self.json_message(
                "The operation journal query is invalid.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        try:
            limit_value = query.get("limit", "100")
            limit = int(limit_value)
            if str(limit) != str(limit_value) or not 1 <= limit <= MAX_OPERATION_JOURNAL_RECORDS:
                raise ValueError
            source = query.get("source")
            correlation_id = query.get("correlation_id")
            if source is not None and not isinstance(source, str):
                raise ValueError
            if correlation_id is not None and not isinstance(correlation_id, str):
                raise ValueError
            service = self._service()
            if service is None:
                return self.json_message(
                    "The HausmanHub operation journal is unavailable.",
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    headers=NO_STORE_HEADERS,
                )
            payload = service.snapshot(
                limit=limit,
                source=source,
                correlation_id=correlation_id,
            )
        except (TypeError, ValueError):
            return self.json_message(
                "The operation journal query is invalid.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        return self.json(payload, headers=NO_STORE_HEADERS)

    def _service(self) -> OperationJournalService | None:
        candidate = self._hass.data.get(DOMAIN, {}).get(DATA_OPERATION_JOURNAL)
        return candidate if isinstance(candidate, OperationJournalService) else None


def register_operation_journal_api(hass: HomeAssistant) -> None:
    data = hass.data.setdefault(DOMAIN, {})
    if DATA_OPERATION_JOURNAL_VIEW in data:
        return
    view = OperationJournalView(hass)
    hass.http.register_view(view)
    data[DATA_OPERATION_JOURNAL_VIEW] = view


def clear_operation_journal(hass: HomeAssistant) -> None:
    data = hass.data.get(DOMAIN)
    if isinstance(data, dict):
        data.pop(DATA_OPERATION_JOURNAL, None)
