"""Policy and lifecycle tests for durable manual light-off protection."""

import asyncio
import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from custom_components.hausman_hub.application.manual_light_off_protection import (
    ManualLightOffProtectionCoordinator,
    valid_manual_light_off_protection_payload,
)

from custom_components.hausman_hub.domain.manual_light_off_protection import (
    resolve_manual_off_policy,
)


def test_profile_override_inherits_unspecified_room_and_global_fields() -> None:
    settings = {
        "globalPolicy": {
            "enabled": True,
            "minimumIntervalSeconds": 600,
            "releaseMode": "timer_and_absence",
            "stableAbsenceSeconds": 30,
            "extendOnRepeatedManualOff": True,
            "noSensorFallback": "timer_only",
            "protectedScope": "profile",
            "allowManualRelease": True,
        },
        "roomOverrides": {"tambur": {"minimumIntervalSeconds": 900}},
        "profileOverrides": {
            "tambur_points": {"releaseMode": "timer_and_absence"}
        },
        "profiles": [],
    }

    effective = resolve_manual_off_policy(
        settings, "tambur", "tambur_points"
    )

    assert effective.minimum_interval_seconds == 900
    assert effective.release_mode.value == "timer_and_absence"
    assert effective.stable_absence_seconds == 30


class MemoryStore:
    def __init__(self, payload: object | None = None) -> None:
        self.payload = payload

    async def async_load(self) -> object | None:
        return copy.deepcopy(self.payload)

    async def async_save(self, payload: dict[str, object]) -> None:
        self.payload = copy.deepcopy(payload)


def test_catalog_coverage_can_recover_without_clearing_runtime_failure() -> None:
    coordinator = ManualLightOffProtectionCoordinator(MemoryStore())

    coordinator.set_catalog_coverage_healthy(False)
    assert coordinator.unhealthy

    coordinator.set_catalog_coverage_healthy(True)
    assert not coordinator.unhealthy

    coordinator.mark_unhealthy()
    coordinator.set_catalog_coverage_healthy(False)
    coordinator.set_catalog_coverage_healthy(True)
    assert coordinator.unhealthy


def _settings(*, release_mode: str = "timer_and_absence") -> dict[str, object]:
    return {
        "globalPolicy": {
            "enabled": True,
            "minimumIntervalSeconds": 600,
            "releaseMode": release_mode,
            "stableAbsenceSeconds": 30,
            "extendOnRepeatedManualOff": True,
            "noSensorFallback": "timer_only",
            "protectedScope": "profile",
            "allowManualRelease": True,
        },
        "roomOverrides": {},
        "profileOverrides": {},
        "profiles": [
            {
                "roomId": "tambur",
                "profileId": "tambur_points",
                "lightIds": ["light.tambur_points", "light.tambur_lamp"],
                "presenceSensorIds": ["binary_sensor.tambur_presence"],
            }
        ],
    }


def test_manual_off_lifecycle_keeps_profile_blocked_until_timer_and_absence() -> None:
    async def exercise() -> None:
        now = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
        store = MemoryStore()
        coordinator = ManualLightOffProtectionCoordinator(
            store, now=lambda: now
        )
        await coordinator.async_load()
        await coordinator.async_replace_settings("settings.1", 0, _settings())

        await coordinator.async_note_state_transition(
            "light.tambur_points",
            SimpleNamespace(state="on"),
            SimpleNamespace(state="off"),
            None,
        )
        assert coordinator.snapshot()["protections"][0]["state"] == "active"

        blocked = await coordinator.async_decide_entity(
            "light.tambur_lamp", automatic=True, dry_run=False
        )
        assert not blocked.allowed
        assert blocked.reason == "manual_off_protection_active"

        now += timedelta(minutes=10)
        still_blocked = await coordinator.async_decide_entity(
            "light.tambur_lamp", automatic=True, dry_run=False
        )
        assert not still_blocked.allowed
        assert still_blocked.reason == "manual_off_protection_absence_required"

        await coordinator.async_note_state_transition(
            "binary_sensor.tambur_presence",
            SimpleNamespace(state="on"),
            SimpleNamespace(state="off"),
            None,
        )
        assert coordinator.snapshot()["protections"][0]["absenceSince"] is not None
        now += timedelta(seconds=30)
        released = await coordinator.async_decide_entity(
            "light.tambur_lamp", automatic=True, dry_run=False
        )
        assert released.allowed
        assert coordinator.snapshot()["protections"] == []

    asyncio.run(exercise())


