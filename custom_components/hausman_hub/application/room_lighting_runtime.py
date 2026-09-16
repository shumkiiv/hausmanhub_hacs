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
import math
import time
from typing import TYPE_CHECKING

from ..domain.room_lighting import (
    RoomLightingConfig,
    SensorKind,
    SwitchAction,
    SwitchBinding,
    room_lighting_entity_collisions,
)
from ..domain.room_lighting_auxiliary import evaluate_bathroom_exhaust
from ..domain.shower_exhaust import (
    SHOWER_ABSENCE_TIMER,
    SHOWER_PRESENCE_TIMER,
    ShowerExhaustObservation,
    ShowerExhaustPolicy,
    ShowerFanAction,
    ShowerTimer,
    evaluate_shower_exhaust,
    evaluate_shower_exhaust_due,
)
from ..domain.room_lighting_engine import (
    DecisionReason,
    LightAction,
    PlannedCommand,
    ProtectionSnapshot,
    RoomLightingDecision,
    RoomLightingContext,
    bathroom_auxiliary_inputs,
    curve_profile_block_reason,
    evaluate_curve_room,
    evaluate_room_lighting,
)
from ..domain.room_lighting_ownership import (
    OwnershipSnapshot,
    OwnershipSource,
    OwnershipViolation,
    SensorState,
    has_proven_auto_ownership,
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
CURVE_PRESENCE_CONFIRM_MS = 15_000
CURVE_MOTION_CONFIRM_WINDOW_MS = 20_000
ENTRY_WAKEUP_GRACE_MS = 5_000
CURVE_SENSOR_FRESHNESS_MS = 300_000
DEFAULT_INTERVAL_SECONDS = 60
MAX_OWNERSHIP_RECORDS = 500
OWNERSHIP_STORAGE_VERSION = 1
# MQTT can replay one event, but a person can deliberately make a second press
# within the three-second configurable sequence window.  A short transport
# deduplication guard preserves the former protection without eating the next
# human press.
DEVICE_TRIGGER_DEDUP_MS = 250
# Window in which a state change caused by our own command is not mistaken for
# a person switching the light. Device reports can arrive a few seconds after
# the service call, so the marker must outlive the immediate call.
COMMAND_GRACE_MS = 15_000
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


def _is_unlock_event(data: Mapping[str, object]) -> bool:
    """Return true only for an observed lock transition to ``unlocked``."""

    new_state = getattr(data.get("new_state"), "state", None)
    old_state = getattr(data.get("old_state"), "state", None)
    return str(new_state).lower() == "unlocked" and str(old_state).lower() != "unlocked"


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

    def clear_explicit_manual_ownership(
        self, room_id: str, target_ids: Iterable[str]
    ) -> tuple[str, ...]:
        """Return explicitly selected non-protected targets to automatic control.

        This is intentionally not a recovery heuristic: a caller must name the
        targets. A manual-off protection is never cleared by this operation.
        """

        cleared: list[str] = []
        for target_id in sorted(set(target_ids)):
            key = (room_id, target_id)
            if key in self._manual_off:
                continue
            bucket = self._records.get(key)
            if not bucket or bucket[-1].source is not OwnershipSource.MANUAL:
                continue
            self._records.pop(key, None)
            cleared.append(target_id)
        if cleared:
            self._dirty = True
        return tuple(cleared)

    def return_room_to_automatic(
        self, room_id: str, target_ids: Iterable[str], at: int
    ) -> tuple[str, ...]:
        """Apply an explicit whole-room return to automatic control.

        This is deliberately distinct from the settings action above.  A user
        can assign it only to a confirmed physical switch gesture, so clearing
        the manual-off record is safe and remains auditable as AUTO ownership.
        """

        returned: list[str] = []
        for target_id in sorted(set(target_ids)):
            key = (room_id, target_id)
            had_manual = key in self._manual_off or (
                bool(self._records.get(key))
                and self._records[key][-1].source is OwnershipSource.MANUAL
            )
            self._manual_off.pop(key, None)
            self._records.pop(key, None)
            self.record_auto(room_id, target_id, at, confirmed=True)
            if had_manual:
                returned.append(target_id)
        return tuple(returned)

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
        reserved_target_ids: Iterable[str] = (),
        reserved_entity_ids_provider: Callable[[], Iterable[str]] | None = None,
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
        self._curve_presence: dict[str, dict[str, int | None]] = {}
        self._curve_motion: dict[str, dict[str, int | None]] = {}
        self._shower_exhaust: dict[str, dict[str, int | None]] = {}
        self._state_provider = state_provider or RoomLightingHaStateProvider(
            now_ms=now_ms,
            ownership_provider=lambda config: self._ownership.snapshots_for(
                config.room_id,
                config.devices.light_target_ids
                | frozenset(item.id for item in config.devices.auxiliaries),
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
        self._reserved_target_ids = frozenset(reserved_target_ids)
        self._reserved_entity_ids_provider = reserved_entity_ids_provider
        self._device_trigger_seen: dict[tuple[str, str, str], int] = {}
        self._device_trigger_sequence: dict[tuple[str, str, str], tuple[int, int]] = {}
        self._interval_seconds = max(1, int(interval_seconds))
        self._configs: dict[str, RoomLightingConfig] = {}
        self._rooms_by_entity: EntityRooms = {}
        self._entry_by_entity: EntityRooms = {}
        self._entry_wakeup_until: dict[str, int] = {}
        self._targets_by_entity: dict[str, tuple[str, str]] = {}
        self._power_by_entity: dict[str, str] = {}
        self._target_last_state: dict[str, str] = {}
        self._command_grace: dict[str, tuple[str, int]] = {}
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
        # Seed the observed states so the first event after start is never
        # mistaken for a manual transition.
        self._target_last_state = {
            entity_id: self._entity_state(entity_id)
            for entity_id in (*self._targets_by_entity, *self._power_by_entity)
        }
        # Legacy journals were keyed by target id only; attach them to rooms
        # now that the room/target mapping is known.
        self._ownership.migrate_legacy(
            {
                room_id: config.devices.light_target_ids
                | frozenset(item.id for item in config.devices.auxiliaries)
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
        self._observe_absence(config, context)
        return replace(context, away=self._away, protection=self._protection_for(config))

    async def async_context_for(
        self, config: RoomLightingConfig
    ) -> RoomLightingContext:
        """Build the real context for the status endpoint and the engine."""

        return await self._build_context(config)

    async def async_return_targets_to_automatic(
        self, room_id: str, target_ids: Iterable[str]
    ) -> tuple[str, ...]:
        """Explicitly drop non-protected manual ownership without device calls."""

        async with self._lock:
            config = self._configs.get(room_id)
            if config is None:
                raise ValueError("room lighting configuration is unavailable")
            allowed = set(config.devices.light_target_ids)
            selected = set(target_ids)
            if not selected or not selected <= allowed:
                raise ValueError("room lighting target selection is invalid")
            cleared = self._ownership.clear_explicit_manual_ownership(
                room_id, selected
            )
            if cleared:
                await self._persist_ownership()
            return cleared

    async def async_return_room_to_automatic(self, room_id: str) -> tuple[str, ...]:
        """Explicitly return a whole room to auto control without a device call.

        Unlike the narrow settings action, a physical switch that is configured
        as "return to auto" is an unambiguous user intent.  It may clear a
        manual-off protection, but never changes a lamp by itself.  The normal
        room evaluation that follows still decides whether any physical command
        is appropriate.
        """

        async with self._lock:
            config = self._configs.get(room_id)
            if config is None:
                raise ValueError("room lighting configuration is unavailable")
            target_ids = tuple(target.id for target in config.devices.light_targets)
            returned = self._ownership.return_room_to_automatic(
                room_id, target_ids, self._now_ms()
            )
            if target_ids:
                await self._persist_ownership()
            return returned

    async def async_execute_command(
        self, config: RoomLightingConfig, command: PlannedCommand
    ) -> dict[str, object]:
        """Execute one planned command through the shared HA executor."""

        if self._hass is None:
            return {"confirmed": False}
        if self._command_conflicts_with_reserved_writer(config, command):
            _LOGGER.warning(
                "room lighting command refused: target is owned by a controller"
            )
            return {
                "confirmed": False,
                "blocked": True,
                "reason": "reserved_command_target",
            }
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
        entry_rooms = self._entry_by_entity.get(entity_id, set())
        if entry_rooms and _is_unlock_event(data):
            until = self._now_ms() + ENTRY_WAKEUP_GRACE_MS
            for room_id in entry_rooms:
                self._entry_wakeup_until[room_id] = until
        self._observe_target_transition(entity_id)
        self._observe_power_transition(entity_id)
        self._create_task(self.async_process(sorted(rooms)))

    def _observe_target_transition(self, entity_id: str) -> None:
        """Attribution for a light that was switched off outside Home Assistant.

        A physical wall switch or a direct Zigbee binding changes the light
        state without a ``call_service`` event. The runtime used to miss that
        manual off, and the automatic branch immediately turned the light back
        on. An observed on -> off transition that this runtime did not command
        now starts the room manual-off protection, exactly like a manual off
        published through Home Assistant.
        """

        target = self._targets_by_entity.get(entity_id)
        if target is None:
            return
        state = self._entity_state(entity_id)
        previous = self._target_last_state.get(entity_id)
        self._target_last_state[entity_id] = state
        if previous is None or previous != "on" or state != "off":
            return
        moment = self._now_ms()
        commanded = self._command_grace.get(entity_id)
        if (
            commanded is not None
            # Only our own turn-off can explain an observed off report. A
            # recent automatic turn-on must never mask a person switching the
            # light off right after the automation turned it on.
            and commanded[0] == "turn_off"
            and moment - commanded[1] < COMMAND_GRACE_MS
        ):
            # Consume the marker: it explains this report only. Keeping it
            # would mask a later real manual off inside the same window
            # (a person switching the light off again after our own off).
            self._command_grace.pop(entity_id, None)
            return
        room_id, target_id = target
        self._ownership.record_manual(
            room_id, target_id, moment, confirmed=True, turned_off=True
        )
        self._absence.pop(room_id, None)
        self._schedule_ownership_save()

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
        owns_context = getattr(self._executor, "owns_service_context", None)
        if callable(owns_context) and owns_context(getattr(event, "context", None)):
            return
        for entity_id in _entity_ids(service_data):
            power_room = self._power_by_entity.get(entity_id)
            if power_room is not None:
                self._record_power_manual(power_room, entity_id, service)
                continue
            target = self._targets_by_entity.get(entity_id)
            if target is None:
                continue
            room_id, target_id = target
            moment = self._now_ms()
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

    def _observe_power_transition(self, entity_id: str) -> None:
        """Attribute a physical power-switch off without a service call.

        A wall switch can cut the room power directly; Home Assistant then
        reports only the state change. Without this the engine sees an
        unpowered room and immediately restores power and turns the light on.
        """

        room_id = self._power_by_entity.get(entity_id)
        if room_id is None:
            return
        state = self._entity_state(entity_id)
        previous = self._target_last_state.get(entity_id)
        self._target_last_state[entity_id] = state
        if previous is None or previous != "on" or state != "off":
            return
        moment = self._now_ms()
        commanded = self._command_grace.get(entity_id)
        if (
            commanded is not None
            and commanded[0] == "turn_off"
            and moment - commanded[1] < COMMAND_GRACE_MS
        ):
            self._command_grace.pop(entity_id, None)
            return
        if self._executing_actions.get(entity_id) == "turn_off":
            return
        self._record_power_manual(room_id, entity_id, "turn_off")

    def _record_power_manual(self, room_id: str, entity_id: str, service: str) -> None:
        """Attribute a manual room-power switch change to the whole room.

        The room power switch feeds one or more light targets and Home
        Assistant does not expose which ones, so every target of the room is
        attributed. This is the core guard against the race where a manual
        switch-off is immediately overridden by the automatic branch: a
        manual power-off starts the room manual-off protection, and
        ``_dispatch`` refuses to re-power the room while it is active.
        """

        config = self._configs.get(room_id)
        if config is None:
            return
        if service == "turn_on":
            # Powering a room is not itself evidence that a person selected a
            # light source. In particular, an unattributed startup call must
            # not make every target permanently manual-only.
            return
        turned_off = service == "turn_off" or (
            service == "toggle" and self._entity_is_on(entity_id)
        )
        moment = self._now_ms()
        for target in config.devices.light_targets:
            self._ownership.record_manual(
                room_id,
                target.id,
                moment,
                confirmed=True,
                turned_off=turned_off,
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
        sequence_bindings = tuple(
            binding for binding in bindings if binding.sequence_index is not None
        )
        if sequence_bindings:
            bindings = list(
                self._next_sequence_bindings(
                    config.room_id, switch_id, subtype, moment, sequence_bindings
                )
            )
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
            if binding.action is SwitchAction.RETURN_TO_AUTO:
                returned = await self.async_return_room_to_automatic(config.room_id)
                _LOGGER.info(
                    "room lighting device trigger %s/%s returned room to auto targets=%s",
                    device_id,
                    subtype,
                    ",".join(returned),
                )
                if self._running:
                    await self.async_process((config.room_id,))
                continue
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

    def _next_sequence_bindings(
        self,
        room_id: str,
        switch_id: str,
        subtype: str,
        moment: int,
        bindings: Sequence[SwitchBinding],
    ) -> tuple[SwitchBinding, ...]:
        """Select the next configurable press-sequence step for one trigger."""

        window_ms = min(
            int(binding.sequence_window_seconds or 0) for binding in bindings
        ) * 1000
        key = (room_id, switch_id, subtype)
        previous = self._device_trigger_sequence.get(key)
        count = 1
        if previous is not None and moment - previous[0] <= window_ms:
            count = previous[1] + 1
        maximum = max(int(binding.sequence_index or 0) for binding in bindings)
        selected_index = min(count, maximum)
        self._device_trigger_sequence[key] = (
            moment,
            0 if count >= maximum else count,
        )
        return tuple(
            binding for binding in bindings if binding.sequence_index == selected_index
        )

    async def _execute_binding(
        self,
        config: RoomLightingConfig,
        binding: SwitchBinding,
        target_ids: Sequence[str],
    ) -> None:
        if self._hass is None:
            return
        if self._binding_conflicts_with_reserved_writer(config, target_ids):
            _LOGGER.warning(
                "room lighting binding refused: target is owned by a controller"
            )
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
                _LOGGER.exception("room lighting binding command failed")

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
            if config.profile == "day_curve":
                entry_wakeup = self._entry_wakeup_until.get(config.room_id, 0) >= moment
                if entry_wakeup:
                    curve_presence = SensorState.ON
                    confirmed, absence_seconds = True, 0
                    motion_preview = False
                    motion_rejected = False
                else:
                    curve_presence = self._curve_sensor_state(
                        context, frozenset({SensorKind.PRESENCE})
                    )
                    confirmed, absence_seconds = self._observe_curve_presence(
                        config, context, moment, presence=curve_presence
                    )
                    motion_preview, motion_rejected, motion_confirmed = (
                        self._curve_motion_phase(config, context, moment, curve_presence)
                    )
                    confirmed = confirmed or motion_confirmed
                decision = evaluate_curve_room(
                    config,
                    context,
                    presence=curve_presence,
                    presence_confirmed=confirmed,
                    absence_seconds=absence_seconds,
                    motion_preview=motion_preview,
                    motion_rejected=motion_rejected,
                )
            else:
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
        await self._record_auxiliary_shadow(config, moment)
        await self._process_shower_exhaust(
            config, context, moment, enabled=enabled
        )
        if not enabled:
            _LOGGER.debug(
                "room lighting shadow decision journaled for %s", config.room_id
            )
            return
        # Re-check after the await: cancel() may have run while state was read.
        if not self._running:
            return
        await self._dispatch(config, decision, moment)

    def _observe_curve_presence(
        self,
        config: RoomLightingConfig,
        context: RoomLightingContext,
        moment: int,
        *,
        presence: SensorState | None = None,
    ) -> tuple[bool, int | None]:
        """Track the day-curve presence confirmation and absence for one room.

        The absence accrued before the current presence is frozen and returned
        while the presence is still unconfirmed, so a reduced brightness is
        held until the return is confirmed. After a restart no absence is
        assumed: the timer starts fresh when presence is first observed.
        """

        tracker = self._curve_presence.setdefault(
            config.room_id, {"since": None, "last_seen": None}
        )
        present = presence if presence is not None else self._curve_presence_state(context)
        if present is SensorState.ON:
            if tracker["since"] is None:
                tracker["since"] = moment
                tracker["absence"] = (
                    0
                    if tracker["last_seen"] is None
                    else max(0, (moment - int(tracker["last_seen"])) // 1000)
                )
            tracker["last_seen"] = moment
            confirmed = moment - int(tracker["since"]) >= CURVE_PRESENCE_CONFIRM_MS
            return confirmed, int(tracker.get("absence") or 0)
        tracker["since"] = None
        if present is not SensorState.OFF:
            # An unknown, unavailable or stale presence is not absence. The
            # unobserved interval is not counted at all: after the fault the
            # proven-absence window starts fresh instead of inheriting the gap.
            tracker["last_seen"] = None
            tracker["absence"] = 0
            return False, 0
        if tracker["last_seen"] is None:
            # No proven presence yet after start: do not invent an absence.
            tracker["last_seen"] = moment
            return False, 0
        absence = max(0, (moment - int(tracker["last_seen"])) // 1000)
        tracker["absence"] = absence
        return False, absence

    def _curve_motion_phase(
        self,
        config: RoomLightingConfig,
        context: RoomLightingContext,
        moment: int,
        presence: SensorState,
    ) -> tuple[bool, bool, bool]:
        """Return preview, rejection and confirmation for a motion trial.

        Motion starts a single 20-second preview. A fresh dedicated presence
        observation during that window promotes the curve immediately. Without
        it, the curve reverses to its time-of-day minimum. Another trial needs
        a real motion-off before a new motion-on, so a stuck sensor cannot keep
        restarting the lamp.
        """

        motion = self._curve_sensor_state(context, frozenset({SensorKind.MOTION}))
        tracker = self._curve_motion.setdefault(
            config.room_id, {"started": None, "deadline": None}
        )
        if motion is not SensorState.ON:
            if motion is SensorState.OFF:
                tracker["started"] = None
                tracker["deadline"] = None
            return False, False, False
        if tracker["started"] is None:
            deadline = moment + CURVE_MOTION_CONFIRM_WINDOW_MS
            tracker["started"] = moment
            tracker["deadline"] = deadline
            self._create_task(self._async_process_motion_deadline(config.room_id, deadline))
        deadline = int(tracker["deadline"] or moment)
        if presence is SensorState.ON:
            return False, False, moment <= deadline
        if moment < deadline:
            return True, False, False
        return False, True, False

    async def _async_process_motion_deadline(self, room_id: str, deadline: int) -> None:
        """Re-evaluate at the end of the motion confirmation window."""

        await asyncio.sleep(max(0, deadline - self._now_ms()) / 1000)
        if not self._running:
            return
        tracker = self._curve_motion.get(room_id)
        if tracker is None or tracker.get("deadline") != deadline:
            return
        await self.async_process((room_id,))

    @staticmethod
    def _curve_presence_state(context: RoomLightingContext) -> SensorState:
        """Classify the room presence for the day curve.

        Only a *fresh* ``on`` counts as presence. A sensor that stopped
        updating is ignored: it is neither presence nor absence. Absence is
        therefore proven by the remaining sensors when none of them is a fresh
        ``on``, none is unknown, and at least one is ``off``. This keeps a
        stuck ``on`` sensor from blocking the absence fade forever while an
        unknown or unavailable sensor still fails closed.
        """

        return RoomLightingRuntime._curve_sensor_state(context, _PRESENCE_KINDS)

    @staticmethod
    def _curve_sensor_state(
        context: RoomLightingContext, kinds: frozenset[SensorKind]
    ) -> SensorState:
        """Classify one explicit set of fresh binary occupancy sensors."""

        relevant = [sensor for sensor in context.sensors if sensor.kind in kinds]
        if not relevant:
            return SensorState.UNKNOWN
        fresh_on = False
        has_unknown = False
        has_off = False
        for sensor in relevant:
            if sensor.state in (SensorState.UNKNOWN, SensorState.UNAVAILABLE):
                has_unknown = True
                continue
            if sensor.state is SensorState.ON:
                if context.now - sensor.last_changed <= CURVE_SENSOR_FRESHNESS_MS:
                    fresh_on = True
                # A stale "on" is ignored: it proves neither occupancy nor
                # absence.
                continue
            if sensor.state is SensorState.OFF:
                has_off = True
        if fresh_on:
            return SensorState.ON
        if has_unknown:
            return SensorState.UNKNOWN
        if has_off:
            return SensorState.OFF
        return SensorState.UNKNOWN

    async def _process_shower_exhaust(
        self,
        config: RoomLightingConfig,
        context: RoomLightingContext,
        moment: int,
        *,
        enabled: bool,
    ) -> None:
        """Evaluate the shower exhaust fan and apply its own timers.

        The fan is presence-and-humidity led. The light absence handling stays
        with the room engine, so this path keeps separate presence and absence
        timers and never touches the lights. Unknown or lost sensors are never
        treated as absence, and the fan is only switched off while the runtime
        owns it.
        """

        if self._hass is None or config.auxiliary is None:
            return
        exhaust = config.auxiliary.exhaust
        if exhaust is None:
            return
        target = config.devices.auxiliary(exhaust.target_id)
        if target is None or target.entity_id is None:
            return
        policy = ShowerExhaustPolicy(
            humidity_threshold=exhaust.humidity_threshold,
            presence_run_seconds=exhaust.presence_run_seconds,
            absence_seconds=exhaust.absence_seconds,
            fan_off_seconds=exhaust.fan_off_seconds,
        )
        tracker = self._shower_exhaust.setdefault(
            config.room_id,
            {
                "presence_deadline": None,
                "absence_started": None,
                "absence_deadline": None,
            },
        )
        presence = self._curve_presence_state(context)
        elapsed: int | None = None
        if tracker["absence_started"] is not None:
            elapsed = max(
                0, (moment - int(tracker["absence_started"])) // 1000
            )

        due_kind: str | None = None
        if (
            tracker["presence_deadline"] is not None
            and moment >= int(tracker["presence_deadline"])
        ):
            due_kind = SHOWER_PRESENCE_TIMER
            tracker["presence_deadline"] = None
        elif (
            tracker["absence_deadline"] is not None
            and moment >= int(tracker["absence_deadline"])
        ):
            due_kind = SHOWER_ABSENCE_TIMER

        records = self._ownership.snapshots_for(
            config.room_id, frozenset({exhaust.target_id})
        )
        observation = ShowerExhaustObservation(
            presence=presence,
            humidity=self._humidity_value(config),
            fan=self._entity_sensor_state(target.entity_id),
            fan_owned=has_proven_auto_ownership(records, exhaust.target_id),
            lights_owned_on=self._lights_owned_on(config),
            pending_timer=due_kind,
            elapsed_seconds=elapsed,
        )
        if due_kind is not None:
            decision = evaluate_shower_exhaust_due(
                observation, timer_kind=due_kind, policy=policy
            )
        else:
            decision = evaluate_shower_exhaust(observation, policy)

        if decision.action is not None and enabled:
            await self._dispatch_exhaust(config, target, decision.action, moment)

        self._apply_shower_timers(tracker, observation, decision, moment)

        recorder = getattr(self._shadow, "async_record_exhaust", None)
        if callable(recorder):
            try:
                await recorder(
                    at=moment,
                    room_id=config.room_id,
                    observation=observation,
                    decision=decision,
                    enabled=enabled,
                )
            except Exception:  # noqa: BLE001 - journal failure must not dispatch
                _LOGGER.warning("shower exhaust journal write failed")

    @staticmethod
    def _apply_shower_timers(
        tracker: dict[str, int | None],
        observation: ShowerExhaustObservation,
        decision: object,
        moment: int,
    ) -> None:
        arm_timer = getattr(decision, "arm_timer", None)
        arm_seconds = getattr(decision, "arm_seconds", None)
        if observation.presence is SensorState.ON:
            tracker["absence_started"] = None
            tracker["absence_deadline"] = None
            if arm_timer is ShowerTimer.PRESENCE:
                if tracker["presence_deadline"] is None:
                    tracker["presence_deadline"] = moment + int(
                        arm_seconds or 0
                    ) * 1000
            else:
                tracker["presence_deadline"] = None
            return
        if observation.presence is not SensorState.OFF:
            # Unknown presence: never fire the presence timer and keep the
            # absence clock frozen.
            tracker["presence_deadline"] = None
            return
        tracker["presence_deadline"] = None
        if arm_timer is ShowerTimer.ABSENCE:
            if tracker["absence_started"] is None:
                tracker["absence_started"] = moment
            tracker["absence_deadline"] = moment + int(arm_seconds or 0) * 1000
        else:
            tracker["absence_started"] = None
            tracker["absence_deadline"] = None

    async def _dispatch_exhaust(
        self,
        config: RoomLightingConfig,
        target: object,
        action: ShowerFanAction,
        moment: int,
    ) -> None:
        target_id = target.id  # type: ignore[attr-defined]
        entity_id = target.entity_id  # type: ignore[attr-defined]
        command = PlannedCommand(
            target_id,
            (
                LightAction.TURN_ON
                if action is ShowerFanAction.TURN_ON
                else LightAction.TURN_OFF
            ),
            reason=DecisionReason.SCHEDULE,
        )
        if self._command_conflicts_with_reserved_writer(config, command):
            _LOGGER.warning(
                "shower exhaust dispatch refused: target is owned by a controller"
            )
            return
        self._executing_actions[entity_id] = _planned_service_name(command)
        try:
            receipt = await self._executor.execute(  # type: ignore[attr-defined]
                self._hass, command
            )
        except Exception:  # noqa: BLE001 - a failed command is not a crash
            _LOGGER.warning("shower exhaust command failed")
            receipt = None
        finally:
            self._executing_actions.pop(entity_id, None)
        if _receipt_confirmed(receipt):
            self._ownership.record_auto(
                config.room_id, target_id, moment, confirmed=True
            )
            self._schedule_ownership_save()
            await self._persist_ownership()

    def _lights_owned_on(self, config: RoomLightingConfig) -> bool:
        for target in config.devices.light_targets:
            if target.entity_id is None or not self._entity_is_on(target.entity_id):
                continue
            records = self._ownership.snapshots_for(
                config.room_id, frozenset({target.id})
            )
            if has_proven_auto_ownership(records, target.id):
                return True
        return False

    def _humidity_value(self, config: RoomLightingConfig) -> float | None:
        if self._hass is None:
            return None
        for sensor in config.devices.sensors:
            if sensor.kind is not SensorKind.HUMIDITY or sensor.entity_id is None:
                continue
            state = self._hass.states.get(sensor.entity_id)
            raw = getattr(state, "state", None)
            try:
                value = float(str(raw))
            except (TypeError, ValueError):
                continue
            if math.isfinite(value):
                return value
        return None

    def _entity_sensor_state(self, entity_id: str) -> SensorState:
        raw = self._entity_state(entity_id)
        if raw == "on":
            return SensorState.ON
        if raw == "off":
            return SensorState.OFF
        if raw == "unavailable":
            return SensorState.UNAVAILABLE
        return SensorState.UNKNOWN

    async def _record_auxiliary_shadow(
        self, config: RoomLightingConfig, moment: int
    ) -> None:
        """Compute and journal the pure auxiliary decision; never command.

        This is the shadow-parity path for the bathroom fan: the legacy
        controller is still the single writer, so the new engine only records
        what it would decide. A room without an auxiliary policy, or one whose
        fan cannot be mapped to exactly two lights, is skipped without a guess.
        """

        if self._hass is None or config.auxiliary is None:
            return
        recorder = getattr(self._shadow, "async_record_auxiliary", None)
        if not callable(recorder):
            return
        try:
            inputs = bathroom_auxiliary_inputs(config)
            observation = (
                self._state_provider.build_auxiliary_observation(
                    self._hass, config, moment
                )
                if inputs is not None
                else None
            )
            if inputs is None or observation is None:
                return
            decision = evaluate_bathroom_exhaust(observation, inputs[0])
            await recorder(
                at=moment,
                room_id=config.room_id,
                observation=observation,
                decision=decision,
            )
        except Exception:  # noqa: BLE001 - shadow evidence must never break a room
            _LOGGER.warning("room lighting auxiliary shadow failed")

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
        protection = self._protection_for(config)
        auto_on_blocked = protection.blocks_auto_on(
            now=moment,
            absence_proven=protection.absence_confirmed,
            absence_since=protection.absence_since,
        )
        failed_curve_targets: set[str] = set()
        for command in decision.commands:
            if not self._running:
                return
            if config.profile == "day_curve":
                # Each awaited device call can be interleaved with a manual
                # action or a failed transition. Re-evaluate from current
                # evidence before sending the next step, especially mirror OFF.
                current = await self._build_context(config, self._now_ms())
                if (
                    command.target_id in failed_curve_targets
                    or curve_profile_block_reason(config, current) is not None
                ):
                    continue
                if command.action is LightAction.TURN_OFF:
                    presence = self._curve_presence_state(current)
                    confirmed, absence_seconds = self._observe_curve_presence(
                        config, current, current.now, presence=presence
                    )
                    refreshed = evaluate_curve_room(
                        config, current, presence=presence,
                        presence_confirmed=confirmed, absence_seconds=absence_seconds,
                    )
                    if command not in refreshed.commands:
                        continue
            if self._command_conflicts_with_reserved_writer(config, command):
                _LOGGER.warning(
                    "room lighting dispatch refused: target is owned by a controller"
                )
                continue
            if command.action is not LightAction.TURN_OFF and auto_on_blocked:
                # Core race guard: a manual switch-off owns the room until its
                # protection window releases. The automatic branch must never
                # re-power a light or turn a target back on right after a
                # person switched it off.
                continue
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
                    self._executing_actions[power_entity] = "turn_on"
                    self._command_grace[power_entity] = ("turn_on", self._now_ms())
                    try:
                        powered = bool(await ensure_power(self._hass, power_entity))
                    except Exception:  # noqa: BLE001 - keep the room isolated
                        powered = False
                    finally:
                        self._executing_actions.pop(power_entity, None)
                if not powered:
                    _LOGGER.warning(
                        "room lighting power switch is still off; "
                        "skipping the target command"
                    )
                    continue
                if config.profile == "day_curve":
                    current = await self._build_context(config, self._now_ms())
                    if curve_profile_block_reason(config, current) is not None:
                        continue
            # Power-on and context reads can yield to another controller.
            # Its newly acquired reservation must win before target dispatch.
            if not self._running:
                return
            if self._command_conflicts_with_reserved_writer(config, command):
                continue
            target = config.devices.target(command.target_id)
            entity_id = target.entity_id if target is not None else None
            if entity_id is not None:
                self._executing_actions[entity_id] = _planned_service_name(command)
                self._command_grace[entity_id] = (_planned_service_name(command), self._now_ms())
            try:
                receipt = await self._executor.execute(  # type: ignore[attr-defined]
                    self._hass, command
                )
            except Exception:  # noqa: BLE001 - a failed command is not a crash
                _LOGGER.exception("room lighting command failed")
                receipt = None
            finally:
                if entity_id is not None:
                    self._executing_actions.pop(entity_id, None)
            if _receipt_confirmed(receipt):
                self._ownership.record_auto(
                    config.room_id, command.target_id, moment, confirmed=True
                )
                self._schedule_ownership_save()
            elif config.profile == "day_curve":
                failed_curve_targets.add(command.target_id)
        await self._persist_ownership()

    # -- helpers -----------------------------------------------------------

    def _reserved_entity_ids(self) -> frozenset[str] | None:
        provider = self._reserved_entity_ids_provider
        if provider is None:
            return frozenset()
        try:
            return frozenset(
                entity_id
                for entity_id in provider()
                if isinstance(entity_id, str) and entity_id
            )
        except Exception:  # noqa: BLE001 - an unknown owner must fail closed
            _LOGGER.warning("room lighting reserved entity inventory is unavailable")
            return None

    def _is_reserved_target_or_entity(
        self, target_id: str | None, entity_id: str | None
    ) -> bool:
        if target_id is not None and target_id in self._reserved_target_ids:
            return True
        reserved_entities = self._reserved_entity_ids()
        return reserved_entities is None or (
            entity_id is not None and entity_id in reserved_entities
        )

    def _command_conflicts_with_reserved_writer(
        self, config: RoomLightingConfig, command: PlannedCommand
    ) -> bool:
        target = config.devices.target(command.target_id)
        target_entity = target.entity_id if target is not None else None
        if self._is_reserved_target_or_entity(command.target_id, target_entity):
            return True
        power = config.devices.power_switch
        return power is not None and self._is_reserved_target_or_entity(
            power.id, power.entity_id
        )

    def _binding_conflicts_with_reserved_writer(
        self, config: RoomLightingConfig, target_ids: Sequence[str]
    ) -> bool:
        if any(
            self._is_reserved_target_or_entity(
                target_id,
                config.devices.target(target_id).entity_id
                if config.devices.target(target_id) is not None
                else None,
            )
            for target_id in target_ids
        ):
            return True
        power = config.devices.power_switch
        return power is not None and self._is_reserved_target_or_entity(
            power.id, power.entity_id
        )

    def _entity_is_on(self, entity_id: str) -> bool:
        if self._hass is None:
            return False
        state = self._hass.states.get(entity_id)
        return state is not None and str(getattr(state, "state", "")).lower() == "on"

    def _entity_state(self, entity_id: str) -> str:
        if self._hass is None:
            return "unknown"
        state = self._hass.states.get(entity_id)
        if state is None:
            return "unknown"
        return str(getattr(state, "state", "unknown")).lower()

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
        entry_by_entity: EntityRooms = {}
        targets_by_entity: dict[str, tuple[str, str]] = {}
        power_by_entity: dict[str, str] = {}
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
                    if sensor.kind is SensorKind.ENTRY:
                        entry_by_entity.setdefault(sensor.entity_id, set()).add(
                            config.room_id
                        )
            power = config.devices.power_switch
            if power is not None and power.entity_id:
                # A power switch changes the effective state of every target in
                # the room, so its state changes must trigger a recompute.
                rooms_by_entity.setdefault(power.entity_id, set()).add(
                    config.room_id
                )
                power_by_entity.setdefault(power.entity_id, config.room_id)
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
            for auxiliary in config.devices.auxiliaries:
                targets[auxiliary.id] = auxiliary
                if auxiliary.entity_id:
                    rooms_by_entity.setdefault(auxiliary.entity_id, set()).add(
                        config.room_id
                    )
                    targets_by_entity.setdefault(
                        auxiliary.entity_id,
                        (config.room_id, auxiliary.id),
                    )
        self._rooms_by_entity = rooms_by_entity
        self._entry_by_entity = entry_by_entity
        self._targets_by_entity = targets_by_entity
        self._power_by_entity = power_by_entity
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
        if any(sensor.state in {SensorState.UNKNOWN, SensorState.UNAVAILABLE}
               for sensor in relevant):
            self._absence.pop(config.room_id, None)
            return
        if config.profile == "day_curve" and any(
            sensor.state is SensorState.ON
            and context.now - sensor.last_changed > CURVE_SENSOR_FRESHNESS_MS
            for sensor in relevant
        ):
            self._absence.pop(config.room_id, None)
            return
        for sensor in relevant:
            if sensor.state is SensorState.ON:
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
            _discard_coroutine(coroutine)
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
            target_id,
            LightAction.TURN_ON,
            brightness=binding.brightness,
            color_temperature=binding.color_temperature,
            reason=DecisionReason.PRESENCE,
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
                brightness=binding.brightness or 100,
                color_temperature=binding.color_temperature,
                reason=DecisionReason.PRESENCE,
            )
        return PlannedCommand(
            target_id,
            LightAction.TURN_ON,
            color_temperature=binding.color_temperature,
            reason=DecisionReason.PRESENCE,
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
