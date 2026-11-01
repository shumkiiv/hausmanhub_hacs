"""Real room runtime, synthetic HA: explicit per-target manual-on absence off."""
from dataclasses import replace
from datetime import datetime, timezone
import pytest

from custom_components.hausman_hub.domain.room_lighting import Timers
from custom_components.hausman_hub.domain.room_lighting_engine import evaluate_room_lighting
from tests.test_room_lighting_force_auto import make_case
from tests.test_room_lighting_runtime import _NOW_MS, _ROOM_ID


async def ready_case(monkeypatch):
    import time
    clock = [_NOW_MS]
    monkeypatch.setattr(time, "time", lambda: clock[0] / 1000)
    case = await make_case("on")
    cfg = case.runtime._configs[_ROOM_ID]
    target = replace(cfg.devices.light_targets[0], auto_off_manual_on_absence=True)
    cfg = replace(cfg, devices=replace(cfg.devices, light_targets=(target,)),
                  commands_enabled=True, timers=Timers(absence_seconds=300))
    case.runtime._configs[_ROOM_ID] = cfg
    now = _NOW_MS + 301_000
    clock[0] = now
    case.runtime._now_ms = lambda: now
    case.hass.states.set("binary_sensor.demo_presence", "off",
                         last_changed=datetime.fromtimestamp((_NOW_MS + 1000) / 1000, timezone.utc))
    case.hass.states.get("binary_sensor.demo_presence").last_updated = datetime.fromtimestamp(now / 1000, timezone.utc)
    case.hass.states.get("light.demo_main").last_updated = datetime.fromtimestamp(now / 1000, timezone.utc)
    return case, cfg, now


@pytest.mark.parametrize("state,age", [("unavailable", 0), ("unknown", 0), ("off", 301_000)])
async def test_manual_off_return_stays_blocked_if_sensor_lost_after_absence(state, age):
    case = await make_case("off")
    try:
        cfg = case.runtime._configs[_ROOM_ID]
        now = _NOW_MS + 601_000
        case.runtime._now_ms = lambda: now
        case.runtime._absence_release_latch.add(_ROOM_ID)
        case.runtime._absence.pop(_ROOM_ID, None)
        case.hass.states.set("binary_sensor.demo_presence", state,
                             last_changed=datetime.fromtimestamp((now - age) / 1000, timezone.utc))
        assert case.runtime._auto_on_blocked(cfg, now)
    finally:
        await case.runtime.stop()


async def test_manual_on_target_is_switched_off_after_fresh_stable_absence(monkeypatch):
    case, cfg, now = await ready_case(monkeypatch)
    try:
        context = await case.runtime._build_context(cfg, now)
        assert [command.action.value for command in evaluate_room_lighting(cfg, context).commands] == ["turn_off"]
        await case.runtime.async_process([_ROOM_ID])
        assert [command.action.value for command in case.executor.calls] == ["turn_off"], case.runtime._shadow.journal_payload()["entries"][-1]["dispatch"][0]["reason"]
    finally:
        await case.runtime.stop()


async def test_manual_on_absence_off_does_not_depend_on_lux_sensor(monkeypatch):
    from custom_components.hausman_hub.domain.room_lighting import Illumination

    case, cfg, now = await ready_case(monkeypatch)
    try:
        cfg = replace(cfg, illumination=Illumination(sensor="sensor.demo_lux"))
        case.runtime._configs[_ROOM_ID] = cfg
        context = await case.runtime._build_context(cfg, now)
        assert [command.action.value for command in evaluate_room_lighting(cfg, context).commands] == ["turn_off"]
        await case.runtime.async_process([_ROOM_ID])
        assert [command.action.value for command in case.executor.calls] == ["turn_off"]
    finally:
        await case.runtime.stop()


