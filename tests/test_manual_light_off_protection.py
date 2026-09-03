"""Policy and lifecycle tests for durable manual light-off protection."""

import asyncio
import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from custom_components.hausman_hub.application.manual_light_off_protection import (
    ManualLightOffProtectionCoordinator,
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
