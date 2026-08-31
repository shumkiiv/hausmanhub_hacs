"""Home Assistant Store adapter for durable tablet climate operations."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
from typing import TYPE_CHECKING

from homeassistant.helpers.storage import Store

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class HomeAssistantClimateOperationStore:
    """Persist a bounded versioned operation ledger per config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        *,
        reliable_scope_integrity_key: str = "",
        allow_unsigned_migration: bool = False,
    ) -> None:
        self.reliable_scope_integrity_key = reliable_scope_integrity_key
        self._integrity_key = (
            bytes.fromhex(reliable_scope_integrity_key)
            if re.fullmatch(r"[a-f0-9]{64}", reliable_scope_integrity_key)
            else None
        )
        self._allow_unsigned_migration = allow_unsigned_migration
        self._store: Store[dict[str, object]] = Store(
            hass,
            1,
            f"hausman_hub.climate_operations.{entry_id}",
        )
        self._reliable_scope_store: Store[dict[str, object]] = Store(
            hass,
            1,
            f"hausman_hub.climate_operation_scopes.{entry_id}",
        )
        # Tablet actions and native contour actions intentionally share one
        # file.  Their callers use different service locks, therefore the
        # storage boundary itself must serialize the read-modify-write merge.
        self._lock = asyncio.Lock()

    async def async_load(self) -> object | None:
        """Return the exact stored payload for strict application validation."""

        async with self._lock:
            return await self._load_main_unlocked()

    async def async_save(self, payload: dict[str, object]) -> None:
        """Atomically replace the complete bounded operation ledger."""

        async with self._lock:
            current = await self._load_main_unlocked()
            merged = dict(payload)
            if isinstance(current, dict):
                if "direct_control_records" in current:
                    merged["direct_control_records"] = current["direct_control_records"]
                current_revision = current.get("control_revision", 0)
                requested_revision = merged.get("control_revision", 0)
                if type(current_revision) is not int or type(requested_revision) is not int:
                    raise ValueError("stored control revision is invalid")
                merged["control_revision"] = max(current_revision, requested_revision)
            await self._save_main_unlocked(merged)

    async def async_load_reliable_scope_bindings(self) -> object | None:
        """Read the separate server-side binding for reliable device scopes."""

        async with self._lock:
            loaded = await self._reliable_scope_store.async_load()
            visible = dict(loaded) if isinstance(loaded, dict) else {}
            visible.pop("__storage_state__", None)
            return visible

    async def async_save_reliable_scope_bindings(
        self, bindings: dict[str, object]
    ) -> None:
        """Persist receipt scope provenance away from the public operation ledger."""

        async with self._lock:
            current = await self._reliable_scope_store.async_load()
            merged = dict(bindings)
            if isinstance(current, dict) and "__storage_state__" in current:
                merged["__storage_state__"] = current["__storage_state__"]
            await self._reliable_scope_store.async_save(merged)

    async def async_load_direct_control(self) -> object | None:
        """Read the isolated direct-control ledger, retaining tablet records."""

        async with self._lock:
            payload = await self._load_main_unlocked()
            return payload.get("direct_control_records", []) if isinstance(payload, dict) else None

    async def async_save_direct_control(self, records: list[dict[str, object]]) -> None:
        """Atomically merge direct receipts without discarding tablet records."""

        async with self._lock:
            payload = await self._load_main_unlocked()
            base = dict(payload) if isinstance(payload, dict) else {
                "version": 2, "records": [], "recoveries": [], "control_revision": 0,
                "desired_intents": {},
            }
            base["version"] = max(2, int(base.get("version", 2)))
            base["direct_control_records"] = records
            await self._save_main_unlocked(base)

    async def async_reserve_control_revision(self, expected: int) -> int:
        """Atomically allocate the one shared climate control revision."""

        if type(expected) is not int or expected < 0:
            raise ValueError("control revision is invalid")
        async with self._lock:
            payload = await self._load_main_unlocked()
            base = dict(payload) if isinstance(payload, dict) else {
                "version": 2, "records": [], "recoveries": [],
                "desired_intents": {}, "direct_control_records": [],
            }
            current = base.get("control_revision", 0)
            if type(current) is not int or current < 0:
                raise ValueError("stored control revision is invalid")
            if current != expected:
                raise ValueError("control revision is stale")
            base["version"] = max(2, int(base.get("version", 2)))
            base["control_revision"] = current + 1
            await self._save_main_unlocked(base)
            return current + 1

    async def async_current_control_revision(self) -> int:
        """Read the shared token under the same storage coordinator lock."""

        async with self._lock:
            payload = await self._load_main_unlocked()
            value = payload.get("control_revision", 0) if isinstance(payload, dict) else 0
            if type(value) is not int or value < 0:
                raise ValueError("stored control revision is invalid")
            return value

    async def async_migrate_integrity(self) -> None:
        """Sign one pre-feature payload, then permanently close unsigned reads."""

        async with self._lock:
            raw = await self._store.async_load()
            if raw is not None:
                payload = await self._load_main_unlocked()
                if payload is None:
                    raise ValueError("stored climate operation payload is invalid")
                if "storage_integrity_tag" not in raw:
                    await self._save_main_unlocked(payload)
            self._allow_unsigned_migration = False

    def _signed(self, payload: dict[str, object]) -> dict[str, object]:
        if self._integrity_key is None:
            return payload
        unsigned = {key: value for key, value in payload.items() if key != "storage_integrity_tag"}
        encoded = json.dumps(
            unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return {
            **unsigned,
            "storage_integrity_tag": hmac.new(
                self._integrity_key, encoded, hashlib.sha256
            ).hexdigest(),
        }

    async def _load_main_unlocked(self) -> dict[str, object] | None:
        raw = await self._store.async_load()
        sidecar = await self._reliable_scope_store.async_load()
        marker = sidecar.get("__storage_state__") if isinstance(sidecar, dict) else None
        return self._verified(raw, marker)

    async def _save_main_unlocked(self, payload: dict[str, object]) -> None:
        signed = self._signed(payload)
        if self._integrity_key is None:
            await self._store.async_save(signed)
            return
        sidecar = await self._reliable_scope_store.async_load()
        prepared = dict(sidecar) if isinstance(sidecar, dict) else {}
        prepared["__storage_state__"] = self._storage_state_checkpoint(
            prepared.get("__storage_state__"), signed, retain_previous=True
        )
        await self._reliable_scope_store.async_save(prepared)
        await self._store.async_save(signed)
        prepared["__storage_state__"] = self._storage_state_checkpoint(
            prepared["__storage_state__"], signed, retain_previous=False
        )
        await self._reliable_scope_store.async_save(prepared)

    def _storage_state_checkpoint(
        self, previous: object, payload: dict[str, object], *, retain_previous: bool,
    ) -> dict[str, object]:
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        fingerprint = hashlib.sha256(encoded).hexdigest()
        item = {
            "fingerprint": fingerprint,
            "integrity_tag": hmac.new(
                self._integrity_key, b"storage-state:" + encoded, hashlib.sha256
            ).hexdigest(),
        }
        old = previous.get("checkpoints", []) if isinstance(previous, dict) else []
        retained = [value for value in old if isinstance(value, dict) and value.get("fingerprint") != fingerprint]
        return {"checkpoints": ([*retained[-1:], item] if retain_previous else [item])}

    def _verified(self, payload: object, marker: object) -> dict[str, object] | None:
        if payload is None:
            return None
        if not isinstance(payload, dict):
            raise ValueError("stored climate operation payload is invalid")
        if self._integrity_key is None:
            return dict(payload)
        tag = payload.get("storage_integrity_tag")
        unsigned = {key: value for key, value in payload.items() if key != "storage_integrity_tag"}
        if tag is None and self._allow_unsigned_migration:
            return unsigned
        encoded = json.dumps(
            unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        expected = hmac.new(self._integrity_key, encoded, hashlib.sha256).hexdigest()
        if not isinstance(tag, str) or not hmac.compare_digest(tag, expected):
            raise ValueError("stored climate operation integrity is invalid")
        encoded_signed = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        fingerprint = hashlib.sha256(encoded_signed).hexdigest()
        state_tag = hmac.new(
            self._integrity_key, b"storage-state:" + encoded_signed, hashlib.sha256
        ).hexdigest()
        checkpoints = marker.get("checkpoints") if isinstance(marker, dict) else None
        if not isinstance(checkpoints, list) or not any(
            isinstance(item, dict)
            and item.get("fingerprint") == fingerprint
            and isinstance(item.get("integrity_tag"), str)
            and hmac.compare_digest(item["integrity_tag"], state_tag)
            for item in checkpoints
        ):
            raise ValueError("stored climate operation generation is invalid")
        return unsigned
