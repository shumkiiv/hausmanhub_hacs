"""Regression coverage for the manual block after durable target pending write."""

from __future__ import annotations

import copy
import asyncio

import pytest

from custom_components.hausman_hub.application.manual_light_authority import (
    ManualLightAuthority,
)
from custom_components.hausman_hub.application.room_lighting_runtime import (
    RoomLightingRuntime,
)
from custom_components.hausman_hub.application.room_lighting_shadow import (
    RoomLightingShadowService,
)
from custom_components.hausman_hub.domain.room_lighting import config_from_payload
from custom_components.hausman_hub.domain.room_lighting_engine import (
    LightAction,
    PlannedCommand,
    RoomLightingDecision,
    TargetDecision,
)
from test_room_lighting_runtime import (
    AWAY_ENTITY_ID,
    _ConfigService,
    _FakeHass,
    _MemoryOwnershipStore,
    _MemoryShadowStore,
    _NOW_DT,
    _NOW_MS,
    _ROOM_ID,
    _SpyExecutor,
    _config_payload,
)


def _decision(action: LightAction) -> RoomLightingDecision:
    command = PlannedCommand("light_main", action)
    return RoomLightingDecision(
        room_id=_ROOM_ID,
        evaluated_at=_NOW_MS,
        mode="live",
        targets=(TargetDecision("light_main", "off", None, None, (command,)),),
        commands_enabled=True,
    )


def _payload(*, peer: bool) -> dict[str, object]:
    payload = copy.deepcopy(_config_payload(commands_enabled=True))
    if peer:
        payload["devices"]["light_targets"].append({  # type: ignore[index]
            "id": "light_peer",
            "name": "Зеркало",
            "kind": "light",
            "entityId": "light.demo_peer",
            "role": "accent",
            "groupId": None,
            "brightness": True,
            "color_temperature": True,
            "autoAdoptOverride": None,
        })
    return payload


class _ClosureGateStore(_MemoryShadowStore):
    """Pause the second durable save, which is the honest closure receipt."""

    def __init__(self) -> None:
        super().__init__()
        self.saves = 0
        self.closure_entered = asyncio.Event()
        self.release_closure = asyncio.Event()

    async def async_save(self, payload: dict[str, object]) -> None:
        self.saves += 1
        if self.saves == 2:
            self.closure_entered.set()
            await self.release_closure.wait()
        self.payload = payload


async def _runtime(
    *,
    manual: bool = True,
    manual_entity: str = "light.demo_main",
    peer: bool = False,
    confirmed: bool = True,
    shadow_store: object | None = None,
    authority_store: object | None = None,
) -> tuple[RoomLightingRuntime, _SpyExecutor, ManualLightAuthority, object]:
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_NOW_DT)
    hass.states.set("light.demo_main", "on", last_changed=_NOW_DT)
    if peer:
        hass.states.set("light.demo_peer", "off", last_changed=_NOW_DT)
    hass.states.set(AWAY_ENTITY_ID, "off", last_changed=_NOW_DT)
    config = config_from_payload(_payload(peer=peer))
    executor = _SpyExecutor(confirmed=confirmed)
    store = shadow_store or _MemoryShadowStore()
    authority = ManualLightAuthority(authority_store)
    if manual:
        authority.capture(manual_entity, "off", "service", "mirror-manual")
        if authority_store is not None:
            await authority.async_drain()
    runtime = RoomLightingRuntime(
        hass,
        _ConfigService(config),
        RoomLightingShadowService(store),
        executor=executor,
        ownership_store=_MemoryOwnershipStore(),
        now_ms=lambda: _NOW_MS,
        track_state_changes=lambda _hass, _entities, _callback: (lambda: None),
        track_interval=lambda _hass, _callback, _interval: (lambda: None),
        listen_bus=lambda _hass, _event, _callback: (lambda: None),
        away_entity_id=AWAY_ENTITY_ID,
        manual_light_authority=authority,
    )
    runtime._running = True  # exercise the live dispatch seam without start-up work
    runtime._configs = {config.room_id: config}
    runtime._ownership.record_auto(_ROOM_ID, "light_main", _NOW_MS, confirmed=True)
    return runtime, executor, authority, store


