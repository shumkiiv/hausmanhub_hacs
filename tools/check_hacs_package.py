#!/usr/bin/env python3
"""Check the prepared Git package needed for manual HACS installation.

The check reads only blobs and modes from the local Git index. It does not
start Home Assistant, contact HACS, connect to a home, or change any file.
"""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import struct
import sys
from typing import Iterable, Mapping
import zlib

from check_repository_boundary import (
    RepositoryCheckError,
    RepositoryFile,
    checked_relative_path,
    run_git,
    tracked_files,
)
from check_staged_release_version import ReleaseVersionCheckError, parse_release_version


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
INTEGRATION_DIRECTORY = PurePosixPath("custom_components/hausman_hub")
HACS_METADATA_PATH = PurePosixPath("hacs.json")
MANIFEST_PATH = INTEGRATION_DIRECTORY / "manifest.json"
ICON_PATH = INTEGRATION_DIRECTORY / "brand/icon.png"
CHANGELOG_PATH = PurePosixPath("CHANGELOG.md")
LICENSE_PATH = PurePosixPath("LICENSE")
README_PATH = PurePosixPath("README.md")
TRANSLATION_PATHS = (
    INTEGRATION_DIRECTORY / "strings.json",
    INTEGRATION_DIRECTORY / "translations/en.json",
    INTEGRATION_DIRECTORY / "translations/ru.json",
)
CONTRACT_PATHS = tuple(
    INTEGRATION_DIRECTORY / "contracts" / "v1" / name
    for name in (
        "api-capabilities.schema.json",
        "climate-action-request.schema.json",
        "climate-admin-import.schema.json",
        "climate-canary-preflight-query.schema.json",
        "climate-canary-preflight.schema.json",
        "climate-home.schema.json",
        "climate-operation-query.schema.json",
        "climate-operation-receipt.schema.json",
        "climate-readiness.schema.json",
        "climate-registry-preview.schema.json",
        "climate-registry.schema.json",
        "climate-shadow-candidate-query.schema.json",
        "climate-shadow-evidence.schema.json",
        "contour-apply-preview.schema.json",
        "contour-apply-receipt.schema.json",
        "contour-apply-request.schema.json",
        "contours.schema.json",
        "temporary-temperature-request.schema.json",
    )
) + (
    INTEGRATION_DIRECTORY / "contracts" / "v2" / "climate-home.schema.json",
    INTEGRATION_DIRECTORY / "contracts" / "v2" / "contours.schema.json",
    INTEGRATION_DIRECTORY / "contracts" / "v3" / "climate-home.schema.json",
    INTEGRATION_DIRECTORY / "contracts" / "v3" / "contours.schema.json",
    INTEGRATION_DIRECTORY / "contracts" / "v4" / "climate-home.schema.json",
    INTEGRATION_DIRECTORY / "contracts" / "v4" / "contours.schema.json",
    INTEGRATION_DIRECTORY / "contracts" / "v5" / "contours.schema.json",
)
REQUIRED_PACKAGE_PATHS = (
    HACS_METADATA_PATH,
    README_PATH,
    CHANGELOG_PATH,
    LICENSE_PATH,
    INTEGRATION_DIRECTORY / "__init__.py",
    INTEGRATION_DIRECTORY / "config_flow.py",
    INTEGRATION_DIRECTORY / "sensor.py",
    INTEGRATION_DIRECTORY / "switch.py",
    INTEGRATION_DIRECTORY / "application/control.py",
    INTEGRATION_DIRECTORY / "application/api_capabilities.py",
    INTEGRATION_DIRECTORY / "application/android_climate.py",
    INTEGRATION_DIRECTORY / "application/climate_canary_preflight.py",
    INTEGRATION_DIRECTORY / "application/climate_commands.py",
    INTEGRATION_DIRECTORY / "application/climate_evidence.py",
    INTEGRATION_DIRECTORY / "application/climate_import.py",
    INTEGRATION_DIRECTORY / "application/climate_operations.py",
    INTEGRATION_DIRECTORY / "application/climate_registry.py",
    INTEGRATION_DIRECTORY / "application/climate_registry_import.py",
    INTEGRATION_DIRECTORY / "application/climate_runtime.py",
    INTEGRATION_DIRECTORY / "application/contour_apply.py",
    INTEGRATION_DIRECTORY / "application/contour_override.py",
    INTEGRATION_DIRECTORY / "application/contours.py",
    INTEGRATION_DIRECTORY / "domain/control.py",
    INTEGRATION_DIRECTORY / "domain/climate.py",
    INTEGRATION_DIRECTORY / "domain/climate_bridge.py",
    INTEGRATION_DIRECTORY / "domain/contours.py",
    INTEGRATION_DIRECTORY / "climate_api.py",
    INTEGRATION_DIRECTORY / "climate_bridge.py",
    INTEGRATION_DIRECTORY / "climate_evidence_storage.py",
    INTEGRATION_DIRECTORY / "climate_storage.py",
    INTEGRATION_DIRECTORY / "climate_schedule.py",
    INTEGRATION_DIRECTORY / "contour_storage.py",
    MANIFEST_PATH,
    ICON_PATH,
    *TRANSLATION_PATHS,
    *CONTRACT_PATHS,
)
EXPECTED_NAME = "HASC — управление домом"
EXPECTED_DOMAIN = "hausman_hub"
EXPECTED_HOME_ASSISTANT = "2026.6.4"
EXPECTED_HACS_METADATA = {
    "name": EXPECTED_NAME,
    "homeassistant": EXPECTED_HOME_ASSISTANT,
}
EXPECTED_MANIFEST_VALUES = {
    "domain": EXPECTED_DOMAIN,
    "name": EXPECTED_NAME,
    "codeowners": ["@shumkiiv"],
    "config_flow": True,
    "documentation": "https://github.com/shumkiiv/hausmanhub_hasc",
    "issue_tracker": "https://github.com/shumkiiv/hausmanhub_hasc/issues",
    "integration_type": "hub",
    "single_config_entry": True,
}
EXPECTED_MANIFEST_KEYS = frozenset((*EXPECTED_MANIFEST_VALUES, "version"))
REGULAR_FILE_MODE = "100644"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_ICON_BYTES = 1_000_000


