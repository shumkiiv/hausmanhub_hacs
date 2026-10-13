"""Synchronous HA event boundary for shared manual light authority."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .application.manual_light_authority import ManualLightAuthority
from .manual_light_off_protection_events import _is_fresh_light_transition

if TYPE_CHECKING:
    from .domain.room_lighting import RoomLightingConfig


_LOGGER = logging.getLogger(__name__)
MAX_STARTUP_EVENTS = 512
_EXACT_ENTITY_ID = re.compile(r"^(?:light|switch)\.[a-z0-9_]+$")


def room_light_entity_ids(configs: Iterable[RoomLightingConfig]) -> frozenset[str]:
    """Only physical light targets are manual-light authority subjects."""
    return frozenset(
        target.entity_id
        for config in configs
        for target in config.devices.light_targets
        if isinstance(target.entity_id, str)
        and target.entity_id.startswith(("light.", "switch."))
    )


@dataclass(frozen=True, slots=True)
class _StartupEvidence:
    entity_id: str
    action: str
    source: str
    context_id: str | None
    event_identity: str
    observed_at_ms: int | None = None


class ManualLightAuthorityEvents:
    def __init__(
        self,
        hass: object,
        authority: ManualLightAuthority,
        managed_entities: Callable[[], Iterable[str]],
        automatic_contexts: object,
        room_executor: Callable[[], object | None],
    ) -> None:
        self._hass = hass
        self._authority = authority
        self._managed_entities = managed_entities
        self._automatic_contexts = automatic_contexts
        self._room_executor = room_executor
        self._closed = False
        self._drain_tasks: set = set()
        self._unsubscribers: list[Callable[[], None]] = []
        self._service_actions: dict[tuple[str, str], str] = {}
        self._managed_ready = True
        self._startup_evidence: list[_StartupEvidence] = []
        self._ambiguous_startup_domains: set[str] = set()

    def start(self, *, buffer_startup: bool = False) -> Callable[[], None]:
        # The event loop is captured here (async setup context): event
        # callbacks may be dispatched from worker threads, so the drain
        # task must be scheduled threadsafe (live regression 2026-09-29:
        # hass.async_create_task from SyncWorker was blocked by HA and
        # the drain coroutine was never awaited). Without a running loop
        # (sync tests) the drain falls back to plain create_task.
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        if buffer_startup:
            self._managed_ready = False
            self._authority.begin_startup_observation()
        bus = getattr(self._hass, "bus", None)
        listen = getattr(bus, "async_listen", None)
        if not callable(listen):
            self._authority.mark_unhealthy()
            return self.cancel
        self._unsubscribers.append(listen("call_service", self.service_event))
        self._unsubscribers.append(listen("state_changed", self.state_event))
        return self.cancel

    async def async_complete_startup(self) -> None:
        """Drain room-only events before removing the temporary dispatch deny."""
        if self._managed_ready:
            return
        try:
            while True:
                ambiguous_domains = self._ambiguous_startup_domains
                self._ambiguous_startup_domains = set()
                for domain in ambiguous_domains:
                    self._authority.mark_selector_uncertain(domain)
                managed = frozenset(self._managed_entities())
                batch = self._startup_evidence
                self._startup_evidence = []
                for evidence in batch:
                    if evidence.entity_id in managed:
                        self._capture_evidence(evidence, schedule_drain=False)
                await self._authority.async_drain()
                # An event may arrive while the verified save is in flight.
                # The loop drains that new generation before opening dispatch.
                if not self._startup_evidence and not self._ambiguous_startup_domains:
                    self._managed_ready = True
                    self._authority.finish_startup_observation(managed)
                    return
        except Exception:
            self._authority.mark_unhealthy()
            raise

    def cancel(self) -> None:
        # Closing stops new scheduling AND new captures: after unload the
        # authority must not receive (or persist) further actions
        # (SEC-REPAIR-02). Already captured actions are flushed once by a
        # dedicated final drain that bypasses the closed flag (it runs on
        # the live loop during unload), so nothing captured is lost.
        self._closed = True
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        if self._loop is not None and self._loop.is_running():
            try:
                task = self._authority.async_drain()
                self._loop.call_soon_threadsafe(
                    self._create_final_drain_task, task
                )
            except Exception:
                _LOGGER.exception(
                    "final authority drain could not be scheduled on unload"
                )

    def set_automatic_contexts(self, contexts: object) -> None:
        """Attach scenario attribution before its first automatic subscription."""
        self._automatic_contexts = contexts

    def _is_automatic(self, context: object, entity_id: str, action: str) -> bool:
        match = getattr(self._automatic_contexts, "match", None)
        if callable(match) and match(context, entity_id, action) is not None:
            return True
        room = self._room_executor()
        owns = getattr(room, "owns_service_context", None)
        return bool(callable(owns) and owns(context, entity_id, action))

    def _capture_evidence(
        self, evidence: _StartupEvidence, *, schedule_drain: bool = True
    ) -> None:
        if self._closed:
            return
        if not evidence.context_id:
            # A state event still carries a HA event id in most installations.
            # Without a stable identity two deliveries cannot be distinguished.
            self._authority.mark_unhealthy()
            return
        fingerprint = (
            f"{evidence.source}:{evidence.entity_id}:"
            f"{evidence.context_id}:{evidence.event_identity}"
        )
        try:
            changed = self._authority.capture(
                evidence.entity_id, evidence.action, evidence.source, fingerprint,
                observed_at_ms=evidence.observed_at_ms,
            )
        except Exception:
            self._authority.mark_unhealthy()
            _LOGGER.exception("manual light action could not be captured")
            return
        if changed and schedule_drain:
            task = self._authority.async_drain()
            self._schedule_drain(task)

    def _schedule_drain(self, task) -> None:
        """Schedule the drain coroutine on the captured event loop.

        Safe to call from any thread (live repair 2026-09-29). A closed
        listener never creates the task. Without a captured loop: if this
        call is already running on an event loop thread, schedule on it;
        from a true worker thread fail closed — the coroutine is closed
        and the authority is marked unhealthy, so manual light automation
        stays conservatively denied (SEC-REPAIR-01/02R1)."""
        if self._closed:
            task.close()
            return
        loop = getattr(self, "_loop", None)
        if loop is None or not loop.is_running():
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                task.close()
                self._authority.mark_unhealthy()
                _LOGGER.warning(
                    "authority drain skipped: no event loop is running; "
                    "manual light automation stays denied"
                )
                return
        try:
            loop.call_soon_threadsafe(self._create_drain_task, task)
        except RuntimeError:
            task.close()
            self._authority.mark_unhealthy()
            _LOGGER.warning(
                "authority drain could not be scheduled; "
                "manual light automation stays denied"
            )

    def _create_drain_task(self, task) -> None:
        if self._closed:
            task.close()
            return
        try:
            create_task = getattr(self._hass, "async_create_task", None)
            if callable(create_task):
                created = create_task(task)
            else:
                created = asyncio.create_task(task)
        except Exception:
            task.close()
            self._authority.mark_unhealthy()
            _LOGGER.exception(
                "authority drain could not be scheduled; "
                "manual light automation stays denied"
            )
            return
        add_done = getattr(created, "add_done_callback", None)
        if callable(add_done):
            self._drain_tasks.add(created)
            add_done(self._drain_tasks.discard)

    def _create_final_drain_task(self, task) -> None:
        """The cancel-time flush: persists already captured actions even
        though the listener is closed (SEC-REPAIR-02R1). Runs on the live
        loop during unload."""
        try:
            create_task = getattr(self._hass, "async_create_task", None)
            if callable(create_task):
                created = create_task(task)
            else:
                created = asyncio.create_task(task)
        except Exception:
            task.close()
            _LOGGER.exception("final authority drain could not be created")
            return
        self._drain_tasks.add(created)

    def _capture_or_buffer(
        self, entity_id: str, action: str, context: object, source: str,
        *, event_identity: object,
    ) -> None:
        context_id = getattr(context, "id", None)
        evidence = _StartupEvidence(
            entity_id, action, source,
            context_id if isinstance(context_id, str) else None,
            str(event_identity),
            int(time.time() * 1000),
        )
        if not self._managed_ready:
            if len(self._startup_evidence) >= MAX_STARTUP_EVENTS:
                # Lost startup attribution is a real uncertainty, not a room
                # with no manual action.
                self._authority.mark_unhealthy()
                return
            self._startup_evidence.append(evidence)
            return
        self._capture_evidence(evidence)

    def service_event(self, event: object) -> None:
        data = getattr(event, "data", None)
        if not isinstance(data, Mapping) or data.get("domain") not in {"light", "switch"}:
            return
        domain = data["domain"]
        service = data.get("service")
        if service not in {"turn_on", "turn_off", "toggle"}:
            return
        service_data = data.get("service_data")
        # HA event payloads can carry targeting at the outer level as well as
        # inside service_data.  An outer target makes the effective scope
        # ambiguous even when service_data happens to contain an entity_id.
        entities = _exact_service_entities(
            service_data, domain, outer_target=data.get("target")
        )
        if entities is None:
            self._mark_selector_uncertain(domain)
            return
        managed = frozenset(self._managed_entities()) if self._managed_ready else None
        action = {"turn_on": "on", "turn_off": "off", "toggle": "unknown"}[service]
        for entity_id in entities:
            if (
                entity_id.startswith(f"{domain}.")
                and (managed is None or entity_id in managed)
                and not self._is_automatic(
                    getattr(event, "context", None), entity_id, action
                )
            ):
                context = getattr(event, "context", None)
                context_id = getattr(context, "id", None)
                if isinstance(context_id, str):
                    self._service_actions[(entity_id, context_id)] = action
                    if len(self._service_actions) > 512:
                        self._service_actions.pop(next(iter(self._service_actions)))
                self._capture_or_buffer(
                    entity_id, action, context, "service",
                    event_identity=getattr(event, "time_fired", None) or id(event),
                )

    def _mark_selector_uncertain(self, domain: str) -> None:
        """Preserve a broad service's domain without inventing target actions."""
        if self._managed_ready:
            self._authority.mark_selector_uncertain(domain)
            return
        self._ambiguous_startup_domains.add(domain)

    def state_event(self, event: object) -> None:
        data = getattr(event, "data", None)
        if not isinstance(data, Mapping):
            return
        entity_id = data.get("entity_id")
        if (
            not isinstance(entity_id, str)
            or not entity_id.startswith(("light.", "switch."))
            or (
                self._managed_ready
                and entity_id not in frozenset(self._managed_entities())
            )
        ):
            return
        old_state, new_state = data.get("old_state"), data.get("new_state")
        if old_state is None or new_state is None:
            return
        action = getattr(new_state, "state", None)
        if (
            action not in {"on", "off"}
            or action == getattr(old_state, "state", None)
            or not _is_fresh_light_transition(old_state, new_state)
        ):
            return
        context = getattr(event, "context", None) or getattr(new_state, "context", None)
        context_id = getattr(context, "id", None)
        if isinstance(context_id, str) and (
            self._service_actions.get((entity_id, context_id)) in {action, "unknown"}
        ):
            return
        if not self._is_automatic(context, entity_id, action):
            self._capture_or_buffer(
                entity_id, action, context, "state",
                event_identity=getattr(new_state, "last_changed", None)
                or getattr(new_state, "last_updated", None),
            )


