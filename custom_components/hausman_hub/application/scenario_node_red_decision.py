"""Trusted staged Node-RED decision graph for the Tambur light controller.

The graph only calculates one bounded decision. Hausman owns durable state,
authority attribution, command persistence and physical execution.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from referencing import Registry, Resource


TAMBUR_DECISION_SCENARIO_ID = "system-tambur-adaptive-controller"
TAMBUR_DECISION_ENDPOINT = "/hausman/decisions/system-tambur-adaptive-controller/v1"
TAMBUR_DECISION_TOPOLOGY = "tambur-decision-eight-node-v1"

_STAGES = (
    ("Вход", None),
    ("Проверка", "01_validate.js"),
    ("Приоритет", "02_priority.js"),
    ("Профиль", "03_profile.js"),
    ("Ожидания", "04_waits.js"),
    ("Следующий шаг", "05_next_step.js"),
    ("Диагностика", "06_diagnostics.js"),
    ("Ответ", None),
)
_SOURCE_DIRECTORY = Path(__file__).parents[1] / "managed_scenarios" / "tambur_decision_v1"
_CONTRACT_DIRECTORY = Path(__file__).parents[1] / "contracts" / "v1"
_INPUT_SCHEMA_NAME = "scenario-node-red-decision-input.schema.json"
_DECISION_SCHEMA_NAME = "scenario-node-red-decision.schema.json"
_EXECUTION_SCHEMA_NAME = "scenario-node-red-execution.schema.json"


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _flow_id() -> str:
    return hashlib.sha256(
        f"decision-flow:{TAMBUR_DECISION_SCENARIO_ID}".encode()
    ).hexdigest()[:16]


def tambur_decision_flow_id() -> str:
    """Return the reserved Node-RED tab ID without loading its source files."""

    return _flow_id()


def _node_id(index: int, name: str) -> str:
    return hashlib.sha256(
        f"decision-node:{TAMBUR_DECISION_SCENARIO_ID}:{index}:{name}".encode()
    ).hexdigest()[:16]


def _read_stage_source(filename: str) -> str:
    source = (_SOURCE_DIRECTORY / filename).read_text(encoding="utf-8")
    if not source.endswith("\n"):
        raise RuntimeError(f"Tambur Node-RED stage lacks final newline: {filename}")
    return source


def _canonical_bundle() -> dict[str, object]:
    tab_id = _flow_id()
    node_ids = [_node_id(index, name) for index, (name, _) in enumerate(_STAGES)]
    nodes: list[dict[str, object]] = []
    for index, ((name, source_name), node_id) in enumerate(
        zip(_STAGES, node_ids, strict=True)
    ):
        common: dict[str, object] = {
            "id": node_id,
            "z": tab_id,
            "name": name,
            "x": 140 + index * 180,
            "y": 120,
            "wires": [] if index == len(_STAGES) - 1 else [[node_ids[index + 1]]],
        }
        if index == 0:
            common.update(
                type="http in",
                url=TAMBUR_DECISION_ENDPOINT,
                method="post",
                upload=False,
                swaggerDoc="",
            )
        elif index == len(_STAGES) - 1:
            common.update(type="http response", statusCode="", headers={})
        else:
            if source_name is None:
                raise RuntimeError("Tambur Node-RED function stage has no source")
            common.update(
                type="function",
                func=_read_stage_source(source_name),
                outputs=1,
                timeout="4",
                noerr=0,
                initialize="",
                finalize="",
                libs=[],
            )
        nodes.append(common)

    function_hashes = {
        str(node["id"]): hashlib.sha256(str(node["func"]).encode("utf-8")).hexdigest()
        for node in nodes
        if node["type"] == "function"
    }
    bundle: dict[str, object] = {
        "id": tab_id,
        "scenarioId": TAMBUR_DECISION_SCENARIO_ID,
        "topology": TAMBUR_DECISION_TOPOLOGY,
        "label": "Hausman: адаптивный свет тамбура",
        "disabled": False,
        "info": (
            "Управляемый вычислитель решения тамбура. Физические команды, "
            "владение и долговечное состояние остаются в Hausman."
        ),
        "nodes": nodes,
        "configs": [],
        "subflows": [],
        "functionHashes": function_hashes,
    }
    bundle["topologyHash"] = _digest(bundle)
    return bundle


def build_tambur_decision_bundle() -> dict[str, object]:
    """Build the exact signed eight-node decision bundle."""

    return _canonical_bundle()


def prepare_tambur_decision_bundle() -> dict[str, object]:
    """Prepare the complete immutable bundle without a network side effect."""

    return build_tambur_decision_bundle()


def verify_tambur_decision_bundle(bundle: Mapping[str, object]) -> str:
    """Fail closed unless every graph field and every function byte is exact."""

    if not isinstance(bundle, Mapping):
        raise ValueError("tambur decision bundle is invalid")
    expected = _canonical_bundle()
    if set(bundle) != set(expected):
        raise ValueError("tambur decision bundle fields are invalid")
    if bundle.get("scenarioId") != TAMBUR_DECISION_SCENARIO_ID:
        raise ValueError("tambur decision bundle scenario is invalid")
    if bundle.get("topology") != TAMBUR_DECISION_TOPOLOGY:
        raise ValueError("tambur decision topology is invalid")
    if bundle.get("functionHashes") != expected["functionHashes"]:
        raise ValueError("tambur decision source hashes are invalid")
    if bundle.get("topologyHash") != expected["topologyHash"]:
        raise ValueError("tambur decision topology hash is invalid")
    unsigned = {key: value for key, value in bundle.items() if key != "topologyHash"}
    if _digest(unsigned) != bundle.get("topologyHash"):
        raise ValueError("tambur decision graph digest is invalid")
    if bundle != expected:
        raise ValueError("tambur decision graph is untrusted")
    return str(expected["topologyHash"])


def tambur_decision_flow(bundle: Mapping[str, object]) -> dict[str, object]:
    """Return the exact object accepted and returned by Node-RED's flow API."""

    verify_tambur_decision_bundle(bundle)
    return {
        "id": bundle["id"],
        "label": bundle["label"],
        "disabled": bundle["disabled"],
        "info": bundle["info"],
        "env": [],
        "nodes": json.loads(json.dumps(bundle["nodes"], ensure_ascii=False)),
        "configs": [],
        "subflows": [],
    }


