from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass, replace as dataclass_replace

from custom_components.hausman_hub.application.tambur_room_migration import (
    TAMBUR_INPUT_TARGET_IDS,
    TAMBUR_NATIVE_COMPETITORS,
    TAMBUR_PRESENCE_TARGET_IDS,
    TAMBUR_ROOM_PLAN,
    TAMBUR_SCENARIO_ID,
    TamburNativeAutomationMigration,
    TamburRoomMigration,
    TamburRoomMigrationConflict,
    TamburRoomStartupCoordinator,
)
from custom_components.hausman_hub.application.tambur_decision_runtime import (
    TamburDecisionRuntime,
)
from custom_components.hausman_hub.application.native_automation_migration import (
    HomeAssistantNativeAutomationAdapter,
)
from custom_components.hausman_hub.application.scenario_node_red import (
    NodeRedBackendError,
)
from custom_components.hausman_hub.application.scenario_node_red_decision import (
    prepare_tambur_decision_bundle,
    tambur_decision_global_nodes,
)
from custom_components.hausman_hub.application.scenario_service import (
    ScenarioServiceError,
)
from custom_components.hausman_hub.domain.scenarios import ScenarioRegistry
from tests.test_managed_switch_migration_service import (
    Backend,
    MigrationReceiptStore,
    RegistryStore,
    _native_hass,
    _registry_from_inventory58,
    _service,
)


@dataclass(frozen=True)
class _Device:
    target_id: str


class _Catalog:
    def __init__(self, target_ids: tuple[str, ...]) -> None:
        self._devices = {target_id: _Device(target_id) for target_id in target_ids}

    def device(self, target_id: str) -> _Device | None:
        return self._devices.get(target_id)


class _Store:
    def __init__(self, value: object | None = None) -> None:
        self.value = copy.deepcopy(value)
        self.saves = 0

    async def async_load(self) -> object | None:
        return copy.deepcopy(self.value)

    async def async_save(self, value: object) -> None:
        self.saves += 1
        self.value = copy.deepcopy(value)


class _ScopedService:
    def __init__(self, registry: dict[str, dict[str, object]]) -> None:
        self.registry = copy.deepcopy(registry)
        self.before = copy.deepcopy(registry[TAMBUR_SCENARIO_ID])
        self.staged_flows: set[str] = set()
        self.calls: list[str] = []
        self.operation_permits: list[tuple[object, object, object]] = []
        self.cancelled_runs: list[object] = []
        self.catalog = _Catalog(
            (
                "entity_156050daca86aa6c",
                "entity_402b26d150a1ef3f",
                "entity_10b78187426f8485",
                "entity_6b9ccdab9bb484b2",
                "entity_71859313239a14e4",
                "entity_cd0098e5ff95da46",
                "entity_fbdf27871edb89bf",
                "entity_b47991988cc6b9f3",
                "entity_170c7a4e2505b803",
            )
        )

    def current_catalog(self) -> _Catalog:
        return self.catalog

    def add_catalog_warmup_observer(self, _observer):
        return lambda: None

    def _issue_tambur_room_migration_operation_permit(
        self, plan, *, operation_run
    ):
        for saved_run, saved_plan, permit in self.operation_permits:
            if saved_run is operation_run:
                return permit
        permit = object()
        self.operation_permits.append((operation_run, plan, permit))
        return permit

    def _bind_tambur_room_migration_operation_permit(
        self, plan, *, journal, operation_run, operation_permit
    ):
        assert any(
            saved_run is operation_run
            and saved_plan is plan
            and permit is operation_permit
            for saved_run, saved_plan, permit in self.operation_permits
        )

    def _cancel_tambur_room_migration_operation(self, _plan, *, operation_run):
        if not any(saved_run is operation_run for saved_run in self.cancelled_runs):
            self.cancelled_runs.append(operation_run)

    async def async_capture_tambur_room_migration(self, _plan):
        self.calls.append("capture")
        return copy.deepcopy(self.registry[TAMBUR_SCENARIO_ID])

    async def async_stage_tambur_room_migration(self, _plan, *, journal, on_staged):
        self.calls.append("stage")
        self.staged_flows.add("2886468f3eaa5735")
        await on_staged({"state": "applied", "flowId": "2886468f3eaa5735"})

    async def async_apply_tambur_room_migration(
        self,
        _plan,
        *,
        journal,
        on_registry,
        operation_run,
        operation_permit,
    ):
        assert any(
            saved_run is operation_run and permit is operation_permit
            for saved_run, _saved_plan, permit in self.operation_permits
        )
        self.calls.append("registry_apply")
        current = self.registry[TAMBUR_SCENARIO_ID]
        assert current == journal["before"]
        self.registry[TAMBUR_SCENARIO_ID] = {
            **current,
            "revision": 9,
            "flowId": "2886468f3eaa5735",
            "topology": "tambur-decision-eight-node-v1",
            "topologyHash": "4ea5bb05b278a2c2ef9e1dbcfd67b85d40f1708a5e512d64f147ab03b6950ff0",
        }
        await on_registry({"state": "applied"})

    async def async_verify_tambur_room_migration(self, _plan, *, journal, require_final):
        self.calls.append("verify")
        assert self.registry[TAMBUR_SCENARIO_ID]["flowId"] == "2886468f3eaa5735"

    async def async_finalize_tambur_room_migration(self, _plan, *, journal, on_registry):
        self.calls.append("finalize")
        await on_registry({"state": "finalized"})

    async def async_commit_tambur_room_migration(
        self, _plan, *, journal, operation_run, operation_permit
    ):
        assert any(
            saved_run is operation_run and permit is operation_permit
            for saved_run, _saved_plan, permit in self.operation_permits
        )
        assert not any(
            cancelled_run is operation_run for cancelled_run in self.cancelled_runs
        )
        self.calls.append("commit")


