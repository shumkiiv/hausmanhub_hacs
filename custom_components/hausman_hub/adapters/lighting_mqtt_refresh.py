"""Bounded read-only refresh of the already verified lighting rollout channels."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import json
import logging
import time

from .lighting_mqtt_observations import LightingMqttObservations
from ..application.lighting_observation import (
    ObservedLightState, physical_observed_ms, physical_brightness_observed_ms,
    paired_recovery_brightness_observed_ms,
)

REFRESH_TIMEOUT_SECONDS = 8
CHECK_INTERVAL_SECONDS = 0.05
_LOGGER = logging.getLogger(__name__)
# No caller-provided topics or values. These are the existing rollout bindings.
READ_CHANNELS = (
    ("switch.0x603d61fffe761c63_1", "zigbee2mqtt/Выключатель тамбур/get", "state_1"),
    ("switch.0xa4c138ffecbc07b5_l1", "zigbee2mqtt/Зеркало тамбур/get", "state_l1"),
    ("switch.0x603d61fffe761c63_2", "zigbee2mqtt/Выключатель тамбур/get", "state_2"),
    ("light.liustra_tambur", "zigbee2mqtt/Люстра тамбур/get", "state"),
)
SUPPORTED_ENTITIES = frozenset(row[0] for row in READ_CHANNELS)
CORRIDOR_CHANNELS = (
    ("switch.0x603d61fffe759363_1", "zigbee2mqtt/Выключатель малый коридор/get", "state_1"),
    ("light.0xa4c138d69d102803", "zigbee2mqtt/Люстра малый коридор/get", "state"),
)
CORRIDOR_SINGLETON_CHANNELS = (CORRIDOR_CHANNELS[1],)
SHOWER_CHANNELS = (
    ("switch.0x603d61fffe767766_1", "zigbee2mqtt/Выключатель душевая/get", "state_1"),
    ("switch.0xacbac0fffebbe3c4_1", "zigbee2mqtt/Выключатель душевая доп свет/get", "state_1"),
    ("switch.0xa4c1385af46163eb", "zigbee2mqtt/Подсветка шкафа душевая/get", "state"),
)
READ_SCOPES = {frozenset(row[0] for row in channels): channels
               for channels in (READ_CHANNELS, CORRIDOR_CHANNELS, CORRIDOR_SINGLETON_CHANNELS, SHOWER_CHANNELS)}
ALL_READ_CHANNELS = READ_CHANNELS + CORRIDOR_SINGLETON_CHANNELS + SHOWER_CHANNELS
POWER_ENTITY = READ_CHANNELS[2][0]
LIGHT_ENTITY = READ_CHANNELS[3][0]
CORRIDOR_POWER_ENTITY = CORRIDOR_CHANNELS[0][0]
CORRIDOR_LIGHT_ENTITY = CORRIDOR_CHANNELS[1][0]


class LightingMqttRefresh:
    """GET only, no ownership, service commands, persistence or background tasks."""

    zero_pair_entities = frozenset({POWER_ENTITY, LIGHT_ENTITY})

    def __init__(
        self, observations: LightingMqttObservations,
        publish: Callable[[str, str], Awaitable[None]], *,
        now_ms: Callable[[], int] | None = None,
        pause: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._observations = observations
        self._publish = publish
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._pause = pause

    def subscribe_pair(self, listener: Callable[[str | None], None]) -> Callable[[], None]:
        return self._observations.subscribe(self.zero_pair_entities, listener)

    async def refresh_zero_pair(
        self, entities: frozenset[str], is_current: Callable[[], bool],
    ) -> bool:
        """One bounded read of the owner's existing pair, never a SET."""
        if entities != self.zero_pair_entities or not self._observations.refresh_ready(entities):
            return False
        generation = self._observations.boundary_generation

        def current() -> bool:
            return (is_current() and self._observations.boundary_generation == generation
                    and self._observations.refresh_ready(entities))

        try:
            async with asyncio.timeout(REFRESH_TIMEOUT_SECONDS):
                for entity, topic, prop in (READ_CHANNELS[2], READ_CHANNELS[3],
                        (LIGHT_ENTITY, READ_CHANNELS[3][1], "brightness")):
                    if not current():
                        return False
                    before = self._observations.read_state(entity)
                    if not isinstance(before, ObservedLightState):
                        return False
                    level = prop == "brightness"
                    previous = before.brightness_observation if level else before.observation
                    issued_at = self._now_ms()
                    await self._publish(topic, json.dumps({prop: ""}))
                    while current():
                        view = self._observations.read_state(entity)
                        if isinstance(view, ObservedLightState):
                            report = view.brightness_observation if level else view.observation
                            observed = (paired_recovery_brightness_observed_ms(view, now_ms=self._now_ms())
                                        if level else physical_observed_ms(
                                            view, now_ms=self._now_ms(), freshness_ms=300_000))
                            if (report is not None and report != previous
                                    and observed is not None and observed >= issued_at):
                                if not level and entity == POWER_ENTITY and report.value != "on":
                                    return False
                                break
                        await self._pause(CHECK_INTERVAL_SECONDS)
                    else:
                        return False
                return current()
        except (TimeoutError, OSError):
            return False

    async def __call__(self, entities: frozenset[str], is_current: Callable[[], bool]) -> bool:
        channels = READ_SCOPES.get(entities)
        scope = ("tambur" if channels is READ_CHANNELS else
                 "corridor" if channels in {CORRIDOR_CHANNELS, CORRIDOR_SINGLETON_CHANNELS} else
                 "shower" if channels is SHOWER_CHANNELS else "unsupported")
        phase, outcome = "preflight", "unexpected_error"
        channel_index = completed_reads = 0
        bulb_skipped = False
        try:
            if channels is None:
                outcome = "unsupported_scope"
                return False
            if not self._observations.refresh_ready(entities):
                outcome = "receiver_not_ready"
                return False
            generation = self._observations.boundary_generation

            def current() -> bool:
                return (is_current() and self._observations.boundary_generation == generation
                        and self._observations.refresh_ready(entities))

            async def read(entity: str, topic: str, prop: str) -> str | None:
                nonlocal phase, outcome
                if not current():
                    outcome = "current_guard_failed"
                    return None
                before = self._observations.read_state(entity)
                previous = before.observation if isinstance(before, ObservedLightState) else None
                issued_at = self._now_ms()
                phase = "publishing"
                await self._publish(topic, json.dumps({prop: ""}))
                phase = "waiting_report"
                while current():
                    view = self._observations.read_state(entity)
                    if isinstance(view, ObservedLightState):
                        observed = physical_observed_ms(view, now_ms=self._now_ms(), freshness_ms=300_000)
                        if (observed is not None and observed >= issued_at
                                and view.observation != previous):
                            return view.observation.value
                    await self._pause(CHECK_INTERVAL_SECONDS)
                outcome = "current_guard_failed"
                return None

            try:
                async with asyncio.timeout(REFRESH_TIMEOUT_SECONDS):
                    power = None
                    for index, (entity, topic, prop) in enumerate(channels, 1):
                        # An unpowered bulb cannot answer. This is not a report
                        # from the bulb; only the relay's OFF was observed.
                        if (entity == LIGHT_ENTITY or entity == CORRIDOR_LIGHT_ENTITY) and power == "off":
                            bulb_skipped = True
                            break
                        channel_index = index
                        value = await read(entity, topic, prop)
                        if value is None:
                            return False
                        completed_reads += 1
                        if entity in {POWER_ENTITY, CORRIDOR_POWER_ENTITY}:
                            power = value
                    ready = current()
                    outcome = "completed" if ready else "current_guard_failed"
                    if ready:
                        phase = "complete"
                    return ready
            except TimeoutError:
                outcome = "timeout"
                return False
            except OSError:
                outcome = "io_error"
                return False
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        finally:
            # Diagnostics only: finite fields, no transport/exception/house data.
            _LOGGER.log(logging.INFO if outcome == "completed" else logging.WARNING,
                "Lighting observation refresh: scope=%s phase=%s channelIndex=%s "
                "completedReads=%s outcome=%s bulbSkipped=%s",
                scope, phase, channel_index, completed_reads, outcome, bulb_skipped)

    async def refresh_confirmation(
        self, entity_id: str, is_current: Callable[[], bool],
    ) -> bool:
        """One physical GET for a dispatched OFF, without changing authority."""
        channel = next((row for row in ALL_READ_CHANNELS if row[0] == entity_id), None)
        entities = frozenset({entity_id})
        if channel is None or not self._observations.refresh_ready(entities):
            return False
        generation = self._observations.boundary_generation

        def current() -> bool:
            return (is_current() and self._observations.boundary_generation == generation
                    and self._observations.refresh_ready(entities))

        if not current():
            return False
        before = self._observations.read_state(entity_id)
        previous = before.observation if isinstance(before, ObservedLightState) else None
        issued_at = self._now_ms()
        try:
            async with asyncio.timeout(REFRESH_TIMEOUT_SECONDS):
                await self._publish(channel[1], json.dumps({channel[2]: ""}))
                while current():
                    view = self._observations.read_state(entity_id)
                    if isinstance(view, ObservedLightState):
                        observed = physical_observed_ms(view, now_ms=self._now_ms(), freshness_ms=300_000)
                        if (observed is not None and observed >= issued_at
                                and view.observation != previous):
                            return current()
                    await self._pause(CHECK_INTERVAL_SECONDS)
                return False
        except (TimeoutError, OSError):
            return False