def tambur_decision_global_nodes(bundle: Mapping[str, object]) -> list[dict[str, object]]:
    """Flatten the trusted flow into a Node-RED v2 global-flow document."""

    flow = tambur_decision_flow(bundle)
    tab = {
        "id": flow["id"],
        "type": "tab",
        "label": flow["label"],
        "disabled": False,
        "info": flow["info"],
        "env": json.loads(json.dumps(flow["env"], ensure_ascii=False)),
    }
    return [tab, *json.loads(json.dumps(flow["nodes"], ensure_ascii=False))]


def _contains_reserved_reference(value: object, reserved_ids: set[str]) -> bool:
    if isinstance(value, str):
        return value in reserved_ids
    if isinstance(value, Mapping):
        return any(
            _contains_reserved_reference(child, reserved_ids)
            for child in value.values()
        )
    if isinstance(value, Sequence):
        return any(
            _contains_reserved_reference(child, reserved_ids)
            for child in value
        )
    return False


def _reject_external_reserved_references(
    global_nodes: Sequence[object], expected: Sequence[Mapping[str, object]]
) -> None:
    """Reject any foreign exact reference into the reserved decision graph."""

    reserved_ids = {str(node["id"]) for node in expected}
    for node in global_nodes:
        if isinstance(node, Mapping) and node.get("id") in reserved_ids:
            continue
        if _contains_reserved_reference(node, reserved_ids):
            raise ValueError("tambur decision graph has an external reference")


def verify_tambur_decision_flow(
    flow: Mapping[str, object], bundle: Mapping[str, object] | None = None
) -> str:
    """Verify an exact GET /flow representation."""

    trusted = bundle or _canonical_bundle()
    verify_tambur_decision_bundle(trusted)
    if not isinstance(flow, Mapping):
        raise ValueError("tambur decision flow graph is invalid")
    normalized = dict(flow)
    normalized.setdefault("configs", [])
    normalized.setdefault("subflows", [])
    if normalized != tambur_decision_flow(trusted):
        raise ValueError("tambur decision flow graph is invalid")
    return str(trusted["topologyHash"])


def verify_tambur_decision_global_nodes(
    global_nodes: Sequence[object], bundle: Mapping[str, object] | None = None
) -> str:
    """Require one exact installed copy and reject ID or endpoint collisions."""

    trusted = bundle or _canonical_bundle()
    verify_tambur_decision_bundle(trusted)
    if not isinstance(global_nodes, list):
        raise ValueError("tambur decision global graph is invalid")
    expected = tambur_decision_global_nodes(trusted)
    _reject_external_reserved_references(global_nodes, expected)
    expected_ids = {str(node["id"]) for node in expected}
    flow_id = trusted["id"]
    owned = [
        node
        for node in global_nodes
        if isinstance(node, Mapping)
        and (node.get("id") == flow_id or node.get("z") == flow_id)
    ]
    present = [
        node
        for node in global_nodes
        if isinstance(node, Mapping) and node.get("id") in expected_ids
    ]
    endpoints = [
        node
        for node in global_nodes
        if isinstance(node, Mapping)
        and node.get("type") == "http in"
        and node.get("url") == TAMBUR_DECISION_ENDPOINT
    ]
    if owned != expected or present != expected or endpoints != [expected[1]]:
        raise ValueError("tambur decision global graph is invalid")
    if any(
        sum(
            1
            for candidate in global_nodes
            if isinstance(candidate, Mapping) and candidate.get("id") == node["id"]
        )
        != 1
        for node in expected
    ):
        raise ValueError("tambur decision graph IDs are not unique")
    return str(trusted["topologyHash"])