@pytest.mark.parametrize("state", ["unknown", "unavailable", "off"])
def test_unknown_unavailable_and_stale_presence_never_release_automatically(
    state: str,
) -> None:
    async def exercise() -> None:
        now = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
        coordinator = ManualLightOffProtectionCoordinator(MemoryStore(), now=lambda: now)
        await coordinator.async_load()
        await coordinator.async_replace_settings("settings.1", 0, _settings())
        await coordinator.async_note_state_transition(
            "light.tambur_points", SimpleNamespace(state="on"), SimpleNamespace(state="off"), None
        )
        now += timedelta(minutes=11)
        sensor = SimpleNamespace(
            state=state,
            last_updated=now - timedelta(minutes=6) if state == "off" else now,
        )
        await coordinator.async_note_state_transition(
            "binary_sensor.tambur_presence", SimpleNamespace(state="on"), sensor, None
        )
        now += timedelta(seconds=31)
        decision = await coordinator.async_decide_entity(
            "light.tambur_points", automatic=True, dry_run=False
        )
        assert not decision.allowed

    asyncio.run(exercise())


def test_repeated_off_extends_frozen_policy_and_manual_on_cancels_protection() -> None:
    async def exercise() -> None:
        now = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
        coordinator = ManualLightOffProtectionCoordinator(MemoryStore(), now=lambda: now)
        await coordinator.async_load()
        await coordinator.async_replace_settings("settings.1", 0, _settings())
        on, off = SimpleNamespace(state="on"), SimpleNamespace(state="off")
        await coordinator.async_note_state_transition("light.tambur_points", on, off, None)
        first = coordinator.snapshot()["protections"][0]
        now += timedelta(minutes=1)
        await coordinator.async_note_state_transition("light.tambur_points", on, off, None)
        repeated = coordinator.snapshot()["protections"][0]
        assert repeated["notBefore"] > first["notBefore"]
        assert repeated["revision"] == first["revision"] + 1
        await coordinator.async_note_state_transition("light.tambur_points", off, on, None)
        assert coordinator.snapshot()["protections"] == []

    asyncio.run(exercise())


def test_settings_cas_duplicate_request_and_frozen_active_policy() -> None:
    async def exercise() -> None:
        now = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
        coordinator = ManualLightOffProtectionCoordinator(MemoryStore(), now=lambda: now)
        await coordinator.async_load()
        first = await coordinator.async_replace_settings("settings.1", 0, _settings())
        duplicate = await coordinator.async_replace_settings("settings.1", 999, _settings())
        assert duplicate == first
        with pytest.raises(ValueError, match="revision"):
            await coordinator.async_replace_settings("settings.2", 0, _settings())
        await coordinator.async_note_state_transition(
            "light.tambur_points", SimpleNamespace(state="on"), SimpleNamespace(state="off"), None
        )
        changed = _settings(release_mode="timer_only")
        await coordinator.async_replace_settings("settings.3", 1, changed)
        now += timedelta(minutes=1)
        await coordinator.async_note_state_transition(
            "light.tambur_points", SimpleNamespace(state="on"), SimpleNamespace(state="off"), None
        )
        active = coordinator.snapshot()["protections"][0]
        assert active["effectivePolicy"]["releaseMode"] == "timer_and_absence"

    asyncio.run(exercise())


def test_frozen_scope_blocks_after_profile_removal_and_sensor_change() -> None:
    async def exercise() -> None:
        now = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
        coordinator = ManualLightOffProtectionCoordinator(MemoryStore(), now=lambda: now)
        await coordinator.async_load()
        await coordinator.async_replace_settings("one", 0, _settings())
        await coordinator.async_note_state_transition("light.tambur_points", SimpleNamespace(state="on"), SimpleNamespace(state="off"), None)
        removed = _settings(); removed["profiles"] = []
        await coordinator.async_replace_settings("two", 1, removed)
        now += timedelta(days=1)
        assert not (await coordinator.async_decide_entity("light.tambur_points", automatic=True, dry_run=False)).allowed

    asyncio.run(exercise())


