import asyncio
import hashlib
import json
from pathlib import Path
import struct

import pytest

from custom_components.hausman_hub.application.operation_journal import OperationJournalService
from custom_components.hausman_hub.application.operation_journal_admin import (
    JournalArchiveError,
    OperationJournalArchiveService,
    canonical_json,
)


class Store:
    def __init__(self): self.payload = None
    async def async_load(self): return self.payload
    async def async_save(self, payload): self.payload = payload


class FailingStore(Store):
    def __init__(self):
        super().__init__()
        self.fail_next = False

    async def async_save(self, payload):
        if self.fail_next:
            self.fail_next = False
            raise OSError("simulated durable storage failure")
        await super().async_save(payload)


class JournalStore(Store):
    pass


class SimulatedCrash(BaseException):
    """Stop a transaction after a selected durable write."""


class ControlledStore(Store):
    def __init__(self):
        super().__init__()
        self.save_calls = 0
        self.crash_after: set[int] = set()
        self.fail_before: set[int] = set()
        self.fail_after: set[int] = set()

    async def async_save(self, payload):
        self.save_calls += 1
        if self.save_calls in self.fail_before:
            raise OSError("simulated durable storage failure")
        self.payload = json.loads(json.dumps(payload))
        if self.save_calls in self.crash_after:
            raise SimulatedCrash
        if self.save_calls in self.fail_after:
            raise OSError("simulated ambiguous durable storage failure")


class Keyring:
    active_key_id = "test-key"
    keys = {active_key_id: bytes.fromhex("42" * 32)}
    active_key = keys[active_key_id]
    backup_separated = True

    def key_for(self, key_id):
        return self.keys.get(key_id)


def archive_service(journal, store, *, now_ms=lambda: 100):
    return OperationJournalArchiveService(
        journal, store, keyring=Keyring(), now_ms=now_ms
    )


def receipt(correlation):
    return {"correlation_id": correlation, "operation": "device_action", "accepted": True,
            "confirmed": True, "status": "confirmed", "reason": None, "error_code": None}


def test_readme_does_not_claim_haos_media_key_protects_archive_token():
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(
        encoding="utf-8"
    )
    normalized = " ".join(readme.split())
    assert "Полная резервная копия Home Assistant включает и `config`, и `media`" in normalized
    assert "создание архива для сброса закрывается безопасно" in normalized
    assert "вне `config`, `share`, `addons`, `ssl`, `media`" in normalized


@pytest.mark.asyncio
async def test_archive_then_reset_is_durable_and_command_free():
    journal_store = JournalStore()
    journal = OperationJournalService(journal_store, now_ms=lambda: 100)
    await journal.async_append(receipt("r1"))
    archive_store = Store()
    service = archive_service(journal, archive_store)
    archived = await service.async_archive("admin")
    result = await service.async_reset("admin", archived["archiveToken"], archived["snapshot"]["generation"],
                                      archived["snapshot"]["sequence"], archived["snapshot"]["revision"])
    assert result["result"] == "reset"
    assert result["physicalCommandsSent"] is False
    assert journal.snapshot()["records"] == []
    restored = archive_service(journal, archive_store)
    await restored.async_load()
    with pytest.raises(JournalArchiveError, match="reset_archive_token_invalid"):
        await restored.async_reset("admin", archived["archiveToken"], 1, 1, "0" * 64)


@pytest.mark.asyncio
async def test_archive_repeated_for_same_cas_returns_same_token():
    journal = OperationJournalService(JournalStore(), now_ms=lambda: 100)
    service = archive_service(journal, Store())
    first = await service.async_archive("admin")
    second = await service.async_archive("admin")
    assert second == first


@pytest.mark.asyncio
async def test_reset_cas_conflict_preserves_archive_and_token():
    journal = OperationJournalService(JournalStore(), now_ms=lambda: 100)
    await journal.async_append(receipt("r1"))
    service = archive_service(journal, Store())
    archive = await service.async_archive("admin")
    await journal.async_append(receipt("r2"))
    with pytest.raises(JournalArchiveError, match="reset_precondition_conflict"):
        await service.async_reset("admin", archive["archiveToken"], archive["snapshot"]["generation"],
                                  archive["snapshot"]["sequence"], archive["snapshot"]["revision"])
    assert service._archives[archive["archivedSnapshotId"]]["consumed"] is False


def test_canonical_json_uses_rfc8785_number_and_key_rules():
    """JCS values must match ECMAScript serialization at binary64 boundaries."""

    assert canonical_json({"z": 1.0, "a": 1e-7, "m": 1e-6}) == (
        '{"a":1e-7,"m":0.000001,"z":1}'
    )
    assert canonical_json({"n": -0.0, "wide": 1e20, "huge": 1e21}) == (
        '{"huge":1e+21,"n":0,"wide":100000000000000000000}'
    )


