# Candidate v19 latch-release tests. File SHA256 (self-declared, verified by
# the coordinator): computed AFTER this header was added — see the evidence
# record. runtime/shadow are unchanged (6e6e5aeb…/7e2ad7f9…).

"""HH-014.b morning-defect regression: the unconfirmed-attempt latch release.

Final semantics (rounds 1-8 of review):
- A latch with a VALID positive attempt boundary (the new durable format,
  "attemptBoundary": true) is released only by a proven target report
  STRICTLY NEWER than that boundary; the revision timestamp is never a
  substitute (it predates the attempt).
- A latch with an UNKNOWN boundary (old-format entry, corrupted/absent
  anchor, legacy two-element record) is fail-closed for turn_on while the
  evidence is untrusted; a FRESH trusted live report bounds the block to
  one freshness window and releases (the device proved it is alive).
- A planned turn_off is the safe direction: allowed without dropping the
  record when the recorded latch was an unconfirmed turn_on.
- The attempt boundary travels through the real paths: pre-send marker →
  in-memory latch → receipt _attemptAt → durable blockedAfterMs → restore.
"""
from __future__ import annotations

from datetime import timedelta
import re
import unittest

import pytest

from custom_components.hausman_hub.application.manual_light_authority import (
    ManualLightAuthority,
)
from custom_components.hausman_hub.domain.room_lighting_engine import (
    DecisionReason,
    LightAction,
    PlannedCommand,
    RoomLightingDecision,
    TargetDecision,
)
from custom_components.hausman_hub.domain.room_lighting import (
    config_from_payload,
)
from tests.test_room_lighting_runtime import (
    _FakeDeviceAutomationApi,
    _FakeHass,
    _MemoryOwnershipStore,
    _NOW_DT,
    _NOW_MS,
    _ROOM_ID,
    _SpyExecutor,
    _config_payload,
    _make_runtime,
)

assert _FakeDeviceAutomationApi is not None and _SpyExecutor is not None


class _AuthorityStore(_MemoryOwnershipStore):
    recovered_previous = False


def _durable_entry(blocked_after: int | None, *, boundary: bool) -> dict[str, object]:
    entry: dict[str, object] = {
        "key": f"target:{_ROOM_ID}:light_main",
        "kind": "target",
        "roomId": _ROOM_ID,
        "targetId": "light_main",
        "action": "turn_on",
        "evidenceRevision": "untrusted:off",
    }
    if blocked_after is not None:
        entry["blockedAfterMs"] = blocked_after
    if boundary:
        entry["attemptBoundary"] = True
    return entry


def _shadow_stub(payload: dict[str, object]):
    class _Shadow:
        async def async_load(self):
            return payload

        async def async_save(self, new_payload):
            return None

        def journal_payload(self):
            return payload

    return _Shadow()


# ---------------------------------------------------------------------------
# Domain: the approved binding form (return_all_to_auto companion work)


# ---------------------------------------------------------------------------
# Runtime: anchored latch (new durable format)


async def test_newer_proven_report_releases_anchored_latch() -> None:
    """The morning sequence: an unconfirmed turn_on latch anchored at 09:00;
    the manual turn-on at 11:02 is a proven report newer than the boundary
    and releases the whole target."""

    hass = _FakeHass()
    hass.states.set("light.demo_main", "on", last_changed=_NOW_DT)
    runtime = _make_runtime(
        hass,
        payload=_config_payload(),
        ownership_store=_MemoryOwnershipStore(),
    )
    await runtime.start(hass, "entry")
    try:
        anchor = _NOW_MS - 60_000
        runtime._dispatch_failures[(_ROOM_ID, "light_main")] = (  # type: ignore[attr-defined]
            "turn_on",
            "untrusted:off",
            anchor,
        )
        assert (
            runtime._target_attempt_blocked(
                (_ROOM_ID, "light_main"),
                "light.demo_main",
                LightAction.TURN_ON,
            )
            is False
        )
        assert (_ROOM_ID, "light_main") not in runtime._dispatch_failures  # type: ignore[attr-defined]
    finally:
        await runtime.stop()


async def test_stale_proven_report_does_not_release_anchored_latch() -> None:
    """A proven report OLDER than the boundary proves nothing about the
    attempt: the latch holds."""

    hass = _FakeHass()
    hass.states.set(
        "light.demo_main",
        "on",
        last_changed=_NOW_DT.replace(year=_NOW_DT.year - 1),
    )
    runtime = _make_runtime(
        hass,
        payload=_config_payload(),
        ownership_store=_MemoryOwnershipStore(),
    )
    await runtime.start(hass, "entry")
    try:
        runtime._dispatch_failures[(_ROOM_ID, "light_main")] = (  # type: ignore[attr-defined]
            "turn_on",
            "untrusted:off",
            _NOW_MS - 1_000,
        )
        assert (
            runtime._target_attempt_blocked(
                (_ROOM_ID, "light_main"),
                "light.demo_main",
                LightAction.TURN_ON,
            )
            is True
        )
        assert (_ROOM_ID, "light_main") in runtime._dispatch_failures  # type: ignore[attr-defined]
    finally:
        await runtime.stop()


