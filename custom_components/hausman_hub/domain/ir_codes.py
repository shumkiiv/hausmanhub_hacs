"""Versioned IR command code domain for HausmanHub.

Stores learned or imported IR/RF command codes mapped to logical climate
devices.  The model is pure: no Home Assistant, HTTP, or storage imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
import time


IR_CODE_REGISTRY_VERSION = 1
_MAX_STABLE_ID = 64
_MAX_ENTITY_ID = 255
_MAX_CODE_DATA = 65536
_MAX_COMMAND_NAME = 128
_MAX_SOURCE_LENGTH = 32

_STABLE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_ENTITY_ID = re.compile(r"^[a-z][a-z0-9_]*\.[a-z0-9_]+$")


class IRCodeViolation(ValueError):
    """An IR code value is unsafe or internally inconsistent."""


class IRCodeSource(StrEnum):
    """How the IR command code was obtained."""

    SMARTIR = "smartir"
    BROADLINK = "broadlink"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class IRCommandCode:
    """One IR/RF command code bound to a logical climate device."""

    code_id: str
    device_id: str
    remote_entity_id: str
    command_name: str
    code_data: str
    source: IRCodeSource
    created_at: int

    def __post_init__(self) -> None:
        if not isinstance(self.code_id, str) or not _STABLE_ID.fullmatch(self.code_id):
            raise IRCodeViolation("code id must be a stable HausmanHub id")
        if not isinstance(self.device_id, str) or not _STABLE_ID.fullmatch(self.device_id):
            raise IRCodeViolation("device id must be a stable HausmanHub id")
        if (
            not isinstance(self.remote_entity_id, str)
            or not self.remote_entity_id
            or not _ENTITY_ID.fullmatch(self.remote_entity_id)
        ):
            raise IRCodeViolation("remote entity id must be a valid HA entity id")
        if (
            not isinstance(self.command_name, str)
            or not self.command_name
            or len(self.command_name) > _MAX_COMMAND_NAME
        ):
            raise IRCodeViolation("command name is required and must be bounded")
        if (
            not isinstance(self.code_data, str)
            or not self.code_data
            or len(self.code_data) > _MAX_CODE_DATA
        ):
            raise IRCodeViolation("code data is required and must be bounded")
        if not isinstance(self.source, IRCodeSource):
            raise IRCodeViolation("code source must be approved")
        if type(self.created_at) is not int or self.created_at < 0:
            raise IRCodeViolation("created_at must be a non-negative timestamp")


@dataclass(frozen=True, slots=True)
class IRCodeRegistry:
    """A complete set of learned or imported IR command codes."""

    version: int = IR_CODE_REGISTRY_VERSION
    codes: tuple[IRCommandCode, ...] = ()

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != IR_CODE_REGISTRY_VERSION:
            raise IRCodeViolation("IR code registry version is unsupported")
        if type(self.codes) is not tuple:
            raise IRCodeViolation("codes must be an immutable tuple")
        ids = [code.code_id for code in self.codes]
        if len(ids) != len(set(ids)):
            raise IRCodeViolation("code ids must be unique")

    def code(self, code_id: str) -> IRCommandCode | None:
        """Return one code by stable id."""
        return next((c for c in self.codes if c.code_id == code_id), None)

    def codes_for_device(self, device_id: str) -> tuple[IRCommandCode, ...]:
        """Return all codes registered for one logical device."""
        return tuple(c for c in self.codes if c.device_id == device_id)

    def code_for_command(
        self, device_id: str, command_name: str
    ) -> IRCommandCode | None:
        """Return the most-recent code for a device + command pair."""
        matches = [
            c
            for c in self.codes
            if c.device_id == device_id and c.command_name == command_name
        ]
        if not matches:
            return None
        return max(matches, key=lambda c: c.created_at)

    def without(self, code_id: str) -> IRCodeRegistry:
        """Return a new registry with one code removed."""
        return IRCodeRegistry(
            version=self.version,
            codes=tuple(c for c in self.codes if c.code_id != code_id),
        )

    def with_code(self, code: IRCommandCode) -> IRCodeRegistry:
        """Return a new registry with a code added or replaced."""
        existing = [c for c in self.codes if c.code_id != code.code_id]
        return IRCodeRegistry(
            version=self.version,
            codes=(*existing, code),
        )


def generate_code_id(device_id: str, command_name: str) -> str:
    """Create a deterministic stable id from device + command."""
    import hashlib

    raw = f"{device_id}:{command_name}"
    digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"ir_{digest}"


def ir_code_registry_from_payload(payload: object) -> IRCodeRegistry:
    """Reconstruct an IRCodeRegistry from a persisted dict payload."""

    if not isinstance(payload, dict):
        raise IRCodeViolation("IR code payload must be a dict")
    version = payload.get("version")
    if version != IR_CODE_REGISTRY_VERSION:
        raise IRCodeViolation("stored IR code version is unsupported")
    raw_codes = payload.get("codes")
    if raw_codes is None:
        return IRCodeRegistry()
    if not isinstance(raw_codes, list):
        raise IRCodeViolation("codes must be a list")
    codes: list[IRCommandCode] = []
    for item in raw_codes:
        if not isinstance(item, dict):
            raise IRCodeViolation("each code must be a dict")
        codes.append(
            IRCommandCode(
                code_id=str(item.get("code_id", "")),
                device_id=str(item.get("device_id", "")),
                remote_entity_id=str(item.get("remote_entity_id", "")),
                command_name=str(item.get("command_name", "")),
                code_data=str(item.get("code_data", "")),
                source=IRCodeSource(str(item.get("source", ""))),
                created_at=int(item.get("created_at", 0)),
            )
        )
    return IRCodeRegistry(version=IR_CODE_REGISTRY_VERSION, codes=tuple(codes))


def ir_code_registry_to_payload(registry: IRCodeRegistry) -> dict[str, object]:
    """Serialize an IRCodeRegistry to a persisted dict payload."""

    return {
        "version": registry.version,
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
            for code in registry.codes
        ],
    }