class DuplicateJsonKeyError(ValueError):
    """Reject a JSON object whose repeated key would hide packaged data."""


def main() -> int:
    """Check the complete installation surface in the current Git index."""

    try:
        findings = find_hacs_package_violations(
            tracked_files(REPOSITORY_ROOT),
            indexed_file_modes(REPOSITORY_ROOT),
        )
    except RepositoryCheckError as exc:
        print(f"HACS package check could not run: {exc}", file=sys.stderr)
        return 2

    if findings:
        print("HACS package check failed:", file=sys.stderr)
        for finding in findings:
            print(f"- {finding}", file=sys.stderr)
        return 1

    print("HACS package check passed for the prepared Git package.")
    return 0


def find_hacs_package_violations(
    files: Iterable[RepositoryFile],
    file_modes: Mapping[PurePosixPath, str],
) -> tuple[str, ...]:
    """Return fixed, non-sensitive findings for the manual HACS package."""

    indexed_files = {file.path: file.content for file in files}
    findings = [
        f"{path}: missing required HACS package file"
        for path in REQUIRED_PACKAGE_PATHS
        if path not in indexed_files
    ]
    findings.extend(
        f"{path}: must be a regular Git file"
        for path in REQUIRED_PACKAGE_PATHS
        if path in indexed_files and file_modes.get(path) != REGULAR_FILE_MODE
    )

    hacs_metadata = read_json_object(indexed_files, HACS_METADATA_PATH, findings)
    if hacs_metadata is not None and hacs_metadata != EXPECTED_HACS_METADATA:
        findings.append(
            f"{HACS_METADATA_PATH}: must keep the approved minimal HACS metadata"
        )

    manifest = read_json_object(indexed_files, MANIFEST_PATH, findings)
    if manifest is not None:
        add_manifest_findings(manifest, findings)

    translations = [
        read_json_object(indexed_files, translation_path, findings)
        for translation_path in TRANSLATION_PATHS
    ]
    if all(translation is not None for translation in translations):
        if json_shape(translations[0]) != json_shape(translations[1]):
            findings.append(
                "translations: English and Russian files must have the same key shape"
            )

    icon = indexed_files.get(ICON_PATH)
    if icon is not None and not is_expected_icon(icon):
        findings.append(f"{ICON_PATH}: must be a 512px transparent PNG")

    changelog = read_utf8_text(indexed_files, CHANGELOG_PATH, findings)
    if manifest is not None and changelog is not None:
        version = manifest.get("version")
        if isinstance(version, str) and f"## {version} —" not in changelog:
            findings.append(f"{CHANGELOG_PATH}: must describe manifest version {version}")

    return tuple(sorted(findings))


