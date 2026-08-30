"""Authenticated local device-action boundary for the HausmanHub tablet."""

from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
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
        context = await service.async_resolve_device_action_context(
            target_id, action_id
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
        intercom_action = await service.async_is_intercom_action(
            target_id, action_id
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
            except RuntimeError as error:
                if str(error) == "dangerous action idempotency store is full":
                    return _idempotency_journal_full(self)
                return _coordinator_unavailable(self)
            if existing_reassert.outcome == "conflict":
                return _idempotency_conflict(
                    self,
                    expected_hash=existing_reassert.existing_fingerprint,
                    request_hash=fingerprint,
                )
            if existing_reassert.outcome == "in_progress":
                return _idempotency_in_progress(self, existing_reassert.state)
            if (
                existing_reassert.outcome == "replay"
                and existing_reassert.receipt is not None
            ):
                return self.json(
                    existing_reassert.receipt,
                    headers=_response_headers(
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
        intercom_release_prepared = False
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
            try:
                reservation = await idempotency.async_reserve(
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
            except RuntimeError as error:
                if str(error) == "dangerous action idempotency store is full":
                    return _idempotency_journal_full(self)
                return _coordinator_unavailable(self)
            if reservation.outcome == "conflict":
                return _idempotency_conflict(
                    self,
                    expected_hash=reservation.existing_fingerprint,
                    request_hash=fingerprint,
                )
            if reservation.outcome == "in_progress":
                return _idempotency_in_progress(self, reservation.state)
            if reservation.outcome == "replay" and reservation.receipt is not None:
                return self.json(
                    reservation.receipt,
                    headers=_response_headers(
                        reservation.response_media_type or response_media_type
                    ),
                )
            await idempotency.async_mark_pending(idempotency_key)

        release_seconds = None
        if intercom_action and not dry_run:
            try:
                release_seconds = await service.async_prepare_intercom_release(
                    target_id,
                    action_id,
                    correlation_id=correlation_id,
                    request_id=f"{dispatch_request_id}.release",
                    expected_entity_id=entity_id,
                )
            except Exception:
                return _coordinator_unavailable(self)
            if release_seconds is None:
                return _coordinator_unavailable(self)
            intercom_release_prepared = True
        if coordination_key is not None and isinstance(
            idempotency, DangerousActionIdempotency
        ):
            try:
                await idempotency.async_mark_dispatching(coordination_key)
            except Exception:
                if intercom_release_prepared:
                    await service.async_cancel_intercom_release(
                        target_id,
                        expected_entity_id=entity_id,
                        expected_request_id=f"{dispatch_request_id}.release",
                    )
                return _coordinator_unavailable(self)

        climate_runtime = self._hass.data.get(DOMAIN, {}).get("climate_runtime")
        mode_writer = getattr(
            climate_runtime, "async_set_device_mode_for_entity", None
        )
        try:
            climate_entity_id = None
            if not dry_run and action_id == "turn_off" and callable(mode_writer):
                resolved = await service.async_resolve_device_action(
                    target_id, action_id
                )
                if resolved is not None and resolved[1] == "climate":
                    climate_entity_id = resolved[0]
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
            if intercom_action and not dry_run:
                execute_options["intercom_release_required"] = True
            result = await service.async_execute_device_action(
                target_id,
                action_id,
                payload.get("value"),
                **execute_options,
            )
        except Exception:
            if intercom_release_prepared:
                await service.async_cancel_intercom_release(
                    target_id,
                    expected_entity_id=entity_id,
                    expected_request_id=f"{dispatch_request_id}.release",
                )
            raise
        if result.get("accepted") is True and climate_entity_id is not None:
            climate_mode_change = await mode_writer(climate_entity_id, "automatic")
            result = {
                **result,
                "climateMode": climate_mode_change["mode"],
                "climateModeName": "Автоматический режим",
            }
        if intercom_release_prepared and result.get("accepted") is not True:
            # Prepare is durable before dispatch. A normal failed receipt means
            # the executor did not cross the physical dispatch boundary, so
            # the unarmed obligation must not block the next command.
            await service.async_cancel_intercom_release(
                target_id,
                expected_entity_id=entity_id,
                expected_request_id=f"{dispatch_request_id}.release",
            )
            release_seconds = None
            intercom_release_prepared = False
        if result.get("accepted") is True:
            if dry_run and intercom_action:
                service.publish_intercom_dry_run(
                    target_id=target_id,
                    correlation_id=correlation_id,
                    request_id=str(result.get("requestId")),
                )
            elif intercom_action and release_seconds is None:
                release_seconds = await service.async_schedule_intercom_release(
                    target_id,
                    action_id,
                    correlation_id=correlation_id,
                    request_id=str(result.get("requestId")),
                )
        response: dict[str, object] = {
            "contract": {
                "name": "hausman-hub-device-action-receipt",
                "version": 1,
            },
            **result,
            "targetType": target_type,
        }
        if full_response:
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
        if release_seconds is not None:
            response["autoReleaseSeconds"] = release_seconds
            response["releaseReceiptPending"] = True
        if dry_run:
            response["dryRun"] = True
        if coordination_key is not None and isinstance(
            idempotency, DangerousActionIdempotency
        ):
            await idempotency.async_complete(coordination_key, response)
        publish_command_receipt(self._hass, response, operation="device_action")
        return self.json(
            response,
            status_code=(
                HTTPStatus.OK
                if result.get("accepted") is True
                else HTTPStatus.CONFLICT
            ),
            headers=_response_headers(response_media_type),
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
        contexts = []
        for item in normalized:
            contexts.append(
                await service.async_resolve_device_action_context(
                    str(item["targetId"]), str(item["actionId"])
                )
            )
        intercom_flags = [
            await service.async_is_intercom_action(
                str(item["targetId"]), str(item["actionId"])
            )
            for item in normalized
        ]
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
        physical_full = full_request and any(
            item.get("dryRun") is not True for item in normalized
        )
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
            except RuntimeError as error:
                if str(error) == "dangerous action idempotency store is full":
                    return _idempotency_journal_full(self)
                return _coordinator_unavailable(self)
            if existing_reassert.outcome == "conflict":
                return _idempotency_conflict(
                    self,
                    expected_hash=existing_reassert.existing_fingerprint,
                    request_hash=fingerprint,
                )
            if existing_reassert.outcome == "in_progress":
                return _idempotency_in_progress(self, existing_reassert.state)
            if (
                existing_reassert.outcome == "replay"
                and existing_reassert.receipt is not None
            ):
                return self.json(
                    existing_reassert.receipt,
                    headers=_response_headers(
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
        intercom_release_prepared = False
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
            try:
                reservation = await idempotency.async_reserve(
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
            except RuntimeError as error:
                if str(error) == "dangerous action idempotency store is full":
                    return _idempotency_journal_full(self)
                return _coordinator_unavailable(self)
            if reservation.outcome == "conflict":
                return _idempotency_conflict(
                    self,
                    expected_hash=reservation.existing_fingerprint,
                    request_hash=fingerprint,
                )
            if reservation.outcome == "in_progress":
                return _idempotency_in_progress(self, reservation.state)
            if reservation.outcome == "replay" and reservation.receipt is not None:
                return self.json(
                    reservation.receipt,
                    headers=_response_headers(
                        reservation.response_media_type or response_media_type
                    ),
                )
            await idempotency.async_mark_pending(idempotency_key)

        if dangerous_indexes:
            index = dangerous_indexes[0]
            if intercom_flags[index]:
                intercom_release_index = index
                item = normalized[index]
                try:
                    prepared = await service.async_prepare_intercom_release(
                        str(item["targetId"]),
                        str(item["actionId"]),
                        correlation_id=correlation_id,
                        request_id=f"{dispatch_request_ids[index]}.release",
                        expected_entity_id=(
                            contexts[index][0] if contexts[index] is not None else None
                        ),
                    )
                except Exception:
                    return _coordinator_unavailable(self)
                if prepared is None:
                    return _coordinator_unavailable(self)
                intercom_release_prepared = True
        if idempotency_key is not None and isinstance(
            idempotency, DangerousActionIdempotency
        ):
            try:
                await idempotency.async_mark_dispatching(idempotency_key)
            except Exception:
                if intercom_release_prepared and intercom_release_index is not None:
                    await service.async_cancel_intercom_release(
                        str(normalized[intercom_release_index]["targetId"]),
                        expected_entity_id=contexts[intercom_release_index][0],
                        expected_request_id=(
                            f"{dispatch_request_ids[intercom_release_index]}.release"
                        ),
                    )
                return _coordinator_unavailable(self)

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
        try:
            receipts = await service.async_execute_device_action_batch(
                normalized,
                **batch_options,
            )
        except Exception:
            if intercom_release_prepared and intercom_release_index is not None:
                await service.async_cancel_intercom_release(
                    str(normalized[intercom_release_index]["targetId"]),
                    expected_entity_id=contexts[intercom_release_index][0],
                    expected_request_id=(
                        f"{dispatch_request_ids[intercom_release_index]}.release"
                    ),
                )
            raise
        if intercom_release_prepared and intercom_release_index is not None:
            release_receipt = receipts[intercom_release_index]
            if release_receipt.get("accepted") is not True:
                await service.async_cancel_intercom_release(
                    str(normalized[intercom_release_index]["targetId"]),
                    expected_entity_id=contexts[intercom_release_index][0],
                    expected_request_id=(
                        f"{dispatch_request_ids[intercom_release_index]}.release"
                    ),
                )
                intercom_release_prepared = False
        if full_response:
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
        if idempotency_key is not None and isinstance(
            idempotency, DangerousActionIdempotency
        ):
            await idempotency.async_complete(
                idempotency_key,
                response,
                item_journal=[dict(item) for item in wrapped],
            )
        return self.json(
            response,
            headers=_response_headers(response_media_type),
        )


def _response_headers(media_type: str) -> dict[str, str]:
    return {**NO_STORE_HEADERS, "Content-Type": media_type}


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


def _idempotency_in_progress(view: HomeAssistantView, state: str) -> Any:
    return view.json(
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
        headers=NO_STORE_HEADERS,
    )


def _idempotency_journal_full(view: HomeAssistantView) -> Any:
    """Fail before dispatch while retained dangerous replays fill the journal."""

    return view.json(
        api_error_payload(
            "unavailable", request_id=f"request-idempotency-full-{uuid.uuid4().hex[:16]}"
        ),
        status_code=api_error_status("unavailable"),
        headers=NO_STORE_HEADERS,
    )


def _idempotency_conflict(
    view: HomeAssistantView,
    *,
    expected_hash: str | None,
    request_hash: str,
) -> Any:
    return view.json(
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
        headers=NO_STORE_HEADERS,
    )
