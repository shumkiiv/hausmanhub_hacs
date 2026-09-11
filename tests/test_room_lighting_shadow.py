"""Shadow-mode tests: decisions are journalled and zero commands are sent."""

from __future__ import annotations

from datetime import datetime, time, timezone

from custom_components.hausman_hub.application.room_lighting_shadow import (
    MAX_JOURNAL_ENTRIES,
    RoomLightingShadowService,
)
from custom_components.hausman_hub.domain.room_lighting import (
    SensorKind,
    config_from_payload,
)
from custom_components.hausman_hub.domain.room_lighting_engine import (
    LightSnapshot,
    RoomLightingContext,
    SensorSnapshot,
)
from custom_components.hausman_hub.domain.room_lighting_ownership import (
    OwnershipSnapshot,
    OwnershipSource,
    SensorState,
)

_TZ = timezone.utc


def _at(hour: int) -> int:
    return int(datetime(2026, 9, 11, hour, 0, tzinfo=_TZ).timestamp() * 1000)


def _config():
    payload = {
        "contract": {"name": "hausman-hub-room-lighting-config", "version": 1},
        "roomId": "room_demo_entry",
        "name": "Тамбур",
        "version": 1,
        "devices": {
            "sensors": [
                {
                    "id": "sensor_demo_presence",
                    "name": "Присутствие",
                    "kind": "presence",
                    "entityId": "binary_sensor.demo_presence",
                    "autoAdoptOverride": None,
                }
            ],
            "light_targets": [
                {
                    "id": "light_main",
                    "name": "Люстра",
                    "kind": "light",
                    "entityId": "light.demo_main",
                    "role": "main",
                    "groupId": None,
                    "brightness": True,
                    "color_temperature": True,
                    "autoAdoptOverride": None,
                }
            ],
            "power_switch": None,
            "wireless_switches": [],
            "selectAll": False,
        },
        "schedule": [
            {
                "id": "sch_day",
                "title": "День",
                "when": {
                    "daysOfWeek": "all",
                    "holiday": False,
                    "anchor": {"kind": "fixed", "time": "09:00", "offsetMinutes": 0},
                },
                "targets": {
                    "lightTargets": ["light_main"],
                    "groupIds": [],
                    "roles": [],
                },
                "how": {
                    "brightness": 60,
                    "colorTemperature": 3000,
                    "fade": True,
                    "mode": "on_presence",
                },
            }
        ],
        "switchBindings": [],
        "illumination": None,
        "dimming": {
            "enabled": True,
            "onAbsence": True,
            "fadeSeconds": 20,
            "targetPercent": 0,
        },
        "manualOffProtection": {
            "enabled": True,
            "minimumIntervalSeconds": 600,
            "releaseMode": "timer_and_absence",
            "stableAbsenceSeconds": 30,
            "priority": "manual_above_auto",
        },
        "awayBehavior": {"mode": "none"},
        "autoAdopt": True,
        "updatedAt": 1,
        "overrides": {},
    }
    return config_from_payload(payload)


def _ctx(now: int, lights: tuple[LightSnapshot, ...] = (), ownership: tuple[OwnershipSnapshot, ...] = ()) -> RoomLightingContext:
    return RoomLightingContext(
        now=now,
        timezone=_TZ,
        sunrise=time(7, 0),
        sunset=time(19, 0),
        sensors=(
            SensorSnapshot(
                sensor_id="sensor_demo_presence",
                kind=SensorKind.PRESENCE,
                state=SensorState.ON,
                last_changed=now,
            ),
        ),
        lights=lights,
        ownership=ownership,
    )


class _MemoryStore:
    def __init__(self) -> None:
        self.payload: object | None = None
        self.saves = 0

    async def async_load(self) -> object | None:
        return self.payload

    async def async_save(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.saves += 1


class _SpyExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def __call__(self, *args: object, **kwargs: object) -> None:
        self.calls.append((args, kwargs))


async def test_shadow_journals_decision_and_sends_no_command() -> None:
    now = _at(10)
    store = _MemoryStore()
    spy = _SpyExecutor()
    service = RoomLightingShadowService(store, executor=spy)

    decision = await service.async_evaluate(_config(), _ctx(now))

    assert service.commands_enabled is False
    assert spy.calls == []
    assert decision.commands
    payload = service.journal_payload()
    assert payload["mode"] == "shadow"
    assert payload["commandsEnabled"] is False
    assert payload["entries"][0]["roomId"] == "room_demo_entry"
    assert payload["entries"][0]["commands"][0]["action"] == "turn_on"
    assert store.saves == 1


async def test_shadow_repeat_on_already_on_light_is_idempotent() -> None:
    now = _at(10)
    store = _MemoryStore()
    service = RoomLightingShadowService(store)
    lights = (
        LightSnapshot("light_main", SensorState.ON, now - 1000, brightness=60, color_temperature=3000),
    )
    ownership = (OwnershipSnapshot("light_main", OwnershipSource.AUTO, True, now - 1000),)

    first = await service.async_evaluate(_config(), _ctx(now, lights, ownership))
    second = await service.async_evaluate(_config(), _ctx(now, lights, ownership))

    assert first.commands == ()
    assert second.commands == ()
    entries = service.journal_payload()["entries"]
    assert entries[-1]["commands"] == []


async def test_damaged_journal_loads_empty() -> None:
    store = _MemoryStore()
    store.payload = "broken"
    service = RoomLightingShadowService(store)

    await service.async_load()

    assert service.journal_payload()["entries"] == []


async def test_journal_is_bounded() -> None:
    now = _at(10)
    store = _MemoryStore()
    service = RoomLightingShadowService(store)

    for _ in range(MAX_JOURNAL_ENTRIES + 5):
        await service.async_evaluate(_config(), _ctx(now))

    assert len(service.journal_payload()["entries"]) == MAX_JOURNAL_ENTRIES
