"""Explicit owner click accepts stored last states; every other path stays strict.

The tambur button failure (2026-11-01): devices can stay silent for hours
without changing state, but the explicit return demanded a fresh report from
every light, power source and presence/motion sensor. The guarded route now
accepts each member's stored last on/off report (real, consistent,
timestamped, never restored/cached/assumed) while the legacy authorized
route, the timer return and the startup path keep the strict freshness.

Planner binding decision (EXPLICIT-RETURN-QUIET-EVIDENCE continuation): a
stored plain-state reading (e.g. a broker-retained Z2M sensor value) grounds
only the owner's explicit intent-token release. It is not proof of a fresh
physical report, never counts unobserved time as confirmed absence, and
never releases manual protection by itself: without a consumed one-time
intent — and on any replay of a spent token — the strict freshness window
applies unchanged (Z2M round2 retain/cache is the owner-accepted transport
semantics, not a freshness proof).
"""

import inspect
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from custom_components.hausman_hub.application import manual_light_authority
from custom_components.hausman_hub.application.manual_light_authority import ManualLightAuthority
from custom_components.hausman_hub.application.related_light_sources import RelatedLightSources
from custom_components.hausman_hub.domain.room_lighting import ReleaseMode
from custom_components.hausman_hub.domain.room_lighting_engine import LightAction
from custom_components.hausman_hub.domain.room_lighting_ownership import OwnershipSource
from tests.test_room_lighting_force_auto import Store, seed_participants
from tests.test_room_lighting_runtime import (
    _FakeHass, _MemoryOwnershipStore, _NOW_DT, _NOW_MS, _ROOM_ID,
    _SpyExecutor, _config_payload, _make_runtime, _now_ms, POWER_ENTITY,
)

ACTOR = "fixture-admin"
QUIET_MS = 4 * 3600 * 1000


@pytest.fixture(autouse=True)
def _authority_clock(monkeypatch):
    monkeypatch.setattr(manual_light_authority.time, "time", lambda: _NOW_MS / 1000)


def _quiet_dt(age_ms: int = QUIET_MS):
    return datetime.fromtimestamp((_NOW_MS - age_ms) / 1000, timezone.utc)


def _future_dt():
    return datetime.fromtimestamp((_NOW_MS + 60_000) / 1000, timezone.utc)


async def _case(*, action: str = "off", power: str | None = None,
                payload: dict | None = None, wire=None):
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "on", last_changed=_NOW_DT)
    hass.states.set("light.demo_main", "on" if action == "on" else "off",
                    last_changed=_NOW_DT)
    store, ownership_store = Store(), _MemoryOwnershipStore()
    authority = ManualLightAuthority(store)
    await authority.async_load()
    priority, off = await seed_participants(hass)
    executor = _SpyExecutor()
    runtime = _make_runtime(hass, ownership_store=ownership_store,
                            manual_light_authority=authority, light_priority=priority,
                            executor=executor,
                            payload=payload if payload is not None
                            else _config_payload(power_switch_entity=power))
    if wire is not None:
        wire(runtime)
    await runtime.start(hass, "entry")
    runtime._ownership.record_manual(_ROOM_ID, "light_main", _NOW_MS,
                                     turned_off=action == "off")
    await authority.async_capture("light.demo_main", action, "service", "old",
                                  observed_at_ms=_now_ms())
    return SimpleNamespace(hass=hass, runtime=runtime, authority=authority,
                           store=store, ownership_store=ownership_store,
                           priority=priority, off=off, executor=executor)


def _quiet(case, *, light: str = "off", presence: str = "on", power: str | None = None,
           stamp_light: bool = True, attrs: dict | None = None) -> None:
    stamp = _quiet_dt()
    case.hass.states.set("binary_sensor.demo_presence", presence, last_changed=stamp)
    case.hass.states.set("light.demo_main", light,
                         last_changed=stamp if stamp_light else None, attributes=attrs)
    if power is not None:
        case.hass.states.set(POWER_ENTITY, power, last_changed=stamp)


async def _guarded(case, *, actor: str = ACTOR):
    issued = case.runtime.issue_force_auto_intent(_ROOM_ID, actor_id=actor)
    assert issued is not None
    return await case.runtime.async_force_auto_guarded(
        _ROOM_ID, actor_id=actor, intent_token=issued["intentToken"],
        reason="stored-evidence test",
    )


