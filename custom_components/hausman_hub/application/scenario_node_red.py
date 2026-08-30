"""Managed Node-RED function-flow backend for Hausman scenarios.

Node-RED calculates a bounded action plan. Hausman remains the only component
that validates, sends and confirms physical device commands.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ..domain.scenarios import (
    ScenarioAction,
    ScenarioActionType,
    ScenarioDefinition,
    ScenarioNodeRedGeneratedBy,
    ScenarioNodeRedMetadata,
    ScenarioNodeRedSyncStatus,
    ScenarioViolation,
    _action_from_payload,
    _definition_to_payload,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .scenarios import ScenarioCatalog


NODE_RED_ADDON_SLUG = "a0d7b954_nodered"
NODE_RED_STATUS_CONTRACT = "hausman-hub-scenario-node-red-status"
NODE_RED_EXECUTION_CONTRACT = "hausman-node-red-scenario-execution"
NODE_RED_ENDPOINT_PREFIX = "endpoint/hausman/scenarios"
NODE_RED_TIMEOUT_SECONDS = 5
MAX_RETURNED_ACTIONS = 32
MAX_MANAGED_SOURCE_BYTES = 65_536
_ALLOWED_TRACE_STATUSES = frozenset({"passed", "failed", "selected", "skipped"})
_ALLOWED_RESULT_STATUSES = frozenset({"completed", "skipped", "failed"})
_TRUSTED_SYSTEM_SOURCE_HASHES = {
    "system-tambur-adaptive-controller": frozenset(
        {
            "3183bc1806afdadd797968bafcc7cbb738f13d80cc02c28cc42852de07c36d21",
            "baa8044bf3cbcb360a963599b164434eb7b385f4d3b9a8cd156ee901fbf6dcff",
        }
    ),
    "system-shower-comfort-controller": frozenset(
        {
            "fb775cdeb1ca327ac761f9e8e11bc5e77d1492da9d0499d2a7003257c6424f2b",
            "ef263e1adbcaa2e69ff118d14411b31892cf73497626751fbfa3106b81f2e933",
        }
    ),
}
# Release-trusted managed sources may select a branch, but cannot enlarge the
# physical envelope. Each listed device action is allowed at most once; their
# delays are exact source constants, not values supplied by Node-RED.
_SYSTEM_PLAN_ENVELOPES = {
    "system-shower-comfort-controller": {
        "actions": {
            ("entity_4be32416634e6416", "turn_on"): 1,
            ("entity_4be32416634e6416", "turn_off"): 1,
            ("entity_1fdcd8b244637246", "turn_on"): 1,
            ("entity_1fdcd8b244637246", "turn_off"): 1,
            ("entity_e7a7c61eec7bdff8", "turn_on"): 1,
            ("entity_e7a7c61eec7bdff8", "turn_off"): 1,
            ("entity_afef5df0e0cae309", "turn_on"): 1,
            ("entity_afef5df0e0cae309", "turn_off"): 1,
        },
        "delays": {120: 1, 300: 1},
        "runScenarios": {},
    },
    "system-tambur-adaptive-controller": {
        "actions": {
            ("entity_71859313239a14e4", "turn_on"): 1,
            ("entity_71859313239a14e4", "turn_off"): 1,
            ("entity_71859313239a14e4", "set_brightness_percent"): 1,
            ("entity_71859313239a14e4", "set_color_temperature"): 2,
            ("entity_fbdf27871edb89bf", "turn_on"): 1,
            ("entity_fbdf27871edb89bf", "turn_off"): 1,
        },
        "delays": {1: 2, 600: 1},
        "runScenarios": {},
    },
}
_SYSTEM_INPUT_ATTRIBUTE_ALLOWLIST = {
    "system-shower-comfort-controller": {},
    "system-tambur-adaptive-controller": {
        "entity_6b9ccdab9bb484b2": frozenset({"next_setting"}),
        "entity_71859313239a14e4": frozenset(
            {"brightness", "color_temp_kelvin", "color_temp"}
        ),
    },
}
_TRACE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_FORBIDDEN_SOURCE_PATTERNS = (
    (
        "global_context",
        re.compile(r"\bglobal\s*\.\s*(?:get|set)\s*\("),
        "Глобальный контекст Node-RED недоступен управляемому алгоритму.",
    ),
    (
        "flow_context",
        re.compile(r"\bflow\s*\.\s*(?:get|set)\s*\("),
        "Контекст вкладки Node-RED недоступен управляемому алгоритму.",
    ),
    (
        "environment",
        re.compile(r"\benv\s*\.\s*get\s*\(|\bprocess\s*\."),
        "Переменные окружения недоступны управляемому алгоритму.",
    ),
    (
        "module_access",
        re.compile(r"\brequire\s*\(|\bimport\s*\("),
        "Подключение внешних модулей запрещено.",
    ),
    (
        "network_access",
        re.compile(r"\bfetch\s*\(|\bXMLHttpRequest\b|\bhttps?\s*\.\s*request\s*\("),
        "Сетевые вызовы из функции запрещены.",
    ),
    (
        "direct_node_output",
        re.compile(r"\bnode\s*\.\s*(?:send|done)\s*\("),
        "Функция должна вернуть один проверяемый результат через return msg.",
    ),
)

RequestAdapter = Callable[
    [str, str, Mapping[str, str], Mapping[str, object] | None],
    Awaitable[tuple[int, object]],
]


class NodeRedBackendError(RuntimeError):
    """Node-RED is unavailable, inconsistent or returned unsafe data."""


class NodeRedFlowConflict(NodeRedBackendError):
    """A managed function was edited in Node-RED and must not be overwritten."""


class NodeRedSourceConflict(NodeRedBackendError):
    """The function changed after an editor loaded it."""

    def __init__(self, expected_hash: str, current_hash: str) -> None:
        super().__init__("Функция уже изменена в другом редакторе. Перечитайте исходник.")
        self.expected_hash = expected_hash
        self.current_hash = current_hash


class NodeRedSourceInvalid(NodeRedBackendError):
    """The proposed function breaks the bounded managed-source policy."""

    def __init__(self, message: str, *, code: str, line: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.line = line


def _canonical_definition(definition: ScenarioDefinition) -> dict[str, object]:
    payload = _definition_to_payload(definition)
    payload.pop("nodeRed", None)
    return payload


def compile_managed_function(scenario_id: str, definition: ScenarioDefinition) -> str:
    """Compile one compact default function that returns an action plan."""

    definition_json = json.dumps(
        _canonical_definition(definition),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "\n".join(
        (
            f"// HAUSMAN_MANAGED_SCENARIO {scenario_id}",
            "const started = Date.now();",
            "const request = (msg.payload && typeof msg.payload === 'object') ? msg.payload : {};",
            f"const definition = {definition_json};",
            "const actions = Array.isArray(definition.actions) ? definition.actions : [];",
            "msg.statusCode = 200;",
            "msg.headers = {'content-type':'application/json; charset=utf-8','cache-control':'no-store'};",
            "msg.payload = {",
            "  contract: {name: 'hausman-node-red-scenario-execution', version: 1},",
            "  correlationId: String(request.correlationId || ''),",
            f"  scenarioId: '{scenario_id}',",
            "  status: 'completed',",
            "  summary: 'Node-RED выбрал действия сценария.',",
            "  selectedBranch: 'default',",
            "  durationMs: Math.max(0, Date.now() - started),",
            "  trace: [{id:'default', title:'Основная ветка', status:'selected', actual:null, expected:null, reason:null}],",
            "  actions",
            "};",
            "return msg;",
        )
    )


def managed_source_hash(source: str) -> str:
    """Return the stable conflict hash stored with one scenario."""

    return hashlib.sha256(source.encode()).hexdigest()


def validate_managed_source(
    scenario_id: str, source: str
) -> tuple[dict[str, object], ...]:
    """Validate one editable function without executing or deploying it."""

    if not isinstance(source, str):
        raise NodeRedSourceInvalid(
            "Исходник функции должен быть текстом.", code="source_not_text"
        )
    source_bytes = source.encode("utf-8")
    if len(source_bytes) > MAX_MANAGED_SOURCE_BYTES:
        raise NodeRedSourceInvalid(
            "Исходник функции превышает 64 КиБ.", code="source_too_large"
        )
    if "\x00" in source:
        raise NodeRedSourceInvalid(
            "Исходник функции содержит недопустимый нулевой символ.",
            code="source_invalid_character",
        )
    marker = f"// HAUSMAN_MANAGED_SCENARIO {scenario_id}"
    first_line = source.splitlines()[0].strip() if source.splitlines() else ""
    if first_line != marker:
        raise NodeRedSourceInvalid(
            f"Первая строка должна быть: {marker}", code="managed_marker_missing", line=1
        )
    if NODE_RED_EXECUTION_CONTRACT not in source:
        raise NodeRedSourceInvalid(
            "Функция должна вернуть контракт hausman-node-red-scenario-execution.",
            code="execution_contract_missing",
        )
    if re.search(r"\breturn\s+msg\s*;?", source) is None:
        raise NodeRedSourceInvalid(
            "Функция должна завершаться возвратом return msg.",
            code="return_missing",
        )
    for code, pattern, message in _FORBIDDEN_SOURCE_PATTERNS:
        match = pattern.search(source)
        if match is None:
            continue
        line = source.count("\n", 0, match.start()) + 1
        raise NodeRedSourceInvalid(message, code=code, line=line)
    contract_offset = source.find(NODE_RED_EXECUTION_CONTRACT)
    return (
        {
            "id": "contract_present",
            "severity": "info",
            "line": source.count("\n", 0, contract_offset) + 1,
            "message": "Контракт результата найден.",
        },
        {
            "id": "physical_commands_owned_by_hausman",
            "severity": "info",
            "line": None,
            "message": "Функция возвращает план, физические команды выполняет Hausman.",
        },
    )


def _node_ids(scenario_id: str) -> tuple[str, str, str]:
    digest = hashlib.sha256(scenario_id.encode()).hexdigest()
    return (digest[:16], digest[16:32], digest[32:48])


def _managed_flow_id(scenario_id: str) -> str:
    """Return the server-owned, deterministic tab ID for one scenario."""

    return hashlib.sha256(f"flow:{scenario_id}".encode()).hexdigest()[:16]


def build_managed_flow(
    scenario_id: str,
    title: str,
    source: str,
    *,
    flow_id: str | None = None,
) -> dict[str, object]:
    """Build a readable three-node tab: HTTP input, function and response."""

    input_id, function_id, response_id = _node_ids(scenario_id)
    tab_id = flow_id or _managed_flow_id(scenario_id)
    endpoint = f"/hausman/scenarios/{scenario_id}"
    return {
        "id": tab_id,
        "label": f"Hausman: {title}"[:120],
        "disabled": False,
        "info": (
            "Управляемый сценарий Hausman. Логику меняйте в function-узле. "
            "Узлы команд Home Assistant сюда добавлять не нужно: действия "
            "проверяет и выполняет Hausman."
        ),
        "nodes": [
            {
                "id": input_id,
                "type": "http in",
                "z": tab_id,
                "name": f"POST {endpoint}",
                "url": endpoint,
                "method": "post",
                "upload": False,
                "swaggerDoc": "",
                "x": 170,
                "y": 120,
                "wires": [[function_id]],
            },
            {
                "id": function_id,
                "type": "function",
                "z": tab_id,
                "name": title[:120],
                "func": source,
                "outputs": 1,
                "timeout": "4",
                "noerr": 0,
                "initialize": "",
                "finalize": "",
                "libs": [],
                "x": 450,
                "y": 120,
                "wires": [[response_id]],
            },
            {
                "id": response_id,
                "type": "http response",
                "z": tab_id,
                "name": "Результат в Hausman",
                "statusCode": "",
                "headers": {},
                "x": 750,
                "y": 120,
                "wires": [],
            },
        ],
        "configs": [],
    }


def _function_source(flow: Mapping[str, object]) -> str | None:
    nodes = flow.get("nodes")
    if not isinstance(nodes, list):
        return None
    for node in nodes:
        if not isinstance(node, Mapping) or node.get("type") != "function":
            continue
        source = node.get("func")
        if isinstance(source, str) and source.startswith("// HAUSMAN_MANAGED_SCENARIO "):
            return source
    return None


def _execution_topology_hash(
    scenario_id: str,
    flow_id: str,
    flow: Mapping[str, object],
    expected_source_hash: str,
) -> str:
    """Validate the complete command-free graph and return its exact digest."""

    allowed_flow_keys = {
        "id",
        "label",
        "disabled",
        "info",
        "nodes",
        "configs",
        "subflows",
    }
    if (
        not {"id", "nodes", "configs"}.issubset(flow)
        or not set(flow).issubset(allowed_flow_keys)
        or flow.get("id") != flow_id
        or flow.get("disabled", False) is not False
        or flow.get("configs") != []
        or flow.get("subflows", []) != []
    ):
        raise NodeRedBackendError("Node-RED managed flow topology is invalid")
    nodes = flow.get("nodes")
    if not isinstance(nodes, list) or len(nodes) != 3:
        raise NodeRedBackendError("Node-RED managed flow topology is invalid")
    input_id, function_id, response_id = _node_ids(scenario_id)
    by_id: dict[str, Mapping[str, object]] = {}
    for node in nodes:
        if not isinstance(node, Mapping) or not isinstance(node.get("id"), str):
            raise NodeRedBackendError("Node-RED managed flow topology is invalid")
        node_id = str(node["id"])
        if node_id in by_id:
            raise NodeRedBackendError("Node-RED managed flow topology is invalid")
        by_id[node_id] = node
    if set(by_id) != {input_id, function_id, response_id}:
        raise NodeRedBackendError("Node-RED managed flow topology is invalid")

    input_node = by_id[input_id]
    function_node = by_id[function_id]
    response_node = by_id[response_id]
    input_keys = {
        "id", "type", "z", "name", "url", "method", "upload",
        "swaggerDoc", "x", "y", "wires",
    }
    function_keys = {
        "id", "type", "z", "name", "func", "outputs", "timeout",
        "noerr", "initialize", "finalize", "libs", "x", "y", "wires",
    }
    response_keys = {
        "id", "type", "z", "name", "statusCode", "headers", "x", "y",
        "wires",
    }
    endpoint = f"/hausman/scenarios/{scenario_id}"
    source = function_node.get("func")
    valid = (
        set(input_node) == input_keys
        and input_node.get("type") == "http in"
        and input_node.get("z") == flow_id
        and input_node.get("url") == endpoint
        and input_node.get("method") == "post"
        and input_node.get("upload") is False
        and input_node.get("swaggerDoc") == ""
        and input_node.get("wires") == [[function_id]]
        and set(function_node) == function_keys
        and function_node.get("type") == "function"
        and function_node.get("z") == flow_id
        and isinstance(source, str)
        and source.startswith(f"// HAUSMAN_MANAGED_SCENARIO {scenario_id}")
        and managed_source_hash(source) == expected_source_hash
        and function_node.get("outputs") == 1
        and function_node.get("timeout") == "4"
        and function_node.get("noerr") == 0
        and function_node.get("initialize") == ""
        and function_node.get("finalize") == ""
        and function_node.get("libs") == []
        and function_node.get("wires") == [[response_id]]
        and set(response_node) == response_keys
        and response_node.get("type") == "http response"
        and response_node.get("z") == flow_id
        and response_node.get("statusCode") == ""
        and response_node.get("headers") == {}
        and response_node.get("wires") == []
    )
    if not valid:
        raise NodeRedBackendError("Node-RED managed flow topology is invalid")
    projection = {
        "scenarioId": scenario_id,
        "flowId": flow_id,
        "sourceHash": expected_source_hash,
        "input": {
            "id": input_id,
            "url": endpoint,
            "wires": [[function_id]],
        },
        "function": {
            "id": function_id,
            "wires": [[response_id]],
        },
        "response": {"id": response_id, "wires": []},
    }
    return hashlib.sha256(
        json.dumps(
            projection,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _replace_function_source(
    flow: Mapping[str, object], scenario_id: str, source: str
) -> dict[str, object]:
    """Copy one flow and replace only its managed function body."""

    copied = json.loads(json.dumps(flow, ensure_ascii=False))
    nodes = copied.get("nodes") if isinstance(copied, dict) else None
    if not isinstance(nodes, list):
        raise NodeRedBackendError("Управляемая function-схема повреждена.")
    marker = f"// HAUSMAN_MANAGED_SCENARIO {scenario_id}"
    for node in nodes:
        if not isinstance(node, dict) or node.get("type") != "function":
            continue
        current = node.get("func")
        if isinstance(current, str) and current.startswith(marker):
            node["func"] = source
            return copied
    raise NodeRedBackendError("Управляемая function не найдена.")


class NodeRedScenarioBackend:
    """Discover, provision and execute compact Node-RED scenario tabs."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        request_adapter: RequestAdapter | None = None,
        supervisor_token: str | None = None,
        addon_slug: str = NODE_RED_ADDON_SLUG,
    ) -> None:
        self._hass = hass
        self._request_adapter = request_adapter
        self._supervisor_token = supervisor_token or os.environ.get("SUPERVISOR_TOKEN", "")
        self._addon_slug = addon_slug
        self._ingress_token: str | None = None
        self._ingress_session: str | None = None
        self._version: str | None = None
        self._last_checked_at: int | None = None
        self._last_error = "Node-RED ещё не проверен."
        self._last_prepare_operation: dict[str, object] | None = None

    async def _raw_request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, object] | None = None,
        ingress: bool = False,
    ) -> tuple[int, object]:
        headers = {"accept": "application/json"}
        if self._supervisor_token:
            headers["authorization"] = f"Bearer {self._supervisor_token}"
        if ingress:
            await self._ensure_ingress()
            if not self._ingress_token or not self._ingress_session:
                raise NodeRedBackendError("Node-RED ingress session is unavailable")
            headers["cookie"] = f"ingress_session={self._ingress_session}"
            headers["Node-RED-API-Version"] = "v2"
            if method.upper() == "POST" and path.rstrip("/") == "/flows":
                headers["Node-RED-Deployment-Type"] = "nodes"
            target = f"/ingress/{self._ingress_token}/{path.lstrip('/')}"
        else:
            target = path
        if self._request_adapter is not None:
            return await self._request_adapter(method, target, headers, payload)
        if not self._supervisor_token:
            raise NodeRedBackendError("Home Assistant Supervisor is unavailable")
        from homeassistant.helpers.aiohttp_client import async_get_clientsession  # noqa: PLC0415

        session = async_get_clientsession(self._hass)
        url = f"http://supervisor{target}"
        try:
            async with session.request(
                method,
                url,
                headers=headers,
                json=payload,
                timeout=NODE_RED_TIMEOUT_SECONDS,
            ) as response:
                try:
                    body: object = await response.json(content_type=None)
                except Exception:  # noqa: BLE001
                    body = await response.text()
                return response.status, body
        except TimeoutError as error:
            raise NodeRedBackendError("Node-RED did not answer in time") from error

    @staticmethod
    def _data(body: object) -> Mapping[str, object]:
        if not isinstance(body, Mapping):
            raise NodeRedBackendError("Node-RED returned an invalid response")
        nested = body.get("data")
        if isinstance(nested, Mapping):
            return nested
        return body

    async def _ensure_ingress(self) -> None:
        if self._ingress_token and self._ingress_session:
            return
        status, body = await self._raw_request(
            "GET", f"/addons/{self._addon_slug}/info"
        )
        if status == 404:
            raise NodeRedBackendError("Node-RED is not installed")
        if status != 200:
            raise NodeRedBackendError("Cannot read Node-RED add-on status")
        data = self._data(body)
        state = str(data.get("state", "unknown"))
        if state not in {"started", "running"}:
            raise NodeRedBackendError("Node-RED is installed but not running")
        self._version = str(data.get("version")) if data.get("version") else None
        ingress_entry = data.get("ingress_entry") or data.get("ingress_url")
        if not isinstance(ingress_entry, str) or not ingress_entry.strip("/"):
            raise NodeRedBackendError("Node-RED ingress is not available")
        self._ingress_token = ingress_entry.rstrip("/").split("/")[-1]
        status, body = await self._raw_request("POST", "/ingress/session", payload={})
        if status != 200:
            raise NodeRedBackendError("Cannot create Node-RED ingress session")
        data = self._data(body)
        session = data.get("session")
        if not isinstance(session, str) or not session:
            raise NodeRedBackendError("Node-RED ingress session is invalid")
        self._ingress_session = session

    async def async_status(
        self, scenarios: tuple[object, ...] = ()
    ) -> dict[str, object]:
        installed = running = connected = can_provision = False
        flows: list[dict[str, object]] = []
        try:
            await self._ensure_ingress()
            installed = running = True
            status, body = await self._raw_request("GET", "/flows", ingress=True)
            connected = can_provision = status == 200 and isinstance(body, Mapping)
            if not connected:
                raise NodeRedBackendError("Node-RED editor API is not available")
            self._last_error = "Node-RED доступен, управляемые flow можно создавать и обновлять."
        except NodeRedBackendError as error:
            message = str(error)
            installed = "not installed" not in message
            running = installed and "not running" not in message
            self._last_error = message
        self._last_checked_at = int(time.time() * 1000)
        for scenario in scenarios:
            definition = getattr(scenario, "definition", None)
            metadata = getattr(definition, "node_red", None)
            if not isinstance(metadata, ScenarioNodeRedMetadata) or not metadata.flow_id:
                continue
            flows.append(
                {
                    "scenarioId": str(getattr(scenario, "id", "")),
                    "title": str(getattr(scenario, "title", "")),
                    "flowId": metadata.flow_id,
                    "sourceHash": metadata.source_hash,
                    "syncStatus": metadata.sync_status.value,
                    "sourcePath": (
                        "/api/hausman_hub/v1/scenarios/node-red/source/"
                        f"{getattr(scenario, 'id', '')}"
                    ),
                    "openPath": f"/hassio/ingress/{self._addon_slug}?flow={metadata.flow_id}",
                }
            )
        return {
            "contract": {"name": NODE_RED_STATUS_CONTRACT, "version": 1},
            "available": connected,
            "installed": installed,
            "running": running,
            "connected": connected,
            "canProvision": can_provision,
            "version": self._version,
            "message": self._last_error,
            "lastCheckedAt": self._last_checked_at,
            "flows": flows,
        }

    async def async_read_source(
        self, scenario_id: str, flow_id: str
    ) -> dict[str, object]:
        """Read the exact managed function currently deployed in Node-RED."""

        status, body = await self._raw_request(
            "GET", f"/flow/{flow_id}", ingress=True
        )
        if status == 404:
            raise NodeRedBackendError("Управляемая function-схема не найдена.")
        if status != 200 or not isinstance(body, Mapping):
            raise NodeRedBackendError("Не удалось прочитать function-схему Node-RED.")
        if body.get("id") != flow_id:
            raise NodeRedBackendError("Node-RED returned an unexpected flow id")
        source = _function_source(body)
        marker = f"// HAUSMAN_MANAGED_SCENARIO {scenario_id}"
        if source is None or not source.startswith(marker):
            raise NodeRedBackendError("Function не принадлежит выбранному сценарию.")
        return {
            "flow": body,
            "source": source,
            "source_hash": managed_source_hash(source),
        }

    async def _async_flow_revision(
        self, scenario_id: str, flow_id: str
    ) -> str:
        """Verify unique endpoint ownership and return the global revision."""

        revision, _flows = await self._async_global_flow_snapshot(
            scenario_id, flow_id
        )
        return revision

    async def _async_global_snapshot(self) -> tuple[str, list[object]]:
        """Read one immutable Node-RED v2 global flow snapshot."""

        status, body = await self._raw_request("GET", "/flows", ingress=True)
        data = self._data(body)
        revision = data.get("rev")
        flows = data.get("flows")
        if (
            status != 200
            or not isinstance(revision, str)
            or not revision
            or not isinstance(flows, list)
        ):
            raise NodeRedBackendError("Node-RED flow revision is unavailable")
        return revision, json.loads(json.dumps(flows, ensure_ascii=False))

    @staticmethod
    def _validate_global_flow_ownership(
        scenario_id: str, flow_id: str, global_flows: list[object]
    ) -> None:
        """Require one exact endpoint and one copy of every managed node ID."""

        endpoint = f"/hausman/scenarios/{scenario_id}"
        owners = [
            node
            for node in global_flows
            if isinstance(node, Mapping)
            and node.get("type") == "http in"
            and node.get("url") == endpoint
        ]
        input_id, function_id, response_id = _node_ids(scenario_id)
        if (
            len(owners) != 1
            or owners[0].get("id") != input_id
            or owners[0].get("z") != flow_id
            or owners[0].get("method") != "post"
        ):
            raise NodeRedBackendError(
                "Node-RED managed endpoint ownership is invalid"
            )
        expected_ids = (input_id, function_id, response_id)
        if any(
            sum(
                1
                for node in global_flows
                if isinstance(node, Mapping) and node.get("id") == node_id
            )
            != 1
            for node_id in expected_ids
        ):
            raise NodeRedBackendError("Node-RED managed flow node ownership is invalid")

    async def _async_global_flow_snapshot(
        self, scenario_id: str, flow_id: str
    ) -> tuple[str, list[object]]:
        """Return one revision-bound global snapshot after endpoint validation."""

        revision, flows = await self._async_global_snapshot()
        self._validate_global_flow_ownership(scenario_id, flow_id, flows)
        return revision, flows

    @staticmethod
    def _validate_new_flow_snapshot(
        scenario_id: str, flow_id: str, global_flows: list[object]
    ) -> None:
        """Reject endpoint or node ID collisions before a create POST."""

        endpoint = f"/hausman/scenarios/{scenario_id}"
        if any(
            isinstance(node, Mapping)
            and node.get("type") == "http in"
            and node.get("url") == endpoint
            for node in global_flows
        ):
            raise NodeRedBackendError(
                "Node-RED managed endpoint conflicts with an existing flow"
            )
        reserved_ids = {flow_id, *_node_ids(scenario_id)}
        if any(
            isinstance(node, Mapping) and node.get("id") in reserved_ids
            for node in global_flows
        ):
            raise NodeRedBackendError(
                "Node-RED managed flow ID conflicts with an existing node"
            )

    @staticmethod
    def _created_flow_id_from_global(
        scenario_id: str,
        global_flows: list[object],
        *,
        expected_flow_id: str | None = None,
    ) -> str:
        """Find one managed tab from the flat global node list."""

        endpoint = f"/hausman/scenarios/{scenario_id}"
        input_id, function_id, response_id = _node_ids(scenario_id)
        owners = [
            node
            for node in global_flows
            if isinstance(node, Mapping)
            and node.get("type") == "http in"
            and node.get("url") == endpoint
        ]
        if len(owners) != 1 or owners[0].get("id") != input_id:
            raise NodeRedBackendError(
                "Node-RED managed endpoint ownership is ambiguous"
            )
        candidate = owners[0].get("z")
        if not isinstance(candidate, str) or not candidate:
            raise NodeRedBackendError("Node-RED returned an invalid server flow id")
        if expected_flow_id is not None and candidate != expected_flow_id:
            raise NodeRedBackendError("Node-RED server flow id does not own endpoint")
        for node_id in (input_id, function_id, response_id):
            matches = [
                node
                for node in global_flows
                if isinstance(node, Mapping) and node.get("id") == node_id
            ]
            if len(matches) != 1 or matches[0].get("z") != candidate:
                raise NodeRedBackendError(
                    "Node-RED managed flow node ownership is ambiguous"
                )
        return candidate

    async def _async_reconcile_created_flow(
        self,
        scenario_id: str,
        expected_source_hash: str,
        revision_before: str,
        *,
        server_flow_id: str | None = None,
    ) -> tuple[str, str] | None:
        """Reconcile an unclear create reply using one endpoint/global diff."""

        try:
            revision_after, global_flows = await self._async_global_snapshot()
            if revision_after == revision_before:
                return None
            try:
                flow_id = self._created_flow_id_from_global(
                    scenario_id, global_flows, expected_flow_id=server_flow_id
                )
            except NodeRedBackendError:
                # The POST reply can be lost or malformed even though the
                # server committed one uniquely-owned graph. The exact global
                # endpoint and all deterministic node IDs are the authority.
                flow_id = self._created_flow_id_from_global(
                    scenario_id, global_flows
                )
            deployed = await self.async_read_source(scenario_id, flow_id)
            _execution_topology_hash(
                scenario_id, flow_id, deployed["flow"], expected_source_hash
            )
        except Exception:  # noqa: BLE001
            return None
        return flow_id, revision_after

    async def _async_compensate_created_flow_after_failure(
        self,
        scenario_id: str,
        expected_source_hash: str,
    ) -> None:
        """Remove a partially-created graph when its create cannot be accepted."""

        revision, global_flows = await self._async_global_snapshot()
        endpoint = f"/hausman/scenarios/{scenario_id}"
        owners = [
            node
            for node in global_flows
            if isinstance(node, Mapping)
            and node.get("type") == "http in"
            and node.get("url") == endpoint
        ]
        if not owners:
            return
        owner_id = owners[0].get("z") if len(owners) == 1 else None
        if not isinstance(owner_id, str) or not owner_id:
            raise NodeRedBackendError(
                "Node-RED create compensation owner is ambiguous"
            )
        # The uniquely-owned endpoint in the post-create global snapshot is
        # authoritative. A malformed POST reply must never redirect cleanup to
        # an unrelated or non-existent flow ID.
        delete_id = owner_id
        placeholder_id = _managed_flow_id(scenario_id)
        await self.async_delete_managed_flow(
            scenario_id,
            delete_id,
            expected_source_hash=expected_source_hash,
            expected_global_revision=revision,
            placeholder_flow_id=placeholder_id,
        )

    async def _async_create_managed_flow(
        self, scenario_id: str, title: str, source: str, expected_source_hash: str
    ) -> tuple[str, int, str]:
        """Create one server-owned managed flow without retries."""

        provisional_id = _managed_flow_id(scenario_id)
        revision_before, global_flows = await self._async_global_snapshot()
        self._validate_new_flow_snapshot(scenario_id, provisional_id, global_flows)
        managed = build_managed_flow(
            scenario_id, title, source, flow_id=provisional_id
        )
        managed.pop("id", None)

        async def fail_create(
            error: Exception, *, server_flow_id: str | None = None
        ) -> tuple[str, int, str]:
            reconciled = await self._async_reconcile_created_flow(
                scenario_id,
                expected_source_hash,
                revision_before,
                server_flow_id=server_flow_id,
            )
            if reconciled is not None:
                flow_id, revision_after = reconciled
                return flow_id, 1, revision_after
            try:
                await self._async_compensate_created_flow_after_failure(
                    scenario_id,
                    expected_source_hash,
                )
            except NodeRedBackendError as compensation_error:
                raise NodeRedBackendError(
                    "Node-RED flow creation is ambiguous and compensation failed; "
                    "operator recovery is required"
                ) from compensation_error
            raise NodeRedBackendError(
                "Node-RED flow creation result is ambiguous; recovery is required"
            ) from error

        try:
            status, body = await self._raw_request(
                "POST", "/flow", payload=managed, ingress=True
            )
        except Exception as error:  # noqa: BLE001
            return await fail_create(error)
        if status not in {200, 201}:
            return await fail_create(NodeRedBackendError("Node-RED flow creation failed"))
        candidate = self._data(body).get("id") if isinstance(body, Mapping) else None
        if not isinstance(candidate, str) or not candidate:
            return await fail_create(
                NodeRedBackendError("Node-RED did not return a server-owned flow ID")
            )
        try:
            revision_after, global_flows_after = await self._async_global_snapshot()
            if revision_after == revision_before:
                raise NodeRedBackendError(
                    "Node-RED did not advance the global flow revision"
                )
            server_flow_id = self._created_flow_id_from_global(
                scenario_id, global_flows_after, expected_flow_id=candidate
            )
            deployed = await self.async_read_source(scenario_id, server_flow_id)
            _execution_topology_hash(
                scenario_id, server_flow_id, deployed["flow"], expected_source_hash
            )
            return server_flow_id, 1, revision_after
        except Exception as error:  # noqa: BLE001
            return await fail_create(error, server_flow_id=candidate)

    @staticmethod
    def _global_source_update_payload(
        scenario_id: str,
        flow_id: str,
        flow: Mapping[str, object],
        global_flows: list[object],
        revision: str,
    ) -> dict[str, object]:
        """Replace exactly the three managed nodes in a v2 revisioned snapshot."""

        nodes = flow.get("nodes")
        if not isinstance(nodes, list):
            raise NodeRedBackendError("Node-RED managed flow topology is invalid")
        replacements = {
            str(node.get("id")): dict(node)
            for node in nodes
            if isinstance(node, Mapping) and isinstance(node.get("id"), str)
        }
        expected_ids = set(_node_ids(scenario_id))
        if set(replacements) != expected_ids:
            raise NodeRedBackendError("Node-RED managed flow topology is invalid")
        counts = {
            node_id: sum(
                1
                for item in global_flows
                if isinstance(item, Mapping) and item.get("id") == node_id
            )
            for node_id in expected_ids
        }
        if any(count != 1 for count in counts.values()):
            raise NodeRedBackendError("Node-RED managed flow topology is invalid")
        updated = [
            replacements[str(item["id"])]
            if isinstance(item, Mapping) and item.get("id") in replacements
            else copy.deepcopy(item)
            for item in global_flows
        ]
        return {"rev": revision, "flows": updated}

    async def _async_compensate_ambiguous_source_update(
        self,
        scenario_id: str,
        flow_id: str,
        *,
        previous_source: str,
        previous_hash: str,
        previous_topology: str,
        proposed_hash: str,
        proposed_topology: str,
        cause: Exception,
    ) -> None:
        """Restore only a fully recognized applied update after an unclear reply."""

        try:
            deployed = await self.async_read_source(scenario_id, flow_id)
            deployed_hash = str(deployed["source_hash"])
            deployed_topology = _execution_topology_hash(
                scenario_id, flow_id, deployed["flow"], deployed_hash
            )
            await self._async_global_flow_snapshot(scenario_id, flow_id)
        except Exception as inspection_error:  # noqa: BLE001
            raise NodeRedBackendError(
                "Node-RED source update outcome is ambiguous; operator recovery "
                "is required."
            ) from inspection_error
        if (
            deployed_hash == proposed_hash
            and deployed_topology == proposed_topology
        ):
            await self.async_restore_source(
                scenario_id,
                flow_id,
                previous_source,
                expected_current_hash=proposed_hash,
            )
            raise NodeRedBackendError(
                "Node-RED returned an ambiguous update result; the previous "
                "source was restored."
            ) from cause
        if (
            deployed_hash == previous_hash
            and deployed_topology == previous_topology
        ):
            raise NodeRedBackendError(
                "Node-RED rejected the source update without changing the flow."
            ) from cause
        raise NodeRedBackendError(
            "Node-RED source update outcome is ambiguous; operator recovery is "
            "required."
        ) from cause

    async def async_update_source(
        self,
        scenario_id: str,
        definition: ScenarioDefinition,
        flow_id: str,
        source: str,
        expected_source_hash: str,
        catalog: ScenarioCatalog | None,
        *,
        validate_only: bool,
    ) -> dict[str, object]:
        """Statically validate and optionally deploy one managed function."""

        del catalog

        revision_before, global_flows = await self._async_global_flow_snapshot(
            scenario_id, flow_id
        )
        current = await self.async_read_source(scenario_id, flow_id)
        current_hash = str(current["source_hash"])
        topology_before = _execution_topology_hash(
            scenario_id, flow_id, current["flow"], current_hash
        )
        if current_hash != expected_source_hash:
            raise NodeRedSourceConflict(expected_source_hash, current_hash)
        diagnostics = validate_managed_source(scenario_id, source)
        proposed_hash = managed_source_hash(source)
        canonical_hash = managed_source_hash(
            compile_managed_function(scenario_id, definition)
        )
        trusted_hashes = _TRUSTED_SYSTEM_SOURCE_HASHES.get(scenario_id, frozenset())
        if proposed_hash != canonical_hash and proposed_hash not in trusted_hashes:
            raise NodeRedSourceInvalid(
                "Исходник не входит в подписанный набор этого выпуска.",
                code="source_not_release_trusted",
            )
        if validate_only:
            revision_after, _ = await self._async_global_flow_snapshot(
                scenario_id, flow_id
            )
            verified = await self.async_read_source(scenario_id, flow_id)
            verified_hash = str(verified["source_hash"])
            if (
                revision_after != revision_before
                or verified_hash != current_hash
                or _execution_topology_hash(
                    scenario_id, flow_id, verified["flow"], verified_hash
                )
                != topology_before
            ):
                raise NodeRedBackendError(
                    "Node-RED flow changed during source validation"
                )
            return {
                "saved": False,
                "current_source_hash": current_hash,
                "proposed_source_hash": proposed_hash,
                "diagnostics": diagnostics,
                "verification": None,
                "previous_source": current["source"],
            }
        if proposed_hash == current_hash:
            revision_after, _ = await self._async_global_flow_snapshot(
                scenario_id, flow_id
            )
            verified = await self.async_read_source(scenario_id, flow_id)
            verified_hash = str(verified["source_hash"])
            if (
                revision_after != revision_before
                or verified_hash != current_hash
                or _execution_topology_hash(
                    scenario_id, flow_id, verified["flow"], verified_hash
                )
                != topology_before
            ):
                raise NodeRedBackendError(
                    "Node-RED flow changed during source verification"
                )
            return {
                "saved": False,
                "current_source_hash": current_hash,
                "proposed_source_hash": proposed_hash,
                "diagnostics": diagnostics,
                "verification": None,
                "previous_source": current["source"],
            }

        updated_flow = _replace_function_source(
            current["flow"], scenario_id, source
        )
        _execution_topology_hash(
            scenario_id, flow_id, updated_flow, proposed_hash
        )
        revision_at_dispatch, dispatch_flows = (
            await self._async_global_flow_snapshot(scenario_id, flow_id)
        )
        dispatch_current = await self.async_read_source(scenario_id, flow_id)
        dispatch_hash = str(dispatch_current["source_hash"])
        if (
            revision_at_dispatch != revision_before
            or dispatch_hash != current_hash
            or _execution_topology_hash(
                scenario_id, flow_id, dispatch_current["flow"], dispatch_hash
            )
            != topology_before
        ):
            raise NodeRedBackendError("Node-RED flow changed before source update")
        update_payload = self._global_source_update_payload(
            scenario_id,
            flow_id,
            updated_flow,
            dispatch_flows,
            revision_at_dispatch,
        )
        proposed_topology = _execution_topology_hash(
            scenario_id, flow_id, updated_flow, proposed_hash
        )
        try:
            status, update_result = await self._raw_request(
                "POST", "/flows", payload=update_payload, ingress=True
            )
        except Exception as error:  # noqa: BLE001
            await self._async_compensate_ambiguous_source_update(
                scenario_id,
                flow_id,
                previous_source=str(current["source"]),
                previous_hash=current_hash,
                previous_topology=topology_before,
                proposed_hash=proposed_hash,
                proposed_topology=proposed_topology,
                cause=error,
            )
        update_data = self._data(update_result) if isinstance(update_result, Mapping) else {}
        returned_revision = update_data.get("rev")
        if (
            status != 200
            or not isinstance(returned_revision, str)
            or not returned_revision
        ):
            await self._async_compensate_ambiguous_source_update(
                scenario_id,
                flow_id,
                previous_source=str(current["source"]),
                previous_hash=current_hash,
                previous_topology=topology_before,
                proposed_hash=proposed_hash,
                proposed_topology=proposed_topology,
                cause=NodeRedBackendError(
                    "Node-RED did not confirm the new flow revision"
                ),
            )
        try:
            deployed = await self.async_read_source(scenario_id, flow_id)
            deployed_hash = str(deployed["source_hash"])
            deployed_topology = _execution_topology_hash(
                scenario_id, flow_id, deployed["flow"], deployed_hash
            )
            revision_after, _ = await self._async_global_flow_snapshot(
                scenario_id, flow_id
            )
            if (
                deployed_hash != proposed_hash
                or deployed_topology
                != proposed_topology
                or revision_after == revision_before
                or revision_after != returned_revision
            ):
                raise NodeRedBackendError(
                    "Node-RED flow verification after source update failed."
                )
        except Exception as error:  # noqa: BLE001
            await self.async_restore_source(
                scenario_id,
                flow_id,
                str(current["source"]),
                expected_current_hash=proposed_hash,
            )
            if isinstance(error, NodeRedBackendError):
                raise
            raise NodeRedBackendError(
                "Проверочный запуск функции не прошёл, прежний исходник восстановлен."
            ) from error
        return {
            "saved": True,
            "current_source_hash": proposed_hash,
            "proposed_source_hash": proposed_hash,
            "diagnostics": diagnostics,
            "verification": None,
            "previous_source": current["source"],
        }

    async def async_restore_source(
        self,
        scenario_id: str,
        flow_id: str,
        source: str,
        *,
        expected_current_hash: str,
    ) -> None:
        """Restore an exact previous source after a later persistence failure."""

        revision_before, global_flows = await self._async_global_flow_snapshot(
            scenario_id, flow_id
        )
        current = await self.async_read_source(scenario_id, flow_id)
        if current["source_hash"] != expected_current_hash:
            raise NodeRedSourceConflict(
                expected_current_hash, str(current["source_hash"])
            )
        restored_flow = _replace_function_source(
            current["flow"], scenario_id, source
        )
        restored_hash = managed_source_hash(source)
        _execution_topology_hash(
            scenario_id, flow_id, current["flow"], expected_current_hash
        )
        _execution_topology_hash(
            scenario_id, flow_id, restored_flow, restored_hash
        )
        status, restore_result = await self._raw_request(
            "POST",
            "/flows",
            payload=self._global_source_update_payload(
                scenario_id,
                flow_id,
                restored_flow,
                global_flows,
                revision_before,
            ),
            ingress=True,
        )
        restore_data = (
            self._data(restore_result) if isinstance(restore_result, Mapping) else {}
        )
        restore_revision = restore_data.get("rev")
        if (
            status != 200
            or not isinstance(restore_revision, str)
            or not restore_revision
        ):
            raise NodeRedBackendError("Не удалось восстановить прежний исходник.")
        restored = await self.async_read_source(scenario_id, flow_id)
        revision_after, _ = await self._async_global_flow_snapshot(
            scenario_id, flow_id
        )
        if (
            restored["source_hash"] != restored_hash
            or revision_after == revision_before
            or revision_after != restore_revision
            or _execution_topology_hash(
                scenario_id, flow_id, restored["flow"], restored_hash
            )
            != _execution_topology_hash(
                scenario_id, flow_id, restored_flow, restored_hash
            )
        ):
            raise NodeRedBackendError(
                "Контрольная сумма восстановленного исходника не совпала."
            )

    async def async_delete_managed_flow(
        self,
        scenario_id: str,
        flow_id: str,
        *,
        expected_source_hash: str,
        expected_global_revision: str | None = None,
        placeholder_flow_id: str | None = None,
    ) -> None:
        """Delete a just-created flow only after exact ownership checks."""

        revision, global_flows = await self._async_global_snapshot()
        endpoint = f"/hausman/scenarios/{scenario_id}"
        input_id, function_id, response_id = _node_ids(scenario_id)
        owners = [
            node
            for node in global_flows
            if isinstance(node, Mapping)
            and node.get("type") == "http in"
            and node.get("url") == endpoint
        ]
        managed_ids = (input_id, function_id, response_id)
        if not owners and not any(
            isinstance(node, Mapping) and node.get("id") in managed_ids
            for node in global_flows
        ):
            return
        if expected_global_revision is not None and revision != expected_global_revision:
            raise NodeRedBackendError(
                "Node-RED flow changed before safe compensation deletion"
            )
        expected_z = {flow_id}
        if placeholder_flow_id:
            expected_z.add(placeholder_flow_id)
        if (
            len(owners) != 1
            or owners[0].get("id") != input_id
            or owners[0].get("z") not in expected_z
        ):
            raise NodeRedBackendError(
                "Node-RED create compensation ownership is ambiguous"
            )
        for node_id in managed_ids:
            matches = [
                node
                for node in global_flows
                if isinstance(node, Mapping) and node.get("id") == node_id
            ]
            if len(matches) != 1 or matches[0].get("z") not in expected_z:
                raise NodeRedBackendError(
                    "Node-RED create compensation node ownership is ambiguous"
                )
        current = await self.async_read_source(scenario_id, flow_id)
        current_flow = copy.deepcopy(dict(current["flow"]))
        nodes = current_flow.get("nodes")
        if not isinstance(nodes, list):
            raise NodeRedBackendError("Node-RED managed flow topology is invalid")
        if current_flow.get("id") != flow_id:
            raise NodeRedBackendError("Node-RED create compensation flow ID changed")
        for node in nodes:
            if isinstance(node, dict) and node.get("id") in managed_ids:
                node["z"] = flow_id
        if str(current.get("source_hash")) != expected_source_hash:
            raise NodeRedBackendError(
                "Node-RED create compensation source or topology is ambiguous"
            )
        _execution_topology_hash(
            scenario_id, flow_id, current_flow, expected_source_hash
        )
        status, _ = await self._raw_request(
            "DELETE", f"/flow/{flow_id}", ingress=True
        )
        if status not in {200, 202, 204}:
            raise NodeRedBackendError("Node-RED managed flow compensation failed")
        revision_after, remaining = await self._async_global_snapshot()
        remaining_endpoint = [
            node
            for node in remaining
            if isinstance(node, Mapping)
            and node.get("type") == "http in"
            and node.get("url") == endpoint
        ]
        remaining_managed = [
            node
            for node in remaining
            if isinstance(node, Mapping) and node.get("id") in managed_ids
        ]
        if revision_after == revision or remaining_endpoint or remaining_managed:
            raise NodeRedBackendError(
                "Node-RED managed flow compensation outcome is ambiguous"
            )

    async def async_compensate_last_prepare(self) -> None:
        """Compensate a Node-RED mutation before registry persistence failed."""

        operation = self._last_prepare_operation
        if not isinstance(operation, Mapping):
            return
        scenario_id = operation.get("scenarioId")
        flow_id = operation.get("flowId")
        source_hash = operation.get("sourceHash")
        revision = operation.get("globalRevision")
        if not all(isinstance(value, str) and value for value in (scenario_id, flow_id, source_hash)):
            raise NodeRedBackendError("Node-RED prepare compensation metadata is invalid")
        if operation.get("kind") == "create":
            await self.async_delete_managed_flow(
                scenario_id,
                flow_id,
                expected_source_hash=source_hash,
                expected_global_revision=revision if isinstance(revision, str) else None,
                placeholder_flow_id=_managed_flow_id(scenario_id),
            )
        elif operation.get("kind") == "update":
            previous_source = operation.get("previousSource")
            if not isinstance(previous_source, str):
                raise NodeRedBackendError(
                    "Node-RED update compensation metadata is invalid"
                )
            await self.async_restore_source(
                scenario_id,
                flow_id,
                previous_source,
                expected_current_hash=source_hash,
            )
        else:
            raise NodeRedBackendError("Node-RED prepare compensation kind is invalid")
        self._last_prepare_operation = None

    async def async_commit_last_prepare(self) -> None:
        """Forget compensation metadata after registry persistence succeeds."""

        self._last_prepare_operation = None

    async def async_prepare(
        self,
        scenario_id: str,
        title: str,
        definition: ScenarioDefinition,
        *,
        previous: ScenarioNodeRedMetadata | None = None,
    ) -> ScenarioDefinition:
        """Create/update one tab without overwriting manual function edits."""

        self._last_prepare_operation = None
        requested_metadata = definition.node_red
        if requested_metadata is None:
            raise NodeRedBackendError("Node-RED metadata is missing")
        source = compile_managed_function(scenario_id, definition)
        expected_hash = managed_source_hash(source)
        if previous is None:
            metadata = ScenarioNodeRedMetadata(
                generated_by=ScenarioNodeRedGeneratedBy.HAUSMAN,
                sync_status=ScenarioNodeRedSyncStatus.PENDING,
                input_target_ids=requested_metadata.input_target_ids,
            )
            flow_id, revision, global_revision = await self._async_create_managed_flow(
                scenario_id, title, source, expected_hash
            )
            self._last_prepare_operation = {
                "kind": "create",
                "scenarioId": scenario_id,
                "flowId": flow_id,
                "sourceHash": expected_hash,
                "globalRevision": global_revision,
            }
        else:
            metadata = replace(
                previous,
                input_target_ids=requested_metadata.input_target_ids,
            )
            flow_id = previous.flow_id
            if not flow_id:
                flow_id, revision, global_revision = await self._async_create_managed_flow(
                    scenario_id, title, source, expected_hash
                )
                self._last_prepare_operation = {
                    "kind": "create",
                    "scenarioId": scenario_id,
                    "flowId": flow_id,
                    "sourceHash": expected_hash,
                    "globalRevision": global_revision,
                }
            else:
                if (
                    previous.sync_status is not ScenarioNodeRedSyncStatus.SYNCED
                    or not previous.source_hash
                ):
                    raise NodeRedBackendError(
                        "Existing Node-RED flow metadata is not trusted"
                    )
                current = await self.async_read_source(scenario_id, flow_id)
                actual_hash = str(current["source_hash"])
                if actual_hash != previous.source_hash:
                    return replace(
                        definition,
                        node_red=replace(
                            metadata,
                            source_hash=previous.source_hash,
                            sync_status=ScenarioNodeRedSyncStatus.CHANGED,
                        ),
                    )
                _execution_topology_hash(
                    scenario_id, flow_id, current["flow"], actual_hash
                )
                if actual_hash != expected_hash:
                    result = await self.async_update_source(
                        scenario_id,
                        definition,
                        flow_id,
                        source,
                        previous.source_hash,
                        None,
                        validate_only=False,
                    )
                    if result.get("saved") is not True:
                        raise NodeRedBackendError(
                            "Node-RED trusted flow update was not saved"
                        )
                    revision = max(1, previous.flow_revision + 1)
                    self._last_prepare_operation = {
                        "kind": "update",
                        "scenarioId": scenario_id,
                        "flowId": flow_id,
                        "sourceHash": expected_hash,
                        "previousSource": result.get("previous_source"),
                    }
                else:
                    await self._async_global_flow_snapshot(scenario_id, flow_id)
                    revision = max(1, previous.flow_revision)
        return replace(
            definition,
            node_red=replace(
                metadata,
                flow_id=flow_id,
                flow_revision=revision,
                source_hash=expected_hash,
                sync_status=ScenarioNodeRedSyncStatus.SYNCED,
            ),
        )

    def _input_snapshot(
        self, scenario_id: str, definition: ScenarioDefinition, catalog: ScenarioCatalog
    ) -> dict[str, object]:
        metadata = definition.node_red
        target_ids = metadata.input_target_ids if metadata is not None else ()
        result: dict[str, object] = {}
        for target_id in target_ids:
            device = catalog.device(target_id)
            if device is None:
                result[target_id] = {"state": None, "attributes": {}}
                continue
            state = self._hass.states.get(device.entity_id)
            attributes = getattr(state, "attributes", {}) if state is not None else {}
            allowed_attributes = _SYSTEM_INPUT_ATTRIBUTE_ALLOWLIST.get(
                scenario_id, {}
            ).get(target_id, frozenset())
            safe_attributes = {
                str(key): value
                for key, value in attributes.items()
                if key in allowed_attributes and isinstance(value, (str, int, float, bool))
            }
            result[target_id] = {
                "state": getattr(state, "state", None),
                "attributes": dict(list(safe_attributes.items())[:64]),
            }
        return result

    async def async_plan(
        self,
        scenario_id: str,
        definition: ScenarioDefinition,
        run_id: str,
        catalog: ScenarioCatalog,
        *,
        dry_run: bool,
    ) -> tuple[tuple[ScenarioAction, ...], dict[str, object]]:
        metadata = definition.node_red
        if metadata is None or not metadata.flow_id:
            raise NodeRedBackendError("Node-RED flow is not synchronized")
        if (
            metadata.sync_status is not ScenarioNodeRedSyncStatus.SYNCED
            or not metadata.source_hash
        ):
            raise NodeRedBackendError("Node-RED source is not trusted for execution")
        revision_before = await self._async_flow_revision(
            scenario_id, metadata.flow_id
        )
        current = await self.async_read_source(scenario_id, metadata.flow_id)
        current_hash = str(current["source_hash"])
        if current_hash != metadata.source_hash:
            raise NodeRedBackendError("Node-RED source changed before execution")
        trusted_hashes = _TRUSTED_SYSTEM_SOURCE_HASHES.get(scenario_id, frozenset())
        if trusted_hashes and current_hash not in trusted_hashes:
            raise NodeRedBackendError(
                "Node-RED system source is not release-trusted"
            )
        topology_hash = _execution_topology_hash(
            scenario_id,
            metadata.flow_id,
            current["flow"],
            current_hash,
        )
        payload = {
            "correlationId": run_id,
            "scenarioId": scenario_id,
            "dryRun": dry_run,
            "inputs": self._input_snapshot(scenario_id, definition, catalog),
            "context": {"timestampMs": int(time.time() * 1000)},
        }
        status, body = await self._raw_request(
            "POST",
            f"/{NODE_RED_ENDPOINT_PREFIX}/{scenario_id}",
            payload=payload,
            ingress=True,
        )
        verified = await self.async_read_source(scenario_id, metadata.flow_id)
        verified_hash = str(verified["source_hash"])
        verified_topology_hash = _execution_topology_hash(
            scenario_id,
            metadata.flow_id,
            verified["flow"],
            verified_hash,
        )
        revision_after = await self._async_flow_revision(
            scenario_id, metadata.flow_id
        )
        if (
            revision_after != revision_before
            or verified_hash != current_hash
            or verified_topology_hash != topology_hash
        ):
            raise NodeRedBackendError("Node-RED flow changed during execution")
        if status != 200 or not isinstance(body, Mapping):
            raise NodeRedBackendError("Node-RED scenario execution failed")
        contract = body.get("contract")
        if not isinstance(contract, Mapping) or contract.get("name") != NODE_RED_EXECUTION_CONTRACT or contract.get("version") != 1:
            raise NodeRedBackendError("Node-RED returned an unsupported contract")
        if body.get("correlationId") != run_id or body.get("scenarioId") != scenario_id:
            raise NodeRedBackendError("Node-RED correlation evidence does not match")
        raw_actions = body.get("actions")
        if not isinstance(raw_actions, list) or len(raw_actions) > MAX_RETURNED_ACTIONS:
            raise NodeRedBackendError("Node-RED returned too many actions")
        actions: list[ScenarioAction] = []
        for index, raw_action in enumerate(raw_actions):
            try:
                action = _action_from_payload(raw_action, f"Node-RED action {index}")
            except ScenarioViolation as error:
                raise NodeRedBackendError(
                    f"Node-RED action {index} does not match the contract"
                ) from error
            if action.type not in {
                ScenarioActionType.DEVICE_ACTION,
                ScenarioActionType.DELAY,
                ScenarioActionType.RUN_SCENARIO,
                ScenarioActionType.NOTIFICATION,
            }:
                raise NodeRedBackendError("Node-RED returned a forbidden action type")
            actions.append(action)
        self._validate_plan_envelope(scenario_id, definition, actions)
        trace = body.get("trace")
        if not isinstance(trace, list) or len(trace) > 64:
            raise NodeRedBackendError("Node-RED trace is invalid")
        safe_trace: list[dict[str, object]] = []
        for index, item in enumerate(trace):
            if not isinstance(item, Mapping):
                raise NodeRedBackendError("Node-RED trace item is invalid")
            trace_id = item.get("id")
            title = item.get("title")
            trace_status = item.get("status")
            if (
                not isinstance(trace_id, str)
                or _TRACE_ID.fullmatch(trace_id) is None
                or not isinstance(title, str)
                or not title
                or len(title) > 240
                or trace_status not in _ALLOWED_TRACE_STATUSES
            ):
                raise NodeRedBackendError(
                    f"Node-RED trace item {index} does not match the contract"
                )
            safe_item: dict[str, object] = {
                "id": trace_id,
                "title": title,
                "status": trace_status,
            }
            for key in ("actual", "expected"):
                value = item.get(key)
                if value is None or isinstance(value, (str, int, float, bool)):
                    safe_item[key] = value
                else:
                    raise NodeRedBackendError("Node-RED trace value is invalid")
            reason = item.get("reason")
            if reason is not None and not isinstance(reason, str):
                raise NodeRedBackendError("Node-RED trace reason is invalid")
            safe_item["reason"] = reason[:500] if isinstance(reason, str) else None
            safe_trace.append(safe_item)
        result_status = body.get("status")
        if result_status not in _ALLOWED_RESULT_STATUSES:
            raise NodeRedBackendError("Node-RED result status is invalid")
        selected_branch = body.get("selectedBranch")
        if selected_branch is not None and not isinstance(selected_branch, str):
            raise NodeRedBackendError("Node-RED selected branch is invalid")
        try:
            duration_ms = int(body.get("durationMs", 0))
        except (TypeError, ValueError) as error:
            raise NodeRedBackendError("Node-RED duration is invalid") from error
        result = {
            "status": result_status,
            "summary": str(body.get("summary", "Node-RED завершил расчёт."))[:500],
            "selectedBranch": selected_branch[:120] if selected_branch else None,
            "durationMs": max(0, min(duration_ms, 10_000)),
            "trace": safe_trace,
        }
        return tuple(actions), result

    @staticmethod
    def _validate_plan_envelope(
        scenario_id: str, definition: ScenarioDefinition, actions: list[ScenarioAction]
    ) -> None:
        if scenario_id not in _SYSTEM_PLAN_ENVELOPES:
            _validate_definition_subsequence(definition, actions)
            return
        _validate_system_branch(scenario_id, actions)
        envelope = _SYSTEM_PLAN_ENVELOPES[scenario_id]
        counts: dict[tuple[str, object], int] = {}
        for action in actions:
            if action.type is ScenarioActionType.DEVICE_ACTION:
                key = (str(action.target_id), str(action.action_id))
                limit = envelope["actions"].get(key)
                counts[("action", key)] = counts.get(("action", key), 0) + 1
                if limit is None or counts[("action", key)] > limit:
                    raise NodeRedBackendError("Node-RED action exceeds the release-trusted envelope")
            elif action.type is ScenarioActionType.DELAY:
                key = action.delay_seconds
                limit = envelope["delays"].get(key)
                counts[("delay", key)] = counts.get(("delay", key), 0) + 1
                if limit is None or counts[("delay", key)] > limit:
                    raise NodeRedBackendError("Node-RED delay exceeds the release-trusted envelope")
            elif action.type is ScenarioActionType.RUN_SCENARIO:
                target = str(action.scenario_id)
                limit = envelope["runScenarios"].get(target)
                counts[("runScenario", target)] = counts.get(("runScenario", target), 0) + 1
                if limit is None or counts[("runScenario", target)] > limit:
                    raise NodeRedBackendError("Node-RED nested scenario exceeds the release-trusted envelope")
            else:
                raise NodeRedBackendError("Node-RED action type exceeds the release-trusted envelope")


