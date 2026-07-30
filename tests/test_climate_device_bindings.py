"""Safe explicit native binding tests for migrated climate devices."""

from __future__ import annotations

import unittest

from custom_components.hausman_hub.application.climate_device_bindings import (
    ClimateDeviceBindingViolation,
    apply_climate_device_bindings,
    climate_device_binding_options,
    preview_climate_device_bindings,
)
from custom_components.hausman_hub.application.climate_native_setup import (
    ClimateHaCatalogEntry,
    ClimateHaCatalogRoom,
    ClimateHaEntityCatalog,
)
from custom_components.hausman_hub.application.climate_runtime import ClimateRuntime
from custom_components.hausman_hub.domain.configuration import SafeConfiguration
from custom_components.hausman_hub.domain.climate import (
    ClimateCapability,
    ClimateControlOwner,
    ClimateControlScope,
    ClimateDevice,
    ClimateDeviceKind,
    ClimateRegistry,
    ClimateRoom,
)


def registry() -> ClimateRegistry:
    return ClimateRegistry(
        rooms=(
            ClimateRoom(room_id="living", name="Гостиная"),
            ClimateRoom(room_id="kids", name="Детская"),
        ),
        devices=(
            ClimateDevice(
                device_id="living_ac",
                name="Кондиционер гостиная",
                room_id="living",
                kind=ClimateDeviceKind.AIR_CONDITIONER,
                source_id="legacy-living-ac",
                control_scope=ClimateControlScope.MANAGED,
                control_owner=ClimateControlOwner.CLIMATE_CORE,
                capabilities=(
                    ClimateCapability.POWER,
                    ClimateCapability.TARGET_TEMPERATURE,
                ),
                endpoints=(),
            ),
            ClimateDevice(
                device_id="kids_temperature",
                name="Климат Kojima детская",
                room_id="kids",
                kind=ClimateDeviceKind.TEMPERATURE_SENSOR,
                source_id="legacy-kids-temperature",
                control_scope=ClimateControlScope.OBSERVED,
                control_owner=ClimateControlOwner.OBSERVED,
                capabilities=(),
                endpoints=(),
            ),
        ),
    )


def catalog(*, living_available: bool = True) -> ClimateHaEntityCatalog:
    return ClimateHaEntityCatalog(
        rooms=(
            ClimateHaCatalogRoom(room_id="living", name="Гостиная"),
            ClimateHaCatalogRoom(room_id="kids", name="Детская"),
        ),
        entries=(
            ClimateHaCatalogEntry(
                entity_id="climate.living_ac",
                domain="climate",
                state="cool",
                device_class=None,
                supported_features=1,
                friendly_name="Кондиционер гостиная",
                available=living_available,
                last_updated_ms=1,
                room_id="living",
                hvac_modes=("off", "cool", "heat"),
            ),
            ClimateHaCatalogEntry(
                entity_id="climate.kids_ac",
                domain="climate",
                state="off",
                device_class=None,
                supported_features=1,
                friendly_name="Кондиционер детская",
                available=True,
                last_updated_ms=1,
                room_id="kids",
                hvac_modes=("off", "cool", "heat"),
            ),
            ClimateHaCatalogEntry(
                entity_id="sensor.kids_temperature",
                domain="sensor",
                state="24.1",
                device_class="temperature",
                supported_features=0,
                friendly_name="Климат Kojima детская",
                available=True,
                last_updated_ms=1,
                room_id="kids",
            ),
        ),
    )


