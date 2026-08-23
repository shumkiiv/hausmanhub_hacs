from __future__ import annotations

import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from custom_components.hausman_hub.application.tablet_power import (
    TabletPowerService,
    TabletPowerViolation,
    charging_policy_decision,
    parse_tablet_power_status,
)


NOW_MS = 1787454000000


def request(**changes: object) -> dict[str, object]:
    return {
        "contract": {
            "name": "hausman-hub-tablet-power-status-request",
            "version": 1,
        },
        "correlationId": "tablet.power.test-1",
        "tabletId": "wall_tablet",
        "batteryPercent": 39,
        "charging": False,
        "powerSource": "battery",
        "batteryTemperatureC": 31.5,
        "reportedAt": NOW_MS,
        **changes,
    }


class TabletPowerTest(unittest.TestCase):
    def test_request_is_bounded_and_rejects_private_extra_fields(self) -> None:
        parsed = parse_tablet_power_status(request())
        self.assertEqual(39, parsed.battery_percent)
        self.assertEqual("battery", parsed.power_source)

        with self.assertRaises(TabletPowerViolation):
            parse_tablet_power_status(request(serialNumber="private"))
        with self.assertRaises(TabletPowerViolation):
            parse_tablet_power_status(request(batteryPercent=101))
        with self.assertRaises(TabletPowerViolation):
            parse_tablet_power_status(request(tabletId="../private"))
        with self.assertRaises(TabletPowerViolation):
            parse_tablet_power_status(request(powerSource={"private": True}))

    def test_charging_policy_uses_39_80_and_fail_safe_boundaries(self) -> None:
        self.assertEqual("turn_on", charging_policy_decision(39))
        self.assertEqual("hold", charging_policy_decision(40))
        self.assertEqual("hold", charging_policy_decision(79))
        self.assertEqual("turn_off", charging_policy_decision(80))
        self.assertEqual(
            "fallback_on",
            charging_policy_decision(None, battery_available=False),
        )
        self.assertEqual(
            "fallback_on",
            charging_policy_decision(50, plug_available=False),
        )

    def test_service_expires_stale_status_and_notifies_entities(self) -> None:
        now = [NOW_MS]
        service = TabletPowerService(now_ms=lambda: now[0])
        notifications: list[bool] = []
        service.subscribe(lambda: notifications.append(service.available()))

        service.update(request())
        self.assertTrue(service.available())
        now[0] += 20 * 60 * 1000 + 1
        self.assertTrue(service.expire())
        self.assertFalse(service.available())
        self.assertEqual([True, False], notifications)

    def test_public_fixtures_and_blueprint_cover_safe_fallback(self) -> None:
        root = Path(__file__).resolve().parents[1]
        contracts = root / "custom_components/hausman_hub/contracts/v1"
        for stem in ("request", "receipt"):
            schema = json.loads(
                (contracts / f"tablet-power-status-{stem}.schema.json").read_text()
            )
            fixture = json.loads(
                (root / f"fixtures/hausmanhub_tablet_power_v1/{stem}.json").read_text()
            )
            Draft202012Validator(schema).validate(fixture)

        blueprint_path = (
            root / "blueprints/automation/hausman_hub/tablet_charging.yaml"
        )
        source = blueprint_path.read_text(encoding="utf-8")
        self.assertIn("\nmode: restart\n", source)
        self.assertIn("battery | int(101) < 40", source)
        self.assertIn("battery | int(-1) >= 80", source)
        self.assertIn("switch.turn_on", source)
        self.assertIn("continue_on_error: true", source)
        self.assertIn("notification_action", source)

    def test_sensor_projection_has_stable_entity_ids_and_translations(self) -> None:
        root = Path(__file__).resolve().parents[1]
        sensor = (root / "custom_components/hausman_hub/sensor.py").read_text()
        strings = json.loads(
            (root / "custom_components/hausman_hub/strings.json").read_text()
        )
        self.assertIn("sensor.hausman_hub_tablet_battery", sensor)
        self.assertIn("sensor.hausman_hub_tablet_power", sensor)
        self.assertEqual(
            "Заряд планшета",
            strings["entity"]["sensor"]["tablet_battery"]["name"],
        )
