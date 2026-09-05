"""Verified atomic Home Assistant storage for safety-critical state."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Protocol


class SafetyStoreBackend(Protocol):
    version: int
    key: str
    path: str

    async def async_load(self) -> object | None: ...

    async def async_save(self, payload: Mapping[str, object]) -> None: ...


RunSync = Callable[..., Awaitable[object]]
PayloadValidator = Callable[[object], bool]


class VerifiedSafetyStore:
    """Detect swallowed HA Store write errors and corrupt-file recovery."""

    def __init__(
        self,
        backend: SafetyStoreBackend,
        run_sync: RunSync,
        *,
        payload_validator: PayloadValidator | None = None,
    ) -> None:
        self._backend = backend
        self._run_sync = run_sync
        self._payload_validator = payload_validator or (lambda value: True)
        self._previous_path = f"{self._backend.path}.previous"
        self._recovery_latch_path = f"{self._backend.path}.recovery-required"
        self.recovered_previous = False

    async def async_load(self) -> object | None:
        if await self._run_sync(_is_file, self._recovery_latch_path):
            self.recovered_previous = True
        existed = bool(await self._run_sync(_is_file, self._backend.path))
        payload = await self._backend.async_load()
        if payload is not None and self._payload_validator(payload):
            return payload
        previous = await self._run_sync(_read_document, self._previous_path)
        previous_payload = _document_payload(
            previous,
            version=self._backend.version,
            key=self._backend.key,
        )
        if previous_payload is not None and self._payload_validator(previous_payload):
            # Latch ambiguity before rewriting the current file. A later
            # restart must not silently treat the N-1 generation as current
            # execution authority.
            await self._run_sync(
                _write_document_atomic,
                self._recovery_latch_path,
                {"recoveryRequired": True},
            )
            await self._backend.async_save(previous_payload)
            persisted = await self._run_sync(_read_document, self._backend.path)
            if _document_payload(
                persisted,
                version=self._backend.version,
                key=self._backend.key,
            ) != previous_payload:
                raise RuntimeError(
                    f"safety store {self._backend.key} N-1 restore failed"
                )
            self.recovered_previous = True
            return previous_payload
        if payload is None and not existed:
            return None
        raise RuntimeError(f"safety store {self._backend.key} is unreadable")

    async def async_save(self, payload: dict[str, object]) -> None:
        if not self._payload_validator(payload):
            raise RuntimeError(f"safety store {self._backend.key} payload is invalid")
        current = await self._run_sync(_read_document, self._backend.path)
        current_payload = _document_payload(
            current,
            version=self._backend.version,
            key=self._backend.key,
        )
        if current_payload is not None and self._payload_validator(current_payload):
            await self._run_sync(_write_document_atomic, self._previous_path, current)
        await self._backend.async_save(payload)
        persisted = await self._run_sync(_read_document, self._backend.path)
        if not isinstance(persisted, Mapping):
            raise RuntimeError(f"safety store {self._backend.key} was not written")
        if (
            persisted.get("version") != self._backend.version
            or persisted.get("key") != self._backend.key
            or persisted.get("data") != payload
        ):
            raise RuntimeError(
                f"safety store {self._backend.key} write verification failed"
            )


def _is_file(path: str) -> bool:
    return Path(path).is_file()


def _read_document(path: str) -> object:
    try:
        with Path(path).open(encoding="utf-8") as source:
            return json.load(source)
    except (OSError, ValueError):
        return None


def _document_payload(
    value: object, *, version: int, key: str
) -> dict[str, object] | None:
    if (
        not isinstance(value, Mapping)
        or value.get("version") != version
        or value.get("key") != key
        or not isinstance(value.get("data"), Mapping)
    ):
        return None
    return dict(value["data"])


def _write_document_atomic(path: str, payload: object) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as destination:
            json.dump(payload, destination, ensure_ascii=False, separators=(",", ":"))
            destination.flush()
            os.fsync(destination.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, target)
    finally:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
