"""Restart-safe ownership keeps automatic and manual light separate."""

from __future__ import annotations

import asyncio
import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from custom_components.hausman_hub.application.scenario_light_priority import (
    LightAutomationPriority,
)
from custom_components.hausman_hub.application.scenarios import (
    ScenarioCatalog,
    ScenarioDeviceAction,
    ScenarioDeviceEntry,
)
from custom_components.hausman_hub.domain.scenarios import (
    ScenarioAction,
    ScenarioActionType,
)


class MemoryStore:
    def __init__(self, *, recovered_previous: bool = False) -> None:
        self.payload: object | None = None
        self.recovered_previous = recovered_previous

    async def async_load(self) -> object | None:
        return self.payload

    async def async_save(self, payload: dict[str, object]) -> None:
        self.payload = json.loads(json.dumps(payload))


def test_previous_generation_never_restores_automatic_off_authority() -> None:
    store = MemoryStore(recovered_previous=True)
    now = datetime.now(timezone.utc)
    store.payload = {
        "version": 1,
        "records": [
            {
                "entityId": "light.hall",
                "targetId": "light_1",
                "ownership": "automation",
                "evidenceRevision": now.isoformat(),
                "confirmedAt": int(now.timestamp() * 1000),
                "expiresAt": int(now.timestamp() * 1000) + 60_000,
            }
        ],
    }
    priority = LightAutomationPriority(store)
    hass = SimpleNamespace(
        states=SimpleNamespace(
            get=lambda _: SimpleNamespace(
                state="on", attributes={}, last_updated=now
            )
        )
    )

    asyncio.run(priority.async_load())

    assert not priority.is_owned("light.hall", hass)
    assert store.payload == {"version": 1, "records": []}


def test_stale_reassert_budget_survives_restart_and_resets_on_new_evidence() -> None:
    action_spec = ScenarioDeviceAction(
        action_id="turn_on",
        title="Включить",
        domain="light",
        service="turn_on",
        allowed_fields=frozenset(),
    )
    catalog = ScenarioCatalog(
        devices={
            "light_1": ScenarioDeviceEntry(
                target_id="light_1",
                name="Люстра",
                entity_id="light.hall",
                actions=(action_spec,),
            )
        },
        scenarios={},
    )
    first_revision = datetime.now(timezone.utc) - timedelta(hours=2)
    state = SimpleNamespace(
        state="on", attributes={}, last_updated=first_revision
    )
    hass = SimpleNamespace(states=SimpleNamespace(get=lambda _: state))
    store = MemoryStore()

    async def exercise() -> None:
        first = LightAutomationPriority(store)
        assert await first.async_claim_stale_reassert(
            "light_1", catalog, hass
        )
        restarted = LightAutomationPriority(store)
        await restarted.async_load()
        assert not await restarted.async_claim_stale_reassert(
            "light_1", catalog, hass
        )
        state.last_updated = first_revision + timedelta(seconds=1)
        assert await restarted.async_claim_stale_reassert(
            "light_1", catalog, hass
        )

    asyncio.run(exercise())