def test_source_scope_and_completed_history_allow_repeated_lifecycles() -> None:
    async def exercise() -> None:
        now = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
        settings = _settings()
        settings["globalPolicy"]["protectedScope"] = "source"
        settings["globalPolicy"]["releaseMode"] = "timer_only"
        coordinator = ManualLightOffProtectionCoordinator(MemoryStore(), now=lambda: now)
        await coordinator.async_load(); await coordinator.async_replace_settings("one", 0, settings)
        on, off = SimpleNamespace(state="on"), SimpleNamespace(state="off")
        await coordinator.async_note_state_transition("light.tambur_points", on, off, None)
        assert not (await coordinator.async_decide_entity("light.tambur_points", automatic=True, dry_run=False)).allowed
        assert (await coordinator.async_decide_entity("light.tambur_lamp", automatic=True, dry_run=False)).allowed
        now += timedelta(minutes=10)
        await coordinator.async_note_state_transition(
            "binary_sensor.tambur_presence", SimpleNamespace(state="on"), SimpleNamespace(state="off"), None
        )
        assert (await coordinator.async_decide_entity("light.tambur_points", automatic=True, dry_run=False)).allowed
        await coordinator.async_note_state_transition("light.tambur_points", on, off, None)
        assert not (await coordinator.async_decide_entity("light.tambur_points", automatic=True, dry_run=False)).allowed

    asyncio.run(exercise())


def test_snapshot_is_exact_get_contract_and_presence_clears_absence() -> None:
    async def exercise() -> None:
        now = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
        coordinator = ManualLightOffProtectionCoordinator(MemoryStore(), now=lambda: now)
        await coordinator.async_load(); await coordinator.async_replace_settings("one", 0, _settings())
        await coordinator.async_note_state_transition("light.tambur_points", SimpleNamespace(state="on"), SimpleNamespace(state="off"), None)
        await coordinator.async_note_state_transition("binary_sensor.tambur_presence", SimpleNamespace(state="on"), SimpleNamespace(state="off"), None)
        await coordinator.async_note_state_transition("binary_sensor.tambur_presence", SimpleNamespace(state="off"), SimpleNamespace(state="unknown"), None)
        assert coordinator.snapshot()["protections"][0]["absenceSince"] is None
        snapshot = coordinator.snapshot()
        assert set(snapshot) == {"contract", "revision", "updatedAt", "settings", "protections"}
        assert snapshot["revision"] > 0 and len(snapshot["protections"]) <= 64

    asyncio.run(exercise())


def test_frozen_sensor_ids_survive_profile_change_removal_and_restart() -> None:
    async def exercise() -> None:
        now = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
        store = MemoryStore()
        initial = _settings(release_mode="absence_only")
        coordinator = ManualLightOffProtectionCoordinator(store, now=lambda: now)
        await coordinator.async_load()
        await coordinator.async_replace_settings("one", 0, initial)
        await coordinator.async_note_state_transition(
            "light.tambur_points", SimpleNamespace(state="on"), SimpleNamespace(state="off"), None
        )

        changed = _settings(release_mode="absence_only")
        changed["profiles"][0]["presenceSensorIds"] = ["binary_sensor.new_presence"]
        await coordinator.async_replace_settings("two", 1, changed)
        await coordinator.async_note_state_transition(
            "light.tambur_points", SimpleNamespace(state="on"), SimpleNamespace(state="off"), None
        )
        now += timedelta(seconds=31)
        await coordinator.async_note_state_transition(
            "binary_sensor.new_presence", SimpleNamespace(state="on"), SimpleNamespace(state="off"), None
        )
        now += timedelta(seconds=31)
        assert not (await coordinator.async_decide_entity(
            "light.tambur_points", automatic=True, dry_run=False
        )).allowed

        restarted = ManualLightOffProtectionCoordinator(store, now=lambda: now)
        await restarted.async_load()
        assert not (await restarted.async_decide_entity(
            "light.tambur_points", automatic=True, dry_run=False
        )).allowed
        removed = _settings(release_mode="absence_only")
        removed["profiles"] = []
        await restarted.async_replace_settings("three", 2, removed)
        assert not (await restarted.async_decide_entity(
            "light.tambur_points", automatic=True, dry_run=False
        )).allowed

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("release_mode", "fallback", "has_sensor", "allowed"),
    [
        ("timer_only", "manual_release", True, False),
        ("absence_only", "timer_only", False, True),
        ("timer_and_absence", "timer_only", False, True),
        ("absence_only", "manual_release", False, False),
    ],
)
def test_release_modes_and_no_sensor_fallbacks_are_frozen(
    release_mode: str, fallback: str, has_sensor: bool, allowed: bool
) -> None:
    async def exercise() -> None:
        now = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
        settings = _settings(release_mode=release_mode)
        settings["globalPolicy"]["noSensorFallback"] = fallback
        if not has_sensor:
            settings["profiles"][0]["presenceSensorIds"] = []
        coordinator = ManualLightOffProtectionCoordinator(MemoryStore(), now=lambda: now)
        await coordinator.async_load()
        await coordinator.async_replace_settings("one", 0, settings)
        await coordinator.async_note_state_transition(
            "light.tambur_points", SimpleNamespace(state="on"), SimpleNamespace(state="off"), None
        )
        now += timedelta(minutes=11)
        assert (await coordinator.async_decide_entity(
            "light.tambur_points", automatic=True, dry_run=False
        )).allowed is allowed

    asyncio.run(exercise())


