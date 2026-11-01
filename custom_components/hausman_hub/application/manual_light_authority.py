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
    release_entity_ids: frozenset[str] | None = None


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
        self._return_operation: dict[str, object] | None = None
        self._return_operations: dict[int, dict[str, object]] = {}
        self._active_return_scopes: dict[int, frozenset[str]] = {}
        self._seeded_empty_store = False
        self._writer = asyncio.Lock()
        self._related_sources = RelatedLightSources(True, {})
        self._related_sources_invalidated = False
        # Volatile evidence only: never reconstructed from a saved record.
        self._manual_event_epochs: dict[str, int] = {}
        self._manual_topology_epochs: dict[str, int] = {}
        self._manual_scope_overflow = False
        # Service selectors (area/device/label/all) cannot be expanded safely
        # at this event boundary.  Keep this evidence by HA service domain;
        # it is monotonic for the running authority and is never inferred away
        # by a later state transition.
        self._selector_uncertain_domains: set[str] = set()

    def set_related_sources(self, graph: RelatedLightSources) -> None:
        """Atomically replace the only relation graph; an invalid graph denies all."""
        if not self._related_sources_invalidated:
            if graph == self._related_sources:
                return
            if graph != self._related_sources:
                self._scope_generation += 1
                changed = {
                    item for item in self._related_sources.components.keys() | graph.components.keys()
                    if self._related_sources.related(item) != graph.related(item)
                }
                evidence_changed = set(changed)
                evidence_changed.update(self._related_sources.components.keys() ^ graph.components.keys())
                if not graph.ready or not self._related_sources.ready:
                    evidence_changed.update(self._related_sources.components.keys() | graph.components.keys())
                    changed.update(self._reconciled_entities)
                for item in evidence_changed:
                    if item not in self._manual_topology_epochs and len(self._manual_topology_epochs) >= MAX_RECORDS:
                        self._manual_scope_overflow = True
                        break
                    self._manual_topology_epochs[item] = self._manual_topology_epochs.get(item, 0) + 1
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

    @property
    def related_source_entities(self) -> frozenset[str]:
        """Entities requiring manual-event observation, including power sources."""
        return frozenset(self._related_sources.components)

    @property
    def related_sources(self) -> RelatedLightSources:
        """Immutable current graph for a synchronous dispatch proof."""
        return self._related_sources

    def invalidate_related_sources(self) -> None:
        self._related_sources_invalidated = True
        self._related_sources = RelatedLightSources(False, {})

    def manual_event_scope(self, entity_id: str) -> tuple[tuple[str, int, int], ...] | None:
        """Snapshot local event evidence; this does not authorize a command."""
        graph = self._related_sources
        if (self._manual_scope_overflow or not self.persistence_ready or not graph.ready
                or entity_id not in graph.components):
            return None
        scope = graph.related(entity_id)
        if (not scope or any(item not in graph.components or graph.related(item) != scope
                             or self.barrier_reason_for(item) is not None
                             or item in self._pending for item in scope)):
            return None
        return tuple((item, self._manual_event_epochs.get(item, 0),
                      self._manual_topology_epochs.get(item, 0)) for item in sorted(scope))

    def scope_matches_graph(
        self, entity_id: str, graph: RelatedLightSources,
        evidence: tuple[tuple[str, int, int], ...] | None,
    ) -> bool:
        """Check a previously captured local scope against current evidence."""
        return (evidence is not None and graph is self._related_sources
                and self.manual_event_scope(entity_id) == evidence)

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
        if global_reason is not None and global_reason != "return_pending":
            return global_reason
        if not self._related_sources.ready:
            return "attribution_uncertain"
        related = self._related_sources.related(entity_id)
        if self._explicit_return_pending and not self._return_operations and not self._active_return_scopes:
            return "return_pending"
        if any(related.intersection(op["entityIds"]) for op in self._return_operations.values()):
            return "return_pending"
        if any(related.intersection(scope) for scope in self._active_return_scopes.values()):
            return "return_pending"
        if (not self._pending_return_scopes_proved()
                or (self._return_operations or self._active_return_scopes)
                and entity_id not in self._related_sources.components):
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

    def _scope_is_complete(self, scope: frozenset[str]) -> bool:
        """Accept a union of complete, internally consistent physical groups."""
        graph = self._related_sources
        if not graph.ready or not scope:
            return False
        # Preserve the pre-existing standalone singleton behavior when no
        # graph has been registered at all. Once a graph exists, an omitted
        # entity is never treated as proof of an independent component.
        if not graph.components:
            return len(scope) == 1
        for entity in scope:
            group = graph.components.get(entity)
            if (not isinstance(group, frozenset) or entity not in group
                    or not group <= scope
                    or any(graph.components.get(member) != group for member in group)):
                return False
        return True

    def _pending_return_scopes_proved(self) -> bool:
        """A removed or regrouped source cannot prove another room independent."""
        scopes = [frozenset(op["entityIds"]) for op in self._return_operations.values()]
        scopes.extend(self._active_return_scopes.values())
        for scope in scopes:
            if not self._scope_is_complete(scope):
                return False
        return True

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
        if not self._scope_is_complete(scope):
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
                and self.barrier_reason in {None, "return_pending"}
                and self._related_sources.ready
                and self._restart_scope_registered
                and self._observation_started_ms is not None
                and scope <= self._observed_scope
                and self._recovery_scopes.get(scope) == version
                and self._scope_generation == scope_generation
                and self._generation == generation
                and not self._pending
                and self._scope_is_complete(scope)
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

    def blocks_departure_off(self, entity_id: str) -> bool:
        """Explicit departure may supersede known choices, never an evidence gap.

        This is only an OFF decision. It does not release manual records or
        grant automatic ownership, and linked uncertain evidence still denies.
        """
        if self.barrier_reason_for(entity_id) is not None:
            return True
        now_ms = int(time.time() * 1000)
        return any(
            (record := self.record(related)) is not None
            and self._priority_active(record, now_ms)
            and (record.legacy or record.action not in {"on", "off"})
            for related in self._related_sources.related(entity_id)
        )

    def set_priority_duration_resolver(self, resolver: Callable[[str, str], int]) -> None:
        """Room settings may change a deadline, never the proved gesture time."""
        self._priority_duration_resolver = resolver

    def _priority_active(self, record: ManualLightRecord, now_ms: int) -> bool:
        # ``deadline_ms > now_ms`` means still active; equality is expired.
        if record.legacy or record.action == "unknown":
            return True
        resolver = getattr(self, "_priority_duration_resolver", None)
        if resolver is not None:
            try:
                duration = resolver(record.entity_id, record.action)
                if type(duration) is not int or duration <= 0:
                    return True
                return record.observed_at_ms + duration > now_ms
            except Exception:
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
                    self._return_operations = {self._return_operation["operationId"]: self._return_operation}
                    self._explicit_return_operation_id = self._return_operation["operationId"]
                    self._explicit_return_pending = True
                elif payload.get("version") == 4:
                    self._return_operations = {
                        op["operationId"]: copy.deepcopy(op)
                        for op in payload["returnOperations"]
                    }
                    self._return_operation = next(iter(self._return_operations.values()))
                    self._explicit_return_operation_id = max(self._return_operations)
                    self._explicit_return_pending = True
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
        if entity_id not in self._manual_event_epochs and len(self._manual_event_epochs) >= MAX_RECORDS:
            self._manual_scope_overflow = True
        else:
            self._manual_event_epochs[entity_id] = self._generation
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
        allow_stale_replacement: Callable[[frozenset[str], int], bool] | None = None,
    ) -> str | None:
        """A new explicit intent may replace only an idle, identical pending scope.

        A durable pending fence whose own recorded profile version no longer
        exists can never be committed: versions only grow, so its exact
        ``(entityIds, configVersion)`` precondition is gone forever.  When the
        caller proves that staleness (the same complete entity set now carries
        a different saved version), a new explicit owner intent may replace
        the fence.  ``allow_stale_replacement`` is that proof; only the
        explicit owner flow passes it — the clock tick never does.
        """
        scope = frozenset(entity_ids)
        reason = self.barrier_reason
        if reason not in {None, "return_pending"}:
            return reason
        if any(scope.intersection(active) for active in self._active_return_scopes.values()):
            return "return_busy"
        overlaps = [op for op in self._return_operations.values()
                    if scope.intersection(op["entityIds"])]
        if (not overlaps and (self._return_operations or self._active_return_scopes) and (
                not self._pending_return_scopes_proved()
                or any(entity not in self._related_sources.components for entity in scope))):
            return "attribution_uncertain"
        if overlaps:
            if not all_manual:
                return "return_pending"
            if len(overlaps) != 1:
                return "return_scope_changed"
            operation = overlaps[0]
            if (operation is None or frozenset(operation["entityIds"]) != scope
                    or operation["configVersion"] != config_version):
                stale = (
                    allow_stale_replacement is not None and operation is not None
                    and frozenset(operation["entityIds"]) == scope
                    and allow_stale_replacement(
                        frozenset(operation["entityIds"]), operation["configVersion"])
                )
                if not stale:
                    return "return_scope_changed"
        if not self.persistence_ready or not self._scope_is_complete(scope):
            return "attribution_uncertain"
        if any(entity.startswith(f"{domain}.") for entity in scope
               for domain in self._selector_uncertain_domains):
            return "selector_uncertain"
        if not all_manual and self._restart_uncertain and not scope <= self._reconciled_entities:
            return "restart_uncertain"
        return None

    def _sync_return_state(self) -> None:
        self._explicit_return_pending = bool(self._return_operations or self._active_return_scopes)
        self._return_operation = next(iter(self._return_operations.values()), None)

    async def async_begin_explicit_return(
        self, entity_ids: Iterable[str], *, all_manual: bool = False,
        config_version: int = 1,
        observations_current: Callable[[], bool] | None = None,
        allow_stale_replacement: Callable[[frozenset[str], int], bool] | None = None,
        release_entity_ids: frozenset[str] | None = None,
    ) -> ManualLightReturnToken | None:
        """Persist one room's fence before changing its other participants."""
        scope = frozenset(entity_ids)
        if release_entity_ids is not None and (not release_entity_ids or not release_entity_ids <= scope):
            return None
        if (not scope or any(not isinstance(entity, str)
                             or not entity.startswith(MANAGED_LIGHT_DOMAINS)
                             for entity in scope)):
            return None
        if (self.explicit_return_blocker(
                scope, config_version=config_version, all_manual=all_manual,
                allow_stale_replacement=allow_stale_replacement)
                or not callable(getattr(self._store, "async_save_if", None))
                or (all_manual and (observations_current is None or not observations_current()))):
            return None
        fence_generation = self._generation
        scope_generation = self._scope_generation
        self._explicit_return_operation_id += 1
        operation_id = self._explicit_return_operation_id
        self._active_return_scopes[operation_id] = scope
        self._sync_return_state()
        started = False
        try:
            async with self._writer:
                if (not self.persistence_ready or self._dispatch_failed or self._recovery
                        or self._startup_observation_pending
                        or self._scope_generation != scope_generation
                        or (observations_current is not None and not observations_current())
                        or self._pending or not self._scope_is_complete(scope)
                        or any(entity.startswith(f"{domain}.") for entity in scope
                               for domain in self._selector_uncertain_domains)
                        or any(record is not None and record.generation > fence_generation
                               for record in (self._records.get(entity) for entity in scope))):
                    return None
                present = [record for entity in scope
                           if (record := self._records.get(entity)) is not None]
                if not all_manual and (not present or any(
                        record.legacy or record.action != "on" for record in present)):
                    return None
                overlaps = [op for op in self._return_operations.values()
                            if scope.intersection(op["entityIds"])]
                if len(overlaps) > 1:
                    return None
                previous = overlaps[0] if overlaps else None
                if previous is not None:
                    if not all_manual or frozenset(previous["entityIds"]) != scope:
                        return None
                    if previous["configVersion"] != config_version and (
                            allow_stale_replacement is None
                            or not allow_stale_replacement(scope, previous["configVersion"])):
                        return None
                new_operation = {
                    "operationId": operation_id, "entityIds": sorted(scope),
                    "fenceGeneration": fence_generation, "configVersion": config_version,
                    **({"releaseEntityIds": sorted(release_entity_ids)}
                       if release_entity_ids is not None else {}),
                }
                operations = dict(self._return_operations)
                if previous is not None:
                    operations.pop(previous["operationId"])
                operations[operation_id] = new_operation
                payload = self._payload(self._records, self._generation, operations=operations)
                if self._store is None:
                    raise RuntimeError("manual light authority store is unavailable")
                if previous is not None and previous["configVersion"] != config_version:
                    def can_replace() -> bool:
                        return (
                            self._active_return_scopes.get(operation_id) == scope
                            and self._return_operations.get(previous["operationId"]) is previous
                            and not self._pending and self._scope_generation == scope_generation
                            and not any(record is not None and record.generation > fence_generation
                                        for record in (self._records.get(entity) for entity in scope))
                            and (observations_current is None or observations_current())
                            and (previous["configVersion"] == config_version
                                 or (allow_stale_replacement is not None
                                     and allow_stale_replacement(scope, previous["configVersion"])))
                        )
                    if not await self._store.async_save_if(copy.deepcopy(payload), can_replace):
                        return None
                else:
                    await self._store.async_save(copy.deepcopy(payload))
                self._return_operations = operations
                self._sync_return_state()
                started = True
                return ManualLightReturnToken(
                    scope, fence_generation, operation_id, scope_generation, config_version,
                    release_entity_ids,
                )
        except BaseException:
            self._storage_failed = True
            raise
        finally:
            if not started:
                self._active_return_scopes.pop(operation_id, None)
                self._sync_return_state()

    async def async_abort_explicit_return(self, token: ManualLightReturnToken) -> None:
        """Keep the durable deny, but let healthy storage retain newer gestures."""
        if self._active_return_scopes.get(token.operation_id) != token.entity_ids:
            raise RuntimeError("manual light return is not pending")
        try:
            # No bypass or reset of storage health. A genuine failed drain
            # still marks the authority unhealthy through its ordinary path.
            await self.async_drain()
        finally:
            self._active_return_scopes.pop(token.operation_id, None)
            self._sync_return_state()

    async def async_cancel_selective_return(self, token: ManualLightReturnToken) -> bool:
        """Close a selective fence without removing any authority records."""
        if token.release_entity_ids is None or not self.persistence_ready:
            return False
        await self.async_drain()
        async with self._writer:
            operation = self._return_operations.get(token.operation_id)
            if (not self.persistence_ready or self._dispatch_failed or self._recovery
                    or self._pending or operation is None
                    or frozenset(operation.get("entityIds", ())) != token.entity_ids
                    or frozenset(operation.get("releaseEntityIds", ())) != token.release_entity_ids
                    or self._scope_generation != token.scope_generation
                    or not self._scope_is_complete(token.entity_ids)):
                return False
            generation = self._generation

            def current() -> bool:
                return (self.persistence_ready and not self._pending
                        and self._generation == generation
                        and self._scope_generation == token.scope_generation
                        and self._return_operations.get(token.operation_id) is operation)

            operations = {key: value for key, value in self._return_operations.items()
                          if key != token.operation_id}
            try:
                saved = await self._store.async_save_if(
                    self._payload(self._records, generation, operations=operations), current)
                if not saved:
                    return False
                self._return_operations = operations
                self._sync_return_state()
                return True
            except Exception:  # noqa: BLE001 - keep the fence if durable cancellation fails
                self._storage_failed = True
                return False

    async def async_recover_selective_return(self, entity_ids: Iterable[str]) -> bool:
        """Cancel an interrupted selective fence, retaining all manual evidence."""
        scope = frozenset(entity_ids)
        if not self.persistence_ready or not self._related_sources.ready:
            return False
        matches = [op for op in self._return_operations.values()
                   if frozenset(op["entityIds"]) == scope and "releaseEntityIds" in op]
        if len(matches) != 1:
            return False
        operation = matches[0]
        if operation["operationId"] in self._active_return_scopes:
            return False
        selected = frozenset(operation["releaseEntityIds"])
        if not selected or not selected <= scope or any(self.record(entity) is None for entity in selected):
            return False
        token = ManualLightReturnToken(scope, operation["fenceGeneration"],
                                       operation["operationId"], self._scope_generation,
                                       operation["configVersion"], selected)
        return await self.async_cancel_selective_return(token)

    async def async_finish_explicit_return(
        self, token: ManualLightReturnToken, *, persisted: bool,
        observations_current: Callable[[], bool] | None = None,
    ) -> bool:
        """Commit only after all participants; preserve every newer event."""

        if (
            self._active_return_scopes.get(token.operation_id) != token.entity_ids
        ):
            raise RuntimeError("manual light return is not pending")
        try:
            return await self._async_commit_explicit_return(
                token, persisted=persisted, observations_current=observations_current,
            )
        finally:
            # Finishing a call is not finishing the durable transaction. A
            # later explicit click may retry; background recovery never does.
            self._active_return_scopes.pop(token.operation_id, None)
            self._sync_return_state()

    async def _async_commit_explicit_return(
        self, token: ManualLightReturnToken, *, persisted: bool,
        observations_current: Callable[[], bool] | None,
    ) -> bool:
        if not persisted:
            # The durable v3 fence survives a crash and is rejected by older
            # v2 loaders as well. A code rollback cannot silently open it.
            self._storage_failed = True
            return False
        async with self._writer:
            if (not self._explicit_return_is_current(token)
                    or observations_current is not None and not observations_current()):
                return False
            try:
                generation = self._generation
                released_entities = token.entity_ids if token.release_entity_ids is None else token.release_entity_ids
                snapshot = {entity: record for entity, record in self._records.items()
                            if entity not in released_entities}

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
                    self._payload(snapshot, generation, operations={
                        key: value for key, value in self._return_operations.items()
                        if key != token.operation_id
                    }), can_commit,
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
                self._return_operations.pop(token.operation_id, None)
                self._sync_return_state()
                return True
            except BaseException:
                self._storage_failed = True
                raise

    def _explicit_return_is_current(self, token: ManualLightReturnToken) -> bool:
        """Recheck every independent deny while ignoring only our own fence."""
        scope = token.entity_ids
        return (
            self.persistence_ready and not self._dispatch_failed and not self._recovery
            and self._active_return_scopes.get(token.operation_id) == scope
            and token.operation_id in self._return_operations
            and not self._startup_observation_pending and self._related_sources.ready
            and self._scope_generation == token.scope_generation
            and self._scope_is_complete(scope)
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
        operations: Mapping[int, dict[str, object]] | None = None,
    ) -> dict[str, object]:
        pending = list((self._return_operations if operations is None else operations).values()) if include_return else []
        return {
            "version": 4 if len(pending) > 1 else 3 if pending else 2,
            "records": [_record_payload(record) for record in
                        sorted(records.values(), key=lambda item: item.entity_id)],
            "recentFingerprints": [key for key, seen in self._recent.items()
                                   if seen <= generation],
            **({"returnOperations": copy.deepcopy(pending)} if len(pending) > 1
               else {"returnOperation": copy.deepcopy(pending[0])} if pending else {}),
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
            if (self._storage_failed or not self._related_sources.ready
                    or not self._pending_return_scopes_proved()):
                return ()
            fenced = set().union(
                *(set(op["entityIds"]) for op in self._return_operations.values()),
                *(set(scope) for scope in self._active_return_scopes.values()),
            )

            def releasable(entity_id: str) -> bool:
                return not self._related_sources.related(entity_id).intersection(fenced)
            # Freeze the exact record objects selected for release. A capture
            # that arrives while the save is pending replaces the dict entry
            # with a new object of a later generation: only the frozen
            # objects themselves may be dropped after the save.
            doomed_records = {
                entity_id: record
                for entity_id, record in self._records.items()
                if record.generation <= boundary_generation and releasable(entity_id)
            }
            doomed_pending = {
                entity_id: record
                for entity_id, record in self._pending.items()
                if record.generation <= boundary_generation and releasable(entity_id)
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
            payload = self._payload(snapshot, self._generation)
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
        if not isinstance(value, Mapping) or value.get("version") not in {2, 3, 4}:
            return False
        version = value.get("version")
        if version == 3:
            operations = [value.get("returnOperation")]
            if "returnOperations" in value:
                return False
        elif version == 4:
            operations = value.get("returnOperations")
            if (not isinstance(operations, list) or not operations
                    or len(operations) > MAX_RECORDS or "returnOperation" in value):
                return False
        else:
            operations = []
            if "returnOperation" in value or "returnOperations" in value:
                return False
        entities: set[str] = set()
        operation_ids: set[int] = set()
        for operation in operations:
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
            if entities.intersection(scope) or operation["operationId"] in operation_ids:
                return False
            selected = operation.get("releaseEntityIds")
            if "releaseEntityIds" in operation and (not isinstance(selected, list) or not selected
                    or any(not isinstance(item, str) for item in selected)
                    or len(set(selected)) != len(selected) or not set(selected) <= set(scope)):
                return False
            entities.update(scope)
            operation_ids.add(operation["operationId"])
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
