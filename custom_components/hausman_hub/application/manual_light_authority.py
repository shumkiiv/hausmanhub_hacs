"""Entity keyed, deny-only evidence of manual light actions."""

from __future__ import annotations

import asyncio
import copy
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol
from .related_light_sources import RelatedLightSources


DEFAULT_MANUAL_PRIORITY_MS = 3_600_000
MAX_RECORDS = 256
MAX_RECENT_FINGERPRINTS = 512
MANAGED_LIGHT_DOMAINS = ("light.", "switch.")


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
    fingerprint: str
    observed_at_ms: int
    deadline_ms: int
    generation: int
    legacy: bool = False


class ManualLightAuthority:
    """One shared deny for scenario, room and upstream light power commands."""

    def __init__(self, store: ManualLightAuthorityStore | None = None) -> None:
        self._store = store
        self._records: dict[str, ManualLightRecord] = {}
        self._pending: dict[str, ManualLightRecord] = {}
        self._generation = 0
        self._recent: dict[str, int] = {}
        self._ready = store is None
        self._storage_failed = False
        self._dispatch_failed = False
        self._recovery = False
        # An event can be lost if the process dies before its async save.
        # A loaded file alone therefore cannot prove a clean observation gap.
        self._restart_uncertain = False
        self._startup_observation_pending = False
        self._seeded_empty_store = False
        self._writer = asyncio.Lock()
        self._related_sources = RelatedLightSources(True, {})
        self._related_sources_invalidated = False
        # Service selectors (area/device/label/all) cannot be expanded safely
        # at this event boundary.  Keep this evidence by HA service domain;
        # it is monotonic for the running authority and is never inferred away
        # by a later state transition.
        self._selector_uncertain_domains: set[str] = set()

    def set_related_sources(self, graph: RelatedLightSources) -> None:
        """Atomically replace the only relation graph; an invalid graph denies all."""
        if not self._related_sources_invalidated:
            self._related_sources = graph

    def invalidate_related_sources(self) -> None:
        self._related_sources_invalidated = True
        self._related_sources = RelatedLightSources(False, {})

    def mark_selector_uncertain(self, domain: str) -> None:
        """Deny only graph components that could have matched a selector."""
        if domain not in {"light", "switch"}:
            raise ValueError("manual light selector domain is invalid")
        self._selector_uncertain_domains.add(domain)

    @property
    def seeded_empty_store(self) -> bool:
        return self._seeded_empty_store

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def unhealthy(self) -> bool:
        return (
            not self._ready
            or self._storage_failed
            or self._dispatch_failed
            or self._recovery
            or self._restart_uncertain
            or self._startup_observation_pending
        )

    @property
    def persistence_ready(self) -> bool:
        """A loaded durable store can accept evidence despite dispatch barriers."""
        return self._store is not None and self._ready and not self._storage_failed

    @property
    def barrier_reason(self) -> str | None:
        """Expose the global deny cause without promising an expiry."""
        if not self._ready or self._storage_failed or self._dispatch_failed or self._recovery:
            return "storage_unavailable"
        if self._restart_uncertain:
            return "restart_uncertain"
        if self._startup_observation_pending:
            return "restart_uncertain"
        return None

    def barrier_reason_for(self, entity_id: str) -> str | None:
        global_reason = self.barrier_reason
        if global_reason is not None:
            return global_reason
        if not self._related_sources.ready:
            return "attribution_uncertain"
        if any(
            related.startswith(f"{domain}.")
            for related in self._related_sources.related(entity_id)
            for domain in self._selector_uncertain_domains
        ):
            return "selector_uncertain"
        return None

    def begin_startup_observation(self) -> None:
        """Close dispatch until the full room entity set has been restored."""
        self._startup_observation_pending = True

    def finish_startup_observation(self) -> None:
        """Open dispatch only after buffered evidence was verified."""
        self._startup_observation_pending = False

    def record(self, entity_id: str) -> ManualLightRecord | None:
        return self._pending.get(entity_id) or self._records.get(entity_id)

    def has_durable_import(self, entity_ids: Iterable[str]) -> bool:
        """Whether every imported entity is persisted, regardless of dispatch."""
        return (
            self.persistence_ready
            and not self._pending
            and all(entity_id in self._records for entity_id in entity_ids)
        )

    def blocks(self, entity_id: str) -> bool:
        # First slice: the clock never releases a manual choice. A later
        # migration must prove every other condition before using deadline.
        if self.barrier_reason_for(entity_id) is not None:
            return True
        return any(
            related in self._pending or related in self._records
            for related in self._related_sources.related(entity_id)
        )

    def blocking_evidence(self, entity_id: str) -> ManualLightRecord | None:
        """Return linked evidence without copying it to a sibling entity."""
        own = self.record(entity_id)
        if own is not None:
            return own
        records = [
            record for related in self._related_sources.related(entity_id)
            if (record := self.record(related)) is not None
        ]
        return max(records, key=lambda record: (record.generation, record.entity_id), default=None)

    def mark_unhealthy(self) -> None:
        """Close every automatic light route after ambiguous attribution."""
        self._dispatch_failed = True

    async def async_load(self) -> None:
        if self._store is None:
            self._ready = True
            return
        try:
            payload = await self._store.async_load()
            self._recovery = bool(self._store.recovered_previous)
            if payload is None:
                # A first installation cannot have accepted an event yet:
                # subscriptions are attached only after this verified seed.
                await self._store.async_save(
                    {"version": 2, "records": [], "recentFingerprints": []}
                )
                self._seeded_empty_store = True
            else:
                self._restart_uncertain = True
            loaded: dict[str, ManualLightRecord] = {}
            if payload is not None:
                if not isinstance(payload, Mapping) or payload.get("version") != 2:
                    raise ValueError("manual light authority version is invalid")
                raw_records = payload.get("records")
                if not isinstance(raw_records, list) or len(raw_records) > MAX_RECORDS:
                    raise ValueError("manual light authority records are invalid")
                recent = payload.get("recentFingerprints", [])
                if (
                    not isinstance(recent, list)
                    or len(recent) > MAX_RECENT_FINGERPRINTS
                    or any(not isinstance(item, str) or not item for item in recent)
                    or len(set(recent)) != len(recent)
                ):
                    raise ValueError("manual light fingerprints are invalid")
                self._recent = {fingerprint: 0 for fingerprint in recent}
                for raw in raw_records:
                    record = _parse_record(raw)
                    if record.entity_id in loaded:
                        raise ValueError("duplicate manual light entity")
                    loaded[record.entity_id] = record
            # A capture during load remains pending; startup is denied until
            # loading completes.
            self._records = loaded
            self._generation = max(
                [self._generation, *(r.generation for r in loaded.values())]
            )
            self._ready = True
        except Exception:
            self._storage_failed = True
            raise

    def capture(
        self, entity_id: str, action: str, source: str, fingerprint: str,
        *, observed_at_ms: int | None = None,
        duration_ms: int = DEFAULT_MANUAL_PRIORITY_MS,
        legacy: bool = False,
    ) -> bool:
        """Latch synchronously; caller schedules ``async_drain`` afterwards."""
        if not isinstance(entity_id, str) or not entity_id.startswith(MANAGED_LIGHT_DOMAINS):
            raise ValueError("manual light entity is invalid")
        if action not in {"on", "off", "unknown"} or not source or not fingerprint:
            raise ValueError("manual light evidence is invalid")
        if type(duration_ms) is not int or duration_ms <= 0:
            raise ValueError("manual light duration is invalid")
        previous = self.record(entity_id)
        if fingerprint in self._recent or (
            previous is not None and previous.fingerprint == fingerprint
        ):
            return False
        if entity_id not in self._records and entity_id not in self._pending and (
            len(self._records) + len(self._pending) >= MAX_RECORDS
        ):
            self._storage_failed = True
            raise RuntimeError("manual light authority is full")
        observed = int(time.time() * 1000) if observed_at_ms is None else observed_at_ms
        if type(observed) is not int or observed < 0:
            raise ValueError("manual light timestamp is invalid")
        self._generation += 1
        self._recent[fingerprint] = self._generation
        while len(self._recent) > MAX_RECENT_FINGERPRINTS:
            self._recent.pop(next(iter(self._recent)))
        self._pending[entity_id] = ManualLightRecord(
            entity_id, action, source, fingerprint, observed,
            observed + duration_ms, self._generation, legacy,
        )
        return True

    async def async_capture(
        self, entity_id: str, action: str, source: str, fingerprint: str,
        *, observed_at_ms: int | None = None,
        duration_ms: int = DEFAULT_MANUAL_PRIORITY_MS,
    ) -> bool:
        changed = self.capture(entity_id, action, source, fingerprint,
                               observed_at_ms=observed_at_ms, duration_ms=duration_ms)
        if changed:
            await self.async_drain()
        return changed

    def capture_legacy(self, entity_id: str, *, source: str) -> bool:
        # The old record has no proved action time or safe expiry.
        if self.record(entity_id) is not None:
            return False
        return self.capture(entity_id, "unknown", source,
                            f"legacy:{source}:{entity_id}", legacy=True)

    async def async_capture_initial_room_scope(
        self, entity_ids: Iterable[str]
    ) -> None:
        """Hold pre-existing room lights when no earlier authority file exists."""
        if not self._seeded_empty_store:
            return
        for entity_id in entity_ids:
            self.capture_legacy(entity_id, source="startup_unobserved")
        await self.async_drain()

    async def async_drain(self) -> None:
        async with self._writer:
            while self._pending and not self._storage_failed:
                generation = self._generation
                snapshot = dict(self._records)
                snapshot.update(self._pending)
                payload = {
                    "version": 2,
                    "records": [_record_payload(r) for r in
                                sorted(snapshot.values(), key=lambda item: item.entity_id)],
                    "recentFingerprints": [
                        fingerprint for fingerprint, seen_generation in self._recent.items()
                        if seen_generation <= generation
                    ],
                }
                try:
                    if self._store is not None:
                        await self._store.async_save(copy.deepcopy(payload))
                except Exception:
                    self._storage_failed = True
                    raise
                self._records = snapshot
                # A later event can arrive during save. Only the verified
                # generation may be drained.
                self._pending = {
                    entity_id: r for entity_id, r in self._pending.items()
                    if r.generation > generation
                }