async def test_guarded_click_accepts_quiet_stored_evidence():
    case = await _case(action="off")
    try:
        _quiet(case, light="off", presence="on")
        result = await _guarded(case)
        assert result["outcome"] == "returned", result
        assert case.authority.record("light.demo_main") is None
        assert not case.authority.blocks("light.demo_main")
        config = case.runtime._configs[_ROOM_ID]
        records = case.runtime._control_ownership_for(config)
        assert not any(r.source is OwnershipSource.EXPLICIT_HANDOFF for r in records)
        assert not case.executor.calls
    finally:
        await case.runtime.stop()


async def test_stored_on_handoff_keeps_real_observation_stamp():
    case = await _case(action="on")
    try:
        case.runtime._configs[_ROOM_ID] = replace(
            case.runtime._configs[_ROOM_ID], commands_enabled=True,
        )
        _quiet(case, light="on", presence="on")
        result = await _guarded(case)
        assert result["outcome"] == "returned", result
        config = case.runtime._configs[_ROOM_ID]
        records = case.runtime._control_ownership_for(config)
        assert records[-1].source is OwnershipSource.EXPLICIT_HANDOFF
        assert records[-1].handoff.observed_at == _NOW_MS - QUIET_MS
        assert records[-1].handoff.entity_id == "light.demo_main"
        assert all(c.action is not LightAction.TURN_ON for c in case.executor.calls)
    finally:
        await case.runtime.stop()


async def test_legacy_route_stays_strict_with_quiet_evidence():
    case = await _case(action="off")
    try:
        _quiet(case, light="off", presence="on")
        result = await case.runtime.async_force_auto(_ROOM_ID, reason="legacy route")
        assert result["outcome"] == "blocked", result
        assert result["blockers"] == ["observations_unavailable"], result
        config = case.runtime._configs[_ROOM_ID]
        scope = case.runtime._room_authority_scope(config)
        assert scope
        assert case.runtime._explicit_return_observations(
            config, include_presence=True, scope=scope) is None
        assert case.runtime._explicit_return_observations(
            config, include_presence=True, scope=scope,
            observation_policy="explicit_stored") is not None
        assert case.authority.record("light.demo_main") is not None
    finally:
        await case.runtime.stop()


async def test_timer_return_does_not_accept_stored_evidence():
    from tests.test_timed_manual_return import make_timed_case
    case = await make_timed_case("off")
    try:
        _quiet(case, light="off", presence="on")
        case.runtime._now_ms = lambda: _NOW_MS + 3600_000
        assert not await case.runtime._async_maybe_return_manual(_ROOM_ID)
        assert case.authority.record("light.demo_main") is not None
        assert not case.executor.calls
    finally:
        await case.runtime.stop()


def test_stored_policy_is_opt_in_only():
    from custom_components.hausman_hub.application.room_lighting_runtime import RoomLightingRuntime
    sig = inspect.signature(RoomLightingRuntime._prepare_room_manual_return)
    assert sig.parameters["observation_policy"].default == "strict"
    sig2 = inspect.signature(RoomLightingRuntime._explicit_return_observations)
    assert sig2.parameters["observation_policy"].default == "strict"
    for name in ("_async_maybe_return_manual_scope", "_refresh_manual_return_observations"):
        source = inspect.getsource(getattr(RoomLightingRuntime, name))
        assert "observation_policy" not in source, name
        assert "explicit_stored" not in source, name


@pytest.mark.parametrize("mode", ["unknown", "no_stamp", "future", "restored"])
async def test_quiet_evidence_still_rejects_untrustworthy_states(mode):
    case = await _case(action="off")
    try:
        if mode == "unknown":
            _quiet(case, light="off", presence="unknown")
        elif mode == "no_stamp":
            _quiet(case, light="off", presence="on", stamp_light=False)
        elif mode == "future":
            case.hass.states.set("binary_sensor.demo_presence", "on",
                                 last_changed=_future_dt())
            case.hass.states.set("light.demo_main", "off", last_changed=_future_dt())
        else:
            _quiet(case, light="off", presence="on", attrs={"restored": True})
        result = await _guarded(case)
        assert result["outcome"] == "blocked", result
        assert result["blockers"] == ["observations_unavailable"], result
        assert case.authority.record("light.demo_main") is not None
    finally:
        await case.runtime.stop()