class _NativeRoomHandover:
    def __init__(self, states: dict[str, str]) -> None:
        self.states = states
        self.before = copy.deepcopy(states)
        self.applies = 0

    async def async_require_ready(self) -> None:
        return None

    async def async_apply(self) -> str:
        self.applies += 1
        for entity_id in TAMBUR_NATIVE_COMPETITORS:
            self.states[entity_id] = "off"
        return "completed"

    async def async_verify_completed(self) -> bool:
        return all(self.states[item] == "off" for item in TAMBUR_NATIVE_COMPETITORS)

    async def async_rollback(self) -> bool:
        self.states.clear()
        self.states.update(copy.deepcopy(self.before))
        return True


def test_room_only_startup_and_restart_preserve_every_foreign_object() -> None:
    async def exercise() -> None:
        curtains_v5 = {
            "id": "scenario_msirqdih",
            "revision": 5,
            "name": "synthetic owner curtains with cabinet changes",
            "steps": [{"type": "cover", "target": "cabinet"}],
        }
        registry = {
            TAMBUR_SCENARIO_ID: {
                "id": TAMBUR_SCENARIO_ID,
                "revision": 8,
                "flowId": "a7a6f6b13cc83ca0",
                "topology": "managed-three-node-v1",
            },
            "scenario_msirqdih": copy.deepcopy(curtains_v5),
            "system-foreign-room": {"id": "system-foreign-room", "revision": 17},
        }
        registry_before = copy.deepcopy(registry)
        native_states = {
            TAMBUR_NATIVE_COMPETITORS[0]: "on",
            TAMBUR_NATIVE_COMPETITORS[1]: "on",
            "automation.foreign_owner_rule": "on",
        }
        native_before = copy.deepcopy(native_states)
        global_receipt = _Store(
            {
                "migrationId": "managed-switches",
                "version": 2,
                "state": "completed",
                "manifestHash": "a3554f0a7108160238cbd4fd49f2f643ad3d446d7f344dec618d9a0d979d2c60",
            }
        )
        room_receipt = _Store()
        service = _ScopedService(registry)
        native = _NativeRoomHandover(native_states)
        activations = []

        async def activate(scope):
            activations.append(scope)

        migration = TamburRoomMigration(
            service,
            room_receipt,
            global_receipt_store=global_receipt,
            native_automation_migration=native,
            migration_lock=asyncio.Lock(),
        )
        startup = TamburRoomStartupCoordinator(service, migration, activate)
        await startup.async_start()

        assert startup.ready is True
        assert set(registry) == set(registry_before)
        assert registry["scenario_msirqdih"] == curtains_v5
        assert registry["system-foreign-room"] == registry_before["system-foreign-room"]
        assert service.staged_flows == {"2886468f3eaa5735"}
        assert native_states["automation.foreign_owner_rule"] == native_before["automation.foreign_owner_rule"]
        assert all(native_states[item] == "off" for item in TAMBUR_NATIVE_COMPETITORS)
        assert len(activations) == 1
        assert activations[0].scenario_ids == (TAMBUR_SCENARIO_ID,)
        assert activations[0].presence_target_ids == TAMBUR_PRESENCE_TARGET_IDS
        assert global_receipt.saves == 0

        first_registry = copy.deepcopy(registry)
        first_room_saves = room_receipt.saves
        first_native_applies = native.applies
        restart = TamburRoomStartupCoordinator(
            service,
            TamburRoomMigration(
                service,
                room_receipt,
                global_receipt_store=global_receipt,
                native_automation_migration=native,
                migration_lock=asyncio.Lock(),
            ),
            activate,
        )
        await restart.async_start()

        assert restart.ready is True
        assert registry == first_registry
        assert room_receipt.saves == first_room_saves
        assert native.applies == first_native_applies
        assert global_receipt.saves == 0
        assert len(activations) == 2

    asyncio.run(exercise())


class _DecisionBackend(Backend):
    def __init__(self) -> None:
        super().__init__()
        self.decision_present = False
        self.global_revision = 1
        self.fail_prepare_after_write = False

    async def _async_global_snapshot(self):
        bundle = prepare_tambur_decision_bundle()
        nodes = tambur_decision_global_nodes(bundle) if self.decision_present else []
        return f"revision.{self.global_revision}", copy.deepcopy(nodes)

    async def _async_tambur_decision_snapshot(self):
        if not self.decision_present:
            raise NodeRedBackendError("missing synthetic decision graph")
        bundle = prepare_tambur_decision_bundle()
        return f"revision.{self.global_revision}", bundle["topologyHash"]

    async def async_prepare_tambur_decision_bundle(self):
        created = not self.decision_present
        if created:
            self.decision_present = True
            self.global_revision += 1
        bundle = prepare_tambur_decision_bundle()
        if self.fail_prepare_after_write:
            self.fail_prepare_after_write = False
            raise OSError("synthetic lost response")
        return {
            "created": created,
            "flowId": bundle["id"],
            "revision": f"revision.{self.global_revision}",
            "topologyHash": bundle["topologyHash"],
        }

    async def _raw_request(self, method, path, *, payload, ingress):
        assert (method, path, ingress) == ("POST", "/flows", True)
        assert payload["rev"] == f"revision.{self.global_revision}"
        self.decision_present = False
        self.global_revision += 1
        return 200, {"rev": f"revision.{self.global_revision}"}