def test_confirmed_ownership_survives_restart_but_new_manual_event_invalidates_it() -> None:
    light_action = ScenarioDeviceAction(
        action_id="turn_on",
        title="Включить",
        domain="light",
        service="turn_on",
        allowed_fields=frozenset(),
    )
    catalog = ScenarioCatalog(
        devices={
            "light_1": ScenarioDeviceEntry(
                target_id="light_1",
                name="Люстра",
                entity_id="light.hall",
                actions=(light_action,),
            ),
            "presence_1": ScenarioDeviceEntry(
                target_id="presence_1",
                name="Присутствие",
                entity_id="binary_sensor.hall_presence",
                actions=(),
            ),
        },
        scenarios={},
    )
    action = ScenarioAction(
        id="light_on",
        type=ScenarioActionType.DEVICE_ACTION,
        target_id="light_1",
        action_id="turn_on",
    )
    revision = datetime.now(timezone.utc)
    values = {
        "light.hall": SimpleNamespace(
            state="off", attributes={}, last_updated=revision
        )
    }
    hass = SimpleNamespace(states=SimpleNamespace(get=values.get))
    trigger = {
        "source": "device_state",
        "target_id": "presence_1",
    }
    store = MemoryStore()
    first = LightAutomationPriority(store)
    plan = first.plan(
        (action,),
        catalog,
        hass,
        scenario_text="Свет в прихожей",
        trigger_context=trigger,
        power_dependencies={},
    )
    values["light.hall"] = SimpleNamespace(
        state="on", attributes={}, last_updated=revision + timedelta(seconds=1)
    )
    asyncio.run(
        first.note_results(
            (action,),
            (
                {
                    "action_id": "light_on",
                    "status": "completed",
                    "confirmed": True,
                },
            ),
            plan,
            catalog,
            hass,
            automatic=True,
            dry_run=False,
            scenario_id="hall_presence",
            run_id="run.1",
        )
    )

    first._owned_records["light.hall"]["expiresAt"] = 0
    expired = first.plan(
        (action,),
        catalog,
        hass,
        scenario_text="Свет в прихожей",
        trigger_context=trigger,
        power_dependencies={},
    )
    assert expired.applied
    assert expired.manual_target_ids == frozenset({"light_1"})

    # Recreate current ownership to verify the durable restart path separately.
    values["light.hall"] = SimpleNamespace(
        state="off", attributes={}, last_updated=revision + timedelta(seconds=2)
    )
    plan = first.plan(
        (action,),
        catalog,
        hass,
        scenario_text="Свет в прихожей",
        trigger_context=trigger,
        power_dependencies={},
    )
    values["light.hall"] = SimpleNamespace(
        state="on", attributes={}, last_updated=revision + timedelta(seconds=3)
    )
    asyncio.run(
        first.note_results(
            (action,),
            (
                {
                    "action_id": "light_on",
                    "status": "completed",
                    "confirmed": True,
                },
            ),
            plan,
            catalog,
            hass,
            automatic=True,
            dry_run=False,
            scenario_id="hall_presence",
            run_id="run.2",
        )
    )

    restarted = LightAutomationPriority(store)
    asyncio.run(restarted.async_load())
    still_owned = restarted.plan(
        (action,),
        catalog,
        hass,
        scenario_text="Свет в прихожей",
        trigger_context=trigger,
        power_dependencies={},
    )
    assert not still_owned.applied

    values["light.hall"] = SimpleNamespace(
        state="on", attributes={}, last_updated=revision + timedelta(seconds=4)
    )
    manual = restarted.plan(
        (action,),
        catalog,
        hass,
        scenario_text="Свет в прихожей",
        trigger_context=trigger,
        power_dependencies={},
    )
    assert manual.applied
    assert manual.manual_target_ids == frozenset({"light_1"})


def test_manual_priority_survives_stale_state_and_planning_is_pure() -> None:
    light_action = ScenarioDeviceAction(
        action_id="turn_on",
        title="Включить",
        domain="light",
        service="turn_on",
        allowed_fields=frozenset(),
    )
    catalog = ScenarioCatalog(
        devices={
            "light_1": ScenarioDeviceEntry(
                target_id="light_1",
                name="Люстра",
                entity_id="light.hall",
                actions=(light_action,),
            ),
            "presence_1": ScenarioDeviceEntry(
                target_id="presence_1",
                name="Присутствие",
                entity_id="binary_sensor.hall_presence",
                actions=(),
            ),
        },
        scenarios={},
    )
    action = ScenarioAction(
        id="light_on",
        type=ScenarioActionType.DEVICE_ACTION,
        target_id="light_1",
        action_id="turn_on",
    )
    values = {
        "light.hall": SimpleNamespace(
            state="on",
            attributes={},
            last_updated=datetime.now(timezone.utc) - timedelta(hours=2),
        )
    }
    hass = SimpleNamespace(states=SimpleNamespace(get=values.get))
    store = MemoryStore()
    priority = LightAutomationPriority(store)
    asyncio.run(
        priority.note_direct_action(
            "light_1",
            "turn_on",
            {"status": "completed", "confirmed": True},
            catalog,
            hass,
        )
    )
    saved = json.loads(json.dumps(store.payload))

    restarted = LightAutomationPriority(store)
    asyncio.run(restarted.async_load())
    plan = restarted.plan(
        (action,),
        catalog,
        hass,
        scenario_text="Свет в прихожей",
        trigger_context={"source": "device_state", "target_id": "presence_1"},
        power_dependencies={},
    )

    assert plan.applied
    assert plan.manual_target_ids == frozenset({"light_1"})
    assert store.payload == saved