async def test_stored_power_off_against_stored_light_on_is_rejected():
    case = await _case(action="on", power=POWER_ENTITY)
    try:
        _quiet(case, light="on", presence="on", power="off")
        result = await _guarded(case)
        assert result["outcome"] == "blocked", result
        assert result["blockers"] == ["observations_unavailable"], result
    finally:
        await case.runtime.stop()


async def test_stored_power_on_supports_guarded_click():
    case = await _case(action="on", power=POWER_ENTITY)
    try:
        _quiet(case, light="on", presence="on", power="on")
        result = await _guarded(case)
        assert result["outcome"] == "returned", result
    finally:
        await case.runtime.stop()


async def test_missing_power_state_rejected_under_stored_policy():
    case = await _case(action="on", power=POWER_ENTITY)
    try:
        _quiet(case, light="on", presence="on")
        result = await _guarded(case)
        assert result["outcome"] == "blocked", result
    finally:
        await case.runtime.stop()


def test_stored_evidence_rejects_restored_wrapped_observations():
    from custom_components.hausman_hub.application.lighting_observation import (
        ObservedLightState, PhysicalLightObservation,
    )
    from custom_components.hausman_hub.application.power_evidence import stored_target_evidence
    observation = PhysicalLightObservation(
        entity_id="light.demo_main", value="on", observed_at_ms=_NOW_MS - QUIET_MS,
        session="stored-session", sequence=3, ieee="0xabc", endpoint=1,
    )

    def _native(attrs: dict | None = None):
        return SimpleNamespace(state="on", attributes=dict(attrs or {}),
                               entity_id="light.demo_main", last_changed=_quiet_dt())
    ok = ObservedLightState(native=_native(), observation=observation)
    assert stored_target_evidence(ok, now_ms=_NOW_MS) == ("on", _NOW_MS - QUIET_MS)
    for attrs in ({"restored": True}, {"cached": True}, {"assumed_state": True}):
        bad = ObservedLightState(native=_native(attrs), observation=observation)
        assert stored_target_evidence(bad, now_ms=_NOW_MS) == (None, None), attrs


async def test_spent_intent_token_is_not_accepted_again():
    case = await _case(action="off")
    try:
        _quiet(case, light="off", presence="on")
        issued = case.runtime.issue_force_auto_intent(_ROOM_ID, actor_id=ACTOR)
        assert issued is not None
        token = issued["intentToken"]
        first = await case.runtime.async_force_auto_guarded(
            _ROOM_ID, actor_id=ACTOR, intent_token=token, reason="first click")
        assert first["outcome"] == "returned", first
        case.runtime._ownership.record_manual(_ROOM_ID, "light_main", _NOW_MS,
                                              turned_off=True)
        await case.authority.async_capture("light.demo_main", "off", "service",
                                           "again", observed_at_ms=_now_ms())
        replay = await case.runtime.async_force_auto_guarded(
            _ROOM_ID, actor_id=ACTOR, intent_token=token, reason="replayed token")
        assert replay["outcome"] == "blocked", replay
        assert replay["blockers"] == ["return_intent_invalid"], replay
        assert case.authority.record("light.demo_main") is not None
    finally:
        await case.runtime.stop()


def _with_mirror(payload: dict, *, entity: str = "light.demo_mirror") -> dict:
    payload["devices"]["light_targets"].append({
        "id": "light_mirror", "name": "Зеркало", "kind": "light",
        "entityId": entity, "role": "mirror", "groupId": None,
        "brightness": False, "color_temperature": False, "autoAdoptOverride": None,
    })
    return payload