class _MemoryNativeStore:
    def __init__(self) -> None:
        self.value = None

    async def async_load(self):
        return copy.deepcopy(self.value)

    async def async_save(self, value):
        self.value = copy.deepcopy(value)


def _owner_changed_registry() -> ScenarioRegistry:
    baseline = _registry_from_inventory58()
    curtains = baseline.scenario("scenario_msirqdih")
    assert curtains is not None
    changed = dataclass_replace(
        curtains,
        revision=5,
        description="synthetic owner curtains revision 5",
    )
    return ScenarioRegistry(
        scenarios=tuple(
            changed if item.id == changed.id else item for item in baseline.scenarios
        )
    )


async def _prepare_service_owned_tambur_operation():
    registry = _owner_changed_registry()
    registry_store = RegistryStore(registry)
    backend = _DecisionBackend()
    service = _service(registry_store, backend)
    await service.async_load()
    operation_run = object()
    operation_permit = service._issue_tambur_room_migration_operation_permit(  # noqa: SLF001
        TAMBUR_ROOM_PLAN,
        operation_run=operation_run,
    )
    captured = await service.async_capture_tambur_room_migration(TAMBUR_ROOM_PLAN)
    journal = {
        "before": captured,
        "staged": {"state": "intent"},
        "registry": {"state": "pending"},
        "after": None,
    }
    service._bind_tambur_room_migration_operation_permit(  # noqa: SLF001
        TAMBUR_ROOM_PLAN,
        journal=journal,
        operation_run=operation_run,
        operation_permit=operation_permit,
    )

    async def on_staged(value):
        journal["staged"] = copy.deepcopy(dict(value))

    async def on_registry(value):
        journal["registry"] = copy.deepcopy(dict(value))

    await service.async_stage_tambur_room_migration(
        TAMBUR_ROOM_PLAN,
        journal=journal,
        on_staged=on_staged,
    )
    await service.async_apply_tambur_room_migration(
        TAMBUR_ROOM_PLAN,
        journal=journal,
        on_registry=on_registry,
        operation_run=operation_run,
        operation_permit=operation_permit,
    )
    await service.async_finalize_tambur_room_migration(
        TAMBUR_ROOM_PLAN,
        journal=journal,
        on_registry=on_registry,
    )
    journal["after"] = await service.async_capture_tambur_room_migration(
        TAMBUR_ROOM_PLAN
    )
    return service, journal, operation_run, operation_permit


def test_service_owned_operation_permit_rejects_every_forged_identity() -> None:
    async def exercise() -> None:
        service, journal, operation_run, operation_permit = (
            await _prepare_service_owned_tambur_operation()
        )
        transaction = service._tambur_room_migration_transaction  # noqa: SLF001
        assert transaction is not None

        async def reject(
            *,
            attempted_plan=TAMBUR_ROOM_PLAN,
            expected_transaction=transaction,
            **overrides,
        ):
            arguments = {
                "journal": journal,
                "operation_run": operation_run,
                "operation_permit": operation_permit,
            }
            arguments.update(overrides)
            try:
                await service.async_commit_tambur_room_migration(
                    attempted_plan,
                    **arguments,
                )
            except ScenarioServiceError as error:
                assert error.status == 409
            else:
                raise AssertionError("forged operation permit must fail closed")
            assert (  # noqa: SLF001
                service._tambur_room_migration_transaction is expected_transaction
            )

        try:
            await service.async_commit_tambur_room_migration(
                TAMBUR_ROOM_PLAN,
                journal=journal,
            )
        except TypeError:
            pass
        else:
            raise AssertionError("missing operation identity must be rejected")
        assert service._tambur_room_migration_transaction is transaction  # noqa: SLF001

        await reject(operation_permit=None)
        await reject(operation_permit=object())
        await reject(
            operation_permit=type("FakePermit", (), {"cancelled": False})()
        )
        await reject(operation_run=object())
        await reject(journal=copy.deepcopy(journal))
        await reject(attempted_plan=dataclass_replace(TAMBUR_ROOM_PLAN))

        foreign_registry_store = RegistryStore(_owner_changed_registry())
        foreign_service = _service(foreign_registry_store, _DecisionBackend())
        await foreign_service.async_load()
        foreign_run = object()
        foreign_permit = (
            foreign_service._issue_tambur_room_migration_operation_permit(  # noqa: SLF001
                TAMBUR_ROOM_PLAN,
                operation_run=foreign_run,
            )
        )
        await reject(operation_permit=foreign_permit)

        async def commit_from_foreign_task():
            await reject()

        await asyncio.create_task(commit_from_foreign_task())

        equal_transaction = dataclass_replace(transaction)
        service._tambur_room_migration_transaction = equal_transaction  # noqa: SLF001
        await reject(expected_transaction=equal_transaction)
        service._tambur_room_migration_transaction = transaction  # noqa: SLF001

        await service.async_commit_tambur_room_migration(
            TAMBUR_ROOM_PLAN,
            journal=journal,
            operation_run=operation_run,
            operation_permit=operation_permit,
        )
        assert service._tambur_room_migration_transaction is None  # noqa: SLF001

        try:
            await service.async_commit_tambur_room_migration(
                TAMBUR_ROOM_PLAN,
                journal=journal,
                operation_run=operation_run,
                operation_permit=operation_permit,
            )
        except ScenarioServiceError as error:
            assert error.status == 409
        else:
            raise AssertionError("used operation permit must stay retired")
        assert service._tambur_room_migration_transaction is None  # noqa: SLF001

    asyncio.run(exercise())


