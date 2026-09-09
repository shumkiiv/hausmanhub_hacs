"""Executable safety checks for the staged Tambur Node-RED decision bundle."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import unittest

import pytest

from custom_components.hausman_hub.application.scenario_node_red_decision import (
    TAMBUR_DECISION_SCENARIO_ID,
    build_tambur_decision_bundle,
    prepare_tambur_decision_bundle,
    validate_tambur_decision,
    verify_tambur_decision_bundle,
)


ROOT = Path(__file__).parents[1]


def _input(*, minute: int = 600, presence: str = "on") -> dict[str, object]:
    return {
        "contract": {"name": "hausman-node-red-decision-input", "version": 1},
        "correlationId": "tambur.test.1", "scenarioId": TAMBUR_DECISION_SCENARIO_ID,
        "controllerVersion": 1, "settingsRevision": 7, "snapshotRevision": 31,
        "observationEpoch": 4, "issuedAtMs": 1_800_000_000_000,
        "expiresAtMs": 1_800_000_060_000,
        "event": {"id": "presence.1", "kind": "sensor", "observedAtMs": 1_800_000_000_000},
        "clock": {"nowMs": 1_800_000_000_000, "timezone": "Asia/Omsk", "localDate": "2027-01-15", "minutesOfDay": minute, "sunsetAtMs": 1_800_010_800_000},
        "bindings": {"chandelier": "lamp.chandelier", "points": "lamp.points", "mirror": "lamp.mirror", "power": "switch.power", "presenceSensors": ["sensor.presence"]},
        "settings": {"morningStart": "09:00", "morningEnd": "10:00", "eveningLatestStart": "21:00", "mainOff": "23:00", "mirrorOff": "01:00", "minPercent": 5, "maxPercent": 80, "dayKelvin": 3000, "eveningKelvin": 2200, "absenceDaySeconds": 600, "absenceNightSeconds": 180, "fadeSeconds": 20, "manualOffMinSeconds": 600, "manualOffAbsenceSeconds": 30, "manualOnHoldSeconds": 3600},
        "observations": {"lamp.chandelier": {"state": "off", "revision": 11, "observedAtMs": 1_800_000_000_000, "fresh": True, "continuityEpoch": 4}, "lamp.points": {"state": "off", "revision": 12, "observedAtMs": 1_800_000_000_000, "fresh": True, "continuityEpoch": 4}, "sensor.presence": {"state": presence, "revision": 13, "observedAtMs": 1_800_000_000_000, "fresh": True, "continuityEpoch": 4}},
        "authority": {"lamp.chandelier": {"owner": "none", "generation": 2, "protectionActive": False}, "lamp.points": {"owner": "none", "generation": 3, "protectionActive": False}, "lamp.mirror": {"owner": "none", "generation": 4, "protectionActive": False}},
        "durable": {"revision": 9, "phase": "idle", "phaseStartedAtMs": None, "absenceSinceMs": None, "absenceEpoch": None, "fadeStartPercent": None, "fadeStartedAtMs": None, "fadeReason": None, "pendingReceiptId": None, "wakeups": []}, "receipts": [],
    }


def _run_node(source: str, request: dict[str, object]) -> dict[str, object]:
    completed = subprocess.run(
        ["node", "-e", "const s=process.argv[1], p=JSON.parse(process.argv[2]); const r=(new Function('msg',s))({payload:p}); process.stdout.write(JSON.stringify(r.payload));", source, json.dumps(request)],
        check=True, capture_output=True, text=True,
    )
    return json.loads(completed.stdout)


def test_bundle_is_a_closed_readable_graph_and_runs_in_node() -> None:
    """Removing a decision node or a graph edge must make the bundle untrusted."""
    bundle = prepare_tambur_decision_bundle()
    assert bundle["scenarioId"] == TAMBUR_DECISION_SCENARIO_ID
    assert [node["name"] for node in bundle["nodes"]] == ["Вход", "Проверка", "Приоритет", "Профиль", "Ожидания", "Следующий шаг", "Диагностика", "Ответ"]
    source = bundle["nodes"][6]["func"]
    decision = _run_node(source, _input())
    validate_tambur_decision(_input(), decision)
    assert decision["reasonCode"] == "presence_day"
    assert decision["action"]["targetId"] == "lamp.chandelier"
    assert decision["action"]["actionId"] == "set_brightness_percent"
    assert verify_tambur_decision_bundle(bundle) == bundle["topologyHash"]
    broken = {**bundle, "nodes": bundle["nodes"][:-1]}
    with pytest.raises(ValueError, match="topology"):
        build_tambur_decision_bundle(broken)


def test_manual_authority_is_per_target_and_night_blocks_new_automatic_light() -> None:
    """Collapsing ownership or dropping the 23:00 boundary would change this plan."""
    bundle = build_tambur_decision_bundle()
    source = bundle["nodes"][6]["func"]
    manual = _input()
    manual["authority"]["lamp.chandelier"] = {"owner": "manual", "generation": 2, "protectionActive": False}
    decision = _run_node(source, manual)
    validate_tambur_decision(manual, decision)
    assert decision["reasonCode"] == "presence_points"
    assert decision["action"]["targetId"] == "lamp.points"
    night = _input(minute=23 * 60 + 1)
    decision = _run_node(source, night)
    validate_tambur_decision(night, decision)
    assert decision["reasonCode"] == "automatic_on_forbidden"
    assert decision["action"] is None


class TamburNodeRedDecisionReleaseTest(unittest.TestCase):
    """Keep the behavioural contract in the normal unittest release harness."""

    def test_closed_graph_and_day_decision(self) -> None:
        test_bundle_is_a_closed_readable_graph_and_runs_in_node()

    def test_authority_and_night_decision(self) -> None:
        test_manual_authority_is_per_target_and_night_blocks_new_automatic_light()
