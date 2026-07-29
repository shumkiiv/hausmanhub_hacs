"""Atomic Home Assistant area assignment application and adapter tests."""

from __future__ import annotations

import asyncio
from types import ModuleType, SimpleNamespace
from unittest.mock import patch
import sys
import unittest

from custom_components.hausman_hub.application.climate_area_assignment import (
    ClimateAreaAssignmentTarget,
    ClimateAreaAssignmentViolation,
    climate_area_assignment_targets,
)
from custom_components.hausman_hub.application.climate_discovery import (
    ClimateImportSnapshot,
    ImportedClimateDevice,
    ImportedClimateRoom,
)
from custom_components.hausman_hub.application.climate_setup import climate_setup_options
from custom_components.hausman_hub.domain.climate import ClimateDeviceKind, ClimateRegistry
from custom_components.hausman_hub.ha_area_assignment import (
    HomeAssistantAreaAssignmentService,
)


def _snapshot() -> ClimateImportSnapshot:
    room = ImportedClimateRoom(
        room_id="kids",
        name="Детская",
        temperature=22.0,
        humidity=45.0,
        target_temperature=None,
        target_humidity=None,
        target_strategy=None,
        mode="auto",
        authority_eligible=True,
    )
    return ClimateImportSnapshot(
        generated_at=100,
        runtime_fresh=True,
        rooms=(room,),
        devices=(
            ImportedClimateDevice(
                source_id="sensor.device_temperature",
                name="Климат детская Температура",
                room_id="",
                domain="sensor",
                category="temperature",
                state="22",
                available=True,
                command_types=(),
                suggested_kinds=(ClimateDeviceKind.TEMPERATURE_SENSOR,),
                device_group_id="physical-climate",
            ),
            ImportedClimateDevice(
                source_id="sensor.device_humidity",
                name="Климат детская Влажность",
                room_id="",
                domain="sensor",
                category="humidity",
                state="45",
                available=True,
                command_types=(),
                suggested_kinds=(ClimateDeviceKind.HUMIDITY_SENSOR,),
                device_group_id="physical-climate",
            ),
            ImportedClimateDevice(
                source_id="sensor.entity_only",
                name="Отдельный датчик",
                room_id="",
                domain="sensor",
                category="temperature",
                state="21",
                available=True,
                command_types=(),
                suggested_kinds=(ClimateDeviceKind.TEMPERATURE_SENSOR,),
            ),
        ),
    )


class ClimateAreaAssignmentValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ClimateRegistry()
        self.snapshot = _snapshot()
        options = climate_setup_options(self.registry, self.snapshot)
        self.revision = options["snapshot_revision"]
        self.by_source = {
            device["name"]: device["candidate_id"] for device in options["devices"]
        }

    def test_resolves_one_physical_group_without_exposing_registry_ids(self) -> None:
        targets = climate_area_assignment_targets(
            self.registry,
            self.snapshot,
            {
                "snapshot_revision": self.revision,
                "assignments": [
                    {
                        "candidate_ids": [
                            self.by_source["Климат детская Температура"],
                            self.by_source["Климат детская Влажность"],
                        ],
                        "room_id": "kids",
                    }
                ],
            },
        )
        self.assertEqual(
            (
                ClimateAreaAssignmentTarget(
                    room_id="kids",
                    entity_ids=(
                        "sensor.device_temperature",
                        "sensor.device_humidity",
                    ),
                ),
            ),
            targets,
        )

    def test_rejects_stale_revision_before_resolution(self) -> None:
        with self.assertRaises(ClimateAreaAssignmentViolation) as caught:
            climate_area_assignment_targets(
                self.registry,
                self.snapshot,
                {"snapshot_revision": self.revision + 1, "assignments": []},
            )
        self.assertEqual("snapshot_changed", caught.exception.code)

    def test_rejects_boolean_revision(self) -> None:
        with self.assertRaises(ClimateAreaAssignmentViolation):
            climate_area_assignment_targets(
                self.registry,
                self.snapshot,
                {"snapshot_revision": True, "assignments": []},
            )

    def test_rejects_mixed_physical_groups(self) -> None:
        with self.assertRaises(ClimateAreaAssignmentViolation):
            climate_area_assignment_targets(
                self.registry,
                self.snapshot,
                {
                    "snapshot_revision": self.revision,
                    "assignments": [
                        {
                            "candidate_ids": [
                                self.by_source["Климат детская Температура"],
                                self.by_source["Отдельный датчик"],
                            ],
                            "room_id": "kids",
                        }
                    ],
                },
            )