def test_service_owned_operation_cancel_is_sticky_and_cannot_be_forged_clear() -> None:
    async def exercise() -> None:
        service, journal, operation_run, operation_permit = (
            await _prepare_service_owned_tambur_operation()
        )
        transaction = service._tambur_room_migration_transaction  # noqa: SLF001
        service._cancel_tambur_room_migration_operation(  # noqa: SLF001
            TAMBUR_ROOM_PLAN,
            operation_run=operation_run,
        )
        try:
            operation_permit.cancelled = False
        except AttributeError:
            pass
        else:
            raise AssertionError("caller must not own cancellation state")
        replacement = service._issue_tambur_room_migration_operation_permit(  # noqa: SLF001
            TAMBUR_ROOM_PLAN,
            operation_run=operation_run,
        )
        assert replacement is operation_permit

        try:
            await service.async_commit_tambur_room_migration(
                TAMBUR_ROOM_PLAN,
                journal=journal,
                operation_run=operation_run,
                operation_permit=replacement,
            )
        except ScenarioServiceError as error:
            assert error.status == 409
        else:
            raise AssertionError("cancelled operation must never commit")
        assert service._tambur_room_migration_transaction is transaction  # noqa: SLF001

    asyncio.run(exercise())


def test_completed_recovery_with_active_transaction_uses_new_permit_to_clean_up() -> None:
    async def exercise() -> None:
        service, journal, stale_run, stale_permit = (
            await _prepare_service_owned_tambur_operation()
        )
        transaction = service._tambur_room_migration_transaction  # noqa: SLF001
        room_store = _Store(
            {
                "migrationId": "tambur-room-decision",
                "version": 1,
                "state": "completed",
                "planHash": "7a1108c6396662a6d65fc060f6fb530671f2a50a2d35c30adb90a198111af850",
                "stage": "binding",
                "journal": journal,
            }
        )
        native = _NativeRoomHandover(
            {item: "off" for item in TAMBUR_NATIVE_COMPETITORS}
        )
        migration = TamburRoomMigration(
            service,
            room_store,
            global_receipt_store=MigrationReceiptStore(
                {
                    "migrationId": "managed-switches",
                    "version": 2,
                    "state": "completed",
                    "manifestHash": "a3554f0a7108160238cbd4fd49f2f643ad3d446d7f344dec618d9a0d979d2c60",
                }
            ),
            native_automation_migration=native,
            migration_lock=asyncio.Lock(),
        )

        assert transaction is not None
        assert await migration.async_apply(operation_run=object()) == "completed"
        assert service._tambur_room_migration_transaction is None  # noqa: SLF001
        assert room_store.saves == 0

        try:
            await service.async_commit_tambur_room_migration(
                TAMBUR_ROOM_PLAN,
                journal=journal,
                operation_run=stale_run,
                operation_permit=stale_permit,
            )
        except ScenarioServiceError as error:
            assert error.status == 409
        else:
            raise AssertionError("superseded permit must stay stale")
        assert service._tambur_room_migration_transaction is None  # noqa: SLF001

    asyncio.run(exercise())


def test_real_scoped_service_and_native_adapter_migrate_only_tambur() -> None:
    async def exercise() -> None:
        registry = _owner_changed_registry()
        before_storage = registry.to_storage()
        registry_store = RegistryStore(registry)
        backend = _DecisionBackend()
        service = _service(registry_store, backend)
        await service.async_load()
        hass, services = _native_hass(context_prefix="tambur", updated_hour=12)
        native_store = _MemoryNativeStore()
        native = TamburNativeAutomationMigration(
            HomeAssistantNativeAutomationAdapter(hass), native_store
        )
        room_store = _Store()
        migration = TamburRoomMigration(
            service,
            room_store,
            global_receipt_store=MigrationReceiptStore(
                {
                    "migrationId": "managed-switches",
                    "version": 2,
                    "state": "completed",
                    "manifestHash": "a3554f0a7108160238cbd4fd49f2f643ad3d446d7f344dec618d9a0d979d2c60",
                }
            ),
            native_automation_migration=native,
            migration_lock=asyncio.Lock(),
        )

        assert await migration.async_apply(operation_run=object()) == "completed"

        after_storage = registry_store.registry.to_storage()
        before_by_id = {item["id"]: item for item in before_storage["scenarios"]}
        after_by_id = {item["id"]: item for item in after_storage["scenarios"]}
        assert set(after_by_id) == set(before_by_id)
        assert all(
            after_by_id[item_id] == before_by_id[item_id]
            for item_id in before_by_id
            if item_id != TAMBUR_SCENARIO_ID
        )
        assert after_by_id["scenario_msirqdih"]["revision"] == 5
        migrated = registry_store.registry.scenario(TAMBUR_SCENARIO_ID)
        assert migrated is not None and migrated.revision == 9
        assert migrated.definition.node_red.flow_id == TAMBUR_ROOM_PLAN.decision_flow_id
        assert migrated.definition.node_red.input_target_ids == TAMBUR_ROOM_PLAN.input_target_ids
        assert backend.decision_present is True
        assert [item[2]["entity_id"] for item in services.calls] == list(
            TAMBUR_NATIVE_COMPETITORS
        )
        assert all(item[1] == "turn_off" for item in services.calls)

        saved_count = len(registry_store.saved)
        native_call_count = len(services.calls)
        room_save_count = room_store.saves
        assert await migration.async_apply(operation_run=object()) == "completed"
        assert len(registry_store.saved) == saved_count
        assert len(services.calls) == native_call_count
        assert room_store.saves == room_save_count

    asyncio.run(exercise())


