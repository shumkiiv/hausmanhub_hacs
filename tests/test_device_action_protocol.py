"""Strict and restart-safe device-action protocol gates."""

from __future__ import annotations

import asyncio
import json

import pytest

from custom_components.hausman_hub.application.device_action_idempotency import (
    MAX_DANGEROUS_IDEMPOTENCY_RECORDS,
    DangerousActionIdempotency,
)
from custom_components.hausman_hub.application.device_action_protocol import (
    FULL_SINGLE_REQUEST_MEDIA_TYPE,
    FULL_SINGLE_RESPONSE_MEDIA_TYPE,
    LEGACY_REQUEST_MEDIA_TYPE,
    StrictJsonError,
    negotiated_response_media_type,
    strict_request_json,
    validate_batch_request,
    validate_single_request,
)


class RawRequest:
    def __init__(
        self,
        raw: bytes,
        *,
        content_type: str = LEGACY_REQUEST_MEDIA_TYPE,
        accept: str | None = None,
    ) -> None:
        self._raw = raw
        self.content_length = len(raw)
        self.content_type = content_type
        self.headers = {} if accept is None else {"Accept": accept}

    async def read(self) -> bytes:
        return self._raw


class MemoryStore:
    def __init__(self, payload: object | None = None) -> None:
        self.payload = payload

    async def async_load(self) -> object | None:
        return self.payload

    async def async_save(self, payload: dict[str, object]) -> None:
        self.payload = json.loads(json.dumps(payload))


