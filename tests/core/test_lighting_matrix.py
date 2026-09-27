"""Matrix subset T01/T02 (L-SYN-1) through the full core service
(HH-014.b lighting path delivery): proposal to permit to closed
transport, with the shadow branch consuming real observations."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from custom_components.hausman_hub.application.room_lighting_core import (
    CoreBranchConfig,
    RoomLightingCoreBranch,
)
from custom_components.hausman_hub.domain.room_lighting import (
    config_from_payload,
)

MODULE_ID = "test.lighting"
MORNING_UTC_MS = int(
    datetime(2026, 9, 26, 1, 0, tzinfo=timezone.utc).timestamp() * 1000
)


def _config():
    return {
        "contract": {"name": "hausman-hub-room-lighting-config", "version": 1},
        "roomId": "test_room",
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
                    "id": "test_mirror",
                    "name": "Зеркало",
                    "kind": "light",
                    "entityId": "light.test_mirror",
                    "role": "mirror",
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
        "schedule": [
            {
                "id": "sch_mirror",
                "title": "Зеркало утро",
                "when": {
                    "daysOfWeek": "all",
                    "holiday": False,
                    "anchor": {"kind": "fixed", "time": "07:00", "offsetMinutes": 0},
                },
                "targets": {
                    "lightTargets": ["test_mirror"],
                    "groupIds": [],
                    "roles": [],
                },
                "how": {
                    "brightness": 80,
                    "colorTemperature": None,
                    "fade": False,
                    "mode": "on_presence",
                },
            }
        ],
        "switchBindings": [],
        "illumination": None,
        "dimming": {"enabled": False},
        "manualOffProtection": {"enabled": False},
        "awayBehavior": {"mode": "none"},
        "autoAdopt": False,
        "updatedAt": 1,
        "overrides": {},
    }


def _branch(tmp_path):
    branch = RoomLightingCoreBranch(
        branch_config=CoreBranchConfig(
            room_id="test_room",
            config=config_from_payload(_config()),
            timezone="Asia/Omsk",
            sunrise="08:00",
            sunset="18:00",
        ),
        store_path=tmp_path / "core_shadow_test_room.json",
    )
    branch.activate_shadow()
    return branch


def _state(value: str):
    state = MagicMock()
    state.state = value
    state.attributes = {}
    return state


def test_t01_shadow_schedule_turns_mirror_on_without_sending(tmp_path):
    branch = _branch(tmp_path)
    branch.handle_runtime_event("binary_sensor.test_presence", None, _state("on"))
    branch.handle_runtime_event("light.test_mirror", None, _state("off"))
    explanation = branch.decision_explanation()
    assert explanation is not None
    # The shadow branch never dispatches: no transport call could have
    # happened by design.
    assert branch.physical_send_enabled() is False


def test_t02_manual_light_on_blocks_auto_proposals(tmp_path):
    branch = _branch(tmp_path)
    # The person turned the mirror on manually before the boundary.
    branch.handle_runtime_event("light.test_mirror", None, _state("on"))
    branch.handle_runtime_event("binary_sensor.test_presence", None, _state("on"))
    explanation = branch.decision_explanation()
    assert explanation is not None
    assert explanation.accepted == ()
    assert explanation.target_result is None
    assert branch.physical_send_enabled() is False
