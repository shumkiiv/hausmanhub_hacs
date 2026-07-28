"""Read-only scanners for IR code sources external to HausmanHub.

SmartIR stores its command codes as JSON files under the HA config directory
(``custom_components/smartir/codes/climate/<device_code>.json``).  Broadlink
remotes persist learned commands in ``<config>/.storage/broadlink_remote_*_codes``.

Both scanners are fully defensive: any malformed file is silently skipped.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..domain.ir_codes import IRCommandCode, IRCodeSource, generate_code_id

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class SmartIRCodeInfo:
    """One available command name from a SmartIR device-code file."""

    __slots__ = ("device_code", "device_name", "command_name")

    def __init__(self, device_code: str, device_name: str, command_name: str) -> None:
        self.device_code = device_code
        self.device_name = device_name
        self.command_name = command_name


class BroadlinkCodeInfo:
    """One learned command from a Broadlink remote storage file."""

    __slots__ = ("remote_entity_id", "command_name", "slot")

    def __init__(self, remote_entity_id: str, command_name: str, slot: int) -> None:
        self.remote_entity_id = remote_entity_id
        self.command_name = command_name
        self.slot = slot


# ---------------------------------------------------------------------------
# SmartIR scanner
# ---------------------------------------------------------------------------

def _smartir_codes_dir(hass: HomeAssistant) -> Path:
    """Return the SmartIR climate codes directory path."""
    config_dir = Path(hass.config.config_dir)
    return config_dir / "custom_components" / "smartir" / "codes" / "climate"


def scan_smartir_device_codes(
    hass: HomeAssistant,
) -> dict[str, str]:
    """Scan available SmartIR climate device-code files.

    Returns a mapping of ``device_code -> device_name`` for every valid JSON
    file in the SmartIR climate codes directory.  Missing directory or empty
    result is not an error.
    """
    codes_dir = _smartir_codes_dir(hass)
    if not codes_dir.is_dir():
        return {}
    result: dict[str, str] = {}
    for entry in codes_dir.iterdir():
        if not entry.is_file() or not entry.suffix == ".json":
            continue
        device_code = entry.stem
        try:
            data: dict[str, Any] = json.loads(entry.read_text(encoding="utf-8"))
            device_name = str(data.get("device_name", device_code))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        result[device_code] = device_name
    return result


def scan_smartir_commands(
    hass: HomeAssistant,
    device_code: str,
) -> list[SmartIRCodeInfo]:
    """Scan command names for one SmartIR device-code file.

    Returns a list of ``SmartIRCodeInfo`` for every command found in the
    ``commands`` dict of the SmartIR JSON.  Missing file or malformed content
    returns an empty list.
    """
    codes_dir = _smartir_codes_dir(hass)
    target = codes_dir / f"{device_code}.json"
    if not target.is_file():
        return []
    try:
        data: dict[str, Any] = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return []
    commands = data.get("commands")
    if not isinstance(commands, dict):
        return []
    device_name = str(data.get("device_name", device_code))
    result: list[SmartIRCodeInfo] = []
    for command_name in sorted(commands.keys()):
        result.append(
            SmartIRCodeInfo(
                device_code=device_code,
                device_name=device_name,
                command_name=command_name,
            )
        )
    return result


def read_smartir_command_code_data(
    hass: HomeAssistant,
    device_code: str,
    command_name: str,
) -> str | None:
    """Read the raw code_data string for one SmartIR command.

    Returns the Base64 or hex-encoded IR payload as a string, or ``None``
    if the file/command is missing or malformed.
    """
    codes_dir = _smartir_codes_dir(hass)
    target = codes_dir / f"{device_code}.json"
    if not target.is_file():
        return None
    try:
        data: dict[str, Any] = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    commands = data.get("commands")
    if not isinstance(commands, dict):
        return None
    code_value = commands.get(command_name)
    if code_value is None:
        return None
    return str(code_value)


# ---------------------------------------------------------------------------
# Broadlink code reader
# ---------------------------------------------------------------------------

def _broadlink_storage_glob(hass: HomeAssistant) -> list[Path]:
    """Return all Broadlink remote code storage files."""
    storage_dir = Path(hass.config.config_dir) / ".storage"
    if not storage_dir.is_dir():
        return []
    return sorted(
        p
        for p in storage_dir.iterdir()
        if p.is_file() and p.name.startswith("broadlink_remote_") and p.name.endswith("_codes")
    )


def read_broadlink_codes(
    hass: HomeAssistant,
) -> list[BroadlinkCodeInfo]:
    """Parse all Broadlink remote code storage files.

    Returns a flat list of ``BroadlinkCodeInfo`` for every learned command
    found across all remote devices.  Malformed entries are silently skipped.
    """
    results: list[BroadlinkCodeInfo] = []
    for path in _broadlink_storage_glob(hass):
        # Derive the remote entity_id from the filename:
        # broadlink_remote_<host>_<mac>_codes -> remote.<mac>
        raw_name = path.name.removeprefix("broadlink_remote_").removesuffix("_codes")
        parts = raw_name.rsplit("_", 1)
        mac_suffix = parts[-1].replace("-", "_").lower() if len(parts) >= 2 else raw_name
        entity_id = f"remote.{mac_suffix}"
        try:
            data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        # Broadlink storage structure: {"commands": {"command_name": "base64...", ...}}
        commands = data.get("commands")
        if isinstance(commands, dict):
            for cmd_name, cmd_value in commands.items():
                if isinstance(cmd_value, str):
                    results.append(
                        BroadlinkCodeInfo(
                            remote_entity_id=entity_id,
                            command_name=cmd_name,
                            slot=0,
                        )
                    )
        # Also scan "s" (scenes) and "packet" keys for slot-based codes
        for slot_key in ("s", "a", "b", "c", "d"):
            slot_data = data.get(slot_key)
            if not isinstance(slot_data, dict):
                continue
            for cmd_name, cmd_value in slot_data.items():
                if isinstance(cmd_value, str):
                    results.append(
                        BroadlinkCodeInfo(
                            remote_entity_id=entity_id,
                            command_name=cmd_name,
                            slot=ord(slot_key[-1]) - ord("a") + 1 if len(slot_key) == 1 else 0,
                        )
                    )
    return results


def read_broadlink_command_code_data(
    hass: HomeAssistant,
    remote_entity_id: str,
    command_name: str,
) -> str | None:
    """Read the raw code_data string for one Broadlink command.

    ``remote_entity_id`` should be ``remote.<mac_suffix>`` matching the
    storage filename.  Returns the raw Base64 payload or ``None``.
    """
    # Reconstruct the storage filename from entity_id
    mac_part = remote_entity_id.removeprefix("remote.")
    storage_dir = Path(hass.config.config_dir) / ".storage"
    if not storage_dir.is_dir():
        return None
    # Search for the matching file
    prefix = "broadlink_remote_"
    suffix = f"_{mac_part.replace('_', '-')}_codes"
    # Also try without dashes
    suffix_alt = f"_{mac_part}_codes"
    for path in storage_dir.iterdir():
        if not path.is_file():
            continue
        if not path.name.startswith(prefix) or not path.name.endswith("_codes"):
            continue
        if mac_part not in path.name:
            continue
        try:
            data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        # Direct command match
        commands = data.get("commands")
        if isinstance(commands, dict) and command_name in commands:
            value = commands[command_name]
            return str(value) if isinstance(value, str) else None
        # Slot-based search
        for slot_key in ("s", "a", "b", "c", "d"):
            slot_data = data.get(slot_key)
            if isinstance(slot_data, dict) and command_name in slot_data:
                value = slot_data[command_name]
                return str(value) if isinstance(value, str) else None
    return None