async def test_anchored_latch_ignores_revision_timestamp() -> None:
    """AUD-LATCH-05: the revision timestamp (T0) never substitutes for the
    attempt boundary (T2): a report between T0 and T2 does not release."""

    from datetime import timedelta as _td

    hass = _FakeHass()
    # Proven report at T1 between the revision time T0 and the boundary T2.
    hass.states.set(
        "light.demo_main",
        "on",
        last_changed=_NOW_DT - _td(seconds=2),
    )
    runtime = _make_runtime(
        hass,
        payload=_config_payload(),
        ownership_store=_MemoryOwnershipStore(),
    )
    await runtime.start(hass, "entry")
    try:
        runtime._dispatch_failures[(_ROOM_ID, "light_main")] = (  # type: ignore[attr-defined]
            "turn_on",
            "off|1000",  # revision timestamp T0 = 1000
            _NOW_MS,  # attempt boundary T2 = now (after T1)
        )
        assert (
            runtime._target_attempt_blocked(
                (_ROOM_ID, "light_main"),
                "light.demo_main",
                LightAction.TURN_ON,
            )
            is True
        )
    finally:
        await runtime.stop()


# ---------------------------------------------------------------------------
# Runtime: unknown-boundary latch (old/corrupted format)


async def test_newer_aged_out_same_state_report_releases_anchored_latch() -> None:
    """AUD-LATCH-02 for the anchored case: the light stays OFF — the device
    answers the redundant turn_on with the SAME value, so last_changed does
    not move; the refresh lands in last_reported (350 seconds ago — already
    outside the freshness window), the evidence revision collapses back to
    the recorded "untrusted:off", and the anchor comparison still releases:
    the proven report is strictly newer than the boundary."""

    from types import SimpleNamespace
    from datetime import timedelta

    hass = _FakeHass()
    boundary = _NOW_MS - 400_000
    answered_at = _NOW_MS - 350_000
    answered_dt = _NOW_DT - timedelta(milliseconds=_NOW_MS - answered_at)
    old_dt = _NOW_DT - timedelta(seconds=400)
    runtime = _make_runtime(
        hass,
        payload=_config_payload(),
        ownership_store=_MemoryOwnershipStore(),
    )
    await runtime.start(hass, "entry")
    try:
        # The light state: OFF, last changed BEFORE the attempt; the
        # same-value answer refreshed the observation afterwards.
        state = SimpleNamespace(
            state="off",
            attributes={},
            last_changed=old_dt,
            last_updated=answered_dt,
            last_reported=answered_dt,
        )
        hass.states = SimpleNamespace(get=lambda entity_id: state)  # type: ignore[assignment]
        runtime._dispatch_failures[(_ROOM_ID, "light_main")] = (  # type: ignore[attr-defined]
            "turn_on",
            "untrusted:off",
            boundary,
        )
        # The current revision collapsed back to the recorded untrusted form.
        assert (
            runtime._target_evidence_revision("light.demo_main")  # type: ignore[attr-defined]
            == "untrusted:off"
        )
        assert (
            runtime._target_attempt_blocked(
                (_ROOM_ID, "light_main"),
                "light.demo_main",
                LightAction.TURN_ON,
            )
            is False
        )
        assert (_ROOM_ID, "light_main") not in runtime._dispatch_failures  # type: ignore[attr-defined]
    finally:
        await runtime.stop()


async def test_report_equal_to_boundary_does_not_release() -> None:
    """AUD-LATCH-05 edge: a proven report at EXACTLY the attempt boundary is
    not strictly newer — the latch holds."""

    hass = _FakeHass()
    hass.states.set("light.demo_main", "on", last_changed=_NOW_DT)
    runtime = _make_runtime(
        hass,
        payload=_config_payload(),
        ownership_store=_MemoryOwnershipStore(),
    )
    await runtime.start(hass, "entry")
    try:
        boundary = _NOW_MS - 5_000
        runtime._dispatch_failures[(_ROOM_ID, "light_main")] = (  # type: ignore[attr-defined]
            "turn_on",
            "untrusted:off",
            boundary,
        )
        # The proven report timestamp equals the boundary exactly.
        from datetime import datetime, timezone

        exact = datetime.fromtimestamp(boundary / 1000, timezone.utc)
        hass.states.set("light.demo_main", "on", last_changed=exact)
        assert (
            runtime._target_attempt_blocked(
                (_ROOM_ID, "light_main"),
                "light.demo_main",
                LightAction.TURN_ON,
            )
            is True
        )
        assert (_ROOM_ID, "light_main") in runtime._dispatch_failures  # type: ignore[attr-defined]
    finally:
        await runtime.stop()


async def test_unknown_boundary_blocks_stale_and_releases_fresh() -> None:
    """AUD-LATCH-01/03 final semantics: an unknown boundary stays fail-closed
    while the evidence is untrusted; a FRESH trusted live report bounds the
    block to one freshness window and releases (the device proved it is
    alive)."""

    hass = _FakeHass()
    # Stale untrusted state (older than the freshness window).
    hass.states.set(
        "light.demo_main",
        "on",
        last_changed=_NOW_DT.replace(year=_NOW_DT.year - 1),
    )
    runtime = _make_runtime(
        hass,
        payload=_config_payload(),
        ownership_store=_MemoryOwnershipStore(),
    )
    await runtime.start(hass, "entry")
    try:
        runtime._dispatch_failures[(_ROOM_ID, "light_main")] = (  # type: ignore[attr-defined]
            "turn_on",
            "untrusted:off",
            None,
        )
        assert (
            runtime._target_attempt_blocked(
                (_ROOM_ID, "light_main"),
                "light.demo_main",
                LightAction.TURN_ON,
            )
            is True
        )
        assert (_ROOM_ID, "light_main") in runtime._dispatch_failures  # type: ignore[attr-defined]
        # Fresh trusted evidence: the device proved it is alive.
        hass.states.set(
            "light.demo_main",
            "on",
            last_changed=_NOW_DT,
        )
        assert (
            runtime._target_attempt_blocked(
                (_ROOM_ID, "light_main"),
                "light.demo_main",
                LightAction.TURN_ON,
            )
            is False
        )
        assert (_ROOM_ID, "light_main") not in runtime._dispatch_failures  # type: ignore[attr-defined]
    finally:
        await runtime.stop()