def test_lost_stage_response_is_compensated_and_foreign_cas_drift_blocks() -> None:
    async def exercise() -> None:
        registry = _owner_changed_registry()
        registry_store = RegistryStore(registry)
        backend = _DecisionBackend()
        backend.fail_prepare_after_write = True
        service = _service(registry_store, backend)
        await service.async_load()
        room_store = _Store()
        native = _NativeRoomHandover(
            {item: "on" for item in TAMBUR_NATIVE_COMPETITORS}
        )
        migration = TamburRoomMigration(
            service,
            room_store,
            global_receipt_store=MigrationReceiptStore(
                {
                    "migrationId": "managed-switches",
                    "version": 2,
                    "state": "completed",
                    "manifestHash": "a3554f0a7108160238cbd4fd49f2f643ad3d446d7f344dec618d9a0d979d2c60",
                }
            ),
            native_automation_migration=native,
            migration_lock=asyncio.Lock(),
        )
        try:
            await migration.async_apply(operation_run=object())
        except TamburRoomMigrationConflict as error:
            assert error.stage == "stage"
        else:
            raise AssertionError("lost stage response must fail closed")
        assert backend.decision_present is False
        assert registry_store.registry == registry

        captured = await service.async_capture_tambur_room_migration(TAMBUR_ROOM_PLAN)
        foreign = registry.scenario("scenario_msirqdih")
        assert foreign is not None
        drifted = ScenarioRegistry(
            scenarios=tuple(
                dataclass_replace(foreign, description="manual drift")
                if item.id == foreign.id
                else item
                for item in registry.scenarios
            )
        )
        registry_store.registry = drifted
        service._registry = drifted  # noqa: SLF001 - synthetic concurrent owner edit
        journal = {
            "before": captured,
            "staged": {"state": "applied", "created": False, "flowId": TAMBUR_ROOM_PLAN.decision_flow_id, "topologyHash": TAMBUR_ROOM_PLAN.decision_topology_hash},
            "registry": {"state": "pending"},
            "after": None,
        }
        assert await service.async_rollback_tambur_room_migration(
            TAMBUR_ROOM_PLAN, journal=journal
        ) is False
        assert registry_store.registry == drifted

    asyncio.run(exercise())


def test_final_verification_failure_rolls_back_receipts_and_can_retry() -> None:
    async def exercise() -> None:
        registry = _owner_changed_registry()
        registry_store = RegistryStore(registry)
        backend = _DecisionBackend()
        service = _service(registry_store, backend)
        await service.async_load()
        original_verify = service.async_verify_tambur_room_migration
        final_verifications = 0

        async def verify_with_one_failure(
            plan, *, journal, require_final
        ):
            nonlocal final_verifications
            result = await original_verify(
                plan, journal=journal, require_final=require_final
            )
            if require_final:
                final_verifications += 1
            if require_final and final_verifications == 2:
                raise OSError("synthetic final verification failure")
            return result

        service.async_verify_tambur_room_migration = verify_with_one_failure
        hass, services = _native_hass(context_prefix="retry", updated_hour=12)
        native_store = _MemoryNativeStore()
        native = TamburNativeAutomationMigration(
            HomeAssistantNativeAutomationAdapter(hass), native_store
        )
        room_store = _Store()
        migration = TamburRoomMigration(
            service,
            room_store,
            global_receipt_store=MigrationReceiptStore(
                {
                    "migrationId": "managed-switches",
                    "version": 2,
                    "state": "completed",
                    "manifestHash": "a3554f0a7108160238cbd4fd49f2f643ad3d446d7f344dec618d9a0d979d2c60",
                }
            ),
            native_automation_migration=native,
            migration_lock=asyncio.Lock(),
        )

        try:
            await migration.async_apply(operation_run=object())
        except TamburRoomMigrationConflict as error:
            assert error.stage == "binding"
        else:
            raise AssertionError("failed final verification must block activation")

        assert registry_store.registry == registry
        assert backend.decision_present is False
        assert room_store.value["state"] == "prepared"
        assert room_store.value["journal"]["staged"] is None
        assert room_store.value["journal"]["registry"] == {"state": "pending"}
        assert room_store.value["journal"]["after"] is None
        assert native_store.value["state"] == "prepared"
        assert native_store.value["operations"] == {}
        assert all(
            hass.states.get(entity_id).state == "on"
            for entity_id in TAMBUR_NATIVE_COMPETITORS
        )

        assert await migration.async_apply(operation_run=object()) == "completed"
        assert registry_store.registry.scenario(TAMBUR_SCENARIO_ID).revision == 9
        assert backend.decision_present is True
        assert room_store.value["state"] == "completed"
        assert native_store.value["state"] == "completed"
        assert len(services.calls) == 6

    asyncio.run(exercise())


