from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from custom_components.hausman_hub.application.native_automation_migration import (
    EXPECTED_NATIVE_AUTOMATIONS,
    HomeAssistantNativeAutomationAdapter,
    NATIVE_AUTOMATION_ENTITY_IDS,
    NATIVE_AUTOMATION_PRESERVE_ENTITY_IDS,
    NativeAutomationMigration,
    NativeAutomationMigrationConflict,
    valid_native_automation_migration_payload,
)


class Store:
    def __init__(self) -> None:
        self.value = None
        self.saved = []

    async def async_load(self):
        return self.value

    async def async_save(self, value):
        self.value = dict(value)
        self.saved.append(dict(value))


class Adapter:
    def __init__(self, *, fail_at: int | None = None) -> None:
        self.states = {
            entity_id: {
                **expected,
                "contextId": f"initial-{index}",
                "lastUpdated": f"2026-09-07T06:{index:02d}:00+00:00",
            }
            for index, (entity_id, expected) in enumerate(
                EXPECTED_NATIVE_AUTOMATIONS.items()
            )
        }
        self.fail_at = fail_at
        self.calls = []
        self.restores = []
        self.operation_ids = 0

    def new_operation_id(self):
        self.operation_ids += 1
        return f"operation-{self.operation_ids}"

    async def async_snapshot(self, entity_ids):
        return {entity_id: dict(self.states[entity_id]) for entity_id in entity_ids}

    async def async_disable(self, entity_id, expected, *, operation_id):
        assert self.states[entity_id] == expected
        self.calls.append(entity_id)
        if self.fail_at == len(self.calls):
            raise OSError("automation service failed")
        self.states[entity_id] = {
            **expected,
            "state": "off",
            "contextId": operation_id,
            "lastUpdated": f"disabled-{operation_id}",
        }
        return dict(self.states[entity_id])

    async def async_restore(
        self, entity_id, expected_current, desired_state, *, operation_id
    ):
        assert self.states[entity_id] == expected_current
        self.restores.append(entity_id)
        self.states[entity_id] = {
            **expected_current,
            "state": desired_state,
            "contextId": operation_id,
            "lastUpdated": f"restored-{operation_id}",
        }
        return dict(self.states[entity_id])


def test_native_handover_disables_exactly_five_and_is_idempotent() -> None:
    async def exercise() -> None:
        adapter = Adapter()
        store = Store()
        migration = NativeAutomationMigration(adapter, store)

        await migration.async_apply()
        await migration.async_apply()

        assert adapter.calls == list(NATIVE_AUTOMATION_ENTITY_IDS.values())
        assert {
            adapter.states[entity]["state"]
            for entity in NATIVE_AUTOMATION_ENTITY_IDS.values()
        } == {"off"}
        assert {
            adapter.states[entity]["state"]
            for entity in NATIVE_AUTOMATION_PRESERVE_ENTITY_IDS.values()
        } == {
            EXPECTED_NATIVE_AUTOMATIONS[entity]["state"]
            for entity in NATIVE_AUTOMATION_PRESERVE_ENTITY_IDS.values()
        }
        assert store.saved[-1]["state"] == "completed"
        assert set(store.saved[-1]["before"]) == set(adapter.states)
        assert {
            store.saved[-1]["after"][entity]["state"]
            for entity in NATIVE_AUTOMATION_ENTITY_IDS.values()
        } == {"off"}

    asyncio.run(exercise())


def test_native_handover_restores_before_evidence_after_failure() -> None:
    async def exercise() -> None:
        adapter = Adapter(fail_at=3)
        store = Store()

        with pytest.raises(OSError, match="service failed"):
            await NativeAutomationMigration(adapter, store).async_apply()

        assert {
            entity_id: evidence["state"]
            for entity_id, evidence in adapter.states.items()
        } == {
            entity_id: expected["state"]
            for entity_id, expected in EXPECTED_NATIVE_AUTOMATIONS.items()
        }
        assert store.value["state"] == "prepared"
        assert store.value["after"] is None

    asyncio.run(exercise())


def test_native_receipt_rejects_state_only_and_incomplete_completed_images() -> None:
    entities = {
        **NATIVE_AUTOMATION_ENTITY_IDS,
        **NATIVE_AUTOMATION_PRESERVE_ENTITY_IDS,
    }
    state_only = {
        "state": "prepared",
        "before": {entity_id: "on" for entity_id in entities.values()},
        "after": None,
    }
    assert not valid_native_automation_migration_payload(state_only)


def _native_fixture_by_entity() -> dict[str, dict[str, object]]:
    payload = json.loads(
        (
            Path(__file__).parents[1]
            / "fixtures"
            / "hausmanhub_scenario_consolidation_v1"
            / "native18.json"
        ).read_text()
    )
    return {item["entity_id"]: item for item in payload["automations"]}


