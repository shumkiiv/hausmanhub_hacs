"""One bounded Home Assistant transport for the optional lighting branch.

The core calls this adapter from its worker thread.  The adapter owns only
the Home Assistant representation of an already permitted ``PreparedCall``;
it cannot add a target, retry a command, or confirm a device state.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Mapping

from ...application.core.command_service import (
    PreparedCall,
    TransportOutcome,
    TransportPort,
    TransportStatus,
)


class HomeAssistantTransport(TransportPort):
    """Dispatch an allowlisted call on the HA loop exactly once.

    This class is deliberately injected only when a separately authorised
    branch is opened.  ``RoomLightingCoreBranch`` keeps ``ClosedTransport``
    as its default, so constructing this adapter cannot activate sending.
    """

    def __init__(
        self,
        hass: object,
        *,
        loop: asyncio.AbstractEventLoop,
        allowed_entities: Mapping[str, str],
        timeout_seconds: float = 5.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._hass = hass
        self._loop = loop
        self._allowed_entities = dict(allowed_entities)
        self._timeout_seconds = timeout_seconds

    def call(self, prepared: PreparedCall) -> TransportOutcome:
        """Schedule one HA service call and wait only from a worker thread."""

        try:
            payload = self._payload(prepared)
            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                running_loop = None
            if running_loop is self._loop:
                raise RuntimeError("HA transport cannot block the HA event loop")
        except Exception as error:
            # Payload and event-loop checks run before ``async_call``.  Their
            # refusal is therefore proof that no HA call was attempted.
            return TransportOutcome(
                status=TransportStatus.ERROR,
                error_detail=f"ha_transport_preflight_error:{type(error).__name__}",
                proven_not_sent=True,
            )
        try:
            result = self._hass.services.async_call(  # type: ignore[attr-defined]
                prepared.domain,
                prepared.service,
                payload,
                blocking=True,
            )
            if not inspect.isawaitable(result):
                raise RuntimeError("HA async_call did not return an awaitable")
            future = asyncio.run_coroutine_threadsafe(result, self._loop)
            future.result(timeout=self._timeout_seconds)
        except (TimeoutError, asyncio.TimeoutError):
            # A timed-out call may have reached HA.  The core records the
            # outcome as uncertain and never retries it automatically.
            return TransportOutcome(
                status=TransportStatus.ERROR,
                error_detail="ha_transport_timeout",
                proven_not_sent=False,
            )
        except Exception as error:  # a call may already have reached HA
            return TransportOutcome(
                status=TransportStatus.ERROR,
                error_detail=f"ha_transport_error:{type(error).__name__}",
                proven_not_sent=False,
            )
        return TransportOutcome(status=TransportStatus.ACCEPTED)

    def _payload(self, prepared: PreparedCall) -> dict[str, object]:
        """Validate the entire HA call before it can reach ``async_call``."""

        if prepared.domain not in {"light", "switch"}:
            raise ValueError("unsupported HA domain")
        if prepared.service not in {"turn_on", "turn_off"}:
            raise ValueError("unsupported HA service")
        if self._allowed_entities.get(prepared.entity_id) != prepared.domain:
            raise ValueError("entity is not an allowed HA binding")
        if prepared.domain == "switch" and set(prepared.data):
            raise ValueError("switch calls cannot carry light fields")
        if prepared.service == "turn_off" and prepared.data:
            raise ValueError("turn_off cannot carry turn_on fields")
        allowed_data = {"brightness_pct", "color_temperature_k"}
        if set(prepared.data) - allowed_data:
            raise ValueError("unsupported HA call field")
        if prepared.domain != "light" and prepared.data:
            raise ValueError("only lights can carry light fields")

        payload: dict[str, object] = {"entity_id": prepared.entity_id}
        for name, value in prepared.data.items():
            if name == "brightness_pct":
                if type(value) is not int or not 0 <= value <= 100:
                    raise ValueError("brightness_pct is invalid")
                payload[name] = value
            elif name == "color_temperature_k":
                if type(value) is not int or value <= 0:
                    raise ValueError("color_temperature_k is invalid")
                payload["color_temp_kelvin"] = value
        return payload
