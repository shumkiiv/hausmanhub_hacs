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
    REASON_OWNERSHIP_UNPROVEN,
)

from conftest import (
    BASE_TARGET_ON40,
    MAIN_ENTITY,
    MAIN_RESOURCE,
    OWNER_EPISODE,
    OWNER_MODULE,
    OWNER_PROPOSAL,
    auto_on40_confirmed_anyon,
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


def test_own01_confirmed_off_clears_ownership_proof():
    from custom_components.hausman_hub.domain.core.models import (
        ArbitrationMode,
    )

    stand = build_stand(
        ArbitrationMode.ANY_ON,
        module_specs={
            OWNER_MODULE: MAIN_RESOURCE,
            "test.module_a": MAIN_RESOURCE,
            "test.module_b": MAIN_RESOURCE,
        },
        episode_map={OWNER_PROPOSAL: OWNER_EPISODE},
    )
    auto_on40_confirmed_anyon(stand)
    assert stand.service.record(MAIN_RESOURCE).auto_proof is not None
    # Idle OFF acts on the proof, then the device confirms the OFF.
    stand.service.process_module_event("test.module_a", "ev-a-gone")
    stand.service.drain()
    stand.ingest(
        MAIN_RESOURCE,
        sequence=3,
        mono_ms=200,
        properties={"on": False},
    )
    # A confirmed automatic OFF leaves no ownership proof behind
    # (SEC-FIRST-OWN-01). Two dispatches so far: turn_on and idle OFF.
    assert stand.service.record(MAIN_RESOURCE).auto_proof is None
    assert stand.service.dispatch_count() == 2
    # The lamp is on again by a plain device report; nobody owns it.
    # A require_on demand changes nothing, and after its withdrawal the
    # idle OFF is forbidden: ownership unproven (no dispatch either way).
    from custom_components.hausman_hub.domain.core.models import ProposalKind

    stand.ingest(
        MAIN_RESOURCE,
        sequence=4,
        mono_ms=300,
        properties={"on": True, "brightness_pct": 40},
    )
    stand.modules["test.module_a"].queue_proposal(
        owner_proposal(
            proposal_id="a",
            module_id="test.module_a",
            kind=ProposalKind.REQUIRE_ON,
        )
    )
    stand.service.process_module_event("test.module_a", "ev-a")
    assert stand.service.dispatch_count() == 2
    stand.service.process_module_event("test.module_a", "ev-a-gone")
    stand.service.drain()
    assert stand.service.dispatch_count() == 2
    explanation = stand.service.explanation(MAIN_RESOURCE)
    assert explanation.result_reason == REASON_OWNERSHIP_UNPROVEN


def test_own02_manual_attribution_report_never_confirms_auto(ranked_stand):
    from custom_components.hausman_hub.domain.core.models import Authority

    stand = ranked_stand
    _dispatch_on40(stand)
    stand.clock.advance(99)
    raw = stand.raw_report(
        MAIN_RESOURCE, 2, 100, {"on": True, "brightness_pct": 40}
    )
    raw["attribution"] = "manual"
    result = "unset"
    for observation in stand.adapter.normalize(raw):
        result = stand.service.ingest_observation(observation)
    assert result == "manual_evidence"
    record = stand.service.record(MAIN_RESOURCE)
    assert record.auto_proof is None
    assert record.authority is Authority.UNCERTAIN
    assert record.uncertainty_reason == "manual_observation_attribution"
    operation = stand.service.operations(MAIN_RESOURCE)[-1]
    assert (
        operation.phase is OperationPhase.SUPERSEDED_AFTER_DISPATCH
    )
    # The closed rights also stop the next automatic attempt.
    stand.modules[OWNER_MODULE].queue_proposal(owner_proposal())
    stand.service.process_module_event(OWNER_MODULE, "ev-2")
    stand.service.drain()
    assert stand.service.dispatch_count() == 1


def test_conf01_deadline_decides_lateness(ranked_stand):
    stand = ranked_stand
    _dispatch_on40(stand)  # dispatch at mono 1, deadline 10001
    # A report exactly at the deadline is on time (FIRST section 3).
    stand.clock.advance(10000)
    result = stand.ingest(
        MAIN_RESOURCE,
        sequence=2,
        mono_ms=10001,
        properties={"on": True, "brightness_pct": 40},
    )
    assert result == "confirmed"
    assert stand.service.record(MAIN_RESOURCE).auto_proof is not None
    # Fresh scenario: a report after the deadline, observed before the
    # timeout tick, is late and never creates ownership.
    from custom_components.hausman_hub.domain.core.models import ArbitrationMode

    stand2 = build_stand(
        ArbitrationMode.RANKED_TARGET,
        episode_map={OWNER_PROPOSAL: OWNER_EPISODE},
    )
    _dispatch_on40(stand2)
    stand2.clock.advance(10001)
    result = stand2.ingest(
        MAIN_RESOURCE,
        sequence=2,
        mono_ms=10002,
        properties={"on": True, "brightness_pct": 40},
    )
    assert result == "confirmed_late"
    assert stand2.service.record(MAIN_RESOURCE).auto_proof is None
    operation = stand2.service.operations(MAIN_RESOURCE)[-1]
    assert operation.phase is OperationPhase.CONFIRMED_LATE


def test_conf02_mixed_evidence_group_is_ambiguous(ranked_stand):
    stand = ranked_stand
    _dispatch_on40(stand)
    stand.clock.advance(99)
    # Two messages with one key: a real wrong report plus an optimistic
    # matching one. Together they are ambiguous and confirm nothing.
    real = stand.raw_report(
        MAIN_RESOURCE, 2, 100, {"on": True, "brightness_pct": 10}
    )
    for observation in stand.adapter.normalize(real):
        stand.service.ingest_observation(observation)
    optimistic = stand.raw_report(
        MAIN_RESOURCE, 2, 100, {"on": True, "brightness_pct": 40},
        evidence="optimistic",
    )
    for observation in stand.adapter.normalize(optimistic):
        stand.service.ingest_observation(observation)
    stand.clock.advance(9901)
    stand.service.advance_timeouts()
    view = stand.service.resource_view(MAIN_RESOURCE)
    assert view.operation_phase == "unknown"
    assert stand.service.record(MAIN_RESOURCE).auto_proof is None


def test_val01_wrongly_typed_value_never_confirms(ranked_stand):
    stand = ranked_stand
    _dispatch_on40(stand)
    stand.clock.advance(99)
    result = stand.ingest(
        MAIN_RESOURCE,
        sequence=2,
        mono_ms=100,
        properties={"on": "false", "brightness_pct": 40},
    )
    # The string "false" is an invalid boolean: it must not be read as
    # True and must not confirm the ON command (SEC-FIRST-VAL-01).
    assert result == "recorded"
    stand.clock.advance(10000)
    stand.service.advance_timeouts()
    view = stand.service.resource_view(MAIN_RESOURCE)
    assert view.operation_phase == "unknown"
    facts = {fact.property: fact for fact in view.facts}
    assert facts["on"].value == "false"
    assert stand.service.record(MAIN_RESOURCE).auto_proof is None


def test_fact01_freshness_covers_quality_and_all_matched_properties(
    ranked_stand,
):
    # (a) A retained (stale) fact cannot justify an automatic dispatch.
    stand = ranked_stand
    stand.ingest(
        MAIN_RESOURCE,
        sequence=2,
        mono_ms=10,
        properties={"on": True},
        evidence="retained",
    )
    stand.modules[OWNER_MODULE].queue_proposal(owner_proposal())
    stand.service.process_module_event(OWNER_MODULE, "ev-1")
    stand.service.drain()
    assert stand.service.dispatch_count() == 0
    view = stand.service.resource_view(MAIN_RESOURCE)
    assert view.operation_phase == "cancelled"
    # (b) A no_op needs every requested property fresh: fresh ON but an
    # aged brightness fact forces a real dispatch instead of a silent no_op.
    stand2 = ranked_stand
    stand2.ingest(
        MAIN_RESOURCE, sequence=2, mono_ms=0, properties={"on": True, "brightness_pct": 40}
    )
    stand2.ingest(MAIN_RESOURCE, sequence=3, mono_ms=61000, properties={"on": True})
    stand2.clock.advance(61001)
    receipt = stand2.service.submit_manual(
        resource_ref=MAIN_RESOURCE,
        request_id="req-fresh",
        action_id="manual.on40",
        target=BASE_TARGET_ON40,
        source="test.ui",
        expected_control_revision=12,
    )
    from custom_components.hausman_hub.application.core.command_service import (
        ManualStatus,
    )

    assert receipt.status is ManualStatus.ACCEPTED
    assert stand2.service.dispatch_count() == 1
