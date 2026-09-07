"""Executable coordinator regressions for the two corridor light profiles."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace
from typing import Mapping
from zoneinfo import ZoneInfo

import pytest

from custom_components.hausman_hub.application.scenario_control_coordinator import (
    SMALL_CORRIDOR_CHANDELIER_TARGET_ID,
    SMALL_CORRIDOR_LUX_TARGET_ID,
    SMALL_CORRIDOR_MOTION_TARGET_ID,
    SMALL_CORRIDOR_RELAY_TARGET_ID,
    SMALL_CORRIDOR_SCENARIO_ID,
    STORAGE_LIGHT_TARGET_ID,
    STORAGE_MOTION_TARGET_ID,
    SUN_TARGET_ID,
    TAMBUR_CHANDELIER_TARGET_ID,
    TAMBUR_ENTRY_DOOR_TARGET_ID,
    TAMBUR_MIRROR_TARGET_ID,
    TAMBUR_MOTION_TARGET_ID,
    TAMBUR_POINTS_TARGET_ID,
    TAMBUR_POWER_TARGET_ID,
    TAMBUR_PRESENCE_TARGET_IDS,
    TAMBUR_SCENARIO_ID,
    ScenarioControlCoordinator,
)
from custom_components.hausman_hub.application.scenario_control_policy import (
    ScenarioControlPolicyService,
)


class MemoryStore:
    def __init__(self) -> None:
        self.payload: object | None = None

    async def async_load(self) -> object | None:
        return self.payload

    async def async_save(self, payload: dict[str, object]) -> None:
        self.payload = payload


class MutableStates:
    def __init__(self) -> None:
        self.values: dict[str, SimpleNamespace] = {}

    def get(self, entity_id: str) -> object | None:
        return self.values.get(entity_id)

    def set(
        self,
        entity_id: str,
        state: str,
        *,
        brightness: int | None = None,
        kelvin: int | None = None,
    ) -> None:
        attributes: dict[str, object] = {}
        previous = self.values.get(entity_id)
        if previous is not None:
            attributes.update(previous.attributes)
        if brightness is not None:
            attributes["brightness"] = round(brightness * 255 / 100)
        if kelvin is not None:
            attributes["color_temp_kelvin"] = kelvin
        self.values[entity_id] = SimpleNamespace(
            state=state,
            attributes=attributes,
        )


class LightPriority:
    def __init__(
        self,
        *,
        owned: tuple[str, ...] = (),
        manual: tuple[str, ...] = (),
    ) -> None:
        self.owned = set(owned)
        self.manual = set(manual)
        self.released: list[frozenset[str]] = []

    def is_owned(self, entity_id: str, _hass: object) -> bool:
        return entity_id in self.owned

    def ownership_revision(self, entity_id: str, _hass: object) -> str | None:
        return f"owned:{entity_id}" if entity_id in self.owned else None

    def manual_claim_entity_ids(
        self, entity_ids: frozenset[str]
    ) -> frozenset[str]:
        return frozenset(self.manual.intersection(entity_ids))

    async def async_release_manual_claims(
        self, entity_ids: frozenset[str]
    ) -> None:
        self.released.append(entity_ids)
        self.manual.difference_update(entity_ids)


class ExecutingScenarioService:
    """Execute the coordinator's durable action instead of stubbing its result."""

    def __init__(
        self,
        states: MutableStates,
        priority: LightPriority,
        target_entities: Mapping[str, str],
    ) -> None:
        self.states = states
        self.priority = priority
        self.target_entities = target_entities
        self.coordinator: ScenarioControlCoordinator | None = None
        self.actions: list[tuple[str, str, str, int | None]] = []

    async def async_run_scenario(
        self, scenario_id: str, **kwargs: object
    ) -> dict[str, object]:
        coordinator = self.coordinator
        assert coordinator is not None
        correlation_id = str(kwargs["correlation_id"])
        trigger = kwargs["trigger_context"]
        assert isinstance(trigger, Mapping)
        controls = await coordinator.async_control_context(
            scenario_id, correlation_id, trigger
        )
        assert controls["state"]["ready"] is True
        action = controls["state"].get("action")
        if action is None:
            return {"status": "completed", "confirmed": True, "receipts": []}
        assert isinstance(action, Mapping)
        target_id = str(action["targetId"])
        action_id = str(action["actionId"])
        value = action.get("value")
        assert value is None or type(value) is int
        self.actions.append((scenario_id, target_id, action_id, value))
        entity_id = self.target_entities[target_id]
        if action_id == "turn_off":
            self.states.set(entity_id, "off")
            self.priority.owned.discard(entity_id)
        elif action_id == "turn_on":
            self.states.set(entity_id, "on")
            self.priority.owned.add(entity_id)
        elif action_id == "set_brightness_percent":
            assert type(value) is int
            self.states.set(entity_id, "on", brightness=value)
            self.priority.owned.add(entity_id)
        elif action_id == "set_color_temperature":
            assert type(value) is int
            self.states.set(entity_id, "on", kelvin=value)
            self.priority.owned.add(entity_id)
        else:  # pragma: no cover - a new action must be modeled deliberately
            raise AssertionError(action_id)
        return {"status": "completed", "confirmed": True, "receipts": []}


