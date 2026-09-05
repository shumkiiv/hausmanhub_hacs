from __future__ import annotations

import unittest
import asyncio
from dataclasses import replace
from types import SimpleNamespace

from custom_components.hausman_hub.application.managed_switch_binding_migration import (
    BINDING_MIGRATION_MANIFEST,
)
from custom_components.hausman_hub.application.managed_switch_migration import (
    MANAGED_TOPOLOGY,
    MIGRATION_MANIFEST,
)
from custom_components.hausman_hub.application.scenario_service import ScenarioService
from custom_components.hausman_hub.application.scenarios import (
    ScenarioCatalog,
    ScenarioDeviceEntry,
    ScenarioDeviceProperty,
    ScenarioPropertyOption,
)
from custom_components.hausman_hub.domain.scenarios import (
    Scenario,
    ScenarioAction,
    ScenarioActionType,
    ScenarioComparison,
    ScenarioDefinition,
    ScenarioExecutionBackend,
    ScenarioExecutionMode,
    ScenarioNodeRedGeneratedBy,
    ScenarioNodeRedMetadata,
    ScenarioNodeRedSyncStatus,
    ScenarioRegistry,
    ScenarioTrigger,
    ScenarioTriggerType,
)


SCENARIO_ID = "system-small-corridor-light-controller"
OLD_TARGET = "entity_ff0244d6b760be7e"
NEW_TARGET = "entity_4be32416634e6416"
SOURCE_HASH = "bc9a2c7883046e568a428e355af312953d70f0f504393b063130f516fe5052b1"
INPUTS = next(
    item.input_target_ids for item in MIGRATION_MANIFEST if item.scenario_id == SCENARIO_ID
)
BINDING = BINDING_MIGRATION_MANIFEST[0]


class Store:
    def __init__(self, registry: ScenarioRegistry, *, fail=False) -> None:
        self.registry = registry
        self.saved = []
        self.fail = fail

    async def async_load(self):
        return self.registry

    async def async_save(self, value):
        self.saved.append(value)
        if self.fail:
            self.fail = False
            raise OSError("registry save failed")
        self.registry = value


class Backend:
    async def async_verify_managed_topology(self, scenario_id, flow_id):
        return {
            "source_hash": SOURCE_HASH,
            "topology": MANAGED_TOPOLOGY,
            "revision": "revision.stable",
        }


def registry(*, migrated: bool = False) -> ScenarioRegistry:
    trigger = ScenarioTrigger(
        "manual_chandelier_on",
        ScenarioTriggerType.DEVICE_STATE,
        target_id=NEW_TARGET if migrated else OLD_TARGET,
        target_name=(
            "Выключатель малого коридора"
            if migrated
            else "Выключатель малый коридор"
        ),
        property="state",
        comparison=ScenarioComparison.EQUALS,
        value="on",
    )
    definition = ScenarioDefinition(
        version=1,
        execution_mode=ScenarioExecutionMode.RESTART,
        execution_backend=ScenarioExecutionBackend.NODE_RED,
        node_red=ScenarioNodeRedMetadata(
            flow_id="flow-small",
            flow_revision=9,
            source_hash=SOURCE_HASH,
            generated_by=ScenarioNodeRedGeneratedBy.HAUSMAN,
            sync_status=ScenarioNodeRedSyncStatus.SYNCED,
            input_target_ids=INPUTS,
        ),
        triggers=(trigger,),
        conditions=(),
        actions=(
            ScenarioAction(
                "notify",
                ScenarioActionType.NOTIFICATION,
                message="ok",
            ),
        ),
    )
    return ScenarioRegistry(
        scenarios=(
            Scenario.from_definition(
                SCENARIO_ID,
                SCENARIO_ID,
                definition,
                group="system",
                revision=3 if migrated else 2,
                protected=True,
            ),
        )
    )