@pytest.mark.parametrize(
    ("binary64", "expected"),
    [
        ("0000000000000000", "0"),
        ("8000000000000000", "0"),
        ("0000000000000001", "5e-324"),
        ("8000000000000001", "-5e-324"),
        ("7fefffffffffffff", "1.7976931348623157e+308"),
        ("ffefffffffffffff", "-1.7976931348623157e+308"),
        ("4340000000000000", "9007199254740992"),
        ("c340000000000000", "-9007199254740992"),
        ("4430000000000000", "295147905179352830000"),
        ("44b52d02c7e14af5", "9.999999999999997e+22"),
        ("44b52d02c7e14af6", "1e+23"),
        ("44b52d02c7e14af7", "1.0000000000000001e+23"),
        ("444b1ae4d6e2ef4e", "999999999999999700000"),
        ("444b1ae4d6e2ef4f", "999999999999999900000"),
        ("444b1ae4d6e2ef50", "1e+21"),
        ("3eb0c6f7a0b5ed8c", "9.999999999999997e-7"),
        ("3eb0c6f7a0b5ed8d", "0.000001"),
        ("41b3de4355555553", "333333333.3333332"),
        ("41b3de4355555554", "333333333.33333325"),
        ("41b3de4355555555", "333333333.3333333"),
        ("41b3de4355555556", "333333333.3333334"),
        ("41b3de4355555557", "333333333.33333343"),
        ("becbf647612f3696", "-0.0000033333333333333333"),
        ("43143ff3c1cb0959", "1424953923781206.2"),
    ],
)
def test_canonical_json_matches_rfc8785_appendix_b_numbers(binary64, expected):
    value = struct.unpack(">d", bytes.fromhex(binary64))[0]
    assert canonical_json(value) == expected


@pytest.mark.parametrize("binary64", ["7ff8000000000000", "7ff0000000000000", "fff0000000000000"])
def test_canonical_json_rejects_non_finite_rfc8785_numbers(binary64):
    value = struct.unpack(">d", bytes.fromhex(binary64))[0]
    with pytest.raises(ValueError, match="not finite"):
        canonical_json(value)


def test_canonical_json_matches_rfc8785_object_vector_and_rejects_surrogates():
    value = {
        "numbers": [333333333.33333329, 1e30, 4.50, 2e-3, 1e-27],
        "string": "€$\u000f\nA'B\"\\\\\"/",
        "literals": [None, True, False],
    }
    assert canonical_json(value) == (
        '{"literals":[null,true,false],"numbers":[333333333.3333333,1e+30,4.5,0.002,1e-27],'
        '"string":"€$\\u000f\\nA\'B\\\"\\\\\\\\\\\"/"}'
    )
    with pytest.raises(ValueError, match="valid Unicode"):
        canonical_json("\udead")


@pytest.mark.asyncio
async def test_durable_archive_never_persists_raw_token_or_response_body():
    journal = OperationJournalService(JournalStore(), now_ms=lambda: 100)
    await journal.async_append(receipt("r1"))
    store = Store()
    archived = await archive_service(journal, store).async_archive("admin")

    assert archived["archiveToken"] not in json.dumps(store.payload, ensure_ascii=False)
    encoded = json.dumps(store.payload, ensure_ascii=False)
    assert "archiveToken" not in encoded
    assert "receipt" not in encoded
    assert store.payload["archives"][archived["archivedSnapshotId"]]["snapshot"] == archived["snapshot"]
    assert Keyring.active_key.hex() not in encoded
    assert (
        OperationJournalArchiveService._archive_encryption_key(Keyring.active_key)
        != Keyring.active_key
    )


@pytest.mark.asyncio
async def test_same_cas_returns_exact_token_after_restart_and_key_rotation():
    journal = OperationJournalService(JournalStore(), now_ms=lambda: 100)
    store = Store()
    original = await archive_service(journal, store).async_archive(
        "admin", "archive.original"
    )

    class RotatedKeyring(Keyring):
        active_key_id = "new-key"
        keys = {**Keyring.keys, active_key_id: bytes.fromhex("73" * 32)}
        active_key = keys[active_key_id]

    restored = OperationJournalArchiveService(
        journal, store, keyring=RotatedKeyring(), now_ms=lambda: 100
    )
    await restored.async_load()
    repeated = await restored.async_archive("admin", "archive.repeated")
    assert repeated == original
    assert repeated["archiveToken"] == original["archiveToken"]
    assert repeated["archivedSnapshotId"] == original["archivedSnapshotId"]
    record = store.payload["archives"][original["archivedSnapshotId"]]
    assert record["tokenKeyId"] == "new-key"
    assert record["recordAuthKeyId"] == "new-key"
    assert store.payload["storeAuthKeyId"] == "new-key"
    assert original["archiveToken"] not in json.dumps(store.payload)

    class ActiveOnlyKeyring(RotatedKeyring):
        keys = {RotatedKeyring.active_key_id: RotatedKeyring.active_key}

    second_restart = OperationJournalArchiveService(
        journal, store, keyring=ActiveOnlyKeyring(), now_ms=lambda: 60_101
    )
    await second_restart.async_load()
    after_old_key_removal = await second_restart.async_archive("admin")
    assert after_old_key_removal["archiveToken"] == original["archiveToken"]


