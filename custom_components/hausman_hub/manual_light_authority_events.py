"""Synchronous HA event boundary for shared manual light authority."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from .application.manual_light_authority import ManualLightAuthority
from .manual_light_off_protection_events import _is_fresh_light_transition


_LOGGER = logging.getLogger(__name__)
MAX_STARTUP_EVENTS = 512


@dataclass(frozen=True, slots=True)
class _StartupEvidence:
    entity_id: str
    action: str
    source: str
    context_id: str | None
    event_identity: str


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
        self._unsubscribers: list[Callable[[], None]] = []
        self._service_actions: dict[tuple[str, str], str] = {}
        self._managed_ready = True
        self._startup_evidence: list[_StartupEvidence] = []
        self._ambiguous_startup_domains: set[str] = set()

    def start(self, *, buffer_startup: bool = False) -> Callable[[], None]:
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
                managed = frozenset(self._managed_entities())
                if any(
                    entity.startswith(f"{domain}.")
                    for domain in self._ambiguous_startup_domains
                    for entity in managed
                ):
                    self._authority.mark_unhealthy()
                batch = self._startup_evidence
                self._startup_evidence = []
                for evidence in batch:
                    if evidence.entity_id in managed:
                        self._capture_evidence(evidence, schedule_drain=False)
                await self._authority.async_drain()
                # An event may arrive while the verified save is in flight.
                # The loop drains that new generation before opening dispatch.
                if not self._startup_evidence:
                    self._managed_ready = True
                    self._authority.finish_startup_observation()
                    return
        except Exception:
            self._authority.mark_unhealthy()
            raise

    def cancel(self) -> None:
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()

    def set_automatic_contexts(self, contexts: object) -> None:
        """Attach scenario attribution before its first automatic subscription."""
        self._automatic_contexts = contexts

    def _is_automatic(self, context: object, entity_id: str, action: str) -> bool:
        match = getattr(self._automatic_contexts, "match", None)
        if callable(match) and match(context, entity_id, action) is not None:
            return True
        room = self._room_executor()
        owns = getattr(room, "owns_service_context", None)
        return bool(callable(owns) and owns(context))

    def _capture_evidence(
        self, evidence: _StartupEvidence, *, schedule_drain: bool = True
    ) -> None:
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
                evidence.entity_id, evidence.action, evidence.source, fingerprint
            )
        except Exception:
            self._authority.mark_unhealthy()
            _LOGGER.exception("manual light action could not be captured")
            return
        if changed and schedule_drain:
            create_task = getattr(self._hass, "async_create_task", None)
            task = self._authority.async_drain()
            if callable(create_task):
                create_task(task)
            else:
                asyncio.create_task(task)

    def _capture_or_buffer(
        self, entity_id: str, action: str, context: object, source: str,
        *, event_identity: object,
    ) -> None:
        context_id = getattr(context, "id", None)
        evidence = _StartupEvidence(
            entity_id, action, source,
            context_id if isinstance(context_id, str) else None,
            str(event_identity),
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
        if not isinstance(service_data, Mapping):
            if not self._managed_ready:
                self._ambiguous_startup_domains.add(domain)
            elif any(entity.startswith(f"{domain}.") for entity in self._managed_entities()):
                self._authority.mark_unhealthy()
            return
        raw_entities = service_data.get("entity_id")
        if isinstance(raw_entities, str):
            entities = (raw_entities,)
        elif isinstance(raw_entities, (list, tuple)) and all(
            isinstance(item, str) for item in raw_entities
        ):
            entities = tuple(raw_entities)
        else:
            # An area, device or selector may include managed lights. The
            # scope is unknown at this boundary, so close every light route.
            if not self._managed_ready:
                self._ambiguous_startup_domains.add(domain)
            elif any(entity.startswith(f"{domain}.") for entity in self._managed_entities()):
                self._authority.mark_unhealthy()
            return
        if any(not entity_id.startswith(f"{domain}.") for entity_id in entities):
            # HA also accepts broad strings such as ``entity_id: all``.
            if not self._managed_ready:
                self._ambiguous_startup_domains.add(domain)
            elif any(entity.startswith(f"{domain}.") for entity in self._managed_entities()):
                self._authority.mark_unhealthy()
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