def _record_payload(record: ManualLightRecord) -> dict[str, object]:
    return {
        "entityId": record.entity_id, "action": record.action,
        "source": record.source, "fingerprint": record.fingerprint,
        "observedAtMs": record.observed_at_ms, "deadlineMs": record.deadline_ms,
        "generation": record.generation, "legacy": record.legacy,
    }


def _parse_record(raw: object) -> ManualLightRecord:
    if not isinstance(raw, Mapping):
        raise ValueError("manual light record is invalid")
    entity_id, action = raw.get("entityId"), raw.get("action")
    source, fingerprint = raw.get("source"), raw.get("fingerprint")
    observed, deadline = raw.get("observedAtMs"), raw.get("deadlineMs")
    generation, legacy = raw.get("generation"), raw.get("legacy", False)
    if (
        not isinstance(entity_id, str) or not entity_id.startswith(MANAGED_LIGHT_DOMAINS)
        or action not in {"on", "off", "unknown"}
        or not isinstance(source, str) or not source
        or not isinstance(fingerprint, str) or not fingerprint
        or type(observed) is not int or observed < 0
        or type(deadline) is not int or deadline <= observed
        or type(generation) is not int or generation <= 0
        or type(legacy) is not bool
    ):
        raise ValueError("manual light record is invalid")
    return ManualLightRecord(entity_id, action, source, fingerprint,
                             observed, deadline, generation, legacy)


def valid_manual_light_authority_payload(value: object) -> bool:
    try:
        if not isinstance(value, Mapping) or value.get("version") != 2:
            return False
        records = value.get("records")
        if not isinstance(records, list) or len(records) > MAX_RECORDS:
            return False
        recent = value.get("recentFingerprints", [])
        if (
            not isinstance(recent, list)
            or len(recent) > MAX_RECENT_FINGERPRINTS
            or any(not isinstance(item, str) or not item for item in recent)
            or len(set(recent)) != len(recent)
        ):
            return False
        parsed = [_parse_record(record) for record in records]
        return len({r.entity_id for r in parsed}) == len(parsed)
    except ValueError:
        return False
