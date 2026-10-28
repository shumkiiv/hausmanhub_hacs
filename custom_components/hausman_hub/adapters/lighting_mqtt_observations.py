"""Private, passive Zigbee report input for the bounded lighting rollout."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from ..application.lighting_observation import (
    ObservedLightState, PhysicalLightObservation, PhysicalBrightnessObservation,
    PhysicalColorObservation,
)

_LOGGER = logging.getLogger(__name__)
REPORT_TOPIC = "zigbee2mqtt/hausman/light_reports/raw_v1"
BOUNDARY_TOPIC = "zigbee2mqtt/hausman/light_reports/boundary_v1"
BRIDGE_TOPIC = "zigbee2mqtt/bridge/state"
FRESHNESS_MS = 300_000
MAX_PAYLOAD_BYTES = 2048
MAX_CLOSED_SESSIONS = 32
MQTT_ENTRY = "264b9f98e116f9430a713d5e8f9ad1bb"
_REPORT_KEYS = frozenset({
    "schema", "source", "ieee", "endpoint", "attribute", "value", "type",
    "transaction", "observed_at_ms", "session", "sequence",
})
_BOUNDARY_KEYS = frozenset({"schema", "source", "session", "sequence", "observed_at_ms"})


@dataclass(frozen=True, slots=True)
class LightReportBinding:
    entity_id: str
    ieee: str
    endpoint: int
    unique_id: str


ROLLOUT_BINDINGS = (
    LightReportBinding("switch.0x603d61fffe761c63_1", "0x603d61fffe761c63", 1,
                       "0x603d61fffe761c63_switch_1_zigbee2mqtt"),
    LightReportBinding("switch.0x603d61fffe761c63_2", "0x603d61fffe761c63", 2,
                       "0x603d61fffe761c63_switch_2_zigbee2mqtt"),
    LightReportBinding("switch.0xa4c138ffecbc07b5_l1", "0xa4c138ffecbc07b5", 1,
                       "0xa4c138ffecbc07b5_switch_l1_zigbee2mqtt"),
    LightReportBinding("light.liustra_tambur", "0xa4c138784e5cbcd1", 1,
                       "0xa4c138784e5cbcd1_light_zigbee2mqtt"),
    LightReportBinding("light.0xa4c138d69d102803", "0xa4c138d69d102803", 1,
                       "0xa4c138d69d102803_light_zigbee2mqtt"),
    LightReportBinding("switch.0x603d61fffe759363_1", "0x603d61fffe759363", 1,
                       "0x603d61fffe759363_switch_1_zigbee2mqtt"),
    LightReportBinding("switch.0x603d61fffe767766_1", "0x603d61fffe767766", 1,
                       "0x603d61fffe767766_switch_1_zigbee2mqtt"),
    LightReportBinding("switch.0xacbac0fffebbe3c4_1", "0xacbac0fffebbe3c4", 1,
                       "0xacbac0fffebbe3c4_switch_1_zigbee2mqtt"),
    LightReportBinding("switch.0xa4c1385af46163eb", "0xa4c1385af46163eb", 1,
                       "0xa4c1385af46163eb_switch_zigbee2mqtt"),
)
PROPERTY_ENTITIES = frozenset({"light.liustra_tambur", "light.0xa4c138d69d102803"})


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _safe_integer(value: object, minimum: int = 0) -> bool:
    return type(value) is int and minimum <= value <= 2**53 - 1


def _session_id(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 36:
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


class LightingMqttObservations:
    """Volatile evidence only. No services, state writes, decisions or storage."""

    def __init__(self, native_reader: Callable[[str], object | None], *,
                 now_ms: Callable[[], int] | None = None,
                 bindings: tuple[LightReportBinding, ...] = ROLLOUT_BINDINGS) -> None:
        self._native_reader = native_reader
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._scope = frozenset(b.entity_id for b in bindings)
        self._bindings = {(b.ieee, b.endpoint): b for b in bindings}
        self._by_entity = {b.entity_id: b for b in bindings}
        self._registry_reader: Callable[[str], object | None] | None = None
        self._enabled: frozenset[str] = frozenset()
        self._unsubscribes: list[Callable[[], None]] = []
        self._reports: dict[str, PhysicalLightObservation] = {}
        self._brightness_reports: dict[str, PhysicalBrightnessObservation] = {}
        self._color_reports: dict[str, PhysicalColorObservation] = {}
        self._closed: dict[str, None] = {}
        self._session: str | None = None
        self._sequence = 0
        self._observed_at_ms = 0
        self._last_signature: tuple[object, ...] | None = None
        self._floor_ms = self._now_ms()
        self._connected = False
        self._started = False
        self._boundary_generation = 0
        self._listeners: list[tuple[frozenset[str], Callable[[str | None], None]]] = []
        self.reason = "not_started"

    def subscribe(
        self, entities: frozenset[str], listener: Callable[[str | None], None],
    ) -> Callable[[], None]:
        """Notify evidence changes only; the consumer retains every decision."""
        if not entities or not entities <= self._scope:
            raise ValueError("observation subscription outside rollout")
        entry = (entities, listener)
        self._listeners.append(entry)

        def unsubscribe() -> None:
            if entry in self._listeners:
                self._listeners.remove(entry)
        return unsubscribe

    def _notify(self, entity: str | None) -> None:
        for entities, listener in tuple(self._listeners):
            if entity is None or entity in entities:
                try:
                    listener(entity)
                except Exception:
                    _LOGGER.exception("Physical lighting evidence subscriber failed")

    @property
    def boundary_generation(self) -> int:
        """A refresh must not cross any observation or connection boundary."""
        return self._boundary_generation

    def refresh_ready(self, entity_ids: frozenset[str]) -> bool:
        return (self._started and self._connected and bool(entity_ids)
                and all(entity in self._by_entity
                        and self._binding_matches(self._by_entity[entity])
                        for entity in entity_ids))

    async def async_start(self, hass: object) -> None:
        from homeassistant.components import mqtt
        from homeassistant.core import callback
        from homeassistant.helpers import entity_registry as er

        registry = er.async_get(hass)
        self._registry_reader = registry.async_get
        enabled = []
        for binding in self._bindings.values():
            entity = registry.async_get(binding.entity_id)
            if (entity is not None and entity.platform == "mqtt"
                and entity.config_entry_id == MQTT_ENTRY
                and entity.unique_id == binding.unique_id
                and entity.disabled_by is None):
                enabled.append(binding.entity_id)
        self._enabled = frozenset(enabled)
        self._started = True
        self.invalidate("startup")

        @callback
        def receive(message: object) -> None:
            self.receive(getattr(message, "topic", ""), getattr(message, "payload", None),
                         retained=getattr(message, "retain", True))

        @callback
        def connection(connected: bool) -> None:
            self.connection_changed(connected)

        try:
            self._unsubscribes.append(mqtt.async_subscribe_connection_status(hass, connection))
            self._connected = mqtt.is_connected(hass)
            for topic in (REPORT_TOPIC, BOUNDARY_TOPIC, BRIDGE_TOPIC):
                self._unsubscribes.append(await mqtt.async_subscribe(hass, topic, receive, qos=0))
        except Exception:  # MQTT may not be configured; this room stays fail closed.
            self.stop()
            _LOGGER.warning("Physical lighting input unavailable; selected channels remain unconfirmed")

    def connection_changed(self, connected: bool) -> None:
        self.invalidate("mqtt_connection_boundary")
        self._connected = connected is True

    def _remember_closed(self, session: str) -> None:
        self._closed[session] = None
        if len(self._closed) > MAX_CLOSED_SESSIONS:
            self._closed.pop(next(iter(self._closed)))

    def invalidate(self, reason: str, *, boundary_ms: int | None = None) -> None:
        self._boundary_generation += 1
        if self._session is not None:
            self._remember_closed(self._session)
        boundary = self._now_ms() if boundary_ms is None else boundary_ms
        self._floor_ms = max(self._floor_ms, boundary, self._observed_at_ms)
        self._session = None
        self._sequence = 0
        self._last_signature = None
        self._reports.clear()
        self._brightness_reports.clear()
        self._color_reports.clear()
        self.reason = reason
        self._notify(None)

    def stop(self) -> None:
        self._started = False
        self._connected = False
        self.invalidate("stopped")
        while self._unsubscribes:
            try:
                self._unsubscribes.pop()()
            except Exception:
                _LOGGER.warning("Physical lighting input unsubscribe failed")

    def read_state(self, entity_id: str) -> object | None:
        native = self._native_reader(entity_id)
        if native is None or entity_id not in self._scope:
            return native
        report = self._reports.get(entity_id) if self._started and self._connected else None
        level = self._brightness_reports.get(entity_id) if self._started and self._connected else None
        color = self._color_reports.get(entity_id) if self._started and self._connected else None
        if (report is not None or level is not None or color is not None) and not self._binding_matches(self._by_entity[entity_id]):
            self.invalidate("binding_changed")
            report = None
            level = None
            color = None
        return ObservedLightState(native, report, level, color)

    def _binding_matches(self, binding: LightReportBinding) -> bool:
        if binding.entity_id not in self._enabled:
            return False
        if self._registry_reader is None:
            return True
        try:
            entity = self._registry_reader(binding.entity_id)
            return bool(entity is not None and entity.platform == "mqtt"
                and entity.config_entry_id == MQTT_ENTRY and entity.unique_id == binding.unique_id
                and entity.disabled_by is None)
        except Exception:
            return False

    def receive(self, topic: str, payload: object, *, retained: object) -> None:
        if not self._started:
            return
        if topic == BRIDGE_TOPIC:
            # Both retained startup status and a new bridge lifecycle are boundaries.
            self.invalidate("bridge_boundary")
            return
        if topic not in {REPORT_TOPIC, BOUNDARY_TOPIC} or not self._connected:
            return
        try:
            if retained is not False or not isinstance(payload, (str, bytes)):
                raise ValueError("retained or invalid payload")
            if len(payload) > MAX_PAYLOAD_BYTES:
                raise ValueError("oversize payload")
            raw = payload.encode("utf-8") if isinstance(payload, str) else payload
            if len(raw) > MAX_PAYLOAD_BYTES:
                raise ValueError("oversize payload")
            body = json.loads(raw, object_pairs_hook=_unique_object)
            if not isinstance(body, dict) or type(body.get("schema")) is not int or body["schema"] != 1:
                raise ValueError("invalid schema")
            session, sequence, observed = body.get("session"), body.get("sequence"), body.get("observed_at_ms")
            if not _session_id(session) or not _safe_integer(sequence, 1) or not _safe_integer(observed, 1):
                raise ValueError("invalid revision")
            if topic == BOUNDARY_TOPIC:
                if frozenset(body) != _BOUNDARY_KEYS or body["source"] != "observation_closed":
                    raise ValueError("invalid boundary")
                if session == self._session or self._session is None:
                    self.invalidate("observer_closed")
                self._remember_closed(session)
                return
            if frozenset(body) != _REPORT_KEYS or body["source"] not in {"physical_onoff", "physical_brightness", "physical_color"}:
                raise ValueError("invalid source")
            brightness = body["source"] == "physical_brightness"
            color = body["source"] == "physical_color"
            valid_property = (
                body["attribute"] == "genLevelCtrl.currentLevel"
                and type(body["value"]) is int and 0 <= body["value"] <= 254
                if brightness else (
                    body["attribute"] == "lightingColorCtrl.colorTemperature"
                    and type(body["value"]) is int and 153 <= body["value"] <= 500
                    if color else
                    body["attribute"] == "genOnOff.onOff" and body["value"] in {"ON", "OFF"}
                )
            )
            if (not valid_property or body["type"] not in {"attributeReport", "readResponse"}
                or not isinstance(body["ieee"], str)
                or not _safe_integer(body["endpoint"], 1) or not _safe_integer(body["transaction"])
                or body["transaction"] > 255):
                raise ValueError("invalid physical property")
            binding = self._bindings.get((body["ieee"], body["endpoint"]))
            if binding is None or not self._binding_matches(binding):
                raise ValueError("invalid binding")
            if (brightness or color) and binding.entity_id not in PROPERTY_ENTITIES:
                raise ValueError("light property outside rollout")
            now = self._now_ms()
            if not 0 <= now - observed <= FRESHNESS_MS or observed <= self._floor_ms:
                raise ValueError("outside observed period")
            if session in self._closed:
                return
            signature = (body["ieee"], body["endpoint"], body["attribute"], body["value"], observed, body["type"], body["transaction"])
            if session == self._session:
                if sequence <= self._sequence:
                    if sequence == self._sequence and signature != self._last_signature:
                        self.invalidate("conflicting_sequence")
                    return
                if sequence != self._sequence + 1 or observed < self._observed_at_ms:
                    self.invalidate("sequence_gap")
                    return
            else:
                if self._session is not None:
                    self.invalidate("new_observer_session", boundary_ms=self._observed_at_ms)
                    # New physical evidence must be newer than the closed period.
                    if observed <= self._floor_ms:
                        return
                self._session = session
            self._sequence = sequence
            self._observed_at_ms = observed
            self._last_signature = signature
            if color:
                self._color_reports[binding.entity_id] = PhysicalColorObservation(
                    binding.entity_id, body["value"], observed, session, sequence,
                    binding.ieee, binding.endpoint,
                )
            elif brightness:
                self._brightness_reports[binding.entity_id] = PhysicalBrightnessObservation(
                    binding.entity_id, body["value"], observed, session, sequence,
                    binding.ieee, binding.endpoint,
                )
            else:
                self._reports[binding.entity_id] = PhysicalLightObservation(
                    binding.entity_id, body["value"].lower(), observed, session, sequence,
                    binding.ieee, binding.endpoint,
                )
            self.reason = "physical_report"
            self._notify(binding.entity_id)
        except (ValueError, TypeError, UnicodeError, RecursionError):
            self.invalidate("invalid_report")