def test_manual_release_eviction_write_failure_and_repeated_lifecycle() -> None:
    class FailingStore(MemoryStore):
        async def async_save(self, payload: dict[str, object]) -> None:
            raise OSError("disk full")

    async def exercise() -> None:
        now = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
        store = MemoryStore()
        settings = _settings(release_mode="timer_only")
        coordinator = ManualLightOffProtectionCoordinator(store, now=lambda: now)
        await coordinator.async_load()
        for revision in range(129):
            settings["globalPolicy"]["minimumIntervalSeconds"] = 600 + revision
            await coordinator.async_replace_settings(f"settings.{revision}", revision, settings)
        assert len(store.payload["receipts"]) == 128
        await coordinator.async_note_state_transition(
            "light.tambur_points", SimpleNamespace(state="on"), SimpleNamespace(state="off"), None
        )
        protection = coordinator.snapshot()["protections"][0]
        decision = await coordinator.async_decide_entity(
            "light.tambur_points", automatic=True, dry_run=True
        )
        await coordinator.async_release("release.1", decision.protection_id, protection["revision"])
        await coordinator.async_note_state_transition(
            "light.tambur_points", SimpleNamespace(state="on"), SimpleNamespace(state="off"), None
        )
        assert not coordinator.unhealthy

        failed = ManualLightOffProtectionCoordinator(FailingStore(), now=lambda: now)
        await failed.async_load()
        with pytest.raises(OSError, match="persistence failed"):
            await failed.async_replace_settings("failure.1", 0, _settings())
        assert failed.unhealthy
        assert not (await failed.async_decide_entity(
            "light.tambur_points", automatic=True, dry_run=False
        )).allowed

    asyncio.run(exercise())


def test_legacy_receipt_is_dropped_before_restart_persistence() -> None:
    async def exercise() -> None:
        store = MemoryStore()
        coordinator = ManualLightOffProtectionCoordinator(store)
        await coordinator.async_load()
        await coordinator.async_replace_settings("current.1", 0, _settings())
        legacy = store.payload.copy()
        legacy_receipt = legacy["receipts"][0]["receipt"].copy()
        legacy_receipt["requestId"] = "legacy.1"
        legacy["receipts"] = [{
            "requestId": "legacy.1",
            "receipt": legacy_receipt,
        }]
        store.payload = legacy
        restarted = ManualLightOffProtectionCoordinator(store)
        await restarted.async_load()
        await restarted.async_replace_settings("current.2", 1, _settings())
        assert all("operation" in item and "payloadFingerprint" in item for item in store.payload["receipts"])
        assert all(item["requestId"] != "legacy.1" for item in store.payload["receipts"])

    asyncio.run(exercise())


