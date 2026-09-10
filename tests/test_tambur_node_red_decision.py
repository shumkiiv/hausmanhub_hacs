"""Executable contract and trust-boundary tests for the Tambur decision graph."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest

import pytest

from custom_components.hausman_hub.application.scenario_node_red import (
    NodeRedBackendError,
    NodeRedScenarioBackend,
    NodeRedSourceInvalid,
    build_managed_flow,
    managed_source_hash,
    validate_managed_source,
)
from custom_components.hausman_hub.application.scenario_service import (
    ScenarioService,
    ScenarioValidationError,
)
from custom_components.hausman_hub.application.scenarios import ScenarioCatalog
from custom_components.hausman_hub.application import scenario_node_red_decision
from custom_components.hausman_hub.application.scenario_node_red_decision import (
    TAMBUR_DECISION_SCENARIO_ID,
    prepare_tambur_decision_bundle,
    validate_tambur_decision,
    verify_tambur_decision_bundle,
)
from custom_components.hausman_hub.domain.scenarios import (
    Scenario,
    ScenarioAction,
    ScenarioActionType,
    ScenarioDefinition,
    ScenarioExecutionBackend,
    ScenarioExecutionMode,
    ScenarioNodeRedGeneratedBy,
    ScenarioNodeRedMetadata,
    ScenarioNodeRedSyncStatus,
    ScenarioRegistry,
    ScenarioTrigger,
    ScenarioTriggerType,
)


ROOT = Path(__file__).parents[1]
NOW = 1_800_000_000_000
CHANDELIER = "lamp.chandelier"
POINTS = "lamp.points"
MIRROR = "lamp.mirror"
POWER = "switch.power"
SENSORS = ("sensor.presence.one", "sensor.presence.two", "sensor.motion")


def tambur_decision_flow(bundle: dict[str, object]) -> dict[str, object]:
    return scenario_node_red_decision.tambur_decision_flow(bundle)


def tambur_decision_global_nodes(bundle: dict[str, object]) -> list[dict[str, object]]:
    return scenario_node_red_decision.tambur_decision_global_nodes(bundle)


def validate_tambur_decision_input(request: dict[str, object]) -> None:
    scenario_node_red_decision.validate_tambur_decision_input(request)


def _observation(
    state: str,
    revision: int,
    *,
    now: int = NOW,
    epoch: int = 4,
    fresh: bool = True,
    brightness: int | None = None,
    kelvin: int | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "state": state,
        "revision": revision,
        "observedAtMs": now,
        "fresh": fresh,
        "continuityEpoch": epoch,
    }
    if brightness is not None:
        result["brightnessPercent"] = brightness
    if kelvin is not None:
        result["colorTemperatureKelvin"] = kelvin
    return result


def _authority(
    owner: str = "none",
    generation: int = 1,
    *,
    observation: dict[str, object] | None = None,
    protected: bool = False,
    protected_until: int | None = None,
    manual_until: int | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "owner": owner,
        "generation": generation,
        "protectionActive": protected,
    }
    if owner == "automatic" and observation is not None:
        result.update(
            confirmedReceiptId=f"receipt.{generation}",
            confirmedStateRevision=observation["revision"],
        )
    if protected_until is not None:
        result["protectedUntilMs"] = protected_until
    if manual_until is not None:
        result["manualOnHoldUntilMs"] = manual_until
    if owner == "manual":
        result["lastManualAtMs"] = NOW - 1_000
    return result


def _request(
    minute: int = 10 * 60,
    *,
    now: int = NOW,
    sunset_minute: int | None = 18 * 60,
    sunrise_minute: int | None = None,
    sensor_states: tuple[str, ...] = ("on",),
    event_kind: str = "sensor",
    timezone: str = "Asia/Omsk",
) -> dict[str, object]:
    sensors = list(SENSORS[: len(sensor_states)])
    observations: dict[str, object] = {
        CHANDELIER: _observation("off", 11, now=now),
        POINTS: _observation("off", 12, now=now),
        MIRROR: _observation("off", 13, now=now),
    }
    for index, (sensor, state) in enumerate(zip(sensors, sensor_states, strict=True)):
        observations[sensor] = _observation(state, 20 + index, now=now)
    return {
        "contract": {"name": "hausman-node-red-decision-input", "version": 1},
        "correlationId": "tambur.test.1",
        "scenarioId": TAMBUR_DECISION_SCENARIO_ID,
        "controllerVersion": 1,
        "settingsRevision": 7,
        "snapshotRevision": 31,
        "observationEpoch": 4,
        "issuedAtMs": now - 1_000,
        "expiresAtMs": now + 60_000,
        "event": {"id": "event.1", "kind": event_kind, "observedAtMs": now},
        "clock": {
            "nowMs": now,
            "timezone": timezone,
            "localDate": "2027-01-15",
            "minutesOfDay": minute,
            "sunsetAtMs": None if sunset_minute is None else now + (sunset_minute - minute) * 60_000,
            "sunriseAtMs": None if sunrise_minute is None else now + (sunrise_minute - minute) * 60_000,
        },
        "bindings": {
            "chandelier": CHANDELIER,
            "points": POINTS,
            "mirror": MIRROR,
            "power": POWER,
            "presenceSensors": sensors,
        },
        "settings": {
            "morningStart": "09:00",
            "morningEnd": "10:00",
            "eveningLatestStart": "21:00",
            "mainOff": "23:00",
            "mirrorOff": "01:00",
            "minPercent": 5,
            "maxPercent": 80,
            "dayKelvin": 3000,
            "eveningKelvin": 2200,
            "absenceDaySeconds": 600,
            "absenceNightSeconds": 180,
            "fadeSeconds": 20,
            "manualOffMinSeconds": 600,
            "manualOffAbsenceSeconds": 30,
            "manualOnHoldSeconds": 3600,
        },
        "observations": observations,
        "authority": {
            CHANDELIER: _authority(generation=2),
            POINTS: _authority(generation=3),
            MIRROR: _authority(generation=4),
        },
        "durable": {
            "revision": 9,
            "phase": "idle",
            "phaseStartedAtMs": None,
            "absenceSinceMs": None,
            "absenceEpoch": None,
            "fadeStartPercent": None,
            "fadeStartedAtMs": None,
            "fadeReason": None,
            "pendingReceiptId": None,
            "wakeups": [],
        },
        "receipts": [],
    }


def _set_light(
    request: dict[str, object], target: str, state: str, owner: str, *,
    brightness: int | None = None, kelvin: int | None = None,
    protected: bool = False, protected_until: int | None = None,
    manual_until: int | None = None,
) -> None:
    now = int(request["clock"]["nowMs"])
    revision = {CHANDELIER: 11, POINTS: 12, MIRROR: 13}[target]
    observation = _observation(
        state, revision, now=now, epoch=int(request["observationEpoch"]),
        brightness=brightness, kelvin=kelvin,
    )
    request["observations"][target] = observation
    request["authority"][target] = _authority(
        owner, {CHANDELIER: 2, POINTS: 3, MIRROR: 4}[target],
        observation=observation, protected=protected,
        protected_until=protected_until, manual_until=manual_until,
    )


def _move_now(request: dict[str, object], now: int) -> None:
    previous = int(request["clock"]["nowMs"])
    delta = now - previous
    request["clock"]["nowMs"] = now
    sunset = request["clock"]["sunsetAtMs"]
    if sunset is not None:
        request["clock"]["sunsetAtMs"] = int(sunset) + delta
    sunrise = request["clock"].get("sunriseAtMs")
    if sunrise is not None:
        request["clock"]["sunriseAtMs"] = int(sunrise) + delta
    request["issuedAtMs"] = now - 1_000
    request["expiresAtMs"] = now + 60_000
    request["event"]["observedAtMs"] = now
    for observation in request["observations"].values():
        observation["observedAtMs"] = now


def _run_chain(request: dict[str, object], bundle: dict[str, object] | None = None) -> dict[str, object]:
    graph = bundle or prepare_tambur_decision_bundle()
    sources = [node["func"] for node in graph["nodes"] if node["type"] == "function"]
    harness = """