async def make_corridor_coordinator(
    *,
    local_now: list[datetime],
    clock_ms: list[int],
    state_overrides: Mapping[str, tuple[str, int | None, int | None]] | None = None,
    owned_targets: tuple[str, ...] = (),
    manual_targets: tuple[str, ...] = (),
) -> tuple[
    ScenarioControlCoordinator,
    ExecutingScenarioService,
    MutableStates,
    LightPriority,
]:
    target_entities = {
        STORAGE_MOTION_TARGET_ID: "binary_sensor.storage_motion",
        STORAGE_LIGHT_TARGET_ID: "light.storage",
        TAMBUR_MOTION_TARGET_ID: "binary_sensor.tambur_motion",
        TAMBUR_PRESENCE_TARGET_IDS[0]: "binary_sensor.tambur_presence_1",
        TAMBUR_PRESENCE_TARGET_IDS[1]: "binary_sensor.tambur_presence_2",
        TAMBUR_CHANDELIER_TARGET_ID: "light.tambur_chandelier",
        TAMBUR_POINTS_TARGET_ID: "light.tambur_points",
        TAMBUR_MIRROR_TARGET_ID: "light.tambur_mirror",
        TAMBUR_POWER_TARGET_ID: "switch.tambur_power",
        TAMBUR_ENTRY_DOOR_TARGET_ID: "lock.entry",
        SMALL_CORRIDOR_MOTION_TARGET_ID: "binary_sensor.small_corridor_motion",
        SMALL_CORRIDOR_LUX_TARGET_ID: "sensor.small_corridor_lux",
        SMALL_CORRIDOR_RELAY_TARGET_ID: "switch.small_corridor_relay",
        SMALL_CORRIDOR_CHANDELIER_TARGET_ID: "light.small_corridor_chandelier",
        SUN_TARGET_ID: "sun.sun",
    }
    defaults = {
        STORAGE_MOTION_TARGET_ID: ("off", None, None),
        STORAGE_LIGHT_TARGET_ID: ("off", None, None),
        TAMBUR_MOTION_TARGET_ID: ("on", None, None),
        TAMBUR_PRESENCE_TARGET_IDS[0]: ("off", None, None),
        TAMBUR_PRESENCE_TARGET_IDS[1]: ("off", None, None),
        TAMBUR_CHANDELIER_TARGET_ID: ("off", None, None),
        TAMBUR_POINTS_TARGET_ID: ("off", None, None),
        TAMBUR_MIRROR_TARGET_ID: ("off", None, None),
        TAMBUR_POWER_TARGET_ID: ("on", None, None),
        TAMBUR_ENTRY_DOOR_TARGET_ID: ("locked", None, None),
        SMALL_CORRIDOR_MOTION_TARGET_ID: ("on", None, None),
        SMALL_CORRIDOR_LUX_TARGET_ID: ("0", None, None),
        SMALL_CORRIDOR_RELAY_TARGET_ID: ("on", None, None),
        SMALL_CORRIDOR_CHANDELIER_TARGET_ID: ("off", None, None),
        SUN_TARGET_ID: ("above_horizon", None, None),
    }
    defaults.update(state_overrides or {})
    states = MutableStates()
    for target_id, (state, brightness, kelvin) in defaults.items():
        states.set(
            target_entities[target_id],
            state,
            brightness=brightness,
            kelvin=kelvin,
        )
    priority = LightPriority(
        owned=tuple(target_entities[target] for target in owned_targets),
        manual=tuple(target_entities[target] for target in manual_targets),
    )
    service = ExecutingScenarioService(states, priority, target_entities)
    policy = ScenarioControlPolicyService(MemoryStore())
    await policy.async_load()
    tasks: list[asyncio.Task[None]] = []
    hass = SimpleNamespace(
        states=states,
        config=SimpleNamespace(time_zone="Asia/Omsk"),
        async_create_task=lambda coroutine: tasks.append(asyncio.create_task(coroutine)),
    )
    coordinator = ScenarioControlCoordinator(
        hass,
        service,
        policy,
        MemoryStore(),
        priority,
        catalog_resolver=lambda target_id: (
            SimpleNamespace(entity_id=target_entities[target_id])
            if target_id in target_entities
            else None
        ),
        now_ms=lambda: clock_ms[0],
        now=lambda: local_now[0],
        schedule_tasks=False,
    )
    await coordinator.async_load()
    coordinator._test_tasks = tasks  # type: ignore[attr-defined]
    service.coordinator = coordinator
    return coordinator, service, states, priority


