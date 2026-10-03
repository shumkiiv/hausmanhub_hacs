"""Assembly tests of the shadow lighting branch (HH-014.b, приложение 06).

Verifies the real assembly point: the integration creates the branch
only for an explicitly configured room, starts it in SHADOW with the
closed transport, and the runtime delivers events through the input
port. No physical send is possible by construction.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.hausman_hub.application.room_lighting_core import (
    CoreBranchConfig,
    RoomLightingCoreBranch,
)
from custom_components.hausman_hub.application.core.command_service import (
    StorageUnavailable,
)
from custom_components.hausman_hub.domain.room_lighting import (
    config_from_payload,
)


def _config_payload(room_id: str = "test_room") -> dict:
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


def _branch(tmp_path, room_id: str = "test_room"):
    config = config_from_payload(_config_payload(room_id))
    return RoomLightingCoreBranch(
        branch_config=CoreBranchConfig(
            room_id=room_id,
            config=config,
            timezone="Asia/Omsk",
            sunrise="08:00",
            sunset="18:00",
        ),
        store_path=tmp_path / f"core_shadow_{room_id}.json",
    )


def _state(value: str, brightness: int | None = None):
    state = MagicMock()
    state.state = value
    state.attributes = {"brightness": brightness} if brightness else {}
    return state


def test_branch_starts_off_and_activates_into_shadow(tmp_path):
    branch = _branch(tmp_path)
    assert branch.mode == "off"
    assert branch.physical_send_enabled() is False
    branch.activate_shadow()
    assert branch.mode == "shadow"
    # Activation processes one room event through the core.
    assert branch.decision_explanation() is not None


def test_branch_events_are_observed_in_shadow_without_sending(tmp_path):
    branch = _branch(tmp_path)
    branch.activate_shadow()
    # Presence turns on: the core records the observation and evaluates.
    branch.handle_runtime_event("binary_sensor.test_presence", None, _state("on"))
    explanation = branch.decision_explanation()
    assert explanation is not None
    # The closed transport never sent anything and never will.
    assert branch.physical_send_enabled() is False


def test_branch_off_ignores_events(tmp_path):
    branch = _branch(tmp_path)
    branch.handle_runtime_event("binary_sensor.test_presence", None, _state("on"))
    # In OFF mode nothing is evaluated.
    assert branch.decision_explanation() is None


def test_deactivation_stops_processing(tmp_path):
    branch = _branch(tmp_path)
    branch.activate_shadow()
    before = branch.decision_explanation()
    branch.deactivate()
    branch.handle_runtime_event("binary_sensor.test_presence", None, _state("on"))
    branch.process_schedule_tick()
    # After deactivation no new decision cycle replaced the stored one.
    assert branch.decision_explanation() is before


@pytest.mark.parametrize("callbacks", (("deactivate", "on_runtime_stop"), ("on_runtime_stop", "deactivate")))
def test_stop_callbacks_retry_the_same_pending_close_without_reopening_input(
    tmp_path, monkeypatch, callbacks
):
    branch = _branch(tmp_path)
    branch.activate_shadow()
    real_close = branch._service.close_dispatch_for_restore
    attempts = 0

    def fail_once_then_close():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise StorageUnavailable("injected durable close failure")
        real_close()

    monkeypatch.setattr(
        branch._service, "close_dispatch_for_restore", fail_once_then_close
    )
    with pytest.raises(StorageUnavailable):
        getattr(branch, callbacks[0])()
    getattr(branch, callbacks[1])()

    assert attempts == 2
    assert branch.input_closed is True
    assert branch.physical_send_enabled() is False


def test_resource_view_reports_facts_not_fiction(tmp_path):
    branch = _branch(tmp_path)
    branch.activate_shadow()
    branch.handle_runtime_event("binary_sensor.test_presence", None, _state("on"))
    view = branch.resource_view("sensor_presence")
    assert view is not None
    # Without dispatch permission there is no operation pretending to be
    # a physical result.
    assert branch.physical_send_enabled() is False
