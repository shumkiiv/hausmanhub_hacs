from __future__ import annotations

import asyncio
import importlib
import sys
import unittest
from unittest.mock import AsyncMock, patch

from tests.test_local_summary_access import (
    FAKE_MODULE_NAMES,
    FakeEntry,
    FakeHomeAssistant,
    FakeJsonRequest,
    FakeRequest,
    fake_home_assistant_modules,
    reader_user,
)
from custom_components.hausman_hub.domain.ir_codes import (
    IRCodeSource,
    IRCodeViolation,
    IRCommandCode,
)
from custom_components.hausman_hub.application.ir_code_service import IRCodeBindingError


PACKAGE_MODULE = "custom_components.hausman_hub"
IR_CODE_API_MODULE = f"{PACKAGE_MODULE}.ir_code_api"
SCAN_PATH = "/api/hausman_hub/v1/admin/ir-codes/scan"
BINDINGS_PATH = "/api/hausman_hub/v1/admin/ir-codes/bindings"
IR_CODES_PATH = "/api/hausman_hub/v1/admin/ir-codes"


class IrCodeApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.previous_modules = {
            name: sys.modules.get(name)
            for name in (*FAKE_MODULE_NAMES, PACKAGE_MODULE, IR_CODE_API_MODULE)
        }
        for name in (*FAKE_MODULE_NAMES, PACKAGE_MODULE, IR_CODE_API_MODULE):
            sys.modules.pop(name, None)
        sys.modules.update(fake_home_assistant_modules())
        cls.integration = importlib.import_module(PACKAGE_MODULE)

    @classmethod
    def tearDownClass(cls) -> None:
        for name in (*FAKE_MODULE_NAMES, PACKAGE_MODULE, IR_CODE_API_MODULE):
            sys.modules.pop(name, None)
        sys.modules.update(
            {name: module for name, module in cls.previous_modules.items() if module is not None}
        )

    def setUp(self) -> None:
        self.hass = FakeHomeAssistant()
        entry = FakeEntry(
            {"mode": "read-only", "direct_execution_status": "direct_execution_blocked"},
            {},
        )
        self.hass.config_entries.entries = [entry]
        self.assertTrue(asyncio.run(self.integration.async_setup_entry(self.hass, entry)))
        self.view = {view.url: view for view in self.hass.http.views}[SCAN_PATH]
        self.bindings_view = {view.url: view for view in self.hass.http.views}[BINDINGS_PATH]
        self.import_view = {view.url: view for view in self.hass.http.views}[IR_CODES_PATH]

    def test_scan_keeps_legacy_fields_and_adds_importable_catalogs(self) -> None:
        service = self.hass.data["hausman_hub"]["ir_code_service"]
        catalog = {
            "smartir": {"1001": "Daikin FTX"},
            "broadlink_remotes": [],
            "smartir_catalog": [{"brand": "Daikin", "models": []}],
            "broadlink_catalog": [{"remote_entity_id": "remote.pult", "commands": []}],
        }
        with patch.object(service, "async_scan_catalog", new=AsyncMock(return_value=catalog)):
            response = asyncio.run(
                self.view.get(FakeRequest("127.0.0.1", reader_user("admin", admin=True), path=SCAN_PATH))
            )

        self.assertEqual(200, response.status)
        self.assertEqual({"1001": "Daikin FTX"}, response.payload["smartir"])
        self.assertEqual([], response.payload["broadlink_remotes"])
        self.assertEqual([{"brand": "Daikin", "models": []}], response.payload["smartir_catalog"])
        self.assertEqual(
            [{"remote_entity_id": "remote.pult", "commands": []}],
            response.payload["broadlink_catalog"],
        )

    def test_bindings_returns_universal_ir_runtime_bindings_to_local_admin(self) -> None:
        runtime = self.hass.data["hausman_hub"]["climate_runtime"]
        bindings = {
            "bindings": [
                {
                    "candidate_id": "candidate_0001",
                    "configured_device_id": "living_air_conditioner",
                    "remote_entity_id": "remote.living_broadlink",
                }
            ]
        }
        with patch.object(
            runtime, "async_ir_code_bindings", new=AsyncMock(return_value=bindings)
        ):
            response = asyncio.run(
                self.bindings_view.get(
                    FakeRequest(
                        "127.0.0.1",
                        reader_user("admin", admin=True),
                        path=BINDINGS_PATH,
                    )
                )
            )

        self.assertEqual(200, response.status)
        self.assertEqual(bindings, response.payload)

    def test_import_rejects_invalid_json_structure(self) -> None:
        response = asyncio.run(
            self.import_view.post(
                FakeJsonRequest(
                    "127.0.0.1",
                    reader_user("admin", admin=True),
                    IR_CODES_PATH,
                    ["not", "an", "object"],
                )
            )
        )

        self.assertEqual(400, response.status)

    def test_import_rejects_ir_code_domain_violation(self) -> None:
        service = self.hass.data["hausman_hub"]["ir_code_service"]
        payload = {
            "device_id": "living_air_conditioner",
            "remote_entity_id": "remote.living_broadlink",
            "command_name": "off",
            "code_data": "JgBQAAAB",
            "source": "broadlink",
        }
        with patch.object(
            service,
            "async_import_code",
            new=AsyncMock(side_effect=IRCodeViolation("invalid code")),
        ):
            response = asyncio.run(
                self.import_view.post(
                    FakeJsonRequest(
                        "127.0.0.1",
                        reader_user("admin", admin=True),
                        IR_CODES_PATH,
                        payload,
                    )
                )
            )

        self.assertEqual(400, response.status)

    def test_import_passes_explicit_replace_confirmation_to_service(self) -> None:
        service = self.hass.data["hausman_hub"]["ir_code_service"]
        payload = {
            "device_id": "living_air_conditioner",
            "remote_entity_id": "remote.living_broadlink",
            "command_name": "ac.off",
            "code_data": "JgBQAAAB",
            "source": "manual",
            "replace": True,
        }
        code = IRCommandCode(
            code_id="ir_test_code",
            device_id=payload["device_id"],
            remote_entity_id=payload["remote_entity_id"],
            command_name=payload["command_name"],
            code_data=payload["code_data"],
            source=IRCodeSource.MANUAL,
            created_at=1,
        )
        with patch.object(
            service,
            "async_import_code",
            new=AsyncMock(return_value=code),
        ) as import_code:
            response = asyncio.run(
                self.import_view.post(
                    FakeJsonRequest(
                        "127.0.0.1",
                        reader_user("admin", admin=True),
                        IR_CODES_PATH,
                        payload,
                    )
                )
            )

        self.assertEqual(200, response.status)
        self.assertTrue(import_code.await_args.kwargs["replace"])

    def test_import_returns_4xx_when_saved_device_remote_binding_rejects_request(self) -> None:
        service = self.hass.data["hausman_hub"]["ir_code_service"]
        payload = {
            "device_id": "living_air_conditioner",
            "remote_entity_id": "remote.other",
            "command_name": "ac.off",
            "code_data": "JgBQAAAB",
            "source": "manual",
        }
        with patch.object(
            service,
            "async_import_code",
            new=AsyncMock(side_effect=IRCodeBindingError("IR code remote does not match saved device.")),
        ):
            response = asyncio.run(
                self.import_view.post(
                    FakeJsonRequest(
                        "127.0.0.1",
                        reader_user("admin", admin=True),
                        IR_CODES_PATH,
                        payload,
                    )
                )
            )

        self.assertEqual(422, response.status)