def test_partial_native_handover_is_compensated_before_retry_receipt() -> None:
    async def exercise() -> None:
        registry = _owner_changed_registry()
        registry_store = RegistryStore(registry)
        backend = _DecisionBackend()
        service = _service(registry_store, backend)
        await service.async_load()
        room_store = _Store()

        class PartialNative:
            def __init__(self) -> None:
                self.changed = False
                self.rollbacks = 0

            async def async_require_ready(self) -> None:
                return None

            async def async_apply(self) -> str:
                self.changed = True
                raise OSError("synthetic second native write failure")

            async def async_rollback(self) -> bool:
                self.rollbacks += 1
                self.changed = False
                return True

        native = PartialNative()
        migration = TamburRoomMigration(
            service,
            room_store,
            global_receipt_store=MigrationReceiptStore(
                {
                    "migrationId": "managed-switches",
                    "version": 2,
                    "state": "completed",
                    "manifestHash": "a3554f0a7108160238cbd4fd49f2f643ad3d446d7f344dec618d9a0d979d2c60",
                }
            ),
            native_automation_migration=native,
            migration_lock=asyncio.Lock(),
        )

        try:
            await migration.async_apply(operation_run=object())
        except TamburRoomMigrationConflict as error:
            assert error.stage == "native_handover"
        else:
            raise AssertionError("partial native handover must fail closed")

        assert native.rollbacks == 1
        assert native.changed is False
        assert registry_store.registry == registry
        assert backend.decision_present is False
        assert room_store.value["state"] == "prepared"
        assert room_store.value["journal"]["staged"] is None

    asyncio.run(exercise())


def test_incomplete_global_receipt_never_activates_task3() -> None:
    async def exercise() -> None:
        registry = _owner_changed_registry()
        service = _service(RegistryStore(registry), _DecisionBackend())
        await service.async_load()
        activations = []
        statuses = []
        coordinator = TamburRoomStartupCoordinator(
            service,
            TamburRoomMigration(
                service,
                _Store(),
                global_receipt_store=MigrationReceiptStore(
                    {
                        "migrationId": "managed-switches",
                        "version": 4,
                        "state": "prepared",
                    }
                ),
                native_automation_migration=_NativeRoomHandover(
                    {item: "on" for item in TAMBUR_NATIVE_COMPETITORS}
                ),
                migration_lock=asyncio.Lock(),
            ),
            lambda scope: activations.append(scope),
            status_publisher=statuses.append,
        )

        await coordinator.async_start()

        assert coordinator.ready is False
        assert activations == []
        assert statuses == [{"state": "blocked", "stage": "receipt_load"}]

    asyncio.run(exercise())


def test_room_commit_activates_once_and_cancel_revokes_scope() -> None:
    async def exercise() -> None:
        catalog = _Catalog(
                (
                    "entity_156050daca86aa6c",
                    "entity_402b26d150a1ef3f",
                    "entity_10b78187426f8485",
                    "entity_6b9ccdab9bb484b2",
                    "entity_71859313239a14e4",
                    "entity_cd0098e5ff95da46",
                    "entity_fbdf27871edb89bf",
                    "entity_b47991988cc6b9f3",
                    "entity_170c7a4e2505b803",
                )
        )
        service = type(
            "CatalogService",
            (),
            {
                "current_catalog": lambda self: catalog,
                "add_catalog_warmup_observer": lambda self, observer: lambda: None,
            },
        )()
        migration = type(
            "Migration",
            (),
            {
                "async_apply": lambda self, **_kwargs: asyncio.sleep(
                    0, result="completed"
                ),
                "cancel": lambda self, **_kwargs: None,
            },
        )()
        calls: list[str] = []

        class Activation:
            cleanup = staticmethod(lambda: calls.append("cleanup"))
            commit = staticmethod(lambda: calls.append("commit"))
            revoke = staticmethod(lambda: calls.append("revoke"))

        async def activate(scope):
            assert scope.scenario_ids == (TAMBUR_SCENARIO_ID,)
            assert scope.presence_target_ids == TAMBUR_PRESENCE_TARGET_IDS
            calls.append("prepare")
            return Activation()

        coordinator = TamburRoomStartupCoordinator(service, migration, activate)
        await coordinator.async_start()
        await coordinator.async_start()
        assert coordinator.ready is True
        assert calls == ["prepare", "commit"]
        coordinator.cancel()
        assert calls == ["prepare", "commit", "revoke", "cleanup"]

    asyncio.run(exercise())


def test_cancel_during_apply_waits_for_result_and_never_activates_or_retries() -> None:
    async def exercise() -> None:
        entered = asyncio.Event()
        release = asyncio.Event()
        apply_calls = 0
        task_cancelled = False
        late_observer = None

        class CatalogService:
            def current_catalog(self):
                return _Catalog(TAMBUR_INPUT_TARGET_IDS)

            def add_catalog_warmup_observer(self, observer):
                nonlocal late_observer
                late_observer = observer
                return lambda: None

        class Migration:
            def __init__(self):
                self.cancelled_run = None

            async def async_apply(self, *, operation_run):
                nonlocal apply_calls, task_cancelled
                apply_calls += 1
                entered.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    task_cancelled = True
                    raise
                assert self.cancelled_run is operation_run
                return "completed"

            def cancel(self, *, operation_run):
                self.cancelled_run = operation_run

        activations = 0

        async def activate(_scope):
            nonlocal activations
            activations += 1

        coordinator = TamburRoomStartupCoordinator(
            CatalogService(), Migration(), activate
        )
        task = asyncio.create_task(coordinator.async_start())
        await asyncio.wait_for(entered.wait(), timeout=1)

        coordinator.cancel()
        coordinator.cancel()
        await asyncio.sleep(0)
        assert task.done() is False
        assert task_cancelled is False

        release.set()
        await task
        assert apply_calls == 1
        assert activations == 0
        assert coordinator.ready is False

        assert late_observer is not None
        await late_observer(_Catalog(TAMBUR_INPUT_TARGET_IDS), True)
        assert apply_calls == 1
        assert activations == 0

    asyncio.run(exercise())


