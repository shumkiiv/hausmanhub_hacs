"""Synchronous HA event boundary for shared manual light authority."""

from __future__ import annotations

import asyncio
import logging
import re
import time
import math
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .application.manual_light_authority import ManualLightAuthority
from .application.power_evidence import state_is_restored_or_cached
from .domain.manual_light_off_protection import ScenarioCommandAttribution
from .manual_light_off_protection_events import _is_fresh_light_transition

if TYPE_CHECKING:
    from .domain.room_lighting import RoomLightingConfig


try:
    from homeassistant.core import callback as _ha_callback
except ModuleNotFoundError:
    def _ha_callback(func):
        setattr(func, "_hass_callback", True)
        return func


_LOGGER = logging.getLogger(__name__)
MAX_STARTUP_EVENTS = 512
_EXACT_ENTITY_ID = re.compile(r"^(?:light|switch)\.[a-z0-9_]+$")


def _finite_parameter(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _controlled_parameters(state: object) -> dict[str, object]:
    """Compare active controls, never diagnostic or derived colour attributes."""
    attrs = getattr(state, "attributes", None)
    if not isinstance(attrs, Mapping):
        return {}
    result: dict[str, object] = {}
    brightness = _finite_parameter(attrs.get("brightness"))
    if brightness is not None and 0 <= brightness <= 255:
        result["brightness"] = brightness
    mode = attrs.get("color_mode")
    if mode is not None and not isinstance(mode, str):
        return result
    if mode in {None, "color_temp"}:
        kelvin = _finite_parameter(attrs.get("color_temp_kelvin"))
        if kelvin is None:
            mireds = _finite_parameter(attrs.get("color_temp"))
            if mireds is not None and mireds > 0:
                kelvin = 1_000_000 / mireds
        if kelvin is not None and kelvin > 0:
            result["color"] = ("color_temp", round(kelvin))
    elif mode in {"brightness", "onoff", "white"}:
        result["color"] = (mode, None)
    else:
        lengths = {"hs": 2, "xy": 2, "rgb": 3, "rgbw": 4, "rgbww": 5}
        value = attrs.get(f"{mode}_color")
        if mode in lengths and isinstance(value, (tuple, list)) and len(value) == lengths[mode]:
            parts = tuple(_finite_parameter(part) for part in value)
            if all(part is not None for part in parts):
                result["color"] = (mode, parts)
    return result


def _parameter_action(old_state: object, new_state: object) -> str | None:
    old, new = _controlled_parameters(old_state), _controlled_parameters(new_state)
    if any(name in new and new[name] != value for name, value in old.items()):
        return "on"
    if old.keys() - new.keys():
        return "unknown"  # Lost evidence is not a proved manual turn-on.
    return None


def _fresh_parameter_observation(state: object) -> datetime | None:
    if state_is_restored_or_cached(state):
        return None
    for name in ("last_updated", "last_reported"):
        observed = getattr(state, name, None)
        if observed is None:
            continue
        if not isinstance(observed, datetime):
            return None
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - observed).total_seconds()
        return observed if 0 <= age <= 300 else None
    return None


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
        priority_duration: Callable[[str, str], int] | None = None,
        single_entity_evidence: Callable[[Iterable[str]], frozenset[str]] | None = None,
    ) -> None:
        self._hass = hass
        self._authority = authority
        self._managed_entities = managed_entities
        self._automatic_contexts = automatic_contexts
        self._room_executor = room_executor
        self._priority_duration = priority_duration
        self._single_entity_evidence = single_entity_evidence
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
        return self.automatic_attribution(context, entity_id, action) is not None

    def automatic_attribution(
        self, context: object, entity_id: str, action: str,
    ) -> ScenarioCommandAttribution | None:
        """Share exact command provenance with every manual-state consumer."""
        match = getattr(self._automatic_contexts, "match", None)
        matched = match(context, entity_id, action) if callable(match) else None
        if matched is not None:
            return ScenarioCommandAttribution(
                source=matched.origin, attribution_id=matched.request_id or entity_id,
            )
        room = self._room_executor()
        owns = getattr(room, "owns_service_context", None)
        if callable(owns) and owns(context, entity_id, action):
            return ScenarioCommandAttribution(
                source="automatic", attribution_id=getattr(context, "id", None) or entity_id,
            )
        return None

    def _capture_evidence(
        self, evidence: _StartupEvidence, *, schedule_drain: bool = True
    ) -> None:
        if self._closed:
            return
        if not evidence.context_id:
            # The exact entity is known, but the action cannot be attributed.
            # Persist an indefinite unknown-action fence for this entity (and
            # its proven peers), rather than blocking unrelated components.
            try:
                synthetic_id = f"unidentified-{uuid.uuid4()}"
            except Exception:
                self._authority.mark_unhealthy()
                _LOGGER.exception("manual light evidence identity could not be created")
                return
            evidence = _StartupEvidence(
                evidence.entity_id, "unknown", evidence.source,
                synthetic_id, evidence.event_identity, evidence.observed_at_ms,
            )
        fingerprint = (
            f"{evidence.source}:{evidence.entity_id}:"
            f"{evidence.context_id}:{evidence.event_identity}"
        )
        try:
            changed = self._authority.capture(
                evidence.entity_id, evidence.action, evidence.source, fingerprint,
                observed_at_ms=evidence.observed_at_ms,
                **({"duration_ms": self._priority_duration(evidence.entity_id, evidence.action)}
                   if self._priority_duration is not None and evidence.action in {"on", "off"} else {}),
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

    @_ha_callback
    def service_event(self, event: object) -> None:
        data = getattr(event, "data", None)
        if not isinstance(data, Mapping) or data.get("domain") not in {"light", "switch"}:
            return
        domain = data["domain"]
        service = data.get("service")
        if service not in {"turn_on", "turn_off", "toggle"}:
            return
        try:
            managed = frozenset(self._managed_entities()) if self._managed_ready else None
        except Exception:
            _LOGGER.error("Manual light authority: unable to read managed entities for service event")
            self._authority.mark_unhealthy()
            return
        service_data = data.get("service_data")
        # HA event payloads can carry targeting at the outer level as well as
        # inside service_data.  An outer target makes the effective scope
        # ambiguous even when service_data happens to contain an entity_id.
        direct_entities = _exact_service_entities(
            service_data, domain, outer_target=data.get("target")
        )
        incomplete_selection = False
        entities = direct_entities
        if direct_entities is not None:
            # A direct entity may be an HA group. Resolve its members before
            # the service handler can forward commands to them. A failed
            # resolution must not erase the exact direct evidence itself.
            expanded = _resolve_ha_selector(self._hass, service_data, domain, "entity_id")
            if expanded is None:
                incomplete_selection = True
            else:
                entities = tuple(sorted(set(direct_entities) | set(expanded)))
        if entities is None and data.get("target") is None:
            entities = _resolve_device_selector(self._hass, service_data, domain)
            if entities is None and self._managed_ready:
                entities = _resolve_area_selector(self._hass, service_data, domain, managed)
        if entities is not None and not incomplete_selection:
            # Current graph is read per event so a provenance change between
            # events can never extend an earlier allowance.
            graph = self._verified_graph()
            expanded_entities = _expand_group_members(self._hass, entities, domain, graph)
            if expanded_entities is None:
                # The full selection stays uncertain, but exact direct
                # evidence is a fact of the call and is kept for attribution.
                incomplete_selection = True
            else:
                entities = expanded_entities
        if incomplete_selection or entities is None:
            self._mark_selector_uncertain(domain)
            if not direct_entities:
                return
            # Only the exact direct evidence survives an uncertain
            # selection: presumed group members are never captured.
            entities = tuple(dict.fromkeys(direct_entities))
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

    def _verified_graph(self):
        """Re-check single-device evidence right before group expansion.

        Events may only REMOVE confirmation: the runtime graph builder adds
        it from the current physical scheme. A lost registry proof is
        published back so every consumer sees the narrowed graph. There is
        deliberately no await between the re-check, the expansion and the
        manual capture.
        """
        graph = getattr(self._authority, "related_sources", None)
        provider = self._single_entity_evidence
        proven = getattr(graph, "proven_entities", frozenset())
        if (graph is None or not getattr(graph, "ready", False)
                or not proven or provider is None):
            return graph
        try:
            verified = frozenset(provider(proven))
        except Exception:  # No evidence means no bypass, never an error path.
            verified = frozenset()
        if proven <= verified:
            return graph
        from .application.related_light_sources import RelatedLightSources

        narrowed = RelatedLightSources(True, graph.components, proven & verified)
        try:
            self._authority.set_related_sources(narrowed)
        except Exception:
            return narrowed
        return getattr(self._authority, "related_sources", None) or narrowed

    def _mark_selector_uncertain(self, domain: str) -> None:
        """Preserve a broad service's domain without inventing target actions."""
        if self._managed_ready:
            self._authority.mark_selector_uncertain(domain)
            return
        self._ambiguous_startup_domains.add(domain)

    @_ha_callback
    def state_event(self, event: object) -> None:
        data = getattr(event, "data", None)
        if not isinstance(data, Mapping):
            return
        entity_id = data.get("entity_id")
        if not isinstance(entity_id, str) or not entity_id.startswith(("light.", "switch.")):
            return
        if self._managed_ready:
            try:
                managed = frozenset(self._managed_entities())
            except Exception:
                _LOGGER.error("Manual light authority: unable to read managed entities for state event")
                self._authority.mark_unhealthy()
                return
            if entity_id not in managed:
                return
        old_state, new_state = data.get("old_state"), data.get("new_state")
        if old_state is None or new_state is None:
            return
        action = getattr(new_state, "state", None)
        parameter_action = None
        parameter_observed = None
        if action == getattr(old_state, "state", None):
            if action != "on" or not entity_id.startswith("light."):
                return
            parameter_action = _parameter_action(old_state, new_state)
            parameter_observed = _fresh_parameter_observation(new_state)
            if parameter_action is None or parameter_observed is None:
                return
        if (
            action not in {"on", "off"}
            or (parameter_action is None and not _is_fresh_light_transition(old_state, new_state))
        ):
            return
        context = getattr(event, "context", None) or getattr(new_state, "context", None)
        context_id = getattr(context, "id", None)
        if isinstance(context_id, str) and (
            self._service_actions.get((entity_id, context_id)) in {action, "unknown"}
        ):
            return
        if not self._is_automatic(context, entity_id, action):
            identity = (
                f"{parameter_observed}|{_controlled_parameters(new_state)}"
                if parameter_observed is not None else
                getattr(new_state, "last_changed", None) or getattr(new_state, "last_updated", None)
            )
            self._capture_or_buffer(
                entity_id, parameter_action or action, context, "state",
                event_identity=identity,
            )


def _expand_group_members(
    hass: object, entities: Iterable[str], domain: str,
    graph: object | None = None,
) -> tuple[str, ...] | None:
    """Include nested HA light/switch group members before the service runs."""
    try:
        states = hass.states
        pending = list(entities)
        seen: set[str] = set()
        while pending:
            entity = pending.pop()
            if entity in seen:
                continue
            if len(seen) >= 512:
                return None
            seen.add(entity)
            state = states.get(entity)
            attributes = getattr(state, "attributes", None)
            if isinstance(attributes, Mapping) and "entity_id" in attributes:
                # Known group composition keeps the unchanged fail-closed path,
                # including a group whose own state is not on/off.
                if getattr(state, "state", None) not in {"on", "off"}:
                    return None
                members = attributes["entity_id"]
                if not isinstance(members, (list, tuple)) or not members:
                    return None
                if any(not isinstance(member, str) or not _EXACT_ENTITY_ID.fullmatch(member)
                       or not member.startswith(f"{domain}.") for member in members):
                    return None
                pending.extend(members)
                continue
            state_value = getattr(state, "state", None)
            if state_value not in {"on", "off"}:
                # Unknown/unavailable without a known composition stays closed
                # unless the complete physical scheme proved this entity's scope.
                if (state_value in {"unknown", "unavailable"}
                        and graph is not None
                        and getattr(graph, "ready", False)
                        and entity in getattr(graph, "proven_entities", frozenset())):
                    continue
                return None
            if not isinstance(attributes, Mapping):
                return None
        return tuple(sorted(seen))
    except Exception:  # Any state-read failure must preserve the uncertainty barrier.
        return None


def _resolve_ha_selector(hass: object, service_data: object, domain: str, key: str) -> tuple[str, ...] | None:
    """Delegate selection, including child and composite devices, to HA itself."""
    if not isinstance(service_data, Mapping) or key not in service_data:
        return None
    other = {name: value for name, value in service_data.items() if name != key}
    if _has_target_selector(other) or "entity_id" in other:
        return None
    raw = service_data[key]
    ids = (raw,) if isinstance(raw, str) else tuple(raw) if isinstance(raw, list) else ()
    if not ids or any(not isinstance(item, str) or not item for item in ids):
        return None
    try:
        from homeassistant.helpers import target
        selection = target.TargetSelection({key: list(ids)})
        selected = target.async_extract_referenced_entity_ids(hass, selection)
        if any(getattr(selected, name, None) is None or getattr(selected, name)
               for name in ("missing_devices", "missing_areas", "missing_floors", "missing_labels")):
            return None
        result = selected.referenced | selected.indirectly_referenced
        if any(not isinstance(entity, str) for entity in result):
            return None
        result = {entity for entity in result if entity.startswith(f"{domain}.")}
        if any(not _EXACT_ENTITY_ID.fullmatch(entity) for entity in result):
            return None
        return tuple(sorted(result))
    except Exception:  # Registry failures must preserve the uncertainty barrier.
        return None


def _resolve_device_selector(hass: object, service_data: object, domain: str) -> tuple[str, ...] | None:
    return _resolve_ha_selector(hass, service_data, domain, "device_id")


def _resolve_area_selector(hass: object, service_data: object, domain: str, managed_entities: Iterable[str]) -> tuple[str, ...] | None:
    return _resolve_ha_selector(hass, service_data, domain, "area_id")


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
