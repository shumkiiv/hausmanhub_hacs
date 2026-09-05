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
_RECEIPT_FIELDS = {
    "receiptId",
    "binding",
    "rawSubtype",
    "observedAtMs",
    "dedupDisposition",
}


def valid_smart_switch_dedup_payload(value: object) -> bool:
    """Validate bounded persisted adapter receipts."""

    if not isinstance(value, Mapping) or set(value) != {"version", "receipts"}:
        return False
    receipts = value.get("receipts")
    if value.get("version") != 1 or not isinstance(receipts, list) or len(receipts) > _MAX_RECEIPTS:
        return False
    seen: set[str] = set()
    for item in receipts:
        if not isinstance(item, Mapping):
            return False
        item_fields = set(item)
        if item_fields not in (
            _RECEIPT_FIELDS,
            _RECEIPT_FIELDS | {"lifecycle"},
        ):
            return False
        receipt_id = item.get("receiptId")
        binding = item.get("binding")
        subtype = item.get("rawSubtype")
        disposition = item.get("dedupDisposition")
        lifecycle = item.get("lifecycle", "consumed")
        if (
            not isinstance(receipt_id, str)
            or _RECEIPT_ID.fullmatch(receipt_id) is None
            or receipt_id in seen
            or binding not in {"shower-cabinet", "tambur-light-group"}
            or not isinstance(subtype, str)
            or disposition not in {"accepted", "deduplicated", "ignored"}
            or lifecycle not in {"accepted", "consumed"}
            or disposition != "accepted" and lifecycle != "consumed"
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
                or binding == "tambur-light-group"
                and subtype in {"on_down", "toggle_down", "off_up"}
            )
        ):
            return False
        seen.add(receipt_id)
    return True


def validate_exact_device_trigger(
    actual: Mapping[str, object], expected: Mapping[str, object]
) -> bool:
    """Require the complete public device-trigger identity, fail closed."""

    return dict(actual) == dict(expected)