const sources = JSON.parse(process.argv[1]);
let msg = {payload: JSON.parse(process.argv[2])};
for (const source of sources) {
  msg = (new Function('msg', source))(msg);
  if (!msg || typeof msg !== 'object') throw new Error('stage_did_not_return_msg');
}
process.stdout.write(JSON.stringify(msg));
"""
    completed = subprocess.run(
        ["node", "-e", harness, json.dumps(sources), json.dumps(request)],
        check=True, capture_output=True, text=True,
    )
    message = json.loads(completed.stdout)
    assert "_hausman" not in message
    return message["payload"]


def _decision(request: dict[str, object]) -> dict[str, object]:
    result = _run_chain(request)
    validate_tambur_decision(request, result)
    return result


def _action_signature(decision: dict[str, object]) -> tuple[object, ...] | None:
    action = decision["action"]
    if action is None:
        return None
    return action["targetId"], action["actionId"], action.get("value")


def _night_mirror_request(
    minute: int,
    *,
    now: int = NOW,
    sunrise_minute: int | None = 6 * 60,
    sensor_states: tuple[str, ...] = ("on",),
    event_kind: str = "sensor",
) -> dict[str, object]:
    request = _request(
        minute,
        now=now,
        sunrise_minute=sunrise_minute,
        sensor_states=sensor_states,
        event_kind=event_kind,
    )
    if event_kind == "sensor":
        request["event"]["targetId"] = SENSORS[0]
    return request


def test_night_mirror_uses_literal_start_and_actual_sunrise_boundary() -> None:
    before = _night_mirror_request(119)
    assert _action_signature(_decision(before)) is None

    at_start = _night_mirror_request(120)
    started = _decision(at_start)
    assert _action_signature(started) == (MIRROR, "turn_on", None)
    assert started["nextState"]["phase"] == "night"
    assert started["nextState"]["phaseStartedAtMs"] == NOW
    assert {item["id"] for item in started["wakeups"]} >= {"tambur.night_mirror_minimum"}

    at_sunrise = _night_mirror_request(360)
    assert _action_signature(_decision(at_sunrise)) is None


def test_night_mirror_requires_fresh_sensor_event_and_trusted_future_sunrise() -> None:
    cases = (
        _night_mirror_request(120, event_kind="clock"),
        _night_mirror_request(120, sunrise_minute=None),
        _night_mirror_request(120, sunrise_minute=119),
    )
    for request in cases:
        decision = _decision(request)
        assert _action_signature(decision) is None
        assert decision["reasonCode"] in {
            "night_mirror_event_required",
            "night_mirror_sunrise_unavailable",
            "night_mirror_sunrise_elapsed",
        }

    stale = _night_mirror_request(120)
    stale["observations"][SENSORS[0]]["fresh"] = False
    stale["observations"][SENSORS[0]]["continuityEpoch"] = 0
    assert _action_signature(_decision(stale)) is None


def test_night_mirror_minimum_and_fresh_absence_are_preserved_after_sunrise() -> None:
    started = _night_mirror_request(5 * 60 + 59, sunrise_minute=6 * 60)
    first = _decision(started)
    assert _action_signature(first) == (MIRROR, "turn_on", None)

    waiting = _night_mirror_request(
        6 * 60,
        now=NOW + 60_000,
        sunrise_minute=6 * 60,
        sensor_states=("off",),
    )
    _set_light(waiting, MIRROR, "on", "automatic")
    waiting["durable"] = {
        **first["nextState"],
        "revision": 10,
        "pendingReceiptId": None,
        "wakeups": first["wakeups"],
    }
    before_minimum = _decision(waiting)
    assert _action_signature(before_minimum) is None
    assert before_minimum["reasonCode"] == "night_mirror_minimum_waiting"

    after_minimum = deepcopy(waiting)
    _move_now(after_minimum, NOW + 11 * 60_000)
    after_minimum["clock"]["minutesOfDay"] = 6 * 60 + 10
    expired = _decision(after_minimum)
    assert _action_signature(expired) == (MIRROR, "turn_off", None)


def test_night_mirror_never_switches_off_with_fresh_presence_or_broken_continuity() -> None:
    started = _night_mirror_request(2 * 60, sunrise_minute=6 * 60)
    first = _decision(started)
    for state, fresh in (("on", True), ("unknown", False)):
        request = _night_mirror_request(
            6 * 60 + 15,
            now=NOW + 4 * 60 * 60_000,
            sunrise_minute=6 * 60,
            sensor_states=(state,),
        )
        _set_light(request, MIRROR, "on", "automatic")
        request["observations"][SENSORS[0]]["fresh"] = fresh
        request["observations"][SENSORS[0]]["continuityEpoch"] = 4 if fresh else 0
        request["durable"] = {
            **first["nextState"],
            "revision": 10,
            "pendingReceiptId": None,
            "wakeups": first["wakeups"],
        }
        assert _action_signature(_decision(request)) is None


def test_full_graph_has_real_stages_exact_sources_and_no_command_nodes() -> None:
    bundle = prepare_tambur_decision_bundle()
    assert bundle["scenarioId"] == "system-tambur-adaptive-controller"
    assert [node["name"] for node in bundle["nodes"]] == [
        "Вход", "Проверка", "Приоритет", "Профиль", "Ожидания",
        "Следующий шаг", "Диагностика", "Ответ",
    ]
    assert [node["type"] for node in bundle["nodes"]] == [
        "http in", "function", "function", "function", "function",
        "function", "function", "http response",
    ]
    function_nodes = [node for node in bundle["nodes"] if node["type"] == "function"]
    assert len({node["func"] for node in function_nodes}) == 6
    assert all(node["func"].strip() != "return msg;" for node in function_nodes)
    combined_source = "\n".join(node["func"] for node in function_nodes)
    assert not any(
        forbidden in combined_source
        for forbidden in (
            "global.", "flow.", "setTimeout(", "setInterval(", "require(",
            "import(", "fetch(", "node.send(",
        )
    )
    assert set(bundle["functionHashes"]) == {node["id"] for node in function_nodes}
    assert verify_tambur_decision_bundle(bundle) == bundle["topologyHash"]
    flow = tambur_decision_flow(bundle)
    assert flow["id"] == bundle["id"]
    assert flow["nodes"] == bundle["nodes"]
    actual_readback = deepcopy(flow)
    actual_readback["env"] = []
    assert (
        scenario_node_red_decision.verify_tambur_decision_flow(
            actual_readback, bundle
        )
        == bundle["topologyHash"]
    )
    foreign_environment = deepcopy(actual_readback)
    foreign_environment["env"] = [{"name": "FOREIGN", "value": "1"}]
    with pytest.raises(ValueError, match="flow graph"):
        scenario_node_red_decision.verify_tambur_decision_flow(
            foreign_environment, bundle
        )
    assert all(node["type"] not in {"api-call-service", "mqtt out", "ha-service"} for node in flow["nodes"])

    runtime = ROOT / "custom_components/hausman_hub/managed_scenarios/tambur_decision_v1"
    tooling = ROOT / "tools/managed_scenarios/tambur_decision_v1"
    assert sorted(path.name for path in runtime.glob("*.js")) == sorted(path.name for path in tooling.glob("*.js"))
    for runtime_path in runtime.glob("*.js"):
        assert runtime_path.read_bytes() == (tooling / runtime_path.name).read_bytes()


def test_every_graph_or_source_mutation_fails_closed() -> None:
    bundle = prepare_tambur_decision_bundle()
    mutations = []
    extra_node = deepcopy(bundle)
    extra_node["nodes"].append({"id": "foreign", "type": "mqtt out", "wires": []})
    mutations.append(extra_node)
    edge = deepcopy(bundle)
    edge["nodes"][1]["wires"] = [[edge["nodes"][3]["id"]]]
    mutations.append(edge)
    source = deepcopy(bundle)
    source["nodes"][2]["func"] += "\nreturn msg;"
    mutations.append(source)
    init = deepcopy(bundle)
    init["nodes"][2]["initialize"] = "global.set('x', 1);"
    mutations.append(init)
    libs = deepcopy(bundle)
    libs["nodes"][2]["libs"] = [{"var": "x", "module": "fs"}]
    mutations.append(libs)
    configs = deepcopy(bundle)
    configs["configs"] = [{"id": "foreign-config"}]
    mutations.append(configs)
    top_level = deepcopy(bundle)
    top_level["foreign"] = True
    mutations.append(top_level)
    for changed in mutations:
        with pytest.raises(ValueError, match="(bundle|topology|source|graph)"):
            verify_tambur_decision_bundle(changed)


def test_profile_boundaries_and_valid_ha_timezone_are_literal() -> None:
    cases = (
        (8 * 60 + 59, 18 * 60, None), (9 * 60, 18 * 60, 5),
        (9 * 60 + 30, 18 * 60, 43), (10 * 60, 18 * 60, 80),
        (20 * 60 + 30, 18 * 60, 43), (21 * 60, 22 * 60, 80),
        (22 * 60, 22 * 60, 43), (22 * 60 + 59, 22 * 60, 6),
    )
    for minute, sunset, expected in cases:
        request = _request(minute, sunset_minute=sunset, timezone="Europe/Moscow")
        decision = _decision(request)
        if expected is None:
            assert _action_signature(decision) is None
            assert decision["reasonCode"] == "automatic_on_forbidden"
        else:
            assert _action_signature(decision) == (CHANDELIER, "set_brightness_percent", expected)


def test_existing_automatic_chandelier_tracks_brightness_then_cct_without_motion() -> None:
    request = _request(22 * 60, sunset_minute=22 * 60, sensor_states=("off",), event_kind="clock")
    _set_light(request, CHANDELIER, "on", "automatic", brightness=80, kelvin=3000)
    first = _decision(request)
    assert _action_signature(first) == (CHANDELIER, "set_brightness_percent", 43)
    assert {item["kind"] for item in first["wakeups"]} >= {"profile"}
    _set_light(request, CHANDELIER, "on", "automatic", brightness=43, kelvin=3000)
    second = _decision(request)
    assert _action_signature(second) == (CHANDELIER, "set_color_temperature", 2600)


def test_manual_chandelier_stays_at_100_while_points_remain_independent() -> None:
    request = _request(12 * 60)
    _set_light(request, CHANDELIER, "on", "manual", brightness=100, kelvin=3000)
    decision = _decision(request)
    assert _action_signature(decision) == (POINTS, "turn_on", None)
    _set_light(request, POINTS, "on", "manual")
    decision = _decision(request)
    assert decision["action"] is None
    assert request["observations"][CHANDELIER]["brightnessPercent"] == 100


def test_mirror_schedule_wraps_midnight_and_handover_is_ordered() -> None:
    request = _request(23 * 60)
    _set_light(request, CHANDELIER, "on", "automatic", brightness=30, kelvin=2300)
    _set_light(request, POINTS, "on", "automatic")
    decision = _decision(request)
    assert _action_signature(decision) == (MIRROR, "turn_on", None)
    assert decision["nextState"]["phase"] == "mirror_handover"
    assert decision["nextState"]["fadeReason"] == "night"

    for minute in (0, 59):
        request = _request(minute)
        _set_light(request, MIRROR, "on", "automatic")
        decision = _decision(request)
        assert _action_signature(decision) is None
        assert decision["reasonCode"] in {"night_handover_complete", "automatic_on_forbidden"}

    request = _request(60)
    _set_light(request, MIRROR, "on", "automatic")
    decision = _decision(request)
    assert _action_signature(decision) == (MIRROR, "turn_off", None)


def test_confirmed_mirror_starts_night_fade_before_points_turn_off() -> None:
    request = _request(23 * 60)
    _set_light(request, CHANDELIER, "on", "automatic", brightness=40, kelvin=2200)
    _set_light(request, POINTS, "on", "automatic")
    _set_light(request, MIRROR, "on", "automatic")
    decision = _decision(request)
    assert decision["action"] is None
    assert decision["reasonCode"] == "night_fade_started"
    assert decision["nextState"]["fadeStartPercent"] == 40
    assert decision["nextState"]["fadeReason"] == "night"
    assert next(item for item in decision["wakeups"] if item["kind"] == "fade")["dueAtMs"] == NOW + 1_000

    request["durable"].update(decision["nextState"])
    _move_now(request, NOW + 10_000)
    decision = _decision(request)
    assert _action_signature(decision) == (CHANDELIER, "set_brightness_percent", 20)
    request["durable"].update(decision["nextState"])
    _move_now(request, NOW + 20_000)
    _set_light(request, CHANDELIER, "on", "automatic", brightness=1, kelvin=2200)
    decision = _decision(request)
    assert _action_signature(decision) == (CHANDELIER, "turn_off", None)
    _set_light(request, CHANDELIER, "off", "automatic", brightness=0, kelvin=2200)
    decision = _decision(request)
    assert _action_signature(decision) == (POINTS, "turn_off", None)


@pytest.mark.parametrize("status", ["failed", "uncertain"])
def test_failed_or_uncertain_mirror_receipt_keeps_main_and_does_not_retry(status: str) -> None:
    request = _request(23 * 60 + 1, event_kind="receipt")
    _set_light(request, CHANDELIER, "on", "automatic", brightness=30, kelvin=2200)
    request["durable"].update(
        phase="mirror_handover", phaseStartedAtMs=NOW - 1_000,
        fadeReason="night", pendingReceiptId="receipt.mirror.1",
    )
    request["receipts"] = [{
        "id": "receipt.mirror.1", "planId": "old.plan", "actionId": "turn_on",
        "targetId": MIRROR, "status": status, "observedRevision": 13,
        "observedAtMs": NOW,
    }]
    decision = _decision(request)
    assert decision["action"] is None
    assert decision["reasonCode"] == "mirror_handover_failed"
    assert decision["nextState"]["phase"] == "night"
    request["durable"].update(decision["nextState"])
    request["durable"]["pendingReceiptId"] = None
    request["receipts"] = []
    request["event"].update(kind="wakeup", wakeupId="tambur.mirror")
    decision = _decision(request)
    assert decision["action"] is None
    assert decision["reasonCode"] == "mirror_handover_not_retried"


def test_protected_mirror_keeps_main_but_manual_on_mirror_can_handover() -> None:
    protected = _request(23 * 60)
    _set_light(protected, CHANDELIER, "on", "automatic", brightness=30)
    _set_light(protected, MIRROR, "off", "manual", protected=True, protected_until=NOW + 600_000)
    decision = _decision(protected)
    assert decision["action"] is None
    assert decision["reasonCode"] == "mirror_handover_protected"
    assert next(item for item in decision["wakeups"] if item["kind"] == "hold")["dueAtMs"] == NOW + 600_000

    manual_on = _request(23 * 60)
    _set_light(manual_on, CHANDELIER, "on", "automatic", brightness=30)
    _set_light(manual_on, MIRROR, "on", "manual")
    decision = _decision(manual_on)
    assert decision["action"] is None
    assert decision["reasonCode"] == "night_fade_started"


def test_all_sensors_start_literal_absence_deadlines_and_unreliable_input_resets() -> None:
    day = _request(22 * 60, sensor_states=("off", "off", "off"))
    _set_light(day, CHANDELIER, "on", "automatic", brightness=40)
    decision = _decision(day)
    assert decision["reasonCode"] == "absence_waiting"
    assert decision["nextState"]["absenceSinceMs"] == NOW
    assert next(item for item in decision["wakeups"] if item["kind"] == "absence")["dueAtMs"] == NOW + 600_000

    night = _request(23 * 60 + 20, sensor_states=("off", "off", "off"))
    _set_light(night, CHANDELIER, "on", "automatic", brightness=40)
    _set_light(night, MIRROR, "off", "manual", protected=True)
    decision = _decision(night)
    assert next(item for item in decision["wakeups"] if item["kind"] == "absence")["dueAtMs"] == NOW + 180_000

    for state, fresh, epoch in (("unknown", True, 4), ("off", False, 4), ("off", True, 3)):
        request = _request(22 * 60, sensor_states=(state,))
        sensor = request["bindings"]["presenceSensors"][0]
        request["observations"][sensor]["fresh"] = fresh
        request["observations"][sensor]["continuityEpoch"] = epoch
        request["durable"].update(
            phase="absent", phaseStartedAtMs=NOW - 500_000,
            absenceSinceMs=NOW - 500_000, absenceEpoch=4,
        )
        decision = _decision(request)
        assert decision["nextState"]["absenceSinceMs"] is None
        assert decision["nextState"]["absenceEpoch"] is None
        assert not any(item["kind"] in {"absence", "fade"} for item in decision["wakeups"])


def test_fade_uses_fresh_snapshots_never_raises_and_points_follow_chandelier() -> None:
    request = _request(22 * 60, sensor_states=("off", "off", "off"), event_kind="wakeup")
    _set_light(request, CHANDELIER, "on", "automatic", brightness=40)
    _set_light(request, POINTS, "on", "automatic")
    request["durable"].update(
        phase="absent", phaseStartedAtMs=NOW - 600_000,
        absenceSinceMs=NOW - 600_000, absenceEpoch=4,
    )
    started = _decision(request)
    assert started["reasonCode"] == "absence_fade_started"
    assert started["action"] is None
    assert started["nextState"]["fadeStartPercent"] == 40
    request["durable"].update(started["nextState"])
    _move_now(request, NOW + 10_000)
    _set_light(request, CHANDELIER, "on", "automatic", brightness=40)
    middle = _decision(request)
    assert _action_signature(middle) == (CHANDELIER, "set_brightness_percent", 20)
    _move_now(request, NOW + 15_000)
    _set_light(request, CHANDELIER, "on", "automatic", brightness=5)
    lower = _decision(request)
    assert lower["action"] is None
    assert lower["reasonCode"] == "fade_snapshot_already_lower"
    _move_now(request, NOW + 20_000)
    zero = _decision(request)
    assert _action_signature(zero) == (CHANDELIER, "turn_off", None)
    _set_light(request, CHANDELIER, "off", "automatic", brightness=0)
    points = _decision(request)
    assert _action_signature(points) == (POINTS, "turn_off", None)


def test_missing_or_zero_fade_brightness_is_never_replaced_with_100() -> None:
    for brightness in (None, 0):
        request = _request(22 * 60, sensor_states=("off",), event_kind="wakeup")
        _set_light(request, CHANDELIER, "on", "automatic", brightness=brightness)
        request["durable"].update(
            phase="absent", phaseStartedAtMs=NOW - 600_000,
            absenceSinceMs=NOW - 600_000, absenceEpoch=4,
        )
        decision = _decision(request)
        assert _action_signature(decision) in {None, (CHANDELIER, "turn_off", None)}
        if decision["action"] is not None:
            assert decision["action"].get("value") != 100
        assert decision["nextState"]["fadeStartPercent"] == brightness


def test_presence_cancels_absence_fade_and_arrival_obeys_day_and_night_rules() -> None:
    request = _request(12 * 60, sensor_states=("on",), event_kind="sensor")
    _set_light(request, CHANDELIER, "on", "automatic", brightness=10, kelvin=3000)
    request["durable"].update(
        phase="fade", phaseStartedAtMs=NOW - 10_000,
        absenceSinceMs=NOW - 610_000, absenceEpoch=4,
        fadeStartPercent=40, fadeStartedAtMs=NOW - 10_000, fadeReason="absence",
    )
    decision = _decision(request)
    assert _action_signature(decision) == (CHANDELIER, "set_brightness_percent", 80)
    assert decision["nextState"]["absenceSinceMs"] is None
    assert decision["nextState"]["fadeReason"] is None
    assert not any(item["kind"] in {"absence", "fade"} for item in decision["wakeups"])

    arrival = _request(12 * 60, sensor_states=("off",), event_kind="arrival")
    assert _action_signature(_decision(arrival)) == (CHANDELIER, "set_brightness_percent", 80)
    arrival["clock"]["minutesOfDay"] = 2 * 60
    assert _decision(arrival)["action"] is None


def _set_profile_refresh_event(request: dict[str, object], kind: str) -> None:
    request["event"]["kind"] = kind
    request["event"].pop("wakeupId", None)
    if kind == "wakeup":
        request["event"]["wakeupId"] = "tambur.profile"


def _active_fade_request(
    minute: int,
    *,
    reason: str,
    brightness: int | None,
    sensor_state: str,
    event_kind: str,
) -> dict[str, object]:
    request = _request(
        minute, sensor_states=(sensor_state,), event_kind=event_kind
    )
    _set_light(
        request,
        CHANDELIER,
        "on",
        "automatic",
        brightness=brightness,
        kelvin=2200 if reason == "night" else 3000,
    )
    if reason == "night":
        _set_light(request, MIRROR, "on", "automatic")
    request["durable"].update(
        phase="fade",
        phaseStartedAtMs=NOW - 15_000,
        absenceSinceMs=NOW - 615_000 if reason == "absence" else None,
        absenceEpoch=4 if reason == "absence" else None,
        fadeStartPercent=40,
        fadeStartedAtMs=NOW - 15_000,
        fadeReason=reason,
    )
    return request


def test_absence_fade_preempts_profile_refresh_and_keeps_its_deadline() -> None:
    for event_kind in ("clock", "settings", "wakeup"):
        for brightness in (5, None):
            started = _request(
                12 * 60, sensor_states=("off",), event_kind=event_kind
            )
            _set_profile_refresh_event(started, event_kind)
            _set_light(
                started,
                CHANDELIER,
                "on",
                "automatic",
                brightness=brightness,
                kelvin=3000,
            )
            started["durable"].update(
                phase="absent",
                phaseStartedAtMs=NOW - 600_000,
                absenceSinceMs=NOW - 600_000,
                absenceEpoch=4,
            )
            decision = _decision(started)
            assert decision["action"] is None
            assert decision["reasonCode"] in {
                "absence_fade_started",
                "absence_fade_brightness_unknown",
            }
            assert decision["nextState"]["phase"] == "fade"
            assert decision["nextState"]["fadeStartPercent"] == brightness
            assert decision["nextState"]["fadeStartedAtMs"] == NOW
            assert next(
                item for item in decision["wakeups"] if item["kind"] == "fade"
            )["dueAtMs"] == NOW + (1_000 if brightness == 5 else 20_000)

            middle = _active_fade_request(
                12 * 60,
                reason="absence",
                brightness=brightness,
                sensor_state="off",
                event_kind=event_kind,
            )
            _set_profile_refresh_event(middle, event_kind)
            decision = _decision(middle)
            assert decision["action"] is None
            assert decision["reasonCode"] in {
                "fade_snapshot_already_lower",
                "absence_fade_brightness_unknown",
            }
            assert decision["nextState"]["fadeStartPercent"] == 40
            assert decision["nextState"]["fadeStartedAtMs"] == NOW - 15_000
            assert next(
                item for item in decision["wakeups"] if item["kind"] == "fade"
            )["dueAtMs"] == NOW + (1_000 if brightness == 5 else 5_000)

        before_due = _request(
            12 * 60, sensor_states=("off",), event_kind=event_kind
        )
        _set_profile_refresh_event(before_due, event_kind)
        _set_light(
            before_due,
            CHANDELIER,
            "on",
            "automatic",
            brightness=5,
            kelvin=3000,
        )
        before_due["durable"].update(
            phase="absent",
            phaseStartedAtMs=NOW - 599_000,
            absenceSinceMs=NOW - 599_000,
            absenceEpoch=4,
        )
        assert _action_signature(_decision(before_due)) == (
            CHANDELIER,
            "set_brightness_percent",
            80,
        )


def test_absence_fade_middle_end_and_points_order_remain_literal() -> None:
    request = _active_fade_request(
        12 * 60,
        reason="absence",
        brightness=40,
        sensor_state="off",
        event_kind="clock",
    )
    _set_light(request, POINTS, "on", "automatic")
    middle = _decision(request)
    assert _action_signature(middle) == (
        CHANDELIER,
        "set_brightness_percent",
        10,
    )

    request["durable"].update(middle["nextState"])
    _move_now(request, NOW + 5_000)
    _set_light(request, CHANDELIER, "on", "automatic", brightness=10)
    finished = _decision(request)
    assert _action_signature(finished) == (CHANDELIER, "turn_off", None)

    request["durable"].update(finished["nextState"])
    _set_light(request, CHANDELIER, "off", "automatic", brightness=0)
    points = _decision(request)
    assert _action_signature(points) == (POINTS, "turn_off", None)


def test_day_arrival_preempts_existing_fade_and_restores_profile() -> None:
    for event_kind in ("sensor", "arrival"):
        for brightness in (40, 5):
            request = _active_fade_request(
                12 * 60,
                reason="absence",
                brightness=brightness,
                sensor_state="on" if event_kind == "sensor" else "off",
                event_kind=event_kind,
            )
            if event_kind == "sensor":
                request["event"]["targetId"] = request["bindings"][
                    "presenceSensors"
                ][0]
            decision = _decision(request)
            assert _action_signature(decision) == (
                CHANDELIER,
                "set_brightness_percent",
                80,
            )
            assert decision["nextState"]["phase"] == "occupied"
            assert decision["nextState"]["absenceSinceMs"] is None
            assert decision["nextState"]["absenceEpoch"] is None
            assert decision["nextState"]["fadeStartPercent"] is None
            assert decision["nextState"]["fadeStartedAtMs"] is None
            assert decision["nextState"]["fadeReason"] is None
            assert not any(
                item["kind"] in {"absence", "fade"}
                for item in decision["wakeups"]
            )


def _night_fade_request(
    *, event_kind: str, sensor_state: str = "on"
) -> dict[str, object]:
    request = _active_fade_request(
        23 * 60 + 30,
        reason="night",
        brightness=40,
        sensor_state=sensor_state,
        event_kind=event_kind,
    )
    if event_kind == "sensor":
        request["event"]["targetId"] = request["bindings"][
            "presenceSensors"
        ][0]
    return request


def _assert_night_fade_cancelled(decision: dict[str, object]) -> None:
    assert decision["action"] is None
    assert decision["nextState"]["phase"] == "night"
    assert decision["nextState"]["phaseStartedAtMs"] == NOW
    assert decision["nextState"]["absenceSinceMs"] is None
    assert decision["nextState"]["absenceEpoch"] is None
    assert decision["nextState"]["fadeStartPercent"] is None
    assert decision["nextState"]["fadeStartedAtMs"] is None
    assert decision["nextState"]["fadeReason"] is None
    assert not any(
        item["kind"] in {"absence", "fade"} for item in decision["wakeups"]
    )


def test_night_arrival_cancels_fade_once_and_marker_blocks_restart() -> None:
    for event_kind, sensor_state in (("sensor", "on"), ("arrival", "off")):
        request = _night_fade_request(
            event_kind=event_kind, sensor_state=sensor_state
        )
        cancelled = _decision(request)
        _assert_night_fade_cancelled(cancelled)

        request["durable"].update(cancelled["nextState"])
        request["event"].pop("targetId", None)
        for repeated_kind in ("clock", "receipt"):
            request["event"]["kind"] = repeated_kind
            repeated = _decision(request)
            assert repeated["action"] is None
            assert repeated["nextState"]["phase"] == "night"
            assert repeated["nextState"]["fadeReason"] is None
            assert not any(
                item["kind"] == "fade" for item in repeated["wakeups"]
            )
            request["durable"].update(repeated["nextState"])


def test_night_transition_ignores_snapshot_only_and_invalid_sensor_arrivals() -> None:
    for event_kind in ("clock", "receipt"):
        request = _request(
            23 * 60, sensor_states=("on",), event_kind=event_kind
        )
        _set_light(
            request,
            CHANDELIER,
            "on",
            "automatic",
            brightness=40,
            kelvin=2200,
        )
        _set_light(request, MIRROR, "on", "automatic")
        decision = _decision(request)
        assert decision["reasonCode"] == "night_fade_started"
        assert decision["nextState"]["fadeReason"] == "night"

    invalid_cases = (
        (None, "on", True, 4),
        ("sensor.foreign", "on", True, 4),
        (SENSORS[0], "off", True, 4),
        (SENSORS[0], "unknown", True, 4),
        (SENSORS[0], "on", False, 4),
        (SENSORS[0], "on", True, 3),
    )
    for target_id, state, fresh, epoch in invalid_cases:
        request = _night_fade_request(
            event_kind="sensor", sensor_state=state
        )
        if target_id is None:
            request["event"].pop("targetId", None)
        else:
            request["event"]["targetId"] = target_id
        sensor = request["bindings"]["presenceSensors"][0]
        request["observations"][sensor]["fresh"] = fresh
        request["observations"][sensor]["continuityEpoch"] = epoch
        decision = _decision(request)
        assert decision["nextState"]["phase"] == "fade"
        assert decision["nextState"]["fadeReason"] == "night"
        assert decision["nextState"]["fadeStartedAtMs"] == NOW - 15_000


def test_night_cancel_then_new_absence_waits_180_seconds_and_next_window_runs() -> None:
    request = _night_fade_request(event_kind="sensor", sensor_state="on")
    cancelled = _decision(request)
    _assert_night_fade_cancelled(cancelled)

    request["durable"].update(cancelled["nextState"])
    _move_now(request, NOW + 1_000)
    sensor = request["bindings"]["presenceSensors"][0]
    request["observations"][sensor]["state"] = "off"
    request["observations"][sensor]["revision"] += 1
    request["event"].update(kind="sensor", targetId=sensor)
    waiting = _decision(request)
    assert waiting["action"] is None
    assert waiting["nextState"]["phase"] == "night"
    assert waiting["nextState"]["absenceSinceMs"] == NOW + 1_000
    assert next(
        item for item in waiting["wakeups"] if item["kind"] == "absence"
    )["dueAtMs"] == NOW + 181_000

    request["durable"].update(waiting["nextState"])
    _move_now(request, NOW + 180_000)
    request["event"].update(kind="clock")
    before_due = _decision(request)
    assert before_due["action"] is None
    assert before_due["nextState"]["phase"] == "night"
    assert next(
        item for item in before_due["wakeups"] if item["kind"] == "absence"
    )["dueAtMs"] == NOW + 181_000

    request["durable"].update(before_due["nextState"])
    _move_now(request, NOW + 181_000)
    request["event"].update(kind="wakeup", wakeupId="tambur.absence")
    faded = _decision(request)
    assert faded["action"] is None
    assert faded["reasonCode"] == "absence_fade_started"
    assert faded["nextState"]["phase"] == "fade"
    assert faded["nextState"]["fadeReason"] == "absence"

    after_window = _night_fade_request(event_kind="clock", sensor_state="on")
    after_window["durable"].update(cancelled["nextState"])
    _move_now(after_window, NOW + 90 * 60 * 1000)
    after_window["clock"]["minutesOfDay"] = 60
    after_window_decision = _decision(after_window)
    assert _action_signature(after_window_decision) == (
        MIRROR,
        "turn_off",
        None,
    )
    assert after_window_decision["nextState"]["fadeReason"] is None

    next_window = _night_fade_request(event_kind="clock", sensor_state="on")
    next_window["durable"].update(cancelled["nextState"])
    _move_now(next_window, NOW + 24 * 60 * 60 * 1000)
    next_window["clock"]["minutesOfDay"] = 23 * 60 + 30
    decision = _decision(next_window)
    assert decision["reasonCode"] == "night_fade_started"
    assert decision["nextState"]["fadeReason"] == "night"


def test_manual_hold_expiry_never_assigns_or_releases_authority() -> None:
    request = _request(12 * 60)
    _set_light(request, CHANDELIER, "on", "manual", brightness=100, manual_until=NOW + 3_600_000)
    decision = _decision(request)
    assert decision["action"] is not None and decision["action"]["targetId"] == POINTS
    assert next(item for item in decision["wakeups"] if item["kind"] == "hold")["dueAtMs"] == NOW + 3_600_000
    assert "authority" not in decision["nextState"]
    _move_now(request, NOW + 3_600_000)
    request["event"].update(kind="wakeup", wakeupId="tambur.hold")
    _set_light(request, POINTS, "on", "manual")
    decision = _decision(request)
    assert decision["action"] is None
    assert decision["reasonCode"] == "manual_authority_preserved"


def test_recovery_restarts_absence_for_new_epoch() -> None:
    request = _request(12 * 60, sensor_states=("off",), event_kind="recovery")
    request["observationEpoch"] = 5
    sensor = request["bindings"]["presenceSensors"][0]
    request["observations"][sensor]["continuityEpoch"] = 5
    request["durable"].update(
        phase="absent", phaseStartedAtMs=NOW - 500_000,
        absenceSinceMs=NOW - 500_000, absenceEpoch=4,
    )
    decision = _decision(request)
    assert decision["reasonCode"] == "recovery_absence_restarted"
    assert decision["nextState"]["absenceSinceMs"] == NOW
    assert next(item for item in decision["wakeups"] if item["kind"] == "absence")["dueAtMs"] == NOW + 600_000


def test_task1_schema_and_semantics_reject_nested_forgery_and_binding_mutations() -> None:
    request = _request()
    validate_tambur_decision_input(request)
    decision = _decision(request)
    mutations: list[dict[str, object]] = []
    forged = deepcopy(decision)
    forged["nextState"]["authority"] = {CHANDELIER: "automatic"}
    mutations.append(forged)
    invalid_action = deepcopy(decision)
    invalid_action["action"]["actionId"] = "raw_inverted_brightness"
    mutations.append(invalid_action)
    generation = deepcopy(decision)
    generation["action"]["authorityGeneration"] += 1
    mutations.append(generation)
    revision = deepcopy(decision)
    revision["action"]["observedRevision"] += 1
    mutations.append(revision)
    expiry = deepcopy(decision)
    expiry["expiresAtMs"] += 1
    mutations.append(expiry)
    duplicate = deepcopy(decision)
    duplicate["wakeups"] = [
        {"id": "tambur.same", "kind": "profile", "dueAtMs": NOW + 1_000},
        {"id": "tambur.same", "kind": "fade", "dueAtMs": NOW + 2_000},
    ]
    mutations.append(duplicate)
    oversized = deepcopy(decision)
    oversized["planId"] = "p" * 129
    mutations.append(oversized)
    for candidate in mutations:
        with pytest.raises(ValueError, match="decision"):
            validate_tambur_decision(request, candidate)

    manual_request = deepcopy(request)
    manual_request["authority"][CHANDELIER] = _authority(
        "manual", generation=2,
        observation=manual_request["observations"][CHANDELIER],
    )
    with pytest.raises(ValueError, match="decision"):
        validate_tambur_decision(manual_request, decision)

    bad_input = deepcopy(request)
    bad_input["authority"][CHANDELIER]["forged"] = True
    with pytest.raises(ValueError, match="input"):
        validate_tambur_decision_input(bad_input)


def test_old_public_source_editor_explicitly_rejects_decision_bundle() -> None:
    with pytest.raises(NodeRedSourceInvalid) as captured:
        validate_managed_source(TAMBUR_DECISION_SCENARIO_ID, prepare_tambur_decision_bundle())
    assert captured.value.code == "decision_bundle_requires_internal_api"


def _foreign_references(bundle: dict[str, object]) -> list[dict[str, object]]:
    target_id = bundle["nodes"][1]["id"]
    flow_id = bundle["id"]
    return [
        {
            "id": "foreign-wire",
            "type": "function",
            "z": "foreign-tab",
            "wires": [[target_id]],
        },
        {
            "id": "foreign-link",
            "type": "link out",
            "z": "foreign-tab",
            "links": [target_id],
            "wires": [],
        },
        {
            "id": "foreign-catch",
            "type": "catch",
            "z": "foreign-tab",
            "scope": [target_id],
            "wires": [],
        },
        {
            "id": "foreign-status",
            "type": "status",
            "z": "foreign-tab",
            "scope": [target_id],
            "wires": [],
        },
        {
            "id": "foreign-config",
            "type": "unknown-config",
            "server": {"nested": {"target": target_id}},
        },
        {
            "id": "foreign-owned",
            "type": "function",
            "z": flow_id,
            "wires": [],
        },
    ]


async def _case_external_references_fail_closed_and_unrelated_tabs_survive() -> None:
    for foreign in _foreign_references(prepare_tambur_decision_bundle()):
        network = _DecisionNetwork(installed=True)
        network.global_nodes.append(deepcopy(foreign))
        with pytest.raises(NodeRedBackendError, match="global graph"):
            await _backend(network).async_calculate_tambur_decision(_request())
        assert not any(
            method == "POST" and "/endpoint/" in path
            for method, path in network.calls
        )

    network = _DecisionNetwork(installed=True)
    target_id = network.bundle["nodes"][1]["id"]
    network.graph_mutator = lambda _flow, nodes: nodes.append(
        {
            "id": "foreign-after-decision",
            "type": "link out",
            "z": "foreign-tab",
            "links": [target_id],
            "wires": [],
        }
    )
    with pytest.raises(NodeRedBackendError, match="changed during decision"):
        await _backend(network).async_calculate_tambur_decision(_request())
    assert any(
        method == "POST" and "/endpoint/" in path
        for method, path in network.calls
    )

    unrelated = [
        {
            "id": "foreign-tab",
            "type": "tab",
            "label": "Unrelated",
            "disabled": False,
            "info": "",
            "env": [],
        },
        {
            "id": "foreign-node",
            "type": "function",
            "z": "foreign-tab",
            "name": "Unrelated",
            "func": "return msg;",
            "wires": [["foreign-sink"]],
        },
    ]
    network = _DecisionNetwork(installed=False)
    network.global_nodes = deepcopy(unrelated)
    result = await _backend(network).async_prepare_tambur_decision_bundle()
    assert result["created"] is True
    assert network.global_nodes[: len(unrelated)] == unrelated


async def _case_dangling_references_block_first_install_without_writes() -> None:
    for foreign in _foreign_references(prepare_tambur_decision_bundle()):
        network = _DecisionNetwork(installed=False)
        before = [deepcopy(foreign)]
        network.global_nodes = deepcopy(before)
        with pytest.raises(NodeRedBackendError, match="install target conflicts"):
            await _backend(network).async_prepare_tambur_decision_bundle()
        assert network.global_nodes == before
        assert not any(method == "POST" for method, _path in network.calls)


def _editor_definition(flow_id: str, source_hash: str) -> ScenarioDefinition:
    return ScenarioDefinition(
        version=1,
        execution_mode=ScenarioExecutionMode.RESTART,
        execution_backend=ScenarioExecutionBackend.NODE_RED,
        node_red=ScenarioNodeRedMetadata(
            flow_id=flow_id,
            flow_revision=1,
            source_hash=source_hash,
            generated_by=ScenarioNodeRedGeneratedBy.USER,
            sync_status=ScenarioNodeRedSyncStatus.SYNCED,
        ),
        triggers=(ScenarioTrigger("manual", ScenarioTriggerType.MANUAL),),
        conditions=(),
        actions=(
            ScenarioAction(
                "notify",
                ScenarioActionType.NOTIFICATION,
                message="Тест",
            ),
        ),
    )


class _EditorStore:
    def __init__(self, scenario: Scenario) -> None:
        self.registry = ScenarioRegistry(scenarios=(scenario,))
        self.save_calls = 0

    async def async_load(self) -> ScenarioRegistry:
        return self.registry

    async def async_save(self, registry: ScenarioRegistry) -> None:
        self.save_calls += 1
        self.registry = registry


async def _case_real_service_rejects_reserved_flow_for_both_editor_modes() -> None:
    source = (
        ROOT / "tools/managed_scenarios/tambur_controller.js"
    ).read_text(encoding="utf-8")
    source_hash = managed_source_hash(source)
    flow_id = str(prepare_tambur_decision_bundle()["id"])
    scenario = Scenario.from_definition(
        TAMBUR_DECISION_SCENARIO_ID,
        "Тамбур",
        _editor_definition(flow_id, source_hash),
        group="system",
        revision=7,
    )
    store = _EditorStore(scenario)
    calls: list[tuple[str, str]] = []

    async def adapter(method, path, headers, payload):
        del headers, payload
        calls.append((method, path))
        raise AssertionError("reserved editor path must not access Node-RED")

    backend = NodeRedScenarioBackend(SimpleNamespace(), request_adapter=adapter)
    backend._ingress_token = "token"  # noqa: SLF001
    backend._ingress_session = "session"  # noqa: SLF001
    service = ScenarioService(
        SimpleNamespace(), store, ScenarioCatalog(devices={}, scenarios={}),
        node_red_backend=backend,
    )
    await service.async_load()

    for validate_only in (True, False):
        with pytest.raises(ScenarioValidationError) as captured:
            await service.async_update_node_red_source(
                TAMBUR_DECISION_SCENARIO_ID,
                {
                    "expectedScenarioRevision": 7,
                    "expectedSourceHash": source_hash,
                    "source": source,
                    "validateOnly": validate_only,
                },
            )
        violation = captured.value.violations[0]
        assert violation.code == "decision_bundle_requires_internal_api"
        assert violation.path == "source"
    assert calls == []
    assert store.save_calls == 0


async def _case_legacy_three_node_tambur_editor_still_works() -> None:
    source = (
        ROOT / "tools/managed_scenarios/tambur_controller.js"
    ).read_text(encoding="utf-8")
    source_hash = managed_source_hash(source)
    flow_id = "legacy-tambur-flow"
    flow = build_managed_flow(
        TAMBUR_DECISION_SCENARIO_ID, "Тамбур", source, flow_id=flow_id
    )
    scenario = Scenario.from_definition(
        TAMBUR_DECISION_SCENARIO_ID,
        "Тамбур",
        _editor_definition(flow_id, source_hash),
        group="system",
        revision=7,
    )
    store = _EditorStore(scenario)
    writes = 0

    async def adapter(method, path, headers, payload):
        nonlocal writes
        del headers, payload
        if method == "GET" and path.endswith("/flows"):
            return 200, {
                "rev": "rev-one",
                "flows": [deepcopy(node) for node in flow["nodes"]],
            }
        if method == "GET" and path.endswith(f"/flow/{flow_id}"):
            return 200, deepcopy(flow)
        if method == "POST":
            writes += 1
        raise AssertionError((method, path))

    backend = NodeRedScenarioBackend(SimpleNamespace(), request_adapter=adapter)
    backend._ingress_token = "token"  # noqa: SLF001
    backend._ingress_session = "session"  # noqa: SLF001
    service = ScenarioService(
        SimpleNamespace(), store, ScenarioCatalog(devices={}, scenarios={}),
        node_red_backend=backend,
    )
    await service.async_load()
    for validate_only in (True, False):
        result = await service.async_update_node_red_source(
            TAMBUR_DECISION_SCENARIO_ID,
            {
                "expectedScenarioRevision": 7,
                "expectedSourceHash": source_hash,
                "source": source,
                "validateOnly": validate_only,
            },
        )
        assert result["valid"] is True
        assert result["saved"] is False
    assert writes == 0
    assert store.save_calls == 0


class _DecisionNetwork:
    def __init__(self, *, installed: bool) -> None:
        self.bundle = prepare_tambur_decision_bundle()
        self.flow = tambur_decision_flow(self.bundle)
        self.flow["env"] = []
        self.global_nodes = tambur_decision_global_nodes(self.bundle) if installed else []
        self.revision = "rev-one" if installed else "rev-zero"
        self.calls: list[tuple[str, str]] = []
        self.response_mutator = None
        self.graph_mutator = None

    async def __call__(self, method, path, headers, payload):
        del headers
        self.calls.append((method, path))
        if method == "GET" and path.endswith("/flows"):
            return 200, {"rev": self.revision, "flows": deepcopy(self.global_nodes)}
        if method == "GET" and path.endswith(f"/flow/{self.flow['id']}"):
            return 200, deepcopy(self.flow)
        if method == "POST" and path.endswith("/flows"):
            assert payload["rev"] == "rev-zero"
            self.global_nodes = deepcopy(payload["flows"])
            self.revision = "rev-one"
            return 200, {"rev": "rev-one"}
        if method == "POST" and path.endswith("/endpoint/hausman/decisions/system-tambur-adaptive-controller/v1"):
            result = _run_chain(deepcopy(payload), self.bundle)
            if self.response_mutator is not None:
                self.response_mutator(result)
            if self.graph_mutator is not None:
                self.graph_mutator(self.flow, self.global_nodes)
            return 200, result
        raise AssertionError((method, path))


def _backend(network: _DecisionNetwork) -> NodeRedScenarioBackend:
    class NoStates:
        def get(self, _entity_id):
            raise AssertionError("decision transport must not read HA or devices")

    backend = NodeRedScenarioBackend(SimpleNamespace(states=NoStates()), request_adapter=network)
    backend._ingress_token = "token"  # noqa: SLF001
    backend._ingress_session = "session"  # noqa: SLF001
    return backend


async def _case_full_bundle_provision_and_get_verification() -> None:
    network = _DecisionNetwork(installed=False)
    backend = _backend(network)
    result = await backend.async_prepare_tambur_decision_bundle()
    assert result == {
        "created": True, "flowId": network.flow["id"],
        "revision": "rev-one", "topologyHash": network.bundle["topologyHash"],
    }
    assert network.calls == [
        ("GET", "/ingress/token/flows"), ("POST", "/ingress/token/flows"),
        ("GET", "/ingress/token/flows"),
        ("GET", f"/ingress/token/flow/{network.flow['id']}"),
    ]


async def _case_internal_transport_checks_graph_before_and_after() -> None:
    network = _DecisionNetwork(installed=True)
    backend = _backend(network)
    result = await backend.async_calculate_tambur_decision(_request(9 * 60))
    assert _action_signature(result) == (CHANDELIER, "set_brightness_percent", 5)
    assert network.calls == [
        ("GET", "/ingress/token/flows"),
        ("GET", f"/ingress/token/flow/{network.flow['id']}"),
        ("POST", "/ingress/token/endpoint/hausman/decisions/system-tambur-adaptive-controller/v1"),
        ("GET", "/ingress/token/flows"),
        ("GET", f"/ingress/token/flow/{network.flow['id']}"),
    ]

    network = _DecisionNetwork(installed=True)
    network.graph_mutator = lambda flow, nodes: flow["nodes"][2].__setitem__("func", flow["nodes"][2]["func"] + "\nreturn msg;")
    with pytest.raises(NodeRedBackendError, match="changed during decision"):
        await _backend(network).async_calculate_tambur_decision(_request())

    network = _DecisionNetwork(installed=True)
    network.graph_mutator = lambda flow, nodes: nodes.append({
        "id": "foreign-node", "type": "function", "z": flow["id"],
        "name": "foreign", "func": "return msg;", "wires": [],
    })
    with pytest.raises(NodeRedBackendError, match="changed during decision"):
        await _backend(network).async_calculate_tambur_decision(_request())

    network = _DecisionNetwork(installed=True)
    network.response_mutator = lambda result: result["nextState"].__setitem__("authority", {"forged": "automatic"})
    with pytest.raises(NodeRedBackendError, match="decision response"):
        await _backend(network).async_calculate_tambur_decision(_request())


def test_full_bundle_provision_and_get_verification() -> None:
    asyncio.run(_case_full_bundle_provision_and_get_verification())


def test_internal_transport_checks_graph_before_and_after() -> None:
    asyncio.run(_case_internal_transport_checks_graph_before_and_after())


def test_external_references_fail_closed_and_unrelated_tabs_survive() -> None:
    asyncio.run(_case_external_references_fail_closed_and_unrelated_tabs_survive())


def test_dangling_references_block_first_install_without_writes() -> None:
    asyncio.run(_case_dangling_references_block_first_install_without_writes())


def test_real_service_editor_boundary_and_legacy_tambur_flow() -> None:
    asyncio.run(_case_real_service_rejects_reserved_flow_for_both_editor_modes())
    asyncio.run(_case_legacy_three_node_tambur_editor_still_works())


class TamburNodeRedDecisionReleaseTest(unittest.TestCase):
    """Keep all Task 2 acceptance groups in unittest release discovery."""

    def test_graph_and_schema_trust_boundary(self) -> None:
        test_full_graph_has_real_stages_exact_sources_and_no_command_nodes()
        test_every_graph_or_source_mutation_fails_closed()
        test_task1_schema_and_semantics_reject_nested_forgery_and_binding_mutations()
        test_old_public_source_editor_explicitly_rejects_decision_bundle()

    def test_profile_and_independent_authority(self) -> None:
        test_profile_boundaries_and_valid_ha_timezone_are_literal()
        test_existing_automatic_chandelier_tracks_brightness_then_cct_without_motion()
        test_manual_chandelier_stays_at_100_while_points_remain_independent()
        test_manual_hold_expiry_never_assigns_or_releases_authority()

    def test_mirror_and_absence_behavior(self) -> None:
        test_mirror_schedule_wraps_midnight_and_handover_is_ordered()
        test_confirmed_mirror_starts_night_fade_before_points_turn_off()
        test_failed_or_uncertain_mirror_receipt_keeps_main_and_does_not_retry("failed")
        test_failed_or_uncertain_mirror_receipt_keeps_main_and_does_not_retry("uncertain")
        test_protected_mirror_keeps_main_but_manual_on_mirror_can_handover()
        test_all_sensors_start_literal_absence_deadlines_and_unreliable_input_resets()
        test_fade_uses_fresh_snapshots_never_raises_and_points_follow_chandelier()
        test_missing_or_zero_fade_brightness_is_never_replaced_with_100()
        test_presence_cancels_absence_fade_and_arrival_obeys_day_and_night_rules()
        test_absence_fade_preempts_profile_refresh_and_keeps_its_deadline()
        test_absence_fade_middle_end_and_points_order_remain_literal()
        test_day_arrival_preempts_existing_fade_and_restores_profile()
        test_night_arrival_cancels_fade_once_and_marker_blocks_restart()
        test_night_transition_ignores_snapshot_only_and_invalid_sensor_arrivals()
        test_night_cancel_then_new_absence_waits_180_seconds_and_next_window_runs()
        test_recovery_restarts_absence_for_new_epoch()

    def test_real_internal_http_path(self) -> None:
        asyncio.run(_case_full_bundle_provision_and_get_verification())
        asyncio.run(_case_internal_transport_checks_graph_before_and_after())

    def test_external_graph_references_and_real_editor_boundary(self) -> None:
        asyncio.run(_case_external_references_fail_closed_and_unrelated_tabs_survive())
        asyncio.run(_case_dangling_references_block_first_install_without_writes())
        asyncio.run(_case_real_service_rejects_reserved_flow_for_both_editor_modes())
        asyncio.run(_case_legacy_three_node_tambur_editor_still_works())
