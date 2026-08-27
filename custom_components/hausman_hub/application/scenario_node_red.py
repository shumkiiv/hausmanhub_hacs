"""Managed Node-RED function-flow backend for Hausman scenarios.

Node-RED calculates a bounded action plan. Hausman remains the only component
that validates, sends and confirms physical device commands.
"""

from __future__ import annotations

import hashlib
import json
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
    ScenarioNodeRedMetadata,
    ScenarioNodeRedSyncStatus,
    ScenarioViolation,
    _action_from_payload,
    _action_to_payload,
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


def build_managed_flow(
    scenario_id: str,
    title: str,
    source: str,
    *,
    flow_id: str | None = None,
) -> dict[str, object]:
    """Build a readable three-node tab: HTTP input, function and response."""

    input_id, function_id, response_id = _node_ids(scenario_id)
    tab_id = flow_id or hashlib.sha256(f"flow:{scenario_id}".encode()).hexdigest()[:16]
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
        source = _function_source(body)
        marker = f"// HAUSMAN_MANAGED_SCENARIO {scenario_id}"
        if source is None or not source.startswith(marker):
            raise NodeRedBackendError("Function не принадлежит выбранному сценарию.")
        return {
            "flow": body,
            "source": source,
            "source_hash": managed_source_hash(source),
        }

    async def async_update_source(
        self,
        scenario_id: str,
        definition: ScenarioDefinition,
        flow_id: str,
        source: str,
        expected_source_hash: str,
        catalog: ScenarioCatalog,
        *,
        validate_only: bool,
    ) -> dict[str, object]:
        """Validate and optionally deploy one function with rollback verification."""

        current = await self.async_read_source(scenario_id, flow_id)
        current_hash = str(current["source_hash"])
        if current_hash != expected_source_hash:
            raise NodeRedSourceConflict(expected_source_hash, current_hash)
        diagnostics = validate_managed_source(scenario_id, source)
        proposed_hash = managed_source_hash(source)
        if validate_only:
            return {
                "saved": False,
                "current_source_hash": current_hash,
                "proposed_source_hash": proposed_hash,
                "diagnostics": diagnostics,
                "verification": None,
                "previous_source": current["source"],
            }
        if proposed_hash == current_hash:
            run_id = f"verify-{scenario_id}-{int(time.time() * 1000)}"
            actions, result = await self.async_plan(
                scenario_id,
                definition,
                run_id,
                catalog,
                dry_run=True,
            )
            if result["status"] == "failed":
                raise NodeRedBackendError(
                    "Проверочный запуск функции завершился ошибкой."
                )
            verification = {
                "contract": {
                    "name": NODE_RED_EXECUTION_CONTRACT,
                    "version": 1,
                },
                "correlationId": run_id,
                "scenarioId": scenario_id,
                **result,
                "actions": [_action_to_payload(action) for action in actions],
            }
            return {
                "saved": False,
                "current_source_hash": current_hash,
                "proposed_source_hash": proposed_hash,
                "diagnostics": diagnostics,
                "verification": verification,
                "previous_source": current["source"],
            }

        updated_flow = _replace_function_source(
            current["flow"], scenario_id, source
        )
        status, _ = await self._raw_request(
            "PUT", f"/flow/{flow_id}", payload=updated_flow, ingress=True
        )
        if status not in {200, 204}:
            raise NodeRedBackendError("Node-RED отклонил новый исходник функции.")
        try:
            deployed = await self.async_read_source(scenario_id, flow_id)
            if deployed["source_hash"] != proposed_hash:
                raise NodeRedBackendError(
                    "Контрольная сумма функции после сохранения не совпала."
                )
            run_id = f"verify-{scenario_id}-{int(time.time() * 1000)}"
            actions, result = await self.async_plan(
                scenario_id,
                definition,
                run_id,
                catalog,
                dry_run=True,
            )
            if result["status"] == "failed":
                raise NodeRedBackendError(
                    "Проверочный запуск новой функции завершился ошибкой."
                )
            verification = {
                "contract": {
                    "name": NODE_RED_EXECUTION_CONTRACT,
                    "version": 1,
                },
                "correlationId": run_id,
                "scenarioId": scenario_id,
                **result,
                "actions": [_action_to_payload(action) for action in actions],
            }
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
            "verification": verification,
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

        current = await self.async_read_source(scenario_id, flow_id)
        if current["source_hash"] != expected_current_hash:
            raise NodeRedSourceConflict(
                expected_current_hash, str(current["source_hash"])
            )
        restored_flow = _replace_function_source(
            current["flow"], scenario_id, source
        )
        status, _ = await self._raw_request(
            "PUT", f"/flow/{flow_id}", payload=restored_flow, ingress=True
        )
        if status not in {200, 204}:
            raise NodeRedBackendError("Не удалось восстановить прежний исходник.")
        restored = await self.async_read_source(scenario_id, flow_id)
        if restored["source_hash"] != managed_source_hash(source):
            raise NodeRedBackendError(
                "Контрольная сумма восстановленного исходника не совпала."
            )

    async def async_prepare(
        self,
        scenario_id: str,
        title: str,
        definition: ScenarioDefinition,
        *,
        previous: ScenarioNodeRedMetadata | None = None,
    ) -> ScenarioDefinition:
        """Create/update one tab without overwriting manual function edits."""

        metadata = definition.node_red
        if metadata is None:
            raise NodeRedBackendError("Node-RED metadata is missing")
        source = compile_managed_function(scenario_id, definition)
        expected_hash = managed_source_hash(source)
        current = previous or metadata
        flow_id = current.flow_id
        revision = current.flow_revision
        if flow_id:
            status, body = await self._raw_request(
                "GET", f"/flow/{flow_id}", ingress=True
            )
            if status == 200 and isinstance(body, Mapping):
                actual_source = _function_source(body)
                actual_hash = managed_source_hash(actual_source) if actual_source else None
                if current.source_hash and actual_hash != current.source_hash:
                    return replace(
                        definition,
                        node_red=replace(
                            metadata,
                            flow_id=flow_id,
                            flow_revision=revision,
                            source_hash=current.source_hash,
                            sync_status=ScenarioNodeRedSyncStatus.CHANGED,
                        ),
                    )
                if actual_hash != expected_hash:
                    managed = build_managed_flow(
                        scenario_id, title, source, flow_id=flow_id
                    )
                    status, _ = await self._raw_request(
                        "PUT", f"/flow/{flow_id}", payload=managed, ingress=True
                    )
                    if status not in {200, 204}:
                        raise NodeRedBackendError("Node-RED flow update failed")
                    revision += 1
            elif status != 404:
                raise NodeRedBackendError("Cannot inspect the managed Node-RED flow")
            else:
                flow_id = None
        if not flow_id:
            managed = build_managed_flow(scenario_id, title, source)
            status, body = await self._raw_request(
                "POST", "/flow", payload=managed, ingress=True
            )
            if status not in {200, 201}:
                raise NodeRedBackendError("Node-RED flow creation failed")
            data = self._data(body)
            candidate = data.get("id")
            if not isinstance(candidate, str) or not candidate:
                raise NodeRedBackendError("Node-RED did not return the created flow id")
            flow_id = candidate
            revision = 1
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
        self, definition: ScenarioDefinition, catalog: ScenarioCatalog
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
            safe_attributes = {
                str(key): value
                for key, value in attributes.items()
                if isinstance(value, (str, int, float, bool))
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
        payload = {
            "correlationId": run_id,
            "scenarioId": scenario_id,
            "dryRun": dry_run,
            "inputs": self._input_snapshot(definition, catalog),
            "context": {"timestampMs": int(time.time() * 1000)},
        }
        status, body = await self._raw_request(
            "POST",
            f"/{NODE_RED_ENDPOINT_PREFIX}/{scenario_id}",
            payload=payload,
            ingress=True,
        )
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
