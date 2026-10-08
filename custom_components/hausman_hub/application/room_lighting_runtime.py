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
from datetime import date, datetime, timedelta, timezone
import logging
import math
import re
import time
from typing import TYPE_CHECKING

from ..domain.device_power_dependencies import (
    AUTO_TURN_ON_POLICY,
    DevicePowerDependency,
    effective_device_state,
)
from ..domain.room_lighting import (
    RoomLightingConfig,
    SensorKind,
    SwitchAction,
    SwitchBinding,
    room_lighting_entity_collisions,
)
from ..domain.room_lighting_auxiliary import evaluate_bathroom_exhaust
from ..domain.protected_away import (
    ProtectedAwayObservation,
    classify_protected_away,
)
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
    EnginePolicy,
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
    latest_ownership,
)
from .power_evidence import (
    state_is_restored_or_cached,
    trusted_power_state,
    trusted_target_observed_ms,
    trusted_target_state,
)
from .room_lighting_ha_executor import RoomLightingHaExecutor
from .manual_light_auto_return import ManualLightAutoReturn
from .room_lighting_ha_state import RoomLightingHaStateProvider, _resolve_timezone
from .room_power_migration import (
    EXACT_POWER_TARGET_MANIFEST,
    ExactPowerDecision,
    decide_exact_power,
)

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
# Default manual priority (owner rule): an active legacy light-priority
# claim transfers to the shadow branch as a hold of at most this length.
MANUAL_PRIORITY_TRANSFER_MS = 3_600_000


def _opaque_entity_tag(entity_id: str) -> str:
    """Stable opaque tag: HA entity names never cross the adapter."""

    import hashlib

    return hashlib.sha256(entity_id.encode("utf-8")).hexdigest()[:16]
CURVE_SENSOR_FRESHNESS_MS = 300_000
# A morning boundary date must be a real calendar day; a damaged storage
# entry is dropped instead of being trusted as "already transitioned".
_MORNING_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
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
_REPORT_FUTURE_SKEW_MS = 60_000
# Compatibility fixture id for framework tests. Production setup injects the
# per-entry protected binding; an absent binding is not treated as factual home.
AWAY_ENTITY_ID = "away_sensor_fixture"
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


def _valid_morning_date(value: object) -> bool:
    """Whether the value is a real calendar day in ``YYYY-MM-DD`` form."""

    if not isinstance(value, str) or _MORNING_DATE.fullmatch(value) is None:
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


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
        # Morning boundary day-state per room. Two shapes: a transitioned day
        # ``{"date", "at"}`` and an in-progress overnight wait
        # ``{"date", "at", "waiting": True, "boundaryMs"}``. Survives a
        # restart so the day profile starts exactly once per day and a wait
        # for the first presence survives midnight.
        self._morning: dict[str, dict[str, object]] = {}
        self._dirty = False
        # The physical return gesture has to distinguish history that existed
        # before it from a manual action delivered while a durable write waits.
        self._manual_generation = 0
        self._manual_record_generation: dict[tuple[str, str], int] = {}
        self._manual_off_generation: dict[tuple[str, str], int] = {}
        self._revision = 0

    @property
    def dirty(self) -> bool:
        return self._dirty

    def mark_clean(self) -> None:
        self._dirty = False

    @property
    def revision(self) -> int:
        return self._revision

    def mark_clean_if_revision(self, revision: int) -> bool:
        if self._revision != revision:
            return False
        self._dirty = False
        return True

    def _mark_dirty(self) -> None:
        self._revision += 1
        self._dirty = True

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
        self._manual_generation += 1
        self._manual_record_generation[(room_id, target_id)] = self._manual_generation
        if turned_off:
            self._manual_off[(room_id, target_id)] = moment
            self._manual_off_generation[(room_id, target_id)] = self._manual_generation
        self._mark_dirty()
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
        self._mark_dirty()
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
        unique across the current room configs is attached to that room.
        Ambiguous and missing ids stay in the legacy journal: dropping them
        would erase the evidence before the shared manual authority can close
        automatic light routes safely.
        """

        if not self.has_legacy():
            return
        owned = {
            room_id: set(target_ids)
            for room_id, target_ids in rooms_targets.items()
        }
        unresolved_records: list[OwnershipSnapshot] = []
        migrated = False
        for record in self._legacy_records:
            room_id = _single_room_for(record.target_id, owned)
            if room_id is not None:
                self._append(room_id, record)
                migrated = True
            else:
                unresolved_records.append(record)
        unresolved_manual_off: dict[str, int] = {}
        for target_id, moment in self._legacy_manual_off.items():
            room_id = _single_room_for(target_id, owned)
            if room_id is not None:
                self._manual_off[(room_id, target_id)] = moment
                migrated = True
            else:
                unresolved_manual_off[target_id] = moment
        self._legacy_records = unresolved_records
        self._legacy_manual_off = unresolved_manual_off
        if migrated:
            self._mark_dirty()

    def legacy_manual_target_ids(self) -> frozenset[str]:
        """Read-only legacy manual targets for migration into shared authority."""
        return frozenset(
            [
                record.target_id
                for record in self._legacy_records
                if record.source is OwnershipSource.MANUAL
            ]
            + list(self._legacy_manual_off)
        )

    def manual_authority_target_refs(self) -> tuple[tuple[str | None, str], ...]:
        """Read active manual evidence without changing its persisted shape."""
        refs = [
            (room_id, target_id)
            for (room_id, target_id), bucket in self._records.items()
            if (room_id, target_id) in self._manual_off
            or (bucket and bucket[-1].source is OwnershipSource.MANUAL)
        ]
        refs.extend((None, target_id) for target_id in self.legacy_manual_target_ids())
        return tuple(refs)

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
            self._manual_off_generation.pop((room_id, target_id), None)
            self._records.pop((room_id, target_id), None)
            self._manual_record_generation.pop((room_id, target_id), None)
            removed = True
        if removed:
            self._mark_dirty()

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
            self._manual_record_generation.pop(key, None)
            cleared.append(target_id)
        if cleared:
            self._mark_dirty()
        return tuple(cleared)

    def return_room_to_automatic(
        self, room_id: str, target_ids: Iterable[str], at: int
    ) -> tuple[str, ...]:
        """Keep manual holds until the common durable release can prove them.

        A return gesture is not a confirmed automatic command.  Recording AUTO
        here would fabricate ownership and could later authorize a light-off.
        The deny-only HH-008.b stage intentionally leaves the journal intact.
        """
        del room_id, target_ids, at
        return ()

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
            "morning": [
                {
                    "roomId": room_id,
                    "date": entry.get("date"),
                    "at": entry.get("at"),
                    **(
                        {"waiting": True, "boundaryMs": entry.get("boundaryMs")}
                        if entry.get("waiting")
                        else {}
                    ),
                }
                for room_id, entry in sorted(self._morning.items())
                if isinstance(entry, Mapping)
            ],
        }

    def morning_entry(self, room_id: str) -> dict[str, object] | None:
        """The room's persisted morning day-state, if any."""

        entry = self._morning.get(room_id)
        return dict(entry) if isinstance(entry, Mapping) else None

    def morning_transition_date(self, room_id: str) -> str | None:
        """Local date of the room's last morning transition, if any.

        A room still waiting for its first presence has no transition date.
        """

        entry = self.morning_entry(room_id)
        if entry is None or entry.get("waiting"):
            return None
        date_value = entry.get("date")
        return date_value if _valid_morning_date(date_value) else None

    def mark_morning_transition(self, room_id: str, date_value: str, at: int) -> None:
        """Record that the room's day profile started for the local date."""

        if not _valid_morning_date(date_value):
            _LOGGER.warning(
                "room lighting rejected an invalid morning date for %s", room_id
            )
            return
        self._morning[room_id] = {"date": date_value, "at": at}
        self._mark_dirty()

    def mark_morning_waiting(
        self, room_id: str, date_value: str, boundary_ms: int, at: int
    ) -> None:
        """Record an in-progress wait for the first presence after a boundary.

        The wait survives midnight and a restart: the room keeps its night
        state until the first trusted presence releases it, no matter how
        many days pass.
        """

        if not _valid_morning_date(date_value) or type(boundary_ms) is not int:
            _LOGGER.warning(
                "room lighting rejected an invalid morning wait for %s", room_id
            )
            return
        self._morning[room_id] = {
            "date": date_value,
            "at": at,
            "waiting": True,
            "boundaryMs": boundary_ms,
        }
        self._mark_dirty()

    def release_room_manual_holds(
        self, room_id: str, target_ids, *, before_ms: int
    ) -> int:
        """Drop manual evidence for the room's light targets.

        The morning boundary returns command rights to the automatic profile:
        manual records and manual-off marks dated before ``before_ms`` are
        forgotten so the day profile is no longer blocked by the previous
        evening's manual actions. A manual action the person made after the
        boundary keeps its ordinary hold. The cutoff is mandatory: an
        unbounded release of manual protection is not an offered operation.
        Automatic records are kept: proven automatic ownership must survive
        so the engine can still turn a light off by itself. The caller logs
        the reason; the release itself never commands light.
        """

        removed = 0
        for target_id in target_ids:
            key = (room_id, target_id)
            bucket = self._records.get(key)
            if bucket:
                kept = [
                    record
                    for record in bucket
                    if record.source is not OwnershipSource.MANUAL
                    or record.at >= before_ms
                ]
                if len(kept) != len(bucket):
                    removed += len(bucket) - len(kept)
                    if kept:
                        self._records[key] = kept
                    else:
                        self._records.pop(key, None)
                    self._manual_record_generation.pop(key, None)
            moment = self._manual_off.get(key)
            if moment is not None and moment < before_ms:
                del self._manual_off[key]
                self._manual_off_generation.pop(key, None)
                removed += 1
        if removed:
            self._mark_dirty()
        return removed

    def restore(self, payload: object) -> None:
        """Load a persisted journal, ignoring any damaged entry."""

        self._records = {}
        self._manual_off = {}
        self._manual_record_generation = {}
        self._manual_off_generation = {}
        self._legacy_records = []
        self._legacy_manual_off = {}
        self._morning = {}
        self._manual_generation = 0
        self._revision = 0
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
                    if record.source is OwnershipSource.MANUAL:
                        self._manual_record_generation[(room_id, record.target_id)] = 0
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
                    self._manual_off_generation[(room_id, target_id)] = 0
                elif (
                    isinstance(target_id, str)
                    and target_id
                    and type(moment) is int
                    and moment >= 0
                ):
                    # ``to_payload`` has emitted legacy manual-off entries as
                    # a list since the room-aware format was introduced. Keep
                    # their missing room key instead of erasing the hold on
                    # the next restart.
                    previous = self._legacy_manual_off.get(target_id)
                    if previous is None or moment > previous:
                        self._legacy_manual_off[target_id] = moment
        raw_morning = payload.get("morning")
        if isinstance(raw_morning, (list, tuple)):
            for raw in raw_morning:
                if not isinstance(raw, Mapping):
                    continue
                room_id = raw.get("roomId")
                at = raw.get("at")
                if not (
                    isinstance(room_id, str)
                    and room_id
                    and _valid_morning_date(raw.get("date"))
                    and type(at) is int
                    and at >= 0
                ):
                    continue
                waiting = raw.get("waiting") is True
                boundary_ms = raw.get("boundaryMs")
                if waiting:
                    # A damaged wait is dropped, never demoted to a completed
                    # transition: demoting it would silently disable the
                    # presence gate for the whole day.
                    if type(boundary_ms) is int and boundary_ms >= 0:
                        self._morning[room_id] = {
                            "date": raw.get("date"),
                            "at": at,
                            "waiting": True,
                            "boundaryMs": boundary_ms,
                        }
                    continue
                if "boundaryMs" in raw or "waiting" in raw:
                    # A mixed shape (neither a clean transition nor a valid
                    # wait) is damage: drop it instead of guessing.
                    continue
                self._morning[room_id] = {"date": raw.get("date"), "at": at}
        self._dirty = False

    def manual_return_boundary(self) -> int:
        """Freeze the manual-event generation at the return gesture."""

        return self._manual_generation

    def room_history_is_releasable(
        self,
        room_id: str,
        target_ids: Iterable[str],
        authority_target_ids: Iterable[str],
        *,
        through_generation: int,
    ) -> bool:
        """Match common manual-ON evidence to room history, fail closed."""

        scope = tuple(target_ids)
        evidence = frozenset(authority_target_ids)
        if not evidence or not evidence <= set(scope):
            return False
        for target_id in evidence:
            bucket = self._records.get((room_id, target_id), ())
            latest = bucket[-1] if bucket else None
            if (
                latest is None
                or latest.source is not OwnershipSource.MANUAL
                or not latest.confirmed
            ):
                return False

        return not any(
            (room_id, target_id) in self._manual_off
            and self._manual_off_generation.get((room_id, target_id), 0)
            <= through_generation
            for target_id in target_ids
        )

    def release_room_manual_history_through(
        self,
        room_id: str,
        target_ids: Iterable[str],
        authority_target_ids: Iterable[str],
        *,
        through_generation: int,
    ) -> tuple[str, ...] | None:
        """Remove only pre-gesture manual history for the exact room scope.

        A manual event observed while a storage write is outstanding has a
        later generation and wins, even if a test clock gives both events the
        same millisecond.
        """

        if not self.room_history_is_releasable(
            room_id, target_ids, authority_target_ids,
            through_generation=through_generation,
        ):
            return None
        cleared: list[str] = []
        for target_id in sorted(set(target_ids)):
            key = (room_id, target_id)
            changed = False
            bucket = self._records.get(key, [])
            manual_generation = self._manual_record_generation.get(key, 0)
            if manual_generation <= through_generation:
                # Do not leave an old AUTO behind. It could later be mistaken
                # for current ownership and authorize an automatic OFF.
                if bucket:
                    self._records.pop(key, None)
                    changed = True
                self._manual_record_generation.pop(key, None)
            else:
                # A fresh manual action won while the two stores were being
                # reconciled. Keep only that current manual evidence, never a
                # pre-gesture AUTO record.
                newest_manual = next(
                    (record for record in reversed(bucket)
                     if record.source is OwnershipSource.MANUAL),
                    None,
                )
                if newest_manual is None:
                    return None
                if bucket != [newest_manual]:
                    self._records[key] = [newest_manual]
                    changed = True
            if changed:
                cleared.append(target_id)
        if cleared:
            self._mark_dirty()
        return tuple(cleared)


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


