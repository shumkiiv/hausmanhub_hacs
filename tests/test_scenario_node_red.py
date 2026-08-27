"""Safety and synchronization tests for managed Node-RED scenario flows."""

from __future__ import annotations

import asyncio
import inspect
import unittest
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, Callable, Coroutine
from unittest.mock import AsyncMock

from custom_components.hausman_hub.application.scenario_node_red import (
    NodeRedBackendError,
    NodeRedScenarioBackend,
    NodeRedSourceConflict,
    NodeRedSourceInvalid,
    build_managed_flow,
    compile_managed_function,
    managed_source_hash,
    validate_managed_source,
)
from custom_components.hausman_hub.application.scenario_executor import ScenarioExecutor
from custom_components.hausman_hub.domain.scenarios import (
    ScenarioAction,
    ScenarioActionType,
    ScenarioDefinition,
    ScenarioExecutionBackend,
    ScenarioExecutionMode,
    ScenarioNodeRedMetadata,
    ScenarioNodeRedSyncStatus,
    ScenarioTrigger,
    ScenarioTriggerType,
)


def _definition(**changes: object) -> ScenarioDefinition:
    values: dict[str, object] = {
        "version": 1,
        "execution_mode": ScenarioExecutionMode.RESTART,
        "execution_backend": ScenarioExecutionBackend.NODE_RED,
        "node_red": ScenarioNodeRedMetadata(input_target_ids=("sensor_one",)),
        "triggers": (ScenarioTrigger("manual", ScenarioTriggerType.MANUAL),),
        "conditions": (),
        "actions": (
            ScenarioAction(
                "notify",
                ScenarioActionType.NOTIFICATION,
                message="Готово",
            ),
        ),
    }
    values.update(changes)
    return ScenarioDefinition(**values)  # type: ignore[arg-type]


def test_compiler_builds_one_compact_command_free_function_flow() -> None:
    source = compile_managed_function("test_flow", _definition())
    flow = build_managed_flow("test_flow", "Тест", source)

    assert [node["type"] for node in flow["nodes"]] == [
        "http in",
        "function",
        "http response",
    ]
    assert "call-service" not in source
    assert managed_source_hash(source) == managed_source_hash(source)


def test_embedded_source_policy_rejects_direct_commands_and_context() -> None:
    source = compile_managed_function("test_flow", _definition())
    diagnostics = validate_managed_source("test_flow", source)
    assert diagnostics[0]["id"] == "contract_present"

    for forbidden, code in (
        (source.replace("return msg;", "node.send(msg);\nreturn msg;"), "direct_node_output"),
        (source.replace("return msg;", "global.set('secret', 1);\nreturn msg;"), "global_context"),
        (source.replace("return msg;", "fetch('https://example.com');\nreturn msg;"), "network_access"),
    ):
        try:
            validate_managed_source("test_flow", forbidden)
        except NodeRedSourceInvalid as error:
            assert error.code == code
        else:
            raise AssertionError(f"{code} must fail closed")


