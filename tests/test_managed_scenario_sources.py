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
SMALL_CORRIDOR_SOURCE = ROOT / "tools" / "managed_scenarios" / "small_corridor_controller.js"

PRESENCE = "entity_156050daca86aa6c"
MOTION = "entity_10b78187426f8485"
SUN = "entity_6b9ccdab9bb484b2"
CHANDELIER = "entity_71859313239a14e4"
MIRROR = "entity_fbdf27871edb89bf"
OUTSIDE_LUX = "entity_5f3b4436fb7b6f2b"
POINTS = "entity_cd0098e5ff95da46"
TAMBUR_POWER = "entity_b47991988cc6b9f3"
ENTRY_DOOR = "entity_170c7a4e2505b803"
SMALL_MOTION = "entity_90417aada6a33491"
SMALL_LOCAL_LIGHT = "entity_c9d6bc67f172f30d"
SMALL_RELAY = "entity_ff0244d6b760be7e"
SMALL_CHANDELIER = "entity_9ed909332fdaa8fd"
SHOWER_PRESENCE = "entity_d1fb2cbf2a691bba"
SHOWER_HUMIDITY = "entity_fd3945cf1a2110f8"
SHOWER_MAIN = "entity_4be32416634e6416"
SHOWER_EXTRA = "entity_1fdcd8b244637246"
SHOWER_FAN = "entity_afef5df0e0cae309"
SHOWER_CABINET = "entity_e7a7c61eec7bdff8"


