"""Pure orchestration tests with in-memory climate and storage adapters."""

from __future__ import annotations

import json
import unittest

from custom_components.hausman_hub.application.climate_commands import (
    ClimateCommandRejected,
    ClimateCommandViolation,
)
from custom_components.hausman_hub.application.climate_import import import_climate_state
from custom_components.hausman_hub.application.climate_registry import registry_from_payload
from custom_components.hausman_hub.application.climate_runtime import (
    ClimateRuntime,
    ClimateRuntimeUnavailable,
)
from custom_components.hausman_hub.domain.climate import ClimateRegistry
from custom_components.hausman_hub.domain.climate_bridge import (
    ClimateBridgeMode,
    climate_bridge_target,
)
from custom_components.hausman_hub.domain.configuration import SafeConfiguration
from tests.test_climate_import import registry_payload, source_payload


class MemoryStore:
    def __init__(self, registry: ClimateRegistry) -> None:
        self.registry = registry
        self.saved: list[ClimateRegistry] = []

    async def async_load(self) -> ClimateRegistry:
        return self.registry

    async def async_save(self, registry: ClimateRegistry) -> None:
        self.registry = registry
        self.saved.append(registry)


class MemoryBridge:
    def __init__(self) -> None:
        self.snapshot = import_climate_state(source_payload())
        self.fetch_count = 0
        self.executed = []

    async def async_fetch_state(self):
        self.fetch_count += 1
        return self.snapshot

    async def async_execute(self, plan):
        self.executed.append(plan)
        return {"ok": True}


class RejectingBridge(MemoryBridge):
    async def async_execute(self, plan):
        self.executed.append(plan)
        raise ClimateCommandRejected("synthetic explicit rejection")


class AmbiguousBridge(MemoryBridge):
    async def async_execute(self, plan):
        self.executed.append(plan)
        raise RuntimeError("synthetic transport ambiguity")


def configuration(mode: ClimateBridgeMode) -> SafeConfiguration:
    return SafeConfiguration(
        mode="shadow",
        climate_bridge_mode=mode,
        climate_bridge_target=climate_bridge_target("http://127.0.0.1:1880"),
        climate_canary_room_id=(
            "living" if mode is ClimateBridgeMode.CANARY else None
        ),
    )


class ClimateRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def test_shadow_refreshes_but_never_posts(self) -> None:
        bridge = MemoryBridge()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=MemoryStore(registry_from_payload(registry_payload())),
            bridge_client=bridge,
            operation_id_factory=lambda: "0" * 32,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()

        result = await runtime.async_action(
            {
                "request_id": "shadow-1",
                "action": "set_room_target",
                "room_id": "living",
                "target_temperature": 24.5,
            }
        )

        self.assertEqual("accepted", result.status)
        self.assertEqual("shadow", result.execution)
        self.assertEqual("0" * 32, result.operation_id)
        self.assertEqual([], bridge.executed)
        self.assertGreaterEqual(bridge.fetch_count, 2)

    async def test_canary_posts_only_private_plan_and_returns_public_result(self) -> None:
        bridge = MemoryBridge()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.CANARY),
            registry_store=MemoryStore(registry_from_payload(registry_payload())),
            bridge_client=bridge,
            operation_id_factory=lambda: "1" * 32,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()

        result = await runtime.async_action(
            {
                "request_id": "canary-1",
                "action": "set_device_power",
                "device_id": "living_ac",
                "on": True,
            }
        )

        self.assertEqual("pending", result.status)
        self.assertEqual("living_ac", result.device_id)
        self.assertEqual(1, len(bridge.executed))
        self.assertNotIn("deviceId", result.as_payload())

    async def test_duplicate_request_is_idempotent_and_conflicting_reuse_is_rejected(self) -> None:
        bridge = MemoryBridge()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.CANARY),
            registry_store=MemoryStore(registry_from_payload(registry_payload())),
            bridge_client=bridge,
            operation_id_factory=lambda: "2" * 32,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        payload = {
            "request_id": "retry-1",
            "action": "set_room_target",
            "room_id": "living",
            "target_temperature": 24.5,
        }

        first = await runtime.async_action(payload)
        fetches_after_first = bridge.fetch_count
        duplicate = await runtime.async_action(dict(payload))

        self.assertEqual(first, duplicate)
        self.assertEqual(fetches_after_first, bridge.fetch_count)
        self.assertEqual(1, len(bridge.executed))
        with self.assertRaisesRegex(ClimateCommandViolation, "already used"):
            await runtime.async_action({**payload, "target_temperature": 24.0})
        self.assertEqual(1, len(bridge.executed))

    async def test_explicit_backend_rejection_is_a_terminal_idempotent_receipt(self) -> None:
        bridge = RejectingBridge()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.CANARY),
            registry_store=MemoryStore(registry_from_payload(registry_payload())),
            bridge_client=bridge,
            operation_id_factory=lambda: "7" * 32,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        payload = {
            "request_id": "reject-1",
            "action": "turn_room_off",
            "room_id": "living",
        }

        first = await runtime.async_action(payload)
        duplicate = await runtime.async_action(dict(payload))

        self.assertEqual("rejected", first.status)
        self.assertEqual(first, duplicate)
        self.assertEqual(1, len(bridge.executed))

    async def test_ambiguous_post_is_reserved_before_io_and_cannot_repeat(self) -> None:
        bridge = AmbiguousBridge()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.CANARY),
            registry_store=MemoryStore(registry_from_payload(registry_payload())),
            bridge_client=bridge,
            operation_id_factory=lambda: "9" * 32,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        payload = {
            "request_id": "ambiguous-1",
            "action": "turn_room_off",
            "room_id": "living",
        }

        with self.assertRaisesRegex(RuntimeError, "ambiguity"):
            await runtime.async_action(payload)
        retry = await runtime.async_action(dict(payload))

        self.assertEqual("pending", retry.status)
        self.assertEqual(1, len(bridge.executed))

    async def test_evicted_request_id_fails_closed_instead_of_repeating(self) -> None:
        bridge = MemoryBridge()
        operation_ids = iter(f"{value:032x}" for value in range(1, 259))
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=MemoryStore(registry_from_payload(registry_payload())),
            bridge_client=bridge,
            operation_id_factory=lambda: next(operation_ids),
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        first_payload = {
            "request_id": "bounded-0",
            "action": "set_room_target",
            "room_id": "living",
            "target_temperature": 24.5,
        }
        first = await runtime.async_action(first_payload)
        for value in range(1, 257):
            await runtime.async_action(
                {**first_payload, "request_id": f"bounded-{value}"}
            )
        fetches_before_retry = bridge.fetch_count

        with self.assertRaisesRegex(ClimateCommandViolation, "lifecycle"):
            await runtime.async_action(dict(first_payload))
        unknown = await runtime.async_operation({"operation_id": first.operation_id})

        self.assertEqual(fetches_before_retry, bridge.fetch_count)
        self.assertFalse(unknown.known)

    async def test_room_off_confirmation_tracks_the_planned_device_only(self) -> None:
        bridge = MemoryBridge()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.CANARY),
            registry_store=MemoryStore(registry_from_payload(registry_payload())),
            bridge_client=bridge,
            operation_id_factory=lambda: "8" * 32,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        receipt = await runtime.async_action(
            {
                "request_id": "room-off-1",
                "action": "turn_room_off",
                "room_id": "living",
            }
        )
        source = source_payload()
        source["devices"][1]["roomId"] = "living"
        bridge.snapshot = import_climate_state(source)

        still_pending = await runtime.async_operation(
            {"operation_id": receipt.operation_id}
        )
        source["devices"][0]["state"] = "off"
        bridge.snapshot = import_climate_state(source)
        confirmed = await runtime.async_operation(
            {"operation_id": receipt.operation_id}
        )

        self.assertEqual("pending", still_pending.status)
        self.assertEqual("confirmed", confirmed.status)
        self.assertEqual(1, len(bridge.executed))

    async def test_pending_room_operation_confirms_from_a_later_read_only_snapshot(self) -> None:
        bridge = MemoryBridge()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.CANARY),
            registry_store=MemoryStore(registry_from_payload(registry_payload())),
            bridge_client=bridge,
            operation_id_factory=lambda: "3" * 32,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        receipt = await runtime.async_action(
            {
                "request_id": "confirm-1",
                "action": "set_room_target",
                "room_id": "living",
                "target_temperature": 24.5,
            }
        )
        source = source_payload()
        source["rooms"][0]["targets"]["temperature"] = 24.5  # type: ignore[index]
        bridge.snapshot = import_climate_state(source)

        confirmed = await runtime.async_operation(
            {"operation_id": receipt.operation_id}
        )

        self.assertEqual("confirmed", confirmed.status)
        self.assertEqual(1, len(bridge.executed))

    async def test_canary_blocks_a_second_room_submission_until_timeout(self) -> None:
        bridge = MemoryBridge()
        operation_ids = iter(("5" * 32, "6" * 32))
        now = [1784280005000]
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.CANARY),
            registry_store=MemoryStore(registry_from_payload(registry_payload())),
            bridge_client=bridge,
            operation_id_factory=lambda: next(operation_ids),
            now_ms=lambda: now[0],
        )
        await runtime.async_start()
        first = await runtime.async_action(
            {
                "request_id": "room-lock-1",
                "action": "set_room_target",
                "room_id": "living",
                "target_temperature": 24.5,
            }
        )

        with self.assertRaisesRegex(ClimateCommandViolation, "pending"):
            await runtime.async_action(
                {
                    "request_id": "room-lock-2",
                    "action": "set_room_mode",
                    "room_id": "living",
                    "mode": "manual",
                }
            )
        self.assertEqual(1, len(bridge.executed))

        now[0] += 30_000
        second = await runtime.async_action(
            {
                "request_id": "room-lock-2",
                "action": "set_room_mode",
                "room_id": "living",
                "mode": "manual",
            }
        )
        first_after_timeout = await runtime.async_operation(
            {"operation_id": first.operation_id}
        )

        self.assertEqual("pending", second.status)
        self.assertEqual("timed_out", first_after_timeout.status)
        self.assertEqual(2, len(bridge.executed))

    async def test_unknown_operation_is_redacted_and_shadow_readiness_is_count_only(self) -> None:
        bridge = MemoryBridge()
        full_registry = registry_payload()
        full_registry["devices"].append(  # type: ignore[union-attr]
            {
                "id": "kids_humidifier",
                "name": "Kids humidifier",
                "room_id": "kids",
                "kind": "humidifier",
                "source_id": "synthetic-humidifier-source-kids",
                "control_scope": "observed",
                "control_owner": "observed",
                "capabilities": ["power", "target_humidity"],
                "endpoints": [
                    {
                        "role": "control",
                        "entity_id": "humidifier.synthetic_kids",
                    }
                ],
            }
        )
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=MemoryStore(registry_from_payload(full_registry)),
            bridge_client=bridge,
            operation_id_factory=lambda: "4" * 32,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()

        readiness = await runtime.async_readiness()
        unknown = await runtime.async_operation({"operation_id": "f" * 32})

        self.assertTrue(readiness["ready"])
        self.assertEqual([], readiness["reasons"])
        self.assertNotIn("source_id", json.dumps(readiness, sort_keys=True))
        self.assertFalse(unknown.known)
        self.assertEqual("unknown", unknown.status)
        self.assertIsNone(unknown.request_id)

    async def test_registry_preview_validates_without_saving_then_atomic_save_remains_separate(self) -> None:
        store = MemoryStore(ClimateRegistry())
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=store,
            bridge_client=MemoryBridge(),
        )
        await runtime.async_start()

        preview = await runtime.async_preview_registry(registry_payload())

        self.assertTrue(preview["save_allowed"])
        self.assertEqual([], store.saved)
        await runtime.async_replace_registry(registry_payload())
        self.assertEqual(1, len(store.saved))

    async def test_registry_replacement_is_exact_and_persisted(self) -> None:
        store = MemoryStore(ClimateRegistry())
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=store,
            bridge_client=MemoryBridge(),
        )
        await runtime.async_start()

        result = await runtime.async_replace_registry(registry_payload())

        self.assertEqual(1, len(store.saved))
        self.assertEqual("living_ac", result["devices"][0]["id"])

    async def test_canary_cannot_change_registry_bindings(self) -> None:
        store = MemoryStore(registry_from_payload(registry_payload()))
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.CANARY),
            registry_store=store,
            bridge_client=MemoryBridge(),
        )
        await runtime.async_start()

        with self.assertRaises(ClimateRuntimeUnavailable):
            await runtime.async_replace_registry(registry_payload())

        self.assertEqual([], store.saved)


if __name__ == "__main__":
    unittest.main()
