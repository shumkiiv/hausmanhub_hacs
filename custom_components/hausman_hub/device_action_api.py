"""Authenticated local device-action boundary for the HausmanHub tablet."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
import inspect
from http import HTTPStatus
import logging
import time
from typing import TYPE_CHECKING, Any
import uuid

from homeassistant.components.http import HomeAssistantView

from .application.api_capabilities import (
    DEVICE_ACTIONS_BATCH_PATH,
    DEVICE_ACTIONS_PATH,
    DEVICE_FEATURES_PATH,
)
from .application.device_features import device_feature_matrix_snapshot
from .application.device_action_idempotency import DangerousActionIdempotency
from .application.device_action_protocol import (
    DANGEROUS_ACTION_IDS,
    FULL_BATCH_REQUEST_MEDIA_TYPE,
    FULL_BATCH_RESPONSE_MEDIA_TYPE,
    FULL_SINGLE_REQUEST_MEDIA_TYPE,
    FULL_SINGLE_RESPONSE_MEDIA_TYPE,
    LEGACY_REQUEST_MEDIA_TYPE,
    StrictJsonError,
    canonical_request_fingerprint,
    negotiated_response_media_type,
    request_is_full,
    strict_request_json,
    validate_batch_request,
    validate_single_request,
)
from .application.device_action_receipts import (
    evidence_snapshot,
    full_action_receipt,
)
from .application.scenario_light_priority import _state_is_fresh
from .application.safe_device_command_lifecycle import (
    CommandDeadline,
    SafeDeviceCommandHandle,
    SafeDeviceCommandLifecycle,
    SafeDeviceCommandOperation,
    is_safe_device_descriptor,
)
from .application.scenario_executor import (
    _range_error_for_action,
    _state_is_restored_or_cached,
    _state_revision,
)
from .application.scenario_service import ScenarioService
from .climate_api import (
    DOMAIN,
    NO_STORE_HEADERS,
    _forbidden,
    _is_exact_request,
    _is_local_admin_request,
    _is_local_tablet_request,
    _not_found,
)
from .correlation import CorrelationIdError, resolve_correlation_id
from .error_taxonomy import api_error_payload, api_error_status
from .realtime_api import publish_command_receipt

_LOGGER = logging.getLogger(__name__)

_DIRECT_IDEMPOTENT_ACTIONS = frozenset(
    {
        "turn_on", "turn_off", "set_temperature", "set_hvac_mode",
        "set_fan_mode", "set_brightness", "set_brightness_percent",
        "set_color_temperature", "set_rgb_color", "set_humidity",
        "set_operation_mode", "set_value",
    }
)
_DIRECT_IDEMPOTENT_BLOCKED_TYPES = frozenset(
    {"cover", "lock", "valve", "water", "breaker", "electrical_breaker", "button", "intercom"}
)


class _SafeCommandDeadlineExpired(RuntimeError):
    """A pre-dispatch read exceeded the bounded safe-command HTTP budget."""


_MAX_TRACKED_SAFE_ADMISSION_TASKS = 32
_TRACKED_SAFE_ADMISSION_TASKS: set[asyncio.Task[Any]] = set()
SafeTimeoutSettled = Callable[[asyncio.Task[Any]], Awaitable[None]]


def _track_safe_admission_task(task: asyncio.Task[Any]) -> None:
    _TRACKED_SAFE_ADMISSION_TASKS.add(task)

    def consume_result(completed: asyncio.Task[Any]) -> None:
        _TRACKED_SAFE_ADMISSION_TASKS.discard(completed)
        if completed.cancelled():
            return
        try:
            completed.exception()
        except Exception:  # noqa: BLE001
            return

    task.add_done_callback(consume_result)


async def _await_before_safe_deadline(
    awaitable: Any,
    deadline: CommandDeadline,
    *,
    on_timeout_settled: SafeTimeoutSettled | None = None,
) -> Any:
    """Bound admission without waiting for a cancellation-resistant coroutine."""

    if len(_TRACKED_SAFE_ADMISSION_TASKS) >= _MAX_TRACKED_SAFE_ADMISSION_TASKS:
        if inspect.iscoroutine(awaitable):
            awaitable.close()
        raise _SafeCommandDeadlineExpired("safe command admission capacity is full")
    task = asyncio.create_task(awaitable)
    _track_safe_admission_task(task)
    done, _pending = await asyncio.wait({task}, timeout=deadline.remaining())
    if task in done:
        return task.result()
    task.cancel()
    if on_timeout_settled is not None:
        async def settle_and_finalize() -> None:
            try:
                await task
            except BaseException:  # cancellation is part of settlement
                pass
            try:
                await on_timeout_settled(task)
            except Exception:  # noqa: BLE001
                _LOGGER.warning(
                    "HausmanHub timed-out safe admission cleanup failed",
                    exc_info=True,
                )

        finalizer = asyncio.create_task(
            settle_and_finalize(),
            name="hausman-safe-admission-cleanup",
        )
        _track_safe_admission_task(finalizer)
    raise _SafeCommandDeadlineExpired("safe command admission deadline expired")


def _safe_action_candidate(
    *,
    context: object,
    action_id: str,
    dangerous: bool,
    external_cover: bool,
    intercom: bool,
    reassert_key: object,
    dry_run: bool,
) -> bool:
    if (
        dry_run
        or dangerous
        or external_cover
        or intercom
        or reassert_key is not None
        or not isinstance(context, tuple)
        or len(context) < 4
    ):
        return False
    return is_safe_device_descriptor(
        domain=context[1], service=context[3], action_id=action_id
    )


def _catalog_safe_descriptor(
    service: ScenarioService, target_id: str, action_id: str
) -> bool:
    """Use the loaded catalog to keep unrelated actions on their existing path."""

    try:
        device = service.current_catalog().device(target_id)
        action = device.action(action_id) if device is not None else None
    except Exception:  # noqa: BLE001
        return False
    return bool(
        action is not None
        and is_safe_device_descriptor(
            domain=action.domain,
            service=action.service,
            action_id=action_id,
        )
    )


def _safe_action_admitted(
    *,
    service: ScenarioService,
    hass: HomeAssistant,
    target_id: str,
    action_id: str,
    value: object,
    context: tuple[str, str, tuple[str, ...], str],
) -> bool:
    """Require current identity, fresh availability, value range, and mode options."""

    entity_id, domain, _actions, expected_service = context
    if not entity_id.startswith(f"{domain}."):
        return False
    device = service.current_catalog().device(target_id)
    allowed = device.action(action_id) if device is not None else None
    if (
        device is None
        or allowed is None
        or device.entity_id != entity_id
        or allowed.domain != domain
        or allowed.service != expected_service
        or not is_safe_device_descriptor(
            domain=allowed.domain,
            service=allowed.service,
            action_id=action_id,
        )
    ):
        return False
    state = hass.states.get(entity_id)
    if (
        state is None
        or str(getattr(state, "state", "unknown")) in {"unknown", "unavailable"}
        or not _state_is_fresh(state)
        or _state_is_restored_or_cached(state)
        or _range_error_for_action(device, state, action_id, value) is not None
    ):
        return False
    if action_id in {"set_temperature", "set_humidity"}:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if action_id in {"turn_on", "turn_off"}:
        return value is None
    attributes = getattr(state, "attributes", {})
    if not isinstance(attributes, Mapping) or not isinstance(value, str) or not value:
        return False
    options_key = {
        "set_hvac_mode": "hvac_modes",
        "set_fan_mode": "fan_modes",
        "set_operation_mode": "available_modes",
    }.get(action_id)
    options = attributes.get(options_key) if options_key is not None else None
    return isinstance(options, (list, tuple)) and value in options


def _state_observed_at_ms(state: object | None) -> int | None:
    if state is None:
        return None
    observed = (
        getattr(state, "last_reported", None)
        or getattr(state, "last_updated", None)
        or getattr(state, "last_changed", None)
    )
    if isinstance(observed, datetime):
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        return max(0, int(observed.timestamp() * 1000))
    return time.time_ns() // 1_000_000


def _climate_mode_precondition(
    climate_runtime: object, entity_id: str | None
) -> Mapping[str, object] | None:
    if entity_id is None:
        return None
    snapshot = getattr(climate_runtime, "device_mode_snapshot_for_entity", None)
    if not callable(snapshot):
        return None
    try:
        value = snapshot(entity_id)
    except Exception:  # noqa: BLE001
        _LOGGER.warning("HausmanHub climate mode snapshot failed", exc_info=True)
        return None
    return dict(value) if isinstance(value, Mapping) else None


async def _async_write_climate_mode(
    writer: Any,
    entity_id: str,
    mode: str,
    precondition: Mapping[str, object] | None,
) -> object:
    options: dict[str, object] = {}
    if precondition is not None:
        if _supports_keyword(writer, "expected_revision"):
            options["expected_revision"] = precondition.get("revision")
        if _supports_keyword(writer, "expected_mode"):
            options["expected_mode"] = precondition.get("mode")
    return await writer(entity_id, mode, **options)


def _pending_safe_result(
    *,
    handle: SafeDeviceCommandHandle,
    hass: HomeAssistant,
    correlation_id: str,
) -> dict[str, object]:
    """Build an accepted result from one real state read after actual dispatch."""

    operation = handle.operation
    state = hass.states.get(operation.entity_id)
    return {
        "correlationId": correlation_id,
        "requestId": operation.request_id,
        "targetId": operation.target_id,
        "actionId": operation.action_id,
        "accepted": True,
        "confirmed": False,
        "status": "accepted",
        "statusName": "Проверяется",
        "observedState": (
            str(getattr(state, "state", "unknown")) if state is not None else None
        ),
        "appliedAt": handle.dispatch_at_ms or handle.created_at_ms,
        "message": "Команда принята, состояние ещё не подтверждено.",
        "confirmationWindowMs": 30000,
        "readBack": {
            "attempted": True,
            "matched": False,
            "observedAt": _state_observed_at_ms(state),
            "observedState": (
                str(getattr(state, "state", "unknown"))
                if state is not None
                else None
            ),
            "attempts": 1,
        },
        "reason": "state_not_confirmed",
        "error": None,
    }


def _blocked_unstarted_result(
    *, request_id: str, target_id: str, action_id: str, correlation_id: str
) -> dict[str, object]:
    return {
        "correlationId": correlation_id,
        "requestId": request_id,
        "targetId": target_id,
        "actionId": action_id,
        "accepted": False,
        "confirmed": False,
        "status": "failed",
        "statusName": "Не выполнено",
        "message": "Команда не отправлена: исчерпан общий бюджет пакета.",
        "confirmationWindowMs": 30000,
        "readBack": {
            "attempted": False,
            "matched": False,
            "observedAt": None,
            "observedState": None,
            "attempts": 0,
        },
        "reason": "batch_response_budget_exhausted",
        "error": "batch_response_budget_exhausted",
    }


def _blocked_safe_admission_result(
    *, request_id: str, target_id: str, action_id: str, correlation_id: str
) -> dict[str, object]:
    return {
        "correlationId": correlation_id,
        "requestId": request_id,
        "targetId": target_id,
        "actionId": action_id,
        "accepted": False,
        "confirmed": False,
        "status": "failed",
        "statusName": "Не выполнено",
        "message": "Команда не отправлена: проверка устройства или значения не пройдена.",
        "confirmationWindowMs": 30000,
        "readBack": {
            "attempted": False,
            "matched": False,
            "observedAt": None,
            "observedState": None,
            "attempts": 0,
        },
        "reason": "safe_device_admission_failed",
        "error": "safe_device_admission_failed",
    }


def _direct_idempotent_allowed(
    *, action_id: str, target_type: str, entity_id: str | None,
    state: object | None, dangerous: bool, external_cover: bool,
    reassert_key: object,
) -> bool:
    """Allow only fresh, non-dangerous direct actions to use preflight."""

    if action_id not in _DIRECT_IDEMPOTENT_ACTIONS or dangerous or external_cover:
        return False
    if action_id == "toggle" or any(
        marker in target_type.lower() for marker in _DIRECT_IDEMPOTENT_BLOCKED_TYPES
    ):
        return False
    if reassert_key is not None or state is None or not _state_is_fresh(state):
        return False
    # The executor additionally excludes configured power dependencies and
    # stale light ownership. Keep direct API policy conservative here too.
    if target_type == "light" and entity_id is None:
        return False
    return True

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class _DeviceActionLifecycle:
    """Own one full-request reservation from reserve through durable completion."""

    def __init__(
        self,
        *,
        view: HomeAssistantView,
        service: ScenarioService,
        idempotency: DangerousActionIdempotency,
        key: str,
        request_id: str,
        response_media_type: str,
    ) -> None:
        self._view = view
        self._service = service
        self._idempotency = idempotency
        self._key = key
        self._request_id = request_id
        self._response_media_type = response_media_type
        self._reservation_owned = False
        self._terminal_persisted = False
        self._dispatch_crossed = False
        self._intercom_cleanup: tuple[str, str | None, str] | None = None
        self._intercom_cleanup_attempted = False
        self._completion_lock = asyncio.Lock()

    @property
    def dispatch_crossed(self) -> bool:
        return self._dispatch_crossed

    @property
    def terminal_persisted(self) -> bool:
        return self._terminal_persisted

    def mark_reservation_owned(self) -> None:
        self._reservation_owned = True

    def mark_dispatch_crossed(self) -> None:
        self._dispatch_crossed = True

    def note_intercom_prepare_attempt(
        self,
        target_id: str,
        *,
        expected_entity_id: str | None,
        expected_request_id: str,
    ) -> None:
        self._intercom_cleanup = (
            target_id,
            expected_entity_id,
            expected_request_id,
        )

    async def async_cancel_unarmed_intercom(self) -> bool:
        cleanup = self._intercom_cleanup
        if cleanup is None or self._intercom_cleanup_attempted:
            return True
        self._intercom_cleanup_attempted = True
        target_id, expected_entity_id, expected_request_id = cleanup
        try:
            cancelled = await self._service.async_cancel_intercom_release(
                target_id,
                expected_entity_id=expected_entity_id,
                expected_request_id=expected_request_id,
                unarmed_only=True,
            )
        except Exception:  # noqa: BLE001
            _LOGGER.warning(
                "HausmanHub unarmed intercom release cleanup failed",
                exc_info=True,
            )
            return False
        if cancelled is not True:
            _LOGGER.debug(
                "HausmanHub intercom release cleanup found no matching unarmed obligation"
            )
        return cancelled is True

    async def async_failure(self) -> Any:
        """Attempt independent cleanup and return a negotiated safe failure."""

        await self.async_cancel_unarmed_intercom()
        if (
            not self._dispatch_crossed
            and self._reservation_owned
            and not self._terminal_persisted
        ):
            try:
                await self._idempotency.async_abandon_pre_dispatch(self._key)
            except Exception:  # noqa: BLE001
                _LOGGER.warning(
                    "HausmanHub pre-dispatch reservation cleanup failed",
                    exc_info=True,
                )
        failure = _execution_failure_response(
            request_id=self._request_id,
            dispatch_crossed=self._dispatch_crossed,
        )
        return _negotiated_json(
            self._view,
            failure["payload"],
            status_code=failure["status"],
            media_type=self._response_media_type,
        )

    async def async_complete(
        self,
        response: Mapping[str, object],
        *,
        item_journal: list[dict[str, object]] | None = None,
    ) -> Any | None:
        async with self._completion_lock:
            if self._terminal_persisted:
                return None
            try:
                await self._idempotency.async_complete(
                    self._key,
                    response,
                    item_journal=item_journal,
                )
            except Exception:  # noqa: BLE001
                _LOGGER.warning(
                    "HausmanHub device action completion persistence failed",
                    exc_info=True,
                )
                return await self.async_failure()
            self._terminal_persisted = True
            return None


async def _async_action_failure(
    view: HomeAssistantView,
    lifecycle: _DeviceActionLifecycle | None,
    *,
    request_id: str,
    response_media_type: str,
    dispatch_crossed: bool = False,
) -> Any:
    """Return one negotiated failure with lifecycle cleanup when it exists."""

    if lifecycle is not None:
        return await lifecycle.async_failure()
    failure = _execution_failure_response(
        request_id=request_id,
        dispatch_crossed=dispatch_crossed,
    )
    return _negotiated_json(
        view,
        failure["payload"],
        status_code=failure["status"],
        media_type=response_media_type,
    )


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
        deadline = CommandDeadline.start()
        if not _is_exact_request(request, DEVICE_ACTIONS_PATH):
            return _not_found(self)
        if not (
            _is_local_tablet_request(request) or _is_local_admin_request(request)
        ):
            return _forbidden(self)
        response_media_type = negotiated_response_media_type(request, batch=False)
        if response_media_type is None:
            return _not_acceptable(self)
        full_response = response_media_type == FULL_SINGLE_RESPONSE_MEDIA_TYPE
        service = self._hass.data.get(DOMAIN, {}).get("scenario_service")
        if not isinstance(service, ScenarioService):
            return self.json_message(
                "The HausmanHub device action API is unavailable.",
                HTTPStatus.SERVICE_UNAVAILABLE,
                headers=NO_STORE_HEADERS,
            )
        try:
            payload = await strict_request_json(
                request,
                allowed_media_types=frozenset(
                    {LEGACY_REQUEST_MEDIA_TYPE, FULL_SINGLE_REQUEST_MEDIA_TYPE}
                ),
            )
            full_request = request_is_full(request, batch=False)
            payload = validate_single_request(payload, full=full_request)
        except StrictJsonError:
            return self.json_message(
                "The device action body is invalid.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        target_id = str(payload["targetId"])
        action_id = str(payload["actionId"])
        dry_run = payload.get("dryRun", False)
        potentially_safe_action = bool(
            not dry_run
            and _catalog_safe_descriptor(service, target_id, action_id)
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
        try:
            context = (
                await _await_before_safe_deadline(
                    service.async_resolve_device_action_context(
                        target_id, action_id
                    ),
                    deadline,
                )
                if potentially_safe_action
                else await service.async_resolve_device_action_context(
                    target_id, action_id
                )
            )
        except _SafeCommandDeadlineExpired:
            return await _async_action_failure(
                self,
                None,
                request_id=str(
                    payload.get("requestId")
                    or f"safe-admission.{uuid.uuid4().hex}"
                ),
                response_media_type=response_media_type,
            )
        entity_id = context[0] if context is not None else None
        target_type = context[1] if context is not None else "sensor"
        allowed_actions = context[2] if context is not None else ()
        allowed_service = (
            context[3] if context is not None and len(context) > 3 else None
        )
        decision_at = time.time_ns() // 1_000_000
        state = self._hass.states.get(entity_id) if isinstance(entity_id, str) else None
        reassert_key = payload.get("reassertKey")
        pre_command_evidence = (
            evidence_snapshot(
                target_id=target_id,
                state=state,
                allowed_actions=allowed_actions,
            )
            if (full_response or reassert_key is not None) and target_type == "light"
            else {}
        )
        try:
            intercom_action = (
                await _await_before_safe_deadline(
                    service.async_is_intercom_action(target_id, action_id),
                    deadline,
                )
                if potentially_safe_action
                else await service.async_is_intercom_action(target_id, action_id)
            )
        except _SafeCommandDeadlineExpired:
            return await _async_action_failure(
                self,
                None,
                request_id=str(
                    payload.get("requestId")
                    or f"safe-admission.{uuid.uuid4().hex}"
                ),
                response_media_type=response_media_type,
            )
        contextual_dangerous_action = (
            intercom_action
            or service.is_contextually_dangerous_action(target_id, action_id)
        )
        external_cover_action = service.is_external_cover_action(
            target_id, action_id
        )
        dangerous_action = (
            action_id in DANGEROUS_ACTION_IDS or contextual_dangerous_action
        )
        safe_action_candidate = _safe_action_candidate(
            context=context,
            action_id=action_id,
            dangerous=dangerous_action,
            external_cover=external_cover_action,
            intercom=intercom_action,
            reassert_key=reassert_key,
            dry_run=bool(dry_run),
        )
        safe_action_admitted = bool(
            safe_action_candidate
            and context is not None
            and _safe_action_admitted(
                service=service,
                hass=self._hass,
                target_id=target_id,
                action_id=action_id,
                value=payload.get("value"),
                context=context,
            )
        )
        direct_idempotent_allowed = _direct_idempotent_allowed(
            action_id=action_id,
            target_type=target_type,
            entity_id=entity_id,
            state=state,
            dangerous=dangerous_action,
            external_cover=external_cover_action,
            reassert_key=reassert_key,
        )
        if dangerous_action and not dry_run:
            if not full_request:
                return _legacy_dangerous_forbidden(self)
            if not _confirmed_action(payload):
                return _dangerous_confirmation_required(self)
        if (
            external_cover_action
            and not dry_run
            and (
                state is None
                or str(getattr(state, "state", "unknown"))
                in {"unknown", "unavailable"}
                or not _state_is_fresh(state)
            )
        ):
            return _stale_critical_evidence(self)
        idempotency_key: str | None = None
        idempotency = self._hass.data.get(DOMAIN, {}).get(
            "device_action_idempotency"
        )
        fingerprint = canonical_request_fingerprint(payload)
        if reassert_key is not None:
            if context is None or not isinstance(
                idempotency, DangerousActionIdempotency
            ):
                return _coordinator_unavailable(self)
            try:
                existing_reassert = await idempotency.async_lookup(
                    key=_reassert_coordination_key(payload), fingerprint=fingerprint
                )
            except Exception as error:  # noqa: BLE001
                if (
                    isinstance(error, RuntimeError)
                    and str(error) == "dangerous action idempotency store is full"
                ):
                    return _idempotency_journal_full(
                        self, media_type=response_media_type
                    )
                failure = _execution_failure_response(
                    request_id=str(payload.get("requestId")),
                    dispatch_crossed=False,
                )
                return _negotiated_json(
                    self,
                    failure["payload"],
                    status_code=failure["status"],
                    media_type=response_media_type,
                )
            if existing_reassert.outcome == "conflict":
                return _idempotency_conflict(
                    self,
                    expected_hash=existing_reassert.existing_fingerprint,
                    request_hash=fingerprint,
                    media_type=response_media_type,
                )
            if existing_reassert.outcome == "in_progress":
                return _idempotency_in_progress(
                    self,
                    existing_reassert.state,
                    media_type=response_media_type,
                )
            if (
                existing_reassert.outcome == "replay"
                and existing_reassert.receipt is not None
            ):
                return _negotiated_json(
                    self,
                    existing_reassert.receipt,
                    status_code=_replay_status(existing_reassert.receipt),
                    media_type=(
                        existing_reassert.response_media_type or response_media_type
                    ),
                )
            state = self._hass.states.get(entity_id)
            pre_command_evidence = evidence_snapshot(
                target_id=target_id,
                state=state,
                allowed_actions=allowed_actions,
            )
            if not _valid_reassert_evidence(
                payload, pre_command_evidence, target_type=target_type
            ):
                return _stale_reassert_evidence(self)
        coordination_key: str | None = None
        dispatch_request_id: str | None = None
        lifecycle: _DeviceActionLifecycle | None = None
        if full_request and not dry_run:
            if context is None or not isinstance(
                idempotency, DangerousActionIdempotency
            ):
                return self.json_message(
                    "The dangerous device action coordinator is unavailable.",
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    headers=NO_STORE_HEADERS,
                )
            idempotency_key = (
                str(payload["idempotencyKey"])
                if dangerous_action
                else _reassert_coordination_key(payload)
                if reassert_key is not None
                else f"request:{payload['requestId']}"
            )
            coordination_key = idempotency_key
            dispatch_id = uuid.uuid4().hex
            dispatch_request_id = f"dispatch.{dispatch_id}"
            lifecycle = _DeviceActionLifecycle(
                view=self,
                service=service,
                idempotency=idempotency,
                key=idempotency_key,
                request_id=dispatch_request_id,
                response_media_type=response_media_type,
            )

            async def abandon_late_reservation(
                reservation_task: asyncio.Task[Any],
            ) -> None:
                if reservation_task.cancelled():
                    return
                try:
                    late_reservation = reservation_task.result()
                except Exception:  # noqa: BLE001
                    return
                if getattr(late_reservation, "outcome", None) != "reserved":
                    return
                await idempotency.async_abandon_pre_dispatch(idempotency_key)

            async def abandon_late_transition(_task: asyncio.Task[Any]) -> None:
                await idempotency.async_abandon_pre_dispatch(idempotency_key)

            try:
                reservation_awaitable = idempotency.async_reserve(
                    key=idempotency_key,
                    fingerprint=fingerprint,
                    dispatch_id=dispatch_id,
                    bindings=[
                        {
                            "actionIndex": 0,
                            "targetId": target_id,
                            "targetType": target_type,
                            "actionId": action_id,
                            "correlationId": correlation_id,
                            "requestId": dispatch_request_id,
                        }
                    ],
                    response_media_type=response_media_type,
                )
                reservation = (
                    await _await_before_safe_deadline(
                        reservation_awaitable,
                        deadline,
                        on_timeout_settled=abandon_late_reservation,
                    )
                    if safe_action_candidate
                    else await reservation_awaitable
                )
            except _SafeCommandDeadlineExpired:
                return await _async_action_failure(
                    self,
                    None,
                    request_id=str(payload.get("requestId")),
                    response_media_type=response_media_type,
                )
            except Exception as error:  # noqa: BLE001
                if (
                    isinstance(error, RuntimeError)
                    and str(error) == "dangerous action idempotency store is full"
                ):
                    return _idempotency_journal_full(
                        self, media_type=response_media_type
                    )
                failure = _execution_failure_response(
                    request_id=str(payload.get("requestId")),
                    dispatch_crossed=False,
                )
                return _negotiated_json(
                    self,
                    failure["payload"],
                    status_code=failure["status"],
                    media_type=response_media_type,
                )
            if reservation.outcome == "conflict":
                return _idempotency_conflict(
                    self,
                    expected_hash=reservation.existing_fingerprint,
                    request_hash=fingerprint,
                    media_type=response_media_type,
                )
            if reservation.outcome == "in_progress":
                return _idempotency_in_progress(
                    self,
                    reservation.state,
                    media_type=response_media_type,
                )
            if reservation.outcome == "replay" and reservation.receipt is not None:
                return _negotiated_json(
                    self,
                    reservation.receipt,
                    status_code=_replay_status(reservation.receipt),
                    media_type=(
                        reservation.response_media_type or response_media_type
                    ),
                )
            lifecycle.mark_reservation_owned()
            try:
                pending_awaitable = idempotency.async_mark_pending(idempotency_key)
                if safe_action_candidate:
                    await _await_before_safe_deadline(
                        pending_awaitable,
                        deadline,
                        on_timeout_settled=abandon_late_transition,
                    )
                else:
                    await pending_awaitable
            except _SafeCommandDeadlineExpired:
                return await _async_action_failure(
                    self,
                    None,
                    request_id=str(payload.get("requestId")),
                    response_media_type=response_media_type,
                )
            except Exception:  # noqa: BLE001
                _LOGGER.warning(
                    "HausmanHub device action pending persistence failed",
                    exc_info=True,
                )
                return await lifecycle.async_failure()

        release_seconds = None
        if intercom_action and not dry_run:
            assert lifecycle is not None
            release_request_id = f"{dispatch_request_id}.release"
            lifecycle.note_intercom_prepare_attempt(
                target_id,
                expected_entity_id=entity_id,
                expected_request_id=release_request_id,
            )
            try:
                release_seconds = await service.async_prepare_intercom_release(
                    target_id,
                    action_id,
                    correlation_id=correlation_id,
                    request_id=release_request_id,
                    expected_entity_id=entity_id,
                )
            except Exception:  # noqa: BLE001
                _LOGGER.warning(
                    "HausmanHub intercom release preparation failed",
                    exc_info=True,
                )
                return await lifecycle.async_failure()
            if release_seconds is None:
                return await lifecycle.async_failure()
        if coordination_key is not None and isinstance(
            idempotency, DangerousActionIdempotency
        ):
            try:
                dispatching_awaitable = idempotency.async_mark_dispatching(
                    coordination_key
                )
                if safe_action_admitted:
                    await _await_before_safe_deadline(
                        dispatching_awaitable,
                        deadline,
                        on_timeout_settled=abandon_late_transition,
                    )
                else:
                    await dispatching_awaitable
            except _SafeCommandDeadlineExpired:
                return await _async_action_failure(
                    self,
                    None,
                    request_id=str(payload.get("requestId")),
                    response_media_type=response_media_type,
                )
            except Exception:  # noqa: BLE001
                _LOGGER.warning(
                    "HausmanHub device action dispatch fence persistence failed",
                    exc_info=True,
                )
                assert lifecycle is not None
                return await lifecycle.async_failure()

        climate_runtime = self._hass.data.get(DOMAIN, {}).get("climate_runtime")
        safe_coordinator = self._hass.data.get(DOMAIN, {}).get(
            "safe_device_command_lifecycle"
        )
        if safe_action_admitted and not isinstance(
            safe_coordinator, SafeDeviceCommandLifecycle
        ):
            return await _async_action_failure(
                self,
                lifecycle,
                request_id=str(
                    payload.get("requestId") or f"safe-command.{uuid.uuid4().hex}"
                ),
                response_media_type=response_media_type,
            )
        dispatch_state = {"crossed": False}
        safe_handle: SafeDeviceCommandHandle | None = None

        def mark_dispatch_crossed() -> None:
            dispatch_state["crossed"] = True
            if lifecycle is not None:
                lifecycle.mark_dispatch_crossed()

        try:
            mode_writer = getattr(
                climate_runtime, "async_set_device_mode_for_entity", None
            )
        except Exception:  # noqa: BLE001
            _LOGGER.warning(
                "HausmanHub climate mode writer lookup failed",
                exc_info=True,
            )
            mode_writer = None
        try:
            climate_entity_id = None
            if not dry_run and action_id in {"turn_on", "turn_off"} and callable(mode_writer):
                if context is not None and context[1] in {"climate", "humidifier", "switch"}:
                    climate_entity_id = context[0]
                else:
                    resolved = await service.async_resolve_device_action(
                        target_id, action_id
                    )
                    if resolved is not None and resolved[1] in {"climate", "humidifier", "switch"}:
                        climate_entity_id = resolved[0]
            climate_mode_precondition = _climate_mode_precondition(
                climate_runtime, climate_entity_id
            )
            execute_options: dict[str, Any] = {
                "correlation_id": correlation_id
            }
            if dry_run:
                execute_options["dry_run"] = True
            if dangerous_action and not dry_run:
                execute_options["dangerous_authorized"] = True
            if reassert_key is not None:
                execute_options["force_new_readback"] = True
                execute_options["automatic_reassert"] = True
                execute_options["reassert_claim_id"] = reassert_key
                execute_options["expected_evidence_revision"] = payload[
                    "expectedEvidenceRevision"
                ]
                execute_options["expected_evidence_sequence"] = payload[
                    "expectedEvidenceSequence"
                ]
            if dispatch_request_id is not None:
                execute_options["request_id"] = dispatch_request_id
            if context is not None:
                execute_options["expected_entity_id"] = entity_id
                execute_options["expected_domain"] = target_type
                execute_options["expected_service"] = allowed_service
            if direct_idempotent_allowed and not dry_run:
                execute_options["idempotent_actions"] = True
            if contextual_dangerous_action:
                execute_options["contextually_dangerous"] = True
            if intercom_action and not dry_run:
                execute_options["intercom_release_required"] = True
            if safe_action_candidate and _supports_keyword(
                service.async_execute_device_action, "require_safe_evidence"
            ):
                execute_options["require_safe_evidence"] = True

            async def execute_safe_once(
                coordinator_marker: Any,
                validate_authority: Any,
            ) -> Mapping[str, object]:
                owned_options = dict(execute_options)

                def combined_dispatch_marker() -> None:
                    mark_dispatch_crossed()
                    coordinator_marker()

                owned_options["dispatch_marker"] = combined_dispatch_marker
                owned_options["before_dispatch"] = validate_authority
                owned_result = dict(
                    await service.async_execute_device_action(
                        target_id,
                        action_id,
                        payload.get("value"),
                        **owned_options,
                    )
                )
                if owned_result.get("accepted") is True and climate_entity_id is not None:
                    try:
                        climate_mode_change = await _async_write_climate_mode(
                            mode_writer,
                            climate_entity_id,
                            "automatic" if action_id == "turn_on" else "manual",
                            climate_mode_precondition,
                        )
                        if isinstance(climate_mode_change, Mapping):
                            owned_result.update(
                                {
                                    "climateMode": climate_mode_change["mode"],
                                    "climateModeName": "Автоматический режим" if climate_mode_change["mode"] == "automatic" else "Ручной режим",
                                }
                            )
                    except Exception:  # noqa: BLE001
                        _LOGGER.warning(
                            "HausmanHub climate mode postprocessing failed after safe device action",
                            exc_info=True,
                        )
                return owned_result

            def project_safe_result(
                owned_result: Mapping[str, object],
            ) -> dict[str, object]:
                projected: dict[str, object] = {
                    "contract": {
                        "name": "hausman-hub-device-action-receipt",
                        "version": 1,
                    },
                    **dict(owned_result),
                    "targetType": target_type,
                }
                if full_response:
                    projected = full_action_receipt(
                        payload=payload,
                        result=owned_result,
                        target_type=target_type,
                        state=(
                            self._hass.states.get(entity_id)
                            if isinstance(entity_id, str)
                            else None
                        ),
                        allowed_actions=allowed_actions,
                        pre_command_evidence=pre_command_evidence,
                        decision_at=decision_at,
                    )
                return projected

            async def publish_late_safe_result(
                handle: SafeDeviceCommandHandle,
                owned_result: Mapping[str, object],
            ) -> None:
                assert isinstance(safe_coordinator, SafeDeviceCommandLifecycle)
                projected = project_safe_result(owned_result)
                if lifecycle is not None and not lifecycle.terminal_persisted:
                    completion_failure = await lifecycle.async_complete(projected)
                    if completion_failure is not None:
                        return
                if not await safe_coordinator.async_record_late_receipt(
                    handle, projected
                ):
                    return
                try:
                    publish_command_receipt(
                        self._hass, projected, operation="device_action"
                    )
                except Exception:  # noqa: BLE001
                    _LOGGER.warning(
                        "HausmanHub late safe device receipt publication failed",
                        exc_info=True,
                    )

            if safe_action_admitted:
                assert isinstance(context, tuple)
                assert isinstance(safe_coordinator, SafeDeviceCommandLifecycle)
                owned_request_id = dispatch_request_id or f"safe.{uuid.uuid4().hex}"
                execute_options["request_id"] = owned_request_id
                operation = SafeDeviceCommandOperation(
                    request_fingerprint=fingerprint,
                    request_id=owned_request_id,
                    target_id=target_id,
                    entity_id=context[0],
                    domain=context[1],
                    service=context[3],
                    action_id=action_id,
                    response_media_type=response_media_type,
                    pre_evidence_revision=_state_revision(
                        self._hass.states.get(context[0])
                    ),
                )
                try:
                    safe_handle = await _await_before_safe_deadline(
                        safe_coordinator.async_start(
                            operation,
                            execute_safe_once,
                            late_callback=publish_late_safe_result,
                            deadline=deadline,
                        ),
                        deadline,
                    )
                    result_or_pending = await safe_coordinator.async_response(
                        safe_handle, deadline
                    )
                except asyncio.CancelledError:
                    if safe_handle is not None:
                        await safe_coordinator.async_note_http_abandoned(safe_handle)
                    raise
                except Exception:
                    _LOGGER.warning(
                        "HausmanHub safe device command admission failed",
                        exc_info=True,
                    )
                    return await _async_action_failure(
                        self,
                        lifecycle,
                        request_id=str(payload.get("requestId") or owned_request_id),
                        response_media_type=response_media_type,
                    )
                result = (
                    dict(result_or_pending)
                    if result_or_pending is not None
                    else _pending_safe_result(
                        handle=safe_handle,
                        hass=self._hass,
                        correlation_id=correlation_id,
                    )
                )
            else:
                if not dry_run and _supports_keyword(
                    service.async_execute_device_action, "dispatch_marker"
                ):
                    execute_options["dispatch_marker"] = mark_dispatch_crossed
                result = await service.async_execute_device_action(
                    target_id,
                    action_id,
                    payload.get("value"),
                    **execute_options,
                )
        except Exception:
            _LOGGER.warning("HausmanHub device action execution failed", exc_info=True)
            if lifecycle is not None:
                return await lifecycle.async_failure()
            failure = _execution_failure_response(
                request_id=str(payload.get("requestId")),
                dispatch_crossed=dispatch_state["crossed"],
            )
            return _negotiated_json(
                self, failure["payload"], status_code=failure["status"],
                media_type=response_media_type,
            )
        try:
            if not isinstance(result, Mapping):
                raise TypeError("device action receipt is not a mapping")
            result = dict(result)
        except Exception:  # noqa: BLE001
            _LOGGER.warning("HausmanHub device action returned a malformed receipt")
            return await _async_action_failure(
                self,
                lifecycle,
                request_id=str(payload.get("requestId")),
                response_media_type=response_media_type,
                dispatch_crossed=dispatch_state["crossed"],
            )
        if result.get("accepted") is not True and dispatch_state["crossed"]:
            return await _async_action_failure(
                self,
                lifecycle,
                request_id=str(payload.get("requestId")),
                response_media_type=response_media_type,
                dispatch_crossed=True,
            )
        if (
            safe_handle is None
            and result.get("accepted") is True
            and climate_entity_id is not None
        ):
            try:
                climate_mode_change = await _async_write_climate_mode(
                    mode_writer, climate_entity_id,
                    "automatic" if action_id == "turn_on" else "manual",
                    climate_mode_precondition,
                )
                if isinstance(climate_mode_change, Mapping):
                    result = {
                        **result,
                        "climateMode": climate_mode_change["mode"],
                        "climateModeName": "Автоматический режим" if climate_mode_change["mode"] == "automatic" else "Ручной режим",
                    }
            except Exception:  # noqa: BLE001
                _LOGGER.warning(
                    "HausmanHub climate mode postprocessing failed after device action",
                    exc_info=True,
                )
        if (
            lifecycle is not None
            and intercom_action
            and result.get("accepted") is not True
        ):
            # Prepare is durable before dispatch. A normal failed receipt means
            # the executor did not cross the physical dispatch boundary, so
            # the unarmed obligation must not block the next command.
            if not await lifecycle.async_cancel_unarmed_intercom():
                return await lifecycle.async_failure()
            release_seconds = None
        if result.get("accepted") is True:
            if dry_run and intercom_action:
                service.publish_intercom_dry_run(
                    target_id=target_id,
                    correlation_id=correlation_id,
                    request_id=str(result.get("requestId")),
                )
            elif intercom_action and release_seconds is None:
                try:
                    release_seconds = await service.async_schedule_intercom_release(
                        target_id,
                        action_id,
                        correlation_id=correlation_id,
                        request_id=str(result.get("requestId")),
                    )
                except Exception:  # noqa: BLE001
                    _LOGGER.warning(
                        "HausmanHub intercom release scheduling failed",
                        exc_info=True,
                    )
                    if lifecycle is not None:
                        return await lifecycle.async_failure()
                    raise
        response: dict[str, object] = {
            "contract": {
                "name": "hausman-hub-device-action-receipt",
                "version": 1,
            },
            **result,
            "targetType": target_type,
        }
        if full_response:
            try:
                final_state = (
                    self._hass.states.get(entity_id)
                    if isinstance(entity_id, str)
                    else None
                )
                response = full_action_receipt(
                    payload=payload,
                    result=result,
                    target_type=target_type,
                    state=final_state,
                    allowed_actions=allowed_actions,
                    pre_command_evidence=pre_command_evidence,
                    decision_at=decision_at,
                )
            except Exception:  # noqa: BLE001
                _LOGGER.warning(
                    "HausmanHub device action receipt construction failed",
                    exc_info=True,
                )
                return await _async_action_failure(
                    self,
                    lifecycle,
                    request_id=str(payload.get("requestId")),
                    response_media_type=response_media_type,
                    dispatch_crossed=dispatch_state["crossed"],
                )
        if release_seconds is not None:
            response["autoReleaseSeconds"] = release_seconds
            response["releaseReceiptPending"] = True
        if dry_run:
            response["dryRun"] = True
        if lifecycle is not None:
            try:
                completion_failure = await lifecycle.async_complete(response)
            except asyncio.CancelledError:
                if safe_handle is not None and isinstance(
                    safe_coordinator, SafeDeviceCommandLifecycle
                ):
                    await safe_coordinator.async_note_http_abandoned(safe_handle)
                raise
            if completion_failure is not None:
                if safe_handle is not None and isinstance(
                    safe_coordinator, SafeDeviceCommandLifecycle
                ):
                    await safe_coordinator.async_note_http_abandoned(safe_handle)
                return completion_failure
        if safe_handle is not None and isinstance(
            safe_coordinator, SafeDeviceCommandLifecycle
        ):
            await safe_coordinator.async_note_first_response_persisted(safe_handle)
        try:
            publish_command_receipt(self._hass, response, operation="device_action")
        except Exception:  # noqa: BLE001
            _LOGGER.warning(
                "HausmanHub device action receipt publication failed",
                exc_info=True,
            )
        return _negotiated_json(
            self,
            response,
            status_code=(
                HTTPStatus.OK
                if result.get("accepted") is True
                else HTTPStatus.CONFLICT
            ),
            media_type=response_media_type,
        )

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
        deadline = CommandDeadline.start()
        if not _is_exact_request(request, self.url):
            return _not_found(self)
        if not (
            _is_local_tablet_request(request) or _is_local_admin_request(request)
        ):
            return _forbidden(self)
        response_media_type = negotiated_response_media_type(request, batch=True)
        if response_media_type is None:
            return _not_acceptable(self)
        full_response = response_media_type == FULL_BATCH_RESPONSE_MEDIA_TYPE
        service = self._hass.data.get(DOMAIN, {}).get("scenario_service")
        if not isinstance(service, ScenarioService):
            return self.json_message(
                "The HausmanHub device action API is unavailable.",
                HTTPStatus.SERVICE_UNAVAILABLE,
                headers=NO_STORE_HEADERS,
            )
        try:
            payload = await strict_request_json(
                request,
                allowed_media_types=frozenset(
                    {LEGACY_REQUEST_MEDIA_TYPE, FULL_BATCH_REQUEST_MEDIA_TYPE}
                ),
            )
            full_request = request_is_full(request, batch=True)
            normalized = validate_batch_request(payload, full=full_request)
        except StrictJsonError:
            return self.json_message(
                "The device action batch body is invalid.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        try:
            correlation_id = resolve_correlation_id(payload, field="correlationId")
        except CorrelationIdError:
            return self.json_message(
                "correlationId is invalid.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        potentially_safe_batch = bool(
            normalized
            and all(
                item.get("dryRun") is not True
                and _catalog_safe_descriptor(
                    service,
                    str(item["targetId"]),
                    str(item["actionId"]),
                )
                for item in normalized
            )
        )
        try:
            contexts = []
            for item in normalized:
                resolution = service.async_resolve_device_action_context(
                    str(item["targetId"]), str(item["actionId"])
                )
                contexts.append(
                    await _await_before_safe_deadline(resolution, deadline)
                    if potentially_safe_batch
                    else await resolution
                )
            intercom_flags = []
            for item in normalized:
                resolution = service.async_is_intercom_action(
                    str(item["targetId"]), str(item["actionId"])
                )
                intercom_flags.append(
                    await _await_before_safe_deadline(resolution, deadline)
                    if potentially_safe_batch
                    else await resolution
                )
        except _SafeCommandDeadlineExpired:
            return await _async_action_failure(
                self,
                None,
                request_id=str(
                    payload.get("requestId")
                    or f"safe-batch-admission.{uuid.uuid4().hex}"
                ),
                response_media_type=response_media_type,
            )
        contextual_dangerous_flags = [
            intercom_flags[index]
            or service.is_contextually_dangerous_action(
                str(item["targetId"]), str(item["actionId"])
            )
            for index, item in enumerate(normalized)
        ]
        external_cover_flags = [
            service.is_external_cover_action(
                str(item["targetId"]), str(item["actionId"])
            )
            for item in normalized
        ]
        dangerous_indexes = [
            index
            for index, item in enumerate(normalized)
            if item.get("dryRun") is not True
            and (
                item["actionId"] in DANGEROUS_ACTION_IDS
                or contextual_dangerous_flags[index]
            )
        ]
        reassert_indexes = [
            index for index, item in enumerate(normalized)
            if item.get("reassertKey") is not None
        ]
        safe_batch_candidates = bool(
            potentially_safe_batch
            and not dangerous_indexes
            and not reassert_indexes
            and all(
                _safe_action_candidate(
                    context=contexts[index],
                    action_id=str(item["actionId"]),
                    dangerous=False,
                    external_cover=external_cover_flags[index],
                    intercom=intercom_flags[index],
                    reassert_key=item.get("reassertKey"),
                    dry_run=False,
                )
                for index, item in enumerate(normalized)
            )
        )
        safe_batch_admitted = bool(
            safe_batch_candidates
            and all(
                context is not None
                and _safe_action_admitted(
                    service=service,
                    hass=self._hass,
                    target_id=str(item["targetId"]),
                    action_id=str(item["actionId"]),
                    value=item.get("value"),
                    context=context,
                )
                for item, context in zip(normalized, contexts, strict=True)
            )
        )
        if dangerous_indexes and not full_request:
            return _legacy_dangerous_forbidden(self)
        if (
            len(dangerous_indexes) > 1
            or len(reassert_indexes) > 1
            or dangerous_indexes and reassert_indexes
        ):
            return self.json_message(
                "A batch may contain one coordinated physical recovery action.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        if dangerous_indexes and not _confirmed_action(
            normalized[dangerous_indexes[0]]
        ):
            return _dangerous_confirmation_required(self)
        if any(
            external_cover_flags[index]
            and normalized[index].get("dryRun") is not True
            and (
                contexts[index] is None
                or (state := self._hass.states.get(contexts[index][0])) is None
                or str(getattr(state, "state", "unknown"))
                in {"unknown", "unavailable"}
                or not _state_is_fresh(state)
            )
            for index in range(len(normalized))
        ):
            return _stale_critical_evidence(self)
        physical_actions = any(
            item.get("dryRun") is not True for item in normalized
        )
        physical_full = full_request and physical_actions
        decision_at = time.time_ns() // 1_000_000
        pre_evidence = [
            (
                evidence_snapshot(
                    target_id=str(item["targetId"]),
                    state=self._hass.states.get(context[0]),
                    allowed_actions=context[2],
                )
                if context is not None
                and context[1] == "light"
                and (full_response or item.get("reassertKey") is not None)
                else {}
            )
            for item, context in zip(normalized, contexts, strict=True)
        ]
        idempotency = self._hass.data.get(DOMAIN, {}).get(
            "device_action_idempotency"
        )
        fingerprint = canonical_request_fingerprint(payload)
        if reassert_indexes:
            index = reassert_indexes[0]
            context = contexts[index]
            item = normalized[index]
            if not isinstance(idempotency, DangerousActionIdempotency):
                return _coordinator_unavailable(self)
            try:
                existing_reassert = await idempotency.async_lookup(
                    key=_reassert_coordination_key(item),
                    fingerprint=fingerprint,
                )
            except Exception as error:  # noqa: BLE001
                if (
                    isinstance(error, RuntimeError)
                    and str(error) == "dangerous action idempotency store is full"
                ):
                    return _idempotency_journal_full(
                        self, media_type=response_media_type
                    )
                failure = _execution_failure_response(
                    request_id=str(payload.get("requestId")),
                    dispatch_crossed=False,
                )
                return _negotiated_json(
                    self,
                    failure["payload"],
                    status_code=failure["status"],
                    media_type=response_media_type,
                )
            if existing_reassert.outcome == "conflict":
                return _idempotency_conflict(
                    self,
                    expected_hash=existing_reassert.existing_fingerprint,
                    request_hash=fingerprint,
                    media_type=response_media_type,
                )
            if existing_reassert.outcome == "in_progress":
                return _idempotency_in_progress(
                    self,
                    existing_reassert.state,
                    media_type=response_media_type,
                )
            if (
                existing_reassert.outcome == "replay"
                and existing_reassert.receipt is not None
            ):
                return _negotiated_json(
                    self,
                    existing_reassert.receipt,
                    status_code=_replay_status(existing_reassert.receipt),
                    media_type=(
                        existing_reassert.response_media_type or response_media_type
                    ),
                )
            if context is not None:
                pre_evidence[index] = evidence_snapshot(
                    target_id=str(item["targetId"]),
                    state=self._hass.states.get(context[0]),
                    allowed_actions=context[2],
                )
            if not _valid_reassert_evidence(
                item,
                pre_evidence[index],
                target_type=context[1] if context is not None else "sensor",
            ):
                return _stale_reassert_evidence(self)
        idempotency_key: str | None = None
        dispatch_request_ids: tuple[str, ...] | None = None
        intercom_release_index: int | None = None
        lifecycle: _DeviceActionLifecycle | None = None
        item_dispatch_crossed = [False for _item in normalized]
        unattributed_dispatch_state = {"crossed": False}
        coordinated_index = (
            dangerous_indexes[0]
            if dangerous_indexes
            else reassert_indexes[0]
            if reassert_indexes
            else None
        )
        if physical_full:
            if not isinstance(idempotency, DangerousActionIdempotency) or (
                coordinated_index is not None
                and any(context is None for context in contexts)
            ):
                return self.json_message(
                    "The dangerous device action coordinator is unavailable.",
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    headers=NO_STORE_HEADERS,
                )
            coordinated_item = (
                normalized[coordinated_index]
                if coordinated_index is not None
                else None
            )
            idempotency_key = (
                str(coordinated_item["idempotencyKey"])
                if dangerous_indexes
                else _reassert_coordination_key(coordinated_item)
                if reassert_indexes and coordinated_item is not None
                else f"batch:{payload['requestId']}"
            )
            dispatch_id = uuid.uuid4().hex
            dispatch_request_ids = tuple(
                f"dispatch.{dispatch_id}.{index}"
                for index in range(len(normalized))
            )
            lifecycle = _DeviceActionLifecycle(
                view=self,
                service=service,
                idempotency=idempotency,
                key=idempotency_key,
                request_id=str(payload.get("requestId")),
                response_media_type=response_media_type,
            )

            async def abandon_late_batch_reservation(
                reservation_task: asyncio.Task[Any],
            ) -> None:
                if reservation_task.cancelled():
                    return
                try:
                    late_reservation = reservation_task.result()
                except Exception:  # noqa: BLE001
                    return
                if getattr(late_reservation, "outcome", None) != "reserved":
                    return
                await idempotency.async_abandon_pre_dispatch(idempotency_key)

            async def abandon_late_batch_transition(
                _task: asyncio.Task[Any],
            ) -> None:
                await idempotency.async_abandon_pre_dispatch(idempotency_key)

            try:
                reservation_awaitable = idempotency.async_reserve(
                    key=idempotency_key,
                    fingerprint=fingerprint,
                    dispatch_id=dispatch_id,
                    bindings=[
                        {
                            "actionIndex": index,
                            "targetId": str(item["targetId"]),
                            "targetType": (
                                contexts[index][1]
                                if contexts[index] is not None
                                else "sensor"
                            ),
                            "actionId": str(item["actionId"]),
                            "correlationId": correlation_id,
                            "requestId": dispatch_request_ids[index],
                        }
                        for index, item in enumerate(normalized)
                    ],
                    response_media_type=response_media_type,
                )
                reservation = (
                    await _await_before_safe_deadline(
                        reservation_awaitable,
                        deadline,
                        on_timeout_settled=abandon_late_batch_reservation,
                    )
                    if safe_batch_candidates
                    else await reservation_awaitable
                )
            except _SafeCommandDeadlineExpired:
                return await _async_action_failure(
                    self,
                    None,
                    request_id=str(payload.get("requestId")),
                    response_media_type=response_media_type,
                )
            except Exception as error:  # noqa: BLE001
                if (
                    isinstance(error, RuntimeError)
                    and str(error) == "dangerous action idempotency store is full"
                ):
                    return _idempotency_journal_full(
                        self, media_type=response_media_type
                    )
                failure = _execution_failure_response(
                    request_id=str(payload.get("requestId")),
                    dispatch_crossed=False,
                )
                return _negotiated_json(
                    self,
                    failure["payload"],
                    status_code=failure["status"],
                    media_type=response_media_type,
                )
            if reservation.outcome == "conflict":
                return _idempotency_conflict(
                    self,
                    expected_hash=reservation.existing_fingerprint,
                    request_hash=fingerprint,
                    media_type=response_media_type,
                )
            if reservation.outcome == "in_progress":
                return _idempotency_in_progress(
                    self,
                    reservation.state,
                    media_type=response_media_type,
                )
            if reservation.outcome == "replay" and reservation.receipt is not None:
                return _negotiated_json(
                    self,
                    reservation.receipt,
                    status_code=_replay_status(reservation.receipt),
                    media_type=(
                        reservation.response_media_type or response_media_type
                    ),
                )
            lifecycle.mark_reservation_owned()
            try:
                pending_awaitable = idempotency.async_mark_pending(idempotency_key)
                if safe_batch_candidates:
                    await _await_before_safe_deadline(
                        pending_awaitable,
                        deadline,
                        on_timeout_settled=abandon_late_batch_transition,
                    )
                else:
                    await pending_awaitable
            except _SafeCommandDeadlineExpired:
                return await _async_action_failure(
                    self,
                    None,
                    request_id=str(payload.get("requestId")),
                    response_media_type=response_media_type,
                )
            except Exception:  # noqa: BLE001
                _LOGGER.warning(
                    "HausmanHub device action batch pending persistence failed",
                    exc_info=True,
                )
                return await lifecycle.async_failure()

        if dangerous_indexes:
            index = dangerous_indexes[0]
            if intercom_flags[index]:
                intercom_release_index = index
                item = normalized[index]
                assert lifecycle is not None
                release_request_id = f"{dispatch_request_ids[index]}.release"
                lifecycle.note_intercom_prepare_attempt(
                    str(item["targetId"]),
                    expected_entity_id=(
                        contexts[index][0] if contexts[index] is not None else None
                    ),
                    expected_request_id=release_request_id,
                )
                try:
                    prepared = await service.async_prepare_intercom_release(
                        str(item["targetId"]),
                        str(item["actionId"]),
                        correlation_id=correlation_id,
                        request_id=release_request_id,
                        expected_entity_id=(
                            contexts[index][0] if contexts[index] is not None else None
                        ),
                    )
                except Exception:  # noqa: BLE001
                    _LOGGER.warning(
                        "HausmanHub intercom batch release preparation failed",
                        exc_info=True,
                    )
                    return await lifecycle.async_failure()
                if prepared is None:
                    return await lifecycle.async_failure()
        if idempotency_key is not None and isinstance(
            idempotency, DangerousActionIdempotency
        ):
            try:
                dispatching_awaitable = idempotency.async_mark_dispatching(
                    idempotency_key
                )
                if safe_batch_admitted:
                    await _await_before_safe_deadline(
                        dispatching_awaitable,
                        deadline,
                        on_timeout_settled=abandon_late_batch_transition,
                    )
                else:
                    await dispatching_awaitable
            except _SafeCommandDeadlineExpired:
                return await _async_action_failure(
                    self,
                    None,
                    request_id=str(payload.get("requestId")),
                    response_media_type=response_media_type,
                )
            except Exception:  # noqa: BLE001
                _LOGGER.warning(
                    "HausmanHub device action batch dispatch fence persistence failed",
                    exc_info=True,
                )
                assert lifecycle is not None
                return await lifecycle.async_failure()

        batch_options: dict[str, object] = {"correlation_id": correlation_id}
        if dispatch_request_ids is not None:
            batch_options["request_ids"] = dispatch_request_ids
            batch_options["dispatch_contexts"] = tuple(contexts)
        if dangerous_indexes:
            batch_options["dangerous_authorized"] = frozenset(
                (
                    str(normalized[index]["targetId"]),
                    str(normalized[index]["actionId"]),
                )
                for index in dangerous_indexes
            )
            batch_options["intercom_release_required"] = frozenset(
                (
                    str(normalized[index]["targetId"]),
                    str(normalized[index]["actionId"]),
                )
                for index in dangerous_indexes
                if intercom_flags[index]
            )
        batch_options["initial_contextually_dangerous"] = frozenset(
            (str(normalized[index]["targetId"]), str(normalized[index]["actionId"]))
            for index, flag in enumerate(contextual_dangerous_flags) if flag
        )
        safe_coordinator = self._hass.data.get(DOMAIN, {}).get(
            "safe_device_command_lifecycle"
        )
        safe_batch_handles: list[SafeDeviceCommandHandle] = []
        if safe_batch_admitted and not isinstance(
            safe_coordinator, SafeDeviceCommandLifecycle
        ):
            return await _async_action_failure(
                self,
                lifecycle,
                request_id=str(
                    payload.get("requestId")
                    or f"safe-batch.{uuid.uuid4().hex}"
                ),
                response_media_type=response_media_type,
            )

        def project_safe_batch_item(
            index: int, result: Mapping[str, object]
        ) -> dict[str, object]:
            item = normalized[index]
            context = contexts[index]
            if full_response:
                return full_action_receipt(
                    payload=item,
                    result=result,
                    target_type=context[1] if context is not None else "sensor",
                    state=(
                        self._hass.states.get(context[0])
                        if context is not None
                        else None
                    ),
                    allowed_actions=context[2] if context is not None else (),
                    pre_command_evidence=pre_evidence[index],
                    decision_at=decision_at + index,
                    action_index=index,
                )
            return {
                "contract": {
                    "name": "hausman-hub-device-action-receipt",
                    "version": 1,
                },
                **dict(result),
                "targetType": context[1] if context is not None else "sensor",
                "actionIndex": index,
            }

        def batch_response_from_wrapped(
            wrapped_items: list[dict[str, object]],
        ) -> dict[str, object]:
            accepted_count = sum(
                item.get("accepted") is True for item in wrapped_items
            )
            confirmed_count = sum(
                item.get("confirmed") is True for item in wrapped_items
            )
            failed_count = sum(
                item.get("status") == "failed" for item in wrapped_items
            )
            return {
                "contract": {
                    "name": "hausman-hub-device-action-batch-receipt",
                    "version": 1,
                },
                "correlationId": correlation_id,
                "status": (
                    "confirmed"
                    if confirmed_count == len(wrapped_items)
                    else "failed"
                    if failed_count == len(wrapped_items)
                    else "partial"
                    if failed_count
                    else "accepted"
                ),
                "total": len(wrapped_items),
                "acceptedCount": accepted_count,
                "confirmedCount": confirmed_count,
                "failedCount": failed_count,
                "receipts": wrapped_items,
            }

        try:
            if safe_batch_admitted:
                assert isinstance(safe_coordinator, SafeDeviceCommandLifecycle)
                per_item_markers_supported = True
                receipts = []
                climate_runtime = self._hass.data.get(DOMAIN, {}).get(
                    "climate_runtime"
                )
                mode_writer = getattr(
                    climate_runtime, "async_set_device_mode_for_entity", None
                )
                for index, item in enumerate(normalized):
                    context = contexts[index]
                    assert context is not None
                    request_id = (
                        dispatch_request_ids[index]
                        if dispatch_request_ids is not None
                        else f"safe.{uuid.uuid4().hex}.{index}"
                    )
                    mode_precondition = (
                        _climate_mode_precondition(climate_runtime, context[0])
                        if item["actionId"] in {"turn_on", "turn_off"}
                        and context[1] in {"climate", "humidifier", "switch"}
                        and callable(mode_writer)
                        else None
                    )
                    if deadline.remaining() <= 0:
                        receipts.extend(
                            _blocked_unstarted_result(
                                request_id=(
                                    dispatch_request_ids[remaining_index]
                                    if dispatch_request_ids is not None
                                    else f"safe.{uuid.uuid4().hex}.{remaining_index}"
                                ),
                                target_id=str(normalized[remaining_index]["targetId"]),
                                action_id=str(normalized[remaining_index]["actionId"]),
                                correlation_id=correlation_id,
                            )
                            for remaining_index in range(index, len(normalized))
                        )
                        break

                    async def execute_safe_batch_item(
                        coordinator_marker: Any,
                        validate_authority: Any,
                        *,
                        item: Mapping[str, object] = item,
                        context: tuple[str, str, tuple[str, ...], str] = context,
                        request_id: str = request_id,
                        index: int = index,
                        mode_precondition: Mapping[str, object] | None = mode_precondition,
                    ) -> Mapping[str, object]:
                        def combined_dispatch_marker() -> None:
                            item_dispatch_crossed[index] = True
                            if lifecycle is not None:
                                lifecycle.mark_dispatch_crossed()
                            coordinator_marker()

                        item_result = dict(
                            await service.async_execute_device_action(
                                str(item["targetId"]),
                                str(item["actionId"]),
                                item.get("value"),
                                correlation_id=correlation_id,
                                request_id=request_id,
                                expected_entity_id=context[0],
                                expected_domain=context[1],
                                expected_service=context[3],
                                dispatch_marker=combined_dispatch_marker,
                                before_dispatch=validate_authority,
                                require_safe_evidence=True,
                            )
                        )
                        if (
                            item_result.get("accepted") is True
                            and item["actionId"] in {"turn_on", "turn_off"}
                            and context[1] in {"climate", "humidifier", "switch"}
                            and callable(mode_writer)
                        ):
                            try:
                                mode_change = await _async_write_climate_mode(
                                    mode_writer,
                                    context[0],
                                    "automatic" if item["actionId"] == "turn_on" else "manual",
                                    mode_precondition,
                                )
                                if isinstance(mode_change, Mapping):
                                    item_result.update(
                                        {
                                            "climateMode": mode_change["mode"],
                                            "climateModeName": "Автоматический режим" if mode_change["mode"] == "automatic" else "Ручной режим",
                                        }
                                    )
                            except Exception:  # noqa: BLE001
                                _LOGGER.warning(
                                    "HausmanHub climate mode postprocessing failed after safe batch item",
                                    exc_info=True,
                                )
                        return item_result

                    async def publish_late_batch_item(
                        handle: SafeDeviceCommandHandle,
                        late_result: Mapping[str, object],
                        *,
                        index: int = index,
                    ) -> None:
                        projected_item = project_safe_batch_item(index, late_result)
                        if (
                            lifecycle is not None
                            and not lifecycle.terminal_persisted
                            and handle.http_abandoned
                        ):
                            raw_items = [dict(receipt) for receipt in receipts[:index]]
                            raw_items.append(dict(late_result))
                            raw_items.extend(
                                _blocked_unstarted_result(
                                    request_id=(
                                        dispatch_request_ids[remaining_index]
                                        if dispatch_request_ids is not None
                                        else f"safe.abandoned.{remaining_index}"
                                    ),
                                    target_id=str(normalized[remaining_index]["targetId"]),
                                    action_id=str(normalized[remaining_index]["actionId"]),
                                    correlation_id=correlation_id,
                                )
                                for remaining_index in range(index + 1, len(normalized))
                            )
                            disconnected_response = batch_response_from_wrapped(
                                [
                                    project_safe_batch_item(item_index, raw)
                                    for item_index, raw in enumerate(raw_items)
                                ]
                            )
                            completion_failure = await lifecycle.async_complete(
                                disconnected_response,
                                item_journal=[
                                    dict(receipt)
                                    for receipt in disconnected_response["receipts"]
                                ],
                            )
                            if completion_failure is not None:
                                return
                        if not await safe_coordinator.async_record_late_receipt(
                            handle, projected_item
                        ):
                            return
                        try:
                            publish_command_receipt(
                                self._hass,
                                projected_item,
                                operation="device_action",
                            )
                        except Exception:  # noqa: BLE001
                            _LOGGER.warning(
                                "HausmanHub late safe batch receipt publication failed",
                                exc_info=True,
                            )

                    operation = SafeDeviceCommandOperation(
                        request_fingerprint=fingerprint,
                        request_id=request_id,
                        target_id=str(item["targetId"]),
                        entity_id=context[0],
                        domain=context[1],
                        service=context[3],
                        action_id=str(item["actionId"]),
                        response_media_type=response_media_type,
                        pre_evidence_revision=_state_revision(
                            self._hass.states.get(context[0])
                        ),
                    )
                    handle: SafeDeviceCommandHandle | None = None
                    try:
                        handle = await _await_before_safe_deadline(
                            safe_coordinator.async_start(
                                operation,
                                execute_safe_batch_item,
                                late_callback=publish_late_batch_item,
                                deadline=deadline,
                            ),
                            deadline,
                        )
                        safe_batch_handles.append(handle)
                        item_result = await safe_coordinator.async_response(
                            handle, deadline
                        )
                    except asyncio.CancelledError:
                        if handle is not None:
                            await safe_coordinator.async_note_http_abandoned(handle)
                        raise
                    except Exception:  # noqa: BLE001
                        _LOGGER.warning(
                            "HausmanHub safe batch item admission failed",
                            exc_info=True,
                        )
                        receipts.extend(
                            _blocked_unstarted_result(
                                request_id=(
                                    dispatch_request_ids[remaining_index]
                                    if dispatch_request_ids is not None
                                    else f"safe.{uuid.uuid4().hex}.{remaining_index}"
                                ),
                                target_id=str(normalized[remaining_index]["targetId"]),
                                action_id=str(normalized[remaining_index]["actionId"]),
                                correlation_id=correlation_id,
                            )
                            for remaining_index in range(index, len(normalized))
                        )
                        break
                    if item_result is None:
                        receipts.append(
                            _pending_safe_result(
                                handle=handle,
                                hass=self._hass,
                                correlation_id=correlation_id,
                            )
                        )
                        receipts.extend(
                            _blocked_unstarted_result(
                                request_id=(
                                    dispatch_request_ids[remaining_index]
                                    if dispatch_request_ids is not None
                                    else f"safe.{uuid.uuid4().hex}.{remaining_index}"
                                ),
                                target_id=str(normalized[remaining_index]["targetId"]),
                                action_id=str(normalized[remaining_index]["actionId"]),
                                correlation_id=correlation_id,
                            )
                            for remaining_index in range(index + 1, len(normalized))
                        )
                        break
                    receipts.append(dict(item_result))
            elif safe_batch_candidates:
                per_item_markers_supported = True
                receipts = [
                    _blocked_safe_admission_result(
                        request_id=(
                            dispatch_request_ids[index]
                            if dispatch_request_ids is not None
                            else f"safe.rejected.{uuid.uuid4().hex}.{index}"
                        ),
                        target_id=str(item["targetId"]),
                        action_id=str(item["actionId"]),
                        correlation_id=correlation_id,
                    )
                    for index, item in enumerate(normalized)
                ]
            else:
                if physical_actions and _supports_keyword(
                    service.async_execute_device_action_batch, "dispatch_marker"
                ):
                    def mark_unattributed_dispatch() -> None:
                        unattributed_dispatch_state["crossed"] = True
                        if lifecycle is not None:
                            lifecycle.mark_dispatch_crossed()

                    batch_options["dispatch_marker"] = mark_unattributed_dispatch
                per_item_markers_supported = bool(
                    physical_actions
                    and _supports_keyword(
                        service.async_execute_device_action_batch,
                        "dispatch_markers",
                    )
                )
                if per_item_markers_supported:
                    def item_marker(index: int) -> None:
                        if normalized[index].get("dryRun") is True:
                            return
                        item_dispatch_crossed[index] = True
                        if lifecycle is not None:
                            lifecycle.mark_dispatch_crossed()

                    batch_options["dispatch_markers"] = tuple(
                        (lambda index=index: item_marker(index))
                        for index in range(len(normalized))
                    )
                receipts = await service.async_execute_device_action_batch(
                    normalized,
                    **batch_options,
                )
        except Exception:  # noqa: BLE001
            _LOGGER.warning("HausmanHub device action batch execution failed", exc_info=True)
            if lifecycle is not None:
                return await lifecycle.async_failure()
            dispatch_crossed = bool(
                unattributed_dispatch_state["crossed"]
                or any(item_dispatch_crossed)
            )
            failure = _execution_failure_response(
                request_id=str(payload.get("requestId")),
                dispatch_crossed=dispatch_crossed,
            )
            return _negotiated_json(
                self, failure["payload"], status_code=failure["status"],
                media_type=response_media_type,
            )
        try:
            if (
                not isinstance(receipts, list)
                or len(receipts) != len(normalized)
                or not all(isinstance(receipt, Mapping) for receipt in receipts)
            ):
                raise TypeError("device action batch receipts are malformed")
            receipts = [dict(receipt) for receipt in receipts]
        except Exception:  # noqa: BLE001
            _LOGGER.warning("HausmanHub device action batch returned malformed receipts")
            return await _async_action_failure(
                self,
                lifecycle,
                request_id=str(payload.get("requestId")),
                response_media_type=response_media_type,
                dispatch_crossed=(
                    unattributed_dispatch_state["crossed"]
                    or any(item_dispatch_crossed)
                ),
            )
        rejected_indexes = [
            index
            for index, receipt in enumerate(receipts)
            if receipt.get("accepted") is not True
        ]
        rejection_after_dispatch = bool(rejected_indexes) and (
            any(item_dispatch_crossed[index] for index in rejected_indexes)
            or unattributed_dispatch_state["crossed"]
            or (lifecycle is None and any(item_dispatch_crossed))
            or (
                lifecycle is not None
                and not per_item_markers_supported
                and lifecycle.dispatch_crossed
            )
        )
        if rejection_after_dispatch:
            return await _async_action_failure(
                self,
                lifecycle,
                request_id=str(payload.get("requestId")),
                response_media_type=response_media_type,
                dispatch_crossed=True,
            )
        if lifecycle is not None and intercom_release_index is not None:
            release_receipt = receipts[intercom_release_index]
            if release_receipt.get("accepted") is not True:
                if not await lifecycle.async_cancel_unarmed_intercom():
                    return await lifecycle.async_failure()
        if full_response:
            try:
                wrapped = [
                    full_action_receipt(
                        payload=action,
                        result=receipt,
                        target_type=context[1] if context is not None else "sensor",
                        state=(
                            self._hass.states.get(context[0])
                            if context is not None
                            else None
                        ),
                        allowed_actions=context[2] if context is not None else (),
                        pre_command_evidence=pre,
                        decision_at=decision_at + index,
                        action_index=index,
                    )
                    for index, (action, receipt, context, pre) in enumerate(
                        zip(normalized, receipts, contexts, pre_evidence, strict=True)
                    )
                ]
                if not all(isinstance(receipt, Mapping) for receipt in wrapped):
                    raise TypeError("device action batch full receipt is malformed")
                wrapped = [dict(receipt) for receipt in wrapped]
            except Exception:  # noqa: BLE001
                _LOGGER.warning(
                    "HausmanHub device action batch receipt construction failed",
                    exc_info=True,
                )
                return await _async_action_failure(
                    self,
                    lifecycle,
                    request_id=str(payload.get("requestId")),
                    response_media_type=response_media_type,
                    dispatch_crossed=(
                        unattributed_dispatch_state["crossed"]
                        or any(item_dispatch_crossed)
                    ),
                )
        else:
            wrapped = [
                {
                    "contract": {
                        "name": "hausman-hub-device-action-receipt",
                        "version": 1,
                    },
                    **item,
                    "targetType": (
                        contexts[index][1]
                        if contexts[index] is not None
                        else "sensor"
                    ),
                    "actionIndex": index,
                }
                for index, item in enumerate(receipts)
            ]
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
        response: dict[str, object] = {
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
            }
        if lifecycle is not None:
            try:
                completion_failure = await lifecycle.async_complete(
                    response,
                    item_journal=[dict(item) for item in wrapped],
                )
            except asyncio.CancelledError:
                if isinstance(safe_coordinator, SafeDeviceCommandLifecycle):
                    for handle in safe_batch_handles:
                        await safe_coordinator.async_note_http_abandoned(handle)
                raise
            if completion_failure is not None:
                if isinstance(safe_coordinator, SafeDeviceCommandLifecycle):
                    for handle in safe_batch_handles:
                        await safe_coordinator.async_note_http_abandoned(handle)
                return completion_failure
        if isinstance(safe_coordinator, SafeDeviceCommandLifecycle):
            for handle in safe_batch_handles:
                await safe_coordinator.async_note_first_response_persisted(handle)
        for receipt in wrapped:
            try:
                publish_command_receipt(
                    self._hass, receipt, operation="device_action"
                )
            except Exception:  # noqa: BLE001
                _LOGGER.warning(
                    "HausmanHub device action batch receipt publication failed",
                    exc_info=True,
                )
        return _negotiated_json(
            self,
            response,
            media_type=response_media_type,
        )


def _negotiated_json(
    view: HomeAssistantView,
    payload: object,
    *,
    media_type: str,
    status_code: int = HTTPStatus.OK,
) -> Any:
    """Let Home Assistant create JSON before replacing its response media type."""

    response = view.json(
        payload,
        status_code=status_code,
        headers=NO_STORE_HEADERS,
    )
    response.headers["Content-Type"] = media_type
    return response


def _replay_status(receipt: Mapping[str, object]) -> int:
    """Replay the original single-action success or conflict semantics."""

    contract = receipt.get("contract")
    if (
        isinstance(contract, Mapping)
        and contract.get("name") == "hausman-hub-device-action-receipt"
        and receipt.get("accepted") is not True
    ):
        return HTTPStatus.CONFLICT
    return HTTPStatus.OK


def _not_acceptable(view: HomeAssistantView) -> Any:
    request_id = f"request-content-negotiation-{uuid.uuid4().hex[:16]}"
    return view.json(
        {
            "contract": {"name": "hausman-hub-error", "version": 1},
            "code": "not_acceptable",
            "category": "capability",
            "message": "Формат ответа не поддерживается. Обновите клиент.",
            "retryable": False,
            "status": 406,
            "requestId": request_id,
        },
        status_code=HTTPStatus.NOT_ACCEPTABLE,
        headers=NO_STORE_HEADERS,
    )


def _execution_failure_response(
    *,
    request_id: str,
    dispatch_crossed: bool,
) -> dict[str, object]:
    """Translate expected executor failures without inventing physical outcome."""

    if dispatch_crossed:
        payload = api_error_payload(
            "conflict",
            request_id=request_id,
            details={
                "detailCode": "idempotency_in_progress",
                "state": "dispatch_unknown",
                "recoveryRequired": True,
                "operatorRecoveryRequired": True,
                "automaticRetryAllowed": False,
                "newUserActionRequired": False,
                "freshConfirmationRequired": False,
            },
        )
        return {"status": HTTPStatus.CONFLICT, "payload": payload}
    payload = api_error_payload("unavailable", request_id=request_id)
    return {"status": HTTPStatus.SERVICE_UNAVAILABLE, "payload": payload}


def _supports_keyword(callable_object: object, keyword: str) -> bool:
    """Keep test doubles and older service adapters source-compatible."""

    try:
        parameters = inspect.signature(callable_object).parameters.values()
    except (TypeError, ValueError):
        return True
    return any(
        parameter.name == keyword
        or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _legacy_dangerous_forbidden(view: HomeAssistantView) -> Any:
    return view.json(
        {
            "contract": {"name": "hausman-hub-error", "version": 1},
            "code": "forbidden",
            "category": "authorization",
            "message": "Опасная команда требует обновлённый подтверждённый протокол.",
            "retryable": False,
            "status": 403,
            "requestId": f"request-dangerous-legacy-{uuid.uuid4().hex[:16]}",
        },
        status_code=HTTPStatus.FORBIDDEN,
        headers=NO_STORE_HEADERS,
    )


def _confirmed_action(payload: Mapping[str, object]) -> bool:
    key = payload.get("idempotencyKey")
    return (
        payload.get("confirmedByUser") is True
        and isinstance(key, str)
        and 1 <= len(key) <= 256
        and key.isascii()
        and key[0].isalnum()
        and all(char.isalnum() or char in "._:-" for char in key)
    )


def _valid_reassert_evidence(
    payload: Mapping[str, object],
    evidence: Mapping[str, object],
    *,
    target_type: str,
) -> bool:
    """Consume only the exact stale light evidence identity advertised by HA."""

    return bool(
        target_type == "light"
        and payload.get("actionId") == "turn_on"
        and evidence.get("reassertPolicy") == "light_turn_on"
        and evidence.get("reassertBudget") == 1
        and payload.get("expectedEvidenceRevision")
        == evidence.get("evidenceRevision")
        and payload.get("expectedEvidenceSequence")
        == evidence.get("evidenceSequence")
    )


def _reassert_coordination_key(payload: Mapping[str, object]) -> str:
    """Bind one budget to server evidence, never to a client-selected key."""

    identity = {
        "targetId": payload.get("targetId"),
        "evidenceRevision": payload.get("expectedEvidenceRevision"),
        "evidenceSequence": payload.get("expectedEvidenceSequence"),
    }
    return f"reassert-evidence:{canonical_request_fingerprint(identity)}"


def _dangerous_confirmation_required(view: HomeAssistantView) -> Any:
    return view.json_message(
        "Explicit user confirmation and an idempotency key are required.",
        HTTPStatus.CONFLICT,
        headers=NO_STORE_HEADERS,
    )


def _stale_reassert_evidence(view: HomeAssistantView) -> Any:
    return view.json_message(
        "The light reassert evidence is stale or does not allow this command.",
        HTTPStatus.CONFLICT,
        headers=NO_STORE_HEADERS,
    )


def _stale_critical_evidence(view: HomeAssistantView) -> Any:
    return view.json_message(
        "Fresh device evidence is required for this external cover command.",
        HTTPStatus.CONFLICT,
        headers=NO_STORE_HEADERS,
    )


def _coordinator_unavailable(view: HomeAssistantView) -> Any:
    return view.json_message(
        "The device action safety coordinator is unavailable.",
        HTTPStatus.SERVICE_UNAVAILABLE,
        headers=NO_STORE_HEADERS,
    )


def _idempotency_in_progress(
    view: HomeAssistantView,
    state: str,
    *,
    media_type: str,
) -> Any:
    return _negotiated_json(
        view,
        {
            "contract": {"name": "hausman-hub-error", "version": 1},
            "code": "conflict",
            "category": "conflict",
            "message": "Команда уже обрабатывается. Обновите состояние, не повторяя действие автоматически.",
            "retryable": False,
            "status": 409,
            "requestId": f"request-idempotency-{uuid.uuid4().hex[:16]}",
            "details": {
                "detailCode": "idempotency_in_progress",
                "state": state,
                "recoveryRequired": True,
                "operatorRecoveryRequired": state == "dispatch_unknown",
                "automaticRetryAllowed": False,
                "newUserActionRequired": False,
                "freshConfirmationRequired": False,
            },
        },
        status_code=HTTPStatus.CONFLICT,
        media_type=media_type,
    )


def _idempotency_journal_full(
    view: HomeAssistantView,
    *,
    media_type: str,
) -> Any:
    """Fail before dispatch while retained dangerous replays fill the journal."""

    return _negotiated_json(
        view,
        api_error_payload(
            "unavailable", request_id=f"request-idempotency-full-{uuid.uuid4().hex[:16]}"
        ),
        status_code=api_error_status("unavailable"),
        media_type=media_type,
    )


def _idempotency_conflict(
    view: HomeAssistantView,
    *,
    expected_hash: str | None,
    request_hash: str,
    media_type: str,
) -> Any:
    return _negotiated_json(
        view,
        {
            "contract": {"name": "hausman-hub-error", "version": 1},
            "code": "conflict",
            "category": "conflict",
            "message": "Ключ команды уже использован для другого запроса. Запустите действие заново вручную.",
            "retryable": False,
            "status": 409,
            "requestId": f"request-idempotency-conflict-{uuid.uuid4().hex[:12]}",
            "details": {
                "detailCode": "idempotency_key_conflict",
                "expectedHash": f"sha256:{expected_hash or '0' * 64}",
                "requestHash": f"sha256:{request_hash}",
                "recoveryRequired": False,
                "automaticRetryAllowed": False,
                "newUserActionRequired": True,
                "freshConfirmationRequired": True,
            },
        },
        status_code=HTTPStatus.CONFLICT,
        media_type=media_type,
    )
