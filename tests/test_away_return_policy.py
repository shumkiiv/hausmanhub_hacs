"""Return admission and pre-send guards, entirely offline."""

from dataclasses import replace
import asyncio
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.hausman_hub.application.away_return_policy import (
    AwayReturnActionFilter, AwayReturnPolicyBlocked,
)
from custom_components.hausman_hub.application.scenarios import (
    ScenarioCatalog, ScenarioDeviceEntry,
)
from custom_components.hausman_hub.domain.away_settings import AwayAction
from custom_components.hausman_hub.domain.device_power_dependencies import DevicePowerDependency
from custom_components.hausman_hub.domain.room_lighting import config_from_payload
from tests.test_room_lighting_domain import _payload
from tests.test_away_settings import RuntimeHarness, _settings_document, _state, _drain
from custom_components.hausman_hub.domain.away_settings import validate_away_settings
from custom_components.hausman_hub.application.device_power_dependencies import DevicePowerDependencyService
from custom_components.hausman_hub.application.scenario_service import ScenarioService
from custom_components.hausman_hub.application.scenario_executor import ScenarioExecutor, PowerSourceDispatchUncertain
from custom_components.hausman_hub.application.scenarios import ScenarioDeviceAction


def _config(action="auto_only", restore="by_current_conditions"):
    payload = _payload()
    payload["awayBehavior"] = {"mode": "room_off"}
    if action is not None:
        payload["awayBehavior"]["return"] = {"initialAction": action, "restore": restore}
    return config_from_payload(payload)


class Harness:
    def __init__(self, config):
        self.configs = (config,)
        self.generation = 1
        self.dependency_revision = 1
        self.catalog_generation = 1
        self.devices = {
            key: ScenarioDeviceEntry(key, key, entity, ()) for key, entity in {
                "alias_main": "light.demo_main", "alias_spots": "switch.demo_spots",
                "alias_power": "switch.demo_power", "alias_upstream": "switch.upstream",
                "other": "light.other", "same_power": "light.same_power",
            }.items()
        }
        self.dependencies = {
            "light.demo_main": DevicePowerDependency("light.demo_main", "switch.demo_power"),
            "switch.demo_power": DevicePowerDependency("switch.demo_power", "switch.upstream"),
            "light.same_power": DevicePowerDependency("light.same_power", "switch.upstream"),
        }
        self.filter = AwayReturnActionFilter(
            configs=self.snapshot, generation=lambda: self.generation,
            catalog=lambda: ScenarioCatalog(self.devices, {}),
            dependencies=lambda: self.dependencies,
            dependency_revision=lambda: self.dependency_revision,
            catalog_generation=lambda: self.catalog_generation,
        )

    async def snapshot(self):
        return self.generation, self.configs


@pytest.mark.asyncio
@pytest.mark.parametrize("initial", ["auto_only", "evaluate_profile"])
@pytest.mark.parametrize("restore", ["none", "by_current_conditions"])
@pytest.mark.parametrize("action,value", [
    ("turn_on", None), ("turn_off", None),
    ("set_brightness_percent", 65), ("set_color_temperature", 3500),
])
async def test_explicit_room_policy_blocks_all_general_light_and_power_actions(initial, restore, action, value):
    harness = Harness(_config(initial, restore))
    targets = ("alias_main", "alias_spots", "alias_power", "alias_upstream",
               "light_demo_main", "light.demo_main", "same_power", "unresolved", "other")
    actions = tuple(AwayAction(target, action, value) for target in targets)
    assert await harness.filter(actions) == (actions[-1],)
    harness.filter.dispatch_guard("other")()
    with pytest.raises(AwayReturnPolicyBlocked):
        harness.filter.dispatch_guard("alias_main")


@pytest.mark.asyncio
async def test_no_explicit_policy_preserves_legacy_even_for_unresolved_target():
    harness = Harness(_config(None))
    actions = tuple(AwayAction(target, "turn_on") for target in ("alias_main", "unresolved", "other"))
    assert await harness.filter(actions) == actions
    for item in actions:
        harness.filter.dispatch_guard(item.target_id)()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["generation", "dependency", "catalog", "unavailable", "dependency_revision", "catalog_generation"])
async def test_pre_send_guard_rejects_later_policy_or_binding_changes(change):
    harness = Harness(_config())
    assert await harness.filter((AwayAction("other", "turn_on"),))
    guard = harness.filter.dispatch_guard("other")
    if change == "generation":
        harness.generation += 1
    elif change == "dependency":
        harness.dependencies["light.other"] = DevicePowerDependency("light.other", "switch.demo_power")
    elif change == "catalog":
        harness.devices["other"] = replace(harness.devices["other"], entity_id="light.demo_main")
    elif change == "dependency_revision":
        harness.dependency_revision += 1
    elif change == "catalog_generation":
        harness.catalog_generation += 1
    else:
        harness.devices.pop("other")
    with pytest.raises(AwayReturnPolicyBlocked):
        guard()


