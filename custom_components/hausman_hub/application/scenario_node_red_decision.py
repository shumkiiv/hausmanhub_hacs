"""Staged, closed Node-RED decision bundle for the Tambur light controller.

It is deliberately separate from the legacy three-node execution endpoint.
Task 3 supplies the server bridge which persists decisions and dispatches them.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


TAMBUR_DECISION_SCENARIO_ID = "tambur_adaptive_light"
_NODE_NAMES = ("Вход", "Проверка", "Приоритет", "Профиль", "Ожидания", "Следующий шаг", "Диагностика", "Ответ")

# The final function is executable as an ordinary Node-RED Function node.  The
# preceding small nodes only label the trace stages: no flow/global context,
# timers, imports or direct HA/MQTT command nodes are part of this bundle.
_DECISION_SOURCE = r'''// HAUSMAN_MANAGED_SCENARIO tambur_adaptive_light
const q = msg.payload && typeof msg.payload === 'object' ? msg.payload : {};
const bad = code => { throw new Error(`invalid_decision_input:${code}`); };
const obj = (x, code) => x && typeof x === 'object' && !Array.isArray(x) ? x : bad(code);
const clock = obj(q.clock, 'clock'), bind = obj(q.bindings, 'bindings');
const set = obj(q.settings, 'settings'), obs = obj(q.observations, 'observations');
const auth = obj(q.authority, 'authority'), durable = obj(q.durable, 'durable');
if (!q.contract || q.contract.name !== 'hausman-node-red-decision-input' || q.contract.version !== 1 || q.scenarioId !== 'tambur_adaptive_light') bad('contract');
if (!Array.isArray(bind.presenceSensors) || bind.presenceSensors.length < 1 || bind.presenceSensors.length > 3) bad('presenceSensors');
const now = Number(clock.nowMs), minute = Number(clock.minutesOfDay);
if (!Number.isSafeInteger(now) || !Number.isInteger(minute) || minute < 0 || minute > 1439 || clock.timezone !== 'Asia/Omsk') bad('clock');
const at = text => { const m = /^(\d\d):(\d\d)$/.exec(String(text)); return m ? Number(m[1]) * 60 + Number(m[2]) : bad('setting_time'); };
const morning = at(set.morningStart), morningEnd = at(set.morningEnd), off = at(set.mainOff), mirrorOff = at(set.mirrorOff);
const state = id => obj(obs[id], `observation:${id}`);
const owner = id => obj(auth[id] || {owner:'uncertain', generation:0, protectionActive:false}, `authority:${id}`);
const fresh = id => state(id).fresh === true && ['on', 'off'].includes(state(id).state);
const automatic = id => owner(id).owner === 'automatic' && owner(id).protectionActive === false;
const manual = id => ['manual', 'uncertain'].includes(owner(id).owner) || owner(id).protectionActive === true;
const chandelier = bind.chandelier, points = bind.points, mirror = bind.mirror;
const sensors = bind.presenceSensors;
const present = sensors.some(id => fresh(id) && state(id).state === 'on');
const absent = sensors.length > 0 && sensors.every(id => fresh(id) && state(id).state === 'off');
const base = {contract:{name:'hausman-node-red-decision',version:1}, correlationId:String(q.correlationId || ''), scenarioId:q.scenarioId, planId:`${q.correlationId}.decision`, controllerVersion:1, settingsRevision:q.settingsRevision, baseRevision:durable.revision, snapshotRevision:q.snapshotRevision, observationEpoch:q.observationEpoch, expiresAtMs:q.expiresAtMs, trace:[], nextState:{phase:durable.phase,phaseStartedAtMs:durable.phaseStartedAtMs,absenceSinceMs:durable.absenceSinceMs,absenceEpoch:durable.absenceEpoch,fadeStartPercent:durable.fadeStartPercent,fadeStartedAtMs:durable.fadeStartedAtMs,fadeReason:durable.fadeReason}, wakeups:[], action:null};
const skip = (reason, phase) => { base.status='skipped'; base.reasonCode=reason; if (phase) { base.nextState.phase=phase; base.nextState.phaseStartedAtMs=now; } msg.payload=base; return msg; };
const act = (reason, targetId, actionId, value) => { base.status='decided'; base.reasonCode=reason; base.trace=[{id:'decision',title:'Решение тамбура',status:'selected',actual:reason,expected:null,reason:null}]; base.action={id:`${q.correlationId}.${actionId}`,targetId,actionId,authorityGeneration:owner(targetId).generation,observedRevision:state(targetId).revision}; if (value !== undefined) base.action.value=value; msg.payload=base; return msg; };
const sunsetMinute = clock.sunsetAtMs === null ? null : minute + Math.floor((Number(clock.sunsetAtMs)-now)/60000);
const eveningStart = Math.min(sunsetMinute === null ? at(set.eveningLatestStart) : sunsetMinute, at(set.eveningLatestStart));
const profile = () => { if (minute < morning || minute >= off) return null; if (minute < morningEnd) return Math.round(Number(set.minPercent)+(Number(set.maxPercent)-Number(set.minPercent))*(minute-morning)/(morningEnd-morning)); if (minute < eveningStart) return Number(set.maxPercent); return Math.max(Number(set.minPercent), Math.round(Number(set.maxPercent)-(Number(set.maxPercent)-Number(set.minPercent))*(minute-eveningStart)/(off-eveningStart))); };
if (minute >= mirrorOff && automatic(mirror) && fresh(mirror) && state(mirror).state === 'on') return act('mirror_schedule_off', mirror, 'turn_off');
if (minute >= off && minute < mirrorOff) { if (!manual(mirror) && fresh(mirror) && state(mirror).state === 'off') return act('mirror_schedule_on', mirror, 'turn_on'); if (automatic(chandelier) && fresh(chandelier) && state(chandelier).state === 'on') return skip('automatic_on_forbidden', 'night'); return skip('automatic_on_forbidden', 'night'); }
if (minute < morning || minute >= off) return skip('automatic_on_forbidden');
if (manual(chandelier) && manual(points)) return skip('manual_main_lights');
if (present) { base.nextState={phase:'occupied',phaseStartedAtMs:now,absenceSinceMs:null,absenceEpoch:null,fadeStartPercent:null,fadeStartedAtMs:null,fadeReason:null}; if (!manual(chandelier) && fresh(chandelier) && state(chandelier).state === 'off') return act('presence_day', chandelier, 'set_brightness_percent', profile()); if (!manual(points) && fresh(points) && state(points).state === 'off') return act('presence_points', points, 'turn_on'); return skip(manual(chandelier) ? 'manual_chandelier' : 'presence_already_lit'); }
if (!absent) return skip('absence_not_confirmed');
if (durable.absenceSinceMs === null || durable.absenceEpoch !== q.observationEpoch) { base.nextState={phase:'absent',phaseStartedAtMs:now,absenceSinceMs:now,absenceEpoch:q.observationEpoch,fadeStartPercent:null,fadeStartedAtMs:null,fadeReason:null}; base.wakeups=[{id:`${q.correlationId}.absence`,kind:'absence',dueAtMs:now+Number(set.absenceDaySeconds)*1000}]; return skip('absence_waiting','absent'); }
if (now-Number(durable.absenceSinceMs) < Number(set.absenceDaySeconds)*1000) { base.wakeups=[{id:`${q.correlationId}.absence`,kind:'absence',dueAtMs:Number(durable.absenceSinceMs)+Number(set.absenceDaySeconds)*1000}]; return skip('absence_waiting'); }
if (automatic(chandelier) && fresh(chandelier) && state(chandelier).state === 'on') { const start = durable.fadeStartPercent === null ? Math.min(Number(state(chandelier).brightnessPercent || 100), 100) : Number(durable.fadeStartPercent); const begun = durable.fadeStartedAtMs === null ? now : Number(durable.fadeStartedAtMs); const level = Math.max(0, Math.floor(start*(1-(now-begun)/(Number(set.fadeSeconds)*1000)))); base.nextState={phase:'fade',phaseStartedAtMs:begun,absenceSinceMs:durable.absenceSinceMs,absenceEpoch:durable.absenceEpoch,fadeStartPercent:start,fadeStartedAtMs:begun,fadeReason:'absence'}; if (level > 0) { base.wakeups=[{id:`${q.correlationId}.fade`,kind:'fade',dueAtMs:Math.min(begun+Number(set.fadeSeconds)*1000,now+1000)}]; return act('absence_fade', chandelier, 'set_brightness_percent', level); } return act('absence_fade_complete', chandelier, 'turn_off'); }
if (automatic(points) && fresh(points) && state(points).state === 'on') return act('absence_points_off', points, 'turn_off');
return skip('absence_complete','idle');
'''


def _node_id(name: str) -> str:
    return hashlib.sha256(f"tambur-decision:{name}".encode()).hexdigest()[:16]


def _topology_hash(bundle: Mapping[str, object]) -> str:
    nodes = bundle.get("nodes")
    if not isinstance(nodes, list) or len(nodes) != len(_NODE_NAMES):
        raise ValueError("tambur decision topology is invalid")
    expected = [_node_id(name) for name in _NODE_NAMES]
    if [node.get("id") if isinstance(node, Mapping) else None for node in nodes] != expected:
        raise ValueError("tambur decision topology is invalid")
    expected_types = ("http in", "function", "function", "function", "function", "function", "function", "http response")
    if [node.get("type") if isinstance(node, Mapping) else None for node in nodes] != list(expected_types):
        raise ValueError("tambur decision topology is invalid")
    if any(not isinstance(node, Mapping) or node.get("wires") != ([] if index == 7 else [[expected[index + 1]]]) for index, node in enumerate(nodes)):
        raise ValueError("tambur decision topology is invalid")
    return hashlib.sha256(json.dumps(nodes, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_tambur_decision_bundle(bundle: Mapping[str, object] | None = None) -> dict[str, object]:
    """Return the sole trusted graph, or fail closed when a graph was altered."""
    nodes: list[dict[str, object]] = []
    for index, name in enumerate(_NODE_NAMES):
        node: dict[str, object] = {"id": _node_id(name), "name": name, "wires": []}
        if index == 0:
            node.update(type="http in", url="/hausman/decisions/tambur_adaptive_light", method="post")
        elif index == len(_NODE_NAMES) - 1:
            node.update(type="http response", statusCode="", headers={})
        else:
            node.update(type="function", func=_DECISION_SOURCE if index == 6 else "return msg;", outputs=1, timeout="4", libs=[])
        nodes.append(node)
    for index in range(len(nodes) - 1):
        nodes[index]["wires"] = [[nodes[index + 1]["id"]]]
    result = {"scenarioId": TAMBUR_DECISION_SCENARIO_ID, "topology": "tambur-decision-eight-node-v1", "nodes": nodes}
    _topology_hash(result if bundle is None else bundle)
    result["topologyHash"] = _topology_hash(result)
    result["sourceHash"] = hashlib.sha256(_DECISION_SOURCE.encode()).hexdigest()
    return result


def prepare_tambur_decision_bundle() -> dict[str, object]:
    """Prepare the complete immutable bundle for the future bridge.

    This internal operation has no network side effect.  It intentionally
    avoids the legacy public source-update API, which can only replace one
    function in the old three-node execution graph.
    """
    return build_tambur_decision_bundle()


def verify_tambur_decision_bundle(bundle: Mapping[str, object]) -> str:
    """Return trusted topology evidence for a complete received bundle."""
    if bundle.get("scenarioId") != TAMBUR_DECISION_SCENARIO_ID:
        raise ValueError("tambur decision scenario is invalid")
    actual = _topology_hash(bundle)
    expected = build_tambur_decision_bundle()["topologyHash"]
    if actual != expected:
        raise ValueError("tambur decision topology is untrusted")
    return actual


def validate_tambur_decision(request: Mapping[str, object], decision: Mapping[str, object]) -> None:
    """Validate Node output at the Python boundary without making policy choices."""
    required = ("contract", "correlationId", "scenarioId", "planId", "controllerVersion", "settingsRevision", "baseRevision", "snapshotRevision", "observationEpoch", "expiresAtMs", "status", "reasonCode", "trace", "nextState", "wakeups", "action")
    if set(decision) != set(required) or decision.get("contract") != {"name": "hausman-node-red-decision", "version": 1}:
        raise ValueError("tambur decision contract is invalid")
    for key in ("correlationId", "scenarioId", "controllerVersion", "settingsRevision", "snapshotRevision", "observationEpoch", "expiresAtMs"):
        if decision.get(key) != request.get(key):
            raise ValueError("tambur decision correlation is invalid")
    if decision.get("baseRevision") != request.get("durable", {}).get("revision") if isinstance(request.get("durable"), Mapping) else True:
        raise ValueError("tambur decision base revision is invalid")
    action = decision.get("action")
    if action is not None:
        if not isinstance(action, Mapping):
            raise ValueError("tambur decision action is invalid")
        bindings = request.get("bindings")
        if not isinstance(bindings, Mapping) or action.get("targetId") not in {bindings.get("chandelier"), bindings.get("points"), bindings.get("mirror")}:
            raise ValueError("tambur decision target is invalid")
        if action.get("actionId") in {"set_brightness_percent", "set_color_temperature"} and action.get("targetId") != bindings.get("chandelier"):
            raise ValueError("tambur decision appearance target is invalid")