@pytest.mark.asyncio
async def test_tampered_ciphertext_fails_closed_after_restart_without_minting_token():
    journal = OperationJournalService(JournalStore(), now_ms=lambda: 100)
    store = Store()
    original = await archive_service(journal, store).async_archive("admin")
    record = store.payload["archives"][original["archivedSnapshotId"]]
    replacement = "B" if record["tokenCiphertext"][0] == "A" else "A"
    record["tokenCiphertext"] = replacement + record["tokenCiphertext"][1:]
    tampered = json.loads(json.dumps(store.payload))

    restored = archive_service(journal, store)
    await restored.async_load()
    with pytest.raises(JournalArchiveError, match="archive_storage_unavailable"):
        await restored.async_archive("admin")

    assert restored._archives == {}
    assert restored._pending == {}
    assert store.payload == tampered


@pytest.mark.asyncio
async def test_tampered_token_digest_cannot_authorize_reset_after_restart():
    """Changing mutable Store metadata must not mint an attacker-known token."""

    journal_store = ControlledStore()
    journal = OperationJournalService(journal_store, now_ms=lambda: 100)
    await journal.async_append(receipt("r1"))
    archive_store = ControlledStore()
    service = archive_service(journal, archive_store)
    archived = await service.async_archive("admin")
    attacker_token = "A" * 43
    record = archive_store.payload["archives"][archived["archivedSnapshotId"]]
    record["tokenDigest"] = hashlib.sha256(attacker_token.encode()).hexdigest()

    restored_journal = OperationJournalService(journal_store, now_ms=lambda: 100)
    await restored_journal.async_load()
    restored = archive_service(restored_journal, archive_store)
    await restored.async_load()

    with pytest.raises(JournalArchiveError, match="archive_storage_unavailable"):
        await restored.async_reset(
            "admin",
            attacker_token,
            archived["snapshot"]["generation"],
            archived["snapshot"]["sequence"],
            archived["snapshot"]["revision"],
        )
    assert restored_journal.snapshot(limit=512)["records"] == archived["snapshot"]["records"]


@pytest.mark.asyncio
async def test_reset_reverifies_encrypted_token_and_authenticated_record_in_memory():
    journal = OperationJournalService(JournalStore(), now_ms=lambda: 100)
    await journal.async_append(receipt("r1"))
    service = archive_service(journal, Store())
    archived = await service.async_archive("admin")
    attacker_token = "A" * 43
    service._archives[archived["archivedSnapshotId"]]["tokenDigest"] = hashlib.sha256(
        attacker_token.encode()
    ).hexdigest()

    with pytest.raises(JournalArchiveError, match="archive_storage_unavailable"):
        await service.async_reset(
            "admin",
            attacker_token,
            archived["snapshot"]["generation"],
            archived["snapshot"]["sequence"],
            archived["snapshot"]["revision"],
        )
    assert journal.snapshot(limit=512)["records"] == archived["snapshot"]["records"]


@pytest.mark.asyncio
async def test_same_cas_reissue_reverifies_authenticated_archive_metadata():
    journal = OperationJournalService(JournalStore(), now_ms=lambda: 100)
    service = archive_service(journal, Store())
    archived = await service.async_archive("admin")
    service._archives[archived["archivedSnapshotId"]]["issuedAt"] = 99

    with pytest.raises(JournalArchiveError, match="archive_storage_unavailable"):
        await service.async_archive("admin")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tampered_field",
    (
        "archive_id",
        "owner",
        "snapshot_digest",
        "cas",
        "timestamps",
        "state",
        "rate_limit",
    ),
)
async def test_authenticated_archive_fields_cannot_be_rewritten_in_store(
    tampered_field,
):
    journal = OperationJournalService(JournalStore(), now_ms=lambda: 100)
    store = Store()
    archived = await archive_service(journal, store).async_archive("admin")
    archive_id = archived["archivedSnapshotId"]
    record = store.payload["archives"][archive_id]

    if tampered_field == "archive_id":
        replacement_id = f"journal.{'A' * 32}"
        store.payload["archives"] = {replacement_id: record}
        store.payload["pending"]["admin"] = replacement_id
    elif tampered_field == "owner":
        record["ownerId"] = "attacker"
        store.payload["pending"] = {"attacker": archive_id}
    elif tampered_field == "snapshot_digest":
        record["snapshot"]["generated_at"] = 101
        record["archiveSha256"] = hashlib.sha256(
            canonical_json(record["snapshot"]).encode()
        ).hexdigest()
    elif tampered_field == "cas":
        record["generation"] = 2
        record["snapshot"]["generation"] = 2
        record["archiveSha256"] = hashlib.sha256(
            canonical_json(record["snapshot"]).encode()
        ).hexdigest()
    elif tampered_field == "timestamps":
        record["issuedAt"] = 99
        record["expiresAt"] = 900_099
        record["completedAt"] = 99
    else:
        if tampered_field == "rate_limit":
            store.payload["rateBuckets"]["archive"] = {}
            restored = archive_service(journal, store)
            await restored.async_load()
            with pytest.raises(
                JournalArchiveError, match="archive_storage_unavailable"
            ):
                await restored.async_archive("admin")
            return
        transaction_id = "A" * 32
        record["tokenState"] = "reset_prepared"
        record["resetTransactionId"] = transaction_id
        record["resetRatePoint"] = 100
        store.payload["resetTransaction"] = {
            "id": transaction_id,
            "archiveId": archive_id,
            "ownerId": "admin",
            "phase": "prepared",
            "expectedGeneration": record["generation"],
            "expectedSequence": record["sequence"],
            "expectedRevision": record["revision"],
        }

    restored = archive_service(journal, store)
    await restored.async_load()

    with pytest.raises(JournalArchiveError, match="archive_storage_unavailable"):
        await restored.async_archive("admin")