@pytest.mark.asyncio
async def test_failed_snapshot_does_not_leave_previous_dispatch_permission():
    harness = Harness(_config())
    await harness.filter((AwayAction("other", "turn_on"),))
    async def failed_snapshot():
        raise OSError("unavailable")
    harness.filter._configs = failed_snapshot
    with pytest.raises(OSError):
        await harness.filter((AwayAction("other", "turn_on"),))
    with pytest.raises(AwayReturnPolicyBlocked):
        harness.filter.dispatch_guard("other")


@pytest.mark.asyncio
async def test_room_name_and_identifier_do_not_choose_initial_action():
    config = replace(_config(), room_id="room_other", name="Другая комната")
    harness = Harness(config)
    assert await harness.filter((AwayAction("alias_main", "turn_on"),)) == ()


@pytest.mark.asyncio
async def test_configuration_generation_changed_during_snapshot_is_rejected():
    harness = Harness(_config())
    async def changed_snapshot():
        harness.generation += 1
        return harness.generation - 1, harness.configs
    harness.filter._configs = changed_snapshot
    with pytest.raises(AwayReturnPolicyBlocked):
        await harness.filter((AwayAction("other", "turn_on"),))


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["dependencies", "catalog"])
async def test_binding_aba_during_real_executor_source_lock_never_dispatches(change):
    policy = Harness(_config())
    action = ScenarioDeviceAction("turn_on", "On", "light", "turn_on", frozenset())
    devices = dict(policy.devices)
    devices["other"] = replace(devices["other"], actions=(action,))
    catalog = ScenarioCatalog(dict(devices), {})
    async def load_catalog():
        return ScenarioCatalog(dict(devices), {})
    service = ScenarioService(None, SimpleNamespace(), catalog, catalog_loader=load_catalog)
    graph = DevicePowerDependencyService(
        SimpleNamespace(async_load=AsyncMock(return_value=None), async_save=AsyncMock()),
        electrical_breaker_resolver=lambda _: False,
    )
    await graph.async_load()
    def link(entity):
        return {"dependentEntityId": entity, "powerSourceEntityId": "switch.demo_power",
                "policy": "auto_turn_on", "warmupSeconds": 0}
    original = [link("light.demo_main")]
    await graph.async_replace(0, original)
    policy.filter._dependencies = lambda: graph.mapping
    policy.filter._catalog = service.current_catalog
    policy.filter._dependency_revision = lambda: graph.revision
    policy.filter._catalog_generation = lambda: service.catalog_binding_generation
    assert await policy.filter((AwayAction("other", "turn_on"),))
    guard = policy.filter.dispatch_guard("other")
    if change == "dependencies":
        await graph.async_replace(1, original + [link("light.other")])
    else:
        devices["other"] = replace(devices["other"], entity_id="light.demo_main")
        await service._async_replace_catalog()
    hass = SimpleNamespace(
        services=SimpleNamespace(async_call=AsyncMock()),
        states=SimpleNamespace(get=lambda _: SimpleNamespace(state="off", attributes={})),
    )
    executor = ScenarioExecutor(
        hass, service.current_catalog(), AsyncMock(),
        power_dependency_resolver=lambda: graph.mapping,
        electrical_breaker_resolver=lambda _: False, command_guard=lambda *_: None,
    )
    executor._wait_for_entity_state = AsyncMock(return_value=False)
    waiting = asyncio.Event()
    class ObservedLock(asyncio.Lock):
        async def acquire(self):
            waiting.set()
            return await super().acquire()
    lock = ObservedLock()
    await asyncio.Lock.acquire(lock)
    executor._power_source_locks["switch.demo_power"] = lock
    task = asyncio.create_task(executor.async_execute_device_action("other", "turn_on", dispatch_marker=guard))
    try:
        await asyncio.wait_for(waiting.wait(), 1)
        if change == "dependencies":
            await graph.async_replace(2, original)
        else:
            devices["other"] = catalog.devices["other"]
            await service._async_replace_catalog()
            executor.replace_catalog(service.current_catalog())
            assert service.current_catalog().device("other") == catalog.device("other")
        lock.release()
        with pytest.raises(PowerSourceDispatchUncertain):
            await asyncio.wait_for(task, 1)
        hass.services.async_call.assert_not_awaited()
    finally:
        if lock.locked():
            lock.release()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_equal_catalog_refresh_preserves_real_return_dispatch():
    policy = Harness(_config())
    action = ScenarioDeviceAction("turn_on", "On", "light", "turn_on", frozenset())
    devices = dict(policy.devices)
    devices["other"] = replace(devices["other"], actions=(action,))
    async def loader():
        return ScenarioCatalog(dict(devices), {})
    service = ScenarioService(None, SimpleNamespace(), await loader(), catalog_loader=loader)
    policy.filter._catalog = service.current_catalog
    policy.filter._catalog_generation = lambda: service.catalog_binding_generation
    await policy.filter((AwayAction("other", "turn_on"),))
    guard = policy.filter.dispatch_guard("other")
    previous = service.current_catalog()
    await service._async_replace_catalog()
    assert previous is not service.current_catalog()
    hass = SimpleNamespace(
        services=SimpleNamespace(async_call=AsyncMock()),
        states=SimpleNamespace(get=lambda _: SimpleNamespace(state="off", attributes={})),
    )
    executor = ScenarioExecutor(
        hass, service.current_catalog(), AsyncMock(),
        readback_window_seconds=0.01, readback_interval_seconds=0.01,
    )
    await executor.async_execute_device_action("other", "turn_on", dispatch_marker=guard)
    hass.services.async_call.assert_awaited_once_with(
        "light", "turn_on", {"entity_id": "light.other"}, blocking=True,
    )


