from __future__ import annotations

import asyncio
import copy
import hashlib
import json

import pytest

from custom_components.hausman_hub.application.managed_switch_migration import (
    LEGACY_MANAGED_SWITCHES,
    MANIFEST_HASH,
    MIGRATION_MANIFEST,
    ManagedSwitchMigration,
    ManagedSwitchMigrationConflict,
    _initial_journal,
    valid_managed_switch_migration_payload,
)


def test_manifest_marks_only_snapshot_managed_scenarios_as_replacements() -> None:
    """The production snapshot starts at three controllers, not eight."""

    replacements = {
        entry.scenario_id
        for entry in MIGRATION_MANIFEST
        if entry.operation == "replace"
    }
    creates = {
        entry.scenario_id
        for entry in MIGRATION_MANIFEST
        if entry.operation == "create"
    }

    assert replacements == {
        "system-shower-comfort-controller",
        "system-small-corridor-light-controller",
        "system-tambur-adaptive-controller",
    }
    assert creates == {
        "system-toilet-comfort-controller",
        "system-bathroom-exhaust-controller",
        "system-storage-light-controller",
        "system-cabinet-light-controller",
        "system-curtains-privacy-controller",
    }
    assert all(entry.expected_revision is None for entry in MIGRATION_MANIFEST if entry.operation == "create")


class Store:
    def __init__(self, value=None, *, fail_save=False, fail_save_at=None):
        self.value = value
        self.saved = []
        self.fail_save = fail_save
        self.fail_save_at = fail_save_at
        self.save_attempts = 0

    async def async_load(self):
        return self.value

    async def async_save(self, value):
        self.save_attempts += 1
        if self.fail_save or self.save_attempts == self.fail_save_at:
            raise OSError("verified save failed")
        self.saved.append(value)
        self.value = value


