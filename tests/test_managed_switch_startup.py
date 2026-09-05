from __future__ import annotations

import asyncio
import unittest
from dataclasses import dataclass

from custom_components.hausman_hub.application.managed_switch_migration import (
    MIGRATION_MANIFEST,
    ManagedSwitchStartupCoordinator,
    async_load_managed_switch_migration_entries,
)
from custom_components.hausman_hub.application.activation_latch import ActivationLatch


@dataclass(frozen=True)
class _Device:
    target_id: str


class _Catalog:
    def __init__(self, target_ids: tuple[str, ...]) -> None:
        self._devices = {target_id: _Device(target_id) for target_id in target_ids}

    def device(self, target_id: str) -> _Device | None:
        return self._devices.get(target_id)


class _Service:
    def __init__(self, catalog: _Catalog) -> None:
        self.catalog = catalog
        self.observers = []

    def current_catalog(self) -> _Catalog:
        return self.catalog

    def add_catalog_warmup_observer(self, observer):
        self.observers.append(observer)

        def remove() -> None:
            if observer in self.observers:
                self.observers.remove(observer)

        return remove

    async def publish(self, catalog: _Catalog, *, final: bool) -> None:
        self.catalog = catalog
        for observer in tuple(self.observers):
            await observer(catalog, final)


class _Migration:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls = 0
        self.error = error

    async def async_apply(self) -> str:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return "completed"


@dataclass(frozen=True)
class _Activation:
    cleanup: object
    commit: object
    revoke: object


def _required_targets() -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            target_id
            for entry in MIGRATION_MANIFEST
            for target_id in entry.input_target_ids
        )
    )


def test_incomplete_initial_catalog_activates_once_after_later_snapshot() -> None:
    async def exercise() -> None:
        states = []
        activations = 0

        async def activate() -> None:
            nonlocal activations
            activations += 1

        service = _Service(_Catalog(()))
        migration = _Migration()
        coordinator = ManagedSwitchStartupCoordinator(
            service,
            migration,
            activate,
            status_publisher=states.append,
        )

        await coordinator.async_start()
        assert states[-1] == {
            "state": "waiting",
            "reason": "catalog_warmup",
        }
        assert migration.calls == 0
        assert activations == 0
        assert len(service.observers) == 1

        await service.publish(_Catalog(_required_targets()[:-1]), final=False)
        assert migration.calls == 0
        assert activations == 0

        await service.publish(_Catalog(_required_targets()), final=False)
        assert coordinator.ready is True
        assert states[-1] == {"state": "completed"}
        assert migration.calls == 1
        assert activations == 1
        assert service.observers == []

        await service.publish(_Catalog(_required_targets()), final=True)
        assert migration.calls == 1
        assert activations == 1

    asyncio.run(exercise())


def test_unload_while_waiting_removes_listener_and_prevents_activation() -> None:
    async def exercise() -> None:
        activations = 0

        async def activate() -> None:
            nonlocal activations
            activations += 1

        service = _Service(_Catalog(()))
        migration = _Migration()
        coordinator = ManagedSwitchStartupCoordinator(service, migration, activate)

        await coordinator.async_start()
        coordinator.cancel()
        assert service.observers == []

        await service.publish(_Catalog(_required_targets()), final=True)
        assert migration.calls == 0
        assert activations == 0
        assert coordinator.ready is False

    asyncio.run(exercise())


def test_unload_during_migration_prevents_late_activation() -> None:
    async def exercise() -> None:
        migration_started = asyncio.Event()
        release_migration = asyncio.Event()
        activations = 0

        class _PendingMigration:
            async def async_apply(self) -> str:
                migration_started.set()
                await release_migration.wait()
                return "completed"

        async def activate() -> None:
            nonlocal activations
            activations += 1

        service = _Service(_Catalog(()))
        coordinator = ManagedSwitchStartupCoordinator(
            service,
            _PendingMigration(),
            activate,
        )
        await coordinator.async_start()

        publish = asyncio.create_task(
            service.publish(_Catalog(_required_targets()), final=False)
        )
        await migration_started.wait()
        coordinator.cancel()
        release_migration.set()
        await publish

        assert coordinator.ready is False
        assert activations == 0
        assert service.observers == []

    asyncio.run(exercise())


