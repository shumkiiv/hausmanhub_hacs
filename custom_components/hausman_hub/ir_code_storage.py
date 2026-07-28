"""Versioned Home Assistant storage adapter for HausmanHub IR command codes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .domain.ir_codes import (
    IR_CODE_REGISTRY_VERSION,
    IRCodeRegistry,
    IRCodeViolation,
    ir_code_registry_from_payload,
    ir_code_registry_to_payload,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class IRCodeStorageError(RuntimeError):
    """Persisted IR code data is damaged or unavailable."""


class HomeAssistantIRCodeStore:
    """Persist one complete IR code registry per HausmanHub config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        from homeassistant.helpers.storage import Store

        class _MigratingIRCodeStore(Store[dict[str, object]]):  # type: ignore[type-arg]
            """Let Home Assistant rewrite the exact legacy IR code payload once."""

            async def _async_migrate_func(
                self,
                old_major_version: int,
                old_minor_version: int,
                old_data: object,
            ) -> dict[str, object]:
                del old_major_version
                del old_minor_version
                return {"version": IR_CODE_REGISTRY_VERSION, "codes": []}

        self._store: Store[dict[str, object]] = _MigratingIRCodeStore(
            hass,
            IR_CODE_REGISTRY_VERSION,
            f"hausman_hub.ir_codes.{entry_id}",
            max_readable_version=IR_CODE_REGISTRY_VERSION,
        )

    async def async_load(self) -> IRCodeRegistry:
        """Return an empty registry only before any IR code is saved."""

        payload = await self._store.async_load()
        if payload is None:
            return IRCodeRegistry()
        try:
            return ir_code_registry_from_payload(payload)
        except IRCodeViolation as error:
            raise IRCodeStorageError("stored IR code registry is invalid") from error

    async def async_save(self, registry: IRCodeRegistry) -> None:
        """Save only the exact validated IR code payload."""

        await self._store.async_save(ir_code_registry_to_payload(registry))
