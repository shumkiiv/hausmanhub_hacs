"""Regression tests for manually maintained managed-scenario functions."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).parents[1]
TAMBUR_SOURCE = ROOT / "tools" / "managed_scenarios" / "tambur_controller.js"

PRESENCE = "entity_156050daca86aa6c"
MOTION = "entity_10b78187426f8485"
SUN = "entity_6b9ccdab9bb484b2"
CHANDELIER = "entity_71859313239a14e4"
MIRROR = "entity_fbdf27871edb89bf"


def _run_tambur(*, timestamp: str, states: dict[str, object]) -> dict[str, object]:
    request = {
        "correlationId": "managed-source-test",
        "context": {"timestampMs": int(datetime.fromisoformat(timestamp).timestamp() * 1000)},
        "inputs": {
            target_id: (
                value
                if isinstance(value, dict)
                else {"state": value, "attributes": {}}
            )
            for target_id, value in states.items()
        },
    }
    harness = """
const fs = require('fs');
const source = fs.readFileSync(process.argv[1], 'utf8');
const request = JSON.parse(process.argv[2]);
const execute = new Function('msg', source);
const result = execute({payload: request});
process.stdout.write(JSON.stringify(result.payload));
"""
    completed = subprocess.run(
        ["node", "-e", harness, str(TAMBUR_SOURCE), json.dumps(request)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _action_ids(payload: dict[str, object]) -> list[str]:
    return [str(item["id"]) for item in payload["actions"]]


class ManagedTamburSourceTest(unittest.TestCase):
    def test_day_handoff_turns_chandelier_on_before_mirror_off(self) -> None:
        payload = _run_tambur(
            timestamp="2026-08-27T10:00:00+06:00",
            states={
                PRESENCE: "on",
                MOTION: "off",
                SUN: "above_horizon",
                CHANDELIER: "off",
                MIRROR: "on",
            },
        )

        actions = _action_ids(payload)
        self.assertEqual("morning_day", payload["selectedBranch"])
        self.assertEqual("brightness", actions[0])
        self.assertLess(
            actions.index("temperature_wait_1"), actions.index("mirror_off")
        )
        self.assertEqual("mirror_off", actions[-1])

    def test_night_handoff_confirms_mirror_before_chandelier_off(self) -> None:
        payload = _run_tambur(
            timestamp="2026-08-27T23:30:00+06:00",
            states={
                PRESENCE: "on",
                MOTION: "off",
                SUN: "below_horizon",
                CHANDELIER: "on",
                MIRROR: "off",
            },
        )

        self.assertEqual("night_mirror", payload["selectedBranch"])
        self.assertEqual(
            ["mirror_on", "mirror_handoff_wait", "chandelier_off"],
            _action_ids(payload),
        )

    def test_uncertain_presence_never_turns_either_light_off(self) -> None:
        payload = _run_tambur(
            timestamp="2026-08-27T10:00:00+06:00",
            states={
                PRESENCE: "unknown",
                MOTION: "off",
                SUN: "above_horizon",
                CHANDELIER: "on",
                MIRROR: "on",
            },
        )

        self.assertEqual("presence_uncertain", payload["selectedBranch"])
        self.assertEqual([], payload["actions"])
