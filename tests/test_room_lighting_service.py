"""Service-level checks for room lighting entity-collision handling.

A shared sensor (presence, motion, illuminance) is legitimate shared
infrastructure, while a shared control entity (light target, power switch or
wireless switch) must be rejected.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
import pytest

from custom_components.hausman_hub.application.room_lighting_service import (
    RoomLightingService,
    RoomLightingRevisionConflictError,
    RoomLightingStorageError,
)
from custom_components.hausman_hub.domain.room_lighting import (
    RoomLightingViolation,
    config_from_payload,
)


class _MemoryStore:
    def __init__(self) -> None:
        self.configs: dict[str, object] = {}

    async def async_get(self, room_id: str):
        return self.configs.get(room_id)

    async def async_load_all(self):
        return tuple(self.configs.values())

    async def async_upsert(self, config) -> None:
        self.configs[config.room_id] = config

    async def async_delete(self, room_id: str) -> bool:
        return self.configs.pop(room_id, None) is not None


def _payload(
    room_id: str,
    *,
    sensor_entity: str = "binary_sensor.demo_presence",
    light_entity: str = "light.demo_main",
    switch_entity: str = "switch.demo_spots",
) -> dict:
    return {
        "contract": {"name": "hausman-hub-room-lighting-config", "version": 1},
        "roomId": room_id,
        "name": f"Комната {room_id}",
        "version": 1,
        "devices": {
            "sensors": [
                {
                    "id": "sensor_demo_presence",
                    "name": "Присутствие",
                    "kind": "presence",
                    "entityId": sensor_entity,
                    "autoAdoptOverride": None,
                }
            ],
            "light_targets": [
                {
                    "id": "light_main",
                    "name": "Люстра",
                    "kind": "light",
                    "entityId": light_entity,
                    "role": "main",
                    "groupId": None,
                    "brightness": True,
                    "color_temperature": True,
                    "autoAdoptOverride": None,
                },
                {
                    "id": "light_spots",
                    "name": "Точки",
                    "kind": "switch",
                    "entityId": switch_entity,
                    "role": "accent",
                    "groupId": None,
                    "brightness": False,
                    "color_temperature": False,
                    "autoAdoptOverride": False,
                },
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
                "targets": {"lightTargets": ["light_main"], "groupIds": [], "roles": []},
                "how": {
                    "brightness": 60,
                    "colorTemperature": 3000,
                    "fade": True,
                    "mode": "on_presence",
                    "minOnSeconds": 0,
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


async def test_shared_sensor_between_rooms_is_persisted() -> None:
    service = RoomLightingService(_MemoryStore())

    await service.async_put_config(config_from_payload(_payload("room_a")))
    saved = await service.async_put_config(
        config_from_payload(
            _payload(
                "room_b",
                light_entity="light.other_main",
                switch_entity="switch.other_spots",
            )
        )
    )

    assert saved.room_id == "room_b"


async def test_shared_light_target_is_rejected() -> None:
    service = RoomLightingService(_MemoryStore())

    await service.async_put_config(config_from_payload(_payload("room_a")))
    with pytest.raises(RoomLightingViolation):
        await service.async_put_config(
            config_from_payload(_payload("room_b", switch_entity="switch.other_spots"))
        )


async def test_shared_switch_target_is_rejected() -> None:
    service = RoomLightingService(_MemoryStore())

    await service.async_put_config(config_from_payload(_payload("room_a")))
    with pytest.raises(RoomLightingViolation):
        await service.async_put_config(
            config_from_payload(_payload("room_b", light_entity="light.other_main"))
        )


async def test_omitted_manual_policy_is_preserved_under_service_lock() -> None:
    store = _MemoryStore()
    lock = asyncio.Lock()
    service = RoomLightingService(store, mutation_lock=lock)
    original = _payload("room_a")
    original["manualControlReturn"] = {"prioritySeconds": 7200}
    saved = await service.async_put_config(config_from_payload(original))
    legacy = _payload("room_a")
    legacy["name"] = "Новое название"
    checked = []
    real_get = store.async_get

    async def locked_get(room_id):
        checked.append(lock.locked())
        return await real_get(room_id)

    store.async_get = locked_get
    changed = await service.async_put_config(
        config_from_payload(legacy), expected_version=saved.version,
        preserve_manual_control_return=True,
    )
    assert checked and all(checked)
    assert changed.manual_control_return.priority_seconds == 7200
    assert changed.name == "Новое название"
    assert changed.version == 2


async def test_stale_service_write_is_rejected_without_save_or_invalidation() -> None:
    store = _MemoryStore()
    invalidations = []
    service = RoomLightingService(store, topology_invalidator=lambda: invalidations.append(True))
    config = config_from_payload(_payload("room_a"))
    saved = await service.async_put_config(config)
    newer = await service.async_put_config(replace(saved, name="Новое"))
    before = len(invalidations)
    with pytest.raises(RoomLightingRevisionConflictError) as error:
        await service.async_put_config(replace(saved, name="Старое"), expected_version=1)
    assert error.value.expected == 1 and error.value.actual == 2
    assert store.configs["room_a"] == newer
    assert len(invalidations) == before


async def test_concurrent_service_writers_same_revision_only_one_succeeds() -> None:
    service = RoomLightingService(_MemoryStore())
    saved = await service.async_put_config(config_from_payload(_payload("room_a")))
    results = await asyncio.gather(
        service.async_put_config(replace(saved, name="Первое"), expected_version=1),
        service.async_put_config(replace(saved, name="Второе"), expected_version=1),
        return_exceptions=True,
    )
    assert sum(isinstance(value, RoomLightingRevisionConflictError) for value in results) == 1
    current = await service.async_get_config("room_a")
    assert current.version == 2
    assert current.name == "Первое"


async def test_legacy_create_and_internal_explicit_write_remain_compatible() -> None:
    service = RoomLightingService(_MemoryStore())
    config = config_from_payload(_payload("room_a"))
    saved = await service.async_put_config(config, expected_version=1, preserve_manual_control_return=True)
    assert saved.manual_control_return.priority_seconds == 3600
    priority = replace(saved.manual_control_return, priority_seconds=7200)
    explicit = await service.async_put_config(replace(saved, manual_control_return=priority))
    assert explicit.manual_control_return.priority_seconds == 7200


async def test_storage_failure_releases_lock_and_keeps_previous_config() -> None:
    store = _MemoryStore()
    lock = asyncio.Lock()
    service = RoomLightingService(store, mutation_lock=lock)
    saved = await service.async_put_config(config_from_payload(_payload("room_a")))

    async def failed_save(config):
        return False

    store.async_upsert = failed_save
    with pytest.raises(RoomLightingStorageError):
        await service.async_put_config(replace(saved, name="Не сохранено"), expected_version=1)
    assert not lock.locked()
    assert store.configs["room_a"] == saved
