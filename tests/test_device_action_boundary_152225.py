from __future__ import annotations

import sys
import unittest

from tests.test_local_summary_access import fake_home_assistant_modules


_FAKE_HOME_ASSISTANT_MODULES = fake_home_assistant_modules()
_PREVIOUS_HOME_ASSISTANT_MODULES = {
    name: sys.modules.get(name) for name in _FAKE_HOME_ASSISTANT_MODULES
}
sys.modules.update(_FAKE_HOME_ASSISTANT_MODULES)

from custom_components.hausman_hub.device_action_api import (
    _execution_failure_response,
)
from custom_components.hausman_hub.device_discovery_ha import _values

for _module_name in _FAKE_HOME_ASSISTANT_MODULES:
    sys.modules.pop(_module_name, None)
sys.modules.update(
    {
        _module_name: _module
        for _module_name, _module in _PREVIOUS_HOME_ASSISTANT_MODULES.items()
        if _module is not None
    }
)


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

    def test_discovery_returns_mapping_values_not_legacy_registry_keys(self) -> None:
        entries = {"device-one": object(), "device-two": object()}
        registry = type("Registry", (), {"devices": entries})()
        self.assertEqual(tuple(entries.values()), _values(registry, "devices"))

    def test_discovery_does_not_consume_async_or_parameterized_accessors(self) -> None:
        async def asynchronous_entries() -> list[object]:
            return [object()]

        class Registry:
            devices = None
            async_entries = staticmethod(asynchronous_entries)

        self.assertEqual((), _values(Registry(), "devices"))

        class ParameterizedRegistry:
            devices = None

            @staticmethod
            def async_entries(domain: str) -> list[object]:
                raise AssertionError(domain)

        self.assertEqual((), _values(ParameterizedRegistry(), "devices"))
