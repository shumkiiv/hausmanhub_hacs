"""Strict, command-free configuration of the automatic climate schedule."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

from custom_components.hausman_hub.application.climate_runtime import ClimateRuntime
from custom_components.hausman_hub.application.climate_setup import (
    ClimateSetupViolation,
    climate_setup_revision,
    update_climate_schedule,
)
from custom_components.hausman_hub.application.contours import (
    contour_registry_to_payload,
    with_climate_contour_mode,
    with_climate_schedule,
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


def request_for(
    registry: object,
    contours: object,
    *,
    enabled: bool,
    day_start: str = "06:30",
    night_start: str = "22:30",
) -> dict[str, object]:
    """Build one exact schedule request from the currently opened setup."""

    return {
        "contract": {
            "name": "hausman-hub-climate-schedule-update-request",
            "version": 1,
        },
        "setup_revision": climate_setup_revision(registry, contours),  # type: ignore[arg-type]
        "schedule": {
            "enabled": enabled,
            "day_start": day_start,
            "night_start": night_start,
        },
        "confirm_automatic_application": enabled,
    }


class ClimateScheduleUpdateTest(unittest.IsolatedAsyncioTestCase):
    """Schedule edits preserve the contour and never command during saving."""

    def test_changed_enabled_schedule_is_exact_and_clears_temporary_values(
        self,
    ) -> None:
        registry, contours, _ = configured_setup()
        before = contour_registry_to_payload(contours)  # type: ignore[arg-type]
        request = request_for(registry, contours, enabled=True)

        updated, receipt = update_climate_schedule(
            registry,  # type: ignore[arg-type]
            contours,  # type: ignore[arg-type]
            request,
            saved_at=1784512800000,
            automatic_application_enabled=True,
        )

        request_schema = json.loads(
            (CONTRACTS / "climate-schedule-update-request.schema.json").read_text(
                encoding="utf-8"
            )
        )
        receipt_schema = json.loads(
            (CONTRACTS / "climate-schedule-update.schema.json").read_text(
                encoding="utf-8"
            )
        )
        Draft202012Validator(request_schema).validate(request)
        Draft202012Validator(receipt_schema).validate(receipt)
        self.assertEqual("saved", receipt["status"])
        self.assertFalse(receipt["commands_sent"])
        self.assertTrue(receipt["automatic_application_pending"])
        self.assertEqual(1, receipt["temporary_overrides_cleared"])
        self.assertNotEqual(request["setup_revision"], receipt["setup_revision"])

        old_contour = contours.contour("climate")  # type: ignore[union-attr]
        new_contour = updated.contour("climate")
        self.assertIsNotNone(old_contour)
        self.assertIsNotNone(new_contour)
        self.assertEqual(old_contour.name, new_contour.name)  # type: ignore[union-attr]
        self.assertEqual(old_contour.mode, new_contour.mode)  # type: ignore[union-attr]
        self.assertEqual(
            [room.device_ids for room in old_contour.rooms],  # type: ignore[union-attr]
            [room.device_ids for room in new_contour.rooms],  # type: ignore[union-attr]
        )
        self.assertEqual(
            [room.day_profile for room in old_contour.rooms],  # type: ignore[union-attr]
            [room.day_profile for room in new_contour.rooms],  # type: ignore[union-attr]
        )
        self.assertEqual(
            [room.night_profile for room in old_contour.rooms],  # type: ignore[union-attr]
            [room.night_profile for room in new_contour.rooms],  # type: ignore[union-attr]
        )
        self.assertEqual(
            [room.active_profile for room in old_contour.rooms],  # type: ignore[union-attr]
            [room.active_profile for room in new_contour.rooms],  # type: ignore[union-attr]
        )
        self.assertTrue(all(room.temporary_override is None for room in new_contour.rooms))  # type: ignore[union-attr]
        self.assertIsNone(new_contour.schedule.last_applied_profile)  # type: ignore[union-attr]
        self.assertEqual(before, contour_registry_to_payload(contours))  # type: ignore[arg-type]

    def test_unchanged_enabled_schedule_keeps_current_period_and_override(self) -> None:
        registry, contours, _ = configured_setup()

        updated, receipt = update_climate_schedule(
            registry,  # type: ignore[arg-type]
            contours,  # type: ignore[arg-type]
            request_for(
                registry,
                contours,
                enabled=True,
                day_start="07:00",
                night_start="23:00",
            ),
            saved_at=1784512800000,
            automatic_application_enabled=True,
        )

        contour = updated.contour("climate")
        self.assertFalse(receipt["automatic_application_pending"])
        self.assertEqual(0, receipt["temporary_overrides_cleared"])
        self.assertEqual("day", receipt["schedule"]["last_applied_profile"])
        self.assertIsNotNone(contour.rooms[0].temporary_override)  # type: ignore[union-attr]

    def test_disabling_is_safe_without_managed_control(self) -> None:
        registry, contours, _ = configured_setup()

        updated, receipt = update_climate_schedule(
            registry,  # type: ignore[arg-type]
            contours,  # type: ignore[arg-type]
            request_for(registry, contours, enabled=False),
            saved_at=1784512800000,
            automatic_application_enabled=False,
        )

        contour = updated.contour("climate")
        self.assertFalse(contour.schedule.enabled)  # type: ignore[union-attr]
        self.assertIsNone(contour.schedule.last_applied_profile)  # type: ignore[union-attr]
        self.assertFalse(receipt["automatic_application_pending"])
        self.assertEqual(1, receipt["temporary_overrides_cleared"])
        self.assertIn("Расписание выключено", receipt["message"])

    def test_stale_unconfirmed_invalid_and_extra_requests_are_rejected(self) -> None:
        registry, contours, _ = configured_setup()
        original = contour_registry_to_payload(contours)  # type: ignore[arg-type]
        valid = request_for(registry, contours, enabled=True)
        cases: list[tuple[str, dict[str, object], str | None]] = []

        stale = copy.deepcopy(valid)
        stale["setup_revision"] = int(stale["setup_revision"]) + 1
        cases.append(("stale", stale, "setup_changed"))
        unconfirmed = copy.deepcopy(valid)
        unconfirmed["confirm_automatic_application"] = False
        cases.append(("unconfirmed", unconfirmed, "confirmation_required"))
        equal_times = copy.deepcopy(valid)
        equal_times["schedule"]["night_start"] = "06:30"  # type: ignore[index]
        cases.append(("equal_times", equal_times, None))
        malformed = copy.deepcopy(valid)
        malformed["schedule"]["day_start"] = "6:30"  # type: ignore[index]
        cases.append(("malformed", malformed, None))
        extra = copy.deepcopy(valid)
        extra["schedule"]["timezone"] = "Europe/Moscow"  # type: ignore[index]
        cases.append(("extra", extra, None))
        disabled_confirmed = request_for(registry, contours, enabled=False)
        disabled_confirmed["confirm_automatic_application"] = True
        cases.append(("disabled_confirmed", disabled_confirmed, None))

        for name, payload, code in cases:
            with self.subTest(name=name):
                with self.assertRaises(ClimateSetupViolation) as raised:
                    update_climate_schedule(
                        registry,  # type: ignore[arg-type]
                        contours,  # type: ignore[arg-type]
                        payload,
                        saved_at=1784512800000,
                        automatic_application_enabled=True,
                    )
                if code is not None:
                    self.assertEqual(code, raised.exception.code)
                self.assertEqual(original, contour_registry_to_payload(contours))  # type: ignore[arg-type]

    def test_enabling_requires_automatic_mode_and_managed_control(self) -> None:
        registry, contours, _ = configured_setup()

        with self.assertRaises(ClimateSetupViolation) as raised:
            update_climate_schedule(
                registry,  # type: ignore[arg-type]
                contours,  # type: ignore[arg-type]
                request_for(registry, contours, enabled=True),
                saved_at=1784512800000,
                automatic_application_enabled=False,
            )

        self.assertEqual("managed_control_required", raised.exception.code)

        disabled = with_climate_schedule(
            contours,  # type: ignore[arg-type]
            enabled=False,
            day_start="07:00",
            night_start="23:00",
        )
        observed = with_climate_contour_mode(disabled, "observe")
        with self.assertRaises(ClimateSetupViolation) as observed_error:
            update_climate_schedule(
                registry,  # type: ignore[arg-type]
                observed,
                request_for(registry, observed, enabled=True),
                saved_at=1784512800000,
                automatic_application_enabled=True,
            )
        self.assertEqual("automatic_mode_required", observed_error.exception.code)

    async def test_runtime_saves_once_without_bridge_io_and_keeps_memory_on_failure(
        self,
    ) -> None:
        registry, contours, _ = configured_setup()
        registry_store = MemoryStore(registry)  # type: ignore[arg-type]
        contour_store = MemoryContourStore(contours)  # type: ignore[arg-type]
        bridge = MemoryBridge()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.MANAGED),
            registry_store=registry_store,
            contour_store=contour_store,
            bridge_client=bridge,
            now_ms=lambda: 1784512800000,
        )
        await runtime.async_start()
        fetches_before = bridge.fetch_count

        receipt = await runtime.async_update_climate_schedule(
            request_for(registry, contours, enabled=True)
        )

        self.assertTrue(receipt["automatic_application_pending"])
        self.assertEqual(fetches_before, bridge.fetch_count)
        self.assertEqual([], bridge.executed)
        self.assertEqual(1, len(contour_store.saved))
        self.assertEqual([], registry_store.saved)

        before_failure = await runtime.async_contour_registry_payload()
        contour_store.fail = True
        next_request = request_for(registry, contour_store.registry, enabled=False)
        with self.assertRaisesRegex(RuntimeError, "persistence"):
            await runtime.async_update_climate_schedule(next_request)
        self.assertEqual(
            before_failure,
            await runtime.async_contour_registry_payload(),
        )
        self.assertEqual([], bridge.executed)

    async def test_shadow_runtime_can_disable_but_cannot_enable_schedule(self) -> None:
        registry, contours, _ = configured_setup()
        contour_store = MemoryContourStore(contours)  # type: ignore[arg-type]
        bridge = MemoryBridge()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=MemoryStore(registry),  # type: ignore[arg-type]
            contour_store=contour_store,
            bridge_client=bridge,
            now_ms=lambda: 1784512800000,
        )
        await runtime.async_start()
        fetches_before = bridge.fetch_count

        disabled = await runtime.async_update_climate_schedule(
            request_for(registry, contours, enabled=False)
        )
        self.assertFalse(disabled["schedule"]["enabled"])
        self.assertEqual(1, len(contour_store.saved))

        with self.assertRaises(ClimateSetupViolation) as raised:
            await runtime.async_update_climate_schedule(
                request_for(registry, contour_store.registry, enabled=True)
            )
        self.assertEqual("managed_control_required", raised.exception.code)
        self.assertEqual(1, len(contour_store.saved))
        self.assertEqual(fetches_before, bridge.fetch_count)
        self.assertEqual([], bridge.executed)

    async def test_canary_runtime_can_only_disarm_schedule(self) -> None:
        registry, contours, _ = configured_setup()
        contour_store = MemoryContourStore(contours)  # type: ignore[arg-type]
        bridge = MemoryBridge()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.CANARY),
            registry_store=MemoryStore(registry),  # type: ignore[arg-type]
            contour_store=contour_store,
            bridge_client=bridge,
            now_ms=lambda: 1784512800000,
        )
        await runtime.async_start()
        fetches_before = bridge.fetch_count

        disabled = await runtime.async_update_climate_schedule(
            request_for(registry, contours, enabled=False)
        )
        self.assertFalse(disabled["schedule"]["enabled"])
        self.assertEqual(1, len(contour_store.saved))

        with self.assertRaises(ClimateSetupViolation) as raised:
            await runtime.async_update_climate_schedule(
                request_for(registry, contour_store.registry, enabled=True)
            )
        self.assertEqual("managed_control_required", raised.exception.code)
        self.assertEqual(1, len(contour_store.saved))
        self.assertEqual(fetches_before, bridge.fetch_count)
        self.assertEqual([], bridge.executed)


if __name__ == "__main__":
    unittest.main()
