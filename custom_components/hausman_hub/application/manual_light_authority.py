"""Durable, deny-only authority for externally manual light changes."""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol


class ManualLightAuthorityStore(Protocol):
    async def async_load(self) -> object | None: ...
    async def async_save(self, payload: dict[str, object]) -> None: ...
    @property
    def recovered_previous(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class ManualLightRecord:
    entity_id: str
    action: str
    source: str
    event_id: str
    generation: int


class ManualLightAuthority:
    """Store immutable manual records; uncertainty always denies automation."""

    def __init__(self, store: ManualLightAuthorityStore | None = None) -> None:
        self._store = store
        self._records: dict[str, ManualLightRecord] = {}
        self._event_ids: set[str] = set()
        self._generation = 0
        self._pending = False
        self._failed = False
        self._recovery = False
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        if self._store is None:
            return
        payload = await self._store.async_load()
        self._recovery = bool(self._store.recovered_previous)
        if payload is None:
            return
        if not isinstance(payload, Mapping) or payload.get("version") != 1:
            self._failed = True
            return
        records = payload.get("records")
        if not isinstance(records, list) or len(records) > 256:
            self._failed = True
            return
        for raw in records:
            if not isinstance(raw, Mapping):
                self._failed = True; return
            try:
                record = ManualLightRecord(
                    str(raw["entityId"]), str(raw["action"]), str(raw["source"]),
                    str(raw["eventId"]), int(raw["generation"]),
                )
            except (KeyError, TypeError, ValueError):
                self._failed = True; return
            if not record.entity_id.startswith("light.") or record.action not in {"on", "off"}:
                self._failed = True; return
            self._records[record.entity_id] = record
            self._event_ids.add(record.event_id)
            self._generation = max(self._generation, record.generation)

    def blocks(self, entity_id: str) -> bool:
        return self._pending or self._failed or self._recovery or entity_id in self._records

    async def async_capture(self, entity_id: str, action: str, source: str, event_id: str) -> bool:
        """Synchronously latch before await; a duplicate never extends authority."""
        if not entity_id.startswith("light.") or action not in {"on", "off"} or not event_id:
            raise ValueError("invalid manual light authority record")
        async with self._lock:
            if event_id in self._event_ids:
                return False
            self._pending = True
            generation = self._generation + 1
            next_records = dict(self._records)
            next_records[entity_id] = ManualLightRecord(entity_id, action, source, event_id, generation)
            payload = {"version": 1, "records": [
                {"entityId": r.entity_id, "action": r.action, "source": r.source,
                 "eventId": r.event_id, "generation": r.generation}
                for r in sorted(next_records.values(), key=lambda item: item.entity_id)
            ]}
            try:
                if self._store is not None:
                    await self._store.async_save(copy.deepcopy(payload))
            except Exception:  # noqa: BLE001
                self._failed = True
                raise
            finally:
                self._pending = False
            self._records = next_records
            self._event_ids.add(event_id)
            self._generation = generation
            return True
