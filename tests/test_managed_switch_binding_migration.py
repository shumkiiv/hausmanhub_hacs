from __future__ import annotations

import asyncio
import unittest

from custom_components.hausman_hub.application.managed_switch_binding_migration import (
    BINDING_MANIFEST_HASH,
    BINDING_MIGRATION_MANIFEST,
    ManagedSwitchBindingMigration,
    ManagedSwitchBindingMigrationConflict,
    valid_managed_switch_binding_payload,
)


class Store:
    def __init__(self, value=None, *, fail_at=None) -> None:
        self.value = value
        self.saved = []
        self.fail_at = fail_at
        self.attempts = 0

    async def async_load(self):
        return self.value

    async def async_save(self, value):
        self.attempts += 1
        if self.attempts == self.fail_at:
            raise OSError("receipt save failed")
        self.value = value
        self.saved.append(value)


class Service:
    def __init__(self, *, block_verify=False) -> None:
        self.applies = 0
        self.verifies = 0
        self.finalizes = 0
        self.rollbacks = 0
        self.block_verify = block_verify
        self.verify_started = asyncio.Event()

    async def async_apply_managed_switch_binding_migration(self, entries):
        assert entries == BINDING_MIGRATION_MANIFEST
        self.applies += 1

    async def async_verify_managed_switch_binding_migration(self, entries):
        assert entries == BINDING_MIGRATION_MANIFEST
        self.verifies += 1
        if self.block_verify:
            self.verify_started.set()
            await asyncio.Event().wait()
        return "revision.stable"

    async def async_finalize_managed_switch_binding_migration(self, entries):
        assert entries == BINDING_MIGRATION_MANIFEST
        self.finalizes += 1

    async def async_rollback_managed_switch_binding_migration(self, entries):
        assert entries == BINDING_MIGRATION_MANIFEST
        self.rollbacks += 1
        return True


class ManagedSwitchBindingMigrationTest(unittest.TestCase):
    def test_separate_prepared_and_completed_receipt_is_restart_safe(self) -> None:
        store = Store()
        service = Service()
        self.assertEqual(
            "completed",
            asyncio.run(ManagedSwitchBindingMigration(service, store).async_apply()),
        )
        self.assertEqual(["prepared", "completed"], [x["state"] for x in store.saved])
        self.assertTrue(all(valid_managed_switch_binding_payload(x) for x in store.saved))
        self.assertEqual((1, 2, 1, 0), (service.applies, service.verifies, service.finalizes, service.rollbacks))

        prepared = Store({
            "migrationId": "managed-switch-bindings",
            "version": 1,
            "state": "prepared",
            "manifestHash": BINDING_MANIFEST_HASH,
        })
        resumed = Service()
        asyncio.run(ManagedSwitchBindingMigration(resumed, prepared).async_apply())
        self.assertEqual("completed", prepared.value["state"])
        self.assertEqual(1, resumed.finalizes)

    def test_receipt_failure_rolls_back_and_stays_prepared(self) -> None:
        store = Store(fail_at=2)
        service = Service()
        with self.assertRaisesRegex(OSError, "receipt save failed"):
            asyncio.run(ManagedSwitchBindingMigration(service, store).async_apply())
        self.assertEqual("prepared", store.value["state"])
        self.assertEqual(1, service.rollbacks)
        self.assertEqual(0, service.finalizes)

    def test_cancellation_rolls_back_before_reraising(self) -> None:
        async def exercise() -> None:
            store = Store()
            service = Service(block_verify=True)
            task = asyncio.create_task(
                ManagedSwitchBindingMigration(service, store).async_apply()
            )
            await service.verify_started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual("prepared", store.value["state"])
            self.assertEqual(1, service.rollbacks)

        asyncio.run(exercise())

    def test_invalid_or_foreign_receipt_fails_closed(self) -> None:
        store = Store({
            "migrationId": "managed-switches",
            "version": 2,
            "state": "completed",
            "manifestHash": "f" * 64,
        })
        with self.assertRaisesRegex(
            ManagedSwitchBindingMigrationConflict,
            "receipt",
        ):
            asyncio.run(ManagedSwitchBindingMigration(Service(), store).async_apply())
