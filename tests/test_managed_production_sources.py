from __future__ import annotations

import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).parents[1]
SCENARIOS = {
    "toilet_controller.js": ({"binary_sensor.datchik_dvizheniia_tualet_zaniatost": "on"}, {}),
    "bathroom_controller.js": ({"binary_sensor.bathroom_presence": "on"}, {}),
    "storage_controller.js": ({"binary_sensor.storage_motion": "on"}, {}),
    "cabinet_controller.js": ({"binary_sensor.office_presence": "on"}, {"light": "entity_0123456789abcdef"}),
    "curtains_controller.js": ({"cover.shtory_gostinaia": {"state": "open", "attributes": {"current_position": 80}}}, {}),
}


def test_all_new_production_sources_return_typed_positive_plans() -> None:
    harness = """
const fs = require('fs');
const source = fs.readFileSync(process.argv[1], 'utf8');
const request = JSON.parse(process.argv[2]);
const result = new Function('msg', source)({payload: request});
process.stdout.write(JSON.stringify(result.payload));
"""
    for filename, (raw_inputs, bindings) in SCENARIOS.items():
        inputs = {
            key: value if isinstance(value, dict) else {"state": value, "attributes": {}}
            for key, value in raw_inputs.items()
        }
        request = {"correlationId": "production-source-test", "inputs": inputs, "bindings": bindings, "context": {"trigger": {"source": "manual", "trigger_id": "sunset"}}}
        result = subprocess.run(
            ["node", "-e", harness, str(ROOT / "custom_components/hausman_hub/managed_scenarios" / filename), json.dumps(request)],
            check=True, capture_output=True, text=True,
        )
        payload = json.loads(result.stdout)
        assert payload["statusCode"] if "statusCode" in payload else True
        assert payload["scenarioId"].startswith("system-")
        assert payload["actions"], filename
