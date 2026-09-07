"""End-to-end state-machine tests for the managed storage controller."""

from __future__ import annotations

from dataclasses import replace
import asyncio
import sys
from types import ModuleType
from types import SimpleNamespace

import pytest

from custom_components.hausman_hub.application.scenario_control_coordinator import (
    STORAGE_LIGHT_TARGET_ID,
    ScenarioControlCoordinator,
    valid_scenario_control_state_payload,
)
from custom_components.hausman_hub.application.scenario_control_policy import (
    ScenarioControlPolicyService,
)
from custom_components.hausman_hub.domain.scenario_controls import (
    ScenarioControlPolicy,
)


class MemoryStore:
    def __init__(self, payload: object | None = None) -> None:
        self.payload = payload
        self.saved: list[dict[str, object]] = []

    async def async_load(self) -> object | None:
        return self.payload

    async def async_save(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.saved.append(payload)


class States:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    def get(self, entity_id: str) -> object | None:
        value = self.values.get(entity_id)
        return None if value is None else SimpleNamespace(state=value, attributes={})


class LightPriority:
    def __init__(self, *, owned: bool = False) -> None:
        self.owned = owned

    def is_owned(self, entity_id: str, hass: object) -> bool:
        del entity_id, hass
        return self.owned

    def ownership_revision(self, entity_id: str, hass: object) -> str | None:
        del entity_id, hass
        return "owned-revision" if self.owned else None


class ScenarioService:
    def __init__(self, states: States, light_priority: LightPriority) -> None:
        self.calls: list[dict[str, object]] = []
        self.states = states
        self.light_priority = light_priority

    async def async_run_scenario(self, scenario_id: str, **kwargs: object) -> dict[str, object]:
        self.calls.append({"scenario_id": scenario_id, **kwargs})
        trigger = kwargs["trigger_context"]
        transition = trigger["trigger_id"]
        if transition == "storage_light_on":
            self.states.values["light.storage"] = "on"
            self.light_priority.owned = True
        elif transition == "storage_light_off_due":
            self.states.values["light.storage"] = "off"
            self.light_priority.owned = False
        return {"status": "completed", "confirmed": True, "receipts": []}


async def make_coordinator(
    *,
    clock: list[int],
    states: dict[str, str] | None = None,
    owned: bool = False,
    state_store: MemoryStore | None = None,
    policy_store: MemoryStore | None = None,
    motion_target_id: str | None = "motion-target",
    presence_target_id: str | None = None,
    exhaust_target_id: str | None = None,
) -> tuple[
    ScenarioControlCoordinator,
    ScenarioControlPolicyService,
    ScenarioService,
    States,
    LightPriority,
    MemoryStore,
]:
    state_values = States(
        states
        or {
            "binary_sensor.motion": "off",
            "light.storage": "on",
        }
    )
    light_priority = LightPriority(owned=owned)
    service = ScenarioService(state_values, light_priority)
    exhaust_device = SimpleNamespace(
        entity_id="fan.storage",
        name="Вытяжка кладовки",
        action=lambda action_id: SimpleNamespace(
            domain="fan", service=action_id
        ) if action_id in {"turn_on", "turn_off"} else None,
    )
    policy = ScenarioControlPolicyService(
        policy_store or MemoryStore(),
        capability_resolver=lambda target_id: (
            exhaust_device if target_id == exhaust_target_id else None
        ),
    )
    await policy.async_load()
    if exhaust_target_id is not None:
        await policy.async_replace(
            0,
            replace(
                ScenarioControlPolicy(),
                storage_exhaust_target_id=exhaust_target_id,
            ),
        )
    devices = {
        "motion-target": SimpleNamespace(entity_id="binary_sensor.motion"),
        "presence-target": SimpleNamespace(entity_id="binary_sensor.presence"),
        STORAGE_LIGHT_TARGET_ID: SimpleNamespace(entity_id="light.storage"),
    }
    store = state_store or MemoryStore()
    coordinator = ScenarioControlCoordinator(
        SimpleNamespace(states=state_values),
        service,
        policy,
        store,
        light_priority,
        catalog_resolver=devices.get,
        storage_motion_target_id=motion_target_id,
        storage_presence_target_id=presence_target_id,
        now_ms=lambda: clock[0],
        schedule_tasks=False,
    )
    await coordinator.async_load()
    return coordinator, policy, service, state_values, light_priority, store


@pytest.mark.asyncio
async def test_storage_absence_boundaries_are_common_10_and_120_seconds() -> None:
    clock = [0]
    coordinator, _policy, service, _states, _ownership, _store = await make_coordinator(
        clock=clock,
        owned=True,
    )

    await coordinator.async_handle_storage_change()
    assert coordinator.storage_state["absenceStartedAtMs"] == 0
    assert coordinator.storage_state["deadlineMs"] == 120_000

    clock[0] = 9_999
    assert (await coordinator.async_control_context(
        "system-storage-light-controller", "probe-9999", {}
    ))["state"]["absenceConfirmed"] is False
    await coordinator.async_reconcile_storage_due()
    assert service.calls == []

    clock[0] = 10_000
    assert (await coordinator.async_control_context(
        "system-storage-light-controller", "probe-10000", {}
    ))["state"]["absenceConfirmed"] is True
    await coordinator.async_reconcile_storage_due()
    assert service.calls == []

    clock[0] = 119_999
    await coordinator.async_reconcile_storage_due()
    assert service.calls == []

    clock[0] = 120_000
    await coordinator.async_reconcile_storage_due()
    assert [call["trigger_context"]["trigger_id"] for call in service.calls] == [
        "storage_light_off_due"
    ]
    assert coordinator.storage_state["transition"] == "idle"


@pytest.mark.asyncio
async def test_restart_at_60_seconds_keeps_original_deadline_and_rechecks_authority() -> None:
    clock = [0]
    first, _policy, _service, _states, _ownership, store = await make_coordinator(
        clock=clock,
        owned=True,
    )
    await first.async_handle_storage_change()

    clock[0] = 60_000
    restarted, _policy2, service2, _states2, _ownership2, _ = await make_coordinator(
        clock=clock,
        owned=True,
        state_store=store,
    )
    await restarted.async_reconcile_storage_startup()

    assert restarted.storage_state["deadlineMs"] == 120_000
    assert restarted.storage_remaining_seconds == 60
    assert service2.calls == []
    clock[0] = 120_000
    await restarted.async_reconcile_storage_due()
    assert len(service2.calls) == 1


@pytest.mark.asyncio
async def test_manual_on_and_unknown_evidence_never_create_off_authority() -> None:
    clock = [0]
    manual, _policy, manual_service, _states, _ownership, _store = await make_coordinator(
        clock=clock,
        owned=False,
    )
    await manual.async_handle_storage_change()
    assert manual.storage_state["transition"] == "manual_light_hold"
    assert manual.storage_state["deadlineMs"] is None
    clock[0] = 999_000
    await manual.async_reconcile_storage_due()
    assert manual_service.calls == []

    unknown, _policy2, unknown_service, _states2, _ownership2, _store2 = await make_coordinator(
        clock=[0],
        states={"binary_sensor.motion": "unavailable", "light.storage": "on"},
        owned=True,
    )
    await unknown.async_handle_storage_change()
    assert unknown.storage_state["transition"] == "occupancy_unknown"
    assert unknown.storage_state["absenceStartedAtMs"] is None
    assert unknown_service.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("motion", "presence"),
    (("on", "off"), ("off", "on")),
)
async def test_either_motion_or_presence_turns_storage_light_on_immediately(
    motion: str,
    presence: str,
) -> None:
    coordinator, _policy, service, _states, ownership, _store = await make_coordinator(
        clock=[0],
        states={
            "binary_sensor.motion": motion,
            "binary_sensor.presence": presence,
            "light.storage": "off",
        },
        presence_target_id="presence-target",
    )

    await coordinator.async_handle_storage_change()

    assert service.calls[0]["trigger_context"]["trigger_id"] == "storage_light_on"
    assert ownership.owned is True
    assert coordinator.storage_state["deadlineMs"] is None