@pytest.mark.asyncio
async def test_tambur_profile_continues_after_ramp_completion() -> None:
    local_now = [datetime(2026, 9, 7, 12, 0, tzinfo=ZoneInfo("Asia/Omsk"))]
    clock_ms = [0]
    coordinator, service, _states, _priority = await make_corridor_coordinator(
        local_now=local_now,
        clock_ms=clock_ms,
        state_overrides={
            TAMBUR_CHANDELIER_TARGET_ID: ("on", 79, 2200),
        },
        owned_targets=(TAMBUR_CHANDELIER_TARGET_ID,),
    )

    await coordinator.async_handle_tambur_change()
    assert coordinator.payload["tambur"]["sequence"]["target"] == 80
    clock_ms[0] = 30_000
    await coordinator.async_reconcile_zone_due(TAMBUR_SCENARIO_ID)
    await coordinator.async_handle_tambur_change()

    assert service.actions == [
        (
            TAMBUR_SCENARIO_ID,
            TAMBUR_CHANDELIER_TARGET_ID,
            "set_brightness_percent",
            80,
        ),
        (
            TAMBUR_SCENARIO_ID,
            TAMBUR_CHANDELIER_TARGET_ID,
            "set_color_temperature",
            3000,
        ),
        (TAMBUR_SCENARIO_ID, TAMBUR_POINTS_TARGET_ID, "turn_on", None),
    ]


@pytest.mark.asyncio
async def test_manual_profile_release_continues_into_owned_light_fade() -> None:
    local_now = [datetime(2026, 9, 7, 12, 0, tzinfo=ZoneInfo("Asia/Omsk"))]
    clock_ms = [0]
    coordinator, service, _states, priority = await make_corridor_coordinator(
        local_now=local_now,
        clock_ms=clock_ms,
        state_overrides={
            TAMBUR_MOTION_TARGET_ID: ("off", None, None),
            TAMBUR_CHANDELIER_TARGET_ID: ("on", 80, 3000),
            TAMBUR_POINTS_TARGET_ID: ("on", None, None),
        },
        owned_targets=(TAMBUR_CHANDELIER_TARGET_ID,),
        manual_targets=(TAMBUR_POINTS_TARGET_ID,),
    )

    await coordinator.async_handle_tambur_change()
    assert coordinator.payload["tambur"]["transition"] == "manual_release_pending"
    clock_ms[0] = 300_000
    await coordinator.async_handle_tambur_change()

    assert priority.released == [frozenset({"light.tambur_points"})]
    assert service.actions == [
        (
            TAMBUR_SCENARIO_ID,
            TAMBUR_CHANDELIER_TARGET_ID,
            "set_color_temperature",
            3800,
        )
    ]
    sequence = coordinator.payload["tambur"]["sequence"]
    assert sequence == {
        "kind": "fade",
        "startedAtMs": 0,
        "deadlineMs": 300_000,
        "start": 80,
        "target": 72,
        "current": 80,
    }


