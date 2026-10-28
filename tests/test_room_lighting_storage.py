"""Storage and service tests for room lighting configurations."""

from __future__ import annotations

import asyncio
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.hausman_hub.application.room_lighting_service import (
    DEFAULT_TEMPLATE_ID,
    RoomLightingService,
)
from custom_components.hausman_hub.domain.room_lighting import (
    ROOM_LIGHTING_STORAGE_VERSION,
    RoomLightingViolation,
    config_from_payload,
)


def _fake_ha_storage_modules() -> tuple[dict[str, types.ModuleType], type]:
    modules: dict[str, types.ModuleType] = {}
    if "homeassistant" not in sys.modules:
        homeassistant = types.ModuleType("homeassistant")
        homeassistant.__path__ = []
        modules["homeassistant"] = homeassistant
    if "homeassistant.core" not in sys.modules:
        core = types.ModuleType("homeassistant.core")
        core.HomeAssistant = object  # type: ignore[attr-defined]
        modules["homeassistant.core"] = core
    if "homeassistant.helpers" not in sys.modules:
        helpers = types.ModuleType("homeassistant.helpers")
        helpers.__path__ = []
        modules["homeassistant.helpers"] = helpers
    storage = types.ModuleType("homeassistant.helpers.storage")

    class FakeStore:
        backing: dict[str, dict[str, object]] = {}

        def __class_getitem__(cls, item: object) -> type:
            return cls

        def __init__(self, hass: object, version: int, key: str, **kwargs: object) -> None:
            self.hass = hass
            self.version = version
            self.key = key
            self.max_readable_version = kwargs.get("max_readable_version")

        async def async_load(self) -> dict[str, object] | None:
            return self.backing.get(self.key)

        async def async_save(self, value: dict[str, object]) -> None:
            self.backing[self.key] = value

    storage.Store = FakeStore  # type: ignore[attr-defined]
    modules["homeassistant.helpers.storage"] = storage
    return modules, FakeStore


def _payload(room_id: str = "room_demo_entry", name: str = "Тамбур") -> dict[str, object]:
    return {
        "contract": {"name": "hausman-hub-room-lighting-config", "version": 1},
        "roomId": room_id,
        "name": name,
        "version": 1,
        "devices": {
            "sensors": [],
            "light_targets": [
                {
                    "id": "light_demo_a",
                    "name": "Свет A",
                    "kind": "switch",
                    "entityId": "switch.demo_a",
                    "role": "main",
                    "groupId": None,
                    "brightness": False,
                    "color_temperature": False,
                    "autoAdoptOverride": None,
                }
            ],
            "power_switch": None,
            "wireless_switches": [],
            "selectAll": False,
        },
        "schedule": [],
        "switchBindings": [],
        "dimming": {
            "enabled": False,
            "onAbsence": False,
            "fadeSeconds": 0,
            "targetPercent": 0,
        },
        "manualOffProtection": {
            "enabled": False,
            "minimumIntervalSeconds": 600,
            "releaseMode": "timer_only",
            "stableAbsenceSeconds": 30,
            "priority": "manual_above_auto",
        },
        "awayBehavior": {"mode": "none"},
        "autoAdopt": False,
        "updatedAt": 1,
        "overrides": {},
    }


class RoomLightingStorageTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.modules, self.fake_store = _fake_ha_storage_modules()
        self.fake_store.backing.clear()
        self.module_patch = patch.dict(sys.modules, self.modules)
        self.module_patch.start()
        self.hass = MagicMock()

    def tearDown(self) -> None:
        self.module_patch.stop()

    def make_store(self):
        from custom_components.hausman_hub.application.room_lighting_storage import (
            HomeAssistantRoomLightingStore,
        )

        return HomeAssistantRoomLightingStore(self.hass, "entry_1")

    async def test_empty_store_loads_empty(self) -> None:
        store = self.make_store()
        self.assertEqual((), await store.async_load_all())
        self.assertIsNone(await store.async_get("room_demo_entry"))

    async def test_save_and_reload_round_trip(self) -> None:
        store = self.make_store()
        config = config_from_payload(_payload())
        await store.async_upsert(config)

        restored = await self.make_store().async_get("room_demo_entry")
        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual("Тамбур", restored.name)
        self.assertEqual(config.to_dict(), restored.to_dict())
        self.assertEqual(
            ROOM_LIGHTING_STORAGE_VERSION,
            self.fake_store.backing["hausman_hub.room_lighting.entry_1"]["version"],
        )

    async def test_upsert_replaces_existing_room(self) -> None:
        store = self.make_store()
        await store.async_upsert(config_from_payload(_payload()))
        await store.async_upsert(config_from_payload(_payload(name="Новое имя")))

        all_configs = await store.async_load_all()
        self.assertEqual(1, len(all_configs))
        self.assertEqual("Новое имя", all_configs[0].name)

    async def test_delete_reports_existence(self) -> None:
        store = self.make_store()
        await store.async_upsert(config_from_payload(_payload()))

        self.assertTrue(await store.async_delete("room_demo_entry"))
        self.assertFalse(await store.async_delete("room_demo_entry"))
        self.assertEqual((), await store.async_load_all())

    async def test_damaged_payload_fails_closed(self) -> None:
        key = "hausman_hub.room_lighting.entry_1"
        store = self.make_store()
        self.fake_store.backing[key] = {"version": 1, "rooms": [{"bad": True}]}
        self.assertEqual((), await store.async_load_all())
        self.assertIsNone(await store.async_get("room_demo_entry"))

        self.fake_store.backing[key] = {"version": 1}
        self.assertEqual((), await store.async_load_all())

        self.fake_store.backing[key] = "not-an-object"  # type: ignore[assignment]
        self.assertEqual((), await store.async_load_all())

    async def test_pre_version_storage_migrates_to_empty(self) -> None:
        store = self.make_store()
        migrated = await store._store._async_migrate_func(0, 3, {"unsafe": True})
        self.assertEqual({"version": ROOM_LIGHTING_STORAGE_VERSION, "rooms": []}, migrated)


class RoomLightingServiceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.modules, self.fake_store = _fake_ha_storage_modules()
        self.fake_store.backing.clear()
        self.module_patch = patch.dict(sys.modules, self.modules)
        self.module_patch.start()
        self.hass = MagicMock()

        from custom_components.hausman_hub.application.room_lighting_storage import (
            HomeAssistantRoomLightingStore,
        )

        self.service = RoomLightingService(
            HomeAssistantRoomLightingStore(self.hass, "entry_1")
        )

    def tearDown(self) -> None:
        self.module_patch.stop()

    async def test_put_bumps_version_only_on_change(self) -> None:
        initial = config_from_payload(_payload(room_id="room_demo_entry"))
        stored = await self.service.async_put_config(initial)
        self.assertEqual(1, stored.version)

        changed = config_from_payload(_payload(room_id="room_demo_entry", name="Другое"))
        updated = await self.service.async_put_config(changed)
        self.assertEqual(2, updated.version)

        same = await self.service.async_put_config(changed)
        self.assertEqual(2, same.version)

    async def test_apply_template_builds_full_config(self) -> None:
        config = await self.service.async_apply_template(
            DEFAULT_TEMPLATE_ID,
            {"name": "Шаблон для тамбура"},
            room_id="room_demo_entry",
        )
        self.assertEqual("room_demo_entry", config.room_id)
        self.assertEqual("Шаблон для тамбура", config.name)
        self.assertEqual(DEFAULT_TEMPLATE_ID, config.template_id)
        self.assertTrue(config.schedule)
        self.assertEqual("room_off", config.away_behavior.mode.value)

    async def test_apply_template_keeps_room_auxiliary_policy_with_devices(self) -> None:
        room_id = "room_demo_bathroom"
        base = await self.service.async_apply_template(DEFAULT_TEMPLATE_ID, room_id=room_id)
        payload = base.to_dict()
        payload["devices"]["auxiliaries"] = [  # type: ignore[index]
            {
                "id": "aux_demo_fan",
                "name": "Вытяжка",
                "kind": "fan",
                "entityId": "switch.demo_fan",
                "autoAdoptOverride": None,
            }
        ]
        payload["auxiliary"] = {  # type: ignore[index]
            "fan": {
                "targetId": "aux_demo_fan",
                "humidityThreshold": 70,
                "dayOffSeconds": 900,
                "quietStart": "06:00",
                "dayStart": "08:00",
                "nightStart": "22:00",
            }
        }
        stored = await self.service.async_put_config(config_from_payload(payload))
        self.assertIsNotNone(stored.auxiliary)

        kept = await self.service.async_apply_template(
            DEFAULT_TEMPLATE_ID,
            {"name": "Шаблон ванной"},
            room_id=room_id,
            keep_devices=True,
        )
        self.assertIsNotNone(kept.auxiliary)
        self.assertEqual("aux_demo_fan", kept.auxiliary.fan.target_id)  # type: ignore[union-attr]
        self.assertEqual(70, kept.auxiliary.fan.humidity_threshold)  # type: ignore[union-attr]
        self.assertEqual(["aux_demo_fan"], [item.id for item in kept.devices.auxiliaries])

        replaced = await self.service.async_apply_template(
            DEFAULT_TEMPLATE_ID,
            {"name": "Шаблон без устройств"},
            room_id=room_id,
        )
        self.assertIsNone(replaced.auxiliary)
        self.assertEqual((), replaced.devices.auxiliaries)

    async def test_apply_template_keeps_real_devices_and_targets_only_main_role(self) -> None:
        from custom_components.hausman_hub.domain.room_lighting_engine import (
            _entry_targets_target,
        )

        payload = _payload(room_id="room_real_entry")
        main = payload["devices"]["light_targets"][0]
        main.update(id="chandelier", kind="light", entityId="light.real_main", brightness=True,
                    color_temperature=True)
        payload["devices"]["light_targets"].extend([
            {**main, "id": "spots", "entityId": "light.real_spots", "role": "accent",
             "brightness": False, "color_temperature": False},
            {**main, "id": "mirror", "entityId": "light.real_mirror", "role": "mirror",
             "brightness": False, "color_temperature": False},
        ])
        stored = await self.service.async_put_config(config_from_payload(payload))
        generation = self.service.configuration_generation
        draft = await self.service.async_apply_template(
            DEFAULT_TEMPLATE_ID, room_id=stored.room_id, keep_devices=True,
        )
        self.assertEqual(stored.devices, draft.devices)
        self.assertEqual(3, len(draft.schedule))
        for entry in draft.schedule:
            selected = [target.id for target in draft.devices.light_targets
                        if _entry_targets_target(entry, target)]
            self.assertEqual(["chandelier"], selected)
            self.assertNotIn("light_demo_main", entry.targets.light_targets)
        self.assertEqual(stored, await self.service.async_get_config(stored.room_id))
        self.assertEqual(generation, self.service.configuration_generation)
        self.assertFalse(self.hass.services.async_call.called)

    async def test_apply_template_missing_main_role_fails_without_changing_room(self) -> None:
        payload = _payload(room_id="room_accent_only")
        payload["devices"]["light_targets"][0].update(id="spots", role="accent")
        stored = await self.service.async_put_config(config_from_payload(payload))
        generation = self.service.configuration_generation
        with self.assertRaisesRegex(RoomLightingViolation, "unused role: main"):
            await self.service.async_apply_template(
                DEFAULT_TEMPLATE_ID, room_id=stored.room_id, keep_devices=True,
            )
        self.assertEqual(stored, await self.service.async_get_config(stored.room_id))
        self.assertEqual(generation, self.service.configuration_generation)

    async def test_operations_never_call_home_assistant_services(self) -> None:
        config = config_from_payload(_payload(room_id="room_demo_entry"))
        await self.service.async_put_config(config)
        await self.service.async_get_config("room_demo_entry")
        await self.service.async_list_configs()
        await self.service.async_apply_template(DEFAULT_TEMPLATE_ID)
        await self.service.async_delete_config("room_demo_entry")

        self.assertFalse(self.hass.services.async_call.called)