async def test_manual_on_absence_off_reaches_real_ha_executor(monkeypatch):
    from types import SimpleNamespace
    from custom_components.hausman_hub.application import room_lighting_ha_executor as module
    from custom_components.hausman_hub.application.room_lighting_ha_executor import RoomLightingHaExecutor

    case, cfg, now = await ready_case(monkeypatch)
    try:
        monkeypatch.setattr(module, "_new_service_context", lambda: SimpleNamespace(id="own-command"))
        executor = RoomLightingHaExecutor(manual_light_authority=case.authority,
            light_readback_window_seconds=0, now_ms=lambda: now)
        executor.update_targets({}, room_targets={_ROOM_ID: {"light_main": cfg.devices.light_targets[0]}})
        case.runtime._executor = executor
        case.hass._command_report_clock = lambda: now
        await case.runtime.async_process([_ROOM_ID])
        assert [call[1] for call in case.hass.services.calls] == ["turn_off"]
        assert case.hass.states.get("light.demo_main").state == "off"
        assert case.authority.record("light.demo_main") is None
    finally:
        await case.runtime.stop()


@pytest.mark.parametrize("change", ["presence", "manual"])
async def test_real_executor_rechecks_safety_at_physical_boundary(monkeypatch, change):
    from types import SimpleNamespace
    from custom_components.hausman_hub.application import room_lighting_ha_executor as module
    from custom_components.hausman_hub.application.room_lighting_ha_executor import RoomLightingHaExecutor

    case, cfg, now = await ready_case(monkeypatch)
    try:
        monkeypatch.setattr(module, "_new_service_context", lambda: SimpleNamespace(id="own-command"))
        executor = RoomLightingHaExecutor(manual_light_authority=case.authority,
            light_readback_window_seconds=0, now_ms=lambda: now)
        executor.update_targets({}, room_targets={_ROOM_ID: {"light_main": cfg.devices.light_targets[0]}})
        original_call = executor._call

        async def presence_before_call(*args, **kwargs):
            if change == "presence":
                case.hass.states.set("binary_sensor.demo_presence", "on",
                                     last_changed=datetime.fromtimestamp(now / 1000, timezone.utc))
            else:
                case.authority.capture("light.demo_main", "on", "service", "new", observed_at_ms=now)
                case.runtime._ownership.record_manual(_ROOM_ID, "light_main", now)
            return await original_call(*args, **kwargs)

        monkeypatch.setattr(executor, "_call", presence_before_call)
        case.runtime._executor = executor
        await case.runtime.async_process([_ROOM_ID])
        assert not case.hass.services.calls
        assert case.authority.record("light.demo_main") is not None
    finally:
        await case.runtime.stop()


async def test_stale_manual_target_is_refreshed_before_absence_off(monkeypatch):
    case, cfg, now = await ready_case(monkeypatch)
    try:
        case.hass.states.get("light.demo_main").last_updated = datetime.fromtimestamp(_NOW_MS / 1000, timezone.utc)

        class Refresher:
            calls = 0

            async def refresh_confirmation(self, entity, is_current):
                assert is_current()
                self.calls += 1
                case.hass.states.get(entity).last_updated = datetime.fromtimestamp(now / 1000, timezone.utc)
                return is_current()

        refresher = Refresher()
        case.runtime._observation_refresher = refresher
        await case.runtime.async_process([_ROOM_ID])
        assert refresher.calls == 1
        assert [command.action.value for command in case.executor.calls] == ["turn_off"]
    finally:
        await case.runtime.stop()


@pytest.mark.parametrize("sensor", ["stale", "on", "unknown"])
async def test_target_refresh_never_invents_absence(monkeypatch, sensor):
    case, cfg, now = await ready_case(monkeypatch)
    try:
        case.hass.states.get("light.demo_main").last_updated = datetime.fromtimestamp(_NOW_MS / 1000, timezone.utc)
        if sensor == "stale":
            case.hass.states.get("binary_sensor.demo_presence").last_updated = datetime.fromtimestamp(_NOW_MS / 1000, timezone.utc)
        else:
            case.hass.states.set("binary_sensor.demo_presence", sensor,
                                 last_changed=datetime.fromtimestamp(now / 1000, timezone.utc))

        class Refresher:
            calls = 0

            async def refresh_confirmation(self, entity, is_current):
                self.calls += 1
                return True

        refresher = Refresher()
        case.runtime._observation_refresher = refresher
        await case.runtime.async_process([_ROOM_ID])
        assert refresher.calls == 0
        assert not case.executor.calls
    finally:
        await case.runtime.stop()