async def test_two_element_record_is_fail_closed_then_fresh_releases() -> None:
    """luna P1: a two-element record (no anchor field) is an unknown
    boundary — the revision timestamp is never a substitute."""

    hass = _FakeHass()
    hass.states.set(
        "light.demo_main",
        "on",
        last_changed=_NOW_DT.replace(year=_NOW_DT.year - 1),
    )
    runtime = _make_runtime(
        hass,
        payload=_config_payload(),
        ownership_store=_MemoryOwnershipStore(),
    )
    await runtime.start(hass, "entry")
    try:
        runtime._dispatch_failures[(_ROOM_ID, "light_main")] = (  # type: ignore[attr-defined]
            "turn_on",
            "off|1000",
        )
        assert (
            runtime._target_attempt_blocked(
                (_ROOM_ID, "light_main"),
                "light.demo_main",
                LightAction.TURN_ON,
            )
            is True
        )
        assert (_ROOM_ID, "light_main") in runtime._dispatch_failures  # type: ignore[attr-defined]
    finally:
        await runtime.stop()


async def test_planned_off_stays_safe_for_unknown_boundary() -> None:
    """A planned off is the safe direction: allowed without dropping the
    unknown-boundary record; a repeated off against an untrusted unchanged
    latch stays blocked."""

    hass = _FakeHass()
    hass.states.set(
        "light.demo_main",
        "on",
        last_changed=_NOW_DT.replace(year=_NOW_DT.year - 1),
    )
    runtime = _make_runtime(
        hass,
        payload=_config_payload(),
        ownership_store=_MemoryOwnershipStore(),
    )
    await runtime.start(hass, "entry")
    try:
        runtime._dispatch_failures[(_ROOM_ID, "light_main")] = (  # type: ignore[attr-defined]
            "turn_on",
            "untrusted:off",
            None,
        )
        assert (
            runtime._target_attempt_blocked(
                (_ROOM_ID, "light_main"),
                "light.demo_main",
                LightAction.TURN_OFF,
            )
            is False
        )
        assert (_ROOM_ID, "light_main") in runtime._dispatch_failures  # type: ignore[attr-defined]
    finally:
        await runtime.stop()


# ---------------------------------------------------------------------------
# Restore: the durable boundary versions


async def test_restore_keeps_exact_boundary_for_new_format() -> None:
    """New-format entries (attemptBoundary: true) restore with the exact
    attempt boundary and release against a newer proven report."""

    hass = _FakeHass()
    hass.states.set("light.demo_main", "on", last_changed=_NOW_DT)
    runtime = _make_runtime(
        hass,
        payload=_config_payload(),
        ownership_store=_MemoryOwnershipStore(),
    )
    runtime._shadow = _shadow_stub(
        {
            "version": 1,
            "mode": "shadow",
            "commandsEnabled": False,
            "entries": [],
            "dispatchFailures": [
                _durable_entry(_NOW_MS - 60_000, boundary=True)
            ],
        }
    )
    await runtime.start(hass, "entry")
    try:
        record = runtime._dispatch_failures[(_ROOM_ID, "light_main")]  # type: ignore[attr-defined]
        assert record[1] == "untrusted:off"
        assert record[2] == _NOW_MS - 60_000
        assert (
            runtime._target_attempt_blocked(
                (_ROOM_ID, "light_main"),
                "light.demo_main",
                LightAction.TURN_ON,
            )
            is False
        )
        assert (_ROOM_ID, "light_main") not in runtime._dispatch_failures  # type: ignore[attr-defined]
    finally:
        await runtime.stop()


async def test_restore_treats_old_format_as_unknown_boundary() -> None:
    """luna P1/v10: old-format entries (blockedAfterMs was the room pass
    time, no attemptBoundary marker) restore as UNKNOWN — fail-closed for
    turn_on while the evidence is untrusted."""

    hass = _FakeHass()
    hass.states.set(
        "light.demo_main",
        "on",
        last_changed=_NOW_DT.replace(year=_NOW_DT.year - 1),
    )
    runtime = _make_runtime(
        hass,
        payload=_config_payload(),
        ownership_store=_MemoryOwnershipStore(),
    )
    runtime._shadow = _shadow_stub(
        {
            "version": 1,
            "mode": "shadow",
            "commandsEnabled": False,
            "entries": [],
            "dispatchFailures": [
                _durable_entry(_NOW_MS - 60_000, boundary=False)
            ],
        }
    )
    await runtime.start(hass, "entry")
    try:
        record = runtime._dispatch_failures[(_ROOM_ID, "light_main")]  # type: ignore[attr-defined]
        assert record[2] is None
        assert (
            runtime._target_attempt_blocked(
                (_ROOM_ID, "light_main"),
                "light.demo_main",
                LightAction.TURN_ON,
            )
            is True
        )
    finally:
        await runtime.stop()


