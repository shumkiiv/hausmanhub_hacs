"""Unit tests for the IRCodeService application service."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from custom_components.hausman_hub.application.ir_code_service import (
    IRCodeBindingError,
    IRCodeLearnRejectedError,
    IRCodeLearnTimeoutError,
    IRCodeNotFoundError,
    IRCodeSourcePriorityError,
    IRCodeSendError,
    IRCodeService,
)
from custom_components.hausman_hub.domain.ir_codes import (
    IRCodeRegistry,
    IRCodeSource,
    IRCommandCode,
)
from custom_components.hausman_hub.ir_code_storage import HomeAssistantIRCodeStore


def _code(
    *,
    code_id: str = "ir_test_code",
    device_id: str = "test_device",
    command_name: str = "on",
) -> IRCommandCode:
    return IRCommandCode(
        code_id=code_id,
        device_id=device_id,
        remote_entity_id="remote.test_ir",
        command_name=command_name,
        code_data="JgBQAAAB",
        source=IRCodeSource.MANUAL,
        created_at=1_800_000_000,
    )


class _ServiceTestCase(unittest.IsolatedAsyncioTestCase):
    """Base test case with mocked service dependencies."""

    async def asyncSetUp(self) -> None:
        self.mock_hass = MagicMock()
        self.mock_store = MagicMock(spec=HomeAssistantIRCodeStore)
        self.mock_store.async_load = AsyncMock(return_value=IRCodeRegistry())
        self.mock_store.async_save = AsyncMock()
        self.mock_catalog = MagicMock()
        self.mock_catalog.async_read_broadlink_command_code_data = AsyncMock()
        self.mock_catalog.async_scan_catalog = AsyncMock(return_value={})
        self.mock_transmitter = MagicMock()
        self.mock_transmitter.async_learn_command = AsyncMock()
        self.mock_transmitter.async_send_command = AsyncMock()
        self.service = IRCodeService(
            self.mock_store, self.mock_catalog, self.mock_transmitter
        )
        await self.service.async_load()


class IRCodeServiceLoadTest(_ServiceTestCase):
    """Test loading and saving the registry."""

    async def test_load_initializes_empty_registry(self) -> None:
        self.assertIsNotNone(self.service.registry)
        self.assertEqual(0, len(self.service.all_codes()))

    async def test_load_with_persisted_data(self) -> None:
        code = _code()
        self.mock_store.async_load.return_value = IRCodeRegistry(codes=(code,))
        service = IRCodeService(
            self.mock_store, self.mock_catalog, self.mock_transmitter
        )
        await service.async_load()
        self.assertEqual(1, len(service.all_codes()))

    async def test_save_persists_current_registry(self) -> None:
        await self.service.async_save()
        self.mock_store.async_save.assert_called_once_with(self.service.registry)

    async def test_save_raises_when_no_registry(self) -> None:
        service = IRCodeService(
            self.mock_store, self.mock_catalog, self.mock_transmitter
        )
        with self.assertRaises(Exception):
            await service.async_save()


class IRCodeServiceQueryTest(_ServiceTestCase):
    """Test query methods on the service."""

    async def test_codes_for_device_returns_matching(self) -> None:
        await self.service.async_import_code(
            "dev1", "remote.r1", "on", "DATA1", IRCodeSource.MANUAL
        )
        await self.service.async_import_code(
            "dev1", "remote.r1", "off", "DATA2", IRCodeSource.MANUAL
        )
        await self.service.async_import_code(
            "dev2", "remote.r2", "on", "DATA3", IRCodeSource.MANUAL
        )
        codes = self.service.codes_for_device("dev1")
        self.assertEqual(2, len(codes))

    async def test_code_for_command_returns_best_match(self) -> None:
        await self.service.async_import_code(
            "dev1", "remote.r1", "on", "DATA_OLD", IRCodeSource.MANUAL
        )
        await self.service.async_import_code(
            "dev1", "remote.r1", "on", "DATA_NEW", IRCodeSource.MANUAL
        )
        code = self.service.code_for_command("dev1", "on")
        self.assertIsNotNone(code)
        self.assertEqual("DATA_NEW", code.code_data)

    async def test_code_by_id_returns_existing(self) -> None:
        await self.service.async_import_code(
            "dev1", "remote.r1", "on", "DATA", IRCodeSource.MANUAL
        )
        codes = self.service.all_codes()
        found = self.service.code_by_id(codes[0].code_id)
        self.assertIsNotNone(found)

    async def test_code_by_id_returns_none_for_unknown(self) -> None:
        self.assertIsNone(self.service.code_by_id("ir_nonexistent"))

    async def test_query_returns_empty_when_no_registry(self) -> None:
        service = IRCodeService(
            self.mock_store, self.mock_catalog, self.mock_transmitter
        )
        self.assertEqual((), service.codes_for_device("dev1"))
        self.assertIsNone(service.code_for_command("dev1", "on"))
        self.assertIsNone(service.code_by_id("ir_x"))
        self.assertEqual((), service.all_codes())


class IRCodeServiceImportTest(_ServiceTestCase):
    """Test code import."""

    async def test_import_adds_code_to_registry(self) -> None:
        code = await self.service.async_import_code(
            "dev1", "remote.r1", "on", "ABC", IRCodeSource.SMARTIR
        )
        self.assertEqual("ABC", code.code_data)
        self.assertEqual(IRCodeSource.SMARTIR, code.source)
        self.assertEqual(1, len(self.service.all_codes()))
        self.mock_store.async_save.assert_called()

    async def test_import_replaces_same_id(self) -> None:
        await self.service.async_import_code(
            "dev1", "remote.r1", "on", "OLD", IRCodeSource.MANUAL
        )
        await self.service.async_import_code(
            "dev1", "remote.r1", "on", "NEW", IRCodeSource.MANUAL
        )
        self.assertEqual(1, len(self.service.all_codes()))
        self.assertEqual("NEW", self.service.all_codes()[0].code_data)

    async def test_lower_priority_source_needs_explicit_replace_confirmation(self) -> None:
        await self.service.async_import_code(
            "dev1", "remote.r1", "ac.off", "SMARTIR", IRCodeSource.SMARTIR
        )

        with self.assertRaises(IRCodeSourcePriorityError):
            await self.service.async_import_code(
                "dev1", "remote.r1", "ac.off", "MANUAL", IRCodeSource.MANUAL
            )

        replacement = await self.service.async_import_code(
            "dev1",
            "remote.r1",
            "ac.off",
            "MANUAL",
            IRCodeSource.MANUAL,
            replace=True,
        )

        self.assertEqual(IRCodeSource.MANUAL, replacement.source)

    async def test_higher_priority_source_replaces_lower_priority_without_confirmation(self) -> None:
        await self.service.async_import_code(
            "dev1", "remote.r1", "ac.off", "MANUAL", IRCodeSource.MANUAL
        )

        replacement = await self.service.async_import_code(
            "dev1", "remote.r1", "ac.off", "SMARTIR", IRCodeSource.SMARTIR
        )

        self.assertEqual(IRCodeSource.SMARTIR, replacement.source)

    async def test_import_rejects_device_or_remote_not_bound_to_saved_contour(self) -> None:
        validator = MagicMock()
        validator.async_validate_ir_code_binding = AsyncMock(
            return_value="IR code remote does not match the saved device control endpoint."
        )
        self.service.set_binding_validator(validator)

        with self.assertRaises(IRCodeBindingError):
            await self.service.async_import_code(
                "dev1", "remote.other", "ac.off", "DATA", IRCodeSource.MANUAL
            )


class IRCodeServiceDeleteTest(_ServiceTestCase):
    """Test code deletion."""

    async def test_delete_removes_code(self) -> None:
        await self.service.async_import_code(
            "dev1", "remote.r1", "on", "DATA", IRCodeSource.MANUAL
        )
        code_id = self.service.all_codes()[0].code_id
        await self.service.async_delete_code(code_id)
        self.assertEqual(0, len(self.service.all_codes()))
        self.mock_store.async_save.assert_called()

    async def test_delete_raises_for_unknown_code(self) -> None:
        with self.assertRaises(IRCodeNotFoundError):
            await self.service.async_delete_code("ir_nonexistent")

    async def test_delete_device_codes_removes_all_for_device(self) -> None:
        await self.service.async_import_code(
            "dev1", "remote.r1", "on", "D1", IRCodeSource.MANUAL
        )
        await self.service.async_import_code(
            "dev1", "remote.r1", "off", "D2", IRCodeSource.MANUAL
        )
        await self.service.async_import_code(
            "dev2", "remote.r2", "on", "D3", IRCodeSource.MANUAL
        )
        removed = await self.service.async_delete_device_codes("dev1")
        self.assertEqual(2, removed)
        self.assertEqual(1, len(self.service.all_codes()))
        self.assertEqual("dev2", self.service.all_codes()[0].device_id)


class IRCodeServiceTestSendTest(_ServiceTestCase):
    """Test remote command sending."""

    async def test_test_send_calls_remote_service(self) -> None:
        await self.service.async_test_send("remote.r1", "dev1", "ABC")
        self.mock_transmitter.async_send_command.assert_awaited_once_with(
            "dev1", "remote.r1", "ABC"
        )

    async def test_test_send_raises_on_failure(self) -> None:
        self.mock_transmitter.async_send_command.side_effect = RuntimeError("device offline")
        with self.assertRaises(IRCodeSendError):
            await self.service.async_test_send("remote.r1", "dev1", "ABC")


class IRCodeServiceLearnTest(_ServiceTestCase):
    """Test IR code learning."""

    async def test_learn_success(self) -> None:
        self.mock_catalog.async_read_broadlink_command_code_data.return_value = "JgBQAAAB"
        code = await self.service.async_learn_code(
            "dev1", "remote.r1", "cool"
        )
        self.assertIsNotNone(code)
        self.assertEqual("JgBQAAAB", code.code_data)
        self.assertEqual(IRCodeSource.MANUAL, code.source)
        self.assertEqual(1, len(self.service.all_codes()))
        self.mock_transmitter.async_learn_command.assert_awaited_once_with(
            "dev1", "remote.r1", "cool", 30.0
        )

    async def test_learn_timeout(self) -> None:
        self.mock_transmitter.async_learn_command.side_effect = asyncio.TimeoutError
        with self.assertRaises(IRCodeLearnTimeoutError):
            await self.service.async_learn_code(
                "dev1", "remote.r1", "cool", timeout_seconds=0.01
            )

    async def test_learn_rejects_on_service_error(self) -> None:
        self.mock_transmitter.async_learn_command.side_effect = ValueError("entity not found")
        with self.assertRaises(IRCodeLearnRejectedError):
            await self.service.async_learn_code("dev1", "remote.r1", "cool")


if __name__ == "__main__":
    unittest.main()