@pytest.mark.asyncio
@pytest.mark.parametrize("archive_write_after_prepare", (1, 2, 3))
async def test_reset_recovers_after_crash_following_each_archive_transaction_write(
    archive_write_after_prepare,
):
    journal_store = ControlledStore()
    journal = OperationJournalService(journal_store, now_ms=lambda: 100)
    await journal.async_append(receipt("r1"))
    archive_store = ControlledStore()
    service = archive_service(journal, archive_store)
    archived = await service.async_archive("admin")
    expected = (
        archived["snapshot"]["generation"],
        archived["snapshot"]["sequence"],
        archived["snapshot"]["revision"],
    )
    archive_store.crash_after.add(
        archive_store.save_calls + archive_write_after_prepare
    )

    with pytest.raises(SimulatedCrash):
        await service.async_reset("admin", archived["archiveToken"], *expected)
    archive_store.crash_after.clear()

    restored_journal = OperationJournalService(journal_store, now_ms=lambda: 100)
    await restored_journal.async_load()
    restored = archive_service(restored_journal, archive_store)
    await restored.async_load()
    if archive_write_after_prepare == 1:
        repeated = await restored.async_archive("admin")
        assert repeated["archiveToken"] == archived["archiveToken"]
        result = await restored.async_reset("admin", archived["archiveToken"], *expected)
        assert result["physicalCommandsSent"] is False
    else:
        assert restored_journal.snapshot(limit=512)["generation"] == expected[0] + 1
        with pytest.raises(JournalArchiveError, match="reset_archive_token_invalid"):
            await restored.async_reset("admin", archived["archiveToken"], *expected)


@pytest.mark.asyncio
async def test_reset_recovers_after_crash_following_durable_journal_write():
    journal_store = ControlledStore()
    journal = OperationJournalService(journal_store, now_ms=lambda: 100)
    await journal.async_append(receipt("r1"))
    archive_store = ControlledStore()
    service = archive_service(journal, archive_store)
    archived = await service.async_archive("admin")
    expected = (
        archived["snapshot"]["generation"],
        archived["snapshot"]["sequence"],
        archived["snapshot"]["revision"],
    )
    journal_store.crash_after.add(journal_store.save_calls + 1)

    with pytest.raises(SimulatedCrash):
        await service.async_reset("admin", archived["archiveToken"], *expected)
    journal_store.crash_after.clear()

    restored_journal = OperationJournalService(journal_store, now_ms=lambda: 100)
    await restored_journal.async_load()
    restored = archive_service(restored_journal, archive_store)
    await restored.async_load()
    assert restored_journal.snapshot(limit=512)["generation"] == expected[0] + 1
    with pytest.raises(JournalArchiveError, match="reset_archive_token_invalid"):
        await restored.async_reset("admin", archived["archiveToken"], *expected)


@pytest.mark.asyncio
async def test_double_store_failure_quarantines_runtime_then_restart_recovers_token():
    journal_store = ControlledStore()
    journal = OperationJournalService(journal_store, now_ms=lambda: 100)
    await journal.async_append(receipt("r1"))
    archive_store = ControlledStore()
    service = archive_service(journal, archive_store)
    archived = await service.async_archive("admin")
    expected = (
        archived["snapshot"]["generation"],
        archived["snapshot"]["sequence"],
        archived["snapshot"]["revision"],
    )
    journal_store.fail_before.add(journal_store.save_calls + 1)
    archive_store.fail_before.add(archive_store.save_calls + 2)
    archive_calls_before = archive_store.save_calls

    with pytest.raises(JournalArchiveError, match="archive_storage_unavailable"):
        await service.async_reset("admin", archived["archiveToken"], *expected)
    with pytest.raises(JournalArchiveError, match="archive_storage_unavailable"):
        await service.async_archive("admin")
    assert archive_store.save_calls == archive_calls_before + 1
    journal_store.fail_before.clear()
    archive_store.fail_before.clear()

    restored_journal = OperationJournalService(journal_store, now_ms=lambda: 100)
    await restored_journal.async_load()
    restored = archive_service(restored_journal, archive_store)
    await restored.async_load()
    repeated = await restored.async_archive("admin")
    assert repeated["archiveToken"] == archived["archiveToken"]