def _exact_service_entities(
    service_data: object, domain: str, *, outer_target: object = None,
) -> tuple[str, ...] | None:
    """Accept only an explicit, non-broad entity set for a service call."""
    if outer_target is not None or not isinstance(service_data, Mapping):
        return None
    if _has_target_selector(service_data):
        return None
    raw_entities = service_data.get("entity_id")
    if isinstance(raw_entities, str):
        entities = (raw_entities,)
    elif isinstance(raw_entities, list) and raw_entities and all(
        isinstance(item, str) for item in raw_entities
    ):
        entities = tuple(raw_entities)
    else:
        return None
    if any(
        not _EXACT_ENTITY_ID.fullmatch(entity_id)
        or not entity_id.startswith(f"{domain}.")
        or entity_id == f"{domain}.all"
        for entity_id in entities
    ):
        return None
    return entities


def _has_target_selector(service_data: Mapping[object, object]) -> bool:
    """Reject broad and future selector shapes without rejecting light options."""
    for raw_key in service_data:
        if not isinstance(raw_key, str):
            return True
        key = raw_key.lower()
        if key == "entity_id":
            continue
        if (
            key in {"target", "targets", "entity_ids"}
            or key.startswith("target_")
            or key.endswith("_id")
        ):
            return True
    return False
