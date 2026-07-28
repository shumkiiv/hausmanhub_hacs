"""Unit tests for SmartIR and Broadlink IR code source scanners."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from custom_components.hausman_hub.application.ir_code_sources import (
    BroadlinkCodeInfo,
    SmartIRCodeInfo,
    read_broadlink_command_code_data,
    read_broadlink_codes,
    read_smartir_command_code_data,
    scan_smartir_commands,
    scan_smartir_device_codes,
)


def _mock_hass(config_dir: str = "/config") -> MagicMock:
    hass = MagicMock()
    hass.config.config_dir = config_dir
    return hass


class ScanSmartIRDeviceCodesTest(unittest.TestCase):
    """Test SmartIR device code scanner."""

    def test_returns_empty_when_dir_missing(self) -> None:
        with patch("pathlib.Path.is_dir", return_value=False):
            result = scan_smartir_device_codes(_mock_hass())
            self.assertEqual({}, result)

    def test_skips_non_json_files(self) -> None:
        mock_dir = MagicMock()
        mock_dir.is_dir.return_value = True
        txt_file = MagicMock()
        txt_file.is_file.return_value = True
        txt_file.suffix = ".txt"
        mock_dir.iterdir.return_value = [txt_file]

        with patch("pathlib.Path.is_dir", return_value=True), \
             patch("pathlib.Path.__truediv__", return_value=mock_dir):
            result = scan_smartir_device_codes(_mock_hass())
            self.assertEqual({}, result)


class ReadSmartIRCommandCodeDataTest(unittest.TestCase):
    """Test reading code data from a SmartIR command."""

    def test_returns_none_when_file_missing(self) -> None:
        with patch("pathlib.Path.is_file", return_value=False):
            result = read_smartir_command_code_data(_mock_hass(), "1234", "on")
            self.assertIsNone(result)

    def test_returns_none_when_command_not_found(self) -> None:
        payload = {"device_name": "Test AC", "commands": {"off": "ABC123"}}
        mock_file = MagicMock()
        mock_file.is_file.return_value = True
        mock_file.read_text.return_value = json.dumps(payload)

        with patch("pathlib.Path.is_file", return_value=True), \
             patch("pathlib.Path.read_text", return_value=json.dumps(payload)):
            result = read_smartir_command_code_data(_mock_hass(), "1234", "on")
            self.assertIsNone(result)

    def test_returns_code_data_for_existing_command(self) -> None:
        payload = {"device_name": "Test AC", "commands": {"on": "ABC123"}}
        with patch("pathlib.Path.is_file", return_value=True), \
             patch("pathlib.Path.read_text", return_value=json.dumps(payload)):
            result = read_smartir_command_code_data(_mock_hass(), "1234", "on")
            self.assertEqual("ABC123", result)


class ReadBroadlinkCodesTest(unittest.TestCase):
    """Test Broadlink remote code reader."""

    def test_returns_empty_when_no_storage_files(self) -> None:
        with patch("pathlib.Path.is_dir", return_value=False):
            result = read_broadlink_codes(_mock_hass())
            self.assertEqual([], result)

    def test_parses_commands_from_storage_file(self) -> None:
        storage_data = {"commands": {"power_on": "JgBQAAAB", "power_off": "JgBQAAAC"}}
        mock_path = MagicMock()
        mock_path.name = "broadlink_remote_192_168_1_100_AA_BB_CC_DD_EE_FF_codes"
        mock_path.is_file.return_value = True
        mock_path.read_text.return_value = json.dumps(storage_data)

        mock_dir = MagicMock()
        mock_dir.is_dir.return_value = True
        mock_dir.iterdir.return_value = [mock_path]

        with patch("pathlib.Path.__truediv__", return_value=mock_dir):
            result = read_broadlink_codes(_mock_hass())
            self.assertEqual(2, len(result))
            names = {r.command_name for r in result}
            self.assertIn("power_on", names)
            self.assertIn("power_off", names)

    def test_skips_malformed_json(self) -> None:
        mock_path = MagicMock()
        mock_path.name = "broadlink_remote_192_168_1_100_AA_BB_CC_codes"
        mock_path.is_file.return_value = True
        mock_path.read_text.return_value = "not json"

        mock_dir = MagicMock()
        mock_dir.is_dir.return_value = True
        mock_dir.iterdir.return_value = [mock_path]

        with patch("pathlib.Path.__truediv__", return_value=mock_dir):
            result = read_broadlink_codes(_mock_hass())
            self.assertEqual([], result)


class ReadBroadlinkCommandCodeDataTest(unittest.TestCase):
    """Test reading a single Broadlink command."""

    def test_returns_none_when_storage_dir_missing(self) -> None:
        with patch("pathlib.Path.is_dir", return_value=False):
            result = read_broadlink_command_code_data(_mock_hass(), "remote.aa_bb_cc", "on")
            self.assertIsNone(result)

    def test_returns_code_data_for_matching_command(self) -> None:
        storage_data = {"commands": {"on": "JgBQAAAB", "off": "JgBQAAAC"}}
        mock_path = MagicMock()
        mock_path.name = "broadlink_remote_192_168_1_100_aa_bb_cc_codes"
        mock_path.is_file.return_value = True
        mock_path.read_text.return_value = json.dumps(storage_data)

        mock_dir = MagicMock()
        mock_dir.is_dir.return_value = True
        mock_dir.iterdir.return_value = [mock_path]

        with patch("pathlib.Path.__truediv__", return_value=mock_dir):
            result = read_broadlink_command_code_data(
                _mock_hass(), "remote.aa_bb_cc", "on"
            )
            self.assertEqual("JgBQAAAB", result)

    def test_returns_none_for_nonexistent_command(self) -> None:
        storage_data = {"commands": {"off": "JgBQAAAC"}}
        mock_path = MagicMock()
        mock_path.name = "broadlink_remote_192_168_1_100_aa_bb_cc_codes"
        mock_path.is_file.return_value = True
        mock_path.read_text.return_value = json.dumps(storage_data)

        mock_dir = MagicMock()
        mock_dir.is_dir.return_value = True
        mock_dir.iterdir.return_value = [mock_path]

        with patch("pathlib.Path.__truediv__", return_value=mock_dir):
            result = read_broadlink_command_code_data(
                _mock_hass(), "remote.aa_bb_cc", "on"
            )
            self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
