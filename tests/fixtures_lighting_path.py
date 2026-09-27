"""Shared builders for lighting-path tests (setup + path modules)."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.hausman_hub.application.room_lighting_core import (
    CoreBranchConfig,
    RoomLightingCoreBranch,
)
from custom_components.hausman_hub.domain.room_lighting import (
    config_from_payload,
)


def make_config_payload(room_id: str = "test_room") -> dict:
    return {
        "contract": {"name": "hausman-hub-room-lighting-config", "version": 1},
        "roomId": room_id,
        "name": "Тест",
        "version": 1,
        "devices": {
            "sensors": [
                {
                    "id": "sensor_presence",
                    "name": "Присутствие",
                    "kind": "presence",
                    "entityId": "binary_sensor.test_presence",
                    "autoAdoptOverride": None,
                }
            ],
            "light_targets": [
                {
                    "id": "test_main",
                    "name": "Основной",
                    "kind": "light",
                    "entityId": "light.test_main",
                    "role": "main",
                    "groupId": None,
                    "brightness": True,
                    "color_temperature": False,
                    "autoAdoptOverride": None,
                }
            ],
            "power_switch": None,
            "wireless_switches": [],
            "selectAll": False,
        },
        "schedule": [],
        "switchBindings": [],
        "illumination": None,
        "dimming": {"enabled": False},
        "manualOffProtection": {"enabled": False},
        "awayBehavior": {"mode": "none"},
        "autoAdopt": False,
        "updatedAt": 1,
        "overrides": {},
    }


def make_branch(tmp_path, room_id: str = "test_room"):
    return RoomLightingCoreBranch(
        branch_config=CoreBranchConfig(
            room_id=room_id,
            config=config_from_payload(make_config_payload(room_id)),
            timezone="Asia/Omsk",
            sunrise="08:00",
            sunset="18:00",
        ),
        store_path=tmp_path / f"core_shadow_{room_id}.json",
    )


def make_state(value: str):
    state = MagicMock()
    state.state = value
    state.attributes = {}
    return state
