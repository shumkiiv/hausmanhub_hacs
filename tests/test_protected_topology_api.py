"""Boundary tests for protected-topology readiness input."""

from __future__ import annotations

import asyncio
import importlib
import sys
import unittest

from tests.test_local_summary_access import (
    FAKE_MODULE_NAMES,
    FakeHomeAssistant,
    FakeJsonRequest,
    fake_home_assistant_modules,
    reader_user,
)


PACKAGE_MODULE = "custom_components.hausman_hub"
API_MODULE = f"{PACKAGE_MODULE}.protected_topology_api"
READINESS_PATH = "/api/hausman_hub/v1/admin/protected-topology/readiness"


class _Store:
    async def async_load(self):
        return None


class ProtectedTopologyReadinessApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.previous_modules = {
            name: sys.modules.get(name)
            for name in (*FAKE_MODULE_NAMES, PACKAGE_MODULE, API_MODULE)
        }
        for name in (*FAKE_MODULE_NAMES, PACKAGE_MODULE, API_MODULE):
            sys.modules.pop(name, None)
        sys.modules.update(fake_home_assistant_modules())
        cls.protected_topology = importlib.import_module(
            f"{PACKAGE_MODULE}.application.protected_topology"
        )
        cls.api = importlib.import_module(API_MODULE)

    @classmethod
    def tearDownClass(cls) -> None:
        for name in (*FAKE_MODULE_NAMES, PACKAGE_MODULE, API_MODULE):
            sys.modules.pop(name, None)
        sys.modules.update(
            {name: module for name, module in cls.previous_modules.items() if module is not None}
        )

    def setUp(self) -> None:
        self.hass = FakeHomeAssistant()
        service = self.protected_topology.ProtectedTopologyService(_Store())
        asyncio.run(service.async_load())
        self.hass.data["hausman_hub"] = {
            self.api.DATA_PROTECTED_TOPOLOGY: service,
        }
        self.view = self.api.ProtectedTopologyReadinessView(self.hass)

    def test_readiness_rejects_empty_duplicate_invalid_and_too_many_roles(self) -> None:
        cases = (
            [],
            ["managed.test.light", "managed.test.light"],
            ["not a role"],
            [f"managed.test.role{index}" for index in range(65)],
        )
        for roles in cases:
            with self.subTest(roles=roles[:2]):
                response = asyncio.run(
                    self.view.post(
                        FakeJsonRequest(
                            "127.0.0.1",
                            reader_user("admin", admin=True),
                            READINESS_PATH,
                            {
                                "contract": {
                                    "name": "hausman-hub-protected-topology-readiness-request",
                                    "version": 1,
                                },
                                "roles": roles,
                            },
                        )
                    )
                )
                self.assertEqual(400, response.status)