async def _dispatch(runtime: RoomLightingRuntime, action: LightAction = LightAction.TURN_OFF):
    config = runtime._configs[_ROOM_ID]
    return await runtime._dispatch(
        config, _decision(action), _NOW_MS, operation_id="mirror-unsent", config_version=1
    )


def _failures(runtime: RoomLightingRuntime) -> list[dict[str, object]]:
    return runtime._shadow.journal_payload().get("dispatchFailures", [])  # type: ignore[attr-defined]


async def test_manual_post_pending_closure_removes_real_shadow_pending_and_never_sends() -> None:
    runtime, executor, _authority, _store = await _runtime()

    receipts = await _dispatch(runtime)

    assert executor.calls == []
    assert receipts[0]["reason"] == "manual_light_priority"
    assert receipts[0]["sent"] is False
    assert receipts[0]["dispatchAttempt"] is False
    assert _failures(runtime) == []
    assert (_ROOM_ID, "light_main") not in runtime._dispatch_failures


async def test_peer_manual_post_pending_closure_has_complete_honest_receipt_and_restores_markers() -> None:
    runtime, executor, authority, _store = await _runtime(
        peer=True, manual_entity="light.demo_peer"
    )
    runtime._executing_actions["light.demo_main"] = "previous_action"
    runtime._command_grace["light.demo_main"] = ("previous_action", _NOW_MS - 1)

    receipts = await _dispatch(runtime)

    receipt = receipts[0]
    assert authority.blocks("light.demo_peer")
    assert executor.calls == []
    assert receipt["reason"] == "manual_light_priority"
    assert receipt["sent"] is False
    assert receipt["sendOutcome"] == "not_sent"
    assert receipt["dispatchAttempt"] is False
    assert receipt["ownershipChanged"] == "none"
    assert isinstance(receipt["evidenceRevision"], str)
    assert receipt["evidenceRevision"] == receipt["currentEvidenceRevision"]
    assert runtime._executing_actions["light.demo_main"] == "previous_action"
    assert runtime._command_grace["light.demo_main"] == (
        "previous_action", _NOW_MS - 1
    )
    assert _failures(runtime) == []


async def test_manual_post_pending_repeat_stays_manual_not_unconfirmed_attempt() -> None:
    runtime, executor, _authority, _store = await _runtime()

    first = await _dispatch(runtime)
    second = await _dispatch(runtime)

    assert [receipt["reason"] for receipt in (*first, *second)] == [
        "manual_light_priority", "manual_light_priority"
    ]
    assert executor.calls == []
    assert _failures(runtime) == []


async def test_manual_post_pending_restart_has_no_false_latch_and_keeps_manual_authority() -> None:
    shadow_store = _MemoryShadowStore()
    authority_store = _MemoryOwnershipStore()
    authority_store.recovered_previous = False
    runtime, executor, authority, _ = await _runtime(
        shadow_store=shadow_store, authority_store=authority_store
    )

    assert (await _dispatch(runtime))[0]["reason"] == "manual_light_priority"
    shadow2 = RoomLightingShadowService(shadow_store)
    await shadow2.async_load()
    restored_authority = ManualLightAuthority(authority_store)
    await restored_authority.async_load()
    restored, restored_executor, _unused, _ = await _runtime(
        shadow_store=shadow_store, authority_store=authority_store
    )
    restored._shadow = shadow2
    restored._manual_light_authority = restored_authority
    restored._restore_dispatch_failures()

    assert executor.calls == []
    assert authority.blocks("light.demo_main")
    assert restored_authority.blocks("light.demo_main")
    assert restored._dispatch_failures == {}
    assert (await _dispatch(restored))[0]["reason"] == "manual_light_priority"
    assert restored_executor.calls == []


