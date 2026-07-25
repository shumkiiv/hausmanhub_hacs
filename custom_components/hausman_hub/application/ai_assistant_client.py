from __future__ import annotations

import asyncio  # noqa: ANYIO_OK
from collections.abc import Callable
import json
from types import TracebackType
from typing import Protocol

from .ai_assistant import (
    AiProviderCompletion,
    AiProviderHttpError,
    AiProviderTimeout,
    AiProviderUnavailable,
)
from ..domain.ai_assistant import AiAssistantSettings, AiAssistantViolation
from ..domain.ai_assistant_json import AiJsonObject, AiJsonValue


_REQUEST_TIMEOUT_SECONDS = 10
_SYSTEM_PROMPT = (
    "Ты климатический помощник HausmanHub. Верни только JSON advisory без команд, "
    "с полями version, source, generatedAt, summary, recommendations и riskFlags."
)


class AiProviderResponse(Protocol):
    status: int

    async def json(self) -> AiJsonObject: ...


class AiProviderRequest(Protocol):
    async def __aenter__(self) -> AiProviderResponse: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


class AiProviderSession(Protocol):
    def post(self, url: str, **kwargs: AiJsonValue) -> AiProviderRequest: ...


class OpenAiCompatibleTransport:
    def __init__(
        self,
        session_factory: Callable[[], AiProviderSession],
        unavailable_error: type[Exception] = OSError,
    ) -> None:
        self._session_factory = session_factory
        self._unavailable_error = unavailable_error

    async def async_complete(
        self,
        settings: AiAssistantSettings,
        api_key: str,
        evidence: AiJsonObject,
    ) -> AiProviderCompletion:
        session = self._session_factory()
        try:
            async with session.post(
                f"{settings.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.model,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": json.dumps(
                                {"evidence": evidence},
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        },
                    ],
                },
                timeout=_REQUEST_TIMEOUT_SECONDS,
                allow_redirects=False,
            ) as response:
                if response.status >= 400:
                    raise AiProviderHttpError(response.status)
                payload = await response.json()
        except asyncio.TimeoutError as error:
            raise AiProviderTimeout() from error
        except self._unavailable_error as error:
            raise AiProviderUnavailable() from error
        return _completion_from_response(payload)


def _completion_from_response(payload: AiJsonValue) -> AiProviderCompletion:
    if type(payload) is not dict:
        raise AiAssistantViolation("invalid_provider_completion")
    choices = payload.get("choices")
    if type(choices) is not list or not choices or type(choices[0]) is not dict:
        raise AiAssistantViolation("invalid_provider_completion")
    message = choices[0].get("message")
    if type(message) is not dict or type(message.get("content")) is not str:
        raise AiAssistantViolation("invalid_provider_completion")
    usage = payload.get("usage", {})
    if type(usage) is not dict:
        raise AiAssistantViolation("invalid_provider_completion")
    return AiProviderCompletion(
        content=message["content"],
        prompt_tokens=_token_count(usage, "prompt_tokens"),
        completion_tokens=_token_count(usage, "completion_tokens"),
    )


def _token_count(usage: AiJsonObject, field: str) -> int:
    value = usage.get(field, 0)
    if type(value) is not int or value < 0:
        raise AiAssistantViolation("invalid_provider_completion")
    return value
