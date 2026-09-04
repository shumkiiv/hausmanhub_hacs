"""Release-owned device-automation adapters for smart switch actions.

Only Home Assistant's public device-automation trigger API is used here.  MQTT
topics and native automations are deliberately outside the integration.
"""

from __future__ import annotations

import inspect
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

SHOWER_DEVICE_ID = "2685c1523cb5151baeaf65aebe830c53"
PASSTHROUGH_DEVICE_ID = "609ee914f1d93194cd157612d7d086e9"
_BASE = {"platform": "device", "domain": "mqtt", "type": "action"}
SHOWER_TRIGGER_CONFIGS = tuple(
    {**_BASE, "device_id": SHOWER_DEVICE_ID, "subtype": subtype}
    for subtype in ("toggle_b2_down", "on_b2_down")
)
PASS_THROUGH_TRIGGER_CONFIGS = tuple(
    {**_BASE, "device_id": PASSTHROUGH_DEVICE_ID, "subtype": subtype}
    for subtype in ("on_down", "toggle_down", "off_up")
)
_ALL_CONFIGS = SHOWER_TRIGGER_CONFIGS + PASS_THROUGH_TRIGGER_CONFIGS
_DEDUP_SECONDS = 0.6


def validate_exact_device_trigger(
    actual: Mapping[str, object], expected: Mapping[str, object]
) -> bool:
    """Require the complete public device-trigger identity, fail closed."""

    return dict(actual) == dict(expected)


class SmartSwitchTriggerAdapter:
    def __init__(
        self,
        hass: object,
        service: object,
        *,
        trigger_api: object | None = None,
        state_store: object | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._hass = hass
        self._service = service
        self._api = trigger_api
        self._store = state_store
        self._clock = clock
        self._last_shower_action = 0.0
        self._unloads: list[Callable[[], None]] = []
        self._healthy = True

    async def async_start(self) -> None:
        if self._api is None:
            from homeassistant.components.device_automation import trigger as api  # type: ignore[import-not-found]
            self._api = api
        if self._store is not None:
            try:
                loaded = await self._store.async_load()
                self._last_shower_action = float(loaded.get("lastShowerAction", 0.0)) if isinstance(loaded, Mapping) else 0.0
            except Exception as err:  # noqa: BLE001
                self._healthy = False
                raise RuntimeError("smart switch dedup storage unavailable") from err
        get_triggers = getattr(self._api, "async_get_triggers", None)
        attach = getattr(self._api, "async_attach_trigger", None)
        if not callable(get_triggers) or not callable(attach):
            raise RuntimeError("Home Assistant device automation trigger API unavailable")
        pending: list[Callable[[], None]] = []
        try:
            for device_id in (SHOWER_DEVICE_ID, PASSTHROUGH_DEVICE_ID):
                discovered = await get_triggers(self._hass, device_id)
                for expected in (SHOWER_TRIGGER_CONFIGS if device_id == SHOWER_DEVICE_ID else PASS_THROUGH_TRIGGER_CONFIGS):
                    if not any(validate_exact_device_trigger(item, expected) for item in discovered if isinstance(item, Mapping)):
                        raise RuntimeError(f"required MQTT device trigger missing: {expected['subtype']}")
                    cleanup = await attach(self._hass, dict(expected), self._make_action(expected), {"source": "hausman_hub"})
                    if not callable(cleanup):
                        raise RuntimeError("device trigger attach did not return cleanup callback")
                    pending.append(cleanup)
        except Exception:
            for cleanup in pending:
                cleanup()
            raise
        self._unloads.extend(pending)

    def _make_action(self, config: Mapping[str, object]) -> Callable[[Mapping[str, object]], Awaitable[None]]:
        async def action(trigger_data: Mapping[str, object] | None = None) -> None:
            await self.async_handle_trigger(config, trigger_data or {})
        return action

    async def async_handle_trigger(self, config: Mapping[str, object], trigger_data: Mapping[str, object]) -> bool:
        if not self._healthy or not any(validate_exact_device_trigger(config, item) for item in _ALL_CONFIGS):
            return False
        subtype = str(config["subtype"])
        if subtype == "toggle_b2_up":
            return False
        if config["device_id"] == SHOWER_DEVICE_ID:
            now = self._clock()
            if now - self._last_shower_action < _DEDUP_SECONDS:
                return False
            self._last_shower_action = now
            if self._store is not None:
                try:
                    await self._store.async_save({"lastShowerAction": now})
                except Exception:
                    self._healthy = False
                    return False
            intent = {"binding": "shower-cabinet", "action": "toggle", "correlation_id": self._correlation(trigger_data), "source": "manual", "trigger_id": subtype}
        else:
            intent = {"binding": "tambur-light-group", "action": {"on_down": "on", "off_up": "off", "toggle_down": "toggle"}[subtype], "correlation_id": self._correlation(trigger_data), "source": "manual", "trigger_id": subtype}
        result = self._service.async_run_typed_intent(**intent)
        if inspect.isawaitable(result):
            await result
        return True

    @staticmethod
    def _correlation(data: Mapping[str, object]) -> str:
        value = data.get("correlation_id") or data.get("correlationId")
        return str(value) if isinstance(value, str) and value else f"smart-switch-{time.time_ns()}"

    def async_unload(self) -> None:
        for cleanup in self._unloads:
            cleanup()
        self._unloads.clear()