async def test_manual_post_pending_durable_closure_failure_keeps_pending_and_no_receipt() -> None:
    runtime, executor, _authority, _store = await _runtime()
    original = runtime._shadow.async_record_dispatch  # type: ignore[attr-defined]

    async def reject_closure(**_kwargs: object) -> None:
        raise RuntimeError("durable closure unavailable")

    runtime._shadow.async_record_dispatch = reject_closure  # type: ignore[attr-defined]
    receipts = await _dispatch(runtime)
    runtime._shadow.async_record_dispatch = original  # type: ignore[attr-defined]

    assert receipts == []
    assert executor.calls == []
    assert (_ROOM_ID, "light_main") in runtime._dispatch_failures
    assert len(_failures(runtime)) == 1


@pytest.mark.parametrize("anchor", [_NOW_MS - 1, None])
async def test_manual_post_pending_superseded_off_preserves_previous_failure_anchor(anchor: int | None) -> None:
    shadow_store = _MemoryShadowStore()
    runtime, executor, _authority, _store = await _runtime(shadow_store=shadow_store)
    runtime._dispatch_failures[(_ROOM_ID, "light_main")] = ("turn_on", "old-revision", anchor)

    receipts = await _dispatch(runtime)

    assert receipts[0]["reason"] == "manual_light_priority"
    assert runtime._dispatch_failures[(_ROOM_ID, "light_main")] == (
        "turn_on", "old-revision", anchor
    )
    failure = _failures(runtime)[0]
    assert failure["action"] == "turn_on"
    assert failure["evidenceRevision"] == "old-revision"
    if anchor is None:
        assert "blockedAfterMs" not in failure
    else:
        assert failure["blockedAfterMs"] == anchor
    assert executor.calls == []
    shadow2 = RoomLightingShadowService(shadow_store)
    await shadow2.async_load()
    restored, restored_executor, _restored_authority, _ = await _runtime(
        shadow_store=shadow_store
    )
    restored._shadow = shadow2
    restored._restore_dispatch_failures()
    assert restored._dispatch_failures[(_ROOM_ID, "light_main")] == (
        "turn_on", "old-revision", anchor
    )
    assert restored_executor.calls == []


async def test_manual_action_arriving_during_pending_write_blocks_the_send() -> None:
    runtime, executor, authority, _store = await _runtime(manual=False)
    original = runtime._persist_pending_target_dispatch

    async def save_then_manual(**kwargs: object) -> bool:
        saved = await original(**kwargs)
        authority.capture("light.demo_main", "off", "service", "manual-race")
        return saved

    runtime._persist_pending_target_dispatch = save_then_manual  # type: ignore[method-assign]
    receipts = await _dispatch(runtime)

    assert receipts[0]["reason"] == "manual_light_priority"
    assert executor.calls == []
    assert _failures(runtime) == []


async def test_peer_manual_action_arriving_while_real_closure_is_pending_survives() -> None:
    store = _ClosureGateStore()
    runtime, executor, authority, _ = await _runtime(
        manual=False, peer=True, shadow_store=store
    )
    original = runtime._persist_pending_target_dispatch

    async def save_then_first_peer_manual(**kwargs: object) -> bool:
        saved = await original(**kwargs)
        authority.capture("light.demo_peer", "off", "service", "manual-before-closure")
        return saved

    runtime._persist_pending_target_dispatch = save_then_first_peer_manual  # type: ignore[method-assign]
    dispatch = asyncio.create_task(_dispatch(runtime))
    await store.closure_entered.wait()
    authority.capture("light.demo_peer", "on", "service", "manual-during-closure")
    store.release_closure.set()
    receipts = await dispatch

    assert store.saves == 2
    assert receipts[0]["reason"] == "manual_light_priority"
    assert executor.calls == []
    assert authority.blocks("light.demo_peer")
    assert _failures(runtime) == []


async def test_true_unconfirmed_send_keeps_the_live_failure_latch() -> None:
    runtime, executor, _authority, _store = await _runtime(manual=False, confirmed=False)

    receipts = await _dispatch(runtime)

    assert len(executor.calls) == 1
    assert receipts[0]["reason"] == "unconfirmed"
    assert receipts[0]["dispatchAttempt"] is True
    assert (_ROOM_ID, "light_main") in runtime._dispatch_failures
    assert len(_failures(runtime)) == 1
