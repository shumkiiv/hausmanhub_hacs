from __future__ import annotations

import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).parents[1]
SCENARIOS = {
    "toilet_controller.js": ({"entity_ce73f88bda2e6812": "on"}, {}),
    "bathroom_controller.js": ({"entity_a591e035e3e5b34f": "on"}, {}),
    "storage_controller.js": ({
        "entity_00dcf0ebdc0bc6cb": "on",
        "entity_0ec37ef18b4b39a6": "off",
    }, {}),
    "cabinet_controller.js": ({"entity_5f3b4436fb7b6f2b": "on"}, {"light": "entity_aeaf7c250c68e8c2"}),
    "curtains_controller.js": ({
        "entity_8746cfd7f6f7103d": {"state": "open", "attributes": {"current_position": 40}},
        "entity_2da2065add6e2168": {"state": "open", "attributes": {"current_position": 40}},
        "entity_1e0b476b7d082cc0": {"state": "open", "attributes": {"current_position": 40}},
        "entity_9164132c7692d6f5": {"state": "open", "attributes": {"current_position": 40}},
    }, {}),
}


def _run_source(filename: str, request: dict) -> dict:
    harness = """
const fs = require('fs');
const source = fs.readFileSync(process.argv[1], 'utf8');
const request = JSON.parse(process.argv[2]);
const result = new Function('msg', source)({payload: request});
process.stdout.write(JSON.stringify(result.payload));
"""
    result = subprocess.run(
        ["node", "-e", harness, str(ROOT / "custom_components/hausman_hub/managed_scenarios" / filename), json.dumps(request)],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


def test_all_new_production_sources_return_typed_positive_plans() -> None:
    for filename, (raw_inputs, bindings) in SCENARIOS.items():
        inputs = {
            key: value if isinstance(value, dict) else {"state": value, "attributes": {}}
            for key, value in raw_inputs.items()
        }
        context = {"trigger": {"source": "manual", "trigger_id": "sunset"}}
        if filename == "storage_controller.js":
            context["controls"] = {
                "policyRevision": 0,
                "policy": {
                    "storageExhaustTargetId": None,
                    "storageExhaustTimes": ["11:00", "20:00"],
                    "storageExhaustRunSeconds": 1800,
                },
                "state": {
                    "ready": True,
                    "transition": "storage_light_on",
                    "generation": 1,
                    "evidence": {
                        "motion": "on",
                        "presence": None,
                        "light": "off",
                        "ownershipRevision": None,
                    },
                },
            }
        request = {"correlationId": "production-source-test", "inputs": inputs, "bindings": bindings, "context": context}
        payload = _run_source(filename, request)
        assert payload["statusCode"] if "statusCode" in payload else True
        assert payload["scenarioId"].startswith("system-")
        assert payload["actions"], filename


def test_curtain_positive_plan_enforces_kitchen_and_office_caps() -> None:
    request = {
        "inputs": {
            target: {"state": "open", "attributes": {"current_position": 100}}
            for target in SCENARIOS["curtains_controller.js"][0]
        },
        "context": {"trigger": {"source": "manual", "trigger_id": "manual_open_all"}},
    }
    payload = _run_source("curtains_controller.js", request)
    positions = {action["targetId"]: action["value"] for action in payload["actions"]}
    assert positions["entity_2da2065add6e2168"] == 80
    assert positions["entity_9164132c7692d6f5"] == 90


def test_old_or_unknown_bindings_fail_closed() -> None:
    cabinet = _run_source("cabinet_controller.js", {
        "inputs": {"binary_sensor.office_presence": {"state": "on", "attributes": {}}},
        "bindings": {"light": "entity_0123456789abcdef"},
        "context": {"trigger": {"source": "device", "trigger_id": "motion_changed"}},
    })
    curtains = _run_source("curtains_controller.js", {
        "inputs": {"cover.shtory_gostinaia": {"state": "open", "attributes": {"current_position": 80}}},
        "context": {"trigger": {"source": "device", "trigger_id": "sunrise"}},
    })
    assert cabinet["actions"] == []
    assert curtains["actions"] == []