def validate_tambur_decision_install_target(global_nodes: Sequence[object]) -> None:
    """Reject any reserved ID or endpoint before the first bundle install."""

    if not isinstance(global_nodes, list):
        raise ValueError("tambur decision global graph is invalid")
    expected = tambur_decision_global_nodes(_canonical_bundle())
    _reject_external_reserved_references(global_nodes, expected)
    reserved_ids = {str(node["id"]) for node in expected}
    if any(
        isinstance(node, Mapping)
        and (
            node.get("id") in reserved_ids
            or node.get("type") == "http in"
            and node.get("url") == TAMBUR_DECISION_ENDPOINT
        )
        for node in global_nodes
    ):
        raise ValueError("tambur decision install target conflicts")


@lru_cache(maxsize=2)
def _contract_validator(schema_name: str) -> Draft202012Validator:
    schemas: dict[str, Mapping[str, Any]] = {}
    for name in (schema_name, _EXECUTION_SCHEMA_NAME):
        schema = json.loads((_CONTRACT_DIRECTORY / name).read_text(encoding="utf-8"))
        if not isinstance(schema, Mapping) or not isinstance(schema.get("$id"), str):
            raise RuntimeError(f"Invalid vendored contract schema: {name}")
        Draft202012Validator.check_schema(schema)
        schemas[name] = schema
    registry = Registry()
    for schema in schemas.values():
        registry = registry.with_resource(
            str(schema["$id"]), Resource.from_contents(dict(schema))
        )
    return Draft202012Validator(
        schemas[schema_name], registry=registry, format_checker=FormatChecker()
    )


def _validate_schema(value: object, schema_name: str, label: str) -> None:
    try:
        _contract_validator(schema_name).validate(value)
    except (ValidationError, OSError, json.JSONDecodeError, RuntimeError) as error:
        raise ValueError(f"tambur decision {label} is invalid") from error


def _time_minutes(value: object) -> int:
    hour, minute = str(value).split(":", maxsplit=1)
    return int(hour) * 60 + int(minute)


def validate_tambur_decision_input(request: Mapping[str, object]) -> None:
    """Apply Task 1 schema plus Tambur-specific semantic bindings."""

    _validate_schema(request, _INPUT_SCHEMA_NAME, "input")
    if request.get("scenarioId") != TAMBUR_DECISION_SCENARIO_ID:
        raise ValueError("tambur decision input scenario is invalid")
    clock = request.get("clock")
    bindings = request.get("bindings")
    observations = request.get("observations")
    authority = request.get("authority")
    settings = request.get("settings")
    durable = request.get("durable")
    if not all(
        isinstance(value, Mapping)
        for value in (clock, bindings, observations, authority, settings, durable)
    ):
        raise ValueError("tambur decision input semantics are invalid")
    now = clock.get("nowMs")
    event = request.get("event")
    if (
        not isinstance(now, int)
        or not isinstance(event, Mapping)
        or request.get("issuedAtMs", 0) > now
        or now > request.get("expiresAtMs", -1)
        or event.get("observedAtMs", 0) > now
    ):
        raise ValueError("tambur decision input time is invalid")
    lights = [bindings.get(name) for name in ("chandelier", "points", "mirror")]
    sensors = bindings.get("presenceSensors")
    if not isinstance(sensors, list):
        raise ValueError("tambur decision input sensors are invalid")
    bound = [*lights, bindings.get("power"), *sensors]
    if len(set(bound)) != len(bound):
        raise ValueError("tambur decision input bindings are invalid")
    if not set(lights + sensors).issubset(observations):
        raise ValueError("tambur decision input observations are incomplete")
    if set(authority) != set(lights):
        raise ValueError("tambur decision input authority is invalid")
    if any(
        item.get("observedAtMs", now + 1) > now
        for item in observations.values()
        if isinstance(item, Mapping)
    ):
        raise ValueError("tambur decision input observations are from the future")
    times = [
        _time_minutes(settings[name])
        for name in ("morningStart", "morningEnd", "eveningLatestStart", "mainOff")
    ]
    if times != sorted(times) or len(set(times)) != len(times):
        raise ValueError("tambur decision input schedule is invalid")
    wakeups = durable.get("wakeups")
    if isinstance(wakeups, list) and len({item["id"] for item in wakeups}) != len(wakeups):
        raise ValueError("tambur decision input wakeups are duplicated")
    receipts = request.get("receipts")
    if isinstance(receipts, list) and len({item["id"] for item in receipts}) != len(receipts):
        raise ValueError("tambur decision input receipts are duplicated")


