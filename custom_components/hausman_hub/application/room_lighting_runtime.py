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
import copy
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta, timezone
import logging
import math
import re
import secrets
import time
from typing import TYPE_CHECKING

from ..domain.device_power_dependencies import (
    AUTO_TURN_ON_POLICY,
    DevicePowerDependency,
    effective_device_state,
)
from ..domain.light_curve import time_mode_brightness
from ..domain.room_lighting import (
    AwayMode,
    LightRole,
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
    LEGACY_CONFIGURED_ABSENCE_SECONDS,
    DecisionReason,
    EnginePolicy,
    sensor_observation_is_fresh,
    LightAction,
    PlannedCommand,
    ProtectionSnapshot,
    RoomLightingDecision,
    RoomLightingContext,
    SensorSnapshot,
    bathroom_auxiliary_inputs,
    curve_profile_block_reason,
    curve_target_block_reason,
    curve_profile_from_config,
    curve_params_block,
    evaluate_curve_room,
    evaluate_room_lighting,
)
from ..domain.room_lighting_ownership import (
    ExplicitHandoffProof,
    OwnershipSnapshot,
    OwnershipSource,
    OwnershipViolation,
    SensorState,
    has_proven_auto_ownership,
    has_automatic_control,
    latest_ownership,
)
from .lighting_observation import (
    ObservedLightState, physical_evidence_token, physical_brightness_observed_ms,
    physical_color_observed_ms,
    paired_recovery_brightness_observed_ms,
)
from .power_evidence import (
    state_is_restored_or_cached,
    trusted_power_state,
    trusted_target_observed_ms,
    trusted_target_state,
)
from .room_lighting_ha_executor import RoomLightingHaExecutor
from .manual_light_auto_return import ManualLightAutoReturn
from .lighting_return_intent import (
    LightingReturnIntent, LightingReturnIntents, RETURN_INTENT_TTL_SECONDS,
    records_match,
)
from .room_lighting_ha_state import RoomLightingHaStateProvider, _last_changed_ms, _resolve_timezone
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
# Delivery episodes of an unconfirmed power-on (see _power_attempt_blocked):
# a confirmed presence opens ONE bounded, spaced delivery episode.
POWER_RETRY_LIMIT_PER_REVISION = 3
POWER_RETRY_INTERVAL_MS = 8_000
CURVE_MOTION_CONFIRM_WINDOW_MS = 20_000


@dataclass(frozen=True, slots=True)
class PowerDeliveryEpisode:
    """One bounded delivery episode of an unconfirmed power-on.

    Opened by a confirmed presence after the previous attempt went
    unconfirmed; counts spaced attempts over the same evidence revision.
    A fresh relay report (a new evidence revision) replaces it; an
    EXHAUSTED episode is replaced only by that fresh evidence — a
    restart or a stale-untrusted revision never refreshes the budget."""

    revision: str
    presence_since: int
    attempts: int
    last_attempt_ms: int
    exhausted: bool = False


@dataclass(frozen=True, slots=True)
class PowerAttemptPlan:
    """Read-only admission result; a retry is committed only at send completion."""

    revision: str
    retry_episode: PowerDeliveryEpisode | None = None


@dataclass(slots=True)
class PowerAttempt:
    """Transient execution state, owned by the existing source lock."""

    source: str
    revision: str
    room_id: str
    detail: dict[str, object]
    plan: PowerAttemptPlan | None = None
    pending: bool = False
    entered_at: int | None = None
    previous_grace: tuple[str, int] | None = None