def test_source_scoped_records_and_completed_history_survive_restart_at_capacity() -> None:
    async def exercise() -> None:
        now = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
        store = MemoryStore()
        source_settings = _settings(release_mode="timer_only")
        source_settings["globalPolicy"]["protectedScope"] = "source"
        coordinator = ManualLightOffProtectionCoordinator(store, now=lambda: now)
        await coordinator.async_load()
        await coordinator.async_replace_settings("source.settings", 0, source_settings)
        on, off = SimpleNamespace(state="on"), SimpleNamespace(state="off")
        await coordinator.async_note_state_transition("light.tambur_points", on, off, None)
        await coordinator.async_note_state_transition("light.tambur_lamp", on, off, None)
        restarted = ManualLightOffProtectionCoordinator(store, now=lambda: now)
        await restarted.async_load()
        assert not (await restarted.async_decide_entity(
            "light.tambur_points", automatic=True, dry_run=True
        )).allowed
        assert not (await restarted.async_decide_entity(
            "light.tambur_lamp", automatic=True, dry_run=True
        )).allowed

        history_store = MemoryStore()
        history = ManualLightOffProtectionCoordinator(history_store, now=lambda: now)
        await history.async_load()
        await history.async_replace_settings("history.settings", 0, _settings(release_mode="timer_only"))
        for _ in range(257):
            await history.async_note_state_transition("light.tambur_points", on, off, None)
            now += timedelta(minutes=11)
            await history.async_note_state_transition(
                "binary_sensor.tambur_presence", on, off, None
            )
            assert (await history.async_decide_entity(
                "light.tambur_points", automatic=True, dry_run=False
            )).allowed
        assert len(history_store.payload["completed"]) == 256
        assert not history.unhealthy

    asyncio.run(exercise())


def test_active_protection_capacity_never_evicts_an_active_record() -> None:
    async def exercise() -> None:
        now = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
        settings = _settings(release_mode="timer_only")
        settings["globalPolicy"]["protectedScope"] = "source"
        settings["profiles"] = [
            {
                "roomId": f"room_{index}", "profileId": f"profile_{index}",
                "lightIds": [f"light.room_{index}_one", f"light.room_{index}_two"],
                "presenceSensorIds": [],
            }
            for index in range(64)
        ]
        coordinator = ManualLightOffProtectionCoordinator(MemoryStore(), now=lambda: now)
        await coordinator.async_load()
        await coordinator.async_replace_settings("settings", 0, settings)
        on, off = SimpleNamespace(state="on"), SimpleNamespace(state="off")
        for index in range(64):
            await coordinator.async_note_state_transition(
                f"light.room_{index}_one", on, off, None
            )
        assert len(coordinator.snapshot()["protections"]) == 64
        with pytest.raises(RuntimeError, match="full"):
            await coordinator.async_note_state_transition("light.room_0_two", on, off, None)
        assert len(coordinator.snapshot()["protections"]) == 64
        assert coordinator.unhealthy

    asyncio.run(exercise())


def test_timer_only_requires_fresh_known_evidence_for_configured_sensor() -> None:
    async def exercise() -> None:
        now = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
        coordinator = ManualLightOffProtectionCoordinator(MemoryStore(), now=lambda: now)
        await coordinator.async_load()
        await coordinator.async_replace_settings("settings", 0, _settings(release_mode="timer_only"))
        await coordinator.async_note_state_transition(
            "light.tambur_points", SimpleNamespace(state="on"), SimpleNamespace(state="off"), None
        )
        now += timedelta(minutes=11)
        for state in ("unknown", "unavailable"):
            await coordinator.async_note_state_transition(
                "binary_sensor.tambur_presence", SimpleNamespace(state="on"), SimpleNamespace(state=state), None
            )
            assert not (await coordinator.async_decide_entity(
                "light.tambur_points", automatic=True, dry_run=False
            )).allowed
        await coordinator.async_note_state_transition(
            "binary_sensor.tambur_presence", SimpleNamespace(
                state="unknown"), SimpleNamespace(state="off", last_updated=now - timedelta(minutes=6)), None
        )
        assert coordinator.snapshot()["protections"][0]["absenceSince"] is None
        assert not (await coordinator.async_decide_entity(
            "light.tambur_points", automatic=True, dry_run=False
        )).allowed
        await coordinator.async_note_state_transition(
            "binary_sensor.tambur_presence", SimpleNamespace(state="unknown"), SimpleNamespace(state="off"), None
        )
        assert (await coordinator.async_decide_entity(
            "light.tambur_points", automatic=True, dry_run=False
        )).allowed

    asyncio.run(exercise())


