from __future__ import annotations

import asyncio
import json
import unittest

from custom_components.hausman_hub.application.ai_assistant_client import (
    OpenAiCompatibleTransport,
)
from custom_components.hausman_hub.domain.ai_assistant import (
    AiAssistantSettings,
    AiProviderPreset,
)


class Response:
    def __init__(self, payload: dict[str, object], status: int = 200) -> None:
        self._payload = payload
        self.status = status

    async def __aenter__(self) -> Response:
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        return None

    async def json(self) -> dict[str, object]:
        return self._payload


class Session:
    def __init__(self, response: Response) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, object]]] = []

    def post(self, url: str, **kwargs: object) -> Response:
        self.calls.append((url, dict(kwargs)))
        return self.response


def settings() -> AiAssistantSettings:
    return AiAssistantSettings(
        enabled=True,
        preset=AiProviderPreset.OPENAI,
        base_url="https://api.openai.example/v1",
        model="gpt-test",
    )


class OpenAiCompatibleTransportTest(unittest.TestCase):
    def test_transport_posts_bounded_evidence_to_chat_completions(self) -> None:
        session = Session(
            Response(
                {
                    "choices": [
                        {
                            "message": {
                                "content": '{"version":1,"source":"provider","generatedAt":1800000000000,"summary":{"code":"advisory_available"},"recommendations":[],"riskFlags":[]}'
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 8, "completion_tokens": 3},
                }
            )
        )
        transport = OpenAiCompatibleTransport(lambda: session)

        completion = asyncio.run(
            transport.async_complete(
                settings(),
                "test-key-123",
                {
                    "version": 1,
                    "rooms": [],
                    "mismatch_room_ids": [],
                    "outdoor_temperature": None,
                },
            )
        )

        self.assertEqual(8, completion.prompt_tokens)
        self.assertEqual(3, completion.completion_tokens)
        self.assertEqual("https://api.openai.example/v1/chat/completions", session.calls[0][0])
        request = session.calls[0][1]
        self.assertFalse(request["allow_redirects"])
        self.assertEqual("Bearer test-key-123", request["headers"]["Authorization"])
        self.assertEqual("gpt-test", request["json"]["model"])

    def test_task_transport_uses_server_prompt_and_exact_json_payload(self) -> None:
        session = Session(
            Response(
                {
                    "choices": [{"message": {"content": '{"status":"ready"}'}}],
                    "usage": {"prompt_tokens": 4, "completion_tokens": 2},
                }
            )
        )
        transport = OpenAiCompatibleTransport(lambda: session)

        completion = asyncio.run(
            transport.async_complete_task(
                settings(),
                "test-key-123",
                "Верни JSON сценария",
                {"request": "Включи свет", "catalog": {"devices": []}},
            )
        )

        messages = session.calls[0][1]["json"]["messages"]
        self.assertEqual("Верни JSON сценария", messages[0]["content"])
        self.assertEqual(
            {"request": "Включи свет", "catalog": {"devices": []}},
            json.loads(messages[1]["content"]),
        )
        self.assertEqual(2, completion.completion_tokens)