def _run_source(
    source_path: Path,
    *,
    timestamp: str,
    states: dict[str, object],
    trigger: dict[str, object] | None = None,
) -> dict[str, object]:
    request = {
        "correlationId": "managed-source-test",
        "context": {
            "timestampMs": int(datetime.fromisoformat(timestamp).timestamp() * 1000),
            "trigger": trigger or {},
        },
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


def _run_tambur(
    *,
    timestamp: str,
    states: dict[str, object],
    trigger: dict[str, object] | None = None,
) -> dict[str, object]:
    return _run_source(TAMBUR_SOURCE, timestamp=timestamp, states=states, trigger=trigger)


def _run_shower(*, timestamp: str, states: dict[str, object]) -> dict[str, object]:
    return _run_source(SHOWER_SOURCE, timestamp=timestamp, states=states)


def _run_small_corridor(
    *,
    timestamp: str,
    states: dict[str, object],
    trigger: dict[str, object] | None = None,
) -> dict[str, object]:
    return _run_source(
        SMALL_CORRIDOR_SOURCE,
        timestamp=timestamp,
        states=states,
        trigger=trigger,
    )


def _action_ids(payload: dict[str, object]) -> list[str]:
    return [str(item["id"]) for item in payload["actions"]]


class ManagedTamburSourceTest(unittest.TestCase):
    def base_states(self) -> dict[str, object]:
        return {
            PRESENCE: "off", MOTION: "off", SUN: "above_horizon",
            OUTSIDE_LUX: "500", CHANDELIER: "off", POINTS: "off",
            MIRROR: "off", TAMBUR_POWER: "on", ENTRY_DOOR: "locked",
        }

    def test_day_uses_full_neutral_profile_and_points(self) -> None:
        states = self.base_states()
        states[PRESENCE] = "on"
        payload = _run_tambur(timestamp="2026-08-27T10:00:00+06:00", states=states)
        self.assertEqual("sunrise_to_sunset", payload["selectedBranch"])
        self.assertEqual(
            ["chandelier_on", "chandelier_ownership_wait", "brightness", "temperature_target", "points_on"],
            _action_ids(payload),
        )
        values = {action["id"]: action.get("value") for action in payload["actions"]}
        self.assertEqual(100, values["brightness"])
        self.assertEqual(3000, values["temperature_target"])

    def test_entry_door_uses_day_profile_without_waiting_for_presence(self) -> None:
        states = self.base_states()
        states[ENTRY_DOOR] = "unlocked"
        payload = _run_tambur(
            timestamp="2026-08-27T10:00:00+06:00",
            states=states,
            trigger={
                "source": "device_state",
                "trigger_id": "entry_door_unlocked",
            },
        )

        self.assertEqual("entry_sunrise_to_sunset", payload["selectedBranch"])
        self.assertEqual(
            [
                "chandelier_on",
                "chandelier_ownership_wait",
                "brightness",
                "temperature_target",
                "points_on",
            ],
            _action_ids(payload),
        )

    def test_entry_door_uses_evening_lux_profile(self) -> None:
        states = self.base_states()
        states.update({ENTRY_DOOR: "unlocked", SUN: "below_horizon", OUTSIDE_LUX: "5"})
        payload = _run_tambur(
            timestamp="2026-08-27T22:00:00+06:00",
            states=states,
            trigger={
                "source": "device_state",
                "trigger_id": "entry_door_unlocked",
            },
        )

        self.assertEqual("entry_after_sunset_dark", payload["selectedBranch"])
        values = {action["id"]: action.get("value") for action in payload["actions"]}
        self.assertEqual((85, 6500), (values["brightness"], values["temperature_target"]))

    def test_darkness_increases_brightness_and_physical_warmth(self) -> None:
        profiles = []
        for lux in (500, 200, 50, 5):
            states = self.base_states()
            states.update({PRESENCE: "on", SUN: "below_horizon", OUTSIDE_LUX: str(lux)})
            payload = _run_tambur(timestamp="2026-08-27T22:00:00+06:00", states=states)
            values = {action["id"]: action.get("value") for action in payload["actions"]}
            profiles.append((values["brightness"], values["temperature_target"]))
        self.assertEqual([(35, 3600), (50, 4400), (70, 5200), (85, 6500)], profiles)

    def test_manual_switch_forces_full_neutral_without_points(self) -> None:
        states = self.base_states()
        payload = _run_tambur(
            timestamp="2026-08-27T23:30:00+06:00",
            states=states,
            trigger={"source": "manual", "trigger_id": "manual_chandelier_on"},
        )
        self.assertEqual("manual_chandelier", payload["selectedBranch"])
        self.assertNotIn("points_on", _action_ids(payload))
        values = {action["id"]: action.get("value") for action in payload["actions"]}
        self.assertEqual((100, 3000), (values["brightness"], values["temperature_target"]))

    def test_absence_waits_then_fades_both_automatic_loads(self) -> None:
        states = self.base_states()
        states.update({
            CHANDELIER: {"state": "on", "attributes": {"brightness": 255}},
            POINTS: "on",
        })
        payload = _run_tambur(
            timestamp="2026-08-27T12:00:00+06:00",
            states=states,
            trigger={"trigger_id": "motion_changed"},
        )
        actions = _action_ids(payload)
        self.assertEqual("absence_wait", actions[0])
        self.assertEqual(["chandelier_off", "points_off"], actions[-2:])
        self.assertEqual([75, 50, 25, 5], [a["value"] for a in payload["actions"] if str(a["id"]).startswith("fade_") and not str(a["id"]).startswith("fade_wait")])

    def test_lux_change_does_not_restart_absence_timer(self) -> None:
        states = self.base_states()
        states.update({CHANDELIER: "on", POINTS: "on"})
        payload = _run_tambur(
            timestamp="2026-08-27T12:05:00+06:00",
            states=states,
            trigger={"trigger_id": "outside_lux_changed"},
        )
        self.assertEqual([], payload["actions"])

    def test_mirror_has_only_23_and_01_schedule_branches(self) -> None:
        states = self.base_states()
        on = _run_tambur(timestamp="2026-08-27T23:00:00+06:00", states=states, trigger={"trigger_id": "mirror_window_start"})
        self.assertEqual(["mirror_on"], _action_ids(on))
        states[MIRROR] = "on"
        off = _run_tambur(timestamp="2026-08-28T01:00:00+06:00", states=states, trigger={"trigger_id": "mirror_window_end"})
        self.assertEqual(["mirror_off"], _action_ids(off))

    def test_uncertain_presence_never_turns_lighting_off(self) -> None:
        states = self.base_states()
        states.update({PRESENCE: "unknown", CHANDELIER: "on", POINTS: "on"})
        payload = _run_tambur(timestamp="2026-08-27T10:00:00+06:00", states=states)
        self.assertEqual("presence_uncertain", payload["selectedBranch"])
        self.assertEqual([], payload["actions"])


class ManagedSmallCorridorSourceTest(unittest.TestCase):
    def base_states(self) -> dict[str, object]:
        return {SMALL_MOTION: "off", SUN: "above_horizon", OUTSIDE_LUX: "500",
            SMALL_LOCAL_LIGHT: "dark", SMALL_RELAY: "off", SMALL_CHANDELIER: "off"}

    def test_motion_day_turns_on_full_neutral(self) -> None:
        states = self.base_states()
        states[SMALL_MOTION] = "on"
        payload = _run_small_corridor(timestamp="2026-08-27T10:00:00+06:00", states=states)
        self.assertEqual("day", payload["selectedBranch"])
        self.assertEqual(["chandelier_on", "ownership_wait", "brightness", "temperature"], _action_ids(payload))

    def test_absence_keeps_light_five_minutes_then_fades(self) -> None:
        states = self.base_states()
        states.update({SMALL_RELAY: "on", SMALL_CHANDELIER: {"state": "on", "attributes": {"brightness": 255}}})
        payload = _run_small_corridor(
            timestamp="2026-08-27T20:00:00+06:00",
            states=states,
            trigger={"trigger_id": "motion_changed"},
        )
        actions = _action_ids(payload)
        self.assertEqual("absence_wait", actions[0])
        self.assertEqual(["chandelier_off", "relay_off"], actions[-2:])

    def test_lux_change_does_not_restart_five_minute_timer(self) -> None:
        states = self.base_states()
        states.update({SMALL_RELAY: "on", SMALL_CHANDELIER: "on"})
        payload = _run_small_corridor(
            timestamp="2026-08-27T20:01:00+06:00",
            states=states,
            trigger={"trigger_id": "outside_lux_changed"},
        )
        self.assertEqual([], payload["actions"])

    def test_manual_switch_claims_relay_and_full_neutral_chandelier(self) -> None:
        states = self.base_states()
        states[SMALL_RELAY] = "on"
        payload = _run_small_corridor(
            timestamp="2026-08-28T00:30:00+06:00",
            states=states,
            trigger={"source": "manual", "trigger_id": "manual_chandelier_on"},
        )
        self.assertEqual("manual_chandelier", payload["selectedBranch"])
        self.assertEqual(
            ["relay_on", "chandelier_on", "ownership_wait", "brightness", "temperature"],
            _action_ids(payload),
        )

    def test_midnight_to_sunrise_never_turns_on(self) -> None:
        states = self.base_states()
        states.update({SMALL_MOTION: "on", SUN: "below_horizon"})
        payload = _run_small_corridor(timestamp="2026-08-28T00:30:00+06:00", states=states)
        self.assertEqual("night_blocked_until_sunrise", payload["selectedBranch"])
        self.assertEqual([], payload["actions"])

    def test_sunrise_reenables_motion_profile(self) -> None:
        states = self.base_states()
        states.update({SMALL_MOTION: "on", SUN: "above_horizon"})
        payload = _run_small_corridor(timestamp="2026-08-28T06:30:00+06:00", states=states, trigger={"trigger_id": "sunrise"})
        self.assertEqual("day", payload["selectedBranch"])
        self.assertIn("chandelier_on", _action_ids(payload))


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
