"""Device evidence examples: F-07 and the S-03 confirmation rules.

Only a post-dispatch device report that carries every requested property,
causally after the dispatch, confirms an operation. Optimistic state,
retained snapshots and unrelated reports never do.
"""

from __future__ import annotations

import pytest

from custom_components.hausman_hub.application.core.command_service import (
    ManualStatus,
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


def test_own01_r1_late_off_still_invalidates_proof():
    from custom_components.hausman_hub.domain.core.models import ArbitrationMode

    stand = build_stand(
        ArbitrationMode.RANKED_TARGET,
        episode_map={OWNER_PROPOSAL: OWNER_EPISODE},
    )
    stand.modules[OWNER_MODULE].queue_proposal(owner_proposal())
    stand.service.process_module_event(OWNER_MODULE, "ev-1")
    stand.clock.advance(1)
    stand.service.drain()
    stand.ingest(
        MAIN_RESOURCE, sequence=2, mono_ms=100,
        properties={"on": True, "brightness_pct": 40},
    )
    assert stand.service.record(MAIN_RESOURCE).auto_proof is not None
    # Idle OFF acts on the proof; its report arrives after the deadline.
    stand.service.process_module_event(OWNER_MODULE, "ev-1-gone")
    stand.clock.advance(100)
    stand.service.drain()
    stand.clock.advance(10100)
    result = stand.ingest(
        MAIN_RESOURCE, sequence=3, mono_ms=10201, properties={"on": False}
    )
    assert result == "confirmed_late"
    # Even a late-resolved automatic OFF invalidates the stale proof.
    assert stand.service.record(MAIN_RESOURCE).auto_proof is None


def test_own02_r1_manual_report_confirms_only_the_manual_operation(
    ranked_stand,
):
    stand = ranked_stand
    # The lamp is off; a manual ON must physically dispatch.
    receipt = stand.service.submit_manual(
        resource_ref=MAIN_RESOURCE,
        request_id="req-manual-on",
        action_id="manual.on40",
        target=BASE_TARGET_ON40,
        source="test.ui",
        expected_control_revision=12,
    )
    assert receipt.status is ManualStatus.ACCEPTED
    assert stand.service.dispatch_count() == 1
    from custom_components.hausman_hub.domain.core.models import Authority

    raw = stand.raw_report(
        MAIN_RESOURCE, 2, 100, {"on": True, "brightness_pct": 40}
    )
    raw["attribution"] = "manual"
    result = "unset"
    for observation in stand.adapter.normalize(raw):
        result = stand.service.ingest_observation(observation)
    # The manually attributed report legitimately confirms the manual
    # command; authority stays manual and no auto proof appears.
    assert result == "confirmed"
    record = stand.service.record(MAIN_RESOURCE)
    assert record.authority is Authority.MANUAL
    assert record.auto_proof is None
    operation = stand.service.operations(MAIN_RESOURCE)[-1]
    assert operation.phase is OperationPhase.CONFIRMED


def test_conf01_r1_late_resolution_locks_the_episode(ranked_stand):
    stand = ranked_stand
    _dispatch_on40(stand)
    # The report arrives after the deadline but before the timeout tick.
    stand.clock.advance(10001)
    result = stand.ingest(
        MAIN_RESOURCE,
        sequence=2,
        mono_ms=10002,
        properties={"on": True, "brightness_pct": 40},
    )
    assert result == "confirmed_late"
    # Both service orders of timeout tick and late report leave the same
    # rights: the episode is locked.
    assert (
        stand.service.episode_lock_reason(MAIN_RESOURCE, OWNER_EPISODE)
        == REASON_CONFIRMATION_TIMEOUT
    )
    stand.modules[OWNER_MODULE].queue_proposal(owner_proposal())
    stand.service.process_module_event(OWNER_MODULE, "ev-2")
    stand.service.drain()
    assert stand.service.dispatch_count() == 1


def test_conf02_r1_late_contradiction_revokes_the_proof(ranked_stand):
    stand = ranked_stand
    _dispatch_on40(stand)
    stand.clock.advance(99)
    assert stand.ingest(
        MAIN_RESOURCE,
        sequence=2,
        mono_ms=100,
        properties={"on": True, "brightness_pct": 40},
    ) == "confirmed"
    assert stand.service.record(MAIN_RESOURCE).auto_proof is not None
    # A contradicting member of the same report group arrives after the
    # confirmation: the proof is revoked with a visible ambiguity reason.
    contradiction = stand.raw_report(
        MAIN_RESOURCE, 2, 100, {"brightness_pct": 10}
    )
    for observation in stand.adapter.normalize(contradiction):
        stand.service.ingest_observation(observation)
    assert stand.service.record(MAIN_RESOURCE).auto_proof is None
    operation = stand.service.operations(MAIN_RESOURCE)[-1]
    assert operation.phase is OperationPhase.UNKNOWN
    assert operation.reason_code == "ambiguous_feedback_revoked"
    assert (
        stand.service.episode_lock_reason(MAIN_RESOURCE, OWNER_EPISODE)
        == "ambiguous_feedback_revoked"
    )


def test_val01_r1_non_finite_number_becomes_invalid(ranked_stand):
    stand = ranked_stand
    _dispatch_on40(stand)
    stand.clock.advance(99)
    result = stand.ingest(
        MAIN_RESOURCE,
        sequence=2,
        mono_ms=100,
        properties={"on": True, "brightness_pct": float("nan")},
    )
    # A NaN brightness is an invalid value, not an exception and never a
    # confirmation (SEC-FIRST-VAL-01-R1).
    assert result == "recorded"
    stand.clock.advance(10000)
    stand.service.advance_timeouts()
    view = stand.service.resource_view(MAIN_RESOURCE)
    assert view.operation_phase == "unknown"
    facts = {fact.property: fact for fact in view.facts}
    assert facts["brightness_pct"].quality.value == "invalid"
    assert stand.service.record(MAIN_RESOURCE).auto_proof is None


def test_conf02_r2_revoked_proof_blocks_queued_idle_off():
    from custom_components.hausman_hub.domain.core.models import (
        ArbitrationMode,
        OperationPhase,
    )

    stand = build_stand(
        ArbitrationMode.RANKED_TARGET,
        episode_map={OWNER_PROPOSAL: OWNER_EPISODE},
    )
    stand.modules[OWNER_MODULE].queue_proposal(owner_proposal())
    stand.service.process_module_event(OWNER_MODULE, "ev-1")
    stand.clock.advance(1)
    stand.service.drain()
    stand.ingest(
        MAIN_RESOURCE,
        sequence=2,
        mono_ms=100,
        properties={"on": True, "brightness_pct": 40},
    )
    assert stand.service.record(MAIN_RESOURCE).auto_proof is not None
    # Idle OFF is queued on the valid proof...
    stand.service.process_module_event(OWNER_MODULE, "ev-1-gone")
    # ...then the group of the ON confirmation becomes contradictory and
    # the proof is revoked.
    contradiction = stand.raw_report(
        MAIN_RESOURCE, 2, 100, {"brightness_pct": 10}
    )
    for observation in stand.adapter.normalize(contradiction):
        stand.service.ingest_observation(observation)
    assert stand.service.record(MAIN_RESOURCE).auto_proof is None
    # The queued idle-OFF must not dispatch without proven ownership.
    stand.clock.advance(1)
    stand.service.drain()
    assert stand.service.dispatch_count() == 1
    assert stand.transport.calls == [
        ("light", "turn_on", MAIN_ENTITY, {"brightness_pct": 40})
    ]
    idle_off = stand.service.operations(MAIN_RESOURCE)[-1]
    assert idle_off.phase is OperationPhase.CANCELLED
    assert idle_off.reason_code == "ownership_unproven"


def test_conf02_r3_revocation_covers_manual_and_auto_off(ranked_stand):
    # (a) A confirmed manual command is revoked by a later contradiction
    # of the same report group (no proof, no episode lock for manual).
    stand = ranked_stand
    receipt = stand.service.submit_manual(
        resource_ref=MAIN_RESOURCE,
        request_id="req-manual",
        action_id="manual.on40",
        target=BASE_TARGET_ON40,
        source="test.ui",
        expected_control_revision=12,
    )
    assert receipt.status is ManualStatus.ACCEPTED
    result = stand.ingest(
        MAIN_RESOURCE,
        sequence=2,
        mono_ms=50,
        properties={"on": True, "brightness_pct": 40},
    )
    assert result == "confirmed"
    contradiction = stand.raw_report(MAIN_RESOURCE, 2, 50, {"on": False})
    for observation in stand.adapter.normalize(contradiction):
        stand.service.ingest_observation(observation)
    operation = stand.service.operations(MAIN_RESOURCE)[-1]
    assert operation.phase is OperationPhase.UNKNOWN
    assert operation.reason_code == "ambiguous_feedback_revoked"
    # (b) A confirmed automatic OFF is revoked the same way.
    from custom_components.hausman_hub.domain.core.models import ArbitrationMode

    stand2 = build_stand(
        ArbitrationMode.RANKED_TARGET,
        episode_map={OWNER_PROPOSAL: OWNER_EPISODE},
    )
    stand2.modules[OWNER_MODULE].queue_proposal(owner_proposal())
    stand2.service.process_module_event(OWNER_MODULE, "ev-1")
    stand2.clock.advance(1)
    stand2.service.drain()
    stand2.ingest(
        MAIN_RESOURCE,
        sequence=2,
        mono_ms=100,
        properties={"on": True, "brightness_pct": 40},
    )
    stand2.service.process_module_event(OWNER_MODULE, "ev-1-gone")
    stand2.clock.advance(100)
    stand2.service.drain()
    off_result = stand2.ingest(
        MAIN_RESOURCE, sequence=3, mono_ms=200, properties={"on": False}
    )
    assert off_result == "confirmed"
    off_contradiction = stand2.raw_report(
        MAIN_RESOURCE, 3, 200, {"on": True}
    )
    for observation in stand2.adapter.normalize(off_contradiction):
        stand2.service.ingest_observation(observation)
    off_operation = stand2.service.operations(MAIN_RESOURCE)[-1]
    assert off_operation.phase is OperationPhase.UNKNOWN
    assert off_operation.reason_code == "ambiguous_feedback_revoked"


def test_own02_r2_manual_report_never_resolves_unknown_auto(ranked_stand):
    from custom_components.hausman_hub.domain.core.models import Authority

    stand = ranked_stand
    _dispatch_on40(stand)
    stand.clock.advance(10000)
    stand.service.advance_timeouts()
    operation = stand.service.operations(MAIN_RESOURCE)[-1]
    assert operation.phase is OperationPhase.UNKNOWN
    raw = stand.raw_report(
        MAIN_RESOURCE, 2, 11000, {"on": True, "brightness_pct": 40}
    )
    raw["attribution"] = "manual"
    result = "unset"
    for observation in stand.adapter.normalize(raw):
        result = stand.service.ingest_observation(observation)
    # A manually attributed report must not turn the timed-out automatic
    # operation into a (late) confirmation (SEC-FIRST-OWN-02-R2).
    operation = stand.service.operations(MAIN_RESOURCE)[-1]
    assert result == "manual_evidence"
    assert operation.phase is OperationPhase.UNKNOWN
    assert operation.reason_code == REASON_CONFIRMATION_TIMEOUT
    assert (
        stand.service.episode_lock_reason(MAIN_RESOURCE, OWNER_EPISODE)
        == REASON_CONFIRMATION_TIMEOUT
    )
    assert stand.service.record(MAIN_RESOURCE).auto_proof is None
    assert stand.service.record(MAIN_RESOURCE).authority is Authority.UNCERTAIN


def test_own02_r2b_manual_member_poisons_the_group_for_auto(ranked_stand):
    from custom_components.hausman_hub.domain.core.models import Authority

    stand = ranked_stand
    _dispatch_on40(stand)
    stand.clock.advance(10000)
    stand.service.advance_timeouts()
    operation = stand.service.operations(MAIN_RESOURCE)[-1]
    assert operation.phase is OperationPhase.UNKNOWN
    # 1) A manually attributed report is stored but never confirms auto.
    manual = stand.raw_report(
        MAIN_RESOURCE, 2, 11000, {"on": True, "brightness_pct": 40}
    )
    manual["attribution"] = "manual"
    for observation in stand.adapter.normalize(manual):
        stand.service.ingest_observation(observation)
    # 2) A later ordinary member of the SAME group (same resource,
    #    sequence, time, source) must not combine with it either.
    ordinary = stand.raw_report(MAIN_RESOURCE, 2, 11000, {"brightness_pct": 40})
    result = "unset"
    for observation in stand.adapter.normalize(ordinary):
        result = stand.service.ingest_observation(observation)
    assert result == "recorded"
    # The timed-out automatic operation stays unknown: no false late
    # confirmation from any mix of manual and ordinary members
    # (SEC-FIRST-OWN-02-R2).
    operation = stand.service.operations(MAIN_RESOURCE)[-1]
    assert operation.phase is OperationPhase.UNKNOWN
    assert operation.reason_code == REASON_CONFIRMATION_TIMEOUT
    assert (
        stand.service.episode_lock_reason(MAIN_RESOURCE, OWNER_EPISODE)
        == REASON_CONFIRMATION_TIMEOUT
    )
    assert stand.service.record(MAIN_RESOURCE).auto_proof is None
    assert stand.service.record(MAIN_RESOURCE).authority is Authority.UNCERTAIN
    # The manual hold itself is unaffected by the recorded evidence.
    assert stand.service.dispatch_count() == 1


def test_val01_r2_overflow_is_a_mismatch_not_a_crash(ranked_stand):
    from decimal import Decimal

    from custom_components.hausman_hub.application.core.command_service import (
        ManualStatus,
        _value_matches,
    )
    from conftest import light_profile

    stand = ranked_stand
    # A huge but finite value is stored as invalid and never crashes the
    # numeric comparison (SEC-FIRST-VAL-01-R2).
    stand.ingest(
        MAIN_RESOURCE,
        sequence=2,
        mono_ms=10,
        properties={"on": True, "brightness_pct": Decimal("1E+1000000")},
    )
    rule = light_profile().properties["brightness_pct"]
    assert (
        _value_matches(Decimal("1E+1000000"), Decimal(40), rule) is False
    )
    receipt = stand.service.submit_manual(
        resource_ref=MAIN_RESOURCE,
        request_id="req-huge",
        action_id="manual.on40",
        target=BASE_TARGET_ON40,
        source="test.ui",
        expected_control_revision=12,
    )
    # The manual acceptance completes: the invalid fact forbids a no_op,
    # so a real command is dispatched instead of an exception.
    assert receipt.status is ManualStatus.ACCEPTED
    assert stand.service.dispatch_count() == 1
    assert stand.transport.calls[-1][1] == "turn_on"
