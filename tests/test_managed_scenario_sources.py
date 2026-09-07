"""Regression tests for manually maintained managed-scenario functions."""

from __future__ import annotations

import json
import itertools
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
SMALL_RELAY = "entity_4be32416634e6416"
SMALL_CHANDELIER = "entity_9ed909332fdaa8fd"
SHOWER_PRESENCE = "entity_d1fb2cbf2a691bba"
SHOWER_HUMIDITY = "entity_fd3945cf1a2110f8"
SHOWER_MAIN = "entity_46174e1ff9913212"
SHOWER_MAIN_NEW = "entity_46174e1ff9913212"
SHOWER_EXTRA = "entity_1fdcd8b244637246"
SHOWER_FAN = "entity_afef5df0e0cae309"
SHOWER_CABINET = "entity_e7a7c61eec7bdff8"


def _run_source(
    source_path: Path,
    *,
    timestamp: str,
    states: dict[str, object],
    trigger: dict[str, object] | None = None,
    controls: dict[str, object] | None = None,
) -> dict[str, object]:
    request = {
        "correlationId": "managed-source-test",
        "context": {
            "timestampMs": int(datetime.fromisoformat(timestamp).timestamp() * 1000),
            "trigger": trigger or {},
            "controls": controls or {},
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
    controls: dict[str, object] | None = None,
) -> dict[str, object]:
    return _run_source(
        TAMBUR_SOURCE,
        timestamp=timestamp,
        states=states,
        trigger=trigger,
        controls=controls,
    )


def _run_shower(
    *,
    timestamp: str,
    states: dict[str, object],
    trigger: dict[str, object] | None = None,
    controls: dict[str, object] | None = None,
) -> dict[str, object]:
    return _run_source(
        SHOWER_SOURCE,
        timestamp=timestamp,
        states=states,
        trigger=trigger,
        controls=controls,
    )


def _run_small_corridor(
    *,
    timestamp: str,
    states: dict[str, object],
    trigger: dict[str, object] | None = None,
    controls: dict[str, object] | None = None,
) -> dict[str, object]:
    return _run_source(
        SMALL_CORRIDOR_SOURCE,
        timestamp=timestamp,
        states=states,
        trigger=trigger,
        controls=controls,
    )


def _action_ids(payload: dict[str, object]) -> list[str]:
    return [str(item["id"]) for item in payload["actions"]]


def _typed(binding: str, trigger_id: str, typed: str, direct: str) -> dict[str, object]:
    return {
        "source": "manual", "trigger_id": trigger_id, "recovery": False,
        "binding": binding, "typed_intent": typed,
        "direct_user_intent": direct, "intent_receipt_id": "receipt.test",
        "raw_subtype": trigger_id, "dedup_disposition": "accepted",
        "correlation_id": "receipt.test",
    }


def _controls(
    target_id: str,
    action_id: str,
    value: int | None = None,
    *,
    transition: str = "light_action",
) -> dict[str, object]:
    return {
        "policyRevision": 3,
        "policy": {},
        "state": {
            "ready": True,
            "generation": 7,
            "transition": transition,
            "evidence": {"motion": "on", "presence": "off"},
            "action": {
                "targetId": target_id,
                "actionId": action_id,
                "value": value,
            },
        },
    }


class ManagedTamburSourceTest(unittest.TestCase):
    def test_direct_group_on_only_enables_off_member_without_profile_rewrite(self) -> None:
        states = self.base_states()
        states.update({
            CHANDELIER: {"state": "on", "attributes": {"brightness": 13, "color_temp_kelvin": 6500}},
            POINTS: "off",
            MIRROR: "on",
        })
        payload = _run_tambur(
            timestamp="2026-08-27T12:00:00+06:00", states=states,
            trigger=_typed("tambur-light-group", "on_down", "on", "on"),
        )
        self.assertEqual(["points_on"], _action_ids(payload))
        self.assertNotIn(MIRROR, {item.get("targetId") for item in payload["actions"]})

    def test_typed_manual_group_off_turns_off_both_without_mirror(self) -> None:
        states = self.base_states()
        states.update({CHANDELIER: "on", POINTS: "on", MIRROR: "on"})
        payload = _run_tambur(
            timestamp="2026-08-27T12:00:00+06:00", states=states,
            trigger=_typed("tambur-light-group", "off_up", "off", "off"),
        )
        self.assertEqual(["chandelier_off", "points_off"], _action_ids(payload))

    def test_resolved_direct_off_ignores_untrusted_payload_identity(self) -> None:
        states = self.base_states()
        states.update({CHANDELIER: "unknown", POINTS: "off"})
        payload = _run_tambur(
            timestamp="2026-08-27T12:00:00+06:00", states=states,
            trigger=_typed("tambur-light-group", "toggle_down", "toggle", "off"),
        )
        self.assertEqual(["chandelier_off", "points_off"], _action_ids(payload))

    def base_states(self) -> dict[str, object]:
        return {
            PRESENCE: "off", MOTION: "off", SUN: "above_horizon",
            OUTSIDE_LUX: "500", CHANDELIER: "off", POINTS: "off",
            MIRROR: "off", TAMBUR_POWER: "on", ENTRY_DOOR: "locked",
        }

    def test_sensor_snapshot_cannot_create_a_profile_without_server_action(self) -> None:
        states = self.base_states()
        states[PRESENCE] = "on"
        payload = _run_tambur(timestamp="2026-08-27T10:00:00+06:00", states=states)
        self.assertEqual("stale_generation", payload["selectedBranch"])
        self.assertEqual([], _action_ids(payload))

    def test_entry_door_cannot_create_a_profile_without_server_action(self) -> None:
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

        self.assertEqual("stale_generation", payload["selectedBranch"])
        self.assertEqual([], _action_ids(payload))

    def test_evening_inputs_cannot_create_a_profile_without_server_action(self) -> None:
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

        self.assertEqual("stale_generation", payload["selectedBranch"])
        self.assertEqual([], _action_ids(payload))

    def test_lux_values_never_author_server_actions(self) -> None:
        for lux in (500, 200, 50, 5):
            states = self.base_states()
            states.update({PRESENCE: "on", SUN: "below_horizon", OUTSIDE_LUX: str(lux)})
            payload = _run_tambur(timestamp="2026-08-27T22:00:00+06:00", states=states)
            self.assertEqual([], _action_ids(payload))

    def test_untyped_manual_switch_does_not_overwrite_light_parameters(self) -> None:
        states = self.base_states()
        payload = _run_tambur(
            timestamp="2026-08-27T23:30:00+06:00",
            states=states,
            trigger={"source": "manual", "trigger_id": "manual_chandelier_on"},
        )
        self.assertEqual("stale_generation", payload["selectedBranch"])
        self.assertEqual([], _action_ids(payload))

    def test_absence_inputs_do_not_author_client_side_fade(self) -> None:
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
        self.assertEqual([], _action_ids(payload))

    def test_lux_change_does_not_restart_absence_timer(self) -> None:
        states = self.base_states()
        states.update({CHANDELIER: "on", POINTS: "on"})
        payload = _run_tambur(
            timestamp="2026-08-27T12:05:00+06:00",
            states=states,
            trigger={"trigger_id": "outside_lux_changed"},
        )
        self.assertEqual([], payload["actions"])

    def test_clock_input_does_not_author_mirror_actions(self) -> None:
        states = self.base_states()
        on = _run_tambur(timestamp="2026-08-27T23:00:00+06:00", states=states, trigger={"trigger_id": "mirror_window_start"})
        self.assertEqual([], _action_ids(on))
        states[MIRROR] = "on"
        off = _run_tambur(timestamp="2026-08-28T01:00:00+06:00", states=states, trigger={"trigger_id": "mirror_window_end"})
        self.assertEqual([], _action_ids(off))

    def test_uncertain_presence_never_turns_lighting_off(self) -> None:
        states = self.base_states()
        states.update({PRESENCE: "unknown", CHANDELIER: "on", POINTS: "on"})
        payload = _run_tambur(timestamp="2026-08-27T10:00:00+06:00", states=states)
        self.assertEqual("stale_generation", payload["selectedBranch"])
        self.assertEqual([], payload["actions"])

    def test_exact_server_action_is_forwarded_without_profile_expansion(self) -> None:
        payload = _run_tambur(
            timestamp="2026-08-27T22:00:00+06:00",
            states=self.base_states(),
            controls=_controls(CHANDELIER, "set_brightness_percent", 72),
        )
        self.assertEqual(["server_action"], _action_ids(payload))
        self.assertEqual(72, payload["actions"][0]["value"])


class ManagedSmallCorridorSourceTest(unittest.TestCase):
    def base_states(self) -> dict[str, object]:
        return {SMALL_MOTION: "off", SUN: "above_horizon", OUTSIDE_LUX: "500",
            SMALL_LOCAL_LIGHT: "dark", SMALL_RELAY: "off", SMALL_CHANDELIER: "off"}

    def test_motion_snapshot_cannot_author_profile_without_server_action(self) -> None:
        states = self.base_states()
        states[SMALL_MOTION] = "on"
        payload = _run_small_corridor(timestamp="2026-08-27T10:00:00+06:00", states=states)
        self.assertEqual("stale_generation", payload["selectedBranch"])
        self.assertEqual([], _action_ids(payload))

    def test_absence_snapshot_cannot_author_client_side_fade(self) -> None:
        states = self.base_states()
        states.update({SMALL_RELAY: "on", SMALL_CHANDELIER: {"state": "on", "attributes": {"brightness": 255}}})
        payload = _run_small_corridor(
            timestamp="2026-08-27T20:00:00+06:00",
            states=states,
            trigger={"trigger_id": "motion_changed"},
        )
        self.assertEqual([], _action_ids(payload))

    def test_lux_change_does_not_restart_five_minute_timer(self) -> None:
        states = self.base_states()
        states.update({SMALL_RELAY: "on", SMALL_CHANDELIER: "on"})
        payload = _run_small_corridor(
            timestamp="2026-08-27T20:01:00+06:00",
            states=states,
            trigger={"trigger_id": "outside_lux_changed"},
        )
        self.assertEqual([], payload["actions"])

    def test_untyped_manual_switch_does_not_rewrite_profile(self) -> None:
        states = self.base_states()
        states[SMALL_RELAY] = "on"
        payload = _run_small_corridor(
            timestamp="2026-08-28T00:30:00+06:00",
            states=states,
            trigger={"source": "manual", "trigger_id": "manual_chandelier_on"},
        )
        self.assertEqual("stale_generation", payload["selectedBranch"])
        self.assertEqual([], _action_ids(payload))

    def test_midnight_to_sunrise_never_turns_on(self) -> None:
        states = self.base_states()
        states.update({SMALL_MOTION: "on", SUN: "below_horizon"})
        payload = _run_small_corridor(timestamp="2026-08-28T00:30:00+06:00", states=states)
        self.assertEqual("stale_generation", payload["selectedBranch"])
        self.assertEqual([], payload["actions"])

    def test_sunrise_reenables_motion_profile(self) -> None:
        states = self.base_states()
        states.update({SMALL_MOTION: "on", SUN: "above_horizon"})
        payload = _run_small_corridor(timestamp="2026-08-28T06:30:00+06:00", states=states, trigger={"trigger_id": "sunrise"})
        self.assertEqual("stale_generation", payload["selectedBranch"])
        self.assertEqual([], _action_ids(payload))

    def test_exact_server_action_is_forwarded_without_profile_expansion(self) -> None:
        payload = _run_small_corridor(
            timestamp="2026-08-27T20:00:00+06:00",
            states=self.base_states(),
            controls=_controls(SMALL_CHANDELIER, "set_color_temperature", 2200),
        )
        self.assertEqual(["server_action"], _action_ids(payload))
        self.assertEqual(2200, payload["actions"][0]["value"])


class ManagedShowerSourceTest(unittest.TestCase):
    def test_direct_user_off_matrix_has_exactly_1296_safe_cases(self) -> None:
        """A server-validated user-off ignores every raw room-state combination.

        Six independently realistic reports over four affected actuators give
        6^4 cases. This catches regressions where an automatic profile leaks
        into a direct user decision already validated by ScenarioService.
        """
        reports = ("on", "off", "unknown", "unavailable", "restored", None)
        observed: set[tuple[object, object, object, object]] = set()
        for main, extra, fan, cabinet in itertools.product(reports, repeat=4):
            case = (main, extra, fan, cabinet)
            self.assertNotIn(case, observed)
            observed.add(case)
            payload = _run_shower(
                timestamp="2026-08-27T12:00:00+06:00",
                states={
                    SHOWER_PRESENCE: "off", SHOWER_HUMIDITY: "45", SUN: "above_horizon",
                    SHOWER_MAIN: main, SHOWER_EXTRA: extra, SHOWER_FAN: fan,
                    SHOWER_CABINET: cabinet,
                },
                trigger=_typed("shower-cabinet", "toggle_b2_down", "toggle", "off"),
            )
            actions = _action_ids(payload)
            self.assertEqual(["set_cabinet_off"], actions, case)
            self.assertEqual("cabinet_toggle", payload["selectedBranch"], case)
            self.assertEqual("completed", payload["status"], case)
            self.assertEqual(
                [
                    {
                        "id": "set_cabinet_off",
                        "type": "device_action",
                        "targetId": SHOWER_CABINET,
                        "targetName": "Душевая: подсветка шкафа",
                        "actionId": "turn_off",
                        "actionTitle": "Выключить",
                    }
                ],
                payload["actions"],
                case,
            )
            self.assertNotIn("absence_wait", actions)
            self.assertFalse(
                any(action.get("delaySeconds") == 300 for action in payload["actions"])
            )
            self.assertTrue(
                all(action.get("targetId") == SHOWER_CABINET for action in payload["actions"]),
                case,
            )
        self.assertEqual(6**4, len(observed))
        self.assertEqual(1296, len(observed))

    def test_cabinet_toggle_down_toggles_cabinet_without_touching_key1_relay(self) -> None:
        payload = _run_shower(
            timestamp="2026-08-27T12:00:00+06:00",
            states={SHOWER_MAIN: "on", SHOWER_CABINET: "off"},
            trigger=_typed("shower-cabinet", "toggle_b2_down", "toggle", "on"),
        )
        self.assertEqual(["set_cabinet_on"], _action_ids(payload))
        self.assertNotIn("set_main_off", _action_ids(payload))

    def test_cabinet_direct_intent_does_not_reinspect_raw_snapshot(self) -> None:
        payload = _run_shower(
            timestamp="2026-08-27T12:00:00+06:00",
            states={SHOWER_MAIN: "on", SHOWER_CABINET: "unknown"},
            trigger=_typed("shower-cabinet", "toggle_b2_down", "toggle", "on"),
        )
        self.assertEqual(["set_cabinet_on"], _action_ids(payload))

    def test_cabinet_toggle_up_is_ignored(self) -> None:
        payload = _run_shower(
            timestamp="2026-08-27T12:00:00+06:00",
            states={SHOWER_MAIN: "on", SHOWER_CABINET: "off"},
            trigger={"source": "device", "trigger_id": "toggle_b2_up"},
        )
        self.assertEqual([], _action_ids(payload))

    def test_shower_forwards_exact_released_main_relay_target(self) -> None:
        states = {
            SHOWER_PRESENCE: "on", SHOWER_HUMIDITY: "45", SUN: "above_horizon",
            SHOWER_MAIN_NEW: "off", SHOWER_EXTRA: "off", SHOWER_FAN: "off", SHOWER_CABINET: "off",
        }
        payload = _run_shower(
            timestamp="2026-08-27T12:00:00+06:00",
            states=states,
            trigger={"source": "scenario_control"},
            controls=_controls(SHOWER_MAIN_NEW, "turn_on"),
        )
        actions = {item["id"]: item for item in payload["actions"]}
        self.assertEqual(SHOWER_MAIN_NEW, actions["server_action"]["targetId"])

    def test_absent_humid_inputs_cannot_plan_actions_in_node_red(self) -> None:
        payload = _run_shower(
            timestamp="2026-08-27T12:00:00+06:00",
            states={
                SHOWER_PRESENCE: "off", SHOWER_HUMIDITY: "60", SUN: "above_horizon",
                SHOWER_MAIN: "on", SHOWER_EXTRA: "off", SHOWER_FAN: "off", SHOWER_CABINET: "off",
            },
        )
        self.assertEqual([], _action_ids(payload))
        self.assertEqual("server_hold", payload["selectedBranch"])

    def test_absent_humid_fan_on_cannot_plan_light_actions_in_node_red(self) -> None:
        payload = _run_shower(
            timestamp="2026-08-27T12:00:00+06:00",
            states={
                SHOWER_PRESENCE: "off", SHOWER_HUMIDITY: "60", SUN: "above_horizon",
                SHOWER_MAIN: "on", SHOWER_EXTRA: "on", SHOWER_FAN: "on", SHOWER_CABINET: "on",
            },
        )
        self.assertEqual([], _action_ids(payload))
        self.assertEqual("server_hold", payload["selectedBranch"])

    def test_absence_input_cannot_create_a_node_red_timer(self) -> None:
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

        self.assertEqual("server_hold", payload["selectedBranch"])
        self.assertEqual([], _action_ids(payload))

    def test_day_inputs_cannot_claim_or_switch_a_profile_in_node_red(self) -> None:
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

        self.assertEqual("server_hold", payload["selectedBranch"])
        self.assertEqual([], _action_ids(payload))

    def test_presence_input_cannot_start_fan_or_light_in_node_red(self) -> None:
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

        self.assertEqual("server_hold", payload["selectedBranch"])
        self.assertEqual([], _action_ids(payload))

    def test_unknown_presence_without_server_action_holds_every_output(self) -> None:
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

        self.assertEqual("server_hold", payload["selectedBranch"])
        self.assertEqual([], payload["actions"])
