"""Executable regressions for shower, toilet, bathroom and office controllers."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
from types import SimpleNamespace
from typing import Mapping
from zoneinfo import ZoneInfo

import pytest

from custom_components.hausman_hub.application.scenario_control_coordinator import (
    BATHROOM_SCENARIO_ID,
    BATHROOM_FAN_TARGET_ID,
    BATHROOM_HUMIDITY_TARGET_ID,
    BATHROOM_LIGHT_TARGET_IDS,
    OFFICE_SCENARIO_ID,
    OFFICE_LIGHT_TARGET_ID,
    OFFICE_LUX_TARGET_ID,
    OFFICE_RELAY_TARGET_ID,
    SHOWER_SCENARIO_ID,
    SHOWER_CABINET_TARGET_ID,
    SHOWER_EXTRA_TARGET_ID,
    SHOWER_FAN_TARGET_ID,
    SHOWER_HUMIDITY_TARGET_ID,
    SHOWER_MAIN_TARGET_ID,
    SHOWER_PRESENCE_TARGET_ID,
    SUN_TARGET_ID,
    TOILET_SCENARIO_ID,
    TOILET_AWAY_TARGET_ID,
    TOILET_FAN_TARGET_ID,
    TOILET_MAIN_TARGET_ID,
    TOILET_MOTION_TARGET_IDS,
    TOILET_NIGHT_TARGET_ID,
    ScenarioControlCoordinator,
)
from custom_components.hausman_hub.application.scenario_control_policy import (
    ScenarioControlPolicyService,
)
from custom_components.hausman_hub.application.scenario_node_red import (
    NodeRedScenarioBackend,
    build_managed_flow,
    managed_source_hash,
)
from custom_components.hausman_hub.application.scenario_executor import ScenarioExecutor
from custom_components.hausman_hub.application.scenario_light_priority import (
    LightAutomationPriority,
)
from custom_components.hausman_hub.application.scenario_service import ScenarioService
from custom_components.hausman_hub.application.managed_switch_migration import (
    FULL_MIGRATION_MANIFEST,
)
from custom_components.hausman_hub.application.scenarios import (
    ScenarioCatalog,
    ScenarioDeviceAction,
    ScenarioDeviceEntry,
)
from custom_components.hausman_hub.domain.scenarios import (
    Scenario,
    ScenarioAction,
    ScenarioActionType,
    ScenarioDefinition,
    ScenarioExecutionBackend,
    ScenarioExecutionMode,
    ScenarioNodeRedMetadata,
    ScenarioNodeRedSyncStatus,
    ScenarioRegistry,
    ScenarioTrigger,
    ScenarioTriggerType,
)


ROOM_SOURCES = {
    SHOWER_SCENARIO_ID: "shower_controller.js",
    TOILET_SCENARIO_ID: "toilet_controller.js",
    BATHROOM_SCENARIO_ID: "bathroom_controller.js",
    OFFICE_SCENARIO_ID: "cabinet_controller.js",
}


class MemoryStore:
    def __init__(self, payload: object | None = None) -> None:
        self.payload = payload

    async def async_load(self) -> object | None:
        return self.payload

    async def async_save(self, payload: dict[str, object]) -> None:
        self.payload = payload


class MutableStates:
    def __init__(self) -> None:
        self.values: dict[str, SimpleNamespace] = {}
        self.revision = datetime(2026, 9, 7, tzinfo=timezone.utc)

    def get(self, entity_id: str) -> object | None:
        return self.values.get(entity_id)

    def set(self, entity_id: str, state: str, **attributes: object) -> None:
        self.revision += timedelta(milliseconds=1)
        old = self.values.get(entity_id)
        merged = dict(getattr(old, "attributes", {}))
        merged.update(attributes)
        self.values[entity_id] = SimpleNamespace(
            state=state,
            attributes=merged,
            last_changed=self.revision,
            last_updated=self.revision,
        )


class Priority:
    def __init__(self) -> None:
        self.owned: set[str] = set()
        self.manual: set[str] = set()

    def is_owned(self, entity_id: str, _hass: object) -> bool:
        return entity_id in self.owned

    def ownership_revision(self, entity_id: str, _hass: object) -> str | None:
        return f"owned:{entity_id}" if entity_id in self.owned else None

    def manual_claim_entity_ids(self, entity_ids: frozenset[str]) -> frozenset[str]:
        return frozenset(self.manual.intersection(entity_ids))


class ExecutingService:
    def __init__(
        self, states: MutableStates, priority: Priority, entities: Mapping[str, str]
    ) -> None:
        self.states = states
        self.priority = priority
        self.entities = entities
        self.coordinator: ScenarioControlCoordinator | None = None
        self.actions: list[tuple[str, str, str, int | None]] = []
        self.uncertain = False

    async def async_run_scenario(self, scenario_id: str, **kwargs: object) -> dict[str, object]:
        coordinator = self.coordinator
        assert coordinator is not None
        controls = await coordinator.async_control_context(
            scenario_id,
            str(kwargs["correlation_id"]),
            kwargs["trigger_context"],
        )
        action = controls["state"]["action"]
        assert isinstance(action, Mapping)
        value = action["value"]
        assert value is None or type(value) is int
        signature = (
            scenario_id,
            str(action["targetId"]),
            str(action["actionId"]),
            value,
        )
        self.actions.append(signature)
        if self.uncertain:
            return {"status": "failed", "confirmed": False, "receipts": []}
        entity_id = self.entities[signature[1]]
        if signature[2] == "turn_on":
            self.states.set(entity_id, "on")
            if signature[1] not in {
                SHOWER_FAN_TARGET_ID,
                TOILET_FAN_TARGET_ID,
                BATHROOM_FAN_TARGET_ID,
            }:
                self.priority.owned.add(entity_id)
        elif signature[2] == "turn_off":
            self.states.set(entity_id, "off")
            self.priority.owned.discard(entity_id)
        elif signature[2] == "set_brightness_percent":
            self.states.set(entity_id, "on", brightness=round(int(value) * 255 / 100))
        elif signature[2] == "set_color_temperature":
            self.states.set(entity_id, "on", color_temp_kelvin=value)
        else:
            raise AssertionError(signature)
        return {"status": "completed", "confirmed": True, "receipts": []}


async def make_room_coordinator(
    *,
    now: list[datetime],
    clock: list[int],
    overrides: Mapping[str, str] | None = None,
    state_store: MemoryStore | None = None,
) -> tuple[ScenarioControlCoordinator, ExecutingService, MutableStates, Priority, MemoryStore]:
    targets = (
        SHOWER_PRESENCE_TARGET_ID, SHOWER_HUMIDITY_TARGET_ID,
        SHOWER_MAIN_TARGET_ID, SHOWER_EXTRA_TARGET_ID, SHOWER_CABINET_TARGET_ID,
        SHOWER_FAN_TARGET_ID, *TOILET_MOTION_TARGET_IDS, TOILET_MAIN_TARGET_ID,
        TOILET_NIGHT_TARGET_ID, TOILET_FAN_TARGET_ID, TOILET_AWAY_TARGET_ID,
        *BATHROOM_LIGHT_TARGET_IDS, BATHROOM_HUMIDITY_TARGET_ID,
        BATHROOM_FAN_TARGET_ID, OFFICE_LIGHT_TARGET_ID, OFFICE_RELAY_TARGET_ID,
        OFFICE_LUX_TARGET_ID, SUN_TARGET_ID,
    )
    entities = {target: f"test.{target}" for target in targets}
    defaults = {target: "off" for target in targets}
    defaults.update({
        SHOWER_HUMIDITY_TARGET_ID: "45",
        BATHROOM_HUMIDITY_TARGET_ID: "45",
        OFFICE_LUX_TARGET_ID: "500",
        SUN_TARGET_ID: "above_horizon",
    })
    defaults.update(overrides or {})
    states = MutableStates()
    for target, value in defaults.items():
        states.set(entities[target], value)
    priority = Priority()
    service = ExecutingService(states, priority, entities)
    policy = ScenarioControlPolicyService(MemoryStore())
    await policy.async_load()
    store = state_store or MemoryStore()
    coordinator = ScenarioControlCoordinator(
        SimpleNamespace(
            states=states,
            config=SimpleNamespace(time_zone="Asia/Omsk"),
        ),
        service,
        policy,
        store,
        priority,
        catalog_resolver=lambda target: (
            SimpleNamespace(entity_id=entities[target]) if target in entities else None
        ),
        now_ms=lambda: clock[0],
        now=lambda: now[0],
        schedule_tasks=False,
    )
    await coordinator.async_load()
    service.coordinator = coordinator
    return coordinator, service, states, priority, store


def _run_source(scenario_id: str, payload: dict[str, object]) -> dict[str, object]:
    source = Path(
        "custom_components/hausman_hub/managed_scenarios", ROOM_SOURCES[scenario_id]
    ).read_text(encoding="utf-8")
    completed = subprocess.run(
        [
            "node",
            "-e",
            "const p=JSON.parse(process.argv[1]);const s=process.argv[2];"
            "const r=(new Function('msg',s))({payload:p});"
            "process.stdout.write(JSON.stringify(r.payload));",
            json.dumps(payload),
            source,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


@pytest.mark.parametrize("scenario_id", tuple(ROOM_SOURCES))
def test_room_sources_execute_only_the_exact_durable_server_action(
    scenario_id: str,
) -> None:
    action = {
        "targetId": {
            SHOWER_SCENARIO_ID: "entity_afef5df0e0cae309",
            TOILET_SCENARIO_ID: "entity_5d95de599d2b5cec",
            BATHROOM_SCENARIO_ID: "entity_c15f5df5382ee180",
            OFFICE_SCENARIO_ID: "entity_aeaf7c250c68e8c2",
        }[scenario_id],
        "actionId": "turn_on" if scenario_id != OFFICE_SCENARIO_ID else "set_brightness_percent",
        "value": None if scenario_id != OFFICE_SCENARIO_ID else 65,
    }
    result = _run_source(
        scenario_id,
        {
            "correlationId": "room-run",
            "scenarioId": scenario_id,
            "context": {
                "trigger": {"source": "scenario_control"},
                "controls": {
                    "state": {"ready": True, "action": action},
                },
            },
            "inputs": {},
        },
    )

    assert result["status"] == "completed"
    assert result["actions"] == [
        {
            "id": "server_action",
            "type": "device_action",
            "targetId": action["targetId"],
            "targetName": "Управляемое устройство",
            "actionId": action["actionId"],
            "actionTitle": "Серверное действие",
            **({"value": action["value"]} if action["value"] is not None else {}),
        }
    ]


def test_room_sources_skip_stale_or_malformed_server_actions() -> None:
    for scenario_id in ROOM_SOURCES:
        stale = _run_source(
            scenario_id,
            {
                "correlationId": "stale",
                "scenarioId": scenario_id,
                "context": {
                    "trigger": {"source": "scenario_control"},
                    "controls": {
                        "state": {
                            "ready": False,
                            "action": {
                                "targetId": "entity_not_allowed",
                                "actionId": "turn_on",
                                "value": None,
                            },
                        }
                    },
                },
                "inputs": {},
            },
        )
        assert stale["status"] == "skipped"
        assert stale["actions"] == []


def test_coordinator_owns_all_seven_completed_runtime_controllers() -> None:
    coordinator = object.__new__(ScenarioControlCoordinator)
    assert coordinator.owned_scenario_ids == frozenset(
        {
            "system-storage-light-controller",
            "system-tambur-adaptive-controller",
            "system-small-corridor-light-controller",
            SHOWER_SCENARIO_ID,
            TOILET_SCENARIO_ID,
            BATHROOM_SCENARIO_ID,
            OFFICE_SCENARIO_ID,
        }
    )


def test_coordinator_excludes_the_externally_managed_tambur_runtime() -> None:
    coordinator = object.__new__(ScenarioControlCoordinator)
    coordinator.set_externally_managed_scenarios(
        frozenset({"system-tambur-adaptive-controller"})
    )
    owned = coordinator.owned_scenario_ids
    assert "system-tambur-adaptive-controller" not in owned
    assert SHOWER_SCENARIO_ID in owned
    assert "system-small-corridor-light-controller" in owned
    assert len(owned) == 6


def test_room_runtime_and_tool_sources_are_exact_manifest_bytes() -> None:
    manifest = {item.scenario_id: item for item in FULL_MIGRATION_MANIFEST}
    for scenario_id, file_name in ROOM_SOURCES.items():
        runtime = Path(
            "custom_components/hausman_hub/managed_scenarios", file_name
        ).read_bytes()
        tooling = Path("tools/managed_scenarios", file_name).read_bytes()
        assert runtime == tooling
        assert runtime.endswith(b"\n") and not runtime.endswith(b"\n\n")
        assert managed_source_hash(runtime.decode("utf-8")) == manifest[
            scenario_id
        ].new_source_hash


class RegistryStore:
    def __init__(self, registry: ScenarioRegistry) -> None:
        self.registry = registry

    async def async_load(self) -> ScenarioRegistry:
        return self.registry

    async def async_save(self, registry: ScenarioRegistry) -> None:
        self.registry = registry


def _managed_definition(flow_id: str, source_hash: str, inputs: tuple[str, ...]) -> ScenarioDefinition:
    return ScenarioDefinition(
        version=1,
        execution_mode=ScenarioExecutionMode.RESTART,
        execution_backend=ScenarioExecutionBackend.NODE_RED,
        node_red=ScenarioNodeRedMetadata(
            flow_id=flow_id,
            source_hash=source_hash,
            input_target_ids=inputs,
            sync_status=ScenarioNodeRedSyncStatus.SYNCED,
        ),
        triggers=(ScenarioTrigger("manual", ScenarioTriggerType.MANUAL),),
        conditions=(),
        actions=(
            ScenarioAction(
                "placeholder",
                ScenarioActionType.NOTIFICATION,
                message="Сервер выбирает действие.",
            ),
        ),
    )


async def make_actual_room_runtime(
    scenario_id: str,
    *,
    now: list[datetime],
    clock: list[int],
    overrides: Mapping[str, str],
) -> tuple[ScenarioControlCoordinator, object, MutableStates, LightAutomationPriority]:
    """Build the production service, real JS backend and production executor."""

    item = next(entry for entry in FULL_MIGRATION_MANIFEST if entry.scenario_id == scenario_id)
    source = Path("custom_components/hausman_hub/managed_scenarios", item.source_file).read_text(
        encoding="utf-8"
    )
    flow_id = f"flow-{scenario_id}"
    deployed = build_managed_flow(scenario_id, scenario_id, source, flow_id=flow_id)
    targets = set(item.input_target_ids)
    output_targets = {
        SHOWER_MAIN_TARGET_ID,
        SHOWER_EXTRA_TARGET_ID,
        SHOWER_CABINET_TARGET_ID,
        SHOWER_FAN_TARGET_ID,
        TOILET_MAIN_TARGET_ID,
        TOILET_NIGHT_TARGET_ID,
        TOILET_FAN_TARGET_ID,
        BATHROOM_FAN_TARGET_ID,
        OFFICE_LIGHT_TARGET_ID,
    }
    fan_targets = {SHOWER_FAN_TARGET_ID, TOILET_FAN_TARGET_ID, BATHROOM_FAN_TARGET_ID}
    light_targets = output_targets - fan_targets
    entity_ids: dict[str, str] = {}
    for index, target in enumerate(sorted(targets)):
        if target in fan_targets:
            domain = "fan"
        elif target in light_targets or target in BATHROOM_LIGHT_TARGET_IDS:
            domain = "light"
        elif target in {SHOWER_HUMIDITY_TARGET_ID, BATHROOM_HUMIDITY_TARGET_ID, OFFICE_LUX_TARGET_ID}:
            domain = "sensor"
        elif target == SUN_TARGET_ID:
            domain = "sun"
        else:
            domain = "binary_sensor"
        entity_ids[target] = f"{domain}.room_{index}"

    states = MutableStates()
    for target in targets:
        states.set(entity_ids[target], overrides.get(target, "off"))
    if OFFICE_LIGHT_TARGET_ID in targets:
        state = states.get(entity_ids[OFFICE_LIGHT_TARGET_ID])
        assert state is not None
        state.attributes.update(
            {"brightness": 128, "color_temp_kelvin": 3000,
             "min_color_temp_kelvin": 1500, "max_color_temp_kelvin": 6500}
        )

    class Services:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, dict[str, object]]] = []

        async def async_call(
            self, domain: str, service_name: str, data: dict[str, object], **_kwargs: object
        ) -> None:
            self.calls.append((domain, service_name, dict(data)))
            entity_id = str(data["entity_id"])
            current = states.get(entity_id)
            attributes = dict(getattr(current, "attributes", {}))
            if service_name == "turn_off":
                states.set(entity_id, "off", **attributes)
                return
            if service_name != "turn_on":
                raise AssertionError((domain, service_name, data))
            if "brightness" in data:
                attributes["brightness"] = data["brightness"]
            if "color_temp_kelvin" in data:
                attributes["color_temp_kelvin"] = data["color_temp_kelvin"]
            states.set(entity_id, "on", **attributes)

    services = Services()
    hass = SimpleNamespace(
        states=states,
        services=services,
        config=SimpleNamespace(time_zone="Asia/Omsk"),
    )

    def actions_for(target: str) -> tuple[ScenarioDeviceAction, ...]:
        if target not in output_targets:
            return ()
        domain = "fan" if target in fan_targets else "light"
        actions = [
            ScenarioDeviceAction("turn_on", "Включить", domain, "turn_on", frozenset()),
            ScenarioDeviceAction("turn_off", "Выключить", domain, "turn_off", frozenset()),
        ]
        if target == OFFICE_LIGHT_TARGET_ID:
            actions.extend(
                (
                    ScenarioDeviceAction(
                        "set_brightness_percent", "Яркость", "light", "turn_on", frozenset({"value"})
                    ),
                    ScenarioDeviceAction(
                        "set_color_temperature", "Температура", "light", "turn_on", frozenset({"value"})
                    ),
                )
            )
        return tuple(actions)

    devices = {
        target: ScenarioDeviceEntry(
            target_id=target,
            name=("Вытяжка" if target in fan_targets else "Свет" if target in light_targets else "Датчик"),
            entity_id=entity_ids[target],
            actions=actions_for(target),
        )
        for target in targets
    }
    catalog = ScenarioCatalog(devices=devices, scenarios={})
    definition = _managed_definition(flow_id, item.new_source_hash, item.input_target_ids)
    scenario = Scenario.from_definition(scenario_id, scenario_id, definition, group="system")

    async def adapter(
        method: str,
        path: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object] | None,
    ) -> tuple[int, object]:
        del headers
        if method == "GET" and path.endswith("/flows"):
            return 200, {"rev": "room-revision", "flows": list(deployed["nodes"])}
        if method == "GET" and path.endswith(f"/flow/{flow_id}"):
            return 200, deployed
        if method == "POST":
            assert payload is not None
            return 200, _run_source(scenario_id, dict(payload))
        raise AssertionError((method, path))

    backend = NodeRedScenarioBackend(hass, request_adapter=adapter)
    backend._ingress_token = "token"  # noqa: SLF001
    backend._ingress_session = "session"  # noqa: SLF001
    service = ScenarioService(
        hass,
        RegistryStore(ScenarioRegistry(scenarios=(scenario,))),
        catalog,
        node_red_backend=backend,
    )
    await service.async_load()
    priority = LightAutomationPriority(MemoryStore())
    await priority.async_load()
    policy = ScenarioControlPolicyService(MemoryStore())
    await policy.async_load()
    coordinator = ScenarioControlCoordinator(
        hass,
        service,
        policy,
        MemoryStore(),
        priority,
        catalog_resolver=catalog.device,
        now_ms=lambda: clock[0],
        now=lambda: now[0],
        schedule_tasks=False,
    )
    await coordinator.async_load()
    backend.set_control_context_provider(coordinator.async_control_context)
    executor = ScenarioExecutor(
        hass,
        catalog,
        service.async_run_scenario,
        node_red_backend=backend,
        light_priority=priority,
    )
    executor.set_scenario_generation_validator(coordinator.async_validate_generation)
    service.set_executor(executor)
    return coordinator, services, states, priority


@pytest.mark.asyncio
async def test_shower_full_service_js_executor_completes_each_independent_action() -> None:
    now = [datetime(2026, 9, 7, 12, tzinfo=ZoneInfo("Asia/Omsk"))]
    clock = [0]
    coordinator, services, states, priority = await make_actual_room_runtime(
        SHOWER_SCENARIO_ID,
        now=now,
        clock=clock,
        overrides={
            SHOWER_PRESENCE_TARGET_ID: "on",
            SHOWER_HUMIDITY_TARGET_ID: "60",
            SUN_TARGET_ID: "above_horizon",
            SHOWER_MAIN_TARGET_ID: "off",
            SHOWER_EXTRA_TARGET_ID: "off",
            SHOWER_CABINET_TARGET_ID: "off",
            SHOWER_FAN_TARGET_ID: "off",
        },
    )

    await coordinator.async_handle_shower_change()

    assert [(domain, service) for domain, service, _data in services.calls] == [
        ("fan", "turn_on"),
        ("light", "turn_on"),
        ("light", "turn_on"),
    ]
    assert all(call[2]["entity_id"] for call in services.calls)
    assert priority.manual_claim_entity_ids(
        frozenset(str(call[2]["entity_id"]) for call in services.calls[1:])
    ) == frozenset()
    assert coordinator.payload["shower"]["transition"] == "light_action"
    assert states.get(str(services.calls[0][2]["entity_id"])).state == "on"
    await coordinator.async_handle_shower_change()
    assert len(services.calls) == 3


@pytest.mark.asyncio
async def test_shower_profile_fan_timers_and_owned_absence_are_durable() -> None:
    now = [datetime(2026, 9, 7, 12, tzinfo=ZoneInfo("Asia/Omsk"))]
    clock = [0]
    coordinator, service, states, _priority, _store = await make_room_coordinator(
        now=now,
        clock=clock,
        overrides={SHOWER_PRESENCE_TARGET_ID: "on"},
    )

    await coordinator.async_handle_shower_change()
    assert service.actions == [
        (SHOWER_SCENARIO_ID, SHOWER_MAIN_TARGET_ID, "turn_on", None),
        (SHOWER_SCENARIO_ID, SHOWER_CABINET_TARGET_ID, "turn_on", None),
    ]
    assert coordinator.payload["shower"]["deadlineMs"] == 120_000

    clock[0] = 119_999
    await coordinator.async_reconcile_zone_due(SHOWER_SCENARIO_ID)
    assert len(service.actions) == 2
    clock[0] = 120_000
    await coordinator.async_reconcile_zone_due(SHOWER_SCENARIO_ID)
    assert service.actions[-1] == (
        SHOWER_SCENARIO_ID, SHOWER_FAN_TARGET_ID, "turn_on", None
    )

    states.set(f"test.{SHOWER_PRESENCE_TARGET_ID}", "off")
    await coordinator.async_handle_shower_change()
    assert coordinator.payload["shower"]["deadlineMs"] == 420_000
    clock[0] = 419_999
    await coordinator.async_reconcile_zone_due(SHOWER_SCENARIO_ID)
    assert len(service.actions) == 3
    clock[0] = 420_000
    await coordinator.async_reconcile_zone_due(SHOWER_SCENARIO_ID)
    assert service.actions[-3:] == [
        (SHOWER_SCENARIO_ID, SHOWER_MAIN_TARGET_ID, "turn_off", None),
        (SHOWER_SCENARIO_ID, SHOWER_CABINET_TARGET_ID, "turn_off", None),
        (SHOWER_SCENARIO_ID, SHOWER_FAN_TARGET_ID, "turn_off", None),
    ]


@pytest.mark.asyncio
async def test_shower_presence_fan_timer_resumes_after_restart() -> None:
    now = [datetime(2026, 9, 7, 12, tzinfo=ZoneInfo("Asia/Omsk"))]
    clock = [0]
    store = MemoryStore()
    coordinator, _service, _states, _priority, _ = await make_room_coordinator(
        now=now,
        clock=clock,
        overrides={SHOWER_PRESENCE_TARGET_ID: "on"},
        state_store=store,
    )
    await coordinator.async_handle_shower_change()
    assert coordinator.payload["shower"]["deadlineMs"] == 120_000

    restarted, service, _states, _priority, _ = await make_room_coordinator(
        now=now,
        clock=clock,
        overrides={
            SHOWER_PRESENCE_TARGET_ID: "on",
            SHOWER_MAIN_TARGET_ID: "on",
            SHOWER_CABINET_TARGET_ID: "on",
        },
        state_store=store,
    )
    await restarted.async_handle_shower_change(recovery=True, allow_activation=False)
    assert restarted.payload["shower"]["deadlineMs"] == 120_000
    clock[0] = 120_000
    await restarted.async_reconcile_zone_due(SHOWER_SCENARIO_ID)
    assert service.actions == [
        (SHOWER_SCENARIO_ID, SHOWER_FAN_TARGET_ID, "turn_on", None)
    ]


@pytest.mark.asyncio
async def test_shower_manual_or_unknown_presence_keeps_light_profile_but_not_safe_fan_on() -> None:
    now = [datetime(2026, 9, 7, 12, tzinfo=ZoneInfo("Asia/Omsk"))]
    for presence, expected_transition in (
        ("on", "shower_profile"),
        ("unavailable", "controller_unknown"),
    ):
        coordinator, service, _states, _priority, _store = await make_room_coordinator(
            now=now,
            clock=[0],
            overrides={
                SHOWER_PRESENCE_TARGET_ID: presence,
                SHOWER_HUMIDITY_TARGET_ID: "60",
                SHOWER_EXTRA_TARGET_ID: "on",
            },
        )
        await coordinator.async_handle_shower_change()
        assert service.actions == [
            (SHOWER_SCENARIO_ID, SHOWER_FAN_TARGET_ID, "turn_on", None)
        ]
        assert coordinator.payload["shower"]["transition"] == expected_transition


@pytest.mark.asyncio
async def test_shower_unknown_humidity_never_authorizes_owned_fan_off() -> None:
    now = [datetime(2026, 9, 7, 12, tzinfo=ZoneInfo("Asia/Omsk"))]
    clock = [0]
    coordinator, service, states, _priority, _store = await make_room_coordinator(
        now=now,
        clock=clock,
        overrides={
            SHOWER_PRESENCE_TARGET_ID: "off",
            SHOWER_HUMIDITY_TARGET_ID: "60",
        },
    )
    states.set(f"test.{SHOWER_PRESENCE_TARGET_ID}", "on")
    await coordinator.async_handle_shower_change()
    assert service.actions[0] == (
        SHOWER_SCENARIO_ID, SHOWER_FAN_TARGET_ID, "turn_on", None
    )
    states.set(f"test.{SHOWER_PRESENCE_TARGET_ID}", "off")
    states.set(f"test.{SHOWER_HUMIDITY_TARGET_ID}", "unavailable")
    await coordinator.async_handle_shower_change()
    clock[0] = 300_000
    await coordinator.async_reconcile_zone_due(SHOWER_SCENARIO_ID)
    assert not any(action[2] == "turn_off" and action[1] == SHOWER_FAN_TARGET_ID for action in service.actions)


@pytest.mark.asyncio
async def test_failed_shower_action_is_not_retried_after_evidence_change_or_restart() -> None:
    now = [datetime(2026, 9, 7, 12, tzinfo=ZoneInfo("Asia/Omsk"))]
    clock = [0]
    store = MemoryStore()
    coordinator, service, states, _priority, _ = await make_room_coordinator(
        now=now,
        clock=clock,
        overrides={SHOWER_PRESENCE_TARGET_ID: "on", SHOWER_HUMIDITY_TARGET_ID: "60"},
        state_store=store,
    )
    service.uncertain = True
    await coordinator.async_handle_shower_change()
    await coordinator.async_handle_shower_change()
    states.set(f"test.{SHOWER_HUMIDITY_TARGET_ID}", "61")
    await coordinator.async_handle_shower_change()
    assert len(service.actions) == 1
    assert coordinator.payload["shower"]["transition"] == "light_action_failed"

    restarted, restarted_service, _states, _priority, _ = await make_room_coordinator(
        now=now,
        clock=clock,
        overrides={SHOWER_PRESENCE_TARGET_ID: "on", SHOWER_HUMIDITY_TARGET_ID: "60"},
        state_store=store,
    )
    await restarted.async_handle_shower_change(recovery=True)
    await restarted.async_reconcile_zone_due(SHOWER_SCENARIO_ID)
    assert restarted_service.actions == []


@pytest.mark.asyncio
async def test_toilet_positive_motion_wins_or_and_runs_light_then_fan() -> None:
    now = [datetime(2026, 9, 7, 12, tzinfo=ZoneInfo("Asia/Omsk"))]
    overrides = {
        TOILET_MOTION_TARGET_IDS[0]: "unavailable",
        TOILET_MOTION_TARGET_IDS[1]: "on",
        SUN_TARGET_ID: "above_horizon",
    }
    coordinator, service, _states, _priority, _store = await make_room_coordinator(
        now=now, clock=[0], overrides=overrides
    )
    await coordinator.async_handle_toilet_change()
    assert service.actions == [
        (TOILET_SCENARIO_ID, TOILET_MAIN_TARGET_ID, "turn_on", None),
        (TOILET_SCENARIO_ID, TOILET_FAN_TARGET_ID, "turn_on", None),
    ]


@pytest.mark.asyncio
async def test_toilet_unavailable_plus_off_blocks_absence_but_preserves_independent_fan() -> None:
    now = [datetime(2026, 9, 7, 12, tzinfo=ZoneInfo("Asia/Omsk"))]
    coordinator, service, _states, _priority, _store = await make_room_coordinator(
        now=now,
        clock=[0],
        overrides={
            TOILET_MOTION_TARGET_IDS[0]: "unavailable",
            TOILET_MOTION_TARGET_IDS[1]: "off",
            TOILET_MAIN_TARGET_ID: "on",
        },
    )
    await coordinator.async_handle_toilet_change()
    assert service.actions == [
        (TOILET_SCENARIO_ID, TOILET_FAN_TARGET_ID, "turn_on", None)
    ]
    assert coordinator.payload["toilet"]["transition"] == "controller_unknown"
    assert coordinator.payload["toilet"]["deadlineMs"] is None


@pytest.mark.asyncio
async def test_toilet_mutual_profiles_owned_480_off_and_fan_180_off() -> None:
    now = [datetime(2026, 9, 7, 2, tzinfo=ZoneInfo("Asia/Omsk"))]
    clock = [0]
    coordinator, service, states, _priority, _store = await make_room_coordinator(
        now=now,
        clock=clock,
        overrides={
            TOILET_MOTION_TARGET_IDS[0]: "off",
            TOILET_MOTION_TARGET_IDS[1]: "on",
            SUN_TARGET_ID: "below_horizon",
        },
    )
    await coordinator.async_handle_toilet_change()
    assert service.actions == [
        (TOILET_SCENARIO_ID, TOILET_NIGHT_TARGET_ID, "turn_on", None)
    ]
    states.set(f"test.{TOILET_MOTION_TARGET_IDS[1]}", "off")
    await coordinator.async_handle_toilet_change()
    assert coordinator.payload["toilet"]["deadlineMs"] == 480_000
    clock[0] = 480_000
    await coordinator.async_reconcile_zone_due(TOILET_SCENARIO_ID)
    assert service.actions[-1] == (
        TOILET_SCENARIO_ID, TOILET_NIGHT_TARGET_ID, "turn_off", None
    )

    now[0] = now[0].replace(hour=12)
    states.set(f"test.{TOILET_MAIN_TARGET_ID}", "on")
    await coordinator.async_handle_toilet_change()
    assert service.actions[-1] == (
        TOILET_SCENARIO_ID, TOILET_FAN_TARGET_ID, "turn_on", None
    )
    states.set(f"test.{TOILET_MAIN_TARGET_ID}", "off")
    await coordinator.async_handle_toilet_change()
    assert coordinator.payload["toilet"]["deadlineMs"] == 660_000
    clock[0] = 660_000
    await coordinator.async_reconcile_zone_due(TOILET_SCENARIO_ID)
    assert service.actions[-1] == (
        TOILET_SCENARIO_ID, TOILET_FAN_TARGET_ID, "turn_off", None
    )


@pytest.mark.asyncio
async def test_toilet_manual_light_is_never_switched_to_another_profile() -> None:
    coordinator, service, _states, _priority, _store = await make_room_coordinator(
        now=[datetime(2026, 9, 7, 2, tzinfo=ZoneInfo("Asia/Omsk"))],
        clock=[0],
        overrides={
            TOILET_MOTION_TARGET_IDS[1]: "on",
            TOILET_MAIN_TARGET_ID: "on",
            SUN_TARGET_ID: "below_horizon",
        },
    )
    await coordinator.async_handle_toilet_change()
    assert service.actions == []


@pytest.mark.asyncio
async def test_bathroom_bands_only_operate_fan_and_unknown_humidity_blocks_off() -> None:
    cases = (
        (7, "on", "off", "45"),
        (12, "on", "off", "65"),
        (23, "off", "on", "45"),
    )
    for hour, light1, light2, humidity in cases:
        now = [datetime(2026, 9, 7, hour, tzinfo=ZoneInfo("Asia/Omsk"))]
        clock = [0]
        coordinator, service, states, _priority, _store = await make_room_coordinator(
            now=now,
            clock=clock,
            overrides={
                BATHROOM_LIGHT_TARGET_IDS[0]: light1,
                BATHROOM_LIGHT_TARGET_IDS[1]: light2,
                BATHROOM_HUMIDITY_TARGET_ID: humidity,
            },
        )
        await coordinator.async_handle_bathroom_change()
        assert service.actions == [
            (BATHROOM_SCENARIO_ID, BATHROOM_FAN_TARGET_ID, "turn_on", None)
        ]
        states.set(f"test.{BATHROOM_LIGHT_TARGET_IDS[0]}", "off")
        states.set(f"test.{BATHROOM_LIGHT_TARGET_IDS[1]}", "off")
        states.set(f"test.{BATHROOM_HUMIDITY_TARGET_ID}", "unavailable")
        await coordinator.async_handle_bathroom_change()
        assert not any(action[2] == "turn_off" for action in service.actions)
        assert all(action[1] == BATHROOM_FAN_TARGET_ID for action in service.actions)


@pytest.mark.asyncio
async def test_bathroom_day_off_is_restart_safe_and_requires_owned_fan() -> None:
    now = [datetime(2026, 9, 7, 12, tzinfo=ZoneInfo("Asia/Omsk"))]
    clock = [0]
    store = MemoryStore()
    coordinator, service, states, _priority, _ = await make_room_coordinator(
        now=now,
        clock=clock,
        overrides={
            BATHROOM_LIGHT_TARGET_IDS[0]: "on",
            BATHROOM_HUMIDITY_TARGET_ID: "65",
        },
        state_store=store,
    )
    await coordinator.async_handle_bathroom_change()
    states.set(f"test.{BATHROOM_LIGHT_TARGET_IDS[0]}", "off")
    states.set(f"test.{BATHROOM_HUMIDITY_TARGET_ID}", "45")
    await coordinator.async_handle_bathroom_change()
    assert coordinator.payload["bathroom"]["deadlineMs"] == 1_800_000

    restarted, restarted_service, restarted_states, _priority, _ = await make_room_coordinator(
        now=now,
        clock=clock,
        overrides={
            BATHROOM_FAN_TARGET_ID: "on",
            BATHROOM_HUMIDITY_TARGET_ID: "45",
        },
        state_store=store,
    )
    # Restore the exact confirmed revision used as the durable non-light ownership token.
    token = restarted.payload["bathroom"]["ownedTargets"][BATHROOM_FAN_TARGET_ID]
    restarted_states.values[f"test.{BATHROOM_FAN_TARGET_ID}"].last_changed = datetime.fromisoformat(token)
    clock[0] = 1_800_000
    await restarted.async_reconcile_zone_due(BATHROOM_SCENARIO_ID)
    assert restarted_service.actions == [
        (BATHROOM_SCENARIO_ID, BATHROOM_FAN_TARGET_ID, "turn_off", None)
    ]


@pytest.mark.asyncio
async def test_failed_bathroom_immediate_off_is_not_retried_after_evidence_change_or_restart() -> None:
    now = [datetime(2026, 9, 7, 23, tzinfo=ZoneInfo("Asia/Omsk"))]
    clock = [0]
    store = MemoryStore()
    coordinator, service, states, _priority, _ = await make_room_coordinator(
        now=now,
        clock=clock,
        overrides={BATHROOM_LIGHT_TARGET_IDS[0]: "on"},
        state_store=store,
    )
    await coordinator.async_handle_bathroom_change()
    states.set(f"test.{BATHROOM_LIGHT_TARGET_IDS[0]}", "off")
    service.uncertain = True
    await coordinator.async_handle_bathroom_change()
    states.set(f"test.{BATHROOM_HUMIDITY_TARGET_ID}", "46")
    await coordinator.async_handle_bathroom_change()
    assert service.actions == [
        (BATHROOM_SCENARIO_ID, BATHROOM_FAN_TARGET_ID, "turn_on", None),
        (BATHROOM_SCENARIO_ID, BATHROOM_FAN_TARGET_ID, "turn_off", None),
    ]
    assert coordinator.payload["bathroom"]["transition"] == "light_action_failed"

    restarted, restarted_service, _states, _priority, _ = await make_room_coordinator(
        now=now,
        clock=clock,
        overrides={BATHROOM_FAN_TARGET_ID: "on"},
        state_store=store,
    )
    await restarted.async_handle_bathroom_change(recovery=True)
    await restarted.async_reconcile_zone_due(BATHROOM_SCENARIO_ID)
    assert restarted_service.actions == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("hour", "sun", "lux", "profile"),
    (
        (12, "above_horizon", "50", ("day_low", 40, 3000)),
        (12, "above_horizon", "500", ("day_medium", 65, 2500)),
        (12, "above_horizon", "1000", ("day_bright", 85, 2200)),
        (20, "below_horizon", "50", ("evening_dark", 25, 6500)),
        (20, "below_horizon", "500", ("evening_medium", 40, 5700)),
        (20, "below_horizon", "1000", ("evening_bright", 55, 4800)),
        (2, "below_horizon", "500", ("night", 5, 6500)),
    ),
)
async def test_office_exact_profile_sequence(hour: int, sun: str, lux: str, profile: tuple[str, int, int]) -> None:
    now = [datetime(2026, 9, 7, hour, tzinfo=ZoneInfo("Asia/Omsk"))]
    clock = [0]
    coordinator, service, _states, _priority, _store = await make_room_coordinator(
        now=now,
        clock=clock,
        overrides={
            OFFICE_LIGHT_TARGET_ID: "on",
            OFFICE_RELAY_TARGET_ID: "on",
            OFFICE_LUX_TARGET_ID: lux,
            SUN_TARGET_ID: sun,
        },
    )
    await coordinator.async_handle_office_change()
    assert coordinator.payload["office"]["program"]["profile"] == profile[0]
    clock[0] = 3_000
    await coordinator.async_reconcile_zone_due(OFFICE_SCENARIO_ID)
    clock[0] = 4_000
    await coordinator.async_reconcile_zone_due(OFFICE_SCENARIO_ID)
    clock[0] = 5_000
    await coordinator.async_reconcile_zone_due(OFFICE_SCENARIO_ID)
    prime = profile[2] - 100 if profile[2] >= 4000 else profile[2] + 100
    assert service.actions == [
        (OFFICE_SCENARIO_ID, OFFICE_LIGHT_TARGET_ID, "set_brightness_percent", profile[1]),
        (OFFICE_SCENARIO_ID, OFFICE_LIGHT_TARGET_ID, "set_color_temperature", prime),
        (OFFICE_SCENARIO_ID, OFFICE_LIGHT_TARGET_ID, "set_color_temperature", profile[2]),
        (OFFICE_SCENARIO_ID, OFFICE_LIGHT_TARGET_ID, "set_color_temperature", profile[2]),
    ]


@pytest.mark.asyncio
async def test_office_never_activates_light_and_failed_step_is_not_retried() -> None:
    now = [datetime(2026, 9, 7, 12, tzinfo=ZoneInfo("Asia/Omsk"))]
    clock = [0]
    coordinator, service, states, _priority, _store = await make_room_coordinator(
        now=now,
        clock=clock,
        overrides={OFFICE_LIGHT_TARGET_ID: "off", OFFICE_RELAY_TARGET_ID: "on"},
    )
    await coordinator.async_handle_office_change()
    assert service.actions == []
    states.set(f"test.{OFFICE_LIGHT_TARGET_ID}", "on")
    await coordinator.async_handle_office_change()
    service.uncertain = True
    clock[0] = 3_000
    await coordinator.async_reconcile_zone_due(OFFICE_SCENARIO_ID)
    await coordinator.async_reconcile_zone_due(OFFICE_SCENARIO_ID)
    assert service.actions == [
        (OFFICE_SCENARIO_ID, OFFICE_LIGHT_TARGET_ID, "set_brightness_percent", 65)
    ]
    assert coordinator.payload["office"]["transition"] == "light_action_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario_id", "overrides", "method", "expected_calls"),
    (
        (
            TOILET_SCENARIO_ID,
            {
                TOILET_MOTION_TARGET_IDS[0]: "unavailable",
                TOILET_MOTION_TARGET_IDS[1]: "on",
                SUN_TARGET_ID: "above_horizon",
            },
            "async_handle_toilet_change",
            (("light", "turn_on"), ("fan", "turn_on")),
        ),
        (
            BATHROOM_SCENARIO_ID,
            {
                BATHROOM_LIGHT_TARGET_IDS[0]: "on",
                BATHROOM_HUMIDITY_TARGET_ID: "65",
            },
            "async_handle_bathroom_change",
            (("fan", "turn_on"),),
        ),
    ),
)
async def test_other_rooms_cross_real_service_js_and_executor(
    scenario_id: str,
    overrides: Mapping[str, str],
    method: str,
    expected_calls: tuple[tuple[str, str], ...],
) -> None:
    coordinator, services, _states, _priority = await make_actual_room_runtime(
        scenario_id,
        now=[datetime(2026, 9, 7, 12, tzinfo=ZoneInfo("Asia/Omsk"))],
        clock=[0],
        overrides=overrides,
    )
    await getattr(coordinator, method)()
    assert tuple((domain, service) for domain, service, _data in services.calls) == expected_calls


@pytest.mark.asyncio
async def test_office_crosses_real_service_js_executor_for_each_program_step() -> None:
    clock = [0]
    coordinator, services, _states, _priority = await make_actual_room_runtime(
        OFFICE_SCENARIO_ID,
        now=[datetime(2026, 9, 7, 12, tzinfo=ZoneInfo("Asia/Omsk"))],
        clock=clock,
        overrides={
            OFFICE_LIGHT_TARGET_ID: "on",
            OFFICE_RELAY_TARGET_ID: "on",
            OFFICE_LUX_TARGET_ID: "500",
            SUN_TARGET_ID: "above_horizon",
        },
    )
    await coordinator.async_handle_office_change()
    for due in (3_000, 4_000, 5_000):
        clock[0] = due
        await coordinator.async_reconcile_zone_due(OFFICE_SCENARIO_ID)
    assert [
        (domain, service, data.get("brightness"), data.get("color_temp_kelvin"))
        for domain, service, data in services.calls
    ] == [
        ("light", "turn_on", 166, None),
        ("light", "turn_on", None, 2600),
        ("light", "turn_on", None, 2500),
        ("light", "turn_on", None, 2500),
    ]