def test_manual_claim_is_cleared_by_real_off_before_first_presence_turn_on() -> None:
    action_spec = ScenarioDeviceAction(
        action_id="turn_on",
        title="Включить",
        domain="light",
        service="turn_on",
        allowed_fields=frozenset(),
    )
    catalog = ScenarioCatalog(
        devices={
            "light_1": ScenarioDeviceEntry(
                target_id="light_1",
                name="Люстра",
                entity_id="light.hall",
                actions=(action_spec,),
            ),
            "presence_1": ScenarioDeviceEntry(
                target_id="presence_1",
                name="Присутствие",
                entity_id="binary_sensor.hall_presence",
                actions=(),
            ),
        },
        scenarios={},
    )
    state = SimpleNamespace(
        state="on", attributes={}, last_updated=datetime.now(timezone.utc)
    )
    values = {"light.hall": state}
    hass = SimpleNamespace(states=SimpleNamespace(get=values.get))
    priority = LightAutomationPriority(MemoryStore())
    asyncio.run(
        priority.note_direct_action(
            "light_1",
            "turn_on",
            {"status": "completed", "confirmed": True},
            catalog,
            hass,
        )
    )

    state.state = "off"
    state.last_updated = datetime.now(timezone.utc)
    plan = priority.plan(
        (
            ScenarioAction(
                id="light_on",
                type=ScenarioActionType.DEVICE_ACTION,
                target_id="light_1",
                action_id="turn_on",
            ),
        ),
        catalog,
        hass,
        scenario_text="Свет по присутствию",
        trigger_context={
            "source": "device_state",
            "target_id": "presence_1",
            "new_value": "on",
        },
        power_dependencies={},
    )

    assert not plan.applied
    assert plan.manual_target_ids == frozenset()
    assert "light.hall" not in priority._manual_records  # noqa: SLF001


def test_manual_claim_survives_restored_and_stale_off_after_restart() -> None:
    action_spec = ScenarioDeviceAction(
        action_id="turn_on",
        title="Включить",
        domain="light",
        service="turn_on",
        allowed_fields=frozenset(),
    )
    catalog = ScenarioCatalog(
        devices={
            "light_1": ScenarioDeviceEntry(
                target_id="light_1",
                name="Люстра",
                entity_id="light.hall",
                actions=(action_spec,),
            ),
            "presence_1": ScenarioDeviceEntry(
                target_id="presence_1",
                name="Присутствие",
                entity_id="binary_sensor.hall_presence",
                actions=(),
            ),
        },
        scenarios={},
    )
    action = ScenarioAction(
        id="light_on",
        type=ScenarioActionType.DEVICE_ACTION,
        target_id="light_1",
        action_id="turn_on",
    )
    trigger = {"source": "device_state", "target_id": "presence_1"}
    now = datetime.now(timezone.utc)
    state = SimpleNamespace(
        state="on", attributes={}, last_changed=now, last_updated=now
    )
    hass = SimpleNamespace(states=SimpleNamespace(get=lambda _: state))
    store = MemoryStore()
    priority = LightAutomationPriority(store)
    asyncio.run(
        priority.note_direct_action(
            "light_1",
            "turn_on",
            {"status": "completed", "confirmed": True},
            catalog,
            hass,
        )
    )
    saved = json.loads(json.dumps(store.payload))

    untrusted_off_states = (
        SimpleNamespace(
            state="off",
            attributes={"restored": True},
            last_changed=now,
            last_updated=now,
        ),
        SimpleNamespace(
            state="off",
            attributes={},
            last_changed=now - timedelta(hours=2),
            last_updated=now - timedelta(hours=2),
        ),
    )
    for untrusted_off in untrusted_off_states:
        store.payload = json.loads(json.dumps(saved))
        restarted = LightAutomationPriority(store)
        asyncio.run(restarted.async_load())
        state = untrusted_off
        plan = restarted.plan(
            (action,),
            catalog,
            hass,
            scenario_text="Свет по присутствию",
            trigger_context=trigger,
            power_dependencies={},
        )

        assert plan.clear_entity_ids == frozenset()
        assert "light.hall" in restarted._manual_records  # noqa: SLF001
        assert asyncio.run(restarted.async_has_manual_claim(plan, catalog, hass))
        assert "light.hall" in restarted._manual_records  # noqa: SLF001


