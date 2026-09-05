"""Safety and synchronization tests for managed Node-RED scenario flows."""

from __future__ import annotations

import asyncio
import ast
from itertools import product
import json
from pathlib import Path
import subprocess
import sys
import unittest
from dataclasses import replace
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.hausman_hub.application import scenario_node_red
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
from custom_components.hausman_hub.application.scenarios import (
    ScenarioDeviceAction,
    ScenarioDeviceEntry,
)
from custom_components.hausman_hub.domain.scenarios import (
    ScenarioAction,
    ScenarioActionType,
    ScenarioDefinition,
    ScenarioExecutionBackend,
    ScenarioExecutionMode,
    ScenarioNodeRedGeneratedBy,
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


def _global_revision(
    flow: dict[str, object], revision: str
) -> dict[str, object]:
    nodes = flow.get("nodes")
    assert isinstance(nodes, list)
    return {"rev": revision, "flows": [dict(node) for node in nodes]}


def test_release_trust_allows_exact_previous_and_current_system_sources() -> None:
    assert scenario_node_red._TRUSTED_SYSTEM_SOURCE_HASHES == {  # noqa: SLF001
        "system-tambur-adaptive-controller": frozenset(
            {"4daef9ac2de8dc1c95dd2da6887e178751a65d0e47bcf48443635f68eb1ba5dc"}
        ),
        "system-small-corridor-light-controller": frozenset(
            {"bc9a2c7883046e568a428e355af312953d70f0f504393b063130f516fe5052b1"}
        ),
        "system-shower-comfort-controller": frozenset(
                {"757bde711c85ebad4826c2ec0bf2695d0034f7dd820c9ec7c30816f3f37c1551"}
        ),
    }


def test_compiler_builds_one_compact_command_free_function_flow() -> None:
    generic = replace(
        _definition(),
        actions=(
            ScenarioAction("confirm", ScenarioActionType.RUN_SCENARIO, scenario_id="confirm_off"),
        ),
    )
    source = compile_managed_function("test_flow", generic)
    flow = build_managed_flow("test_flow", "Тест", source)

    assert [node["type"] for node in flow["nodes"]] == [
        "http in",
        "function",
        "http response",
    ]
    assert "call-service" not in source
    assert managed_source_hash(source) == managed_source_hash(source)


def test_topology_accepts_live_get_without_empty_configs_only() -> None:
    source = compile_managed_function("test_flow", _definition())
    flow = build_managed_flow("test_flow", "Тест", source, flow_id="flow-one")
    flow.pop("configs")

    assert scenario_node_red._execution_topology_hash(  # noqa: SLF001
        "test_flow", "flow-one", flow, managed_source_hash(source)
    )

    for invalid in (
        {**flow, "configs": [{}]},
        {**flow, "configs": {}},
        {**flow, "unexpected": True},
    ):
        with pytest.raises(NodeRedBackendError, match="topology"):
            scenario_node_red._execution_topology_hash(  # noqa: SLF001
                "test_flow", "flow-one", invalid, managed_source_hash(source)
            )


def test_source_view_accepts_home_assistant_route_keyword() -> None:
    """HA dispatches ``{scenario_id}`` as a handler keyword argument."""

    source = Path(
        "custom_components/hausman_hub/scenario_api.py"
    ).read_text(encoding="utf-8")
    module = ast.parse(source)
    view = next(
        node
        for node in module.body
        if isinstance(node, ast.ClassDef)
        and node.name == "ScenarioNodeRedSourceView"
    )
    methods = {
        node.name: node
        for node in view.body
        if isinstance(node, ast.AsyncFunctionDef)
    }
    for name in ("get", "put"):
        assert "scenario_id" in [argument.arg for argument in methods[name].args.args]


def test_revisioned_flow_post_uses_nodes_deployment_header() -> None:
    captured: dict[str, object] = {}

    async def adapter(method, path, headers, payload):
        captured.update(method=method, path=path, headers=dict(headers), payload=payload)
        return 200, {"rev": "rev-two"}

    async def run() -> None:
        backend = NodeRedScenarioBackend(SimpleNamespace(), request_adapter=adapter)
        backend._ingress_token = "token"  # noqa: SLF001
        backend._ingress_session = "session"  # noqa: SLF001
        await backend._raw_request(  # noqa: SLF001
            "POST", "/flows", payload={"rev": "rev-one", "flows": []}, ingress=True
        )

    asyncio.run(run())
    assert captured["headers"]["Node-RED-Deployment-Type"] == "nodes"


class _StreamedResponse:
    def __init__(self, chunks: list[bytes], *, content_length: str | None) -> None:
        self.status = 200
        self.headers = (
            {} if content_length is None else {"Content-Length": content_length}
        )
        self.closed = False
        self.chunks_read = 0

        async def iter_chunked(_size: int):
            for chunk in chunks:
                self.chunks_read += 1
                yield chunk

        self.content = SimpleNamespace(iter_chunked=iter_chunked)


class _ResponseContext:
    def __init__(self, response: _StreamedResponse) -> None:
        self.response = response

    async def __aenter__(self) -> _StreamedResponse:
        return self.response

    async def __aexit__(self, *_args: object) -> None:
        self.response.closed = True


def _install_aiohttp_session(monkeypatch, response: _StreamedResponse) -> None:
    session = SimpleNamespace(
        request=lambda *_args, **_kwargs: _ResponseContext(response)
    )
    homeassistant = ModuleType("homeassistant")
    helpers = ModuleType("homeassistant.helpers")
    aiohttp_client = ModuleType("homeassistant.helpers.aiohttp_client")
    aiohttp_client.async_get_clientsession = lambda _hass: session
    homeassistant.helpers = helpers
    helpers.aiohttp_client = aiohttp_client
    monkeypatch.setitem(sys.modules, "homeassistant", homeassistant)
    monkeypatch.setitem(sys.modules, "homeassistant.helpers", helpers)
    monkeypatch.setitem(
        sys.modules,
        "homeassistant.helpers.aiohttp_client",
        aiohttp_client,
    )


@pytest.mark.asyncio
async def test_raw_request_rejects_oversized_content_length_without_reading(
    monkeypatch,
) -> None:
    response = _StreamedResponse(
        [b'{}'],
        content_length=str(scenario_node_red.MAX_NODE_RED_RESPONSE_BYTES + 1),
    )
    _install_aiohttp_session(monkeypatch, response)
    backend = NodeRedScenarioBackend(SimpleNamespace(), supervisor_token="trusted")

    with pytest.raises(NodeRedBackendError, match="too large"):
        await backend._raw_request("GET", "/flows")  # noqa: SLF001

    assert response.chunks_read == 0
    assert response.closed


@pytest.mark.asyncio
async def test_raw_request_bounds_unknown_length_stream_and_closes_response(
    monkeypatch,
) -> None:
    limit = scenario_node_red.MAX_NODE_RED_RESPONSE_BYTES
    response = _StreamedResponse(
        [b"x" * limit, b"y"],
        content_length=None,
    )
    _install_aiohttp_session(monkeypatch, response)
    backend = NodeRedScenarioBackend(SimpleNamespace(), supervisor_token="trusted")

    with pytest.raises(NodeRedBackendError, match="too large"):
        await backend._raw_request("GET", "/flows")  # noqa: SLF001

    assert response.chunks_read == 2
    assert response.closed


def test_prepare_compensation_restores_update_and_commit_clears_metadata() -> None:
    async def run() -> None:
        backend = NodeRedScenarioBackend(SimpleNamespace())
        backend.async_restore_source = AsyncMock()
        backend._last_prepare_operation = {  # noqa: SLF001
            "kind": "update",
            "scenarioId": "test_flow",
            "flowId": "server-flow-id",
            "sourceHash": "a" * 64,
            "previousSource": "return msg;",
        }
        await backend.async_compensate_last_prepare()
        backend.async_restore_source.assert_awaited_once_with(
            "test_flow",
            "server-flow-id",
            "return msg;",
            expected_current_hash="a" * 64,
        )
        assert backend._last_prepare_operation is None  # noqa: SLF001

        backend._last_prepare_operation = {"kind": "update"}  # noqa: SLF001
        await backend.async_commit_last_prepare()
        assert backend._last_prepare_operation is None  # noqa: SLF001

    asyncio.run(run())


def test_embedded_source_policy_rejects_direct_commands_and_context() -> None:
    generic = replace(
        _definition(),
        actions=(
            ScenarioAction("confirm", ScenarioActionType.RUN_SCENARIO, scenario_id="confirm_off"),
        ),
    )
    source = compile_managed_function("test_flow", generic)
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


def test_release_trust_hashes_match_managed_system_sources() -> None:
    sources = {
        "system-tambur-adaptive-controller": "tambur_controller.js",
        "system-shower-comfort-controller": "shower_controller.js",
        "system-small-corridor-light-controller": "small_corridor_controller.js",
    }
    for scenario_id, filename in sources.items():
        source = Path("tools/managed_scenarios", filename).read_text(encoding="utf-8")
        trusted = scenario_node_red._TRUSTED_SYSTEM_SOURCE_HASHES[scenario_id]
        assert managed_source_hash(source) in trusted
        assert len(trusted) == 1

    assert (
        "9060257eaa344944611e3992b33591ab6ddb24ddd984137fce7aeeea6703b55c"
        not in scenario_node_red._TRUSTED_SYSTEM_SOURCE_HASHES[
            "system-shower-comfort-controller"
        ]
    )
    assert "ef263e1adbcaa2e69ff118d14411b31892cf73497626751fbfa3106b81f2e933" not in scenario_node_red._TRUSTED_SYSTEM_SOURCE_HASHES["system-shower-comfort-controller"]
    assert (
        "3183bc1806afdadd797968bafcc7cbb738f13d80cc02c28cc42852de07c36d21"
        not in scenario_node_red._TRUSTED_SYSTEM_SOURCE_HASHES[
            "system-tambur-adaptive-controller"
        ]
    )


async def _case_embedded_source_update_uses_hash_lock_and_dry_run() -> None:
    proposed = compile_managed_function("test_flow", _definition())
    original = proposed.replace("Основная ветка", "Предыдущая ветка")
    deployed = build_managed_flow(
        "test_flow", "Тест", original, flow_id="flow-one"
    )
    # Real GET /flow/{id} replies omit an empty configs list.
    deployed.pop("configs")
    calls: list[tuple[str, str]] = []
    revision = "rev-one"

    async def adapter(method, path, headers, payload):
        nonlocal revision
        calls.append((method, path))
        if method == "GET" and path.endswith("/flows"):
            return 200, _global_revision(deployed, revision)
        if method == "GET" and path.endswith("/flow/flow-one"):
            return 200, deployed
        if method == "POST" and path.endswith("/flows"):
            assert payload["rev"] == revision
            nodes = [
                dict(node)
                for node in payload["flows"]
                if node.get("z") == "flow-one"
            ]
            deployed["nodes"] = nodes
            revision = "rev-two"
            return 200, {"rev": revision}
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
    assert result["verification"] is None
    assert ("POST", "/ingress/token/flows") in calls
    assert not any(method == "POST" and "/endpoint/" in path for method, path in calls)

    bypass = proposed.replace(
        "const started = Date.now();",
        "global['get']('secret');\nconst started = Date.now();",
    )
    try:
        await backend.async_update_source(
            "test_flow",
            definition,
            "flow-one",
            bypass,
            managed_source_hash(proposed),
            SimpleNamespace(device=lambda _: None),
            validate_only=True,
        )
    except NodeRedSourceInvalid as error:
        assert error.code == "source_not_release_trusted"
    else:
        raise AssertionError("untrusted source must fail closed")

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


async def _case_source_update_blocks_duplicate_endpoint_owner() -> None:
    proposed = compile_managed_function("test_flow", _definition())
    original = proposed.replace("Основная ветка", "Предыдущая ветка")
    deployed = build_managed_flow(
        "test_flow", "Тест", original, flow_id="flow-one"
    )
    global_body = _global_revision(deployed, "rev-one")
    global_body["flows"].append(
        {
            "id": "rogue-input",
            "type": "http in",
            "z": "rogue-flow",
            "url": "/hausman/scenarios/test_flow",
            "method": "post",
        }
    )
    calls: list[tuple[str, str]] = []

    async def adapter(method, path, headers, payload):
        del headers, payload
        calls.append((method, path))
        if method == "GET" and path.endswith("/flows"):
            return 200, global_body
        raise AssertionError("source flow must not be read or changed")

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

    try:
        await backend.async_update_source(
            "test_flow",
            definition,
            "flow-one",
            proposed,
            managed_source_hash(original),
            SimpleNamespace(device=lambda _: None),
            validate_only=False,
        )
    except NodeRedBackendError as error:
        assert "endpoint ownership" in str(error)
    else:
        raise AssertionError("duplicate source endpoint must fail closed")
    assert calls == [("GET", "/ingress/token/flows")]


async def _case_source_update_blocks_revision_change_before_dispatch() -> None:
    proposed = compile_managed_function("test_flow", _definition())
    original = proposed.replace("Основная ветка", "Предыдущая ветка")
    deployed = build_managed_flow(
        "test_flow", "Тест", original, flow_id="flow-one"
    )
    revisions = iter(("rev-one", "rev-two"))
    calls: list[tuple[str, str]] = []

    async def adapter(method, path, headers, payload):
        del headers, payload
        calls.append((method, path))
        if method == "GET" and path.endswith("/flows"):
            return 200, _global_revision(deployed, next(revisions))
        if method == "GET" and path.endswith("/flow/flow-one"):
            return 200, deployed
        raise AssertionError("revision race must block source update")

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

    try:
        await backend.async_update_source(
            "test_flow",
            definition,
            "flow-one",
            proposed,
            managed_source_hash(original),
            SimpleNamespace(device=lambda _: None),
            validate_only=False,
        )
    except NodeRedBackendError as error:
        assert "before source update" in str(error)
    else:
        raise AssertionError("source revision race must fail closed")
    assert not any(method == "POST" for method, _path in calls)


async def _case_source_update_blocks_rerouted_topology() -> None:
    proposed = compile_managed_function("test_flow", _definition())
    original = proposed.replace("Основная ветка", "Предыдущая ветка")
    deployed = build_managed_flow(
        "test_flow", "Тест", original, flow_id="flow-one"
    )
    deployed["nodes"][0]["wires"] = [["rogue-function"]]
    calls: list[tuple[str, str]] = []

    async def adapter(method, path, headers, payload):
        del headers, payload
        calls.append((method, path))
        if method == "GET" and path.endswith("/flows"):
            return 200, _global_revision(deployed, "rev-one")
        if method == "GET" and path.endswith("/flow/flow-one"):
            return 200, deployed
        raise AssertionError("rerouted topology must block source update")

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

    try:
        await backend.async_update_source(
            "test_flow",
            definition,
            "flow-one",
            proposed,
            managed_source_hash(original),
            SimpleNamespace(device=lambda _: None),
            validate_only=False,
        )
    except NodeRedBackendError as error:
        assert "topology" in str(error)
    else:
        raise AssertionError("rerouted source topology must fail closed")
    assert not any(method == "POST" for method, _path in calls)


async def _case_ambiguous_applied_source_update_restores_previous_source() -> None:
    proposed = compile_managed_function("test_flow", _definition())
    original = proposed.replace("Основная ветка", "Предыдущая ветка")
    deployed = build_managed_flow(
        "test_flow", "Тест", original, flow_id="flow-one"
    )
    revision = "rev-one"
    posts = 0

    async def adapter(method, path, headers, payload):
        nonlocal posts, revision
        del headers
        if method == "GET" and path.endswith("/flows"):
            return 200, _global_revision(deployed, revision)
        if method == "GET" and path.endswith("/flow/flow-one"):
            return 200, deployed
        if method == "POST" and path.endswith("/flows"):
            posts += 1
            nodes = [
                dict(node)
                for node in payload["flows"]
                if node.get("z") == "flow-one"
            ]
            deployed["nodes"] = nodes
            revision = f"rev-{posts + 1}"
            if posts == 1:
                return 500, {}
            return 200, {"rev": revision}
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

    try:
        await backend.async_update_source(
            "test_flow",
            definition,
            "flow-one",
            proposed,
            managed_source_hash(original),
            SimpleNamespace(device=lambda _: None),
            validate_only=False,
        )
    except NodeRedBackendError as error:
        assert "previous source was restored" in str(error)
    else:
        raise AssertionError("ambiguous applied update must report compensation")
    assert posts == 2
    assert managed_source_hash(str(deployed["nodes"][1]["func"])) == (
        managed_source_hash(original)
    )


async def _case_prepare_ignores_client_flow_id_and_confirms_exact_flow() -> None:
    calls: list[tuple[str, str]] = []
    deployed: dict[str, object] | None = None
    revision = "rev-one"
    generic = replace(
        _definition(),
        actions=(
            ScenarioAction("confirm", ScenarioActionType.RUN_SCENARIO, scenario_id="confirm_off"),
        ),
    )
    source = compile_managed_function("test_flow", generic)
    expected_flow = build_managed_flow("test_flow", "Тест", source)
    provisional_id = expected_flow["id"]
    expected_id = "server-flow-id"

    async def adapter(method, path, headers, payload):
        nonlocal deployed, revision
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
        if method == "GET" and path.endswith("/flows"):
            return 200, (
                _global_revision(deployed, revision)
                if deployed is not None
                else {"rev": revision, "flows": []}
            )
        if path.endswith("/flow") and method == "POST":
            assert headers["Node-RED-API-Version"] == "v2"
            assert "id" not in payload
            assert [node["type"] for node in payload["nodes"]] == [
                "http in",
                "function",
                "http response",
            ]
            deployed = dict(payload)
            deployed["id"] = expected_id
            deployed["nodes"] = [
                {**node, "z": expected_id} for node in payload["nodes"]
            ]
            assert all(node["z"] != provisional_id for node in deployed["nodes"])
            revision = "rev-two"
            return 201, {"id": expected_id}
        if method == "GET" and path.endswith(f"/flow/{expected_id}"):
            assert deployed is not None
            return 200, deployed
        raise AssertionError((method, path))

    backend = NodeRedScenarioBackend(SimpleNamespace(), request_adapter=adapter)
    definition = replace(
        _definition(),
        node_red=ScenarioNodeRedMetadata(
            flow_id="foreign-flow",
            flow_revision=99,
            source_hash="a" * 64,
            generated_by=ScenarioNodeRedGeneratedBy.USER,
            sync_status=ScenarioNodeRedSyncStatus.SYNCED,
            input_target_ids=("sensor_one",),
        ),
    )
    prepared = await backend.async_prepare("test_flow", "Тест", definition)

    assert prepared.node_red.flow_id == expected_id
    assert prepared.node_red.flow_revision == 1
    assert prepared.node_red.generated_by is ScenarioNodeRedGeneratedBy.HAUSMAN
    assert prepared.node_red.sync_status is ScenarioNodeRedSyncStatus.SYNCED
    assert ("POST", "/ingress/token-one/flow") in calls
    assert ("GET", "/ingress/token-one/flow/foreign-flow") not in calls
    assert not any(method == "PUT" for method, _path in calls)


async def _case_prepare_never_overwrites_a_manually_changed_function() -> None:
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


async def _case_prepare_reconciles_invalid_create_reply_by_exact_readback() -> None:
    deployed: dict[str, object] | None = None
    revision = "rev-one"
    source = compile_managed_function("test_flow", _definition())
    expected_flow = build_managed_flow("test_flow", "Тест", source)
    expected_id = str(expected_flow["id"])

    async def adapter(method, path, headers, payload):
        nonlocal deployed, revision
        del headers
        if method == "GET" and path.endswith("/flows"):
            return 200, (
                _global_revision(deployed, revision)
                if deployed is not None
                else {"rev": revision, "flows": []}
            )
        if method == "POST" and path.endswith("/flow"):
            deployed = dict(payload)
            deployed["id"] = expected_id
            deployed["nodes"] = [
                {**node, "z": expected_id} for node in payload["nodes"]
            ]
            revision = "rev-two"
            return 201, {"id": "unexpected-id"}
        if method == "GET" and path.endswith(f"/flow/{expected_id}"):
            assert deployed is not None
            return 200, deployed
        raise AssertionError((method, path))

    backend = NodeRedScenarioBackend(SimpleNamespace(), request_adapter=adapter)
    backend._ingress_token = "token"  # noqa: SLF001
    backend._ingress_session = "session"  # noqa: SLF001

    prepared = await backend.async_prepare("test_flow", "Тест", _definition())

    assert prepared.node_red.flow_id == expected_id
    assert prepared.node_red.sync_status is ScenarioNodeRedSyncStatus.SYNCED


async def _case_prepare_trusted_update_uses_revisioned_global_post() -> None:
    original_definition = _definition()
    updated_definition = replace(
        original_definition,
        actions=(
            ScenarioAction(
                "notify",
                ScenarioActionType.NOTIFICATION,
                message="Обновлено",
            ),
        ),
    )
    original = compile_managed_function("test_flow", original_definition)
    proposed = compile_managed_function("test_flow", updated_definition)
    deployed = build_managed_flow(
        "test_flow", "Тест", original, flow_id="flow-one"
    )
    revision = "rev-one"
    calls: list[tuple[str, str]] = []

    async def adapter(method, path, headers, payload):
        nonlocal revision
        del headers
        calls.append((method, path))
        if method == "GET" and path.endswith("/flows"):
            return 200, _global_revision(deployed, revision)
        if method == "GET" and path.endswith("/flow/flow-one"):
            return 200, deployed
        if method == "POST" and path.endswith("/flows"):
            nodes = [
                dict(node)
                for node in payload["flows"]
                if node.get("z") == "flow-one"
            ]
            deployed["nodes"] = nodes
            revision = "rev-two"
            return 200, {"rev": revision}
        raise AssertionError((method, path))

    previous = ScenarioNodeRedMetadata(
        flow_id="flow-one",
        flow_revision=2,
        source_hash=managed_source_hash(original),
        generated_by=ScenarioNodeRedGeneratedBy.AI,
        sync_status=ScenarioNodeRedSyncStatus.SYNCED,
    )
    backend = NodeRedScenarioBackend(
        SimpleNamespace(states=SimpleNamespace(get=lambda _: None)),
        request_adapter=adapter,
    )
    backend._ingress_token = "token"  # noqa: SLF001
    backend._ingress_session = "session"  # noqa: SLF001

    prepared = await backend.async_prepare(
        "test_flow",
        "Тест",
        replace(updated_definition, node_red=previous),
        previous=previous,
    )

    assert prepared.node_red.flow_id == "flow-one"
    assert prepared.node_red.flow_revision == 3
    assert prepared.node_red.source_hash == managed_source_hash(proposed)
    assert prepared.node_red.generated_by is ScenarioNodeRedGeneratedBy.AI
    assert prepared.node_red.sync_status is ScenarioNodeRedSyncStatus.SYNCED
    assert any(method == "POST" and path.endswith("/flows") for method, path in calls)
    assert not any(method == "PUT" for method, _path in calls)


async def _case_plan_accepts_nested_scenario_but_rejects_existing_actions() -> None:
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

    generic = replace(
        _definition(),
        actions=(
            ScenarioAction("confirm", ScenarioActionType.RUN_SCENARIO, scenario_id="confirm_off"),
        ),
    )
    source = compile_managed_function("test_flow", generic)
    deployed = build_managed_flow(
        "test_flow", "Тест", source, flow_id="flow-one"
    )
    calls: list[tuple[str, str]] = []
    posts: list[object] = []

    async def adapter(method, path, headers, payload):
        calls.append((method, path))
        if method == "GET" and path.endswith("/flows"):
            return 200, _global_revision(deployed, "rev-one")
        if method == "GET" and path.endswith("/flow/flow-one"):
            return 200, deployed
        if method == "POST":
            posts.append(payload)
            return 200, responses.pop(0)
        raise AssertionError((method, path))

    metadata = ScenarioNodeRedMetadata(
        flow_id="flow-one",
        source_hash=managed_source_hash(source),
        sync_status=ScenarioNodeRedSyncStatus.SYNCED,
    )
    definition = replace(generic, node_red=metadata)
    backend = NodeRedScenarioBackend(SimpleNamespace(states=SimpleNamespace(get=lambda _: None)), request_adapter=adapter)
    backend._ingress_token = "token"  # noqa: SLF001
    backend._ingress_session = "session"  # noqa: SLF001
    catalog = SimpleNamespace(device=lambda _: None)

    actions, result = await backend.async_plan(
        "test_flow",
        definition,
        "run-one",
        catalog,
        dry_run=True,
        trigger_context={
            "source": "manual",
            "trigger_id": "wall_on",
            "target_id": "relay",
            "new_value": "on",
            "nested": {"secret": True},
        },
    )
    assert actions[0].type is ScenarioActionType.RUN_SCENARIO
    assert result["selectedBranch"] == "off_delay"
    assert posts[0]["context"]["trigger"] == {
        "source": "manual",
        "trigger_id": "wall_on",
        "target_id": "relay",
        "new_value": "on",
    }

    try:
        await backend.async_plan(
            "test_flow", definition, "run-one", catalog, dry_run=True
        )
    except NodeRedBackendError as err:
        assert "forbidden" in str(err)
    else:
        raise AssertionError("Node-RED existing_action must fail closed")
    assert sum(method == "GET" for method, _path in calls) == 8


def test_tambur_power_up_plan_matches_release_envelope() -> None:
    source = Path("tools/managed_scenarios/tambur_controller.js").read_text(encoding="utf-8")
    payload = {
        "inputs": {
            "entity_156050daca86aa6c": {"state": "on", "attributes": {}},
            "entity_10b78187426f8485": {"state": "off", "attributes": {}},
            "entity_6b9ccdab9bb484b2": {"state": "above_horizon", "attributes": {}},
            "entity_71859313239a14e4": {"state": "off", "attributes": {"brightness": 13, "color_temp_kelvin": 6500}},
            "entity_fbdf27871edb89bf": {"state": "off", "attributes": {}},
        },
        "correlationId": "run.power-up",
        "context": {"timestampMs": 1_785_553_200_000},
    }
    executed = subprocess.run(
        ["node", "-e", f"const msg={{payload:{json.dumps(payload)}}};\n(function(){{\n{source}\n}})();\nconsole.log(JSON.stringify(msg.payload));"],
        check=True, capture_output=True, text=True,
    )
    result = json.loads(executed.stdout)
    actions = [
        scenario_node_red._action_from_payload(item, "test")  # noqa: SLF001
        for item in result["actions"]
    ]
    NodeRedScenarioBackend._validate_plan_envelope(  # noqa: SLF001
        "system-tambur-adaptive-controller", _definition(), actions
    )
    assert [action.action_id for action in actions].count("set_color_temperature") == 1
    assert sum(action.type is ScenarioActionType.DELAY for action in actions) == 1


def test_release_owned_trigger_context_is_exact_and_correlated() -> None:
    trigger = {
        "source": "manual",
        "trigger_id": "off_up",
        "recovery": False,
        "binding": "tambur-light-group",
        "typed_intent": "off",
        "direct_user_intent": "off",
        "intent_receipt_id": "receipt.off-1",
        "raw_subtype": "off_up",
        "dedup_disposition": "accepted",
        "correlation_id": "receipt.off-1",
    }
    assert scenario_node_red._validated_trigger_context(  # noqa: SLF001
        trigger, "receipt.off-1"
    ) == trigger
    for invalid in (
        {**trigger, "payload": {"identity": "attacker"}},
        {**trigger, "correlation_id": "different"},
        {**trigger, "raw_subtype": "toggle_down"},
        {**trigger, "direct_user_intent": {"value": "off"}},
        {key: value for key, value in trigger.items() if key != "binding"},
    ):
        with pytest.raises(NodeRedBackendError, match="trigger context"):
            scenario_node_red._validated_trigger_context(  # noqa: SLF001
                invalid, "receipt.off-1"
            )


def test_release_owned_direct_off_plan_is_exact_group_without_mirror() -> None:
    trigger = {
        "source": "manual", "trigger_id": "off_up", "recovery": False,
        "binding": "tambur-light-group", "typed_intent": "off",
        "direct_user_intent": "off", "intent_receipt_id": "receipt.off-1",
        "raw_subtype": "off_up", "dedup_disposition": "accepted",
        "correlation_id": "receipt.off-1",
    }
    group = [
        ScenarioAction("chandelier_off", ScenarioActionType.DEVICE_ACTION, target_id="entity_71859313239a14e4", action_id="turn_off"),
        ScenarioAction("points_off", ScenarioActionType.DEVICE_ACTION, target_id="entity_cd0098e5ff95da46", action_id="turn_off"),
    ]
    NodeRedScenarioBackend._validate_typed_plan(  # noqa: SLF001
        "system-tambur-adaptive-controller", group, trigger
    )
    with pytest.raises(NodeRedBackendError, match="typed intent"):
        NodeRedScenarioBackend._validate_typed_plan(  # noqa: SLF001
            "system-tambur-adaptive-controller", group[:-1], trigger
        )
    with pytest.raises(NodeRedBackendError, match="typed intent"):
        NodeRedScenarioBackend._validate_typed_plan(  # noqa: SLF001
            "system-tambur-adaptive-controller",
            [*group, ScenarioAction("mirror_off", ScenarioActionType.DEVICE_ACTION, target_id="entity_fbdf27871edb89bf", action_id="turn_off")],
            trigger,
        )
def test_system_input_snapshot_drives_real_tambur_sunset_mired_branch() -> None:
    ids = {
        "sun": "entity_6b9ccdab9bb484b2",
        "chandelier": "entity_71859313239a14e4",
        "presence": "entity_156050daca86aa6c",
        "motion": "entity_10b78187426f8485",
        "mirror": "entity_fbdf27871edb89bf",
    }
    devices = {
        entity_id: ScenarioDeviceEntry(entity_id, entity_id, entity_id, ())
        for entity_id in ids.values()
    }
    states = {
        ids["sun"]: SimpleNamespace(
            state="below_horizon",
            attributes={"next_setting": "2026-08-27T13:00:00Z", "token": "secret"},
        ),
        ids["chandelier"]: SimpleNamespace(
            state="on",
            attributes={"brightness": 153, "color_temp": 278, "internal_url": "http://private"},
        ),
        ids["presence"]: SimpleNamespace(state="on", attributes={}),
        ids["motion"]: SimpleNamespace(state="off", attributes={}),
        ids["mirror"]: SimpleNamespace(state="off", attributes={}),
    }
    definition = _definition(
        node_red=ScenarioNodeRedMetadata(input_target_ids=tuple(devices)),
    )
    backend = NodeRedScenarioBackend(
        SimpleNamespace(states=SimpleNamespace(get=states.get))
    )
    snapshot = backend._input_snapshot(  # noqa: SLF001
        "system-tambur-adaptive-controller",
        definition,
        SimpleNamespace(device=devices.get),
    )
    assert snapshot[ids["sun"]]["attributes"] == {"next_setting": "2026-08-27T13:00:00Z"}
    assert snapshot[ids["chandelier"]]["attributes"] == {"brightness": 153, "color_temp": 278}
    source = Path("tools/managed_scenarios/tambur_controller.js").read_text(encoding="utf-8")
    request = {"inputs": snapshot, "context": {"timestampMs": 1_787_839_200_000}}
    executed = subprocess.run(
        ["node", "-e", f"const msg={{payload:{json.dumps(request)}}};\n(function(){{\n{source}\n}})();\nconsole.log(JSON.stringify(msg.payload));"],
        check=True, capture_output=True, text=True,
    )
    payload = json.loads(executed.stdout)
    actions = [scenario_node_red._action_from_payload(item, "tambur") for item in payload["actions"]]  # noqa: SLF001
    NodeRedScenarioBackend._validate_plan_envelope(  # noqa: SLF001
        "system-tambur-adaptive-controller", definition, actions
    )
    assert payload["selectedBranch"] == "after_sunset_lux_fallback"
    assert [action.id for action in actions] == [
        "chandelier_on", "chandelier_ownership_wait", "brightness",
        "temperature_target", "points_on",
    ]


def test_system_branch_validator_rejects_mutated_values_order_unions_and_excess() -> None:
    tambur = [
        ScenarioAction("chandelier_on", ScenarioActionType.DEVICE_ACTION, target_id="entity_71859313239a14e4", action_id="turn_on"),
        ScenarioAction("chandelier_ownership_wait", ScenarioActionType.DELAY, delay_seconds=1),
        ScenarioAction("brightness", ScenarioActionType.DEVICE_ACTION, target_id="entity_71859313239a14e4", action_id="set_brightness_percent", value=50),
        ScenarioAction("temperature_target", ScenarioActionType.DEVICE_ACTION, target_id="entity_71859313239a14e4", action_id="set_color_temperature", value=4400),
    ]
    shower_source = Path("tools/managed_scenarios/shower_controller.js").read_text(encoding="utf-8")
    shower_request = {
        "inputs": {
            "entity_d1fb2cbf2a691bba": {"state": "off", "attributes": {}},
            "entity_fd3945cf1a2110f8": {"state": "45", "attributes": {}},
            "entity_6b9ccdab9bb484b2": {"state": "above_horizon", "attributes": {}},
            "entity_46174e1ff9913212": {"state": "on", "attributes": {}},
            "entity_1fdcd8b244637246": {"state": "off", "attributes": {}},
            "entity_afef5df0e0cae309": {"state": "on", "attributes": {}},
            "entity_e7a7c61eec7bdff8": {"state": "off", "attributes": {}},
        },
        "context": {"timestampMs": 1_787_810_400_000},
    }
    shower_run = subprocess.run(
        ["node", "-e", f"const msg={{payload:{json.dumps(shower_request)}}};\n(function(){{\n{shower_source}\n}})();\nconsole.log(JSON.stringify(msg.payload));"],
        check=True, capture_output=True, text=True,
    )
    shower = [
        scenario_node_red._action_from_payload(item, "shower")  # noqa: SLF001
        for item in json.loads(shower_run.stdout)["actions"]
    ]
    NodeRedScenarioBackend._validate_plan_envelope("system-tambur-adaptive-controller", _definition(), tambur)  # noqa: SLF001
    NodeRedScenarioBackend._validate_plan_envelope("system-shower-comfort-controller", _definition(), shower)  # noqa: SLF001
    invalid = (
        ("system-tambur-adaptive-controller", [*tambur[:-1], replace(tambur[-1], value=3500)]),
        ("system-tambur-adaptive-controller", [*tambur[:-1], replace(tambur[-1], value=float("nan"))]),
        ("system-tambur-adaptive-controller", [*tambur[:-1], replace(tambur[-1], value=float("inf"))]),
        ("system-tambur-adaptive-controller", [tambur[1], tambur[0], *tambur[2:]]),
        ("system-shower-comfort-controller", [ScenarioAction("fan_presence_wait", ScenarioActionType.DELAY, delay_seconds=120), ScenarioAction("set_fan_on", ScenarioActionType.DEVICE_ACTION, target_id="entity_afef5df0e0cae309", action_id="turn_on"), *shower]),
        ("system-shower-comfort-controller", [*shower, ScenarioAction("extra_wait", ScenarioActionType.DELAY, delay_seconds=300)]),
        ("system-shower-comfort-controller", [*shower, ScenarioAction("set_fan_off_again", ScenarioActionType.DEVICE_ACTION, target_id="entity_afef5df0e0cae309", action_id="turn_off")]),
        ("system-shower-comfort-controller", [ScenarioAction("set_fan_on", ScenarioActionType.DEVICE_ACTION, target_id="entity_afef5df0e0cae309", action_id="turn_on"), ScenarioAction("absence_wait", ScenarioActionType.DELAY, delay_seconds=300), ScenarioAction("set_fan_off", ScenarioActionType.DEVICE_ACTION, target_id="entity_afef5df0e0cae309", action_id="turn_off")]),
    )
    for scenario_id, actions in invalid:
        with pytest.raises(NodeRedBackendError):
            NodeRedScenarioBackend._validate_plan_envelope(scenario_id, _definition(), actions)  # noqa: SLF001


def test_system_branch_validator_accepts_exhaustive_real_source_plans() -> None:
    def run_many(source_path: str, requests: list[dict[str, object]]) -> list[dict[str, object]]:
        source = Path(source_path).read_text(encoding="utf-8")
        script = (
            f"const source={json.dumps(source)};const requests=JSON.parse(require('fs').readFileSync(0,'utf8'));"
            "const execute=new Function('msg',source);"
            "console.log(JSON.stringify(requests.map(payload=>execute({payload}).payload)));"
        )
        result = subprocess.run(["node", "-e", script], input=json.dumps(requests), check=True, capture_output=True, text=True)
        return json.loads(result.stdout)

    shower_requests = []
    for hour, sun, presence, humidity, main, extra, fan, cabinet in product(
        (2, 12, 20), ("above_horizon", "below_horizon", "unknown"),
        ("on", "off"), ("45", "60", "unknown"), ("on", "off"),
        ("on", "off"), ("on", "off"), ("on", "off"),
    ):
        shower_requests.append({"context": {"timestampMs": 1_787_760_000_000 + hour * 3_600_000}, "inputs": {
            "entity_d1fb2cbf2a691bba": {"state": presence, "attributes": {}},
            "entity_fd3945cf1a2110f8": {"state": humidity, "attributes": {}},
            "entity_6b9ccdab9bb484b2": {"state": sun, "attributes": {}},
            "entity_46174e1ff9913212": {"state": main, "attributes": {}},
            "entity_1fdcd8b244637246": {"state": extra, "attributes": {}},
            "entity_afef5df0e0cae309": {"state": fan, "attributes": {}},
            "entity_e7a7c61eec7bdff8": {"state": cabinet, "attributes": {}},
        }})
    for payload in run_many("tools/managed_scenarios/shower_controller.js", shower_requests):
        actions = [scenario_node_red._action_from_payload(item, "shower") for item in payload["actions"]]  # noqa: SLF001
        NodeRedScenarioBackend._validate_plan_envelope("system-shower-comfort-controller", _definition(), actions)  # noqa: SLF001

    tambur_requests = []
    for hour, sun, presence, motion, chandelier, mirror in product(
        (2, 12, 20), ("above_horizon", "below_horizon", "unknown"),
        ("on", "off", "unknown"), ("on", "off", "unknown"),
        ("on", "off"), ("on", "off"),
    ):
        tambur_requests.append({"context": {"timestampMs": 1_787_760_000_000 + hour * 3_600_000}, "inputs": {
            "entity_156050daca86aa6c": {"state": presence, "attributes": {}},
            "entity_10b78187426f8485": {"state": motion, "attributes": {}},
            "entity_6b9ccdab9bb484b2": {"state": sun, "attributes": {"next_setting": "2026-08-27T13:00:00Z"}},
            "entity_71859313239a14e4": {"state": chandelier, "attributes": {"brightness": 153, "color_temp_kelvin": 3600}},
            "entity_fbdf27871edb89bf": {"state": mirror, "attributes": {}},
            "entity_5f3b4436fb7b6f2b": {"state": "50", "attributes": {}},
            "entity_cd0098e5ff95da46": {"state": "on", "attributes": {}},
            "entity_b47991988cc6b9f3": {"state": "on", "attributes": {}},
        }})
    for payload in run_many("tools/managed_scenarios/tambur_controller.js", tambur_requests):
        actions = [scenario_node_red._action_from_payload(item, "tambur") for item in payload["actions"]]  # noqa: SLF001
        NodeRedScenarioBackend._validate_plan_envelope("system-tambur-adaptive-controller", _definition(), actions)  # noqa: SLF001

    small_requests = []
    for hour, sun, motion, relay, chandelier, local_light, lux in product(
        (0, 6, 20), ("above_horizon", "below_horizon", "unknown"),
        ("on", "off", "unknown"), ("on", "off"), ("on", "off"),
        ("bright", "dark"), ("5", "50", "500", "unknown"),
    ):
        small_requests.append({
            "context": {
                "timestampMs": 1_787_760_000_000 + hour * 3_600_000,
                "trigger": {},
            },
            "inputs": {
                "entity_90417aada6a33491": {"state": motion, "attributes": {}},
                "entity_6b9ccdab9bb484b2": {"state": sun, "attributes": {}},
                "entity_5f3b4436fb7b6f2b": {"state": lux, "attributes": {}},
                "entity_c9d6bc67f172f30d": {"state": local_light, "attributes": {}},
                "entity_4be32416634e6416": {"state": relay, "attributes": {}},
                "entity_9ed909332fdaa8fd": {
                    "state": chandelier,
                    "attributes": {"brightness": 255, "color_temp_kelvin": 3000},
                },
            },
        })
    for payload in run_many("tools/managed_scenarios/small_corridor_controller.js", small_requests):
        actions = [scenario_node_red._action_from_payload(item, "small") for item in payload["actions"]]  # noqa: SLF001
        NodeRedScenarioBackend._validate_plan_envelope("system-small-corridor-light-controller", _definition(), actions)  # noqa: SLF001


def test_plan_envelope_rejects_untrusted_expansion() -> None:
    definition = replace(
        _definition(),
        actions=(
            ScenarioAction("off", ScenarioActionType.DEVICE_ACTION, target_id="light_1", action_id="turn_off"),
            ScenarioAction("wait", ScenarioActionType.DELAY, delay_seconds=5),
            ScenarioAction("nested", ScenarioActionType.RUN_SCENARIO, scenario_id="child"),
        ),
    )
    invalid = (
        [ScenarioAction("off1", ScenarioActionType.DEVICE_ACTION, target_id="light_1", action_id="turn_off"), ScenarioAction("off2", ScenarioActionType.DEVICE_ACTION, target_id="light_1", action_id="turn_off")],
        [ScenarioAction("wait1", ScenarioActionType.DELAY, delay_seconds=5), ScenarioAction("wait2", ScenarioActionType.DELAY, delay_seconds=5)],
        [ScenarioAction("notice", ScenarioActionType.NOTIFICATION, message="no")],
        [ScenarioAction("other", ScenarioActionType.DEVICE_ACTION, target_id="light_2", action_id="turn_on")],
        [ScenarioAction("other", ScenarioActionType.RUN_SCENARIO, scenario_id="other")],
    )
    for actions in invalid:
        try:
            NodeRedScenarioBackend._validate_plan_envelope("generic", definition, list(actions))  # noqa: SLF001
        except NodeRedBackendError:
            continue
        raise AssertionError("untrusted Node-RED expansion must fail closed")


def test_generic_plan_requires_the_exact_server_authored_sequence() -> None:
    definition = replace(
        _definition(),
        actions=(
            ScenarioAction("wait", ScenarioActionType.DELAY, delay_seconds=5),
            ScenarioAction("notice", ScenarioActionType.NOTIFICATION, message="Готово"),
            ScenarioAction("child", ScenarioActionType.RUN_SCENARIO, scenario_id="child"),
        ),
    )
    expected = list(definition.actions)
    NodeRedScenarioBackend._validate_plan_envelope("generic", definition, expected)  # noqa: SLF001
    invalid = (
        expected[1:],
        [replace(expected[0], id="other"), *expected[1:]],
        [expected[0], replace(expected[1], message="Изменено"), expected[2]],
        [expected[1], expected[0], expected[2]],
        [*expected, expected[2]],
    )
    for actions in invalid:
        with pytest.raises(NodeRedBackendError):
            NodeRedScenarioBackend._validate_plan_envelope("generic", definition, list(actions))  # noqa: SLF001


async def _case_plan_blocks_changed_source_before_execution_post() -> None:
    source = compile_managed_function("test_flow", _definition())
    changed = source.replace("const started = Date.now();", "const started = 1;")
    deployed = build_managed_flow(
        "test_flow", "Тест", changed, flow_id="flow-one"
    )
    calls: list[tuple[str, str]] = []

    async def adapter(method, path, headers, payload):
        calls.append((method, path))
        if method == "GET" and path.endswith("/flows"):
            return 200, _global_revision(deployed, "rev-one")
        if method == "GET" and path.endswith("/flow/flow-one"):
            return 200, deployed
        raise AssertionError("execution endpoint must not be called")

    metadata = ScenarioNodeRedMetadata(
        flow_id="flow-one",
        source_hash=managed_source_hash(source),
        sync_status=ScenarioNodeRedSyncStatus.SYNCED,
    )
    definition = replace(_definition(), node_red=metadata)
    backend = NodeRedScenarioBackend(
        SimpleNamespace(states=SimpleNamespace(get=lambda _: None)),
        request_adapter=adapter,
    )
    backend._ingress_token = "token"  # noqa: SLF001
    backend._ingress_session = "session"  # noqa: SLF001

    try:
        await backend.async_plan(
            "test_flow",
            definition,
            "run-changed",
            SimpleNamespace(device=lambda _: None),
            dry_run=False,
        )
    except NodeRedBackendError as error:
        assert "changed before execution" in str(error)
    else:
        raise AssertionError("changed Node-RED source must fail closed")
    assert calls == [
        ("GET", "/ingress/token/flows"),
        ("GET", "/ingress/token/flow/flow-one"),
    ]


async def _case_system_plan_blocks_source_outside_release() -> None:
    scenario_id = "system-tambur-adaptive-controller"
    trusted = Path("tools/managed_scenarios/tambur_controller.js").read_text(
        encoding="utf-8"
    )
    changed = trusted + "\n// changed after release\n"
    deployed = build_managed_flow(
        scenario_id, "Тамбур", changed, flow_id="flow-tambur"
    )
    calls: list[tuple[str, str]] = []

    async def adapter(method, path, headers, payload):
        calls.append((method, path))
        if method == "GET" and path.endswith("/flows"):
            return 200, _global_revision(deployed, "rev-one")
        if method == "GET" and path.endswith("/flow/flow-tambur"):
            return 200, deployed
        raise AssertionError("execution endpoint must not be called")

    metadata = ScenarioNodeRedMetadata(
        flow_id="flow-tambur",
        source_hash=managed_source_hash(changed),
        sync_status=ScenarioNodeRedSyncStatus.SYNCED,
    )
    backend = NodeRedScenarioBackend(
        SimpleNamespace(states=SimpleNamespace(get=lambda _: None)),
        request_adapter=adapter,
    )
    backend._ingress_token = "token"  # noqa: SLF001
    backend._ingress_session = "session"  # noqa: SLF001

    try:
        await backend.async_plan(
            scenario_id,
            replace(_definition(), node_red=metadata),
            "run-untrusted-system-source",
            SimpleNamespace(device=lambda _: None),
            dry_run=False,
        )
    except NodeRedBackendError as error:
        assert "release-trusted" in str(error)
    else:
        raise AssertionError("untrusted system source must fail closed")
    assert calls == [
        ("GET", "/ingress/token/flows"),
        ("GET", "/ingress/token/flow/flow-tambur"),
    ]


async def _case_plan_blocks_rerouted_managed_topology() -> None:
    source = compile_managed_function("test_flow", _definition())
    deployed = build_managed_flow(
        "test_flow", "Тест", source, flow_id="flow-one"
    )
    input_node = deployed["nodes"][0]
    response_node = deployed["nodes"][2]
    input_node["wires"] = [["injected-function"]]
    deployed["nodes"].append(
        {
            "id": "injected-function",
            "type": "function",
            "z": "flow-one",
            "name": "Подмена",
            "func": "return msg;",
            "outputs": 1,
            "timeout": "4",
            "noerr": 0,
            "initialize": "",
            "finalize": "",
            "libs": [],
            "x": 450,
            "y": 220,
            "wires": [[response_node["id"]]],
        }
    )
    calls: list[tuple[str, str]] = []

    async def adapter(method, path, headers, payload):
        calls.append((method, path))
        if method == "GET" and path.endswith("/flows"):
            return 200, _global_revision(deployed, "rev-one")
        if method == "GET" and path.endswith("/flow/flow-one"):
            return 200, deployed
        raise AssertionError("execution endpoint must not be called")

    metadata = ScenarioNodeRedMetadata(
        flow_id="flow-one",
        source_hash=managed_source_hash(source),
        sync_status=ScenarioNodeRedSyncStatus.SYNCED,
    )
    backend = NodeRedScenarioBackend(
        SimpleNamespace(states=SimpleNamespace(get=lambda _: None)),
        request_adapter=adapter,
    )
    backend._ingress_token = "token"  # noqa: SLF001
    backend._ingress_session = "session"  # noqa: SLF001

    try:
        await backend.async_plan(
            "test_flow",
            replace(_definition(), node_red=metadata),
            "run-rerouted",
            SimpleNamespace(device=lambda _: None),
            dry_run=False,
        )
    except NodeRedBackendError as error:
        assert "topology" in str(error)
    else:
        raise AssertionError("rerouted Node-RED topology must fail closed")
    assert not any(method == "POST" for method, _path in calls)


async def _case_plan_blocks_duplicate_global_endpoint_owner() -> None:
    source = compile_managed_function("test_flow", _definition())
    deployed = build_managed_flow(
        "test_flow", "Тест", source, flow_id="flow-one"
    )
    global_body = _global_revision(deployed, "rev-one")
    global_body["flows"].append(
        {
            "id": "rogue-http-input",
            "type": "http in",
            "z": "rogue-flow",
            "url": "/hausman/scenarios/test_flow",
            "method": "post",
        }
    )
    calls: list[tuple[str, str]] = []

    async def adapter(method, path, headers, payload):
        calls.append((method, path))
        if method == "GET" and path.endswith("/flows"):
            return 200, global_body
        raise AssertionError("managed flow and execution must not be read")

    metadata = ScenarioNodeRedMetadata(
        flow_id="flow-one",
        source_hash=managed_source_hash(source),
        sync_status=ScenarioNodeRedSyncStatus.SYNCED,
    )
    backend = NodeRedScenarioBackend(
        SimpleNamespace(states=SimpleNamespace(get=lambda _: None)),
        request_adapter=adapter,
    )
    backend._ingress_token = "token"  # noqa: SLF001
    backend._ingress_session = "session"  # noqa: SLF001

    try:
        await backend.async_plan(
            "test_flow",
            replace(_definition(), node_red=metadata),
            "run-duplicate-endpoint",
            SimpleNamespace(device=lambda _: None),
            dry_run=False,
        )
    except NodeRedBackendError as error:
        assert "endpoint ownership" in str(error)
    else:
        raise AssertionError("duplicate Node-RED endpoint must fail closed")
    assert calls == [("GET", "/ingress/token/flows")]


async def _case_plan_rejects_revision_change_during_execution() -> None:
    source = compile_managed_function("test_flow", _definition())
    deployed = build_managed_flow(
        "test_flow", "Тест", source, flow_id="flow-one"
    )
    revisions = iter(("rev-one", "rev-two"))
    response = {
        "contract": {
            "name": "hausman-node-red-scenario-execution",
            "version": 1,
        },
        "correlationId": "run-toctou",
        "scenarioId": "test_flow",
        "status": "completed",
        "summary": "Подменённый ответ не должен исполняться.",
        "selectedBranch": None,
        "durationMs": 1,
        "trace": [],
        "actions": [
            {
                "id": "light_on",
                "type": "device_action",
                "targetId": "light_one",
                "actionId": "turn_on",
            }
        ],
    }
    calls: list[tuple[str, str]] = []

    async def adapter(method, path, headers, payload):
        calls.append((method, path))
        if method == "GET" and path.endswith("/flows"):
            return 200, _global_revision(deployed, next(revisions))
        if method == "GET" and path.endswith("/flow/flow-one"):
            return 200, deployed
        if method == "POST":
            return 200, response
        raise AssertionError((method, path))

    metadata = ScenarioNodeRedMetadata(
        flow_id="flow-one",
        source_hash=managed_source_hash(source),
        sync_status=ScenarioNodeRedSyncStatus.SYNCED,
    )
    backend = NodeRedScenarioBackend(
        SimpleNamespace(states=SimpleNamespace(get=lambda _: None)),
        request_adapter=adapter,
    )
    backend._ingress_token = "token"  # noqa: SLF001
    backend._ingress_session = "session"  # noqa: SLF001

    try:
        await backend.async_plan(
            "test_flow",
            replace(_definition(), node_red=metadata),
            "run-toctou",
            SimpleNamespace(device=lambda _: None),
            dry_run=False,
        )
    except NodeRedBackendError as error:
        assert "changed during execution" in str(error)
    else:
        raise AssertionError("Node-RED revision race must fail closed")
    assert sum(method == "POST" for method, _path in calls) == 1


async def _case_executor_uses_node_red_plan_without_bypassing_dry_run() -> None:
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


async def _case_executor_applies_manual_light_priority_after_node_red_plan() -> None:
    planned = ScenarioAction(
        "planned_light",
        ScenarioActionType.DEVICE_ACTION,
        target_id="light_one",
        action_id="turn_on",
    )
    backend = SimpleNamespace(
        async_plan=AsyncMock(
            return_value=(
                (planned,),
                {
                    "status": "completed",
                    "summary": "Выбрана ветка света.",
                    "selectedBranch": "presence",
                    "durationMs": 1,
                    "trace": [],
                },
            )
        )
    )
    states = {
        "light.hall": SimpleNamespace(state="on", attributes={}),
        "binary_sensor.hall_motion": SimpleNamespace(state="on", attributes={}),
    }
    hass = SimpleNamespace(
        states=SimpleNamespace(get=states.get),
        services=AsyncMock(),
    )
    devices = {
        "light_one": ScenarioDeviceEntry(
            target_id="light_one",
            name="Люстра",
            entity_id="light.hall",
            actions=(
                ScenarioDeviceAction(
                    action_id="turn_on",
                    title="Включить",
                    domain="light",
                    service="turn_on",
                    allowed_fields=frozenset(),
                ),
            ),
        ),
        "sensor_one": ScenarioDeviceEntry(
            target_id="sensor_one",
            name="Датчик движения",
            entity_id="binary_sensor.hall_motion",
            actions=(),
        ),
    }
    catalog = SimpleNamespace(device=devices.get)
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
        definition,
        "run-node-red-priority",
        scenario_id="hall_light",
        scenario_title="Свет по движению",
        trigger_context={
            "source": "device_state",
            "trigger_id": "motion",
            "target_id": "sensor_one",
            "old_value": "off",
            "new_value": "on",
            "recovery": False,
        },
    )

    assert result["status"] == "completed"
    assert result["node_red"]["selectedBranch"] == "presence"
    assert result["manual_light_priority"]["applied"] is True
    assert result["receipts"][0]["reason"] == "manual_light_already_on"
    hass.services.async_call.assert_not_awaited()


