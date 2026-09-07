"""Durable manual-open latch and calibration-authority tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.hausman_hub.application.curtain_command_policy import (
    KITCHEN_CURTAIN_TARGET,
    LIVING_CURTAIN_TARGET,
    CurtainCalibrationAuthority,
    CurtainCommandPolicy,
    CurtainPolicyError,
)
from custom_components.hausman_hub.application.curtain_protection import (
    CURTAIN_TARGET_IDS,
    CurtainProtectionCoordinator,
    valid_curtain_protection_payload,
)
from custom_components.hausman_hub.application.scenario_executor import ScenarioExecutor
from custom_components.hausman_hub.application.scenarios import (
    ScenarioCatalog,
    ScenarioDeviceAction,
    ScenarioDeviceEntry,
)
from custom_components.hausman_hub.domain.scenarios import (
    ScenarioAction,
    ScenarioActionType,
    ScenarioDefinition,
    ScenarioExecutionMode,
    ScenarioSafetyPolicy,
    ScenarioTrigger,
    ScenarioTriggerType,
)


class MemoryStore:
    def __init__(self, payload=None, *, fail_load=False, fail_save=False):
        self.payload = payload
        self.fail_load = fail_load
        self.fail_save = fail_save
        self.recovered_previous = False
        self.saves = 0

    async def async_load(self):
        if self.fail_load:
            raise RuntimeError("broken")
        return self.payload

    async def async_save(self, payload):
        self.saves += 1
        if self.fail_save:
            raise RuntimeError("broken")
        self.payload = payload


def devices():
    return {
        target: SimpleNamespace(target_id=target, entity_id=f"cover.{index}")
        for index, target in enumerate(CURTAIN_TARGET_IDS)
    }


async def coordinator(store=None, *, clock=None, sunrise=10_000):
    table = devices()
    clock = clock or [1_000]
    result = CurtainProtectionCoordinator(
        store or MemoryStore(),
        catalog_resolver=table.get,
        next_sunrise_ms=lambda: sunrise,
        now_ms=lambda: clock[0],
    )
    await result.async_load()
    return result, table, clock


async def confirmed_auto_close(value, table, *, target=LIVING_CURTAIN_TARGET):
    decision = await value.async_before_action(
        target_id=target,
        entity_id=table[target].entity_id,
        action_id="set_position",
        requested=0,
        applied=0,
        current_position=50,
        automatic=True,
        dry_run=False,
        receipt_id="auto-close-1",
    )
    assert decision.allowed
    await value.async_note_result(
        target_id=target,
        entity_id=table[target].entity_id,
        action_id="set_position",
        requested=0,
        applied=0,
        current_position=50,
        automatic=True,
        dry_run=False,
        receipt_id="auto-close-1",
        protection_generation=decision.token,
        confirmed=True,
        evidence_revision="state-close-1",
    )


@pytest.mark.asyncio
async def test_stale_automatic_close_generation_is_rejected_and_late_receipt_is_ignored() -> None:
    value, table, _clock = await coordinator()
    await confirmed_auto_close(value, table)
    record_before = value.payload["targets"][LIVING_CURTAIN_TARGET]
    old_receipt = record_before["confirmedAutomaticClose"]
    automatic = await value.async_before_action(
        target_id=LIVING_CURTAIN_TARGET,
        entity_id=table[LIVING_CURTAIN_TARGET].entity_id,
        action_id="set_position",
        requested=0,
        applied=0,
        current_position=50,
        automatic=True,
        dry_run=False,
        receipt_id="late-auto-close",
    )
    manual = await value.async_before_action(
        target_id=LIVING_CURTAIN_TARGET,
        entity_id=table[LIVING_CURTAIN_TARGET].entity_id,
        action_id="set_position",
        requested=100,
        applied=100,
        current_position=0,
        automatic=False,
        dry_run=False,
        receipt_id="racing-manual-open",
    )
    rejected = await value.async_validate_before_dispatch(
        target_id=LIVING_CURTAIN_TARGET,
        entity_id=table[LIVING_CURTAIN_TARGET].entity_id,
        action_id="set_position",
        requested=0,
        applied=0,
        current_position=50,
        automatic=True,
        token=automatic.token,
    )
    assert manual.allowed
    assert not rejected.allowed
    assert rejected.reason == "curtain_protection_generation_changed"

    await value.async_note_result(
        target_id=LIVING_CURTAIN_TARGET,
        entity_id=table[LIVING_CURTAIN_TARGET].entity_id,
        action_id="set_position",
        requested=0,
        applied=0,
        current_position=50,
        automatic=True,
        dry_run=False,
        receipt_id="late-auto-close",
        protection_generation=automatic.token,
        confirmed=True,
        evidence_revision="late-close-state",
    )
    record_after = value.payload["targets"][LIVING_CURTAIN_TARGET]
    assert record_after["confirmedAutomaticClose"] == old_receipt
    assert record_after["manualOpenEvidence"]["receiptId"] == "racing-manual-open"


@pytest.mark.asyncio
async def test_unknown_release_boundary_is_durable_and_never_guessed_after_restart() -> None:
    store = MemoryStore()
    value, table, _clock = await coordinator(store, sunrise=None)
    await confirmed_auto_close(value, table)
    await value.async_before_action(
        target_id=LIVING_CURTAIN_TARGET,
        entity_id=table[LIVING_CURTAIN_TARGET].entity_id,
        action_id="open_cover",
        requested=None,
        applied=100,
        current_position=0,
        automatic=False,
        dry_run=False,
        receipt_id="manual-open-without-astronomy",
    )
    await confirmed_auto_close(value, table, target=KITCHEN_CURTAIN_TARGET)
    await value.async_handle_external_open(
        target_id=KITCHEN_CURTAIN_TARGET,
        entity_id=table[KITCHEN_CURTAIN_TARGET].entity_id,
        old_position=0,
        new_position=40,
        evidence_revision="external-without-astronomy",
    )
    assert store.payload["targets"][LIVING_CURTAIN_TARGET]["releaseSunriseAtMs"] is None
    assert store.payload["targets"][KITCHEN_CURTAIN_TARGET]["releaseSunriseAtMs"] is None

    restarted, restarted_table, _clock = await coordinator(store, sunrise=20_000)
    blocked = await restarted.async_before_action(
        target_id=LIVING_CURTAIN_TARGET,
        entity_id=restarted_table[LIVING_CURTAIN_TARGET].entity_id,
        action_id="set_position",
        requested=0,
        applied=0,
        current_position=50,
        automatic=True,
        dry_run=False,
        receipt_id="automatic-after-restart",
    )
    calls = 0
    observed = None

    async def run(*_args, **kwargs):
        nonlocal calls, observed
        calls += 1
        observed = await restarted.async_control_state(
            kwargs["correlation_id"], kwargs["trigger_context"]
        )
        return {"status": "completed"}

    result = await restarted.async_run_trusted_sunrise(20_000, run)
    assert not blocked.allowed
    assert result["status"] == "completed"
    assert calls == 1
    assert observed["targets"][LIVING_CURTAIN_TARGET]["morningOpenAllowed"] is False
    assert observed["targets"][KITCHEN_CURTAIN_TARGET]["morningOpenAllowed"] is False
    assert observed["targets"][CURTAIN_TARGET_IDS[2]]["morningOpenAllowed"] is True
    assert observed["targets"][CURTAIN_TARGET_IDS[3]]["morningOpenAllowed"] is True
    assert restarted.payload["targets"][LIVING_CURTAIN_TARGET]["latchedAtMs"] is not None
    assert restarted.payload["targets"][KITCHEN_CURTAIN_TARGET]["latchedAtMs"] is not None


@pytest.mark.asyncio
async def test_event_bus_ignores_own_sunrise_open_but_latches_external_open() -> None:
    value, table, clock = await coordinator(sunrise=10_000)
    callbacks = []

    class Bus:
        def async_listen(self, event_type, callback):
            assert event_type == "state_changed"
            callbacks.append(callback)
            return lambda: None

        async def fire_position(self, old_position, new_position, revision):
            def state(position):
                return SimpleNamespace(
                    state="closed" if position == 0 else "open",
                    attributes={"current_position": position},
                    last_updated=revision,
                )

            event = SimpleNamespace(data={
                "entity_id": table[LIVING_CURTAIN_TARGET].entity_id,
                "old_state": state(old_position),
                "new_state": state(new_position),
            })
            await callbacks[0](event)

    bus = Bus()
    value.start(SimpleNamespace(bus=bus))
    await confirmed_auto_close(value, table)

    async def run(*_args, **_kwargs):
        await bus.fire_position(
            0, 100, datetime(2026, 9, 7, 5, 0, tzinfo=timezone.utc)
        )
        return {"status": "completed"}

    await value.async_run_trusted_sunrise(2_000, run)
    record = value.payload["targets"][LIVING_CURTAIN_TARGET]
    assert record["confirmedAutomaticClose"] is None
    assert record["latchedAtMs"] is None

    clock[0] = 3_000
    await confirmed_auto_close(value, table)
    await bus.fire_position(
        0, 40, datetime(2026, 9, 7, 18, 0, tzinfo=timezone.utc)
    )
    record = value.payload["targets"][LIVING_CURTAIN_TARGET]
    assert record["latchedAtMs"] == 3_000
    assert record["manualOpenEvidence"]["outcome"] == "external"


@pytest.mark.asyncio
async def test_manual_open_intent_latches_before_dispatch_and_survives_restart() -> None:
    store = MemoryStore()
    value, table, _clock = await coordinator(store)
    await confirmed_auto_close(value, table)

    decision = await value.async_before_action(
        target_id=LIVING_CURTAIN_TARGET,
        entity_id=table[LIVING_CURTAIN_TARGET].entity_id,
        action_id="open_cover",
        requested=None,
        applied=100,
        current_position=0,
        automatic=False,
        dry_run=False,
        receipt_id="manual-open-1",
    )
    assert decision.allowed
    assert store.payload["targets"][LIVING_CURTAIN_TARGET]["manualOpenEvidence"]["outcome"] == "pending"

    restarted, _, _ = await coordinator(store)
    blocked = await restarted.async_before_action(
        target_id=LIVING_CURTAIN_TARGET,
        entity_id=table[LIVING_CURTAIN_TARGET].entity_id,
        action_id="close_cover",
        requested=None,
        applied=0,
        current_position=60,
        automatic=True,
        dry_run=False,
        receipt_id="auto-close-2",
    )
    assert not blocked.allowed
    assert blocked.reason == "curtain_manual_open_latched"


@pytest.mark.asyncio
async def test_unknown_manual_open_and_manual_close_do_not_release_latch() -> None:
    value, table, _clock = await coordinator()
    await confirmed_auto_close(value, table)
    decision = await value.async_before_action(
        target_id=LIVING_CURTAIN_TARGET,
        entity_id=table[LIVING_CURTAIN_TARGET].entity_id,
        action_id="open_cover", requested=None, applied=100, current_position=0,
        automatic=False, dry_run=False, receipt_id="manual-open-unknown",
    )
    await value.async_note_result(
        target_id=LIVING_CURTAIN_TARGET,
        entity_id=table[LIVING_CURTAIN_TARGET].entity_id,
        action_id="open_cover", requested=None, applied=100, current_position=0,
        automatic=False, dry_run=False, receipt_id="manual-open-unknown",
        protection_generation=decision.token,
        confirmed=False, evidence_revision=None,
    )
    await value.async_note_result(
        target_id=LIVING_CURTAIN_TARGET,
        entity_id=table[LIVING_CURTAIN_TARGET].entity_id,
        action_id="close_cover", requested=None, applied=0, current_position=50,
        automatic=False, dry_run=False, receipt_id="manual-close",
        protection_generation=None,
        confirmed=True, evidence_revision="manual-close-state",
    )
    record = value.payload["targets"][LIVING_CURTAIN_TARGET]
    assert record["latchedAtMs"] is not None
    assert record["manualOpenEvidence"]["outcome"] == "unknown"


@pytest.mark.asyncio
async def test_trusted_sunrise_saves_release_before_run_and_old_duplicate_cannot_clear_new_latch() -> None:
    store = MemoryStore()
    value, table, clock = await coordinator(store, sunrise=2_000)
    await confirmed_auto_close(value, table)
    await value.async_before_action(
        target_id=LIVING_CURTAIN_TARGET,
        entity_id=table[LIVING_CURTAIN_TARGET].entity_id,
        action_id="open_cover", requested=None, applied=100, current_position=0,
        automatic=False, dry_run=False, receipt_id="manual-open-1",
    )
    observed = []

    async def run(_scenario_id, **kwargs):
        observed.append((store.saves, value.payload, kwargs))
        state = await value.async_control_state(kwargs["correlation_id"], kwargs["trigger_context"])
        assert state["trustedSunrise"] is True
        return {"status": "completed"}

    result = await value.async_run_trusted_sunrise(2_000, run)
    assert result["status"] == "completed"
    assert observed[0][1]["targets"][LIVING_CURTAIN_TARGET]["latchedAtMs"] is None

    clock[0] = 2_100
    await confirmed_auto_close(value, table)
    value._next_sunrise_ms = lambda: 3_000  # noqa: SLF001
    await value.async_before_action(
        target_id=LIVING_CURTAIN_TARGET,
        entity_id=table[LIVING_CURTAIN_TARGET].entity_id,
        action_id="open_cover", requested=None, applied=100, current_position=0,
        automatic=False, dry_run=False, receipt_id="manual-open-2",
    )
    await value.async_run_trusted_sunrise(2_000, run)
    assert value.payload["targets"][LIVING_CURTAIN_TARGET]["latchedAtMs"] is not None
    assert len(observed) == 1


@pytest.mark.asyncio
async def test_corrupt_or_failed_store_blocks_only_automatic_close() -> None:
    for store in (MemoryStore(fail_load=True), MemoryStore(fail_save=True)):
        value, table, _clock = await coordinator(store)
        automatic = await value.async_before_action(
            target_id=LIVING_CURTAIN_TARGET,
            entity_id=table[LIVING_CURTAIN_TARGET].entity_id,
            action_id="close_cover", requested=None, applied=0, current_position=50,
            automatic=True, dry_run=False, receipt_id="automatic",
        )
        manual = await value.async_before_action(
            target_id=LIVING_CURTAIN_TARGET,
            entity_id=table[LIVING_CURTAIN_TARGET].entity_id,
            action_id="close_cover", requested=None, applied=0, current_position=50,
            automatic=False, dry_run=False, receipt_id="manual",
        )
        assert not automatic.allowed
        assert manual.allowed


@pytest.mark.asyncio
async def test_targets_are_independent_and_dry_run_never_saves() -> None:
    store = MemoryStore()
    value, table, _clock = await coordinator(store)
    await confirmed_auto_close(value, table)
    await value.async_before_action(
        target_id=LIVING_CURTAIN_TARGET,
        entity_id=table[LIVING_CURTAIN_TARGET].entity_id,
        action_id="open_cover", requested=None, applied=100, current_position=0,
        automatic=False, dry_run=False, receipt_id="manual-open",
    )
    saves = store.saves
    other = await value.async_before_action(
        target_id=KITCHEN_CURTAIN_TARGET,
        entity_id=table[KITCHEN_CURTAIN_TARGET].entity_id,
        action_id="set_position", requested=0, applied=0, current_position=50,
        automatic=True, dry_run=True, receipt_id="dry-run",
    )
    assert other.allowed
    assert store.saves == saves


def test_payload_validator_rejects_identity_and_partial_target_sets() -> None:
    table = {
        target: {
            "targetId": target, "entityId": f"cover.{index}", "generation": 0,
            "confirmedAutomaticClose": None, "manualOpenEvidence": None,
            "latchedAtMs": None, "releaseSunriseAtMs": None,
            "lastProcessedSunriseMs": None,
        }
        for index, target in enumerate(CURTAIN_TARGET_IDS)
    }
    assert valid_curtain_protection_payload({"version": 1, "targets": table})
    table.pop(LIVING_CURTAIN_TARGET)
    assert not valid_curtain_protection_payload({"version": 1, "targets": table})


def test_calibration_grant_is_exact_expiring_revocable_and_single_use() -> None:
    clock = [1_000]
    authority = CurtainCalibrationAuthority(now_ms=lambda: clock[0])
    policy = CurtainCommandPolicy()
    actions = {
        "open_cover": SimpleNamespace(domain="cover", service="open_cover"),
        "close_cover": SimpleNamespace(domain="cover", service="close_cover"),
    }
    device = SimpleNamespace(
        target_id=KITCHEN_CURTAIN_TARGET,
        entity_id="cover.kitchen",
        action=actions.get,
    )
    grant = authority.issue(KITCHEN_CURTAIN_TARGET, "full_open", expires_at_ms=2_000)
    with pytest.raises(CurtainPolicyError, match="curtain_calibration_unauthorized"):
        policy.plan_calibration(
            device=device, operation="full_close", grant=grant,
            authority=authority, current_state=None,
        )
    with pytest.raises(CurtainPolicyError, match="curtain_calibration_unauthorized"):
        policy.plan_calibration(
            device=device, operation="full_open", grant=grant,
            authority=authority, current_state=None,
        )

    revoked = authority.issue(KITCHEN_CURTAIN_TARGET, "full_open", expires_at_ms=2_000)
    authority.revoke(revoked)
    assert not authority.consume(
        revoked, target_id=KITCHEN_CURTAIN_TARGET, operation="full_open"
    )
    expired = authority.issue(KITCHEN_CURTAIN_TARGET, "full_open", expires_at_ms=2_000)
    clock[0] = 2_000
    assert not authority.consume(
        expired, target_id=KITCHEN_CURTAIN_TARGET, operation="full_open"
    )

    clock[0] = 1_000
    usable = authority.issue(KITCHEN_CURTAIN_TARGET, "full_open", expires_at_ms=2_000)
    plan = policy.plan_calibration(
        device=device, operation="full_open", grant=usable,
        authority=authority, current_state=None,
    )
    assert plan.applied == 100
    assert plan.service == "open_cover"
    assert policy.target(KITCHEN_CURTAIN_TARGET).scale_confirmed is False
    assert not authority.consume(
        usable, target_id=KITCHEN_CURTAIN_TARGET, operation="full_open"
    )


@pytest.mark.asyncio
async def test_executor_latches_explicit_manual_no_op_after_confirmed_automatic_close() -> None:
    before = datetime.now(timezone.utc) - timedelta(seconds=1)
    state = SimpleNamespace(
        state="open", attributes={"current_position": 50}, last_updated=before
    )
    service = AsyncMock()

    async def apply(_domain, _service, data, **_kwargs):
        position = data.get("position", 0)
        state.state = "closed" if position == 0 else "open"
        state.attributes = {"current_position": position}
        state.last_updated = datetime.now(timezone.utc)

    service.side_effect = apply
    hass = SimpleNamespace(
        states=SimpleNamespace(get=lambda _entity_id: state),
        services=SimpleNamespace(async_call=service),
    )
    actions = tuple(
        ScenarioDeviceAction(
            action_id=action_id,
            title=action_id,
            domain="cover",
            service=ha_service,
            allowed_fields=(frozenset({"value"}) if action_id == "set_position" else frozenset()),
        )
        for action_id, ha_service in (
            ("open_cover", "open_cover"),
            ("close_cover", "close_cover"),
            ("set_position", "set_cover_position"),
        )
    )
    device = ScenarioDeviceEntry(
        target_id=LIVING_CURTAIN_TARGET,
        name="Шторы",
        entity_id="cover.living",
        actions=actions,
    )
    catalog = ScenarioCatalog(devices={LIVING_CURTAIN_TARGET: device}, scenarios={})
    store = MemoryStore()
    clock = [1_000]
    protection = CurtainProtectionCoordinator(
        store,
        catalog_resolver=catalog.device,
        next_sunrise_ms=lambda: 10_000,
        now_ms=lambda: clock[0],
    )
    # The durable image always contains four exact targets, so unresolved test
    # catalog entries are represented explicitly and then remain untouched.
    await protection.async_load()

    async def nested(*_args, **_kwargs):
        return {"status": "completed", "receipts": []}

    executor = ScenarioExecutor(
        hass,
        catalog,
        nested,
        readback_window_seconds=0.02,
        readback_interval_seconds=0.01,
        curtain_protection=protection,
    )
    definition = ScenarioDefinition(
        version=1,
        execution_mode=ScenarioExecutionMode.SINGLE,
        triggers=(ScenarioTrigger("sunset", ScenarioTriggerType.SUNSET),),
        conditions=(),
        actions=(ScenarioAction(
            "close", ScenarioActionType.DEVICE_ACTION,
            target_id=LIVING_CURTAIN_TARGET,
            action_id="set_position",
            value=0,
        ),),
        safety_policy=ScenarioSafetyPolicy(idempotent_actions=True),
    )
    closed = await executor.async_execute(
        definition,
        "automatic-close-run",
        scenario_id="curtain-test",
        trigger_context={"source": "schedule", "trigger_id": "sunset"},
    )
    assert closed["confirmed"] is True

    state.state = "open"
    state.attributes = {"current_position": 100}
    state.last_updated = datetime.now(timezone.utc)
    manual = await executor.async_execute_device_action(
        LIVING_CURTAIN_TARGET,
        "set_position",
        100,
        request_id="manual-no-op",
        idempotent_actions=True,
    )
    assert manual["skipped"] is True
    assert protection.payload["targets"][LIVING_CURTAIN_TARGET]["latchedAtMs"] == 1_000

    state.attributes = {"current_position": 50}
    blocked = await executor.async_execute(
        definition,
        "automatic-close-run-2",
        scenario_id="curtain-test",
        trigger_context={"source": "schedule", "trigger_id": "sunset"},
    )
    assert blocked["receipts"][0]["reason"] == "curtain_manual_open_latched"
    assert service.await_count == 1


@pytest.mark.asyncio
async def test_executor_revalidates_latch_generation_after_dispatch_barrier() -> None:
    revision = datetime.now(timezone.utc)
    states = {
        target: SimpleNamespace(
            state="open",
            attributes={"current_position": 90 if target == KITCHEN_CURTAIN_TARGET else 50},
            last_updated=revision,
        )
        for target in CURTAIN_TARGET_IDS
    }
    service = AsyncMock()
    hass = SimpleNamespace(
        states=SimpleNamespace(get=states.get),
        services=SimpleNamespace(async_call=service),
    )
    actions = tuple(
        ScenarioDeviceAction(
            action_id=action_id,
            title=action_id,
            domain="cover",
            service=ha_service,
            allowed_fields=(
                frozenset({"value"})
                if action_id == "set_position"
                else frozenset()
            ),
        )
        for action_id, ha_service in (
            ("open_cover", "open_cover"),
            ("close_cover", "close_cover"),
            ("set_position", "set_cover_position"),
        )
    )
    devices_by_target = {
        target: ScenarioDeviceEntry(
            target_id=target,
            name=target,
            entity_id=target,
            actions=actions,
        )
        for target in CURTAIN_TARGET_IDS
    }
    catalog = ScenarioCatalog(devices=devices_by_target, scenarios={})
    protection = CurtainProtectionCoordinator(
        MemoryStore(),
        catalog_resolver=catalog.device,
        next_sunrise_ms=lambda: 10_000,
        now_ms=lambda: 1_000,
    )
    await protection.async_load()
    await confirmed_auto_close(
        protection, devices_by_target, target=KITCHEN_CURTAIN_TARGET
    )
    executor = ScenarioExecutor(
        hass,
        catalog,
        AsyncMock(),
        curtain_command_policy=CurtainCommandPolicy.with_confirmed_scales(
            {KITCHEN_CURTAIN_TARGET}
        ),
        curtain_protection=protection,
    )

    async def racing_manual_intent() -> None:
        decision = await protection.async_before_action(
            target_id=KITCHEN_CURTAIN_TARGET,
            entity_id=KITCHEN_CURTAIN_TARGET,
            action_id="set_position",
            requested=100,
            applied=80,
            current_position=90,
            automatic=False,
            dry_run=False,
            receipt_id="racing-manual",
        )
        assert decision.allowed

    receipt = await executor._execute_action(  # noqa: SLF001
        ScenarioAction(
            "automatic-close-via-clamp",
            ScenarioActionType.DEVICE_ACTION,
            target_id=KITCHEN_CURTAIN_TARGET,
            action_id="set_position",
            value=100,
        ),
        "automatic-run",
        frozenset(),
        trigger_context={"source": "schedule", "trigger_id": "sunset"},
        before_dispatch=racing_manual_intent,
    )

    assert receipt["skipped"] is True
    assert receipt["reason"] == "curtain_protection_generation_changed"
    assert receipt["physicalAttempted"] is False
    service.assert_not_awaited()
