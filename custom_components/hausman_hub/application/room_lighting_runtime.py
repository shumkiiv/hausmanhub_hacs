"""Live room lighting runtime driver.

The runtime subscribes to every room entity and to the Home Assistant service
bus, builds a real state context, evaluates the deterministic engine, journals
the decision and only then, when the commands flag is explicitly enabled,
dispatches the plan through :class:`RoomLightingHaExecutor`.

Shadow-first: ``commands_enabled`` defaults to ``False``. In that mode the
executor is never called and the decision is only written to the bounded shadow
journal.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import timedelta
import logging
import time
from typing import TYPE_CHECKING

from ..domain.room_lighting import RoomLightingConfig, SensorKind
from ..domain.room_lighting_engine import (
    ProtectionSnapshot,
    RoomLightingDecision,
    RoomLightingContext,
    evaluate_room_lighting,
)
from ..domain.room_lighting_ownership import (
    OwnershipSnapshot,
    OwnershipSource,
    OwnershipViolation,
    SensorState,
)
from .room_lighting_ha_executor import RoomLightingHaExecutor
from .room_lighting_ha_state import RoomLightingHaStateProvider

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

EVENT_CALL_SERVICE = "call_service"
DEFAULT_INTERVAL_SECONDS = 60
MAX_OWNERSHIP_RECORDS = 500
OWNERSHIP_STORAGE_VERSION = 1
_TARGET_DOMAINS = frozenset({"light", "switch"})
_MANUAL_OFF_SERVICES = frozenset({"turn_off", "toggle"})
_PRESENCE_KINDS = frozenset({SensorKind.PRESENCE, SensorKind.MOTION})

NowMs = Callable[[], int]
EntityRooms = dict[str, set[str]]
AbsenceState = dict[str, tuple[bool, int | None]]


def _default_now_ms() -> int:
    return int(time.time() * 1000)


class RoomLightingOwnershipJournal:
    """Bounded attribution journal for room light targets.

    Manual records block the automatic branch; confirmed automatic records are
    the only proof that allows the engine to turn a light off by itself. The
    journal is serializable so ``AUTO`` and manual-off evidence survive a Home
    Assistant restart.
    """

    def __init__(
        self,
        *,
        max_records: int = MAX_OWNERSHIP_RECORDS,
        now_ms: NowMs | None = None,
    ) -> None:
        self._max_records = max(1, int(max_records))
        self._now_ms = now_ms or _default_now_ms
        self._records: dict[str, list[OwnershipSnapshot]] = {}
        self._manual_off: dict[str, int] = {}
        self._dirty = False

    @property
    def dirty(self) -> bool:
        return self._dirty

    def mark_clean(self) -> None:
        self._dirty = False

    def record_manual(
        self,
        target_id: str,
        at: int | None = None,
        *,
        confirmed: bool = True,
        turned_off: bool = False,
    ) -> OwnershipSnapshot:
        moment = self._now_ms() if at is None else at
        record = OwnershipSnapshot(
            target_id, OwnershipSource.MANUAL, confirmed, moment
        )
        self._append(record)
        if turned_off:
            self._manual_off[target_id] = moment
        self._dirty = True
        return record

    def record_auto(
        self,
        target_id: str,
        at: int | None = None,
        *,
        confirmed: bool = True,
    ) -> OwnershipSnapshot:
        moment = self._now_ms() if at is None else at
        record = OwnershipSnapshot(target_id, OwnershipSource.AUTO, confirmed, moment)
        self._append(record)
        self._dirty = True
        return record

    def _append(self, record: OwnershipSnapshot) -> None:
        bucket = self._records.setdefault(record.target_id, [])
        bucket.append(record)
        if len(bucket) > self._max_records:
            del bucket[: len(bucket) - self._max_records]

    def snapshots(self) -> tuple[OwnershipSnapshot, ...]:
        return tuple(
            record
            for bucket in self._records.values()
            for record in bucket
        )

    def snapshots_for(
        self, target_ids: Iterable[str]
    ) -> tuple[OwnershipSnapshot, ...]:
        wanted = set(target_ids)
        return tuple(
            record
            for target_id, bucket in self._records.items()
            if target_id in wanted
            for record in bucket
        )

    def last_manual_off_at(self, target_ids: Iterable[str]) -> int | None:
        moments = [
            self._manual_off[target_id]
            for target_id in target_ids
            if target_id in self._manual_off
        ]
        return max(moments) if moments else None

    def to_payload(self) -> dict[str, object]:
        return {
            "version": OWNERSHIP_STORAGE_VERSION,
            "records": [
                {
                    "targetId": record.target_id,
                    "source": record.source.value,
                    "confirmed": record.confirmed,
                    "at": record.at,
                }
                for record in self.snapshots()
            ],
            "manualOff": dict(self._manual_off),
        }

    def restore(self, payload: object) -> None:
        """Load a persisted journal, ignoring any damaged entry."""

        self._records = {}
        self._manual_off = {}
        if not isinstance(payload, Mapping):
            self._dirty = False
            return
        raw_records = payload.get("records")
        if isinstance(raw_records, (list, tuple)):
            for raw in raw_records:
                record = _ownership_record(raw)
                if record is not None:
                    self._append(record)
        raw_manual_off = payload.get("manualOff")
        if isinstance(raw_manual_off, Mapping):
            for target_id, moment in raw_manual_off.items():
                if (
                    isinstance(target_id, str)
                    and target_id
                    and type(moment) is int
                    and moment >= 0
                ):
                    self._manual_off[target_id] = moment
        self._dirty = False


def _ownership_record(raw: object) -> OwnershipSnapshot | None:
    if not isinstance(raw, Mapping):
        return None
    try:
        return OwnershipSnapshot(
            target_id=str(raw.get("targetId") or ""),
            source=OwnershipSource(str(raw.get("source") or "")),
            confirmed=bool(raw.get("confirmed")),
            at=raw.get("at"),  # type: ignore[arg-type]
        )
    except (OwnershipViolation, ValueError, TypeError):
        return None


class HomeAssistantRoomLightingOwnershipStore:
    """Persist the ownership journal and manual-off moments for one entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        from homeassistant.helpers.storage import Store

        self._store: Store[dict[str, object]] = Store(
            hass,
            OWNERSHIP_STORAGE_VERSION,
            f"hausman_hub.room_lighting_ownership.{entry_id}",
        )

    async def async_load(self) -> object | None:
        return await self._store.async_load()

    async def async_save(self, payload: dict[str, object]) -> None:
        await self._store.async_save(payload)


