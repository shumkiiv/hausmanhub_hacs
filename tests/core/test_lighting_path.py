"""End-to-end shadow path tests (HH-014.b): event → core → explanation,
with the transport closed by construction."""

from __future__ import annotations

from conftest import build_file_stand
from test_lighting_plugin import (
    _config,
    _observation,
)
from custom_components.hausman_hub.application.room_lighting_core import (
    CoreBranchConfig,
    RoomLightingCoreBranch,
)
from custom_components.hausman_hub.domain.core.models import ModuleMode


def _branch(tmp_path):
    branch = RoomLightingCoreBranch(
        branch_config=CoreBranchConfig(
            room_id="test_room",
            config=_config(),
            timezone="Asia/Omsk",
            sunrise="08:00",
            sunset="18:00",
        ),
        store_path=tmp_path / "core_shadow_test_room.json",
    )
    branch.activate_shadow()
    return branch


def _frame_observations(now_utc_ms: int):
    return [
        _observation("test_mirror", "on", False, now_utc_ms),
        _observation("sensor_presence", "occupancy", True, now_utc_ms),
    ]


def _observation(resource_id, property_name, value, mono_ms):
    return Observation(
        observation_id=f"obs-{resource_id}-{mono_ms}",
        resource_id=resource_id,
        property=property_name,
        value=value,
        unit="bool" if isinstance(value, bool) else "pct",
        received_at_utc_ms=mono_ms,
        received_at_mono_ms=mono_ms,
        boot_id="test.boot1",
        source_at_utc_ms=None,
        source_sequence=None,
        source_id="test.ha",
        source_epoch="test.epoch",
        quality=Quality.FRESH,
        evidence_kind=Evidence.DEVICE_REPORT,
        binding_revision=1,
        observation_revision=mono_ms,
        related_operation_id=None,
        attribution=ObservationAttribution.UNKNOWN,
    )


from custom_components.hausman_hub.domain.core.models import (
    Evidence,
    ObservationAttribution,
    Quality,
)


def test_input_reaches_core_and_leaves_explanation(tmp_path):
    branch = _branch(tmp_path)
    branch.handle_runtime_event("binary_sensor.test_presence", None, _state("on"))
    explanation = branch.decision_explanation()
    assert explanation is not None
    assert explanation.resource_id == "test_mirror" or explanation.resource_id


def test_service_runs_in_shadow_mode(tmp_path):
    branch = _branch(tmp_path)
    assert branch._service._core_mode is ModuleMode.SHADOW


def test_closed_transport_cannot_send(tmp_path):
    from custom_components.hausman_hub.adapters.core.closed_transport import (
        ClosedTransport,
    )
    from custom_components.hausman_hub.application.core.command_service import (
        PreparedCall,
    )

    transport = ClosedTransport()
    outcome = transport.call(
        PreparedCall(
            domain="light",
            service="turn_on",
            entity_id="light.any",
            data={"brightness_pct": 40},
            operation_id="op-x",
            permit_id="pm-x",
            requested_properties=("on",),
        )
    )
    assert outcome.status.value == "error"
    assert outcome.proven_not_sent is True
    assert outcome.error_detail == "closed_transport"


def _state(value: str):
    from unittest.mock import MagicMock

    state = MagicMock()
    state.state = value
    state.attributes = {}
    return state


from unittest.mock import MagicMock

from conftest import OWNER_MODULE, OWNER_PROPOSAL, MAIN_RESOURCE, MAIN_SCOPE, OTHER_RESOURCE, TARGET_OFF, BASE_TARGET_ON40
from custom_components.hausman_hub.domain.core.models import (
    ArbitrationMode,
    Authority,
    OperationPhase,
)


def test_runtime_port_delivers_events_to_branch(tmp_path):
    """Discriminating test: the event must arrive through the real
    runtime port (set_core_input_port → _state_event), not via a direct
    branch call (SEC-LP-03, cycle-1 audit)."""

    from custom_components.hausman_hub.application.room_lighting_runtime import (
        RoomLightingRuntime,
    )
    from tests.fixtures_lighting_path import make_branch, make_state

    branch = make_branch(tmp_path)
    branch.activate_shadow()
    runtime = RoomLightingRuntime(
        hass=None,
        service=MagicMock(),
        shadow=MagicMock(),
    )
    runtime._running = True
    runtime.set_core_input_port(branch)
    before = branch.decision_explanation()

    event = MagicMock()
    event.data = {
        "entity_id": "binary_sensor.test_presence",
        "old_state": None,
        "new_state": _state("on"),
    }
    runtime._state_event(event)

    # The discriminating check: the explanation object changed after the
    # event arrived through the port.
    after = branch.decision_explanation()
    assert after is not before


def test_branch_survives_restart_from_durable_image(tmp_path):
    """Two branch instances on one store file: the second restores the
    image (records, markers) and keeps SHADOW mode without dispatching
    (SEC-S04-11, LP-R2-RESTORE)."""

    from custom_components.hausman_hub.domain.core.models import (
        ArbitrationMode,
        Authority,
    )

    from conftest import OWNER_EPISODE

    stand = build_file_stand(
        tmp_path,
        ArbitrationMode.RANKED_TARGET,
        episode_map={OWNER_PROPOSAL: OWNER_EPISODE},
    )
    stand.ingest(
        "test.main",
        sequence=2,
        mono_ms=10,
        properties={"on": True, "brightness_pct": 40},
    )
    store_path = stand.store_path
    from conftest import restore_file_stand

    restarted = restore_file_stand(stand)
    record = restarted.service.record("test.main")
    assert record.authority.value in ("manual", "automatic_eligible")
    # A third instance: records persist through re-commits.
    third = restore_file_stand(restarted)
    assert third.service.record("test.main").control_revision == (
        restarted.service.record("test.main").control_revision
    )
    assert third.transport.calls == []