class _StrictServices:
    def __init__(self, states: dict[str, object]) -> None:
        self.states = states
        self.calls: list[tuple[str, str, dict[str, object], str]] = []

    async def async_call(
        self, domain, service, data, *, blocking, context
    ) -> None:
        assert domain == "automation"
        assert blocking is True
        assert set(data) == ({"entity_id", "stop_actions"} if service == "turn_off" else {"entity_id"})
        entity_id = data["entity_id"]
        self.calls.append((domain, service, dict(data), context.id))
        state = self.states[entity_id]
        state.state = "off" if service == "turn_off" else "on"
        state.context = SimpleNamespace(id=context.id, parent_id=None, user_id=None)
        state.last_updated = datetime.now(UTC)


class _AutomationComponent:
    def __init__(self, configs: dict[str, dict[str, object]]) -> None:
        self.configs = configs

    def get_entity(self, entity_id: str) -> object:
        return SimpleNamespace(raw_config=self.configs[entity_id])


def _fake_hass_from_native_fixture(
    *,
    state_overrides: dict[str, str] | None = None,
    context_prefix: str = "initial",
    updated_hour: int = 6,
) -> tuple[object, _StrictServices]:
    fixture = _native_fixture_by_entity()
    states = {
        entity_id: SimpleNamespace(
            state=(
                item["state"]
                if state_overrides is None
                else state_overrides[entity_id]
            ),
            attributes={"id": item["definition"]["id"]},
            context=SimpleNamespace(
                id=f"{context_prefix}-{index}", parent_id=None, user_id=None
            ),
            last_updated=datetime(2026, 9, 7, updated_hour, index, tzinfo=UTC),
        )
        for index, (entity_id, item) in enumerate(fixture.items())
    }
    services = _StrictServices(states)
    data = {"automation": _AutomationComponent({key: item["definition"] for key, item in fixture.items()})}
    hass = SimpleNamespace(
        states=SimpleNamespace(get=states.get), services=services, data=data
    )
    return hass, services


def test_completed_handover_accepts_fresh_ha_state_objects_after_restart() -> None:
    async def exercise() -> None:
        hass, services = _fake_hass_from_native_fixture()
        store = Store()
        migration = NativeAutomationMigration(
            HomeAssistantNativeAutomationAdapter(hass), store
        )
        await migration.async_apply()
        stable_states = {
            entity_id: state.state
            for entity_id, state in services.states.items()
        }
        saved_before_restart = len(store.saved)
        receipt_before_restart = json.loads(json.dumps(store.value))

        restarted_hass, restarted_services = _fake_hass_from_native_fixture(
            state_overrides=stable_states,
            context_prefix="restart",
            updated_hour=7,
        )
        assert all(
            restarted_services.states[entity_id]
            is not services.states[entity_id]
            for entity_id in stable_states
        )
        restarted = NativeAutomationMigration(
            HomeAssistantNativeAutomationAdapter(restarted_hass), store
        )

        assert await restarted.async_verify_completed() is True
        await restarted.async_apply()

        assert len(services.calls) == len(NATIVE_AUTOMATION_ENTITY_IDS)
        assert restarted_services.calls == []
        assert len(store.saved) == saved_before_restart
        assert store.value == receipt_before_restart

    asyncio.run(exercise())


def test_ha_adapter_uses_exact_definition_identity_and_service_schemas() -> None:
    async def exercise() -> None:
        hass, services = _fake_hass_from_native_fixture()
        adapter = HomeAssistantNativeAutomationAdapter(hass)
        entities = tuple(
            (*NATIVE_AUTOMATION_ENTITY_IDS.values(), *NATIVE_AUTOMATION_PRESERVE_ENTITY_IDS.values())
        )

        before = await adapter.async_snapshot(entities)
        first = next(iter(NATIVE_AUTOMATION_ENTITY_IDS.values()))
        after = await adapter.async_disable(
            first, before[first], operation_id="01JTESTDISABLE0000000000000"
        )
        restored = await adapter.async_restore(
            first,
            after,
            before[first]["state"],
            operation_id="01JTESTRESTORE0000000000000",
        )

        assert after["automationId"] == before[first]["automationId"]
        assert after["definitionHash"] == before[first]["definitionHash"]
        assert after["contextId"] == "01JTESTDISABLE0000000000000"
        assert restored["state"] == before[first]["state"]
        assert services.calls[0][2] == {
            "entity_id": first,
            "stop_actions": True,
        }
        assert services.calls[1][2] == {"entity_id": first}

    asyncio.run(exercise())