async def _mirror_case(*, quiet: bool, mirror_source: str | None = None):
    """Main light + independent mirror; optional exact dependency for the mirror."""
    from custom_components.hausman_hub.domain.device_power_dependencies import (
        DevicePowerDependency,
    )
    payload = _with_mirror(_config_payload(power_switch_entity=POWER_ENTITY))
    mirror = "light.demo_mirror"

    def wire(runtime):
        # Wire the exact dependency BEFORE start so the authority graph and
        # the state provider see the mirror's own power source.
        if mirror_source is None:
            return
        dependency = DevicePowerDependency(
            dependent_entity_id=mirror, power_source_entity_id=mirror_source,
            policy="auto_turn_on", warmup_seconds=0)
        runtime._power_dependency_resolver = lambda: {mirror: dependency}
        runtime._power_migration_manifest = {"light_mirror": mirror_source}
        runtime._state_provider._power_dependency_resolver = (
            runtime._power_dependency_resolver)
        runtime._state_provider._power_migration_manifest = (
            runtime._power_migration_manifest)

    case = await _case(action="off", power=POWER_ENTITY, payload=payload, wire=wire)
    stamp = _quiet_dt() if quiet else _NOW_DT
    case.hass.states.set("binary_sensor.demo_presence", "on", last_changed=stamp)
    case.hass.states.set("light.demo_main", "off", last_changed=stamp)
    case.hass.states.set(POWER_ENTITY, "off", last_changed=stamp)
    case.hass.states.set(mirror, "on", last_changed=stamp)
    if mirror_source is not None:
        # The graph must know the mirror's own feed after a normal start.
        scope = case.runtime._room_authority_scope(
            case.runtime._configs[_ROOM_ID])
        assert mirror_source in scope
        source_stamp = _NOW_DT  # exact-manifest provenance needs a trusted source
        case.hass.states.set(mirror_source, "on", last_changed=source_stamp)
    return case


@pytest.mark.parametrize("quiet", [True, False])
async def test_independent_mirror_on_is_not_blocked_by_main_switch(quiet):
    case = await _mirror_case(quiet=quiet)
    try:
        result = await _guarded(case)
        assert result["outcome"] == "returned", result
    finally:
        await case.runtime.stop()


async def test_mirror_with_exact_dependency_on_own_source_returns():
    case = await _mirror_case(quiet=True, mirror_source="switch.demo_mirror_power")
    try:
        result = await _guarded(case)
        assert result["outcome"] == "returned", result
    finally:
        await case.runtime.stop()


async def test_mirror_on_with_own_source_off_is_blocked():
    case = await _mirror_case(quiet=True, mirror_source="switch.demo_mirror_power")
    try:
        case.hass.states.set("switch.demo_mirror_power", "off",
                             last_changed=_quiet_dt())
        result = await _guarded(case)
        assert result["outcome"] == "blocked", result
        assert result["blockers"] == ["observations_unavailable"], result
    finally:
        await case.runtime.stop()


async def test_explicit_click_only_accepts_stamp_below_restart_floor():
    case = await _case(action="off")
    try:
        _quiet(case, light="off", presence="on")
        await case.runtime.stop()
        authority = ManualLightAuthority(case.store)
        await authority.async_load()
        scope = frozenset({"light.demo_main"})
        authority.begin_startup_observation()
        authority.set_related_sources(RelatedLightSources(True, {"light.demo_main": scope}))
        authority.register_recovery_scope(scope, 1)
        authority.finish_startup_observation(scope)
        floor = authority.explicit_return_observation_floor(scope)
        assert floor is not None and floor >= _NOW_MS - QUIET_MS
        runtime = _make_runtime(
            case.hass, ownership_store=_MemoryOwnershipStore(),
            manual_light_authority=authority, light_priority=case.priority,
            executor=case.executor, payload=_config_payload())
        await runtime.start(case.hass, "restart-entry")
        try:
            runtime._ownership.record_manual(_ROOM_ID, "light_main", _NOW_MS,
                                             turned_off=True)
            result = await runtime.async_force_auto(_ROOM_ID, reason="strict after restart")
            assert result["outcome"] == "blocked", result
            assert result["blockers"] == ["observations_unavailable"], result
            issued = runtime.issue_force_auto_intent(_ROOM_ID, actor_id=ACTOR)
            assert issued is not None
            guarded = await runtime.async_force_auto_guarded(
                _ROOM_ID, actor_id=ACTOR, intent_token=issued["intentToken"],
                reason="explicit click after restart")
            assert guarded["outcome"] == "returned", guarded
        finally:
            await runtime.stop()
    finally:
        if case.runtime._running:
            await case.runtime.stop()
