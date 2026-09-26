"""Synchronous HA event boundary for shared manual light authority."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable, Mapping

from .application.manual_light_authority import ManualLightAuthority
from .manual_light_off_protection_events import _is_fresh_light_transition


_LOGGER = logging.getLogger(__name__)


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

    def start(self) -> Callable[[], None]:
        bus = getattr(self._hass, "bus", None)
        listen = getattr(bus, "async_listen", None)
        if not callable(listen):
            self._authority.mark_unhealthy()
            return self.cancel
        self._unsubscribers.append(listen("call_service", self.service_event))
        self._unsubscribers.append(listen("state_changed", self.state_event))
        return self.cancel

    def cancel(self) -> None:
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()

    def _is_automatic(self, context: object, entity_id: str, action: str) -> bool:
        match = getattr(self._automatic_contexts, "match", None)
        if callable(match) and match(context, entity_id, action) is not None:
            return True
        room = self._room_executor()
        owns = getattr(room, "owns_service_context", None)
        return bool(callable(owns) and owns(context))

    def _capture(
        self, entity_id: str, action: str, context: object, source: str,
        *, event_identity: object,
    ) -> None:
        context_id = getattr(context, "id", None)
        if not isinstance(context_id, str) or not context_id:
            # A state event still carries a HA event id in most installations.
            # Without a stable identity two deliveries cannot be distinguished.
            self._authority.mark_unhealthy()
            return
        fingerprint = f"{source}:{entity_id}:{context_id}:{event_identity}"
        try:
            changed = self._authority.capture(entity_id, action, source, fingerprint)
        except Exception:
            self._authority.mark_unhealthy()
            _LOGGER.exception("manual light action could not be captured")
            return
        if changed:
            create_task = getattr(self._hass, "async_create_task", None)
            task = self._authority.async_drain()
            if callable(create_task):
                create_task(task)
            else:
                asyncio.create_task(task)

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
            if any(entity.startswith(f"{domain}.") for entity in self._managed_entities()):
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
            if any(entity.startswith(f"{domain}.") for entity in self._managed_entities()):
                self._authority.mark_unhealthy()
            return
        managed = frozenset(self._managed_entities())
        action = {"turn_on": "on", "turn_off": "off", "toggle": "unknown"}[service]
        for entity_id in entities:
            if entity_id in managed and not self._is_automatic(
                getattr(event, "context", None), entity_id, action
            ):
                context = getattr(event, "context", None)
                context_id = getattr(context, "id", None)
                if isinstance(context_id, str):
                    self._service_actions[(entity_id, context_id)] = action
                    if len(self._service_actions) > 512:
                        self._service_actions.pop(next(iter(self._service_actions)))
                self._capture(
                    entity_id, action, context, "service",
                    event_identity=getattr(event, "time_fired", None) or id(event),
                )

    def state_event(self, event: object) -> None:
        data = getattr(event, "data", None)
        if not isinstance(data, Mapping):
            return
        entity_id = data.get("entity_id")
        if entity_id not in frozenset(self._managed_entities()):
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
            self._capture(
                entity_id, action, context, "state",
                event_identity=getattr(new_state, "last_changed", None)
                or getattr(new_state, "last_updated", None),
            )
