"""Server-owned, command-free archive and reset protocol for the journal."""

from __future__ import annotations

import asyncio
import base64
import binascii
import copy
import hashlib
import hmac
import json
import math
import re
import secrets
import time
from collections.abc import Mapping
from typing import Callable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .operation_journal import MAX_OPERATION_JOURNAL_RECORDS, OperationJournalService

MAX_ARCHIVES = 64
MAX_ARCHIVE_BYTES = 67_108_864
MAX_PENDING_ARCHIVES_GLOBAL = 8
RATE_WINDOW_MS = 60_000
RATE_LIMITS = {
    "archive": {"admin": 2, "global": 10},
    "reset": {"admin": 6, "global": 30},
}
TOKEN_TTL_MS = 900_000
MIN_COMPLETED_RETENTION_MS = 2_592_000_000
MIN_COMPLETED_GENERATIONS = 8
_MAX_ADMIN_ID_LENGTH = 128
_CORRELATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_ARCHIVE_TOKEN = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
_ARCHIVE_ID = re.compile(r"^journal\.[A-Za-z0-9_-]{32}$")
_ARCHIVE_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_ARCHIVE_NONCE = re.compile(r"^[A-Za-z0-9_-]{16}$")
_ARCHIVE_CIPHERTEXT = re.compile(r"^[A-Za-z0-9_-]{79,192}$")
_ARCHIVE_AUTH_TAG = re.compile(r"^[a-f0-9]{64}$")
_RESET_TRANSACTION_ID = re.compile(r"^[A-Za-z0-9_-]{32}$")
_HEX_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_REVISION = re.compile(r"^[a-f0-9]{16,64}$")
_MAX_SAFE_INTEGER = 9_007_199_254_740_991
_MAX_LOAD_ARCHIVES = 256
_MAX_LOAD_REFERENCES = 256
_MAX_LOAD_RATE_ADMINS = 256
_MAX_LOAD_RATE_POINTS = 256
_MAX_TREE_DEPTH = 16
_MAX_SNAPSHOT_TREE_NODES = 100_000
_MAX_STORE_TREE_NODES = 2_000_000
_MAX_CONTAINER_ITEMS = 4_096
_MAX_STRING_BYTES = 16_384
_MAX_SNAPSHOT_PREFLIGHT_BYTES = 2_097_152
_MAX_STORE_PREFLIGHT_BYTES = MAX_ARCHIVE_BYTES + 16_777_216
_TOKEN_STATES = frozenset({"pending", "reset_prepared", "journal_committed", "consumed"})


