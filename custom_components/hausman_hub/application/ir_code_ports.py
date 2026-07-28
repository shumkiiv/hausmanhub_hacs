"""Application ports for IR persistence, source catalogues, transport, and bindings."""

from __future__ import annotations

from typing import Protocol

from ..domain.ir_codes import IRCodeRegistry


class IRCodeRepository(Protocol):
    """Persist the local immutable IR-code registry."""

    async def async_load(self) -> IRCodeRegistry | None: ...

    async def async_save(self, registry: IRCodeRegistry) -> None: ...


class IRCodeCatalog(Protocol):
    """Read external IR catalogues without changing their source files."""

    async def async_scan_catalog(self) -> dict[str, object]: ...

    async def async_read_broadlink_command_code_data(
        self, remote_entity_id: str, command_name: str
    ) -> str | None: ...


class IRCodeTransmitter(Protocol):
    """Perform Home Assistant remote learning and test transmission."""

    async def async_learn_command(
        self, device_id: str, remote_entity_id: str, command_name: str, timeout_seconds: float
    ) -> None: ...

    async def async_send_command(
        self, device_id: str, remote_entity_id: str, code_data: str
    ) -> None: ...


class IRCodeBindingValidator(Protocol):
    """Verify that an IR code targets one saved raw-remote climate device."""

    async def async_validate_ir_code_binding(
        self, device_id: str, remote_entity_id: str
    ) -> str | None: ...