def test_snapshot_revision_is_the_settings_cas_revision() -> None:
    async def exercise() -> None:
        now = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
        coordinator = ManualLightOffProtectionCoordinator(MemoryStore(), now=lambda: now)
        await coordinator.async_load()
        receipt = await coordinator.async_replace_settings("one", 0, _settings())
        assert coordinator.snapshot()["revision"] == receipt["revision"] == 1
        await coordinator.async_note_state_transition(
            "light.tambur_points", SimpleNamespace(state="on"), SimpleNamespace(state="off"), None
        )
        assert coordinator.snapshot()["revision"] == 1
        next_receipt = await coordinator.async_replace_settings(
            "two", coordinator.snapshot()["revision"], _settings(release_mode="timer_only")
        )
        assert coordinator.snapshot()["revision"] == next_receipt["revision"] == 2

    asyncio.run(exercise())


def test_snapshot_derives_remaining_minimum_seconds_without_persisting_it() -> None:
    async def exercise() -> None:
        now = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
        store = MemoryStore()
        coordinator = ManualLightOffProtectionCoordinator(store, now=lambda: now)
        await coordinator.async_load()
        await coordinator.async_replace_settings("one", 0, _settings(release_mode="timer_only"))
        await coordinator.async_note_state_transition(
            "light.tambur_points", SimpleNamespace(state="on"), SimpleNamespace(state="off"), None
        )
        assert coordinator.snapshot()["protections"][0]["remainingMinimumSeconds"] == 600
        now += timedelta(seconds=599, microseconds=1)
        assert coordinator.snapshot()["protections"][0]["remainingMinimumSeconds"] == 1
        assert "remainingMinimumSeconds" not in store.payload["protections"][0]

    asyncio.run(exercise())


def test_overlapping_protections_are_decided_atomically_without_partial_release() -> None:
    async def exercise() -> None:
        now = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
        coordinator = ManualLightOffProtectionCoordinator(MemoryStore(), now=lambda: now)
        initial = _settings(release_mode="timer_only")
        initial["profiles"][0]["presenceSensorIds"] = []
        await coordinator.async_load()
        await coordinator.async_replace_settings("one", 0, initial)
        on, off = SimpleNamespace(state="on"), SimpleNamespace(state="off")
        await coordinator.async_note_state_transition("light.tambur_points", on, off, None)
        now += timedelta(minutes=11)
        moved = _settings(release_mode="timer_only")
        moved["profiles"] = [{
            "roomId": "other", "profileId": "other_points",
            "lightIds": ["light.tambur_points"], "presenceSensorIds": [],
        }]
        await coordinator.async_replace_settings("two", 1, moved)
        await coordinator.async_note_state_transition("light.tambur_points", on, off, None)
        decision = await coordinator.async_decide_entity(
            "light.tambur_points", automatic=True, dry_run=False
        )
        assert not decision.allowed
        assert len(coordinator.snapshot()["protections"]) == 2

    asyncio.run(exercise())


def test_protection_id_is_bounded_for_maximum_contract_ids() -> None:
    async def exercise() -> None:
        now = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
        room_id = "r" + "a" * 63
        profile_id = "p" + "b" * 63
        entity_id = "light." + "c" * 122
        settings = _settings(release_mode="timer_only")
        settings["globalPolicy"]["protectedScope"] = "source"
        settings["profiles"] = [{
            "roomId": room_id, "profileId": profile_id, "lightIds": [entity_id],
            "presenceSensorIds": [],
        }]
        store = MemoryStore()
        coordinator = ManualLightOffProtectionCoordinator(store, now=lambda: now)
        await coordinator.async_load()
        await coordinator.async_replace_settings("settings", 0, settings)
        await coordinator.async_note_state_transition(entity_id, SimpleNamespace(state="on"), SimpleNamespace(state="off"), None)
        decision = await coordinator.async_decide_entity(entity_id, automatic=True, dry_run=True)
        assert decision.protection_id is not None and len(decision.protection_id) <= 128
        assert valid_manual_light_off_protection_payload(store.payload)

    asyncio.run(exercise())
