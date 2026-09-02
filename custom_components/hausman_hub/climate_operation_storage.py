"""Home Assistant Store adapter for durable tablet climate operations."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import hmac
import json
import re
from typing import TYPE_CHECKING

from homeassistant.helpers.storage import Store

from .climate_ledger_keyring import ClimateLedgerKeyring
from .climate_revision import MAX_JS_SAFE_INTEGER, is_control_revision
from .climate_storage_errors import ClimateOperationRevisionConflict

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class HomeAssistantClimateOperationStore:
    """Persist a bounded versioned operation ledger per config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        *,
        reliable_scope_integrity_key: str | ClimateLedgerKeyring = "",
        allow_unsigned_migration: bool = False,
        require_authenticated: bool = False,
    ) -> None:
        self._entry_id = entry_id
        self.reliable_scope_integrity_key = reliable_scope_integrity_key
        self._keyring = reliable_scope_integrity_key if isinstance(
            reliable_scope_integrity_key, ClimateLedgerKeyring
        ) else None
        self._integrity_key = (
            bytes.fromhex(reliable_scope_integrity_key)
            if isinstance(reliable_scope_integrity_key, str)
            and re.fullmatch(r"[a-f0-9]{64}", reliable_scope_integrity_key)
            else None
        )
        if self._keyring is not None:
            self._integrity_key = self._keyring.active_key
        self._allow_unsigned_migration = allow_unsigned_migration
        self._require_authenticated = require_authenticated
        # Set only by the explicit external-ledger initialization path.  A
        # key object alone is not proof that the persistent authenticated
        # ledger is usable.
        self._authenticated_external_ledger_initialized = False
        self._persistence_failed = False
        self._anchor_generation = 0
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
            self._require_mutation_ready()
            current = await self._load_main_unlocked()
            merged = dict(payload)
            if isinstance(current, dict):
                if "direct_control_records" in current:
                    merged["direct_control_records"] = current["direct_control_records"]
                current_revision = current.get("control_revision", 0)
                requested_revision = merged.get("control_revision", 0)
                if not is_control_revision(current_revision) or not is_control_revision(requested_revision):
                    raise ValueError("stored control revision is invalid")
                merged["control_revision"] = max(current_revision, requested_revision)
            if not is_control_revision(merged.get("control_revision", 0)):
                raise ValueError("stored control revision is invalid")
            await self._save_main_unlocked(merged)

    async def async_load_reliable_scope_bindings(self) -> object | None:
        """Read the separate server-side binding for reliable device scopes."""

        async with self._lock:
            loaded = await self._load_sidecar_unlocked()
            visible = dict(loaded) if isinstance(loaded, dict) else {}
            visible.pop("__storage_state__", None)
            return visible

    async def async_save_reliable_scope_bindings(
        self, bindings: dict[str, object]
    ) -> None:
        """Persist receipt scope provenance away from the public operation ledger."""

        async with self._lock:
            self._require_mutation_ready()
            current = await self._load_sidecar_unlocked()
            merged = dict(bindings)
            if isinstance(current, dict) and "__storage_state__" in current:
                merged["__storage_state__"] = current["__storage_state__"]
            await self._save_sidecar_unlocked(merged)

    async def async_load_direct_control(self) -> object | None:
        """Read the isolated direct-control ledger, retaining tablet records."""

        async with self._lock:
            payload = await self._load_main_unlocked()
            return payload.get("direct_control_records", []) if isinstance(payload, dict) else None

    async def async_direct_control_is_authenticated(self) -> bool:
        """Tell the runtime whether frozen direct history is HMAC-authenticated."""

        async with self._lock:
            raw = await self._store.async_load()
            authenticated = (
                self._integrity_key is not None
                and isinstance(raw, dict)
                and (
                    isinstance(raw.get("storage_integrity_tag"), str)
                    or self._is_authenticated_envelope(raw)
                )
                and not self._allow_unsigned_migration
            )
            if authenticated:
                await self._load_main_unlocked()
            return authenticated

    async def async_save_direct_control(self, records: list[dict[str, object]]) -> None:
        """Atomically merge direct receipts without discarding tablet records."""

        async with self._lock:
            self._require_mutation_ready()
            payload = await self._load_main_unlocked()
            base = dict(payload) if isinstance(payload, dict) else {
                "version": 2, "records": [], "recoveries": [], "control_revision": 0,
                "desired_intents": {},
            }
            if not is_control_revision(base.get("control_revision", 0)):
                raise ValueError("stored control revision is invalid")
            base["version"] = max(2, int(base.get("version", 2)))
            base["direct_control_records"] = records
            await self._save_main_unlocked(base)

    async def async_reserve_control_revision(self, expected: int) -> int:
        """Atomically allocate the one shared climate control revision."""

        if not is_control_revision(expected):
            raise ValueError("control revision is invalid")
        async with self._lock:
            self._require_mutation_ready()
            payload = await self._load_main_unlocked()
            base = dict(payload) if isinstance(payload, dict) else {
                "version": 2, "records": [], "recoveries": [],
                "desired_intents": {}, "direct_control_records": [],
            }
            current = base.get("control_revision", 0)
            if not is_control_revision(current):
                raise ValueError("stored control revision is invalid")
            if current != expected:
                raise ClimateOperationRevisionConflict("control revision is stale")
            if current >= MAX_JS_SAFE_INTEGER:
                raise ClimateOperationRevisionConflict("control revision is exhausted")
            base["version"] = max(2, int(base.get("version", 2)))
            base["control_revision"] = current + 1
            await self._save_main_unlocked(base)
            return current + 1

    async def async_current_control_revision(self) -> int:
        """Read the shared token under the same storage coordinator lock."""

        async with self._lock:
            payload = await self._load_main_unlocked()
            value = payload.get("control_revision", 0) if isinstance(payload, dict) else 0
            if not is_control_revision(value):
                raise ValueError("stored control revision is invalid")
            return value

    async def async_migrate_integrity(self) -> None:
        """Sign one pre-feature payload, then permanently close unsigned reads."""

        async with self._lock:
            self._require_mutation_ready()
            if self._require_authenticated and self._keyring is None:
                raise ValueError("external climate ledger keyring is unavailable")
            # This compatibility path is retained only for direct in-process
            # callers that still supply the old key explicitly. Production
            # setup always has an external keyring and uses reset, never this
            # migration path.
            if self._keyring is None:
                raw = await self._store.async_load()
                if raw is not None:
                    payload = await self._load_main_unlocked()
                    if payload is None:
                        raise ValueError("stored climate operation payload is invalid")
                    if "storage_integrity_tag" not in raw:
                        await self._save_main_unlocked(payload)
            self._allow_unsigned_migration = False

    async def async_initialize_external_ledger(self) -> bool:
        """Start a new anchored ledger instead of trusting pre-keyring history.

        Before an external anchor exists both local files are untrusted. This
        deliberately removes old flat records and nested Tablet state.
        """

        if self._keyring is None or self._keyring.source_path is None:
            raise ValueError("external climate ledger keyring is unavailable")
        async with self._lock:
            self._require_mutation_ready()
            if self._keyring.has_committed_ledger_anchor(self._entry_id):
                # An anchored ledger must either validate or fail closed. A
                # reset here would hide a rollback or local substitution.
                payload = await self._load_main_unlocked()
                if payload is None:
                    raise ValueError("committed climate ledger anchor has no authenticated payload")
                self._authenticated_external_ledger_initialized = True
                return False
            if self._keyring.has_ledger_anchor(self._entry_id):
                # A pending first anchor may be the last step of a successful
                # local write. Only its exact signed envelope may promote it.
                # Any flat or mismatched local state is still untrusted and is
                # overwritten below without parsing or replaying legacy data.
                stored = await self._store.async_load()
                if not self._is_authenticated_envelope(stored):
                    raise ValueError("pending climate ledger anchor has no authenticated payload")
                payload = await self._load_main_unlocked()
                if payload is None:
                    raise ValueError("pending climate ledger anchor has no authenticated payload")
                self._authenticated_external_ledger_initialized = True
                return False
            self._anchor_generation = 0
            await self._save_sidecar_unlocked({})
            await self._save_main_unlocked({
                "version": 2,
                "records": [],
                "recoveries": [],
                "control_revision": 0,
                "desired_intents": {},
                "direct_control_records": [],
            })
            self._allow_unsigned_migration = False
            self._authenticated_external_ledger_initialized = True
            return True

    @property
    def authenticated_external_ledger_ready(self) -> bool:
        """Whether setup verified the persistent external ledger boundary."""

        return (
            self._authenticated_external_ledger_initialized
            and not self._persistence_failed
            and self._require_authenticated
            and self._keyring is not None
            and self._keyring.source_path is not None
        )

    def _require_mutation_ready(self) -> None:
        """A failed durable write remains terminal for this store instance."""

        if self._persistence_failed:
            raise RuntimeError("climate operation persistence requires restart")

    def _signed(self, payload: dict[str, object], *, ledger_generation: int | None = None) -> dict[str, object]:
        if self._integrity_key is None:
            return payload
        if self._keyring is not None:
            return self._authenticated_envelope(payload, ledger_generation=ledger_generation)
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
        stored = await self._store.async_load()
        raw = self._unwrap_authenticated(stored)
        sidecar = await self._load_sidecar_unlocked()
        marker = sidecar.get("__storage_state__") if isinstance(sidecar, dict) else None
        return self._verified(raw, marker, stored)

    async def _load_sidecar_unlocked(self) -> dict[str, object]:
        loaded = self._unwrap_authenticated(await self._reliable_scope_store.async_load())
        return dict(loaded) if isinstance(loaded, dict) else {}

    async def _save_sidecar_unlocked(self, payload: dict[str, object]) -> None:
        # Store implementations may retain the supplied object until their
        # asynchronous write completes. Never mutate that object afterwards.
        try:
            value: dict[str, object] = deepcopy(payload)
            if self._keyring is not None:
                value = self._authenticated_envelope(payload)
                value["payload"] = deepcopy(payload)
            await self._reliable_scope_store.async_save(value)
        except Exception:
            self._persistence_failed = True
            raise

    def _authenticated_envelope(self, payload: dict[str, object], *, ledger_generation: int | None = None) -> dict[str, object]:
        if self._keyring is None:
            raise ValueError("climate ledger keyring is unavailable")
        body = {
            "format": "hausman_climate_ledger_auth_v1",
            "key_id": self._keyring.active_key_id,
            "payload": payload,
        }
        if ledger_generation is not None:
            body["ledger_generation"] = ledger_generation
        return {**body, "authentication_tag": self._tag(self._keyring.active_key, body)}

    def _unwrap_authenticated(self, raw: object) -> object:
        if raw is None or self._keyring is None:
            return raw
        if not isinstance(raw, dict):
            raise ValueError("stored climate operation payload is invalid")
        if raw.get("format") != "hausman_climate_ledger_auth_v1":
            if self._allow_unsigned_migration:
                return raw
            raise ValueError("stored climate operation authentication is invalid")
        if set(raw) not in (
            {"format", "key_id", "payload", "authentication_tag"},
            {"format", "key_id", "payload", "authentication_tag", "ledger_generation"},
        ):
            raise ValueError("stored climate operation authentication is invalid")
        if "ledger_generation" in raw and (type(raw["ledger_generation"]) is not int or raw["ledger_generation"] < 1):
            raise ValueError("stored climate operation authentication is invalid")
        key = self._keyring.key_for(raw.get("key_id"))
        tag = raw.get("authentication_tag")
        body = {name: value for name, value in raw.items() if name != "authentication_tag"}
        if not isinstance(key, bytes) or not isinstance(tag, str) or not hmac.compare_digest(tag, self._tag(key, body)):
            raise ValueError("stored climate operation authentication is invalid")
        if not isinstance(raw.get("payload"), dict):
            raise ValueError("stored climate operation payload is invalid")
        return raw["payload"]

    @staticmethod
    def _tag(key: bytes, value: object) -> str:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hmac.new(key, encoded, hashlib.sha256).hexdigest()

    async def _save_main_unlocked(self, payload: dict[str, object]) -> None:
        try:
            await self._save_main_unlocked_impl(payload)
        except Exception:
            self._persistence_failed = True
            raise

    async def _save_main_unlocked_impl(self, payload: dict[str, object]) -> None:
        if self._require_authenticated and self._keyring is None:
            raise ValueError("external climate ledger keyring is unavailable")
        generation = self._anchor_generation + 1 if self._keyring is not None and self._keyring.source_path is not None else None
        signed = self._signed(payload, ledger_generation=generation)
        if self._keyring is not None and generation is not None:
            self._keyring.prepare_ledger_anchor(self._entry_id, signed)
        if self._integrity_key is None:
            await self._store.async_save(signed)
            return
        sidecar = await self._load_sidecar_unlocked()
        prepared = dict(sidecar) if isinstance(sidecar, dict) else {}
        prepared["__storage_state__"] = self._storage_state_checkpoint(
            prepared.get("__storage_state__"), signed, retain_previous=True
        )
        await self._save_sidecar_unlocked(prepared)
        await self._store.async_save(signed)
        prepared["__storage_state__"] = self._storage_state_checkpoint(
            prepared["__storage_state__"], signed, retain_previous=False
        )
        await self._save_sidecar_unlocked(prepared)
        if self._keyring is not None and generation is not None:
            self._keyring.finalize_ledger_anchor(self._entry_id, signed)
            self._anchor_generation = generation

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

    def _verified(
        self, payload: object, marker: object, stored: object | None = None,
    ) -> dict[str, object] | None:
        if payload is None:
            return None
        if self._require_authenticated and self._keyring is None:
            raise ValueError("external climate ledger keyring is unavailable")
        if not isinstance(payload, dict):
            raise ValueError("stored climate operation payload is invalid")
        if not is_control_revision(payload.get("control_revision", 0)):
            raise ValueError("stored control revision is invalid")
        if self._integrity_key is None:
            return dict(payload)
        if self._keyring is not None and self._is_authenticated_envelope(stored):
            if self._keyring.source_path is not None:
                if not self._keyring.verify_ledger_anchor(self._entry_id, stored):
                    raise ValueError("stored climate operation anchor is invalid")
                generation = stored.get("ledger_generation")
                if type(generation) is not int or generation < 1:
                    raise ValueError("stored climate operation anchor is invalid")
                self._anchor_generation = generation
            encoded_envelope = json.dumps(
                stored, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            if not self._valid_generation(marker, encoded_envelope):
                raise ValueError("stored climate operation generation is invalid")
            if self._require_authenticated and self._keyring.source_path is not None:
                self._authenticated_external_ledger_initialized = True
            return dict(payload)
        tag = payload.get("storage_integrity_tag")
        unsigned = {key: value for key, value in payload.items() if key != "storage_integrity_tag"}
        if tag is None and self._allow_unsigned_migration and self._keyring is None:
            return unsigned
        encoded = json.dumps(
            unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if not isinstance(tag, str) or not self._legacy_or_integrity_matches(tag, encoded):
            raise ValueError("stored climate operation integrity is invalid")
        encoded_signed = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if not self._valid_generation(marker, encoded_signed):
            raise ValueError("stored climate operation generation is invalid")
        return unsigned

    @staticmethod
    def _is_authenticated_envelope(value: object) -> bool:
        return isinstance(value, dict) and value.get("format") == "hausman_climate_ledger_auth_v1"

    def _valid_generation(self, marker: object, encoded: bytes) -> bool:
        fingerprint = hashlib.sha256(encoded).hexdigest()
        checkpoints = marker.get("checkpoints") if isinstance(marker, dict) else None
        return isinstance(checkpoints, list) and any(
            isinstance(item, dict)
            and item.get("fingerprint") == fingerprint
            and isinstance(item.get("integrity_tag"), str)
            and self._legacy_or_integrity_matches(
                item["integrity_tag"], b"storage-state:" + encoded
            )
            for item in checkpoints
        )

    def _legacy_or_integrity_matches(self, tag: str, payload: bytes) -> bool:
        return self._integrity_matches(tag, payload)

    def _integrity_matches(self, tag: str, payload: bytes) -> bool:
        keys = self._keyring.keys.values() if self._keyring is not None else (self._integrity_key,)
        return any(
            isinstance(key, bytes)
            and hmac.compare_digest(tag, hmac.new(key, payload, hashlib.sha256).hexdigest())
            for key in keys
        )