def test_embedded_source_update_uses_hash_lock_and_dry_run() -> None:
    asyncio.run(_case_embedded_source_update_uses_hash_lock_and_dry_run())


def test_source_update_blocks_duplicate_endpoint_owner() -> None:
    asyncio.run(_case_source_update_blocks_duplicate_endpoint_owner())


def test_source_update_blocks_revision_change_before_dispatch() -> None:
    asyncio.run(_case_source_update_blocks_revision_change_before_dispatch())


def test_source_update_blocks_rerouted_topology() -> None:
    asyncio.run(_case_source_update_blocks_rerouted_topology())


def test_ambiguous_applied_source_update_restores_previous_source() -> None:
    asyncio.run(_case_ambiguous_applied_source_update_restores_previous_source())


def test_prepare_ignores_client_flow_id_and_confirms_exact_flow() -> None:
    asyncio.run(_case_prepare_ignores_client_flow_id_and_confirms_exact_flow())


def test_prepare_never_overwrites_a_manually_changed_function() -> None:
    asyncio.run(_case_prepare_never_overwrites_a_manually_changed_function())


def test_prepare_reconciles_invalid_create_reply_by_exact_readback() -> None:
    asyncio.run(_case_prepare_reconciles_invalid_create_reply_by_exact_readback())


