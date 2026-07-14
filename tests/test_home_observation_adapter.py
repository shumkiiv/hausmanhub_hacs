"""Isolated tests for the Home Assistant aggregate-summary adapter."""

from __future__ import annotations

from dataclasses import asdict
import importlib
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
HOME_OBSERVATION_MODULE = "custom_components.hausman_hub.home_observation"
FAKE_MODULE_NAMES = (
    "homeassistant",
    "homeassistant.const",
    "homeassistant.core",
    "homeassistant.helpers",
    "homeassistant.helpers.area_registry",
    "homeassistant.helpers.device_registry",
    "homeassistant.helpers.entity_registry",
)


class FakeStates:
    """Return synthetic local states without supporting any mutation."""

    def __init__(self, values: dict[str, SimpleNamespace]) -> None:
        self.values = values

    def get(self, entity_id: str) -> SimpleNamespace | None:
        return self.values.get(entity_id)


class FakeHomeAssistant:
    """Minimal read-only shape required by the aggregate adapter."""

    def __init__(self) -> None:
        self.area_registry = SimpleNamespace(areas={"synthetic-area": object()})
        self.device_registry = SimpleNamespace(
            devices={"synthetic-device-one": object(), "synthetic-device-two": object()}
        )
        self.entity_registry = SimpleNamespace(
            entities={
                "synthetic-one": SimpleNamespace(
                    domain="sensor",
                    entity_id="sensor.synthetic_private_temperature",
                ),
                "synthetic-two": SimpleNamespace(
                    domain="switch",
                    entity_id="switch.synthetic_private_light",
                ),
                "synthetic-three": SimpleNamespace(
                    domain="sensor",
                    entity_id="sensor.synthetic_private_air",
                ),
                "synthetic-four": SimpleNamespace(
                    domain="light",
                    entity_id="light.synthetic_private_lamp",
                ),
            }
        )
        self.states = FakeStates(
            {
                "sensor.synthetic_private_temperature": SimpleNamespace(state="21.5"),
                "switch.synthetic_private_light": SimpleNamespace(state="unavailable"),
                "sensor.synthetic_private_air": SimpleNamespace(state="unknown"),
            }
        )


def fake_home_assistant_modules() -> dict[str, ModuleType]:
    """Build only the Home Assistant imports used by the outer adapter."""

    homeassistant = ModuleType("homeassistant")
    const = ModuleType("homeassistant.const")
    const.STATE_UNAVAILABLE = "unavailable"  # type: ignore[attr-defined]
    const.STATE_UNKNOWN = "unknown"  # type: ignore[attr-defined]

    core = ModuleType("homeassistant.core")
    core.HomeAssistant = FakeHomeAssistant  # type: ignore[attr-defined]

    helpers = ModuleType("homeassistant.helpers")
    area_registry = ModuleType("homeassistant.helpers.area_registry")
    area_registry.async_get = lambda hass: hass.area_registry  # type: ignore[attr-defined]
    device_registry = ModuleType("homeassistant.helpers.device_registry")
    device_registry.async_get = lambda hass: hass.device_registry  # type: ignore[attr-defined]
    entity_registry = ModuleType("homeassistant.helpers.entity_registry")
    entity_registry.async_get = lambda hass: hass.entity_registry  # type: ignore[attr-defined]

    homeassistant.const = const  # type: ignore[attr-defined]
    homeassistant.core = core  # type: ignore[attr-defined]
    homeassistant.helpers = helpers  # type: ignore[attr-defined]
    helpers.area_registry = area_registry  # type: ignore[attr-defined]
    helpers.device_registry = device_registry  # type: ignore[attr-defined]
    helpers.entity_registry = entity_registry  # type: ignore[attr-defined]

    return {
        "homeassistant": homeassistant,
        "homeassistant.const": const,
        "homeassistant.core": core,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.area_registry": area_registry,
        "homeassistant.helpers.device_registry": device_registry,
        "homeassistant.helpers.entity_registry": entity_registry,
    }


class HomeObservationAdapterTest(unittest.TestCase):
    """Prove that the outer adapter emits totals, never synthetic details."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.original_sys_path = sys.path[:]
        sys.path.insert(0, str(ROOT))
        cls.previous_modules = {
            name: sys.modules.get(name) for name in (*FAKE_MODULE_NAMES, HOME_OBSERVATION_MODULE)
        }
        for name in (*FAKE_MODULE_NAMES, HOME_OBSERVATION_MODULE):
            sys.modules.pop(name, None)
        sys.modules.update(fake_home_assistant_modules())
        cls.adapter = importlib.import_module(HOME_OBSERVATION_MODULE)

    @classmethod
    def tearDownClass(cls) -> None:
        for name in (*FAKE_MODULE_NAMES, HOME_OBSERVATION_MODULE):
            sys.modules.pop(name, None)
        sys.modules.update(
            {name: module for name, module in cls.previous_modules.items() if module is not None}
        )
        sys.path[:] = cls.original_sys_path

    def test_adapter_reduces_synthetic_identifiers_and_readings_to_counts(self) -> None:
        """The adapter immediately discards source identifiers and state values."""

        summary = self.adapter.collect_home_summary(FakeHomeAssistant())
        serialized = json.dumps(asdict(summary))

        self.assertEqual(1, summary.areas_count)
        self.assertEqual(2, summary.devices_count)
        self.assertEqual(4, summary.entities_count)
        self.assertEqual(2, summary.sensors_count)
        self.assertEqual(1, summary.available_entities_count)
        self.assertEqual(1, summary.unavailable_entities_count)
        self.assertEqual(1, summary.unknown_entities_count)
        self.assertEqual(1, summary.not_reported_entities_count)
        for forbidden_value in (
            "synthetic_private_temperature",
            "synthetic_private_light",
            "synthetic_private_air",
            "synthetic_private_lamp",
            "21.5",
        ):
            self.assertNotIn(forbidden_value, serialized)

    def test_adapter_treats_an_empty_state_value_as_unknown(self) -> None:
        """A state object without a value must not be reported as available."""

        hass = FakeHomeAssistant()
        hass.states.values["light.synthetic_private_lamp"] = SimpleNamespace(state=None)

        summary = self.adapter.collect_home_summary(hass)

        self.assertEqual(2, summary.unknown_entities_count)
        self.assertEqual(0, summary.not_reported_entities_count)

    def test_adapter_handles_empty_registries(self) -> None:
        """An empty Home Assistant inventory is a valid all-zero summary."""

        hass = FakeHomeAssistant()
        hass.area_registry.areas.clear()
        hass.device_registry.devices.clear()
        hass.entity_registry.entities.clear()
        hass.states.values.clear()

        summary = self.adapter.collect_home_summary(hass)

        self.assertEqual(
            {
                "areas_count": 0,
                "devices_count": 0,
                "entities_count": 0,
                "sensors_count": 0,
                "available_entities_count": 0,
                "unavailable_entities_count": 0,
                "unknown_entities_count": 0,
                "not_reported_entities_count": 0,
            },
            asdict(summary),
        )


if __name__ == "__main__":
    unittest.main()