def indexed_file_modes(root: Path) -> dict[PurePosixPath, str]:
    """Read indexed file modes without consulting the working tree."""

    completed = run_git(root, "ls-files", "--stage", "-z")
    modes: dict[PurePosixPath, str] = {}
    for raw_entry in completed.stdout.split(b"\0"):
        if not raw_entry:
            continue
        try:
            raw_header, raw_path = raw_entry.split(b"\t", 1)
            raw_mode, _, raw_stage = raw_header.split()
        except ValueError as exc:
            raise RepositoryCheckError("Git returned an invalid index entry") from exc
        if raw_stage != b"0":
            raise RepositoryCheckError("Git index contains an unresolved conflict")
        path = checked_relative_path(raw_path)
        if path in modes:
            raise RepositoryCheckError(f"Git returned duplicate index data for {path}")
        try:
            modes[path] = raw_mode.decode("ascii")
        except UnicodeDecodeError as exc:
            raise RepositoryCheckError("Git returned an invalid file mode") from exc
    return modes


def read_json_object(
    indexed_files: Mapping[PurePosixPath, bytes],
    path: PurePosixPath,
    findings: list[str],
) -> dict[str, object] | None:
    """Read one indexed JSON object or add one safe package finding."""

    text = read_utf8_text(indexed_files, path, findings)
    if text is None:
        return None
    try:
        value = json.loads(text, object_pairs_hook=unique_json_object)
    except (DuplicateJsonKeyError, json.JSONDecodeError):
        findings.append(f"{path}: must contain a valid JSON object without duplicate keys")
        return None
    if not isinstance(value, dict):
        findings.append(f"{path}: must contain a JSON object")
        return None
    return value


def read_utf8_text(
    indexed_files: Mapping[PurePosixPath, bytes],
    path: PurePosixPath,
    findings: list[str],
) -> str | None:
    """Decode one indexed text file without ever opening its working-tree path."""

    content = indexed_files.get(path)
    if content is None:
        return None
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        findings.append(f"{path}: must use UTF-8 text")
        return None


def unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Build a JSON object while rejecting keys that would silently overwrite."""

    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise DuplicateJsonKeyError(key)
        value[key] = item
    return value


def add_manifest_findings(manifest: dict[str, object], findings: list[str]) -> None:
    """Keep the installed package identity aligned with approved metadata."""

    if set(manifest) != EXPECTED_MANIFEST_KEYS:
        findings.append(f"{MANIFEST_PATH}: must keep the approved manifest fields")
    for key, expected_value in EXPECTED_MANIFEST_VALUES.items():
        if manifest.get(key) != expected_value:
            findings.append(
                f"{MANIFEST_PATH}: {key} must be {expected_value!r}"
            )

    version = manifest.get("version")
    if not isinstance(version, str):
        findings.append(f"{MANIFEST_PATH}: version must be a string")
        return
    try:
        parse_release_version(version)
    except ReleaseVersionCheckError:
        findings.append(f"{MANIFEST_PATH}: version must use three non-negative numbers")


def json_shape(value: object) -> object:
    """Reduce translated values to their nested key shape without reading text."""

    if isinstance(value, dict):
        return {key: json_shape(item) for key, item in value.items()}
    return None


def is_expected_icon(content: bytes) -> bool:
    """Validate the indexed icon without decoding or reading any external file."""

    if len(content) > MAX_ICON_BYTES or not content.startswith(PNG_SIGNATURE):
        return False

    offset = len(PNG_SIGNATURE)
    saw_ihdr = False
    saw_idat = False
    while offset < len(content):
        if offset + 12 > len(content):
            return False
        length = struct.unpack(">I", content[offset : offset + 4])[0]
        chunk_end = offset + 12 + length
        if chunk_end > len(content):
            return False
        chunk_type = content[offset + 4 : offset + 8]
        chunk_data = content[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(">I", content[offset + 8 + length : chunk_end])[0]
        if zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF != expected_crc:
            return False

        if not saw_ihdr:
            if chunk_type != b"IHDR" or length != 13:
                return False
            width, height = struct.unpack(">II", chunk_data[:8])
            if (width, height) != (512, 512) or chunk_data[8:] != b"\x08\x06\x00\x00\x00":
                return False
            saw_ihdr = True
        elif chunk_type == b"IDAT":
            saw_idat = True
        elif chunk_type == b"IEND":
            return saw_idat and length == 0 and chunk_end == len(content)

        offset = chunk_end
    return False


if __name__ == "__main__":
    raise SystemExit(main())
