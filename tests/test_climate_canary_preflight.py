"""Pure tests for the redacted, non-activating climate canary preflight."""

from __future__ import annotations

import copy
import json
import unittest

from custom_components.hausman_hub.application.climate_canary_preflight import (
    ClimateCanaryPreflightViolation,
    climate_canary_preflight,
)
from custom_components.hausman_hub.application.climate_evidence import (
    ClimateShadowEvidence,
)
from custom_components.hausman_hub.application.climate_import import (
    import_climate_state,
)
from custom_components.hausman_hub.application.climate_registry import (
    registry_from_payload,
)
from custom_components.hausman_hub.domain.climate_bridge import ClimateBridgeMode
from tests.test_climate_import import complete_registry_payload, source_payload


NOW = 1784280005000


def ready_inputs():
    registry = registry_from_payload(complete_registry_payload())
    snapshot = import_climate_state(source_payload())
    evidence = ClimateShadowEvidence.for_registry(
        registry,
        now_ms=NOW - 600_000,
    )
    for offset in (600_000, 300_000, 0):
        evidence.record_observation(
            registry,
            snapshot,
            now_ms=NOW - offset,
        )
    for action in ("set_room_target", "turn_room_off"):
        evidence.record_intent(
            category="translated",
            room_id="living",
            action=action,
            now_ms=NOW,
        )
    payload = evidence.as_payload(
        registry=registry,
        snapshot=snapshot,
        bridge_mode=ClimateBridgeMode.SHADOW,
        candidate_room_id="living",
        now_ms=NOW,
    )
    return registry, snapshot, payload


class ClimateCanaryPreflightTest(unittest.TestCase):
    def test_ready_preflight_is_redacted_and_still_cannot_activate(self) -> None:
        registry, snapshot, evidence = ready_inputs()

        result = climate_canary_preflight(
            registry,
            snapshot,
            evidence,
            bridge_mode=ClimateBridgeMode.SHADOW,
            room_id="living",
            pending_operation=False,
            checked_at=NOW,
        )

        self.assertEqual("ready", result["status"])
        self.assertTrue(result["ready_for_authorization"])
        self.assertEqual(
            ["set_room_target", "turn_room_off"],
            result["command_scope"]["actions"],  # type: ignore[index]
        )
        self.assertEqual("clear", result["operation"]["status"])  # type: ignore[index]
        self.assertTrue(result["rollback"]["ready"])  # type: ignore[index]
        self.assertTrue(result["freshness"]["state_fresh"])  # type: ignore[index]
        self.assertEqual(NOW, result["freshness"]["checked_at"])  # type: ignore[index]
        self.assertFalse(result["activation"]["allowed"])  # type: ignore[index]
        self.assertTrue(  # type: ignore[index]
            result["activation"]["separate_authorization_required"]
        )
        serialized = json.dumps(result, sort_keys=True)
        self.assertNotIn("source_id", serialized)
        self.assertNotIn("entity_id", serialized)
        self.assertNotIn("synthetic-ac-source", serialized)

    def test_disabled_or_pending_preflight_stays_blocked(self) -> None:
        registry, snapshot, evidence = ready_inputs()
        disabled_evidence = copy.deepcopy(evidence)
        disabled_evidence["candidate"]["status"] = "blocked"  # type: ignore[index]
        disabled_evidence["candidate"]["ready"] = False  # type: ignore[index]
        disabled_evidence["candidate"]["reasons"] = [  # type: ignore[index]
            "bridge_disabled"
        ]

        disabled = climate_canary_preflight(
            registry,
            snapshot,
            disabled_evidence,
            bridge_mode=ClimateBridgeMode.DISABLED,
            room_id="living",
            pending_operation=False,
            checked_at=NOW,
        )
        pending = climate_canary_preflight(
            registry,
            snapshot,
            evidence,
            bridge_mode=ClimateBridgeMode.SHADOW,
            room_id="living",
            pending_operation=True,
            checked_at=NOW,
        )

        self.assertEqual("blocked", disabled["status"])
        self.assertFalse(disabled["ready_for_authorization"])
        self.assertEqual("effective", disabled["rollback"]["status"])  # type: ignore[index]
        self.assertIn("preflight_requires_shadow", disabled["reasons"])
        self.assertEqual("blocked", pending["status"])
        self.assertEqual("pending", pending["operation"]["status"])  # type: ignore[index]
        self.assertFalse(pending["rollback"]["ready"])  # type: ignore[index]
        self.assertIn("pending_operation", pending["reasons"])

    def test_internal_shadow_scope_mismatch_fails_closed(self) -> None:
        registry, snapshot, evidence = ready_inputs()
        malformed = copy.deepcopy(evidence)
        malformed["candidate"]["required_actions"] = [  # type: ignore[index]
            "set_device_power"
        ]

        with self.assertRaisesRegex(
            ClimateCanaryPreflightViolation,
            "scope",
        ):
            climate_canary_preflight(
                registry,
                snapshot,
                malformed,
                bridge_mode=ClimateBridgeMode.SHADOW,
                room_id="living",
                pending_operation=False,
                checked_at=NOW,
            )

    def test_internal_shadow_readiness_mismatch_fails_closed(self) -> None:
        registry, snapshot, evidence = ready_inputs()
        malformed = copy.deepcopy(evidence)
        malformed["candidate"]["reasons"] = [  # type: ignore[index]
            "required_actions_unsupported"
        ]

        with self.assertRaisesRegex(
            ClimateCanaryPreflightViolation,
            "readiness",
        ):
            climate_canary_preflight(
                registry,
                snapshot,
                malformed,
                bridge_mode=ClimateBridgeMode.SHADOW,
                room_id="living",
                pending_operation=False,
                checked_at=NOW,
            )

    def test_expired_state_blocks_an_otherwise_ready_preflight(self) -> None:
        registry, snapshot, evidence = ready_inputs()

        result = climate_canary_preflight(
            registry,
            snapshot,
            evidence,
            bridge_mode=ClimateBridgeMode.SHADOW,
            room_id="living",
            pending_operation=False,
            checked_at=snapshot.generated_at + 300_001,
        )

        self.assertEqual("blocked", result["status"])
        self.assertFalse(result["ready_for_authorization"])
        self.assertFalse(result["freshness"]["state_fresh"])  # type: ignore[index]
        self.assertIn("preflight_state_not_fresh", result["reasons"])


if __name__ == "__main__":
    unittest.main()