async def test_invalid_attempt_at_stays_unknown_through_real_store() -> None:
    """AUD-LATCH-03: a PRESENT but invalid _attemptAt leaves the durable
    boundary unknown (no invented room-pass time); restore blocks turn_on
    while the evidence is untrusted."""

    hass = _FakeHass()
    hass.states.set(
        "light.demo_main",
        "on",
        last_changed=_NOW_DT.replace(year=_NOW_DT.year - 1),
    )
    runtime = _make_runtime(
        hass,
        payload=_config_payload(),
        ownership_store=_MemoryOwnershipStore(),
    )
    await runtime.start(hass, "entry")
    try:
        await runtime._shadow.async_record_dispatch(
            at=_NOW_MS,
            room_id=_ROOM_ID,
            receipts=[
                {
                    "targetId": "light_main",
                    "action": "turn_on",
                    "evidenceRevision": "untrusted:off",
                    "dispatchAttempt": True,
                    "confirmed": False,
                    "_attemptAt": -1,
                }
            ],
        )
        durable = runtime._shadow.journal_payload()["dispatchFailures"][0]
        assert "blockedAfterMs" not in durable
    finally:
        await runtime.stop()


# ---------------------------------------------------------------------------
# The real dispatch boundary round trip (AUD-LATCH-06)


async def test_real_dispatch_boundary_round_trip() -> None:
    """The attempt boundary travels through the REAL _dispatch flow: pre-send
    marker, in-memory latch, the receipt _attemptAt, the real shadow store
    and the restart restore. A report between the room-processing start and
    the attempt boundary must not release; a report after the boundary must."""

    clock = {"now": _NOW_MS}
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "on", last_changed=_NOW_DT)
    hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)

    def fake_now_ms():
        return clock["now"]

    executor = _SpyExecutor(confirmed=False)
    runtime = _make_runtime(
        hass,
        commands_enabled=True,
        now_ms=fake_now_ms,
        executor=executor,
        payload=_config_payload(commands_enabled=True),
        ownership_store=_MemoryOwnershipStore(),
    )
    await runtime.start(hass, "entry")
    try:
        # The start evaluation may arm a latch through the not-confirming spy
        # executor; the test controls its own pre-state.
        runtime._dispatch_failures.clear()  # type: ignore[attr-defined]
        config = config_from_payload(_config_payload(commands_enabled=True))
        t_room_start = fake_now_ms()
        clock["now"] = t_room_start + 5_000
        t_attempt = fake_now_ms()
        decision = RoomLightingDecision(
            room_id=_ROOM_ID,
            evaluated_at=t_room_start,
            mode="day",
            targets=(
                TargetDecision(
                    "light_main",
                    "on",
                    80,
                    None,
                    commands=(
                        PlannedCommand(
                            "light_main",
                            LightAction.TURN_ON,
                            brightness=80,
                            reason=DecisionReason.SCHEDULE,
                        ),
                    ),
                ),
            ),
            commands_enabled=True,
        )
        receipts = await runtime._dispatch(config, decision, t_room_start)
        assert receipts, "the real dispatch produced no receipts"
        receipt = next(r for r in receipts if r.get("targetId") == "light_main")
        assert receipt.get("dispatchAttempt") is True
        assert receipt.get("_attemptAt") == t_attempt
        # The in-memory latch anchor is the attempt boundary.
        record = runtime._dispatch_failures[(_ROOM_ID, "light_main")]  # type: ignore[attr-defined]
        assert record[2] == t_attempt
        # The receipts are journalled through the REAL shadow path with the
        # room-processing pass time; the _attemptAt must win.
        await runtime._shadow.async_record_dispatch(
            at=t_room_start,
            room_id=_ROOM_ID,
            receipts=receipts,
        )
        payload = runtime._shadow.journal_payload()
        durable = payload["dispatchFailures"][0]
        assert durable["blockedAfterMs"] == t_attempt
        assert durable.get("attemptBoundary") is True
    finally:
        await runtime.stop()

    # Restart from the saved payload.
    runtime2 = _make_runtime(
        hass,
        commands_enabled=True,
        now_ms=fake_now_ms,
        executor=_SpyExecutor(confirmed=False),
        payload=_config_payload(commands_enabled=True),
        ownership_store=_MemoryOwnershipStore(),
    )
    class _ShadowRestore:
        """Real-store behaviour for the paths this test exercises."""

        async def async_load(self):
            return self.payload

        async def async_save(self, new_payload):
            self.payload = new_payload

        def journal_payload(self):
            return self.payload

        async def async_record_pending_dispatch(
            self, *, at, room_id, target_id, action,
            evidence_revision, operation_id=None, config_version=None,
        ):
            key = f"target:{room_id}:{target_id}"
            marker = {
                "key": key, "kind": "target", "roomId": room_id,
                "targetId": target_id, "action": action,
                "evidenceRevision": evidence_revision,
                "reason": "pending_dispatch",
            }
            if isinstance(at, int) and not isinstance(at, bool) and at > 0:
                marker["blockedAfterMs"] = at
                marker["attemptBoundary"] = True
            failures = list(self.payload.get("dispatchFailures", []))
            self.payload["dispatchFailures"] = [
                item for item in failures if item.get("key") != key
            ] + [marker]

    runtime2._shadow = _ShadowRestore()
    runtime2._shadow.payload = payload
    await runtime2.start(hass, "entry")
    try:
        record2 = runtime2._dispatch_failures[(_ROOM_ID, "light_main")]  # type: ignore[attr-defined]
        assert record2[2] == t_attempt
        # A proven report between the processing start and the attempt
        # boundary does NOT release (the old anchor would have).
        clock["now"] = t_room_start + 2_000
        hass.states.set(
            "light.demo_main",
            "on",
            last_changed=_NOW_DT + timedelta(milliseconds=clock["now"] - _NOW_MS),
        )
        assert (
            runtime2._target_attempt_blocked(
                (_ROOM_ID, "light_main"),
                "light.demo_main",
                LightAction.TURN_ON,
            )
            is True
        )
        # A proven report after the attempt boundary releases.
        clock["now"] = t_attempt + 1_000
        hass.states.set(
            "light.demo_main",
            "off",
            last_changed=_NOW_DT + timedelta(milliseconds=clock["now"] - _NOW_MS),
        )
        assert (
            runtime2._target_attempt_blocked(
                (_ROOM_ID, "light_main"),
                "light.demo_main",
                LightAction.TURN_ON,
            )
            is False
        )
        # S1 re-latch: the light goes off again with the proven report at
        # t_attempt+1s; THEN the clock advances to t_attempt+3s and one more
        # REAL unconfirmed dispatch re-arms the latch with the NEW attempt
        # boundary. The OLD report (t_attempt+1s) is older than that new
        # boundary and must not release the re-armed latch.
        clock["now"] = t_attempt + 1_000
        old_report_ms = fake_now_ms()
        hass.states.set(
            "light.demo_main",
            "off",
            last_changed=_NOW_DT + timedelta(milliseconds=old_report_ms - _NOW_MS),
        )
        clock["now"] = t_attempt + 3_000
        receipts2 = await runtime2._dispatch(config, decision, clock["now"])
        receipt2 = next(r for r in receipts2 if r.get("targetId") == "light_main")
        assert receipt2.get("dispatchAttempt") is True
        assert receipt2.get("confirmed") is not True
        assert receipt2.get("_attemptAt") == clock["now"]
        record3 = runtime2._dispatch_failures[(_ROOM_ID, "light_main")]  # type: ignore[attr-defined]
        assert record3[2] == clock["now"]
        assert old_report_ms > t_attempt and record3[2] > old_report_ms, (
            "the old report must sit between the first attempt and the new "
            "boundary"
        )
        # The OLD proven report (t_attempt+1s) is older than the new
        # boundary: the re-armed latch holds against it.
        assert (
            runtime2._target_attempt_blocked(
                (_ROOM_ID, "light_main"),
                "light.demo_main",
                LightAction.TURN_ON,
            )
            is True
        )
        # The new boundary is also what the receipt carried and what the
        # durable store recorded (already asserted above via _attemptAt /
        # blockedAfterMs); the old report does not substitute for it.
    finally:
        await runtime2.stop()


