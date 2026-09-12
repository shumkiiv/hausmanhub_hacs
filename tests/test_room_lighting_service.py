"""Service-level checks for room lighting entity-collision handling.

A shared sensor (presence, motion, illuminance) is legitimate shared
infrastructure, while a shared control entity (light target, power switch or
wireless switch) must be rejected.
"""

from __future__ import annotations

import pytest

from custom_components.hausman_hub.application.room_lighting_service import (
    RoomLightingService,
)
from custom_components.hausman_hub.domain.room_lighting import (
    RoomLightingViolation,
    config_from_payload,
)


class _MemoryStore:
    def __init__(self) -> None:
        self.configs: dict[str, object] = {}

    async def async_get(self, room_id: str):
        return self.configs.get(room_id)

    async def async_load_all(self):
        return tuple(self.configs.values())

    async def async_upsert(self, config) -> None:
        self.configs[config.room_id] = config

    async def async_delete(self, room_id: str) -> bool:
        return self.configs.pop(room_id, None) is not None


def _payload(
    room_id: str,
    *,
    sensor_entity: str = "binary_sensor.demo_presence",
    light_entity: str = "light.demo_main",
    switch_entity: str = "switch.demo_spots",
) -> dict:
    return {
        "contract": {"name": "hausman-hub-room-lighting-config", "version": 1},
        "roomId": room_id,
        "name": f"Комната {room_id}",
        "version": 1,
        "devices": {
            "sensors": [
                {
                    "id": "sensor_demo_presence",
                    "name": "Присутствие",
                    "kind": "presence",
                    "entityId": sensor_entity,
                    "autoAdoptOverride": None,
                }
            ],
            "light_targets": [
                {
                    "id": "light_main",
                    "name": "Люстра",
                    "kind": "light",
                    "entityId": light_entity,
                    "role": "main",
                    "groupId": None,
                    "brightness": True,
                    "color_temperature": True,
                    "autoAdoptOverride": None,
                },
                {
                    "id": "light_spots",
                    "name": "Точки",
                    "kind": "switch",
                    "entityId": switch_entity,
                    "role": "accent",
                    "groupId": None,
                    "brightness": False,
                    "color_temperature": False,
                    "autoAdoptOverride": False,
                },
            ],
            "power_switch": None,
            "wireless_switches": [],
            "selectAll": False,
        },
        "schedule": [
            {
                "id": "sch_day",
                "title": "День",
                "when": {
                    "daysOfWeek": "all",
                    "holiday": False,
                    "anchor": {"kind": "fixed", "time": "09:00", "offsetMinutes": 0},
                },
                "targets": {"lightTargets": ["light_main"], "groupIds": [], "roles": []},
                "how": {
                    "brightness": 60,
                    "colorTemperature": 3000,
                    "fade": True,
                    "mode": "on_presence",
                    "minOnSeconds": 0,
                },
            }
        ],
        "switchBindings": [],
        "illumination": None,
        "dimming": {
            "enabled": True,
            "onAbsence": True,
            "fadeSeconds": 20,
            "targetPercent": 0,
        },
        "manualOffProtection": {
            "enabled": True,
            "minimumIntervalSeconds": 600,
            "releaseMode": "timer_and_absence",
            "stableAbsenceSeconds": 30,
            "priority": "manual_above_auto",
        },
        "awayBehavior": {"mode": "none"},
        "autoAdopt": True,
        "updatedAt": 1,
        "overrides": {},
    }


async def test_shared_sensor_between_rooms_is_persisted() -> None:
    service = RoomLightingService(_MemoryStore())

    await service.async_put_config(config_from_payload(_payload("room_a")))
    saved = await service.async_put_config(
        config_from_payload(
            _payload(
                "room_b",
                light_entity="light.other_main",
                switch_entity="switch.other_spots",
            )
        )
    )

    assert saved.room_id == "room_b"


async def test_shared_light_target_is_rejected() -> None:
    service = RoomLightingService(_MemoryStore())

    await service.async_put_config(config_from_payload(_payload("room_a")))
    with pytest.raises(RoomLightingViolation):
        await service.async_put_config(
            config_from_payload(_payload("room_b", switch_entity="switch.other_spots"))
        )


async def test_shared_switch_target_is_rejected() -> None:
    service = RoomLightingService(_MemoryStore())

    await service.async_put_config(config_from_payload(_payload("room_a")))
    with pytest.raises(RoomLightingViolation):
        await service.async_put_config(
            config_from_payload(_payload("room_b", light_entity="light.other_main"))
        )