async def test_failed_target_refresh_is_not_retried_on_each_tick(monkeypatch):
    case, cfg, now = await ready_case(monkeypatch)
    try:
        case.hass.states.get("light.demo_main").last_updated = datetime.fromtimestamp(_NOW_MS / 1000, timezone.utc)

        class Refresher:
            calls = 0

            async def refresh_confirmation(self, entity, is_current):
                self.calls += 1
                return False

        refresher = Refresher()
        case.runtime._observation_refresher = refresher
        await case.runtime.async_process([_ROOM_ID])
        await case.runtime.async_process([_ROOM_ID])
        assert refresher.calls == 1
        assert not case.executor.calls
    finally:
        await case.runtime.stop()


async def test_presence_during_target_refresh_still_blocks_off(monkeypatch):
    case, cfg, now = await ready_case(monkeypatch)
    try:
        case.hass.states.get("light.demo_main").last_updated = datetime.fromtimestamp(_NOW_MS / 1000, timezone.utc)

        class Refresher:
            async def refresh_confirmation(self, entity, is_current):
                assert is_current()
                case.hass.states.get(entity).last_updated = datetime.fromtimestamp(now / 1000, timezone.utc)
                case.hass.states.set("binary_sensor.demo_presence", "on",
                                     last_changed=datetime.fromtimestamp(now / 1000, timezone.utc))
                return True

        case.runtime._observation_refresher = Refresher()
        await case.runtime.async_process([_ROOM_ID])
        assert not case.executor.calls
        assert case.authority.record("light.demo_main") is not None
    finally:
        await case.runtime.stop()


@pytest.mark.parametrize("change", ["presence", "manual", "configuration", "storage"])
async def test_manual_on_absence_off_is_cancelled_if_evidence_changes_during_save(monkeypatch, change):
    case, cfg, now = await ready_case(monkeypatch)
    try:
        original = case.runtime._persist_pending_target_dispatch

        async def persist_with_intervention(**kwargs):
            result = await original(**kwargs)
            if change == "presence":
                case.hass.states.set("binary_sensor.demo_presence", "on",
                                     last_changed=datetime.fromtimestamp(now / 1000, timezone.utc))
            elif change == "manual":
                case.authority.capture("light.demo_main", "on", "service", "new", observed_at_ms=now)
                case.runtime._ownership.record_manual(_ROOM_ID, "light_main", now)
            elif change == "configuration":
                case.runtime._configs[_ROOM_ID] = replace(cfg, version=cfg.version + 1)
            else:
                case.authority.mark_unhealthy()
            return result

        monkeypatch.setattr(case.runtime, "_persist_pending_target_dispatch", persist_with_intervention)
        await case.runtime.async_process([_ROOM_ID])
        assert not case.executor.calls
    finally:
        await case.runtime.stop()


async def test_selective_durable_return_preserves_other_manual_lights():
    from custom_components.hausman_hub.application.manual_light_authority import ManualLightAuthority
    from custom_components.hausman_hub.application.related_light_sources import RelatedLightSources
    from tests.test_room_lighting_force_auto import Store

    authority = ManualLightAuthority(Store())
    await authority.async_load()
    scope = frozenset({"light.one", "light.two"})
    authority.set_related_sources(RelatedLightSources(True, {name: scope for name in scope}))
    await authority.async_capture("light.one", "on", "service", "one", observed_at_ms=_NOW_MS)
    await authority.async_capture("light.two", "off", "service", "two", observed_at_ms=_NOW_MS)
    other = authority.record("light.two")
    token = await authority.async_begin_explicit_return(scope, all_manual=True,
        observations_current=lambda: True, release_entity_ids=frozenset({"light.one"}))
    assert token is not None
    assert not await authority.async_recover_selective_return(scope)
    assert await authority.async_finish_explicit_return(token, persisted=True,
                                                        observations_current=lambda: True)
    assert authority.record("light.one") is None
    assert authority.record("light.two") == other


