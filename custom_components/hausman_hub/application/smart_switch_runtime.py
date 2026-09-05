"""Release-owned device-automation adapters for smart switch actions.

Only Home Assistant's public device-automation trigger API is used here.  MQTT
topics and native automations are deliberately outside the integration.
"""

from __future__ import annotations

import asyncio
import inspect
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

SHOWER_DEVICE_ID = "2685c1523cb5151baeaf65aebe830c53"
PASSTHROUGH_DEVICE_ID = "609ee914f1d93194cd157612d7d086e9"
_BASE = {"platform": "device", "domain": "mqtt", "type": "action"}
SHOWER_TRIGGER_CONFIGS = tuple(
    {**_BASE, "device_id": SHOWER_DEVICE_ID, "subtype": subtype}
    for subtype in ("toggle_b2_down", "on_b2_down", "toggle_b2_up")
)
PASS_THROUGH_TRIGGER_CONFIGS = tuple(
    {**_BASE, "device_id": PASSTHROUGH_DEVICE_ID, "subtype": subtype}
    for subtype in ("on_down", "toggle_down", "off_up")
)
_ALL_CONFIGS = SHOWER_TRIGGER_CONFIGS + PASS_THROUGH_TRIGGER_CONFIGS
_DEDUP_SECONDS = 0.6
_MAX_RECEIPTS = 32
_RECEIPT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def valid_smart_switch_dedup_payload(value: object) -> bool:
    """Validate bounded persisted adapter receipts."""

    if not isinstance(value, Mapping) or set(value) != {"version", "receipts"}:
        return False
    receipts = value.get("receipts")
    if value.get("version") != 1 or not isinstance(receipts, list) or len(receipts) > _MAX_RECEIPTS:
        return False
    seen: set[str] = set()
    for item in receipts:
        if not isinstance(item, Mapping) or set(item) != {
            "receiptId", "binding", "rawSubtype", "observedAtMs", "dedupDisposition"
        }:
            return False
        receipt_id = item.get("receiptId")
        binding = item.get("binding")
        subtype = item.get("rawSubtype")
        disposition = item.get("dedupDisposition")
        if (
            not isinstance(receipt_id, str)
            or _RECEIPT_ID.fullmatch(receipt_id) is None
            or receipt_id in seen
            or binding not in {"shower-cabinet", "tambur-light-group"}
            or not isinstance(subtype, str)
            or disposition not in {"accepted", "deduplicated", "ignored"}
            or type(item.get("observedAtMs")) is not int
            or int(item["observedAtMs"]) < 0
            or (
                binding == "shower-cabinet"
                and subtype not in {"toggle_b2_down", "on_b2_down", "toggle_b2_up"}
            )
            or (
                binding == "tambur-light-group"
                and subtype not in {"on_down", "toggle_down", "off_up"}
            )
            or subtype == "toggle_b2_up" and disposition != "ignored"
            or disposition == "ignored" and subtype != "toggle_b2_up"
            or disposition == "deduplicated"
            and not (
                binding == "shower-cabinet"
                and subtype in {"toggle_b2_down", "on_b2_down"}
            )
            or binding == "tambur-light-group" and disposition != "accepted"
        ):
            return False
        seen.add(receipt_id)
    return True


def validate_exact_device_trigger(
    actual: Mapping[str, object], expected: Mapping[str, object]
) -> bool:
    """Require the complete public device-trigger identity, fail closed."""

    return dict(actual) == dict(expected)


class HomeAssistantSmartSwitchDedupStore:
    """Small HA Store-backed state holder used by the runtime adapter."""

    def __init__(self, hass: object, entry_id: str) -> None:
        from homeassistant.helpers.storage import Store  # type: ignore[import-not-found]
        from ..verified_safety_storage import VerifiedSafetyStore

        backend = Store(
            hass,
            1,
            f"hausman_hub.smart_switch_dedup.{entry_id}",
            atomic_writes=True,
        )
        self._store = VerifiedSafetyStore(
            backend,
            hass.async_add_executor_job,
            payload_validator=valid_smart_switch_dedup_payload,
        )

    async def async_load(self) -> object:
        return await self._store.async_load()

    async def async_save(self, value: dict[str, object]) -> None:
        await self._store.async_save(value)


