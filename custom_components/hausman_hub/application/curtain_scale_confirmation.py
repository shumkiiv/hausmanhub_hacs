"""Durable server authority for the observed office curtain scale."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from .curtain_command_policy import OFFICE_CURTAIN_TARGET


OFFICE_CURTAIN_ENTITY_ID = "cover.0xa4c1381b3fb1c985"
_CONFIRMATION_SOURCE = "owner_observed_current_scale"
_PAYLOAD_FIELDS = {
    "version", "revision", "entryId", "targetId", "entityId",
    "entityUniqueId", "deviceId", "deviceIdentifiers", "confirmedAt",
    "confirmationSource", "state",
}


class CurtainScaleConfirmationConflict(RuntimeError):
    """A stale administrative form attempted to change scale authority."""


@dataclass(frozen=True, slots=True)
class CurtainScaleIdentity:
    """Exact registry-backed physical identity for the office curtain."""

    target_id: str
    entity_id: str
    entity_unique_id: str
    device_id: str
    device_identifiers: tuple[tuple[str, str], ...]
    identity_digest: str

    @classmethod
    def create(
        cls,
        *,
        target_id: object,
        entity_id: object,
        entity_unique_id: object,
        device_id: object,
        device_identifiers: object,
    ) -> CurtainScaleIdentity:
        if target_id != OFFICE_CURTAIN_TARGET or entity_id != OFFICE_CURTAIN_ENTITY_ID:
            raise ValueError("only the exact office curtain may be confirmed")
        if any(
            not isinstance(value, str) or not value or len(value) > 256
            for value in (entity_unique_id, device_id)
        ):
            raise ValueError("office curtain registry identity is invalid")
        if (
            isinstance(device_identifiers, (str, bytes, Mapping))
            or not isinstance(device_identifiers, Iterable)
        ):
            raise ValueError("a physical device identifier is required")
        normalized: set[tuple[str, str]] = set()
        for item in device_identifiers:
            if (
                isinstance(item, (str, bytes))
                or not isinstance(item, Sequence)
                or len(item) != 2
                or any(
                    not isinstance(value, str) or not value or len(value) > 256
                    for value in item
                )
            ):
                raise ValueError("a physical device identifier is invalid")
            normalized.add((item[0], item[1]))
        if not normalized:
            raise ValueError("a physical device identifier is required")
        identifiers = tuple(sorted(normalized))
        digest = hashlib.sha256(
            json.dumps(
                [target_id, entity_id, entity_unique_id, device_id, identifiers],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return cls(
            target_id=target_id,
            entity_id=entity_id,
            entity_unique_id=entity_unique_id,
            device_id=device_id,
            device_identifiers=identifiers,
            identity_digest=digest,
        )


@dataclass(frozen=True, slots=True)
class CurtainScaleAuthorization:
    """Dynamic authority snapshot consumed by curtain command policy."""

    confirmed: bool
    revision: int
    identity_digest: str | None


@dataclass(frozen=True, slots=True)
class CurtainScaleConfirmationView:
    """Server-owned form values; none are accepted back from user input."""

    authorization: CurtainScaleAuthorization
    identity: CurtainScaleIdentity


def valid_curtain_scale_confirmation_payload(value: object) -> bool:
    """Accept only one complete schema-1 office confirmation record."""

    if not isinstance(value, Mapping) or set(value) != _PAYLOAD_FIELDS:
        return False
    if (
        value.get("version") != 1
        or type(value.get("revision")) is not int
        or not 1 <= value["revision"] < 2**31
        or value.get("targetId") != OFFICE_CURTAIN_TARGET
        or value.get("entityId") != OFFICE_CURTAIN_ENTITY_ID
        or value.get("confirmationSource") != _CONFIRMATION_SOURCE
        or value.get("state") not in {"confirmed", "revoked"}
        or type(value.get("confirmedAt")) is not int
        or not 0 <= value["confirmedAt"] < 2**63
    ):
        return False
    for key in ("entryId", "entityUniqueId", "deviceId"):
        item = value.get(key)
        if not isinstance(item, str) or not item or len(item) > 256:
            return False
    identifiers = value.get("deviceIdentifiers")
    if not isinstance(identifiers, list) or not identifiers:
        return False
    try:
        identity = CurtainScaleIdentity.create(
            target_id=value["targetId"],
            entity_id=value["entityId"],
            entity_unique_id=value["entityUniqueId"],
            device_id=value["deviceId"],
            device_identifiers=identifiers,
        )
    except (TypeError, ValueError):
        return False
    return [list(item) for item in identity.device_identifiers] == identifiers


class CurtainScaleConfirmation:
    """Persist confirmation before publishing dynamic command authority."""

    def __init__(
        self,
        store: object,
        *,
        entry_id: str,
        identity_resolver: Callable[[str], CurtainScaleIdentity | None],
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        if not isinstance(entry_id, str) or not entry_id:
            raise ValueError("curtain scale entry id is invalid")
        self._store = store
        self._entry_id = entry_id
        self._identity_resolver = identity_resolver
        self._now_ms = now_ms or (lambda: time.time_ns() // 1_000_000)
        self._record: dict[str, object] | None = None
        self._healthy = True
        self._identity_invalidated = False
        self._lock = asyncio.Lock()

    @property
    def healthy(self) -> bool:
        return self._healthy

    @property
    def revision(self) -> int:
        return int(self._record["revision"]) if self._record is not None else 0

    async def async_load(self) -> None:
        """Treat unreadable, rolled-back, or foreign state as no authority."""

        try:
            payload = await self._store.async_load()
        except Exception:  # noqa: BLE001
            self._healthy = False
            self._record = None
            return
        if payload is None:
            self._record = None
            return
        if (
            not valid_curtain_scale_confirmation_payload(payload)
            or payload.get("entryId") != self._entry_id
        ):
            self._healthy = False
            self._record = None
            return
        self._record = dict(payload)
        if getattr(self._store, "recovered_previous", False):
            self._healthy = False

    async def async_confirm_office(
        self, expected_revision: int, expected_identity_digest: str
    ) -> CurtainScaleAuthorization:
        """Confirm only the exact current identity from a fresh admin form."""

        if type(expected_revision) is not int:
            raise CurtainScaleConfirmationConflict("scale confirmation revision is invalid")
        if not isinstance(expected_identity_digest, str) or not expected_identity_digest:
            raise CurtainScaleConfirmationConflict("scale confirmation identity is invalid")
        async with self._lock:
            if not self._healthy:
                raise RuntimeError("curtain scale confirmation store is unavailable")
            if expected_revision != self.revision:
                raise CurtainScaleConfirmationConflict("scale confirmation revision changed")
            identity = self._resolve_office_identity()
            if identity.identity_digest != expected_identity_digest:
                raise CurtainScaleConfirmationConflict("scale confirmation identity changed")
            if self.revision >= 2**31 - 1:
                raise CurtainScaleConfirmationConflict("scale confirmation revision exhausted")
            updated = self._payload(
                identity, revision=self.revision + 1, state="confirmed",
                confirmed_at=self._valid_now_ms(),
            )
            try:
                await self._store.async_save(updated)
            except Exception:
                self._healthy = False
                raise
            self._record = updated
            self._identity_invalidated = False
            return self.authorization_snapshot(OFFICE_CURTAIN_TARGET)

    async def async_revoke(
        self, expected_revision: int
    ) -> CurtainScaleAuthorization:
        """Persist a monotonic revocation without sending a cover command."""

        if type(expected_revision) is not int:
            raise CurtainScaleConfirmationConflict("scale confirmation revision is invalid")
        async with self._lock:
            if not self._healthy:
                raise RuntimeError("curtain scale confirmation store is unavailable")
            if expected_revision != self.revision:
                raise CurtainScaleConfirmationConflict("scale confirmation revision changed")
            if self.revision >= 2**31 - 1:
                raise CurtainScaleConfirmationConflict("scale confirmation revision exhausted")
            identity = self._stored_identity() or self._resolve_office_identity()
            confirmed_at = (
                int(self._record["confirmedAt"])
                if self._record is not None else self._valid_now_ms()
            )
            updated = self._payload(
                identity, revision=self.revision + 1, state="revoked",
                confirmed_at=confirmed_at,
            )
            try:
                await self._store.async_save(updated)
            except Exception:
                self._healthy = False
                raise
            self._record = updated
            self._identity_invalidated = False
            return self.authorization_snapshot(OFFICE_CURTAIN_TARGET)

    def authorization_snapshot(self, target_id: str) -> CurtainScaleAuthorization:
        """Re-read physical identity each time policy snapshots authority."""

        if target_id != OFFICE_CURTAIN_TARGET:
            return CurtainScaleAuthorization(False, self.revision, None)
        current = self._try_resolve_office_identity()
        digest = current.identity_digest if current is not None else None
        stored = self._stored_identity()
        if (
            self._record is not None
            and self._record.get("state") == "confirmed"
            and stored is not None
            and (current is None or current.identity_digest != stored.identity_digest)
        ):
            self._identity_invalidated = True
        confirmed = bool(
            self._healthy and not self._identity_invalidated
            and self._record is not None and self._record.get("state") == "confirmed"
            and stored is not None and current is not None
            and current.identity_digest == stored.identity_digest
        )
        return CurtainScaleAuthorization(confirmed, self.revision, digest)

    def office_confirmation_view(self) -> CurtainScaleConfirmationView:
        identity = self._resolve_office_identity()
        return CurtainScaleConfirmationView(
            authorization=self.authorization_snapshot(OFFICE_CURTAIN_TARGET),
            identity=identity,
        )

    def _try_resolve_office_identity(self) -> CurtainScaleIdentity | None:
        try:
            identity = self._identity_resolver(OFFICE_CURTAIN_TARGET)
        except Exception:  # noqa: BLE001
            return None
        return identity if isinstance(identity, CurtainScaleIdentity) else None

    def _resolve_office_identity(self) -> CurtainScaleIdentity:
        identity = self._try_resolve_office_identity()
        if identity is None:
            raise RuntimeError("office curtain physical identity is unavailable")
        return identity

    def _stored_identity(self) -> CurtainScaleIdentity | None:
        if self._record is None:
            return None
        try:
            return CurtainScaleIdentity.create(
                target_id=self._record["targetId"], entity_id=self._record["entityId"],
                entity_unique_id=self._record["entityUniqueId"], device_id=self._record["deviceId"],
                device_identifiers=self._record["deviceIdentifiers"],
            )
        except (KeyError, TypeError, ValueError):
            return None

    def _payload(
        self, identity: CurtainScaleIdentity, *, revision: int,
        state: str, confirmed_at: int,
    ) -> dict[str, object]:
        return {
            "version": 1, "revision": revision, "entryId": self._entry_id,
            "targetId": identity.target_id, "entityId": identity.entity_id,
            "entityUniqueId": identity.entity_unique_id, "deviceId": identity.device_id,
            "deviceIdentifiers": [list(item) for item in identity.device_identifiers],
            "confirmedAt": confirmed_at, "confirmationSource": _CONFIRMATION_SOURCE,
            "state": state,
        }

    def _valid_now_ms(self) -> int:
        value = self._now_ms()
        if type(value) is not int or not 0 <= value < 2**63:
            raise RuntimeError("curtain scale confirmation clock is invalid")
        return value
