"""Device evidence examples: F-07 and the S-03 confirmation rules.

Only a post-dispatch device report that carries every requested property,
causally after the dispatch, confirms an operation. Optimistic state,
retained snapshots and unrelated reports never do.
"""

from __future__ import annotations

import pytest

from custom_components.hausman_hub.application.core.command_service import (
    TransportStatus,
)
from custom_components.hausman_hub.domain.core.models import (
    OperationPhase,
    REASON_CONFIRMATION_TIMEOUT,
)

from conftest import (
    MAIN_ENTITY,
    MAIN_RESOURCE,
    OWNER_MODULE,
    OWNER_PROPOSAL,
    build_stand,
    owner_proposal,
)


def _dispatch_on40(stand):
    stand.modules[OWNER_MODULE].queue_proposal(owner_proposal())
    stand.service.process_module_event(OWNER_MODULE, "ev-1")
    stand.clock.advance(1)
    stand.service.drain()


def test_f07_wrong_and_optimistic_reports_never_confirm(ranked_stand):
    stand = ranked_stand
    _dispatch_on40(stand)
    stand.clock.advance(99)  # mono 100: real report, wrong brightness
    result = stand.ingest(
        MAIN_RESOURCE,
        sequence=2,
        mono_ms=100,
        properties={"on": True, "brightness_pct": 10},
    )
    assert result == "recorded"
    stand.clock.advance(100)  # mono 200: optimistic ON40 arrives
    optimistic = stand.ingest(
        MAIN_RESOURCE,
        sequence=3,
        mono_ms=200,
        properties={"on": True, "brightness_pct": 40},
        evidence="optimistic",
    )
    assert optimistic == "recorded"
    stand.clock.advance(9801)  # mono 10001
    stand.service.advance_timeouts()
    view = stand.service.resource_view(MAIN_RESOURCE)
    assert view.operation_phase == "unknown"
    assert view.operation_reason == REASON_CONFIRMATION_TIMEOUT
    assert stand.service.record(MAIN_RESOURCE).auto_proof is None
    assert stand.service.dispatch_count() == 1


def test_battery_report_does_not_refresh_brightness_age(ranked_stand):
    stand = ranked_stand
    stand.ingest(
        MAIN_RESOURCE,
        sequence=2,
        mono_ms=50,
        properties={"on": False, "brightness_pct": 10},
    )
    stand.ingest(
        MAIN_RESOURCE,
        sequence=3,
        mono_ms=100,
        properties={"battery": 87},
    )
    view = stand.service.resource_view(MAIN_RESOURCE)
    facts = {fact.property: fact for fact in view.facts}
    # The brightness fact keeps its own age; the battery event is separate.
    assert facts["brightness_pct"].observed_at_mono_ms == 50
    assert facts["battery"].observed_at_mono_ms == 100
    assert facts["battery"].value == 87


@pytest.mark.parametrize("evidence", ["retained", "snapshot", "adapter_state"])
def test_non_report_evidence_never_confirms(ranked_stand, evidence):
    stand = ranked_stand
    _dispatch_on40(stand)
    stand.clock.advance(99)
    result = stand.ingest(
        MAIN_RESOURCE,
        sequence=2,
        mono_ms=100,
        properties={"on": True, "brightness_pct": 40},
        evidence=evidence,
    )
    assert result == "recorded"
    stand.clock.advance(10000)
    stand.service.advance_timeouts()
    view = stand.service.resource_view(MAIN_RESOURCE)
    assert view.operation_phase == "unknown"
    assert stand.service.record(MAIN_RESOURCE).auto_proof is None


def test_stale_sequence_is_not_causally_new(ranked_stand):
    stand = ranked_stand
    _dispatch_on40(stand)
    stand.clock.advance(99)
    # A repeated older report (sequence 1 again) cannot confirm seq-based op.
    result = stand.ingest(
        MAIN_RESOURCE,
        sequence=1,
        mono_ms=100,
        properties={"on": True, "brightness_pct": 40},
    )
    assert result == "recorded"
    stand.clock.advance(10000)
    stand.service.advance_timeouts()
    assert (
        stand.service.resource_view(MAIN_RESOURCE).operation_phase
        == "unknown"
    )


def test_transport_error_is_unknown_until_proven_not_sent():
    from custom_components.hausman_hub.domain.core.models import ArbitrationMode

    stand = build_stand(ArbitrationMode.RANKED_TARGET)
    stand.transport.fail_next = True
    _dispatch_on40(stand)
    operation = stand.service.operations(MAIN_RESOURCE)[-1]
    assert operation.phase is OperationPhase.UNKNOWN
    assert stand.transport.calls[-1][1] == "turn_on"
    stand.transport.fail_next = True
    stand.transport.prove_not_sent_on_fail = True
    stand.modules[OWNER_MODULE].queue_proposal(
        owner_proposal(proposal_id="second.episode")
    )
    stand.service.process_module_event(OWNER_MODULE, "ev-2")
    stand.clock.advance(1)
    stand.service.drain()
    second = stand.service.operations(MAIN_RESOURCE)[-1]
    assert second.phase is OperationPhase.FAILED
    # A proven non-send is terminal failed, not an unknown episode lock.
    assert second.episode_id == "second.episode"
    assert (
        stand.service.episode_lock_reason(MAIN_RESOURCE, "second.episode")
        is None
    )


def test_readback_is_not_used_in_first(ranked_stand):
    stand = ranked_stand
    profile = stand.adapter.capabilities(MAIN_RESOURCE)
    for rule in profile.properties.values():
        assert rule.readback_delay_ms is None
    _dispatch_on40(stand)
    # One physical call, zero readback requests: the transport log holds
    # only the single turn_on call.
    assert len(stand.transport.calls) == 1