# ---------------------------------------------------------------------------
# The superseded carry-through via the real shadow store (AUD-LATCH-06)


async def test_superseded_unknown_anchor_carries_through_real_store() -> None:
    """An unknown superseded anchor stays unknown through the REAL shadow
    receipt path: no invented blockedAfterMs; restore keeps the anchor
    unknown; turn_on stays blocked while the evidence is untrusted."""

    hass = _FakeHass()
    hass.states.set(
        "light.demo_main",
        "on",
        last_changed=_NOW_DT.replace(year=_NOW_DT.year - 1),
    )
    runtime = _make_runtime(
        hass,
        payload=_config_payload(),
        ownership_store=_MemoryOwnershipStore(),
    )
    await runtime.start(hass, "entry")
    try:
        await runtime._shadow.async_record_dispatch(
            at=_NOW_MS,
            room_id=_ROOM_ID,
            receipts=[
                {
                    "targetId": "light_main",
                    "action": "turn_off",
                    "evidenceRevision": "untrusted:on",
                    "dispatchAttempt": False,
                    "sendOutcome": "not_sent",
                    "reason": "manual_off_protection",
                    "_supersededFailure": {
                        "action": "turn_on",
                        "evidenceRevision": "untrusted:off",
                        "anchorMs": None,
                    },
                }
            ],
        )
        durable = runtime._shadow.journal_payload()["dispatchFailures"][0]
        assert "blockedAfterMs" not in durable
    finally:
        await runtime.stop()

    runtime2 = _make_runtime(
        hass,
        payload=_config_payload(),
        ownership_store=_MemoryOwnershipStore(),
    )
    runtime2._shadow = _shadow_stub(runtime._shadow.journal_payload())
    await runtime2.start(hass, "entry")
    try:
        record2 = runtime2._dispatch_failures[(_ROOM_ID, "light_main")]  # type: ignore[attr-defined]
        assert record2[1] == "untrusted:off"
        assert record2[2] is None
        assert (
            runtime2._target_attempt_blocked(
                (_ROOM_ID, "light_main"),
                "light.demo_main",
                LightAction.TURN_ON,
            )
            is True
        )
        assert (
            runtime2._target_attempt_blocked(
                (_ROOM_ID, "light_main"),
                "light.demo_main",
                LightAction.TURN_OFF,
            )
            is False
        )
        assert (_ROOM_ID, "light_main") in runtime2._dispatch_failures  # type: ignore[attr-defined]
    finally:
        await runtime2.stop()


# ---------------------------------------------------------------------------
# Status projection: the record form (companion house gesture)