async def test_embedded_source_update_uses_hash_lock_and_dry_run() -> None:
    original = compile_managed_function("test_flow", _definition())
    proposed = original.replace("Основная ветка", "Проверенная ветка")
    deployed = build_managed_flow(
        "test_flow", "Тест", original, flow_id="flow-one"
    )
    calls: list[tuple[str, str]] = []

    async def adapter(method, path, headers, payload):
        calls.append((method, path))
        if method == "GET" and path.endswith("/flow/flow-one"):
            return 200, deployed
        if method == "PUT" and path.endswith("/flow/flow-one"):
            deployed.clear()
            deployed.update(payload)
            return 204, None
        if method == "POST" and path.endswith("/endpoint/hausman/scenarios/test_flow"):
            return 200, {
                "contract": {"name": "hausman-node-red-scenario-execution", "version": 1},
                "correlationId": payload["correlationId"],
                "scenarioId": "test_flow",
                "status": "completed",
                "summary": "Проверено.",
                "selectedBranch": "default",
                "durationMs": 1,
                "trace": [],
                "actions": [],
            }
        raise AssertionError((method, path))

    backend = NodeRedScenarioBackend(
        SimpleNamespace(states=SimpleNamespace(get=lambda _: None)),
        request_adapter=adapter,
    )
    backend._ingress_token = "token"  # noqa: SLF001
    backend._ingress_session = "session"  # noqa: SLF001
    definition = replace(
        _definition(),
        node_red=ScenarioNodeRedMetadata(
            flow_id="flow-one",
            source_hash=managed_source_hash(original),
            sync_status=ScenarioNodeRedSyncStatus.SYNCED,
        ),
    )
    result = await backend.async_update_source(
        "test_flow",
        definition,
        "flow-one",
        proposed,
        managed_source_hash(original),
        SimpleNamespace(device=lambda _: None),
        validate_only=False,
    )

    assert result["saved"] is True
    assert result["current_source_hash"] == managed_source_hash(proposed)
    assert result["verification"]["actions"] == []
    assert ("PUT", "/ingress/token/flow/flow-one") in calls

    try:
        await backend.async_update_source(
            "test_flow",
            definition,
            "flow-one",
            proposed,
            managed_source_hash(original),
            SimpleNamespace(device=lambda _: None),
            validate_only=True,
        )
    except NodeRedSourceConflict as error:
        assert error.current_hash == managed_source_hash(proposed)
    else:
        raise AssertionError("stale source hash must fail closed")


def _async_test_case(
    case: Callable[[], Coroutine[Any, Any, None]],
) -> unittest.FunctionTestCase:
    def run() -> None:
        asyncio.run(case())

    run.__name__ = case.__name__
    return unittest.FunctionTestCase(run)


async def test_prepare_creates_a_managed_flow_and_records_server_id() -> None:
    calls: list[tuple[str, str]] = []

    async def adapter(method, path, headers, payload):
        calls.append((method, path))
        if path.endswith("/info"):
            return 200, {
                "data": {
                    "state": "started",
                    "version": "22.0.2",
                    "ingress_entry": "/api/hassio_ingress/token-one",
                }
            }
        if path == "/ingress/session":
            return 200, {"data": {"session": "session-one"}}
        if path.endswith("/flow") and method == "POST":
            assert headers["Node-RED-API-Version"] == "v2"
            assert [node["type"] for node in payload["nodes"]] == [
                "http in",
                "function",
                "http response",
            ]
            return 200, {"id": "server-flow-id"}
        raise AssertionError((method, path))

    backend = NodeRedScenarioBackend(SimpleNamespace(), request_adapter=adapter)
    prepared = await backend.async_prepare("test_flow", "Тест", _definition())

    assert prepared.node_red.flow_id == "server-flow-id"
    assert prepared.node_red.sync_status is ScenarioNodeRedSyncStatus.SYNCED
    assert calls[-1] == ("POST", "/ingress/token-one/flow")


async def test_prepare_never_overwrites_a_manually_changed_function() -> None:
    original = compile_managed_function("test_flow", _definition())
    previous = ScenarioNodeRedMetadata(
        flow_id="flow-one",
        flow_revision=2,
        source_hash=managed_source_hash(original),
        sync_status=ScenarioNodeRedSyncStatus.SYNCED,
    )
    current = build_managed_flow(
        "test_flow",
        "Тест",
        original + "\n// ручная ветка",
        flow_id="flow-one",
    )

    async def adapter(method, path, headers, payload):
        assert method == "GET"
        assert path.endswith("/flow/flow-one")
        return 200, current

    backend = NodeRedScenarioBackend(SimpleNamespace(), request_adapter=adapter)
    backend._ingress_token = "token"  # noqa: SLF001
    backend._ingress_session = "session"  # noqa: SLF001
    definition = replace(_definition(), node_red=previous)

    prepared = await backend.async_prepare(
        "test_flow", "Тест", definition, previous=previous
    )

    assert prepared.node_red.sync_status is ScenarioNodeRedSyncStatus.CHANGED
    assert prepared.node_red.source_hash == previous.source_hash


