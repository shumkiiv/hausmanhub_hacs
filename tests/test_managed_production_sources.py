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
SERVER_ACTIONS = {
    "toilet_controller.js": {
        "targetId": "entity_5d95de599d2b5cec",
        "actionId": "turn_on",
        "value": None,
    },
    "bathroom_controller.js": {
        "targetId": "entity_c15f5df5382ee180",
        "actionId": "turn_on",
        "value": None,
    },
    "cabinet_controller.js": {
        "targetId": "entity_aeaf7c250c68e8c2",
        "actionId": "set_brightness_percent",
        "value": 65,
    },
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
        if filename in SERVER_ACTIONS:
            context = {
                "trigger": {
                    "source": "scenario_control",
                    "trigger_id": "light_action",
                },
                "controls": {
                    "state": {
                        "ready": True,
                        "transition": "light_action",
                        "action": SERVER_ACTIONS[filename],
                    }
                },
            }
        elif filename == "storage_controller.js":
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
        elif filename == "curtains_controller.js":
            context["trigger"] = {
                "source": "manual", "trigger_id": "manual_open_all"
            }
            context["controls"] = _curtain_controls()
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
        "context": {
            "trigger": {"source": "manual", "trigger_id": "manual_open_all"},
            "controls": _curtain_controls(),
        },
    }
    payload = _run_source("curtains_controller.js", request)
    positions = {action["targetId"]: action["value"] for action in payload["actions"]}
    assert positions["entity_2da2065add6e2168"] == 80
    assert positions["entity_9164132c7692d6f5"] == 90


def test_curtain_caps_are_read_from_each_server_snapshot() -> None:
    targets = SCENARIOS["curtains_controller.js"][0]
    inputs = {
        target: {"state": "closed", "attributes": {"current_position": 0}}
        for target in targets
    }
    controls = _curtain_controls()
    controls["policy"]["kitchenCoverCapPercent"] = 90
    controls["policy"]["cabinetCoverCapPercent"] = 95
    payload = _run_source("curtains_controller.js", {
        "inputs": inputs,
        "context": {
            "trigger": {"source": "manual", "trigger_id": "manual_open_all"},
            "controls": controls,
        },
    })
    positions = {action["targetId"]: action["value"] for action in payload["actions"]}
    assert positions["entity_2da2065add6e2168"] == 90
    assert positions["entity_9164132c7692d6f5"] == 95


def test_curtain_position_and_state_validation_is_strict_per_target() -> None:
    targets = tuple(SCENARIOS["curtains_controller.js"][0])
    invalid_values = (None, False, "", 40.5)
    for invalid in invalid_values:
        inputs = {
            target: {"state": "open", "attributes": {"current_position": 40}}
            for target in targets
        }
        inputs[targets[0]]["attributes"]["current_position"] = invalid
        payload = _run_source("curtains_controller.js", {
            "inputs": inputs,
            "context": {
                "trigger": {"source": "manual", "trigger_id": "manual_open_all"},
                "controls": _curtain_controls(),
            },
        })
        assert targets[0] not in {action["targetId"] for action in payload["actions"]}
        assert len(payload["actions"]) == 3

    inputs = {
        target: {"state": "open", "attributes": {"current_position": 40}}
        for target in targets
    }
    inputs[targets[0]]["state"] = "unexpected"
    payload = _run_source("curtains_controller.js", {
        "inputs": inputs,
        "context": {
            "trigger": {"source": "manual", "trigger_id": "manual_open_all"},
            "controls": _curtain_controls(),
        },
    })
    assert targets[0] not in {action["targetId"] for action in payload["actions"]}


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


def _curtain_controls(*, latched=(), trusted_sunrise=False) -> dict:
    targets = {
        target: {
            "entityId": f"cover.{target}",
            "generation": 1,
            "latched": target in latched,
            "automaticCloseAllowed": target not in latched,
            "morningOpenAllowed": trusted_sunrise,
        }
        for target in SCENARIOS["curtains_controller.js"][0]
    }
    return {
        "policyRevision": 0,
        "policy": {
            "kitchenCoverCapPercent": 80,
            "cabinetCoverCapPercent": 90,
        },
        "state": {
            "ready": True,
            "transition": "trusted_sunrise" if trusted_sunrise else "curtain_snapshot",
            "trustedSunrise": trusted_sunrise,
            "targets": targets,
        },
    }


def test_curtain_automatic_close_is_per_target_and_unknown_does_not_stop_others() -> None:
    targets = tuple(SCENARIOS["curtains_controller.js"][0])
    inputs = {
        target: {"state": "open", "attributes": {"current_position": 50}}
        for target in targets
    }
    inputs[targets[2]] = {"state": "unavailable", "attributes": {}}
    payload = _run_source("curtains_controller.js", {
        "inputs": inputs,
        "context": {
            "trigger": {"source": "schedule", "trigger_id": "sunset"},
            "controls": _curtain_controls(latched=(targets[0],)),
        },
    })
    assert {item["targetId"] for item in payload["actions"]} == {
        targets[1], targets[3]
    }


def test_only_server_snapshot_can_authorize_sunrise_and_it_opens_four() -> None:
    inputs = {
        target: {"state": "closed", "attributes": {"current_position": 0}}
        for target in SCENARIOS["curtains_controller.js"][0]
    }
    forged = _run_source("curtains_controller.js", {
        "inputs": inputs,
        "context": {"trigger": {"source": "manual", "trigger_id": "sunrise"}},
    })
    assert forged["actions"] == []

    trusted = _run_source("curtains_controller.js", {
        "inputs": inputs,
        "context": {
            "trigger": {
                "source": "curtain_schedule",
                "trigger_id": "curtain_trusted_sunrise",
            },
            "controls": _curtain_controls(trusted_sunrise=True),
        },
    })
    assert len(trusted["actions"]) == 4
    assert {item["value"] for item in trusted["actions"]} == {80, 90, 100}


def test_trusted_sunrise_keeps_unreleased_target_closed_and_opens_the_rest() -> None:
    targets = tuple(SCENARIOS["curtains_controller.js"][0])
    inputs = {
        target: {"state": "closed", "attributes": {"current_position": 0}}
        for target in targets
    }
    controls = _curtain_controls(trusted_sunrise=True)
    controls["state"]["targets"][targets[0]]["morningOpenAllowed"] = False
    trusted = _run_source("curtains_controller.js", {
        "inputs": inputs,
        "context": {
            "trigger": {
                "source": "curtain_schedule",
                "trigger_id": "curtain_trusted_sunrise",
            },
            "controls": controls,
        },
    })
    assert {action["targetId"] for action in trusted["actions"]} == set(targets[1:])


def test_explicit_manual_open_reaches_executor_even_when_position_is_reached() -> None:
    inputs = {
        target: {
            "state": "open",
            "attributes": {
                "current_position": 80 if target == "entity_2da2065add6e2168"
                else 90 if target == "entity_9164132c7692d6f5" else 100
            },
        }
        for target in SCENARIOS["curtains_controller.js"][0]
    }
    payload = _run_source("curtains_controller.js", {
        "inputs": inputs,
        "context": {
            "trigger": {"source": "manual", "trigger_id": "manual_open_all"},
            "controls": _curtain_controls(),
        },
    })
    assert len(payload["actions"]) == 4
