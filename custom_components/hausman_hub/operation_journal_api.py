"""Local admin boundary for the durable cross-domain operation journal."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from homeassistant.components.http import HomeAssistantView

from .application.operation_journal import (
    MAX_OPERATION_JOURNAL_RECORDS,
    OperationJournalService,
)
from .application.operation_journal_admin import JournalArchiveError, OperationJournalArchiveService
from .climate_api import (
    DOMAIN,
    NO_STORE_HEADERS,
    _forbidden,
    _is_local_admin_request,
    _not_found,
)
from .error_taxonomy import api_error_payload, api_error_status

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


ADMIN_OPERATION_JOURNAL_PATH = "/api/hausman_hub/v1/admin/operations"
DATA_OPERATION_JOURNAL = "operation_journal"
DATA_OPERATION_JOURNAL_VIEW = "operation_journal_view"
DATA_OPERATION_JOURNAL_ARCHIVE = "operation_journal_archive"
_MAX_ADMIN_BODY_BYTES = 4096
_ARCHIVE_CONTRACT = {"name": "hausman-hub-operation-journal-archive-request", "version": 1}
_RESET_CONTRACT = {"name": "hausman-hub-operation-journal-reset-request", "version": 1}
_CORRELATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_ARCHIVE_TOKEN = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
_REVISION = re.compile(r"^[a-f0-9]{16,64}$")
_MAX_SAFE_INTEGER = 9_007_199_254_740_991
_ADMIN_HEADERS = {**NO_STORE_HEADERS, "Pragma": "no-cache"}


def _typed_error(
    view: HomeAssistantView,
    code: str,
    status: HTTPStatus | None = None,
    *,
    details: Mapping[str, object] | None = None,
    retry_after_seconds: int | None = None,
) -> Any:
    """Use the global safe envelope even for this small admin-only surface."""
    headers = dict(_ADMIN_HEADERS)
    if retry_after_seconds is not None:
        headers["Retry-After"] = str(max(1, retry_after_seconds))
    return view.json(
        api_error_payload(code, details=details),
        status_code=status or HTTPStatus(api_error_status(code)),
        headers=headers,
    )


def _archive_error(view: HomeAssistantView, error: JournalArchiveError) -> Any:
    if error.detail_code == "reset_rate_limited":
        retry_after = error.retry_after_seconds or 60
        return _typed_error(
            view,
            "rate_limited",
            details={"retryAfterSeconds": retry_after},
            retry_after_seconds=retry_after,
        )
    if error.detail_code == "archive_storage_unavailable":
        return _typed_error(view, "unavailable")
    if error.detail_code == "invalid_request":
        return _typed_error(view, "invalid_request")
    if error.detail_code == "reset_archive_token_invalid":
        details = {
            "detailCode": error.detail_code,
            "newArchiveRequired": True,
            "consumedByThisAttempt": False,
            "physicalCommandsSent": False,
        }
    elif error.detail_code == "reset_precondition_conflict":
        details = {
            "detailCode": "reset_precondition_conflict",
            **error.details,
            "newArchiveRequired": True,
            "archivePreserved": True,
            "consumedByThisAttempt": False,
            "physicalCommandsSent": False,
        }
    else:
        return _typed_error(view, "unavailable")
    payload = api_error_payload("conflict", details=details)
    payload.update({"status": 409, "category": "conflict"})
    return view.json(payload, status_code=HTTPStatus.CONFLICT, headers=_ADMIN_HEADERS)


async def _bounded_json(request: Any) -> Mapping[str, object]:
    """Read a small JSON request after authorization, never an unbounded body."""
    length = getattr(request, "content_length", None)
    if type(length) is not int or length < 0:
        raise ValueError
    if length > _MAX_ADMIN_BODY_BYTES:
        raise OverflowError
    content = getattr(request, "content", None)
    read = getattr(content, "read", None)
    if not callable(read):
        raise ValueError
    raw = await read(_MAX_ADMIN_BODY_BYTES + 1)
    if not isinstance(raw, bytes):
        raise ValueError
    if len(raw) > _MAX_ADMIN_BODY_BYTES:
        raise OverflowError
    if len(raw) != length:
        raise ValueError
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError
    return value


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
            "before_sequence",
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
            before_sequence_value = query.get("before_sequence")
            before_sequence = (
                int(before_sequence_value)
                if before_sequence_value is not None
                else None
            )
            if before_sequence is not None and (
                str(before_sequence) != str(before_sequence_value)
                or before_sequence < 1
            ):
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
                before_sequence=before_sequence,
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


class OperationJournalArchiveView(HomeAssistantView):
    requires_auth = True
    cors_allowed = False
    url = "/api/hausman_hub/v1/admin/operations/archive"
    name = "api:hausman_hub:operation_journal_archive"

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def post(self, request: Any) -> Any:
        if not _is_local_admin_request(request):
            return _typed_error(self, "forbidden")
        try:
            body = await _bounded_json(request)
            correlation_id = body.get("correlationId")
            if (
                set(body) - {"contract", "correlationId"}
                or body.get("contract") != _ARCHIVE_CONTRACT
                or (
                    correlation_id is not None
                    and (
                        not isinstance(correlation_id, str)
                        or _CORRELATION_ID.fullmatch(correlation_id) is None
                    )
                )
            ):
                raise ValueError
            service = self._service()
            if service is None:
                return _typed_error(self, "unavailable")
            user = request["hass_user"]
            receipt = await service.async_archive(str(getattr(user, "id", "admin")), body.get("correlationId"))
        except OverflowError:
            return _typed_error(self, "invalid_request", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        except JournalArchiveError as err:
            return _archive_error(self, err)
        except (TypeError, ValueError, KeyError, UnicodeDecodeError, json.JSONDecodeError):
            return _typed_error(self, "invalid_request")
        return self.json(receipt, status_code=HTTPStatus.CREATED, headers=_ADMIN_HEADERS)

    def _service(self) -> OperationJournalArchiveService | None:
        candidate = self._hass.data.get(DOMAIN, {}).get(DATA_OPERATION_JOURNAL_ARCHIVE)
        return candidate if isinstance(candidate, OperationJournalArchiveService) else None


class OperationJournalResetView(HomeAssistantView):
    requires_auth = True
    cors_allowed = False
    url = "/api/hausman_hub/v1/admin/operations/reset"
    name = "api:hausman_hub:operation_journal_reset"

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def post(self, request: Any) -> Any:
        if not _is_local_admin_request(request):
            return _typed_error(self, "forbidden")
        try:
            body = await _bounded_json(request)
            required = {"contract", "archiveToken", "expectedGeneration", "expectedSequence", "expectedRevision"}
            if set(body) - (required | {"correlationId"}) or not required.issubset(body) or body.get("contract") != _RESET_CONTRACT:
                raise ValueError
            correlation_id = body.get("correlationId")
            if (
                not isinstance(body["archiveToken"], str)
                or _ARCHIVE_TOKEN.fullmatch(body["archiveToken"]) is None
                or type(body["expectedGeneration"]) is not int
                or not 1 <= body["expectedGeneration"] <= _MAX_SAFE_INTEGER
                or type(body["expectedSequence"]) is not int
                or not 0 <= body["expectedSequence"] <= _MAX_SAFE_INTEGER
                or not isinstance(body["expectedRevision"], str)
                or _REVISION.fullmatch(body["expectedRevision"]) is None
                or (
                    correlation_id is not None
                    and (
                        not isinstance(correlation_id, str)
                        or _CORRELATION_ID.fullmatch(correlation_id) is None
                    )
                )
            ):
                raise ValueError
            service = self._hass.data.get(DOMAIN, {}).get(DATA_OPERATION_JOURNAL_ARCHIVE)
            if not isinstance(service, OperationJournalArchiveService):
                return _typed_error(self, "unavailable")
            user = request["hass_user"]
            result = await service.async_reset(str(getattr(user, "id", "admin")), body["archiveToken"],
                body["expectedGeneration"], body["expectedSequence"], body["expectedRevision"], body.get("correlationId"))
        except JournalArchiveError as err:
            return _archive_error(self, err)
        except OverflowError:
            return _typed_error(self, "invalid_request", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        except (TypeError, ValueError, KeyError, AttributeError, UnicodeDecodeError, json.JSONDecodeError):
            return _typed_error(self, "invalid_request")
        return self.json(result, headers=_ADMIN_HEADERS)


def register_operation_journal_api(hass: HomeAssistant) -> None:
    data = hass.data.setdefault(DOMAIN, {})
    if DATA_OPERATION_JOURNAL_VIEW in data:
        return
    view = OperationJournalView(hass)
    hass.http.register_view(view)
    data[DATA_OPERATION_JOURNAL_VIEW] = view
    archive_view = OperationJournalArchiveView(hass)
    reset_view = OperationJournalResetView(hass)
    hass.http.register_view(archive_view)
    hass.http.register_view(reset_view)


def clear_operation_journal(hass: HomeAssistant) -> None:
    data = hass.data.get(DOMAIN)
    if isinstance(data, dict):
        data.pop(DATA_OPERATION_JOURNAL, None)
