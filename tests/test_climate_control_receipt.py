"""One self-describing receipt for all contour-backed climate actions."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

from custom_components.hausman_hub.application.climate_runtime import ClimateRuntime
from custom_components.hausman_hub.application.contour_apply import (
    ClimateControlAction,
    ClimateControlContext,
    ContourApplyReceipt,
    ContourApplyStatus,
    ContourApplyViolation,
)
from custom_components.hausman_hub.application.contours import (
    build_climate_contour_setup,
    with_active_climate_profile,
    with_applied_climate_schedule_profile,
    with_climate_schedule,
)
from custom_components.hausman_hub.domain.climate_bridge import ClimateBridgeMode
from custom_components.hausman_hub.domain.contours import ClimateProfile
from tests.test_climate_runtime import (
    MemoryContourStore,
    MemoryStore,
    ReflectingContourBridge,
    configuration,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = (
    ROOT
    / "custom_components"
    / "hausman_hub"
    / "contracts"
    / "v1"
    / "climate-control-receipt.schema.json"
)


class ClimateControlReceiptTest(unittest.IsolatedAsyncioTestCase):
    """Apply, temporary target, and return expose the same exact envelope."""

    async def test_three_user_actions_have_one_clear_strict_receipt(self) -> None:
        bridge = ReflectingContourBridge()
        registry, contours = build_climate_contour_setup(
            bridge.snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        contours = with_climate_schedule(
            contours,
            enabled=True,
            day_start="07:00",
            night_start="23:00",
        )
        contours = with_active_climate_profile(contours, "day")
        contours = with_applied_climate_schedule_profile(
            contours,
            ClimateProfile.DAY,
        )
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.MANAGED),
            registry_store=MemoryStore(registry),
            contour_store=MemoryContourStore(contours),
            bridge_client=bridge,
            operation_id_factory=iter(("1" * 32, "2" * 32, "3" * 32)).__next__,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()

        applied = await runtime.async_apply_contour(
            {
                "request_id": "apply-settings-1",
                "contour_id": "climate",
                "confirm": True,
            }
        )
        temporary = await runtime.async_temporary_temperature(
            {
                "request_id": "temporary-living-1",
                "contour_id": "climate",
                "room_id": "living",
                "action": "set",
                "target_temperature": 23.5,
                "confirm": True,
            },
            datetime(2026, 7, 19, 12, 0),
        )
        restored = await runtime.async_temporary_temperature(
            {
                "request_id": "temporary-living-clear-1",
                "contour_id": "climate",
                "room_id": "living",
                "action": "clear",
                "target_temperature": None,
                "confirm": True,
            },
            datetime(2026, 7, 19, 12, 5),
        )

        payloads = [
            applied.as_payload(),
            temporary.as_payload(),
            restored.as_payload(),
        ]
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        for payload in payloads:
            validator.validate(payload)
            self.assertEqual(
                {
                    "name": "hausman-hub-climate-control-receipt",
                    "version": 1,
                },
                payload["contract"],
            )
            self.assertEqual("Выполнено", payload["status_name"])
            self.assertEqual([], payload["reason_names"])

        self.assertEqual(
            [
                "apply_saved_settings",
                "set_temporary_temperature",
                "return_to_schedule",
            ],
            [payload["action"]["code"] for payload in payloads],  # type: ignore[index]
        )
        self.assertEqual(
            [None, "living", "living"],
            [payload["action"]["room_id"] for payload in payloads],  # type: ignore[index]
        )
        self.assertEqual(
            [None, 23.5, 25.0],
            [payload["action"]["target_temperature"] for payload in payloads],  # type: ignore[index]
        )
        serialized = json.dumps(payloads, ensure_ascii=True, sort_keys=True)
        self.assertNotIn("entity_id", serialized)
        self.assertNotIn("synthetic-ac-source-living", serialized)

    def test_pending_reason_has_one_stable_russian_explanation(self) -> None:
        receipt = ContourApplyReceipt(
            operation_id="4" * 32,
            request_id="apply-pending-1",
            contour_id="climate",
            context=ClimateControlContext(
                action=ClimateControlAction.APPLY_SAVED_SETTINGS,
            ),
            status=ContourApplyStatus.PENDING,
            room_count=1,
            command_count=1,
            accepted_count=1,
            confirmed_room_count=0,
            temperature_changes=1,
            strategy_changes=0,
            automatic_mode_changes=0,
            reasons=("verification_unavailable",),
            created_at=1784280000000,
            updated_at=1784280001000,
        ).as_payload()

        Draft202012Validator(
            json.loads(SCHEMA.read_text(encoding="utf-8"))
        ).validate(receipt)
        self.assertEqual("Проверяется", receipt["status_name"])
        self.assertEqual(
            ["Команда принята, но проверка результата пока недоступна."],
            receipt["reason_names"],
        )

    def test_action_context_rejects_mixed_or_incomplete_scope(self) -> None:
        invalid = (
            {
                "action": ClimateControlAction.APPLY_SAVED_SETTINGS,
                "room_id": "living",
            },
            {
                "action": ClimateControlAction.APPLY_SCHEDULE_PROFILE,
            },
            {
                "action": ClimateControlAction.SET_TEMPORARY_TEMPERATURE,
                "room_id": "living",
                "target_temperature": None,
            },
        )
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(
                ContourApplyViolation
            ):
                ClimateControlContext(**values)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