def _semantic_intent(binding: str, subtype: str) -> str:
    if binding == "shower-cabinet":
        return "release" if subtype == "toggle_b2_up" else "toggle"
    return {"on_down": "on", "toggle_down": "toggle", "off_up": "off"}[subtype]


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

    @property
    def recovered_previous(self) -> bool:
        return self._store.recovered_previous


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
        readiness_check: Callable[[], bool] | None = None,
    ) -> None:
        self._hass = hass
        self._service = service
        self._api = trigger_api
        self._store = state_store
        self._wall_clock = wall_clock
        self._receipt_factory = receipt_factory or (
            lambda: f"smart-switch.{uuid.uuid4().hex}"
        )
        self._readiness_check = readiness_check or (lambda: True)
        self._receipts: list[dict[str, object]] = []
        # Only receipts accepted by this live adapter generation may authorize
        # execution. Persisted receipts remain useful for deduplication after a
        # restart, but can never be replayed as execution authority.
        self._pending_receipts: dict[str, dict[str, bool] | None] = {}
        self._unloads: list[Callable[[], None]] = []
        self._active_generation: dict[str, bool] | None = None
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
            if getattr(self._store, "recovered_previous", False):
                raise RuntimeError(
                    "smart switch dedup previous generation is ambiguous"
                )
            if loaded is None:
                self._receipts = []
            elif valid_smart_switch_dedup_payload(loaded):
                self._receipts = []
                for item in loaded["receipts"]:
                    normalized = dict(item)
                    normalized.setdefault("lifecycle", "consumed")
                    self._receipts.append(normalized)
            else:
                raise RuntimeError("smart switch dedup store is corrupt")
        except Exception as err:  # noqa: BLE001
            self._healthy = False
            message = (
                "smart switch dedup previous generation is ambiguous"
                if getattr(self._store, "recovered_previous", False)
                else "smart switch dedup storage unavailable"
            )
            raise RuntimeError(message) from err
        self._state_loaded = True

    async def async_start(self) -> None:
        await self.async_load_state()
        self._require_startup_ready()
        if self._api is None:
            from homeassistant.components.device_automation import (  # type: ignore[import-not-found]
                DeviceAutomationType,
                async_get_device_automation_platform,
            )
            self._api = await async_get_device_automation_platform(
                self._hass, "mqtt", DeviceAutomationType.TRIGGER
            )
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
        generation = {"active": False}
        try:
            for expected in _ALL_CONFIGS:
                cleanup = await attach(
                    self._hass,
                    dict(expected),
                    self._make_action(expected, generation),
                    {"source": "hausman_hub"},
                )
                if not callable(cleanup):
                    raise RuntimeError("device trigger attach did not return cleanup callback")
                pending.append(cleanup)
            self._require_startup_ready()
            generation["active"] = True
        except Exception as attach_error:
            generation["active"] = False
            try:
                self._cleanup_callbacks(pending)
            except RuntimeError as cleanup_error:
                raise cleanup_error from attach_error
            raise
        self._active_generation = generation
        self._unloads.extend(pending)

    def _require_startup_ready(self) -> None:
        try:
            ready = self._readiness_check()
        except Exception as err:  # noqa: BLE001
            raise RuntimeError("smart switch startup readiness check failed") from err
        if ready is not True:
            raise RuntimeError("smart switch startup readiness is unavailable")

    def _make_action(
        self,
        config: Mapping[str, object],
        generation: dict[str, bool],
    ) -> Callable[[Mapping[str, object]], Awaitable[None]]:
        async def action(trigger_data: Mapping[str, object] | None = None) -> None:
            if generation.get("active") is True:
                await self.async_handle_trigger(
                    config,
                    trigger_data or {},
                    _generation=generation,
                )
        return action

    async def async_handle_trigger(
        self,
        config: Mapping[str, object],
        trigger_data: Mapping[str, object],
        *,
        _generation: dict[str, bool] | None = None,
    ) -> bool:
        del trigger_data
        if _generation is not None and _generation.get("active") is not True:
            return False
        if not any(validate_exact_device_trigger(config, item) for item in _ALL_CONFIGS):
            return False
        async with self._receipt_lock:
            if _generation is not None and _generation.get("active") is not True:
                return False
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
            elif any(
                item.get("binding") == binding
                and item.get("dedupDisposition") == "accepted"
                and _semantic_intent(
                    str(item["binding"]), str(item["rawSubtype"])
                )
                == _semantic_intent(binding, subtype)
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
                "lifecycle": "accepted" if disposition == "accepted" else "consumed",
            }
            proposed = [*active_receipts, receipt][-_MAX_RECEIPTS:]
            try:
                if self._store is not None:
                    await self._store.async_save({"version": 1, "receipts": proposed})
            except Exception:  # noqa: BLE001
                self._healthy = False
                return False
            self._receipts = proposed
            retained_ids = {
                str(item["receiptId"])
                for item in proposed
                if item.get("lifecycle") == "accepted"
            }
            self._pending_receipts = {
                key: generation
                for key, generation in self._pending_receipts.items()
                if key in retained_ids
            }
            if disposition == "accepted":
                self._pending_receipts[receipt_id] = _generation
        if _generation is not None and _generation.get("active") is not True:
            return False
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

    async def async_consume_intent_receipt(
        self,
        *,
        binding: str,
        action: str,
        correlation_id: str,
        source: str,
        trigger_id: str,
        intent_receipt_id: str,
        raw_subtype: str,
        dedup_disposition: str,
    ) -> bool:
        """Atomically consume execution authority before scenario planning."""

        if (
            source != "manual"
            or dedup_disposition != "accepted"
            or correlation_id != intent_receipt_id
            or trigger_id != raw_subtype
            or not isinstance(intent_receipt_id, str)
            or _RECEIPT_ID.fullmatch(intent_receipt_id) is None
        ):
            return False
        try:
            expected_action = _semantic_intent(binding, raw_subtype)
        except (KeyError, TypeError):
            return False
        if action != expected_action:
            return False

        async with self._receipt_lock:
            if (
                not self._healthy
                or not self._state_loaded
                or self._store is None
                or intent_receipt_id not in self._pending_receipts
            ):
                return False
            generation = self._pending_receipts[intent_receipt_id]
            if generation is not None and generation.get("active") is not True:
                return False
            matches = [
                item
                for item in self._receipts
                if item.get("receiptId") == intent_receipt_id
            ]
            if len(matches) != 1:
                return False
            receipt = matches[0]
            if (
                receipt.get("binding") != binding
                or receipt.get("rawSubtype") != raw_subtype
                or receipt.get("dedupDisposition") != "accepted"
                or receipt.get("lifecycle") != "accepted"
            ):
                return False
            proposed = [
                {**item, "lifecycle": "consumed"}
                if item.get("receiptId") == intent_receipt_id
                else item
                for item in self._receipts
            ]
            try:
                await self._store.async_save({"version": 1, "receipts": proposed})
            except Exception:  # noqa: BLE001
                self._healthy = False
                return False
            self._receipts = proposed
            self._pending_receipts.pop(intent_receipt_id, None)
            if generation is not None and generation.get("active") is not True:
                return False
            return True

    def async_unload(self) -> None:
        if self._active_generation is not None:
            self._active_generation["active"] = False
            self._active_generation = None
        callbacks = list(self._unloads)
        self._unloads.clear()
        self._cleanup_callbacks(callbacks)

    @staticmethod
    def _cleanup_callbacks(callbacks: list[Callable[[], None]]) -> None:
        errors: list[Exception] = []
        for cleanup in callbacks:
            try:
                cleanup()
            except Exception as err:  # noqa: BLE001
                errors.append(err)
        if errors:
            raise RuntimeError("device trigger cleanup failed") from errors[0]
