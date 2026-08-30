"""Regression tests for manually maintained managed-scenario functions."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).parents[1]
TAMBUR_SOURCE = ROOT / "tools" / "managed_scenarios" / "tambur_controller.js"
SHOWER_SOURCE = ROOT / "tools" / "managed_scenarios" / "shower_controller.js"

PRESENCE = "entity_156050daca86aa6c"
MOTION = "entity_10b78187426f8485"
SUN = "entity_6b9ccdab9bb484b2"
CHANDELIER = "entity_71859313239a14e4"
MIRROR = "entity_fbdf27871edb89bf"
SHOWER_PRESENCE = "entity_d1fb2cbf2a691bba"
SHOWER_HUMIDITY = "entity_fd3945cf1a2110f8"
SHOWER_MAIN = "entity_4be32416634e6416"
SHOWER_EXTRA = "entity_1fdcd8b244637246"
SHOWER_FAN = "entity_afef5df0e0cae309"
SHOWER_CABINET = "entity_e7a7c61eec7bdff8"


def _run_source(
    source_path: Path, *, timestamp: str, states: dict[str, object]
) -> dict[str, object]:
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
        ["node", "-e", harness, str(source_path), json.dumps(request)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _run_tambur(*, timestamp: str, states: dict[str, object]) -> dict[str, object]:
    return _run_source(TAMBUR_SOURCE, timestamp=timestamp, states=states)


def _run_shower(*, timestamp: str, states: dict[str, object]) -> dict[str, object]:
    return _run_source(SHOWER_SOURCE, timestamp=timestamp, states=states)


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
        self.assertEqual("chandelier_on", actions[0])
        self.assertEqual("chandelier_ownership_wait", actions[1])
        self.assertLess(
            actions.index("temperature_target"), actions.index("mirror_off")
        )
        self.assertEqual("mirror_off", actions[-1])

    def test_repeated_motion_does_not_reapply_matching_day_profile(self) -> None:
        payload = _run_tambur(
            timestamp="2026-08-27T11:45:00+06:00",
            states={
                PRESENCE: "on",
                MOTION: "on",
                SUN: "above_horizon",
                CHANDELIER: {
                    "state": "on",
                    "attributes": {"brightness": 191, "color_temp_kelvin": 2801},
                },
                MIRROR: "off",
            },
        )

        self.assertEqual("morning_day", payload["selectedBranch"])
        self.assertEqual(
            ["chandelier_on", "chandelier_ownership_wait"],
            _action_ids(payload),
        )

    def test_brightness_drift_does_not_rewrite_matching_temperature(self) -> None:
        payload = _run_tambur(
            timestamp="2026-08-27T11:45:00+06:00",
            states={
                PRESENCE: "on",
                MOTION: "on",
                SUN: "above_horizon",
                CHANDELIER: {
                    "state": "on",
                    "attributes": {"brightness": 128, "color_temp_kelvin": 2801},
                },
                MIRROR: "off",
            },
        )

        self.assertEqual(
            ["chandelier_on", "chandelier_ownership_wait", "brightness"],
            _action_ids(payload),
        )

    def test_temperature_drift_uses_one_direct_correction(self) -> None:
        payload = _run_tambur(
            timestamp="2026-08-27T11:45:00+06:00",
            states={
                PRESENCE: "on",
                MOTION: "on",
                SUN: "above_horizon",
                CHANDELIER: {
                    "state": "on",
                    "attributes": {"brightness": 191, "color_temp_kelvin": 3600},
                },
                MIRROR: "off",
            },
        )

        self.assertEqual(
            [
                "chandelier_on",
                "chandelier_ownership_wait",
                "temperature_target",
            ],
            _action_ids(payload),
        )

    def test_power_recovery_with_stale_temperature_forces_once(self) -> None:
        payload = _run_tambur(
            timestamp="2026-08-27T11:45:00+06:00",
            states={
                PRESENCE: "on",
                MOTION: "on",
                SUN: "above_horizon",
                CHANDELIER: {
                    "state": "off",
                    "attributes": {"brightness": 191, "color_temp_kelvin": 2801},
                },
                MIRROR: "off",
            },
        )

        self.assertEqual(
            [
                "chandelier_on",
                "chandelier_ownership_wait",
                "brightness",
                "temperature_prime",
                "temperature_wait_1",
                "temperature_target",
            ],
            _action_ids(payload),
        )

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

    def test_night_handoff_keeps_manual_priority_guard_when_mirror_is_on(self) -> None:
        payload = _run_tambur(
            timestamp="2026-08-27T23:30:00+06:00",
            states={
                PRESENCE: "on",
                MOTION: "off",
                SUN: "below_horizon",
                CHANDELIER: "on",
                MIRROR: "on",
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


class ManagedShowerSourceTest(unittest.TestCase):
    def test_absent_humid_fan_off_keeps_fan_on_while_delaying_light_off(self) -> None:
        payload = _run_shower(
            timestamp="2026-08-27T12:00:00+06:00",
            states={
                SHOWER_PRESENCE: "off", SHOWER_HUMIDITY: "60", SUN: "above_horizon",
                SHOWER_MAIN: "on", SHOWER_EXTRA: "off", SHOWER_FAN: "off", SHOWER_CABINET: "off",
            },
        )
        self.assertEqual(["set_fan_on", "absence_wait", "set_main_off"], _action_ids(payload))

    def test_absent_humid_fan_on_delays_only_light_off(self) -> None:
        payload = _run_shower(
            timestamp="2026-08-27T12:00:00+06:00",
            states={
                SHOWER_PRESENCE: "off", SHOWER_HUMIDITY: "60", SUN: "above_horizon",
                SHOWER_MAIN: "on", SHOWER_EXTRA: "on", SHOWER_FAN: "on", SHOWER_CABINET: "on",
            },
        )
        self.assertEqual(
            ["absence_wait", "set_main_off", "set_extra_off", "set_cabinet_off"],
            _action_ids(payload),
        )

    def test_absence_uses_one_five_minute_wait_for_light_and_fan(self) -> None:
        payload = _run_shower(
            timestamp="2026-08-27T12:00:00+06:00",
            states={
                SHOWER_PRESENCE: "off",
                SHOWER_HUMIDITY: "45",
                SUN: "above_horizon",
                SHOWER_MAIN: "on",
                SHOWER_EXTRA: "off",
                SHOWER_FAN: "on",
                SHOWER_CABINET: "off",
            },
        )

        self.assertEqual("light_off_5m__fan_off_5m", payload["selectedBranch"])
        self.assertEqual(
            ["absence_wait", "set_main_off", "set_fan_off"],
            _action_ids(payload),
        )
        self.assertEqual(300, payload["actions"][0]["delaySeconds"])

    def test_day_profile_always_claims_main_light_before_other_changes(self) -> None:
        payload = _run_shower(
            timestamp="2026-08-27T12:00:00+06:00",
            states={
                SHOWER_PRESENCE: "on",
                SHOWER_HUMIDITY: "45",
                SUN: "above_horizon",
                SHOWER_MAIN: "on",
                SHOWER_EXTRA: "on",
                SHOWER_FAN: "on",
                SHOWER_CABINET: "off",
            },
        )

        self.assertEqual("day_main__fan_hold", payload["selectedBranch"])
        self.assertEqual(
            ["set_main_on", "set_extra_off", "set_cabinet_on"],
            _action_ids(payload),
        )

    def test_presence_starts_fan_immediately_and_adds_cabinet_light(self) -> None:
        payload = _run_shower(
            timestamp="2026-08-27T12:00:00+06:00",
            states={
                SHOWER_PRESENCE: "on",
                SHOWER_HUMIDITY: "45",
                SUN: "above_horizon",
                SHOWER_MAIN: "off",
                SHOWER_EXTRA: "off",
                SHOWER_FAN: "off",
                SHOWER_CABINET: "off",
            },
        )

        self.assertEqual("day_main__fan_presence", payload["selectedBranch"])
        self.assertEqual(
            ["set_main_on", "set_cabinet_on", "set_fan_on"],
            _action_ids(payload),
        )
        self.assertFalse(
            any(action["type"] == "delay" for action in payload["actions"])
        )

    def test_unknown_presence_never_switches_light_or_fan(self) -> None:
        payload = _run_shower(
            timestamp="2026-08-27T12:00:00+06:00",
            states={
                SHOWER_PRESENCE: "unavailable",
                SHOWER_HUMIDITY: "unknown",
                SUN: "above_horizon",
                SHOWER_MAIN: "on",
                SHOWER_EXTRA: "on",
                SHOWER_FAN: "on",
                SHOWER_CABINET: "on",
            },
        )

        self.assertEqual("light_unknown__fan_hold", payload["selectedBranch"])
        self.assertEqual([], payload["actions"])