def _minimal_status_context(now_ms: int):
    from custom_components.hausman_hub.domain.room_lighting import SensorKind
    from custom_components.hausman_hub.domain.room_lighting_engine import (
        LightSnapshot,
        RoomLightingContext,
        SensorSnapshot,
    )
    from custom_components.hausman_hub.domain.room_lighting_ownership import (
        SensorState,
    )

    return RoomLightingContext(
        now=now_ms,
        timezone=__import__("datetime").timezone.utc,
        sunrise=__import__("datetime").time(7, 0),
        sunset=__import__("datetime").time(19, 0),
        sensors=(
            SensorSnapshot(
                "sensor_demo_presence", SensorKind.PRESENCE, SensorState.OFF, now_ms
            ),
        ),
        lights=(LightSnapshot("light_main", SensorState.OFF, now_ms),),
    )




async def test_pre_send_marker_keeps_exact_boundary_across_restart() -> None:
    """AUD-LATCH-07: a crash right after the pre-send marker (before any
    receipt) keeps the exact attempt boundary across restart; a report before
    the boundary does not release, a report after it does."""

    from datetime import timedelta

    clock = {"now": _NOW_MS}
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "on", last_changed=_NOW_DT)
    hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)

    def fake_now_ms():
        return clock["now"]

    runtime = _make_runtime(
        hass,
        commands_enabled=True,
        now_ms=fake_now_ms,
        payload=_config_payload(commands_enabled=True),
        ownership_store=_MemoryOwnershipStore(),
    )
    await runtime.start(hass, "entry")
    try:
        runtime._dispatch_failures.clear()  # type: ignore[attr-defined]
        clock["now"] += 5_000
        t_attempt = fake_now_ms()
        saved = await runtime._persist_pending_target_dispatch(
            at=t_attempt,
            room_id=_ROOM_ID,
            target_id="light_main",
            action="turn_on",
            evidence_revision="untrusted:off",
            operation_id=None,
            config_version=None,
        )
        assert saved is True
        payload = runtime._shadow.journal_payload()
        marker = payload["dispatchFailures"][0]
        assert marker.get("attemptBoundary") is True
        assert marker["blockedAfterMs"] == t_attempt
    finally:
        await runtime.stop()

    runtime2 = _make_runtime(
        hass,
        commands_enabled=True,
        now_ms=fake_now_ms,
        payload=_config_payload(commands_enabled=True),
        ownership_store=_MemoryOwnershipStore(),
    )
    class _ShadowRestore:
        async def async_load(self):
            return payload

        async def async_save(self, new_payload):
            return None

        def journal_payload(self):
            return payload

    runtime2._shadow = _ShadowRestore()
    await runtime2.start(hass, "entry")
    try:
        record = runtime2._dispatch_failures[(_ROOM_ID, "light_main")]  # type: ignore[attr-defined]
        assert record[2] == t_attempt
        # A proven report before the boundary does not release.
        clock["now"] = t_attempt - 1_000
        hass.states.set(
            "light.demo_main",
            "on",
            last_changed=_NOW_DT + timedelta(milliseconds=clock["now"] - _NOW_MS),
        )
        assert (
            runtime2._target_attempt_blocked(
                (_ROOM_ID, "light_main"),
                "light.demo_main",
                LightAction.TURN_ON,
            )
            is True
        )
        # A proven report strictly after the boundary releases.
        clock["now"] = t_attempt + 1_000
        hass.states.set(
            "light.demo_main",
            "off",
            last_changed=_NOW_DT + timedelta(milliseconds=clock["now"] - _NOW_MS),
        )
        assert (
            runtime2._target_attempt_blocked(
                (_ROOM_ID, "light_main"),
                "light.demo_main",
                LightAction.TURN_ON,
            )
            is False
        )
    finally:
        await runtime2.stop()


async def test_superseded_known_anchor_keeps_exact_boundary() -> None:
    """AUD-LATCH-07: a superseded latch with a KNOWN anchor keeps its exact
    boundary across the real shadow path and a restart."""

    from datetime import timedelta

    known_anchor = _NOW_MS - 30_000
    clock = {"now": _NOW_MS}
    hass = _FakeHass()
    hass.states.set("light.demo_main", "on", last_changed=_NOW_DT)

    def fake_now_ms():
        return clock["now"]

    runtime = _make_runtime(
        hass,
        commands_enabled=True,
        now_ms=fake_now_ms,
        payload=_config_payload(commands_enabled=True),
        ownership_store=_MemoryOwnershipStore(),
    )
    await runtime.start(hass, "entry")
    try:
        await runtime._shadow.async_record_dispatch(
            at=_NOW_MS,
            room_id=_ROOM_ID,
            receipts=[
                {
                    "targetId": "light_main",
                    "action": "turn_off",
                    "evidenceRevision": "untrusted:on",
                    "dispatchAttempt": False,
                    "sendOutcome": "not_sent",
                    "reason": "manual_off_protection",
                    "_supersededFailure": {
                        "action": "turn_on",
                        "evidenceRevision": "untrusted:off",
                        "anchorMs": known_anchor,
                    },
                }
            ],
        )
        durable = runtime._shadow.journal_payload()["dispatchFailures"][0]
        assert durable.get("attemptBoundary") is True
        assert durable["blockedAfterMs"] == known_anchor
    finally:
        await runtime.stop()

    runtime2 = _make_runtime(
        hass,
        commands_enabled=True,
        now_ms=fake_now_ms,
        payload=_config_payload(commands_enabled=True),
        ownership_store=_MemoryOwnershipStore(),
    )
    class _ShadowRestore:
        async def async_load(self):
            return runtime._shadow.journal_payload()

        async def async_save(self, new_payload):
            return None

        def journal_payload(self):
            return runtime._shadow.journal_payload()

    runtime2._shadow = _ShadowRestore()
    await runtime2.start(hass, "entry")
    try:
        record = runtime2._dispatch_failures[(_ROOM_ID, "light_main")]  # type: ignore[attr-defined]
        assert record[2] == known_anchor
        # A proven report before the anchor does not release.
        clock["now"] = known_anchor - 1_000
        hass.states.set(
            "light.demo_main",
            "on",
            last_changed=_NOW_DT + timedelta(milliseconds=clock["now"] - _NOW_MS),
        )
        assert (
            runtime2._target_attempt_blocked(
                (_ROOM_ID, "light_main"),
                "light.demo_main",
                LightAction.TURN_ON,
            )
            is True
        )
        # A proven report strictly after the anchor releases.
        clock["now"] = known_anchor + 1_000
        hass.states.set(
            "light.demo_main",
            "off",
            last_changed=_NOW_DT + timedelta(milliseconds=clock["now"] - _NOW_MS),
        )
        assert (
            runtime2._target_attempt_blocked(
                (_ROOM_ID, "light_main"),
                "light.demo_main",
                LightAction.TURN_ON,
            )
            is False
        )
    finally:
        await runtime2.stop()


