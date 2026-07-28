"""Read-only asynchronous scanners for external SmartIR and Broadlink codes."""

from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


MAX_SOURCE_FILES = 128
MAX_SOURCE_FILE_BYTES = 1_048_576
MAX_SOURCE_CODES = 1_024
_SMARTIR_CODES_PATH = ("custom_components", "smartir", "codes", "climate")
_BROADLINK_FILE_PREFIX = "broadlink_remote_"
_BROADLINK_FILE_SUFFIX = "_codes"


class SmartIRCodeInfo:
    """One flattened command available from a SmartIR device-code file."""

    __slots__ = ("device_code", "device_name", "command_name")

    def __init__(self, device_code: str, device_name: str, command_name: str) -> None:
        self.device_code = device_code
        self.device_name = device_name
        self.command_name = command_name


class BroadlinkCodeInfo:
    """One learned command from a registry-resolved Broadlink Store file."""

    __slots__ = ("remote_entity_id", "command_name", "slot")

    def __init__(self, remote_entity_id: str, command_name: str, slot: int) -> None:
        self.remote_entity_id = remote_entity_id
        self.command_name = command_name
        self.slot = slot


async def scan_smartir_device_codes(hass: HomeAssistant) -> dict[str, str]:
    """Return bounded SmartIR climate device files without blocking the event loop."""

    return await hass.async_add_executor_job(
        _scan_smartir_device_codes, hass.config.config_dir
    )


async def scan_smartir_commands(
    hass: HomeAssistant, device_code: str
) -> list[SmartIRCodeInfo]:
    """Return flattened SmartIR command names for one bounded device file."""

    return await hass.async_add_executor_job(
        _scan_smartir_commands, hass.config.config_dir, device_code
    )


async def read_smartir_command_code_data(
    hass: HomeAssistant, device_code: str, command_name: str
) -> str | None:
    """Read one flattened SmartIR command payload without blocking the event loop."""

    return await hass.async_add_executor_job(
        _read_smartir_command_code_data,
        hass.config.config_dir,
        device_code,
        command_name,
    )


async def browse_smartir_codes(hass: HomeAssistant) -> list[dict[str, object]]:
    """Return a JSON-safe SmartIR catalog from real climate code files."""

    return await hass.async_add_executor_job(
        _browse_smartir_codes, hass.config.config_dir
    )


async def read_broadlink_codes(hass: HomeAssistant) -> list[BroadlinkCodeInfo]:
    """Return bounded Broadlink Store commands resolved through the HA registry."""

    return await hass.async_add_executor_job(
        _read_broadlink_codes,
        hass.config.config_dir,
        _broadlink_remote_entity_ids(hass),
    )


async def read_broadlink_command_code_data(
    hass: HomeAssistant, remote_entity_id: str, command_name: str
) -> str | None:
    """Read one registry-resolved Broadlink command without blocking the event loop."""

    return await hass.async_add_executor_job(
        _read_broadlink_command_code_data,
        hass.config.config_dir,
        _broadlink_remote_entity_ids(hass),
        remote_entity_id,
        command_name,
    )


async def browse_broadlink_codes(hass: HomeAssistant) -> list[dict[str, object]]:
    """Return a JSON-safe Broadlink Store catalog grouped by remote entity id."""

    return await hass.async_add_executor_job(
        _browse_broadlink_codes,
        hass.config.config_dir,
        _broadlink_remote_entity_ids(hass),
    )


