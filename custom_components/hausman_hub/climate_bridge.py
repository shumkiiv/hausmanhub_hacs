"""Fixed-path, bounded Home Assistant HTTP adapter for Climate API v1."""

from __future__ import annotations

from collections.abc import Mapping
import json
import time
from typing import TYPE_CHECKING, Any, Final

from aiohttp import ClientError, ClientTimeout
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .application.climate_commands import ClimateCommandPlan, ClimateCommandRejected
from .application.climate_import import (
    ClimateImportSnapshot,
    ClimateImportViolation,
    import_climate_state,
)
from .domain.climate_bridge import (
    ClimateBridgeTarget,
    UnsafeClimateBridgeTarget,
    climate_bridge_target,
)

if TYPE_CHECKING:
    from aiohttp import ClientResponse
    from homeassistant.core import HomeAssistant


CLIMATE_STATE_PATH: Final = "/endpoint/climate/api/v1/state"
CLIMATE_COMMAND_PATH: Final = "/endpoint/climate/api/v1/command"
MAX_CLIMATE_RESPONSE_BYTES: Final = 1024 * 1024
CLIMATE_HTTP_TIMEOUT: Final = ClientTimeout(total=5, connect=2)


class ClimateBridgeError(RuntimeError):
    """The fixed Climate API boundary failed or returned unsafe data."""


class ClimateApiClient:
    """Use only the two fixed Climate API v1 routes on one private origin."""

    def __init__(self, hass: HomeAssistant, target: ClimateBridgeTarget) -> None:
        try:
            verified = climate_bridge_target(target.origin)
        except UnsafeClimateBridgeTarget as error:
            raise ClimateBridgeError("climate bridge target is unsafe") from error
        if verified != target:
            raise ClimateBridgeError("climate bridge target is not normalized")
        self._session = async_get_clientsession(hass)
        self._origin = verified.origin

    async def async_fetch_state(self) -> ClimateImportSnapshot:
        """GET, bound, decode, and validate one Climate API state."""

        payload = await self._async_json_request("GET", CLIMATE_STATE_PATH)
        try:
            return import_climate_state(payload, now_ms=int(time.time() * 1000))
        except ClimateImportViolation as error:
            raise ClimateBridgeError("climate state contract is invalid") from error

    async def async_execute(self, plan: ClimateCommandPlan) -> object:
        """POST only an executable typed plan produced by the application layer."""

        if not isinstance(plan, ClimateCommandPlan) or not plan.execute:
            raise ClimateBridgeError("climate command plan is not executable")
        result = await self._async_json_request(
            "POST",
            CLIMATE_COMMAND_PATH,
            json_payload=plan.backend_payload,
        )
        if isinstance(result, Mapping) and (
            result.get("accepted") is True or result.get("ok") is True
        ):
            return result
        if isinstance(result, Mapping) and (
            result.get("accepted") is False or result.get("ok") is False
        ):
            raise ClimateCommandRejected("climate API rejected the command")
        raise ClimateBridgeError("climate API command response is invalid")

    async def _async_json_request(
        self,
        method: str,
        path: str,
        *,
        json_payload: Mapping[str, Any] | None = None,
    ) -> object:
        try:
            async with self._session.request(
                method,
                self._origin + path,
                json=json_payload,
                allow_redirects=False,
                timeout=CLIMATE_HTTP_TIMEOUT,
                headers={"Accept": "application/json"},
            ) as response:
                if response.status != 200:
                    raise ClimateBridgeError("climate API returned a non-success status")
                return await _bounded_json(response)
        except ClimateBridgeError:
            raise
        except (ClientError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ClimateBridgeError("climate API request failed") from error


async def _bounded_json(response: ClientResponse) -> object:
    length = response.content_length
    if length is not None and length > MAX_CLIMATE_RESPONSE_BYTES:
        raise ClimateBridgeError("climate API response is too large")
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.content.iter_chunked(64 * 1024):
        size += len(chunk)
        if size > MAX_CLIMATE_RESPONSE_BYTES:
            raise ClimateBridgeError("climate API response is too large")
        chunks.append(chunk)
    return json.loads(b"".join(chunks).decode("utf-8"))
