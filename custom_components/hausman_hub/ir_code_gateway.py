"""Home Assistant adapter for IR remote service calls."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class HomeAssistantIRCodeTransmitter:
    """Send bounded learning and raw-code commands through Home Assistant."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def async_learn_command(
        self, device_id: str, remote_entity_id: str, command_name: str, timeout_seconds: float
    ) -> None:
        data: dict[str, Any] = {
            "entity_id": remote_entity_id,
            "device": device_id,
            "command_type": "ir",
            "command": command_name,
        }
        await asyncio.wait_for(
            self._hass.services.async_call("remote", "learn_command", data, blocking=True),
            timeout=timeout_seconds,
        )

    async def async_send_command(
        self, device_id: str, remote_entity_id: str, code_data: str
    ) -> None:
        await self._hass.services.async_call(
            "remote",
            "send_command",
            {
                "entity_id": remote_entity_id,
                "device": device_id,
                "command": code_data if code_data.startswith("b64:") else f"b64:{code_data}",
            },
            blocking=True,
        )