class ClimateDeviceBindingTest(unittest.TestCase):
    def test_options_are_explicit_room_aware_and_never_auto_select(self) -> None:
        options = climate_device_binding_options(registry(), catalog())

        self.assertEqual(
            {"device_count": 2, "bound_count": 0, "missing_count": 2, "candidate_count": 3},
            options["summary"],
        )
        living = options["rooms"][0]["devices"][0]
        self.assertIsNone(living["current_entity_id"])
        self.assertEqual(
            ["climate.living_ac", "climate.kids_ac"],
            [candidate["entity_id"] for candidate in living["candidates"]],
        )
        self.assertTrue(living["candidates"][0]["same_room"])
        self.assertFalse(living["candidates"][1]["same_room"])

    def test_snapshot_ignores_unrelated_switches(self) -> None:
        source = catalog()
        unrelated_switch = ClimateHaCatalogEntry(
            entity_id="switch.living_socket",
            domain="switch",
            state="on",
            device_class=None,
            supported_features=0,
            friendly_name="Розетка у дивана",
            available=False,
            last_updated_ms=1,
            room_id="living",
        )
        extended = ClimateHaEntityCatalog(
            rooms=source.rooms,
            entries=(*source.entries, unrelated_switch),
        )

        self.assertEqual(
            climate_device_binding_options(registry(), source)["snapshot_revision"],
            climate_device_binding_options(registry(), extended)["snapshot_revision"],
        )

    def test_floor_heating_switch_is_an_explicit_compatible_candidate(self) -> None:
        floor_registry = ClimateRegistry(
            rooms=(ClimateRoom(room_id="living", name="Гостиная"),),
            devices=(
                ClimateDevice(
                    device_id="living_floor",
                    name="Тёплый пол гостиной",
                    room_id="living",
                    kind=ClimateDeviceKind.FLOOR_HEATING,
                    source_id="legacy-living-floor",
                    control_scope=ClimateControlScope.MANAGED,
                    control_owner=ClimateControlOwner.CLIMATE_CORE,
                    capabilities=(
                        ClimateCapability.POWER,
                        ClimateCapability.TARGET_TEMPERATURE,
                    ),
                    endpoints=(),
                ),
            ),
        )
        floor_catalog = ClimateHaEntityCatalog(
            rooms=(ClimateHaCatalogRoom(room_id="living", name="Гостиная"),),
            entries=(
                ClimateHaCatalogEntry(
                    entity_id="switch.living_floor",
                    domain="switch",
                    state="off",
                    device_class=None,
                    supported_features=0,
                    friendly_name="Тёплый пол гостиной",
                    available=True,
                    last_updated_ms=1,
                    room_id="living",
                ),
            ),
        )

        options = climate_device_binding_options(floor_registry, floor_catalog)

        self.assertEqual(
            ["switch.living_floor"],
            [
                candidate["entity_id"]
                for candidate in options["rooms"][0]["devices"][0]["candidates"]
            ],
        )

    def test_preview_blocks_cross_room_and_unavailable_bindings(self) -> None:
        options = climate_device_binding_options(registry(), catalog())
        preview = preview_climate_device_bindings(
            registry(),
            catalog(),
            {
                "snapshot_revision": options["snapshot_revision"],
                "bindings": [
                    {"device_id": "living_ac", "entity_id": "climate.kids_ac"}
                ],
            },
        )
        self.assertFalse(preview["save_allowed"])
        self.assertEqual("room_mismatch", preview["issues"][0]["code"])
        self.assertFalse(preview["commands_sent"])

        unavailable_options = climate_device_binding_options(
            registry(), catalog(living_available=False)
        )
        unavailable = preview_climate_device_bindings(
            registry(),
            catalog(living_available=False),
            {
                "snapshot_revision": unavailable_options["snapshot_revision"],
                "bindings": [
                    {"device_id": "living_ac", "entity_id": "climate.living_ac"}
                ],
            },
        )
        self.assertEqual("entity_unavailable", unavailable["issues"][0]["code"])

    def test_apply_requires_exact_preview_and_adds_typed_endpoints(self) -> None:
        source = registry()
        source_catalog = catalog()
        options = climate_device_binding_options(source, source_catalog)
        request = {
            "snapshot_revision": options["snapshot_revision"],
            "bindings": [
                {"device_id": "living_ac", "entity_id": "climate.living_ac"},
                {
                    "device_id": "kids_temperature",
                    "entity_id": "sensor.kids_temperature",
                },
            ],
        }
        preview = preview_climate_device_bindings(source, source_catalog, request)
        updated, receipt = apply_climate_device_bindings(
            source,
            source_catalog,
            {**request, "preview_revision": preview["preview_revision"]},
        )

        self.assertTrue(preview["save_allowed"])
        self.assertEqual("climate.living_ac", updated.device("living_ac").endpoints[0].entity_id)
        self.assertEqual("control", updated.device("living_ac").endpoints[0].role.value)
        self.assertEqual(
            "temperature", updated.device("kids_temperature").endpoints[0].role.value
        )
        self.assertEqual(2, receipt["updated_devices"])
        self.assertFalse(receipt["commands_sent"])

        with self.assertRaisesRegex(ClimateDeviceBindingViolation, "preview changed"):
            apply_climate_device_bindings(
                source,
                source_catalog,
                {**request, "preview_revision": preview["preview_revision"] + 1},
            )


class _MemoryRegistryStore:
    def __init__(self, value: ClimateRegistry) -> None:
        self.value = value
        self.saved: list[ClimateRegistry] = []

    async def async_load(self) -> ClimateRegistry:
        return self.value

    async def async_save(self, value: ClimateRegistry) -> None:
        self.value = value
        self.saved.append(value)


class _BindingStateView:
    def __init__(self, value: ClimateHaEntityCatalog) -> None:
        self.value = value

    def binding_entity_catalog(self) -> ClimateHaEntityCatalog:
        return self.value


class ClimateDeviceBindingRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_saves_only_after_preview_and_without_executor(self) -> None:
        store = _MemoryRegistryStore(registry())
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=SafeConfiguration(mode="read-only"),
            registry_store=store,
            ha_state_view=_BindingStateView(catalog()),
        )
        await runtime.async_start()
        options = await runtime.async_climate_device_binding_options()
        request = {
            "snapshot_revision": options["snapshot_revision"],
            "bindings": [
                {"device_id": "living_ac", "entity_id": "climate.living_ac"}
            ],
        }

        checked = await runtime.async_preview_climate_device_bindings(request)
        self.assertEqual([], store.saved)
        receipt = await runtime.async_save_climate_device_bindings(
            {**request, "preview_revision": checked["preview_revision"]}
        )

        self.assertEqual(1, len(store.saved))
        self.assertEqual("saved", receipt["status"])
        self.assertFalse(receipt["commands_sent"])
        self.assertEqual(
            "climate.living_ac",
            store.value.device("living_ac").endpoints[0].entity_id,
        )

    def test_rejects_duplicate_entity_binding(self) -> None:
        options = climate_device_binding_options(registry(), catalog())
        with self.assertRaisesRegex(ClimateDeviceBindingViolation, "entity binding is repeated"):
            preview_climate_device_bindings(
                registry(),
                catalog(),
                {
                    "snapshot_revision": options["snapshot_revision"],
                    "bindings": [
                        {"device_id": "living_ac", "entity_id": "climate.living_ac"},
                        {"device_id": "kids_temperature", "entity_id": "climate.living_ac"},
                    ],
                },
            )