@pytest.mark.asyncio
async def test_ambiguous_journal_save_is_resolved_from_durable_prepared_transaction():
    journal_store = ControlledStore()
    journal = OperationJournalService(journal_store, now_ms=lambda: 100)
    await journal.async_append(receipt("r1"))
    archive_store = ControlledStore()
    service = archive_service(journal, archive_store)
    archived = await service.async_archive("admin")
    expected = (
        archived["snapshot"]["generation"],
        archived["snapshot"]["sequence"],
        archived["snapshot"]["revision"],
    )
    journal_store.fail_after.add(journal_store.save_calls + 1)

    with pytest.raises(JournalArchiveError, match="archive_storage_unavailable"):
        await service.async_reset("admin", archived["archiveToken"], *expected)
    assert archive_store.payload["resetTransaction"]["phase"] == "prepared"
    journal_store.fail_after.clear()

    restored_journal = OperationJournalService(journal_store, now_ms=lambda: 100)
    await restored_journal.async_load()
    restored = archive_service(restored_journal, archive_store)
    await restored.async_load()

    assert restored_journal.snapshot(limit=512)["generation"] == expected[0] + 1
    with pytest.raises(JournalArchiveError, match="reset_archive_token_invalid"):
        await restored.async_reset("admin", archived["archiveToken"], *expected)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "load_error",
    (OSError("private archive path is unavailable"), RecursionError("hostile payload")),
)
async def test_archive_load_failure_is_quarantined_without_raising(load_error):
    class LoadFailureStore(Store):
        async def async_load(self):
            raise load_error

    journal = OperationJournalService(JournalStore(), now_ms=lambda: 100)
    service = archive_service(journal, LoadFailureStore())
    await service.async_load()
    with pytest.raises(JournalArchiveError, match="archive_storage_unavailable"):
        await service.async_archive("admin")


@pytest.mark.asyncio
async def test_archive_migration_save_failure_is_quarantined_without_raising():
    store = FailingStore()
    store.payload = {"version": 1, "archives": {}}
    store.fail_next = True
    journal = OperationJournalService(JournalStore(), now_ms=lambda: 100)
    service = archive_service(journal, store)
    await service.async_load()
    with pytest.raises(JournalArchiveError, match="archive_storage_unavailable"):
        await service.async_archive("admin")


@pytest.mark.asyncio
async def test_deep_archive_payload_is_rejected_before_canonicalization_without_raising():
    journal = OperationJournalService(JournalStore(), now_ms=lambda: 100)
    store = Store()
    archived = await archive_service(journal, store).async_archive("admin")
    nested = {}
    cursor = nested
    for _ in range(2_000):
        child = {}
        cursor["child"] = child
        cursor = child
    store.payload["archives"][archived["archivedSnapshotId"]]["snapshot"][
        "malicious"
    ] = nested

    restored = archive_service(journal, store)
    await restored.async_load()
    with pytest.raises(JournalArchiveError, match="archive_storage_unavailable"):
        await restored.async_archive("admin")


@pytest.mark.asyncio
async def test_archive_rate_limit_is_persisted_per_admin_and_pruned_after_window():
    now = 100
    journal = OperationJournalService(JournalStore(), now_ms=lambda: now)
    store = Store()
    service = archive_service(journal, store, now_ms=lambda: now)
    await service.async_archive("admin")
    await service.async_archive("admin")
    restored = archive_service(journal, store, now_ms=lambda: now)
    await restored.async_load()
    with pytest.raises(JournalArchiveError) as limited:
        await restored.async_archive("admin")
    assert limited.value.detail_code == "reset_rate_limited"
    assert limited.value.retry_after_seconds == 60

    now += 60_001
    allowed = await restored.async_archive("admin")
    assert allowed["accepted"] is True
    assert sum(len(points) for points in restored._rate_buckets["archive"].values()) == 1


@pytest.mark.asyncio
async def test_rate_limit_rejection_happens_before_any_storage_call():
    class CountingStore(Store):
        def __init__(self):
            super().__init__()
            self.save_calls = 0

        async def async_save(self, payload):
            self.save_calls += 1
            await super().async_save(payload)

    journal = OperationJournalService(JournalStore(), now_ms=lambda: 100)
    store = CountingStore()
    service = archive_service(journal, store)
    await service.async_archive("admin")
    await service.async_archive("admin")
    before = store.save_calls
    with pytest.raises(JournalArchiveError, match="reset_rate_limited"):
        await service.async_archive("admin")
    assert store.save_calls == before