@dataclass(slots=True)
class _ZeroPowerRecovery:
    """One volatile owner-authorized zero repair, not manual/auto ownership."""

    config: RoomLightingConfig
    target_id: str
    entity: str
    power: str
    session: str
    off_ms: int
    off_sequence: int
    epoch: int
    dependencies: object
    on_ms: int | None = None
    boundary: object = None



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
# Bound for the restored generation maps (RP-10): far above any real home,
# small enough that a damaged document cannot grow the journal without limit.
MAX_RESTORED_GENERATION_ENTRIES = 4096
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
# Per room: (absence confirmed, window start, window end). The end is frozen
# at the proven presence return and stays None while the room stays empty.
AbsenceState = dict[str, tuple[bool, int | None, int | None]]


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
        # Volatile activation is intentionally never restored from disk.
        self._active_handoffs: dict[tuple[str, str], tuple[OwnershipSnapshot, RoomLightingConfig, object]] = {}

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
        self.invalidate_handoffs(room_id)
        self._manual_generation += 1
        generation = self._manual_generation
        record = OwnershipSnapshot(
            target_id,
            OwnershipSource.MANUAL,
            confirmed,
            moment,
            turned_off=turned_off,
            generation=generation,
        )
        self._append(room_id, record)
        self._manual_record_generation[(room_id, target_id)] = generation
        if turned_off:
            self._manual_off[(room_id, target_id)] = moment
            self._manual_off_generation[(room_id, target_id)] = generation
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

    def invalidate_handoffs(self, room_id: str | None = None, *, target_ids: tuple[str, ...] | None = None) -> None:
        for key in tuple(self._active_handoffs):
            if (room_id is None or key[0] == room_id) and (target_ids is None or key[1] in target_ids):
                self._active_handoffs.pop(key, None)

    def stage_handoff(
        self, config: RoomLightingConfig, target_id: str, proof: ExplicitHandoffProof, at: int,
    ) -> OwnershipSnapshot:
        """Persist intent evidence, still projected UNKNOWN until commit succeeds."""
        record = OwnershipSnapshot(target_id, OwnershipSource.EXPLICIT_HANDOFF, True, at, handoff=proof)
        self._records[(config.room_id, target_id)] = [record]
        self._mark_dirty()
        return record

    def activate_handoffs(
        self, config: RoomLightingConfig, records: Sequence[OwnershipSnapshot], configuration_generation: object,
    ) -> None:
        for record in records:
            key = (config.room_id, record.target_id)
            if self._records.get(key) == [record]:
                self._active_handoffs[key] = (record, config, configuration_generation)

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
        self, room_id: str, target_ids: Iterable[str], *, config: RoomLightingConfig | None = None,
        configuration_generation: object = None,
    ) -> tuple[OwnershipSnapshot, ...]:
        wanted = set(target_ids)
        return tuple(
            (record if record.source is not OwnershipSource.EXPLICIT_HANDOFF
             or (config is not None and (active := self._active_handoffs.get((record_room, target_id))) is not None
                 and active[0] is record and active[1] is config and active[2] == configuration_generation)
             else replace(record, source=OwnershipSource.UNKNOWN, confirmed=False, handoff=None))
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

    def newest_manual_action_is_off(self, room_id: str, target_id: str) -> bool:
        """Whether the newest manual record for one target is its turn-off.

        The proof is the SPECIFIC record: the manual-off anchor names the
        moment, the off-generation names the record's place in the manual
        lineage, and the record itself must carry the turn-off direction
        and be the newest manual record for the target (by generation).
        Everything else answers False (fail-safe): a trimmed history, a
        damaged generation, an ambiguous same-moment action, a later
        manual turn-on, a non-pair power source, a legacy record without
        direction."""

        bucket = self._records.get((room_id, target_id))
        if not bucket:
            return False
        off_moment = self._manual_off.get((room_id, target_id))
        off_generation = self._manual_off_generation.get((room_id, target_id))
        if off_moment is None or not off_generation:
            return False
        manual_records = [
            record for record in bucket
            if record.source is OwnershipSource.MANUAL
        ]
        if not manual_records:
            return False
        anchor_records = [
            record for record in manual_records
            if record.at == off_moment
            and record.generation == off_generation
            and record.turned_off
        ]
        if len(anchor_records) != 1:
            # No — or ambiguous — anchor record: the document cannot prove
            # which action the off-generation names.
            return False
        anchor = anchor_records[0]
        for record in manual_records:
            if record is anchor:
                continue
            if record.at > anchor.at:
                # A later manual action exists — even one whose generation
                # was lost to damage vetoes the proof.
                return False
            if record.at == anchor.at:
                # Another action at the very same moment: only a VALID
                # strictly older generation keeps the order provable. A
                # lost (zero), equal or newer generation cannot prove it
                # was not the newest — the turn-off is not proven.
                if record.generation == 0 or record.generation >= anchor.generation:
                    return False
                continue
            if record.generation >= off_generation:
                # Another record claims the same or a newer place in the
                # manual lineage: the lineage is duplicated or the off is
                # not the newest — fail safe.
                return False
        return True

    def release_manual_protections_through(self, through_generation: int) -> int:
        """Drop manual-off anchors and pre-return manual records (away→home).

        Owner decision 2026-10-09: the away→home transition is the single
        contour point that re-arms automation for a returning household.
        Only evidence at or before the return gesture is released; a fresh
        manual action that wins the race keeps its normal priority. Manual
        evidence observed after the boundary is never touched here.
        """

        released = 0
        for key in list(self._manual_off):
            if self._manual_off_generation.get(key, 0) <= through_generation:
                self._manual_off.pop(key, None)
                self._manual_off_generation.pop(key, None)
                released += 1
        for key, bucket in list(self._records.items()):
            latest = bucket[-1] if bucket else None
            if latest is None or latest.source is not OwnershipSource.MANUAL:
                continue
            if self._manual_record_generation.get(key, 0) > through_generation:
                continue
            self._records.pop(key, None)
            self._manual_record_generation.pop(key, None)
            released += 1
        if released:
            self._mark_dirty()
        return released

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
                    "turnedOff": record.turned_off,
                    "generation": record.generation,
                    **({"handoff": asdict(record.handoff)} if record.handoff is not None else {}),
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
                    "turnedOff": record.turned_off,
                    "generation": record.generation,
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
            # Generations keep the manual-off proof distinguishable across a
            # restart (RP-10): which manual action was newest. An older
            # payload without the maps restores zeros — the safe answer is
            # then False (see newest_manual_action_is_off).
            "manualRecordGenerations": [
                {
                    "roomId": room_id,
                    "targetId": target_id,
                    "generation": generation,
                }
                for (room_id, target_id), generation in (
                    self._manual_record_generation.items()
                )
            ],
            "manualOffGenerations": [
                {
                    "roomId": room_id,
                    "targetId": target_id,
                    "generation": generation,
                }
                for (room_id, target_id), generation in (
                    self._manual_off_generation.items()
                )
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
        self._active_handoffs = {}
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
                    # No zero placeholder in the generation maps: a missing
                    # value already means "unknown" (the safe answer), and
                    # placeholder entries would bypass the restored bound.
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
        # Generations are accepted only with their own evidence (a manual
        # record / a manual-off anchor for the same room+target) and within
        # a bounded count: a damaged document must not fabricate a proof or
        # grow the journal without limit. First valid entry wins.
        counterpart_limits = (
            ("manualRecordGenerations", self._manual_record_generation),
            ("manualOffGenerations", self._manual_off_generation),
        )
        for key, target_map in counterpart_limits:
            raw_generations = payload.get(key)
            if not isinstance(raw_generations, (list, tuple)):
                continue
            accepted = 0
            for raw in raw_generations:
                if accepted >= MAX_RESTORED_GENERATION_ENTRIES:
                    break
                if not isinstance(raw, Mapping):
                    continue
                room_id = raw.get("roomId")
                target_id = raw.get("targetId")
                generation = raw.get("generation")
                if not (
                    isinstance(room_id, str)
                    and room_id
                    and isinstance(target_id, str)
                    and target_id
                    and type(generation) is int
                    and generation > 0
                ):
                    continue
                pair = (room_id, target_id)
                if target_map.get(pair, 0) > 0:
                    # First valid entry wins; the pre-existing zero is only
                    # the "restored, generation unknown" placeholder.
                    continue
                if key == "manualRecordGenerations":
                    bucket = self._records.get(pair)
                    has_manual = bool(bucket) and any(
                        record.source is OwnershipSource.MANUAL
                        for record in bucket
                    )
                else:
                    anchor_moment = self._manual_off.get(pair)
                    if anchor_moment is None:
                        continue
                    record_generation = self._manual_record_generation.get(pair, 0)
                    if generation > record_generation:
                        # A turn-off is itself a manual record: its
                        # generation can never exceed the newest manual
                        # record's generation.
                        continue
                    # The moment/ambiguity checks are superseded: the
                    # call-time validation proves the off against the
                    # specific record (moment, generation, direction) and
                    # its novelty — a forged map value alone cannot
                    # produce a positive proof.
                    has_manual = True
                if not has_manual:
                    continue
                target_map[pair] = generation
                accepted += 1
        # New manual actions must take strictly fresh generations: reuse of
        # a restored number would forge a false newest-action proof (RP-10).
        restored_generations = [
            *self._manual_record_generation.values(),
            *self._manual_off_generation.values(),
        ]
        self._manual_generation = max(restored_generations, default=0)
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
        all_manual: bool = False,
    ) -> bool:
        """Match common manual-ON evidence to room history, fail closed."""

        scope = tuple(target_ids)
        if all_manual:
            return bool(scope) and not self.has_legacy()
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
        all_manual: bool = False,
    ) -> tuple[str, ...] | None:
        """Remove only pre-gesture manual history for the exact room scope.

        A manual event observed while a storage write is outstanding has a
        later generation and wins, even if a test clock gives both events the
        same millisecond.
        """

        if not self.room_history_is_releasable(
            room_id, target_ids, authority_target_ids,
            through_generation=through_generation,
            all_manual=all_manual,
        ):
            return None
        cleared: list[str] = []
        manual_targets = set(authority_target_ids)
        for target_id in sorted(set(target_ids)):
            key = (room_id, target_id)
            changed = False
            bucket = self._records.get(key, [])
            if (all_manual and target_id not in manual_targets and key not in self._manual_off
                    and not any(record.source is OwnershipSource.MANUAL for record in bucket)):
                continue
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
            if (all_manual and key in self._manual_off
                    and self._manual_off_generation.get(key, 0) <= through_generation):
                self._manual_off.pop(key, None)
                self._manual_off_generation.pop(key, None)
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
        turned_off = raw.get("turnedOff")
        generation = raw.get("generation")
        source = OwnershipSource(str(raw.get("source") or ""))
        # An explicit delegation never survives an unobserved process gap.
        # Keep a tombstone rather than exposing an older AUTO behind it.
        if source is OwnershipSource.EXPLICIT_HANDOFF:
            source = OwnershipSource.UNKNOWN
        return OwnershipSnapshot(
            target_id=str(raw.get("targetId") or ""),
            source=source,
            confirmed=bool(raw.get("confirmed")),
            at=raw.get("at"),  # type: ignore[arg-type]
            # Older documents carry neither field: the unknown values
            # (False/0) keep the manual-off proof fail-safe.
            turned_off=turned_off is True,
            generation=generation if type(generation) is int and generation > 0 else 0,
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
        state_reader: Callable[[str], object | None] | None = None,
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
        observation_refresher: Callable[
            [frozenset[str], Callable[[], bool]], Awaitable[bool]
        ] | None = None,
        return_intent_clock: Callable[[], float] = time.monotonic,
        return_intent_id: Callable[[], str] | None = None,
        single_entity_evidence: Callable[[Iterable[str]], frozenset[str]] | None = None,
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
        self._state_reader = state_reader
        self._observation_refresher = observation_refresher
        self._morning_refresh_attempts: dict[str, tuple[date, int]] = {}
        self._morning_refresh_active: set[str] = set()
        self._restart_refresh_attempts: dict[str, tuple[RoomLightingConfig, int]] = {}
        self._adjustment_refresh_attempts: dict[str, tuple] = {}
        self._manual_absence_refresh_attempts: dict[tuple[str, str], tuple] = {}
        self._manual_absence_refresh_active: set[tuple[str, str]] = set()
        self._adjustment_refresh_active: set[str] = set()
        self._service = service
        self._shadow = shadow
        self._ownership = ownership or RoomLightingOwnershipJournal(now_ms=now_ms)
        self._ownership_store = ownership_store
        self._unobserved_since: int | None = None
        self._pre_start_auto: tuple[OwnershipSnapshot, ...] = ()
        # Restart timestamp kept separately so clearing the unobserved guard on
        # the first resumed observation does not let pre-restart absence count.
        self._started_at: int | None = None
        self._absence: AbsenceState = {}
        # The away→home boundary: absence windows never start before it.
        self._absence_floor_ms: int | None = None
        # Rooms whose completed stable absence has been proven since the last
        # manual action: the ownership release may fire on this latch even
        # when the LATEST absence window is shorter (short re-entries must
        # not erase the earlier stable-absence evidence).
        self._absence_release_latch: set[str] = set()
        # Per room: the last manual-action time. New absence windows start
        # no earlier than this floor, so a stale sensor "off" predating the
        # action cannot latch the release immediately after it.
        self._manual_absence_floor: dict[str, int] = {}
        self._curve_presence: dict[str, dict[str, int | None]] = {}
        self._legacy_curve_absence_tasks: dict[
            str, tuple[RoomLightingConfig, int, asyncio.Task]
        ] = {}
        # Pending deadlines are removed before evaluation, but ownership of
        # their task lasts through that evaluation and its executor awaits.
        self._legacy_curve_absence_owned: set[asyncio.Task] = set()
        self._legacy_absence_sleep = asyncio.sleep
        self._curve_motion: dict[str, dict[str, int | None]] = {}
        self._evening_handover: dict[str, tuple[RoomLightingConfig, str, int]] = {}
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
        self._dispatch_failures: dict[
            tuple[str, str], tuple[str, str, int | None]
        ] = {}
        # Последняя отправленная на недостоверном свидетельстве регулировка
        # (молчание светильника): дедупликация — одна и та же регулировка не
        # отправляется повторно, пока прибор не отчитается новым значением.
        self._last_untrusted_adjustment: dict[str, tuple[int | None, int | None]] = {}
        self._power_failures: dict[str, tuple[str, str]] = {}
        # Delivery episodes of an unconfirmed power-on (see
        # _power_attempt_blocked): bounded, spaced attempts over an
        # unreliable transport while a confirmed presence is active.
        self._power_delivery: dict[str, PowerDeliveryEpisode] = {}
        self._power_failure_at: dict[str, int] = {}
        self._dispatch_overflow_latch = False
        self._dispatch_overflow_blocked_after_ms: int | None = None
        self._dispatch_overflow_boundary_unknown = False
        self._topology_warned: set[tuple[str, tuple[str, ...]]] = set()
        self._state_provider = state_provider or RoomLightingHaStateProvider(
            now_ms=now_ms,
            state_reader=state_reader,
            ownership_provider=self._control_ownership_for,
            protection_provider=self._protection_for,
            unobserved_since_provider=lambda: self._unobserved_since,
            power_dependency_resolver=power_dependency_resolver,
            power_migration_manifest=self._power_migration_manifest,
        )
        self._manual_light_authority = manual_light_authority
        self._executor = executor or RoomLightingHaExecutor(
            manual_light_authority=manual_light_authority,
            state_reader=state_reader,
            confirmation_refresher=getattr(observation_refresher, "refresh_confirmation", None),
            now_ms=now_ms,
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
        self._zero_power_seen: dict[str, object] = {}
        self._zero_processed_power: dict[str, object] = {}
        self._zero_processed_target: dict[str, object] = {}
        self._zero_recoveries: dict[str, _ZeroPowerRecovery] = {}
        self._zero_recovery_tasks: dict[str, asyncio.Task] = {}
        self._zero_recovery_sleep = asyncio.sleep
        self._zero_initial_read_task: asyncio.Task | None = None
        self._unsubscribers: list[Callable[[], None]] = []
        self._lock = asyncio.Lock()
        self._room_locks: dict[str, asyncio.Lock] = {}
        self._explicit_return_active: set[str] = set()
        self._timed_return_attempts: dict[object, object] = {}
        self._away_return_boundary_ms: int | None = None
        self._return_intents = LightingReturnIntents(
            return_intent_clock, return_intent_id or (lambda: secrets.token_hex(32)),
        )
        self._return_intent_epoch = 0
        # Separate registry-backed proof that an entity is one physical
        # device (never a bare HA group); empty without a provided source.
        self._single_entity_evidence = (
            single_entity_evidence or (lambda candidates: frozenset())
        )
        self._power_source_locks: dict[str, asyncio.Lock] = {}
        self._save_lock = asyncio.Lock()
        self._running = False
        self._away = False
        self._away_off_boundaries: dict[str, tuple[RoomLightingConfig, object]] = {}
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
        self._return_intents.clear()
        self._return_intent_epoch += 1
        self._hass = hass
        self._load_trackers()
        self._away_generation += 1
        away_state = self._current_away_state(hass)
        self._away = away_state is True
        # The gap starts at start: an unobserved interval is never counted as
        # absence and cannot restore pre-restart automatic ownership.
        self._unobserved_since = self._now_ms()
        self._started_at = self._unobserved_since
        self._restart_refresh_attempts.clear()
        self._absence = {}
        self._absence_floor_ms = None
        self._absence_release_latch = set()
        self._manual_absence_floor = {}
        self._evening_handover = {}
        self._ownership.invalidate_handoffs()
        if self._ownership_store is not None:
            try:
                payload = await self._ownership_store.async_load()
            except Exception:  # noqa: BLE001 - damaged storage fails closed
                _LOGGER.warning("room lighting ownership load failed; starting empty")
                payload = None
            self._ownership.restore(payload)
        self._pre_start_auto = tuple(
            record for record in self._ownership.snapshots()
            if record.source is OwnershipSource.AUTO
        )
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
        if self._away:
            self._capture_away_off_boundaries()
        subscribe_pair = getattr(self._observation_refresher, "subscribe_pair", None)
        if callable(subscribe_pair):
            self._unsubscribers.append(subscribe_pair(self._zero_physical_event))
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
        if callable(getattr(self._observation_refresher, "refresh_zero_pair", None)):
            task = self._create_task(self._async_initial_pair_read(self._return_intent_epoch))
            if isinstance(task, asyncio.Task):
                self._zero_initial_read_task = task
                task.add_done_callback(self._zero_initial_read_done)
        return self.cancel

    def _zero_initial_read_done(self, task: asyncio.Task) -> None:
        if self._zero_initial_read_task is task:
            self._zero_initial_read_task = None
        if not task.cancelled():
            task.exception()

    async def _async_initial_pair_read(self, epoch: int) -> None:
        """One owned GET-only seed; startup ON never grants recovery rights."""
        refresher = self._observation_refresher
        scope = getattr(refresher, "zero_pair_entities", None)
        for config in tuple(self._configs.values()):
            target = config.devices.target(self._pair_main_target_id(config))
            power = config.devices.power_switch
            if (power is None or target is None or config.profile != "day_curve"
                    or curve_params_block(config) is not None
                    or scope != frozenset({power.entity_id, target.entity_id})):
                continue

            def current() -> bool:
                return (self._running and self._hass is not None
                        and self._return_intent_epoch == epoch
                        and self._configs.get(config.room_id) is config
                        and self._room_commands_enabled(config)
                        and self._observation_refresher is refresher
                        and self._entity_state(power.entity_id) == "on")

            if current():
                try:
                    async with asyncio.timeout(8):
                        result = await refresher.refresh_zero_pair(scope, current)
                    _LOGGER.info("room lighting initial pair observation %s: %s", config.room_id, result)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    _LOGGER.warning("room lighting initial pair observation failed: %s", config.room_id)
            return  # At most one exact pair, one attempt, no retry.

    def cancel(self) -> None:
        if self._zero_initial_read_task is not None:
            self._zero_initial_read_task.cancel()
        self._zero_recoveries.clear()
        self._zero_power_seen.clear()
        self._zero_processed_power.clear()
        self._zero_processed_target.clear()
        for task in tuple(self._zero_recovery_tasks.values()):
            task.cancel()
        for room_id in tuple(self._legacy_curve_absence_tasks):
            self._cancel_legacy_absence_deadline(room_id)
        for task in tuple(self._legacy_curve_absence_owned):
            task.cancel()
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
        self._return_intents.clear()
        self._return_intent_epoch += 1
        for unsubscribe in reversed(self._unsubscribers):
            try:
                unsubscribe()
            except Exception:  # noqa: BLE001 - cleanup must be best effort
                _LOGGER.error("room lighting runtime cleanup failed")
        self._unsubscribers.clear()

    def apply_config(self, config: RoomLightingConfig) -> None:
        """Fence automatic work with a configuration already saved by the API.

        Applying a saved version never evaluates a room or sends a command.
        Device changes remain the existing restart-bound path; a same-device
        update only invalidates deferred automatic work for that room.
        """
        previous = self._configs.get(config.room_id)
        if (previous is None or config.version <= previous.version
                or config == previous or previous.devices != config.devices):
            return
        self._configs[config.room_id] = config
        self._cancel_legacy_absence_deadline(config.room_id)
        tracker = self._curve_presence.get(config.room_id)
        if tracker is not None:
            tracker["deadline"] = None
        motion = self._curve_motion.get(config.room_id)
        if motion is not None:
            motion["deadline"] = None
        self._evening_handover.pop(config.room_id, None)
        self._shower_exhaust.pop(config.room_id, None)

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
        absence_tasks = tuple(self._legacy_curve_absence_owned)
        self.cancel()
        tasks = tuple(self._zero_recovery_tasks.values()) + absence_tasks
        if self._zero_initial_read_task is not None:
            tasks += (self._zero_initial_read_task,)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

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
        if context.unobserved_since is not None and any(
            record.source is OwnershipSource.EXPLICIT_HANDOFF
            and record.at >= context.unobserved_since
            and has_automatic_control(context.ownership, record.target_id)
            for record in context.ownership
        ):
            # This explicit transaction observed this whole room. It does not
            # clear another room's startup gap or fabricate an AUTO command.
            context = replace(context, unobserved_since=None)
        self._observe_absence(config, context)
        context = replace(
            context,
            away=self._away,
            protection=self._protection_for(config),
            observation_started_at=self._started_at,
        )
        context = self._apply_proven_power_pair(context, config)
        return self._with_evening_handover(context, config)

    def _control_ownership_for(self, config: RoomLightingConfig) -> tuple[OwnershipSnapshot, ...]:
        records = self._ownership.snapshots_for(
            config.room_id, config.devices.light_target_ids
            | frozenset(item.id for item in config.devices.auxiliaries),
            config=config if self._configs.get(config.room_id) is config else None,
            configuration_generation=getattr(self._service, "configuration_generation", None),
        )
        # An unrelated state event ends the startup observation gap for the
        # running engine, but cannot prove what happened to a light during that
        # gap. Keep pre-start AUTO records as history, not control authority.
        # A new confirmed AUTO or explicit handoff after startup remains valid.
        if self._pre_start_auto:
            records = tuple(
                replace(record, source=OwnershipSource.UNKNOWN, confirmed=False)
                if any(record is saved for saved in self._pre_start_auto)
                else record
                for record in records
            )
        for record in records:
            if record.source is not OwnershipSource.EXPLICIT_HANDOFF:
                continue
            proof = record.handoff
            state = self._entity_state_object(proof.entity_id)
            session = (state.observation.session if isinstance(state, ObservedLightState)
                       and state.observation is not None else None)
            if (session != proof.observer_session or state_is_restored_or_cached(state)
                    or str(getattr(state, "state", "unknown")) not in {"on", "off"}
                    or (self._unobserved_since is not None and self._unobserved_since > record.at)):
                self._ownership.invalidate_handoffs(config.room_id)
                return self._control_ownership_for(config)
        return records

    def _with_evening_handover(
        self, context: RoomLightingContext, config: RoomLightingConfig,
    ) -> RoomLightingContext:
        """Remember a completed replacement, not renewed physical evidence."""
        from ..domain.light_curve import evening_mirror_window
        from ..domain.room_lighting_engine import curve_params_block

        local = datetime.fromtimestamp(context.now / 1000, context.timezone)
        boundary = self._ownership.manual_return_boundary()
        token = self._evening_handover.get(config.room_id)
        valid = (token is not None and token[0] is config
                 and token[1] == local.date().isoformat() and token[2] == boundary)
        mirror = next((t for t in config.devices.light_targets if t.role is LightRole.MIRROR), None)
        sources = [t for t in config.devices.light_targets if t.role in (LightRole.MAIN, LightRole.ACCENT)]
        mirror_state = context.light(mirror.id) if mirror is not None else None
        if (config.profile != "day_curve" or curve_params_block(config) is not None
                or not evening_mirror_window(local.time()) or context.away
                or context.unobserved_since is not None
                or curve_profile_block_reason(config, context) is not None
                or (mirror_state is not None and mirror_state.state is SensorState.OFF)):
            self._evening_handover.pop(config.room_id, None)
            return context
        completed = (mirror is not None and mirror.auto_control and mirror_state is not None
                     and mirror_state.state is SensorState.ON
                     and has_automatic_control(context.ownership, mirror.id)
                     and bool(sources) and all(t.auto_control and context.light(t.id) is not None
                         and context.light(t.id).state is SensorState.OFF for t in sources))
        if completed:
            self._evening_handover[config.room_id] = (config, local.date().isoformat(), boundary)
        elif not valid:
            self._evening_handover.pop(config.room_id, None)
        return replace(context, evening_handover_complete=bool(completed or valid))

    def _pair_main_target_id(self, config: RoomLightingConfig) -> str | None:
        """The chandelier target of the owner's pair (RP-10).

        The owner's rule binds ONE pair — the wall key and the chandelier.
        Whether the mirror and the point lights share that circuit is an
        open wiring question, so the proven-off proof must not silently
        cover the whole room."""

        main_target = next(
            (
                item for item in config.devices.light_targets
                if item.role is LightRole.MAIN
            ),
            None,
        )
        return main_target.id if main_target is not None else None

    def _proven_power_pair_off(self, config: RoomLightingConfig) -> bool:
        """Whether the room power switch proves the circuit off (RP-10)."""

        power = config.devices.power_switch
        if power is None or power.entity_id is None:
            return False
        return self._entity_trusted_state(power.entity_id) == "off"

    def _untrusted_on_voided_by_manual_off(
        self, config: RoomLightingConfig, target_id: str
    ) -> bool:
        """Whether an untrusted reported ``on`` contradicts the newest manual
        action (RP-10): when the newest manual record for the target is a
        turn-off, a report that could not refresh (the bulb lost power)
        cannot prove a current selection. The proof is the journal's own
        manual-off evidence tied to the newest manual record generation —
        the snapshot carries no turn-off flag."""

        records = self._ownership.snapshots_for(
            config.room_id, config.devices.light_target_ids
        )
        latest = latest_ownership(records, target_id)
        if latest is None or latest.source is not OwnershipSource.MANUAL:
            # A newer automatic action supersedes the manual lineage.
            return False
        return self._ownership.newest_manual_action_is_off(
            config.room_id, target_id
        )

    def _apply_proven_power_pair(
        self, context: RoomLightingContext, config: RoomLightingConfig
    ) -> RoomLightingContext:
        """The owner's pair rule (RP-10): the wall key and the chandelier are
        one pair. When the room power switch PROVES the circuit off, the
        CHANDELIER (the main target) that cannot report (its ``on`` is
        stale, the trusted view is not a fresh ON) reads off: a bulb that
        lost power before it could report keeps a stale ``on`` report, and
        that report must not hold manual ownership (``manual on`` never
        expires) or block the curve. A fresh ON report contradicts the
        proven-off power (an independent circuit, a newer trusted report)
        and is left untouched. Other room targets keep their own evidence:
        whether the mirror and the point lights share the chandelier
        circuit is an open wiring question, so the pair proof must not
        silently cover them. The dispatch re-powers the circuit before the
        paired target, so the planned turn-on reaches a powered lamp; an
        external re-power is attributed by the existing power-path rules."""

        if not self._proven_power_pair_off(config):
            return context
        paired = self._pair_main_target_id(config)
        scoped = {paired} if paired is not None else set()
        replaced = tuple(
            replace(
                light,
                state=SensorState.OFF,
                reported_state=None,
            )
            if light.target_id in scoped and light.state is not SensorState.ON
            else light
            for light in context.lights
        )
        return replace(context, lights=replaced)

    async def async_context_for(
        self, config: RoomLightingConfig
    ) -> RoomLightingContext:
        """Build the real context for the status endpoint and the engine."""

        current = self._configs.get(config.room_id)
        # Storage reconstructs equal documents for reads. Keep the active
        # runtime identity, without granting control to different settings.
        if current is not None and current == config:
            config = current
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
        """Compatibility entry for the same explicit room return as the panel."""
        result = await self.async_force_auto(room_id, reason="switch: return_to_auto")
        return tuple(result.get("releasedTargets", ()))

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
            self._hass, command,
            **({"room_id": config.room_id} if isinstance(self._executor, RoomLightingHaExecutor) else {}),
        )
        to_payload = getattr(receipt, "to_payload", None)
        if callable(to_payload):
            return dict(to_payload())
        return {"confirmed": bool(getattr(receipt, "confirmed", False))}

    def _manual_priority_blocks(
        self, config: RoomLightingConfig, command: PlannedCommand,
        *, manual_absence_permit: Callable[[], bool] | None = None,
    ) -> bool:
        """Fail closed when the shared scenario authority marks this light manual."""
        if self._away_off_is_current(config, command):
            return False
        if manual_absence_permit is not None and manual_absence_permit():
            return False
        authority = self._manual_light_authority
        target = config.devices.target(getattr(command, "target_id", None))
        entity = getattr(target, "entity_id", None)
        if authority is not None:
            if (target in config.devices.light_targets and isinstance(entity, str)
                    and entity.startswith(("light.", "switch."))):
                # The authority includes the target's proven power component.
                # Its legacy graph remains room-wide if topology is unproved.
                if authority.blocks(entity):
                    return True
            elif any(authority.blocks(item.entity_id)
                     for item in config.devices.light_targets
                     if isinstance(item.entity_id, str)
                     and item.entity_id.startswith(("light.", "switch."))):
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

    async def _manual_absence_off_permit(
        self, config: RoomLightingConfig, command: PlannedCommand,
    ) -> tuple[Callable[[], bool], Callable[[], bool]] | None:
        """Freeze one manually-on target and all evidence for one OFF attempt.

        Returns ``(dispatch, confirm)``. ``dispatch`` is the strict pre-send
        check including the trusted ON state and the still-planned command.
        ``confirm`` keeps every manual-intent condition alive while the
        executor confirms the report, without re-requiring the ON state the
        successful OFF itself legitimately ended.
        """
        from .room_lighting_ha_executor import room_manual_off_protection

        target = config.devices.target(command.target_id)
        authority, priority = self._manual_light_authority, self._light_priority
        if (self._hass is None or config.profile == "day_curve" or target is None
                or not target.auto_off_manual_on_absence or not target.auto_control
                or not isinstance(target.entity_id, str)
                or command.action is not LightAction.TURN_OFF
                or command.reason is not DecisionReason.DIMMING
                or authority is None or priority is None
                or room_manual_off_protection(self._hass) is None):
            return None
        try:
            context = await self._build_context(config, self._now_ms())
            if command not in evaluate_room_lighting(config, context).commands:
                return None
            manual = authority.record(target.entity_id)
            if (manual is None or manual.legacy or manual.action != "on"
                    or not authority.has_durable_import({target.entity_id})
                    or authority.blocks_departure_off(target.entity_id)):
                return None
            boundary = self._away_manual_boundary(config)
            lifecycle = (self._started_at, self._unobserved_since)
            trusted, value, revision = self._target_trust(target.entity_id)
            if not trusted or value != "on":
                return None
            configured = tuple(s for s in config.devices.sensors
                               if s.kind in {SensorKind.PRESENCE, SensorKind.MOTION})
            if not configured:
                return None
            sensor_snapshot = getattr(self._state_provider, "_sensor_snapshot", None)
            if not callable(sensor_snapshot):
                return None

            def shared_conditions() -> bool:
                try:
                    now = self._now_ms()
                    if (not self._running or self._configs.get(config.room_id) is not config
                            or lifecycle != (self._started_at, self._unobserved_since)
                            or self._away_manual_boundary(config) != boundary
                            or authority.record(target.entity_id) != manual
                            or not authority.has_durable_import({target.entity_id})
                            or authority.blocks_departure_off(target.entity_id)):
                        return False
                    snapshots = tuple(sensor_snapshot(self._hass, sensor, now) for sensor in configured)
                    if snapshots != tuple(s for s in context.sensors if s.sensor_id in {item.id for item in configured}):
                        return False
                    policy = EnginePolicy.from_config(config)
                    # Equal snapshots are not enough: an unchanged report can
                    # age past the freshness window while the OFF waits for
                    # its confirmation. Stale absence evidence never confirms.
                    if any(
                        sensor.state is not SensorState.OFF
                        or not sensor_observation_is_fresh(
                            sensor, now, policy.sensor_freshness_seconds * 1000
                        )
                        for sensor in snapshots
                    ):
                        return False
                    local_time = datetime.fromtimestamp(now / 1000, context.timezone).time()
                    from ..domain.room_lighting_engine import _absence_threshold
                    if now - manual.observed_at_ms < _absence_threshold(policy, local_time) * 1000:
                        return False
                    return True
                except Exception:  # noqa: BLE001 - uncertain evidence never opens this exception
                    return False

            def current() -> bool:
                # Strict pre-dispatch check: the target must still be trusted
                # ON and the engine must still plan this exact OFF.
                if not shared_conditions():
                    return False
                try:
                    if self._target_trust(target.entity_id) != (True, "on", revision):
                        return False
                    return command in evaluate_room_lighting(
                        config, replace(context, now=self._now_ms())).commands
                except Exception:  # noqa: BLE001 - uncertain evidence never opens this exception
                    return False

            def confirm_still_allowed() -> bool:
                # Post-dispatch check for the executor's read-back loop: the
                # successful OFF legitimately turned the target OFF, so the
                # trusted ON state and the still-planned command are not
                # re-required. Every manual-intent condition stays live.
                return shared_conditions()

            return (current, confirm_still_allowed) if current() else None
        except Exception:  # noqa: BLE001 - uncertain evidence never opens this exception
            return None

    async def _complete_manual_absence_off(
        self, config: RoomLightingConfig, target: object, manual: object,
        boundary: tuple[object, ...],
    ) -> bool:
        """Retire only the completed target's ON claim under the durable room fence."""
        from .room_lighting_ha_executor import room_manual_off_protection

        authority, priority = self._manual_light_authority, self._light_priority
        entity = target.entity_id
        if self._hass is None or authority is None or priority is None or self._ownership_store is None:
            return False
        off = room_manual_off_protection(self._hass)
        if off is None or not isinstance(entity, str):
            return False
        entities = frozenset(t.entity_id for t in config.devices.light_targets)
        if not entities or any(not isinstance(item, str) for item in entities):
            return False
        selected = frozenset({entity})
        try:
            if self._away_manual_boundary(config) != boundary:
                return False
            off_snapshot = off.explicit_return_snapshot(config.room_id, selected)
            if off_snapshot:
                return False
            priority_snapshot = priority.explicit_return_snapshot(selected)
            owner_boundary = self._ownership.manual_return_boundary()
            authority_boundary = authority.generation
            trusted, physical, _revision = self._target_trust(entity)
            owner = latest_ownership(self._control_ownership_for(config), target.id)
            if (not trusted or physical != "off" or authority.record(entity) != manual
                    or owner is None or owner.source is not OwnershipSource.MANUAL
                    or not owner.confirmed or owner.turned_off):
                return False

            def current() -> bool:
                try:
                    return (self._running and self._configs.get(config.room_id) is config
                            and authority.generation == authority_boundary
                            and authority.record(entity) == manual
                            and self._ownership.manual_return_boundary() == owner_boundary
                            and self._target_trust(entity)[:2] == (True, "off")
                            and not off.explicit_return_snapshot(config.room_id, selected))
                except Exception:  # noqa: BLE001
                    return False

            def initial() -> bool:
                return (current() and self._away_manual_boundary(config) == boundary
                        and priority.explicit_return_snapshot(selected) == priority_snapshot)

            def commit_current() -> bool:
                return (current() and not priority.manual_claim_entity_ids(selected)
                        and not any(item.target_id == target.id and item.source is OwnershipSource.MANUAL
                                    for item in self._control_ownership_for(config)))

            def release_history() -> tuple[str, ...] | None:
                return self._ownership.release_room_manual_history_through(
                    config.room_id, (target.id,), (target.id,),
                    through_generation=owner_boundary, all_manual=True)

            result = await ManualLightAutoReturn(authority).async_return(
                entities, all_manual=True, config_version=config.version,
                release_entity_ids=selected, observations_current=current,
                room_history_is_releasable=initial, release_room_history=release_history,
                persist_room_history=self._persist_ownership, release_is_current=current,
                commit_is_current=commit_current,
                release_other_histories=(lambda: priority.async_release_explicit_return(
                    priority_snapshot, intent_current=current),),
            )
            return result.outcome == "returned"
        except Exception:  # noqa: BLE001 - retain the durable deny on uncertain completion
            _LOGGER.exception("Manual absence OFF return failed for %s", config.room_id)
            return False

    def _manual_dispatch_boundary(self, config: RoomLightingConfig) -> tuple[object, ...]:
        """Freeze profile-local manual evidence, including equal-time actions."""
        targets = config.devices.light_targets
        room_records = tuple(
            record for record in self._ownership.snapshots_for(
                config.room_id, (target.id for target in targets)
            ) if record.source is OwnershipSource.MANUAL
        )
        authority = self._manual_light_authority
        record_for = getattr(authority, "record", None)
        linked_for = getattr(authority, "blocking_evidence", None)
        common_records = tuple(
            (target.entity_id,
             record_for(target.entity_id) if callable(record_for) else None,
             linked_for(target.entity_id) if callable(linked_for) else None)
            for target in targets
            if isinstance(target.entity_id, str)
            and target.entity_id.startswith(("light.", "switch."))
        )
        return room_records, common_records

    def _away_manual_boundary(self, config: RoomLightingConfig) -> object:
        """Freeze deny sources without releasing any manual claim."""
        from .room_lighting_ha_executor import room_manual_off_protection

        entities = frozenset(t.entity_id for t in config.devices.light_targets
                             if isinstance(t.entity_id, str))
        shared = []
        off = room_manual_off_protection(self._hass) if self._hass is not None else None
        for source in (self._light_priority, off):
            if source is None:
                shared.append(None)
                continue
            snapshot = getattr(source, "explicit_return_snapshot", None)
            if not callable(snapshot):
                raise ValueError("away manual boundary unavailable")
            records = (snapshot(config.room_id, entities) if source is off
                       else snapshot(entities))
            shared.append(tuple(sorted((entity, id(record))
                                       for entity, record in records.items())))
        return self._manual_dispatch_boundary(config), tuple(shared)

    def _capture_away_off_boundaries(self) -> None:
        self._away_off_boundaries.clear()
        for config in self._configs.values():
            try:
                boundary = self._away_manual_boundary(config)
            except Exception:  # unavailable safety authority remains closed
                continue
            self._away_off_boundaries[config.room_id] = (config, boundary)

    def _away_off_is_current(
        self, config: RoomLightingConfig, command: PlannedCommand,
    ) -> bool:
        """Narrow departure OFF permission, invalidated by any new manual act."""
        saved = self._away_off_boundaries.get(config.room_id)
        if not (self._running and self._hass is not None and self._away
                and self._current_away_state(self._hass) is True
                and self._configs.get(config.room_id) is config
                and config.away_behavior.mode is AwayMode.ROOM_OFF
                and command.action is LightAction.TURN_OFF
                and command.reason is DecisionReason.AWAY
                and any(t.id == command.target_id for t in config.devices.light_targets)
                and saved is not None and saved[0] is config):
            return False
        try:
            authority = self._manual_light_authority
            if authority is not None:
                blocks = getattr(authority, "blocks_departure_off", None)
                if not callable(blocks) or any(
                    blocks(target.entity_id) for target in config.devices.light_targets
                    if isinstance(target.entity_id, str)
                ):
                    return False
            return saved[1] == self._away_manual_boundary(config)
        except Exception:  # failed authority reads must never open dispatch
            return False

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
                            # AUD-HOLD-03 (luna, second round 2026-10-12):
                            # the projection uses ONLY ACTIVE evidence — a
                            # lapsed own record must never mask an active
                            # related one or a barrier (blocking_evidence()
                            # applies the same expiry as blocks()).
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
                            priority_until_utc_ms=(
                                # AUD-HOLD-02 (luna 2026-10-12): a concrete
                                # ACTIVE timed record holds exactly to its
                                # recorded deadline (the clock never re-arms
                                # it here). Barrier-only or untimeable
                                # evidence (legacy/unknown) keeps the
                                # conservative sliding transfer.
                                evidence.deadline_ms
                                if evidence is not None
                                and evidence.action != "unknown"
                                and not evidence.legacy
                                else now_ms + MANUAL_PRIORITY_TRANSFER_MS
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
        self._observe_recovery_target(entity_id)
        state = self._entity_state(entity_id)
        previous = self._target_last_state.get(entity_id)
        self._target_last_state[entity_id] = state
        for room, candidate in tuple(self._zero_recoveries.items()):
            if candidate.entity == entity_id and candidate.on_ms is not None:
                if state not in {"on", "off"} or (previous == "on" and state == "off"):
                    self._cancel_zero_recovery(room, "new_target_off_or_unknown")
                elif not self._zero_native_target_allows_read(entity_id):
                    self._cancel_zero_recovery(room, "target_level_changed")
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
        self._absence_release_latch.discard(room_id)
        self._manual_absence_floor[room_id] = moment
        self._manual_absence_floor[room_id] = moment
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
        else:
            # A gap is not a fresh departure. Old OFF permission must not
            # revive on unknown -> ON without a new observed home/departure.
            self._away_generation += 1
            self._away_off_boundaries.clear()
            self._away_return_boundary_ms = None

    def _arm_away(self) -> None:
        if self._away:
            return
        self._away_generation += 1
        generation = self._away_generation
        self._create_task(self._async_away_after_debounce(generation))

    def _release_away(self) -> None:
        # A pending on-edge debounce must not fire after the source cleared.
        self._away_generation += 1
        self._away_off_boundaries.clear()
        if not self._away:
            return
        self._away = False
        self._away_return_boundary_ms = None
        # HOME resets observation windows, not manual claims. Release belongs
        # to the confirmed AWAY boundary and each room's clearOnAway policy.
        boundary_ms = self._now_ms()
        self._absence.clear()
        self._absence_release_latch.clear()
        self._manual_absence_floor.clear()
        self._absence_floor_ms = boundary_ms
        self._curve_presence.clear()
        self._curve_motion.clear()
        self._schedule_ownership_save()
        self._create_task(self.async_process())

    async def _async_release_shared_manual_sources(
        self, boundary_ms: int, authority_boundary_generation: int | None
    ) -> None:
        """Reconcile the shared manual sources with the away→home boundary.

        Only claims and records observed at or before the return gesture are
        released; each source filters by its own version of the boundary and
        keeps its deny boundary on any failure.
        """

        authority = self._manual_light_authority
        authority_release = getattr(
            authority, "async_release_manual_claims_through", None
        )
        if callable(authority_release) and authority_boundary_generation is not None:
            try:
                await authority_release(authority_boundary_generation)
            except Exception:  # noqa: BLE001 - the deny boundary stays intact
                _LOGGER.warning("room lighting away authority release failed")
        priority = self._light_priority
        priority_release = getattr(
            priority, "async_release_manual_claims_before", None
        )
        if callable(priority_release):
            try:
                await priority_release(boundary_ms)
            except Exception:  # noqa: BLE001 - the deny boundary stays intact
                _LOGGER.warning("room lighting away priority release failed")

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
        self._away_return_boundary_ms = self._now_ms()
        self._capture_away_off_boundaries()
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
            for room, candidate in tuple(self._zero_recoveries.items()):
                if entity_id == candidate.entity or (
                    entity_id == candidate.power and candidate.on_ms is not None
                ):
                    self._cancel_zero_recovery(room, "new_service_action")
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
            self._absence_release_latch.discard(room_id)
            self._manual_absence_floor[room_id] = moment
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
        self._observe_zero_power_cycle(entity_id, previous, state)
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

    def _cancel_zero_recovery(self, room_id: str, reason: str) -> None:
        if self._zero_recoveries.pop(room_id, None) is not None:
            _LOGGER.info("room lighting zero recovery cancelled: %s %s", room_id, reason)
        task = self._zero_recovery_tasks.get(room_id)
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    def _zero_level_state(self, entity_id: str) -> ObservedLightState | None:
        """Eligible OFF (saved level allowed) or an agreed exact-zero ON."""
        view = self._entity_state_object(entity_id)
        if paired_recovery_brightness_observed_ms(view, now_ms=self._now_ms()) is None:
            return None
        return view

    def _zero_native_target_allows_read(self, entity: str) -> bool:
        """Negative screen for GET preparation, never evidence for a command."""
        view = self._entity_state_object(entity)
        state = str(getattr(view, "state", "unknown")).lower()
        attrs = getattr(view, "attributes", {})
        level = attrs.get("brightness") if isinstance(attrs, Mapping) else None
        if state == "off" and level is None:
            return True
        if type(level) not in {int, float} or not math.isfinite(level):
            return False
        return (state == "off" and 0 <= level <= 255) or (state == "on" and level == 0)

    def _zero_physical_event(self, entity: str | None) -> None:
        if not self._running:
            return
        if entity is None:
            for room in tuple(self._zero_recoveries):
                self._cancel_zero_recovery(room, "observation_boundary")
            self._zero_processed_power.clear()
            self._zero_processed_target.clear()
            self._zero_power_seen.clear()
            return
        if self._power_rooms_for(entity):
            self._observe_zero_power_cycle(entity, None, self._entity_state(entity))
        if entity in self._targets_by_entity:
            self._observe_recovery_target(entity)

    def _observe_recovery_target(self, entity: str) -> None:
        if not self._zero_native_target_allows_read(entity):
            for room, candidate in tuple(self._zero_recoveries.items()):
                if candidate.entity == entity:
                    self._cancel_zero_recovery(room, "target_unknown_or_positive")
            return
        view = self._entity_state_object(entity)
        report = view.observation if isinstance(view, ObservedLightState) else None
        if report is None or trusted_target_state(view, now_ms=self._now_ms()) is None:
            return  # The native/raw rendezvous may still be incomplete.
        previous = self._zero_processed_target.get(entity)
        self._zero_processed_target[entity] = report
        for room, candidate in tuple(self._zero_recoveries.items()):
            if candidate.entity != entity or candidate.on_ms is None:
                continue
            if (previous != report and previous is not None and previous.session == report.session
                    and previous.value == "on" and report.value == "off"
                    and report.observed_at_ms >= candidate.on_ms):
                self._cancel_zero_recovery(room, "new_physical_target_off")
            else:
                level = view.brightness_observation
                if (report.value == "on" and level is not None
                        and level.session == report.session and type(level.value) is int
                        and level.value > 0
                        and candidate.on_ms <= level.observed_at_ms <= self._now_ms()
                        and self._now_ms() - level.observed_at_ms <= 300_000):
                    self._cancel_zero_recovery(room, "new_positive_physical_level")

    def _zero_manual_boundary(self, config: RoomLightingConfig) -> tuple:
        # Scope records to the room's light sources. A shared-authority record
        # of THIS relay's ON may be delivered before or after our state handler;
        # it is not a new choice of light and must not reset the frozen boundary.
        # Relay service actions are independently invalidated synchronously.
        from .room_lighting_ha_executor import room_manual_off_protection
        targets = frozenset(t.entity_id for t in config.devices.light_targets)
        priority = getattr(self._light_priority, "explicit_return_snapshot", None)
        off = room_manual_off_protection(self._hass)
        off_snapshot = getattr(off, "explicit_return_snapshot", None)
        return (self._ownership.manual_return_boundary(),
                self._manual_dispatch_boundary(config),
                copy.deepcopy(priority(targets)) if callable(priority) else None,
                copy.deepcopy(off_snapshot(config.room_id, targets)) if callable(off_snapshot) else None)

    def _observe_zero_power_cycle(self, entity: str, previous: str | None, value: str) -> None:
        """Owner opted into this narrow repair even for unattributed cycles."""
        moment = self._now_ms()
        view = self._entity_state_object(entity)
        if str(getattr(view, "state", "unknown")).lower() not in {"on", "off"}:
            for room in self._power_rooms_for(entity):
                self._cancel_zero_recovery(room, "power_unknown")
            self._zero_processed_power.pop(entity, None)
            return
        report = view.observation if isinstance(view, ObservedLightState) else None
        trusted = trusted_power_state(view, same_state_confirmation=True, now_ms=moment)
        if (trusted not in {"on", "off"} or report is None
                or self._started_at is None or report.observed_at_ms <= self._started_at):
            return  # Do not consume a report before its native half arrives.
        processed = self._zero_processed_power.get(entity)
        if processed == report:
            return
        previous = processed.value if processed is not None and processed.session == report.session else None
        self._zero_processed_power[entity] = report
        value = trusted
        own = self._command_grace.get(entity)
        commanded = (entity in self._executing_actions
                     or (own is not None and 0 <= moment - own[1] < COMMAND_GRACE_MS))
        for room in self._power_rooms_for(entity):
            candidate = self._zero_recoveries.get(room)
            if value == "off" and previous != "off":
                self._cancel_zero_recovery(room, "power_off")
                config = self._configs.get(room)
                if config is None or commanded or trusted != "off" or report is None:
                    continue
                power = config.devices.power_switch
                target = config.devices.target(self._pair_main_target_id(config))
                scope = frozenset({entity, getattr(target, "entity_id", None)})
                if (power is None or power.entity_id != entity or target is None
                        or target.entity_id is None or not target.entity_id.startswith("light.")
                        or not target.brightness or config.profile != "day_curve"
                        or curve_params_block(config) is not None
                        or not self._room_commands_enabled(config)
                        or scope != getattr(self._observation_refresher, "zero_pair_entities", None)):
                    continue
                if not self._zero_native_target_allows_read(target.entity_id):
                    continue
                self._zero_recoveries[room] = _ZeroPowerRecovery(
                    config, target.id, target.entity_id, entity, report.session,
                    moment, report.sequence, self._return_intent_epoch, self._power_dependencies())
                _LOGGER.info("room lighting power recovery armed: %s", room)
            elif value == "on" and previous == "off" and candidate is not None:
                if (commanded or trusted != "on" or report is None
                        or report.session != candidate.session
                        or report.sequence <= candidate.off_sequence
                        or not 0 <= moment - candidate.off_ms <= 300_000
                        or room in self._zero_recovery_tasks):
                    self._cancel_zero_recovery(room, "unproven_power_cycle")
                    continue
                candidate.on_ms = moment
                candidate.boundary = self._zero_manual_boundary(candidate.config)
                task = self._create_task(self._async_zero_power_recovery(candidate))
                if isinstance(task, asyncio.Task):
                    self._zero_recovery_tasks[room] = task
                    task.add_done_callback(lambda done, key=room: self._zero_task_done(key, done))
                else:
                    self._cancel_zero_recovery(room, "no_owned_task")
            elif candidate is not None and value not in {"on", "off"}:
                self._cancel_zero_recovery(room, "power_unknown")

    def _zero_task_done(self, room: str, task: asyncio.Task) -> None:
        if self._zero_recovery_tasks.get(room) is task:
            self._zero_recovery_tasks.pop(room, None)
        if not task.cancelled():
            task.exception()  # consume any exception; the worker logs it

    def _zero_recovery_current(self, candidate: _ZeroPowerRecovery) -> bool:
        config = candidate.config
        now = self._now_ms()
        decision = self._exact_power_decision(config, self._power_dependencies())
        authority = self._manual_light_authority
        barrier = getattr(authority, "barrier_reason_for", None)
        power = self._entity_state_object(candidate.power)
        report = power.observation if isinstance(power, ObservedLightState) else None
        return (self._running and self._hass is not None
                and self._zero_recoveries.get(config.room_id) is candidate
                and self._configs.get(config.room_id) is config
                and self._return_intent_epoch == candidate.epoch
                and self._room_commands_enabled(config)
                and self._current_away_state(self._hass) is False
                and candidate.on_ms is not None and 0 <= now - candidate.on_ms < 15_000
                and self._power_dependencies() == candidate.dependencies
                and self._zero_manual_boundary(config) == candidate.boundary
                and (not callable(barrier) or barrier(candidate.entity) in {None, "restart_uncertain"})
                and not self._binding_conflicts_with_reserved_writer(config, (candidate.target_id,),
                    dependencies=decision.projection if decision.active else None)
                and trusted_power_state(power, same_state_confirmation=True, now_ms=now) == "on"
                and report is not None and report.session == candidate.session
                and report.sequence > candidate.off_sequence)

    async def _async_zero_power_recovery(self, candidate: _ZeroPowerRecovery) -> None:
        config = candidate.config
        try:
            await self._zero_recovery_sleep(5)
            if not self._zero_recovery_current(candidate):
                return
            refresh = getattr(self._observation_refresher, "refresh_zero_pair", None)
            if not callable(refresh):
                return
            current = lambda: self._zero_recovery_current(candidate)
            if not await refresh(frozenset({candidate.power, candidate.entity}), current):
                return
            if not current():
                return
            context = await self._state_provider.build_context(self._hass, config, self._now_ms())
            level = time_mode_brightness(curve_profile_from_config(config),
                datetime.fromtimestamp(context.now / 1000, context.timezone).time(),
                context.sunrise, context.sunset)
            zero = self._zero_level_state(candidate.entity)
            if (not current() or zero is None
                    or zero.brightness_observation.session != candidate.session
                    or zero.observation.session != candidate.session
                    or zero.brightness_observation.observed_at_ms <= candidate.on_ms):
                return
            command = PlannedCommand(candidate.target_id, LightAction.TURN_ON,
                                     brightness=50 if level is None else level)
            # Burn the volatile attempt synchronously before the sole dispatch.
            # This owner-authorized repair preserves every manual hold and
            # deliberately creates neither manual nor automatic ownership.
            self._zero_recoveries.pop(config.room_id, None)
            receipt = await self._executor.execute(self._hass, command, automatic=False,
                **({"room_id": config.room_id} if isinstance(self._executor, RoomLightingHaExecutor) else {}))
            _LOGGER.info("room lighting zero recovery result %s: sent=%s confirmed=%s",
                         config.room_id, receipt.sent, receipt.confirmed)
        except asyncio.CancelledError:
            raise
        except Exception:  # no retry, no invented confirmation
            _LOGGER.exception("room lighting zero recovery failed: %s", config.room_id)
        finally:
            if self._zero_recoveries.get(config.room_id) is candidate:
                self._zero_recoveries.pop(config.room_id, None)
                _LOGGER.info("room lighting zero recovery cancelled without dispatch: %s", config.room_id)

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
        turned_off = service == "turn_off" or (
            service == "toggle" and self._entity_is_on(entity_id)
        )
        moment = self._now_ms()
        if not turned_off:
            # Restoring room power is a human action boundary for the absence
            # evidence (a proven latch must not release the automation on
            # evidence that predates this action), but it is NOT evidence
            # that a person selected a light source: an unattributed startup
            # call must not make every target permanently manual-only, so no
            # ownership records are created here.
            self._absence.pop(room_id, None)
            self._absence_release_latch.discard(room_id)
            self._manual_absence_floor[room_id] = moment
            return
        paired_main = self._pair_main_target_id(config)
        pair_switch = (
            config.devices.power_switch.entity_id
            if config.devices.power_switch is not None else None
        )
        # The pair's positive off-proof binds the chandelier AND the source:
        # only the configured pair switch proves the chandelier's circuit.
        # Another power source (an exact-map relay of the mirror or the
        # points) blocks every target's automatic branch, but must not
        # serve as the chandelier's manual-off evidence either — its own
        # circuit is a different one.
        main_off_proof = turned_off and entity_id == pair_switch
        graph = (getattr(self._manual_light_authority, "related_sources", None)
                 if self._independent_curve_ready(config) else None)
        scoped_targets = (tuple(target for target in config.devices.light_targets
                                if target.entity_id in graph.related(entity_id))
                          if graph is not None and entity_id in graph.components else ())
        for target in scoped_targets or config.devices.light_targets:
            self._ownership.record_manual(
                room_id,
                target.id,
                moment,
                confirmed=True,
                turned_off=(bool(scoped_targets) or
                            (main_off_proof and target.id == paired_main)),
            )
        self._absence.pop(room_id, None)
        self._absence_release_latch.discard(room_id)
        # The room power action restarts the absence evidence, exactly like
        # the light/switch manual paths: a stale sensor "off" predating the
        # action must not latch the release right after it (SEC-RP6-01).
        self._manual_absence_floor[room_id] = moment
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
        hass = self._hass
        epoch = self._return_intent_epoch
        attached_bindings = tuple(
            binding for binding in config.switch_bindings
            if binding.switch_id == switch_id and binding.trigger_subtype == subtype
        )

        async def _action(
            run_variables: object | None = None,
            context: object | None = None,
        ) -> None:
            del run_variables, context
            current = self._configs.get(config.room_id)
            if (not self._running or self._hass is not hass
                    or self._return_intent_epoch != epoch or current is None
                    or current.devices != config.devices
                    or tuple(binding for binding in current.switch_bindings
                             if binding.switch_id == switch_id
                             and binding.trigger_subtype == subtype) != attached_bindings):
                return
            await self._handle_device_trigger(
                current, device_id, switch_id, subtype
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
        hass, started_at, epoch = self._hass, self._started_at, self._return_intent_epoch
        generation = getattr(self._service, "configuration_generation", None)

        def active() -> bool:
            return (self._running and self._hass is hass
                    and self._started_at == started_at
                    and self._return_intent_epoch == epoch
                    and self._configs.get(config.room_id) is config
                    and getattr(self._service, "configuration_generation", None) == generation)

        # Device changes/deletion are restart-bound and may leave the old
        # active object in memory. Never use that object as a new press permit.
        try:
            before_read = self._away_manual_boundary(config)
            saved = await self._service.async_get_config(config.room_id)
            if not active() or saved != config or self._away_manual_boundary(config) != before_read:
                return
        except Exception:  # noqa: BLE001 - missing configuration proof closes dispatch
            return
        self._cancel_zero_recovery(config.room_id, "new_device_action")
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
        selected = []
        for binding in bindings:
            target_ids = _binding_target_ids(config, binding)
            selected.append((binding, target_ids))
            _LOGGER.info(
                "room lighting device trigger %s/%s -> %s targets=%s commands_enabled=%s",
                device_id,
                subtype,
                binding.action.value,
                ",".join(target_ids),
                enabled,
            )
            if binding.action is SwitchAction.RETURN_TO_AUTO:
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
                            duration_ms=self.manual_priority_duration_ms(
                                entity_id, "off" if _binding_turns_off(self._hass, target, binding) else "on"),
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
                self._absence_release_latch.discard(config.room_id)
                self._manual_absence_floor[config.room_id] = moment
            self._schedule_ownership_save()
        # Freeze AFTER this press's own manual records, BEFORE its first await.
        # Old holds do not forbid an explicit press; newer evidence revokes it.
        try:
            boundary = self._away_manual_boundary(config)
            dependencies = self._power_dependencies()
            if self._power_dependency_resolver is not None and dependencies is None:
                return
            dependencies = None if dependencies is None else dict(dependencies)
            exact = self._exact_power_decision(config, dependencies)

            def current() -> bool:
                latest_dependencies = self._power_dependencies()
                return (active() and self._room_commands_enabled(config)
                        and self._away_manual_boundary(config) == boundary
                        and latest_dependencies == dependencies
                        and self._exact_power_decision(config, latest_dependencies) == exact
                        and not self._binding_conflicts_with_reserved_writer(
                            config, tuple(target for _, targets in selected for target in targets),
                            dependencies=exact.projection if exact.active else None))
        except Exception:  # noqa: BLE001 - no guessed manual dispatch boundary
            return
        for binding, target_ids in selected:
            if not active():
                return
            if binding.action is SwitchAction.RETURN_TO_AUTO:
                await self.async_return_room_to_automatic(config.room_id)
                continue
            if not enabled or not current():
                continue
            await self._execute_binding(config, binding, target_ids, is_current=current)

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
        *, is_current: Callable[[], bool],
    ) -> None:
        if self._hass is None or not is_current():
            return
        # A declared keyword is not proof that an unknown executor enforces it.
        if type(self._executor) is not RoomLightingHaExecutor:
            return
        execute = self._executor.execute
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
            if not is_current():
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
            if (
                command.action is LightAction.TURN_ON
                and command.brightness is None
                and config.devices.power_switch is not None
                and config.devices.power_switch.entity_id is not None
                and target.id == self._pair_main_target_id(config)
                and target.brightness
                and target.entity_id.startswith("light.")
            ):
                # A confirmed manual ON of the configured pair must not
                # preserve a known zero level. Do not reinterpret missing,
                # malformed or positive levels, or claim automatic ownership.
                state = self._hass.states.get(target.entity_id)
                attributes = getattr(state, "attributes", None)
                brightness = (
                    attributes.get("brightness")
                    if isinstance(attributes, Mapping) else None
                )
                if (
                    getattr(state, "state", None) in {"on", "off"}
                    and type(brightness) in {int, float}
                    and brightness == 0
                ):
                    level = 100
                    if config.profile == "day_curve" and curve_params_block(config) is None:
                        hass, started_at = self._hass, self._started_at
                        intent_epoch = self._return_intent_epoch
                        try:
                            context = await self._state_provider.build_context(
                                hass, config, self._now_ms(),
                            )
                            local_time = datetime.fromtimestamp(
                                context.now / 1000, context.timezone,
                            ).time()
                            mode_level = time_mode_brightness(
                                curve_profile_from_config(config), local_time,
                                context.sunrise, context.sunset,
                            )
                            level = 50 if mode_level is None else mode_level
                        except Exception:  # noqa: BLE001 - never invent a level on failure
                            _LOGGER.exception("room lighting manual time level unavailable")
                            return
                        # The context read is an await boundary. Never dispatch
                        # an old press after stop/restart, reconfiguration,
                        # another writer or an intervening target update.
                        latest_dependencies = self._power_dependencies()
                        latest_decision = self._exact_power_decision(config, latest_dependencies)
                        if (
                            not is_current() or self._hass is not hass
                            or self._started_at != started_at
                            or self._return_intent_epoch != intent_epoch
                            or self._configs.get(config.room_id) is not config
                            or not self._room_commands_enabled(config)
                            or latest_dependencies != dependencies
                            or self._binding_conflicts_with_reserved_writer(
                                config, (target_id,),
                                dependencies=latest_decision.projection if latest_decision.active else None,
                            )
                            or hass.states.get(target.entity_id) is not state
                            or _binding_command(hass, target, binding) != command
                        ):
                            _LOGGER.info("room lighting manual time level cancelled: press became stale")
                            return
                    command = replace(command, brightness=level)
            try:
                if not is_current():
                    return
                await execute(self._hass, command, automatic=False,
                              dispatch_guard=is_current, room_id=config.room_id)
            except Exception:  # noqa: BLE001 - a failed press is not a crash
                _LOGGER.exception("room lighting binding command failed")

    # -- evaluation --------------------------------------------------------

    def issue_force_auto_intent(self, room_id: str, *, actor_id: str) -> dict[str, object] | None:
        """Freeze manual intent synchronously, before any HTTP or runtime await."""
        from .room_lighting_ha_executor import room_manual_off_protection

        config = self._configs.get(room_id)
        if config is None or not self._running or self._ownership_store is None:
            return None
        entities = frozenset(target.entity_id for target in config.devices.light_targets)
        authority, priority = self._manual_light_authority, self._light_priority
        off = room_manual_off_protection(self._hass)
        try:
            frozen = self._observation_refresh_boundary(room_id, entities)
        except Exception:
            return None
        generation = getattr(self._service, "configuration_generation", None)
        started_at, unobserved_since = self._started_at, self._unobserved_since
        epoch = self._return_intent_epoch
        dependencies = copy.deepcopy(self._power_dependencies())
        priority_records = {key: (row[1], row[2]) for key, row in frozen[2].items()}
        off_records = {key: (row[1], row[2]) for key, row in frozen[3].items()}

        def current(*, releasing: bool = False) -> bool:
            if not (
                self._running and self._configs.get(room_id) is config
                and self._return_intent_epoch == epoch
                and self._started_at == started_at and self._unobserved_since == unobserved_since
                and getattr(self._service, "configuration_generation", None) == generation
                and self._power_dependencies() == dependencies
                and self._manual_light_authority is authority and self._light_priority is priority
                and room_manual_off_protection(self._hass) is off
                and authority.generation == frozen[0]
                and self._ownership.manual_return_boundary() == frozen[1]
            ):
                return False
            try:
                return (
                    records_match(priority_records, priority.explicit_return_snapshot(entities),
                                  allow_removed=releasing)
                    and records_match(off_records, off.explicit_return_snapshot(room_id, entities),
                                      allow_removed=releasing)
                )
            except Exception:
                return False

        token = self._return_intents.issue(
            room_id, actor_id, config.version, current, lambda: current(releasing=True),
        )
        if token is None:
            return None
        return {"room": room_id, "configurationVersion": config.version,
                "intentToken": token, "expiresInSeconds": RETURN_INTENT_TTL_SECONDS}

    async def async_force_auto_guarded(
        self, room_id: str, *, actor_id: str, intent_token: object, reason: str,
    ) -> dict[str, object]:
        """Consume the exact process-local intent before the first await."""
        intent = self._return_intents.consume(intent_token, room_id, actor_id)
        if intent is None:
            return {"room": room_id, "known": room_id in self._configs, "released": 0,
                    "releasedTargets": [], "reason": reason, "at": self._now_ms(),
                    "outcome": "blocked", "blockers": ["return_intent_invalid"],
                    "evaluation": "not_requested"}
        return await self.async_force_auto(room_id, reason=reason, _intent=intent)

    async def async_force_auto(
        self, room_id: str, *, reason: str, _intent: LightingReturnIntent | None = None,
    ) -> dict[str, object]:
        """Persist one scoped return, then request the ordinary profile evaluation."""
        if room_id in self._explicit_return_active:
            return {
                "room": room_id, "known": room_id in self._configs, "released": 0,
                "releasedTargets": [], "reason": reason, "at": self._now_ms(),
                "outcome": "blocked", "blockers": ["return_busy"], "evaluation": "not_requested",
            }
        self._explicit_return_active.add(room_id)
        try:
            # Snapshot the intent before waiting for any runtime/room writer.
            # A queued request must never consume a later manual gesture.
            intent_current = (None if _intent is None
                              else lambda: _intent.current(self._return_intents.clock()))
            release_current = (None if _intent is None else lambda: _intent.current(
                self._return_intents.clock(), releasing=True))

            async def prepare(allow_stale=None):
                prepared = self._prepare_room_manual_return(
                    room_id, reason, intent_current=intent_current,
                    release_current=release_current,
                    allow_stale_replacement=allow_stale,
                )
                if (isinstance(prepared, dict)
                        and prepared.get("blockers") == ["observations_unavailable"]
                        and self._observation_refresher is not None):
                    refresh_blocker = await self._refresh_manual_return_observations(
                        room_id, intent_current=intent_current,
                    )
                    if refresh_blocker is not None:
                        prepared["blockers"] = [refresh_blocker]
                        return prepared
                    prepared = self._prepare_room_manual_return(
                        room_id, reason, intent_current=intent_current,
                        release_current=release_current,
                        allow_stale_replacement=allow_stale,
                    )
                return prepared

            prepared = await prepare()
            if (isinstance(prepared, dict)
                    and prepared.get("blockers") == ["return_scope_changed"]):
                # A durable pending fence whose own recorded profile version
                # no longer exists can never be committed.  Prove staleness
                # from the saved profiles and let this explicit owner click
                # replace it; without the proof the guard stays closed.
                # The retry belongs to THIS click: a manual gesture during
                # the profile reads below is a new action, not part of it.
                click_boundary = (self._manual_light_authority.generation,
                                  self._ownership.manual_return_boundary())
                proof_generation = getattr(self._service, "configuration_generation", None)
                staleness = await self._async_explicit_return_staleness()
                if (staleness is not None
                        and click_boundary == (self._manual_light_authority.generation,
                                               self._ownership.manual_return_boundary())):
                    # The proof is tied to the generation captured BEFORE the
                    # profile reads: a profile change after the map was built
                    # invalidates it.
                    def allow_stale(old_entity_ids, old_config_version,
                                    _map=staleness, _gen=proof_generation):
                        if getattr(self._service, "configuration_generation", None) != _gen:
                            return False
                        current = _map.get(frozenset(old_entity_ids))
                        return current is not None and current != old_config_version

                    prepared = await prepare(allow_stale)
                elif staleness is not None:
                    prepared = {
                        "room": room_id, "known": room_id in self._configs,
                        "released": 0, "releasedTargets": [], "reason": reason,
                        "at": self._now_ms(), "outcome": "blocked",
                        "blockers": ["manual_action_or_observation_changed"],
                        "evaluation": "not_requested",
                    }
            if isinstance(prepared, dict):
                return prepared
            async with self._room_lock(room_id):
                # Synchronize with configuration changes, but never hold the
                # global runtime lock across a room's durable I/O.
                async with self._lock:
                    pass
                result = await prepared()
            if result["outcome"] == "returned":
                await self.async_process([room_id])
                # An evaluation request is not a physical command confirmation.
                result["evaluation"] = "requested"
            return result
        finally:
            self._explicit_return_active.discard(room_id)

    def _observation_refresh_boundary(self, room_id: str, targets: frozenset[str]) -> tuple:
        """Retain identities and values across every observation await."""
        from .room_lighting_ha_executor import room_manual_off_protection

        priority_records = self._light_priority.explicit_return_snapshot(targets)
        off_records = room_manual_off_protection(self._hass).explicit_return_snapshot(room_id, targets)
        return (self._manual_light_authority.generation, self._ownership.manual_return_boundary(),
                {key: (id(record), record, copy.deepcopy(record))
                 for key, record in priority_records.items()},
                {key: (id(record), record, copy.deepcopy(record))
                 for key, record in off_records.items()})

    async def _refresh_manual_return_observations(
        self, room_id: str, *, intent_current: Callable[[], bool] | None = None,
    ) -> str | None:
        """Await evidence outside writer locks, retaining the original manual intent."""
        config = self._configs[room_id]
        targets = frozenset(target.entity_id for target in config.devices.light_targets)
        resolved_dependencies = self._power_dependencies()
        dependencies = None if resolved_dependencies is None else dict(resolved_dependencies)
        reads = set(targets)
        if config.devices.power_switch is not None:
            reads.add(config.devices.power_switch.entity_id)
        reads.update(dependency.power_source_entity_id
                     for entity, dependency in (dependencies or {}).items() if entity in targets)

        def boundary() -> tuple:
            return self._observation_refresh_boundary(room_id, targets)

        initial = boundary()

        def is_current() -> bool:
            return (self._running and self._configs.get(room_id) is config
                    and self._power_dependencies() == dependencies
                    and (intent_current is None or intent_current())
                    and boundary() == initial)

        try:
            refreshed = await self._observation_refresher(frozenset(reads), is_current)
        except Exception:  # Keep every hold intact; transport errors never grant ownership.
            _LOGGER.warning("Room lighting return observation refresh failed")
            return "observations_unavailable"
        if not is_current():
            return "manual_action_or_observation_changed"
        return None if refreshed else "observations_unavailable"

    def _startup_recovery_scope(self, room_id: str) -> frozenset[str] | None:
        """Scope of a room whose only blocker is a restart with unknown states.

        Returns the room's full authority scope only when every member is
        exactly ``restart_uncertain``, at least one member is unknown, and no
        manual evidence (priority claim, OFF protection record, explicit
        return in progress) exists. Anything else returns ``None``: this
        pass never guesses and never widens.
        """
        config = self._configs.get(room_id)
        if config is None or not self._running or self._observation_refresher is None:
            return None
        if room_id in self._explicit_return_active:
            return None
        from .room_lighting_ha_executor import room_manual_off_protection

        authority = self._manual_light_authority
        scope = self._room_authority_scope(config)
        if not scope:
            return None
        try:
            if any(authority.barrier_reason_for(entity) != "restart_uncertain"
                   for entity in scope):
                return None
            if not any(self._entity_trusted_state(entity) == "unknown"
                       for entity in scope):
                return None
            # A fresh manual gesture is exactly an active-priority record
            # (blocking_evidence); the restart barrier itself is expected
            # here and must not exclude the room.
            if any(authority.blocking_evidence(entity) is not None
                   for entity in scope):
                return None
            priority = self._light_priority
            manual_claims = getattr(priority, "manual_claim_entity_ids", None)
            if callable(manual_claims) and manual_claims(scope):
                return None
            off_protection = room_manual_off_protection(self._hass)
            if off_protection.explicit_return_snapshot(room_id, scope):
                return None
        except Exception:
            return None
        return scope

    async def async_startup_recovery_pass(
        self, *, max_rooms: int = 4,
    ) -> dict[str, object]:
        """One bounded read-only GET round for restart-uncertain rooms.

        Refreshes missing observations through the existing refresher so a
        responsive device's real state becomes usable evidence. Authority,
        manual protections, ownership and dispatch stay untouched: this pass
        never releases anything and never sends commands. Window limits and
        scheduling live at the Home Assistant edge.
        """
        attempted: list[str] = []
        refreshed: list[str] = []
        candidates = [room_id for room_id in sorted(self._configs)
                      if self._startup_recovery_scope(room_id) is not None]
        if not candidates:
            self._startup_recovery_cursor = 0
            return {"attempted": attempted, "refreshed": refreshed, "skipped": 0}
        cursor = getattr(self, "_startup_recovery_cursor", 0) % len(candidates)
        ordered = candidates[cursor:] + candidates[:cursor]
        for room_id in ordered[:max(1, max_rooms)]:
            if not self._running:
                break
            # Re-select immediately before each refresh: a manual gesture or
            # a configuration change in an earlier room must exclude this one.
            if self._startup_recovery_scope(room_id) is None:
                continue
            attempted.append(room_id)
            try:
                if await self._refresh_manual_return_observations(room_id) is None:
                    refreshed.append(room_id)
            except Exception:
                _LOGGER.debug(
                    "startup lighting recovery refresh failed for %s", room_id
                )
        self._startup_recovery_cursor = (
            cursor + max(1, max_rooms)) % len(candidates)
        if attempted:
            _LOGGER.info(
                "startup lighting recovery pass: attempted=%s refreshed=%s",
                len(attempted), len(refreshed),
            )
        return {"attempted": attempted, "refreshed": refreshed,
                "skipped": len(candidates) - len(attempted)}

    async def _async_explicit_return_staleness(self) -> dict[frozenset[str], int] | None:
        """Map each complete saved room profile to its entity set and version.

        The map is a proof input only: every configured room must agree with
        its durable saved profile (devices and version), and every entity set
        must be unique.  Any disagreement leaves the caller without a proof,
        and without a proof the stale-fence guard stays closed.  The reads
        must all come from one configuration generation: the generation is
        captured before the first read and re-verified after the last one,
        so a profile change during the reads discards the whole map.
        """
        proof_generation = getattr(self._service, "configuration_generation", None)
        rooms = tuple(self._configs.items())
        mapping: dict[frozenset[str], int] = {}
        for room_id, config in rooms:
            saved = await self._service.async_get_config(room_id)
            if (saved is None or saved.devices != config.devices
                    or saved.version != config.version):
                return None
            entities = frozenset(target.entity_id for target in saved.devices.light_targets)
            if not entities or entities in mapping:
                return None
            mapping[entities] = saved.version
        if getattr(self._service, "configuration_generation", None) != proof_generation:
            return None
        return mapping or None

    def manual_priority_duration_ms(self, entity_id: str, action: str) -> int:
        from ..domain.room_lighting import manual_priority_duration_ms
        graph = getattr(self._manual_light_authority, "related_sources", None)
        related = (graph.related(entity_id) if graph is not None and graph.ready
                   and entity_id in graph.components else frozenset((entity_id,)))
        durations = [manual_priority_duration_ms(config, action)
                     for config in self._configs.values()
                     if any(target.entity_id in related for target in config.devices.light_targets)]
        return max(durations, default=3600000)

    def _prepare_room_manual_return(
        self, room_id: str, reason: str,
        *, intent_current: Callable[[], bool] | None = None,
        release_current: Callable[[], bool] | None = None,
        include_presence: bool = True,
        allow_handoff: bool = True,
        allow_stale_replacement: Callable[[frozenset[str], int], bool] | None = None,
        selected_scope: frozenset[str] | None = None,
    ) -> dict[str, object] | Callable[[], Awaitable[dict[str, object]]]:
        from .manual_light_authority import ManualLightAuthority
        from .manual_light_off_protection import (
            ManualLightOffProtectionCoordinator, ManualLightOffProtectionError,
        )
        from .room_lighting_ha_executor import room_manual_off_protection
        from .scenario_light_priority import LightAutomationPriority

        config = self._configs.get(room_id)
        receipt: dict[str, object] = {
            "room": room_id, "known": config is not None, "released": 0,
            "releasedTargets": [], "reason": reason, "at": self._now_ms(),
            "outcome": "blocked", "blockers": [], "evaluation": "not_requested",
        }

        def blocked(reason_code: str) -> dict[str, object]:
            receipt["blockers"] = [reason_code]
            return receipt

        if config is None or not self._running:
            return blocked("runtime_unavailable")
        if intent_current is not None and not intent_current():
            return blocked("return_intent_changed")
        authority = self._manual_light_authority
        priority = self._light_priority
        off_protection = room_manual_off_protection(self._hass)
        if (not isinstance(authority, ManualLightAuthority)
                or not isinstance(priority, LightAutomationPriority)
                or not isinstance(off_protection, ManualLightOffProtectionCoordinator)
                or self._ownership_store is None):
            return blocked("return_participant_unavailable")
        room_scope = self._room_authority_scope(config)
        if room_scope is None and authority.barrier_reason == "return_pending":
            return blocked("return_scope_changed")
        entities = room_scope if selected_scope is None else selected_scope
        if not entities or any(not isinstance(entity, str) for entity in entities):
            return blocked("attribution_uncertain")
        if (room_scope is None or not entities <= room_scope
                or selected_scope is not None and not self._independent_curve_ready(config)
                or not authority._scope_is_complete(entities)):
            return blocked("attribution_uncertain")
        target_ids = tuple(target.id for target in config.devices.light_targets if target.entity_id in entities)
        if not target_ids:
            return blocked("attribution_uncertain")
        selective_records = (frozenset(entity for entity in entities if authority.record(entity) is not None)
                             if selected_scope is not None else None)
        if selected_scope is not None and not selective_records:
            # Recovery of an interrupted selective return needs preserved
            # authority evidence for at least one member of this group.
            return blocked("attribution_uncertain")
        if self._ownership.has_legacy():
            return blocked("attribution_uncertain")
        barrier = authority.explicit_return_blocker(
            entities, config_version=config.version, all_manual=True,
            allow_stale_replacement=allow_stale_replacement,
        )
        if barrier is not None:
            if barrier == "return_pending":
                receipt["outcome"] = "incomplete"
            return blocked(barrier)
        observations = self._explicit_return_observations(config, include_presence=include_presence, scope=entities)
        if observations is None:
            return blocked("observations_unavailable")
        try:
            off_snapshot = off_protection.explicit_return_snapshot(room_id, entities)
        except ManualLightOffProtectionError as error:
            return blocked(str(error))
        priority_snapshot = priority.explicit_return_snapshot(entities)
        configuration_generation = getattr(self._service, "configuration_generation", None)
        boundary = self._ownership.manual_return_boundary()
        authority_boundary = authority.generation
        manual_targets = tuple(target.id for target in config.devices.light_targets if target.id in target_ids
                               if authority.record(target.entity_id) is not None
                               or target.entity_id in priority_snapshot
                               or any(target.entity_id in record["lightIds"] for record in off_snapshot.values()))

        def observations_current() -> bool:
            return (getattr(self._service, "configuration_generation", None) == configuration_generation
                    and (release_current is None or release_current())
                    and self._explicit_return_observations(config, include_presence=include_presence, scope=entities) == observations)

        def intent_is_current() -> bool:
            return (
                (intent_current is None or intent_current())
                and observations_current() and authority.generation == authority_boundary
                and self._ownership.manual_return_boundary() == boundary
                and priority.explicit_return_snapshot(entities) == priority_snapshot
                and off_protection.explicit_return_snapshot(room_id, entities) == off_snapshot
            )

        def commit_is_current() -> bool:
            return (
                observations_current()
                and self._ownership.manual_return_boundary() == boundary
                and not priority.manual_claim_entity_ids(entities)
                and not off_protection.explicit_return_snapshot(room_id, entities)
            )

        def release_room_history() -> tuple[str, ...] | None:
            self._ownership.invalidate_handoffs(room_id, target_ids=target_ids)
            released = self._ownership.release_room_manual_history_through(
                room_id, target_ids, manual_targets, through_generation=boundary, all_manual=True,
            )
            if (released is not None and entities == room_scope
                    and config.morning is not None and config.morning.enabled):
                now_dt = datetime.fromtimestamp(receipt["at"] / 1000, self._home_timezone())
                self._ownership.mark_morning_transition(room_id, now_dt.date().isoformat(), receipt["at"])
            return released

        staged_handoffs: list[OwnershipSnapshot] = []

        def stage_handoffs(operation_id: int) -> None:
            if not allow_handoff:
                return
            for target in config.devices.light_targets:
                if target.id not in target_ids:
                    continue
                observation = next((item for item in observations if item[0] == target.id), None)
                if target.auto_control and observation is not None and observation[1] == target.entity_id and observation[2] == "on":
                    state = self._entity_state_object(target.entity_id)
                    session = (state.observation.session if isinstance(state, ObservedLightState)
                               and state.observation is not None else None)
                    staged_handoffs.append(self._ownership.stage_handoff(
                        config, target.id, ExplicitHandoffProof(target.entity_id, config.version,
                            operation_id, boundary, observation[3], session), receipt["at"],
                    ))

        async def execute() -> dict[str, object]:
            try:
                saved = await self._service.async_get_config(room_id)
                if saved is None or saved.version != config.version or saved.devices != config.devices:
                    return blocked("configuration_changed")
                if not intent_is_current():
                    return blocked("manual_action_or_observation_changed")
                result = await ManualLightAutoReturn(authority).async_return(
                    entities, all_manual=True, config_version=config.version,
                    observations_current=observations_current,
                    allow_stale_replacement=allow_stale_replacement,
                    commit_is_current=commit_is_current,
                    room_history_is_releasable=intent_is_current,
                    release_room_history=release_room_history,
                    persist_room_history=self._persist_ownership,
                    release_is_current=release_current,
                    release_entity_ids=selective_records,
                    stage_room_handoff=stage_handoffs,
                    release_other_histories=(
                        lambda: priority.async_release_explicit_return(
                            priority_snapshot, intent_current=release_current),
                        lambda: off_protection.async_release_explicit_return(
                            off_snapshot, intent_current=release_current),
                    ),
                )
            except Exception:
                _LOGGER.exception("room lighting explicit return did not complete for %s", room_id)
                receipt["outcome"] = "incomplete"
                return blocked("return_persistence_failed")
            receipt.update(outcome=result.outcome, operationId=result.operation_id)
            if result.outcome != "returned":
                room_barrier = next((reason for entity in sorted(entities)
                                     if (reason := authority.barrier_reason_for(entity)) is not None), None)
                return blocked(room_barrier or "manual_action_or_observation_changed")
            if priority.manual_claim_entity_ids(entities) or off_protection.explicit_return_snapshot(room_id, entities):
                receipt["outcome"] = "blocked"
                return blocked("new_manual_action")
            receipt.update(released=len(result.released), releasedTargets=list(result.released))
            self._ownership.activate_handoffs(config, staged_handoffs, configuration_generation)
            _LOGGER.info("room lighting explicit return committed for %s", room_id)
            return receipt

        return execute

    def _explicit_return_observations(self, config: RoomLightingConfig, *, include_presence: bool = True, scope: frozenset[str] | None = None) -> tuple | None:
        """Fresh complete observations, without turning ON into automatic ownership."""
        if not self._running or self._configs.get(config.room_id) is not config:
            return None
        scope = self._room_authority_scope(config) if scope is None else scope
        if not scope:
            return None
        target_ids = tuple(target.id for target in config.devices.light_targets if target.entity_id in scope)
        decision = self._exact_power_decision(config, self._power_dependencies())
        if set(decision.unproven_target_ids) & set(target_ids) or self._binding_conflicts_with_reserved_writer(
            config, target_ids,
            dependencies=decision.projection if decision.active else None,
        ):
            return None
        observed = []
        floor_provider = getattr(self._manual_light_authority, "explicit_return_observation_floor", None)
        floor = floor_provider(scope) if callable(floor_provider) else None
        for target in config.devices.light_targets:
            if target.entity_id not in scope:
                continue
            entity = target.entity_id
            dependency = (decision.projection or {}).get(entity)
            if dependency is not None and self._entity_trusted_state(dependency.power_source_entity_id) == "off":
                entity = dependency.power_source_entity_id
            elif (not decision.active and target.id == self._pair_main_target_id(config)
                    and self._proven_power_pair_off(config)):
                entity = config.devices.power_switch.entity_id
            value = self._entity_trusted_state(entity)
            if entity != target.entity_id and value == "off" and self._entity_trusted_state(target.entity_id) == "on":
                # Opposed fresh reports cannot prove a complete room handoff.
                # Keep manual protection instead of calling the lit target OFF.
                return None
            stamp = self._target_evidence_observed_ms(entity)
            if value not in {"on", "off"} or stamp is None or (floor is not None and stamp < floor):
                return None
            observed.append((target.id, entity, value, stamp))
        target_entities = {target.entity_id for target in config.devices.light_targets}
        for entity in sorted(scope - target_entities):
            value = self._entity_trusted_state(entity)
            stamp = self._target_evidence_observed_ms(entity)
            if value not in {"on", "off"} or stamp is None or (floor is not None and stamp < floor):
                return None
            observed.append(("power", entity, value, stamp))
        for sensor in config.devices.sensors:
            if not include_presence or sensor.kind not in _PRESENCE_KINDS:
                continue
            value = self._entity_trusted_state(sensor.entity_id)
            stamp = self._target_evidence_observed_ms(sensor.entity_id)
            if value not in {"on", "off"} or stamp is None or (floor is not None and stamp < floor):
                return None
            observed.append((sensor.id, sensor.entity_id, value, stamp))
        return tuple(observed)

    def _timed_manual_claims(self, config: RoomLightingConfig, scope: frozenset[str] | None = None) -> tuple | None:
        """Read each participant without treating an expired record as absent."""
        from .timed_manual_return import ManualClaim
        from .room_lighting_ha_executor import room_manual_off_protection
        if self._ownership.has_legacy():
            return None
        authority, priority = self._manual_light_authority, self._light_priority
        off = room_manual_off_protection(self._hass)
        if authority is None or priority is None or off is None:
            return None
        entities = self._room_authority_scope(config) if scope is None else scope
        if not entities:
            return None
        claims = []
        records = self._ownership.snapshots_for(config.room_id, config.devices.light_target_ids)
        target_entities = {target.entity_id for target in config.devices.light_targets}
        for entity in entities - target_entities:
            record = authority.record(entity)
            if record is not None:
                if record.legacy:
                    return None
                claims.append(ManualClaim(entity, record.action, record.observed_at_ms))
        for target in config.devices.light_targets:
            if target.entity_id not in entities:
                continue
            record = authority.record(target.entity_id)
            if record is not None:
                if record.legacy:
                    return None
                claims.append(ManualClaim(target.entity_id, record.action, record.observed_at_ms))
            own = latest_ownership(records, target.id)
            if own is not None and own.source is OwnershipSource.MANUAL:
                if not own.confirmed or own.generation <= 0:
                    return None
                if own.turned_off and not self._ownership.newest_manual_action_is_off(config.room_id, target.id):
                    return None
                claims.append(ManualClaim(target.entity_id, "off" if own.turned_off else "on", own.at))
        try:
            protections = off.explicit_return_snapshot(config.room_id, entities)
        except Exception:
            return None
        for record in protections.values():
            try:
                at = int(datetime.fromisoformat(record["startedAt"].replace("Z", "+00:00")).timestamp() * 1000)
            except (KeyError, TypeError, ValueError, AttributeError):
                return None
            claims.extend(ManualClaim(entity, "off", at) for entity in record["lightIds"])
        for entity, record in priority.explicit_return_snapshot(entities).items():
            # This older marker has no direction. Include every directed
            # journal first, so a matching OFF profile cannot become an ON.
            # A later marker still holds until its own configured deadline.
            if not isinstance(record, Mapping):
                return None
            stamp = record.get("confirmedAt")
            if type(stamp) is not int:
                return None
            if not any(claim.entity_id == entity and claim.observed_at_ms >= stamp for claim in claims):
                claims.append(ManualClaim(entity, "on", stamp))
        return tuple(claims)

    async def _async_maybe_return_manual(self, room_id: str) -> bool:
        config = self._configs.get(room_id)
        if config is None or not self._running or room_id in self._explicit_return_active:
            return False
        if not self._independent_curve_ready(config):
            return await self._async_maybe_return_manual_scope(room_id, None)
        graph = self._manual_light_authority.related_sources
        groups = {graph.related(target.entity_id) for target in config.devices.light_targets}
        returned = False
        for scope in sorted(groups, key=lambda item: tuple(sorted(item))):
            returned = await self._async_maybe_return_manual_scope(room_id, scope) or returned
        return returned

    async def _async_maybe_return_manual_scope(self, room_id: str, scope: frozenset[str] | None) -> bool:
        """Expire proved room claims through the existing durable transaction.

        This method never evaluates a profile or sends a light command. The
        outer processing pass recalculates once after the transaction commits.
        """
        from .timed_manual_return import timed_manual_return
        config = self._configs.get(room_id)
        if config is None or not self._running or room_id in self._explicit_return_active:
            return False
        claims = self._timed_manual_claims(config, scope)
        if claims is None:
            return False
        away_boundary = (self._away_return_boundary_ms if self._away
                         and self._current_away_state(self._hass) is True else None)
        permit = timed_manual_return(config, claims, self._now_ms(), away_boundary_ms=away_boundary)
        if not permit.due:
            return False
        by_away = permit.reason == "away_return"
        targets = tuple(target for target in config.devices.light_targets
                        if scope is None or target.entity_id in scope)
        def auto_on_blocked() -> bool:
            return (self._auto_on_blocked(config, self._now_ms()) if scope is None else
                    any(self._auto_on_blocked(config, self._now_ms(), target_id=target.id)
                        for target in targets))

        if (not by_away and permit.has_off and config.manual_off_protection.enabled
                and config.manual_off_protection.release_mode.value != "timer_only"
                and auto_on_blocked()):
            return False
        authority = self._manual_light_authority
        entities = self._room_authority_scope(config) if scope is None else scope
        if not entities:
            return False
        if any(authority.barrier_reason_for(entity) not in {None, "restart_uncertain"}
               for entity in entities):
            # Only a new explicit user intent may resume a partial return.
            # A clock tick never retries a durable pending transaction.
            return False
        if authority.explicit_return_blocker(entities, config_version=config.version, all_manual=True):
            return False
        lifecycle = (self._started_at, self._unobserved_since, self._away_generation)
        boundary = self._observation_refresh_boundary(room_id, entities)
        dependencies = copy.deepcopy(self._power_dependencies())
        generation = getattr(self._service, "configuration_generation", None)

        def current() -> bool:
            return (self._running and self._configs.get(room_id) is config
                    and lifecycle == (self._started_at, self._unobserved_since, self._away_generation)
                    and generation == getattr(self._service, "configuration_generation", None)
                    and dependencies == self._power_dependencies()
                    and authority.generation == boundary[0]
                    and self._ownership.manual_return_boundary() == boundary[1]
                    and (by_away or not permit.has_off
                         or not config.manual_off_protection.enabled
                         or config.manual_off_protection.release_mode.value == "timer_only"
                         or not auto_on_blocked())
                    and (not by_away or (self._away
                         and self._away_return_boundary_ms == away_boundary
                         and self._current_away_state(self._hass) is True)))

        key = (id(config), claims, lifecycle, boundary,
               tuple(self._target_evidence_observed_ms(entity) for entity in sorted(entities)))
        attempt_id = room_id if scope is None else (room_id, tuple(sorted(scope)))
        if self._timed_return_attempts.get(attempt_id) == key:
            return False
        self._timed_return_attempts[attempt_id] = key
        self._explicit_return_active.add(room_id)
        try:
            if self._explicit_return_observations(config, include_presence=False, scope=entities) is None:
                if scope is not None:
                    return False  # A different group's stale observation must not block this group.
                if self._observation_refresher is None:
                    return False
                if await self._refresh_manual_return_observations(room_id, intent_current=current) is not None:
                    return False
            if not current():
                return False
            # A timer for one light cannot adopt a different, unattributed ON.
            for target in targets:
                own = latest_ownership(self._control_ownership_for(config), target.id)
                if (self._entity_trusted_state(target.entity_id) == "on"
                        and not any(claim.entity_id == target.entity_id for claim in claims)
                        and (own is None or own.source not in {OwnershipSource.AUTO, OwnershipSource.EXPLICIT_HANDOFF})):
                    return False
            async with self._room_lock(room_id):
                async with self._lock:
                    prepared = self._prepare_room_manual_return(
                        room_id, permit.reason, intent_current=lambda: current() and self._timed_manual_claims(config, scope) == claims,
                        release_current=current, include_presence=False, allow_handoff=False,
                        selected_scope=scope,
                    )
                result = prepared if isinstance(prepared, dict) else await prepared()
            if result["outcome"] == "returned":
                self._timed_return_attempts.pop(attempt_id, None)
                if by_away and current():
                    self._away_off_boundaries[room_id] = (config, self._away_manual_boundary(config))
                _LOGGER.info("room manual priority returned by configured policy for %s", room_id)
                return True
            return False
        finally:
            self._explicit_return_active.discard(room_id)

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
        async def process_one(room_id: str) -> None:
            if not self._running:
                return
            try:
                await self._async_maybe_return_manual(room_id)
                ready = await self._refresh_scheduled_morning(room_id)
                if not ready:
                    return
                if not await self._refresh_stale_handoff_observations(room_id):
                    return
                await self._refresh_manual_absence_targets(room_id)
                await self._process_room_locked(room_id)
            except Exception:
                # A broken room must neither cancel nor detach other rooms.
                _LOGGER.exception("Room lighting processing failed for %s", room_id)
        await asyncio.gather(*(process_one(room_id) for room_id in selected))
        await self._persist_ownership()

    async def _refresh_manual_absence_targets(self, room_id: str) -> None:
        """GET a stale opted-in target only after absence is independently proven."""
        from ..domain.room_lighting_engine import _manual_on_absence_off_due

        config = self._configs.get(room_id)
        refresh = getattr(self._observation_refresher, "refresh_confirmation", None)
        authority = self._manual_light_authority
        if (config is None or config.profile == "day_curve" or self._hass is None
                or not callable(refresh) or authority is None or self._light_priority is None
                or not self._automatic_commands_enabled(config)):
            return
        context = await self._build_context(config, self._now_ms())
        policy = EnginePolicy.from_config(config)
        local_time = datetime.fromtimestamp(context.now / 1000, context.timezone).time()
        for target in config.devices.light_targets:
            entity = target.entity_id
            if not target.auto_off_manual_on_absence or not isinstance(entity, str):
                continue
            trusted, known, _ = self._target_trust(entity)
            light = context.light(target.id)
            manual = authority.record(entity)
            if (trusted or known != "on" or light is None or manual is None
                    or manual.action != "on" or manual.legacy
                    or not authority.has_durable_import({entity})
                    or authority.blocks_departure_off(entity)):
                continue
            hypothetical = replace(context, lights=tuple(
                replace(item, state=SensorState.ON) if item.target_id == target.id else item
                for item in context.lights))
            if not _manual_on_absence_off_due(config, target, hypothetical, policy, local_time):
                continue
            key = (room_id, entity)
            boundary = self._away_manual_boundary(config)
            lifecycle = (self._started_at, self._unobserved_since)
            attempt = (id(config), manual, boundary, lifecycle, context.sensors)
            if key in self._manual_absence_refresh_active or self._manual_absence_refresh_attempts.get(key) == attempt:
                continue
            self._manual_absence_refresh_attempts[key] = attempt
            self._manual_absence_refresh_active.add(key)

            def current() -> bool:
                try:
                    return (self._running and self._configs.get(room_id) is config
                            and (self._started_at, self._unobserved_since) == lifecycle
                            and self._away_manual_boundary(config) == boundary
                            and authority.record(entity) == manual
                            and not authority.blocks_departure_off(entity))
                except Exception:
                    return False

            try:
                await refresh(entity, current)
            except Exception:
                _LOGGER.warning("Manual absence target refresh failed for %s", room_id)
            finally:
                self._manual_absence_refresh_active.discard(key)

    async def _refresh_stale_handoff_observations(self, room_id: str) -> bool:
        """Renew physical evidence, not automatic authority, before recalculation.

        The original AUTO or handoff ownership proof remains intact. Failed reads
        are not retried on each tick; a genuinely fresh target report re-arms
        the preflight for a later expiry. No writer lock spans physical IO.
        """
        if room_id in self._adjustment_refresh_active:
            return False
        config = self._configs.get(room_id)
        if (config is None or (config.profile != "day_curve" and room_id != "malyi_koridor")
                or self._observation_refresher is None
                or self._manual_light_authority is None or self._light_priority is None
                or not self._automatic_commands_enabled(config)):
            return True
        targets = config.devices.light_targets
        records = self._control_ownership_for(config)
        stale = []
        for target in targets:
            record = latest_ownership(records, target.id)
            trusted, last_known, _ = self._target_trust(target.entity_id)
            if (target.role is LightRole.MAIN and target.auto_control
                    and record is not None and record.confirmed
                    and record.source in {OwnershipSource.AUTO, OwnershipSource.EXPLICIT_HANDOFF}
                    and not trusted and last_known == "on"):
                stale.append((target.id, record.at))
        if not stale:
            self._adjustment_refresh_attempts.pop(room_id, None)
            return True
        if (self._auto_on_blocked(config, self._now_ms())
                or any(self._manual_light_authority.blocks(target.entity_id) for target in targets)):
            return True
        key = (id(config), tuple(stale))
        if self._adjustment_refresh_attempts.get(room_id) == key:
            return False
        lifecycle = (self._started_at, self._unobserved_since)
        entities = frozenset(target.entity_id for target in targets)
        manual_boundary = self._observation_refresh_boundary(room_id, entities)
        dependencies = copy.deepcopy(self._power_dependencies())

        def current() -> bool:
            return (self._running and self._configs.get(room_id) is config
                    and lifecycle == (self._started_at, self._unobserved_since)
                    and self._power_dependencies() == dependencies
                    and self._observation_refresh_boundary(room_id, entities) == manual_boundary
                    and self._automatic_commands_enabled(config)
                    and not self._auto_on_blocked(config, self._now_ms())
                    and not any(self._manual_light_authority.blocks(target.entity_id) for target in targets))

        self._adjustment_refresh_attempts[room_id] = key
        self._adjustment_refresh_active.add(room_id)
        outcome = "interrupted"
        try:
            ready = await self._refresh_manual_return_observations(room_id, intent_current=current)
            if ready is not None:
                outcome = (ready if ready in {"observations_unavailable", "manual_action_or_observation_changed"}
                           else "observations_unavailable")
                return False
            if not all(self._target_trust(target.entity_id)[0]
                       for target in targets if target.id in {item[0] for item in stale}):
                outcome = "target_still_untrusted"
                return False
            # Sensor evidence is intentionally read only when the room is
            # recalculated below, after the full physical light read. The
            # original dispatch guard still requires a fresh ON and the newly
            # calculated command; OFF/unknown never becomes a permission.
            self._adjustment_refresh_attempts.pop(room_id, None)
            outcome = "fresh_reports"
            return True
        finally:
            self._adjustment_refresh_active.discard(room_id)
            # Do not log repeated ticks rejected by the existing attempt key.
            safe_room = room_id if room_id in {"tambur", "malyi_koridor", "dop_vanna"} else "other"
            _LOGGER.log(logging.INFO if outcome == "fresh_reports" else logging.WARNING,
                "Room lighting stale refresh: room=%s outcome=%s attemptRetained=%s",
                safe_room, outcome, self._adjustment_refresh_attempts.get(room_id) == key)

    async def _refresh_scheduled_morning(self, room_id: str) -> bool:
        """One bounded physical read, never a release of automatic authority."""
        if room_id in self._morning_refresh_active:
            return False
        config = self._configs.get(room_id)
        if (config is None or config.profile != "day_curve"
                or curve_params_block(config) is not None
                or self._observation_refresher is None
                or self._manual_light_authority is None or self._light_priority is None
                or not self._automatic_commands_enabled(config)):
            return True
        targets = config.devices.light_targets
        main = next((target for target in targets if target.role is LightRole.MAIN), None)
        failure = None if main is None else self._dispatch_failures.get((room_id, main.id))
        if (failure is None or failure[0] != "turn_off"
                or self._entity_trusted_state(main.entity_id) in {"on", "off"}):
            return True
        moment = self._now_ms()
        lifecycle = (self._started_at, self._unobserved_since)
        entities = frozenset(target.entity_id for target in targets)
        manual_boundary = self._observation_refresh_boundary(room_id, entities)
        resolved = self._power_dependencies()
        dependencies = None if resolved is None else dict(resolved)

        def original_intent() -> bool:
            return (self._running and self._configs.get(room_id) is config
                    and lifecycle == (self._started_at, self._unobserved_since)
                    and self._power_dependencies() == dependencies
                    and self._observation_refresh_boundary(room_id, entities) == manual_boundary)

        context = await self._build_context(config, moment)
        if not original_intent():
            return False
        profile = curve_profile_from_config(config)

        def phase_current() -> bool:
            local = datetime.fromtimestamp(self._now_ms() / 1000, context.timezone)
            return (local.date() == local_date
                    and max(profile.morning_start, context.sunrise) <= local.time()
                    < max(profile.day_start, context.sunrise))

        local_date = datetime.fromtimestamp(moment / 1000, context.timezone).date()

        def morning_ready() -> bool:
            morning = config.morning
            if morning is None or not morning.enabled or not morning.presence_gate:
                return True
            entry = self._ownership.morning_entry(room_id)
            transitioned = (entry is not None and entry.get("date") == local_date.isoformat()
                            and not entry.get("waiting"))
            return transitioned or self._morning_presence_confirmed(config)

        key = (local_date, config.version)
        if (self._morning_refresh_attempts.get(room_id) == key or not phase_current()
                or not morning_ready()
                or curve_profile_block_reason(config, context) is not None
                or self._auto_on_blocked(config, moment)):
            return True
        if any(self._manual_light_authority.blocks(target.entity_id) for target in targets):
            return True

        def sensors() -> tuple:
            return tuple((sensor.entity_id, self._entity_trusted_state(sensor.entity_id),
                          self._target_evidence_observed_ms(sensor.entity_id))
                         for sensor in config.devices.sensors)

        sensor_boundary = sensors()

        def current() -> bool:
            return (original_intent()
                    and phase_current() and morning_ready()
                    and self._automatic_commands_enabled(config)
                    and not self._auto_on_blocked(config, self._now_ms())
                    and sensors() == sensor_boundary)

        # No await between the marker and the active fence: concurrent ticks
        # cannot start another read or dispatch a partially refreshed plan.
        if room_id in self._morning_refresh_active:
            return False
        if self._morning_refresh_attempts.get(room_id) == key:
            return True
        self._morning_refresh_attempts[room_id] = key
        self._morning_refresh_active.add(room_id)
        try:
            return await self._refresh_manual_return_observations(
                room_id, intent_current=current) is None
        finally:
            self._morning_refresh_active.discard(room_id)

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
            await self._reconcile_manual_restart(config)
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
                    if (config.room_id == "malyi_koridor" and config.behaviors is not None
                            and config.behaviors.only_when_dark):
                        curve_presence = self._curve_sensor_state(
                            context, frozenset({SensorKind.MOTION})
                        )
                    else:
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
                        independent_targets=self._independent_curve_ready(config),
                        target_protections=self._curve_target_protections(config),
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
        # The third element is the attempt anchor (HH-014.b morning defect):
        # when the evidence revision carries no parseable timestamp, this
        # anchor still lets a newer proven target report release the guard.
        self._dispatch_failures[(room_id, target_id)] = (
            action,
            evidence_revision,
            at,
        )
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
        """Compatibility entry point for closing only a proven unsent intent."""
        detail: dict[str, object] = {
            "sourceEntityId": source_entity_id,
            "evidenceRevision": evidence_revision,
            "dispatchAttempt": False,
            "sent": False,
            "sendOutcome": "not_sent",
            "reason": reason,
        }
        previous = self._preserved_power_failure(source_entity_id)
        if previous is not None:
            detail["_preservedFailure"] = previous
            detail["deliveryEpisodeOpen"] = True
        return await self._persist_power_result(
            at=at, room_id=room_id, detail=detail,
            operation_id=operation_id, config_version=config_version,
        )

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
                tracker["legacy_absence_since"] = None
                self._cancel_legacy_absence_deadline(config.room_id)
                return False, 0
            if aggregate is not SensorState.OFF:
                present = SensorState.UNKNOWN
        if present is SensorState.ON:
            tracker["legacy_absence_since"] = None
            self._cancel_legacy_absence_deadline(config.room_id)
            # The confirmation floor follows the sensor's own evidence: a
            # fresh ON reading has held ON continuously since its
            # last_changed, and an advancing last_changed proves a NEW ON
            # episode began (a state transition happened in between), so the
            # proven duration restarts from it. Only fresh ON readings count;
            # a stale ON is classified unreliable upstream and never reaches
            # this branch as ON. The most recent ON start among the
            # participants is the conservative (shortest proven) continuity,
            # and the floor never moves backwards.
            on_starts = [
                sensor.last_changed
                for sensor in context.sensors
                if sensor.kind in _PRESENCE_KINDS
                and sensor.state is SensorState.ON
                and context.now - sensor.last_changed
                <= CURVE_SENSOR_FRESHNESS_MS
            ]
            episode = max(on_starts) if on_starts else moment
            previous_since = tracker["since"]
            tracker["since"] = (
                episode
                if previous_since is None
                else max(int(previous_since), episode)
            )
            if previous_since is None:
                tracker["absence"] = (
                    0
                    if tracker["last_seen"] is None
                    else max(0, (moment - int(tracker["last_seen"])) // 1000)
                )
            tracker["last_seen"] = moment
            confirm_ms = self._presence_confirm_ms(config)
            deadline = int(tracker["since"]) + confirm_ms
            confirmed = moment >= deadline
            if (config.curve is not None and config.curve.presence_confirm_seconds is not None
                    and not confirmed and tracker.get("deadline") != deadline):
                tracker["deadline"] = deadline
                self._create_task(self._async_process_presence_deadline(config.room_id, deadline))
            return confirmed, int(tracker.get("absence") or 0)
        tracker["since"] = None
        tracker["deadline"] = None
        if present is not SensorState.OFF:
            # An unknown, unavailable or stale presence is not absence. The
            # unobserved interval is not counted at all: after the fault the
            # proven-absence window starts fresh instead of inheriting the gap.
            tracker["last_seen"] = None
            tracker["absence"] = 0
            tracker["legacy_absence_since"] = None
            self._cancel_legacy_absence_deadline(config.room_id)
            return False, 0
        if (config.profile == "day_curve" and curve_params_block(config) is None
                and config.dimming.enabled and config.dimming.on_absence):
            # The period since the preceding ON evaluation is not proven
            # absence. Start at the first observed OFF, including after a
            # configuration change; entry/manual floors may only move it on.
            if (tracker.get("legacy_absence_since") is None
                    or tracker.get("legacy_absence_version") != config.version):
                tracker["last_seen"] = moment
            tracker["legacy_absence_since"] = tracker["last_seen"]
            tracker["legacy_absence_version"] = config.version
        if tracker["last_seen"] is None:
            # No proven presence yet after start: do not invent an absence.
            tracker["last_seen"] = moment
            self._arm_legacy_absence_deadline(config, tracker)
            return False, 0
        absence = max(0, (moment - int(tracker["last_seen"])) // 1000)
        tracker["absence"] = absence
        self._arm_legacy_absence_deadline(config, tracker)
        return False, absence

    def _cancel_legacy_absence_deadline(self, room_id: str) -> None:
        pending = getattr(self, "_legacy_curve_absence_tasks", {}).pop(room_id, None)
        if pending is not None:
            pending[2].cancel()

    def _arm_legacy_absence_deadline(
        self, config: RoomLightingConfig, tracker: dict[str, int | None],
    ) -> None:
        """One wake-up per room, never a physical command or a retry loop."""
        if (not getattr(self, "_running", False) or not config.commands_enabled
                or config.profile != "day_curve" or curve_params_block(config) is not None
                or not config.dimming.enabled or not config.dimming.on_absence
                or tracker.get("last_seen") is None):
            self._cancel_legacy_absence_deadline(config.room_id)
            return
        deadline = int(tracker["last_seen"]) + LEGACY_CONFIGURED_ABSENCE_SECONDS * 1000
        if deadline <= self._now_ms():
            self._cancel_legacy_absence_deadline(config.room_id)
            return
        pending = self._legacy_curve_absence_tasks.get(config.room_id)
        if (pending is not None and pending[0] is config and pending[1] == deadline
                and not pending[2].done()):
            return
        self._cancel_legacy_absence_deadline(config.room_id)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        lifecycle = (getattr(self, "_started_at", None), getattr(self, "_unobserved_since", None))
        task = loop.create_task(self._async_process_legacy_absence_deadline(config, deadline, lifecycle))
        self._legacy_curve_absence_owned.add(task)
        task.add_done_callback(self._legacy_curve_absence_owned.discard)
        self._legacy_curve_absence_tasks[config.room_id] = (config, deadline, task)

    async def _async_process_legacy_absence_deadline(
        self, config: RoomLightingConfig, deadline: int, lifecycle: tuple,
    ) -> None:
        room_id = config.room_id
        task = asyncio.current_task()
        try:
            await self._legacy_absence_sleep(max(0, deadline - self._now_ms()) / 1000)
            pending = self._legacy_curve_absence_tasks.get(room_id)
            tracker = self._curve_presence.get(room_id)
            if (not self._running or self._configs.get(room_id) is not config
                    or pending is None or pending[2] is not task or pending[1] != deadline
                    or lifecycle != (getattr(self, "_started_at", None), getattr(self, "_unobserved_since", None))
                    or tracker is None or tracker.get("last_seen") is None
                    or int(tracker["last_seen"]) + LEGACY_CONFIGURED_ABSENCE_SECONDS * 1000 != deadline):
                return
            self._legacy_curve_absence_tasks.pop(room_id, None)
            # The ordinary path re-reads sensors, ownership and manual guards.
            await self.async_process((room_id,))
        finally:
            pending = self._legacy_curve_absence_tasks.get(room_id)
            if pending is not None and pending[2] is task:
                self._legacy_curve_absence_tasks.pop(room_id, None)

    @staticmethod
    def _presence_confirm_ms(config: RoomLightingConfig) -> int:
        value = config.curve.presence_confirm_seconds if config.curve else None
        return value * 1000 if value is not None else CURVE_PRESENCE_CONFIRM_MS

    async def _async_process_presence_deadline(self, room_id: str, deadline: int) -> None:
        """Wake the normal guarded evaluation, never dispatch directly."""
        await asyncio.sleep(max(0, deadline - self._now_ms()) / 1000)
        if not self._running:
            return
        tracker = self._curve_presence.get(room_id)
        if tracker is None or tracker.get("deadline") != deadline or tracker.get("since") is None:
            return
        # The normal evaluation re-reads sensor freshness and manual authority.
        await self.async_process((room_id,))

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
                if sensor_observation_is_fresh(sensor, context.now, CURVE_SENSOR_FRESHNESS_MS):
                    fresh_on = True
                else:
                    has_unknown = True
                continue
            if sensor.state is SensorState.OFF:
                if not sensor_observation_is_fresh(sensor, context.now, EnginePolicy().staleness_seconds * 1000):
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
            allow_activation=(config.quiet_hours is None or not config.quiet_hours.contains(
                datetime.fromtimestamp(moment / 1000, context.timezone).time())),
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
        if self._quiet_hours_blocks_command(config, command):
            _LOGGER.info("shower exhaust dispatch refused: quiet_hours")
            return
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
        permission = self._dispatch_permission_token(config)
        if not self._dispatch_permission_valid(permission):
            return
        self._executing_actions[entity_id] = _planned_service_name(command)
        try:
            guard = ({"dispatch_guard": lambda: self._running
                      and self._dispatch_permission_valid(permission), "room_id": config.room_id}
                     if isinstance(self._executor, RoomLightingHaExecutor) else {})
            receipt = await self._executor.execute(  # type: ignore[attr-defined]
                self._hass, command, **guard
            )
        except Exception:  # noqa: BLE001 - a failed command is not a crash
            _LOGGER.warning("shower exhaust command failed")
            receipt = None
        finally:
            self._executing_actions.pop(entity_id, None)
        if (_receipt_confirmed(receipt)
                and self._dispatch_permission_valid(permission)):
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
        # Planning can yield before reaching dispatch. Refresh the authority
        # graph against the current map again before taking the command's
        # dependency snapshot; the final executor guard checks it once more.
        self._refresh_exact_power_index()
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
            if self._quiet_hours_blocks_command(config, command):
                receipt["reason"] = "quiet_hours"
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
                    or (self._curve_command_block_reason(config, current, command) is not None
                        and not self._away_off_is_current(config, command))
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
                        independent_targets=self._independent_curve_ready(config),
                        target_protections=self._curve_target_protections(config),
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
                and self._auto_on_blocked(config, moment, target_id=command.target_id)
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
            # RP-10 pair: capture the proven-off power evidence BEFORE the
            # power preparation — after the relay confirms on, the stale
            # target report must not turn into "already_on".
            pair_power_off = (
                not exact.active
                and command.action is LightAction.TURN_ON
                and self._proven_power_pair_off(config)
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
                    command_action=command.action.value,
                    permission=permission,
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
                if self._auto_on_blocked(config, self._now_ms(), target_id=command.target_id):
                    receipt["reason"] = "manual_off_protection"
                    receipts.append(receipt)
                    continue
                if config.profile == "day_curve":
                    current = await self._build_context(config, self._now_ms())
                    if self._curve_command_block_reason(config, current, command) is not None:
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
                    command_action=command.action.value,
                    permission=permission,
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
                if self._auto_on_blocked(config, self._now_ms(), target_id=command.target_id):
                    receipt["reason"] = "manual_off_protection"
                    receipts.append(receipt)
                    continue
                if config.profile == "day_curve":
                    current = await self._build_context(config, self._now_ms())
                    if self._curve_command_block_reason(config, current, command) is not None:
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
                        pair_power_off=pair_power_off,
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
            absence_checks = await self._manual_absence_off_permit(config, command)
            manual_absence_permit = None if absence_checks is None else absence_checks[0]
            manual_absence_confirm = None if absence_checks is None else absence_checks[1]
            if command.action is LightAction.TURN_OFF:
                turn_off_block = self._turn_off_dispatch_block_reason(
                    config,
                    command,
                    entity_id,
                    revision_before,
                    manual_absence_permit=manual_absence_permit,
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
                            "anchorMs": (
                                previous_failure[2]
                                if len(previous_failure) > 2
                                and isinstance(previous_failure[2], int)
                                and not isinstance(previous_failure[2], bool)
                                and previous_failure[2] > 0
                                else None
                            ),
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
                    pair_power_off=pair_power_off,
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
                # HH-014.b: the attempt boundary is fixed HERE — immediately
                # before the pre-send marker — not at the room processing
                # start, so a report between the two can never release the
                # restored latch without a report newer than the attempt.
                attempt_boundary = self._now_ms()
                pending_saved = await self._persist_pending_target_dispatch(
                    at=attempt_boundary,
                    room_id=config.room_id,
                    target_id=command.target_id,
                    action=command.action.value,
                    evidence_revision=revision_before,
                    operation_id=operation_id,
                    config_version=config_version,
                )
                receipt["_attemptAt"] = attempt_boundary
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
                            "anchorMs": (
                                previous_failure[2]
                                if len(previous_failure) > 2
                                and isinstance(previous_failure[2], int)
                                and not isinstance(previous_failure[2], bool)
                                and previous_failure[2] > 0
                                else None
                            ),
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
                        # A confirmed OFF of the paired supply was captured
                        # before we restored power. The bulb may still carry
                        # its old, untrusted ON report while it boots. Preserve
                        # that one-target proof through the planning recheck;
                        # newer target evidence or a trusted ON always wins.
                        if (pair_power_off and entity_id is not None
                                and command.action is LightAction.TURN_ON
                                and command.target_id == self._pair_main_target_id(profile_config)
                                and self._target_evidence_revision(entity_id) == revision_before):
                            trusted, last_known, _ = self._target_trust(entity_id)
                            if not trusted and last_known == "on":
                                current = replace(current, lights=tuple(
                                    replace(light, state=SensorState.OFF, reported_state=None)
                                    if light.target_id == command.target_id
                                    and light.state is not SensorState.ON else light
                                    for light in current.lights
                                ))
                        post_pending_profile_block = (
                            self._post_pending_curve_profile_block_reason(
                                profile_config,
                                command,
                                current,
                            )
                        )
                elif (
                    command.action is LightAction.TURN_OFF
                    and command.reason is DecisionReason.DIMMING
                ):
                    # A sensor event can update native state while its room
                    # task waits for our lock during the durable pending save.
                    # Re-plan only this absence OFF; never dispatch new commands
                    # from this read or expand another profile's permissions.
                    current_config = self._configs.get(config.room_id)
                    if current_config is None:
                        post_pending_profile_block = "permission_stale"
                    elif current_config.profile != config.profile:
                        post_pending_profile_block = "profile_blocked"
                    else:
                        try:
                            current = await self._build_context(
                                current_config, self._now_ms()
                            )
                            refreshed = evaluate_room_lighting(current_config, current)
                            if command not in refreshed.commands:
                                post_pending_profile_block = "superseded"
                        except Exception:
                            post_pending_profile_block = "profile_blocked"
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
                    pair_power_off=pair_power_off,
                    manual_absence_permit=manual_absence_permit,
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
                            "anchorMs": (
                                previous_failure[2]
                                if len(previous_failure) > 2
                                and isinstance(previous_failure[2], int)
                                and not isinstance(previous_failure[2], bool)
                                and previous_failure[2] > 0
                                else None
                            ),
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
            if self._manual_priority_blocks(config, command, manual_absence_permit=manual_absence_permit):
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
            manual_boundary = self._manual_dispatch_boundary(config)
            completion_boundary = (self._away_manual_boundary(config)
                                   if manual_absence_permit is not None and manual_absence_permit() else None)
            completion_manual = (self._manual_light_authority.record(entity_id)
                                 if completion_boundary is not None and entity_id is not None else None)
            try:
                if command.reason is DecisionReason.AWAY:
                    execute_away = getattr(self._executor, "execute_away_off", None)
                    if callable(execute_away):
                        result = await execute_away(
                            self._hass, command,
                            is_current=lambda: self._away_off_is_current(config, command),
                            **({"room_id": config.room_id} if isinstance(self._executor, RoomLightingHaExecutor) else {}),
                        )
                    else:
                        result = self._blocked_command_receipt("away_executor_unavailable")
                else:
                    guard = ({"dispatch_guard": lambda: self._running and (
                        permission is None or self._dispatch_permission_valid(permission)
                    ) and self._power_dependencies() == dependencies
                        and (
                            # The confirm-side check survives the OFF's own
                            # state flip and is final for this attempt: stale
                            # absence evidence blocks the confirmation even
                            # when generic manual priority has expired. The
                            # ON-only permit stays at the service boundary
                            # and in the pre-send gates.
                            manual_absence_confirm()
                            if manual_absence_confirm is not None
                            else not self._manual_priority_blocks(
                                config, command,
                                manual_absence_permit=manual_absence_permit)),
                        "room_id": config.room_id} if isinstance(self._executor, RoomLightingHaExecutor) else {})
                    if guard and manual_absence_permit is not None:
                        # The exception is scoped to this OFF and rechecked at
                        # the service boundary. Read-back may legitimately turn
                        # the target OFF, so do not reuse that ON-only guard there.
                        guard.update(automatic=False, pre_dispatch_guard=manual_absence_permit,
                                     confirmation_read=True)
                    result = await self._executor.execute(  # type: ignore[attr-defined]
                        self._hass, command, **guard
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
                self._dispatch_failures.pop(failure_key, None)
                if (
                    (permission is not None and not self._dispatch_permission_valid(permission))
                    or
                    self._manual_dispatch_boundary(config) != manual_boundary
                    or (completion_boundary is None and self._manual_priority_blocks(config, command))
                    or (completion_boundary is not None and self._away_manual_boundary(config) != completion_boundary)
                ):
                    # Delivery is proved, but a newer manual choice owns the
                    # light. A late own report cannot grant automatic rights.
                    receipt["reason"] = (
                        "dispatch_permission_stale"
                        if permission is not None and not self._dispatch_permission_valid(permission)
                        else "manual_superseded"
                    )
                    receipt["ownershipChanged"] = "none"
                elif manual_absence_permit is not None:
                    # The OFF does not turn a person's earlier ON into AUTO ownership.
                    receipt["reason"] = "confirmed"
                    receipt["ownershipChanged"] = "none"
                    if (completion_boundary is not None and completion_manual is not None
                            and target is not None):
                        completed = await self._complete_manual_absence_off(
                            config, target, completion_manual, completion_boundary)
                        receipt["manualReturn"] = "returned" if completed else "held"
                else:
                    receipt["reason"] = "confirmed"
                    receipt["ownershipChanged"] = "auto"
                    current_owner = latest_ownership(self._control_ownership_for(config), command.target_id)
                    # A level/colour receipt does not turn an owner delegation
                    # into a fabricated AUTO ON. Preserve its narrower lineage.
                    if not (current_owner is not None and current_owner.source is OwnershipSource.EXPLICIT_HANDOFF
                            and command.action in (LightAction.SET_BRIGHTNESS, LightAction.SET_COLOR_TEMPERATURE)):
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
                    # One attempt boundary for memory and the durable store:
                    # keep the pre-send marker's anchor when it exists, so a
                    # report between the processing start and the dispatch
                    # cannot release the restored latch without a report
                    # newer than the attempt itself.
                    existing = self._dispatch_failures.get(failure_key)
                    anchor = (
                        existing[2]
                        if isinstance(existing, tuple)
                        and len(existing) > 2
                        and isinstance(existing[2], int)
                        and not isinstance(existing[2], bool)
                        else self._now_ms()
                    )
                    self._dispatch_failures[failure_key] = (
                        command.action.value,
                        revision_before,
                        anchor,
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
                            "anchorMs": (
                                previous_failure[2]
                                if len(previous_failure) > 2
                                and isinstance(previous_failure[2], int)
                                and not isinstance(previous_failure[2], bool)
                                and previous_failure[2] > 0
                                else None
                            ),
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
        *, manual_absence_permit: Callable[[], bool] | None = None,
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
        if command.reason is DecisionReason.AWAY:
            return None if self._away_off_is_current(config, command) else "away_intent_changed"
        if manual_absence_permit is not None and manual_absence_permit():
            return None
        records = self._control_ownership_for(config)
        latest = latest_ownership(records, command.target_id)
        if latest is None:
            return "auto_ownership_missing"
        if latest.source is OwnershipSource.MANUAL:
            return "manual_intervention_after_decision"
        if latest.source not in (OwnershipSource.AUTO, OwnershipSource.EXPLICIT_HANDOFF):
            return "auto_ownership_missing"
        if latest.source is OwnershipSource.EXPLICIT_HANDOFF and not trusted:
            return "handoff_evidence_untrusted"
        if not latest.confirmed or not has_automatic_control(
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
        pair_power_off: bool = False,
    ) -> str | None:
        """Synchronous target-state proof before automatic turn_on dispatch.

        ``pair_power_off`` is captured BEFORE the power preparation (RP-10):
        the pair proof stays valid while the target's own evidence is
        unchanged, even after the dispatch re-powers the circuit. The pair
        binds the chandelier only (the owner's rule): other targets keep
        their own evidence."""

        if pair_power_off and command.target_id != self._pair_main_target_id(config):
            pair_power_off = False
        if entity_id is None:
            return "target_unavailable"
        if self._auto_on_blocked(config, self._now_ms(), target_id=command.target_id):
            return "manual_off_protection"
        authority = self._manual_light_authority
        graph = authority.related_sources if (authority is not None and
                self._independent_curve_ready(config)) else None
        scope = authority.manual_event_scope(entity_id) if graph is not None else None
        manual_peer = self._manual_peer_turn_on_block_reason(
            config,
            command,
            dependencies=dependencies,
            exact_active=exact_active,
            related_sources=graph,
            related_scope=scope,
        )
        if manual_peer is not None:
            return manual_peer
        current_revision = self._target_evidence_revision(entity_id)
        trusted, last_known, _revision = self._target_trust(entity_id)
        # Матрица turn_on: доверенное OFF/ON — штатные проверки; молчаливо-
        # выключенное — команда аддитивная, разрешена (подтверждение свежим
        # чтением обязательно); молчаливо-включённое — «уже включено»;
        # неизвестное/недоступное — вслепую не командуем. Пара «выключатель ↔
        # люстра» (RP-10): доказанно снятое питание (захвачено ДО
        # восстановления) обнуляет устаревшее «on» обесточенной лампы —
        # команда аддитивная, как для молчаливо-выключенной, пока по цели
        # не появилось новое достоверное свидетельство.
        if not trusted:
            effective_last_known = last_known
            if effective_last_known == "on" and (
                pair_power_off
                or self._untrusted_on_voided_by_manual_off(config, command.target_id)
            ):
                effective_last_known = "off"
            if effective_last_known == "on":
                return "already_on"
            if effective_last_known != "off":
                return "target_state_untrusted"
            # Обнулённое «on» (пара или новейшее ручное выключение) читается
            # как «off» ДО КОНЦА проверки: команда аддитивная, как для
            # молчаливо-выключенной, с обязательным свежим подтверждением.
            current_state = "off"
        else:
            current_state = (
                self._effective_entity_state(entity_id, dependencies)
                if exact_active and dependencies is not None
                else self._entity_trusted_state(entity_id)
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
        related_sources: object | None = None,
        related_scope: tuple[tuple[str, int, int], ...] | None = None,
    ) -> str | None:
        """Block a TURN_ON when a person just chose an interchangeable peer."""

        target = config.devices.target(command.target_id)
        if target is None:
            return None
        authority = getattr(self, "_manual_light_authority", None)
        matches = getattr(authority, "scope_matches_graph", None)
        if (related_sources is not None and
                (not callable(matches) or not matches(target.entity_id, related_sources, related_scope))):
            return "related_scope_uncertain"
        records = self._ownership.snapshots_for(
            config.room_id, config.devices.light_target_ids
        )
        for peer in config.devices.light_targets:
            if peer.id == target.id or not self._targets_interchangeable(
                config, target, peer, related_sources=related_sources
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
        *,
        related_sources: object | None = None,
    ) -> bool:
        if related_sources is not None:
            from .related_light_sources import RelatedLightSources
            if not isinstance(related_sources, RelatedLightSources) or not related_sources.ready:
                return True
            target_entity = getattr(target, "entity_id", None)
            peer_entity = getattr(peer, "entity_id", None)
            components = related_sources.components
            if (not isinstance(target_entity, str) or not isinstance(peer_entity, str)
                    or target_entity not in components or peer_entity not in components):
                return True
            target_group = components[target_entity]
            peer_group = components[peer_entity]
            if (not isinstance(target_group, frozenset) or not isinstance(peer_group, frozenset)
                    or any(not isinstance(item, str) for item in target_group | peer_group)
                    or target_entity not in target_group or peer_entity not in peer_group
                    or any(components.get(entity) != target_group for entity in target_group)
                    or any(components.get(entity) != peer_group for entity in peer_group)):
                return True
            return peer_entity in target_group
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
        if self._auto_on_blocked(config, self._now_ms(), target_id=command.target_id):
            return "manual_off_protection"
        records = self._control_ownership_for(config)
        latest = latest_ownership(records, command.target_id)
        if latest is None:
            return "auto_ownership_missing"
        if latest.source is OwnershipSource.MANUAL:
            return "manual_intervention_after_decision"
        if latest.source not in (OwnershipSource.AUTO, OwnershipSource.EXPLICIT_HANDOFF):
            return "auto_ownership_missing"
        if not latest.confirmed or not has_automatic_control(
            records, command.target_id
        ):
            return "manual_intervention_after_decision"
        current_revision = self._target_evidence_revision(entity_id)
        trusted, last_known, _revision = self._target_trust(entity_id)
        if latest.source is OwnershipSource.EXPLICIT_HANDOFF and not trusted:
            return "handoff_evidence_untrusted"
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

        if (self._curve_command_block_reason(config, current, command) is not None
                and not self._away_off_is_current(config, command)):
            return "profile_blocked"
        presence = self._curve_presence_state(current)
        confirmed, absence_seconds = self._observe_curve_presence(
            config, current, current.now, presence=presence
        )
        motion_preview = motion_rejected = False
        if (config.room_id == "malyi_koridor" and config.behaviors is not None
                and config.behaviors.only_when_dark):
            motion_preview, motion_rejected, motion_confirmed = self._curve_motion_phase(
                config, current, current.now, presence
            )
            confirmed = confirmed or motion_confirmed
        refreshed = evaluate_curve_room(
            config,
            current,
            presence=presence,
            presence_confirmed=confirmed,
            absence_seconds=absence_seconds,
            motion_preview=motion_preview,
            motion_rejected=motion_rejected,
            independent_targets=self._independent_curve_ready(config),
            target_protections=self._curve_target_protections(config),
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

    def _quiet_hours_active(self, config: RoomLightingConfig) -> bool:
        """Fresh synchronous clock check, also used after durable awaits."""
        current = self._configs.get(config.room_id, config)
        quiet = current.quiet_hours
        return quiet is not None and quiet.contains(
            datetime.fromtimestamp(self._now_ms() / 1000, self._home_timezone()).time()
        )

    def _quiet_hours_blocks_command(
        self, config: RoomLightingConfig, command: PlannedCommand
    ) -> bool:
        return (
            command.action is not LightAction.TURN_OFF
            and command.reason is not DecisionReason.DIMMING
            and self._quiet_hours_active(config)
        )

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
        pair_power_off: bool = False,
        manual_absence_permit: Callable[[], bool] | None = None,
    ) -> str | None:
        """Final synchronous permit check after durable pending and before executor.

        There must be no ``await`` between this method and ``executor.execute``.
        Any manual action, config stop/disable/version change, reserved writer,
        or changed/untrusted target evidence observed while the pending marker
        was being durably saved wins over the planned automatic command.
        """

        if not self._running:
            return "runtime_stopped"
        if self._quiet_hours_blocks_command(config, command):
            return "quiet_hours"
        away_block = self._automatic_away_block_reason()
        if away_block is not None:
            return away_block
        if permission is not None and not self._dispatch_permission_valid(permission):
            return "permission_stale"
        if self._command_conflicts_with_reserved_writer(
            config, command, dependencies=dependencies if exact_active else None
        ):
            return "reserved"
        if self._manual_priority_blocks(config, command, manual_absence_permit=manual_absence_permit):
            return "manual_light_priority"
        if command.action is LightAction.TURN_OFF:
            return self._turn_off_dispatch_block_reason(
                config,
                command,
                entity_id,
                revision_before,
                manual_absence_permit=manual_absence_permit,
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
                pair_power_off=pair_power_off,
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

    def _auto_on_blocked(
        self, config: RoomLightingConfig, moment: int, *, target_id: str | None = None,
    ) -> bool:
        protection = (self._target_protection(config, target_id)
                      if target_id is not None else self._protection_for(config))
        if (protection.active and protection.release_mode != "timer_only"
                and any(self._entity_trusted_state(sensor.entity_id) == "unknown"
                        for sensor in config.devices.sensors
                        if sensor.kind in _PRESENCE_KINDS)):
            return True
        return protection.blocks_auto_on(
            now=moment,
            absence_proven=protection.absence_confirmed,
            absence_since=protection.absence_since,
            absence_until=protection.absence_until,
            absence_latch=protection.absence_latch,
        )

    async def _ensure_power_source(
        self,
        dependency: DevicePowerDependency,
        *,
        at: int,
        room_id: str,
        operation_id: str | None,
        config_version: int | None,
        command_action: str | None = None,
        permission: tuple[str, int, bool] | None = None,
    ) -> tuple[bool, dict[str, object]]:
        async with self._power_source_lock(dependency.power_source_entity_id):
            return await self._ensure_power_source_locked(
                dependency,
                at=at,
                room_id=room_id,
                operation_id=operation_id,
                config_version=config_version,
                command_action=command_action,
                permission=permission,
            )

    async def _ensure_power_source_locked(
        self,
        dependency: DevicePowerDependency,
        *,
        at: int,
        room_id: str,
        operation_id: str | None,
        config_version: int | None,
        command_action: str | None = None,
        permission: tuple[str, int, bool] | None = None,
    ) -> tuple[bool, dict[str, object]]:
        """Use the shared delivery lifecycle with exact dependency policy."""
        return await self._run_power_attempt(
            dependency.power_source_entity_id,
            room_id=room_id,
            operation_id=operation_id,
            config_version=config_version,
            command_action=command_action,
            dependency=dependency,
            permission=permission,
        )

    async def _ensure_legacy_room_power(
        self,
        power_entity: str,
        *,
        at: int,
        room_id: str,
        operation_id: str | None,
        config_version: int | None,
        command_action: str | None = None,
        permission: tuple[str, int, bool] | None = None,
    ) -> tuple[bool, dict[str, object]]:
        async with self._power_source_lock(power_entity):
            return await self._ensure_legacy_room_power_locked(
                power_entity,
                at=at,
                room_id=room_id,
                operation_id=operation_id,
                config_version=config_version,
                command_action=command_action,
                permission=permission,
            )

    async def _ensure_legacy_room_power_locked(
        self,
        power_entity: str,
        *,
        at: int,
        room_id: str,
        operation_id: str | None,
        config_version: int | None,
        command_action: str | None = None,
        permission: tuple[str, int, bool] | None = None,
    ) -> tuple[bool, dict[str, object]]:
        """Legacy mapping, using the same source-scoped delivery lifecycle."""
        return await self._run_power_attempt(
            power_entity,
            room_id=room_id,
            operation_id=operation_id,
            config_version=config_version,
            command_action=command_action,
            permission=permission,
        )

    async def _run_power_attempt(
        self,
        source: str,
        *,
        room_id: str,
        operation_id: str | None,
        config_version: int | None,
        command_action: str | None,
        dependency: DevicePowerDependency | None = None,
        permission: tuple[str, int, bool] | None = None,
    ) -> tuple[bool, dict[str, object]]:
        """Own one complete transition while the source lock is held.

        Admission is read-only. Only a possible physical send consumes a
        retry. The power result is persisted here, before another room can
        use the source; the later room receipt must not apply it again.
        """
        revision = self._power_evidence_revision(source)
        self._release_power_failure_on_fresh_evidence(source, revision)
        detail: dict[str, object] = {
            "sourceEntityId": source,
            "evidenceRevision": revision,
            "reason": "power_source_unavailable",
            "dispatchAttempt": False,
            "sent": False,
            "sendOutcome": "not_sent",
        }
        if dependency is not None:
            detail.update(
                policy=dependency.policy,
                warmupSeconds=dependency.warmup_seconds,
                sourceTurnedOn=False,
                waitedSeconds=0.0,
            )
        attempt = PowerAttempt(source, revision, room_id, detail)
        powered = False
        try:
            powered = await self._execute_power_attempt(
                attempt,
                operation_id=operation_id,
                config_version=config_version,
                command_action=command_action,
                dependency=dependency,
                permission=permission,
            )
        finally:
            saved = await self._finish_power_attempt(
                attempt,
                powered=powered,
                operation_id=operation_id,
                config_version=config_version,
            )
        return powered and saved, detail

    async def _execute_power_attempt(
        self,
        attempt: PowerAttempt,
        *,
        operation_id: str | None,
        config_version: int | None,
        command_action: str | None,
        dependency: DevicePowerDependency | None,
        permission: tuple[str, int, bool] | None = None,
    ) -> bool:
        source, detail = attempt.source, attempt.detail
        if permission is not None and (
            not self._running or not self._dispatch_permission_valid(permission)
        ):
            detail["reason"] = "dispatch_permission_stale"
            return False
        config = self._configs.get(attempt.room_id)
        if config is not None and self._quiet_hours_active(config):
            detail["reason"] = "quiet_hours"
            return False
        refusal = self._power_send_guard(source, attempt.room_id)
        if refusal is not None:
            detail["reason"] = refusal
            return False
        attempt.plan = self._plan_power_attempt(
            source, room_id=attempt.room_id, command_action=command_action
        )
        if attempt.plan is None:
            detail.update(reason="power_source_unconfirmed", suppressed=True)
            return False
        state = self._entity_state_object(source)
        if trusted_power_state(state, same_state_confirmation=True, now_ms=self._now_ms()) == "on":
            detail["reason"] = "power_source_on"
            return True
        if dependency is not None:
            if dependency.policy != AUTO_TURN_ON_POLICY:
                detail["reason"] = (
                    "power_source_off"
                    if state is not None and getattr(state, "state", None) == "off"
                    else "power_source_unavailable"
                )
                return False
            resolver = self._electrical_breaker_resolver
            try:
                breaker = resolver(source) if resolver is not None else None
            except Exception:  # noqa: BLE001 - unknown safety state refuses power
                breaker = None
            if breaker is None:
                detail["reason"] = "automatic_power_source_safety_unavailable"
                return False
            if breaker:
                detail["reason"] = "automatic_breaker_power_on_forbidden"
                return False
        ensure_power = getattr(self._executor, "async_power_on", None)
        if not callable(ensure_power) or self._hass is None:
            return False
        attempt.pending = await self._persist_pending_power_dispatch(
            at=self._now_ms(),
            room_id=attempt.room_id,
            source_entity_id=source,
            evidence_revision=attempt.revision,
            operation_id=operation_id,
            config_version=config_version,
        )
        if not attempt.pending:
            detail["reason"] = "power_pending_persistence_failed"
            return False

        # Every awaited step can invalidate admission. Re-plan without
        # spending anything, using both current sensor evidence and intent.
        if permission is not None and (
            not self._running or not self._dispatch_permission_valid(permission)
        ):
            detail["reason"] = "dispatch_permission_stale"
            return False
        config = self._configs.get(attempt.room_id)
        if config is not None and self._quiet_hours_active(config):
            detail["reason"] = "quiet_hours"
            return False
        refusal = self._power_send_guard(source, attempt.room_id)
        if refusal is not None:
            detail["reason"] = refusal
            return False
        if (
            self._power_evidence_revision(source) != attempt.revision
            or self._plan_power_attempt(
                source, room_id=attempt.room_id, command_action=command_action
            ) is None
        ):
            detail["reason"] = "power_source_unconfirmed"
            return False

        attempt.entered_at = self._now_ms()
        detail.update(dispatchAttempt=True, sendOutcome="uncertain")
        self._executing_actions[source] = "turn_on"
        attempt.previous_grace = self._command_grace.get(source)
        self._command_grace[source] = ("turn_on", attempt.entered_at)
        try:
            kwargs = (
                {"protected_entities": self._power_protected_entities(attempt.room_id)}
                if isinstance(self._executor, RoomLightingHaExecutor) else {}
            )
            receipt = await ensure_power(self._hass, source, **kwargs)
        except asyncio.CancelledError:
            detail["reason"] = "power_source_uncertain"
            raise
        except Exception:  # noqa: BLE001 - the relay may have received the command
            detail["reason"] = "power_source_uncertain"
            return False
        finally:
            self._executing_actions.pop(source, None)

        confirmed, sent, outcome = self._power_receipt_outcome(receipt)
        detail.update(
            sent=sent,
            sendOutcome=outcome,
            dispatchAttempt=outcome in {"sent", "uncertain"},
        )
        if isinstance(receipt, Mapping):
            detail["powerPreparation"] = dict(receipt)
        if not confirmed:
            detail["reason"] = (
                str(receipt.get("reason") or "power_source_unavailable")
                if isinstance(receipt, Mapping) and outcome == "not_sent"
                else "power_source_uncertain" if outcome == "uncertain"
                else "power_source_unconfirmed"
            )
            return False
        if dependency is not None:
            detail["sourceTurnedOn"] = sent
            if sent and dependency.warmup_seconds > 0:
                # Cancellation remains an attempted, unresolved delivery.
                await asyncio.sleep(dependency.warmup_seconds)
                detail["waitedSeconds"] = dependency.warmup_seconds
                state = self._entity_state_object(source)
                if (
                    trusted_power_state(state, same_state_confirmation=True, now_ms=self._now_ms()) != "on"
                    or state_is_restored_or_cached(state)
                ):
                    detail["reason"] = "power_source_unconfirmed"
                    return False
        detail["reason"] = "power_source_on"
        return True

    def _power_send_guard(self, source: str, room_id: str) -> str | None:
        if self._is_reserved_target_or_entity(None, source):
            return "power_source_reserved"
        refusal = self._automatic_away_block_reason()
        if refusal is not None:
            return refusal
        config = self._configs.get(room_id)
        if config is not None and config.profile == "day_curve" and self._away:
            return "profile_blocked"
        return None

    @staticmethod
    def _power_receipt_outcome(receipt: object) -> tuple[bool, bool, str]:
        """Normalize the executor result without turning uncertainty into refusal."""
        if isinstance(receipt, Mapping):
            confirmed = receipt.get("confirmed") is True
            sent = receipt.get("sent") is True
            outcome = receipt.get("sendOutcome")
        elif isinstance(receipt, bool):
            return receipt, receipt, "sent" if receipt else "uncertain"
        else:
            confirmed = getattr(receipt, "confirmed", False) is True
            sent = getattr(receipt, "sent", False) is True
            outcome = getattr(receipt, "send_outcome", None)
        if not isinstance(outcome, str) or outcome not in {"sent", "not_sent", "uncertain"}:
            return False, sent, "sent" if sent else "uncertain"
        if (sent and outcome != "sent") or (outcome == "sent" and not sent):
            return False, sent, "sent" if sent else "uncertain"
        if outcome == "uncertain":
            confirmed = False
        return confirmed, sent, str(outcome)

    async def _finish_power_attempt(
        self,
        attempt: PowerAttempt,
        *,
        powered: bool,
        operation_id: str | None,
        config_version: int | None,
    ) -> bool:
        """Commit memory and the durable power result once for every exit."""
        source, detail = attempt.source, attempt.detail
        possible_send = (
            attempt.entered_at is not None
            and detail.get("sendOutcome") in {"sent", "uncertain"}
        )
        if (
            attempt.entered_at is not None
            and detail.get("sendOutcome") == "not_sent"
            and self._command_grace.get(source) == ("turn_on", attempt.entered_at)
        ):
            if attempt.previous_grace is None:
                self._command_grace.pop(source, None)
            else:
                self._command_grace[source] = attempt.previous_grace
        if possible_send:
            self._commit_power_attempt(source, attempt.plan, attempt.entered_at)
            detail["_attemptAt"] = attempt.entered_at
        elif not powered:
            previous = self._preserved_power_failure(source)
            if previous is not None:
                detail["_preservedFailure"] = previous
                detail["deliveryEpisodeOpen"] = True

        saved = await self._persist_power_result(
            at=self._now_ms(),
            room_id=attempt.room_id,
            detail=detail,
            operation_id=operation_id,
            config_version=config_version,
        )
        if saved:
            if powered:
                self._clear_power_delivery(source)
            return True
        # The store still has its pending marker or the preceding failure.
        # Do not grant a retry on the strength of an uncommitted completion.
        if attempt.pending or possible_send:
            self._power_failures.setdefault(source, ("turn_on", attempt.revision))
            previous = self._power_delivery.get(source)
            self._power_delivery[source] = PowerDeliveryEpisode(
                revision=previous.revision if previous else attempt.revision,
                presence_since=previous.presence_since if previous else 0,
                attempts=previous.attempts if previous else 0,
                last_attempt_ms=previous.last_attempt_ms if previous else 0,
                exhausted=True,
            )
        detail["reason"] = "power_result_persistence_failed"
        detail["deliveryEpisodeOpen"] = source in self._power_failures
        return False

    def _commit_power_attempt(
        self, source: str, plan: PowerAttemptPlan | None, at: int
    ) -> None:
        """Account for one actual or uncertain send, never for admission."""
        revision = plan.revision if plan is not None else self._power_evidence_revision(source)
        self._record_power_failure(source, revision, at)
        if plan is not None and plan.retry_episode is not None:
            episode = plan.retry_episode
            self._power_delivery[source] = replace(
                episode, attempts=episode.attempts + 1, last_attempt_ms=at
            )

    def _clear_power_delivery(self, source: str) -> None:
        self._power_failures.pop(source, None)
        self._power_failure_at.pop(source, None)
        self._power_delivery.pop(source, None)

    def _release_power_failure_on_fresh_evidence(self, source: str, revision: str) -> None:
        """An explicit evidence transition, separate from the read-only gate."""
        failure = self._power_failures.get(source)
        episode = self._power_delivery.get(source)
        previous = failure[1] if failure is not None else episode.revision if episode else None
        if (
            previous is not None
            and previous != revision
            and self._trusted_evidence_revision(revision)
        ):
            self._clear_power_delivery(source)

    def _preserved_power_failure(self, source: str) -> dict[str, object] | None:
        failure = self._power_failures.get(source)
        episode = self._power_delivery.get(source)
        if failure is None and episode is None:
            return None
        revision = failure[1] if failure is not None else episode.revision
        record: dict[str, object] = {
            "key": f"power:{source}", "kind": "power", "entityId": source,
            "action": "turn_on", "evidenceRevision": revision,
        }
        at = self._power_failure_at.get(source)
        if at is None and episode is not None:
            at = episode.last_attempt_ms
        if isinstance(at, int) and not isinstance(at, bool) and at > 0:
            record["blockedAfterMs"] = at
        return record

    async def _persist_power_result(
        self,
        *,
        at: int,
        room_id: str,
        detail: dict[str, object],
        operation_id: str | None,
        config_version: int | None,
    ) -> bool:
        recorder = getattr(self._shadow, "async_record_dispatch", None)
        if not callable(recorder):
            return False
        try:
            await recorder(
                at=at, room_id=room_id, receipts=({"power": detail},),
                operation_id=operation_id, config_version=config_version,
            )
        except Exception:  # noqa: BLE001 - keep the pending marker fail-closed
            _LOGGER.warning("room lighting final power result write failed")
            return False
        # Only the nested power part is complete; the target still needs
        # its own receipt. This flag is stripped before journal storage.
        detail["_durable_recorded"] = True
        return True

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

    async def _reconcile_manual_restart(self, config: RoomLightingConfig) -> bool:
        scope = self._room_authority_scope(config)
        if not scope:
            return False
        if not self._independent_topology_ready(config):
            return await self._reconcile_manual_restart_scope(config, scope, selective=False)
        graph = self._manual_light_authority.related_sources
        groups = {graph.related(target.entity_id) for target in config.devices.light_targets}
        recovered = False
        for group in sorted(groups, key=lambda item: tuple(sorted(item))):
            if group <= scope:
                recovered = await self._reconcile_manual_restart_scope(config, group, selective=True) or recovered
        return recovered

    async def _reconcile_manual_restart_scope(self, config: RoomLightingConfig, entities: frozenset[str], *, selective: bool) -> bool:
        """Provide live group evidence to the shared deny-only recovery owner."""
        authority = self._manual_light_authority
        reconcile = getattr(authority, "async_reconcile_restart", None)
        if not callable(reconcile):
            return False
        recover_selective = getattr(authority, "async_recover_selective_return", None)
        if callable(recover_selective):
            await recover_selective(entities)
        sensor_proof: tuple[tuple[object, ...], ...] | None = None

        def absence_ready() -> bool:
            nonlocal sensor_proof
            if not self._running or self._configs.get(config.room_id) is not config:
                return False
            relevant = [sensor for sensor in config.devices.sensors
                        if sensor.kind in _PRESENCE_KINDS]
            if relevant:
                now = self._now_ms()
                snapshots: list[SensorSnapshot] = []
                evidence: list[tuple[object, ...]] = []
                for sensor in relevant:
                    value = self._entity_trusted_state(sensor.entity_id)
                    if value == "unknown":
                        return False
                    state = self._entity_state_object(sensor.entity_id)
                    changed = _last_changed_ms(state, now=now)
                    snapshots.append(SensorSnapshot(
                        sensor_id=sensor.id, kind=sensor.kind,
                        state=SensorState(value), last_changed=changed,
                    ))
                    evidence.append((sensor.entity_id, value, changed,
                                     self._state_observed_ms(state)))
                current_proof = tuple(evidence)
                if sensor_proof is not None and current_proof != sensor_proof:
                    return False
                # Use the ordinary absence transition, including the exact
                # return moment and new-OFF start. The room snapshot may lag
                # a live event while this room is awaiting durable storage.
                self._observe_absence_sensors(config, tuple(snapshots), now)
                absence = self._absence.get(config.room_id)
                if absence is None or not absence[0] or absence[1] is None:
                    return False
                end = absence[2] if absence[2] is not None else now
                if (self._started_at is None or absence[1] < self._started_at
                        or end - absence[1]
                        < config.manual_off_protection.stable_absence_seconds * 1000):
                    return False
                sensor_proof = current_proof
            elif config.manual_off_protection.release_mode.value != "timer_only":
                return False
            return True

        def observe_off() -> Mapping[str, int] | None:
            if not absence_ready():
                return None
            result: dict[str, int] = {}
            dependencies = self._power_dependencies()
            decision = self._exact_power_decision(config, dependencies)
            for target in config.devices.light_targets:
                entity = target.entity_id
                if entity not in entities:
                    continue
                if entity is None or target.id in decision.unproven_target_ids:
                    return None
                observed_entity = entity
                # Reuse only the already-proven physical edge. Alternative
                # lights are never presumed off because the chandelier is off.
                dependency = (decision.projection or {}).get(entity)
                if dependency is not None:
                    source = dependency.power_source_entity_id
                    if self._entity_trusted_state(source) == "off":
                        observed_entity = source
                elif (not decision.active and target.id == self._pair_main_target_id(config)
                      and self._proven_power_pair_off(config)):
                    observed_entity = config.devices.power_switch.entity_id
                if self._entity_trusted_state(observed_entity) != "off":
                    return None
                stamp = self._target_evidence_observed_ms(observed_entity)
                if stamp is None:
                    return None
                result[entity] = stamp
            target_entities = {target.entity_id for target in config.devices.light_targets}
            for source in entities - target_entities:
                source_state = self._entity_trusted_state(source)
                if source_state != "off" and not (
                    selective and source_state == "on"
                    and all(self._entity_trusted_state(target.entity_id) == "off"
                            and self._target_evidence_observed_ms(target.entity_id) is not None
                            for target in config.devices.light_targets if target.entity_id in entities)
                ):
                    return None
                stamp = self._target_evidence_observed_ms(source)
                if stamp is None:
                    return None
                result[source] = stamp
            return result

        recovered = await reconcile(
            entities, version=config.version, observe_off=observe_off, now_ms=self._now_ms,
        )
        if (not recovered and not selective and self._observation_refresher is not None
                and callable(getattr(self._light_priority, "explicit_return_snapshot", None))
                and entities and absence_ready()
                and all(authority.barrier_reason_for(entity) == "restart_uncertain"
                        for entity in entities)
                and any(self._entity_trusted_state(entity) == "unknown" for entity in entities)
                and observe_off() is None):
            previous = self._restart_refresh_attempts.get(config.room_id)
            now = self._now_ms()
            if previous is None or previous[0] is not config or now - previous[1] >= 300_000:
                self._restart_refresh_attempts[config.room_id] = (config, now)
                if await self._refresh_manual_return_observations(
                    config.room_id, intent_current=absence_ready,
                ) is None and absence_ready():
                    recovered = await reconcile(
                        entities, version=config.version, observe_off=observe_off,
                        now_ms=self._now_ms,
                    )
        if recovered:
            _LOGGER.info("room lighting restart recovery verified for %s", config.room_id)
        return recovered

    def _entity_state(self, entity_id: str) -> str:
        if self._hass is None:
            return "unknown"
        state = self._hass.states.get(entity_id)
        if state is None:
            return "unknown"
        return str(getattr(state, "state", "unknown")).lower()

    def _entity_state_object(self, entity_id: str) -> object | None:
        if self._state_reader is not None:
            return self._state_reader(entity_id)
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
                f"{trusted_value}|{observed}{physical_evidence_token(state)}"
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
        trusted_state = trusted_power_state(state, same_state_confirmation=True, now_ms=self._now_ms())
        if trusted_state is None:
            value = str(getattr(state, "state", "unknown")).lower()
            if value not in {"off", "on"}:
                value = "unknown"
            return f"untrusted:{value}"
        observed = self._state_observed_ms(state)
        revision = "" if observed is None else str(observed)
        return f"{trusted_state}|{revision}{physical_evidence_token(state)}"

    def _target_evidence_observed_ms(self, entity_id: str) -> int | None:
        state = self._entity_state_object(entity_id)
        if state is None or self._entity_trusted_state(entity_id) == "unknown":
            return None
        return trusted_target_observed_ms(state, now_ms=self._now_ms())

    def _power_evidence_observed_ms(self, entity_id: str) -> int | None:
        state = self._entity_state_object(entity_id)
        if state is None or trusted_power_state(state, same_state_confirmation=True, now_ms=self._now_ms()) is None:
            return None
        return self._state_observed_ms(state)

    @staticmethod
    def _state_observed_ms(state: object) -> int | None:
        if isinstance(state, ObservedLightState):
            observation = state.observation
            return None if observation is None else observation.observed_at_ms
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
                return trusted_power_state(state, same_state_confirmation=True, now_ms=self._now_ms())
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
            # Владелец (2026-10-09): отпирание замка ничего не снимает.
            # День начинается только по доверенному показанию датчика
            # присутствия; отпирание будит кривую (entry wakeup в ветке
            # оценки), но не удаляет ручные защиты (SEC-RP9-MORNING-02).
            if self._morning_presence_confirmed(config):
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

    def _related_sources_for_configs(
        self, dependencies: Mapping[str, DevicePowerDependency] | None,
    ) -> object:
        """Isolate rooms only when their existing topology gate proves them."""
        from .related_light_sources import RelatedLightSources
        projections = {}
        for config in self._configs.values():
            decision = self._exact_power_decision(config, dependencies)
            projections[config.room_id] = (
                decision.projection
                if dependencies is not None and decision.active and not decision.incomplete
                else None
            )
        base = RelatedLightSources.from_room_projections(
            self._configs.values(), projections, fallback_dependencies=dependencies)
        if not base.ready:
            return base
        # Wiring completeness never proves single-device-ness; ask the
        # registry-backed provider for the entities that are one device.
        # A provider failure means no evidence, never an error path.
        try:
            evidence = self._single_entity_evidence(tuple(base.components))
        except Exception:
            evidence = frozenset()
        return RelatedLightSources.from_room_projections(
            self._configs.values(), projections, fallback_dependencies=dependencies,
            proven_single_entities=evidence)

    def _room_authority_scope(self, config: RoomLightingConfig) -> frozenset[str] | None:
        """Include complete physical components without taking another room's light."""
        targets = frozenset(target.entity_id for target in config.devices.light_targets)
        if not targets or any(not isinstance(entity, str) for entity in targets):
            return None
        authority = self._manual_light_authority
        if authority is None:
            return targets
        graph = getattr(authority, "related_sources", None)
        if graph is None or not graph.ready or any(entity not in graph.components for entity in targets):
            return None
        scope = frozenset().union(*(graph.related(entity) for entity in targets))
        other_targets = {target.entity_id for room_id, room in self._configs.items()
                         if room_id != config.room_id for target in room.devices.light_targets}
        return None if scope & other_targets else scope

    def _independent_topology_ready(self, config: RoomLightingConfig) -> bool:
        """Use component scopes only while the proved physical graph matches."""
        authority = self._manual_light_authority
        dependencies = self._power_dependencies()
        if authority is None or dependencies is None:
            return False
        decision = self._exact_power_decision(config, dependencies)
        graph = getattr(authority, "related_sources", None)
        return bool(decision.active and not decision.incomplete
                    and getattr(graph, "ready", False)
                    and graph == self._related_sources_for_configs(dependencies))

    def _independent_curve_ready(self, config: RoomLightingConfig) -> bool:
        return (self._independent_topology_ready(config)
                and self._manual_light_authority.barrier_reason in (None, "return_pending"))

    def _curve_command_block_reason(
        self, config: RoomLightingConfig, context: RoomLightingContext,
        command: PlannedCommand,
    ) -> object:
        if self._independent_curve_ready(config):
            target_context = replace(context, protection=self._target_protection(config, command.target_id))
            return curve_target_block_reason(config, target_context, command.target_id)
        return curve_profile_block_reason(config, context)

    def _target_protection(self, config: RoomLightingConfig, target_id: str) -> ProtectionSnapshot:
        """Use the proved physical group; unknown topology keeps the room guard."""
        if not self._independent_curve_ready(config):
            return self._protection_for(config)
        target = config.devices.target(target_id)
        graph = self._manual_light_authority.related_sources
        entity = getattr(target, "entity_id", None)
        if not isinstance(entity, str) or entity not in graph.components:
            return self._protection_for(config)
        group = graph.related(entity)
        ids = tuple(item.id for item in config.devices.light_targets if item.entity_id in group)
        return self._protection_for(config, target_ids=ids)

    def _curve_target_protections(self, config: RoomLightingConfig) -> dict[str, ProtectionSnapshot] | None:
        if not self._independent_curve_ready(config):
            return None
        return {target.id: self._target_protection(config, target.id)
                for target in config.devices.light_targets}

    def _rebuild_index(self) -> None:
        authority = self._manual_light_authority
        if authority is not None:
            authority.set_related_sources(self._related_sources_for_configs(self._power_dependencies()))
            register = getattr(authority, "register_recovery_scope", None)
            if callable(register):
                for config in self._configs.values():
                    scope = self._room_authority_scope(config)
                    if scope is not None:
                        register(scope, config.version)
                        if self._independent_topology_ready(config):
                            for target in config.devices.light_targets:
                                group = authority.related_sources.related(target.entity_id)
                                if group <= scope:
                                    register(group, config.version)
        rooms_by_entity: EntityRooms = {}
        entry_by_entity: EntityRooms = {}
        targets_by_entity: dict[str, tuple[str, str]] = {}
        power_by_entity: dict[str, str] = {}
        targets: dict[str, object] = {}
        room_targets: dict[str, dict[str, object]] = {}
        # One physical entity must belong to one room. A collision is reported
        # explicitly instead of silently overwriting the first room's owner.
        for collision in room_lighting_entity_collisions(self._configs.values()):
            _LOGGER.warning("room lighting entity collision: %s", collision)
        for config in self._configs.values():
            room_targets[config.room_id] = {
                target.id: target for target in (*config.devices.light_targets, *config.devices.auxiliaries)
            }
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
            if isinstance(self._executor, RoomLightingHaExecutor):
                update(targets, room_targets=room_targets)
            else:
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
        # HH-014.b morning defect: the anchor comparison runs BEFORE the
        # revision-equality shortcut. A genuine newer proven report of the
        # SAME value (state_2 stays "off", the relay simply answers again)
        # must release the guard even after the report has aged out of the
        # freshness window and the revision collapsed back to "untrusted:…".
        # HH-014.b (AUD-LATCH-05 + AUD-LATCH-08): the attempt boundary is
        # authoritative and the anchor states are explicit. A valid positive
        # int marked attemptBoundary is the exact attempt boundary: only a
        # proven physical report strictly newer than it releases the target
        # (even aged out of the freshness window); the revision timestamp
        # predates the attempt and is never a substitute boundary. An
        # absent, unknown or corrupted boundary fails closed for turn_on
        # while the evidence is untrusted; a FRESH trusted live report
        # bounds the block to one freshness window and releases (the device
        # proved it is alive). A planned off keeps the safe-direction
        # semantics and is allowed WITHOUT dropping the existing record.
        anchor = None
        if len(recorded) > 2:
            candidate = recorded[2]
            if (
                isinstance(candidate, int)
                and not isinstance(candidate, bool)
                and candidate > 0
            ):
                anchor = candidate
        if anchor is not None:
            # A level report proves only a new level observation. It may release
            # a failed brightness command, never a failed ON/OFF or an overflow.
            if (
                action is LightAction.SET_BRIGHTNESS
                and recorded[0] == LightAction.SET_BRIGHTNESS.value
            ):
                level_state = self._entity_state_object(entity_id)
                if (
                    str(getattr(level_state, "state", "unknown")).lower() == "on"
                    and not state_is_restored_or_cached(level_state)
                ):
                    level_ms = physical_brightness_observed_ms(
                        level_state, now_ms=self._now_ms(),
                    )
                    if level_ms is not None and level_ms > anchor:
                        self._dispatch_failures.pop(failure_key, None)
                        return False
            if (action is LightAction.SET_COLOR_TEMPERATURE
                and recorded[0] == LightAction.SET_COLOR_TEMPERATURE.value):
                color_state = self._entity_state_object(entity_id)
                if (str(getattr(color_state, "state", "unknown")).lower() == "on"
                    and not state_is_restored_or_cached(color_state)):
                    color_ms = physical_color_observed_ms(color_state, now_ms=self._now_ms())
                    if color_ms is not None and color_ms > anchor:
                        self._dispatch_failures.pop(failure_key, None)
                        return False
            latest_ms = self._proven_report_ms(entity_id)
            if latest_ms is not None and latest_ms > anchor:
                self._dispatch_failures.pop(failure_key, None)
                return False
            if recorded[1] == current_revision:
                return True
            if not safe_off:
                return True
            if recorded[0] == LightAction.TURN_OFF.value:
                return True
            return False
        if safe_off and not (
            recorded[0] == LightAction.TURN_OFF.value
            and not self._trusted_evidence_revision(current_revision)
        ):
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

    def _confirmed_curve_presence_since(self, room_id: str) -> int | None:
        """Start of the CURRENT confirmed presence episode, or ``None``.

        The day-curve tracker confirms an episode after
        ``CURVE_PRESENCE_CONFIRM_MS`` of proven continuity; the confirmed
        occupant is physical evidence that the room's light is wanted.
        """

        tracker = self._curve_presence.get(room_id)
        if not tracker:
            return None
        since = tracker.get("since")
        if since is None:
            return None
        if self._now_ms() - int(since) < CURVE_PRESENCE_CONFIRM_MS:
            return None
        # The confirmation must still be BACKED by a fresh observation:
        # after the sensor faulted (last_seen reset to None) or went stale
        # the pointer alone proves nothing (AUD-POWER-07).
        last_seen = tracker.get("last_seen")
        if last_seen is None:
            return None
        if self._now_ms() - int(last_seen) > CURVE_SENSOR_FRESHNESS_MS:
            return None
        return int(since)

    def _record_power_failure(
        self, entity_id: str, revision: str, at: int
    ) -> None:
        """Record one unconfirmed power-on attempt of ``revision``.

        The attempt moment is kept so the FIRST retry of a delivery
        episode honours the spacing from the ORIGINAL attempt, not from
        the moment the episode happens to be opened (AUD-POWER-02)."""

        self._power_failures[entity_id] = ("turn_on", revision)
        self._power_failure_at[entity_id] = at

    def _power_attempt_blocked(
        self,
        entity_id: str,
        room_id: str | None = None,
        command_action: str | None = None,
    ) -> bool:
        """Read-only admission query; asking never spends the delivery budget."""
        return self._plan_power_attempt(
            entity_id, room_id=room_id, command_action=command_action
        ) is None

    def _plan_power_attempt(
        self,
        entity_id: str,
        *,
        room_id: str | None,
        command_action: str | None,
    ) -> PowerAttemptPlan | None:
        revision = self._power_evidence_revision(entity_id)
        failure = self._power_failures.get(entity_id)
        episode = self._power_delivery.get(entity_id)
        previous = failure[1] if failure is not None else episode.revision if episode else None
        fresh_revision = (
            previous is not None and previous != revision
            and self._trusted_evidence_revision(revision)
        )
        if fresh_revision:
            failure, episode = None, None
        if failure is None and episode is None:
            if not self._trusted_evidence_revision(revision):
                return None
            if self._dispatch_overflow_latch:
                boundary = self._dispatch_overflow_blocked_after_ms
                observed = self._power_evidence_observed_ms(entity_id)
                if (
                    boundary is None or not self._trusted_evidence_revision(revision)
                    or observed is None or observed <= boundary
                ):
                    return None
            return PowerAttemptPlan(revision=revision)

        if episode is not None and (
            episode.exhausted or episode.attempts >= POWER_RETRY_LIMIT_PER_REVISION
        ):
            return None
        if previous != revision:
            return None
        if not self._trusted_evidence_revision(revision) and revision != "untrusted:off":
            return None
        presence_since = (
            self._confirmed_curve_presence_since(room_id) if room_id is not None else None
        )
        if (
            command_action != LightAction.TURN_ON.value
            or presence_since is None
            or not self._confirmed_presence_live(room_id)
        ):
            return None
        failed_at = self._power_failure_at.get(entity_id)
        if failed_at is None and episode is not None:
            failed_at = episode.last_attempt_ms
        if failed_at is None or failed_at <= 0:
            return None
        last_attempt = max(failed_at, episode.last_attempt_ms if episode else failed_at)
        if self._now_ms() - last_attempt < POWER_RETRY_INTERVAL_MS:
            return None
        if episode is None:
            episode = PowerDeliveryEpisode(
                revision=revision, presence_since=presence_since,
                attempts=0, last_attempt_ms=failed_at,
            )
        return PowerAttemptPlan(revision=revision, retry_episode=episode)

    def _confirmed_presence_live(self, room_id: str | None) -> bool:
        """AUD-POWER-11 (luna 2026-10-12): the in-room snapshot can lag the
        physical sensor while the room lock is held; the send boundary must
        confirm the CURRENT trusted sensor state, not only the snapshot."""

        if room_id is None:
            return False
        config = self._configs.get(room_id)
        if config is None:
            return False
        live = False
        for sensor in config.devices.sensors:
            if sensor.kind != "presence":
                continue
            entity_id = sensor.entity_id
            if not isinstance(entity_id, str):
                continue
            if self._entity_trusted_state(entity_id) == "on":
                live = True
        return live

    def _refresh_exact_power_index(self) -> None:
        """Re-read the durable map and keep the event index in sync with it."""

        dependencies = self._power_dependencies()
        authority = self._manual_light_authority
        if authority is not None:
            authority.set_related_sources(self._related_sources_for_configs(dependencies))
            register = getattr(authority, "register_recovery_scope", None)
            if callable(register):
                for config in self._configs.values():
                    scope = self._room_authority_scope(config)
                    if scope is not None:
                        register(scope, config.version)
                        if self._independent_topology_ready(config):
                            for target in config.devices.light_targets:
                                group = authority.related_sources.related(target.entity_id)
                                if group <= scope:
                                    register(group, config.version)
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
        self._power_failure_at = {}
        self._power_delivery = {}
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
                        # Only entries with the explicit attempt-boundary
                        # marker carry an exact boundary. Older entries
                        # (blockedAfterMs was the room pass time) restore as
                        # UNKNOWN and stay fail-closed for turn_on with a
                        # bounded live-report release.
                        anchor = None
                        boundary = raw.get("blockedAfterMs")
                        if (
                            raw.get("attemptBoundary") is True
                            and isinstance(boundary, int)
                            and not isinstance(boundary, bool)
                            and boundary > 0
                        ):
                            anchor = boundary
                        self._dispatch_failures[(room_id, target_id)] = (
                            action,
                            evidence_revision,
                            anchor,
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
                        # A restored latch arrives WITHOUT a delivery
                        # episode: create it EXHAUSTED so a restart cannot
                        # refresh the retry budget on the same evidence
                        # (AUD-POWER-03). Only fresh trusted relay
                        # evidence (a new revision) clears the latch and
                        # the exhausted episode together.
                        self._power_delivery[source] = PowerDeliveryEpisode(
                            revision=evidence_revision,
                            presence_since=0,
                            attempts=POWER_RETRY_LIMIT_PER_REVISION,
                            last_attempt_ms=0,
                            exhausted=True,
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
            # AUD-LATCH-04 + luna P1: the old-format journal has no per-receipt
            # attempt boundary (_attemptAt), so the true attempt moment is
            # unknown. The entry time is the room-processing pass time, not a
            # provable attempt boundary — the latch restores with an UNKNOWN
            # anchor and stays fail-closed for turn_on.
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
                        # The true attempt moment is unknown for old-format
                        # receipts: the latch restores with an UNKNOWN anchor
                        # and stays fail-closed for turn_on.
                        self._dispatch_failures[key] = (
                            str(raw.get("action") or ""),
                            evidence_revision,
                            None,
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
                    # AUD-POWER-05: a latch migrated from the legacy
                    # journal arrives EXHAUSTED too — a restart on an old
                    # journal must not refresh the retry budget; only
                    # fresh trusted relay evidence clears it.
                    self._power_delivery[source] = PowerDeliveryEpisode(
                        revision=power_revision,
                        presence_since=0,
                        attempts=POWER_RETRY_LIMIT_PER_REVISION,
                        last_attempt_ms=0,
                        exhausted=True,
                    )

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

    def _protection_for(
        self, config: RoomLightingConfig, *, target_ids: tuple[str, ...] | None = None,
    ) -> ProtectionSnapshot:
        """Build a snapshot; a supplied scope must already be physically proved.

        Invalid scopes retain the room-wide guard. This helper does not prove
        pair membership or protect events from an upstream power switch.
        """
        if not config.manual_off_protection.enabled:
            return ProtectionSnapshot(minimum_interval_seconds=0, release_mode="timer_only", require_directed_off=True)
        protection = config.manual_off_protection
        absence_confirmed, absence_since, absence_until = self._absence.get(
            config.room_id, (False, None, None)
        )
        room_targets = frozenset(target.id for target in config.devices.light_targets)
        scoped = bool(isinstance(target_ids, tuple) and target_ids
                      and all(isinstance(item, str) and item in room_targets for item in target_ids))
        selected = target_ids if scoped else room_targets
        started_at = self._ownership.last_manual_off_at(config.room_id, selected)
        absence_latch = config.room_id in self._absence_release_latch
        if scoped:
            # The room latch is usable only if its observation floor proves
            # the completed absence started no earlier than this scope's OFF.
            # Every new room manual action clears the latch before moving the
            # floor; restart also clears it and establishes a fresh floor.
            manual_floor = getattr(self, "_manual_absence_floor", {}).get(config.room_id)
            start_floor = getattr(self, "_started_at", None)
            floors = [value for value in (manual_floor, start_floor) if type(value) is int]
            absence_latch = (absence_latch and started_at is not None
                             and bool(floors) and max(floors) >= started_at)
        if started_at is None:
            # No manual-off anchor: the protection itself is inactive, but the
            # room's real absence evidence must still reach the ownership
            # release checks (_curve_manual_blocked reads these fields). A bare
            # default here would keep restored manual records locked forever,
            # because their absence condition would read as never confirmed.
            return ProtectionSnapshot(
                active=False,
                minimum_interval_seconds=protection.minimum_interval_seconds,
                stable_absence_seconds=protection.stable_absence_seconds,
                release_mode=protection.release_mode.value,
                absence_confirmed=absence_confirmed,
                absence_since=absence_since,
                absence_until=absence_until,
                absence_latch=absence_latch,
            )
        protection = config.manual_off_protection
        absence_confirmed, absence_since, absence_until = self._absence.get(
            config.room_id, (False, None, None)
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
            absence_until=absence_until,
            absence_latch=absence_latch,
        )

    def _observe_absence(
        self, config: RoomLightingConfig, context: RoomLightingContext
    ) -> None:
        """Track a completed absence so a returned presence can release."""

        self._observe_absence_sensors(config, context.sensors, context.now)

    def _observe_absence_sensors(
        self, config: RoomLightingConfig, sensors: tuple[SensorSnapshot, ...], now: int
    ) -> None:
        """Use the same absence transition for room snapshots and live fences."""

        relevant = [
            sensor
            for sensor in sensors
            if sensor.kind in _PRESENCE_KINDS
        ]
        if not relevant:
            self._absence.pop(config.room_id, None)
            self._absence_release_latch.discard(config.room_id)
            return
        off_times: list[int] = []
        if any(sensor.state in {SensorState.UNKNOWN, SensorState.UNAVAILABLE}
               or (sensor.state is SensorState.OFF
                   and not sensor_observation_is_fresh(sensor, now, EnginePolicy.from_config(config).staleness_seconds * 1000))
               for sensor in relevant):
            self._absence.pop(config.room_id, None)
            # Sensor uncertainty invalidates the ongoing WINDOW (fail-closed
            # forward), but the already-completed stable absence latch is a
            # historical fact and survives: it is cleared only by a new
            # manual action, not by sensor faults.
            return
        if any(
            sensor.state is SensorState.ON
            and not sensor_observation_is_fresh(sensor, now, EnginePolicy.from_config(config).sensor_freshness_seconds * 1000)
            for sensor in relevant
        ):
            self._absence.pop(config.room_id, None)
            # A stale on-reading invalidates the ongoing WINDOW; the latch
            # (a historical fact) survives, as with other sensor faults.
            return
        held = self._absence.get(config.room_id)
        for sensor in relevant:
            if sensor.state is SensorState.ON:
                # Presence returned: keep the earlier confirmed absence so the
                # manual protection can still release on this new presence —
                # but freeze its end at the proven return moment: the sensor's
                # own ON transition time, not the (possibly delayed) processing
                # time. Post-return processing delay must never stretch a
                # shorter absence, and later evaluations while the person stays
                # must not slide the end forward (SEC-RP5-DEPENDENCY-01).
                if held is not None and held[0]:
                    frozen_end = held[2]
                    if frozen_end is None:
                        on_times = [
                            other.last_changed
                            for other in relevant
                            if other.state is SensorState.ON
                        ]
                        frozen_end = min(on_times)
                    self._absence[config.room_id] = (True, held[1], frozen_end)
                    # A completed stable absence is a historical fact: the
                    # latch sets here too, not only while the room is still
                    # empty. An evaluation gap over the completion moment
                    # (nobody processed between 30s and the return) must not
                    # let the next short window erase the proven window.
                    # The since already includes the start/action floors and
                    # the uncertainty guards above, so only honest windows
                    # reach this branch.
                    if (
                        config.manual_off_protection.enabled
                        and config.manual_off_protection.stable_absence_seconds > 0
                        and frozen_end - held[1]
                        >= config.manual_off_protection.stable_absence_seconds * 1000
                    ):
                        self._absence_release_latch.add(config.room_id)
                return
            off_times.append(sensor.last_changed)
        if not off_times:
            return
        since = max(off_times)
        # An unobserved interval is not absence: a restart makes the confirmed
        # absence window start anew instead of inheriting a stale OFF timestamp.
        # The away→home boundary does the same for the return moment: absence
        # observed before the household came back must not count after it.
        if self._started_at is not None:
            since = max(since, self._started_at)
        if self._absence_floor_ms is not None:
            since = max(since, self._absence_floor_ms)
        # A new manual action restarts the absence evidence: a stale sensor
        # "off" predating the action must not latch the release right after
        # it (SEC-RP6-01).
        manual_floor = self._manual_absence_floor.get(config.room_id)
        if manual_floor is not None:
            since = max(since, manual_floor)
        self._absence[config.room_id] = (True, since, None)
        # A stable completed absence latches the release eligibility: short
        # re-entries must not erase this evidence (the tambur held dark
        # because the latest short window overwrote an 11-minute absence).
        if (
            config.manual_off_protection.enabled
            and config.manual_off_protection.stable_absence_seconds > 0
            and now - since
            >= config.manual_off_protection.stable_absence_seconds * 1000
        ):
            self._absence_release_latch.add(config.room_id)

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