@pytest.mark.asyncio
async def test_new_occupancy_and_policy_revision_cancel_old_generation() -> None:
    clock = [0]
    coordinator, policy, service, states, _ownership, _store = await make_coordinator(
        clock=clock,
        owned=True,
    )
    await coordinator.async_handle_storage_change()
    old_generation = coordinator.storage_state["generation"]

    states.values["binary_sensor.motion"] = "on"
    await coordinator.async_handle_storage_change()
    assert coordinator.storage_state["generation"] > old_generation
    assert coordinator.storage_state["deadlineMs"] is None
    clock[0] = 120_000
    await coordinator.async_reconcile_storage_due()
    assert service.calls == []

    states.values["binary_sensor.motion"] = "off"
    states.values["light.storage"] = "on"
    await coordinator.async_handle_storage_change()
    pending_generation = coordinator.storage_state["generation"]
    await policy.async_replace(
        0,
        replace(ScenarioControlPolicy(), storage_absence_seconds=180),
    )
    assert coordinator.storage_state["generation"] > pending_generation
    assert coordinator.storage_state["policyRevision"] == 1
    assert coordinator.storage_state["transition"] == "policy_changed"
    assert coordinator.storage_state["deadlineMs"] is None


@pytest.mark.asyncio
async def test_uncertain_storage_off_is_not_retried_without_new_occupancy() -> None:
    clock = [0]
    coordinator, _policy, service, _states, _ownership, _store = await make_coordinator(
        clock=clock,
        owned=True,
    )
    await coordinator.async_handle_storage_change()

    async def uncertain(scenario_id: str, **kwargs: object) -> dict[str, object]:
        service.calls.append({"scenario_id": scenario_id, **kwargs})
        return {"status": "failed", "confirmed": False, "receipts": []}

    service.async_run_scenario = uncertain
    clock[0] = 120_000
    await coordinator.async_reconcile_storage_due()
    await coordinator.async_handle_storage_change()

    assert len(service.calls) == 1
    assert coordinator.storage_state["transition"] == "storage_light_off_failed"
    assert coordinator.storage_state["deadlineMs"] is None