def test_unload_during_activation_revokes_authority_and_removes_listeners() -> None:
    async def exercise() -> None:
        activation_started = asyncio.Event()
        release_activation = asyncio.Event()
        listeners = []

        async def activate():
            def cleanup() -> None:
                listeners.clear()

            listeners.append("scenario-schedule")
            listeners.append("state-events")
            activation_started.set()
            try:
                await release_activation.wait()
            except asyncio.CancelledError:
                cleanup()
                raise
            return cleanup

        service = _Service(_Catalog(()))
        coordinator = ManagedSwitchStartupCoordinator(
            service,
            _Migration(),
            activate,
        )
        await coordinator.async_start()

        publish = asyncio.create_task(
            service.publish(_Catalog(_required_targets()), final=False)
        )
        await activation_started.wait()
        assert coordinator.ready is False
        assert coordinator.activation_authorized is True
        coordinator.cancel()
        release_activation.set()
        await asyncio.gather(publish, return_exceptions=True)

        assert coordinator.ready is False
        assert coordinator.activation_authorized is False
        assert listeners == []
        assert service.observers == []

    asyncio.run(exercise())


def test_activation_commit_gates_all_runtime_callbacks_and_cleanup_once() -> None:
    """One coordinator commit authorizes all runtime callback families together."""

    async def exercise() -> None:
        latch = ActivationLatch()
        scenario_runs: list[str] = []
        journal_writes: list[str] = []
        command_calls: list[str] = []
        cleaned: list[str] = []
        callbacks: dict[str, object] = {}
        prepared = asyncio.Event()
        finish_late_attach = asyncio.Event()

        async def schedule_callback() -> None:
            if latch.is_open:
                scenario_runs.append("schedule")

        async def state_callback() -> None:
            if latch.is_open:
                scenario_runs.append("state")

        async def custom_callback() -> None:
            if latch.is_open:
                scenario_runs.append("custom")

        async def smart_callback() -> None:
            if latch.is_open:
                journal_writes.append("smart")
                command_calls.append("smart")

        async def delayed_state_callback() -> None:
            await asyncio.sleep(0)
            if latch.is_open:
                scenario_runs.append("delayed-state")

        def cleanup() -> None:
            cleaned.append("cleanup")

        def revoke() -> None:
            latch.close()

        async def late_attach_failure() -> _Activation:
            callbacks.update(
                schedule=schedule_callback,
                state=state_callback,
                custom=custom_callback,
                smart=smart_callback,
                delayed_state=delayed_state_callback,
            )
            prepared.set()
            await finish_late_attach.wait()
            raise RuntimeError("late adapter attach failed")

        failed = ManagedSwitchStartupCoordinator(
            _Service(_Catalog(_required_targets())), _Migration(), late_attach_failure
        )
        startup = asyncio.create_task(failed.async_start())
        await prepared.wait()
        await asyncio.gather(
            *(callback() for callback in callbacks.values()),
        )
        assert scenario_runs == []
        assert journal_writes == []
        assert command_calls == []
        finish_late_attach.set()
        await startup
        assert latch.is_open is False
        assert cleaned == []

        async def activate() -> _Activation:
            return _Activation(cleanup, latch.open, revoke)

        coordinator = ManagedSwitchStartupCoordinator(
            _Service(_Catalog(_required_targets())), _Migration(), activate
        )
        await coordinator.async_start()
        assert coordinator.ready is True
        assert latch.is_open is True
        await asyncio.gather(
            *(callback() for name, callback in callbacks.items() if name != "delayed_state"),
        )
        assert scenario_runs == ["schedule", "state", "custom"]
        assert journal_writes == ["smart"]
        assert command_calls == ["smart"]

        delayed_state = callbacks["delayed_state"]
        assert callable(delayed_state)
        delayed = asyncio.create_task(delayed_state())
        coordinator.cancel()
        assert latch.is_open is False
        await delayed
        await asyncio.gather(
            *(callback() for callback in callbacks.values()),
        )
        assert scenario_runs == ["schedule", "state", "custom"]
        assert journal_writes == ["smart"]
        assert command_calls == ["smart"]
        assert cleaned == ["cleanup"]

        pre_cleanup_latch = ActivationLatch()
        pre_cleanup_order: list[str] = []
        completed: ManagedSwitchStartupCoordinator

        def pre_cleanup() -> None:
            pre_cleanup_order.append("cleanup")

        def pre_cleanup_revoke() -> None:
            pre_cleanup_order.append("revoke")
            pre_cleanup_latch.close()

        async def completed_activation() -> _Activation:
            asyncio.get_running_loop().call_soon(completed.cancel)
            return _Activation(
                pre_cleanup,
                pre_cleanup_latch.open,
                pre_cleanup_revoke,
            )

        completed = ManagedSwitchStartupCoordinator(
            _Service(_Catalog(_required_targets())), _Migration(), completed_activation
        )
        await completed.async_start()
        assert completed.ready is False
        assert pre_cleanup_latch.is_open is False
        assert pre_cleanup_order == ["revoke", "cleanup"]

    asyncio.run(exercise())


