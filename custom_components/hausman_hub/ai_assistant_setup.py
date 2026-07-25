from __future__ import annotations

import time
from typing import TYPE_CHECKING

from .application.ai_assistant import AiAssistantService
from .application.ai_assistant_client import OpenAiCompatibleTransport
from .application.ai_assistant_config import ai_assistant_binding_from_entry_data
from .domain.ai_assistant import AiAssistantViolation
from .ai_assistant_schedule import async_start_ai_assistant_schedule
from .ai_assistant_storage import HomeAssistantAiAssistantStore

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .application.climate_runtime import ClimateRuntime


async def async_start_ai_assistant(
    hass: HomeAssistant,
    entry: ConfigEntry,
    runtime: ClimateRuntime,
) -> AiAssistantService:
    try:
        binding = ai_assistant_binding_from_entry_data(entry.data)
    except AiAssistantViolation:
        binding = None

    def session_factory():
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        return async_get_clientsession(hass)

    transport = OpenAiCompatibleTransport(session_factory)
    if (
        binding is not None
        and binding.settings is not None
        and binding.settings.enabled
        and binding.api_key is not None
    ):
        from aiohttp import ClientError

        transport = OpenAiCompatibleTransport(session_factory, ClientError)
    service = AiAssistantService(
        settings=None if binding is None else binding.settings,
        api_key=None if binding is None else binding.api_key,
        store=HomeAssistantAiAssistantStore(hass, entry.entry_id),
        evidence_reader=runtime.async_ai_evidence_snapshot,
        transport=transport,
        now_ms=lambda: int(time.time() * 1000),
    )
    await service.async_start()
    await async_start_ai_assistant_schedule(hass, entry, service)
    return service