@pytest.mark.asyncio
async def test_uncertain_storage_on_is_not_retried_without_absence_transition() -> None:
    coordinator, _policy, service, states, _ownership, _store = await make_coordinator(
        clock=[0],
        states={"binary_sensor.motion": "on", "light.storage": "off"},
    )

    async def uncertain(scenario_id: str, **kwargs: object) -> dict[str, object]:
        service.calls.append({"scenario_id": scenario_id, **kwargs})
        return {"status": "failed", "confirmed": False, "receipts": []}

    service.async_run_scenario = uncertain
    await coordinator.async_handle_storage_change()
    await coordinator.async_handle_storage_change()
    assert len(service.calls) == 1
    assert coordinator.storage_state["transition"] == "storage_light_on_failed"

    states.values["binary_sensor.motion"] = "off"
    await coordinator.async_handle_storage_change()
    states.values["binary_sensor.motion"] = "on"
    await coordinator.async_handle_storage_change()
    assert len(service.calls) == 2


@pytest.mark.asyncio
async def test_missing_configured_sensor_is_unknown_not_absent() -> None:
    coordinator, _policy, service, _states, _ownership, _store = await make_coordinator(
        clock=[0],
        states={"binary_sensor.presence": "off", "light.storage": "on"},
        owned=True,
        presence_target_id="presence-target",
    )

    await coordinator.async_handle_storage_change()

    assert coordinator.storage_state["transition"] == "occupancy_unknown"
    assert coordinator.storage_state["deadlineMs"] is None
    assert service.calls == []


@pytest.mark.asyncio
async def test_unbound_exhaust_is_local_typed_skip_at_both_scheduled_times() -> None:
    coordinator, _policy, service, _states, _ownership, _store = await make_coordinator(
        clock=[0]
    )

    assert await coordinator.async_handle_storage_exhaust_schedule("11:00") == "unbound"
    assert await coordinator.async_handle_storage_exhaust_schedule("20:00") == "unbound"
    assert [call["trigger_context"]["trigger_id"] for call in service.calls] == [
        "storage_exhaust_unbound",
        "storage_exhaust_unbound",
    ]
    controls = await coordinator.async_control_context(
        "system-storage-light-controller",
        service.calls[-1]["correlation_id"],
        service.calls[-1]["trigger_context"],
    )
    assert controls["policy"]["storageExhaustTargetId"] is None
    assert controls["state"]["transition"] == "storage_exhaust_unbound"
    assert valid_scenario_control_state_payload(store_payload := coordinator.payload)
    assert store_payload["storage"]["fractionalRemainder"] == 0.0


