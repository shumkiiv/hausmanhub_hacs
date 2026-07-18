"""Pure orchestration tests with in-memory climate and storage adapters."""

from __future__ import annotations

import json
import unittest

from custom_components.hausman_hub.application.climate_commands import (
    ClimateCommandRejected,
    ClimateCommandViolation,
)
from custom_components.hausman_hub.application.climate_import import import_climate_state
from custom_components.hausman_hub.application.climate_evidence import (
    ClimateShadowEvidence,
)
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
from tests.test_climate_import import (
    complete_registry_payload,
    registry_payload,
    source_payload,
)


class MemoryStore:
    def __init__(self, registry: ClimateRegistry) -> None:
        self.registry = registry
        self.saved: list[ClimateRegistry] = []

    async def async_load(self) -> ClimateRegistry:
        return self.registry

    async def async_save(self, registry: ClimateRegistry) -> None:
        self.registry = registry
        self.saved.append(registry)


class MemoryEvidenceStore:
    def __init__(self, evidence: ClimateShadowEvidence | None = None) -> None:
        self.evidence = evidence
        self.saved: list[dict[str, object]] = []

    async def async_load(self) -> ClimateShadowEvidence | None:
        return self.evidence

    async def async_save(self, evidence: ClimateShadowEvidence) -> None:
        self.evidence = evidence
        self.saved.append(evidence.as_storage_payload())


class FailingEvidenceStore(MemoryEvidenceStore):
    def __init__(self) -> None:
        super().__init__()
        self.fail = False

    async def async_save(self, evidence: ClimateShadowEvidence) -> None:
        if self.fail:
            raise RuntimeError("synthetic evidence persistence failure")
        await super().async_save(evidence)


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