async def test_confirmed_absence_off_rearms_only_selected_target(monkeypatch):
    case, cfg, now = await ready_case(monkeypatch)
    try:
        case.priority._manual_records["light.demo_main"] = {
            "entityId": "light.demo_main", "targetId": "light_main", "ownership": "manual",
            "evidenceRevision": None, "confirmedAt": _NOW_MS,
        }

        async def physical_off(hass, command, **kwargs):
            case.executor.calls.append(command)
            state = "on" if command.action.value == "turn_on" else "off"
            hass.states.set("light.demo_main", state, last_changed=datetime.fromtimestamp(now / 1000, timezone.utc))
            return {"confirmed": True, "state_after": {"state": state}}

        monkeypatch.setattr(case.executor, "execute", physical_off)
        await case.runtime.async_process([_ROOM_ID])
        assert case.hass.states.get("light.demo_main").state == "off"
        assert case.authority.record("light.demo_main") is None
        assert not case.priority.manual_claim_entity_ids(frozenset({"light.demo_main"}))
        assert not any(record.source.value == "manual" for record in case.runtime._control_ownership_for(cfg))
        case.hass.states.set("binary_sensor.demo_presence", "on",
                             last_changed=datetime.fromtimestamp(now / 1000, timezone.utc))
        await case.runtime.async_process([_ROOM_ID])
        assert [command.action.value for command in case.executor.calls] == ["turn_off", "turn_on"]
    finally:
        await case.runtime.stop()


async def test_full_room_return_preserves_other_target_manual_choice(monkeypatch):
    from custom_components.hausman_hub.application.related_light_sources import RelatedLightSources

    case, cfg, now = await ready_case(monkeypatch)
    try:
        selected = cfg.devices.light_targets[0]
        other = replace(selected, id="other", entity_id="light.other", auto_off_manual_on_absence=False)
        cfg = replace(cfg, devices=replace(cfg.devices, light_targets=(selected, other)))
        case.runtime._configs[_ROOM_ID] = cfg
        case.authority.set_related_sources(RelatedLightSources.from_configs((cfg,)))
        case.hass.states.set("light.other", "off", last_changed=datetime.fromtimestamp(now / 1000, timezone.utc))
        await case.authority.async_capture("light.other", "off", "service", "other", observed_at_ms=now)
        other_record = case.authority.record("light.other")

        async def physical_off(hass, command, **kwargs):
            case.executor.calls.append(command)
            hass.states.set("light.demo_main", "off", last_changed=datetime.fromtimestamp(now / 1000, timezone.utc))
            return {"confirmed": True, "state_after": {"state": "off"}}

        monkeypatch.setattr(case.executor, "execute", physical_off)
        await case.runtime.async_process([_ROOM_ID])
        assert [command.target_id for command in case.executor.calls] == ["light_main"]
        assert case.authority.record("light.demo_main") is None
        assert case.authority.record("light.other") == other_record
    finally:
        await case.runtime.stop()


async def test_new_manual_action_during_selective_return_preserves_it_without_stuck_fence(monkeypatch):
    case, cfg, now = await ready_case(monkeypatch)
    try:
        async def physical_off(hass, command, **kwargs):
            case.executor.calls.append(command)
            hass.states.set("light.demo_main", "off", last_changed=datetime.fromtimestamp(now / 1000, timezone.utc))
            return {"confirmed": True, "state_after": {"state": "off"}}

        async def intervene(_payload):
            case.store.prepare_hook = None
            case.authority.capture("light.demo_main", "on", "service", "later", observed_at_ms=now)
            case.runtime._ownership.record_manual(_ROOM_ID, "light_main", now)

        monkeypatch.setattr(case.executor, "execute", physical_off)
        case.store.prepare_hook = intervene
        await case.runtime.async_process([_ROOM_ID])
        await case.authority.async_drain()
        assert case.authority.record("light.demo_main").fingerprint == "later"
        assert case.authority.barrier_reason_for("light.demo_main") is None
        assert case.authority.blocks("light.demo_main")
    finally:
        case.store.prepare_hook = None
        await case.runtime.stop()