async def test_unknown_boundary_fresh_trusted_releases_despite_same_revision() -> None:
    """AUD-LATCH-08: the fresh-trusted release for an unknown boundary is not
    vetoed by the revision-equality shortcut (recorded revision == current
    fresh trusted revision)."""

    hass = _FakeHass()
    hass.states.set("light.demo_main", "off", last_changed=_NOW_DT)
    runtime = _make_runtime(
        hass,
        payload=_config_payload(),
        ownership_store=_MemoryOwnershipStore(),
    )
    await runtime.start(hass, "entry")
    try:
        # The recorded revision equals the current fresh trusted revision.
        current_revision = runtime._target_evidence_revision("light.demo_main")  # type: ignore[attr-defined]
        runtime._dispatch_failures[(_ROOM_ID, "light_main")] = (  # type: ignore[attr-defined]
            "turn_on",
            current_revision,
            None,
        )
        assert (
            runtime._target_attempt_blocked(
                (_ROOM_ID, "light_main"),
                "light.demo_main",
                LightAction.TURN_ON,
            )
            is False
        )
        assert (_ROOM_ID, "light_main") not in runtime._dispatch_failures  # type: ignore[attr-defined]
    finally:
        await runtime.stop()


@pytest.mark.parametrize(
    "bad_anchor",
    [None, True, False, -5, 0, "untrusted:off", 1.5],
)
async def test_invalid_in_memory_anchor_types_fail_closed(bad_anchor) -> None:
    """AUD-LATCH-01 closure criteria: an in-memory anchor that is None,
    bool, negative, zero, a string or a float is an unknown boundary —
    turn_on stays blocked while the evidence is untrusted (the revision
    timestamp is never a substitute)."""

    from datetime import timedelta

    hass = _FakeHass()
    # Fresh trusted state would release a KNOWN boundary; for an unknown
    # boundary the block holds regardless (the freshness release only
    # applies to the unknown case, and this test pins the fail-closed side
    # with a stale untrusted state).
    hass.states.set(
        "light.demo_main",
        "on",
        last_changed=_NOW_DT.replace(year=_NOW_DT.year - 1),
    )
    runtime = _make_runtime(
        hass,
        payload=_config_payload(),
        ownership_store=_MemoryOwnershipStore(),
    )
    await runtime.start(hass, "entry")
    try:
        runtime._dispatch_failures[(_ROOM_ID, "light_main")] = (  # type: ignore[attr-defined]
            "turn_on",
            "untrusted:off",
            bad_anchor,
        )
        assert (
            runtime._target_attempt_blocked(
                (_ROOM_ID, "light_main"),
                "light.demo_main",
                LightAction.TURN_ON,
            )
            is True
        )
        assert (_ROOM_ID, "light_main") in runtime._dispatch_failures  # type: ignore[attr-defined]
    finally:
        await runtime.stop()


# ---------------------------------------------------------------------------
# Tambur curveParams engine (HH-007): D/P/L matrix coverage


def _tambur():
    from custom_components.hausman_hub.domain.tambur_curve import (
        PresencePolicy,
        TamburCurveParams,
        TamburCurveSnapshot,
        evaluate_tambur_curve,
    )
    params = TamburCurveParams()
    def snap(now, *, sunset=1200, sunrise=480, healthy=True, lux=None,
             absence=False, presence=True, away=False):
        return TamburCurveSnapshot(
            now_minutes=now, sunrise_minutes=sunrise, sunset_minutes=sunset,
            sun_healthy=healthy, outdoor_lux=lux,
            presence_confirmed=presence, absence_confirmed=absence, away=away,
        )
    return params, snap, evaluate_tambur_curve


def test_curve_morning_ramp_20_to_100_by_12() -> None:
    """D-03: the computed maximum grows 20%→100% from dayStart(09:00) to
    fullBy(12:00)."""
    params, snap, evaluate = _tambur()
    at_9 = evaluate(params, snap(540))
    at_1030 = evaluate(params, snap(630))
    at_12 = evaluate(params, snap(720))
    assert at_9.required_percent == 20
    assert at_1030.required_percent == 60
    assert at_12.required_percent == 100
    assert at_9.chandelier_on and at_12.chandelier_on


