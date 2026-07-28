"""Application service for HausmanHub IR code CRUD and HA service calls.

Coordinates the IR code registry with Home Assistant remote entity services:
learn, test-send, delete, and source scanning.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, TYPE_CHECKING

from ..domain.ir_codes import (
    IRCodeRegistry,
    IRCodeSource,
    IRCodeViolation,
    IRCommandCode,
    generate_code_id,
    ir_code_registry_from_payload,
    ir_code_registry_to_payload,
)
from ..ir_code_storage import HomeAssistantIRCodeStore, IRCodeStorageError

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class IRCodeServiceError(Exception):
    """Base error for IR code service operations."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


class IRCodeNotFoundError(IRCodeServiceError):
    def __init__(self, code_id: str):
        super().__init__(f"IR code {code_id!r} not found", status=404)


class IRCodeLearnTimeoutError(IRCodeServiceError):
    def __init__(self, remote_entity_id: str):
        super().__init__(
            f"IR learn timed out on {remote_entity_id!r}",
            status=408,
        )


class IRCodeLearnRejectedError(IRCodeServiceError):
    def __init__(self, remote_entity_id: str, reason: str):
        super().__init__(
            f"IR learn rejected on {remote_entity_id!r}: {reason}",
            status=422,
        )


class IRCodeSendError(IRCodeServiceError):
    def __init__(self, remote_entity_id: str, reason: str):
        super().__init__(
            f"IR send failed on {remote_entity_id!r}: {reason}",
            status=502,
        )


class IRCodeService:
    """Coordinate IR code persistence, scanning, and HA service calls."""

    def __init__(
        self,
        hass: HomeAssistant,
        store: HomeAssistantIRCodeStore,
    ):
        self._hass = hass
        self._store = store
        self._registry: IRCodeRegistry | None = None
        self._lock = asyncio.Lock()

    @property
    def registry(self) -> IRCodeRegistry | None:
        """Return the loaded registry, or None if not yet loaded."""
        return self._registry

    async def async_load(self) -> None:
        """Load persisted IR codes; fall back to an empty registry."""
        loaded = await self._store.async_load()
        if isinstance(loaded, IRCodeRegistry):
            self._registry = loaded
        else:
            self._registry = IRCodeRegistry()

    async def async_save(self) -> None:
        """Persist the current registry."""
        if self._registry is None:
            raise IRCodeServiceError("no registry loaded", status=500)
        await self._store.async_save(self._registry)

    # -------------------------------------------------------------------
    # Query
    # -------------------------------------------------------------------

    def codes_for_device(self, device_id: str) -> tuple[IRCommandCode, ...]:
        """Return all IR codes registered for a logical device."""
        if self._registry is None:
            return ()
        return self._registry.codes_for_device(device_id)

    def code_for_command(
        self, device_id: str, command_name: str
    ) -> IRCommandCode | None:
        """Return the best IR code for a device + command pair."""
        if self._registry is None:
            return None
        return self._registry.code_for_command(device_id, command_name)

    def code_by_id(self, code_id: str) -> IRCommandCode | None:
        """Return one IR code by stable id."""
        if self._registry is None:
            return None
        return self._registry.code(code_id)

    def all_codes(self) -> tuple[IRCommandCode, ...]:
        """Return all registered IR codes."""
        if self._registry is None:
            return ()
        return self._registry.codes

    # -------------------------------------------------------------------
    # Learn (blocks until remote confirms or times out)
    # -------------------------------------------------------------------

    async def async_learn_code(
        self,
        device_id: str,
        remote_entity_id: str,
        command_name: str,
        source: IRCodeSource = IRCodeSource.MANUAL,
        timeout_seconds: float = 30.0,
    ) -> IRCommandCode:
        """Trigger ``remote.learn_command`` and wait for confirmation.

        Raises IRCodeLearnTimeoutError or IRCodeLearnRejectedError on failure.
        Returns the newly learned IRCommandCode on success.
        """
        learn_data: dict[str, Any] = {
            "entity_id": remote_entity_id,
            "command_type": "ir",
            "command": command_name,
        }

        try:
            result = await asyncio.wait_for(
                self._hass.services.async_call(
                    "remote", "learn_command", learn_data, blocking=True,
                ),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError as err:
            raise IRCodeLearnTimeoutError(remote_entity_id) from err
        except Exception as err:
            raise IRCodeLearnRejectedError(
                remote_entity_id, str(err)
            ) from err

        # After successful learn, the Broadlink integration stores the code.
        # Read it back from the Broadlink storage.
        from .ir_code_sources import read_broadlink_command_code_data

        code_data = read_broadlink_command_code_data(
            self._hass, remote_entity_id, command_name
        )
        if not code_data:
            raise IRCodeLearnRejectedError(
                remote_entity_id,
                "learn succeeded but code data was not found in Broadlink storage",
            )

        now = int(time.time())
        code_id = generate_code_id(device_id, command_name)
        code = IRCommandCode(
            code_id=code_id,
            device_id=device_id,
            remote_entity_id=remote_entity_id,
            command_name=command_name,
            code_data=code_data,
            source=source,
            created_at=now,
        )

        async with self._lock:
            assert self._registry is not None
            self._registry = self._registry.with_code(code)
            await self.async_save()

        return code

    # -------------------------------------------------------------------
    # Test-send
    # -------------------------------------------------------------------

    async def async_test_send(
        self,
        remote_entity_id: str,
        code_data: str,
        command_name: str,
    ) -> None:
        """Send one IR code via ``remote.send_command``.

        Raises IRCodeSendError on failure.
        """
        send_data: dict[str, Any] = {
            "entity_id": remote_entity_id,
            "command": code_data,
        }
        try:
            await self._hass.services.async_call(
                "remote", "send_command", send_data, blocking=True,
            )
        except Exception as err:
            raise IRCodeSendError(remote_entity_id, str(err)) from err

    # -------------------------------------------------------------------
    # Import (from SmartIR / Broadlink scanner)
    # -------------------------------------------------------------------

    async def async_import_code(
        self,
        device_id: str,
        remote_entity_id: str,
        command_name: str,
        code_data: str,
        source: IRCodeSource,
    ) -> IRCommandCode:
        """Import an IR code from an external source into the registry."""
        now = int(time.time())
        code_id = generate_code_id(device_id, command_name)
        code = IRCommandCode(
            code_id=code_id,
            device_id=device_id,
            remote_entity_id=remote_entity_id,
            command_name=command_name,
            code_data=code_data,
            source=source,
            created_at=now,
        )
        async with self._lock:
            assert self._registry is not None
            self._registry = self._registry.with_code(code)
            await self.async_save()
        return code

    # -------------------------------------------------------------------
    # Delete
    # -------------------------------------------------------------------

    async def async_delete_code(self, code_id: str) -> None:
        """Remove one IR code from the registry by stable id."""
        async with self._lock:
            assert self._registry is not None
            if self._registry.code(code_id) is None:
                raise IRCodeNotFoundError(code_id)
            self._registry = self._registry.without(code_id)
            await self.async_save()

    async def async_delete_device_codes(self, device_id: str) -> int:
        """Remove all IR codes for a device. Returns count removed."""
        async with self._lock:
            assert self._registry is not None
            before = self._registry
            remaining = tuple(
                c for c in before.codes if c.device_id != device_id
            )
            removed = len(before.codes) - len(remaining)
            self._registry = IRCodeRegistry(
                version=before.version, codes=remaining,
            )
            await self.async_save()
            return removed