class SmartSwitchTriggerAdapter:

    def __init__(
        self,
        hass: object,
        service: object,
        *,
        trigger_api: object | None = None,
        state_store: object | None = None,
        wall_clock: Callable[[], float] = time.time,
        receipt_factory: Callable[[], str] | None = None,
    ) -> None:
        self._hass = hass
        self._service = service
        self._api = trigger_api
        self._store = state_store
        self._wall_clock = wall_clock
        self._receipt_factory = receipt_factory or (
            lambda: f"smart-switch.{uuid.uuid4().hex}"
        )
        self._receipts: list[dict[str, object]] = []
        self._unloads: list[Callable[[], None]] = []
        self._healthy = True
        self._state_loaded = False
        self._receipt_lock = asyncio.Lock()

    async def async_load_state(self) -> None:
        if self._state_loaded:
            return
        if self._store is None:
            self._state_loaded = True
            return
        try:
            loaded = await self._store.async_load()
            if loaded is None:
                self._receipts = []
            elif valid_smart_switch_dedup_payload(loaded):
                self._receipts = [dict(item) for item in loaded["receipts"]]
            else:
                raise RuntimeError("smart switch dedup store is corrupt")
        except Exception as err:  # noqa: BLE001
            self._healthy = False
            raise RuntimeError("smart switch dedup storage unavailable") from err
        self._state_loaded = True

    async def async_start(self) -> None:
        if self._api is None:
            from homeassistant.components.device_automation import (  # type: ignore[import-not-found]
                DeviceAutomationType,
                async_get_device_automation_platform,
            )
            self._api = await async_get_device_automation_platform(
                self._hass, "mqtt", DeviceAutomationType.TRIGGER
            )
        await self.async_load_state()
        get_triggers = getattr(self._api, "async_get_triggers", None)
        attach = getattr(self._api, "async_attach_trigger", None)
        if not callable(get_triggers) or not callable(attach):
            raise RuntimeError("Home Assistant device automation trigger API unavailable")
        discovered_by_device: dict[str, tuple[Mapping[str, object], ...]] = {}
        for device_id in (SHOWER_DEVICE_ID, PASSTHROUGH_DEVICE_ID):
            discovered = await get_triggers(self._hass, device_id)
            if not isinstance(discovered, list):
                raise RuntimeError("Home Assistant device trigger discovery is invalid")
            discovered_by_device[device_id] = tuple(
                item for item in discovered if isinstance(item, Mapping)
            )
        for expected in _ALL_CONFIGS:
            discovered = discovered_by_device[str(expected["device_id"])]
            if not any(validate_exact_device_trigger(item, expected) for item in discovered):
                raise RuntimeError(
                    f"required MQTT device trigger missing: {expected['subtype']}"
                )

        pending: list[Callable[[], None]] = []
        try:
            for expected in _ALL_CONFIGS:
                cleanup = await attach(
                    self._hass,
                    dict(expected),
                    self._make_action(expected),
                    {"source": "hausman_hub"},
                )
                if not callable(cleanup):
                    raise RuntimeError("device trigger attach did not return cleanup callback")
                pending.append(cleanup)
        except Exception:
            self._cleanup_callbacks(pending)
            raise
        self._unloads.extend(pending)

    def _make_action(self, config: Mapping[str, object]) -> Callable[[Mapping[str, object]], Awaitable[None]]:
        async def action(trigger_data: Mapping[str, object] | None = None) -> None:
            await self.async_handle_trigger(config, trigger_data or {})
        return action

    async def async_handle_trigger(self, config: Mapping[str, object], trigger_data: Mapping[str, object]) -> bool:
        del trigger_data
        if not any(validate_exact_device_trigger(config, item) for item in _ALL_CONFIGS):
            return False
        async with self._receipt_lock:
            if not self._healthy or self._store is None:
                return False
            if not self._state_loaded:
                try:
                    await self.async_load_state()
                except RuntimeError:
                    return False
            subtype = str(config["subtype"])
            binding = (
                "shower-cabinet"
                if config["device_id"] == SHOWER_DEVICE_ID
                else "tambur-light-group"
            )
            now_ms = max(0, int(self._wall_clock() * 1000))
            active_receipts = [
                item
                for item in self._receipts
                if now_ms < int(item["observedAtMs"]) + int(_DEDUP_SECONDS * 1000)
            ]
            disposition = "accepted"
            if subtype == "toggle_b2_up":
                disposition = "ignored"
            elif binding == "shower-cabinet" and any(
                item.get("binding") == binding
                and item.get("dedupDisposition") == "accepted"
                for item in active_receipts
            ):
                disposition = "deduplicated"
            receipt_id = self._receipt_factory()
            if not isinstance(receipt_id, str) or _RECEIPT_ID.fullmatch(receipt_id) is None:
                self._healthy = False
                return False
            receipt = {
                "receiptId": receipt_id,
                "binding": binding,
                "rawSubtype": subtype,
                "observedAtMs": now_ms,
                "dedupDisposition": disposition,
            }
            proposed = [*active_receipts, receipt][-_MAX_RECEIPTS:]
            try:
                if self._store is not None:
                    await self._store.async_save({"version": 1, "receipts": proposed})
            except Exception:  # noqa: BLE001
                self._healthy = False
                return False
            self._receipts = proposed
        if disposition != "accepted":
            record = getattr(self._service, "async_record_typed_intent_disposition", None)
            if callable(record):
                result = record(
                    binding=binding,
                    correlation_id=receipt_id,
                    source="manual",
                    trigger_id=subtype,
                    intent_receipt_id=receipt_id,
                    raw_subtype=subtype,
                    dedup_disposition=disposition,
                )
                if inspect.isawaitable(result):
                    await result
            return False
        action = (
            "toggle"
            if binding == "shower-cabinet"
            else {"on_down": "on", "off_up": "off", "toggle_down": "toggle"}[subtype]
        )
        result = self._service.async_run_typed_intent(
            binding=binding,
            action=action,
            correlation_id=receipt_id,
            source="manual",
            trigger_id=subtype,
            intent_receipt_id=receipt_id,
            raw_subtype=subtype,
            dedup_disposition=disposition,
        )
        if inspect.isawaitable(result):
            await result
        return True

    def async_unload(self) -> None:
        callbacks = list(self._unloads)
        self._unloads.clear()
        self._cleanup_callbacks(callbacks)

    @staticmethod
    def _cleanup_callbacks(callbacks: list[Callable[[], None]]) -> None:
        for cleanup in callbacks:
            try:
                cleanup()
            except Exception:  # noqa: BLE001
                continue