def test_racing_manual_claim_is_rechecked_against_off_state_before_dispatch() -> None:
    action_spec = ScenarioDeviceAction(
        action_id="turn_on",
        title="Включить",
        domain="light",
        service="turn_on",
        allowed_fields=frozenset(),
    )
    catalog = ScenarioCatalog(
        devices={
            "light_1": ScenarioDeviceEntry(
                target_id="light_1",
                name="Люстра",
                entity_id="light.hall",
                actions=(action_spec,),
            ),
            "presence_1": ScenarioDeviceEntry(
                target_id="presence_1",
                name="Присутствие",
                entity_id="binary_sensor.hall_presence",
                actions=(),
            ),
        },
        scenarios={},
    )
    state = SimpleNamespace(
        state="off", attributes={}, last_updated=datetime.now(timezone.utc)
    )
    hass = SimpleNamespace(states=SimpleNamespace(get=lambda _entity_id: state))
    priority = LightAutomationPriority(MemoryStore())
    action = ScenarioAction(
        id="light_on",
        type=ScenarioActionType.DEVICE_ACTION,
        target_id="light_1",
        action_id="turn_on",
    )
    plan = priority.plan(
        (action,),
        catalog,
        hass,
        scenario_text="Свет по присутствию",
        trigger_context={
            "source": "device_state",
            "target_id": "presence_1",
            "new_value": "on",
        },
        power_dependencies={},
    )

    state.state = "on"
    asyncio.run(
        priority.note_direct_action(
            "light_1",
            "turn_on",
            {"status": "completed", "confirmed": True},
            catalog,
            hass,
        )
    )
    state.state = "off"

    assert not asyncio.run(priority.async_has_manual_claim(plan, catalog, hass))
    assert "light.hall" not in priority._manual_records  # noqa: SLF001


def test_stale_on_without_automation_ownership_allows_recovery_profile() -> None:
    actions = (
        ScenarioDeviceAction(
            action_id="turn_on",
            title="Включить",
            domain="light",
            service="turn_on",
            allowed_fields=frozenset(),
        ),
        ScenarioDeviceAction(
            action_id="set_brightness_percent",
            title="Яркость",
            domain="light",
            service="turn_on",
            allowed_fields=frozenset({"value"}),
        ),
    )
    catalog = ScenarioCatalog(
        devices={
            "light_1": ScenarioDeviceEntry(
                target_id="light_1",
                name="Люстра",
                entity_id="light.hall",
                actions=actions,
            ),
            "presence_1": ScenarioDeviceEntry(
                target_id="presence_1",
                name="Присутствие",
                entity_id="binary_sensor.hall_presence",
                actions=(),
            ),
        },
        scenarios={},
    )
    stale = datetime.now(timezone.utc) - timedelta(hours=2)
    hass = SimpleNamespace(
        states=SimpleNamespace(
            get=lambda entity_id: SimpleNamespace(
                state="on", attributes={}, last_updated=stale
            )
            if entity_id == "light.hall"
            else None
        )
    )
    priority = LightAutomationPriority(MemoryStore())
    plan = priority.plan(
        (
            ScenarioAction(
                id="on",
                type=ScenarioActionType.DEVICE_ACTION,
                target_id="light_1",
                action_id="turn_on",
            ),
            ScenarioAction(
                id="brightness",
                type=ScenarioActionType.DEVICE_ACTION,
                target_id="light_1",
                action_id="set_brightness_percent",
                value=50,
            ),
        ),
        catalog,
        hass,
        scenario_text="Свет по присутствию",
        trigger_context={"source": "device_state", "target_id": "presence_1"},
        power_dependencies={},
    )

    assert not plan.applied
    assert plan.manual_target_ids == frozenset()


