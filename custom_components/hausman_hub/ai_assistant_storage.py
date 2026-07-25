from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.storage import Store

from .application.ai_assistant_storage import (
    AI_ASSISTANT_STORAGE_VERSION,
    ai_assistant_state_from_payload,
    ai_assistant_state_to_payload,
)
from .domain.ai_assistant import AiAssistantViolation
from .domain.ai_assistant_state import AiAssistantState
from .domain.ai_assistant_json import AiJsonObject

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class AiAssistantStorageError(RuntimeError):
    pass


class HomeAssistantAiAssistantStore:
    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[AiJsonObject] = Store(
            hass,
            AI_ASSISTANT_STORAGE_VERSION,
            f"hausman_hub.ai_assistant.{entry_id}",
        )

    async def async_load(self) -> AiAssistantState:
        payload = await self._store.async_load()
        if payload is None:
            return AiAssistantState()
        try:
            return ai_assistant_state_from_payload(payload)
        except AiAssistantViolation as error:
            raise AiAssistantStorageError("stored AI assistant state is invalid") from error

    async def async_save(self, state: AiAssistantState) -> None:
        await self._store.async_save(ai_assistant_state_to_payload(state))