def _definition_envelope(definition: ScenarioDefinition) -> dict[str, dict[object, int]]:
    """Derive the only actions a non-system managed source may return."""

    envelope: dict[str, dict[object, int]] = {
        "actions": {}, "delays": {}, "runScenarios": {}
    }
    for action in definition.actions:
        if action.type is ScenarioActionType.DEVICE_ACTION:
            key = (str(action.target_id), str(action.action_id))
            envelope["actions"][key] = envelope["actions"].get(key, 0) + 1
        elif action.type is ScenarioActionType.DELAY:
            key = action.delay_seconds
            envelope["delays"][key] = envelope["delays"].get(key, 0) + 1
        elif action.type is ScenarioActionType.RUN_SCENARIO:
            key = str(action.scenario_id)
            envelope["runScenarios"][key] = envelope["runScenarios"].get(key, 0) + 1
    return envelope


def _action_signature(action: ScenarioAction) -> tuple[object, ...]:
    """Complete canonical identity for a server-authored action."""

    value = action.value
    if isinstance(value, float) and not math.isfinite(value):
        raise NodeRedBackendError("Node-RED action value is not finite")
    return (
        action.id,
        action.type.value,
        action.target_id,
        action.scenario_id,
        action.action_id,
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False),
        action.message,
        action.delay_seconds,
    )


