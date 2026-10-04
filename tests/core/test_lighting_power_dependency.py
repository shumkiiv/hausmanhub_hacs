"""Final power gate for the isolated L-SYN-1 active core stand."""

from dataclasses import replace

from conftest import (
    MAIN_RESOURCE,
    OTHER_RESOURCE,
    OWNER_MODULE,
    arbitration_mode_from_flag,
    build_stand,
    owner_proposal,
)


def _queue(stand, event):
    stand.modules[OWNER_MODULE].queue_proposal(owner_proposal())
    stand.service.process_module_event(OWNER_MODULE, event)
    stand.service.drain()


def _power_guarded_stand(warmup_ms=2000):
    stand = build_stand(arbitration_mode_from_flag(False))
    # This is an immutable L-SYN-1 passport relation: main depends on the
    # otherwise independent second resource, which plays the power switch.
    stand.service._specs[MAIN_RESOURCE] = replace(
        stand.service._specs[MAIN_RESOURCE], power_dependencies=(OTHER_RESOURCE,)
    )
    stand.adapter._profiles[MAIN_RESOURCE] = replace(
        stand.adapter.capabilities(MAIN_RESOURCE), power_warmup_ms=warmup_ms
    )
    return stand


def test_main_waits_for_fresh_power_and_two_second_warmup():
    stand = _power_guarded_stand()

    _queue(stand, "without-power")
    assert stand.transport.calls == []

    stand.clock.advance(1000)
    stand.ingest(OTHER_RESOURCE, 2, 1000, {"on": True})
    _queue(stand, "during-warmup")
    assert stand.transport.calls == []

    stand.clock.advance(2000)
    _queue(stand, "warmup-complete")
    assert len(stand.transport.calls) == 1


def test_power_loss_after_queue_blocks_the_transport_boundary():
    stand = _power_guarded_stand()
    stand.ingest(OTHER_RESOURCE, 2, 0, {"on": True})
    stand.clock.advance(2000)
    stand.modules[OWNER_MODULE].queue_proposal(owner_proposal())
    stand.service.process_module_event(OWNER_MODULE, "queue-main")
    # The new report arrives between arbitration and the final permit.
    stand.ingest(OTHER_RESOURCE, 3, 2000, {"on": False})
    stand.service.drain()
    assert stand.transport.calls == []


def test_capability_passport_keeps_each_power_warmup_explicit():
    for warmup_ms in (3000, 5000):
        stand = _power_guarded_stand(warmup_ms)
        stand.ingest(OTHER_RESOURCE, 2, 0, {"on": True})
        stand.clock.advance(warmup_ms - 1)
        _queue(stand, f"before-{warmup_ms}")
        assert stand.transport.calls == []
        stand.clock.advance(1)
        _queue(stand, f"at-{warmup_ms}")
        assert len(stand.transport.calls) == 1


def test_power_age_is_rechecked_at_the_transport_boundary():
    stand = _power_guarded_stand(warmup_ms=1)
    profile = stand.adapter.capabilities(OTHER_RESOURCE)
    on_rule = profile.properties["on"]
    stand.adapter._profiles[OTHER_RESOURCE] = replace(
        profile,
        properties={
            **profile.properties,
            "on": replace(on_rule, max_age_ms=2),
        },
    )
    stand.ingest(OTHER_RESOURCE, 2, 0, {"on": True})
    stand.clock.advance(1)
    stand.modules[OWNER_MODULE].queue_proposal(owner_proposal())
    stand.service.process_module_event(OWNER_MODULE, "queue-main")
    stand.service.take_ready()
    stand.service.run_taken()
    assert stand.transport.calls == []
    stand.clock.advance(2)
    stand.service.run_dispatching()
    assert stand.transport.calls == []