def test_prepare_trusted_update_uses_revisioned_global_post() -> None:
    asyncio.run(_case_prepare_trusted_update_uses_revisioned_global_post())


def test_plan_accepts_nested_scenario_but_rejects_existing_actions() -> None:
    asyncio.run(_case_plan_accepts_nested_scenario_but_rejects_existing_actions())


def test_plan_blocks_changed_source_before_execution_post() -> None:
    asyncio.run(_case_plan_blocks_changed_source_before_execution_post())


def test_system_plan_blocks_source_outside_release() -> None:
    asyncio.run(_case_system_plan_blocks_source_outside_release())


def test_plan_blocks_rerouted_managed_topology() -> None:
    asyncio.run(_case_plan_blocks_rerouted_managed_topology())


def test_plan_blocks_duplicate_global_endpoint_owner() -> None:
    asyncio.run(_case_plan_blocks_duplicate_global_endpoint_owner())


def test_plan_rejects_revision_change_during_execution() -> None:
    asyncio.run(_case_plan_rejects_revision_change_during_execution())


def test_executor_uses_node_red_plan_without_bypassing_dry_run() -> None:
    asyncio.run(_case_executor_uses_node_red_plan_without_bypassing_dry_run())


def test_executor_applies_manual_light_priority_after_node_red_plan() -> None:
    asyncio.run(_case_executor_applies_manual_light_priority_after_node_red_plan())


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
        suite.addTest(unittest.FunctionTestCase(case))
    return suite
