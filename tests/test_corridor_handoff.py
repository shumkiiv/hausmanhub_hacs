"""Transferred corridor must have no legacy writer, including stale timers."""
import ast
from pathlib import Path
from types import SimpleNamespace
import pytest

from custom_components.hausman_hub.application.scenario_control_coordinator import (
    ScenarioControlCoordinator, SMALL_CORRIDOR_SCENARIO_ID,
    SMALL_CORRIDOR_RELAY_TARGET_ID, SMALL_CORRIDOR_CHANDELIER_TARGET_ID,
    STORAGE_LIGHT_TARGET_ID,
)


def forbidden(*args, **kwargs):
    raise AssertionError('transferred corridor reached legacy logic')


def coordinator():
    import asyncio
    value=ScenarioControlCoordinator.__new__(ScenarioControlCoordinator)
    value._decision_lock=asyncio.Lock()
    value._target_state=forbidden
    value.set_externally_managed_scenarios(frozenset({SMALL_CORRIDOR_SCENARIO_ID}))
    value._resolve_target_id=lambda role:role
    return value


def test_command_inventory_releases_corridor_only():
    value=coordinator()
    assert STORAGE_LIGHT_TARGET_ID in value.command_target_ids
    assert SMALL_CORRIDOR_RELAY_TARGET_ID not in value.command_target_ids
    assert SMALL_CORRIDOR_CHANDELIER_TARGET_ID not in value.command_target_ids


async def test_direct_state_handler_never_evaluates_transferred_room():
    await coordinator().async_handle_small_corridor_change()


async def test_stale_zone_timer_does_not_even_load_transferred_record():
    value=coordinator()
    value._record_for_scenario=forbidden
    await value.async_reconcile_zone_due(SMALL_CORRIDOR_SCENARIO_ID)


def test_stale_zone_timer_is_not_rearmed():
    value=coordinator()
    value._schedule_tasks=True
    value._record_for_scenario=forbidden
    value._schedule_zone_due(SMALL_CORRIDOR_SCENARIO_ID)


async def test_final_dispatch_never_resolves_or_runs_transferred_target():
    value=coordinator()
    value._resolve_target_id=forbidden
    assert not await value._run_zone_action(SMALL_CORRIDOR_SCENARIO_ID,SMALL_CORRIDOR_CHANDELIER_TARGET_ID,'turn_off',None)


async def test_clock_boundary_cannot_touch_transferred_room():
    import asyncio
    value=coordinator()
    value._decision_lock=asyncio.Lock()
    value._policy_service=SimpleNamespace(current=SimpleNamespace(policy=SimpleNamespace(
        evening_latest='22:00',tambur_main_off='23:00',small_corridor_main_off='23:30')))
    value._cancel_zone_task=forbidden
    await value.async_handle_controller_clock('23:30')


def test_startup_transfers_exact_three_selected_room_controllers():
    source=(Path(__file__).parents[1]/'custom_components/hausman_hub/__init__.py').read_text()
    tree=ast.parse(source)
    assignment=next(n for n in ast.walk(tree) if isinstance(n,ast.Assign) and
                    any(isinstance(t,ast.Name) and t.id=='externally_managed_scenarios' for t in n.targets))
    assert {n.id for n in ast.walk(assignment.value) if isinstance(n,ast.Name)}=={
        'frozenset','TAMBUR_SCENARIO_ID','SHOWER_SCENARIO_ID','SMALL_CORRIDOR_SCENARIO_ID'}