def test_ha_adapter_rejects_definition_drift_before_any_service_call() -> None:
    async def exercise() -> None:
        hass, services = _fake_hass_from_native_fixture()
        first = next(iter(NATIVE_AUTOMATION_ENTITY_IDS.values()))
        hass.data["automation"].configs[first]["alias"] = "Чужая автоматизация"
        adapter = HomeAssistantNativeAutomationAdapter(hass)

        with pytest.raises(NativeAutomationMigrationConflict, match="definition"):
            await adapter.async_snapshot(tuple(
                (*NATIVE_AUTOMATION_ENTITY_IDS.values(), *NATIVE_AUTOMATION_PRESERVE_ENTITY_IDS.values())
            ))

        assert services.calls == []

    asyncio.run(exercise())


def test_completed_handover_allows_preserve_trigger_context_but_not_enabled_drift() -> None:
    async def exercise() -> None:
        adapter = Adapter()
        store = Store()
        migration = NativeAutomationMigration(adapter, store)
        await migration.async_apply()
        preserve = next(iter(NATIVE_AUTOMATION_PRESERVE_ENTITY_IDS.values()))
        adapter.states[preserve]["contextId"] = "legitimate-trigger"
        adapter.states[preserve]["lastUpdated"] = "triggered-later"

        await migration.async_apply()
        assert len(adapter.calls) == 5

        adapter.states[preserve]["state"] = (
            "off" if adapter.states[preserve]["state"] == "on" else "on"
        )
        with pytest.raises(NativeAutomationMigrationConflict, match="completion drifted"):
            await migration.async_apply()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "drift",
    (
        "disabled_state",
        "preserved_state",
        "automation_id",
        "definition_hash",
        "missing",
        "unknown",
        "unavailable",
    ),
)
def test_completed_handover_rejects_restart_semantic_drift(drift: str) -> None:
    async def exercise() -> None:
        hass, services = _fake_hass_from_native_fixture()
        store = Store()
        await NativeAutomationMigration(
            HomeAssistantNativeAutomationAdapter(hass), store
        ).async_apply()
        calls_before_drift = len(services.calls)
        saves_before_drift = len(store.saved)
        disabled = next(iter(NATIVE_AUTOMATION_ENTITY_IDS.values()))
        preserved = next(iter(NATIVE_AUTOMATION_PRESERVE_ENTITY_IDS.values()))
        target = preserved if drift == "preserved_state" else disabled

        if drift in {"disabled_state", "preserved_state"}:
            state = services.states[target]
            state.state = "off" if state.state == "on" else "on"
            assert not await NativeAutomationMigration(
                HomeAssistantNativeAutomationAdapter(hass), store
            ).async_verify_completed()
        else:
            if drift == "automation_id":
                services.states[target].attributes["id"] = "foreign-automation"
            elif drift == "definition_hash":
                hass.data["automation"].configs[target]["alias"] = (
                    "Чужая автоматизация"
                )
            elif drift == "missing":
                del services.states[target]
            elif drift in {"unknown", "unavailable"}:
                services.states[target].state = drift
            else:  # pragma: no cover - the parameter list is closed above.
                raise AssertionError(drift)
            with pytest.raises(NativeAutomationMigrationConflict):
                await NativeAutomationMigration(
                    HomeAssistantNativeAutomationAdapter(hass), store
                ).async_verify_completed()

        with pytest.raises(NativeAutomationMigrationConflict):
            await NativeAutomationMigration(
                HomeAssistantNativeAutomationAdapter(hass), store
            ).async_apply()
        assert len(services.calls) == calls_before_drift
        assert len(store.saved) == saves_before_drift

    asyncio.run(exercise())


def test_prepared_handover_rejects_disabled_context_drift_before_writes() -> None:
    async def exercise() -> None:
        adapter = Adapter()
        before = await adapter.async_snapshot(tuple(EXPECTED_NATIVE_AUTOMATIONS))
        store = Store()
        store.value = {
            "version": 1,
            "state": "prepared",
            "mode": "apply",
            "before": before,
            "baseline": {key: dict(value) for key, value in before.items()},
            "operations": {},
            "after": None,
        }
        target = next(iter(NATIVE_AUTOMATION_ENTITY_IDS.values()))
        adapter.states[target]["contextId"] = "foreign-context"
        adapter.states[target]["lastUpdated"] = "foreign-update"

        with pytest.raises(
            NativeAutomationMigrationConflict, match="baseline drifted"
        ):
            await NativeAutomationMigration(adapter, store).async_apply()

        assert adapter.calls == []
        assert adapter.restores == []
        assert store.saved == []

    asyncio.run(exercise())


