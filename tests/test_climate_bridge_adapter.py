"""Isolated tests for the fixed-path bounded Climate API HTTP adapter."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys
import time
from types import ModuleType, SimpleNamespace
import unittest

from custom_components.hausman_hub.application.climate_commands import (
    ClimateCommandPlan,
)
from custom_components.hausman_hub.domain.climate_bridge import (
    ClimateBridgeTarget,
    climate_bridge_target,
)


ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "custom_components.hausman_hub.climate_bridge"
FAKE_NAMES = (
    "aiohttp",
    "homeassistant.helpers.aiohttp_client",
    MODULE_NAME,
)


class FakeClientError(Exception):
    pass


class FakeClientTimeout:
    def __init__(self, **values: object) -> None:
        self.values = values


class FakeContent:
    def __init__(self, body: bytes, *, chunk_size: int = 65536) -> None:
        self.body = body
        self.chunk_size = chunk_size

    async def iter_chunked(self, _: int):
        for offset in range(0, len(self.body), self.chunk_size):
            yield self.body[offset : offset + self.chunk_size]


class FakeResponse:
    def __init__(
        self,
        payload: object = None,
        *,
        status: int = 200,
        raw: bytes | None = None,
        content_length: int | None = None,
    ) -> None:
        body = raw if raw is not None else json.dumps(payload).encode("utf-8")
        self.status = status
        self.content_length = len(body) if content_length is None else content_length
        self.content = FakeContent(body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    def request(self, method: str, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


def fake_modules() -> dict[str, ModuleType]:
    aiohttp = ModuleType("aiohttp")
    aiohttp.ClientError = FakeClientError  # type: ignore[attr-defined]
    aiohttp.ClientTimeout = FakeClientTimeout  # type: ignore[attr-defined]
    client = ModuleType("homeassistant.helpers.aiohttp_client")
    client.async_get_clientsession = lambda hass: hass.session  # type: ignore[attr-defined]
    return {
        "aiohttp": aiohttp,
        "homeassistant.helpers.aiohttp_client": client,
    }


def state_payload() -> dict[str, object]:
    payload = json.loads(
        (ROOT / "fixtures" / "climate_bridge" / "valid_state.json").read_text(
            encoding="utf-8"
        )
    )
    payload["generatedAt"] = int(time.time() * 1000)
    return payload


class ClimateBridgeAdapterTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.previous = {name: sys.modules.get(name) for name in FAKE_NAMES}
        for name in FAKE_NAMES:
            sys.modules.pop(name, None)
        sys.modules.update(fake_modules())
        cls.adapter = importlib.import_module(MODULE_NAME)

    @classmethod
    def tearDownClass(cls) -> None:
        for name in FAKE_NAMES:
            sys.modules.pop(name, None)
        sys.modules.update(
            {name: module for name, module in cls.previous.items() if module is not None}
        )

    async def test_get_and_post_use_only_fixed_paths_without_redirects(self) -> None:
        session = FakeSession(
            [FakeResponse(state_payload()), FakeResponse({"accepted": True})]
        )
        client = self.adapter.ClimateApiClient(
            SimpleNamespace(session=session),
            climate_bridge_target("http://127.0.0.1:1880"),
        )

        snapshot = await client.async_fetch_state()
        plan = ClimateCommandPlan(
            action="set_room_target",
            room_id="living",
            device_id=None,
            backend_command_type="climate.set_temperature",
            backend_payload={
                "command": "set_room_target",
                "roomId": "living",
                "targetTemperature": 24.5,
            },
            execute=True,
        )
        await client.async_execute(plan)

        self.assertTrue(snapshot.runtime_fresh)
        self.assertEqual(
            [
                "http://127.0.0.1:1880/endpoint/climate/api/v1/state",
                "http://127.0.0.1:1880/endpoint/climate/api/v1/command",
            ],
            [call["url"] for call in session.calls],
        )
        self.assertTrue(all(call["allow_redirects"] is False for call in session.calls))
        self.assertEqual("POST", session.calls[1]["method"])
        self.assertEqual(plan.backend_payload, session.calls[1]["json"])

    async def test_non_executable_plan_and_unaccepted_response_fail_closed(self) -> None:
        session = FakeSession([FakeResponse({"accepted": False})])
        client = self.adapter.ClimateApiClient(
            SimpleNamespace(session=session),
            climate_bridge_target("http://127.0.0.1:1880"),
        )
        shadow = ClimateCommandPlan(
            action="turn_room_off",
            room_id="living",
            device_id=None,
            backend_command_type="climate.turn_off",
            backend_payload={"command": "turn_room_off", "roomId": "living"},
            execute=False,
        )
        with self.assertRaisesRegex(self.adapter.ClimateBridgeError, "not executable"):
            await client.async_execute(shadow)
        self.assertEqual([], session.calls)

        executable = ClimateCommandPlan(
            action=shadow.action,
            room_id=shadow.room_id,
            device_id=None,
            backend_command_type=shadow.backend_command_type,
            backend_payload=shadow.backend_payload,
            execute=True,
        )
        with self.assertRaisesRegex(
            self.adapter.ClimateCommandRejected, "rejected"
        ):
            await client.async_execute(executable)

    async def test_redirect_large_invalid_and_public_targets_fail_closed(self) -> None:
        for response in (
            FakeResponse({}, status=302),
            FakeResponse({}, content_length=1024 * 1024 + 1),
            FakeResponse(raw=b"not-json"),
        ):
            with self.subTest(status=response.status, length=response.content_length):
                client = self.adapter.ClimateApiClient(
                    SimpleNamespace(session=FakeSession([response])),
                    climate_bridge_target("http://127.0.0.1:1880"),
                )
                with self.assertRaises(self.adapter.ClimateBridgeError):
                    await client.async_fetch_state()

        with self.assertRaisesRegex(self.adapter.ClimateBridgeError, "unsafe"):
            self.adapter.ClimateApiClient(
                SimpleNamespace(session=FakeSession([])),
                ClimateBridgeTarget("http://8.8.8.8:1880"),
            )


if __name__ == "__main__":
    unittest.main()