@pytest.mark.asyncio
async def test_clock_boundaries_never_activate_an_off_tambur_main_light() -> None:
    local_now = [datetime(2026, 9, 7, 9, 0, tzinfo=ZoneInfo("Asia/Omsk"))]
    coordinator, service, _states, _priority = await make_corridor_coordinator(
        local_now=local_now,
        clock_ms=[0],
    )

    await coordinator.async_handle_controller_clock("09:00")
    local_now[0] = local_now[0].replace(hour=10)
    await coordinator.async_handle_controller_clock("10:00")

    assert service.actions == []


@pytest.mark.asyncio
async def test_restart_reconciliation_never_activates_off_corridor_lights() -> None:
    coordinator, service, _states, _priority = await make_corridor_coordinator(
        local_now=[datetime(2026, 9, 7, 12, 0, tzinfo=ZoneInfo("Asia/Omsk"))],
        clock_ms=[0],
    )

    coordinator.activate()
    await asyncio.gather(*coordinator._test_tasks)  # type: ignore[attr-defined]

    assert service.actions == []
    assert coordinator.payload["tambur"]["transition"] == "controller_idle"
    assert coordinator.payload["smallCorridor"]["transition"] == "controller_idle"


@pytest.mark.asyncio
async def test_tambur_motion_is_immediate_but_presence_requires_ten_seconds() -> None:
    local_now = [datetime(2026, 9, 7, 12, 0, tzinfo=ZoneInfo("Asia/Omsk"))]

    motion, motion_service, _states, _priority = await make_corridor_coordinator(
        local_now=local_now,
        clock_ms=[0],
    )
    await motion.async_handle_tambur_change()
    assert motion_service.actions[0][1:] == (
        TAMBUR_CHANDELIER_TARGET_ID,
        "set_brightness_percent",
        5,
    )

    clock_ms = [0]
    presence, presence_service, _states2, _priority2 = (
        await make_corridor_coordinator(
            local_now=local_now,
            clock_ms=clock_ms,
            state_overrides={
                TAMBUR_MOTION_TARGET_ID: ("off", None, None),
                TAMBUR_PRESENCE_TARGET_IDS[0]: ("on", None, None),
            },
        )
    )
    await presence.async_handle_tambur_change()
    assert presence_service.actions == []
    assert presence.payload["tambur"]["transition"] == "presence_rise_pending"
    clock_ms[0] = 9_999
    await presence.async_reconcile_zone_due(TAMBUR_SCENARIO_ID)
    assert presence_service.actions == []
    clock_ms[0] = 10_000
    await presence.async_reconcile_zone_due(TAMBUR_SCENARIO_ID)
    assert presence_service.actions[0][1:] == (
        TAMBUR_CHANDELIER_TARGET_ID,
        "set_brightness_percent",
        5,
    )


@pytest.mark.asyncio
async def test_cancelled_presence_rise_starts_absence_at_the_falling_edge() -> None:
    clock_ms = [0]
    coordinator, service, states, _priority = await make_corridor_coordinator(
        local_now=[datetime(2026, 9, 7, 12, 0, tzinfo=ZoneInfo("Asia/Omsk"))],
        clock_ms=clock_ms,
        state_overrides={
            TAMBUR_MOTION_TARGET_ID: ("off", None, None),
            TAMBUR_PRESENCE_TARGET_IDS[0]: ("on", None, None),
        },
    )

    await coordinator.async_handle_tambur_change()
    clock_ms[0] = 5_000
    states.set("binary_sensor.tambur_presence_1", "off")
    await coordinator.async_handle_tambur_change()

    assert service.actions == []
    assert coordinator.payload["tambur"]["absenceStartedAtMs"] == 5_000
    assert coordinator.payload["tambur"]["deadlineMs"] == 15_000


