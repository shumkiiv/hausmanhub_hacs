from __future__ import annotations

import unittest

from custom_components.hausman_hub.device_action_api import (
    _execution_failure_response,
)
from custom_components.hausman_hub.device_discovery_ha import _values


class _SupportedRegistry:
    def __init__(self, entries: tuple[object, ...]) -> None:
        self._entries = entries
        self.devices = self

    def __iter__(self):
        return iter(self._entries)


class _EntriesOnlyRegistry:
    def __init__(self, entries: tuple[object, ...]) -> None:
        self._entries = entries

    def async_entries(self):
        return list(self._entries)


class DeviceActionBoundary152225Tests(unittest.TestCase):
    def test_post_dispatch_failure_is_structured_unknown_without_false_not_sent(self) -> None:
        response = _execution_failure_response(
            target_id="entity_intercom",
            action_id="turn_on",
            request_id="dispatch.intercom.1",
            correlation_id="corr.intercom.1",
            target_type="switch",
            dispatch_crossed=True,
        )

        self.assertEqual(409, response["status"])
        payload = response["payload"]
        self.assertEqual("conflict", payload["code"])
        self.assertEqual("dispatch_unknown", payload["details"]["state"])
        self.assertNotIn("physicalCommandsSent", payload["details"])
        self.assertNotIn("accepted", payload)

    def test_pre_dispatch_failure_is_retryable_and_does_not_claim_dispatch(self) -> None:
        response = _execution_failure_response(
            target_id="entity_intercom",
            action_id="turn_on",
            request_id="dispatch.intercom.2",
            correlation_id="corr.intercom.2",
            target_type="switch",
            dispatch_crossed=False,
        )

        self.assertEqual(503, response["status"])
        payload = response["payload"]
        self.assertEqual("unavailable", payload["code"])
        self.assertTrue(payload["retryable"])
        self.assertNotIn("physicalCommandsSent", payload)

    def test_discovery_uses_iterable_registry_without_mapping_methods(self) -> None:
        entries = (object(), object())
        registry = _SupportedRegistry(entries)
        self.assertEqual(entries, _values(registry, "devices"))

    def test_discovery_accepts_supported_async_entries_registry_api(self) -> None:
        entries = (object(), object())
        self.assertEqual(entries, _values(_EntriesOnlyRegistry(entries), "devices"))
