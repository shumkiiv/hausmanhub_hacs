#!/usr/bin/env python3
"""Keep the README release version in sync with the integration manifest.

The tool reads and writes only local files. It does not contact Home
Assistant, a home, devices, Node-RED, or the internet.

Default mode rewrites the README version line from `manifest.json`.
`--check` mode is fail-closed: it exits with 1 when the README drifts from
the manifest, so the local release check and CI stop the publication.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = Path("custom_components/hausman_hub/manifest.json")
README_PATH = Path("README.md")
VERSION_LINE_PATTERN = re.compile(
    r"Текущая версия — \*\*(?P<version>[^*]+)\*\*"
)
VERSION_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


class ReadmeVersionSyncError(RuntimeError):
    """Explain why the README version could not be read or written."""


def manifest_version(root: Path) -> str:
    """Return the integration version recorded in the manifest."""

    manifest_file = root / MANIFEST_PATH
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReadmeVersionSyncError(
            f"Cannot read {MANIFEST_PATH}: {exc}"
        ) from exc
    version = manifest.get("version")
    if not isinstance(version, str) or not VERSION_PATTERN.match(version):
        raise ReadmeVersionSyncError(
            f"Manifest version is missing or malformed: {version!r}"
        )
    return version


def readme_text(root: Path) -> str:
    """Return the current README text."""

    try:
        return (root / README_PATH).read_text(encoding="utf-8")
    except OSError as exc:
        raise ReadmeVersionSyncError(f"Cannot read {README_PATH}: {exc}") from exc


def readme_version(text: str) -> str:
    """Return the version shown on the README status line."""

    match = VERSION_LINE_PATTERN.search(text)
    if match is None:
        raise ReadmeVersionSyncError(
            "README has no 'Текущая версия — **X**' status line."
        )
    return match.group("version")


def synced_readme_text(text: str, version: str) -> str:
    """Return the README text with the manifest version on the status line."""

    return VERSION_LINE_PATTERN.sub(
        f"Текущая версия — **{version}**", text, count=1
    )


def main(argv: tuple[str, ...]) -> int:
    """Sync the README version or check the drift in `--check` mode."""

    check_only = "--check" in argv
    try:
        version = manifest_version(REPOSITORY_ROOT)
        text = readme_text(REPOSITORY_ROOT)
        shown = readme_version(text)
        if shown == version:
            print(f"README version matches the manifest: {version}.")
            return 0
        if check_only:
            print(
                f"README shows {shown}, manifest declares {version}. "
                "Run tools/sync_readme_version.py before publishing.",
                file=sys.stderr,
            )
            return 1
        (REPOSITORY_ROOT / README_PATH).write_text(
            synced_readme_text(text, version), encoding="utf-8"
        )
    except ReadmeVersionSyncError as exc:
        print(f"README version sync failed: {exc}", file=sys.stderr)
        return 1
    print(f"README version updated: {shown} -> {version}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(tuple(sys.argv[1:])))
