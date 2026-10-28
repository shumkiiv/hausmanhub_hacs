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


@pytest.mark.parametrize("operation", ["write", "delete"])
@pytest.mark.parametrize("failure", [False, True])
async def test_configuration_generation_fences_before_persistence(operation, failure):
    store = _MemoryStore()
    service = RoomLightingService(store)
    original = config_from_payload(_payload("room"))
    await service.async_put_config(original)
    generation = service.configuration_generation
    calls = []

    async def persist(*_args):
        calls.append(service.configuration_generation)
        assert service.configuration_generation == generation + 1
        await asyncio.sleep(0)
        if failure:
            raise OSError("synthetic persistence error")
        return True

    store.async_upsert = persist
    store.async_delete = persist
    action = (service.async_put_config(replace(original, name="Новое имя"))
              if operation == "write" else service.async_delete_config("room"))
    if failure:
        with pytest.raises(OSError):
            await action
    else:
        await action
    assert calls == [generation + 1]
    assert service.configuration_generation == generation + 1


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


async def test_omitted_curve_params_block_is_preserved_under_service_lock() -> None:
    store = _MemoryStore()
    lock = asyncio.Lock()
    service = RoomLightingService(store, mutation_lock=lock)
    original = _payload("room_a")
    original["profile"] = "day_curve"
    original["curveParams"] = {
        "confirmedAbsenceSeconds": 600,
        "riseSeconds": 10,
        "dayStart": "09:00",
        "morningRamp": {"startPercent": 20, "fullPercent": 100, "fullBy": "12:00"},
        "eveningDimming": {
            "targetPercent": 5,
            "targetBy": "21:00",
            "reserveStart": "20:00",
        },
    }
    saved = await service.async_put_config(config_from_payload(original))
    update = _payload("room_a")
    update["version"] = saved.version
    update["name"] = "Новое название"
    changed = await service.async_put_config(
        config_from_payload(update), expected_version=saved.version,
        preserve_curve_params=True,
    )
    assert changed.curve is not None
    assert changed.curve.confirmed_absence_seconds == 600
    assert changed.curve.rise_seconds == 10
    assert changed.name == "Новое название"
    assert changed.version == 2


async def test_explicit_curve_params_block_replaces_the_saved_one() -> None:
    store = _MemoryStore()
    lock = asyncio.Lock()
    service = RoomLightingService(store, mutation_lock=lock)
    original = _payload("room_a")
    original["profile"] = "day_curve"
    original["curveParams"] = {"riseSeconds": 30}
    saved = await service.async_put_config(config_from_payload(original))
    update = _payload("room_a")
    update["version"] = saved.version
    update["profile"] = "day_curve"
    update["curveParams"] = {"riseSeconds": 45}
    changed = await service.async_put_config(
        config_from_payload(update), expected_version=saved.version,
    )
    assert changed.curve is not None
    assert changed.curve.rise_seconds == 45


async def test_creation_with_curve_params_normalizes_dimming_target_to_five() -> None:
    store = _MemoryStore()
    lock = asyncio.Lock()
    service = RoomLightingService(store, mutation_lock=lock)
    payload = _payload("room_a")
    payload["profile"] = "day_curve"
    payload["curveParams"] = {"riseSeconds": 10}
    created = await service.async_put_config(
        config_from_payload(payload),
        dimming_target_omitted=True,
    )
    assert created.dimming.target_percent == 5


async def test_creation_with_explicit_zero_target_is_respected() -> None:
    store = _MemoryStore()
    lock = asyncio.Lock()
    service = RoomLightingService(store, mutation_lock=lock)
    payload = _payload("room_a")
    payload["profile"] = "day_curve"
    payload["curveParams"] = {"riseSeconds": 10}
    payload["dimming"]["targetPercent"] = 0
    created = await service.async_put_config(
        config_from_payload(payload),
        dimming_target_omitted=False,
    )
    assert created.dimming.target_percent == 0


async def test_update_is_never_normalized_automatically() -> None:
    store = _MemoryStore()
    lock = asyncio.Lock()
    service = RoomLightingService(store, mutation_lock=lock)
    payload = _payload("room_a")
    payload["dimming"]["targetPercent"] = 40
    saved = await service.async_put_config(config_from_payload(payload))
    update = _payload("room_a")
    update["version"] = saved.version
    update["profile"] = "day_curve"
    update["curveParams"] = {"riseSeconds": 10}
    update["dimming"] = {"enabled": True, "onAbsence": True, "fadeSeconds": 5}
    changed = await service.async_put_config(
        config_from_payload(update),
        expected_version=saved.version,
        dimming_target_omitted=True,
    )
    # The normalization is a CREATION rule only: an update that omits the
    # dimming target keeps the saved 40 unchanged (projection rule).
    assert changed.dimming.target_percent == 40


async def test_creation_with_legacy_block_normalizes_dimming_target_to_five() -> None:
    """The spec 4.2 rule applies to a room created with ANY curveParams
    block form, including the legacy eveningMaxPercent."""
    store = _MemoryStore()
    lock = asyncio.Lock()
    service = RoomLightingService(store, mutation_lock=lock)
    payload = _payload("room_a")
    payload["profile"] = "day_curve"
    payload["curveParams"] = {"eveningMaxPercent": 40}
    created = await service.async_put_config(
        config_from_payload(payload),
        dimming_target_omitted=True,
    )
    assert created.curve is not None
    assert created.curve.evening_max_percent == 40
    assert created.curve.has_new_form is False
    assert created.dimming.target_percent == 5


async def test_legacy_block_update_keeps_the_saved_target() -> None:
    """An update of a legacy-form curve room that omits dimming.targetPercent
    keeps the saved value (the projection rule), not the legacy zero."""
    store = _MemoryStore()
    lock = asyncio.Lock()
    service = RoomLightingService(store, mutation_lock=lock)
    payload = _payload("room_a")
    payload["profile"] = "day_curve"
    payload["curveParams"] = {"eveningMaxPercent": 40}
    payload["dimming"]["targetPercent"] = 25
    saved = await service.async_put_config(config_from_payload(payload))
    update = _payload("room_a")
    update["version"] = saved.version
    update["profile"] = "day_curve"
    update["curveParams"] = {"eveningMaxPercent": 60}
    update["dimming"] = {"enabled": True, "onAbsence": True, "fadeSeconds": 5}
    changed = await service.async_put_config(
        config_from_payload(update),
        expected_version=saved.version,
        dimming_target_omitted=True,
    )
    # The explicit new block replaces the ceiling; the omitted dimming
    # target keeps the saved 25.
    assert changed.curve is not None
    assert changed.curve.evening_max_percent == 60
    assert changed.dimming.target_percent == 25


async def test_rooms_without_the_curve_keep_the_legacy_zero_target() -> None:
    """Rooms without curveParams keep the legacy behaviour unchanged: an
    omitted dimming target in an update falls back to the legacy zero."""

    store = _MemoryStore()
    lock = asyncio.Lock()
    service = RoomLightingService(store, mutation_lock=lock)
    payload = _payload("room_a")
    payload["dimming"]["targetPercent"] = 40
    saved = await service.async_put_config(config_from_payload(payload))
    update = _payload("room_a")
    update["version"] = saved.version
    update["dimming"] = {"enabled": True, "onAbsence": True, "fadeSeconds": 5}
    changed = await service.async_put_config(
        config_from_payload(update),
        expected_version=saved.version,
        dimming_target_omitted=True,
    )
    assert changed.dimming.target_percent == 0


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
