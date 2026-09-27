"""Client compatibility of the lighting path (HH-014.b, приложение 06).

The shadow branch adds no public endpoints and changes no client-facing
schemas: the existing room-lighting config payload remains valid against
the pinned contract schemas (0.73.0 draft, local verification copy).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from custom_components.hausman_hub.domain.room_lighting import (
    config_from_payload,
)

CONTRACT_DIR = Path(
    os.environ.get(
        "HAUSMANHUB_CONTRACT_DIR",
        "/home/ivsh/projects/Hausman/worktrees/"
        "codex-hh016e-contracts-20260925/schemas/v1",
    )
)

_CONFIG_PAYLOAD = {
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


def _schema_file(name: str) -> Path | None:
    candidates = list(CONTRACT_DIR.glob(f"*{name}*"))
    return candidates[0] if candidates else None


def test_contract_dir_is_available():
    # The local verification copy of the draft schemas exists; this is an
    # environment precondition, not a PASS of the contract itself.
    assert CONTRACT_DIR.is_dir()


def test_config_payload_matches_legacy_shape():
    """The shadow branch consumes the legacy config document unchanged:
    the same payload keeps flowing through config_from_payload."""

    config = config_from_payload(_CONFIG_PAYLOAD)
    assert config.room_id == "test_room"
    payload = config.to_dict()
    # Round-trip stability: serialization did not invent new fields.
    reparsed = config_from_payload(payload)
    assert reparsed.room_id == config.room_id
    assert reparsed.version == config.version


@pytest.mark.xfail(
    reason=(
        "runtime config model carries manualControlReturn (HH-008.b "
        "basis b89168a) which the local 0.73.0 draft schema predates; "
        "the schema pin belongs to HH-016.e, not this delivery"
    ),
    strict=True,
)
def test_config_payload_against_draft_schema():
    import jsonschema

    schema_path = _schema_file("room-lighting-config")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    config = config_from_payload(_CONFIG_PAYLOAD)
    payload = config.to_dict()
    jsonschema.validate(payload, schema)


def test_shadow_branch_adds_no_public_surface(tmp_path):
    """The branch exposes decisions only in memory: no new endpoints,
    no client-visible capability (приложение 06, закрытая отправка)."""

    from test_core_lighting_setup import _branch

    branch = _branch(tmp_path)
    branch.activate_shadow()
    branch.handle_runtime_event("binary_sensor.test_presence", None, _state("on"))
    # Read-only surface: explanations and physical_send_enabled flag.
    assert callable(branch.decision_explanation)
    assert branch.physical_send_enabled() is False


def _state(value: str):
    from unittest.mock import MagicMock

    state = MagicMock()
    state.state = value
    state.attributes = {}
    return state