@pytest.mark.asyncio
async def test_positive_or_unknown_sensor_evidence_stops_an_active_fade() -> None:
    for new_motion, expected_transition in (
        ("on", "light_action"),
        ("unavailable", "controller_unknown"),
    ):
        clock_ms = [0]
        coordinator, _service, states, _priority = await make_corridor_coordinator(
            local_now=[datetime(2026, 9, 7, 12, 0, tzinfo=ZoneInfo("Asia/Omsk"))],
            clock_ms=clock_ms,
            state_overrides={
                TAMBUR_MOTION_TARGET_ID: ("off", None, None),
                TAMBUR_CHANDELIER_TARGET_ID: ("on", 80, 3800),
            },
            owned_targets=(TAMBUR_CHANDELIER_TARGET_ID,),
        )
        await coordinator.async_handle_tambur_change()
        clock_ms[0] = 10_000
        await coordinator.async_handle_tambur_change()
        assert coordinator.payload["tambur"]["sequence"]["kind"] == "fade"

        clock_ms[0] = 11_000
        states.set("binary_sensor.tambur_motion", new_motion)
        await coordinator.async_handle_tambur_change()

        assert coordinator.payload["tambur"]["sequence"] is None
        assert coordinator.payload["tambur"]["absenceStartedAtMs"] is None
        assert coordinator.payload["tambur"]["transition"] == expected_transition


@pytest.mark.asyncio
async def test_absence_fade_warms_then_moves_80_to_72_once_in_one_point_steps() -> None:
    clock_ms = [0]
    coordinator, service, _states, _priority = await make_corridor_coordinator(
        local_now=[datetime(2026, 9, 7, 12, 0, tzinfo=ZoneInfo("Asia/Omsk"))],
        clock_ms=clock_ms,
        state_overrides={
            TAMBUR_MOTION_TARGET_ID: ("off", None, None),
            TAMBUR_CHANDELIER_TARGET_ID: ("on", 80, 3000),
        },
        owned_targets=(TAMBUR_CHANDELIER_TARGET_ID,),
    )

    await coordinator.async_handle_tambur_change()
    clock_ms[0] = 10_000
    await coordinator.async_handle_tambur_change()
    assert service.actions[0][2:] == ("set_color_temperature", 3800)
    sequence = coordinator.payload["tambur"]["sequence"]
    assert sequence["startedAtMs"] == 0
    assert sequence["deadlineMs"] == 300_000
    assert sequence["target"] == 72

    for due in (37_500, 75_000, 112_500, 150_000, 187_500, 225_000, 262_500, 300_000):
        clock_ms[0] = due
        await coordinator.async_reconcile_zone_due(TAMBUR_SCENARIO_ID)
        before = list(service.actions)
        await coordinator.async_reconcile_zone_due(TAMBUR_SCENARIO_ID)
        assert service.actions == before

    assert [
        value
        for _scenario, _target, action, value in service.actions
        if action == "set_brightness_percent"
    ] == [79, 78, 77, 76, 75, 74, 73, 72]
    completed_actions = list(service.actions)
    clock_ms[0] = 301_000
    await coordinator.async_handle_tambur_change()
    await coordinator.async_handle_tambur_change()
    assert service.actions == completed_actions
    assert coordinator.payload["tambur"]["sequence"] is None