def test_native_rollback_refuses_manual_off_after_handover_without_any_restore() -> None:
    async def exercise() -> None:
        adapter = Adapter()
        store = Store()
        migration = NativeAutomationMigration(adapter, store)
        await migration.async_apply()
        first = next(iter(NATIVE_AUTOMATION_ENTITY_IDS.values()))
        adapter.states[first]["contextId"] = "manual-off-after-migration"
        adapter.states[first]["lastUpdated"] = "manual-off-after-migration"

        assert not await migration.async_rollback()
        assert adapter.restores == []
        assert adapter.states[first]["state"] == "off"

    asyncio.run(exercise())


def test_prepared_restart_adopts_only_own_disable_and_preserves_original_before() -> None:
    async def exercise() -> None:
        adapter = Adapter()
        before = await adapter.async_snapshot(tuple(EXPECTED_NATIVE_AUTOMATIONS))
        first = next(iter(NATIVE_AUTOMATION_ENTITY_IDS.values()))
        operation_id = "lost-response-operation"
        adapter.states[first] = {
            **before[first],
            "state": "off",
            "contextId": operation_id,
            "lastUpdated": "lost-response-write",
        }
        prepared = {
            "version": 1,
            "state": "prepared",
            "mode": "apply",
            "before": before,
            "baseline": {key: dict(value) for key, value in before.items()},
            "operations": {
                first: {
                    "phase": "intent",
                    "operationId": operation_id,
                    "expected": dict(before[first]),
                    "after": None,
                    "restoreId": None,
                    "restored": None,
                }
            },
            "after": None,
        }
        store = Store()
        store.value = prepared

        await NativeAutomationMigration(adapter, store).async_apply()

        assert store.value["state"] == "completed"
        assert store.value["before"] == before
        assert first not in adapter.calls
        assert len(adapter.calls) == 4

    asyncio.run(exercise())


def test_cancellation_after_native_disable_restores_exactly_and_can_retry() -> None:
    async def exercise() -> None:
        class CancelAfterFirstDisable(Adapter):
            def __init__(self) -> None:
                super().__init__()
                self.cancelled = False

            async def async_disable(self, entity_id, expected, *, operation_id):
                disabled = await super().async_disable(
                    entity_id,
                    expected,
                    operation_id=operation_id,
                )
                if not self.cancelled:
                    self.cancelled = True
                    raise asyncio.CancelledError
                return disabled

        adapter = CancelAfterFirstDisable()
        store = Store()
        migration = NativeAutomationMigration(adapter, store)

        with pytest.raises(asyncio.CancelledError):
            await migration.async_apply()

        assert {
            entity_id: evidence["state"]
            for entity_id, evidence in adapter.states.items()
        } == {
            entity_id: expected["state"]
            for entity_id, expected in EXPECTED_NATIVE_AUTOMATIONS.items()
        }
        assert store.value["state"] == "prepared"
        assert store.value["mode"] == "apply"
        assert store.value["operations"] == {}
        await migration.async_apply()
        assert store.value["state"] == "completed"

    asyncio.run(exercise())


def test_lost_restore_response_is_reconciled_from_own_restore_context() -> None:
    async def exercise() -> None:
        class LoseFirstRestoreResponse(Adapter):
            def __init__(self) -> None:
                super().__init__(fail_at=2)
                self.lost = False

            async def async_restore(
                self, entity_id, expected_current, desired_state, *, operation_id
            ):
                restored = await super().async_restore(
                    entity_id,
                    expected_current,
                    desired_state,
                    operation_id=operation_id,
                )
                if not self.lost:
                    self.lost = True
                    raise asyncio.CancelledError
                return restored

        adapter = LoseFirstRestoreResponse()
        store = Store()
        with pytest.raises(
            NativeAutomationMigrationConflict, match="recovery is required"
        ):
            await NativeAutomationMigration(adapter, store).async_apply()

        assert store.value["mode"] == "rollback"
        adapter.fail_at = None
        await NativeAutomationMigration(adapter, store).async_apply()
        assert store.value["state"] == "completed"
        assert store.value["before"] == store.saved[0]["before"]

    asyncio.run(exercise())


def test_completed_native_receipt_rejects_null_and_wrong_full_images() -> None:
    async def exercise() -> None:
        adapter = Adapter()
        store = Store()
        await NativeAutomationMigration(adapter, store).async_apply()
        completed = json.loads(json.dumps(store.value))

        missing_after = json.loads(json.dumps(completed))
        missing_after["after"] = None
        assert not valid_native_automation_migration_payload(missing_after)

        wrong_after = json.loads(json.dumps(completed))
        preserved = next(iter(NATIVE_AUTOMATION_PRESERVE_ENTITY_IDS.values()))
        wrong_after["after"][preserved]["state"] = (
            "off" if wrong_after["after"][preserved]["state"] == "on" else "on"
        )
        assert not valid_native_automation_migration_payload(wrong_after)

    asyncio.run(exercise())