@pytest.mark.asyncio
async def test_archive_and_reset_global_limits_are_exact_and_independent():
    journal = OperationJournalService(JournalStore(), now_ms=lambda: 100)
    store = Store()
    service = archive_service(journal, store)
    for index in range(5):
        await service.async_archive(f"archive-{index}")
        await service.async_archive(f"archive-{index}")
    with pytest.raises(JournalArchiveError) as archive_limited:
        await service.async_archive("archive-overflow")
    assert archive_limited.value.retry_after_seconds == 60

    for index in range(5):
        for _ in range(6):
            with pytest.raises(JournalArchiveError, match="reset_archive_token_invalid"):
                await service.async_reset(f"reset-{index}", "A" * 43, 1, 0, "0" * 64)
    with pytest.raises(JournalArchiveError) as reset_limited:
        await service.async_reset("reset-overflow", "A" * 43, 1, 0, "0" * 64)
    assert reset_limited.value.detail_code == "reset_rate_limited"
    assert reset_limited.value.retry_after_seconds == 60
    assert sum(len(points) for points in service._rate_buckets["archive"].values()) == 10
    assert sum(len(points) for points in service._rate_buckets["reset"].values()) == 30


@pytest.mark.asyncio
async def test_global_pending_archive_quota_is_exact_and_rejection_is_transactional():
    journal = OperationJournalService(JournalStore(), now_ms=lambda: 100)
    store = Store()
    service = archive_service(journal, store)
    for index in range(8):
        await service.async_archive(f"admin-{index}")
    persisted_before = json.loads(json.dumps(store.payload))

    with pytest.raises(JournalArchiveError) as limited:
        await service.async_archive("admin-overflow")

    assert limited.value.detail_code == "reset_rate_limited"
    assert limited.value.retry_after_seconds == 60
    assert len(service._pending) == 8
    assert len(service._archives) == 8
    assert store.payload == persisted_before


@pytest.mark.asyncio
async def test_archive_save_failure_rolls_back_archive_and_rate_state():
    journal = OperationJournalService(JournalStore(), now_ms=lambda: 100)
    store = FailingStore()
    service = archive_service(journal, store)
    store.fail_next = True
    with pytest.raises(JournalArchiveError, match="archive_storage_unavailable"):
        await service.async_archive("admin")
    assert service._archives == {}
    assert service._rate_buckets["archive"] == {}
    with pytest.raises(JournalArchiveError, match="archive_storage_unavailable"):
        await service.async_archive("admin")
    restored = archive_service(journal, store)
    await restored.async_load()
    await restored.async_archive("admin")
    await restored.async_archive("admin")


@pytest.mark.asyncio
async def test_reset_storage_failure_rolls_back_rate_and_token_after_restart():
    journal_store = FailingStore()
    journal = OperationJournalService(journal_store, now_ms=lambda: 100)
    await journal.async_append(receipt("r1"))
    archive_store = FailingStore()
    service = archive_service(journal, archive_store)
    archived = await service.async_archive("admin")
    expected = (
        archived["snapshot"]["generation"],
        archived["snapshot"]["sequence"],
        archived["snapshot"]["revision"],
    )
    journal_store.fail_next = True
    with pytest.raises(JournalArchiveError, match="archive_storage_unavailable"):
        await service.async_reset("admin", archived["archiveToken"], *expected)
    assert journal.snapshot(limit=512)["revision"] == expected[2]
    record = service._archives[archived["archivedSnapshotId"]]
    assert record["consumed"] is False
    with pytest.raises(JournalArchiveError, match="archive_storage_unavailable"):
        await service.async_reset("admin", archived["archiveToken"], *expected)

    restored_journal = OperationJournalService(journal_store, now_ms=lambda: 100)
    await restored_journal.async_load()
    restored = archive_service(restored_journal, archive_store)
    await restored.async_load()
    assert restored._rate_buckets["reset"] == {}
    result = await restored.async_reset("admin", archived["archiveToken"], *expected)
    assert result["physicalCommandsSent"] is False


@pytest.mark.asyncio
async def test_reset_archive_store_failure_never_mutates_journal_or_consumes_token():
    journal = OperationJournalService(JournalStore(), now_ms=lambda: 100)
    await journal.async_append(receipt("r1"))
    archive_store = FailingStore()
    service = archive_service(journal, archive_store)
    archived = await service.async_archive("admin")
    expected = (
        archived["snapshot"]["generation"],
        archived["snapshot"]["sequence"],
        archived["snapshot"]["revision"],
    )
    archive_store.fail_next = True
    with pytest.raises(JournalArchiveError, match="archive_storage_unavailable"):
        await service.async_reset("admin", archived["archiveToken"], *expected)
    assert journal.snapshot(limit=512)["revision"] == expected[2]
    assert service._archives[archived["archivedSnapshotId"]]["consumed"] is False
    assert service._rate_buckets["reset"] == {}
    with pytest.raises(JournalArchiveError, match="archive_storage_unavailable"):
        await service.async_reset("admin", archived["archiveToken"], *expected)
    restored = archive_service(journal, archive_store)
    await restored.async_load()
    result = await restored.async_reset("admin", archived["archiveToken"], *expected)
    assert result["archiveTokenConsumed"] is True


