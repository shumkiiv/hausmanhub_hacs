from __future__ import annotations

import asyncio
import importlib
import json
import sys
import unittest

from custom_components.hausman_hub.application.configuration import (
    effective_configuration,
)
from tests.test_local_summary_access import (
    FAKE_MODULE_NAMES,
    FakeEntry,
    FakeHomeAssistant,
    FakeJsonRequest,
    FakeRequest,
    fake_home_assistant_modules,
    reader_user,
)


PACKAGE_MODULE = "custom_components.hausman_hub"
CLIMATE_API_MODULE = f"{PACKAGE_MODULE}.climate_api"
PATH = "/api/hausman_hub/v1/admin/ai-assistant"
SETTINGS_PATH = f"{PATH}/settings"
REFRESH_PATH = f"{PATH}/refresh"


class AiAssistantApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.previous_modules = {
            name: sys.modules.get(name)
            for name in (*FAKE_MODULE_NAMES, PACKAGE_MODULE, CLIMATE_API_MODULE)
        }
        for name in (*FAKE_MODULE_NAMES, PACKAGE_MODULE, CLIMATE_API_MODULE):
            sys.modules.pop(name, None)
        sys.modules.update(fake_home_assistant_modules())
        cls.integration = importlib.import_module(PACKAGE_MODULE)

    @classmethod
    def tearDownClass(cls) -> None:
        for name in (*FAKE_MODULE_NAMES, PACKAGE_MODULE, CLIMATE_API_MODULE):
            sys.modules.pop(name, None)
        sys.modules.update(
            {name: module for name, module in cls.previous_modules.items() if module is not None}
        )

    def setUp(self) -> None:
        self.hass = FakeHomeAssistant()
        self.entry = FakeEntry(
            {
                "mode": "read-only",
                "direct_execution_status": "direct_execution_blocked",
            },
            {},
        )
        self.hass.config_entries.entries = [self.entry]
        self.assertTrue(asyncio.run(self.integration.async_setup_entry(self.hass, self.entry)))
        self.views = {view.url: view for view in self.hass.http.views}
        self.updates: list[dict[str, object]] = []

        def async_update_entry(entry, *, data=None, options=None):
            if data is not None:
                entry.data = dict(data)
                self.updates.append(dict(data))
            if options is not None:
                entry.options = dict(options)

        self.hass.config_entries.async_update_entry = async_update_entry

    def _admin(self) -> object:
        return reader_user("system-admin", admin=True)

    def _get(self, path: str, user: object):
        return asyncio.run(self.views[path].get(FakeRequest("127.0.0.1", user, path=path)))

    def _post(self, path: str, payload: dict[str, object], user: object):
        return asyncio.run(
            self.views[path].post(FakeJsonRequest("127.0.0.1", user, path, payload))
        )

    def _settings_payload(self, **extra: object) -> dict[str, object]:
        return {
            "enabled": True,
            "preset": "custom",
            "base_url": "https://provider.example/v1",
            "model": "advisory-test",
            **extra,
        }

    def test_get_and_settings_write_mask_api_key(self) -> None:
        saved = self._post(
            SETTINGS_PATH,
            self._settings_payload(api_key="test-key-123"),
            self._admin(),
        )
        current = self._get(PATH, self._admin())

        self.assertEqual(200, saved.status)
        self.assertTrue(saved.payload["settings"]["key_set"])
        self.assertNotIn("test-key-123", json.dumps(saved.payload))
        self.assertEqual(200, current.status)
        self.assertTrue(current.payload["settings"]["key_set"])
        self.assertNotIn("test-key-123", json.dumps(current.payload))
        self.assertEqual("test-key-123", self.entry.data["ai_assistant_api_key"])
        effective_configuration(self.entry.data, self.entry.options)

    def test_settings_preserve_omitted_key_and_clear_explicit_key(self) -> None:
        self._post(
            SETTINGS_PATH,
            self._settings_payload(api_key="test-key-123"),
            self._admin(),
        )
        preserved = self._post(
            SETTINGS_PATH,
            self._settings_payload(model="advisory-next"),
            self._admin(),
        )
        cleared = self._post(
            SETTINGS_PATH,
            self._settings_payload(clear_key=True),
            self._admin(),
        )

        self.assertTrue(preserved.payload["settings"]["key_set"])
        self.assertFalse(cleared.payload["settings"]["key_set"])
        self.assertNotIn("ai_assistant_api_key", self.entry.data)
        current = self._get(PATH, self._admin())
        self.assertEqual("advisory-test", current.payload["settings"]["model"])

    def test_settings_reject_ssrf_and_non_admin_is_forbidden(self) -> None:
        rejected = self._post(
            SETTINGS_PATH,
            self._settings_payload(base_url="http://169.254.169.254/latest"),
            self._admin(),
        )
        denied = self._get(PATH, reader_user("system-users"))

        self.assertEqual(400, rejected.status)
        self.assertEqual("invalid_base_url", rejected.payload["error"])
        self.assertEqual(403, denied.status)

    def test_refresh_returns_structured_unconfigured_advisory(self) -> None:
        response = self._post(REFRESH_PATH, {}, self._admin())

        self.assertEqual(200, response.status)
        self.assertEqual("unconfigured", response.payload["advisory"]["status"])