def test_cancel_during_activation_prep_revokes_and_cleans_without_commit() -> None:
    async def exercise() -> None:
        entered = asyncio.Event()
        release = asyncio.Event()
        calls: list[str] = []

        class Migration:
            async def async_apply(self, *, operation_run):
                assert operation_run is not None
                return "completed"

            def cancel(self, *, operation_run):
                assert operation_run is not None

        class Activation:
            cleanup = staticmethod(lambda: calls.append("cleanup"))
            commit = staticmethod(lambda: calls.append("commit"))
            revoke = staticmethod(lambda: calls.append("revoke"))

        async def activate(_scope):
            calls.append("prepare")
            entered.set()
            await release.wait()
            return Activation()

        service = type(
            "CatalogService",
            (),
            {
                "current_catalog": lambda self: _Catalog(TAMBUR_INPUT_TARGET_IDS),
                "add_catalog_warmup_observer": lambda self, observer: lambda: None,
            },
        )()
        coordinator = TamburRoomStartupCoordinator(service, Migration(), activate)
        task = asyncio.create_task(coordinator.async_start())
        await asyncio.wait_for(entered.wait(), timeout=1)

        coordinator.cancel()
        release.set()
        await task

        assert calls == ["prepare", "revoke", "cleanup"]
        assert coordinator.ready is False

    asyncio.run(exercise())


def test_activation_commit_error_revokes_and_cleans_exactly_once() -> None:
    async def exercise() -> None:
        calls: list[str] = []
        statuses: list[dict[str, str]] = []

        class Migration:
            async def async_apply(self, *, operation_run):
                assert operation_run is not None
                return "completed"

            def cancel(self, *, operation_run):
                assert operation_run is not None

        class Activation:
            cleanup = staticmethod(lambda: calls.append("cleanup"))
            revoke = staticmethod(lambda: calls.append("revoke"))

            @staticmethod
            def commit():
                calls.append("commit")
                raise RuntimeError("synthetic activation commit failure")

        async def activate(_scope):
            calls.append("prepare")
            return Activation()

        service = type(
            "CatalogService",
            (),
            {
                "current_catalog": lambda self: _Catalog(TAMBUR_INPUT_TARGET_IDS),
                "add_catalog_warmup_observer": lambda self, observer: lambda: None,
            },
        )()
        coordinator = TamburRoomStartupCoordinator(
            service, Migration(), activate, status_publisher=statuses.append
        )
        await coordinator.async_start()
        coordinator.cancel()

        assert calls == ["prepare", "commit", "revoke", "cleanup"]
        assert statuses == [{"state": "blocked", "stage": "binding"}]
        assert coordinator.ready is False

    asyncio.run(exercise())


def test_service_commit_checks_cancel_after_lock_before_forgetting_rollback() -> None:
    async def exercise() -> None:
        registry = _owner_changed_registry()
        registry_store = RegistryStore(registry)
        backend = _DecisionBackend()
        service = _service(registry_store, backend)
        await service.async_load()
        entered = asyncio.Event()
        release = asyncio.Event()
        original_commit = service.async_commit_tambur_room_migration

        class SecondEntryGate:
            def __init__(self, base_lock):
                self.base_lock = base_lock
                self.entries = 0

            async def __aenter__(self):
                await self.base_lock.acquire()
                self.entries += 1
                if self.entries == 2:
                    entered.set()
                    await release.wait()
                return self

            async def __aexit__(self, _type, _value, _traceback):
                self.base_lock.release()

        async def gated_commit(
            plan, *, journal, operation_run, operation_permit
        ):
            service._lock = SecondEntryGate(service._lock)  # noqa: SLF001
            return await original_commit(
                plan,
                journal=journal,
                operation_run=operation_run,
                operation_permit=operation_permit,
            )

        service.async_commit_tambur_room_migration = gated_commit
        native_states = {
            item: "on" for item in TAMBUR_NATIVE_COMPETITORS
        }
        native = _NativeRoomHandover(native_states)
        room_store = _Store()
        migration = TamburRoomMigration(
            service,
            room_store,
            global_receipt_store=MigrationReceiptStore(
                {
                    "migrationId": "managed-switches",
                    "version": 2,
                    "state": "completed",
                    "manifestHash": "a3554f0a7108160238cbd4fd49f2f643ad3d446d7f344dec618d9a0d979d2c60",
                }
            ),
            native_automation_migration=native,
            migration_lock=asyncio.Lock(),
        )
        activations = 0

        async def activate(_scope):
            nonlocal activations
            activations += 1

        coordinator = TamburRoomStartupCoordinator(service, migration, activate)
        task = asyncio.create_task(coordinator.async_start())
        await asyncio.wait_for(entered.wait(), timeout=1)
        coordinator.cancel()
        release.set()
        await task

        assert coordinator.ready is False
        assert activations == 0
        assert registry_store.registry == registry
        assert backend.decision_present is False
        assert all(state == "on" for state in native_states.values())
        assert room_store.value["state"] == "prepared"
        assert room_store.value["journal"]["staged"] is None
        assert service._tambur_room_migration_transaction is None  # noqa: SLF001

    asyncio.run(exercise())