class _DelayedMutationStore:
    """In-memory store whose mutation can be observed before it completes."""

    def __init__(self, config=None, *, save_result: object = None) -> None:
        self.configs = {} if config is None else {config.room_id: config}
        self.save_result = save_result
        self.upsert_started = asyncio.Event()
        self.delete_started = asyncio.Event()
        self.release = asyncio.Event()

    async def async_get(self, room_id: str):
        return self.configs.get(room_id)

    async def async_load_all(self):
        return tuple(self.configs.values())

    async def async_upsert(self, config):
        self.upsert_started.set()
        await self.release.wait()
        if self.save_result is None:
            self.configs[config.room_id] = config
        return self.save_result

    async def async_delete(self, room_id: str) -> bool:
        self.delete_started.set()
        await self.release.wait()
        if self.save_result is not None:
            return False
        return self.configs.pop(room_id, None) is not None


class RoomLightingTopologyInvalidationTest(unittest.IsolatedAsyncioTestCase):
    """Configuration writes must close stale manual-attribution relations first."""

    def _config(self, name: str = "Тамбур"):
        return config_from_payload(_payload(name=name))

    @staticmethod
    def _authority(config):
        from custom_components.hausman_hub.application.manual_light_authority import (
            ManualLightAuthority,
        )
        from custom_components.hausman_hub.application.related_light_sources import (
            RelatedLightSources,
        )

        authority = ManualLightAuthority()
        authority.set_related_sources(RelatedLightSources.from_configs((config,)))
        return authority

    async def test_changed_put_invalidates_before_delayed_save(self) -> None:
        from custom_components.hausman_hub.application.room_lighting_ha_executor import (
            RoomLightingHaExecutor,
        )
        from custom_components.hausman_hub.application.scenario_executor import (
            ScenarioExecutor,
        )
        from custom_components.hausman_hub.application.scenarios import (
            ScenarioCatalog,
            ScenarioDeviceAction,
            ScenarioDeviceEntry,
        )
        from custom_components.hausman_hub.domain.room_lighting_engine import (
            LightAction,
            PlannedCommand,
        )
        from custom_components.hausman_hub.domain.scenarios import (
            ScenarioAction,
            ScenarioActionType,
        )

        initial = self._config()
        store = _DelayedMutationStore(initial)
        authority = self._authority(initial)
        service = RoomLightingService(
            store,
            topology_invalidator=authority.invalidate_related_sources,
        )

        pending = asyncio.create_task(
            service.async_put_config(self._config("Новый тамбур"))
        )
        await store.upsert_started.wait()

        self.assertEqual(
            "attribution_uncertain", authority.barrier_reason_for("switch.demo_a")
        )
        self.assertTrue(authority.blocks("switch.demo_a"))

        room_hass = SimpleNamespace(
            services=SimpleNamespace(async_call=AsyncMock()),
            states=SimpleNamespace(
                get=lambda _entity_id: SimpleNamespace(state="off", attributes={})
            ),
        )
        room_executor = RoomLightingHaExecutor(
            {target.id: target for target in initial.devices.light_targets},
            manual_light_authority=authority,
        )
        room_receipt = await room_executor.execute(
            room_hass,
            PlannedCommand("light_demo_a", LightAction.TURN_ON),
            automatic=True,
        )
        self.assertFalse(room_receipt.sent)
        room_hass.services.async_call.assert_not_awaited()

        scenario_hass = SimpleNamespace(
            services=SimpleNamespace(async_call=AsyncMock()),
            states=SimpleNamespace(
                get=lambda _entity_id: SimpleNamespace(state="off", attributes={})
            ),
        )
        scenario_device = ScenarioDeviceEntry(
            target_id="room_light",
            name="Свет тамбура",
            entity_id="switch.demo_a",
            actions=(
                ScenarioDeviceAction(
                    "turn_on", "Включить", "switch", "turn_on", frozenset()
                ),
            ),
        )
        catalog = ScenarioCatalog({"room_light": scenario_device}, {})

        async def run_callback(*_args: object, **_kwargs: object) -> dict[str, object]:
            return {}

        scenario_executor = ScenarioExecutor(
            scenario_hass,
            catalog,
            run_callback,
            manual_light_authority=authority,
        )
        scenario_receipt = await scenario_executor._device_action_receipt(
            ScenarioAction(
                id="automatic_room_light",
                type=ScenarioActionType.DEVICE_ACTION,
                target_id="room_light",
                action_id="turn_on",
            ),
            {"action_id": "automatic_room_light", "status": "pending"},
            automatic=True,
        )
        self.assertEqual("manual_light_priority", scenario_receipt.get("error"))
        scenario_hass.services.async_call.assert_not_awaited()
        store.release.set()
        await pending

    async def test_delete_existing_invalidates_before_delayed_delete(self) -> None:
        initial = self._config()
        store = _DelayedMutationStore(initial)
        authority = self._authority(initial)
        service = RoomLightingService(
            store,
            topology_invalidator=authority.invalidate_related_sources,
        )

        pending = asyncio.create_task(service.async_delete_config(initial.room_id))
        await store.delete_started.wait()

        self.assertEqual(
            "attribution_uncertain", authority.barrier_reason_for("switch.demo_a")
        )
        store.release.set()
        self.assertTrue(await pending)

    async def test_failed_or_cancelled_mutation_keeps_stale_graph_closed(self) -> None:
        from custom_components.hausman_hub.application.related_light_sources import (
            RelatedLightSources,
        )
        from custom_components.hausman_hub.application.room_lighting_service import (
            RoomLightingStorageError,
        )

        initial = self._config()
        failing_store = _DelayedMutationStore(initial, save_result=False)
        failing_authority = self._authority(initial)
        failing_service = RoomLightingService(
            failing_store,
            topology_invalidator=failing_authority.invalidate_related_sources,
        )
        failed = asyncio.create_task(
            failing_service.async_put_config(self._config("Не сохранится"))
        )
        await failing_store.upsert_started.wait()
        failing_authority.set_related_sources(
            RelatedLightSources.from_configs((initial,))
        )
        self.assertEqual(
            "attribution_uncertain",
            failing_authority.barrier_reason_for("switch.demo_a"),
        )
        failing_store.release.set()
        with self.assertRaises(RoomLightingStorageError):
            await failed
        self.assertEqual(
            "attribution_uncertain",
            failing_authority.barrier_reason_for("switch.demo_a"),
        )

        cancelled_store = _DelayedMutationStore(initial)
        cancelled_authority = self._authority(initial)
        cancelled_service = RoomLightingService(
            cancelled_store,
            topology_invalidator=cancelled_authority.invalidate_related_sources,
        )
        cancelled = asyncio.create_task(
            cancelled_service.async_delete_config(initial.room_id)
        )
        await cancelled_store.delete_started.wait()
        cancelled_authority.set_related_sources(
            RelatedLightSources.from_configs((initial,))
        )
        cancelled.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await cancelled
        self.assertEqual(
            "attribution_uncertain",
            cancelled_authority.barrier_reason_for("switch.demo_a"),
        )

    async def test_noop_put_missing_delete_and_template_build_do_not_invalidate(self) -> None:
        initial = self._config()
        store = _DelayedMutationStore(initial)
        calls = 0

        def invalidate() -> None:
            nonlocal calls
            calls += 1

        service = RoomLightingService(store, topology_invalidator=invalidate)
        same = await service.async_put_config(self._config())
        self.assertEqual(initial.version, same.version)
        self.assertFalse(await service.async_delete_config("room_missing"))
        template = await service.async_apply_template(DEFAULT_TEMPLATE_ID)

        self.assertEqual(DEFAULT_TEMPLATE_ID, template.template_id)
        self.assertEqual(0, calls)


if __name__ == "__main__":
    unittest.main()
