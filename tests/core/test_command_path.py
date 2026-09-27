"""Control-path examples F-01..F-13 of the FIRST delivery.

Each scenario starts from the stand passport state (conftest). ``drain`` is
one permitted executor tick of the real candidate executor, never a direct
HA call. Tests assert both the zero-count of spurious commands and the
authority versions.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from custom_components.hausman_hub.application.core.command_service import (
    ManualStatus,
)
from custom_components.hausman_hub.domain.core.models import (
    OperationPhase,
    ProposalKind,
    REASON_CONFIRMATION_TIMEOUT,
    REASON_OWNERSHIP_UNPROVEN,
)

from conftest import (
    BASE_TARGET_ON40,
    GENERATION_START,
    MAIN_ENTITY,
    MAIN_RESOURCE,
    OTHER_RESOURCE,
    OWNER_EPISODE,
    OWNER_MODULE,
    OWNER_PROPOSAL,
    TARGET_OFF,
    build_stand,
    owner_proposal,
)

ANY_ON = "any_on"


def _auto_on40_confirmed(stand):
    """Run the F-11 setup: an any_on REQUIRE_ON demand of the owner module
    turns the lamp on and is confirmed; the owner then withdraws its
    one-shot demand. See conftest.auto_on40_confirmed_anyon."""

    from conftest import auto_on40_confirmed_anyon

    auto_on40_confirmed_anyon(stand)


def test_f01_confirmed_on40(ranked_stand):
    stand = ranked_stand
    stand.modules[OWNER_MODULE].queue_proposal(owner_proposal())
    stand.service.process_module_event(OWNER_MODULE, "ev-1")
    stand.clock.advance(1)
    stand.service.drain()
    # Exactly one physical call, exactly the allowlist payload.
    assert stand.transport.calls == [
        ("light", "turn_on", MAIN_ENTITY, {"brightness_pct": 40})
    ]
    stand.clock.advance(99)
    result = stand.ingest(
        MAIN_RESOURCE,
        sequence=2,
        mono_ms=100,
        properties={"on": True, "brightness_pct": 40},
    )
    assert result == "confirmed"
    record = stand.service.record(MAIN_RESOURCE)
    assert record.auto_proof is not None
    assert record.auto_proof.generation == GENERATION_START
    view = stand.service.resource_view(MAIN_RESOURCE)
    facts = {fact.property: fact for fact in view.facts}
    assert facts["on"].value is True
    assert int(facts["brightness_pct"].value) == 40


def test_f02_silence_ends_unknown(ranked_stand):
    stand = ranked_stand
    stand.modules[OWNER_MODULE].queue_proposal(owner_proposal())
    stand.service.process_module_event(OWNER_MODULE, "ev-1")
    stand.clock.advance(1)
    stand.service.drain()
    stand.clock.advance(9999)  # mono 10000
    stand.service.advance_timeouts()
    view = stand.service.resource_view(MAIN_RESOURCE)
    assert view.operation_phase == "observing"
    stand.clock.advance(1)  # mono 10001
    stand.service.advance_timeouts()
    view = stand.service.resource_view(MAIN_RESOURCE)
    assert view.operation_phase == "unknown"
    assert view.operation_reason == REASON_CONFIRMATION_TIMEOUT
    assert stand.service.record(MAIN_RESOURCE).auto_proof is None
    facts = {fact.property: fact for fact in view.facts}
    # Honest fact: the lamp never reported; age counts from the last report.
    assert facts["on"].value is False
    assert stand.clock.now_mono_ms() - facts["on"].observed_at_mono_ms == 10001
    assert (
        stand.service.episode_lock_reason(MAIN_RESOURCE, OWNER_EPISODE)
        == REASON_CONFIRMATION_TIMEOUT
    )


def test_f03_late_report_confirms_without_redelivery(ranked_stand):
    stand = ranked_stand
    stand.modules[OWNER_MODULE].queue_proposal(owner_proposal())
    stand.service.process_module_event(OWNER_MODULE, "ev-1")
    stand.clock.advance(1)
    stand.service.drain()
    stand.clock.advance(10000)
    stand.service.advance_timeouts()
    stand.clock.advance(999)  # mono 11000
    result = stand.ingest(
        MAIN_RESOURCE,
        sequence=2,
        mono_ms=11000,
        properties={"on": True, "brightness_pct": 40},
    )
    assert result == "confirmed_late"
    assert stand.service.dispatch_count() == 1
    operation = stand.service.operation(
        stand.service.resource_view(MAIN_RESOURCE).operation_id
    )
    assert operation.phase is OperationPhase.CONFIRMED_LATE
    # v1: a late confirmation never restores auto ownership by itself.
    assert stand.service.record(MAIN_RESOURCE).auto_proof is None


@pytest.mark.parametrize("drain_at", [4999, 5000, 5001])
def test_f06_ttl_boundary(ranked_stand, drain_at):
    stand = ranked_stand
    stand.modules[OWNER_MODULE].queue_proposal(owner_proposal())
    stand.service.process_module_event(OWNER_MODULE, "ev-1")
    stand.clock.advance(drain_at)
    stand.service.drain()
    assert stand.service.dispatch_count() == (1 if drain_at == 4999 else 0)
    view = stand.service.resource_view(MAIN_RESOURCE)
    if drain_at == 4999:
        assert view.operation_phase == "observing"
    else:
        assert view.operation_phase == "expired"


def test_f09_aliases_share_one_queue():
    from custom_components.hausman_hub.domain.core.models import ArbitrationMode

    stand = build_stand(
        ArbitrationMode.RANKED_TARGET,
        episode_map={OWNER_PROPOSAL: OWNER_EPISODE},
    )
    module = stand.modules[OWNER_MODULE]
    # Same lamp addressed by resource id and by alias, equal targets,
    # submitted one after another: one physical queue resolves them.
    module.queue_proposal(owner_proposal(proposal_id="p-main"))
    stand.service.process_module_event(OWNER_MODULE, "ev-1")
    module.queue_proposal(
        owner_proposal(proposal_id="p-alias", resource_id="light.test_main_alias")
    )
    stand.service.process_module_event(OWNER_MODULE, "ev-2")
    stand.clock.advance(1)
    stand.service.drain()
    assert stand.service.dispatch_count() == 1
    # Equal target: the older pending intent is superseded, no duplicate.
    phases = [
        operation.phase.value for operation in stand.service.operations(MAIN_RESOURCE)
    ]
    assert phases.count("superseded") == 1
    # Opposite target while the first is already dispatched: the new one
    # waits for its own dispatch permit, never sends in parallel.
    module.queue_proposal(
        owner_proposal(proposal_id="p-off", target=TARGET_OFF)
    )
    stand.service.process_module_event(OWNER_MODULE, "ev-3")
    stand.service.drain()
    assert stand.service.dispatch_count() == 1
    view = stand.service.resource_view(MAIN_RESOURCE)
    assert view.operation_phase == "queued"
    # After the first operation resolves, the next tick dispatches the
    # waiting target sequentially.
    stand.ingest(
        MAIN_RESOURCE,
        sequence=2,
        mono_ms=200,
        properties={"on": True, "brightness_pct": 40},
    )
    stand.service.drain()
    assert stand.service.dispatch_count() == 2
    assert stand.transport.calls[-1] == ("light", "turn_off", MAIN_ENTITY, {})


def test_f10_unknown_blocks_repeat_of_same_episode(ranked_stand):
    stand = ranked_stand
    stand.modules[OWNER_MODULE].queue_proposal(owner_proposal())
    stand.service.process_module_event(OWNER_MODULE, "ev-1")
    stand.clock.advance(1)
    stand.service.drain()
    stand.clock.advance(10000)
    stand.service.advance_timeouts()
    assert stand.service.dispatch_count() == 1
    for index in range(100):
        stand.modules[OWNER_MODULE].queue_proposal(
            owner_proposal()
        )
        stand.service.process_module_event(OWNER_MODULE, f"ev-recalc-{index}")
        stand.service.drain()
    # One dispatch in total; the uncertainty stays visible.
    assert stand.service.dispatch_count() == 1
    view = stand.service.resource_view(MAIN_RESOURCE)
    assert view.operation_phase == "rejected"
    assert view.operation_reason == "repeat_blocked_unknown_outcome"
    assert (
        stand.service.episode_lock_reason(MAIN_RESOURCE, OWNER_EPISODE)
        == REASON_CONFIRMATION_TIMEOUT
    )


def test_f11_any_on_idle_needs_and_ownership():
    from custom_components.hausman_hub.domain.core.models import ArbitrationMode

    stand = build_stand(
        ArbitrationMode.ANY_ON,
        module_specs={
            OWNER_MODULE: MAIN_RESOURCE,
            "test.module_a": MAIN_RESOURCE,
            "test.module_b": MAIN_RESOURCE,
        },
        episode_map={OWNER_PROPOSAL: OWNER_EPISODE},
    )
    _auto_on40_confirmed(stand)
    assert stand.service.dispatch_count() == 1
    # Needs A and B demand ON; the fact already matches: zero new commands.
    stand.modules["test.module_a"].queue_proposal(
        owner_proposal(
            proposal_id="a",
            module_id="test.module_a",
            kind=ProposalKind.REQUIRE_ON,
        )
    )
    stand.modules["test.module_b"].queue_proposal(
        owner_proposal(
            proposal_id="b",
            module_id="test.module_b",
            kind=ProposalKind.REQUIRE_ON,
        )
    )
    stand.service.process_module_event("test.module_a", "ev-a")
    stand.service.process_module_event("test.module_b", "ev-b")
    assert stand.service.dispatch_count() == 1
    # B withdraws: A still demands ON, no dispatch.
    stand.service.process_module_event("test.module_b", "ev-b-gone")
    assert stand.service.dispatch_count() == 1
    explanation = stand.service.explanation(MAIN_RESOURCE)
    assert explanation.target_result == BASE_TARGET_ON40
    assert len(explanation.withdrawn) == 1
    # A withdraws: idle OFF with proof of ownership is allowed once.
    stand.service.process_module_event("test.module_a", "ev-a-gone")
    stand.service.drain()
    assert stand.service.dispatch_count() == 2
    assert stand.transport.calls[-1] == ("light", "turn_off", MAIN_ENTITY, {})


def test_f11_any_on_idle_without_auto_proof_stays_off_forbidden():
    from custom_components.hausman_hub.domain.core.models import ArbitrationMode

    stand = build_stand(
        ArbitrationMode.ANY_ON,
        module_specs={
            OWNER_MODULE: MAIN_RESOURCE,
            "test.module_a": MAIN_RESOURCE,
            "test.module_b": MAIN_RESOURCE,
        },
        episode_map={OWNER_PROPOSAL: OWNER_EPISODE},
    )
    # The lamp reports ON40 itself; nobody owns it automatically.
    stand.ingest(
        MAIN_RESOURCE,
        sequence=2,
        mono_ms=10,
        properties={"on": True, "brightness_pct": 40},
    )
    stand.modules["test.module_a"].queue_proposal(
        owner_proposal(
            proposal_id="a",
            module_id="test.module_a",
            kind=ProposalKind.REQUIRE_ON,
        )
    )
    stand.modules["test.module_b"].queue_proposal(
        owner_proposal(
            proposal_id="b",
            module_id="test.module_b",
            kind=ProposalKind.REQUIRE_ON,
        )
    )
    stand.service.process_module_event("test.module_a", "ev-a")
    stand.service.process_module_event("test.module_b", "ev-b")
    stand.service.process_module_event("test.module_b", "ev-b-gone")
    stand.service.process_module_event("test.module_a", "ev-a-gone")
    stand.service.drain()
    assert stand.service.dispatch_count() == 0
    explanation = stand.service.explanation(MAIN_RESOURCE)
    assert explanation.result_reason == REASON_OWNERSHIP_UNPROVEN


@pytest.mark.parametrize("order", [["a", "b"], ["b", "a"]])
def test_f13_ranked_tie_break_with_previous(order):
    from custom_components.hausman_hub.domain.core.models import ArbitrationMode

    stand = build_stand(
        ArbitrationMode.RANKED_TARGET,
        episode_map={OWNER_PROPOSAL: OWNER_EPISODE},
    )
    module = stand.modules[OWNER_MODULE]
    proposal_b = owner_proposal(proposal_id="b", target=_on60())
    proposal_a = owner_proposal(proposal_id="a", target=BASE_TARGET_ON40)
    # Previous selected target is b=ON60 from an earlier undelivered decision.
    module.queue_proposal(proposal_b)
    stand.service.process_module_event(OWNER_MODULE, "ev-seed")
    for proposal_id in order:
        module.queue_proposal(proposal_a if proposal_id == "a" else proposal_b)
    stand.service.process_module_event(OWNER_MODULE, "ev-both")
    stand.clock.advance(1)
    stand.service.drain()
    assert stand.service.dispatch_count() == 1
    assert stand.transport.calls[-1][3] == {"brightness_pct": 60}


@pytest.mark.parametrize("order", [["a", "b"], ["b", "a"]])
def test_f13_ranked_tie_break_without_previous(order):
    from custom_components.hausman_hub.domain.core.models import ArbitrationMode

    stand = build_stand(
        ArbitrationMode.RANKED_TARGET,
        episode_map={OWNER_PROPOSAL: OWNER_EPISODE},
    )
    module = stand.modules[OWNER_MODULE]
    proposal_b = owner_proposal(proposal_id="b", target=_on60())
    proposal_a = owner_proposal(proposal_id="a", target=BASE_TARGET_ON40)
    for proposal_id in order:
        module.queue_proposal(proposal_a if proposal_id == "a" else proposal_b)
    stand.service.process_module_event(OWNER_MODULE, "ev-both")
    stand.clock.advance(1)
    stand.service.drain()
    assert stand.service.dispatch_count() == 1
    # Equal priority: the smallest (module_id, proposal_id) wins.
    assert stand.transport.calls[-1][3] == {"brightness_pct": 40}


def test_episode01_metadata_follows_the_chosen_proposal(ranked_stand):
    stand = ranked_stand
    module = stand.modules[OWNER_MODULE]
    proposal_a = owner_proposal(proposal_id="a", target=BASE_TARGET_ON40)
    proposal_b = owner_proposal(proposal_id="b", target=_on60())
    # Order [b, a]: the tie-break selects "a"; the operation must carry
    # the episode of "a", not of the first array element (SEC-FIRST-EPISODE-01).
    module.queue_proposal(proposal_b)
    module.queue_proposal(proposal_a)
    stand.service.process_module_event(OWNER_MODULE, "ev-1")
    stand.clock.advance(1)
    stand.service.drain()
    first = stand.service.operations(MAIN_RESOURCE)[-1]
    assert first.episode_id == "a"
    assert first.target == BASE_TARGET_ON40
    assert stand.service.dispatch_count() == 1
    stand.clock.advance(10000)
    stand.service.advance_timeouts()
    assert (
        stand.service.episode_lock_reason(MAIN_RESOURCE, "a")
        == REASON_CONFIRMATION_TIMEOUT
    )
    # Reordering the same demands selects "a" again: the unknown outcome
    # of episode "a" keeps blocking the repeat.
    for order in ([proposal_a, proposal_b], [proposal_b, proposal_a]):
        module.queue_proposal(order[0])
        module.queue_proposal(order[1])
        stand.service.process_module_event(OWNER_MODULE, f"ev-{order[0].proposal_id}")
        stand.service.drain()
        latest = stand.service.operations(MAIN_RESOURCE)[-1]
        assert latest.episode_id == "a"
        assert latest.phase is OperationPhase.REJECTED
        assert latest.reason_code == "repeat_blocked_unknown_outcome"
    assert stand.service.dispatch_count() == 1


def test_episode01_r1_equal_targets_choose_deterministically(ranked_stand):
    stand = ranked_stand
    module = stand.modules[OWNER_MODULE]
    proposal_a = owner_proposal(proposal_id="a", target=BASE_TARGET_ON40)
    proposal_b = owner_proposal(proposal_id="b", target=BASE_TARGET_ON40)
    # First decision: tie without a previous target selects "a".
    module.queue_proposal(proposal_a)
    module.queue_proposal(proposal_b)
    stand.service.process_module_event(OWNER_MODULE, "ev-1")
    stand.clock.advance(1)
    stand.service.drain()
    assert stand.service.operations(MAIN_RESOURCE)[-1].episode_id == "a"
    stand.clock.advance(10000)
    stand.service.advance_timeouts()
    assert stand.service.dispatch_count() == 1
    # Reordering equal targets with a previous selection must still land
    # on "a": the choice is deterministic, so the unknown-outcome lock of
    # episode "a" holds (SEC-FIRST-EPISODE-01).
    for order in ([proposal_b, proposal_a], [proposal_a, proposal_b]):
        module.queue_proposal(order[0])
        module.queue_proposal(order[1])
        stand.service.process_module_event(
            OWNER_MODULE, f"ev-{order[0].proposal_id}-again"
        )
        stand.service.drain()
        latest = stand.service.operations(MAIN_RESOURCE)[-1]
        assert latest.episode_id == "a"
        assert latest.phase is OperationPhase.REJECTED
        assert latest.reason_code == "repeat_blocked_unknown_outcome"
    assert stand.service.dispatch_count() == 1


def _on60():
    from custom_components.hausman_hub.domain.core.models import LightStateTarget

    return LightStateTarget(
        on=True, brightness_pct=Decimal(60), color_temperature_k=None
    )