def test_attribute_only_update_preserves_automatic_ownership() -> None:
    action_spec = ScenarioDeviceAction(
        action_id="turn_on",
        title="Включить",
        domain="light",
        service="turn_on",
        allowed_fields=frozenset(),
    )
    catalog = ScenarioCatalog(
        devices={
            "light_1": ScenarioDeviceEntry(
                target_id="light_1",
                name="Люстра",
                entity_id="light.hall",
                actions=(action_spec,),
            ),
            "presence_1": ScenarioDeviceEntry(
                target_id="presence_1",
                name="Присутствие",
                entity_id="binary_sensor.hall_presence",
                actions=(),
            ),
        },
        scenarios={},
    )
    action = ScenarioAction(
        id="on",
        type=ScenarioActionType.DEVICE_ACTION,
        target_id="light_1",
        action_id="turn_on",
    )
    changed = datetime.now(timezone.utc)
    state = SimpleNamespace(
        state="off",
        attributes={},
        last_changed=changed,
        last_updated=changed,
    )
    hass = SimpleNamespace(states=SimpleNamespace(get=lambda _: state))
    priority = LightAutomationPriority(MemoryStore())
    trigger = {"source": "device_state", "target_id": "presence_1"}
    plan = priority.plan(
        (action,),
        catalog,
        hass,
        scenario_text="Свет по присутствию",
        trigger_context=trigger,
        power_dependencies={},
    )
    state.state = "on"
    state.last_changed = changed + timedelta(seconds=1)
    state.last_updated = state.last_changed
    asyncio.run(
        priority.note_results(
            (action,),
            ({"action_id": "on", "status": "completed", "confirmed": True},),
            plan,
            catalog,
            hass,
            automatic=True,
            dry_run=False,
            scenario_id="hall_presence",
            run_id="run.attribute",
        )
    )

    state.attributes = {"brightness": 128}
    state.last_updated = changed + timedelta(seconds=2)

    assert priority.is_owned("light.hall", hass)
    later = priority.plan(
        (action,),
        catalog,
        hass,
        scenario_text="Свет по присутствию",
        trigger_context=trigger,
        power_dependencies={},
    )
    assert not later.applied


def test_concurrent_manual_claim_wins_over_late_automatic_ownership() -> None:
    action_spec = ScenarioDeviceAction(
        action_id="turn_on",
        title="Включить",
        domain="light",
        service="turn_on",
        allowed_fields=frozenset(),
    )
    catalog = ScenarioCatalog(
        devices={
            "light_1": ScenarioDeviceEntry(
                target_id="light_1",
                name="Люстра",
                entity_id="light.hall",
                actions=(action_spec,),
            ),
            "presence_1": ScenarioDeviceEntry(
                target_id="presence_1",
                name="Присутствие",
                entity_id="binary_sensor.hall_presence",
                actions=(),
            ),
        },
        scenarios={},
    )
    action = ScenarioAction(
        id="on",
        type=ScenarioActionType.DEVICE_ACTION,
        target_id="light_1",
        action_id="turn_on",
    )
    state = SimpleNamespace(
        state="off", attributes={}, last_updated=datetime.now(timezone.utc)
    )
    hass = SimpleNamespace(states=SimpleNamespace(get=lambda _: state))
    priority = LightAutomationPriority(MemoryStore())
    plan = priority.plan(
        (action,),
        catalog,
        hass,
        scenario_text="Свет по присутствию",
        trigger_context={"source": "device_state", "target_id": "presence_1"},
        power_dependencies={},
    )

    async def exercise() -> None:
        await priority.async_prepare_direct_action(
            "light_1", "turn_on", catalog, hass
        )
        state.state = "on"
        state.last_updated = datetime.now(timezone.utc) + timedelta(seconds=1)
        await priority.note_results(
            (action,),
            ({"action_id": "on", "status": "completed", "confirmed": True},),
            plan,
            catalog,
            hass,
            automatic=True,
            dry_run=False,
            scenario_id="presence",
            run_id="run.race",
        )

    asyncio.run(exercise())

    assert "light.hall" in priority._manual_records
    assert "light.hall" not in priority._owned_records


def load_tests(
    loader: unittest.TestLoader,
    tests: unittest.TestSuite,
    pattern: str | None,
) -> unittest.TestSuite:
    """Expose function-style durability cases to the release runner."""

    del loader, tests, pattern
    suite = unittest.TestSuite()
    for name, case in sorted(globals().items()):
        if name.startswith("test_") and callable(case):
            suite.addTest(unittest.FunctionTestCase(case))
    return suite
