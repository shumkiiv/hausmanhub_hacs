from __future__ import annotations

import unittest

from tools.audit_climate_routes import audit_routes


def storage(data: dict[str, object]) -> dict[str, object]:
    return {"version": 1, "data": data}


class ClimateRouteAuditTest(unittest.TestCase):
    def test_accepts_smartir_yandex_direct_and_raw_remote_routes(self) -> None:
        climate = storage(
            {
                "devices": [
                    device("living", "universal_ir", "climate.living_smartir"),
                    device("kids", "yandex_remote", "climate.kids_yandex"),
                    device("office", "direct_wifi", "climate.office_wifi"),
                    device("kitchen", "universal_ir", "remote.kitchen_ir"),
                ]
            }
        )
        entities = storage(
            {
                "entities": [
                    entity("climate.living_smartir", "smartir"),
                    entity("climate.kids_yandex", "yandex_station"),
                    entity("climate.office_wifi", "midea_ac"),
                    entity("remote.kitchen_ir", "broadlink"),
                ]
            }
        )
        self.assertEqual([], audit_routes(climate, entities))

    def test_reports_missing_and_mismatched_routes_deterministically(self) -> None:
        climate = storage(
            {
                "devices": [
                    device("kitchen", "universal_ir", "climate.kitchen_yandex"),
                    device("kids", "direct_wifi", "climate.kids_smartir"),
                    device("living", "yandex_remote", "climate.living_wifi"),
                    device("office", "direct_wifi", "climate.missing"),
                ]
            }
        )
        entities = storage(
            {
                "entities": [
                    entity("climate.kitchen_yandex", "yandex_station"),
                    entity("climate.kids_smartir", "smartir"),
                    entity("climate.living_wifi", "midea_ac"),
                ]
            }
        )
        self.assertEqual(
            [
                issue("kids", "kids", "direct_wifi", "direct_platform_mismatch"),
                issue("kitchen", "kitchen", "universal_ir", "universal_ir_platform_mismatch"),
                issue("living", "living", "yandex_remote", "yandex_platform_mismatch"),
                issue("office", "office", "direct_wifi", "entity_not_registered"),
            ],
            audit_routes(climate, entities),
        )


def device(device_id: str, channel: str, entity_id: str) -> dict[str, object]:
    return {
        "id": device_id,
        "room_id": device_id,
        "control_channel": channel,
        "endpoints": [{"role": "control", "entity_id": entity_id}],
    }


def entity(entity_id: str, platform: str) -> dict[str, object]:
    return {"entity_id": entity_id, "platform": platform}


def issue(room_id: str, device_id: str, channel: str, code: str) -> dict[str, str]:
    return {
        "device_id": device_id,
        "room_id": room_id,
        "channel": channel,
        "code": code,
    }


if __name__ == "__main__":
    unittest.main()