class _Registry:
    def __init__(self, collection: str, entries: list[object]) -> None:
        setattr(
            self,
            collection,
            {
                (getattr(entry, "id", None) or getattr(entry, "entity_id")): entry
                for entry in entries
            },
        )
        self.collection = collection
        self.calls: list[tuple[str, str | None]] = []
        self.fail_on: str | None = None

    def async_list_areas(self):
        return list(getattr(self, self.collection).values())

    def async_update_device(self, item_id: str, *, area_id: str | None):
        self._update(item_id, area_id)

    def async_update_entity(self, item_id: str, *, area_id: str | None):
        self._update(item_id, area_id)

    def _update(self, item_id: str, area_id: str | None) -> None:
        self.calls.append((item_id, area_id))
        if self.fail_on == item_id:
            self.fail_on = None
            raise RuntimeError("synthetic registry failure")
        entry = next(
            item
            for item in getattr(self, self.collection).values()
            if (
                getattr(item, "entity_id", None)
                if self.collection == "entities"
                else getattr(item, "id", None)
            ) == item_id
        )
        entry.area_id = area_id


def _fake_registry_modules(hass: object) -> dict[str, ModuleType]:
    modules: dict[str, ModuleType] = {}
    for kind in ("area", "device", "entity"):
        module = ModuleType(f"homeassistant.helpers.{kind}_registry")
        module.async_get = lambda value, name=kind: getattr(value, f"{name}_registry")  # type: ignore[attr-defined]
        modules[module.__name__] = module
    return modules


class HomeAssistantAreaAssignmentServiceTests(unittest.TestCase):
    def _hass(self):
        areas = _Registry("areas", [SimpleNamespace(id="kids", name="Детская")])
        devices = _Registry("devices", [SimpleNamespace(id="dev1", area_id=None)])
        entities = _Registry(
            "entities",
            [
                SimpleNamespace(id="one", entity_id="sensor.one", device_id="dev1", area_id="old"),
                SimpleNamespace(id="two", entity_id="sensor.two", device_id="dev1", area_id="override"),
                SimpleNamespace(id="solo", entity_id="sensor.solo", device_id=None, area_id=None),
            ],
        )
        return SimpleNamespace(
            area_registry=areas,
            device_registry=devices,
            entity_registry=entities,
        )

    def test_updates_physical_device_clears_overrides_and_updates_entity_only(self) -> None:
        hass = self._hass()
        with patch.dict(sys.modules, _fake_registry_modules(hass)):
            receipt = asyncio.run(HomeAssistantAreaAssignmentService(hass).async_assign((
                ClimateAreaAssignmentTarget("kids", ("sensor.one",)),
                ClimateAreaAssignmentTarget("kids", ("sensor.solo",)),
            )))
        self.assertEqual("kids", hass.device_registry.devices["dev1"].area_id)
        self.assertIsNone(hass.entity_registry.entities["one"].area_id)
        self.assertIsNone(hass.entity_registry.entities["two"].area_id)
        self.assertEqual("kids", hass.entity_registry.entities["solo"].area_id)
        self.assertEqual(1, receipt["updated_devices"])
        self.assertEqual(1, receipt["updated_entities"])

    def test_rolls_back_every_change_when_registry_update_fails(self) -> None:
        hass = self._hass()
        hass.entity_registry.fail_on = "sensor.solo"
        with patch.dict(sys.modules, _fake_registry_modules(hass)):
            with self.assertRaises(ClimateAreaAssignmentViolation):
                asyncio.run(HomeAssistantAreaAssignmentService(hass).async_assign((
                    ClimateAreaAssignmentTarget("kids", ("sensor.one",)),
                    ClimateAreaAssignmentTarget("kids", ("sensor.solo",)),
                )))
        self.assertIsNone(hass.device_registry.devices["dev1"].area_id)
        self.assertEqual("old", hass.entity_registry.entities["one"].area_id)
        self.assertEqual("override", hass.entity_registry.entities["two"].area_id)
        self.assertIsNone(hass.entity_registry.entities["solo"].area_id)


if __name__ == "__main__":
    unittest.main()
