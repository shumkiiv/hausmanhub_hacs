"""Tests for read-only SmartIR and Broadlink IR source scanners."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from custom_components.hausman_hub.application.ir_code_sources import (
    browse_broadlink_codes,
    browse_smartir_codes,
    read_broadlink_command_code_data,
    read_broadlink_codes,
    read_smartir_command_code_data,
    scan_smartir_commands,
    scan_smartir_device_codes,
)


SMARTIR_DAIKIN = {
    "manufacturer": "Daikin",
    "supportedModels": ["FTXS35K", "FTXS42K"],
    "commands": {
        "off": "b64:DAIKIN_OFF",
        "cool": {"auto": {"24": "b64:DAIKIN_COOL_AUTO_24"}},
        "heat": {"low": {"20": "b64:DAIKIN_HEAT_LOW_20"}},
    },
}

BROADLINK_STORE = {
    "version": 1,
    "minor_version": 1,
    "key": "broadlink_remote_unique_remote_codes",
    "data": {
        "living_ac": {
            "power": "JgBQAAAB",
            "cool_24": "JgBQAAAC",
        }
    },
}


class _SourceHass:
    """Minimal Home Assistant surface for scanner tests."""

    def __init__(self, config_dir: str, entity_registry: object) -> None:
        self.config = SimpleNamespace(config_dir=config_dir)
        self.entity_registry = entity_registry

    async def async_add_executor_job(self, target, *args):  # type: ignore[no-untyped-def]
        return target(*args)


def _remote_registry(*, unique_id: str = "unique_remote") -> object:
    return SimpleNamespace(
        entities={
            "remote-unique": SimpleNamespace(
                domain="remote",
                entity_id="remote.living_broadlink",
                unique_id=unique_id,
            )
        }
    )


class SmartIRSourceTest(unittest.IsolatedAsyncioTestCase):
    """Real SmartIR climate files flatten into stable command keys."""

    async def test_nested_climate_file_flattens_mode_fan_temperature_commands(self) -> None:
        with TemporaryDirectory() as temporary_dir:
            codes_dir = (
                Path(temporary_dir)
                / "custom_components/smartir/codes/climate"
            )
            codes_dir.mkdir(parents=True)
            (codes_dir / "1001.json").write_text(
                json.dumps(SMARTIR_DAIKIN), encoding="utf-8"
            )
            hass = _SourceHass(temporary_dir, _remote_registry())

            device_codes = await scan_smartir_device_codes(hass)
            commands = await scan_smartir_commands(hass, "1001")
            code_data = await read_smartir_command_code_data(
                hass, "1001", "ac.cool.24_0"
            )
            catalog = await browse_smartir_codes(hass)

        self.assertEqual({"1001": "Daikin FTXS35K"}, device_codes)
        self.assertEqual(
            ["ac.cool.24_0", "ac.heat.20_0", "ac.off"],
            [command.command_name for command in commands],
        )
        self.assertEqual("b64:DAIKIN_COOL_AUTO_24", code_data)
        self.assertEqual(
            [
                {
                    "brand": "Daikin",
                    "models": [
                        {
                            "device_code": "1001",
                            "model": "FTXS35K",
                            "name": "Daikin FTXS35K",
                            "commands": [
                                {
                                    "command_name": "ac.cool.24_0",
                                    "code_data": "b64:DAIKIN_COOL_AUTO_24",
                                },
                                {
                                    "command_name": "ac.heat.20_0",
                                    "code_data": "b64:DAIKIN_HEAT_LOW_20",
                                },
                                {
                                    "command_name": "ac.off",
                                    "code_data": "b64:DAIKIN_OFF",
                                },
                            ],
                        }
                    ],
                }
            ],
            catalog,
        )

    async def test_oversized_smartir_file_is_skipped(self) -> None:
        with TemporaryDirectory() as temporary_dir:
            codes_dir = (
                Path(temporary_dir)
                / "custom_components/smartir/codes/climate"
            )
            codes_dir.mkdir(parents=True)
            (codes_dir / "1001.json").write_text(
                json.dumps(SMARTIR_DAIKIN), encoding="utf-8"
            )
            hass = _SourceHass(temporary_dir, _remote_registry())

            with patch(
                "custom_components.hausman_hub.application.ir_code_sources.MAX_SOURCE_FILE_BYTES",
                1,
            ):
                result = await scan_smartir_device_codes(hass)

        self.assertEqual({}, result)


class BroadlinkSourceTest(unittest.IsolatedAsyncioTestCase):
    """Real Broadlink Store files resolve remotes through the entity registry."""

    async def test_store_wrapper_uses_registry_unique_id_not_filename_entity_guess(self) -> None:
        with TemporaryDirectory() as temporary_dir:
            storage_dir = Path(temporary_dir) / ".storage"
            storage_dir.mkdir()
            (storage_dir / "broadlink_remote_unique_remote_codes").write_text(
                json.dumps(BROADLINK_STORE), encoding="utf-8"
            )
            hass = _SourceHass(temporary_dir, _remote_registry())

            commands = await read_broadlink_codes(hass)
            code_data = await read_broadlink_command_code_data(
                hass, "remote.living_broadlink", "cool_24"
            )
            catalog = await browse_broadlink_codes(hass)

        self.assertEqual(
            ["cool_24", "power"], [command.command_name for command in commands]
        )
        self.assertTrue(
            all(command.remote_entity_id == "remote.living_broadlink" for command in commands)
        )
        self.assertEqual("JgBQAAAC", code_data)
        self.assertEqual(
            [
                {
                    "remote_entity_id": "remote.living_broadlink",
                    "commands": [
                        {"command_name": "cool_24", "code_data": "JgBQAAAC", "slot": 0},
                        {"command_name": "power", "code_data": "JgBQAAAB", "slot": 0},
                    ],
                }
            ],
            catalog,
        )

    async def test_unresolvable_store_file_is_skipped(self) -> None:
        with TemporaryDirectory() as temporary_dir:
            storage_dir = Path(temporary_dir) / ".storage"
            storage_dir.mkdir()
            (storage_dir / "broadlink_remote_unknown_codes").write_text(
                json.dumps(BROADLINK_STORE), encoding="utf-8"
            )
            hass = _SourceHass(temporary_dir, _remote_registry())

            result = await read_broadlink_codes(hass)

        self.assertEqual([], result)