@pytest.mark.asyncio
async def test_concurrent_reset_has_one_success_and_one_opaque_invalid_token():
    journal = OperationJournalService(JournalStore(), now_ms=lambda: 100)
    await journal.async_append(receipt("r1"))
    service = archive_service(journal, Store())
    archived = await service.async_archive("admin")
    expected = (
        archived["snapshot"]["generation"],
        archived["snapshot"]["sequence"],
        archived["snapshot"]["revision"],
    )

    async def reset_once():
        try:
            return await service.async_reset("admin", archived["archiveToken"], *expected)
        except JournalArchiveError as error:
            return error.detail_code

    results = await asyncio.gather(reset_once(), reset_once())
    assert sum(isinstance(item, dict) for item in results) == 1
    assert results.count("reset_archive_token_invalid") == 1
    assert journal.snapshot()["generation"] == expected[0] + 1


@pytest.mark.asyncio
async def test_unknown_expired_consumed_and_superseded_tokens_are_indistinguishable():
    async def rejected(awaitable):
        with pytest.raises(JournalArchiveError) as raised:
            await awaitable
        assert raised.value.detail_code == "reset_archive_token_invalid"
        assert raised.value.details == {}

    unknown_journal = OperationJournalService(JournalStore(), now_ms=lambda: 100)
    await rejected(
        archive_service(unknown_journal, Store()).async_reset(
            "admin", "A" * 43, 1, 0, "0" * 64
        )
    )

    now = 100
    expired_journal = OperationJournalService(JournalStore(), now_ms=lambda: now)
    expired_service = archive_service(expired_journal, Store(), now_ms=lambda: now)
    expired = await expired_service.async_archive("admin")
    now += 900_001
    await rejected(
        expired_service.async_reset(
            "admin",
            expired["archiveToken"],
            expired["snapshot"]["generation"],
            expired["snapshot"]["sequence"],
            expired["snapshot"]["revision"],
        )
    )
    assert expired_service._archives[expired["archivedSnapshotId"]]["tokenCiphertext"] == ""

    consumed_journal = OperationJournalService(JournalStore(), now_ms=lambda: 100)
    consumed_service = archive_service(consumed_journal, Store())
    consumed = await consumed_service.async_archive("admin")
    consumed_expected = (
        consumed["snapshot"]["generation"],
        consumed["snapshot"]["sequence"],
        consumed["snapshot"]["revision"],
    )
    await consumed_service.async_reset(
        "admin", consumed["archiveToken"], *consumed_expected
    )
    assert consumed_service._archives[consumed["archivedSnapshotId"]]["tokenCiphertext"] == ""
    await rejected(
        consumed_service.async_reset(
            "admin", consumed["archiveToken"], *consumed_expected
        )
    )

    superseded_journal = OperationJournalService(JournalStore(), now_ms=lambda: 100)
    superseded_service = archive_service(superseded_journal, Store())
    old = await superseded_service.async_archive("admin")
    await superseded_journal.async_append(receipt("new-cas"))
    await superseded_service.async_archive("admin")
    assert superseded_service._archives[old["archivedSnapshotId"]]["tokenCiphertext"] == ""
    await rejected(
        superseded_service.async_reset(
            "admin",
            old["archiveToken"],
            old["snapshot"]["generation"],
            old["snapshot"]["sequence"],
            old["snapshot"]["revision"],
        )
    )


@pytest.mark.asyncio
async def test_legacy_plaintext_token_is_scrubbed_and_invalidated_on_load():
    journal = OperationJournalService(JournalStore(), now_ms=lambda: 100)
    snapshot = journal.snapshot(limit=512)
    token = "sensitive-legacy-token"
    store = Store()
    store.payload = {
        "version": 1,
        "archives": {
            "journal.legacy": {
                "receipt": {
                    "snapshot": snapshot,
                    "archiveToken": token,
                    "archiveSha256": __import__("hashlib").sha256(
                        canonical_json(snapshot).encode()
                    ).hexdigest(),
                    "issuedAt": 90,
                    "expiresAt": 900_090,
                }
            }
        },
    }
    service = archive_service(journal, store)
    await service.async_load()
    encoded = json.dumps(store.payload)
    assert token not in encoded
    assert "archiveToken" not in encoded
    assert store.payload["version"] == 3
    assert store.payload["archives"] == {}


@pytest.mark.asyncio
async def test_missing_external_key_fails_closed_without_persisting_rate_or_token():
    journal = OperationJournalService(JournalStore(), now_ms=lambda: 100)
    store = Store()
    service = OperationJournalArchiveService(journal, store, keyring=None, now_ms=lambda: 100)
    with pytest.raises(JournalArchiveError, match="archive_storage_unavailable"):
        await service.async_archive("admin")
    assert store.payload is None
    assert service._archives == {}


