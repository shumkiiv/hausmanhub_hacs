"""Entity keyed, deny-only evidence of manual light actions."""

from __future__ import annotations

import asyncio
import copy
import time
from collections.abc import Callable, Iterable, Mapping
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
    async def async_save_if(self, payload: dict[str, object], is_current: Callable[[], bool]) -> bool: ...
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


@dataclass(frozen=True, slots=True)
class ManualLightReturnToken:
    """A durable common-store release that still holds the dispatch barrier."""

    entity_ids: frozenset[str]
    fence_generation: int
    operation_id: int
    scope_generation: int = 0
    config_version: int = 1


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
        self._observation_started_ms: int | None = None
        self._restart_scope_registered = False
        self._observed_scope: frozenset[str] = frozenset()
        self._reconciled_entities: set[str] = set()
        self._recovery_scopes: dict[frozenset[str], int] = {}
        self._recovery_scope_floors: dict[frozenset[str], int] = {}
        self._scope_generation = 0
        # Releasing a proved manual ON spans the authority store and the old
        # room journal.  Keep every automatic route closed between those two
        # durable writes; this is not an AUTO ownership claim.
        self._explicit_return_pending = False
        self._explicit_return_operation_id = 0
        self._active_return_operation_id: int | None = None
        self._return_operation: dict[str, object] | None = None
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
            if graph != self._related_sources:
                self._scope_generation += 1
                changed = {
                    item for item in self._related_sources.components.keys() | graph.components.keys()
                    if self._related_sources.related(item) != graph.related(item)
                }
                if not graph.ready or not self._related_sources.ready:
                    changed.update(self._reconciled_entities)
                self._reconciled_entities.difference_update(changed)
                self._recovery_scopes = {
                    scope: version for scope, version in self._recovery_scopes.items()
                    if not scope.intersection(changed)
                }
                self._recovery_scope_floors = {
                    scope: floor for scope, floor in self._recovery_scope_floors.items()
                    if scope in self._recovery_scopes
                }
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
            or (self._restart_uncertain and not self._restart_scope_registered)
            or self._startup_observation_pending
            or self._explicit_return_pending
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
        if self._restart_uncertain and not self._restart_scope_registered:
            return "restart_uncertain"
        if self._startup_observation_pending:
            return "restart_uncertain"
        if self._explicit_return_pending:
            return "return_pending"
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
        if self._restart_uncertain and not (
            self._related_sources.related(entity_id) <= self._reconciled_entities
        ):
            return "restart_uncertain"
        return None

    def begin_startup_observation(self) -> None:
        """Close dispatch until the full room entity set has been restored."""
        self._startup_observation_pending = True
        self._observation_started_ms = int(time.time() * 1000)
        self._restart_scope_registered = False
        self._reconciled_entities.clear()
        self._scope_generation += 1

    def finish_startup_observation(
        self, entity_ids: Iterable[str] | None = None,
    ) -> None:
        """Finish the common startup; each restarted scope still needs proof."""
        if entity_ids is not None:
            scope = frozenset(entity_ids)
            if any(not isinstance(item, str) or not item.startswith(MANAGED_LIGHT_DOMAINS)
                   for item in scope):
                self.mark_unhealthy()
                return
            self._observed_scope = scope
            self._restart_scope_registered = True
        self._startup_observation_pending = False

    def register_recovery_scope(self, entity_ids: Iterable[str], version: int) -> None:
        """Bind recovery to the complete configured group and its revision."""
        scope = frozenset(entity_ids)
        if not scope or type(version) is not int or version < 1:
            return
        if any(self._related_sources.related(item) != scope for item in scope):
            return
        if self._recovery_scopes.get(scope) != version:
            self._scope_generation += 1
            self._reconciled_entities.difference_update(scope)
            self._recovery_scopes[scope] = version
            self._recovery_scope_floors[scope] = (
                self._observation_started_ms
                if self._startup_observation_pending and self._observation_started_ms is not None
                else int(time.time() * 1000)
            )
            # Runtime groups are included by the listener's dynamic managed
            # scope. A newly configured group needs observations AFTER this
            # registration, never reports from before it became managed.
            if self._restart_scope_registered:
                self._observed_scope = self._observed_scope | scope

    async def async_reconcile_restart(
        self, entity_ids: Iterable[str], *, version: int,
        observe_off: Callable[[], Mapping[str, int] | None],
        now_ms: Callable[[], int],
    ) -> bool:
        """Reconcile one group, without releasing records or issuing commands.

        The adapter proves current OFF and a new absence window. This boundary
        alone checks the saved manual evidence, persists it, then fences both
        new manual events and changed observations across the storage await.
        Recovery is deliberately not durable: another restart needs new proof.
        """
        scope = frozenset(entity_ids)
        if not self._restart_uncertain or scope <= self._reconciled_entities:
            return False
        generation, scope_generation = self._generation, self._scope_generation

        def eligible() -> bool:
            return (
                bool(scope) and self.persistence_ready
                and self.barrier_reason is None
                and self._related_sources.ready
                and self._restart_scope_registered
                and self._observation_started_ms is not None
                and scope <= self._observed_scope
                and self._recovery_scopes.get(scope) == version
                and self._scope_generation == scope_generation
                and self._generation == generation
                and not self._pending
                and all(self._related_sources.related(item) == scope for item in scope)
                and all(self.barrier_reason_for(item) in {None, "restart_uncertain"}
                        for item in scope)
                and not any(
                    (record := self.record(item)) is not None
                    and self._priority_active(record, now_ms())
                    for item in scope
                )
            )

        async with self._writer:
            if not eligible():
                return False
            observed = observe_off()
            if observed is None or set(observed) != scope:
                return False
            observed = dict(observed)
            if any(type(stamp) is not int or not (
                max(self._observation_started_ms, self._recovery_scope_floors[scope])
                <= stamp <= now_ms()
            ) for stamp in observed.values()):
                return False
            payload = self._payload(self._records, self._generation)
            try:
                await self._store.async_save(copy.deepcopy(payload))
            except BaseException:
                self._storage_failed = True
                raise
            if not eligible() or observe_off() != observed:
                return False
            self._reconciled_entities.update(scope)
            return True

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
        # Global barriers are unconditional. A proved manual priority is
        # temporary (owner rule 2026-10-09: one hour by default); a lapsed
        # record must not keep blocking dispatch. Legacy imports and
        # unknown-action records carry no proved gesture time, so their
        # technical deadline never opens this deny boundary by the clock.
        if self.barrier_reason_for(entity_id) is not None:
            return True
        now_ms = int(time.time() * 1000)
        return any(
            (record := self.record(related)) is not None
            and self._priority_active(record, now_ms)
            for related in self._related_sources.related(entity_id)
        )

    @staticmethod
    def _priority_active(record: ManualLightRecord, now_ms: int) -> bool:
        # ``deadline_ms > now_ms`` means still active; equality is expired.
        if record.legacy or record.action == "unknown":
            return True
        return record.deadline_ms > now_ms

    def blocking_evidence(self, entity_id: str) -> ManualLightRecord | None:
        """Return linked evidence without copying it to a sibling entity.

        Only records whose priority is still active can be blocking
        evidence: a lapsed own record must not hide an active linked one,
        and with no active record there is no deny evidence at all.
        """

        now_ms = int(time.time() * 1000)
        own = self.record(entity_id)
        if own is not None and self._priority_active(own, now_ms):
            return own
        records = [
            record for related in self._related_sources.related(entity_id)
            if (record := self.record(related)) is not None
            and self._priority_active(record, now_ms)
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
                if not valid_manual_light_authority_payload(payload):
                    raise ValueError("manual light authority version is invalid")
                if payload.get("version") == 3:
                    self._return_operation = copy.deepcopy(payload["returnOperation"])
                    self._explicit_return_operation_id = self._return_operation["operationId"]
                    self._explicit_return_pending = True
                    # A restart never resumes a partial return with a new fence.
                    self._active_return_operation_id = None
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
                # Expiry ends a deny, not its evidence. Retain the last proved
                # action per entity so startup migration cannot turn an expired
                # action back into unknown legacy protection, or fail its
                # durable-import check after saving another entity.
                snapshot = {**self._records, **self._pending}
                payload = self._payload(snapshot, generation)
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

    def explicit_return_blocker(
        self, entity_ids: Iterable[str], *, config_version: int, all_manual: bool = False,
    ) -> str | None:
        """A new explicit intent may replace only an idle, identical pending scope."""
        scope = frozenset(entity_ids)
        reason = self.barrier_reason
        if reason not in {None, "return_pending"}:
            return reason
        if self._active_return_operation_id is not None:
            return "return_busy"
        if self._explicit_return_pending:
            if not all_manual:
                return "return_pending"
            operation = self._return_operation
            if (operation is None or frozenset(operation["entityIds"]) != scope
                    or operation["configVersion"] != config_version):
                return "return_scope_changed"
        if (not scope or not self.persistence_ready or not self._related_sources.ready
                or any(self._related_sources.related(entity) != scope for entity in scope)):
            return "attribution_uncertain"
        if any(entity.startswith(f"{domain}.") for entity in scope
               for domain in self._selector_uncertain_domains):
            return "selector_uncertain"
        if not all_manual and self._restart_uncertain and not scope <= self._reconciled_entities:
            return "restart_uncertain"
        return None

    async def async_begin_explicit_return(
        self, entity_ids: Iterable[str], *, all_manual: bool = False,
        config_version: int = 1,
        observations_current: Callable[[], bool] | None = None,
    ) -> ManualLightReturnToken | None:
        """Persist a transaction fence before changing any participant.

        The caller must finish the matching room-journal write with
        :meth:`async_finish_explicit_return`.  A new manual event can arrive
        while either write awaits: it remains pending and is never removed by
        this operation.
        """

        scope = frozenset(entity_ids)
        if not scope or any(
            not isinstance(entity_id, str)
            or not entity_id.startswith(MANAGED_LIGHT_DOMAINS)
            for entity_id in scope
        ):
            return None
        if (self.explicit_return_blocker(scope, config_version=config_version, all_manual=all_manual)
                or not callable(getattr(self._store, "async_save_if", None))
                or (all_manual and (observations_current is None or not observations_current()))):
            return None
        # This fence belongs to the physical gesture, not to the moment this
        # coroutine eventually acquires the writer.  A later manual event must
        # win even if its drain reaches durable storage before us.
        fence_generation = self._generation
        scope_generation = self._scope_generation
        # Close every dispatch path before waiting for the writer.  Until the
        # old record is durably removed it blocks its own component too.
        was_pending = self._explicit_return_pending
        self._explicit_return_pending = True
        self._explicit_return_operation_id += 1
        operation_id = self._explicit_return_operation_id
        self._active_return_operation_id = operation_id
        try:
            async with self._writer:
                if (
                    not self.persistence_ready
                    or self._dispatch_failed or self._recovery or self._startup_observation_pending
                    or self._scope_generation != scope_generation
                    or self._pending
                    or not self._related_sources.ready
                    or any(
                        self._related_sources.related(entity_id) != scope
                        for entity_id in scope
                    )
                    or any(
                        related.startswith(f"{domain}.")
                        for entity_id in scope
                        for related in self._related_sources.related(entity_id)
                        for domain in self._selector_uncertain_domains
                    )
                    or any(
                        record is not None and record.generation > fence_generation
                        for record in (self._records.get(entity_id) for entity_id in scope)
                    )
                ):
                    self._explicit_return_pending = was_pending
                    self._active_return_operation_id = None
                    return None
                records = [self._records.get(entity_id) for entity_id in scope]
                present = [record for record in records if record is not None]
                if not all_manual and (not present or any(
                    record.legacy or record.action != "on" for record in present
                )):
                    self._explicit_return_pending = was_pending
                    self._active_return_operation_id = None
                    return None
                # The snapshot can contain unrelated evidence that drained
                # while this gesture waited for the writer.  Persist its
                # fingerprint history through the same generation, otherwise
                # a restart could accept that already-observed event again.
                payload_generation = self._generation
                self._return_operation = {
                    "operationId": operation_id,
                    "entityIds": sorted(scope),
                    "fenceGeneration": fence_generation,
                    "configVersion": config_version,
                }
                payload = self._payload(self._records, payload_generation)
                if self._store is None:
                    raise RuntimeError("manual light authority store is unavailable")
                await self._store.async_save(copy.deepcopy(payload))
                return ManualLightReturnToken(
                    scope, fence_generation, operation_id, scope_generation, config_version,
                )
        except BaseException:
            self._storage_failed = True
            self._active_return_operation_id = None
            raise

    async def async_finish_explicit_return(
        self, token: ManualLightReturnToken, *, persisted: bool,
        observations_current: Callable[[], bool] | None = None,
    ) -> bool:
        """Commit only after all participants; preserve every newer event."""

        if (
            not self._explicit_return_pending
            or token.operation_id != self._active_return_operation_id
        ):
            raise RuntimeError("manual light return is not pending")
        try:
            return await self._async_commit_explicit_return(
                token, persisted=persisted, observations_current=observations_current,
            )
        finally:
            # Finishing a call is not finishing the durable transaction. A
            # later explicit click may retry; background recovery never does.
            if token.operation_id == self._active_return_operation_id:
                self._active_return_operation_id = None

    async def _async_commit_explicit_return(
        self, token: ManualLightReturnToken, *, persisted: bool,
        observations_current: Callable[[], bool] | None,
    ) -> bool:
        if not persisted:
            # The durable v3 fence survives a crash and is rejected by older
            # v2 loaders as well. A code rollback cannot silently open it.
            self._storage_failed = True
            self._active_return_operation_id = None
            return False
        async with self._writer:
            if (not self._explicit_return_is_current(token)
                    or observations_current is not None and not observations_current()):
                return False
            try:
                generation = self._generation
                snapshot = {entity: record for entity, record in self._records.items()
                            if entity not in token.entity_ids}

                def can_commit() -> bool:
                    return (
                        self._explicit_return_is_current(token)
                        and self._generation == generation and not self._pending
                        and not any((record := self.record(entity)) is not None
                                    and record.generation > token.fence_generation
                                    for entity in token.entity_ids)
                        and (observations_current is None or observations_current())
                    )

                if not can_commit():
                    return False
                committed = await self._store.async_save_if(
                    self._payload(snapshot, generation, include_return=False), can_commit,
                )
                if not committed:
                    return False
                # The adapter does not yield after its final check and rename.
                # Complete memory in this same event-loop turn.
                self._records = snapshot
                if observations_current is not None:
                    # Only this new explicit intent closes this group's old
                    # observation gap. This is not an AUTO ownership record.
                    self._reconciled_entities.update(token.entity_ids)
                self._return_operation = None
                self._explicit_return_pending = False
                self._active_return_operation_id = None
                return True
            except BaseException:
                self._storage_failed = True
                raise

    def _explicit_return_is_current(self, token: ManualLightReturnToken) -> bool:
        """Recheck every independent deny while ignoring only our own fence."""
        scope = token.entity_ids
        return (
            self.persistence_ready and not self._dispatch_failed and not self._recovery
            and not self._startup_observation_pending and self._related_sources.ready
            and self._scope_generation == token.scope_generation
            and all(self._related_sources.related(entity) == scope for entity in scope)
            and not any(entity.startswith(f"{domain}.") for entity in scope
                        for domain in self._selector_uncertain_domains)
            and (not self._restart_uncertain or scope <= self._reconciled_entities
                 or (self._restart_scope_registered and scope <= self._observed_scope
                     and self._recovery_scopes.get(scope) == token.config_version))
        )

    def explicit_return_observation_floor(self, entity_ids: Iterable[str]) -> int | None:
        """A scoped restart requires observations after subscription/registration."""
        scope = frozenset(entity_ids)
        if not self._restart_uncertain or scope <= self._reconciled_entities:
            return None
        return max(self._observation_started_ms or 0, self._recovery_scope_floors.get(scope, 0))

    def _payload(
        self, records: Mapping[str, ManualLightRecord], generation: int, *,
        include_return: bool = True,
    ) -> dict[str, object]:
        pending = self._return_operation if include_return else None
        return {
            "version": 3 if pending is not None else 2,
            "records": [_record_payload(record) for record in
                        sorted(records.values(), key=lambda item: item.entity_id)],
            "recentFingerprints": [key for key, seen in self._recent.items()
                                   if seen <= generation],
            **({"returnOperation": copy.deepcopy(pending)} if pending is not None else {}),
        }

    async def async_release_manual_claims_through(
        self, boundary_generation: int
    ) -> tuple[str, ...]:
        """Release pre-return manual claims (the away→home transition).

        Owner decision 2026-10-09: the away→home transition is the single
        contour point that re-arms automation for a returning household.
        Only records with a generation at or before the return gesture are
        released: a manual action observed after it — including the same
        millisecond — keeps its normal priority. The reduced state is
        persisted BEFORE anything is dropped from memory, so the deny
        boundary never opens ahead of durable storage; a storage failure
        releases nothing.
        """

        if type(boundary_generation) is not int or boundary_generation < 0:
            return ()
        async with self._writer:
            if self._storage_failed or self._explicit_return_pending:
                return ()
            # Freeze the exact record objects selected for release. A capture
            # that arrives while the save is pending replaces the dict entry
            # with a new object of a later generation: only the frozen
            # objects themselves may be dropped after the save.
            doomed_records = {
                entity_id: record
                for entity_id, record in self._records.items()
                if record.generation <= boundary_generation
            }
            doomed_pending = {
                entity_id: record
                for entity_id, record in self._pending.items()
                if record.generation <= boundary_generation
            }
            if not doomed_records and not doomed_pending:
                return ()
            removed: list[str] = []
            kept = frozenset(doomed_records) | frozenset(doomed_pending)
            snapshot = {
                **{
                    entity_id: record
                    for entity_id, record in self._records.items()
                    if entity_id not in kept
                },
                **{
                    entity_id: record
                    for entity_id, record in self._pending.items()
                    if entity_id not in kept
                },
            }
            payload = {
                "version": 2,
                "records": [_record_payload(r) for r in
                            sorted(snapshot.values(), key=lambda item: item.entity_id)],
                "recentFingerprints": [
                    fingerprint for fingerprint, seen_generation in self._recent.items()
                    if seen_generation <= self._generation
                ],
            }
            try:
                if self._store is not None:
                    await self._store.async_save(copy.deepcopy(payload))
            except Exception:
                # The in-memory deny boundary stays exactly as it was.
                return ()
            for entity_id, doomed in doomed_records.items():
                if self._records.get(entity_id) is doomed:
                    self._records.pop(entity_id, None)
                    removed.append(entity_id)
            for entity_id, doomed in doomed_pending.items():
                if self._pending.get(entity_id) is doomed:
                    self._pending.pop(entity_id, None)
                    removed.append(entity_id)
            return tuple(sorted(removed))

    def current_generation(self) -> int:
        """Freeze the manual-event generation at the away→home gesture."""

        return self._generation


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
        if not isinstance(value, Mapping) or value.get("version") not in {2, 3}:
            return False
        if value.get("version") == 3:
            operation = value.get("returnOperation")
            if not isinstance(operation, Mapping):
                return False
            scope = operation.get("entityIds")
            if (not isinstance(scope, list) or not scope or len(scope) > MAX_RECORDS
                    or any(not isinstance(item, str) or not item.startswith(MANAGED_LIGHT_DOMAINS)
                           for item in scope)
                    or len(set(scope)) != len(scope)
                    or any(type(operation.get(key)) is not int or operation[key] < minimum
                           for key, minimum in (("operationId", 1), ("fenceGeneration", 0),
                                                ("configVersion", 1)))):
                return False
        elif "returnOperation" in value:
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