@pytest.mark.asyncio
async def test_small_corridor_lux_hold_restarts_after_hysteresis_bounce() -> None:
    clock_ms = [0]
    coordinator, service, states, _priority = await make_corridor_coordinator(
        local_now=[datetime(2026, 9, 7, 12, 0, tzinfo=ZoneInfo("Asia/Omsk"))],
        clock_ms=clock_ms,
        state_overrides={
            SMALL_CORRIDOR_LUX_TARGET_ID: ("400", None, None),
        },
    )

    await coordinator.async_handle_small_corridor_change()
    assert coordinator.payload["smallCorridor"]["luxCandidateSinceMs"] == 0
    assert coordinator.payload["smallCorridor"]["deadlineMs"] == 30_000
    clock_ms[0] = 29_999
    await coordinator.async_reconcile_zone_due(SMALL_CORRIDOR_SCENARIO_ID)
    assert service.actions == []

    states.set("sensor.small_corridor_lux", "430")
    await coordinator.async_handle_small_corridor_change()
    assert coordinator.payload["smallCorridor"]["transition"] == "lux_too_bright"
    assert coordinator.payload["smallCorridor"]["luxCandidateSinceMs"] is None
    states.set("sensor.small_corridor_lux", "400")
    clock_ms[0] = 31_000
    await coordinator.async_handle_small_corridor_change()
    assert coordinator.payload["smallCorridor"]["luxCandidateSinceMs"] == 31_000
    clock_ms[0] = 60_999
    await coordinator.async_reconcile_zone_due(SMALL_CORRIDOR_SCENARIO_ID)
    assert service.actions == []
    clock_ms[0] = 61_000
    await coordinator.async_reconcile_zone_due(SMALL_CORRIDOR_SCENARIO_ID)
    assert service.actions[0][1:] == (
        SMALL_CORRIDOR_CHANDELIER_TARGET_ID,
        "set_brightness_percent",
        5,
    )


@pytest.mark.asyncio
async def test_lit_small_corridor_ignores_its_own_high_lux() -> None:
    coordinator, service, _states, _priority = await make_corridor_coordinator(
        local_now=[datetime(2026, 9, 7, 12, 0, tzinfo=ZoneInfo("Asia/Omsk"))],
        clock_ms=[0],
        state_overrides={
            SMALL_CORRIDOR_LUX_TARGET_ID: ("900", None, None),
            SMALL_CORRIDOR_CHANDELIER_TARGET_ID: ("on", 80, 3000),
        },
        owned_targets=(SMALL_CORRIDOR_CHANDELIER_TARGET_ID,),
    )

    await coordinator.async_handle_small_corridor_change()

    assert service.actions == []
    assert coordinator.payload["smallCorridor"]["transition"] == "occupied_hold"
    assert coordinator.payload["smallCorridor"]["luxCandidateSinceMs"] is None


@pytest.mark.asyncio
async def test_policy_revision_cancels_both_corridor_sequences() -> None:
    coordinator, _service, _states, _priority = await make_corridor_coordinator(
        local_now=[datetime(2026, 9, 7, 12, 0, tzinfo=ZoneInfo("Asia/Omsk"))],
        clock_ms=[0],
        state_overrides={
            TAMBUR_CHANDELIER_TARGET_ID: ("on", 79, 3000),
            SMALL_CORRIDOR_CHANDELIER_TARGET_ID: ("on", 79, 3000),
        },
        owned_targets=(
            TAMBUR_CHANDELIER_TARGET_ID,
            SMALL_CORRIDOR_CHANDELIER_TARGET_ID,
        ),
    )
    await coordinator.async_handle_tambur_change()
    await coordinator.async_handle_small_corridor_change()
    assert coordinator.payload["tambur"]["sequence"] is not None
    assert coordinator.payload["smallCorridor"]["sequence"] is not None

    policy = coordinator._policy_service  # noqa: SLF001
    await policy.async_replace(
        0,
        replace(policy.current.policy, brightness_ramp_seconds=31),
    )

    assert coordinator.payload["tambur"]["transition"] == "policy_changed"
    assert coordinator.payload["tambur"]["sequence"] is None
    assert coordinator.payload["smallCorridor"]["transition"] == "policy_changed"
    assert coordinator.payload["smallCorridor"]["sequence"] is None