def _wired_runner(runtime, policy, executor):
    """Compile the actual composition closures, not a rewritten test adapter."""
    source = (Path(__file__).parents[1] / "custom_components/hausman_hub/__init__.py").read_text()
    setup = next(n for n in ast.parse(source).body if isinstance(n, ast.AsyncFunctionDef) and n.name == "async_setup_entry")
    names = {"_away_return_dispatch_guard", "_run_away_actions"}
    nodes = [n for n in setup.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    assert len(nodes) == 2
    environment = {"away_runtime": runtime, "away_return_filter": policy, "scenario_service": executor}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "__init__.py", "exec"), environment)
    return environment["_run_away_actions"]


@pytest.mark.asyncio
async def test_actual_wiring_preserves_valid_return_and_unmarked_departure():
    harness = RuntimeHarness()
    settings = validate_away_settings(_settings_document())
    harness.states = {
        "lock.aqara_smart_lock_a100": _state("unlocked"),
        "binary_sensor.a100_away_zaniatost": _state("off"),
    }
    policy = Harness(_config())
    received = []
    async def execute(batch, **options):
        await _drain()
        markers = options["dispatch_markers"]
        if options["correlation_id"].startswith("away.return."):
            assert len(markers) == len(batch) == 1
            markers[0]()
            markers[0]()  # The same admission applies to power and final light sends.
        else:
            assert markers is None
        received.extend(batch)
    runtime = harness.runtime(lambda: settings, return_action_filter=policy.filter)
    runtime._action_runner = _wired_runner(runtime, policy.filter, SimpleNamespace(async_execute_device_action_batch=execute))
    await runtime.async_start()
    await runtime._run((AwayAction("other", "turn_on"),), "return")
    assert runtime.status["reason"] == "return_applied"
    assert received == [{"targetId": "other", "actionId": "turn_on"}]
    await runtime._run(settings.away_actions, "away")
    assert len(received) == 2
    await runtime.async_stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["refresh", "away", "unreliable", "stop", "policy", "wrapped_error"])
async def test_actual_wiring_rechecks_return_context_after_executor_wait(change):
    harness = RuntimeHarness()
    settings = validate_away_settings(_settings_document())
    harness.states = {
        "lock.aqara_smart_lock_a100": _state("unlocked"),
        "binary_sensor.a100_away_zaniatost": _state("off"),
    }
    policy = Harness(_config())
    sent = []
    async def execute(batch, **options):
        await _drain()
        if change in {"refresh", "wrapped_error"}:
            await runtime.async_refresh()
        elif change == "stop":
            await runtime.async_stop()
        elif change == "policy":
            policy.generation += 1
        else:
            harness.states["lock.aqara_smart_lock_a100"] = _state(
                "locked" if change == "away" else "unavailable"
            )
            if change == "away":
                harness.states["binary_sensor.a100_away_zaniatost"] = _state("on")
                assert runtime._compute_away_active(settings)
            runtime._evaluate(settings)
        try:
            options["dispatch_markers"][0]()
        except RuntimeError as error:
            if change == "wrapped_error":
                raise ValueError("executor converted the refused marker") from error
            raise
        sent.extend(batch)
    runtime = harness.runtime(lambda: settings, return_action_filter=policy.filter)
    runtime._action_runner = _wired_runner(runtime, policy.filter, SimpleNamespace(async_execute_device_action_batch=execute))
    await runtime.async_start()
    await runtime._run((AwayAction("other", "turn_on"),), "return")
    assert sent == []
    assert runtime.status["reason"] != "return_applied"
    if change in {"refresh", "wrapped_error"}:
        assert runtime.status["reason"] == "observed_initial"
    await runtime.async_stop()
