"""Confirmed, command-free migration of retired Node-RED settings."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from custom_components.hausman_hub.application.climate_runtime import ClimateRuntime
from custom_components.hausman_hub.application.legacy_settings_apply import (
    LegacySettingsApplyViolation,
    build_legacy_settings_apply,
)
from custom_components.hausman_hub.application.legacy_settings_import import (
    preview_legacy_settings,
)
from custom_components.hausman_hub.application.settings_service import (
    HausmanHubSettingsService,
)
from custom_components.hausman_hub.application.contours import CLIMATE_CONTOUR_ID
from custom_components.hausman_hub.domain.climate_bridge import ClimateControlMode
from custom_components.hausman_hub.domain.configuration import SafeConfiguration
from custom_components.hausman_hub.domain.contours import (
    ClimateComfortSettings,
    ClimateContourRoom,
    ClimateProfile,
    ClimateSchedule,
    ClimateStrategy,
    ClimateTemporaryOverride,
    ContourDefinition,
    ContourEngine,
    ContourKind,
    ContourMode,
    ContourRegistry,
)
from custom_components.hausman_hub.domain.hub_settings import HausmanHubSettings


ROOT = Path(__file__).resolve().parents[1]
EXPORT_FIXTURE = ROOT / "fixtures" / "hausmanhub_legacy_settings_v1" / "request.json"


def legacy_export() -> dict[str, object]:
    return json.loads(EXPORT_FIXTURE.read_text(encoding="utf-8"))


def contour_registry() -> ContourRegistry:
    day = ClimateComfortSettings(23.0, 40, ClimateStrategy.SOFT)
    night = ClimateComfortSettings(22.0, 42, ClimateStrategy.NORMAL)
    room = ClimateContourRoom(
        room_id="living",
        device_ids=("living_ac",),
        day_profile=day,
        night_profile=night,
        active_profile=ClimateProfile.NIGHT,
        temporary_override=ClimateTemporaryOverride(24.0),
    )
    return ContourRegistry(
        contours=(
            ContourDefinition(
                contour_id=CLIMATE_CONTOUR_ID,
                name="Климат",
                kind=ContourKind.CLIMATE,
                mode=ContourMode.AUTOMATIC,
                engine=ContourEngine.EXISTING_CLIMATE_CORE,
                rooms=(room,),
                schedule=ClimateSchedule(enabled=True),
            ),
        )
    )


def apply_request(export: dict[str, object] | None = None) -> dict[str, object]:
    supplied = legacy_export() if export is None else export
    return {
        "contract": {
            "name": "hausman-hub-legacy-settings-apply",
            "version": 1,
        },
        "preview_id": preview_legacy_settings(supplied)["preview_id"],
        "confirm": True,
        "export": supplied,
        "room_mappings": [
            {"legacy_room_id": "living", "room_id": "living"},
        ],
    }


class MemoryContourStore:
    def __init__(self, value: ContourRegistry) -> None:
        self.value = value
        self.saved: list[ContourRegistry] = []

    async def async_save(self, value: ContourRegistry) -> None:
        self.value = value
        self.saved.append(value)


class MemorySettingsStore:
    def __init__(self, value: HausmanHubSettings, *, fail: bool = False) -> None:
        self.value = value
        self.fail = fail
        self.saved: list[HausmanHubSettings] = []

    async def async_load(self) -> HausmanHubSettings:
        return self.value

    async def async_save(self, value: HausmanHubSettings) -> None:
        if self.fail:
            raise OSError("synthetic settings persistence failure")
        self.value = value
        self.saved.append(value)


class LegacySettingsApplyTest(unittest.TestCase):
    def test_builds_complete_native_models_without_echoing_sensitive_values(self) -> None:
        payload = apply_request()
        payload["export"]["globals"]["private_password"] = "never-echo-this"  # type: ignore[index]
        payload["preview_id"] = preview_legacy_settings(payload["export"])["preview_id"]

        plan = build_legacy_settings_apply(
            payload,
            current_settings=HausmanHubSettings(),
            current_contours=contour_registry(),
        )

        self.assertEqual(("light.living_ceiling",), plan.settings.light_on_entities)
        room = plan.contours.contour(CLIMATE_CONTOUR_ID).rooms[0]  # type: ignore[union-attr]
        self.assertEqual((25.0, 45), (room.day_profile.target_temperature, room.day_profile.target_humidity))
        self.assertEqual((25.0, 45), (room.night_profile.target_temperature, room.night_profile.target_humidity))
        self.assertIsNone(room.temporary_override)
        self.assertEqual(["living"], plan.receipt["climate_rooms_updated"])
        self.assertFalse(plan.receipt["physical_commands_sent"])
        self.assertNotIn("never-echo-this", json.dumps(plan.receipt, sort_keys=True))

    def test_revalidates_preview_confirmation_contract_and_room_mapping(self) -> None:
        cases: list[tuple[str, dict[str, object]]] = []
        stale = apply_request()
        stale["preview_id"] = "0123456789abcdef"
        cases.append(("preview_changed", stale))
        unconfirmed = apply_request()
        unconfirmed["confirm"] = False
        cases.append(("invalid_request", unconfirmed))
        boolean_version = apply_request()
        boolean_version["contract"]["version"] = True  # type: ignore[index]
        cases.append(("invalid_request", boolean_version))
        unmapped = apply_request()
        unmapped["room_mappings"] = []
        cases.append(("invalid_request", unmapped))
        unknown_native = apply_request()
        unknown_native["room_mappings"] = [
            {"legacy_room_id": "living", "room_id": "unknown"}
        ]
        cases.append(("invalid_request", unknown_native))

        for expected_code, payload in cases:
            with self.subTest(expected_code=expected_code):
                with self.assertRaises(LegacySettingsApplyViolation) as raised:
                    build_legacy_settings_apply(
                        payload,
                        current_settings=HausmanHubSettings(),
                        current_contours=contour_registry(),
                    )
                self.assertEqual(expected_code, raised.exception.code)


class LegacySettingsApplyRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def _runtime(
        self,
        contours: ContourRegistry,
        contour_store: MemoryContourStore,
    ) -> ClimateRuntime:
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=SafeConfiguration(
                mode="shadow",
                climate_bridge_mode=ClimateControlMode.MANAGED,
                climate_bridge_target=None,
                climate_canary_room_id=None,
            ),
            registry_store=None,
            contour_store=contour_store,
        )
        runtime._contours = contours
        return runtime

    async def test_persists_both_models_and_sends_no_physical_command(self) -> None:
        original_contours = contour_registry()
        contour_store = MemoryContourStore(original_contours)
        settings_store = MemorySettingsStore(HausmanHubSettings())
        settings_service = HausmanHubSettingsService("entry", settings_store)
        await settings_service.async_load()
        runtime = await self._runtime(original_contours, contour_store)

        receipt = await runtime.async_apply_legacy_settings(
            apply_request(),
            settings_service,
        )

        self.assertEqual(1, len(contour_store.saved))
        self.assertEqual(1, len(settings_store.saved))
        self.assertFalse(receipt["physical_commands_sent"])

    async def test_settings_failure_rolls_contours_back_and_keeps_memory(self) -> None:
        original_contours = contour_registry()
        original_settings = HausmanHubSettings()
        contour_store = MemoryContourStore(original_contours)
        settings_store = MemorySettingsStore(original_settings, fail=True)
        settings_service = HausmanHubSettingsService("entry", settings_store)
        await settings_service.async_load()
        runtime = await self._runtime(original_contours, contour_store)

        with self.assertRaises(OSError):
            await runtime.async_apply_legacy_settings(
                apply_request(),
                settings_service,
            )

        self.assertEqual(2, len(contour_store.saved))
        self.assertEqual(original_contours, contour_store.value)
        self.assertEqual(original_contours, runtime._contours)
        self.assertEqual(original_settings, settings_service.current)


if __name__ == "__main__":
    unittest.main()