async def test_plan_accepts_nested_scenario_but_rejects_existing_actions() -> None:
    responses = [
        {
            "contract": {"name": "hausman-node-red-scenario-execution", "version": 1},
            "correlationId": "run-one",
            "scenarioId": "test_flow",
            "status": "completed",
            "summary": "Ветка выбрана.",
            "selectedBranch": "off_delay",
            "durationMs": 2,
            "trace": [
                {
                    "id": "presence",
                    "title": "Присутствия нет",
                    "status": "passed",
                    "actual": False,
                    "expected": False,
                    "reason": None,
                }
            ],
            "actions": [
                {
                    "id": "confirm",
                    "type": "run_scenario",
                    "scenarioId": "confirm_off",
                }
            ],
        },
        {
            "contract": {"name": "hausman-node-red-scenario-execution", "version": 1},
            "correlationId": "run-one",
            "scenarioId": "test_flow",
            "status": "completed",
            "summary": "Недопустимое действие.",
            "durationMs": 1,
            "trace": [],
            "actions": [
                {"id": "legacy", "type": "existing_action", "targetId": "x"}
            ],
        },
    ]

    async def adapter(method, path, headers, payload):
        return 200, responses.pop(0)

    metadata = ScenarioNodeRedMetadata(
        flow_id="flow-one", sync_status=ScenarioNodeRedSyncStatus.SYNCED
    )
    definition = replace(_definition(), node_red=metadata)
    backend = NodeRedScenarioBackend(SimpleNamespace(states=SimpleNamespace(get=lambda _: None)), request_adapter=adapter)
    backend._ingress_token = "token"  # noqa: SLF001
    backend._ingress_session = "session"  # noqa: SLF001
    catalog = SimpleNamespace(device=lambda _: None)

    actions, result = await backend.async_plan(
        "test_flow", definition, "run-one", catalog, dry_run=True
    )
    assert actions[0].type is ScenarioActionType.RUN_SCENARIO
    assert result["selectedBranch"] == "off_delay"

    try:
        await backend.async_plan(
            "test_flow", definition, "run-one", catalog, dry_run=True
        )
    except NodeRedBackendError as err:
        assert "forbidden" in str(err)
    else:
        raise AssertionError("Node-RED existing_action must fail closed")


async def test_executor_uses_node_red_plan_without_bypassing_dry_run() -> None:
    planned = ScenarioAction(
        "planned_notice", ScenarioActionType.NOTIFICATION, message="Выбрана ветка"
    )
    backend = SimpleNamespace(
        async_plan=AsyncMock(
            return_value=(
                (planned,),
                {
                    "status": "completed",
                    "summary": "Выбрана ветка.",
                    "selectedBranch": "night",
                    "durationMs": 1,
                    "trace": [],
                },
            )
        )
    )
    hass = SimpleNamespace(
        states=SimpleNamespace(get=lambda _: None),
        services=AsyncMock(),
    )
    catalog = SimpleNamespace(device=lambda _: None)
    executor = ScenarioExecutor(
        hass,
        catalog,
        AsyncMock(),
        node_red_backend=backend,
    )
    definition = replace(
        _definition(),
        node_red=ScenarioNodeRedMetadata(
            flow_id="flow-one", sync_status=ScenarioNodeRedSyncStatus.SYNCED
        ),
    )

    result = await executor.async_execute(
        definition, "run-one", scenario_id="test_flow", dry_run=True
    )

    assert result["status"] == "completed"
    assert result["node_red"]["selectedBranch"] == "night"
    assert result["receipts"][0]["status"] == "completed"
    hass.services.async_call.assert_not_awaited()


def load_tests(
    loader: unittest.TestLoader,
    tests: unittest.TestSuite,
    pattern: str | None,
) -> unittest.TestSuite:
    """Expose the compact function-style cases to the stdlib release runner."""
    del loader, tests, pattern
    suite = unittest.TestSuite()
    for name, case in sorted(globals().items()):
        if not name.startswith("test_") or not callable(case):
            continue
        if inspect.iscoroutinefunction(case):
            suite.addTest(_async_test_case(case))
        else:
            suite.addTest(unittest.FunctionTestCase(case))
    return suite