class HomeAssistantIRCodeCatalog:
    """Outer adapter that reads Home Assistant-bound SmartIR and Broadlink sources."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def async_scan_catalog(self) -> dict[str, object]:
        smartir_codes = await scan_smartir_device_codes(self._hass)
        broadlink_codes = await read_broadlink_codes(self._hass)
        return {
            "smartir": smartir_codes,
            "broadlink_remotes": [
                {
                    "remote_entity_id": code.remote_entity_id,
                    "command_name": code.command_name,
                    "slot": code.slot,
                }
                for code in broadlink_codes
            ],
            "smartir_catalog": await browse_smartir_codes(self._hass),
            "broadlink_catalog": await browse_broadlink_codes(self._hass),
        }

    async def async_read_broadlink_command_code_data(
        self, remote_entity_id: str, command_name: str
    ) -> str | None:
        return await read_broadlink_command_code_data(
            self._hass, remote_entity_id, command_name
        )


def _smartir_codes_dir(config_dir: str) -> Path:
    return Path(config_dir).joinpath(*_SMARTIR_CODES_PATH)


def _scan_smartir_device_codes(config_dir: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in _json_files(_smartir_codes_dir(config_dir)):
        payload = _read_json_file(path)
        if payload is None:
            continue
        result[path.stem] = _smartir_device_name(payload, path.stem)
    return result


def _scan_smartir_commands(config_dir: str, device_code: str) -> list[SmartIRCodeInfo]:
    payload = _read_json_file(_smartir_codes_dir(config_dir) / f"{device_code}.json")
    if payload is None:
        return []
    device_name = _smartir_device_name(payload, device_code)
    return [
        SmartIRCodeInfo(device_code, device_name, command_name)
        for command_name in sorted(_smartir_command_codes(payload))
    ]


def _read_smartir_command_code_data(
    config_dir: str, device_code: str, command_name: str
) -> str | None:
    payload = _read_json_file(_smartir_codes_dir(config_dir) / f"{device_code}.json")
    if payload is None:
        return None
    return _smartir_command_codes(payload).get(command_name)


def _browse_smartir_codes(config_dir: str) -> list[dict[str, object]]:
    brands: dict[str, list[dict[str, object]]] = {}
    for path in _json_files(_smartir_codes_dir(config_dir)):
        payload = _read_json_file(path)
        if payload is None:
            continue
        device_name = _smartir_device_name(payload, path.stem)
        name_parts = device_name.split(maxsplit=1)
        brand = name_parts[0] if name_parts else "SmartIR"
        model = name_parts[1] if len(name_parts) == 2 else path.stem
        commands = [
            {"command_name": name, "code_data": value}
            for name, value in sorted(_smartir_command_codes(payload).items())
        ]
        if commands:
            brands.setdefault(brand, []).append(
                {
                    "device_code": path.stem,
                    "model": model,
                    "name": device_name,
                    "commands": commands,
                }
            )
    return [{"brand": brand, "models": brands[brand]} for brand in sorted(brands)]


def _smartir_device_name(payload: Mapping[str, object], fallback: str) -> str:
    device_name = payload.get("device_name")
    if isinstance(device_name, str) and device_name.strip():
        return device_name.strip()
    manufacturer = payload.get("manufacturer")
    supported_models = payload.get("supportedModels")
    model = next(
        (
            value.strip()
            for value in supported_models
            if isinstance(value, str) and value.strip()
        ),
        "",
    ) if isinstance(supported_models, list) else ""
    name_parts = [
        value.strip()
        for value in (manufacturer, model)
        if isinstance(value, str) and value.strip()
    ]
    return " ".join(name_parts) or fallback


def _smartir_command_codes(payload: Mapping[str, object]) -> dict[str, str]:
    commands = payload.get("commands")
    if not isinstance(commands, Mapping):
        return {}
    result: dict[str, str] = {}

    def visit(value: object, path: tuple[str, ...]) -> None:
        if len(result) >= MAX_SOURCE_CODES:
            return
        if isinstance(value, str):
            command_name = _canonical_smartir_command_key(path)
            if command_name and value:
                result[command_name] = value
            return
        if not isinstance(value, Mapping):
            return
        for name, child in sorted(value.items()):
            if isinstance(name, str) and name.strip():
                visit(child, (*path, name.strip()))

    visit(commands, ())
    return result


def _canonical_smartir_command_key(path: tuple[str, ...]) -> str:
    if path == ("off",):
        return "ac.off"
    if (
        len(path) >= 2
        and path[0] in {"cool", "heat"}
        and _smartir_temperature_key(path[-1]) is not None
    ):
        return f"ac.{path[0]}.{_smartir_temperature_key(path[-1])}"
    return ":".join(path)


def _smartir_temperature_key(value: str) -> str | None:
    try:
        temperature = float(value)
    except ValueError:
        return None
    return f"{temperature:.1f}".replace(".", "_")


def _broadlink_remote_entity_ids(hass: HomeAssistant) -> dict[str, str]:
    try:
        from homeassistant.helpers import entity_registry
    except ImportError:
        registry = getattr(hass, "entity_registry", None)
    else:
        registry = entity_registry.async_get(hass)
    entries = getattr(registry, "entities", {})
    if not isinstance(entries, Mapping):
        return {}
    remotes: dict[str, str] = {}
    for entry in entries.values():
        unique_id = getattr(entry, "unique_id", None)
        entity_id = getattr(entry, "entity_id", None)
        if (
            isinstance(unique_id, str)
            and isinstance(entity_id, str)
            and entity_id.startswith("remote.")
        ):
            remotes[unique_id] = entity_id
    return remotes


def _read_broadlink_codes(
    config_dir: str, remote_by_unique_id: Mapping[str, str]
) -> list[BroadlinkCodeInfo]:
    results: list[BroadlinkCodeInfo] = []
    for path in _broadlink_storage_files(config_dir):
        remote_entity_id = remote_by_unique_id.get(_broadlink_file_unique_id(path))
        if remote_entity_id is None:
            continue
        payload = _read_json_file(path)
        if payload is None:
            continue
        for command_name in sorted(_broadlink_command_codes(payload)):
            if len(results) >= MAX_SOURCE_CODES:
                return results
            results.append(BroadlinkCodeInfo(remote_entity_id, command_name, 0))
    return results


def _read_broadlink_command_code_data(
    config_dir: str,
    remote_by_unique_id: Mapping[str, str],
    remote_entity_id: str,
    command_name: str,
) -> str | None:
    matched: str | None = None
    for path in _broadlink_storage_files(config_dir):
        if remote_by_unique_id.get(_broadlink_file_unique_id(path)) != remote_entity_id:
            continue
        payload = _read_json_file(path)
        if payload is None:
            continue
        code_data = _broadlink_command_codes(payload).get(command_name)
        if code_data is None:
            continue
        if matched is not None:
            return None
        matched = code_data
    return matched


def _browse_broadlink_codes(
    config_dir: str, remote_by_unique_id: Mapping[str, str]
) -> list[dict[str, object]]:
    remotes: dict[str, list[dict[str, object]]] = {}
    for path in _broadlink_storage_files(config_dir):
        remote_entity_id = remote_by_unique_id.get(_broadlink_file_unique_id(path))
        if remote_entity_id is None:
            continue
        payload = _read_json_file(path)
        if payload is None:
            continue
        for command_name, code_data in sorted(_broadlink_command_codes(payload).items()):
            commands = remotes.setdefault(remote_entity_id, [])
            if len(commands) >= MAX_SOURCE_CODES:
                break
            commands.append(
                {"command_name": command_name, "code_data": code_data, "slot": 0}
            )
    return [
        {
            "remote_entity_id": remote_entity_id,
            "commands": sorted(
                commands, key=lambda command: (command["command_name"], command["slot"])
            ),
        }
        for remote_entity_id, commands in sorted(remotes.items())
    ]


def _broadlink_storage_files(config_dir: str) -> list[Path]:
    storage_dir = Path(config_dir) / ".storage"
    if not storage_dir.is_dir():
        return []
    return [
        path
        for path in sorted(storage_dir.iterdir(), key=lambda path: path.name)
        if path.is_file()
        and path.name.startswith(_BROADLINK_FILE_PREFIX)
        and path.name.endswith(_BROADLINK_FILE_SUFFIX)
    ][:MAX_SOURCE_FILES]


def _broadlink_file_unique_id(path: Path) -> str:
    return path.name.removeprefix(_BROADLINK_FILE_PREFIX).removesuffix(
        _BROADLINK_FILE_SUFFIX
    )


def _broadlink_command_codes(payload: Mapping[str, object]) -> dict[str, str]:
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return {}
    result: dict[str, str] = {}
    duplicates: set[str] = set()
    for _, commands in sorted(data.items()):
        if not isinstance(commands, Mapping):
            continue
        for command_name, code_data in sorted(commands.items()):
            if len(result) >= MAX_SOURCE_CODES:
                return result
            if not isinstance(command_name, str) or not command_name or not isinstance(code_data, str):
                continue
            if command_name in result:
                duplicates.add(command_name)
                result.pop(command_name, None)
                continue
            if command_name not in duplicates and code_data:
                result[command_name] = code_data
    return result


def _json_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix == ".json"
        ),
        key=lambda path: path.name,
    )[:MAX_SOURCE_FILES]


def _read_json_file(path: Path) -> Mapping[str, object] | None:
    try:
        if path.stat().st_size > MAX_SOURCE_FILE_BYTES:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, Mapping) else None
