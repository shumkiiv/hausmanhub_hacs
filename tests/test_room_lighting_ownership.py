"""Ownership evidence tests for room lighting."""

from __future__ import annotations

from custom_components.hausman_hub.domain.room_lighting_ownership import (
    OwnershipSnapshot,
    OwnershipSource,
    SensorState,
    has_proven_auto_ownership,
    latest_ownership,
    manual_intervention_after,
    observed_absence,
    resolve_manual_ownership,
    restore_after_restart,
)


def _record(
    source: OwnershipSource,
    at: int,
    *,
    target_id: str = "light_main",
    confirmed: bool = True,
) -> OwnershipSnapshot:
    return OwnershipSnapshot(target_id, source, confirmed, at)


def test_latest_ownership_picks_newest() -> None:
    records = [
        _record(OwnershipSource.AUTO, 100),
        _record(OwnershipSource.MANUAL, 200),
    ]
    assert latest_ownership(records, "light_main").at == 200
    assert latest_ownership(records, "light_other") is None


def test_proven_auto_ownership_requires_confirmed_and_no_later_manual() -> None:
    assert has_proven_auto_ownership([_record(OwnershipSource.AUTO, 100)], "light_main")
    assert not has_proven_auto_ownership(
        [_record(OwnershipSource.AUTO, 100, confirmed=False)], "light_main"
    )
    assert not has_proven_auto_ownership(
        [
            _record(OwnershipSource.AUTO, 100),
            _record(OwnershipSource.MANUAL, 200),
        ],
        "light_main",
    )


def test_manual_intervention_after_auto_command() -> None:
    records = [
        _record(OwnershipSource.AUTO, 100),
        _record(OwnershipSource.MANUAL, 150),
    ]
    assert manual_intervention_after(records, "light_main", 100)
    assert not manual_intervention_after(records, "light_main", 150)
    assert not manual_intervention_after(records, "light_main", 200)


def test_on_light_without_owner_counts_as_manual() -> None:
    assert resolve_manual_ownership(
        [], "light_main", light_on=True
    )
    assert not resolve_manual_ownership([], "light_main", light_on=False)
    assert resolve_manual_ownership(
        [_record(OwnershipSource.MANUAL, 100)], "light_main", light_on=False
    )
    assert not resolve_manual_ownership(
        [_record(OwnershipSource.AUTO, 100)], "light_main", light_on=True
    )


def test_restore_after_restart_requires_fresh_proof() -> None:
    records = [_record(OwnershipSource.AUTO, 1000)]
    assert restore_after_restart(
        records,
        "light_main",
        light_state=SensorState.ON,
        light_last_changed=1200,
        unobserved_since=None,
    )
    assert not restore_after_restart(
        records,
        "light_main",
        light_state=SensorState.OFF,
        light_last_changed=1200,
        unobserved_since=None,
    )
    assert not restore_after_restart(
        records,
        "light_main",
        light_state=SensorState.ON,
        light_last_changed=1200,
        unobserved_since=1500,
    )
    assert not restore_after_restart(
        [
            _record(OwnershipSource.AUTO, 1000),
            _record(OwnershipSource.MANUAL, 1100),
        ],
        "light_main",
        light_state=SensorState.ON,
        light_last_changed=1200,
        unobserved_since=None,
    )
    assert not restore_after_restart(
        records,
        "light_main",
        light_state=SensorState.ON,
        light_last_changed=900,
        unobserved_since=None,
    )


def test_observed_absence_requires_continuous_fresh_off() -> None:
    now = 10_000
    assert observed_absence(
        [(SensorState.OFF, 9_000), (SensorState.OFF, 9_500)],
        now=now,
        freshness_ms=2_000,
    )
    assert observed_absence(
        [(SensorState.OFF, 9_000), (SensorState.ON, 9_500)],
        now=now,
        freshness_ms=2_000,
    ) is False
    assert observed_absence(
        [(SensorState.OFF, 9_000), (SensorState.UNKNOWN, 9_500)],
        now=now,
        freshness_ms=2_000,
    ) is None
    assert observed_absence(
        [(SensorState.OFF, 9_000), (SensorState.UNAVAILABLE, 9_500)],
        now=now,
        freshness_ms=2_000,
    ) is None
    assert observed_absence(
        [(SensorState.OFF, 5_000), (SensorState.OFF, 9_500)],
        now=now,
        freshness_ms=2_000,
    ) is None
    assert observed_absence([], now=now, freshness_ms=2_000) is None