async def test_restart_cancels_interrupted_selective_fence_without_erasing_manual_record():
    from custom_components.hausman_hub.application.manual_light_authority import ManualLightAuthority
    from custom_components.hausman_hub.application.related_light_sources import RelatedLightSources
    from tests.test_room_lighting_force_auto import Store

    store = Store()
    original = ManualLightAuthority(store)
    await original.async_load()
    scope = frozenset({"light.one"})
    graph = RelatedLightSources(True, {"light.one": scope})
    original.set_related_sources(graph)
    await original.async_capture("light.one", "on", "service", "one", observed_at_ms=_NOW_MS)
    token = await original.async_begin_explicit_return(scope, all_manual=True,
        observations_current=lambda: True, release_entity_ids=scope)
    assert token is not None

    restored = ManualLightAuthority(store)
    await restored.async_load()
    restored.begin_startup_observation()
    restored.set_related_sources(graph)
    restored.register_recovery_scope(scope, 1)
    restored.finish_startup_observation(scope)
    assert restored.barrier_reason_for("light.one") == "return_pending"
    assert await restored.async_recover_selective_return(scope)
    assert restored.record("light.one").fingerprint == "one"
    assert restored.barrier_reason_for("light.one") == "restart_uncertain"


async def test_runtime_recovers_interrupted_selective_fence_on_startup():
    from custom_components.hausman_hub.application.manual_light_authority import ManualLightAuthority
    from custom_components.hausman_hub.application.related_light_sources import RelatedLightSources

    case = await make_case("on")
    try:
        scope = frozenset({"light.demo_main"})
        token = await case.authority.async_begin_explicit_return(scope, all_manual=True,
            observations_current=lambda: True, release_entity_ids=scope)
        assert token is not None
        restored = ManualLightAuthority(case.store)
        await restored.async_load()
        restored.begin_startup_observation()
        restored.set_related_sources(RelatedLightSources(True, {"light.demo_main": scope}))
        restored.register_recovery_scope(scope, 1)
        restored.finish_startup_observation(scope)
        case.runtime._manual_light_authority = restored
        await case.runtime._reconcile_manual_restart(case.runtime._configs[_ROOM_ID])
        assert restored.barrier_reason_for("light.demo_main") == "restart_uncertain"
        assert restored.record("light.demo_main") is not None
    finally:
        await case.runtime.stop()


async def test_absence_off_with_stale_command_report_stays_unconfirmed(monkeypatch):
    """A report older than the dispatch never confirms the absence OFF."""
    from types import SimpleNamespace
    from custom_components.hausman_hub.application import room_lighting_ha_executor as module
    from custom_components.hausman_hub.application.room_lighting_ha_executor import (
        RoomLightingHaExecutor,
    )

    case, cfg, now = await ready_case(monkeypatch)
    try:
        monkeypatch.setattr(module, "_new_service_context",
                            lambda: SimpleNamespace(id="own-command"))
        executor = RoomLightingHaExecutor(manual_light_authority=case.authority,
            light_readback_window_seconds=0, now_ms=lambda: now)
        executor.update_targets({}, room_targets={_ROOM_ID: {"light_main": cfg.devices.light_targets[0]}})
        case.runtime._executor = executor
        case.hass._command_report_clock = lambda: now - 60_000
        await case.runtime.async_process([_ROOM_ID])
        assert [call[1] for call in case.hass.services.calls] == ["turn_off"]
        entry = case.runtime._shadow.journal_payload()["entries"][-1]
        receipt = entry["dispatch"][0]
        assert receipt["confirmed"] is False
        assert receipt["sendOutcome"] == "sent"
        assert receipt["reason"] == "unconfirmed"
        assert case.authority.record("light.demo_main") is not None
    finally:
        await case.runtime.stop()