def test_final_incomplete_snapshot_exhausts_retry_without_mutation() -> None:
    async def exercise() -> None:
        states = []
        activations = 0

        async def activate() -> None:
            nonlocal activations
            activations += 1

        service = _Service(_Catalog(()))
        migration = _Migration()
        coordinator = ManagedSwitchStartupCoordinator(
            service,
            migration,
            activate,
            status_publisher=states.append,
        )

        await coordinator.async_start()
        await service.publish(_Catalog(_required_targets()[:-1]), final=True)

        assert states[-1] == {
            "state": "blocked",
            "reason": "catalog_retry_exhausted",
        }
        assert migration.calls == 0
        assert activations == 0
        assert service.observers == []
        assert coordinator.ready is False

    asyncio.run(exercise())


def test_non_catalog_migration_failure_is_terminal_and_fails_closed() -> None:
    async def exercise() -> None:
        states = []
        activations = 0

        async def activate() -> None:
            nonlocal activations
            activations += 1

        service = _Service(_Catalog(_required_targets()))
        migration = _Migration(RuntimeError("CAS conflict with private detail"))
        coordinator = ManagedSwitchStartupCoordinator(
            service,
            migration,
            activate,
            status_publisher=states.append,
        )

        await coordinator.async_start()

        assert states[-1] == {
            "state": "blocked",
            "reason": "verified_migration_failed",
        }
        assert migration.calls == 1
        assert activations == 0
        assert service.observers == []
        assert coordinator.ready is False

    asyncio.run(exercise())


def test_release_sources_are_loaded_through_executor_boundary() -> None:
    async def exercise() -> None:
        jobs = []

        async def add_executor_job(target, *args):
            jobs.append((target, args))
            return target(*args)

        entries = await async_load_managed_switch_migration_entries(
            add_executor_job
        )

        assert len(jobs) == 1
        assert len(entries) == 3
        assert all(entry.source for entry in entries)

    asyncio.run(exercise())


def test_binding_phase_completes_before_runtime_activation() -> None:
    async def exercise() -> None:
        order = []

        class OrderedMigration(_Migration):
            def __init__(self, name: str) -> None:
                super().__init__()
                self.name = name

            async def async_apply(self) -> str:
                order.append(self.name)
                return await super().async_apply()

        async def activate() -> None:
            order.append("activate")

        coordinator = ManagedSwitchStartupCoordinator(
            _Service(_Catalog(_required_targets())),
            OrderedMigration("managed-switches"),
            activate,
            binding_migration=OrderedMigration("managed-switch-bindings"),
        )

        await coordinator.async_start()

        assert order == [
            "managed-switches",
            "managed-switch-bindings",
            "activate",
        ]
        assert coordinator.ready is True

    asyncio.run(exercise())


def test_binding_phase_failure_blocks_runtime_after_phase_a() -> None:
    async def exercise() -> None:
        states = []
        activations = 0
        phase_a = _Migration()
        phase_b = _Migration(RuntimeError("binding conflict"))

        async def activate() -> None:
            nonlocal activations
            activations += 1

        coordinator = ManagedSwitchStartupCoordinator(
            _Service(_Catalog(_required_targets())),
            phase_a,
            activate,
            binding_migration=phase_b,
            status_publisher=states.append,
        )

        await coordinator.async_start()

        assert phase_a.calls == 1
        assert phase_b.calls == 1
        assert activations == 0
        assert coordinator.activation_authorized is False
        assert coordinator.ready is False
        assert states[-1] == {
            "state": "blocked",
            "reason": "verified_migration_failed",
        }

    asyncio.run(exercise())


def load_tests(
    loader: unittest.TestLoader,
    tests: unittest.TestSuite,
    pattern: str | None,
) -> unittest.TestSuite:
    """Expose function-style startup cases to the release runner."""

    del loader, tests, pattern
    suite = unittest.TestSuite()
    for name, case in sorted(globals().items()):
        if name.startswith("test_") and callable(case):
            suite.addTest(unittest.FunctionTestCase(case))
    return suite
