from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..domain.ai_assistant import (
    AiAssistantSettings,
    AiAssistantViolation,
    AiProviderPreset,
)
from ..domain.ai_assistant_json import AiJsonObject, AiJsonValue


AI_ASSISTANT_SETTINGS_FIELD = "ai_assistant_settings"
AI_ASSISTANT_API_KEY_FIELD = "ai_assistant_api_key"
_MAX_API_KEY_LENGTH = 512


@dataclass(frozen=True, slots=True)
class AiAssistantBinding:
    settings: AiAssistantSettings | None
    api_key: str | None

    def __post_init__(self) -> None:
        if self.settings is None and self.api_key is not None:
            raise AiAssistantViolation("invalid_ai_assistant_binding")
        if self.api_key is not None and (
            type(self.api_key) is not str or not 0 < len(self.api_key) <= _MAX_API_KEY_LENGTH
        ):
            raise AiAssistantViolation("invalid_ai_assistant_binding")


def ai_assistant_binding_from_entry_data(
    entry_data: Mapping[str, AiJsonValue],
) -> AiAssistantBinding:
    raw_settings = entry_data.get(AI_ASSISTANT_SETTINGS_FIELD)
    raw_key = entry_data.get(AI_ASSISTANT_API_KEY_FIELD)
    if raw_settings is None and raw_key is None:
        return AiAssistantBinding(None, None)
    if raw_settings is None or type(raw_settings) is not dict:
        raise AiAssistantViolation("invalid_ai_assistant_binding")
    if raw_key is not None and type(raw_key) is not str:
        raise AiAssistantViolation("invalid_ai_assistant_binding")
    if set(raw_settings) != {"enabled", "preset", "base_url", "model"}:
        raise AiAssistantViolation("invalid_ai_assistant_binding")
    preset = raw_settings["preset"]
    if type(preset) is not str:
        raise AiAssistantViolation("invalid_ai_assistant_binding")
    try:
        provider = AiProviderPreset(preset)
    except ValueError as error:
        raise AiAssistantViolation("invalid_ai_assistant_binding") from error
    return AiAssistantBinding(
        AiAssistantSettings(
            enabled=raw_settings["enabled"],
            preset=provider,
            base_url=raw_settings["base_url"],
            model=raw_settings["model"],
        ),
        raw_key,
    )


def ai_assistant_binding_update(
    payload: AiJsonValue,
    current: AiAssistantBinding,
) -> AiAssistantBinding:
    if type(payload) is not dict:
        raise AiAssistantViolation("invalid_ai_assistant_binding")
    allowed = {"enabled", "preset", "base_url", "model", "api_key", "clear_key"}
    required = {"enabled", "preset", "base_url", "model"}
    if not required.issubset(payload) or not set(payload).issubset(allowed):
        raise AiAssistantViolation("invalid_ai_assistant_binding")
    preset = payload["preset"]
    if type(preset) is not str:
        raise AiAssistantViolation("invalid_ai_assistant_binding")
    try:
        provider = AiProviderPreset(preset)
    except ValueError as error:
        raise AiAssistantViolation("invalid_ai_assistant_binding") from error
    clear_key = payload.get("clear_key", False)
    api_key = payload.get("api_key")
    if type(clear_key) is not bool or (clear_key and api_key is not None):
        raise AiAssistantViolation("invalid_ai_assistant_binding")
    if api_key is not None and (
        type(api_key) is not str or not 0 < len(api_key) <= _MAX_API_KEY_LENGTH
    ):
        raise AiAssistantViolation("invalid_ai_assistant_binding")
    key = None if clear_key else (api_key if api_key is not None else current.api_key)
    return AiAssistantBinding(
        AiAssistantSettings(
            enabled=payload["enabled"],
            preset=provider,
            base_url=payload["base_url"],
            model=payload["model"],
        ),
        key,
    )


def ai_assistant_entry_data(
    entry_data: Mapping[str, AiJsonValue],
    binding: AiAssistantBinding,
) -> AiJsonObject:
    updated = dict(entry_data)
    if binding.settings is None:
        updated.pop(AI_ASSISTANT_SETTINGS_FIELD, None)
        updated.pop(AI_ASSISTANT_API_KEY_FIELD, None)
        return updated
    updated[AI_ASSISTANT_SETTINGS_FIELD] = {
        "enabled": binding.settings.enabled,
        "preset": binding.settings.preset.value,
        "base_url": binding.settings.base_url,
        "model": binding.settings.model,
    }
    if binding.api_key is None:
        updated.pop(AI_ASSISTANT_API_KEY_FIELD, None)
    else:
        updated[AI_ASSISTANT_API_KEY_FIELD] = binding.api_key
    return updated


def ai_assistant_public_settings(binding: AiAssistantBinding) -> AiJsonObject:
    if binding.settings is None:
        return {
            "enabled": False,
            "preset": AiProviderPreset.CUSTOM.value,
            "base_url": "",
            "model": "",
            "key_set": False,
        }
    return {
        "enabled": binding.settings.enabled,
        "preset": binding.settings.preset.value,
        "base_url": binding.settings.base_url,
        "model": binding.settings.model,
        "key_set": binding.api_key is not None,
    }