class JournalArchiveError(ValueError):
    """A safe typed archive/reset rejection."""

    def __init__(
        self,
        detail_code: str,
        *,
        retry_after_seconds: int | None = None,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(detail_code)
        self.detail_code = detail_code
        self.retry_after_seconds = retry_after_seconds
        self.details = dict(details or {})


def canonical_json(value: object) -> str:
    """Return RFC 8785 JCS for the supported I-JSON value domain."""

    def encode(item: object) -> str:
        if item is None:
            return "null"
        if item is True:
            return "true"
        if item is False:
            return "false"
        if isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeEncodeError as error:
                raise ValueError("JCS string is not valid Unicode") from error
            return json.dumps(item, ensure_ascii=False, separators=(",", ":"))
        if type(item) is int:
            if abs(item) > 9_007_199_254_740_991:
                raise ValueError("JCS integer is outside the I-JSON safe range")
            return str(item)
        if type(item) is float:
            return _jcs_number(item)
        if isinstance(item, (list, tuple)):
            return "[" + ",".join(encode(member) for member in item) + "]"
        if isinstance(item, Mapping):
            if not all(isinstance(key, str) for key in item):
                raise ValueError("JCS object key is not a string")
            try:
                keys = sorted(item, key=lambda key: key.encode("utf-16be"))
            except UnicodeEncodeError as error:
                raise ValueError("JCS object key is not valid Unicode") from error
            return "{" + ",".join(f"{encode(key)}:{encode(item[key])}" for key in keys) + "}"
        raise ValueError("value is outside the I-JSON domain")

    return encode(value)


def _jcs_number(value: float) -> str:
    """Serialize one finite binary64 value with ECMAScript/JCS spelling."""

    if not math.isfinite(value):
        raise ValueError("JCS number is not finite")
    if value == 0:
        return "0"
    rendered = repr(value).lower()
    magnitude = abs(value)
    if 1e-6 <= magnitude < 1e21:
        if "e" in rendered:
            coefficient, exponent = rendered.split("e")
            sign = ""
            if coefficient.startswith("-"):
                sign, coefficient = "-", coefficient[1:]
            digits = coefficient.replace(".", "")
            decimal_at = coefficient.find(".")
            decimal_at = len(coefficient) if decimal_at < 0 else decimal_at
            decimal_at += int(exponent)
            if decimal_at <= 0:
                return sign + "0." + "0" * (-decimal_at) + digits
            if decimal_at >= len(digits):
                return sign + digits + "0" * (decimal_at - len(digits))
            return sign + digits[:decimal_at] + "." + digits[decimal_at:]
        return rendered[:-2] if rendered.endswith(".0") else rendered
    if "e" not in rendered:
        return rendered
    coefficient, exponent = rendered.split("e")
    exponent_value = int(exponent)
    return f"{coefficient}e{'+' if exponent_value >= 0 else ''}{exponent_value}"


class OperationJournalArchiveService:
    """Keep immutable archives and encrypted single-use reset credentials."""

    def __init__(
        self,
        journal: OperationJournalService,
        store: object | None = None,
        *,
        keyring: object | None = None,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self.journal = journal
        self.store = store
        self._keyring = keyring
        self._now_ms = now_ms or (lambda: time.time_ns() // 1_000_000)
        self._archives: dict[str, dict[str, object]] = {}
        self._pending: dict[str, str] = {}
        self._used: set[str] = set()
        self._rate_buckets: dict[str, dict[str, list[int]]] = {
            "archive": {},
            "reset": {},
        }
        self._reset_transaction: dict[str, object] | None = None
        self._available = True
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        """Load and verify bounded state without endangering integration setup."""

        try:
            self._available = True
            load = getattr(self.store, "async_load", None)
            payload = await load() if callable(load) else None
            if payload is None:
                return
            if not isinstance(payload, Mapping):
                raise JournalArchiveError("archive_storage_unavailable")
            if payload.get("version") in {1, 2}:
                # These unpublished formats either retained a plaintext token or
                # had no authentication over mutable archive metadata. Never
                # bless that evidence with a new key: scrub it as a unit.
                self._clear_state()
                save = getattr(self.store, "async_save", None)
                if callable(save):
                    # Remove any legacy plaintext before key validation. The
                    # empty marker is intentionally still legacy so a later
                    # start with a valid keyring upgrades it to signed v3.
                    await save({"version": 1, "archives": {}})
                await self._save()
                return
            if payload.get("version") != 3:
                raise JournalArchiveError("archive_storage_unavailable")
            self._clear_state()
            self._load_v3(payload)
            self._verify_loaded_state()
            await self._recover_reset_transaction()
            self._rotate_record_keys()
            now = max(0, self._now_ms())
            self._expire(now)
            self._prune_rate(now)
            self._normalize_references()
            await self._save()
        except Exception:
            self._clear_state()
            self._available = False

    def _clear_state(self) -> None:
        self._archives = {}
        self._pending = {}
        self._used = set()
        self._rate_buckets = {"archive": {}, "reset": {}}
        self._reset_transaction = None

    def _load_v3(self, payload: Mapping[str, object]) -> None:
        if set(payload) != {
            "version", "archives", "pending", "used", "rateBuckets",
            "resetTransaction", "storeAuthKeyId", "storeAuthTag",
        }:
            raise JournalArchiveError("archive_storage_unavailable")
        _validate_bounded_tree(
            payload,
            max_nodes=_MAX_STORE_TREE_NODES,
            max_bytes=_MAX_STORE_PREFLIGHT_BYTES,
        )
        self._verify_store_payload(payload)
        candidates: list[tuple[str, dict[str, object]]] = []
        archives = payload.get("archives")
        if not isinstance(archives, Mapping) or len(archives) > _MAX_LOAD_ARCHIVES:
            raise JournalArchiveError("archive_storage_unavailable")
        for key, value in archives.items():
            if (
                not isinstance(key, str)
                or _ARCHIVE_ID.fullmatch(key) is None
                or not isinstance(value, Mapping)
                or not _valid_archive_record(value)
            ):
                raise JournalArchiveError("archive_storage_unavailable")
            record = copy.deepcopy(dict(value))
            self._verify_record(key, record)
            candidates.append((key, record))
        candidates.sort(key=lambda item: (int(item[1]["issuedAt"]), item[0]), reverse=True)
        retained_bytes = 0
        for archive_id, record in candidates:
            try:
                size = _archive_snapshot_size(record)
            except (TypeError, ValueError):
                continue
            if size is None:
                continue
            if len(self._archives) >= MAX_ARCHIVES or retained_bytes + size > MAX_ARCHIVE_BYTES:
                continue
            self._archives[archive_id] = record
            retained_bytes += size
        pending = payload.get("pending")
        if not isinstance(pending, Mapping) or len(pending) > _MAX_LOAD_REFERENCES:
            raise JournalArchiveError("archive_storage_unavailable")
        self._pending = {
            admin: archive_id
            for admin, archive_id in pending.items()
            if isinstance(admin, str)
            and 0 < len(admin) <= _MAX_ADMIN_ID_LENGTH
            and isinstance(archive_id, str)
            and archive_id in self._archives
        }
        if len(self._pending) != len(pending):
            raise JournalArchiveError("archive_storage_unavailable")
        used = payload.get("used")
        if not isinstance(used, list) or len(used) > _MAX_LOAD_REFERENCES:
            raise JournalArchiveError("archive_storage_unavailable")
        self._used = {
            digest for digest in used
            if isinstance(digest, str) and _HEX_DIGEST.fullmatch(digest) is not None
        }
        if len(self._used) != len(used):
            raise JournalArchiveError("archive_storage_unavailable")
        buckets = payload.get("rateBuckets")
        if not isinstance(buckets, Mapping):
            raise JournalArchiveError("archive_storage_unavailable")
        for operation, limits in RATE_LIMITS.items():
            operation_buckets = buckets.get(operation, {})
            if (
                not isinstance(operation_buckets, Mapping)
                or len(operation_buckets) > _MAX_LOAD_RATE_ADMINS
            ):
                raise JournalArchiveError("archive_storage_unavailable")
            flattened: list[tuple[int, str]] = []
            for admin, values in operation_buckets.items():
                if (
                    not isinstance(admin, str)
                    or not 0 < len(admin) <= _MAX_ADMIN_ID_LENGTH
                    or not isinstance(values, list)
                    or len(values) > _MAX_LOAD_RATE_POINTS
                ):
                    raise JournalArchiveError("archive_storage_unavailable")
                flattened.extend(
                    (point, admin)
                    for point in values
                    if type(point) is int and point >= 0
                )
            for point, admin in sorted(flattened, reverse=True)[: int(limits["global"])]:
                self._rate_buckets[operation].setdefault(admin, []).append(point)
            for values in self._rate_buckets[operation].values():
                values.sort()
        transaction = payload.get("resetTransaction")
        self._reset_transaction = (
            dict(transaction) if isinstance(transaction, Mapping) else None
        )
        if transaction is not None and not _valid_reset_transaction(transaction):
            raise JournalArchiveError("archive_storage_unavailable")

    async def _save(self) -> None:
        save = getattr(self.store, "async_save", None)
        if callable(save):
            payload = {
                "version": 3,
                "archives": copy.deepcopy(self._archives),
                "pending": dict(self._pending),
                "used": sorted(self._used),
                "rateBuckets": copy.deepcopy(self._rate_buckets),
                "resetTransaction": copy.deepcopy(self._reset_transaction),
                "storeAuthKeyId": "",
                "storeAuthTag": "",
            }
            self._sign_store_payload(payload)
            await save(payload)

    def _state(self) -> tuple[
        dict[str, dict[str, object]],
        dict[str, str],
        set[str],
        dict[str, dict[str, list[int]]],
        dict[str, object] | None,
    ]:
        return (
            copy.deepcopy(self._archives),
            dict(self._pending),
            set(self._used),
            copy.deepcopy(self._rate_buckets),
            copy.deepcopy(self._reset_transaction),
        )

    def _restore(self, state: tuple[
        dict[str, dict[str, object]],
        dict[str, str],
        set[str],
        dict[str, dict[str, list[int]]],
        dict[str, object] | None,
    ]) -> None:
        (
            self._archives,
            self._pending,
            self._used,
            self._rate_buckets,
            self._reset_transaction,
        ) = state

    async def _save_or_rollback(self, state: tuple[
        dict[str, dict[str, object]],
        dict[str, str],
        set[str],
        dict[str, dict[str, list[int]]],
        dict[str, object] | None,
    ]) -> None:
        try:
            await self._save()
        except Exception as error:
            self._restore(state)
            self._available = False
            raise JournalArchiveError("archive_storage_unavailable") from error

    def _ensure_available(self) -> None:
        if not self._available:
            raise JournalArchiveError("archive_storage_unavailable")

    def _verify_loaded_state(self) -> None:
        expected_pending: dict[str, str] = {}
        expected_used: set[str] = set()
        transitional: list[tuple[str, dict[str, object]]] = []
        for archive_id, record in self._archives.items():
            owner_id = str(record["ownerId"])
            if record["consumed"]:
                expected_used.add(str(record["tokenDigest"]))
                continue
            if owner_id in expected_pending:
                raise JournalArchiveError("archive_storage_unavailable")
            expected_pending[owner_id] = archive_id
            if record["tokenState"] != "pending":
                transitional.append((archive_id, record))
        if self._pending != expected_pending:
            raise JournalArchiveError("archive_storage_unavailable")
        self._used = expected_used
        transaction = self._reset_transaction
        if transaction is None:
            if transitional:
                raise JournalArchiveError("archive_storage_unavailable")
            return
        if len(transitional) != 1:
            raise JournalArchiveError("archive_storage_unavailable")
        archive_id, record = transitional[0]
        expected_state = {
            "reset_prepared": "prepared",
            "journal_committed": "journal_committed",
        }.get(str(record["tokenState"]))
        if (
            expected_state is None
            or transaction.get("phase") != expected_state
            or transaction.get("archiveId") != archive_id
            or transaction.get("ownerId") != record["ownerId"]
            or transaction.get("id") != record["resetTransactionId"]
            or transaction.get("expectedGeneration") != record["generation"]
            or transaction.get("expectedSequence") != record["sequence"]
            or transaction.get("expectedRevision") != record["revision"]
        ):
            raise JournalArchiveError("archive_storage_unavailable")

    async def _recover_reset_transaction(self) -> None:
        transaction = self._reset_transaction
        if transaction is None:
            return
        archive_id = str(transaction["archiveId"])
        owner_id = str(transaction["ownerId"])
        record = self._archives[archive_id]
        token = self._decrypt_token(archive_id, record)
        current = self.journal.snapshot(limit=MAX_OPERATION_JOURNAL_RECORDS)
        expected_generation = int(transaction["expectedGeneration"])
        phase = transaction["phase"]
        if current["generation"] > expected_generation:
            self._mark_record_consumed(record)
            self._used.add(str(record["tokenDigest"]))
            self._pending.pop(owner_id, None)
            self._reset_transaction = None
            await self._save()
            return
        if (
            phase == "prepared"
            and current["generation"] == expected_generation
        ):
            self._remove_reset_rate_point(owner_id, record["resetRatePoint"])
            record["tokenState"] = "pending"
            record["resetTransactionId"] = None
            record["resetRatePoint"] = None
            self._seal_token(archive_id, record, token)
            self._sign_record(archive_id, record)
            self._reset_transaction = None
            await self._save()
            return
        raise JournalArchiveError("archive_storage_unavailable")

    def _rotate_record_keys(self) -> None:
        """Reprotect verified records with the active external key."""

        active_key_id, _ = self._active_key()
        for archive_id, record in self._archives.items():
            if record["consumed"]:
                if record["recordAuthKeyId"] != active_key_id:
                    self._sign_record(archive_id, record)
                continue
            if (
                record["tokenKeyId"] != active_key_id
                or record["recordAuthKeyId"] != active_key_id
            ):
                token = self._decrypt_token(archive_id, record)
                self._seal_token(archive_id, record, token)
                self._sign_record(archive_id, record)

    def _prune_rate(self, now: int) -> bool:
        floor = now - RATE_WINDOW_MS
        changed = False
        for operation in RATE_LIMITS:
            old = self._rate_buckets.get(operation, {})
            new = {
                admin: [point for point in points if floor < point <= now]
                for admin, points in old.items()
            }
            new = {admin: points for admin, points in new.items() if points}
            if new != old:
                changed = True
            self._rate_buckets[operation] = new
        return changed

    def _consume_rate(self, operation: str, admin_id: str, now: int) -> None:
        if not isinstance(admin_id, str) or not 0 < len(admin_id) <= _MAX_ADMIN_ID_LENGTH:
            raise JournalArchiveError("invalid_request")
        self._prune_rate(now)
        limits = RATE_LIMITS[operation]
        buckets = self._rate_buckets[operation]
        admin_points = buckets.get(admin_id, [])
        all_points = [point for points in buckets.values() for point in points]
        limiting = None
        if len(admin_points) >= int(limits["admin"]):
            limiting = admin_points
        elif len(all_points) >= int(limits["global"]):
            limiting = all_points
        if limiting:
            retry_ms = min(limiting) + RATE_WINDOW_MS - now
            raise JournalArchiveError(
                "reset_rate_limited",
                retry_after_seconds=max(1, math.ceil(retry_ms / 1000)),
            )
        buckets.setdefault(admin_id, []).append(now)

    def _remove_reset_rate_point(self, admin_id: str, point: object) -> None:
        if type(point) is not int:
            raise JournalArchiveError("archive_storage_unavailable")
        points = self._rate_buckets["reset"].get(admin_id)
        if not isinstance(points, list):
            raise JournalArchiveError("archive_storage_unavailable")
        try:
            points.remove(point)
        except ValueError as error:
            raise JournalArchiveError("archive_storage_unavailable") from error
        if not points:
            self._rate_buckets["reset"].pop(admin_id, None)

    def _expire(self, now: int) -> bool:
        changed = False
        for admin_id, archive_id in tuple(self._pending.items()):
            archive = self._archives.get(archive_id)
            if archive is None or archive.get("consumed") or now >= int(archive.get("expiresAt", 0)):
                if archive is not None and not archive.get("consumed"):
                    if archive.get("tokenState") != "pending":
                        raise JournalArchiveError("archive_storage_unavailable")
                    self._verify_record(archive_id, archive)
                    self._mark_record_consumed(archive)
                    self._used.add(str(archive["tokenDigest"]))
                self._pending.pop(admin_id, None)
                changed = True
        return changed

    def _normalize_references(self) -> None:
        seen: set[str] = set()
        normalized: dict[str, str] = {}
        for admin, archive_id in self._pending.items():
            archive = self._archives.get(archive_id)
            if (
                archive is None
                or archive["consumed"]
                or archive_id in seen
                or archive.get("ownerId") != admin
            ):
                continue
            normalized[admin] = archive_id
            seen.add(archive_id)
        self._pending = normalized
        retained_used = {
            str(record["tokenDigest"])
            for record in self._archives.values()
            if record["consumed"]
        }
        self._used.intersection_update(retained_used)

    def _archive_bytes(self) -> int:
        return sum(
            len(canonical_json(record["snapshot"]).encode("utf-8"))
            for record in self._archives.values()
        )

    @staticmethod
    def _discard_encrypted_token(record: dict[str, object]) -> None:
        record["tokenKeyId"] = ""
        record["tokenNonce"] = ""
        record["tokenCiphertext"] = ""

    def _mark_record_consumed(self, record: dict[str, object]) -> None:
        archive_id = next(
            (
                candidate_id
                for candidate_id, candidate in self._archives.items()
                if candidate is record
            ),
            None,
        )
        if archive_id is None:
            raise JournalArchiveError("archive_storage_unavailable")
        record["consumed"] = True
        record["tokenState"] = "consumed"
        record["resetTransactionId"] = None
        record["resetRatePoint"] = None
        self._discard_encrypted_token(record)
        self._sign_record(archive_id, record)

    def _evictable(self, archive_id: str, record: Mapping[str, object], now: int, current_generation: int) -> bool:
        if not record.get("consumed") or archive_id in self._pending.values():
            return False
        if int(record["generation"]) == current_generation:
            return False
        completed_at = record.get("completedAt")
        if completed_at is None:
            return now >= int(record["expiresAt"])
        if now - int(completed_at) < MIN_COMPLETED_RETENTION_MS:
            return False
        completed_generations = sorted(
            {
                int(candidate["generation"])
                for candidate in self._archives.values()
                if candidate.get("completedAt") is not None
            },
            reverse=True,
        )
        return int(record["generation"]) not in completed_generations[:MIN_COMPLETED_GENERATIONS]

    def _make_room(self, incoming_bytes: int, now: int, current_generation: int) -> bool:
        while len(self._archives) >= MAX_ARCHIVES or self._archive_bytes() + incoming_bytes > MAX_ARCHIVE_BYTES:
            candidates = sorted(
                (
                    (archive_id, record)
                    for archive_id, record in self._archives.items()
                    if self._evictable(archive_id, record, now, current_generation)
                ),
                key=lambda item: (int(item[1].get("completedAt") or item[1]["expiresAt"]), item[0]),
            )
            if not candidates:
                return False
            archive_id, record = candidates[0]
            self._archives.pop(archive_id)
            self._used.discard(str(record["tokenDigest"]))
        return True

    def _active_key(self) -> tuple[str, bytes]:
        key_id = getattr(self._keyring, "active_key_id", None)
        key = getattr(self._keyring, "active_key", None)
        if (
            getattr(self._keyring, "backup_separated", None) is not True
            or not isinstance(key_id, str)
            or not key_id
            or not isinstance(key, bytes)
            or len(key) != 32
        ):
            raise JournalArchiveError("archive_storage_unavailable")
        return key_id, key

    def _key_for(self, key_id: object) -> bytes | None:
        getter = getattr(self._keyring, "key_for", None)
        if callable(getter):
            key = getter(key_id)
        else:
            keys = getattr(self._keyring, "keys", None)
            key = keys.get(key_id) if isinstance(keys, Mapping) else None
        return key if isinstance(key, bytes) and len(key) == 32 else None

    @staticmethod
    def _encode_binary(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_binary(value: object) -> bytes:
        if not isinstance(value, str):
            raise ValueError
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    @staticmethod
    def _protected_record_payload(
        archive_id: str, record: Mapping[str, object]
    ) -> dict[str, object]:
        return {
            "archiveId": archive_id,
            "ownerId": record["ownerId"],
            "snapshot": record["snapshot"],
            "tokenDigest": record["tokenDigest"],
            "tokenKeyId": record["tokenKeyId"],
            "tokenNonce": record["tokenNonce"],
            "generation": record["generation"],
            "sequence": record["sequence"],
            "revision": record["revision"],
            "archiveSha256": record["archiveSha256"],
            "expiresAt": record["expiresAt"],
            "issuedAt": record["issuedAt"],
            "consumed": record["consumed"],
            "completedAt": record["completedAt"],
            "correlationId": record["correlationId"],
            "tokenState": record["tokenState"],
            "resetTransactionId": record["resetTransactionId"],
            "resetRatePoint": record["resetRatePoint"],
        }

    def _archive_aad(self, archive_id: str, record: Mapping[str, object]) -> bytes:
        return canonical_json(
            {
                "context": "hausman-journal-archive-token-v2",
                "record": self._protected_record_payload(archive_id, record),
            }
        ).encode("utf-8")

    def _record_auth_payload(
        self, archive_id: str, record: Mapping[str, object]
    ) -> bytes:
        return canonical_json(
            {
                "context": "hausman-journal-archive-record-v1",
                "record": {
                    **self._protected_record_payload(archive_id, record),
                    "tokenCiphertext": record["tokenCiphertext"],
                },
            }
        ).encode("utf-8")

    def _seal_token(
        self, archive_id: str, record: dict[str, object], token: str
    ) -> None:
        key_id, root_key = self._active_key()
        key = self._archive_encryption_key(root_key)
        nonce = secrets.token_bytes(12)
        record["tokenKeyId"] = key_id
        record["tokenNonce"] = self._encode_binary(nonce)
        record["tokenCiphertext"] = ""
        record["tokenCiphertext"] = self._encode_binary(AESGCM(key).encrypt(
            nonce,
            token.encode("ascii"),
            self._archive_aad(archive_id, record),
        ))

    def _decrypt_token(self, archive_id: str, record: Mapping[str, object]) -> str:
        root_key = self._key_for(record.get("tokenKeyId"))
        if root_key is None:
            raise JournalArchiveError("archive_storage_unavailable")
        key = self._archive_encryption_key(root_key)
        try:
            token = AESGCM(key).decrypt(
                self._decode_binary(record.get("tokenNonce")),
                self._decode_binary(record.get("tokenCiphertext")),
                self._archive_aad(archive_id, record),
            ).decode("ascii")
        except (ValueError, UnicodeDecodeError, binascii.Error, InvalidTag) as error:
            raise JournalArchiveError("archive_storage_unavailable") from error
        if not secrets.compare_digest(hashlib.sha256(token.encode()).hexdigest(), str(record["tokenDigest"])):
            raise JournalArchiveError("archive_storage_unavailable")
        return token

    def _sign_record(self, archive_id: str, record: dict[str, object]) -> None:
        key_id, root_key = self._active_key()
        record["recordAuthKeyId"] = key_id
        record["recordAuthTag"] = hmac.new(
            self._archive_authentication_key(root_key),
            self._record_auth_payload(archive_id, record),
            hashlib.sha256,
        ).hexdigest()

    def _sign_store_payload(self, payload: dict[str, object]) -> None:
        key_id, root_key = self._active_key()
        payload["storeAuthKeyId"] = key_id
        unsigned = dict(payload)
        unsigned.pop("storeAuthTag", None)
        payload["storeAuthTag"] = hmac.new(
            self._archive_store_authentication_key(root_key),
            canonical_json(unsigned).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _verify_store_payload(self, payload: Mapping[str, object]) -> None:
        key_id = payload.get("storeAuthKeyId")
        tag = payload.get("storeAuthTag")
        if (
            not isinstance(key_id, str)
            or _ARCHIVE_KEY_ID.fullmatch(key_id) is None
            or not isinstance(tag, str)
            or _ARCHIVE_AUTH_TAG.fullmatch(tag) is None
        ):
            raise JournalArchiveError("archive_storage_unavailable")
        root_key = self._key_for(key_id)
        if root_key is None:
            raise JournalArchiveError("archive_storage_unavailable")
        unsigned = dict(payload)
        unsigned.pop("storeAuthTag", None)
        expected = hmac.new(
            self._archive_store_authentication_key(root_key),
            canonical_json(unsigned).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not secrets.compare_digest(expected, tag):
            raise JournalArchiveError("archive_storage_unavailable")

    def _verify_record(
        self, archive_id: str, record: Mapping[str, object]
    ) -> None:
        root_key = self._key_for(record.get("recordAuthKeyId"))
        if root_key is None:
            raise JournalArchiveError("archive_storage_unavailable")
        expected = hmac.new(
            self._archive_authentication_key(root_key),
            self._record_auth_payload(archive_id, record),
            hashlib.sha256,
        ).hexdigest()
        if not secrets.compare_digest(expected, str(record.get("recordAuthTag"))):
            raise JournalArchiveError("archive_storage_unavailable")
        if not record["consumed"]:
            self._decrypt_token(archive_id, record)

    @staticmethod
    def _archive_encryption_key(root_key: bytes) -> bytes:
        """Derive a domain-separated AEAD key from the external HA keyring."""

        return HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=b"hausman-hub/operation-journal/archive-token/v1",
        ).derive(root_key)

    @staticmethod
    def _archive_authentication_key(root_key: bytes) -> bytes:
        """Derive a separate record-authentication key from the keyring."""

        return HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=b"hausman-hub/operation-journal/archive-record/v1",
        ).derive(root_key)

    @staticmethod
    def _archive_store_authentication_key(root_key: bytes) -> bytes:
        """Derive a third key for the archive Store transaction envelope."""

        return HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=b"hausman-hub/operation-journal/archive-store/v1",
        ).derive(root_key)

    async def async_archive(self, admin_id: str, correlation_id: str | None = None) -> dict[str, object]:
        async with self._lock:
            self._ensure_available()
            if correlation_id is not None and (
                not isinstance(correlation_id, str)
                or _CORRELATION_ID.fullmatch(correlation_id) is None
            ):
                raise JournalArchiveError("invalid_request")
            now = max(0, self._now_ms())
            state = self._state()
            self._active_key()
            self._expire(now)
            self._consume_rate("archive", admin_id, now)
            snapshot = copy.deepcopy(self.journal.snapshot(limit=MAX_OPERATION_JOURNAL_RECORDS))
            canonical = canonical_json(snapshot).encode("utf-8")
            if len(canonical) > 1_048_576:
                self._restore(state)
                raise JournalArchiveError("reset_rate_limited", retry_after_seconds=60)
            cas = (snapshot["generation"], snapshot["sequence"], snapshot["revision"])
            previous_id = self._pending.get(admin_id)
            prior = self._archives.get(previous_id or "")
            if (
                prior
                and not prior["consumed"]
                and (
                    prior["generation"], prior["sequence"], prior["revision"]
                ) == cas
            ):
                self._verify_record(str(previous_id), prior)
                token = self._decrypt_token(str(previous_id), prior)
                await self._save_or_rollback(state)
                return self._archive_receipt(str(previous_id), prior, token)
            active = sum(
                1
                for archive_id in self._pending.values()
                if not self._archives.get(archive_id, {}).get("consumed")
            )
            if active >= MAX_PENDING_ARCHIVES_GLOBAL and previous_id is None:
                self._restore(state)
                raise JournalArchiveError("reset_rate_limited", retry_after_seconds=60)
            if not self._make_room(len(canonical), now, int(snapshot["generation"])):
                self._restore(state)
                raise JournalArchiveError("reset_rate_limited", retry_after_seconds=60)
            archive_id = f"journal.{secrets.token_urlsafe(24)}"
            token = secrets.token_urlsafe(32)
            digest = hashlib.sha256(canonical).hexdigest()
            record: dict[str, object] = {
                "snapshot": snapshot,
                "tokenDigest": hashlib.sha256(token.encode()).hexdigest(),
                "tokenKeyId": "",
                "tokenNonce": "",
                "tokenCiphertext": "",
                "generation": cas[0],
                "sequence": cas[1],
                "revision": cas[2],
                "archiveSha256": digest,
                "expiresAt": now + TOKEN_TTL_MS,
                "issuedAt": now,
                "consumed": False,
                "completedAt": now,
                "correlationId": correlation_id,
                "ownerId": admin_id,
                "tokenState": "pending",
                "resetTransactionId": None,
                "resetRatePoint": None,
                "recordAuthKeyId": "",
                "recordAuthTag": "",
            }
            self._seal_token(archive_id, record, token)
            self._sign_record(archive_id, record)
            if previous_id and previous_id in self._archives:
                previous = self._archives[previous_id]
                self._verify_record(previous_id, previous)
                self._mark_record_consumed(previous)
                self._used.add(str(previous["tokenDigest"]))
            self._archives[archive_id] = record
            self._pending[admin_id] = archive_id
            self._normalize_references()
            await self._save_or_rollback(state)
            return self._archive_receipt(archive_id, record, token)

    def _archive_receipt(self, archive_id: str, record: Mapping[str, object], token: str) -> dict[str, object]:
        snapshot = copy.deepcopy(record["snapshot"])
        return {
            "contract": {"name": "hausman-hub-operation-journal-archive-receipt", "version": 1},
            **({"correlationId": record["correlationId"]} if record.get("correlationId") else {}),
            "accepted": True,
            "confirmed": True,
            "status": "confirmed",
            "result": "archived",
            "archiveToken": token,
            "archivedSnapshotId": archive_id,
            "archiveSha256": record["archiveSha256"],
            "archiveAlgorithm": "RFC8785-SHA256",
            "archivedRecords": len(snapshot["records"]),
            "archiveStored": True,
            "immutable": True,
            "singleUse": True,
            "issuedAt": record["issuedAt"],
            "expiresAt": record["expiresAt"],
            "snapshot": snapshot,
            "physicalCommandsSent": False,
        }

    async def async_reset(
        self,
        admin_id: str,
        token: str,
        expected_generation: int,
        expected_sequence: int,
        expected_revision: str,
        correlation_id: str | None = None,
    ) -> dict[str, object]:
        async with self._lock:
            self._ensure_available()
            if correlation_id is not None and (
                not isinstance(correlation_id, str)
                or _CORRELATION_ID.fullmatch(correlation_id) is None
            ):
                raise JournalArchiveError("invalid_request")
            now = max(0, self._now_ms())
            state = self._state()
            self._active_key()
            self._consume_rate("reset", admin_id, now)
            if not isinstance(token, str) or _ARCHIVE_TOKEN.fullmatch(token) is None:
                await self._save_or_rollback(state)
                raise JournalArchiveError("reset_archive_token_invalid")
            self._expire(now)
            archive_id = self._pending.get(admin_id)
            archive = self._archives.get(archive_id or "")
            digest = hashlib.sha256(token.encode()).hexdigest()
            if (
                archive is None
                or digest in self._used
                or archive["consumed"]
                or now >= int(archive["expiresAt"])
                or archive.get("ownerId") != admin_id
                or archive.get("tokenState") != "pending"
            ):
                await self._save_or_rollback(state)
                raise JournalArchiveError("reset_archive_token_invalid")
            self._verify_record(str(archive_id), archive)
            stored_token = self._decrypt_token(str(archive_id), archive)
            if not secrets.compare_digest(stored_token, token):
                await self._save_or_rollback(state)
                raise JournalArchiveError("reset_archive_token_invalid")
            expected = (expected_generation, expected_sequence, expected_revision)
            archived = (archive["generation"], archive["sequence"], archive["revision"])
            snapshot = archive["snapshot"]
            snapshot_valid = _archive_snapshot_size(archive) is not None
            if expected != archived or not snapshot_valid:
                await self._save_or_rollback(state)
                current = self.journal.snapshot(limit=MAX_OPERATION_JOURNAL_RECORDS)
                raise JournalArchiveError(
                    "reset_precondition_conflict",
                    details={
                        "expectedGeneration": expected_generation,
                        "expectedSequence": expected_sequence,
                        "expectedRevision": expected_revision,
                        "actualGeneration": current["generation"],
                        "actualSequence": current["sequence"],
                        "actualRevision": current["revision"],
                    },
                )
            transaction_id = secrets.token_urlsafe(24)
            archive["tokenState"] = "reset_prepared"
            archive["resetTransactionId"] = transaction_id
            archive["resetRatePoint"] = now
            self._seal_token(str(archive_id), archive, stored_token)
            self._sign_record(str(archive_id), archive)
            self._reset_transaction = {
                "id": transaction_id,
                "archiveId": archive_id,
                "ownerId": admin_id,
                "phase": "prepared",
                "expectedGeneration": expected_generation,
                "expectedSequence": expected_sequence,
                "expectedRevision": expected_revision,
            }
            await self._save_or_rollback(state)
            try:
                result = await self.journal.async_reset_if_cas(
                    expected_generation,
                    expected_sequence,
                    expected_revision,
                )
            except Exception as error:
                # The journal Store may raise after its atomic replacement is
                # already durable. Keep the prepared marker intact: a fresh
                # instance can compare the durable journal generation and
                # deterministically roll the token back or consume it.
                self._available = False
                raise JournalArchiveError("archive_storage_unavailable") from error
            if result is None:
                self._restore(state)
                try:
                    await self._save()
                except Exception as error:
                    self._available = False
                    raise JournalArchiveError("archive_storage_unavailable") from error
                current = self.journal.snapshot(limit=MAX_OPERATION_JOURNAL_RECORDS)
                raise JournalArchiveError(
                    "reset_precondition_conflict",
                    details={
                        "expectedGeneration": expected_generation,
                        "expectedSequence": expected_sequence,
                        "expectedRevision": expected_revision,
                        "actualGeneration": current["generation"],
                        "actualSequence": current["sequence"],
                        "actualRevision": current["revision"],
                    },
                )
            archive["tokenState"] = "journal_committed"
            self._seal_token(str(archive_id), archive, stored_token)
            self._sign_record(str(archive_id), archive)
            self._reset_transaction["phase"] = "journal_committed"
            try:
                await self._save()
            except Exception as error:
                self._available = False
                raise JournalArchiveError("archive_storage_unavailable") from error
            self._mark_record_consumed(archive)
            self._used.add(digest)
            self._pending.pop(admin_id, None)
            self._reset_transaction = None
            try:
                await self._save()
            except Exception as error:
                self._available = False
                raise JournalArchiveError("archive_storage_unavailable") from error
        previous = result["previous"]
        resulting = result["resulting"]
        return {
            "contract": {"name": "hausman-hub-operation-journal-reset-receipt", "version": 1},
            **({"correlationId": correlation_id} if correlation_id else {}),
            "accepted": True,
            "confirmed": True,
            "status": "confirmed",
            "result": "reset",
            "archivedSnapshotId": archive_id,
            "archiveSha256": archive["archiveSha256"],
            "archivedRecords": len(archive["snapshot"]["records"]),
            "archiveVerified": True,
            "archiveTokenConsumed": True,
            "expectedGeneration": expected_generation,
            "expectedSequence": expected_sequence,
            "expectedRevision": expected_revision,
            "previousGeneration": previous["generation"],
            "previousSequence": previous["sequence"],
            "previousRevision": previous["revision"],
            "resultingGeneration": resulting["generation"],
            "resultingSequence": resulting["sequence"],
            "resultingRevision": resulting["revision"],
            "recordsRemoved": len(previous["records"]),
            "physicalCommandsSent": False,
            "message": "Журнал сброшен после сохранения неизменяемого архива.",
        }


def _valid_archive_record(value: Mapping[str, object]) -> bool:
    """Accept only the bounded authenticated v3 archive record shape."""

    snapshot = value.get("snapshot")
    shape_valid = (
        set(value)
        == {
            "snapshot", "tokenDigest", "tokenKeyId", "tokenNonce", "tokenCiphertext",
            "generation", "sequence", "revision", "archiveSha256", "expiresAt",
            "issuedAt", "consumed", "completedAt", "correlationId", "ownerId",
            "tokenState", "resetTransactionId", "resetRatePoint", "recordAuthKeyId",
            "recordAuthTag",
        }
        and isinstance(snapshot, Mapping)
        and isinstance(snapshot.get("records"), list)
        and isinstance(value.get("tokenDigest"), str)
        and _HEX_DIGEST.fullmatch(str(value.get("tokenDigest"))) is not None
        and isinstance(value.get("tokenKeyId"), str)
        and isinstance(value.get("tokenNonce"), str)
        and isinstance(value.get("tokenCiphertext"), str)
        and isinstance(value.get("recordAuthKeyId"), str)
        and _ARCHIVE_KEY_ID.fullmatch(str(value.get("recordAuthKeyId"))) is not None
        and isinstance(value.get("recordAuthTag"), str)
        and _ARCHIVE_AUTH_TAG.fullmatch(str(value.get("recordAuthTag"))) is not None
        and isinstance(value.get("archiveSha256"), str)
        and _HEX_DIGEST.fullmatch(str(value.get("archiveSha256"))) is not None
        and type(value.get("generation")) is int
        and 1 <= int(value["generation"]) <= _MAX_SAFE_INTEGER
        and type(value.get("sequence")) is int
        and 0 <= int(value["sequence"]) <= _MAX_SAFE_INTEGER
        and isinstance(value.get("revision"), str)
        and _REVISION.fullmatch(str(value.get("revision"))) is not None
        and type(value.get("expiresAt")) is int
        and 0 <= int(value["expiresAt"]) <= _MAX_SAFE_INTEGER
        and type(value.get("issuedAt")) is int
        and 0 <= int(value["issuedAt"]) <= int(value["expiresAt"])
        and int(value["expiresAt"]) - int(value["issuedAt"]) <= TOKEN_TTL_MS
        and type(value.get("consumed")) is bool
        and isinstance(value.get("ownerId"), str)
        and 0 < len(str(value.get("ownerId"))) <= _MAX_ADMIN_ID_LENGTH
        and value.get("tokenState") in _TOKEN_STATES
        and (
            value.get("completedAt") is None
            or (
                type(value.get("completedAt")) is int
                and int(value["issuedAt"]) <= int(value["completedAt"]) <= _MAX_SAFE_INTEGER
            )
        )
        and (
            (
                value.get("consumed") is True
                and value.get("tokenState") == "consumed"
                and value.get("resetTransactionId") is None
                and value.get("resetRatePoint") is None
                and value.get("tokenKeyId") == ""
                and value.get("tokenNonce") == ""
                and value.get("tokenCiphertext") == ""
            )
            or (
                value.get("consumed") is False
                and (
                    (
                        value.get("tokenState") == "pending"
                        and value.get("resetTransactionId") is None
                        and value.get("resetRatePoint") is None
                    )
                    or (
                        value.get("tokenState") in {
                            "reset_prepared", "journal_committed"
                        }
                        and isinstance(value.get("resetTransactionId"), str)
                        and _RESET_TRANSACTION_ID.fullmatch(
                            str(value.get("resetTransactionId"))
                        ) is not None
                        and type(value.get("resetRatePoint")) is int
                        and 0 <= int(value["resetRatePoint"]) <= _MAX_SAFE_INTEGER
                    )
                )
                and _ARCHIVE_KEY_ID.fullmatch(str(value.get("tokenKeyId"))) is not None
                and _ARCHIVE_NONCE.fullmatch(str(value.get("tokenNonce"))) is not None
                and _ARCHIVE_CIPHERTEXT.fullmatch(str(value.get("tokenCiphertext"))) is not None
            )
        )
        and (
            value.get("correlationId") is None
            or (
                isinstance(value.get("correlationId"), str)
                and _CORRELATION_ID.fullmatch(str(value.get("correlationId"))) is not None
            )
        )
    )
    return shape_valid and _archive_snapshot_size(value) is not None


def _archive_snapshot_size(value: Mapping[str, object]) -> int | None:
    snapshot = value.get("snapshot")
    if not isinstance(snapshot, Mapping):
        return None
    records = snapshot.get("records")
    page = snapshot.get("page")
    if (
        not isinstance(records, list)
        or len(records) > MAX_OPERATION_JOURNAL_RECORDS
        or not isinstance(page, Mapping)
        or page.get("retained_records") != len(records)
        or page.get("returned") != len(records)
        or page.get("order") != "sequence_desc"
        or page.get("limit") != MAX_OPERATION_JOURNAL_RECORDS
        or page.get("retention_limit") != MAX_OPERATION_JOURNAL_RECORDS
        or page.get("has_more") is not False
        or page.get("next_before_sequence") is not None
        or snapshot.get("generation") != value.get("generation")
        or snapshot.get("sequence") != value.get("sequence")
        or snapshot.get("revision") != value.get("revision")
    ):
        return None
    try:
        _validate_bounded_tree(snapshot)
        encoded = canonical_json(snapshot).encode("utf-8")
    except (TypeError, ValueError):
        return None
    if len(encoded) > 1_048_576:
        return None
    expected = value.get("archiveSha256")
    if not isinstance(expected, str) or not secrets.compare_digest(
        hashlib.sha256(encoded).hexdigest(), expected
    ):
        return None
    return len(encoded)


def _validate_bounded_tree(
    value: object,
    *,
    max_nodes: int = _MAX_SNAPSHOT_TREE_NODES,
    max_bytes: int = _MAX_SNAPSHOT_PREFLIGHT_BYTES,
) -> None:
    """Reject hostile depth and fan-out before recursive canonicalization."""

    nodes = 0
    encoded_bytes = 0
    stack: list[tuple[object, int]] = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > max_nodes or depth > _MAX_TREE_DEPTH:
            raise ValueError("archive value exceeds structural bounds")
        if isinstance(item, str):
            try:
                item_bytes = json.dumps(item, ensure_ascii=False).encode("utf-8")
            except UnicodeEncodeError as error:
                raise ValueError("archive string is invalid") from error
            if len(item.encode("utf-8")) > _MAX_STRING_BYTES:
                raise ValueError("archive string exceeds structural bounds")
            encoded_bytes += len(item_bytes)
        elif isinstance(item, Mapping):
            if len(item) > _MAX_CONTAINER_ITEMS:
                raise ValueError("archive mapping exceeds structural bounds")
            encoded_bytes += 2 + max(0, len(item) - 1)
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ValueError("archive mapping key is invalid")
                try:
                    key_bytes = json.dumps(key, ensure_ascii=False).encode("utf-8")
                except UnicodeEncodeError as error:
                    raise ValueError("archive mapping key is invalid") from error
                if len(key.encode("utf-8")) > 512:
                    raise ValueError("archive mapping key exceeds structural bounds")
                encoded_bytes += len(key_bytes) + 1
                stack.append((child, depth + 1))
        elif isinstance(item, (list, tuple)):
            if len(item) > _MAX_CONTAINER_ITEMS:
                raise ValueError("archive list exceeds structural bounds")
            encoded_bytes += 2 + max(0, len(item) - 1)
            stack.extend((child, depth + 1) for child in item)
        elif item is None:
            encoded_bytes += 4
        elif type(item) is bool:
            encoded_bytes += 4 if item else 5
        elif type(item) is int:
            if abs(item) > _MAX_SAFE_INTEGER:
                raise ValueError("archive integer is outside structural bounds")
            encoded_bytes += len(str(item))
        elif type(item) is float:
            if not math.isfinite(item):
                raise ValueError("archive number is outside structural bounds")
            encoded_bytes += 24
        else:
            raise ValueError("archive value has an unsupported type")
        if encoded_bytes > max_bytes:
            raise ValueError("archive value exceeds encoded size bounds")


def _valid_reset_transaction(value: Mapping[str, object]) -> bool:
    return (
        set(value)
        == {
            "id", "archiveId", "ownerId", "phase", "expectedGeneration",
            "expectedSequence", "expectedRevision",
        }
        and isinstance(value.get("id"), str)
        and _RESET_TRANSACTION_ID.fullmatch(str(value.get("id"))) is not None
        and isinstance(value.get("archiveId"), str)
        and _ARCHIVE_ID.fullmatch(str(value.get("archiveId"))) is not None
        and isinstance(value.get("ownerId"), str)
        and 0 < len(str(value.get("ownerId"))) <= _MAX_ADMIN_ID_LENGTH
        and value.get("phase") in {"prepared", "journal_committed"}
        and type(value.get("expectedGeneration")) is int
        and 1 <= int(value["expectedGeneration"]) <= _MAX_SAFE_INTEGER
        and type(value.get("expectedSequence")) is int
        and 0 <= int(value["expectedSequence"]) <= _MAX_SAFE_INTEGER
        and isinstance(value.get("expectedRevision"), str)
        and _REVISION.fullmatch(str(value.get("expectedRevision"))) is not None
    )