def ready_evidence_store(
    payload: dict[str, object] | None = None,
) -> MemoryEvidenceStore:
    registry = registry_from_payload(payload or registry_payload())
    snapshot = import_climate_state(source_payload())
    start = 1784279405000
    evidence = ClimateShadowEvidence.for_registry(registry, now_ms=start)
    for offset in (0, 300_000, 600_000):
        evidence.record_observation(registry, snapshot, now_ms=start + offset)
    for action in ("set_room_target", "turn_room_off"):
        evidence.record_intent(
            category="translated",
            room_id="living",
            action=action,
            now_ms=start + 600_000,
        )
    return MemoryEvidenceStore(evidence)


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

    async def test_shadow_collects_persistent_candidate_evidence_without_posts(self) -> None:
        bridge = MemoryBridge()
        now = [1784280005000]
        evidence_store = MemoryEvidenceStore()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=MemoryStore(registry_from_payload(registry_payload())),
            bridge_client=bridge,
            evidence_store=evidence_store,
            operation_id_factory=iter(("a" * 32, "b" * 32)).__next__,
            now_ms=lambda: now[0],
        )
        await runtime.async_start()
        for _ in range(2):
            now[0] += 300_000
            await runtime.async_public_snapshot()
        await runtime.async_action(
            {
                "request_id": "evidence-target",
                "action": "set_room_target",
                "room_id": "living",
                "target_temperature": 24.0,
            }
        )
        await runtime.async_action(
            {
                "request_id": "evidence-off",
                "action": "turn_room_off",
                "room_id": "living",
            }
        )

        evidence = await runtime.async_shadow_evidence({"room_id": "living"})

        self.assertTrue(evidence["candidate"]["ready"])  # type: ignore[index]
        self.assertEqual(3, evidence["counts"]["matched"])  # type: ignore[index]
        self.assertEqual(2, evidence["counts"]["translated"])  # type: ignore[index]
        self.assertGreaterEqual(len(evidence_store.saved), 6)
        self.assertEqual([], bridge.executed)

    async def test_shadow_evidence_query_persists_one_new_sample_once(self) -> None:
        bridge = MemoryBridge()
        now = [1784280005000]
        evidence_store = MemoryEvidenceStore()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=MemoryStore(registry_from_payload(registry_payload())),
            bridge_client=bridge,
            evidence_store=evidence_store,
            now_ms=lambda: now[0],
        )
        await runtime.async_start()
        saves_before_query = len(evidence_store.saved)
        now[0] += 300_000

        await runtime.async_shadow_evidence({"room_id": "living"})

        self.assertEqual(saves_before_query + 1, len(evidence_store.saved))

    async def test_shadow_canary_preflight_is_read_only_and_non_activating(self) -> None:
        bridge = MemoryBridge()
        registry = complete_registry_payload()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=MemoryStore(registry_from_payload(registry)),
            bridge_client=bridge,
            evidence_store=ready_evidence_store(registry),
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        fetches_before = bridge.fetch_count

        result = await runtime.async_canary_preflight({"room_id": "living"})

        self.assertEqual("ready", result["status"])
        self.assertTrue(result["ready_for_authorization"])
        self.assertFalse(result["activation"]["allowed"])  # type: ignore[index]
        self.assertGreater(bridge.fetch_count, fetches_before)
        self.assertEqual([], bridge.executed)

    async def test_disabled_canary_preflight_never_fetches_or_activates(self) -> None:
        bridge = MemoryBridge()
        registry = complete_registry_payload()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.DISABLED),
            registry_store=MemoryStore(registry_from_payload(registry)),
            bridge_client=bridge,
            evidence_store=ready_evidence_store(registry),
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()

        result = await runtime.async_canary_preflight({"room_id": "living"})

        self.assertEqual("blocked", result["status"])
        self.assertFalse(result["ready_for_authorization"])
        self.assertFalse(result["freshness"]["state_fresh"])  # type: ignore[index]
        self.assertEqual(0, bridge.fetch_count)
        self.assertEqual([], bridge.executed)

    async def test_canary_preflight_reports_pending_and_requires_rollback(self) -> None:
        bridge = MemoryBridge()
        registry = complete_registry_payload()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.CANARY),
            registry_store=MemoryStore(registry_from_payload(registry)),
            bridge_client=bridge,
            evidence_store=ready_evidence_store(registry),
            operation_id_factory=lambda: "d" * 32,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        await runtime.async_action(
            {
                "request_id": "preflight-pending",
                "action": "set_room_target",
                "room_id": "living",
                "target_temperature": 24.5,
            }
        )

        result = await runtime.async_canary_preflight({"room_id": "living"})

        self.assertEqual("blocked", result["status"])
        self.assertEqual("pending", result["operation"]["status"])  # type: ignore[index]
        self.assertFalse(result["rollback"]["ready"])  # type: ignore[index]
        self.assertIn("preflight_requires_shadow", result["reasons"])
        self.assertIn("pending_operation", result["reasons"])
        self.assertEqual(1, len(bridge.executed))

    async def test_rejected_intent_keeps_validation_error_when_evidence_save_fails(
        self,
    ) -> None:
        bridge = MemoryBridge()
        evidence_store = FailingEvidenceStore()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=MemoryStore(registry_from_payload(registry_payload())),
            bridge_client=bridge,
            evidence_store=evidence_store,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        evidence_store.fail = True

        with self.assertRaisesRegex(ClimateCommandViolation, "unsupported"):
            await runtime.async_action(
                {
                    "request_id": "invalid-shadow-action",
                    "action": "unsupported",
                }
            )

        self.assertEqual("RuntimeError", runtime.last_error)
        self.assertEqual([], bridge.executed)

    async def test_canary_without_completed_shadow_evidence_fails_closed(self) -> None:
        bridge = MemoryBridge()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.CANARY),
            registry_store=MemoryStore(registry_from_payload(registry_payload())),
            bridge_client=bridge,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()

        snapshot = await runtime.async_public_snapshot()
        with self.assertRaisesRegex(ClimateCommandViolation, "evidence"):
            await runtime.async_action(
                {
                    "request_id": "blocked-canary",
                    "action": "turn_room_off",
                    "room_id": "living",
                }
            )

        self.assertFalse(snapshot["climate"]["commands_enabled"])  # type: ignore[index]
        self.assertEqual(3, snapshot["contract"]["version"])  # type: ignore[index]
        self.assertIn(
            "evidence_not_ready",
            snapshot["rooms"][0]["control"]["blocked_reasons"],  # type: ignore[index]
        )
        self.assertEqual([], bridge.executed)

    async def test_public_room_control_closes_while_canary_operation_is_pending(
        self,
    ) -> None:
        bridge = MemoryBridge()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.CANARY),
            registry_store=MemoryStore(registry_from_payload(registry_payload())),
            bridge_client=bridge,
            evidence_store=ready_evidence_store(),
            operation_id_factory=lambda: "1" * 32,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()

        ready = await runtime.async_public_snapshot()
        await runtime.async_action(
            {
                "request_id": "pending-public-control",
                "action": "turn_room_off",
                "room_id": "living",
            }
        )
        pending = await runtime.async_public_snapshot()

        self.assertTrue(ready["rooms"][0]["control"]["enabled"])  # type: ignore[index]
        self.assertTrue(ready["climate"]["commands_enabled"])  # type: ignore[index]
        self.assertFalse(pending["rooms"][0]["control"]["enabled"])  # type: ignore[index]
        self.assertEqual(
            ["operation_pending"],
            pending["rooms"][0]["control"]["blocked_reasons"],  # type: ignore[index]
        )
        self.assertFalse(pending["climate"]["commands_enabled"])  # type: ignore[index]
        self.assertEqual(1, len(bridge.executed))

    async def test_canary_posts_only_evidence_qualified_private_plan(self) -> None:
        bridge = MemoryBridge()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.CANARY),
            registry_store=MemoryStore(registry_from_payload(registry_payload())),
            bridge_client=bridge,
            evidence_store=ready_evidence_store(),
            operation_id_factory=lambda: "1" * 32,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()

        result = await runtime.async_action(
            {
                "request_id": "canary-1",
                "action": "set_room_target",
                "room_id": "living",
                "target_temperature": 24.5,
            }
        )

        self.assertEqual("pending", result.status)
        self.assertEqual("living", result.room_id)
        self.assertEqual(1, len(bridge.executed))
        self.assertNotIn("deviceId", result.as_payload())

        with self.assertRaisesRegex(ClimateCommandViolation, "canary scope"):
            await runtime.async_action(
                {
                    "request_id": "canary-device-power",
                    "action": "set_device_power",
                    "device_id": "living_ac",
                    "on": True,
                }
            )
        self.assertEqual(1, len(bridge.executed))

    async def test_duplicate_request_is_idempotent_and_conflicting_reuse_is_rejected(self) -> None:
        bridge = MemoryBridge()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.CANARY),
            registry_store=MemoryStore(registry_from_payload(registry_payload())),
            bridge_client=bridge,
            evidence_store=ready_evidence_store(),
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
            evidence_store=ready_evidence_store(),
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
            evidence_store=ready_evidence_store(),
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
            evidence_store=ready_evidence_store(),
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
            evidence_store=ready_evidence_store(),
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
            evidence_store=ready_evidence_store(),
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
                    "action": "turn_room_off",
                    "room_id": "living",
                }
            )
        self.assertEqual(1, len(bridge.executed))

        now[0] += 30_000
        second = await runtime.async_action(
            {
                "request_id": "room-lock-2",
                "action": "turn_room_off",
                "room_id": "living",
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
