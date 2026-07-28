"""Unit tests for the HomeAssistantIRCodeStore adapter."""

from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import MagicMock, patch

from custom_components.hausman_hub.domain.ir_codes import (
    IR_CODE_REGISTRY_VERSION,
    IRCodeRegistry,
    IRCommandCode,
    IRCodeSource,
    IRCodeViolation,
    ir_code_registry_to_payload,
)


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
        code_data="JgBQAAABK5QUERQRFDU=",
        source=IRCodeSource.MANUAL,
        created_at=1_800_000_000,
    )


def _fake_ha_storage_modules() -> dict[str, types.ModuleType]:
    """Provide a per-test Store fake even after another test imports HA storage."""

    modules: dict[str, types.ModuleType] = {}
    if "homeassistant" not in sys.modules:
        homeassistant = types.ModuleType("homeassistant")
        homeassistant.__path__ = []
        modules["homeassistant"] = homeassistant
    if "homeassistant.core" not in sys.modules:
        core = types.ModuleType("homeassistant.core")
        core.HomeAssistant = object  # type: ignore[attr-defined]
        modules["homeassistant.core"] = core
    if "homeassistant.helpers" not in sys.modules:
        helpers = types.ModuleType("homeassistant.helpers")
        helpers.__path__ = []
        modules["homeassistant.helpers"] = helpers

    storage = types.ModuleType("homeassistant.helpers.storage")

    class _FakeStore:
        """Minimal stand-in for homeassistant Store."""

        def __class_getitem__(cls, item: object) -> type:
            return cls  # Allow Store[dict[str, object]] syntax

        def __init__(self, hass: object, version: int, key: str, **kw: object) -> None:
            self.hass = hass
            self.version = version
            self.key = key
            self._data: dict[str, object] | None = None

        async def async_load(self) -> dict[str, object] | None:
            return self._data

        async def async_save(self, data: dict[str, object]) -> None:
            self._data = data

    storage.Store = _FakeStore  # type: ignore[attr-defined]
    modules["homeassistant.helpers.storage"] = storage
    return modules


class HomeAssistantIRCodeStoreTest(unittest.IsolatedAsyncioTestCase):
    """Test the HA Store adapter for IR code persistence."""

    def setUp(self) -> None:
        self.mock_hass = MagicMock()
        self.mock_hass.config.config_dir = "/config"
        self._storage_modules = patch.dict(
            sys.modules,
            _fake_ha_storage_modules(),
        )
        self._storage_modules.start()

    def tearDown(self) -> None:
        self._storage_modules.stop()

    def _make_store(self) -> "HomeAssistantIRCodeStore":
        from custom_components.hausman_hub.ir_code_storage import (
            HomeAssistantIRCodeStore,
        )

        return HomeAssistantIRCodeStore(self.mock_hass, "entry_123")

    def test_store_initializes_without_error(self) -> None:
        store = self._make_store()
        self.assertIsNotNone(store._store)

    async def test_load_returns_empty_registry_when_no_data(self) -> None:
        store = self._make_store()
        registry = await store.async_load()
        self.assertEqual(0, len(registry.codes))
        self.assertEqual(IR_CODE_REGISTRY_VERSION, registry.version)

    async def test_load_returns_registry_from_valid_payload(self) -> None:
        code = _code()
        payload = {
            "version": IR_CODE_REGISTRY_VERSION,
            "codes": [
                {
                    "code_id": code.code_id,
                    "device_id": code.device_id,
                    "remote_entity_id": code.remote_entity_id,
                    "command_name": code.command_name,
                    "code_data": code.code_data,
                    "source": code.source.value,
                    "created_at": code.created_at,
                }
            ],
        }
        store = self._make_store()
        # Pre-populate the fake store's internal data
        store._store._data = payload
        registry = await store.async_load()
        self.assertEqual(1, len(registry.codes))
        self.assertEqual("ir_test_code", registry.codes[0].code_id)

    async def test_load_raises_on_invalid_payload(self) -> None:
        from custom_components.hausman_hub.ir_code_storage import (
            IRCodeStorageError,
        )

        store = self._make_store()
        store._store._data = {"version": 999, "codes": []}
        with self.assertRaises(IRCodeStorageError):
            await store.async_load()

    async def test_save_persists_registry(self) -> None:
        store = self._make_store()
        registry = IRCodeRegistry(codes=(_code(),))
        await store.async_save(registry)
        saved = store._store._data
        self.assertIsNotNone(saved)
        self.assertEqual(IR_CODE_REGISTRY_VERSION, saved["version"])  # type: ignore[index]
        self.assertEqual(1, len(saved["codes"]))  # type: ignore[index]

    async def test_save_empty_registry(self) -> None:
        store = self._make_store()
        registry = IRCodeRegistry()
        await store.async_save(registry)
        saved = store._store._data
        self.assertIsNotNone(saved)
        self.assertEqual([], saved["codes"])  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