@pytest.mark.asyncio
async def test_exact_night_boundaries_cap_then_turn_off_only_owned_lights() -> None:
    local_now = [datetime(2026, 9, 7, 23, 0, tzinfo=ZoneInfo("Asia/Omsk"))]
    coordinator, service, _states, _priority = await make_corridor_coordinator(
        local_now=local_now,
        clock_ms=[0],
        state_overrides={
            TAMBUR_CHANDELIER_TARGET_ID: ("on", 5, 3800),
            TAMBUR_POINTS_TARGET_ID: ("on", None, None),
            SMALL_CORRIDOR_CHANDELIER_TARGET_ID: ("on", 80, 3000),
            SMALL_CORRIDOR_RELAY_TARGET_ID: ("on", None, None),
        },
        owned_targets=(
            TAMBUR_CHANDELIER_TARGET_ID,
            TAMBUR_POINTS_TARGET_ID,
            SMALL_CORRIDOR_CHANDELIER_TARGET_ID,
        ),
    )

    await coordinator.async_handle_controller_clock("23:00")
    assert [action[1:] for action in service.actions] == [
        (TAMBUR_MIRROR_TARGET_ID, "turn_on", None),
        (TAMBUR_CHANDELIER_TARGET_ID, "turn_off", None),
        (TAMBUR_POINTS_TARGET_ID, "turn_off", None),
    ]
    service.actions.clear()
    local_now[0] = local_now[0].replace(minute=30)
    await coordinator.async_handle_controller_clock("23:30")
    assert [action[1:] for action in service.actions] == [
        (SMALL_CORRIDOR_CHANDELIER_TARGET_ID, "turn_off", None),
        (SMALL_CORRIDOR_RELAY_TARGET_ID, "turn_off", None),
    ]


@pytest.mark.asyncio
async def test_sunset_starts_owned_tambur_cap_to_five_without_activating_main() -> None:
    local_now = [datetime(2026, 9, 7, 18, 0, tzinfo=ZoneInfo("Asia/Omsk"))]
    clock_ms = [0]
    coordinator, service, states, _priority = await make_corridor_coordinator(
        local_now=local_now,
        clock_ms=clock_ms,
        state_overrides={
            TAMBUR_CHANDELIER_TARGET_ID: ("on", 80, 3800),
            TAMBUR_POINTS_TARGET_ID: ("on", None, None),
            SUN_TARGET_ID: ("below_horizon", None, None),
        },
        owned_targets=(TAMBUR_CHANDELIER_TARGET_ID, TAMBUR_POINTS_TARGET_ID),
    )
    states.values["sun.sun"].last_changed = local_now[0]

    await coordinator.async_handle_tambur_change(
        trigger_entity_id="sun.sun",
        old_state=SimpleNamespace(state="above_horizon"),
        new_state=SimpleNamespace(state="below_horizon"),
    )

    assert service.actions == [
        (TAMBUR_SCENARIO_ID, TAMBUR_MIRROR_TARGET_ID, "turn_on", None)
    ]
    sequence = coordinator.payload["tambur"]["sequence"]
    assert sequence["kind"] == "cap"
    assert sequence["start"] == 80
    assert sequence["target"] == 5
    assert sequence["startedAtMs"] == 0
    assert sequence["deadlineMs"] == 18_000_000

    off, off_service, off_states, _off_priority = await make_corridor_coordinator(
        local_now=local_now,
        clock_ms=[0],
        state_overrides={SUN_TARGET_ID: ("below_horizon", None, None)},
    )
    off_states.values["sun.sun"].last_changed = local_now[0]
    await off.async_handle_tambur_change(
        trigger_entity_id="sun.sun",
        old_state=SimpleNamespace(state="above_horizon"),
        new_state=SimpleNamespace(state="below_horizon"),
    )
    assert off_service.actions == [
        (TAMBUR_SCENARIO_ID, TAMBUR_MIRROR_TARGET_ID, "turn_on", None)
    ]


@pytest.mark.asyncio
async def test_latest_evening_boundary_starts_cap_when_sunset_is_late() -> None:
    local_now = [datetime(2026, 9, 7, 21, 0, tzinfo=ZoneInfo("Asia/Omsk"))]
    coordinator, service, _states, _priority = await make_corridor_coordinator(
        local_now=local_now,
        clock_ms=[0],
        state_overrides={
            TAMBUR_CHANDELIER_TARGET_ID: ("on", 80, 3800),
            TAMBUR_POINTS_TARGET_ID: ("on", None, None),
            SUN_TARGET_ID: ("above_horizon", None, None),
        },
        owned_targets=(TAMBUR_CHANDELIER_TARGET_ID, TAMBUR_POINTS_TARGET_ID),
    )

    await coordinator.async_handle_controller_clock("21:00")

    assert service.actions == [
        (TAMBUR_SCENARIO_ID, TAMBUR_MIRROR_TARGET_ID, "turn_on", None)
    ]
    sequence = coordinator.payload["tambur"]["sequence"]
    assert sequence["kind"] == "cap"
    assert sequence["target"] == 5
    assert sequence["deadlineMs"] == 7_200_000


