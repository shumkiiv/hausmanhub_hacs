"""Live room lighting runtime driver.

The runtime subscribes to every room entity and to the Home Assistant service
bus, builds a real state context, evaluates the deterministic engine, journals
the decision and only then, when the room's persisted ``commandsEnabled`` config
flag is true, dispatches the plan through :class:`RoomLightingHaExecutor`.

Shadow-first: ``commandsEnabled`` defaults to ``False`` per room. In that mode
the executor is never called and the decision is only written to the bounded
shadow journal.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import replace
from datetime import timedelta
import logging
import time
from typing import TYPE_CHECKING

from ..domain.room_lighting import (
    RoomLightingConfig,
    SensorKind,
    SwitchAction,
    SwitchBinding,
    room_lighting_entity_collisions,
)
from ..domain.room_lighting_engine import (
    DecisionReason,
    LightAction,
    PlannedCommand,
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

try:  # Home Assistant is unavailable in framework-independent tests.
    from homeassistant.core import callback as _ha_callback
except ModuleNotFoundError:  # pragma: no cover - exercised by the test shim

    def _ha_callback(func: Callable[..., None]) -> Callable[..., None]:
        setattr(func, "_hass_callback", True)
        return func


_LOGGER = logging.getLogger(__name__)

EVENT_CALL_SERVICE = "call_service"
DEFAULT_INTERVAL_SECONDS = 60
MAX_OWNERSHIP_RECORDS = 500
OWNERSHIP_STORAGE_VERSION = 1
DEVICE_TRIGGER_DEDUP_MS = 2_000
# Matter device "A100 Away" from the Aqara A100 lock: on = nobody is home.
# This is the same source the legacy ``system-away-turn-off`` scenario used.
AWAY_ENTITY_ID = "binary_sensor.a100_away_zaniatost"
# The legacy scenario only treated "away" as active after the sensor stayed on
# for three seconds, which filters a short unlock blip.
AWAY_ON_DEBOUNCE_SECONDS = 3.0
_DEVICE_TRIGGER_PLATFORM = "mqtt"
_DEVICE_TRIGGER_INFO_NAME = "managed-room-lighting-runtime"
_TARGET_DOMAINS = frozenset({"light", "switch"})
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
        # Ownership is keyed by (room_id, target_id): two rooms may reuse the
        # same target id without sharing ownership or manual protection.
        self._records: dict[tuple[str, str], list[OwnershipSnapshot]] = {}
        self._manual_off: dict[tuple[str, str], int] = {}
        # Records from the legacy target-id-only payload wait here until the
        # runtime knows the room/target mapping and can migrate them safely.
        self._legacy_records: list[OwnershipSnapshot] = []
        self._legacy_manual_off: dict[str, int] = {}
        self._dirty = False

    @property
    def dirty(self) -> bool:
        return self._dirty

    def mark_clean(self) -> None:
        self._dirty = False

    def record_manual(
        self,
        room_id: str,
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
        self._append(room_id, record)
        if turned_off:
            self._manual_off[(room_id, target_id)] = moment
        self._dirty = True
        return record

    def record_auto(
        self,
        room_id: str,
        target_id: str,
        at: int | None = None,
        *,
        confirmed: bool = True,
    ) -> OwnershipSnapshot:
        moment = self._now_ms() if at is None else at
        record = OwnershipSnapshot(target_id, OwnershipSource.AUTO, confirmed, moment)
        self._append(room_id, record)
        self._dirty = True
        return record

    def _append(self, room_id: str, record: OwnershipSnapshot) -> None:
        bucket = self._records.setdefault((room_id, record.target_id), [])
        bucket.append(record)
        if len(bucket) > self._max_records:
            del bucket[: len(bucket) - self._max_records]

    def snapshots(self) -> tuple[OwnershipSnapshot, ...]:
        return tuple(
            record
            for bucket in self._records.values()
            for record in bucket
        ) + tuple(self._legacy_records)

    def has_legacy(self) -> bool:
        return bool(self._legacy_records or self._legacy_manual_off)

    def migrate_legacy(self, rooms_targets: Mapping[str, Iterable[str]]) -> None:
        """Attach legacy target-id-only evidence to a single unambiguous room.

        Old payloads were keyed by target id alone. A legacy id that is still
        unique across the current room configs is attached to that room; an id
        that several rooms still use is ambiguous and is dropped instead of
        being shared between rooms.
        """

        if not self.has_legacy():
            return
        owned = {
            room_id: set(target_ids)
            for room_id, target_ids in rooms_targets.items()
        }
        for record in self._legacy_records:
            room_id = _single_room_for(record.target_id, owned)
            if room_id is not None:
                self._append(room_id, record)
        for target_id, moment in self._legacy_manual_off.items():
            room_id = _single_room_for(target_id, owned)
            if room_id is not None:
                self._manual_off[(room_id, target_id)] = moment
        self._legacy_records = []
        self._legacy_manual_off = {}
        self._dirty = True

    def prune_stale_manual(
        self,
        *,
        now: int,
        minimum_interval_seconds: Mapping[str, int],
        light_on: Mapping[tuple[str, str], bool],
        presence_active: Mapping[str, bool],
    ) -> None:
        """Forget manual evidence whose protection window expired on an off light.

        Only a stale MANUAL record is dropped: the protection interval elapsed
        and the target is currently off, so keeping the manual hold would block
        automation forever after a restart. A room whose own presence or motion
        sensor still reads ``on`` is skipped: somebody is demonstrably there, so
        the hold must survive the restart and wait for absence to be confirmed
        again, exactly as the engine requires.
        """

        removed = False
        for (room_id, target_id), moment in list(self._manual_off.items()):
            if presence_active.get(room_id, False):
                continue
            bucket = self._records.get((room_id, target_id))
            if not bucket or bucket[-1].source is not OwnershipSource.MANUAL:
                continue
            if now - moment < minimum_interval_seconds.get(room_id, 0) * 1000:
                continue
            if light_on.get((room_id, target_id), False):
                continue
            self._manual_off.pop((room_id, target_id), None)
            self._records.pop((room_id, target_id), None)
            removed = True
        if removed:
            self._dirty = True

    def snapshots_for(
        self, room_id: str, target_ids: Iterable[str]
    ) -> tuple[OwnershipSnapshot, ...]:
        wanted = set(target_ids)
        return tuple(
            record
            for (record_room, target_id), bucket in self._records.items()
            if record_room == room_id and target_id in wanted
            for record in bucket
        )

    def last_manual_off_at(
        self, room_id: str, target_ids: Iterable[str]
    ) -> int | None:
        moments = [
            self._manual_off[(room_id, target_id)]
            for target_id in target_ids
            if (room_id, target_id) in self._manual_off
        ]
        return max(moments) if moments else None

    def to_payload(self) -> dict[str, object]:
        return {
            "version": OWNERSHIP_STORAGE_VERSION,
            "records": [
                {
                    "roomId": room_id,
                    "targetId": record.target_id,
                    "source": record.source.value,
                    "confirmed": record.confirmed,
                    "at": record.at,
                }
                for (room_id, _), bucket in self._records.items()
                for record in bucket
            ]
            + [
                {
                    "targetId": record.target_id,
                    "source": record.source.value,
                    "confirmed": record.confirmed,
                    "at": record.at,
                }
                for record in self._legacy_records
            ],
            "manualOff": [
                {"roomId": room_id, "targetId": target_id, "at": moment}
                for (room_id, target_id), moment in self._manual_off.items()
            ]
            + [
                {"targetId": target_id, "at": moment}
                for target_id, moment in self._legacy_manual_off.items()
            ],
        }

    def restore(self, payload: object) -> None:
        """Load a persisted journal, ignoring any damaged entry."""

        self._records = {}
        self._manual_off = {}
        self._legacy_records = []
        self._legacy_manual_off = {}
        if not isinstance(payload, Mapping):
            self._dirty = False
            return
        raw_records = payload.get("records")
        if isinstance(raw_records, (list, tuple)):
            for raw in raw_records:
                record = _ownership_record(raw)
                if record is None:
                    continue
                room_id = raw.get("roomId") if isinstance(raw, Mapping) else None
                if isinstance(room_id, str) and room_id:
                    self._append(room_id, record)
                else:
                    self._legacy_records.append(record)
        raw_manual_off = payload.get("manualOff")
        if isinstance(raw_manual_off, Mapping):
            # Legacy format: {targetId: moment}.
            for target_id, moment in raw_manual_off.items():
                if (
                    isinstance(target_id, str)
                    and target_id
                    and type(moment) is int
                    and moment >= 0
                ):
                    self._legacy_manual_off[target_id] = moment
        elif isinstance(raw_manual_off, (list, tuple)):
            for raw in raw_manual_off:
                if not isinstance(raw, Mapping):
                    continue
                room_id = raw.get("roomId")
                target_id = raw.get("targetId")
                moment = raw.get("at")
                if (
                    isinstance(room_id, str)
                    and room_id
                    and isinstance(target_id, str)
                    and target_id
                    and type(moment) is int
                    and moment >= 0
                ):
                    self._manual_off[(room_id, target_id)] = moment
        self._dirty = False


def _single_room_for(
    target_id: str, owned: Mapping[str, set[str]]
) -> str | None:
    """Return the only room owning a target id, or None when ambiguous."""

    matches = [
        room_id for room_id, target_ids in owned.items() if target_id in target_ids
    ]
    return matches[0] if len(matches) == 1 else None


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
        now_ms: NowMs | None = None,
        track_state_changes: Callable[..., Callable[[], None]] | None = None,
        track_interval: Callable[..., Callable[[], None]] | None = None,
        listen_bus: Callable[..., Callable[[], None]] | None = None,
        device_automation_api: object | None = None,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
        away_debounce_seconds: float = AWAY_ON_DEBOUNCE_SECONDS,
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
                config.room_id, config.devices.light_target_ids
            ),
            protection_provider=self._protection_for,
            unobserved_since_provider=lambda: self._unobserved_since,
        )
        self._executor = executor or RoomLightingHaExecutor()
        self._now_ms = now_ms or _default_now_ms
        self._track_state_changes = track_state_changes
        self._track_interval = track_interval
        self._listen_bus = listen_bus
        self._device_automation_api = device_automation_api
        self._device_trigger_seen: dict[tuple[str, str, str], int] = {}
        self._interval_seconds = max(1, int(interval_seconds))
        self._configs: dict[str, RoomLightingConfig] = {}
        self._rooms_by_entity: EntityRooms = {}
        self._targets_by_entity: dict[str, tuple[str, str]] = {}
        self._executing_actions: dict[str, str] = {}
        self._unsubscribers: list[Callable[[], None]] = []
        self._lock = asyncio.Lock()
        self._save_lock = asyncio.Lock()
        self._running = False
        self._away = False
        self._away_generation = 0
        self._away_debounce_seconds = max(0.0, float(away_debounce_seconds))

    # -- lifecycle ---------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._running

    @property
    def away(self) -> bool:
        """Runtime-wide away state fed from the A100 Away sensor."""

        return self._away

    def _room_commands_enabled(self, config: RoomLightingConfig) -> bool:
        """Physical commands are a per-room, persisted opt-in (default shadow)."""

        return bool(config.commands_enabled)

    async def start(
        self, hass: HomeAssistant, entry_id: str
    ) -> Callable[[], None]:
        if self._running:
            raise RuntimeError("room lighting runtime is already active")
        self._hass = hass
        self._load_trackers()
        self._away_generation += 1
        self._away = self._current_away_state(hass)
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
        # Legacy journals were keyed by target id only; attach them to rooms
        # now that the room/target mapping is known.
        self._ownership.migrate_legacy(
            {
                room_id: config.devices.light_target_ids
                for room_id, config in self._configs.items()
            }
        )
        # Stale manual holds (protection window expired on an off light) would
        # otherwise block automation forever after a restart.
        self._ownership.prune_stale_manual(
            now=self._now_ms(),
            minimum_interval_seconds={
                room_id: (
                    config.manual_off_protection.minimum_interval_seconds
                    if config.manual_off_protection.enabled
                    else 0
                )
                for room_id, config in self._configs.items()
            },
            light_on=self._light_on_by_target(),
            presence_active=self._presence_active_by_room(),
        )
        try:
            entities = tuple(sorted(self._rooms_by_entity))
            if entities and self._track_state_changes is not None:
                self._unsubscribers.append(
                    self._track_state_changes(hass, entities, self._state_event)
                )
            if self._track_state_changes is not None:
                self._unsubscribers.append(
                    self._track_state_changes(
                        hass, (AWAY_ENTITY_ID,), self._away_event
                    )
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
        # Attach after running so a press during the startup window is not
        # dropped by the running guard.
        try:
            await self._attach_device_triggers(hass)
        except Exception:  # noqa: BLE001 - a trigger API failure must not unload
            _LOGGER.warning("room lighting device trigger attach failed")
        return self.cancel

    def cancel(self) -> None:
        if not self._running and not self._unsubscribers:
            return
        # Invalidate any pending away-on debounce so it cannot fire after stop.
        self._away_generation += 1
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
        if self._track_state_changes is None or self._track_interval is None:
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

    async def _build_context(
        self, config: RoomLightingConfig, now: int | None = None
    ) -> RoomLightingContext:
        """Build the real context and stamp the runtime-wide away flag on it."""

        if self._hass is None:
            raise RuntimeError("room lighting runtime has no Home Assistant")
        context = await self._state_provider.build_context(
            self._hass, config, self._now_ms() if now is None else now
        )
        return replace(context, away=self._away)

    async def async_context_for(
        self, config: RoomLightingConfig
    ) -> RoomLightingContext:
        """Build the real context for the status endpoint and the engine."""

        return await self._build_context(config)

    async def async_execute_command(
        self, config: RoomLightingConfig, command: PlannedCommand
    ) -> dict[str, object]:
        """Execute one planned command through the shared HA executor."""

        del config
        if self._hass is None:
            return {"confirmed": False}
        receipt = await self._executor.execute(  # type: ignore[attr-defined]
            self._hass, command
        )
        to_payload = getattr(receipt, "to_payload", None)
        if callable(to_payload):
            return dict(to_payload())
        return {"confirmed": bool(getattr(receipt, "confirmed", False))}

    def configs(self) -> tuple[RoomLightingConfig, ...]:
        return tuple(self._configs.values())

    # -- events ------------------------------------------------------------

    @_ha_callback
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

    @staticmethod
    def _current_away_state(hass: HomeAssistant) -> bool:
        state = hass.states.get(AWAY_ENTITY_ID)
        return str(getattr(state, "state", "")).lower() == "on"

    @_ha_callback
    def _away_event(self, event: object) -> None:
        """Track the A100 Away source with a debounce on the on-edge."""

        del event
        if not self._running or self._hass is None:
            return
        if self._current_away_state(self._hass):
            self._arm_away()
        else:
            self._release_away()

    def _arm_away(self) -> None:
        if self._away:
            return
        self._away_generation += 1
        generation = self._away_generation
        self._create_task(self._async_away_after_debounce(generation))

    def _release_away(self) -> None:
        # A pending on-edge debounce must not fire after the source cleared.
        self._away_generation += 1
        if not self._away:
            return
        self._away = False
        self._create_task(self.async_process())

    async def _async_away_after_debounce(self, generation: int) -> None:
        if self._away_debounce_seconds > 0:
            await asyncio.sleep(self._away_debounce_seconds)
        if not self._running or generation != self._away_generation:
            return
        if self._hass is None or not self._current_away_state(self._hass):
            return
        if self._away:
            return
        self._away = True
        await self.async_process()

    @_ha_callback
    def _clock_event(self, _now: object) -> None:
        if not self._running:
            return
        self._create_task(self.async_process())

    @_ha_callback
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
            target = self._targets_by_entity.get(entity_id)
            if target is None:
                continue
            room_id, target_id = target
            moment = self._now_ms()
            expected = self._executing_actions.get(entity_id)
            if expected is not None and expected == service:
                # A command this runtime is actively issuing never grants
                # automatic ownership here. Only a confirmed read-back receipt
                # in ``_dispatch`` may record AUTO; until then automation has
                # no right to the target.
                continue
            # Conservative and intentional: any other light/switch call is
            # attributed to a person even when the caller is another
            # automation (Node-RED, a scene or a script). Manual intent must
            # win over the automatic branch until the protection releases.
            self._ownership.record_manual(
                room_id,
                target_id,
                moment,
                confirmed=True,
                turned_off=(
                    service == "turn_off"
                    or (service == "toggle" and self._entity_is_on(entity_id))
                ),
            )
            self._absence.pop(room_id, None)
            self._schedule_ownership_save()

    # -- device triggers ---------------------------------------------------

    async def _ensure_device_automation_api(
        self, hass: HomeAssistant
    ) -> object | None:
        if self._device_automation_api is not None:
            return self._device_automation_api
        try:
            from homeassistant.components.device_automation import (  # noqa: PLC0415
                DeviceAutomationType,
                async_get_device_automation_platform,
            )
        except Exception:  # noqa: BLE001 - the platform API is optional
            _LOGGER.warning(
                "room lighting device triggers are disabled: "
                "Home Assistant device automation API is unavailable"
            )
            return None
        try:
            api = await async_get_device_automation_platform(
                hass, _DEVICE_TRIGGER_PLATFORM, DeviceAutomationType.TRIGGER
            )
        except Exception:  # noqa: BLE001 - the mqtt platform may not be loaded
            _LOGGER.warning(
                "room lighting device triggers are disabled: "
                "the mqtt trigger platform is unavailable"
            )
            return None
        if api is None:
            _LOGGER.warning(
                "room lighting device triggers are disabled: "
                "the mqtt trigger platform is missing"
            )
            return None
        self._device_automation_api = api
        return api

    async def _attach_device_triggers(self, hass: HomeAssistant) -> None:
        """Attach MQTT device triggers for switches that have no button entity."""

        specs = self._device_trigger_specs()
        if not specs:
            return
        api = await self._ensure_device_automation_api(hass)
        attach = (
            getattr(api, "async_attach_trigger", None) if api is not None else None
        )
        if not callable(attach):
            _LOGGER.warning(
                "room lighting device triggers are not attached: "
                "the device automation trigger API is unavailable"
            )
            return
        for spec in specs:
            try:
                cleanup = await attach(
                    hass,
                    spec["config"],
                    spec["action"],
                    spec["trigger_info"],
                )
            except Exception:  # noqa: BLE001 - one bad trigger must not unload
                _LOGGER.warning(
                    "room lighting device trigger attach failed for %s",
                    spec["key"],
                )
                continue
            if callable(cleanup):
                self._unsubscribers.append(cleanup)

    def _device_trigger_specs(self) -> list[dict[str, object]]:
        specs: list[dict[str, object]] = []
        attached: set[tuple[str, str, str]] = set()
        index = 0
        for config in self._configs.values():
            bound_subtypes: dict[str, set[str]] = {}
            for binding in config.switch_bindings:
                if binding.trigger_subtype is not None:
                    bound_subtypes.setdefault(binding.switch_id, set()).add(
                        binding.trigger_subtype
                    )
            for switch in config.devices.wireless_switches:
                if switch.device_id is None:
                    continue
                for subtype in switch.trigger_subtypes:
                    if subtype not in bound_subtypes.get(switch.id, set()):
                        continue
                    key = (config.room_id, switch.device_id, subtype)
                    if key in attached:
                        continue
                    attached.add(key)
                    specs.append(
                        {
                            "key": key,
                            "config": {
                                "platform": "device",
                                "device_id": switch.device_id,
                                "domain": _DEVICE_TRIGGER_PLATFORM,
                                "type": "action",
                                "subtype": subtype,
                            },
                            "trigger_info": {
                                "domain": "hausman_hub",
                                "name": _DEVICE_TRIGGER_INFO_NAME,
                                "variables": {},
                                "trigger_data": {
                                    "id": (
                                        f"room-lighting-{config.room_id}-"
                                        f"{switch.id}-{subtype}"
                                    ),
                                    "idx": str(index),
                                    "alias": None,
                                },
                            },
                            "action": self._device_trigger_action(
                                config, switch.device_id, switch.id, subtype
                            ),
                        }
                    )
                    index += 1
        return specs

    def _device_trigger_action(
        self,
        config: RoomLightingConfig,
        device_id: str,
        switch_id: str,
        subtype: str,
    ) -> Callable[..., object]:
        async def _action(
            run_variables: object | None = None,
            context: object | None = None,
        ) -> None:
            del run_variables, context
            await self._handle_device_trigger(
                config, device_id, switch_id, subtype
            )

        return _action

    async def _handle_device_trigger(
        self,
        config: RoomLightingConfig,
        device_id: str,
        switch_id: str,
        subtype: str,
    ) -> None:
        if not self._running:
            return
        bindings = [
            binding
            for binding in config.switch_bindings
            if binding.switch_id == switch_id
            and binding.trigger_subtype == subtype
        ]
        if not bindings:
            return
        moment = self._now_ms()
        dedup_key = (config.room_id, device_id, subtype)
        seen = self._device_trigger_seen.get(dedup_key)
        if seen is not None and moment - seen < DEVICE_TRIGGER_DEDUP_MS:
            _LOGGER.info(
                "room lighting device trigger %s/%s ignored as duplicate",
                device_id,
                subtype,
            )
            return
        self._device_trigger_seen[dedup_key] = moment
        enabled = self._room_commands_enabled(config)
        for binding in bindings:
            target_ids = _binding_target_ids(config, binding)
            _LOGGER.info(
                "room lighting device trigger %s/%s -> %s targets=%s commands_enabled=%s",
                device_id,
                subtype,
                binding.action.value,
                ",".join(target_ids),
                enabled,
            )
            # A press is a manual intent regardless of the command flag.
            for target_id in target_ids:
                target = config.devices.target(target_id)
                self._ownership.record_manual(
                    config.room_id,
                    target_id,
                    moment,
                    confirmed=True,
                    turned_off=_binding_turns_off(self._hass, target, binding),
                )
                self._absence.pop(config.room_id, None)
            self._schedule_ownership_save()
            if not enabled or not self._running:
                continue
            await self._execute_binding(config, binding, target_ids)

    async def _execute_binding(
        self,
        config: RoomLightingConfig,
        binding: SwitchBinding,
        target_ids: Sequence[str],
    ) -> None:
        if self._hass is None:
            return
        for target_id in target_ids:
            if not self._running:
                return
            target = config.devices.target(target_id)
            if target is None or target.entity_id is None:
                continue
            command = _binding_command(self._hass, target, binding)
            if command is None:
                continue
            try:
                await self._executor.execute(  # type: ignore[attr-defined]
                    self._hass, command
                )
            except Exception:  # noqa: BLE001 - a failed press is not a crash
                _LOGGER.warning("room lighting binding command failed")

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
            context = await self._build_context(config, moment)
            self._observe_absence(config, context)
            decision = evaluate_room_lighting(config, context)
        except Exception:  # noqa: BLE001 - isolation per room is deliberate
            _LOGGER.warning("room lighting evaluation failed; skipping the room")
            return
        enabled = self._room_commands_enabled(config)
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
        power = config.devices.power_switch
        power_entity = power.entity_id if power is not None else None
        for command in decision.commands:
            if not self._running:
                return
            if (
                power_entity is not None
                and command.action is not LightAction.TURN_OFF
                and not self._entity_is_on(power_entity)
            ):
                # A target that is switched on through its room power switch
                # must get power first; a confirmed power-on is required.
                ensure_power = getattr(self._executor, "async_power_on", None)
                powered = False
                if callable(ensure_power):
                    try:
                        powered = bool(await ensure_power(self._hass, power_entity))
                    except Exception:  # noqa: BLE001 - keep the room isolated
                        powered = False
                if not powered:
                    _LOGGER.warning(
                        "room lighting power switch is still off; "
                        "skipping the target command"
                    )
                    continue
            target = config.devices.target(command.target_id)
            entity_id = target.entity_id if target is not None else None
            if entity_id is not None:
                self._executing_actions[entity_id] = _planned_service_name(command)
            try:
                receipt = await self._executor.execute(  # type: ignore[attr-defined]
                    self._hass, command
                )
            except Exception:  # noqa: BLE001 - a failed command is not a crash
                _LOGGER.warning("room lighting command failed")
                receipt = None
            finally:
                if entity_id is not None:
                    self._executing_actions.pop(entity_id, None)
            if _receipt_confirmed(receipt):
                self._ownership.record_auto(
                    config.room_id, command.target_id, moment, confirmed=True
                )
                self._schedule_ownership_save()
        await self._persist_ownership()

    # -- helpers -----------------------------------------------------------

    def _entity_is_on(self, entity_id: str) -> bool:
        if self._hass is None:
            return False
        state = self._hass.states.get(entity_id)
        return state is not None and str(getattr(state, "state", "")).lower() == "on"

    def _light_on_by_target(self) -> dict[tuple[str, str], bool]:
        """Current on/off state per (room_id, target_id) for stale pruning."""

        result: dict[tuple[str, str], bool] = {}
        for room_id, config in self._configs.items():
            powered = self._room_power_on(config)
            for target in config.devices.light_targets:
                entity_id = target.entity_id
                result[(room_id, target.id)] = bool(
                    powered
                    and entity_id
                    and self._entity_is_on(entity_id)
                )
        return result

    def _room_power_on(self, config: RoomLightingConfig) -> bool:
        """Whether the room's configured power switch is provably on."""

        power = config.devices.power_switch
        if power is None or power.entity_id is None:
            return True
        return self._entity_is_on(power.entity_id)

    def _presence_active_by_room(self) -> dict[str, bool]:
        """Whether a room's own presence or motion sensor currently reads on.

        Only an explicit ``on`` counts as active presence: off, unknown and
        unavailable sensors do not prove somebody is there, so those rooms may
        still have a stale manual hold pruned.
        """

        result: dict[str, bool] = {}
        for room_id, config in self._configs.items():
            result[room_id] = any(
                sensor.kind in _PRESENCE_KINDS
                and sensor.entity_id is not None
                and self._entity_is_on(sensor.entity_id)
                for sensor in config.devices.sensors
            )
        return result

    def _rebuild_index(self) -> None:
        rooms_by_entity: EntityRooms = {}
        targets_by_entity: dict[str, tuple[str, str]] = {}
        targets: dict[str, object] = {}
        # One physical entity must belong to one room. A collision is reported
        # explicitly instead of silently overwriting the first room's owner.
        for collision in room_lighting_entity_collisions(self._configs.values()):
            _LOGGER.warning("room lighting entity collision: %s", collision)
        for config in self._configs.values():
            for sensor in config.devices.sensors:
                if sensor.entity_id:
                    rooms_by_entity.setdefault(sensor.entity_id, set()).add(
                        config.room_id
                    )
            power = config.devices.power_switch
            if power is not None and power.entity_id:
                # A power switch changes the effective state of every target in
                # the room, so its state changes must trigger a recompute.
                rooms_by_entity.setdefault(power.entity_id, set()).add(
                    config.room_id
                )
            for target in config.devices.light_targets:
                targets[target.id] = target
                if target.entity_id:
                    rooms_by_entity.setdefault(target.entity_id, set()).add(
                        config.room_id
                    )
                    targets_by_entity.setdefault(
                        target.entity_id,
                        (config.room_id, target.id),
                    )
        self._rooms_by_entity = rooms_by_entity
        self._targets_by_entity = targets_by_entity
        update = getattr(self._executor, "update_targets", None)
        if callable(update):
            update(targets)

    def _protection_for(self, config: RoomLightingConfig) -> ProtectionSnapshot:
        if not config.manual_off_protection.enabled:
            return ProtectionSnapshot()
        started_at = self._ownership.last_manual_off_at(
            config.room_id,
            (target.id for target in config.devices.light_targets),
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
        loop = getattr(self._hass, "loop", None)
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        # A callback normally runs on the Home Assistant event loop. If a caller
        # ever reaches here from an executor thread, hop back before creating the
        # task instead of calling hass.async_create_task off-loop.
        if loop is not None and running_loop is not loop:
            schedule = getattr(loop, "call_soon_threadsafe", None)
            if callable(schedule) and callable(create):
                try:
                    schedule(create, coroutine)
                    return
                except Exception:  # noqa: BLE001 - never crash a callback
                    _LOGGER.warning("room lighting task scheduling failed")
                    _discard_coroutine(coroutine)
                    return
        if callable(create):
            try:
                create(coroutine)
                return
            except Exception:  # noqa: BLE001 - never crash a callback
                _LOGGER.warning("room lighting task scheduling failed")
                _discard_coroutine(coroutine)
                return
        try:
            asyncio.ensure_future(coroutine)  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001 - never crash a callback
            _LOGGER.warning("room lighting task scheduling failed")
            _discard_coroutine(coroutine)


def _discard_coroutine(coroutine: object) -> None:
    close = getattr(coroutine, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # noqa: BLE001 - best effort cleanup
            pass


def _entity_ids(service_data: object) -> tuple[str, ...]:
    if not isinstance(service_data, Mapping):
        return ()
    value = service_data.get("entity_id")
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(item for item in value if isinstance(item, str))
    return ()


def _planned_service_name(command: PlannedCommand) -> str:
    """The Home Assistant service a planned command is executed through."""

    if command.action is LightAction.TURN_OFF:
        return "turn_off"
    return "turn_on"


def _receipt_confirmed(receipt: object) -> bool:
    if isinstance(receipt, Mapping):
        return bool(receipt.get("confirmed"))
    return bool(getattr(receipt, "confirmed", False))


def _binding_target_ids(
    config: RoomLightingConfig, binding: SwitchBinding
) -> tuple[str, ...]:
    """Expand one binding's targets to concrete target ids."""

    targets = binding.targets
    if (
        not targets.light_targets
        and not targets.group_ids
        and not targets.roles
    ):
        return tuple(target.id for target in config.devices.light_targets)
    selected = set(targets.light_targets)
    for target in config.devices.light_targets:
        if target.group_id is not None and target.group_id in targets.group_ids:
            selected.add(target.id)
        if target.role is not None and target.role in targets.roles:
            selected.add(target.id)
    return tuple(sorted(selected))


def _binding_turns_off(
    hass: HomeAssistant | None,
    target: object | None,
    binding: SwitchBinding,
) -> bool:
    """Whether this binding intends to turn the target off right now."""

    if binding.action is SwitchAction.TURN_OFF:
        return True
    if binding.action is not SwitchAction.TOGGLE or target is None:
        return False
    entity_id = getattr(target, "entity_id", None)
    state = (
        hass.states.get(entity_id)
        if hass is not None and isinstance(entity_id, str)
        else None
    )
    # A toggle on an already on target is a manual-off intent, even in shadow.
    return state is not None and str(getattr(state, "state", "")).lower() == "on"


def _binding_command(
    hass: HomeAssistant,
    target: object,
    binding: SwitchBinding,
) -> PlannedCommand | None:
    """Translate one confirmed switch binding into a planned target command."""

    target_id = target.id  # type: ignore[attr-defined]
    action = binding.action
    if action is SwitchAction.TURN_ON:
        return PlannedCommand(
            target_id, LightAction.TURN_ON, reason=DecisionReason.PRESENCE
        )
    if action is SwitchAction.TURN_OFF:
        return PlannedCommand(
            target_id, LightAction.TURN_OFF, reason=DecisionReason.SCHEDULE
        )
    if action is SwitchAction.SET_MAX:
        if getattr(target, "brightness", False):
            return PlannedCommand(
                target_id,
                LightAction.SET_BRIGHTNESS,
                brightness=100,
                reason=DecisionReason.PRESENCE,
            )
        return PlannedCommand(
            target_id, LightAction.TURN_ON, reason=DecisionReason.PRESENCE
        )
    if action is SwitchAction.TOGGLE:
        entity_id = getattr(target, "entity_id", None)
        state = hass.states.get(entity_id) if isinstance(entity_id, str) else None
        is_on = state is not None and str(getattr(state, "state", "")).lower() == "on"
        return PlannedCommand(
            target_id,
            LightAction.TURN_OFF if is_on else LightAction.TURN_ON,
            reason=DecisionReason.PRESENCE,
        )
    return None


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
    "AWAY_ENTITY_ID",
    "AWAY_ON_DEBOUNCE_SECONDS",
    "DEFAULT_INTERVAL_SECONDS",
    "OWNERSHIP_STORAGE_VERSION",
]