class ManagedSwitchBindingMigrationServiceTest(unittest.IsolatedAsyncioTestCase):
    def service(self, store: Store, backend=None) -> ScenarioService:
        catalog = SimpleNamespace(
            devices=(NEW_TARGET,),
            device=lambda target_id: SimpleNamespace(
                target_id=target_id,
                entity_id=f"switch.{target_id}",
            )
            if target_id == NEW_TARGET
            else None,
        )
        return ScenarioService(
            None,
            store,
            catalog,
            node_red_backend=backend or Backend(),
        )

    async def test_phase_b_moves_only_the_manual_trigger_to_revision_three(self) -> None:
        store = Store(registry())
        service = self.service(store)
        await service.async_load()
        apply = getattr(service, "async_apply_managed_switch_binding_migration", None)

        self.assertTrue(callable(apply), "phase B service is missing")
        await apply((BINDING,))

        migrated = store.registry.scenario(SCENARIO_ID)
        self.assertEqual(3, migrated.revision)
        trigger = migrated.definition.triggers[0]
        self.assertEqual(NEW_TARGET, trigger.target_id)
        self.assertEqual("Выключатель малого коридора", trigger.target_name)
        self.assertEqual(SOURCE_HASH, migrated.definition.node_red.source_hash)

    async def test_revision_three_with_new_trigger_is_idempotent(self) -> None:
        store = Store(registry(migrated=True))
        service = self.service(store)
        await service.async_load()

        await service.async_apply_managed_switch_binding_migration(
            BINDING_MIGRATION_MANIFEST
        )
        await service.async_verify_managed_switch_binding_migration(
            BINDING_MIGRATION_MANIFEST
        )
        await service.async_finalize_managed_switch_binding_migration(
            BINDING_MIGRATION_MANIFEST
        )

        self.assertEqual([], store.saved)

    async def test_unexpected_trigger_or_source_hash_fails_without_mutation(self) -> None:
        unexpected = registry()
        scenario = unexpected.scenario(SCENARIO_ID)
        bad_trigger = replace(
            scenario.definition.triggers[0],
            value="off",
        )
        unexpected = ScenarioRegistry(
            scenarios=(
                replace(
                    scenario,
                    definition=replace(
                        scenario.definition,
                        triggers=(bad_trigger,),
                    ),
                ),
            )
        )
        store = Store(unexpected)
        service = self.service(store)
        await service.async_load()
        with self.assertRaises(Exception):
            await service.async_apply_managed_switch_binding_migration(
                BINDING_MIGRATION_MANIFEST
            )
        self.assertEqual([], store.saved)

        wrong_hash = registry()
        scenario = wrong_hash.scenario(SCENARIO_ID)
        wrong_hash = ScenarioRegistry(
            scenarios=(
                replace(
                    scenario,
                    definition=replace(
                        scenario.definition,
                        node_red=replace(
                            scenario.definition.node_red,
                            source_hash="f" * 64,
                        ),
                    ),
                ),
            )
        )
        store = Store(wrong_hash)
        service = self.service(store)
        await service.async_load()
        with self.assertRaises(Exception):
            await service.async_apply_managed_switch_binding_migration(
                BINDING_MIGRATION_MANIFEST
            )
        self.assertEqual([], store.saved)

    async def test_registry_save_failure_restores_exact_revision_two(self) -> None:
        original = registry()
        store = Store(original, fail=True)
        service = self.service(store)
        await service.async_load()

        with self.assertRaisesRegex(Exception, "write failed"):
            await service.async_apply_managed_switch_binding_migration(
                BINDING_MIGRATION_MANIFEST
            )

        self.assertEqual(original, store.registry)
        self.assertIsNone(service._managed_switch_binding_migration_transaction)

    async def test_successful_rollback_restores_exact_revision_two_snapshot(self) -> None:
        original = registry()
        store = Store(original)
        service = self.service(store)
        await service.async_load()
        await service.async_apply_managed_switch_binding_migration(
            BINDING_MIGRATION_MANIFEST
        )

        self.assertTrue(
            await service.async_rollback_managed_switch_binding_migration(
                BINDING_MIGRATION_MANIFEST
            )
        )
        self.assertEqual(original, store.registry)
        self.assertEqual(original, service._registry)
        self.assertIsNone(service._managed_switch_binding_migration_transaction)

    async def test_rollback_refuses_registry_or_node_red_drift(self) -> None:
        store = Store(registry())
        backend = Backend()
        service = self.service(store, backend)
        await service.async_load()
        await service.async_apply_managed_switch_binding_migration(
            BINDING_MIGRATION_MANIFEST
        )
        migrated = store.registry.scenario(SCENARIO_ID)
        service._registry = ScenarioRegistry(
            scenarios=(replace(migrated, title="manual edit"),)
        )
        self.assertFalse(
            await service.async_rollback_managed_switch_binding_migration(
                BINDING_MIGRATION_MANIFEST
            )
        )

        store = Store(registry())

        class DriftedBackend(Backend):
            async def async_verify_managed_topology(self, scenario_id, flow_id):
                result = await super().async_verify_managed_topology(
                    scenario_id, flow_id
                )
                result["source_hash"] = "f" * 64
                return result

        service = self.service(store, DriftedBackend())
        await service.async_load()
        with self.assertRaises(Exception):
            await service.async_apply_managed_switch_binding_migration(
                BINDING_MIGRATION_MANIFEST
            )

    async def test_phase_b_clears_missing_device_health_and_listener_uses_new_target(self) -> None:
        state_property = ScenarioDeviceProperty(
            property_id="state",
            label="Состояние",
            value_type="enum",
            comparisons=("equals", "not_equals", "changed"),
            options=(
                ScenarioPropertyOption("on", "Включено"),
                ScenarioPropertyOption("off", "Выключено"),
            ),
        )
        catalog = ScenarioCatalog(
            devices={
                NEW_TARGET: ScenarioDeviceEntry(
                    NEW_TARGET,
                    "Выключатель малого коридора",
                    "switch.small_corridor_power",
                    (),
                    properties=(state_property,),
                ),
            },
            scenarios={},
        )
        store = Store(registry())
        service = ScenarioService(
            None,
            store,
            catalog,
            node_red_backend=Backend(),
        )
        await service.async_load()

        before = await service.async_scenario_health()
        self.assertEqual("degraded", before["status"])
        self.assertEqual(
            {"missing_device"},
            {item["code"] for item in before["violations"]},
        )

        await service.async_apply_managed_switch_binding_migration(
            BINDING_MIGRATION_MANIFEST
        )

        after = await service.async_scenario_health()
        self.assertEqual("healthy", after["status"])
        listeners = service.state_trigger_items()
        self.assertEqual(1, len(listeners))
        self.assertEqual("switch.small_corridor_power", listeners[0][2])
        self.assertEqual(NEW_TARGET, listeners[0][3])
        self.assertNotIn(OLD_TARGET, str(listeners))