def validate_tambur_decision(
    request: Mapping[str, object], decision: Mapping[str, object]
) -> None:
    """Validate schema and bind the returned decision to the exact input snapshot."""

    validate_tambur_decision_input(request)
    _validate_schema(decision, _DECISION_SCHEMA_NAME, "response")
    durable = request["durable"]
    if not isinstance(durable, Mapping):
        raise ValueError("tambur decision response binding is invalid")
    expected = {
        "correlationId": request["correlationId"],
        "scenarioId": request["scenarioId"],
        "planId": request["correlationId"],
        "controllerVersion": request["controllerVersion"],
        "settingsRevision": request["settingsRevision"],
        "baseRevision": durable["revision"],
        "snapshotRevision": request["snapshotRevision"],
        "observationEpoch": request["observationEpoch"],
        "expiresAtMs": request["expiresAtMs"],
    }
    if any(decision.get(key) != value for key, value in expected.items()):
        raise ValueError("tambur decision response binding is invalid")
    action = decision.get("action")
    if (action is None) != (decision.get("status") == "skipped"):
        raise ValueError("tambur decision response status is invalid")
    wakeups = decision.get("wakeups")
    if not isinstance(wakeups, list) or len({item["id"] for item in wakeups}) != len(wakeups):
        raise ValueError("tambur decision response wakeups are invalid")
    clock = request["clock"]
    if not isinstance(clock, Mapping):
        raise ValueError("tambur decision response binding is invalid")
    now = clock["nowMs"]
    if any(item["dueAtMs"] <= now for item in wakeups):
        raise ValueError("tambur decision response wakeup time is invalid")
    if action is not None:
        if not isinstance(action, Mapping):
            raise ValueError("tambur decision response action is invalid")
        bindings = request["bindings"]
        observations = request["observations"]
        authorities = request["authority"]
        if not all(isinstance(value, Mapping) for value in (bindings, observations, authorities)):
            raise ValueError("tambur decision response action binding is invalid")
        target = action.get("targetId")
        allowed = {
            bindings["chandelier"]: {
                "turn_on", "turn_off", "set_brightness_percent", "set_color_temperature"
            },
            bindings["points"]: {"turn_on", "turn_off"},
            bindings["mirror"]: {"turn_on", "turn_off"},
        }
        observation = observations.get(target)
        owner = authorities.get(target)
        owner_kind = owner.get("owner") if isinstance(owner, Mapping) else None
        automatic_proof = (
            isinstance(owner, Mapping)
            and owner_kind == "automatic"
            and owner.get("protectionActive") is False
            and isinstance(owner.get("confirmedReceiptId"), str)
            and isinstance(observation, Mapping)
            and owner.get("confirmedStateRevision") == observation.get("revision")
        )
        available_off = (
            isinstance(owner, Mapping)
            and owner_kind == "none"
            and owner.get("protectionActive") is False
            and isinstance(observation, Mapping)
            and observation.get("state") == "off"
            and action.get("actionId") in {"turn_on", "set_brightness_percent"}
        )
        if (
            target not in allowed
            or action.get("actionId") not in allowed[target]
            or not isinstance(observation, Mapping)
            or not isinstance(owner, Mapping)
            or observation.get("fresh") is not True
            or observation.get("continuityEpoch") != request["observationEpoch"]
            or action.get("observedRevision") != observation.get("revision")
            or action.get("authorityGeneration") != owner.get("generation")
            or action.get("id") != f"{str(request['correlationId'])[:119]}.act"
            or not (automatic_proof or available_off)
        ):
            raise ValueError("tambur decision response action binding is invalid")
    next_state = decision.get("nextState")
    if not isinstance(next_state, Mapping):
        raise ValueError("tambur decision response state is invalid")
    if (next_state.get("absenceSinceMs") is None) != (
        next_state.get("absenceEpoch") is None
    ):
        raise ValueError("tambur decision response absence state is invalid")
    if (
        next_state.get("fadeStartPercent") is not None
        and next_state.get("fadeStartedAtMs") is None
    ):
        raise ValueError("tambur decision response fade state is invalid")
