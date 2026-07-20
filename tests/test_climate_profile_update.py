"""Strict, command-free editing of saved day/night climate profiles."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

from custom_components.hausman_hub.application.climate_runtime import (
    ClimateRuntime,
    ClimateRuntimeUnavailable,
)
from custom_components.hausman_hub.application.climate_setup import (
    ClimateSetupViolation,
    climate_setup_revision,
    update_climate_profiles,
)
from custom_components.hausman_hub.application.contours import (
    contour_registry_to_payload,
)
from custom_components.hausman_hub.domain.climate_bridge import ClimateBridgeMode
from tests.test_climate_runtime import (
    MemoryBridge,
    MemoryContourStore,
    MemoryStore,
    configuration,
)
from tests.test_climate_setup_current import configured_setup


ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "custom_components" / "hausman_hub" / "contracts" / "v1"


def request_for(registry: object, contours: object) -> dict[str, object]:
    """Build one exact request from the currently opened setup."""

    return {
        "contract": {
            "name": "hausman-hub-climate-profile-update-request",
            "version": 1,
        },
        "setup_revision": climate_setup_revision(registry, contours),  # type: ignore[arg-type]
        "rooms": [
            {
                "room_id": "living",
                "profiles": {
                    "day": {
                        "target_temperature": 24.5,
                        "target_humidity": 50,
                        "strategy": "soft",
                    },
                    "night": {
                        "target_temperature": 21.5,
                        "target_humidity": 45,
                        "strategy": "normal",
                    },
                },
            },
            {
                "room_id": "kids",
                "profiles": {
                    "day": {
                        "target_temperature": 23.5,
                        "target_humidity": 55,
                        "strategy": "normal",
                    },
                    "night": {
                        "target_temperature": 20.5,
                        "target_humidity": 50,
                        "strategy": "aggressive",
                    },
                },
            },
        ],
    }


class ClimateProfileUpdateTest(unittest.IsolatedAsyncioTestCase):
    """Profile edits preserve every unrelated saved climate setting."""

    def test_exact_update_preserves_bindings_active_profile_and_override(self) -> None:
        registry, contours, _ = configured_setup()
        before = contour_registry_to_payload(contours)  # type: ignore[arg-type]
        request = request_for(registry, contours)

        updated, receipt = update_climate_profiles(
            registry,  # type: ignore[arg-type]
            contours,  # type: ignore[arg-type]
            request,
            saved_at=1784512800000,
            automatic_application_enabled=True,
        )

        request_schema = json.loads(
            (CONTRACTS / "climate-profile-update-request.schema.json").read_text(
                encoding="utf-8"
            )
        )
        receipt_schema = json.loads(
            (CONTRACTS / "climate-profile-update.schema.json").read_text(
                encoding="utf-8"
            )
        )
        Draft202012Validator(request_schema).validate(request)
        Draft202012Validator(receipt_schema).validate(receipt)
        self.assertEqual("saved", receipt["status"])
        self.assertFalse(receipt["commands_sent"])
        self.assertTrue(receipt["schedule_enabled"])
        self.assertTrue(receipt["automatic_application_pending"])
        self.assertNotEqual(request["setup_revision"], receipt["setup_revision"])

        old_contour = contours.contour("climate")  # type: ignore[union-attr]
        new_contour = updated.contour("climate")
        self.assertIsNotNone(old_contour)
        self.assertIsNotNone(new_contour)
        self.assertEqual(old_contour.mode, new_contour.mode)  # type: ignore[union-attr]
        self.assertEqual(old_contour.name, new_contour.name)  # type: ignore[union-attr]
        self.assertEqual(
            [room.device_ids for room in old_contour.rooms],  # type: ignore[union-attr]
            [room.device_ids for room in new_contour.rooms],  # type: ignore[union-attr]
        )
        self.assertEqual(
            [room.active_profile for room in old_contour.rooms],  # type: ignore[union-attr]
            [room.active_profile for room in new_contour.rooms],  # type: ignore[union-attr]
        )
        self.assertEqual(
            old_contour.rooms[1].temporary_override,  # type: ignore[union-attr]
            new_contour.rooms[1].temporary_override,  # type: ignore[union-attr]
        )
        self.assertIsNone(new_contour.schedule.last_applied_profile)  # type: ignore[union-attr]
        self.assertEqual(before, contour_registry_to_payload(contours))  # type: ignore[arg-type]
        serialized = json.dumps(receipt, ensure_ascii=True, sort_keys=True)
        self.assertNotIn("source_id", serialized)
        self.assertNotIn("synthetic-ac-source-living", serialized)

    def test_stale_partial_duplicate_and_invalid_requests_are_rejected(self) -> None:
        registry, contours, _ = configured_setup()
        original = contour_registry_to_payload(contours)  # type: ignore[arg-type]
        valid = request_for(registry, contours)
        cases: list[tuple[str, dict[str, object], str | None]] = []

        stale = copy.deepcopy(valid)
        stale["setup_revision"] = int(stale["setup_revision"]) + 1
        cases.append(("stale", stale, "setup_changed"))
        partial = copy.deepcopy(valid)
        partial["rooms"] = partial["rooms"][:1]  # type: ignore[index]
        cases.append(("partial", partial, None))
        duplicate = copy.deepcopy(valid)
        duplicate["rooms"][1] = duplicate["rooms"][0]  # type: ignore[index]
        cases.append(("duplicate", duplicate, None))
        invalid_step = copy.deepcopy(valid)
        invalid_step["rooms"][0]["profiles"]["day"][  # type: ignore[index]
            "target_temperature"
        ] = 24.3
        cases.append(("invalid_step", invalid_step, None))
        extra = copy.deepcopy(valid)
        extra["rooms"][0]["profiles"]["extra"] = {}  # type: ignore[index]
        cases.append(("extra", extra, None))

        for name, payload, code in cases:
            with self.subTest(name=name):
                with self.assertRaises(ClimateSetupViolation) as raised:
                    update_climate_profiles(
                        registry,  # type: ignore[arg-type]
                        contours,  # type: ignore[arg-type]
                        payload,
                        saved_at=1784512800000,
                        automatic_application_enabled=True,
                    )
                if code is not None:
                    self.assertEqual(code, raised.exception.code)
                self.assertEqual(original, contour_registry_to_payload(contours))  # type: ignore[arg-type]

    async def test_runtime_saves_once_without_bridge_io_and_keeps_memory_on_failure(
        self,
    ) -> None:
        registry, contours, _ = configured_setup()
        registry_store = MemoryStore(registry)  # type: ignore[arg-type]
        contour_store = MemoryContourStore(contours)  # type: ignore[arg-type]
        bridge = MemoryBridge()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=registry_store,
            contour_store=contour_store,
            bridge_client=bridge,
            now_ms=lambda: 1784512800000,
        )
        await runtime.async_start()
        fetches_before = bridge.fetch_count

        receipt = await runtime.async_update_climate_profiles(
            request_for(registry, contours)
        )

        self.assertEqual("saved", receipt["status"])
        self.assertTrue(receipt["schedule_enabled"])
        self.assertFalse(receipt["automatic_application_pending"])
        self.assertEqual(fetches_before, bridge.fetch_count)
        self.assertEqual([], bridge.executed)
        self.assertEqual(1, len(contour_store.saved))
        self.assertEqual([], registry_store.saved)

        before_failure = await runtime.async_contour_registry_payload()
        contour_store.fail = True
        next_request = request_for(registry, contour_store.registry)
        next_request["setup_revision"] = receipt["setup_revision"]
        with self.assertRaisesRegex(RuntimeError, "persistence"):
            await runtime.async_update_climate_profiles(next_request)
        self.assertEqual(
            before_failure,
            await runtime.async_contour_registry_payload(),
        )
        self.assertEqual([], bridge.executed)

    async def test_runtime_blocks_profile_edits_while_live_canary_is_enabled(self) -> None:
        registry, contours, _ = configured_setup()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.CANARY),
            registry_store=MemoryStore(registry),  # type: ignore[arg-type]
            contour_store=MemoryContourStore(contours),  # type: ignore[arg-type]
            bridge_client=MemoryBridge(),
        )
        await runtime.async_start()

        with self.assertRaises(ClimateRuntimeUnavailable):
            await runtime.async_update_climate_profiles(
                request_for(registry, contours)
            )

    async def test_managed_runtime_reports_pending_schedule_without_commanding_now(
        self,
    ) -> None:
        registry, contours, _ = configured_setup()
        contour_store = MemoryContourStore(contours)  # type: ignore[arg-type]
        bridge = MemoryBridge()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.MANAGED),
            registry_store=MemoryStore(registry),  # type: ignore[arg-type]
            contour_store=contour_store,
            bridge_client=bridge,
            now_ms=lambda: 1784512800000,
        )
        await runtime.async_start()
        fetches_before = bridge.fetch_count

        receipt = await runtime.async_update_climate_profiles(
            request_for(registry, contours)
        )

        self.assertTrue(receipt["schedule_enabled"])
        self.assertTrue(receipt["automatic_application_pending"])
        self.assertIn("расписание применит", receipt["message"])
        self.assertEqual(fetches_before, bridge.fetch_count)
        self.assertEqual([], bridge.executed)
        self.assertEqual(1, len(contour_store.saved))


if __name__ == "__main__":
    unittest.main()