@pytest.mark.asyncio
async def test_bound_exhaust_runs_on_then_off_at_the_durable_deadline() -> None:
    clock = [0]
    coordinator, _policy, service, _states, _ownership, _store = await make_coordinator(
        clock=clock,
        exhaust_target_id="fan-storage-target",
    )

    assert await coordinator.async_handle_storage_exhaust_schedule("11:00") == "started"
    assert coordinator.storage_state["transition"] == "storage_exhaust_running"
    assert coordinator.storage_state["exhaustDeadlineMs"] == 1_800_000
    clock[0] = 1_799_999
    await coordinator.async_reconcile_storage_exhaust_due()
    assert len(service.calls) == 1
    clock[0] = 1_800_000
    await coordinator.async_reconcile_storage_exhaust_due()

    assert [call["trigger_context"]["trigger_id"] for call in service.calls] == [
        "storage_exhaust_on",
        "storage_exhaust_off",
    ]
    assert coordinator.storage_state["transition"] == "idle"
    assert coordinator.storage_state["exhaustDeadlineMs"] is None


@pytest.mark.asyncio
async def test_runtime_wiring_uses_shared_latch_state_events_and_exact_policy_clocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0]
    coordinator, _policy, service, states, _ownership, _store = await make_coordinator(
        clock=clock,
        states={"binary_sensor.motion": "off", "light.storage": "off"},
    )
    callbacks: dict[str, object] = {}
    schedules: list[tuple[int, int, int, object]] = []

    class Bus:
        def async_listen(self, event_type, callback):
            callbacks[event_type] = callback
            return lambda: callbacks.pop(event_type, None)

    class Entry:
        unloads = []

        def async_on_unload(self, callback):
            self.unloads.append(callback)

    def track(_hass, callback, *, hour, minute, second):
        schedules.append((hour, minute, second, callback))
        return lambda: None

    homeassistant = ModuleType("homeassistant")
    helpers = ModuleType("homeassistant.helpers")
    event = ModuleType("homeassistant.helpers.event")
    event.async_track_time_change = track
    helpers.event = event
    homeassistant.helpers = helpers
    monkeypatch.setitem(sys.modules, "homeassistant", homeassistant)
    monkeypatch.setitem(sys.modules, "homeassistant.helpers", helpers)
    monkeypatch.setitem(sys.modules, "homeassistant.helpers.event", event)
    coordinator._hass.bus = Bus()  # noqa: SLF001
    latch = SimpleNamespace(is_open=False)
    entry = Entry()
    await coordinator.async_start(entry, latch)

    assert [(hour, minute, second) for hour, minute, second, _ in schedules] == [
        (0, 0, 0),
        (6, 0, 0),
        (8, 0, 0),
        (8, 30, 0),
        (9, 0, 0),
        (10, 0, 0),
        (11, 0, 0),
        (20, 0, 0),
        (21, 0, 0),
        (22, 0, 0),
        (22, 30, 0),
        (23, 0, 0),
        (23, 30, 0),
    ]
    states.values["binary_sensor.motion"] = "on"
    await callbacks["state_changed"](
        SimpleNamespace(data={"entity_id": "binary_sensor.motion"})
    )
    assert service.calls == []

    latch.is_open = True
    await callbacks["state_changed"](
        SimpleNamespace(data={"entity_id": "binary_sensor.motion"})
    )
    assert service.calls[0]["trigger_context"]["trigger_id"] == "storage_light_on"
    await next(callback for hour, _, _, callback in schedules if hour == 11)(None)
    assert service.calls[-1]["trigger_context"]["trigger_id"] == "storage_exhaust_unbound"
    coordinator.cancel()
    await asyncio.sleep(0)