class RoomLightingRuntime:
    """Own the live subscriptions, the ownership journal and the dispatch flag."""

    def __init__(
        self,
        hass: HomeAssistant | None,
        service: object,
        shadow: object,
        *,
        state_provider: RoomLightingHaStateProvider | None = None,
        executor: object | None = None,
        ownership: RoomLightingOwnershipJournal | None = None,
        ownership_store: object | None = None,
        commands_enabled: bool = False,
        commands_enabled_provider: Callable[[str], bool] | None = None,
        now_ms: NowMs | None = None,
        track_state_changes: Callable[..., Callable[[], None]] | None = None,
        track_interval: Callable[..., Callable[[], None]] | None = None,
        listen_bus: Callable[..., Callable[[], None]] | None = None,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    ) -> None:
        self._hass = hass
        self._service = service
        self._shadow = shadow
        self._ownership = ownership or RoomLightingOwnershipJournal(now_ms=now_ms)
        self._ownership_store = ownership_store
        self._unobserved_since: int | None = None
        self._absence: AbsenceState = {}
        self._state_provider = state_provider or RoomLightingHaStateProvider(
            now_ms=now_ms,
            ownership_provider=lambda config: self._ownership.snapshots_for(
                config.devices.light_target_ids
            ),
            protection_provider=self._protection_for,
            unobserved_since_provider=lambda: self._unobserved_since,
        )
        self._executor = executor or RoomLightingHaExecutor()
        self._commands_enabled = bool(commands_enabled)
        self._commands_enabled_provider = commands_enabled_provider
        self._now_ms = now_ms or _default_now_ms
        self._track_state_changes = track_state_changes
        self._track_interval = track_interval
        self._listen_bus = listen_bus
        self._interval_seconds = max(1, int(interval_seconds))
        self._configs: dict[str, RoomLightingConfig] = {}
        self._rooms_by_entity: EntityRooms = {}
        self._targets_by_entity: dict[str, str] = {}
        self._room_by_target: dict[str, str] = {}
        self._executing_entities: set[str] = set()
        self._unsubscribers: list[Callable[[], None]] = []
        self._lock = asyncio.Lock()
        self._save_lock = asyncio.Lock()
        self._running = False

    # -- lifecycle ---------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._running

    @property
    def commands_enabled(self) -> bool:
        return self._commands_enabled

    def set_commands_enabled(self, enabled: bool) -> None:
        self._commands_enabled = bool(enabled)

    def _room_commands_enabled(self, room_id: str) -> bool:
        if self._commands_enabled_provider is not None:
            return bool(self._commands_enabled_provider(room_id))
        return self._commands_enabled

    async def start(
        self, hass: HomeAssistant, entry_id: str
    ) -> Callable[[], None]:
        if self._running:
            raise RuntimeError("room lighting runtime is already active")
        self._hass = hass
        self._load_trackers()
        # The gap starts at start: an unobserved interval is never counted as
        # absence and cannot restore pre-restart automatic ownership.
        self._unobserved_since = self._now_ms()
        self._absence = {}
        if self._ownership_store is not None:
            try:
                payload = await self._ownership_store.async_load()
            except Exception:  # noqa: BLE001 - damaged storage fails closed
                _LOGGER.warning("room lighting ownership load failed; starting empty")
                payload = None
            self._ownership.restore(payload)
        await self._shadow.async_load()  # type: ignore[attr-defined]
        configs = await self._service.async_list_configs()  # type: ignore[attr-defined]
        self._configs = {config.room_id: config for config in configs}
        self._rebuild_index()
        try:
            entities = tuple(sorted(self._rooms_by_entity))
            if entities and self._track_state_changes is not None:
                self._unsubscribers.append(
                    self._track_state_changes(hass, entities, self._state_event)
                )
            if self._track_interval is not None:
                self._unsubscribers.append(
                    self._track_interval(
                        hass,
                        self._clock_event,
                        timedelta(seconds=self._interval_seconds),
                    )
                )
            if self._listen_bus is not None:
                self._unsubscribers.append(
                    self._listen_bus(hass, EVENT_CALL_SERVICE, self._service_event)
                )
        except Exception:
            self.cancel()
            raise
        self._running = True
        try:
            await self.async_process()
        except Exception:
            self.cancel()
            raise
        return self.cancel

    def cancel(self) -> None:
        if not self._running and not self._unsubscribers:
            return
        if (
            self._ownership_store is not None
            and self._ownership.dirty
            and self._hass is not None
        ):
            # Best-effort final flush; the journal also saves on each mutation.
            self._create_task(self._persist_ownership())
        self._running = False
        for unsubscribe in reversed(self._unsubscribers):
            try:
                unsubscribe()
            except Exception:  # noqa: BLE001 - cleanup must be best effort
                _LOGGER.error("room lighting runtime cleanup failed")
        self._unsubscribers.clear()

    async def stop(self) -> None:
        self.cancel()

    def _load_trackers(self) -> None:
        if self._track_state_changes is not None and self._track_interval is not None:
            if self._listen_bus is None:
                self._listen_bus = _listen_service_events
            return
        import homeassistant.helpers.event as event_helpers  # noqa: PLC0415

        if self._track_state_changes is None:
            self._track_state_changes = getattr(
                event_helpers, "async_track_state_change_event", None
            )
        if self._track_interval is None:
            self._track_interval = getattr(
                event_helpers, "async_track_time_interval", None
            )
        if self._listen_bus is None:
            self._listen_bus = _listen_service_events

    # -- public read model -------------------------------------------------

    async def async_context_for(
        self, config: RoomLightingConfig
    ) -> RoomLightingContext:
        """Build the real context for the status endpoint and the engine."""

        if self._hass is None:
            raise RuntimeError("room lighting runtime has no Home Assistant")
        return await self._state_provider.build_context(
            self._hass, config, self._now_ms()
        )

    def configs(self) -> tuple[RoomLightingConfig, ...]:
        return tuple(self._configs.values())

    # -- events ------------------------------------------------------------

    def _state_event(self, event: object) -> None:
        if not self._running:
            return
        data = getattr(event, "data", None)
        entity_id = None
        if isinstance(data, Mapping):
            entity_id = data.get("entity_id")
        if not isinstance(entity_id, str):
            return
        rooms = self._rooms_by_entity.get(entity_id)
        if not rooms:
            return
        self._create_task(self.async_process(sorted(rooms)))

    def _clock_event(self, _now: object) -> None:
        if not self._running:
            return
        self._create_task(self.async_process())

    def _service_event(self, event: object) -> None:
        if not self._running:
            return
        data = getattr(event, "data", None)
        if not isinstance(data, Mapping):
            return
        domain = str(data.get("domain") or "")
        if domain not in _TARGET_DOMAINS:
            return
        service = str(data.get("service") or "")
        service_data = data.get("service_data")
        for entity_id in _entity_ids(service_data):
            target_id = self._targets_by_entity.get(entity_id)
            if target_id is None:
                continue
            moment = self._now_ms()
            if entity_id in self._executing_entities:
                self._ownership.record_auto(target_id, moment, confirmed=True)
            else:
                # Conservative and intentional: a foreign light/switch call is
                # attributed to a person even when the caller is another
                # automation (Node-RED, a scene or a script). Manual intent must
                # win over the automatic branch until the protection releases.
                self._ownership.record_manual(
                    target_id,
                    moment,
                    confirmed=True,
                    turned_off=service in _MANUAL_OFF_SERVICES,
                )
                self._absence.pop(self._room_by_target.get(target_id), None)
            self._schedule_ownership_save()

    # -- evaluation --------------------------------------------------------

    async def async_process(
        self, room_ids: Sequence[str] | None = None
    ) -> None:
        if not self._running:
            return
        async with self._lock:
            if not self._running:
                return
            selected = (
                tuple(self._configs)
                if room_ids is None
                else tuple(room for room in room_ids if room in self._configs)
            )
            for room_id in selected:
                if not self._running:
                    break
                await self._process_room(self._configs[room_id])
        await self._persist_ownership()

    async def _process_room(self, config: RoomLightingConfig) -> None:
        if self._hass is None:
            return
        moment = self._now_ms()
        # A single broken room must never crash setup or stop the other rooms.
        try:
            context = await self._state_provider.build_context(
                self._hass, config, moment
            )
            self._observe_absence(config, context)
            decision = evaluate_room_lighting(config, context)
        except Exception:  # noqa: BLE001 - isolation per room is deliberate
            _LOGGER.warning("room lighting evaluation failed; skipping the room")
            return
        enabled = self._room_commands_enabled(config.room_id)
        record = getattr(self._shadow, "async_record", None)
        if callable(record):
            try:
                await record(decision, commands_enabled=enabled)
            except Exception:  # noqa: BLE001 - journal failure must not dispatch
                _LOGGER.warning("room lighting journal write failed")
        if not enabled:
            _LOGGER.debug(
                "room lighting shadow decision journaled for %s", config.room_id
            )
            return
        # Re-check after the await: cancel() may have run while state was read.
        if not self._running:
            return
        await self._dispatch(config, decision, moment)

    async def _dispatch(
        self,
        config: RoomLightingConfig,
        decision: RoomLightingDecision,
        moment: int,
    ) -> None:
        if self._hass is None:
            return
        for command in decision.commands:
            if not self._running:
                return
            target = config.devices.target(command.target_id)
            entity_id = target.entity_id if target is not None else None
            if entity_id is not None:
                self._executing_entities.add(entity_id)
            try:
                receipt = await self._executor.execute(  # type: ignore[attr-defined]
                    self._hass, command
                )
            except Exception:  # noqa: BLE001 - a failed command is not a crash
                _LOGGER.warning("room lighting command failed")
                receipt = None
            finally:
                if entity_id is not None:
                    self._executing_entities.discard(entity_id)
            if _receipt_confirmed(receipt):
                self._ownership.record_auto(
                    command.target_id, moment, confirmed=True
                )
                self._schedule_ownership_save()
        await self._persist_ownership()

    # -- helpers -----------------------------------------------------------

    def _rebuild_index(self) -> None:
        rooms_by_entity: EntityRooms = {}
        targets_by_entity: dict[str, str] = {}
        room_by_target: dict[str, str] = {}
        targets: dict[str, object] = {}
        for config in self._configs.values():
            for sensor in config.devices.sensors:
                if sensor.entity_id:
                    rooms_by_entity.setdefault(sensor.entity_id, set()).add(
                        config.room_id
                    )
            for target in config.devices.light_targets:
                targets[target.id] = target
                room_by_target[target.id] = config.room_id
                if target.entity_id:
                    rooms_by_entity.setdefault(target.entity_id, set()).add(
                        config.room_id
                    )
                    targets_by_entity[target.entity_id] = target.id
        self._rooms_by_entity = rooms_by_entity
        self._targets_by_entity = targets_by_entity
        self._room_by_target = room_by_target
        update = getattr(self._executor, "update_targets", None)
        if callable(update):
            update(targets)

    def _protection_for(self, config: RoomLightingConfig) -> ProtectionSnapshot:
        if not config.manual_off_protection.enabled:
            return ProtectionSnapshot()
        started_at = self._ownership.last_manual_off_at(
            target.id for target in config.devices.light_targets
        )
        if started_at is None:
            return ProtectionSnapshot()
        protection = config.manual_off_protection
        absence_confirmed, absence_since = self._absence.get(
            config.room_id, (False, None)
        )
        return ProtectionSnapshot(
            active=True,
            started_at=started_at,
            minimum_interval_seconds=protection.minimum_interval_seconds,
            stable_absence_seconds=protection.stable_absence_seconds,
            release_mode=protection.release_mode.value,
            reason="manual_off",
            absence_confirmed=absence_confirmed,
            absence_since=absence_since,
        )

    def _observe_absence(
        self, config: RoomLightingConfig, context: RoomLightingContext
    ) -> None:
        """Track a completed absence so a returned presence can release."""

        relevant = [
            sensor
            for sensor in context.sensors
            if sensor.kind in _PRESENCE_KINDS
        ]
        if not relevant:
            self._absence.pop(config.room_id, None)
            return
        off_times: list[int] = []
        for sensor in relevant:
            if sensor.state in {
                SensorState.UNKNOWN,
                SensorState.UNAVAILABLE,
                SensorState.ON,
            }:
                # Presence returned: keep the earlier confirmed absence so the
                # manual protection can still release on this new presence.
                return
            off_times.append(sensor.last_changed)
        if not off_times:
            return
        since = max(off_times)
        if self._unobserved_since is not None:
            since = max(since, self._unobserved_since)
        self._absence[config.room_id] = (True, since)

    def _schedule_ownership_save(self) -> None:
        if self._ownership_store is None or self._hass is None:
            return
        self._create_task(self._persist_ownership())

    async def _persist_ownership(self) -> None:
        if self._ownership_store is None or not self._ownership.dirty:
            return
        async with self._save_lock:
            if not self._ownership.dirty:
                return
            try:
                await self._ownership_store.async_save(  # type: ignore[attr-defined]
                    self._ownership.to_payload()
                )
            except Exception:  # noqa: BLE001 - retry on the next mutation
                _LOGGER.warning("room lighting ownership save failed")
                return
            self._ownership.mark_clean()

    def _create_task(self, coroutine: object) -> None:
        if self._hass is None:
            return
        create = getattr(self._hass, "async_create_task", None)
        if callable(create):
            create(coroutine)
        else:
            asyncio.ensure_future(coroutine)  # type: ignore[arg-type]


def _entity_ids(service_data: object) -> tuple[str, ...]:
    if not isinstance(service_data, Mapping):
        return ()
    value = service_data.get("entity_id")
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(item for item in value if isinstance(item, str))
    return ()


def _receipt_confirmed(receipt: object) -> bool:
    if isinstance(receipt, Mapping):
        return bool(receipt.get("confirmed"))
    return bool(getattr(receipt, "confirmed", False))


def _listen_service_events(
    hass: HomeAssistant,
    event_type: str,
    callback: Callable[[object], None],
) -> Callable[[], None]:
    bus = getattr(hass, "bus", None)
    listen = getattr(bus, "async_listen", None)
    if not callable(listen):
        return lambda: None
    return listen(event_type, callback)


__all__ = [
    "HomeAssistantRoomLightingOwnershipStore",
    "RoomLightingOwnershipJournal",
    "RoomLightingRuntime",
    "DEFAULT_INTERVAL_SECONDS",
    "OWNERSHIP_STORAGE_VERSION",
]