def _validate_definition_subsequence(
    definition: ScenarioDefinition, actions: list[ScenarioAction]
) -> None:
    """Generic managed plans are an exact server-authored definition."""

    expected = [_action_signature(action) for action in definition.actions]
    actual = [_action_signature(action) for action in actions]
    if actual != expected:
        raise NodeRedBackendError("Node-RED plan is not the exact server-authored definition")


def _validate_system_branch(scenario_id: str, actions: list[ScenarioAction]) -> None:
    """Validate the ordered, release-authored branches of both controllers."""

    def device(action: ScenarioAction, ident: str, target: str, action_id: str, value: object = None) -> bool:
        return (
            action.id == ident and action.type is ScenarioActionType.DEVICE_ACTION
            and action.target_id == target and action.action_id == action_id
            and action.value == value and action.scenario_id is None and action.message is None
        )

    def delay(action: ScenarioAction, ident: str, seconds: int) -> bool:
        return (
            action.id == ident and action.type is ScenarioActionType.DELAY
            and action.delay_seconds == seconds and action.target_id is None
            and action.action_id is None and action.value is None and action.message is None
        )

    if scenario_id == "system-tambur-adaptive-controller":
        chandelier = "entity_71859313239a14e4"
        mirror = "entity_fbdf27871edb89bf"
        if not actions:
            return
        if delay(actions[0], "absence_wait", 600):
            expected = [actions[0]]
            for ident, target in (("chandelier_off", chandelier), ("mirror_off", mirror)):
                if len(expected) < len(actions) and device(actions[len(expected)], ident, target, "turn_off"):
                    expected.append(actions[len(expected)])
            if len(expected) == len(actions) and len(expected) > 1:
                return
            raise NodeRedBackendError("Node-RED tambur absence branch exceeds release source")
        if device(actions[0], "mirror_on", mirror, "turn_on"):
            if len(actions) == 1:
                return
            if len(actions) == 3 and delay(actions[1], "mirror_handoff_wait", 1) and device(actions[2], "chandelier_off", chandelier, "turn_off"):
                return
            raise NodeRedBackendError("Node-RED tambur night branch exceeds release source")
        if not (len(actions) >= 2 and device(actions[0], "chandelier_on", chandelier, "turn_on") and delay(actions[1], "chandelier_ownership_wait", 1)):
            raise NodeRedBackendError("Node-RED tambur profile prefix exceeds release source")
        cursor = 2
        brightness: int | None = None
        target_kelvin: int | None = None
        if cursor < len(actions) and actions[cursor].id == "brightness":
            action = actions[cursor]
            if not device(action, "brightness", chandelier, "set_brightness_percent", action.value) or type(action.value) is not int:
                raise NodeRedBackendError("Node-RED tambur brightness exceeds release source")
            brightness = action.value
            cursor += 1
        if cursor < len(actions) and actions[cursor].id == "temperature_prime":
            prime = actions[cursor]
            if not device(prime, "temperature_prime", chandelier, "set_color_temperature", prime.value) or type(prime.value) is not int:
                raise NodeRedBackendError("Node-RED tambur prime exceeds release source")
            cursor += 1
            if cursor >= len(actions) or not delay(actions[cursor], "temperature_wait_1", 1):
                raise NodeRedBackendError("Node-RED tambur prime wait exceeds release source")
            cursor += 1
            if cursor >= len(actions) or actions[cursor].id != "temperature_target":
                raise NodeRedBackendError("Node-RED tambur prime target is missing")
            target_kelvin = actions[cursor].value if type(actions[cursor].value) is int else None
            if target_kelvin is None or not device(actions[cursor], "temperature_target", chandelier, "set_color_temperature", target_kelvin) or prime.value != {6500: 6400, 6000: 6100, 5200: 5300, 4400: 4500, 3600: 3700, 2800: 2900, 2200: 2300}.get(target_kelvin):
                raise NodeRedBackendError("Node-RED tambur prime target exceeds release source")
            cursor += 1
        elif cursor < len(actions) and actions[cursor].id == "temperature_target":
            target_kelvin = actions[cursor].value if type(actions[cursor].value) is int else None
            if target_kelvin is None or not device(actions[cursor], "temperature_target", chandelier, "set_color_temperature", target_kelvin):
                raise NodeRedBackendError("Node-RED tambur temperature exceeds release source")
            cursor += 1
        pairs = {(5, 6500), (15, 6000), (30, 5200), (45, 4400), (60, 3600), (75, 2800), (85, 2200), (10, 6500)}
        if (brightness is not None and brightness not in {item[0] for item in pairs}) or (brightness is not None and target_kelvin is not None and (brightness, target_kelvin) not in pairs) or (target_kelvin is not None and target_kelvin not in {item[1] for item in pairs}):
            raise NodeRedBackendError("Node-RED tambur profile values exceed release source")
        if cursor < len(actions) and device(actions[cursor], "mirror_off", mirror, "turn_off"):
            cursor += 1
        if cursor != len(actions):
            raise NodeRedBackendError("Node-RED tambur profile order exceeds release source")
        return
    targets = {"main": "entity_4be32416634e6416", "extra": "entity_1fdcd8b244637246", "cabinet": "entity_e7a7c61eec7bdff8", "fan": "entity_afef5df0e0cae309"}
    cursor = 0
    profiles = (("main", "off", "extra", "off", "cabinet", "on"), ("main", "on", "extra", "off", "cabinet", "off"), ("main", "off", "extra", "on", "cabinet", "off"))
    for profile in profiles:
        expected = [(profile[index], profile[index + 1]) for index in range(0, len(profile), 2)]
        prefix = 0
        while prefix < len(actions) and actions[prefix].id.startswith(("set_main_", "set_extra_", "set_cabinet_")):
            prefix += 1
        actual = actions[:prefix]
        ordered = 0
        valid = True
        for candidate in actual:
            while ordered < len(expected) and not device(candidate, f"set_{expected[ordered][0]}_{expected[ordered][1]}", targets[expected[ordered][0]], f"turn_{expected[ordered][1]}"):
                ordered += 1
            if ordered == len(expected):
                valid = False
                break
            ordered += 1
        forced = next(f"set_{name}_{state}" for name, state in expected if state == "on")
        if valid and any(item.id == forced for item in actual):
            cursor = prefix
            break
            break
    profile_actions = cursor
    presence_fan = False
    immediate_fan = False
    if cursor < len(actions) and delay(actions[cursor], "fan_presence_wait", 120):
        if cursor + 1 >= len(actions) or not device(actions[cursor + 1], "set_fan_on", targets["fan"], "turn_on"):
            raise NodeRedBackendError("Node-RED shower fan delay exceeds release source")
        cursor += 2
        presence_fan = True
    elif cursor < len(actions) and device(actions[cursor], "set_fan_on", targets["fan"], "turn_on"):
        cursor += 1
        immediate_fan = True
    if cursor < len(actions):
        if profile_actions or presence_fan:
            raise NodeRedBackendError("Node-RED shower branch combines exclusive states")
        if not delay(actions[cursor], "absence_wait", 300):
            raise NodeRedBackendError("Node-RED shower branch order exceeds release source")
        cursor += 1
        start = cursor
        for name in ("main", "extra", "cabinet", "fan"):
            if cursor < len(actions) and device(actions[cursor], f"set_{name}_off", targets[name], "turn_off"):
                cursor += 1
        if cursor == start:
            raise NodeRedBackendError("Node-RED shower absence branch must turn something off")
        if immediate_fan and any(item.id == "set_fan_off" for item in actions[start:cursor]):
            raise NodeRedBackendError("Node-RED shower cannot turn fan on and off in one absence branch")
    if cursor != len(actions):
        raise NodeRedBackendError("Node-RED shower branch exceeds release source")
