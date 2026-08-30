"""Tests for the pinned canonical API error policy."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

from custom_components.hausman_hub.error_taxonomy import (
    api_error_payload,
    api_error_status,
    async_preload_error_policies,
    error_policies,
)


ROOT = Path(__file__).resolve().parents[1]
TAXONOMY_PATH = (
    ROOT
    / "custom_components"
    / "hausman_hub"
    / "contracts"
    / "v1"
    / "error-taxonomy.json"
)
ERROR_SCHEMA_PATH = (
    ROOT
    / "custom_components"
    / "hausman_hub"
    / "contracts"
    / "v1"
    / "api-error.schema.json"
)
CONTRACTS_0_63_0_SHA256 = (
    "2041465d8f98e9c46c69fa129bc02f8466b60393965040c42d93952eb90ebb33"
)


class ErrorTaxonomyTests(unittest.TestCase):
    def test_packaged_taxonomy_is_preloaded_through_executor(self) -> None:
        class Hass:
            def __init__(self) -> None:
                self.targets: list[object] = []

            async def async_add_executor_job(self, target, *args):
                self.targets.append(target)
                return target(*args)

        hass = Hass()
        asyncio.run(async_preload_error_policies(hass))
        self.assertEqual([error_policies], hass.targets)

    def test_packaged_taxonomy_matches_contracts_0_63_0(self) -> None:
        self.assertEqual(
            CONTRACTS_0_63_0_SHA256,
            hashlib.sha256(TAXONOMY_PATH.read_bytes()).hexdigest(),
        )
        payload = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            {"name": "hausman-hub-error-taxonomy", "version": 1},
            payload["contract"],
        )
        self.assertEqual(21, len(payload["entries"]))
        self.assertEqual(2, len(payload["detailPolicies"]))

    def test_every_policy_builds_one_safe_error_envelope(self) -> None:
        schema = json.loads(ERROR_SCHEMA_PATH.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        for code, policy in error_policies().items():
            with self.subTest(code=code):
                payload = api_error_payload(code)
                self.assertEqual(code, payload["code"])
                self.assertEqual(policy["safeMessage"], payload["message"])
                self.assertEqual(policy["retryable"], payload["retryable"])
                self.assertEqual(policy["httpStatus"], api_error_status(code))
                self.assertNotIn("details", payload)
                validator.validate(payload)

    def test_unknown_code_fails_closed_without_echo(self) -> None:
        payload = api_error_payload("private_exception_text")
        self.assertEqual("internal_error", payload["code"])
        self.assertNotIn("private_exception_text", json.dumps(payload))
        self.assertEqual(500, api_error_status("private_exception_text"))

    def test_details_are_allowlisted_and_request_id_is_bounded(self) -> None:
        payload = api_error_payload(
            "revision_conflict",
            request_id="request-example",
            details={
                "expectedRevision": 3,
                "actualRevision": 4,
                "rawException": "must not escape",
            },
        )
        self.assertEqual("request-example", payload["requestId"])
        self.assertEqual(
            {"expectedRevision": 3, "actualRevision": 4},
            payload["details"],
        )
        too_long = api_error_payload("invalid_request", request_id="r" * 129)
        self.assertNotIn("requestId", too_long)

    def test_retryable_never_means_automatic_command_retry(self) -> None:
        safe_policies = {"read_only", "after_refresh", "after_delay"}
        for code, policy in error_policies().items():
            with self.subTest(code=code):
                if policy["retryable"]:
                    self.assertIn(policy["retryPolicy"], safe_policies)
                if policy["retryPolicy"] == "new_user_action":
                    self.assertFalse(policy["retryable"])


if __name__ == "__main__":
    unittest.main()