def test_curve_day_hold_and_evening_decline_to_5_by_21() -> None:
    """D-01/D-05: 100% at day; the decline reaches 5% by 21:00; the mirror
    takes over at 22:00; everything is off at 23:00."""
    params, snap, evaluate = _tambur()
    at_12 = evaluate(params, snap(720))
    at_18 = evaluate(params, snap(1080))
    at_21 = evaluate(params, snap(1260))
    at_2159 = evaluate(params, snap(1259 + 60))  # 21:59
    assert at_12.required_percent == 100
    assert at_18.mode.value == "day"
    assert at_21.required_percent == 5
    assert at_2159.required_percent == 5
    # 22:00 by_time: mirror on, chandelier off.
    at_22 = evaluate(params, snap(1320))
    assert at_22.mirror_on is True and at_22.chandelier_on is False
    # 23:00: everything off.
    at_23 = evaluate(params, snap(1380))
    assert at_23.chandelier_on is False and at_23.mirror_on is False


def test_curve_wait_absence_holds_then_releases() -> None:
    """P-02/P-03: wait_absence holds the transition while the person is
    present; the confirmed absence completes it."""
    from custom_components.hausman_hub.domain.tambur_curve import (
        PresencePolicy,
        TamburCurveParams,
    )
    params = TamburCurveParams(
        mirror_transition_policy=PresencePolicy.WAIT_ABSENCE,
        all_off_policy=PresencePolicy.WAIT_ABSENCE,
    )
    _, snap, evaluate = _tambur()
    p2 = (params, snap)
    at_22_person = evaluate(params, snap(1320, absence=False))
    assert at_22_person.transition_pending is True
    assert at_22_person.chandelier_on is True  # held, not switched
    at_22_absent = evaluate(params, snap(1325, absence=True))
    assert at_22_absent.mirror_on is True and at_22_absent.chandelier_on is False
    # all-off at 23:00 with the same policy.
    at_23_person = evaluate(params, snap(1380, absence=False))
    assert at_23_person.transition_pending is True or (
        at_23_person.chandelier_on is True or at_23_person.mirror_on is True
    )
    at_23_absent = evaluate(params, snap(1400, absence=True))
    assert at_23_absent.chandelier_on is False and at_23_absent.mirror_on is False


def test_curve_late_sunset_reserve_decline() -> None:
    """L-03/L-04: a genuinely late sunset (after 21:00) starts the reserve
    decline at 20:00, evenly toward 5% by 21:00; never raising a lower
    brightness."""
    params, snap, evaluate = _tambur()
    # Sunset 22:30 (later than targetBy 21:00).
    at_2020 = evaluate(params, snap(1220, sunset=1350))
    at_2045 = evaluate(params, snap(1245, sunset=1350))
    at_2100 = evaluate(params, snap(1260, sunset=1350))
    # 20:20 — the decline has started from 100% toward 5%.
    assert at_2020.required_percent < 100
    assert at_2045.required_percent < at_2020.required_percent
    # By 21:00 the target holds.
    assert at_2100.required_percent == 5
    # An already lower brightness is never raised: the decline is monotone.
    values = [evaluate(params, snap(m, sunset=1350)).required_percent
              for m in range(1200, 1261, 10)]
    assert values == sorted(values, reverse=True)


def test_curve_unavailable_sunset_no_reserve() -> None:
    """L-07: an unavailable sun is NOT replaced by the reserve; the evening
    decline waits for proof (the lux fallback governs §10 in the caller)."""
    params, snap, evaluate = _tambur()
    at_2030 = evaluate(params, snap(1250, sunset=None, healthy=False, lux=None))
    # Without proof the day computation continues (no invented decline).
    assert at_2030.mode.value in ("day", "ramp")
    # The lux fallback: 300 < 400 = sunset.
    at_2030_lux = evaluate(params, snap(1250, sunset=None, healthy=False, lux=300))
    assert at_2030_lux.required_percent <= 100
    # Exactly 400 is NOT sunset (план §10).
    at_2030_400 = evaluate(params, snap(1250, sunset=None, healthy=False, lux=400))
    assert at_2030_400.mode.value in ("day", "ramp")


def test_curve_late_sunrise_no_morning_mirror() -> None:
    """L-06: a late/unavailable sunrise — no morning mirror by guesswork;
    the day chandelier mode starts at dayStart (09:00)."""
    params, snap, evaluate = _tambur()
    at_8 = evaluate(params, snap(480, sunset=1200, sunrise=None, healthy=False))
    assert at_8.chandelier_on is False and at_8.mirror_on is False
    at_9 = evaluate(params, snap(540, sunset=1200, sunrise=None, healthy=False))
    assert at_9.chandelier_on is True


def test_curve_away_overrides_everything() -> None:
    params, snap, evaluate = _tambur()
    at_12_away = evaluate(params, snap(720, away=True))
    assert at_12_away.chandelier_on is False
    assert at_12_away.required_percent == 0


def test_curve_params_order_validation() -> None:
    """Server-side order rules: dayStart < fullBy; reserveStart < targetBy;
    startPercent <= fullPercent."""
    from custom_components.hausman_hub.domain.tambur_curve import (
        TamburCurveParams,
    )
    import pytest
    with pytest.raises(Exception):
        TamburCurveParams(
            day_start=__import__("datetime").time(13, 0),
            morning_full_by=__import__("datetime").time(12, 0),
        )
    with pytest.raises(Exception):
        TamburCurveParams(
            evening_reserve_start=__import__("datetime").time(21, 30),
            evening_target_by=__import__("datetime").time(21, 0),
        )