def legacy_manual_authority_plan(
    journal: RoomLightingOwnershipJournal,
    configs: Iterable[RoomLightingConfig],
) -> tuple[tuple[str, ...], bool]:
    """Map legacy manual target ids to unique physical light entities.

    The result is deliberately read-only.  Legacy target ids had no room key,
    so a missing or multiply-owned light target cannot be guessed.  Auxiliary
    fan ids are outside the manual-light boundary and are ignored rather than
    becoming a fabricated light claim.
    """
    configs = tuple(configs)
    light_entities: dict[str, list[str | None]] = {}
    auxiliary_ids: set[str] = set()
    auxiliary_by_room: dict[str, set[str]] = {}
    for config in configs:
        for target in config.devices.light_targets:
            light_entities.setdefault(target.id, []).append(target.entity_id)
        auxiliary_ids.update(target.id for target in config.devices.auxiliaries)
        auxiliary_by_room[config.room_id] = {
            target.id for target in config.devices.auxiliaries
        }

    entities: list[str] = []
    uncertain = False
    for room_id, target_id in journal.manual_authority_target_refs():
        candidates = (
            [
                target.entity_id
                for config in configs
                if config.room_id == room_id
                for target in config.devices.light_targets
                if target.id == target_id
            ]
            if room_id is not None
            else light_entities.get(target_id, [])
        )
        if len(candidates) != 1:
            # A fan is outside this boundary only when no room exposes this
            # legacy id as a light.  An auxiliary with the same id must never
            # mask a missing or multiply-owned physical light mapping.
            is_auxiliary = (
                not light_entities.get(target_id)
                and target_id in (
                    auxiliary_ids if room_id is None
                    else auxiliary_by_room.get(room_id, set())
                )
            )
            if not is_auxiliary:
                uncertain = True
            continue
        entity_id = candidates[0]
        if not isinstance(entity_id, str) or not entity_id.startswith(
            ("light.", "switch.")
        ):
            uncertain = True
            continue
        entities.append(entity_id)
    if len(set(entities)) != len(entities):
        uncertain = True
    return tuple(sorted(set(entities))), uncertain


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
        track_time_change: Callable[..., Callable[[], None]] | None = None,
        listen_bus: Callable[..., Callable[[], None]] | None = None,
        device_automation_api: object | None = None,
        reserved_target_ids: Iterable[str] = (),
        reserved_entity_ids_provider: Callable[[], Iterable[str]] | None = None,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
        away_debounce_seconds: float = AWAY_ON_DEBOUNCE_SECONDS,
        power_dependency_resolver: (
            Callable[[], Mapping[str, DevicePowerDependency] | None] | None
        ) = None,
        electrical_breaker_resolver: Callable[[str], bool] | None = None,
        power_migration_manifest: Mapping[str, str] | None = None,
        away_entity_id: str | None = None,
        light_priority: object | None = None,
        manual_light_authority: object | None = None,
        core_input_port: Callable[[str, object, object], None] | None = None,
    ) -> None:
        # Optional input port of the new core branch (HH-014.b lighting
        # path): immutable event snapshots are delivered BEFORE the legacy
        # ownership history changes. The port never dispatches; the legacy
        # executor stays the only writer.
        self._core_input_port = core_input_port
        # Core-input task tracking (AUD-LP-ISO01 cycle 6): dispatched port
        # calls are awaited on stop, so the store is never written after
        # the runtime stopped and the stop hook runs last.
        self._core_input_tasks: set = set()
        self._core_input_closed = False
        self._hass = hass
        self._service = service
        self._shadow = shadow
        self._ownership = ownership or RoomLightingOwnershipJournal(now_ms=now_ms)
        self._ownership_store = ownership_store
        self._unobserved_since: int | None = None
        # Restart timestamp kept separately so clearing the unobserved guard on
        # the first resumed observation does not let pre-restart absence count.
        self._started_at: int | None = None
        self._absence: AbsenceState = {}
        self._curve_presence: dict[str, dict[str, int | None]] = {}
        self._curve_motion: dict[str, dict[str, int | None]] = {}
        self._shower_exhaust: dict[str, dict[str, int | None]] = {}
        # A resolver exposes the live durable map. Only a room with an explicit
        # release-owned migration claim switches to exact per-target semantics;
        # every other room keeps the legacy whole-room power association.
        self._power_dependency_resolver = power_dependency_resolver
        self._electrical_breaker_resolver = electrical_breaker_resolver
        self._power_migration_manifest = (
            EXACT_POWER_TARGET_MANIFEST
            if power_migration_manifest is None
            else dict(power_migration_manifest)
        )
        # A target or power source whose dispatch was not confirmed keeps its
        # pre-attempt evidence revision here: the same command is not repeated
        # while the device reports exactly the same evidence. The shadow journal
        # restores these blocks after a restart through the receipts it carries.
        self._dispatch_failures: dict[tuple[str, str], tuple[str, str]] = {}
        # Последняя отправленная на недостоверном свидетельстве регулировка
        # (молчание светильника): дедупликация — одна и та же регулировка не
        # отправляется повторно, пока прибор не отчитается новым значением.
        self._last_untrusted_adjustment: dict[str, tuple[int | None, int | None]] = {}
        self._power_failures: dict[str, tuple[str, str]] = {}
        self._dispatch_overflow_latch = False
        self._dispatch_overflow_blocked_after_ms: int | None = None
        self._dispatch_overflow_boundary_unknown = False
        self._topology_warned: set[tuple[str, tuple[str, ...]]] = set()
        self._state_provider = state_provider or RoomLightingHaStateProvider(
            now_ms=now_ms,
            ownership_provider=lambda config: self._ownership.snapshots_for(
                config.room_id,
                config.devices.light_target_ids
                | frozenset(item.id for item in config.devices.auxiliaries),
            ),
            protection_provider=self._protection_for,
            unobserved_since_provider=lambda: self._unobserved_since,
            power_dependency_resolver=power_dependency_resolver,
            power_migration_manifest=self._power_migration_manifest,
        )
        self._manual_light_authority = manual_light_authority
        self._executor = executor or RoomLightingHaExecutor(
            manual_light_authority=manual_light_authority
        )
        self._now_ms = now_ms or _default_now_ms
        self._track_state_changes = track_state_changes
        self._track_interval = track_interval
        self._track_time_change = track_time_change
        self._listen_bus = listen_bus
        self._device_automation_api = device_automation_api
        self._reserved_target_ids = frozenset(reserved_target_ids)
        self._reserved_entity_ids_provider = reserved_entity_ids_provider
        self._device_trigger_seen: dict[tuple[str, str, str], int] = {}
        self._device_trigger_sequence: dict[tuple[str, str, str], tuple[int, int]] = {}
        self._interval_seconds = max(1, int(interval_seconds))
        self._operation_sequence = 0
        self._configs: dict[str, RoomLightingConfig] = {}
        self._rooms_by_entity: EntityRooms = {}
        # Sensors, the legacy room power switch and the light targets. Exact
        # power sources are merged into `_rooms_by_entity` from the live map.
        self._core_rooms_by_entity: EntityRooms = {}
        self._exact_power_by_entity: dict[str, set[str]] = {}
        self._exact_source_unsubscribe: Callable[[], None] | None = None
        self._exact_source_entities: frozenset[str] = frozenset()
        self._entry_by_entity: EntityRooms = {}
        self._entry_wakeup_until: dict[str, int] = {}
        self._targets_by_entity: dict[str, tuple[str, str]] = {}
        self._power_by_entity: dict[str, str] = {}
        self._target_last_state: dict[str, str] = {}
        self._command_grace: dict[str, tuple[str, int]] = {}
        self._executing_actions: dict[str, str] = {}
        self._unsubscribers: list[Callable[[], None]] = []
        self._lock = asyncio.Lock()
        self._room_locks: dict[str, asyncio.Lock] = {}
        self._power_source_locks: dict[str, asyncio.Lock] = {}
        self._save_lock = asyncio.Lock()
        self._running = False
        self._away = False
        # One warning per room and process for a presence-gated boundary with
        # no configured presence/motion sensor: the room would wait forever.
        self._morning_wait_warned: set[str] = set()
        self._away_entity_id = (
            away_entity_id.strip()
            if isinstance(away_entity_id, str) and away_entity_id.strip()
            else None
        )
        self._away_diagnostic: str | None = (
            None if self._away_entity_id is not None else "protected_topology_missing"
        )
        self._away_missing_warned = False
        self._away_generation = 0
        self._away_debounce_seconds = max(0.0, float(away_debounce_seconds))
        # Shared with ScenarioExecutor.  A durable manual marker is a deny-only
        # boundary: it never manufactures room ownership or sends a command.
        self._light_priority = light_priority

    # -- lifecycle ---------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._running

    @property
    def away(self) -> bool:
        """Runtime-wide away state fed from the A100 Away sensor."""

        return self._away

    @property
    def away_diagnostic(self) -> str | None:
        """Readiness reason for the protected away sensor binding."""

        return self._away_diagnostic

    def set_power_migration_manifest(self, manifest: Mapping[str, str]) -> None:
        """Apply a protected exact-power claim update without a restart."""

        self._power_migration_manifest = dict(manifest)
        provider_update = getattr(
            self._state_provider,
            "set_power_migration_manifest",
            None,
        )
        if callable(provider_update):
            provider_update(self._power_migration_manifest)
        self._refresh_exact_power_index()

    def _room_commands_enabled(self, config: RoomLightingConfig) -> bool:
        """Whether a room permits explicitly manual physical commands.

        A device-trigger binding is an attributable physical press. It must not
        inherit the automatic Away fail-closed permit, which protects only
        unattended light and exhaust actions.
        """

        return bool(config.commands_enabled)

    def _automatic_away_block_reason(self) -> str | None:
        """Return the fail-closed reason for an unattended command, if any."""

        observation = self._protected_away_observation()
        if observation.diagnostic == "protected_topology_missing":
            if not self._away_missing_warned:
                self._away_missing_warned = True
                _LOGGER.warning(
                    "room lighting automatic commands are disabled: "
                    "protected away sensor role is missing"
                )
        self._away_diagnostic = observation.diagnostic
        return observation.command_block_reason

    def _automatic_commands_enabled(self, config: RoomLightingConfig) -> bool:
        """Permit only automatic commands with a trusted protected Away state."""

        return (
            self._room_commands_enabled(config)
            and self._automatic_away_block_reason() is None
        )

    async def start(
        self, hass: HomeAssistant, entry_id: str
    ) -> Callable[[], None]:
        if self._running:
            raise RuntimeError("room lighting runtime is already active")
        self._hass = hass
        self._load_trackers()
        self._away_generation += 1
        away_state = self._current_away_state(hass)
        self._away = away_state is True
        # The gap starts at start: an unobserved interval is never counted as
        # absence and cannot restore pre-restart automatic ownership.
        self._unobserved_since = self._now_ms()
        self._started_at = self._unobserved_since
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
        # Merge the live exact map into the event index before the first
        # subscription, and restore the durable retry blocks from the receipts
        # that survived the previous run.
        self._refresh_exact_power_index()
        self._restore_dispatch_failures()
        # Seed the observed states so the first event after start is never
        # mistaken for a manual transition.
        self._target_last_state = {
            entity_id: self._entity_state(entity_id)
            for entity_id in (
                *self._targets_by_entity,
                *self._power_by_entity,
                *self._exact_power_by_entity,
            )
        }
        # Legacy journals were keyed by target id only. Import the complete
        # unambiguous batch into durable shared authority while keeping the
        # original ownership payload unchanged for rollback. A missing or
        # ambiguous target closes dispatch without importing a partial batch.
        legacy_entities, legacy_uncertain = legacy_manual_authority_plan(
            self._ownership, self._configs.values()
        )
        legacy_refs = self._ownership.manual_authority_target_refs()
        authority = self._manual_light_authority
        if authority is not None:
            if legacy_uncertain or (legacy_refs and not authority.persistence_ready):
                authority.mark_unhealthy()
            elif authority.persistence_ready:
                try:
                    for entity_id in legacy_entities:
                        authority.capture_legacy(entity_id, source="room")
                    await authority.async_drain()
                    if not authority.has_durable_import(legacy_entities):
                        authority.mark_unhealthy()
                except Exception:  # noqa: BLE001 - all automatic light routes stay closed
                    authority.mark_unhealthy()
                    _LOGGER.exception("room manual light migration failed")
        try:
            # Exact power sources are subscribed separately below so a live map
            # change can add or remove them without touching the core tracker.
            entities = tuple(sorted(self._core_rooms_by_entity))
            if entities and self._track_state_changes is not None:
                self._unsubscribers.append(
                    self._track_state_changes(hass, entities, self._state_event)
                )
            if self._track_state_changes is not None and self._away_entity_id:
                self._unsubscribers.append(
                    self._track_state_changes(
                        hass, (self._away_entity_id,), self._away_event
                    )
                )
            if self._track_time_change is not None:
                # Minute boundaries are schedule boundaries. A relative
                # 60-second interval started at 06:59:59 would first fire at
                # 07:00:59 and miss the 07:00 profile handover.
                self._unsubscribers.append(
                    self._track_time_change(
                        hass,
                        self._clock_event,
                        second=0,
                    )
                )
            elif self._track_interval is not None:
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
        self._refresh_exact_source_subscriptions()
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
        # The sync path cannot await, but it closes new dispatches at
        # once; the async drain for in-flight work is registered
        # separately as the first unload callback (LP-C6-R5).
        self._core_input_closed = True
        self._notify_core_stop()
        # Invalidate any pending away-on debounce so it cannot fire after stop.
        self._away_generation += 1
        if (
            self._ownership_store is not None
            and self._ownership.dirty
            and self._hass is not None
        ):
            # Best-effort final flush; the journal also saves on each mutation.
            self._create_task(self._persist_ownership())
        flush = getattr(self._shadow, "async_flush", None)
        if callable(flush) and self._hass is not None:
            # Make the latest dispatch receipts durable so an unconfirmed
            # attempt is not retried after the next start.
            try:
                self._create_task(flush())
            except Exception:  # noqa: BLE001 - shutdown must stay best effort
                _LOGGER.warning("room lighting shadow flush failed")
        if self._exact_source_unsubscribe is not None:
            try:
                self._exact_source_unsubscribe()
            except Exception:  # noqa: BLE001 - cleanup must be best effort
                _LOGGER.warning("room lighting exact source cleanup failed")
            self._exact_source_unsubscribe = None
        self._exact_source_entities = frozenset()
        self._running = False
        for unsubscribe in reversed(self._unsubscribers):
            try:
                unsubscribe()
            except Exception:  # noqa: BLE001 - cleanup must be best effort
                _LOGGER.error("room lighting runtime cleanup failed")
        self._unsubscribers.clear()

    def set_core_input_port(
        self, port: Callable[[str, object, object], None]
    ) -> None:
        self._core_input_port = port

    def _notify_core_stop(self) -> None:
        if self._core_input_port is None:
            return
        stop_hook = getattr(self._core_input_port, "on_runtime_stop", None)
        if callable(stop_hook):
            try:
                stop_hook()
            except Exception:  # noqa: BLE001 - isolation by design
                _LOGGER.exception("core input port stop failed")

    async def stop(self) -> None:
        # In-flight core-input processing is awaited first (AUD-LP-ISO01
        # cycle 6): after stop() returns, no shadow store write is pending
        # and the stop hook in cancel() runs last.
        await self._drain_core_input()
        self.cancel()

    def _load_trackers(self) -> None:
        if self._track_state_changes is None or (
            self._track_interval is None and self._track_time_change is None
        ):
            import homeassistant.helpers.event as event_helpers  # noqa: PLC0415

            if self._track_state_changes is None:
                self._track_state_changes = getattr(
                    event_helpers, "async_track_state_change_event", None
                )
            if self._track_interval is None:
                self._track_interval = getattr(
                    event_helpers, "async_track_time_interval", None
                )
            if self._track_time_change is None:
                self._track_time_change = getattr(
                    event_helpers, "async_track_time_change", None
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
        """Release only a proved manual ON through both durable journals."""

        async with self._lock:
            config = self._configs.get(room_id)
            authority = self._manual_light_authority
            if config is None:
                raise ValueError("room lighting configuration is unavailable")
            if self._ownership_store is None:
                return ()
            # An unresolved target-id-only record has no exact room scope, so
            # this gesture cannot honestly remove its legacy counterpart.
            if self._ownership.has_legacy():
                return ()
            from .manual_light_authority import ManualLightAuthority
            if not isinstance(authority, ManualLightAuthority):
                return ()
            target_ids = tuple(target.id for target in config.devices.light_targets)
            entity_ids = tuple(target.entity_id for target in config.devices.light_targets)
            if any(not isinstance(entity_id, str) for entity_id in entity_ids):
                return ()
            authority_target_ids = tuple(
                target.id
                for target in config.devices.light_targets
                if authority.record(target.entity_id) is not None
            )
            boundary = self._ownership.manual_return_boundary()
            returned = await ManualLightAutoReturn(authority).async_return(
                entity_ids,
                room_history_is_releasable=lambda: self._ownership.room_history_is_releasable(
                    room_id, target_ids, authority_target_ids,
                    through_generation=boundary,
                ),
                release_room_history=lambda: self._ownership.release_room_manual_history_through(
                    room_id, target_ids, authority_target_ids,
                    through_generation=boundary,
                ),
                persist_room_history=self._persist_ownership,
            )
            return returned

    async def async_execute_command(
        self, config: RoomLightingConfig, command: PlannedCommand
    ) -> dict[str, object]:
        """Execute one planned command through the shared HA executor."""

        if self._hass is None:
            return self._blocked_command_receipt("runtime_unavailable")
        if self._manual_priority_blocks(config, command):
            return self._blocked_command_receipt("manual_light_priority")
        away_block = self._automatic_away_block_reason()
        if away_block is not None:
            return self._blocked_command_receipt(away_block)
        dependencies = self._power_dependencies()
        decision = self._exact_power_decision(config, dependencies)
        exact_dependencies = decision.projection if decision.active else None
        if self._exact_power_blocks_command(decision, command):
            # The same fail-safe as the automatic dispatch: a claimed topology
            # that the live map does not reproduce blocks only the affected
            # target; a corrupt protected store remains a room-wide block.
            return self._blocked_command_receipt(
                "exact_power_topology_incomplete",
                power={
                    "missingDependencies": list(decision.missing),
                    "wrongDependencies": list(decision.wrong),
                    "extraDependencies": list(decision.extra),
                    "diagnostics": list(decision.diagnostics),
                },
            )
        if self._command_conflicts_with_reserved_writer(
            config, command, dependencies=exact_dependencies
        ):
            _LOGGER.warning(
                "room lighting command refused: target is owned by a controller"
            )
            return self._blocked_command_receipt("reserved_command_target")
        receipt = await self._executor.execute(  # type: ignore[attr-defined]
            self._hass, command
        )
        to_payload = getattr(receipt, "to_payload", None)
        if callable(to_payload):
            return dict(to_payload())
        return {"confirmed": bool(getattr(receipt, "confirmed", False))}

    def _manual_priority_blocks(
        self, config: RoomLightingConfig, command: PlannedCommand
    ) -> bool:
        """Fail closed when the shared scenario authority marks this light manual."""
        authority = self._manual_light_authority
        if authority is not None and any(
            authority.blocks(target.entity_id)
            for target in config.devices.light_targets
            if isinstance(target.entity_id, str)
            and target.entity_id.startswith(("light.", "switch."))
        ):
            return True
        priority = self._light_priority
        if priority is None or self._hass is None:
            return False
        target_id = getattr(command, "target_id", None)
        entity_id = None
        for target in (*config.devices.light_targets, *config.devices.auxiliaries):
            if target.id == target_id:
                entity_id = target.entity_id
                break
        if not isinstance(entity_id, str) or not entity_id.startswith("light."):
            return False
        claims = getattr(priority, "manual_claim_entity_ids", None)
        if not callable(claims):
            # An unavailable shared authority must not open an automatic route.
            return True
        try:
            return entity_id in claims(frozenset({entity_id}))
        except Exception:  # noqa: BLE001 - shared safety authority is fail closed
            return True

    def _power_protected_entities(self, room_id: str) -> tuple[str, ...]:
        config = self._configs.get(room_id)
        if config is None:
            return ()
        return tuple(
            target.entity_id for target in config.devices.light_targets
            if isinstance(target.entity_id, str)
            and target.entity_id.startswith(("light.", "switch."))
        )

    @staticmethod
    def _blocked_command_receipt(
        reason: str,
        **extra: object,
    ) -> dict[str, object]:
        """Return an honest pre-executor receipt for direct runtime callers."""

        return {
            "blocked": True,
            "confirmed": False,
            "sent": False,
            "sendOutcome": "not_sent",
            "reason": reason,
            **extra,
        }

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
        if self._core_input_port is not None:
            # Immutable snapshot delivered via a scheduled task in a
            # thread pool: file I/O (fsync) never blocks the event loop
            # (SEC-LP-ISO01). Errors are isolated; the task is tracked so
            # stop() can await it (AUD-LP-ISO01 cycle 6).
            try:
                self._dispatch_core_input(
                    self._async_core_input(entity_id, data)
                )
            except Exception:  # noqa: BLE001
                _LOGGER.exception("core input dispatch failed for %s", entity_id)
        rooms = self._rooms_by_entity.get(entity_id)
        if not rooms:
            return
        # Observations resumed for at least one tracked entity: the restart gap
        # is over, so the unobserved profile guard must not block automation
        # forever.  The restart timestamp stays in ``_started_at`` for absence.
        self._unobserved_since = None
        entry_rooms = self._entry_by_entity.get(entity_id, set())
        if entry_rooms and _is_unlock_event(data):
            until = self._now_ms() + ENTRY_WAKEUP_GRACE_MS
            for room_id in entry_rooms:
                self._entry_wakeup_until[room_id] = until
        self._observe_target_transition(entity_id)
        self._observe_power_transition(entity_id)
        self._create_task(self.async_process(sorted(rooms)))

    def _dispatch_core_input(self, coroutine: object) -> None:
        """Track one core-input task so ``stop()`` can await it.

        The coroutine is wrapped: the task registers itself on start, so
        even the off-loop ``call_soon_threadsafe`` path (where
        ``_create_task`` cannot return the task) stays visible to the
        drain (SEC-C6-05). A task that never started hits the closed
        guard inside the input coroutine and does nothing."""

        if self._core_input_closed:
            _discard_coroutine(coroutine)
            return

        async def _tracked() -> None:
            task = asyncio.current_task()
            if task is not None:
                self._core_input_tasks.add(task)
            try:
                await coroutine
            finally:
                if task is not None:
                    self._core_input_tasks.discard(task)

        created = self._create_task(_tracked())
        if created is not None:
            self._core_input_tasks.add(created)
            created.add_done_callback(self._core_input_tasks.discard)

    async def _drain_core_input(self) -> None:
        """Wait for registered core-input tasks, then close the input.

        From ``stop()``: registered tasks — including ones created but
        not started — are gathered first and still process; the closed
        flag is only set after the queue empties. From the sync unload
        hook (``close_and_drain_core_input``) the flag is set first, so
        not-yet-started tasks are dropped by the guard inside the input
        coroutine instead of writing after unload. Either way nothing
        writes after the caller finished; the branch stop hook closes
        the branch itself under its lock (SEC-C6-R2-02)."""

        while True:
            pending = [
                task
                for task in tuple(self._core_input_tasks)
                if not task.done()
            ]
            if not pending:
                break
            await asyncio.gather(*pending, return_exceptions=True)
        self._core_input_closed = True

    def close_and_drain_core_input(self) -> None:
        """Sync unload callback (registered like every other unload hook):
        new dispatches stop immediately; the in-flight input is awaited by
        a drain task scheduled on the loop. The unload sequence itself is
        synchronous and does not block on that task; store consistency is
        guaranteed by the branch and store locks regardless of ordering."""

        self._core_input_closed = True
        create = (
            getattr(self._hass, "async_create_task", None)
            if self._hass is not None
            else None
        )
        if callable(create):
            try:
                create(self._drain_core_input())
            except Exception:  # noqa: BLE001 - unload must not crash
                _LOGGER.warning("core input drain scheduling failed")

    def core_manual_holds(self):
        """Snapshot of active manual holds for the shadow branch (K-07).

        Returns ``{entity_id: ManualHoldView}`` for tracked room light
        targets, or ``None`` when the shared authority is unavailable —
        the branch must then treat manual state as uncertain (fail
        closed, never widen automation rights). Legacy light-priority
        claims do not expose timestamps, so an active claim transfers as
        a conservative one-hour hold from now (the documented default
        manual priority); it cannot shorten a real hold.
        """

        from .room_lighting_core import ManualHoldView

        holds: dict = {}
        authority = self._manual_light_authority
        if authority is not None:
            # ManualLightAuthority.unhealthy is a bool property; test
            # doubles may expose a method. Both forms must fail closed.
            unhealthy = getattr(authority, "unhealthy", None)
            if isinstance(unhealthy, bool):
                if unhealthy:
                    return None
            elif callable(unhealthy) and unhealthy():
                return None
        priority = self._light_priority
        for config in self._configs.values():
            for target in config.devices.light_targets:
                entity_id = target.entity_id
                if not isinstance(entity_id, str):
                    continue
                record = (
                    authority.record(entity_id)
                    if authority is not None
                    else None
                )
                if record is not None:
                    now_ms = self._now_ms()
                    holds[entity_id] = ManualHoldView(
                        entity_id=entity_id,
                        action=record.action,
                        source=record.source,
                        proven_at_utc_ms=record.observed_at_ms,
                        # The shared authority is the release source of
                        # truth: while its record exists the view re-arms
                        # (never shorter than the recorded deadline), so
                        # the shadow branch never unlocks earlier than
                        # the authority's own barrier.
                        priority_until_utc_ms=max(
                            record.deadline_ms,
                            now_ms + MANUAL_PRIORITY_TRANSFER_MS,
                        ),
                        # Opaque: real authority fingerprints embed HA
                        # entity names and must not cross the adapter
                        # (SEC-C6-R2-01).
                        evidence_id=(
                            f"auth.hold."
                            f"{_opaque_entity_tag(record.fingerprint)}"
                        ),
                    )
                    continue
                if authority is not None:
                    # The FULL prohibition of the authority transfers, not
                    # only its own record: barriers (uncertain selector,
                    # untrusted related-source graph) and holds on related
                    # lights block the same way the dispatch gate does
                    # (SEC-C6-02). Any failure here fails closed.
                    try:
                        blocked = authority.blocks(entity_id)
                    except Exception:  # noqa: BLE001 - fail closed
                        return None
                    if blocked:
                        try:
                            evidence = authority.blocking_evidence(entity_id)
                        except Exception:  # noqa: BLE001 - fail closed
                            return None
                        now_ms = self._now_ms()
                        holds[entity_id] = ManualHoldView(
                            entity_id=entity_id,
                            action=(
                                evidence.action
                                if evidence is not None
                                else "unknown"
                            ),
                            source=(
                                evidence.source
                                if evidence is not None
                                else "authority_barrier"
                            ),
                            proven_at_utc_ms=(
                                evidence.observed_at_ms
                                if evidence is not None
                                else now_ms
                            ),
                            priority_until_utc_ms=max(
                                (
                                    evidence.deadline_ms
                                    if evidence is not None
                                    else 0
                                ),
                                now_ms + MANUAL_PRIORITY_TRANSFER_MS,
                            ),
                            evidence_id=(
                                f"auth.hold."
                                f"{_opaque_entity_tag(evidence.fingerprint)}"
                                if evidence is not None
                                else (
                                    "authority.barrier."
                                    f"{_opaque_entity_tag(entity_id)}"
                                )
                            ),
                        )
                        continue
                claims = getattr(priority, "manual_claim_entity_ids", None)
                if not callable(claims):
                    if priority is not None:
                        # An unavailable shared authority must not open
                        # an automatic route (same rule as dispatch).
                        return None
                    continue
                try:
                    claimed = entity_id in claims(frozenset({entity_id}))
                except Exception:  # noqa: BLE001 - shared safety authority
                    return None
                if claimed:
                    now_ms = self._now_ms()
                    holds[entity_id] = ManualHoldView(
                        entity_id=entity_id,
                        action="on",
                        source="light_priority",
                        proven_at_utc_ms=now_ms,
                        priority_until_utc_ms=(
                            now_ms + MANUAL_PRIORITY_TRANSFER_MS
                        ),
                        # Opaque tag: the HA entity name must not reach the
                        # core frame or the durable image (SEC-C6-06).
                        evidence_id=(
                            f"lp.claim.{_opaque_entity_tag(entity_id)}"
                        ),
                    )
        return holds

    async def _async_core_clock_tick(self) -> None:
        import asyncio

        if self._core_input_closed:
            return
        port = self._core_input_port
        await asyncio.to_thread(port, "__clock__", None, None)

    async def _async_core_input(self, entity_id: str, data: Mapping) -> None:
        # The port does file I/O (shadow store fsync); run it in a thread
        # so the event loop is never blocked by fsync (SEC-LP-ISO01).
        import asyncio

        if self._core_input_closed:
            # Closed between scheduling and execution: drop silently.
            return
        port = self._core_input_port
        old_state = data.get("old_state")
        new_state = data.get("new_state")
        await asyncio.to_thread(port, entity_id, old_state, new_state)

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

    def _current_away_state(self, hass: HomeAssistant) -> bool | None:
        del hass
        observation = self._protected_away_observation()
        self._away_diagnostic = observation.diagnostic
        return observation.is_away

    def _protected_away_observation(self) -> ProtectedAwayObservation:
        """Read the HA boundary once, then apply the pure Away classifier."""

        if self._away_entity_id is None or self._hass is None:
            return classify_protected_away(self._away_entity_id, None)
        state = self._hass.states.get(self._away_entity_id)
        return classify_protected_away(
            self._away_entity_id,
            getattr(state, "state", None),
        )

    @_ha_callback
    def _away_event(self, event: object) -> None:
        """Track the A100 Away source with a debounce on the on-edge."""

        del event
        if not self._running or self._hass is None:
            return
        away_state = self._current_away_state(self._hass)
        if away_state is True:
            self._arm_away()
        elif away_state is False:
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
        if self._hass is None or self._current_away_state(self._hass) is not True:
            return
        if self._away:
            return
        self._away = True
        await self.async_process()

    @_ha_callback
    def _clock_event(self, _now: object) -> None:
        if not self._running:
            return
        if self._core_input_port is not None:
            try:
                self._dispatch_core_input(self._async_core_clock_tick())
            except Exception:  # noqa: BLE001
                _LOGGER.exception("core clock tick dispatch failed")
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
        action = {"turn_on": "on", "turn_off": "off"}.get(service)
        for entity_id in _entity_ids(service_data):
            if (
                action is not None and callable(owns_context)
                and owns_context(getattr(event, "context", None), entity_id, action)
            ):
                continue
            power_rooms = self._power_rooms_for(entity_id)
            if power_rooms:
                for room_id in power_rooms:
                    self._record_power_manual(room_id, entity_id, service)
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
        The same rule applies to an exact source from the live map, even when it
        differs from the room's legacy ``power_switch``.
        """

        power_rooms = self._power_rooms_for(entity_id)
        if not power_rooms:
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
        for room_id in power_rooms:
            self._record_power_manual(room_id, entity_id, "turn_off")

    def _power_rooms_for(self, entity_id: str) -> tuple[str, ...]:
        """Rooms whose automatic profile depends on this power entity.

        The legacy room power switch and the exact map sources are both part of
        the same manual-protection boundary: a manual off must freeze every
        room it feeds, and an exact source may feed more than one room.
        """

        rooms: set[str] = set()
        legacy_room = self._power_by_entity.get(entity_id)
        if legacy_room is not None:
            rooms.add(legacy_room)
        rooms.update(self._exact_power_by_entity.get(entity_id, ()))
        return tuple(sorted(rooms))

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
                # This gesture only changes durable attribution.  It neither
                # manufactures AUTO ownership nor evaluates or commands light.
                continue
            # A press is a manual intent regardless of the command flag.
            for target_id in target_ids:
                target = config.devices.target(target_id)
                entity_id = getattr(target, "entity_id", None)
                authority = self._manual_light_authority
                if (
                    authority is not None and isinstance(entity_id, str)
                    and entity_id.startswith(("light.", "switch."))
                ):
                    try:
                        changed = authority.capture(
                            entity_id,
                            "off" if _binding_turns_off(self._hass, target, binding) else "on",
                            "device_trigger",
                            f"{config.room_id}:{device_id}:{subtype}:{moment}:{target_id}",
                            observed_at_ms=moment,
                            duration_ms=config.manual_control_return.priority_seconds * 1000,
                        )
                        if changed:
                            self._create_task(authority.async_drain())
                    except Exception:  # noqa: BLE001
                        authority.mark_unhealthy()
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
        dependencies = self._power_dependencies()
        decision = self._exact_power_decision(config, dependencies)
        exact_dependencies = decision.projection if decision.active else None
        if self._binding_conflicts_with_reserved_writer(
            config, target_ids, dependencies=exact_dependencies
        ):
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
            if self._binding_conflicts_with_reserved_writer(
                config, (target_id,), dependencies=exact_dependencies
            ):
                _LOGGER.warning(
                    "room lighting binding refused: target is owned by a controller"
                )
                return
            command = _binding_command(self._hass, target, binding)
            if command is None:
                continue
            try:
                execute_manual = getattr(self._executor, "execute_manual", self._executor.execute)
                await execute_manual(  # type: ignore[attr-defined]
                    self._hass, command
                )
            except Exception:  # noqa: BLE001 - a failed press is not a crash
                _LOGGER.exception("room lighting binding command failed")

    # -- evaluation --------------------------------------------------------

    async def async_force_auto(self, room_id: str, *, reason: str) -> dict[str, object]:
        """Перевести комнату в авто по явному действию владельца.

        Снимает все ручные удержания световых целей комнаты (до текущего
        момента) и завершает утреннее ожидание присутствия, если оно идёт.
        Свет напрямую не командуется: после снятия удержаний комната
        обрабатывается штатной оценкой профиля, и включение/выключение
        выполняет решение автоматики с подтверждением результата.
        """

        config = self._configs.get(room_id)
        if config is None:
            return {"room": room_id, "known": False, "released": 0}
        moment = self._now_ms()
        released = self._ownership.release_room_manual_holds(
            room_id, config.devices.light_target_ids, before_ms=moment
        )
        morning = config.morning
        if morning is not None and morning.enabled:
            # Принудительный перевод владельцем завершает ожидание границы:
            # комната переходит под профиль немедленно.
            now_dt = datetime.fromtimestamp(moment / 1000, self._home_timezone())
            self._ownership.mark_morning_transition(
                room_id, now_dt.date().isoformat(), moment
            )
        _LOGGER.info(
            "room lighting force-auto (%s): released %d manual hold(s) for %s",
            reason,
            released,
            room_id,
        )
        await self.async_process([room_id])
        await self._persist_ownership()
        return {
            "room": room_id,
            "known": True,
            "released": released,
            "reason": reason,
            "at": moment,
        }

    # -- evaluation --------------------------------------------------------

    async def async_process(
        self, room_ids: Sequence[str] | None = None
    ) -> None:
        if not self._running:
            return
        async with self._lock:
            if not self._running:
                return
            # The durable map is consulted on every pass under the runtime lock:
            # a live source added or removed by the admin API must update the
            # event index and its subscription as one serialized change.
            self._refresh_exact_power_index()
            selected = (
                tuple(self._configs)
                if room_ids is None
                else tuple(room for room in room_ids if room in self._configs)
            )
        for room_id in selected:
            if not self._running:
                break
            await self._process_room_locked(room_id)
        await self._persist_ownership()

    def _room_lock(self, room_id: str) -> asyncio.Lock:
        lock = self._room_locks.get(room_id)
        if lock is None:
            lock = asyncio.Lock()
            self._room_locks[room_id] = lock
        return lock

    def _power_source_lock(self, entity_id: str) -> asyncio.Lock:
        """Serialize one physical source without blocking other sources."""

        lock = self._power_source_locks.get(entity_id)
        if lock is None:
            lock = asyncio.Lock()
            self._power_source_locks[entity_id] = lock
        return lock

    async def _process_room_locked(self, room_id: str) -> None:
        async with self._room_lock(room_id):
            if not self._running:
                return
            config = self._configs.get(room_id)
            if config is None:
                return
            await self._process_room(config)

    async def _process_room(self, config: RoomLightingConfig) -> None:
        if self._hass is None:
            return
        moment = self._now_ms()
        decision = None
        # A single broken room must never crash setup or stop the other rooms.
        try:
            context = await self._build_context(config, moment)
            morning_waiting = False
            if config.profile == "day_curve":
                morning = config.morning
                if morning is not None and morning.enabled:
                    action = self._morning_gate(config, morning, moment)
                    if action == "wait":
                        # Presence-gated morning: the room keeps its night
                        # state until the first trusted presence at or after
                        # the boundary. The curve is not evaluated; auxiliary
                        # duties below still run.
                        morning_waiting = True
                        _LOGGER.debug(
                            "room lighting morning gate keeps %s in night mode",
                            config.room_id,
                        )
                    if action == "transition":
                        # The gate has already released the holds dated before
                        # the boundary; the decision must see the released
                        # ownership, not the snapshot taken above.
                        context = await self._build_context(config, moment)
                if not morning_waiting:
                    curve_presence = self._curve_sensor_state(
                        context, frozenset({SensorKind.PRESENCE})
                    )
                    entry_wakeup = (
                        self._entry_wakeup_until.get(config.room_id, 0) >= moment
                    )
                    if entry_wakeup and curve_presence is not SensorState.ON:
                        # Unlock without a fresh presence yet: a person is entering
                        # from the street, so wake the curve immediately. Record
                        # the door as presence evidence so the level does not fall
                        # back when the short window expires, and keep the real
                        # absence tracker instead of zeroing it.
                        self._record_entry_wakeup_presence(config.room_id, moment)
                        confirmed, absence_seconds = True, 0
                        motion_preview = False
                        motion_rejected = False
                    else:
                        if entry_wakeup:
                            # Somebody already stood in the room before the unlock;
                            # the door event must not change the current decision.
                            _LOGGER.info(
                                "room lighting entry wakeup skipped for %s: "
                                "presence already active",
                                config.room_id,
                            )
                        confirmed, absence_seconds = self._observe_curve_presence(
                            config, context, moment, presence=curve_presence
                        )
                        (
                            motion_preview,
                            motion_rejected,
                            motion_confirmed,
                        ) = self._curve_motion_phase(
                            config, context, moment, curve_presence
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
        enabled = self._automatic_commands_enabled(config)
        dispatch_block_reason = (
            self._automatic_away_block_reason()
            if self._room_commands_enabled(config)
            else None
        )
        operation_id = self._new_operation_id(config, moment)
        config_version = int(config.version)
        record = getattr(self._shadow, "async_record", None)
        if decision is not None and callable(record):
            try:
                await record(
                    decision,
                    commands_enabled=enabled,
                    operation_id=operation_id,
                    config_version=config_version,
                    dispatch_block_reason=dispatch_block_reason,
                )
            except Exception:  # noqa: BLE001 - journal failure must not dispatch
                _LOGGER.warning("room lighting journal write failed")
                if enabled:
                    return
        if enabled and decision is not None:
            flush = getattr(self._shadow, "async_flush", None)
            if callable(flush):
                try:
                    # A live intent must reach durable storage before the first
                    # physical call. Coalescing is safe for shadow, not for live
                    # dispatch authority.
                    await flush()
                except Exception:  # noqa: BLE001 - no durable intent, no command
                    _LOGGER.warning("room lighting live intent durable write failed")
                    return
        await self._record_auxiliary_shadow(config, moment)
        await self._process_shower_exhaust(
            config, context, moment, enabled=enabled
        )
        if not enabled or decision is None:
            _LOGGER.debug(
                "room lighting shadow decision journaled for %s", config.room_id
            )
            return
        # Re-check after the await: cancel() may have run while state was read.
        if not self._running:
            return
        permission = self._dispatch_permission_token(config)
        if not self._dispatch_permission_valid(permission):
            receipts = self._blocked_dispatch_receipts(
                decision,
                operation_id=operation_id,
                config_version=config_version,
                reason="dispatch_permission_stale",
            )
        else:
            receipts = await self._dispatch(
                config,
                decision,
                moment,
                operation_id=operation_id,
                config_version=config_version,
                permission=permission,
            )
        recorder = getattr(self._shadow, "async_record_dispatch", None)
        receipts_to_record = [
            receipt for receipt in receipts if not receipt.get("_durable_recorded")
        ]
        if receipts_to_record and callable(recorder):
            try:
                await recorder(
                    at=moment,
                    room_id=config.room_id,
                    receipts=receipts_to_record,
                    operation_id=operation_id,
                    config_version=config_version,
                )
            except Exception:  # noqa: BLE001 - journal failure must not crash
                _LOGGER.warning("room lighting dispatch receipt write failed")

    def _new_operation_id(self, config: RoomLightingConfig, moment: int) -> str:
        """Internal trace id for one evaluation and its possible dispatch."""

        self._operation_sequence += 1
        safe_room = "".join(
            char if char.isalnum() or char in {"-", "_"} else "-"
            for char in config.room_id
        )
        return f"room-lighting-{safe_room}-{moment}-{self._operation_sequence}"

    async def _persist_pending_target_dispatch(
        self,
        *,
        at: int,
        room_id: str,
        target_id: str,
        action: str,
        evidence_revision: str,
        operation_id: str | None,
        config_version: int | None,
    ) -> bool:
        """Durable pre-send marker for the narrow post-send receipt window.

        If Home Assistant or storage fails after the physical boundary, restart
        must not blindly retry the same target command.  The marker is written
        immediately before the executor call and cleared by the later honest
        receipt when the command was not actually sent or when it confirmed.
        """

        recorder = getattr(self._shadow, "async_record_pending_dispatch", None)
        if not callable(recorder):
            return False
        try:
            await recorder(
                at=at,
                room_id=room_id,
                target_id=target_id,
                action=action,
                evidence_revision=evidence_revision,
                operation_id=operation_id,
                config_version=config_version,
            )
        except Exception:  # noqa: BLE001 - no durable marker, no command
            _LOGGER.warning("room lighting pending dispatch write failed")
            return False
        self._dispatch_failures[(room_id, target_id)] = (action, evidence_revision)
        return True

    async def _persist_pending_power_dispatch(
        self,
        *,
        at: int,
        room_id: str,
        source_entity_id: str,
        evidence_revision: str,
        operation_id: str | None,
        config_version: int | None,
    ) -> bool:
        """Durable pre-send marker for an upstream power turn_on intent."""

        recorder = getattr(self._shadow, "async_record_pending_power_dispatch", None)
        if not callable(recorder):
            return False
        try:
            await recorder(
                at=at,
                room_id=room_id,
                source_entity_id=source_entity_id,
                evidence_revision=evidence_revision,
                operation_id=operation_id,
                config_version=config_version,
            )
        except Exception:  # noqa: BLE001 - no durable marker, no power command
            _LOGGER.warning("room lighting pending power dispatch write failed")
            return False
        self._power_failures[source_entity_id] = ("turn_on", evidence_revision)
        return True

    async def _persist_not_sent_dispatch_closure(
        self,
        *,
        at: int,
        room_id: str,
        receipt: dict[str, object],
        operation_id: str | None,
        config_version: int | None,
    ) -> bool:
        """Durably close a pending marker when the final pre-executor check refuses.

        The pending marker remains fail-closed if this write fails.  The runtime
        clears its in-memory block only after the durable receipt was accepted.
        """

        recorder = getattr(self._shadow, "async_record_dispatch", None)
        if not callable(recorder):
            return False
        try:
            await recorder(
                at=at,
                room_id=room_id,
                receipts=(receipt,),
                operation_id=operation_id,
                config_version=config_version,
            )
        except Exception:  # noqa: BLE001 - pending marker must stay fail-closed
            _LOGGER.warning("room lighting pending dispatch closure write failed")
            return False
        receipt["_durable_recorded"] = True
        return True

    async def _persist_not_sent_power_dispatch_closure(
        self,
        *,
        at: int,
        room_id: str,
        source_entity_id: str,
        evidence_revision: str,
        reason: str,
        operation_id: str | None,
        config_version: int | None,
    ) -> bool:
        """Close a saved power intent before its executor boundary.

        The shadow service already knows how to clear a pending power marker
        from a not-sent dispatch receipt. Keep the in-memory retry guard until
        that durable closure succeeds, so a storage failure remains fail-closed.
        """

        receipt: dict[str, object] = {
            "power": {
                "sourceEntityId": source_entity_id,
                "evidenceRevision": evidence_revision,
                "dispatchAttempt": False,
                "sent": False,
                "sendOutcome": "not_sent",
                "reason": reason,
            }
        }
        closed = await self._persist_not_sent_dispatch_closure(
            at=at,
            room_id=room_id,
            receipt=receipt,
            operation_id=operation_id,
            config_version=config_version,
        )
        if closed:
            self._power_failures.pop(source_entity_id, None)
        return closed

    @staticmethod
    def _dispatch_permission_token(
        config: RoomLightingConfig,
    ) -> tuple[str, int, bool]:
        return (config.room_id, int(config.version), bool(config.commands_enabled))

    def _dispatch_permission_valid(self, token: tuple[str, int, bool]) -> bool:
        room_id, version, enabled = token
        latest = self._configs.get(room_id)
        return (
            enabled
            and latest is not None
            and int(latest.version) == version
            and bool(latest.commands_enabled)
        )

    @staticmethod
    def _blocked_dispatch_receipts(
        decision: RoomLightingDecision,
        *,
        operation_id: str,
        config_version: int,
        reason: str,
    ) -> list[dict[str, object]]:
        return [
            {
                "operationId": operation_id,
                "configVersion": config_version,
                "targetId": command.target_id,
                "action": command.action.value,
                "sent": False,
                "sendOutcome": "not_sent",
                "confirmed": None,
                "dispatchAttempt": False,
                "reason": reason,
                "ownershipChanged": "none",
            }
            for command in decision.commands
        ]

    def _record_entry_wakeup_presence(self, room_id: str, moment: int) -> None:
        """Record an entry unlock as fresh presence evidence for the curve.

        A door unlock proves somebody is at the entry even before the presence
        sensor reports. Advancing ``last_seen`` keeps the achieved brightness
        after the short wakeup window instead of falling back to the minimum,
        while the recorded absence and confirmation start are left untouched so
        real evidence is not erased.
        """

        tracker = self._curve_presence.setdefault(
            room_id, {"since": None, "last_seen": None}
        )
        tracker["last_seen"] = moment

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
        if present is SensorState.OFF:
            aggregate = self._curve_presence_state(context)
            if aggregate is SensorState.ON:
                # A dedicated presence OFF may coexist with fresh motion ON.
                # Keep its existing motion-confirmation path, but never count
                # that interval as absence of both configured participants.
                tracker["since"] = None
                tracker["last_seen"] = moment
                tracker["absence"] = 0
                return False, 0
            if aggregate is not SensorState.OFF:
                present = SensorState.UNKNOWN
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

        A fresh ON from either participant proves presence. Absence requires
        every participant to be known OFF; a stale ON cannot be ignored to
        manufacture absence from the remaining sensor. OFF uses the existing
        larger staleness window, not the shorter ON confirmation freshness.
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
                else:
                    has_unknown = True
                continue
            if sensor.state is SensorState.OFF:
                if context.now - sensor.last_changed > EnginePolicy().staleness_seconds * 1000:
                    has_unknown = True
                else:
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

        enabled = enabled and self._automatic_commands_enabled(config)
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
        away_block = self._automatic_away_block_reason()
        if away_block is not None:
            _LOGGER.warning(
                "shower exhaust dispatch refused: %s", away_block
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
        *,
        operation_id: str | None = None,
        config_version: int | None = None,
        permission: tuple[str, int, bool] | None = None,
    ) -> list[dict[str, object]]:
        """Dispatch the plan and return one honest receipt per attempt.

        A room switches to exact per-target semantics only with an explicit
        release-owned migration claim. Every receipt states whether the command
        was handed to Home Assistant, whether the device confirmed it, why a
        command was blocked, what evidence a retry would need and whether
        automatic ownership changed.
        """

        if self._hass is None:
            return []
        dependencies = self._power_dependencies()
        exact = self._exact_power_decision(config, dependencies)
        exact_dependencies = exact.projection if exact.active else None
        power = config.devices.power_switch
        power_entity = power.entity_id if power is not None else None
        if exact.incomplete:
            self._warn_incomplete_topology(config, exact)
        receipts: list[dict[str, object]] = []
        failed_curve_targets: set[str] = set()
        externally_on_targets: set[str] = set()
        for command in decision.commands:
            if not self._running:
                break
            receipt: dict[str, object] = {
                "targetId": command.target_id,
                "action": command.action.value,
                "sent": False,
                "sendOutcome": "not_sent",
                "confirmed": None,
                "dispatchAttempt": False,
                "reason": "planned",
                "ownershipChanged": "none",
            }
            if operation_id is not None:
                receipt["operationId"] = operation_id
            if config_version is not None:
                receipt["configVersion"] = config_version
            away_block = self._automatic_away_block_reason()
            if away_block is not None:
                receipt["reason"] = away_block
                receipts.append(receipt)
                continue
            if permission is not None and not self._dispatch_permission_valid(permission):
                receipt["reason"] = "dispatch_permission_stale"
                receipts.append(receipt)
                continue
            if (
                command.target_id in externally_on_targets
                and command.action is not LightAction.TURN_OFF
            ):
                receipt["reason"] = "already_on"
                receipt["supersededBy"] = "target_on_before_dispatch"
                receipts.append(receipt)
                continue
            if config.profile == "day_curve":
                # Each awaited device call can be interleaved with a manual
                # action or a failed transition. Re-evaluate from current
                # evidence before sending the next step, especially mirror OFF.
                current = await self._build_context(config, self._now_ms())
                if (
                    command.target_id in failed_curve_targets
                    or curve_profile_block_reason(config, current) is not None
                ):
                    receipt["reason"] = "profile_blocked"
                    receipts.append(receipt)
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
                        receipt["reason"] = "superseded"
                        receipts.append(receipt)
                        continue
            if self._exact_power_blocks_command(exact, command):
                # The room is explicitly claimed as migrated. Missing or wrong
                # proof for this claimed target fails closed, while independent
                # targets in the same room continue to use their own trusted
                # state. A corrupt protected manifest remains room-wide.
                receipt["reason"] = "exact_power_topology_incomplete"
                receipt["power"] = {
                    "missingDependencies": list(exact.missing),
                    "wrongDependencies": list(exact.wrong),
                    "extraDependencies": list(exact.extra),
                    "diagnostics": list(exact.diagnostics),
                }
                receipts.append(receipt)
                continue
            if self._command_conflicts_with_reserved_writer(
                config, command, dependencies=exact_dependencies
            ):
                _LOGGER.warning(
                    "room lighting dispatch refused: target is owned by a controller"
                )
                receipt["reason"] = "reserved_command_target"
                receipts.append(receipt)
                continue
            if (
                command.action is not LightAction.TURN_OFF
                and self._auto_on_blocked(config, moment)
            ):
                # Core race guard: a manual switch-off owns the room until its
                # protection window releases. The automatic branch must never
                # re-power a light or turn a target back on right after a
                # person switched it off.
                receipt["reason"] = "manual_off_protection"
                receipts.append(receipt)
                continue
            target = config.devices.target(command.target_id)
            entity_id = target.entity_id if target is not None else None
            dependency = (
                exact_dependencies.get(entity_id)
                if exact_dependencies is not None
                and entity_id is not None
                else None
            )
            revision_before = (
                self._target_evidence_revision(entity_id)
                if entity_id is not None
                else ""
            )
            target_snapshot = (
                self._target_evidence_snapshot(entity_id)
                if entity_id is not None
                else None
            )
            if target_snapshot is not None:
                receipt["target"] = target_snapshot
            failure_key = (config.room_id, command.target_id)
            previous_failure = self._dispatch_failures.get(failure_key)
            superseding_off = (
                command.action is LightAction.TURN_OFF
                and previous_failure is not None
                and previous_failure[0] != LightAction.TURN_OFF.value
            )
            legacy_unpowered_assumed_off = (
                not exact.active
                and command.action is LightAction.TURN_ON
                and entity_id is not None
                and self._legacy_unpowered_target_assumed_off(config, entity_id)
            )
            if (
                entity_id is not None
                and self._target_attempt_blocked(failure_key, entity_id, command.action)
            ):
                # An unconfirmed attempt is never repeated blindly: a new
                # attempt needs new physical evidence for that target. The
                # check runs before power preparation so a suppressed target
                # cannot command its source again either.
                receipt["reason"] = "unconfirmed_attempt"
                receipt["evidenceRevision"] = revision_before
                receipts.append(receipt)
                continue
            power_detail: dict[str, object] | None = None
            if dependency is not None and command.action is not LightAction.TURN_OFF:
                powered, power_detail = await self._ensure_power_source(
                    dependency,
                    at=moment,
                    room_id=config.room_id,
                    operation_id=operation_id,
                    config_version=config_version,
                )
                receipt["power"] = power_detail
                if not powered:
                    _LOGGER.warning(
                        "room lighting power source is not ready; "
                        "skipping the target command"
                    )
                    receipt["reason"] = str(
                        power_detail.get("reason") or "power_source_unavailable"
                    )
                    receipts.append(receipt)
                    continue
                if not self._running:
                    break
                if permission is not None and not self._dispatch_permission_valid(permission):
                    receipt["reason"] = "dispatch_permission_stale"
                    receipts.append(receipt)
                    continue
                # Power-on and context reads can yield to another controller
                # or a person. Both must win before target dispatch.
                if self._command_conflicts_with_reserved_writer(
                    config, command, dependencies=exact_dependencies
                ):
                    receipt["reason"] = "reserved_command_target"
                    receipts.append(receipt)
                    continue
                if self._auto_on_blocked(config, self._now_ms()):
                    receipt["reason"] = "manual_off_protection"
                    receipts.append(receipt)
                    continue
                if config.profile == "day_curve":
                    current = await self._build_context(config, self._now_ms())
                    if curve_profile_block_reason(config, current) is not None:
                        receipt["reason"] = "profile_blocked"
                        receipts.append(receipt)
                        continue
                if (
                    command.action is LightAction.TURN_ON
                    and entity_id is not None
                    and exact_dependencies is not None
                    and self._target_evidence_revision(entity_id)
                    != revision_before
                ):
                    # A fresh on report during the power wait is physical
                    # evidence that a person switched the target on; the
                    # automatic branch must not send a second turn_on.
                    reason = self._turn_on_dispatch_block_reason(
                        config,
                        command,
                        entity_id,
                        revision_before,
                        dependencies=exact_dependencies,
                        exact_active=exact.active,
                        current_reason="target_on_after_power_wait",
                    )
                    receipt["reason"] = reason or "dispatch_evidence_changed"
                    if reason == "already_on":
                        receipt["supersededBy"] = "target_on_after_power_wait"
                    receipts.append(receipt)
                    continue
            elif (
                not exact.active
                and power_entity is not None
                and command.action is not LightAction.TURN_OFF
                and not self._entity_is_on(power_entity)
            ):
                # Legacy whole-room association: a target that is switched on
                # through its room power switch must get power first.
                powered, power_detail = await self._ensure_legacy_room_power(
                    power_entity,
                    at=moment,
                    room_id=config.room_id,
                    operation_id=operation_id,
                    config_version=config_version,
                )
                receipt["power"] = power_detail
                if not powered:
                    _LOGGER.warning(
                        "room lighting power switch is still off; "
                        "skipping the target command"
                    )
                    receipt["reason"] = str(
                        power_detail.get("reason") or "power_source_unavailable"
                    )
                    receipts.append(receipt)
                    continue
                if not self._running:
                    break
                if permission is not None and not self._dispatch_permission_valid(permission):
                    receipt["reason"] = "dispatch_permission_stale"
                    receipts.append(receipt)
                    continue
                if self._command_conflicts_with_reserved_writer(
                    config, command, dependencies=exact_dependencies
                ):
                    receipt["reason"] = "reserved_command_target"
                    receipts.append(receipt)
                    continue
                if self._auto_on_blocked(config, self._now_ms()):
                    receipt["reason"] = "manual_off_protection"
                    receipts.append(receipt)
                    continue
                if config.profile == "day_curve":
                    current = await self._build_context(config, self._now_ms())
                    if curve_profile_block_reason(config, current) is not None:
                        receipt["reason"] = "profile_blocked"
                        receipts.append(receipt)
                        continue
                if (
                    command.action is LightAction.TURN_ON
                    and entity_id is not None
                    and self._target_evidence_revision(entity_id)
                    != revision_before
                ):
                    reason = self._turn_on_dispatch_block_reason(
                        config,
                        command,
                        entity_id,
                        revision_before,
                        dependencies=None,
                        exact_active=False,
                        current_reason="target_on_after_power_wait",
                        allow_legacy_unpowered_recheck=legacy_unpowered_assumed_off,
                    )
                    if reason is None:
                        pass
                    else:
                        receipt["reason"] = reason
                        if reason == "already_on":
                            receipt["supersededBy"] = "target_on_after_power_wait"
                        receipts.append(receipt)
                        continue
            if not self._running:
                break
            if permission is not None and not self._dispatch_permission_valid(permission):
                receipt["reason"] = "dispatch_permission_stale"
                receipts.append(receipt)
                continue
            if self._command_conflicts_with_reserved_writer(
                config, command, dependencies=exact_dependencies
            ):
                receipt["reason"] = "reserved_command_target"
                receipts.append(receipt)
                continue
            if command.action is LightAction.TURN_OFF:
                turn_off_block = self._turn_off_dispatch_block_reason(
                    config,
                    command,
                    entity_id,
                    revision_before,
                )
                if turn_off_block is not None:
                    receipt["reason"] = turn_off_block
                    if entity_id is not None:
                        receipt["evidenceRevision"] = revision_before
                        receipt["currentEvidenceRevision"] = (
                            self._target_evidence_revision(entity_id)
                        )
                    if superseding_off:
                        # The refused planned off replaced another action's
                        # record; carry it into the receipt so the durable
                        # store restores that older protection.
                        receipt["_supersededFailure"] = {
                            "action": previous_failure[0],
                            "evidenceRevision": previous_failure[1],
                        }
                    receipts.append(receipt)
                    continue
            if command.action is LightAction.TURN_ON and entity_id is not None:
                current_revision = self._target_evidence_revision(entity_id)
                current_snapshot = self._target_evidence_snapshot(entity_id)
                turn_on_block = self._turn_on_dispatch_block_reason(
                    config,
                    command,
                    entity_id,
                    revision_before,
                    dependencies=exact_dependencies,
                    exact_active=exact.active,
                    allow_legacy_unpowered_recheck=legacy_unpowered_assumed_off,
                )
                if turn_on_block == "already_on":
                    receipt["evidenceRevision"] = revision_before
                    receipt["currentEvidenceRevision"] = current_revision
                    receipt["currentTarget"] = current_snapshot
                    receipt["reason"] = "already_on"
                    receipt["supersededBy"] = "target_on_before_dispatch"
                    externally_on_targets.add(command.target_id)
                    receipts.append(receipt)
                    continue
                if turn_on_block is not None:
                    receipt["evidenceRevision"] = revision_before
                    receipt["currentEvidenceRevision"] = current_revision
                    receipt["currentTarget"] = current_snapshot
                    receipt["reason"] = turn_on_block
                    receipts.append(receipt)
                    continue
            if (
                command.action
                in {LightAction.SET_BRIGHTNESS, LightAction.SET_COLOR_TEMPERATURE}
                and entity_id is not None
            ):
                adjustment_block = self._adjustment_dispatch_block_reason(
                    config,
                    command,
                    entity_id,
                    revision_before,
                    dependencies=exact_dependencies,
                    exact_active=exact.active,
                    operation_moment=moment,
                )
                if adjustment_block is not None:
                    receipt["evidenceRevision"] = revision_before
                    receipt["currentEvidenceRevision"] = (
                        self._target_evidence_revision(entity_id)
                    )
                    receipt["currentTarget"] = self._target_evidence_snapshot(entity_id)
                    receipt["reason"] = adjustment_block
                    receipts.append(receipt)
                    continue
            if entity_id is not None:
                service_name = _planned_service_name(command)
                previous_executing = self._executing_actions.get(entity_id)
                previous_grace = self._command_grace.get(entity_id)
                grace_marker = (service_name, self._now_ms())
                self._executing_actions[entity_id] = service_name
                self._command_grace[entity_id] = grace_marker
                pending_saved = await self._persist_pending_target_dispatch(
                    at=moment,
                    room_id=config.room_id,
                    target_id=command.target_id,
                    action=command.action.value,
                    evidence_revision=revision_before,
                    operation_id=operation_id,
                    config_version=config_version,
                )
                if not pending_saved:
                    self._restore_unsent_command_markers(
                        entity_id,
                        service_name,
                        grace_marker,
                        previous_executing,
                        previous_grace,
                    )
                    receipt["evidenceRevision"] = revision_before
                    receipt["currentEvidenceRevision"] = (
                        self._target_evidence_revision(entity_id)
                    )
                    receipt["currentTarget"] = self._target_evidence_snapshot(entity_id)
                    receipt["reason"] = "dispatch_pending_persistence_failed"
                    if superseding_off:
                        # The pending marker was rolled back, so the previous
                        # record is still stored; carry it into the receipt so
                        # the honest not_sent closure does not drop it.
                        receipt["_supersededFailure"] = {
                            "action": previous_failure[0],
                            "evidenceRevision": previous_failure[1],
                        }
                    receipts.append(receipt)
                    continue
                post_pending_profile_block: str | None = None
                if config.profile == "day_curve":
                    current_config = self._configs.get(config.room_id)
                    if current_config is not None and current_config.profile != config.profile:
                        post_pending_profile_block = "profile_blocked"
                    else:
                        profile_config = current_config or config
                        current = await self._build_context(
                            profile_config, self._now_ms()
                        )
                        post_pending_profile_block = (
                            self._post_pending_curve_profile_block_reason(
                                profile_config,
                                command,
                                current,
                            )
                        )
                post_pending_block = self._post_pending_dispatch_block_reason(
                    config,
                    command,
                    entity_id,
                    revision_before,
                    permission=permission,
                    dependencies=exact_dependencies,
                    exact_active=exact.active,
                    operation_moment=moment,
                    allow_legacy_unpowered_recheck=legacy_unpowered_assumed_off,
                )
                if post_pending_profile_block is not None:
                    post_pending_block = post_pending_profile_block
                if post_pending_block is not None:
                    self._restore_unsent_command_markers(
                        entity_id,
                        service_name,
                        grace_marker,
                        previous_executing,
                        previous_grace,
                    )
                    receipt["evidenceRevision"] = revision_before
                    receipt["currentEvidenceRevision"] = (
                        self._target_evidence_revision(entity_id)
                    )
                    receipt["currentTarget"] = self._target_evidence_snapshot(entity_id)
                    receipt["reason"] = post_pending_block
                    if post_pending_block == "already_on":
                        receipt["supersededBy"] = "target_on_before_dispatch"
                    if superseding_off:
                        # Carry the older protection into the receipt BEFORE it
                        # is durably recorded, so the store can restore it.
                        receipt["_supersededFailure"] = {
                            "action": previous_failure[0],
                            "evidenceRevision": previous_failure[1],
                        }
                    closed = await self._persist_not_sent_dispatch_closure(
                        at=moment,
                        room_id=config.room_id,
                        receipt=receipt,
                        operation_id=operation_id,
                        config_version=config_version,
                    )
                    if closed:
                        if superseding_off:
                            # The superseding off was refused before it was
                            # sent: keep the older protection it replaced; the
                            # durable store restores it from the receipt.
                            self._dispatch_failures[failure_key] = previous_failure
                        else:
                            self._dispatch_failures.pop(failure_key, None)
                        receipts.append(receipt)
                    continue
            if self._manual_priority_blocks(config, command):
                self._restore_unsent_command_markers(
                    entity_id,
                    service_name,
                    grace_marker,
                    previous_executing,
                    previous_grace,
                )
                receipt["reason"] = "manual_light_priority"
                receipt["sent"] = False
                receipt["sendOutcome"] = "not_sent"
                receipts.append(receipt)
                continue
            result: object | None = None
            try:
                result = await self._executor.execute(  # type: ignore[attr-defined]
                    self._hass, command
                )
            except Exception:  # noqa: BLE001 - a failed command is not a crash
                _LOGGER.exception("room lighting command failed")
                result = None
            finally:
                if entity_id is not None:
                    if self._executing_actions.get(entity_id) == _planned_service_name(
                        command
                    ):
                        self._executing_actions.pop(entity_id, None)
            confirmed = _receipt_confirmed(result)
            if confirmed:
                # A confirmed read-back proves the command reached the device;
                # an exception or a None result never does.
                sent, send_outcome = True, "sent"
            else:
                sent, send_outcome = _receipt_send_state(result)
            receipt["dispatchAttempt"] = send_outcome in {"sent", "uncertain"}
            receipt["sent"] = sent
            receipt["sendOutcome"] = send_outcome
            if entity_id is not None:
                receipt["evidenceRevision"] = revision_before
            if confirmed:
                receipt["confirmed"] = True
                receipt["reason"] = "confirmed"
                receipt["ownershipChanged"] = "auto"
                self._dispatch_failures.pop(failure_key, None)
                self._ownership.record_auto(
                    config.room_id, command.target_id, moment, confirmed=True
                )
                self._schedule_ownership_save()
            else:
                receipt["confirmed"] = False
                if send_outcome == "sent":
                    receipt["reason"] = "unconfirmed"
                elif send_outcome == "not_sent" and isinstance(result, Mapping):
                    receipt["reason"] = str(result.get("reason") or "dispatch_not_sent")
                else:
                    receipt["reason"] = f"dispatch_{send_outcome}"
                if entity_id is not None and receipt["dispatchAttempt"] is True:
                    self._dispatch_failures[failure_key] = (
                        command.action.value,
                        revision_before,
                    )
                elif entity_id is not None:
                    if superseding_off:
                        # The superseding off was never sent: keep the older
                        # protection it replaced instead of erasing it, and
                        # carry it into the receipt so the durable store
                        # restores it too.
                        self._dispatch_failures[failure_key] = previous_failure
                        receipt["_supersededFailure"] = {
                            "action": previous_failure[0],
                            "evidenceRevision": previous_failure[1],
                        }
                    else:
                        self._dispatch_failures.pop(failure_key, None)
                if config.profile == "day_curve":
                    failed_curve_targets.add(command.target_id)
            receipts.append(receipt)
        await self._persist_ownership()
        return receipts

    def _turn_off_dispatch_block_reason(
        self,
        config: RoomLightingConfig,
        command: PlannedCommand,
        entity_id: str | None,
        revision_before: str,
    ) -> str | None:
        """Final proof before automatic turn_off reaches Home Assistant.

        A person may turn a light on, change ownership, or produce a new device
        report while the live intent is being durably written.  The automatic
        off branch is allowed to proceed only with current confirmed AUTO
        ownership, no later manual record and trusted unchanged evidence.
        """

        if entity_id is None:
            return "target_unavailable"
        trusted, last_known, current_revision = self._target_trust(entity_id)
        # Матрица turn_off: доверенное + авто-владение (ниже) — штатно;
        # молчаливо-включённое с авто-владением — допустимо (выключение по
        # расписанию); молчаливо-выключенное — нечего гасить; неизвестное/
        # недоступное — вслепую не командуем.
        if not trusted and last_known != "on":
            return "dispatch_evidence_untrusted"
        if trusted and current_revision != revision_before:
            return "dispatch_evidence_changed"
        records = self._ownership.snapshots_for(
            config.room_id, frozenset({command.target_id})
        )
        latest = latest_ownership(records, command.target_id)
        if latest is None:
            return "auto_ownership_missing"
        if latest.source is OwnershipSource.MANUAL:
            return "manual_intervention_after_decision"
        if latest.source is not OwnershipSource.AUTO:
            return "auto_ownership_missing"
        if not latest.confirmed or not has_proven_auto_ownership(
            records, command.target_id
        ):
            return "manual_intervention_after_decision"
        return None

    def _turn_on_dispatch_block_reason(
        self,
        config: RoomLightingConfig,
        command: PlannedCommand,
        entity_id: str | None,
        revision_before: str,
        *,
        dependencies: Mapping[str, DevicePowerDependency] | None,
        exact_active: bool,
        current_reason: str = "target_on_before_dispatch",
        allow_legacy_unpowered_recheck: bool = False,
    ) -> str | None:
        """Synchronous target-state proof before automatic turn_on dispatch."""

        if entity_id is None:
            return "target_unavailable"
        if self._auto_on_blocked(config, self._now_ms()):
            return "manual_off_protection"
        manual_peer = self._manual_peer_turn_on_block_reason(
            config,
            command,
            dependencies=dependencies,
            exact_active=exact_active,
        )
        if manual_peer is not None:
            return manual_peer
        current_revision = self._target_evidence_revision(entity_id)
        trusted, last_known, _revision = self._target_trust(entity_id)
        # Матрица turn_on: доверенное OFF/ON — штатные проверки; молчаливо-
        # выключенное — команда аддитивная, разрешена (подтверждение свежим
        # чтением обязательно); молчаливо-включённое — «уже включено»;
        # неизвестное/недоступное — вслепую не командуем.
        if not trusted and last_known != "off":
            if last_known == "on":
                return "already_on"
            return "target_state_untrusted"
        current_state = (
            self._effective_entity_state(entity_id, dependencies)
            if exact_active and dependencies is not None and trusted
            else (
                self._entity_trusted_state(entity_id)
                if trusted
                else last_known or "off"
            )
        )
        if current_state == "unknown":
            return "target_state_untrusted"
        if current_state == "on":
            return "already_on"
        if current_revision != revision_before:
            if (
                allow_legacy_unpowered_recheck
                and not self._trusted_evidence_revision(revision_before)
                and current_state == "off"
            ):
                return None
            return "dispatch_evidence_changed"
        return None

    def _manual_peer_turn_on_block_reason(
        self,
        config: RoomLightingConfig,
        command: PlannedCommand,
        *,
        dependencies: Mapping[str, DevicePowerDependency] | None,
        exact_active: bool,
    ) -> str | None:
        """Block a TURN_ON when a person just chose an interchangeable peer."""

        target = config.devices.target(command.target_id)
        if target is None:
            return None
        records = self._ownership.snapshots_for(
            config.room_id, config.devices.light_target_ids
        )
        for peer in config.devices.light_targets:
            if peer.id == target.id or not self._targets_interchangeable(
                config, target, peer
            ):
                continue
            latest = latest_ownership(records, peer.id)
            if (
                latest is None
                or latest.source is not OwnershipSource.MANUAL
                or not latest.confirmed
            ):
                continue
            peer_state = (
                self._effective_entity_state(peer.entity_id, dependencies)
                if exact_active and dependencies is not None
                else self._entity_trusted_state(peer.entity_id)
            )
            if peer_state != "off":
                return "manual_peer"
        return None

    @staticmethod
    def _targets_interchangeable(
        config: RoomLightingConfig,
        target: object,
        peer: object,
    ) -> bool:
        if config.profile == "day_curve":
            return True
        target_group = getattr(target, "group_id", None)
        if target_group is not None and target_group == getattr(peer, "group_id", None):
            return True
        target_role = getattr(target, "role", None)
        if target_role is not None and target_role == getattr(peer, "role", None):
            return True
        return any(
            _targets_select_light(entry.targets, target)
            and _targets_select_light(entry.targets, peer)
            for entry in config.schedule
        )

    def _adjustment_dispatch_block_reason(
        self,
        config: RoomLightingConfig,
        command: PlannedCommand,
        entity_id: str | None,
        revision_before: str,
        *,
        dependencies: Mapping[str, DevicePowerDependency] | None,
        exact_active: bool,
        operation_moment: int,
    ) -> str | None:
        """Final proof before brightness/colour adjustment dispatch.

        Adjustments are not turn-on idempotency checks: an already-on target is
        the normal case and must not be blocked as ``already_on``.  They are
        allowed only for a trusted automatic branch (or the same operation right
        after its own confirmed turn-on) and still fail closed on a later manual
        ownership record or unrelated target evidence change.
        """

        if entity_id is None:
            return "target_unavailable"
        if self._auto_on_blocked(config, self._now_ms()):
            return "manual_off_protection"
        records = self._ownership.snapshots_for(
            config.room_id, frozenset({command.target_id})
        )
        latest = latest_ownership(records, command.target_id)
        if latest is None:
            return "auto_ownership_missing"
        if latest.source is OwnershipSource.MANUAL:
            return "manual_intervention_after_decision"
        if latest.source is not OwnershipSource.AUTO:
            return "auto_ownership_missing"
        if not latest.confirmed or not has_proven_auto_ownership(
            records, command.target_id
        ):
            return "manual_intervention_after_decision"
        current_revision = self._target_evidence_revision(entity_id)
        trusted, last_known, _revision = self._target_trust(entity_id)
        # Матрица регулировок:
        # - доверенное состояние — штатные регулировки;
        # - молчаливо-включённое (последнее известное ON, отчётов давно не
        #   было) — одна регулировка на комплект доказательств: дедупликация
        #   по последней запланированной команде, повтор блокируется до
        #   нового отчёта прибора;
        # - молчаливо-выключенное и неизвестное — не диммируем.
        if not trusted and last_known != "on":
            if last_known == "off":
                return "target_state_not_on"
            return "target_state_untrusted"
        if (
            not trusted
            and self._last_untrusted_adjustment.get(entity_id)
            == (command.brightness, command.color_temperature)
        ):
            return "adjustment_deduplicated"
        current_state = (
            self._effective_entity_state(entity_id, dependencies)
            if exact_active and dependencies is not None and trusted
            else (last_known or "on")
        )
        recent_own_turn_on = self._recent_own_turn_on_grace(entity_id)
        same_operation_turn_on = latest.at == operation_moment and recent_own_turn_on
        if current_revision != revision_before and not (
            same_operation_turn_on and current_state == "on"
        ):
            return "dispatch_evidence_changed"
        if current_state == "unknown":
            return "target_state_untrusted"
        if current_state != "on" and not same_operation_turn_on:
            return "target_state_not_on"
        return None

    def _post_pending_curve_profile_block_reason(
        self,
        config: RoomLightingConfig,
        command: PlannedCommand,
        current: RoomLightingContext,
    ) -> str | None:
        """Re-evaluate the day-curve profile after pending-save interleaving."""

        if curve_profile_block_reason(config, current) is not None:
            return "profile_blocked"
        presence = self._curve_presence_state(current)
        confirmed, absence_seconds = self._observe_curve_presence(
            config, current, current.now, presence=presence
        )
        refreshed = evaluate_curve_room(
            config,
            current,
            presence=presence,
            presence_confirmed=confirmed,
            absence_seconds=absence_seconds,
        )
        if command not in refreshed.commands:
            return "superseded"
        return None

    def _recent_own_turn_on_grace(self, entity_id: str) -> bool:
        commanded = self._command_grace.get(entity_id)
        return (
            commanded is not None
            and commanded[0] == "turn_on"
            and self._now_ms() - commanded[1] < COMMAND_GRACE_MS
        )

    def _restore_unsent_command_markers(
        self,
        entity_id: str,
        service_name: str,
        grace_marker: tuple[str, int],
        previous_executing: str | None,
        previous_grace: tuple[str, int] | None,
    ) -> None:
        """Undo only the in-memory markers created for a command not sent."""

        if self._executing_actions.get(entity_id) == service_name:
            if previous_executing is None:
                self._executing_actions.pop(entity_id, None)
            else:
                self._executing_actions[entity_id] = previous_executing
        if self._command_grace.get(entity_id) == grace_marker:
            if previous_grace is None:
                self._command_grace.pop(entity_id, None)
            else:
                self._command_grace[entity_id] = previous_grace

    def _post_pending_dispatch_block_reason(
        self,
        config: RoomLightingConfig,
        command: PlannedCommand,
        entity_id: str | None,
        revision_before: str,
        *,
        permission: tuple[str, int, bool] | None,
        dependencies: Mapping[str, DevicePowerDependency] | None,
        exact_active: bool,
        operation_moment: int,
        allow_legacy_unpowered_recheck: bool = False,
    ) -> str | None:
        """Final synchronous permit check after durable pending and before executor.

        There must be no ``await`` between this method and ``executor.execute``.
        Any manual action, config stop/disable/version change, reserved writer,
        or changed/untrusted target evidence observed while the pending marker
        was being durably saved wins over the planned automatic command.
        """

        if not self._running:
            return "runtime_stopped"
        away_block = self._automatic_away_block_reason()
        if away_block is not None:
            return away_block
        if permission is not None and not self._dispatch_permission_valid(permission):
            return "permission_stale"
        if self._command_conflicts_with_reserved_writer(
            config, command, dependencies=dependencies if exact_active else None
        ):
            return "reserved"
        if command.action is LightAction.TURN_OFF:
            return self._turn_off_dispatch_block_reason(
                config,
                command,
                entity_id,
                revision_before,
            )
        if command.action is LightAction.TURN_ON:
            return self._turn_on_dispatch_block_reason(
                config,
                command,
                entity_id,
                revision_before,
                dependencies=dependencies,
                exact_active=exact_active,
                allow_legacy_unpowered_recheck=allow_legacy_unpowered_recheck,
            )
        if command.action in {
            LightAction.SET_BRIGHTNESS,
            LightAction.SET_COLOR_TEMPERATURE,
        }:
            return self._adjustment_dispatch_block_reason(
                config,
                command,
                entity_id,
                revision_before,
                dependencies=dependencies,
                exact_active=exact_active,
                operation_moment=operation_moment,
            )
        return "unsupported_action"

    def _auto_on_blocked(self, config: RoomLightingConfig, moment: int) -> bool:
        protection = self._protection_for(config)
        return protection.blocks_auto_on(
            now=moment,
            absence_proven=protection.absence_confirmed,
            absence_since=protection.absence_since,
        )

    async def _ensure_power_source(
        self,
        dependency: DevicePowerDependency,
        *,
        at: int,
        room_id: str,
        operation_id: str | None,
        config_version: int | None,
    ) -> tuple[bool, dict[str, object]]:
        async with self._power_source_lock(dependency.power_source_entity_id):
            return await self._ensure_power_source_locked(
                dependency,
                at=at,
                room_id=room_id,
                operation_id=operation_id,
                config_version=config_version,
            )

    async def _ensure_power_source_locked(
        self,
        dependency: DevicePowerDependency,
        *,
        at: int,
        room_id: str,
        operation_id: str | None,
        config_version: int | None,
    ) -> tuple[bool, dict[str, object]]:
        """Prepare the one source a dependent target needs, or refuse.

        The exact graph is the source of truth: a ``requires_on`` source is
        never switched on automatically, while ``auto_turn_on`` follows the
        shared policy, a confirmed read-back and the configured warm-up. After
        the wait the caller re-checks manual protection and reservations.
        """

        source_entity_id = dependency.power_source_entity_id
        detail: dict[str, object] = {
            "sourceEntityId": source_entity_id,
            "policy": dependency.policy,
            "warmupSeconds": dependency.warmup_seconds,
            "sourceTurnedOn": False,
            "waitedSeconds": 0.0,
            "dispatchAttempt": False,
            "sent": False,
            "sendOutcome": "not_sent",
        }
        if self._is_reserved_target_or_entity(None, source_entity_id):
            detail["reason"] = "power_source_reserved"
            return False, detail
        away_block = self._automatic_away_block_reason()
        if away_block is not None:
            detail["reason"] = away_block
            return False, detail
        evidence_revision = self._power_evidence_revision(source_entity_id)
        detail["evidenceRevision"] = evidence_revision
        if self._power_attempt_blocked(source_entity_id):
            # An unconfirmed power preparation is never repeated until the
            # source publishes new physical evidence.
            detail["reason"] = "power_source_unconfirmed"
            detail["suppressed"] = True
            return False, detail
        state = self._entity_state_object(source_entity_id)
        if trusted_power_state(state, same_state_confirmation=True) == "on":
            detail["reason"] = "power_source_on"
            return True, detail
        if dependency.policy != AUTO_TURN_ON_POLICY:
            detail["reason"] = (
                "power_source_off"
                if state is not None
                and str(getattr(state, "state", "unknown")) == "off"
                else "power_source_unavailable"
            )
            return False, detail
        resolver = self._electrical_breaker_resolver
        if resolver is None:
            detail["reason"] = "automatic_power_source_safety_unavailable"
            return False, detail
        try:
            breaker = resolver(source_entity_id)
        except Exception:  # noqa: BLE001 - an unknown breaker must fail closed
            detail["reason"] = "automatic_power_source_safety_unavailable"
            return False, detail
        if breaker:
            detail["reason"] = "automatic_breaker_power_on_forbidden"
            return False, detail
        ensure_power = getattr(self._executor, "async_power_on", None)
        if not callable(ensure_power) or self._hass is None:
            detail["reason"] = "power_source_unavailable"
            return False, detail
        if not await self._persist_pending_power_dispatch(
            at=at,
            room_id=room_id,
            source_entity_id=source_entity_id,
            evidence_revision=evidence_revision,
            operation_id=operation_id,
            config_version=config_version,
        ):
            detail["reason"] = "power_pending_persistence_failed"
            return False, detail
        away_block = self._automatic_away_block_reason()
        if away_block is not None:
            detail["reason"] = away_block
            await self._persist_not_sent_power_dispatch_closure(
                at=at,
                room_id=room_id,
                source_entity_id=source_entity_id,
                evidence_revision=evidence_revision,
                reason=away_block,
                operation_id=operation_id,
                config_version=config_version,
            )
            return False, detail
        if self._is_reserved_target_or_entity(None, source_entity_id):
            detail["reason"] = "power_source_reserved"
            await self._persist_not_sent_power_dispatch_closure(
                at=at,
                room_id=room_id,
                source_entity_id=source_entity_id,
                evidence_revision=evidence_revision,
                reason="power_source_reserved",
                operation_id=operation_id,
                config_version=config_version,
            )
            return False, detail
        self._executing_actions[source_entity_id] = "turn_on"
        self._command_grace[source_entity_id] = ("turn_on", self._now_ms())
        detail["dispatchAttempt"] = True
        try:
            kwargs = (
                {"protected_entities": self._power_protected_entities(room_id)}
                if isinstance(self._executor, RoomLightingHaExecutor) else {}
            )
            receipt = await ensure_power(self._hass, source_entity_id, **kwargs)
        except Exception:  # noqa: BLE001 - keep the room isolated
            # The command may have reached the relay before the exception, so
            # the outcome is unknown, not a confirmed failure or a success.
            self._power_failures[source_entity_id] = (
                "turn_on",
                evidence_revision,
            )
            detail["reason"] = "power_source_uncertain"
            detail["sent"] = False
            detail["sendOutcome"] = "uncertain"
            return False, detail
        finally:
            self._executing_actions.pop(source_entity_id, None)
        if isinstance(receipt, Mapping):
            confirmed = bool(receipt.get("confirmed"))
            sent = bool(receipt.get("sent"))
            send_outcome = str(receipt.get("sendOutcome") or ("sent" if sent else "not_sent"))
            turned_on = sent
            detail["powerPreparation"] = dict(receipt)
        else:
            if isinstance(receipt, bool):
                confirmed = receipt
                sent = receipt
                send_outcome = "sent" if receipt else "uncertain"
            elif receipt is None:
                confirmed = False
                sent = False
                send_outcome = "uncertain"
            else:
                confirmed = bool(getattr(receipt, "confirmed", False))
                sent = bool(getattr(receipt, "sent", False))
                send_outcome = str(
                    getattr(receipt, "send_outcome", "sent" if sent else "uncertain")
                )
            turned_on = sent
        if send_outcome not in {"not_sent", "sent", "uncertain"}:
            send_outcome = "sent" if sent else "not_sent"
        detail["dispatchAttempt"] = send_outcome in {"sent", "uncertain"}
        detail["sent"] = sent
        detail["sendOutcome"] = send_outcome
        if not confirmed:
            if detail["dispatchAttempt"]:
                self._power_failures[source_entity_id] = (
                    "turn_on",
                    evidence_revision,
                )
            if isinstance(receipt, Mapping) and send_outcome == "not_sent":
                reason = receipt.get("reason")
                detail["reason"] = str(reason or "power_source_unavailable")
            elif send_outcome == "uncertain":
                detail["reason"] = "power_source_uncertain"
            else:
                detail["reason"] = "power_source_unconfirmed"
            if not detail["dispatchAttempt"]:
                self._power_failures.pop(source_entity_id, None)
            return False, detail
        self._power_failures.pop(source_entity_id, None)
        detail["sourceTurnedOn"] = turned_on
        if turned_on and dependency.warmup_seconds > 0:
            await asyncio.sleep(dependency.warmup_seconds)
            detail["waitedSeconds"] = dependency.warmup_seconds
            state = self._entity_state_object(source_entity_id)
            if (
                trusted_power_state(state, same_state_confirmation=True) != "on"
                or state_is_restored_or_cached(state)
            ):
                self._power_failures[source_entity_id] = (
                    "turn_on",
                    evidence_revision,
                )
                detail["reason"] = "power_source_unconfirmed"
                return False, detail
        detail["reason"] = "power_source_on"
        return True, detail

    async def _ensure_legacy_room_power(
        self,
        power_entity: str,
        *,
        at: int,
        room_id: str,
        operation_id: str | None,
        config_version: int | None,
    ) -> tuple[bool, dict[str, object]]:
        async with self._power_source_lock(power_entity):
            return await self._ensure_legacy_room_power_locked(
                power_entity,
                at=at,
                room_id=room_id,
                operation_id=operation_id,
                config_version=config_version,
            )

    async def _ensure_legacy_room_power_locked(
        self,
        power_entity: str,
        *,
        at: int,
        room_id: str,
        operation_id: str | None,
        config_version: int | None,
    ) -> tuple[bool, dict[str, object]]:
        """The legacy whole-room power path for rooms without an exact claim."""

        detail: dict[str, object] = {
            "sourceEntityId": power_entity,
            "reason": "power_source_unavailable",
            "dispatchAttempt": False,
            "sent": False,
            "sendOutcome": "not_sent",
        }
        ensure_power = getattr(self._executor, "async_power_on", None)
        if not callable(ensure_power) or self._hass is None:
            return False, detail
        away_block = self._automatic_away_block_reason()
        if away_block is not None:
            detail["reason"] = away_block
            return False, detail
        evidence_revision = self._power_evidence_revision(power_entity)
        detail["evidenceRevision"] = evidence_revision
        if self._power_attempt_blocked(power_entity):
            detail["reason"] = "power_source_unconfirmed"
            detail["suppressed"] = True
            return False, detail
        state = self._entity_state_object(power_entity)
        if trusted_power_state(state, same_state_confirmation=True) == "on":
            detail["reason"] = "power_source_on"
            return True, detail
        if not await self._persist_pending_power_dispatch(
            at=at,
            room_id=room_id,
            source_entity_id=power_entity,
            evidence_revision=evidence_revision,
            operation_id=operation_id,
            config_version=config_version,
        ):
            detail["reason"] = "power_pending_persistence_failed"
            return False, detail
        away_block = self._automatic_away_block_reason()
        if away_block is not None:
            detail["reason"] = away_block
            await self._persist_not_sent_power_dispatch_closure(
                at=at,
                room_id=room_id,
                source_entity_id=power_entity,
                evidence_revision=evidence_revision,
                reason=away_block,
                operation_id=operation_id,
                config_version=config_version,
            )
            return False, detail
        if self._is_reserved_target_or_entity(None, power_entity):
            detail["reason"] = "power_source_reserved"
            await self._persist_not_sent_power_dispatch_closure(
                at=at,
                room_id=room_id,
                source_entity_id=power_entity,
                evidence_revision=evidence_revision,
                reason="power_source_reserved",
                operation_id=operation_id,
                config_version=config_version,
            )
            return False, detail
        self._executing_actions[power_entity] = "turn_on"
        self._command_grace[power_entity] = ("turn_on", self._now_ms())
        detail["dispatchAttempt"] = True
        try:
            kwargs = (
                {"protected_entities": self._power_protected_entities(room_id)}
                if isinstance(self._executor, RoomLightingHaExecutor) else {}
            )
            receipt = await ensure_power(self._hass, power_entity, **kwargs)
        except Exception:  # noqa: BLE001 - keep the room isolated
            self._power_failures[power_entity] = ("turn_on", evidence_revision)
            detail["reason"] = "power_source_uncertain"
            detail["sent"] = False
            detail["sendOutcome"] = "uncertain"
            return False, detail
        finally:
            self._executing_actions.pop(power_entity, None)
        if isinstance(receipt, Mapping):
            powered = bool(receipt.get("confirmed"))
            sent = bool(receipt.get("sent"))
            send_outcome = str(receipt.get("sendOutcome") or ("sent" if sent else "not_sent"))
            detail["powerPreparation"] = dict(receipt)
        else:
            if isinstance(receipt, bool):
                powered = receipt
                sent = receipt
                send_outcome = "sent" if receipt else "uncertain"
            elif receipt is None:
                powered = False
                sent = False
                send_outcome = "uncertain"
            else:
                powered = bool(getattr(receipt, "confirmed", False))
                sent = bool(getattr(receipt, "sent", False))
                send_outcome = str(
                    getattr(receipt, "send_outcome", "sent" if sent else "uncertain")
                )
        if send_outcome not in {"not_sent", "sent", "uncertain"}:
            send_outcome = "sent" if sent else "not_sent"
        detail["dispatchAttempt"] = send_outcome in {"sent", "uncertain"}
        detail["sent"] = sent
        detail["sendOutcome"] = send_outcome
        if powered:
            self._power_failures.pop(power_entity, None)
            detail["reason"] = "power_source_on"
            return True, detail
        if detail["dispatchAttempt"]:
            self._power_failures[power_entity] = ("turn_on", evidence_revision)
        else:
            self._power_failures.pop(power_entity, None)
        if isinstance(receipt, Mapping) and send_outcome == "not_sent":
            reason = receipt.get("reason")
            detail["reason"] = str(reason or "power_source_unavailable")
        elif send_outcome == "uncertain":
            detail["reason"] = "power_source_uncertain"
        else:
            detail["reason"] = "power_source_unconfirmed"
        return False, detail

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
        self,
        config: RoomLightingConfig,
        command: PlannedCommand,
        *,
        dependencies: Mapping[str, DevicePowerDependency] | None = None,
    ) -> bool:
        target = config.devices.target(command.target_id)
        target_entity = target.entity_id if target is not None else None
        if self._is_reserved_target_or_entity(command.target_id, target_entity):
            return True
        if dependencies is not None:
            # Exact map: only this target's own upstream source belongs to its
            # command path. Another circuit's reservation must not block it.
            dependency = (
                dependencies.get(target_entity)
                if target_entity is not None
                else None
            )
            if dependency is None:
                return False
            return self._is_reserved_target_or_entity(
                None, dependency.power_source_entity_id
            )
        power = config.devices.power_switch
        return power is not None and self._is_reserved_target_or_entity(
            power.id, power.entity_id
        )

    def _binding_conflicts_with_reserved_writer(
        self,
        config: RoomLightingConfig,
        target_ids: Sequence[str],
        *,
        dependencies: Mapping[str, DevicePowerDependency] | None = None,
    ) -> bool:
        for target_id in target_ids:
            target = config.devices.target(target_id)
            target_entity = target.entity_id if target is not None else None
            if self._is_reserved_target_or_entity(target_id, target_entity):
                return True
            if dependencies is not None and target_entity is not None:
                dependency = dependencies.get(target_entity)
                if dependency is not None and self._is_reserved_target_or_entity(
                    None, dependency.power_source_entity_id
                ):
                    return True
        if dependencies is not None:
            return False
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

    def _entity_state_object(self, entity_id: str) -> object | None:
        if self._hass is None:
            return None
        states = getattr(self._hass, "states", None)
        return states.get(entity_id) if states is not None else None

    def _power_dependencies(
        self,
    ) -> Mapping[str, DevicePowerDependency] | None:
        """Return the exact dependency map, or ``None`` when it is unavailable.

        A resolver that raises or returns a damaged document fails closed: the
        caller must not guess the electrical circuit.
        """

        resolver = self._power_dependency_resolver
        if resolver is None:
            return None
        try:
            value = resolver()
        except Exception:  # noqa: BLE001 - a damaged map must not fail a room
            _LOGGER.warning("room lighting power dependency map is unavailable")
            return None
        if not isinstance(value, Mapping):
            _LOGGER.warning("room lighting power dependency map is damaged")
            return None
        dependencies: dict[str, DevicePowerDependency] = {}
        for entity_id, dependency in value.items():
            if not isinstance(entity_id, str) or not isinstance(
                dependency, DevicePowerDependency
            ):
                _LOGGER.warning("room lighting power dependency map is damaged")
                return None
            dependencies[entity_id] = dependency
        return dependencies

    def _target_evidence_revision(self, entity_id: str) -> str:
        """Identify the physical evidence a retry would be based on."""

        _trusted, _last_known, revision = self._target_trust(entity_id)
        return revision

    def _target_trust(self, entity_id: str) -> tuple[bool, str | None, str]:
        """Единая классификация доказательств цели (trusted/silent/dead).

        Возвращает ``(trusted, last_known, revision)``: ``trusted`` — отчёт
        свежее окна доверия; ``last_known`` — последнее известное
        ``on``/``off`` для молчащего прибора (или ``None`` для
        unavailable/без отчётов вообще). Единый механизм для всех
        dispatch-проверок: молчание не считается неизвестным состоянием,
        а неизвестное — не выдумывается. Для доверенного состояния ревизия
        хранит момент отчёта: повторный отчёт без изменения значения тоже
        обновляет доказательство (освобождает неподтверждённые попытки).
        """

        state = self._entity_state_object(entity_id)
        if state is None:
            return False, None, "untrusted:missing"
        # Восстановленное/кэшированное состояние (после рестарта HA) — не
        # доказательство физического состояния: считаем цель «мёртвой» для
        # команд, пока прибор сам не подтвердит состояние свежим отчётом.
        if state_is_restored_or_cached(state):
            return False, None, "untrusted:restored"
        trusted_value = self._entity_trusted_state(entity_id)
        raw = str(getattr(state, "state", "unknown")).lower()
        if trusted_value in ("on", "off"):
            # Доверенная ревизия хранит момент отчёта: повторный отчёт без
            # изменения значения тоже обновляет доказательство и освобождает
            # неподтверждённые попытки.
            observed = trusted_target_observed_ms(state, now_ms=self._now_ms())
            revision = (
                f"{trusted_value}|{observed}"
                if observed is not None
                else f"{trusted_value}|trusted"
            )
            return True, trusted_value, revision
        if raw in ("off", "on"):
            return False, raw, f"untrusted:{raw}"
        return False, None, f"untrusted:{raw}"

    def _target_evidence_snapshot(self, entity_id: str) -> dict[str, object]:
        """Trusted target evidence persisted in receipts and compared after awaits."""

        revision = self._target_evidence_revision(entity_id)
        snapshot: dict[str, object] = {
            "entityId": entity_id,
            "evidenceRevision": revision,
            "trusted": self._trusted_evidence_revision(revision),
        }
        observed_ms = self._target_evidence_observed_ms(entity_id)
        if observed_ms is not None:
            snapshot["observedMs"] = observed_ms
        return snapshot

    @staticmethod
    def _trusted_evidence_revision(revision: str) -> bool:
        return not revision.startswith("untrusted:")

    def _power_evidence_revision(self, entity_id: str) -> str:
        state = self._entity_state_object(entity_id)
        if state is None:
            return "untrusted:missing"
        trusted_state = trusted_power_state(state, same_state_confirmation=True)
        if trusted_state is None:
            value = str(getattr(state, "state", "unknown")).lower()
            if value not in {"off", "on"}:
                value = "unknown"
            return f"untrusted:{value}"
        observed = self._state_observed_ms(state)
        revision = "" if observed is None else str(observed)
        return f"{trusted_state}|{revision}"

    def _target_evidence_observed_ms(self, entity_id: str) -> int | None:
        state = self._entity_state_object(entity_id)
        if state is None or self._entity_trusted_state(entity_id) == "unknown":
            return None
        return trusted_target_observed_ms(state, now_ms=self._now_ms())

    def _power_evidence_observed_ms(self, entity_id: str) -> int | None:
        state = self._entity_state_object(entity_id)
        if state is None or trusted_power_state(state, same_state_confirmation=True) is None:
            return None
        return self._state_observed_ms(state)

    @staticmethod
    def _state_observed_ms(state: object) -> int | None:
        for name in ("last_reported", "last_updated", "last_changed"):
            observed = getattr(state, name, None)
            if isinstance(observed, datetime):
                if observed.tzinfo is None:
                    observed = observed.replace(tzinfo=timezone.utc)
                return int(observed.astimezone(timezone.utc).timestamp() * 1000)
            if isinstance(observed, (int, float)) and not isinstance(observed, bool):
                numeric = float(observed)
                return int(numeric if numeric > 1_000_000_000_000 else numeric * 1000)
        return None

    def _effective_entity_state(
        self,
        entity_id: str | None,
        dependencies: Mapping[str, DevicePowerDependency],
    ) -> str:
        """Resolve one entity without treating uncertain reports as off."""

        if entity_id is None:
            return "unknown"
        source_ids = {
            dependency.power_source_entity_id
            for dependency in dependencies.values()
        }

        def read_state(requested_entity_id: str) -> str | None:
            state = self._entity_state_object(requested_entity_id)
            if requested_entity_id in source_ids:
                return trusted_power_state(state, same_state_confirmation=True)
            trusted = self._entity_trusted_state(requested_entity_id)
            return None if trusted == "unknown" else trusted

        effective, _status = effective_device_state(
            entity_id, dependencies, read_state
        )
        return effective

    def _entity_trusted_state(self, entity_id: str | None) -> str:
        if entity_id is None:
            return "unknown"
        state = self._entity_state_object(entity_id)
        return (
            trusted_target_state(
                state,
                now_ms=self._now_ms(),
                freshness_ms=CURVE_SENSOR_FRESHNESS_MS,
            )
            or "unknown"
        )

    def _light_on_by_target(self) -> dict[tuple[str, str], bool]:
        """Current effective on/off state per (room_id, target_id) for pruning."""

        result: dict[tuple[str, str], bool] = {}
        dependencies = self._power_dependencies()
        for room_id, config in self._configs.items():
            decision = self._exact_power_decision(config, dependencies)
            projection = decision.projection or {}
            for target in config.devices.light_targets:
                entity_id = target.entity_id
                if decision.active:
                    if target.id in decision.unproven_target_ids:
                        # A claimed target whose edge is missing or wrong cannot
                        # prove itself off; never prune a manual hold on a guess.
                        state_on = True
                    elif entity_id is not None and entity_id in projection:
                        # Only claimed edges affect effective state. Extra live
                        # map edges for unclaimed targets are ignored here.
                        state_on = self._effective_entity_state(
                            entity_id, projection
                        ) != "off"
                    else:
                        # Independent targets in a claimed room keep their own
                        # trusted state; unknown remains fail-closed.
                        state_on = self._entity_trusted_state(entity_id) != "off"
                else:
                    state_on = not (
                        self._room_power_state(config) == "on"
                        and self._entity_trusted_state(entity_id) == "off"
                    )
                result[(room_id, target.id)] = state_on
        return result

    def _room_power_state(self, config: RoomLightingConfig) -> str:
        """Trusted room power state; unknown is not a safe off proof."""

        power = config.devices.power_switch
        if power is None or power.entity_id is None:
            return "on"
        return self._entity_trusted_state(power.entity_id)

    def _legacy_unpowered_target_assumed_off(
        self, config: RoomLightingConfig, entity_id: str
    ) -> bool:
        """Whether legacy context may plan a retry for an unavailable target.

        This does not turn an unavailable target into a general OFF proof. It is
        only the narrow legacy room-power case: the room power switch itself has
        a trusted fresh OFF report and the dependent Zigbee target reports
        ``unavailable`` because it is unpowered. After power is confirmed, the
        target must publish trusted OFF before the command is actually sent.
        """

        if self._room_power_state(config) != "off":
            return False
        state = self._entity_state_object(entity_id)
        return str(getattr(state, "state", "")).lower() == "unavailable"

    def _presence_active_by_room(self) -> dict[str, bool]:
        """Whether startup must retain a room's manual protection.

        A configured presence or motion sensor must provide a fresh explicit
        ``off`` before restart pruning may treat the room as empty.  ``on``
        keeps the hold as before; unknown, unavailable and stale readings do
        too, because they do not prove absence.  A room without such a sensor
        keeps its configured no-sensor fallback.
        """

        result: dict[str, bool] = {}
        for room_id, config in self._configs.items():
            result[room_id] = any(
                sensor.kind in _PRESENCE_KINDS
                and sensor.entity_id is not None
                and self._entity_trusted_state(sensor.entity_id) != "off"
                for sensor in config.devices.sensors
            )
        return result

    def _home_timezone(self):
        if self._hass is None:
            return timezone.utc
        return _resolve_timezone(self._hass)

    def _morning_gate(self, config, morning, moment: int) -> str | None:
        """Morning boundary decision for one evaluation pass.

        Returns ``None`` when the boundary does not apply to this pass (before
        the boundary time or already transitioned today), ``"wait"`` to keep
        the night state until the first trusted presence, or ``"transition"``
        to release the holds dated before the boundary and let the day profile
        run. There is no time limit on the wait: the first trusted presence at
        any later hour starts the day, and the curve itself then picks the
        scenario that matches the current time of day. A wait persists across
        midnight and a restart until the presence arrives or a new boundary
        re-arms the cycle.
        """

        now_dt = datetime.fromtimestamp(moment / 1000, self._home_timezone())
        today = now_dt.date().isoformat()
        hour_text, minute_text = morning.time.split(":")
        boundary_wall = now_dt.replace(
            hour=int(hour_text), minute=int(minute_text), second=0, microsecond=0
        )
        # DST-safe cutoff: for an ambiguous or nonexistent wall time the two
        # folds map to different instants; the earlier one releases less
        # manual protection, so it is the safe choice. The cutoff is also
        # never in the future.
        boundary_ms = min(
            int(boundary_wall.replace(fold=0).timestamp() * 1000),
            int(boundary_wall.replace(fold=1).timestamp() * 1000),
            moment,
        )
        entry = self._ownership.morning_entry(config.room_id)
        waiting = entry is not None and entry.get("waiting") is True
        transitioned = (
            entry is not None
            and not entry.get("waiting")
            and entry.get("date") == today
        )
        if transitioned:
            return None
        if now_dt < boundary_wall:
            if waiting:
                # An overnight wait keeps the night state; a trusted presence
                # starts the day. The person is present NOW, so their own
                # arrival supersedes every earlier manual hold: the cutoff is
                # the current moment (never in the future by the clamp inside
                # the transition). Without presence the room keeps waiting.
                if self._morning_presence_confirmed(config):
                    self._morning_apply_transition(
                        config, morning, moment, moment
                    )
                    return "transition"
                return "wait"
            return None
        if not morning.presence_gate:
            self._morning_apply_transition(config, morning, moment, boundary_ms)
            return "transition"
        if waiting:
            cutoff = entry.get("boundaryMs")  # type: ignore[union-attr]
            if not type(cutoff) is int:
                cutoff = boundary_ms
            # Открытие входной двери (замок/датчик входа) после границы —
            # то же «первое присутствие»: будим день сразу, не ждём минуту
            # до срабатывания датчика присутствия.
            door_wake = self._entry_wakeup_until.get(config.room_id, 0) >= moment
            if self._morning_presence_confirmed(config) or door_wake:
                self._morning_apply_transition(
                    config, morning, moment, min(cutoff, moment)
                )
                return "transition"
            if cutoff != boundary_ms:
                # Still nobody since the boundary: a new day's wait cycle
                # begins, so a later presence releases up to this morning.
                self._ownership.mark_morning_waiting(
                    config.room_id, today, boundary_ms, moment
                )
            return "wait"
        if self._morning_presence_confirmed(config):
            self._morning_apply_transition(config, morning, moment, boundary_ms)
            return "transition"
        self._ownership.mark_morning_waiting(
            config.room_id, today, boundary_ms, moment
        )
        if not any(
            sensor.kind in _PRESENCE_KINDS and sensor.entity_id is not None
            for sensor in config.devices.sensors
        ):
            if config.room_id not in self._morning_wait_warned:
                self._morning_wait_warned.add(config.room_id)
                _LOGGER.warning(
                    "room lighting morning gate for %s waits for presence, "
                    "but no presence/motion sensor is configured",
                    config.room_id,
                )
        return "wait"

    def _morning_presence_confirmed(self, config) -> bool:
        """Whether a configured presence/motion sensor reads a trusted ``on``."""

        for sensor in config.devices.sensors:
            if sensor.kind not in _PRESENCE_KINDS or sensor.entity_id is None:
                continue
            if self._entity_trusted_state(sensor.entity_id) == "on":
                return True
        return False

    def _morning_apply_transition(
        self, config, morning, moment: int, before_ms: int
    ) -> None:
        # A DST gap can make the wall-clock boundary land after the current
        # moment; the cutoff must never be in the future, or a manual action
        # made after the jump could be erased.
        cutoff = min(int(before_ms), moment)
        removed = self._ownership.release_room_manual_holds(
            config.room_id,
            config.devices.light_target_ids,
            before_ms=cutoff,
        )
        now_dt = datetime.fromtimestamp(moment / 1000, self._home_timezone())
        self._ownership.mark_morning_transition(
            config.room_id, now_dt.date().isoformat(), moment
        )
        self._morning_wait_warned.discard(config.room_id)
        _LOGGER.info(
            "room lighting morning boundary %s released %d manual hold(s) for %s",
            morning.time,
            removed,
            config.room_id,
        )

    def _rebuild_index(self) -> None:
        from .related_light_sources import RelatedLightSources
        authority = self._manual_light_authority
        if authority is not None:
            authority.set_related_sources(RelatedLightSources.from_configs(self._configs.values()))
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
        self._core_rooms_by_entity = rooms_by_entity
        self._rooms_by_entity = rooms_by_entity
        self._entry_by_entity = entry_by_entity
        self._targets_by_entity = targets_by_entity
        self._power_by_entity = power_by_entity
        update = getattr(self._executor, "update_targets", None)
        if callable(update):
            update(targets)

    def _exact_power_decision(
        self,
        config: RoomLightingConfig,
        dependencies: Mapping[str, DevicePowerDependency] | None,
    ) -> ExactPowerDecision:
        """Resolve the release-owned migration claim for one room."""

        return decide_exact_power(
            tuple(
                (target.id, target.entity_id)
                for target in config.devices.light_targets
            ),
            dependencies,
            manifest=self._power_migration_manifest,
            room_id=config.room_id,
        )

    def _warn_incomplete_topology(
        self, config: RoomLightingConfig, decision: ExactPowerDecision
    ) -> None:
        diagnostics = decision.diagnostics
        signature = (config.room_id, diagnostics)
        if signature in self._topology_warned:
            return
        self._topology_warned.add(signature)
        _LOGGER.warning(
            "room lighting exact power topology is incomplete for %s; "
            "affected non-off commands are blocked until these bindings are restored: %s",
            config.room_id,
            ", ".join(diagnostics) or "power dependency map unavailable",
        )

    @staticmethod
    def _exact_power_blocks_command(
        decision: ExactPowerDecision,
        command: PlannedCommand,
    ) -> bool:
        if not decision.active or command.action is LightAction.TURN_OFF:
            return False
        if decision.global_fail_closed:
            return True
        return command.target_id in decision.unproven_target_ids

    def _target_attempt_blocked(
        self,
        failure_key: tuple[str, str],
        entity_id: str,
        action: LightAction | None = None,
    ) -> bool:
        """Whether an unconfirmed series for this target lacks new evidence.

        The check is by evidence revision, not by action: after an unconfirmed
        turn_on, a follow-up brightness or colour command in the same series is
        just as blind. A new physical report releases the whole target.

        HH-016.d: a planned ``turn_off`` is the safe direction, and the trust
        matrix already allows it for a silently-on target with proven auto
        ownership. The retry guard must not veto that plan, so for ``turn_off``
        an unconfirmed series only blocks the SAME evidence revision: a silent
        target receives at most one scheduled off until it reports again.

        A report genuinely newer than the failed attempt releases the guard,
        even when that report has since aged out of the freshness window:
        otherwise a stale record can veto a matrix-authorized scheduled
        command indefinitely (a morning turn_on blocked by last night's off).
        """

        safe_off = action is LightAction.TURN_OFF
        recorded = self._dispatch_failures.get(failure_key)
        current_revision = self._target_evidence_revision(entity_id)
        if recorded is None:
            # The overflow latch means the durable map lost records: stay
            # fail-closed for every action, including a safe off, because a
            # missing record cannot bound a repeat while it is full.
            if not self._dispatch_overflow_latch:
                return False
            boundary = self._dispatch_overflow_blocked_after_ms
            if boundary is None or not self._trusted_evidence_revision(current_revision):
                return True
            observed = self._target_evidence_observed_ms(entity_id)
            return observed is None or observed <= boundary
        if recorded[1] == current_revision:
            return True
        recorded_ms = _revision_observed_ms(recorded[1])
        if recorded_ms is not None:
            latest_ms = self._proven_report_ms(entity_id)
            if latest_ms is not None and latest_ms > recorded_ms:
                # A proven physical report newer than the failed attempt
                # releases the whole target, even when that report has since
                # aged out of the freshness window: the guard must not outlive
                # the evidence it was recorded against.
                self._dispatch_failures.pop(failure_key, None)
                return False
            # The recorded failure is not older than any proven report: no
            # blind repeat while the evidence window is unchanged.
            if not safe_off:
                return True
            if recorded[0] == LightAction.TURN_OFF.value:
                return True
            return False
        if safe_off:
            # A planned off is the safe direction, and the trust matrix already
            # permits it for a silently-on target with proven auto ownership.
            # Allow it WITHOUT dropping the existing record: if the off is
            # refused before it was sent, the protection for other actions must
            # survive, and a prior unconfirmed OFF for the now-stale evidence is
            # not repeated (only a new physical report releases it).
            if (
                recorded[0] == LightAction.TURN_OFF.value
                and not self._trusted_evidence_revision(current_revision)
            ):
                return True
            return False
        if not self._trusted_evidence_revision(current_revision):
            return True
        self._dispatch_failures.pop(failure_key, None)
        return False

    def _proven_report_ms(self, entity_id: str) -> int | None:
        """Physical-report timestamp of a proven, non-future target state.

        Restored/cached, unknown/unavailable and future-dated states are not
        evidence a failed attempt was superseded.
        """

        state = self._entity_state_object(entity_id)
        if state is None or state_is_restored_or_cached(state):
            return None
        raw = str(getattr(state, "state", "unknown")).lower()
        if raw not in ("on", "off"):
            return None
        observed = self._state_observed_ms(state)
        if observed is None or observed > self._now_ms() + _REPORT_FUTURE_SKEW_MS:
            return None
        return observed

    def _power_attempt_blocked(self, entity_id: str) -> bool:
        """Whether the same unconfirmed power-on lacks new evidence."""

        recorded = self._power_failures.get(entity_id)
        current_revision = self._power_evidence_revision(entity_id)
        if recorded is None:
            if not self._dispatch_overflow_latch:
                return False
            boundary = self._dispatch_overflow_blocked_after_ms
            if boundary is None or not self._trusted_evidence_revision(current_revision):
                return True
            observed = self._power_evidence_observed_ms(entity_id)
            return observed is None or observed <= boundary
        if (
            not self._trusted_evidence_revision(current_revision)
            or recorded == ("turn_on", current_revision)
        ):
            return True
        self._power_failures.pop(entity_id, None)
        return False

    def _refresh_exact_power_index(self) -> None:
        """Re-read the durable map and keep the event index in sync with it."""

        dependencies = self._power_dependencies()
        exact_rooms: dict[str, set[str]] = {}
        if dependencies is not None:
            for room_id, config in self._configs.items():
                decision = self._exact_power_decision(config, dependencies)
                if not decision.active:
                    continue
                for dependency in (decision.projection or {}).values():
                    exact_rooms.setdefault(
                        dependency.power_source_entity_id, set()
                    ).add(room_id)
        self._exact_power_by_entity = exact_rooms
        merged = {
            entity_id: set(rooms)
            for entity_id, rooms in self._core_rooms_by_entity.items()
        }
        for entity_id, rooms in exact_rooms.items():
            merged.setdefault(entity_id, set()).update(rooms)
        self._rooms_by_entity = merged
        if self._running:
            self._refresh_exact_source_subscriptions()
        for entity_id in exact_rooms:
            if entity_id not in self._target_last_state:
                self._target_last_state[entity_id] = self._entity_state(entity_id)

    def _refresh_exact_source_subscriptions(self) -> None:
        """Track the exact sources that the core subscription does not cover."""

        current = frozenset(
            entity_id
            for entity_id in self._exact_power_by_entity
            if entity_id not in self._core_rooms_by_entity
        )
        if current == self._exact_source_entities:
            return
        if self._exact_source_unsubscribe is not None:
            try:
                self._exact_source_unsubscribe()
            except Exception:  # noqa: BLE001 - cleanup must be best effort
                _LOGGER.warning("room lighting exact source cleanup failed")
            self._exact_source_unsubscribe = None
        self._exact_source_entities = current
        if (
            not current
            or not self._running
            or self._track_state_changes is None
            or self._hass is None
        ):
            return
        try:
            self._exact_source_unsubscribe = self._track_state_changes(
                self._hass, tuple(sorted(current)), self._state_event
            )
        except Exception:  # noqa: BLE001 - a failed tracker must not stop rooms
            _LOGGER.warning("room lighting exact source subscription failed")
            self._exact_source_unsubscribe = None
            self._exact_source_entities = frozenset()

    def _restore_dispatch_failures(self) -> None:
        """Restore unconfirmed-attempt blocks from the durable receipts.

        Durable retry state is stored beside the bounded shadow journal so it
        survives journal rollover. Old payloads without that top-level state are
        still migrated from the receipts that remain in the journal window.
        """

        self._dispatch_failures = {}
        self._power_failures = {}
        self._dispatch_overflow_latch = False
        self._dispatch_overflow_blocked_after_ms = None
        self._dispatch_overflow_boundary_unknown = False
        provider = getattr(self._shadow, "journal_payload", None)
        if not callable(provider):
            return
        try:
            document = provider()
        except Exception:  # noqa: BLE001 - a damaged journal must not stop start
            _LOGGER.warning("room lighting dispatch journal is unavailable")
            return
        targets = {
            (config.room_id, target.id): target.entity_id
            for config in self._configs.values()
            for target in config.devices.light_targets
        }
        has_durable_failures = (
            isinstance(document, Mapping) and "dispatchFailures" in document
        )
        failures = document.get("dispatchFailures") if isinstance(document, Mapping) else None
        durable_failures_malformed = False
        if has_durable_failures and not isinstance(failures, list):
            durable_failures_malformed = True
        elif isinstance(failures, list):
            for raw in failures:
                if not isinstance(raw, Mapping):
                    durable_failures_malformed = True
                    continue
                kind = raw.get("kind")
                evidence_revision = raw.get("evidenceRevision")
                action = str(raw.get("action") or "")
                if kind == "overflow":
                    self._dispatch_overflow_latch = True
                    self._merge_dispatch_overflow_boundary(raw.get("blockedAfterMs"))
                    continue
                if not isinstance(evidence_revision, str):
                    durable_failures_malformed = True
                    continue
                if kind == "target":
                    room_id = raw.get("roomId")
                    target_id = raw.get("targetId")
                    if (
                        isinstance(room_id, str)
                        and isinstance(target_id, str)
                        and (room_id, target_id) in targets
                    ):
                        self._dispatch_failures[(room_id, target_id)] = (
                            action,
                            evidence_revision,
                        )
                    elif not (isinstance(room_id, str) and isinstance(target_id, str)):
                        durable_failures_malformed = True
                elif kind == "power":
                    source = raw.get("entityId")
                    if isinstance(source, str) and source:
                        self._power_failures[source] = (
                            action or "turn_on",
                            evidence_revision,
                        )
                    else:
                        durable_failures_malformed = True
                else:
                    durable_failures_malformed = True
        if has_durable_failures:
            if durable_failures_malformed:
                _LOGGER.warning(
                    "room lighting durable dispatch retry state is damaged; "
                    "blocking automatic retry until fresh trusted evidence"
                )
                self._dispatch_overflow_latch = True
                self._merge_dispatch_overflow_boundary(None)
            # Top-level dispatchFailures is the durable, newest retry state.
            # Legacy bounded journal replay is only for old payloads that do
            # not have that map at all; otherwise an old confirmed receipt can
            # accidentally clear a newer pending marker after a crash.
            return
        entries = document.get("entries") if isinstance(document, Mapping) else None
        if not isinstance(entries, list):
            return
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            room_id = entry.get("roomId")
            receipts = entry.get("dispatch")
            if not isinstance(room_id, str) or not isinstance(receipts, list):
                continue
            for raw in receipts:
                if not isinstance(raw, Mapping):
                    continue
                target_id = raw.get("targetId")
                evidence_revision = raw.get("evidenceRevision")
                if (
                    isinstance(target_id, str)
                    and isinstance(evidence_revision, str)
                    and (room_id, target_id) in targets
                ):
                    key = (room_id, target_id)
                    if raw.get("confirmed") is True:
                        self._dispatch_failures.pop(key, None)
                    elif (
                        raw.get("dispatchAttempt") is False
                        and raw.get("sendOutcome") == "not_sent"
                        and raw.get("reason") != "unconfirmed_attempt"
                    ):
                        self._dispatch_failures.pop(key, None)
                    elif (
                        raw.get("dispatchAttempt") is True
                        and raw.get("confirmed") is not True
                    ):
                        self._dispatch_failures[key] = (
                            str(raw.get("action") or ""),
                            evidence_revision,
                        )
                power = raw.get("power")
                if not isinstance(power, Mapping):
                    continue
                source = power.get("sourceEntityId")
                power_revision = power.get("evidenceRevision")
                if not isinstance(source, str) or not isinstance(
                    power_revision, str
                ):
                    continue
                if power.get("reason") == "power_source_on":
                    self._power_failures.pop(source, None)
                    continue
                if (
                    power.get("dispatchAttempt") is False
                    and power.get("sendOutcome") == "not_sent"
                    and power.get("reason") != "power_source_unconfirmed"
                ):
                    self._power_failures.pop(source, None)
                    continue
                preparation = power.get("powerPreparation")
                attempted = (
                    power.get("dispatchAttempt") is True
                    or bool(power.get("sourceTurnedOn"))
                    or power.get("sendOutcome") in {"sent", "uncertain"}
                    or (
                        isinstance(preparation, Mapping)
                        and preparation.get("sendOutcome") in {"sent", "uncertain"}
                    )
                )
                if attempted:
                    self._power_failures[source] = ("turn_on", power_revision)

    def _merge_dispatch_overflow_boundary(self, value: object) -> None:
        """Merge an overflow boundary while preserving legacy unknown latches."""

        if self._dispatch_overflow_boundary_unknown:
            return
        if not isinstance(value, int) or isinstance(value, bool):
            self._dispatch_overflow_blocked_after_ms = None
            self._dispatch_overflow_boundary_unknown = True
            return
        if self._dispatch_overflow_blocked_after_ms is None:
            self._dispatch_overflow_blocked_after_ms = value
        else:
            self._dispatch_overflow_blocked_after_ms = max(
                self._dispatch_overflow_blocked_after_ms,
                value,
            )

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
               or (sensor.state is SensorState.OFF
                   and context.now - sensor.last_changed > EnginePolicy.from_config(config).staleness_seconds * 1000)
               for sensor in relevant):
            self._absence.pop(config.room_id, None)
            return
        if any(
            sensor.state is SensorState.ON
            and context.now - sensor.last_changed > EnginePolicy.from_config(config).sensor_freshness_seconds * 1000
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
        if self._started_at is not None:
            since = max(since, self._started_at)
        self._absence[config.room_id] = (True, since)

    def _schedule_ownership_save(self) -> None:
        if self._ownership_store is None or self._hass is None:
            return
        self._create_task(self._persist_ownership())

    async def _persist_ownership(self) -> bool:
        if self._ownership_store is None:
            return False
        if not self._ownership.dirty:
            return True
        async with self._save_lock:
            while self._ownership.dirty:
                revision = self._ownership.revision
                payload = self._ownership.to_payload()
                try:
                    await self._ownership_store.async_save(payload)  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001 - retry on the next mutation
                    _LOGGER.warning("room lighting ownership save failed")
                    return False
                self._ownership.mark_clean_if_revision(revision)
            return True

    def _create_task(self, coroutine: object) -> object | None:
        if self._hass is None:
            _discard_coroutine(coroutine)
            return None
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
                    return None
                except Exception:  # noqa: BLE001 - never crash a callback
                    _LOGGER.warning("room lighting task scheduling failed")
                    _discard_coroutine(coroutine)
                    return None
        if callable(create):
            try:
                return create(coroutine)
            except Exception:  # noqa: BLE001 - never crash a callback
                _LOGGER.warning("room lighting task scheduling failed")
                _discard_coroutine(coroutine)
                return None
        try:
            return asyncio.ensure_future(coroutine)  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001 - never crash a callback
            _LOGGER.warning("room lighting task scheduling failed")
            _discard_coroutine(coroutine)
            return None


def _discard_coroutine(coroutine: object) -> None:
    close = getattr(coroutine, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # noqa: BLE001 - best effort cleanup
            pass


def _targets_select_light(targets: object, target: object) -> bool:
    target_id = getattr(target, "id", None)
    if isinstance(target_id, str) and target_id in getattr(targets, "light_targets", ()):  # type: ignore[operator]
        return True
    group_id = getattr(target, "group_id", None)
    if group_id is not None and group_id in getattr(targets, "group_ids", ()):  # type: ignore[operator]
        return True
    role = getattr(target, "role", None)
    return role is not None and role in getattr(targets, "roles", ())  # type: ignore[operator]


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


def _revision_observed_ms(revision: str) -> int | None:
    """Report timestamp encoded in a trusted evidence revision, if any."""

    _, _, observed = revision.partition("|")
    if not observed or not observed.isdigit():
        return None
    return int(observed)


def _receipt_send_state(receipt: object) -> tuple[bool, str]:
    """Honestly classify a dispatch attempt: sent, not sent or uncertain.

    ``None`` means the executor raised or returned nothing: the command may or
    may not have reached Home Assistant, so it is never reported as sent. A
    normal executor result without an explicit outcome was awaited to
    completion and counts as sent.
    """

    if receipt is None:
        return False, "uncertain"
    if isinstance(receipt, Mapping):
        outcome = receipt.get("sendOutcome")
        if outcome in {"not_sent", "sent", "uncertain"}:
            return bool(receipt.get("sent")), str(outcome)
        if "sent" in receipt:
            sent = bool(receipt.get("sent"))
            return sent, "sent" if sent else "not_sent"
        return True, "sent"
    outcome = getattr(receipt, "send_outcome", None)
    if outcome in {"not_sent", "sent", "uncertain"}:
        return bool(getattr(receipt, "sent", False)), str(outcome)
    return bool(getattr(receipt, "sent", True)), "sent"


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