def test_cancelled_lost_response_preserves_uncertain_evidence_without_retry() -> None:
    async def exercise() -> None:
        registry = _owner_changed_registry()
        registry_store = RegistryStore(registry)
        entered = asyncio.Event()
        release = asyncio.Event()

        class LostResponseBackend(_DecisionBackend):
            def __init__(self):
                super().__init__()
                self.prepares = 0
                self.drifted = False

            async def async_prepare_tambur_decision_bundle(self):
                self.prepares += 1
                self.decision_present = True
                self.global_revision += 1
                self.drifted = True
                entered.set()
                await release.wait()
                raise OSError("synthetic lost response after foreign drift")

            async def _async_global_snapshot(self):
                revision, nodes = await super()._async_global_snapshot()
                if self.drifted and nodes:
                    nodes[0] = {**nodes[0], "name": "foreign concurrent edit"}
                return revision, nodes

        backend = LostResponseBackend()
        service = _service(registry_store, backend)
        await service.async_load()
        room_store = _Store()
        migration = TamburRoomMigration(
            service,
            room_store,
            global_receipt_store=MigrationReceiptStore(
                {
                    "migrationId": "managed-switches",
                    "version": 2,
                    "state": "completed",
                    "manifestHash": "a3554f0a7108160238cbd4fd49f2f643ad3d446d7f344dec618d9a0d979d2c60",
                }
            ),
            native_automation_migration=_NativeRoomHandover(
                {item: "on" for item in TAMBUR_NATIVE_COMPETITORS}
            ),
            migration_lock=asyncio.Lock(),
        )
        activations = 0

        async def activate(_scope):
            nonlocal activations
            activations += 1

        coordinator = TamburRoomStartupCoordinator(service, migration, activate)
        task = asyncio.create_task(coordinator.async_start())
        await asyncio.wait_for(entered.wait(), timeout=1)
        coordinator.cancel()
        release.set()
        await task

        assert backend.prepares == 1
        assert backend.decision_present is True
        assert room_store.value["state"] == "prepared"
        assert room_store.value["journal"]["staged"] == {"state": "intent"}
        assert registry_store.registry == registry
        assert activations == 0

        await coordinator._async_catalog_snapshot(  # noqa: SLF001
            service.current_catalog(), True
        )
        assert backend.prepares == 1
        assert room_store.value["journal"]["staged"] == {"state": "intent"}

    asyncio.run(exercise())


def test_decision_runtime_subscribes_only_three_presence_inputs_and_stays_closed() -> None:
    async def exercise() -> None:
        state_subscriptions = []
        interval_subscriptions = []
        tasks = []

        class Bridge:
            def __init__(self) -> None:
                self.snapshots = []
                self.executions = []

            async def async_recover(self):
                return {"observationEpoch": 7}

            async def async_snapshot(self, scenario_id, event):
                assert scenario_id == TAMBUR_SCENARIO_ID
                self.snapshots.append(copy.deepcopy(event))
                return {"event": copy.deepcopy(event)}

            async def async_execute_decision(self, decision):
                self.executions.append(copy.deepcopy(decision))
                return {"status": "skipped"}

        class DecisionBackend:
            async def async_calculate_tambur_decision(self, request):
                return {"event": request["event"], "wakeups": []}

        class Observations:
            def __init__(self) -> None:
                self.starts = 0
                self.stops = 0

            def start(self):
                self.starts += 1
                return lambda: setattr(self, "stops", self.stops + 1)

        def track_states(_hass, entities, callback):
            state_subscriptions.append((tuple(entities), callback))
            return lambda: None

        def track_interval(_hass, callback, interval):
            interval_subscriptions.append((callback, interval))
            return lambda: None

        def call_later(_hass, _delay, _callback):
            raise AssertionError("no wakeup is expected")

        def create_task(coroutine):
            task = asyncio.create_task(coroutine)
            tasks.append(task)
            return task

        bridge = Bridge()
        observations = Observations()
        mapping = {
            "binary_sensor.tambur_one": TAMBUR_PRESENCE_TARGET_IDS[0],
            "binary_sensor.tambur_two": TAMBUR_PRESENCE_TARGET_IDS[1],
            "binary_sensor.tambur_three": TAMBUR_PRESENCE_TARGET_IDS[2],
        }
        runtime = TamburDecisionRuntime(
            type("Hass", (), {"async_create_task": staticmethod(create_task)})(),
            DecisionBackend(),
            bridge,
            observations,
            presence_entities=mapping,
            now_ms=lambda: 1_000,
            track_state_changes=track_states,
            track_interval=track_interval,
            call_later=call_later,
        )
        await runtime.async_start()
        assert state_subscriptions[0][0] == tuple(mapping)
        assert len(interval_subscriptions) == 1
        assert observations.starts == 1
        assert len(bridge.snapshots) == 1
        assert bridge.snapshots[0]["kind"] == "recovery"
        assert bridge.executions == []

        callback = state_subscriptions[0][1]
        callback(
            type(
                "Event",
                (),
                {
                    "data": {
                        "entity_id": "binary_sensor.foreign_room",
                        "old_state": type("State", (), {"state": "off"})(),
                        "new_state": type("State", (), {"state": "on"})(),
                    }
                },
            )()
        )
        await asyncio.sleep(0)
        assert bridge.executions == []

        runtime.activate()
        await asyncio.gather(*tasks)
        assert len(bridge.executions) == 1
        assert bridge.executions[0]["event"]["kind"] == "recovery"
        runtime.cancel()
        assert observations.stops == 1

    asyncio.run(exercise())