class FailingSaveStore(MemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.fail_next = False

    async def async_save(self, payload: dict[str, object]) -> None:
        if self.fail_next:
            self.fail_next = False
            raise OSError("store unavailable")
        await super().async_save(payload)


def _binding(
    *,
    correlation_id: str,
    request_id: str,
    action_index: int = 0,
    target_id: str = "door",
    target_type: str = "lock",
    action_id: str = "unlock",
) -> dict[str, object]:
    return {
        "actionIndex": action_index,
        "targetId": target_id,
        "targetType": target_type,
        "actionId": action_id,
        "correlationId": correlation_id,
        "requestId": request_id,
    }


@pytest.mark.parametrize(
    "raw",
    [
        b'{"targetId":"light","targetId":"other","actionId":"turn_on"}',
        b'{"targetId":"light","actionId":"set_value","value":NaN}',
        b'{"targetId":"light","actionId":"set_value","value":Infinity}',
    ],
)
def test_strict_json_rejects_duplicate_keys_and_nonfinite_numbers(raw: bytes) -> None:
    with pytest.raises(StrictJsonError):
        asyncio.run(
            strict_request_json(
                RawRequest(raw),
                allowed_media_types=frozenset({LEGACY_REQUEST_MEDIA_TYPE}),
            )
        )


def test_accept_negotiation_preserves_legacy_and_requires_supported_media() -> None:
    assert (
        negotiated_response_media_type(RawRequest(b"{}"), batch=False)
        == LEGACY_REQUEST_MEDIA_TYPE
    )
    assert (
        negotiated_response_media_type(
            RawRequest(b"{}", accept=FULL_SINGLE_RESPONSE_MEDIA_TYPE),
            batch=False,
        )
        == FULL_SINGLE_RESPONSE_MEDIA_TYPE
    )
    assert (
        negotiated_response_media_type(
            RawRequest(b"{}", accept="application/xml"), batch=False
        )
        is None
    )


def test_dangerous_actions_require_full_confirmation_and_one_batch_item() -> None:
    legacy = {"targetId": "door", "actionId": "unlock"}
    assert validate_single_request(legacy, full=False) == legacy
    with pytest.raises(StrictJsonError):
        validate_single_request(legacy, full=True)
    full = {
        **legacy,
        "correlationId": "door.unlock.1",
        "requestId": "door.unlock.request.1",
        "confirmedByUser": True,
        "idempotencyKey": "door.unlock.1",
    }
    assert validate_single_request(full, full=True) == full
    dangerous_item = {
        "targetId": "door",
        "actionId": "unlock",
        "confirmedByUser": True,
        "idempotencyKey": "door.unlock.1",
    }
    with pytest.raises(StrictJsonError):
        validate_batch_request(
            {
                "contract": {
                    "name": "hausman-hub-device-action-batch-request",
                    "version": 1,
                },
                "correlationId": "dangerous.batch.1",
                "requestId": "dangerous.batch.request.1",
                "actions": [
                    dangerous_item,
                    dangerous_item
                    | {
                        "targetId": "valve",
                        "actionId": "open_valve",
                        "idempotencyKey": "valve.open.1",
                    },
                ],
            },
            full=True,
        )


def test_physical_full_requests_require_top_level_request_id() -> None:
    single = {
        "targetId": "light",
        "actionId": "turn_on",
        "correlationId": "single.physical.1",
    }
    with pytest.raises(StrictJsonError, match="requestId"):
        validate_single_request(single, full=True)
    assert validate_single_request(
        single | {"requestId": "single.physical.request.1"}, full=True
    ) == single | {"requestId": "single.physical.request.1"}

    batch = {
        "contract": {
            "name": "hausman-hub-device-action-batch-request",
            "version": 1,
        },
        "correlationId": "batch.physical.1",
        "actions": [{"targetId": "light", "actionId": "turn_on"}],
    }
    with pytest.raises(StrictJsonError, match="requestId"):
        validate_batch_request(batch, full=True)
    assert validate_batch_request(
        batch | {"requestId": "batch.physical.request.1"}, full=True
    ) == batch["actions"]

    assert validate_single_request(
        {"targetId": "light", "actionId": "turn_on", "dryRun": True},
        full=True,
    )
    assert validate_batch_request(
        {
            **batch,
            "actions": [
                {"targetId": "light", "actionId": "turn_on", "dryRun": True}
            ],
        },
        full=True,
    )


def test_restart_converts_uncertain_dispatch_to_no_redispatch_conflict() -> None:
    store = MemoryStore()
    first = DangerousActionIdempotency(store)
    asyncio.run(first.async_load())
    reserved = asyncio.run(
        first.async_reserve(
            key="dangerous.1",
            fingerprint="a" * 64,
            dispatch_id="dispatch-1",
            bindings=[
                _binding(
                    correlation_id="dangerous.1",
                    request_id="dispatch.dispatch-1",
                )
            ],
        )
    )
    assert reserved.outcome == "reserved"
    asyncio.run(first.async_mark_pending("dangerous.1"))
    asyncio.run(first.async_mark_dispatching("dangerous.1"))

    restarted = DangerousActionIdempotency(store)
    asyncio.run(restarted.async_load())
    replay = asyncio.run(
        restarted.async_reserve(
            key="dangerous.1",
            fingerprint="a" * 64,
            dispatch_id="dispatch-2",
            bindings=[
                _binding(
                    correlation_id="dangerous.1",
                    request_id="dispatch.dispatch-2",
                )
            ],
        )
    )
    assert replay.outcome == "in_progress"
    assert replay.state == "dispatch_unknown"


def test_immediate_replay_of_dispatching_record_requires_operator_recovery() -> None:
    store = MemoryStore()
    coordinator = DangerousActionIdempotency(store)
    asyncio.run(coordinator.async_load())
    asyncio.run(
        coordinator.async_reserve(
            key="dangerous.immediate",
            fingerprint="b" * 64,
            dispatch_id="dispatch-immediate",
            bindings=[
                _binding(
                    correlation_id="dangerous.immediate",
                    request_id="dispatch.dispatch-immediate",
                )
            ],
        )
    )
    asyncio.run(coordinator.async_mark_pending("dangerous.immediate"))
    asyncio.run(coordinator.async_mark_dispatching("dangerous.immediate"))

    replay = asyncio.run(
        coordinator.async_lookup(
            key="dangerous.immediate", fingerprint="b" * 64
        )
    )

    assert replay.outcome == "in_progress"
    assert replay.state == "dispatch_unknown"


def test_corrupt_completed_record_fails_closed_without_redispatch() -> None:
    store = MemoryStore(
        {
            "version": 1,
            "records": [
                {
                    "key": "dangerous.corrupt",
                    "hash": "a" * 64,
                    "state": "completed",
                    "receipt": None,
                    "itemJournal": [],
                    "dispatchPhase": "dispatched",
                    "dispatchId": "dispatch-corrupt",
                    "dispatchBindings": [],
                    "responseMediaType": FULL_SINGLE_RESPONSE_MEDIA_TYPE,
                    "updatedAt": 1,
                }
            ],
        }
    )
    coordinator = DangerousActionIdempotency(store)
    asyncio.run(coordinator.async_load())
    with pytest.raises(RuntimeError, match="corrupt"):
        asyncio.run(
            coordinator.async_reserve(
                key="dangerous.corrupt",
                fingerprint="a" * 64,
                dispatch_id="dispatch-new",
                bindings=[
                    _binding(
                        correlation_id="dangerous.corrupt",
                        request_id="dispatch.dispatch-new",
                    )
                ],
            )
        )


@pytest.mark.parametrize("payload", [{}, [], {"version": 99, "records": []}])
def test_existing_unknown_store_shape_fails_closed(payload: object) -> None:
    coordinator = DangerousActionIdempotency(MemoryStore(payload))
    asyncio.run(coordinator.async_load())
    with pytest.raises(RuntimeError, match="corrupt"):
        asyncio.run(
            coordinator.async_reserve(
                key="dangerous.blocked",
                fingerprint="c" * 64,
                dispatch_id="dispatch-blocked",
                bindings=[
                    _binding(
                        correlation_id="dangerous.overflow",
                        request_id="dispatch.dispatch-overflow",
                    )
                ],
            )
        )


def test_full_pending_store_rejects_before_mutating_memory() -> None:
    store = MemoryStore()
    coordinator = DangerousActionIdempotency(store)
    asyncio.run(coordinator.async_load())
    for index in range(MAX_DANGEROUS_IDEMPOTENCY_RECORDS):
        asyncio.run(
            coordinator.async_reserve(
                key=f"dangerous.{index}",
                fingerprint=f"{index:064x}",
                dispatch_id=f"dispatch-{index}",
                bindings=[
                    _binding(
                        correlation_id=f"dangerous.{index}",
                        request_id=f"dispatch.dispatch-{index}",
                    )
                ],
            )
        )
    with pytest.raises(RuntimeError, match="full"):
        asyncio.run(
            coordinator.async_reserve(
                key="dangerous.overflow",
                fingerprint="f" * 64,
                dispatch_id="dispatch-overflow",
                bindings=[
                    _binding(
                        correlation_id="dangerous.overflow",
                        request_id="dispatch.dispatch-overflow",
                    )
                ],
            )
        )
    assert "dangerous.overflow" not in coordinator._records


def test_full_journal_keeps_completed_replay_in_memory() -> None:
    async def exercise() -> None:
        store = FailingSaveStore()
        coordinator = DangerousActionIdempotency(store)
        await coordinator.async_load()
        for index in range(MAX_DANGEROUS_IDEMPOTENCY_RECORDS):
            key = f"dangerous.completed.{index}"
            request_id = f"request-completed-{index}"
            await coordinator.async_reserve(
                key=key,
                fingerprint=f"{index:064x}",
                dispatch_id=f"dispatch-completed-{index}",
                bindings=[
                    _binding(
                        correlation_id=key,
                        request_id=request_id,
                    )
                ],
                response_media_type=FULL_SINGLE_RESPONSE_MEDIA_TYPE,
            )
            await coordinator.async_complete(
                key,
                {
                    "contract": {
                        "name": "hausman-hub-device-action-receipt",
                        "version": 1,
                    },
                    "correlationId": key,
                    "requestId": request_id,
                    "targetId": "door",
                    "targetType": "lock",
                    "actionId": "unlock",
                    "accepted": True,
                    "confirmed": True,
                    "status": "confirmed",
                },
            )

        with pytest.raises(RuntimeError, match="store is full"):
            await coordinator.async_reserve(
                key="dangerous.new",
                fingerprint="f" * 64,
                dispatch_id="dispatch-new",
                bindings=[
                    _binding(
                        correlation_id="dangerous.new",
                        request_id="request-new",
                    )
                ],
            )

        replay = await coordinator.async_lookup(
            key="dangerous.completed.0",
            fingerprint=f"{0:064x}",
        )
        assert replay.outcome == "replay"
        assert "dangerous.new" not in coordinator._records

    asyncio.run(exercise())


def test_replay_preserves_original_response_media_type() -> None:
    store = MemoryStore()
    coordinator = DangerousActionIdempotency(store)
    asyncio.run(coordinator.async_load())
    asyncio.run(
        coordinator.async_reserve(
            key="dangerous.media",
            fingerprint="b" * 64,
            dispatch_id="dispatch-media",
            bindings=[
                _binding(
                    correlation_id="dangerous.media",
                    request_id="request-dangerous-media",
                )
            ],
            response_media_type=FULL_SINGLE_RESPONSE_MEDIA_TYPE,
        )
    )
    asyncio.run(coordinator.async_mark_pending("dangerous.media"))
    asyncio.run(coordinator.async_mark_dispatching("dangerous.media"))
    receipt = {
        "contract": {
            "name": "hausman-hub-device-action-receipt",
            "version": 1,
        },
        "correlationId": "dangerous.media",
        "requestId": "request-dangerous-media",
        "targetId": "door",
        "targetType": "lock",
        "actionId": "unlock",
        "accepted": True,
        "confirmed": True,
        "status": "confirmed",
    }
    asyncio.run(coordinator.async_complete("dangerous.media", receipt))
    coordinator = DangerousActionIdempotency(store)
    asyncio.run(coordinator.async_load())
    replay = asyncio.run(
        coordinator.async_reserve(
            key="dangerous.media",
            fingerprint="b" * 64,
            dispatch_id="dispatch-media-retry",
            bindings=[
                _binding(
                    correlation_id="dangerous.media",
                    request_id="request-dangerous-media-retry",
                )
            ],
            response_media_type=LEGACY_REQUEST_MEDIA_TYPE,
        )
    )
    assert replay.outcome == "replay"
    assert replay.response_media_type == FULL_SINGLE_RESPONSE_MEDIA_TYPE


@pytest.mark.parametrize(
    "field,value",
    [
        ("targetId", "other-door"),
        ("targetType", "switch"),
        ("actionId", "lock"),
        ("correlationId", "another-correlation"),
        ("requestId", "another-request"),
    ],
)
def test_completion_rejects_receipt_for_another_reserved_action(
    field: str, value: object
) -> None:
    coordinator = DangerousActionIdempotency(MemoryStore())
    asyncio.run(coordinator.async_load())
    asyncio.run(
        coordinator.async_reserve(
            key="dangerous.binding",
            fingerprint="e" * 64,
            dispatch_id="dispatch-binding",
            bindings=[
                _binding(
                    correlation_id="dangerous.binding",
                    request_id="request-binding",
                )
            ],
            response_media_type=FULL_SINGLE_RESPONSE_MEDIA_TYPE,
        )
    )
    receipt = {
        "contract": {
            "name": "hausman-hub-device-action-receipt",
            "version": 1,
        },
        "correlationId": "dangerous.binding",
        "requestId": "request-binding",
        "targetId": "door",
        "targetType": "lock",
        "actionId": "unlock",
        "accepted": True,
        "confirmed": True,
        "status": "confirmed",
    }
    receipt[field] = value

    with pytest.raises(RuntimeError, match="receipt is invalid"):
        asyncio.run(coordinator.async_complete("dangerous.binding", receipt))


def test_batch_completion_rejects_mismatched_counts_and_item_journal() -> None:
    bindings = [
        _binding(
            correlation_id="dangerous.batch",
            request_id=f"request-{index}",
            action_index=index,
            target_id=f"target-{index}",
            target_type="switch",
            action_id="turn_on",
        )
        for index in range(2)
    ]
    items = [
        {
            "contract": {
                "name": "hausman-hub-device-action-receipt",
                "version": 1,
            },
            "correlationId": "dangerous.batch",
            "requestId": f"request-{index}",
            "targetId": binding["targetId"],
            "targetType": binding["targetType"],
            "actionId": binding["actionId"],
            "actionIndex": index,
            "accepted": True,
            "confirmed": True,
            "status": "confirmed",
        }
        for index, binding in enumerate(bindings)
    ]
    response = {
        "contract": {
            "name": "hausman-hub-device-action-batch-receipt",
            "version": 1,
        },
        "correlationId": "dangerous.batch",
        "status": "confirmed",
        "total": 2,
        "acceptedCount": 2,
        "confirmedCount": 2,
        "failedCount": 0,
        "receipts": items,
    }

    async def make_coordinator() -> DangerousActionIdempotency:
        result = DangerousActionIdempotency(MemoryStore())
        await result.async_load()
        await result.async_reserve(
            key="dangerous.batch",
            fingerprint="f" * 64,
            dispatch_id="dispatch-batch",
            bindings=bindings,
            response_media_type=(
                "application/vnd.hausmanhub.device-action-batch-receipt.full+json"
            ),
        )
        return result

    count_mismatch = asyncio.run(make_coordinator())
    invalid_counts = dict(response)
    invalid_counts["confirmedCount"] = 1
    with pytest.raises(RuntimeError, match="receipt is invalid"):
        asyncio.run(
            count_mismatch.async_complete(
                "dangerous.batch", invalid_counts, item_journal=items
            )
        )

    journal_mismatch = asyncio.run(make_coordinator())
    wrong_journal = [dict(item) for item in items]
    wrong_journal[1]["requestId"] = "request-substituted"
    with pytest.raises(RuntimeError, match="item journal is invalid"):
        asyncio.run(
            journal_mismatch.async_complete(
                "dangerous.batch", response, item_journal=wrong_journal
            )
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("responseMediaType", "text/html"),
        ("dispatchBindings", [{"targetId": "door", "targetType": "lock"}]),
        ("itemJournal", [{"accepted": True}]),
    ],
)
def test_persisted_record_rejects_untrusted_replay_metadata(
    field: str, value: object
) -> None:
    record = {
        "key": "dangerous.metadata",
        "hash": "d" * 64,
        "state": "reserved",
        "receipt": None,
        "itemJournal": [],
        "dispatchPhase": "not_started",
        "dispatchId": "dispatch-metadata",
        "dispatchBindings": [
            {
                "actionIndex": 0,
                "targetId": "door",
                "targetType": "lock",
                "actionId": "unlock",
                "dispatchId": "dispatch-metadata",
                "correlationId": "dangerous.metadata",
                "requestId": "request-metadata",
            }
        ],
        "responseMediaType": FULL_SINGLE_RESPONSE_MEDIA_TYPE,
        "updatedAt": 1,
    }
    record[field] = value
    coordinator = DangerousActionIdempotency(
        MemoryStore({"version": 1, "records": [record]})
    )
    asyncio.run(coordinator.async_load())
    with pytest.raises(RuntimeError, match="corrupt"):
        asyncio.run(
            coordinator.async_lookup(
                key="dangerous.metadata", fingerprint="d" * 64
            )
        )