async def test_absence_off_with_foreign_context_stays_unconfirmed(monkeypatch):
    """A state attributed to another actor never completes the manual record."""
    from types import SimpleNamespace
    from custom_components.hausman_hub.application import room_lighting_ha_executor as module
    from custom_components.hausman_hub.application.room_lighting_ha_executor import (
        RoomLightingHaExecutor,
    )

    case, cfg, now = await ready_case(monkeypatch)
    try:
        monkeypatch.setattr(module, "_new_service_context",
                            lambda: SimpleNamespace(id="own-command"))
        executor = RoomLightingHaExecutor(manual_light_authority=case.authority,
            light_readback_window_seconds=0, now_ms=lambda: now)
        executor.update_targets({}, room_targets={_ROOM_ID: {"light_main": cfg.devices.light_targets[0]}})
        case.runtime._executor = executor
        case.hass._command_report_clock = lambda: now

        original_async_call = case.hass.services.async_call

        async def foreign_async_call(domain, service, data, blocking=True, context=None):
            foreign = SimpleNamespace(id="manual-guest")
            await original_async_call(domain, service, data,
                                      blocking=blocking, context=foreign)

        monkeypatch.setattr(case.hass.services, "async_call", foreign_async_call)
        await case.runtime.async_process([_ROOM_ID])
        assert [call[1] for call in case.hass.services.calls] == ["turn_off"]
        entry = case.runtime._shadow.journal_payload()["entries"][-1]
        receipt = entry["dispatch"][0]
        assert receipt["confirmed"] is False
        assert receipt["reason"] == "unconfirmed"
        assert case.authority.record("light.demo_main") is not None
    finally:
        await case.runtime.stop()


async def test_absence_off_aging_sensor_during_readback_stays_unconfirmed(monkeypatch):
    """A stale-by-age absence report must not confirm the OFF during readback."""
    import time
    from types import SimpleNamespace
    from custom_components.hausman_hub.application import room_lighting_ha_executor as module
    from custom_components.hausman_hub.application.room_lighting_ha_executor import (
        RoomLightingHaExecutor,
    )
    from custom_components.hausman_hub.domain.room_lighting_engine import (
        EnginePolicy,
        sensor_observation_is_fresh,
    )

    case, cfg, t0 = await ready_case(monkeypatch)
    try:
        clock = [t0]
        monkeypatch.setattr(time, "time", lambda: clock[0] / 1000)
        case.runtime._now_ms = lambda: clock[0]

        freshness_ms = EnginePolicy.from_config(cfg).sensor_freshness_seconds * 1000
        observed_ms = t0 - freshness_ms + 1000  # 299s old at dispatch time.
        sensor_state = case.hass.states.get("binary_sensor.demo_presence")
        assert sensor_state.last_changed is not None
        assert sensor_state.last_changed.timestamp() * 1000 <= observed_ms
        sensor_state.last_updated = datetime.fromtimestamp(observed_ms / 1000, timezone.utc)
        snapshot_now = case.runtime._state_provider._sensor_snapshot(
            case.hass, cfg.devices.sensors[0], t0)
        assert sensor_observation_is_fresh(
            snapshot_now, t0, freshness_ms) is True

        monkeypatch.setattr(module, "_new_service_context",
                            lambda: SimpleNamespace(id="own-command"))
        monotonic = lambda: clock[0] / 1000
        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)
            # First wait: age the sensor past freshness without any new report.
            clock[0] += 2000
            case.hass.states.set(
                "light.demo_main", "off", {},
                last_changed=datetime.fromtimestamp(clock[0] / 1000, timezone.utc),
                context=captured["context"])

        captured = {}
        original_async_call = case.hass.services.async_call

        async def deferred_async_call(domain, service, data, blocking=True, context=None):
            case.hass.services.calls.append((domain, service, dict(data)))
            captured["context"] = context  # Publish the OFF only after the wait.

        monkeypatch.setattr(case.hass.services, "async_call", deferred_async_call)

        executor = RoomLightingHaExecutor(manual_light_authority=case.authority,
            light_readback_window_seconds=5, light_readback_interval_seconds=2,
            now_ms=lambda: clock[0], monotonic=monotonic, sleep=fake_sleep)
        executor.update_targets({}, room_targets={_ROOM_ID: {"light_main": cfg.devices.light_targets[0]}})
        case.runtime._executor = executor
        case.hass._command_report_clock = lambda: clock[0]

        await case.runtime.async_process([_ROOM_ID])
        assert [call[1] for call in case.hass.services.calls] == ["turn_off"]
        assert sleeps, "confirmation wait never ran"
        assert case.hass.states.get("light.demo_main").state == "off"
        assert case.hass.states.get(
            "binary_sensor.demo_presence").last_updated == datetime.fromtimestamp(
                observed_ms / 1000, timezone.utc)
        snapshot_after = case.runtime._state_provider._sensor_snapshot(
            case.hass, cfg.devices.sensors[0], clock[0])
        assert snapshot_after == snapshot_now
        assert sensor_observation_is_fresh(
            snapshot_after, clock[0], freshness_ms) is False
        entry = case.runtime._shadow.journal_payload()["entries"][-1]
        receipt = entry["dispatch"][0]
        assert receipt["confirmed"] is False
        assert receipt["ownershipChanged"] == "none"
        record = case.authority.record("light.demo_main")
        assert record is not None and record.fingerprint == "old"
    finally:
        await case.runtime.stop()