@pytest.mark.asyncio
async def test_small_corridor_is_capped_at_five_from_23_to_2330() -> None:
    coordinator, service, _states, _priority = await make_corridor_coordinator(
        local_now=[datetime(2026, 9, 7, 23, 10, tzinfo=ZoneInfo("Asia/Omsk"))],
        clock_ms=[0],
        state_overrides={
            SMALL_CORRIDOR_CHANDELIER_TARGET_ID: ("on", 80, 3000),
            SUN_TARGET_ID: ("below_horizon", None, None),
        },
        owned_targets=(SMALL_CORRIDOR_CHANDELIER_TARGET_ID,),
    )

    await coordinator.async_handle_small_corridor_change()

    assert service.actions == []
    sequence = coordinator.payload["smallCorridor"]["sequence"]
    assert sequence["kind"] == "cap"
    assert sequence["start"] == 80
    assert sequence["target"] == 5


@pytest.mark.asyncio
async def test_small_corridor_waits_for_sunrise_instead_of_nine_to_ten_clock() -> None:
    local_now = [datetime(2026, 9, 7, 9, 0, tzinfo=ZoneInfo("Asia/Omsk"))]
    clock_ms = [0]
    coordinator, service, states, _priority = await make_corridor_coordinator(
        local_now=local_now,
        clock_ms=clock_ms,
        state_overrides={
            SMALL_CORRIDOR_LUX_TARGET_ID: ("400", None, None),
            SUN_TARGET_ID: ("below_horizon", None, None),
        },
    )

    await coordinator.async_handle_small_corridor_change()
    await coordinator.async_handle_controller_clock("09:00")
    local_now[0] = local_now[0].replace(hour=10)
    await coordinator.async_handle_controller_clock("10:00")
    assert service.actions == []

    local_now[0] = local_now[0].replace(hour=10, minute=30)
    states.set("sun.sun", "above_horizon")
    await coordinator.async_handle_small_corridor_change()
    assert coordinator.payload["smallCorridor"]["transition"] == "lux_hold_pending"
    clock_ms[0] = 30_000
    await coordinator.async_reconcile_zone_due(SMALL_CORRIDOR_SCENARIO_ID)
    assert service.actions[0][1:] == (
        SMALL_CORRIDOR_CHANDELIER_TARGET_ID,
        "set_brightness_percent",
        5,
    )


@pytest.mark.asyncio
async def test_manual_profile_outranks_night_caps_and_off_boundaries() -> None:
    local_now = [datetime(2026, 9, 7, 23, 0, tzinfo=ZoneInfo("Asia/Omsk"))]
    coordinator, service, _states, _priority = await make_corridor_coordinator(
        local_now=local_now,
        clock_ms=[0],
        state_overrides={
            TAMBUR_CHANDELIER_TARGET_ID: ("on", 100, 3000),
            TAMBUR_POINTS_TARGET_ID: ("on", None, None),
            SMALL_CORRIDOR_CHANDELIER_TARGET_ID: ("on", 100, 3000),
        },
        manual_targets=(
            TAMBUR_CHANDELIER_TARGET_ID,
            SMALL_CORRIDOR_CHANDELIER_TARGET_ID,
        ),
    )

    await coordinator.async_handle_controller_clock("23:00")
    local_now[0] = local_now[0].replace(minute=30)
    await coordinator.async_handle_controller_clock("23:30")

    assert service.actions == [
        (TAMBUR_SCENARIO_ID, TAMBUR_MIRROR_TARGET_ID, "turn_on", None)
    ]