class Service:
    def __init__(
        self,
        *,
        fail=False,
        verification_drift=None,
        verification_drift_at=1,
        rollback_complete=True,
        completed=False,
    ):
        self.calls = []
        self.fail = fail
        self.verification_drift = verification_drift
        self.verification_drift_at = verification_drift_at
        self.rollback_complete = rollback_complete
        self.verifications = 0
        self.finalizations = 0
        self.rollbacks = 0
        self.migrated = completed
        self.finalized = completed

    def _capture(self):
        scenarios = [
            {
                "id": item.scenario_id,
                "enabled": (
                    self.finalized
                    or item.operation == "replace"
                ),
            }
            for item in MIGRATION_MANIFEST
            if self.migrated or item.operation == "replace"
        ]
        flows = {}
        for item in MIGRATION_MANIFEST:
            if item.operation == "create" and not self.migrated:
                flows[item.scenario_id] = {"state": "absent"}
                continue
            flows[item.scenario_id] = {
                "state": "present",
                "flowId": f"flow-{item.scenario_id}",
                "sourceHash": (
                    item.new_source_hash
                    if self.migrated
                    else item.legacy_source_hash
                ),
                "source": (
                    f"release:{item.scenario_id}"
                    if self.migrated and item.operation == "create"
                    else f"legacy:{item.scenario_id}"
                ),
                "topology": item.legacy_topology,
            }
        return {
            "registry": {"version": 1, "scenarios": scenarios},
            "flows": flows,
        }

    async def async_stage_managed_switch_migration(
        self, entries, *, journal, on_staged
    ):
        operations = copy.deepcopy(journal["operations"])
        for item in entries:
            operation = operations[item.scenario_id]
            operation["state"] = "intent"
            await on_staged(operations)
            if item.operation == "create":
                operation["flowId"] = f"flow-{item.scenario_id}"
                operation["flowRevision"] = 1
            operation["state"] = "applied"
            await on_staged(operations)

    async def async_apply_managed_switch_migration(
        self, entries, *, journal=None, on_registry=None
    ):
        self.calls.append(entries)
        if self.fail:
            raise RuntimeError("CAS conflict")
        self.migrated = True
        registry = self._capture()["registry"]
        state = {
            "state": "applied",
            "beforeHash": journal["registry"]["beforeHash"],
            "afterHash": hashlib.sha256(
                json.dumps(
                    registry,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode()
            ).hexdigest(),
        }
        if on_registry is not None:
            await on_registry(state)

    async def async_capture_managed_switch_migration(self, entries):
        return self._capture()

    async def async_verify_managed_switch_migration(self, entries):
        self.verifications += 1
        if (
            self.verification_drift is not None
            and self.verifications == self.verification_drift_at
        ):
            raise RuntimeError(f"final drift: {self.verification_drift}")
        assert len(entries) == 8
        return "revision.final"

    async def async_finalize_managed_switch_migration(
        self, entries, *, journal=None, on_registry=None
    ):
        assert len(entries) == 8
        self.finalizations += 1
        self.finalized = True
        if on_registry is not None:
            registry = self._capture()["registry"]
            await on_registry({
                "state": "applied",
                "beforeHash": journal["registry"]["beforeHash"],
                "afterHash": hashlib.sha256(
                    json.dumps(
                        registry,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ).encode()
                ).hexdigest(),
            })

    async def async_commit_managed_switch_migration(self, entries):
        assert len(entries) == 8

    async def async_rollback_managed_switch_migration(
        self, entries, *, journal=None
    ):
        assert len(entries) == 8
        self.rollbacks += 1
        if self.rollback_complete:
            self.migrated = False
            self.finalized = False
        return self.rollback_complete


def test_manifest_contains_exact_eight_protected_scenarios_and_sources() -> None:
    assert set(LEGACY_MANAGED_SWITCHES) == {
        "system-shower-comfort-controller",
        "system-small-corridor-light-controller",
        "system-tambur-adaptive-controller",
        "system-toilet-comfort-controller",
        "system-bathroom-exhaust-controller",
        "system-storage-light-controller",
        "system-cabinet-light-controller",
        "system-curtains-privacy-controller",
    }
    for item in MIGRATION_MANIFEST:
        assert item.legacy_topology == "managed-three-node-v1"
        assert len(item.legacy_source_hash) == len(item.new_source_hash) == 64
        assert item.input_target_ids


def test_migration_persists_prepared_before_cas_and_completed_after() -> None:
    store = Store()
    service = Service()
    assert asyncio.run(ManagedSwitchMigration(service, store).async_apply()) == "completed"
    assert store.saved[0]["state"] == "prepared"
    assert store.saved[-1]["state"] == "completed"
    assert all(item["state"] == "prepared" for item in store.saved[:-1])
    assert all(valid_managed_switch_migration_payload(item) for item in store.saved)
    assert len(service.calls[0]) == 8
    assert all(item.source for item in service.calls[0])
    assert service.finalizations == 1
    assert service.rollbacks == 0


def test_completed_receipt_binds_manifest_operations_and_full_registry_hash() -> None:
    store = Store()
    service = Service()
    asyncio.run(ManagedSwitchMigration(service, store).async_apply())
    completed = copy.deepcopy(store.value)

    wrong_operation = copy.deepcopy(completed)
    scenario_id = MIGRATION_MANIFEST[0].scenario_id
    wrong_operation["journal"]["operations"][scenario_id][
        "newSourceHash"
    ] = "0" * 64
    assert not valid_managed_switch_migration_payload(wrong_operation)

    wrong_registry = copy.deepcopy(completed)
    first = wrong_registry["journal"]["after"]["registry"]["scenarios"][0]
    first["enabled"] = not first["enabled"]
    assert not valid_managed_switch_migration_payload(wrong_registry)


def test_migration_records_create_intent_before_each_external_flow() -> None:
    class SnapshotStore(Store):
        async def async_save(self, value):
            snapshot = copy.deepcopy(value)
            self.saved.append(snapshot)
            self.value = snapshot

    class IntentService(Service):
        async def async_stage_managed_switch_migration(
            self, entries, *, journal, on_staged
        ):
            scenario_id = next(
                item.scenario_id for item in entries if item.operation == "create"
            )
            operations = copy.deepcopy(journal["operations"])
            operations[scenario_id]["state"] = "intent"
            await on_staged(operations)
            operations[scenario_id]["state"] = "applied"
            operations[scenario_id]["flowId"] = "flow-recovered"
            operations[scenario_id]["flowRevision"] = 1
            await on_staged(operations)
            for item in entries:
                operation = operations[item.scenario_id]
                if operation["state"] == "pending":
                    operation["state"] = "applied"
                    if item.operation == "create":
                        operation["flowId"] = f"flow-{item.scenario_id}"
                        operation["flowRevision"] = 1
            await on_staged(operations)

    store = SnapshotStore()
    service = IntentService()

    assert asyncio.run(ManagedSwitchMigration(service, store).async_apply()) == "completed"
    staged_receipts = [
        receipt["journal"]["operations"]
        for receipt in store.saved
        if receipt["state"] == "prepared"
        and receipt["journal"]["operations"][
            "system-toilet-comfort-controller"
        ]["state"]
        in {"intent", "applied"}
    ]
    assert staged_receipts[0]["system-toilet-comfort-controller"]["state"] == "intent"
    assert staged_receipts[1]["system-toilet-comfort-controller"] == {
        "kind": "create",
        "state": "applied",
        "flowId": "flow-recovered",
        "flowRevision": 1,
        "expectedSourceHash": None,
        "newSourceHash": next(
            item.new_source_hash
            for item in MIGRATION_MANIFEST
            if item.scenario_id == "system-toilet-comfort-controller"
        ),
        "previousSource": None,
    }


def test_storage_failure_before_prepare_causes_no_mutation() -> None:
    store = Store(fail_save=True)
    service = Service()
    with pytest.raises(OSError, match="verified save failed"):
        asyncio.run(ManagedSwitchMigration(service, store).async_apply())
    assert service.calls == []


def test_prepared_receipt_reconciles_and_completed_is_idempotent() -> None:
    service = Service()
    prepared = {
        "migrationId": "managed-switches", "version": 3,
        "state": "prepared", "manifestHash": MANIFEST_HASH,
        "journal": _initial_journal(service._capture(), MIGRATION_MANIFEST),
    }
    store = Store(prepared)
    asyncio.run(ManagedSwitchMigration(service, store).async_apply())
    assert len(service.calls) == 1
    completed = Store(store.value)
    second = Service(completed=True)
    assert asyncio.run(ManagedSwitchMigration(second, completed).async_apply()) == "completed"
    assert second.calls == []
    assert completed.saved == []


def test_invalid_or_foreign_receipt_fails_closed() -> None:
    store = Store({"migrationId": "managed-switches", "version": 2, "state": "completed", "manifestHash": "0" * 64})
    with pytest.raises(ManagedSwitchMigrationConflict, match="receipt"):
        asyncio.run(ManagedSwitchMigration(Service(), store).async_apply())


def test_known_completed_v2_receipt_is_verified_and_upgraded_to_v3() -> None:
    store = Store(
        {
            "migrationId": "managed-switches",
            "version": 2,
            "state": "completed",
            "manifestHash": "a3554f0a7108160238cbd4fd49f2f643ad3d446d7f344dec618d9a0d979d2c60",
        }
    )
    service = Service()

    assert asyncio.run(ManagedSwitchMigration(service, store).async_apply()) == "completed"
    assert store.saved[0]["state"] == "prepared"
    assert store.saved[-1]["state"] == "completed"
    assert all(item["state"] == "prepared" for item in store.saved[:-1])
    assert all(item["version"] == 3 for item in store.saved)
    assert service.verifications == 2


def test_cas_conflict_leaves_prepared_receipt_for_restart_reconciliation() -> None:
    store = Store()
    with pytest.raises(RuntimeError, match="CAS conflict"):
        asyncio.run(ManagedSwitchMigration(Service(fail=True), store).async_apply())
    assert store.value["state"] == "prepared"


@pytest.mark.parametrize(
    "scenario_id",
    [item.scenario_id for item in MIGRATION_MANIFEST],
)
def test_final_cross_scenario_drift_never_completes_receipt(
    scenario_id: str,
) -> None:
    store = Store()
    service = Service(verification_drift=scenario_id)

    with pytest.raises(RuntimeError, match="final drift"):
        asyncio.run(ManagedSwitchMigration(service, store).async_apply())

    assert service.verifications == 1
    assert service.rollbacks == 1
    assert service.finalizations == 0
    assert store.value["state"] == "prepared"


def test_completed_receipt_save_failure_rolls_back_prepared_migration() -> None:
    store = Store(fail_save_at=2)
    service = Service()

    with pytest.raises(OSError, match="verified save failed"):
        asyncio.run(ManagedSwitchMigration(service, store).async_apply())

    assert store.value["state"] == "prepared"
    assert service.rollbacks == 1
    assert service.finalizations == 0


def test_cancellation_after_apply_rolls_back_before_it_is_re_raised() -> None:
    async def exercise() -> None:
        class CancelDuringVerification(Service):
            def __init__(self) -> None:
                super().__init__()
                self.verification_started = asyncio.Event()

            async def async_verify_managed_switch_migration(self, entries):
                self.verifications += 1
                self.verification_started.set()
                await asyncio.Event().wait()

        store = Store()
        service = CancelDuringVerification()
        migration = asyncio.create_task(
            ManagedSwitchMigration(service, store).async_apply()
        )
        await service.verification_started.wait()
        migration.cancel()

        with pytest.raises(asyncio.CancelledError):
            await migration

        assert store.value["state"] == "prepared"
        assert service.rollbacks == 1
        assert service.finalizations == 0

    asyncio.run(exercise())


def test_ambiguous_completed_receipt_write_is_reverted_to_prepared() -> None:
    class PartialWriteStore(Store):
        async def async_save(self, value):
            if value["state"] == "completed":
                self.value = value
                self.saved.append(value)
                raise OSError("completed receipt outcome is ambiguous")
            await super().async_save(value)

    store = PartialWriteStore()
    service = Service()

    with pytest.raises(OSError, match="outcome is ambiguous"):
        asyncio.run(ManagedSwitchMigration(service, store).async_apply())

    assert store.value["state"] == "prepared"
    assert service.rollbacks == 1
    assert service.finalizations == 1


def test_drift_after_completed_write_reverts_receipt_and_blocks_unsafe_rollback() -> None:
    store = Store()
    service = Service(
        verification_drift="manual edit",
        verification_drift_at=2,
        rollback_complete=False,
    )

    with pytest.raises(ManagedSwitchMigrationConflict, match="recovery"):
        asyncio.run(ManagedSwitchMigration(service, store).async_apply())

    assert store.value["state"] == "prepared"
    assert service.verifications == 2
    assert service.rollbacks == 1
    assert service.finalizations == 1


def test_native_handover_is_rolled_back_when_final_flow_verification_drifts() -> None:
    class NativeHandover:
        def __init__(self) -> None:
            self.applied = 0
            self.rolled_back = 0

        async def async_apply(self) -> None:
            self.applied += 1

        async def async_rollback(self) -> bool:
            self.rolled_back += 1
            return True

    store = Store()
    service = Service(verification_drift="after native", verification_drift_at=2)
    native = NativeHandover()

    with pytest.raises(RuntimeError, match="final drift"):
        asyncio.run(
            ManagedSwitchMigration(
                service, store, native_automation_migration=native
            ).async_apply()
        )

    assert native.applied == 1
    assert native.rolled_back == 1
    assert service.rollbacks == 1