async def test_absence_off_boundary_freshness_confirms_and_completes(monkeypatch):
    """Sensor age exactly at the freshness limit still confirms (inclusive)."""
    from types import SimpleNamespace
    from custom_components.hausman_hub.application import room_lighting_ha_executor as module
    from custom_components.hausman_hub.application.room_lighting_ha_executor import (
        RoomLightingHaExecutor,
    )
    from custom_components.hausman_hub.domain.room_lighting_engine import EnginePolicy

    case, cfg, now = await ready_case(monkeypatch)
    try:
        freshness_ms = EnginePolicy.from_config(cfg).sensor_freshness_seconds * 1000
        sensor_state = case.hass.states.get("binary_sensor.demo_presence")
        sensor_state.last_updated = datetime.fromtimestamp(
            (now - freshness_ms) / 1000, timezone.utc)

        monkeypatch.setattr(module, "_new_service_context",
                            lambda: SimpleNamespace(id="own-command"))
        executor = RoomLightingHaExecutor(manual_light_authority=case.authority,
            light_readback_window_seconds=0, now_ms=lambda: now)
        executor.update_targets({}, room_targets={_ROOM_ID: {"light_main": cfg.devices.light_targets[0]}})
        case.runtime._executor = executor
        case.hass._command_report_clock = lambda: now
        await case.runtime.async_process([_ROOM_ID])
        assert [call[1] for call in case.hass.services.calls] == ["turn_off"]
        entry = case.runtime._shadow.journal_payload()["entries"][-1]
        receipt = entry["dispatch"][0]
        assert receipt["confirmed"] is True
        assert receipt["ownershipChanged"] == "none"
        assert receipt.get("manualReturn") == "returned"
        assert case.authority.record("light.demo_main") is None
    finally:
        await case.runtime.stop()