@pytest.mark.asyncio
async def test_missing_external_key_still_scrubs_legacy_plaintext_token():
    token = "sensitive-legacy-token"
    store = Store()
    store.payload = {
        "version": 1,
        "archives": {"journal.legacy": {"receipt": {"archiveToken": token}}},
    }
    journal = OperationJournalService(JournalStore(), now_ms=lambda: 100)
    service = OperationJournalArchiveService(
        journal, store, keyring=None, now_ms=lambda: 100
    )

    await service.async_load()

    assert token not in json.dumps(store.payload)
    with pytest.raises(JournalArchiveError, match="archive_storage_unavailable"):
        await service.async_archive("admin")


@pytest.mark.asyncio
async def test_key_from_same_ha_backup_fails_closed_instead_of_false_encryption():
    class SameBackupKeyring(Keyring):
        backup_separated = False

    journal = OperationJournalService(JournalStore(), now_ms=lambda: 100)
    store = Store()
    service = OperationJournalArchiveService(
        journal,
        store,
        keyring=SameBackupKeyring(),
        now_ms=lambda: 100,
    )
    with pytest.raises(JournalArchiveError, match="archive_storage_unavailable"):
        await service.async_archive("admin")
    assert store.payload is None
    assert service._archives == {}


@pytest.mark.asyncio
async def test_load_prunes_authenticated_archive_used_and_rate_collections_to_bounds():
    journal = OperationJournalService(JournalStore(), now_ms=lambda: 100)
    seed_store = Store()
    seed_service = archive_service(journal, seed_store)
    archived = await seed_service.async_archive("seed")
    seed = seed_store.payload["archives"][archived["archivedSnapshotId"]]
    archives = {}
    used = []
    for index in range(100):
        record = json.loads(json.dumps(seed))
        record["issuedAt"] = index
        record["expiresAt"] = index + 900_000
        record["completedAt"] = index
        record["consumed"] = True
        record["tokenDigest"] = f"{index:064x}"
        record["tokenKeyId"] = ""
        record["tokenNonce"] = ""
        record["tokenCiphertext"] = ""
        record["ownerId"] = f"admin-{index}"
        record["tokenState"] = "consumed"
        record["resetTransactionId"] = None
        archive_id = f"journal.{index:032d}"
        seed_service._sign_record(archive_id, record)
        archives[archive_id] = record
        used.append(record["tokenDigest"])
    store = Store()
    store.payload = {
        "version": 3,
        "archives": archives,
        "pending": {},
        "used": used,
        "rateBuckets": {
            "archive": {f"admin-{index}": [100] * 100 for index in range(100)},
            "reset": {f"admin-{index}": [100] * 100 for index in range(100)},
            "unknown": {"attacker": [100] * 100},
        },
        "resetTransaction": None,
        "storeAuthKeyId": "",
        "storeAuthTag": "",
    }
    seed_service._sign_store_payload(store.payload)
    service = archive_service(journal, store)
    await service.async_load()

    assert len(service._archives) == 64
    assert len(service._used) <= 64
    assert service._pending == {}
    assert sum(len(value) for value in service._rate_buckets["archive"].values()) == 10
    assert sum(len(value) for value in service._rate_buckets["reset"].values()) == 30
    assert set(store.payload["rateBuckets"]) == {"archive", "reset"}
    assert len(store.payload["archives"]) == 64


@pytest.mark.asyncio
async def test_retention_never_evicts_young_completed_archives_when_full():
    now = 100
    journal = OperationJournalService(JournalStore(), now_ms=lambda: now)
    store = Store()
    service = archive_service(journal, store, now_ms=lambda: now)
    ids = []
    for index in range(64):
        archived = await service.async_archive(f"admin-{index}")
        ids.append(archived["archivedSnapshotId"])
        result = await service.async_reset(
            f"admin-{index}",
            archived["archiveToken"],
            archived["snapshot"]["generation"],
            archived["snapshot"]["sequence"],
            archived["snapshot"]["revision"],
        )
        assert result["physicalCommandsSent"] is False
        now += 60_001

    persisted_before = json.loads(json.dumps(store.payload))
    with pytest.raises(JournalArchiveError) as quota:
        await service.async_archive("admin-overflow")
    assert quota.value.detail_code == "reset_rate_limited"
    assert set(service._archives) == set(ids)
    assert store.payload == persisted_before


@pytest.mark.asyncio
async def test_retention_evicts_only_old_completed_generation_outside_latest_eight():
    now = 100
    journal = OperationJournalService(JournalStore(), now_ms=lambda: now)
    store = Store()
    service = archive_service(journal, store, now_ms=lambda: now)
    ids = []
    for index in range(64):
        archived = await service.async_archive(f"admin-{index}")
        ids.append(archived["archivedSnapshotId"])
        await service.async_reset(
            f"admin-{index}",
            archived["archiveToken"],
            archived["snapshot"]["generation"],
            archived["snapshot"]["sequence"],
            archived["snapshot"]["revision"],
        )
        now += 2_592_060_001

    latest_eight = set(ids[-8:])
    replacement = await service.async_archive("admin-replacement")
    assert len(service._archives) == 64
    assert ids[0] not in service._archives
    assert latest_eight <= set(service._archives)
    assert replacement["archivedSnapshotId"] in service._archives