async def test_absence_off_stale_evidence_and_expired_priority_stays_unconfirmed(monkeypatch):
    """Stale absence evidence blocks the confirmation even after priority expiry."""
    import time
    from types import SimpleNamespace
    from custom_components.hausman_hub.application import room_lighting_ha_executor as module
    from custom_components.hausman_hub.application.room_lighting_ha_executor import (
        RoomLightingHaExecutor,
    )
    from custom_components.hausman_hub.domain.room_lighting_engine import (
        EnginePolicy,
        sensor_observation_is_fresh,
    )

    case, cfg, t0 = await ready_case(monkeypatch)
    try:
        clock = [t0]
        monkeypatch.setattr(time, "time", lambda: clock[0] / 1000)
        case.runtime._now_ms = lambda: clock[0]

        # Priority expires 3s after t0; the stable absence is already proven.
        await case.authority.async_capture(
            "light.demo_main", "on", "service", "expiring",
            observed_at_ms=t0 - 3_597_000)
        old_record = case.authority.record("light.demo_main")
        assert old_record is not None and old_record.fingerprint == "expiring"
        assert case.authority.blocks("light.demo_main") is True
        assert not case.priority.manual_claim_entity_ids(frozenset({"light.demo_main"}))

        freshness_ms = EnginePolicy.from_config(cfg).sensor_freshness_seconds * 1000
        observed_ms = t0 - freshness_ms + 1000  # 299s old at dispatch time.
        sensor_state = case.hass.states.get("binary_sensor.demo_presence")
        assert sensor_state.last_changed is not None
        assert sensor_state.last_changed.timestamp() * 1000 <= observed_ms
        sensor_state.last_updated = datetime.fromtimestamp(observed_ms / 1000, timezone.utc)
        snapshot_now = case.runtime._state_provider._sensor_snapshot(
            case.hass, cfg.devices.sensors[0], t0)
        assert sensor_observation_is_fresh(snapshot_now, t0, freshness_ms) is True

        monkeypatch.setattr(module, "_new_service_context",
                            lambda: SimpleNamespace(id="own-command"))
        monotonic = lambda: clock[0] / 1000
        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)
            # First wait moves past the priority deadline (t0+3s) and ages the
            # sensor to 303s without any new report; then publish the own OFF.
            clock[0] += 4000
            case.hass.states.set(
                "light.demo_main", "off", {},
                last_changed=datetime.fromtimestamp(clock[0] / 1000, timezone.utc),
                context=captured["context"])

        captured = {}

        async def deferred_async_call(domain, service, data, blocking=True, context=None):
            case.hass.services.calls.append((domain, service, dict(data)))
            captured["context"] = context  # Publish the OFF only after the wait.

        monkeypatch.setattr(case.hass.services, "async_call", deferred_async_call)

        executor = RoomLightingHaExecutor(manual_light_authority=case.authority,
            light_readback_window_seconds=5, light_readback_interval_seconds=2,
            now_ms=lambda: clock[0], monotonic=monotonic, sleep=fake_sleep)
        executor.update_targets({}, room_targets={_ROOM_ID: {"light_main": cfg.devices.light_targets[0]}})
        case.runtime._executor = executor
        case.hass._command_report_clock = lambda: clock[0]

        await case.runtime.async_process([_ROOM_ID])
        assert [call[1] for call in case.hass.services.calls] == ["turn_off"]
        assert sleeps, "confirmation wait never ran"
        assert case.hass.states.get("light.demo_main").state == "off"
        snapshot_after = case.runtime._state_provider._sensor_snapshot(
            case.hass, cfg.devices.sensors[0], clock[0])
        assert snapshot_after == snapshot_now
        assert sensor_observation_is_fresh(
            snapshot_after, clock[0], freshness_ms) is False
        assert case.authority.blocks("light.demo_main") is False
        assert not case.priority.manual_claim_entity_ids(frozenset({"light.demo_main"}))
        entry = case.runtime._shadow.journal_payload()["entries"][-1]
        receipt = entry["dispatch"][0]
        assert receipt["confirmed"] is False
        assert receipt["sendOutcome"] == "sent"
        assert receipt["reason"] == "unconfirmed"
        assert receipt["ownershipChanged"] == "none"
        assert receipt.get("manualReturn") != "returned"
        assert case.authority.record("light.demo_main") is old_record
    finally:
        await case.runtime.stop()
