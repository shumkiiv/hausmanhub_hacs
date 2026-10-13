"""Shadow-mode room lighting: compute and journal, never execute.

The service builds a decision from the deterministic engine, appends a bounded
journal entry and persists it. It advertises ``commands_enabled=False`` and
never calls a physical command executor, so shadow evaluation is safe by
construction.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
import logging
import time
from typing import TYPE_CHECKING, Protocol

from ..domain.room_lighting import RoomLightingConfig
from ..domain.room_lighting_auxiliary import BathroomDecision, BathroomObservation
from ..domain.shower_exhaust import ShowerExhaustDecision, ShowerExhaustObservation
from ..domain.room_lighting_engine import (
    RoomLightingContext,
    RoomLightingDecision,
    evaluate_room_lighting,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

ROOM_LIGHTING_SHADOW_VERSION = 1
MAX_JOURNAL_ENTRIES = 200
MAX_DURABLE_FAILURES = 128
# State events can arrive in bursts; coalesce storage writes instead of
# rewriting the whole bounded journal on every evaluation.
DEFAULT_SAVE_INTERVAL_SECONDS = 1.0


class RoomLightingShadowStore(Protocol):
    """Persistence boundary for bounded shadow journal entries."""

    async def async_load(self) -> object | None: ...

    async def async_save(self, payload: dict[str, object]) -> None: ...


class RoomLightingShadowService:
    """Compute shadow decisions and journal them without any command path."""

    commands_enabled = False

    def __init__(
        self,
        store: RoomLightingShadowStore,
        *,
        executor: object | None = None,
        save_interval_seconds: float = DEFAULT_SAVE_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        # Kept only so callers can pass the future executor; it is never invoked.
        self._executor = executor
        self._entries: list[dict[str, object]] = []
        self._dispatch_failures: dict[str, dict[str, object]] = {}
        self._dispatch_overflow_latch = False
        self._dispatch_overflow_blocked_after_ms: int | None = None
        self._dispatch_overflow_boundary_unknown = False
        self._dispatch_failures_present = False
        self._loaded = False
        self._save_interval = max(0.0, float(save_interval_seconds))
        self._clock = clock
        self._last_save_at: float | None = None
        self._lock = asyncio.Lock()

    @property
    def commands_enabled_flag(self) -> bool:
        return False

    @property
    def executor(self) -> object | None:
        return self._executor

    async def async_load(self) -> None:
        async with self._lock:
            await self._load_locked()

    async def _ensure_loaded_locked(self) -> None:
        if not self._loaded:
            await self._load_locked()

    async def _load_locked(self) -> None:
        payload = await self._store.async_load()
        entries = payload.get("entries") if isinstance(payload, dict) else None
        if isinstance(entries, list):
            self._entries = [
                entry for entry in entries if isinstance(entry, dict)
            ][-MAX_JOURNAL_ENTRIES:]
        else:
            if payload is not None:
                _LOGGER.warning("room lighting shadow journal is damaged; starting empty")
            self._entries = []
        self._dispatch_failures_present = (
            isinstance(payload, dict) and "dispatchFailures" in payload
        )
        failures = payload.get("dispatchFailures") if isinstance(payload, dict) else None
        self._dispatch_failures = {}
        self._dispatch_overflow_latch = False
        self._dispatch_overflow_blocked_after_ms = None
        self._dispatch_overflow_boundary_unknown = False
        failures_malformed = False
        if self._dispatch_failures_present and not isinstance(failures, list):
            failures_malformed = True
        elif isinstance(failures, list):
            records: list[dict[str, object]] = []
            for raw in failures:
                if not isinstance(raw, dict):
                    failures_malformed = True
                    continue
                key = raw.get("key")
                if not isinstance(key, str) or not key:
                    failures_malformed = True
                    continue
                if raw.get("kind") == "overflow":
                    self._dispatch_overflow_latch = True
                    self._merge_overflow_boundary(raw.get("blockedAfterMs"))
                    continue
                if raw.get("kind") not in {"target", "power"}:
                    failures_malformed = True
                    continue
                if not isinstance(raw.get("evidenceRevision"), str):
                    failures_malformed = True
                    continue
                records.append(dict(raw))
            if len(records) > MAX_DURABLE_FAILURES:
                self._dispatch_overflow_latch = True
            capacity = self._failure_capacity()
            for record in records[:capacity]:
                key = record.get("key")
                if isinstance(key, str):
                    self._dispatch_failures[key] = record
            for record in records[capacity:]:
                self._dispatch_overflow_latch = True
                self._merge_overflow_boundary(record.get("blockedAfterMs"))
        if failures_malformed:
            _LOGGER.warning(
                "room lighting durable dispatch retry state is damaged; "
                "blocking automatic retry until fresh trusted evidence"
            )
            self._dispatch_overflow_latch = True
            self._merge_overflow_boundary(None)
        self._loaded = True
        self._last_save_at = None

    def _snapshot_locked(self) -> tuple[
        list[dict[str, object]],
        dict[str, dict[str, object]],
        bool,
        int | None,
        bool,
        bool,
        bool,
        float | None,
    ]:
        return (
            list(self._entries),
            dict(self._dispatch_failures),
            self._dispatch_overflow_latch,
            self._dispatch_overflow_blocked_after_ms,
            self._dispatch_overflow_boundary_unknown,
            self._dispatch_failures_present,
            self._loaded,
            self._last_save_at,
        )

    def _restore_locked(
        self,
        snapshot: tuple[
            list[dict[str, object]],
            dict[str, dict[str, object]],
            bool,
            int | None,
            bool,
            bool,
            bool,
            float | None,
        ],
    ) -> None:
        (
            self._entries,
            self._dispatch_failures,
            self._dispatch_overflow_latch,
            self._dispatch_overflow_blocked_after_ms,
            self._dispatch_overflow_boundary_unknown,
            self._dispatch_failures_present,
            self._loaded,
            self._last_save_at,
        ) = snapshot

    async def _save_document_locked(self, payload: dict[str, object]) -> None:
        saved = await self._store.async_save(payload)
        if saved is False:
            raise RuntimeError("room lighting shadow state was not persisted")

    async def async_evaluate(
        self,
        config: RoomLightingConfig,
        context: RoomLightingContext,
    ) -> RoomLightingDecision:
        """Compute one shadow decision, journal it and return it."""

        decision = evaluate_room_lighting(config, context)
        await self.async_record(decision, commands_enabled=False)
        return decision

    async def async_record(
        self,
        decision: RoomLightingDecision,
        *,
        commands_enabled: bool = False,
        operation_id: str | None = None,
        config_version: int | None = None,
        dispatch_block_reason: str | None = None,
    ) -> None:
        """Journal an already computed decision with the honest command flag.

        The shadow service still never executes anything; the flag only
        describes whether the enclosing runtime was allowed to dispatch.
        """

        enabled = bool(commands_enabled)
        mode = "live" if enabled else "shadow"
        entry: dict[str, object] = {
            "at": decision.evaluated_at,
            "roomId": decision.room_id,
            "mode": mode,
            "commandsEnabled": enabled,
            "commands": [
                {
                    "targetId": command.target_id,
                    "action": command.action.value,
                    "brightness": command.brightness,
                    "colorTemperature": command.color_temperature,
                    "reason": command.reason.value,
                }
                for command in decision.commands
            ],
            "skips": [
                {
                    "targetId": skip.target_id,
                    "reason": skip.reason.value,
                    "detail": skip.detail,
                }
                for skip in decision.skips
            ],
        }
        if operation_id is not None:
            entry["operationId"] = operation_id
        if config_version is not None:
            entry["configVersion"] = config_version
        if dispatch_block_reason is not None:
            entry["dispatchBlockReason"] = dispatch_block_reason
        async with self._lock:
            await self._ensure_loaded_locked()
            snapshot = self._snapshot_locked()
            self._entries.append(entry)
            self._entries = self._entries[-MAX_JOURNAL_ENTRIES:]
            try:
                await self._save_if_due_locked(enabled)
            except Exception:
                self._restore_locked(snapshot)
                raise

    async def async_record_dispatch(
        self,
        *,
        at: int,
        room_id: str,
        receipts: Sequence[Mapping[str, object]],
        operation_id: str | None = None,
        config_version: int | None = None,
    ) -> None:
        """Journal one live dispatch round with one receipt per planned command.

        The plan entry already records what the engine wanted. This entry adds
        what actually happened for each target: whether the command was sent,
        whether the device confirmed it, why it was blocked and whether
        automatic ownership changed.
        """

        if not receipts:
            return
        persisted_receipts = [
            {
                key: value
                for key, value in dict(receipt).items()
                if not str(key).startswith("_")
            }
            for receipt in receipts
        ]
        for receipt in persisted_receipts:
            power = receipt.get("power")
            if isinstance(power, Mapping):
                receipt["power"] = {
                    key: value for key, value in power.items()
                    if not str(key).startswith("_")
                }
        entry: dict[str, object] = {
            "at": at,
            "roomId": room_id,
            "mode": "live",
            "commandsEnabled": True,
            "dispatch": persisted_receipts,
        }
        if operation_id is not None:
            entry["operationId"] = operation_id
        if config_version is not None:
            entry["configVersion"] = config_version
        async with self._lock:
            await self._ensure_loaded_locked()
            snapshot = self._snapshot_locked()
            self._entries.append(entry)
            self._entries = self._entries[-MAX_JOURNAL_ENTRIES:]
            self._apply_dispatch_receipts(room_id, receipts, at=at)
            try:
                # Dispatch receipts carry the retry block; write them immediately
                # even when the preceding plan entry was saved inside coalescing.
                await self._save_document_locked(
                    self._document_with_capacity_locked(True)
                )
            except Exception:
                self._restore_locked(snapshot)
                raise
            self._dispatch_failures_present = True
            self._last_save_at = self._clock()

    async def async_record_pending_dispatch(
        self,
        *,
        at: int,
        room_id: str,
        target_id: str,
        action: str,
        evidence_revision: str,
        operation_id: str | None = None,
        config_version: int | None = None,
    ) -> None:
        """Persist a pre-send retry block before crossing HA service boundary."""

        key = f"target:{room_id}:{target_id}"
        entry: dict[str, object] = {
            "at": at,
            "roomId": room_id,
            "mode": "live",
            "commandsEnabled": True,
            "dispatchPending": {
                "targetId": target_id,
                "action": action,
                "evidenceRevision": evidence_revision,
                "reason": "pending_dispatch",
            },
        }
        if operation_id is not None:
            entry["operationId"] = operation_id
        if config_version is not None:
            entry["configVersion"] = config_version
        async with self._lock:
            await self._ensure_loaded_locked()
            snapshot = self._snapshot_locked()
            marker = {
                "key": key,
                "kind": "target",
                "roomId": room_id,
                "targetId": target_id,
                "action": action,
                "evidenceRevision": evidence_revision,
            }
            if isinstance(at, int) and not isinstance(at, bool) and at > 0:
                # HH-014.b: the pre-send marker carries the exact attempt
                # boundary so a crash before the receipt keeps it.
                marker["blockedAfterMs"] = at
                marker["attemptBoundary"] = True
            marker["reason"] = "pending_dispatch"
            self._remember_dispatch_failure(key, marker)
            self._entries.append(entry)
            self._entries = self._entries[-MAX_JOURNAL_ENTRIES:]
            try:
                await self._save_document_locked(
                    self._document_with_capacity_locked(True)
                )
            except Exception:
                self._restore_locked(snapshot)
                raise
            self._dispatch_failures_present = True
            self._last_save_at = self._clock()

    async def async_record_pending_power_dispatch(
        self,
        *,
        at: int,
        room_id: str,
        source_entity_id: str,
        evidence_revision: str,
        operation_id: str | None = None,
        config_version: int | None = None,
    ) -> None:
        """Persist a pre-send retry block for an upstream power command."""

        key = f"power:{source_entity_id}"
        entry: dict[str, object] = {
            "at": at,
            "roomId": room_id,
            "mode": "live",
            "commandsEnabled": True,
            "dispatchPending": {
                "kind": "power",
                "sourceEntityId": source_entity_id,
                "action": "turn_on",
                "evidenceRevision": evidence_revision,
                "reason": "pending_power_dispatch",
            },
        }
        if operation_id is not None:
            entry["operationId"] = operation_id
        if config_version is not None:
            entry["configVersion"] = config_version
        async with self._lock:
            await self._ensure_loaded_locked()
            snapshot = self._snapshot_locked()
            self._remember_dispatch_failure(
                key,
                {
                    "key": key,
                    "kind": "power",
                    "roomId": room_id,
                    "entityId": source_entity_id,
                    "action": "turn_on",
                    "evidenceRevision": evidence_revision,
                    "blockedAfterMs": at,
                    "reason": "pending_power_dispatch",
                },
            )
            self._entries.append(entry)
            self._entries = self._entries[-MAX_JOURNAL_ENTRIES:]
            try:
                await self._save_document_locked(
                    self._document_with_capacity_locked(True)
                )
            except Exception:
                self._restore_locked(snapshot)
                raise
            self._dispatch_failures_present = True
            self._last_save_at = self._clock()

    def _apply_dispatch_receipts(
        self, room_id: str, receipts: Sequence[Mapping[str, object]], *, at: int
    ) -> None:
        """Maintain restart-safe retry blocks outside the bounded journal."""

        for raw in receipts:
            target_id = raw.get("targetId")
            evidence_revision = raw.get("evidenceRevision")
            if isinstance(target_id, str) and isinstance(evidence_revision, str):
                key = f"target:{room_id}:{target_id}"
                if raw.get("confirmed") is True:
                    self._dispatch_failures.pop(key, None)
                elif (
                    raw.get("dispatchAttempt") is False
                    and raw.get("sendOutcome") == "not_sent"
                    and raw.get("reason") != "unconfirmed_attempt"
                ):
                    # Honest pre-boundary rejections clear the pre-send marker;
                    # they must not turn into a durable unconfirmed-attempt block.
                    self._dispatch_failures.pop(key, None)
                    superseded = raw.get("_supersededFailure")
                    if isinstance(superseded, Mapping) and str(
                        superseded.get("action") or ""
                    ):
                        # A safe planned off that superseded another action but
                        # was never sent must not erase that older protection.
                        superseded_entry = {
                            "key": key,
                            "kind": "target",
                            "roomId": room_id,
                            "targetId": target_id,
                            "action": str(superseded.get("action") or ""),
                            "evidenceRevision": str(
                                superseded.get("evidenceRevision") or ""
                            ),
                        }
                        superseded_anchor = superseded.get("anchorMs")
                        if (
                            isinstance(superseded_anchor, int)
                            and not isinstance(superseded_anchor, bool)
                            and superseded_anchor > 0
                        ):
                            # The superseded latch keeps its own exact attempt
                            # boundary across restarts (HH-014.b). A missing
                            # or invalid anchor stays unknown (AUD-LATCH-03).
                            superseded_entry["blockedAfterMs"] = superseded_anchor
                            superseded_entry["attemptBoundary"] = True
                        # AUD-LATCH-03: an unknown boundary stays unknown — the
                        # failure time must not be invented as the boundary.
                        self._remember_dispatch_failure(key, superseded_entry)
                elif raw.get("dispatchAttempt") is True:
                    # HH-014.b: the durable boundary is the attempt boundary
                    # captured immediately before the dispatch, not the room
                    # processing start — a report between the two must not
                    # release the restored latch. Entries WITH a valid
                    # _attemptAt carry the exact attempt boundary
                    # ("attemptBoundary": true). A receipt WITHOUT
                    # _attemptAt comes from an older producer whose pass time
                    # is not a provable attempt boundary: its entry is saved
                    # WITHOUT a boundary (unknown → fail-closed for turn_on
                    # with a bounded live-report release), never inventing
                    # one from the pass time. A PRESENT but invalid
                    # _attemptAt stays unknown for the same reason.
                    entry = {
                        "key": key,
                        "kind": "target",
                        "roomId": room_id,
                        "targetId": target_id,
                        "action": str(raw.get("action") or ""),
                        "evidenceRevision": evidence_revision,
                    }
                    attempt_at = raw.get("_attemptAt")
                    if (
                        isinstance(attempt_at, int)
                        and not isinstance(attempt_at, bool)
                        and attempt_at > 0
                    ):
                        entry["blockedAfterMs"] = attempt_at
                        entry["attemptBoundary"] = True
                    self._remember_dispatch_failure(key, entry)
            power = raw.get("power")
            if not isinstance(power, Mapping):
                continue
            if power.get("_durable_recorded") is True:
                # The source lock already committed this part. A later room
                # receipt must not overwrite a newer result from another room.
                continue
            source = power.get("sourceEntityId")
            power_revision = power.get("evidenceRevision")
            if not isinstance(source, str) or not isinstance(power_revision, str):
                continue
            power_key = f"power:{source}"
            if power.get("reason") == "power_source_on":
                self._dispatch_failures.pop(power_key, None)
                continue
            previous = power.get("_preservedFailure")
            if (
                power.get("dispatchAttempt") is False
                and power.get("sendOutcome") == "not_sent"
                and isinstance(previous, Mapping)
            ):
                self._remember_dispatch_failure(power_key, dict(previous))
                continue
            if (
                power.get("dispatchAttempt") is False
                and power.get("sendOutcome") == "not_sent"
                and power.get("reason") != "power_source_unconfirmed"
                and power.get("deliveryEpisodeOpen") is not True
            ):
                self._dispatch_failures.pop(power_key, None)
                continue
            if power.get("deliveryEpisodeOpen") is True:
                # AUD-POWER-PERSIST-02 (luna 2026-10-12): an open delivery
                # episode keeps its durable latch across the not-sent
                # closure; a restart restores it fail-closed (exhausted)
                # until fresh trusted relay evidence arrives.
                self._remember_dispatch_failure(
                    power_key,
                    {
                        "key": power_key,
                        "kind": "power",
                        "entityId": source,
                        "action": str(power.get("action") or "turn_on"),
                        "evidenceRevision": power_revision,
                        "blockedAfterMs": at,
                    },
                )
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
                attempt_at = power.get("_attemptAt")
                boundary = (
                    attempt_at
                    if isinstance(attempt_at, int) and not isinstance(attempt_at, bool)
                    and attempt_at > 0 else at
                )
                self._remember_dispatch_failure(
                    power_key,
                    {
                        "key": power_key,
                        "kind": "power",
                        "entityId": source,
                        "action": "turn_on",
                        "evidenceRevision": power_revision,
                        "blockedAfterMs": boundary,
                    },
                )

    def _failure_capacity(self) -> int:
        """Reserve one bounded slot for the fail-closed overflow latch."""

        return MAX_DURABLE_FAILURES - (1 if self._dispatch_overflow_latch else 0)

    def _enforce_failure_capacity(self) -> None:
        """Keep load and save under one durable capacity invariant.

        The persisted list has at most ``MAX_DURABLE_FAILURES`` items total.
        When the overflow latch is present it consumes one slot, and every
        displaced record contributes its last failed-at boundary to that latch.
        """

        capacity = self._failure_capacity()
        while len(self._dispatch_failures) > capacity:
            _key, dropped = self._dispatch_failures.popitem()
            self._dispatch_overflow_latch = True
            self._merge_overflow_boundary(dropped.get("blockedAfterMs"))

    def _remember_dispatch_failure(
        self, key: str, record: dict[str, object]
    ) -> None:
        """Store a retry block without FIFO-evicting another active block.

        If the bounded durable map is full, a single overflow latch is persisted
        instead. Runtime recovery treats targets without a specific record as
        fail-closed until they have trusted, fresh, non-restored evidence, so an
        evicted entry cannot become a blind retry.
        """

        if key in self._dispatch_failures:
            self._dispatch_failures[key] = record
            return
        if len(self._dispatch_failures) >= self._failure_capacity():
            self._dispatch_overflow_latch = True
            self._merge_overflow_boundary(record.get("blockedAfterMs"))
            self._enforce_failure_capacity()
            return
        self._dispatch_failures[key] = record

    def _merge_overflow_boundary(self, value: object) -> None:
        """Keep the newest failed attempt boundary for missing overflow keys.

        ``None`` means fail-closed for old payloads that only had a latch and no
        monotonic boundary.  Once the boundary is unknown, later guesses must not
        make the latch permissive.
        """

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
                self._dispatch_overflow_blocked_after_ms, value
            )

    def _dispatch_failure_payload(self) -> list[dict[str, object]]:
        failures = list(self._dispatch_failures.values())[: self._failure_capacity()]
        if self._dispatch_overflow_latch:
            overflow: dict[str, object] = {
                "key": "overflow:dispatchFailures",
                "kind": "overflow",
                "reason": "dispatch_failures_overflow_fail_closed",
            }
            if self._dispatch_overflow_blocked_after_ms is not None:
                overflow["blockedAfterMs"] = self._dispatch_overflow_blocked_after_ms
            failures.append(overflow)
        return failures

    def _document_with_capacity_locked(
        self, commands_enabled: bool | None = None
    ) -> dict[str, object]:
        self._enforce_failure_capacity()
        return self._document(commands_enabled)

    async def async_record_auxiliary(
        self,
        *,
        at: int,
        room_id: str,
        observation: BathroomObservation,
        decision: BathroomDecision,
    ) -> None:
        """Journal one pure bathroom exhaust decision; never execute.

        The auxiliary engine has no dispatch path yet, so every auxiliary
        entry is shadow by construction: it records exactly what the engine
        computed from the live observation, including the band, the fan state
        and the proven ownership. The mode/flag stay ``shadow`` even for a
        room whose lighting commands are live.
        """

        entry = {
            "at": at,
            "roomId": room_id,
            "mode": "shadow",
            "commandsEnabled": False,
            "auxiliary": {
                "band": observation.band.value,
                "lights": [state.value for state in observation.lights],
                "humidity": observation.humidity,
                "fan": observation.fan.value,
                "fanOwned": observation.fan_owned,
                "allowActivation": observation.allow_activation,
                "pendingTimer": observation.pending_timer,
                "transition": decision.transition.value,
                "action": (
                    decision.action.value if decision.action is not None else None
                ),
                "armTimerSeconds": decision.arm_timer_seconds,
                "clearTimer": decision.clear_timer,
                "blocked": decision.blocked,
            },
        }
        async with self._lock:
            await self._ensure_loaded_locked()
            snapshot = self._snapshot_locked()
            self._entries.append(entry)
            self._entries = self._entries[-MAX_JOURNAL_ENTRIES:]
            try:
                await self._save_if_due_locked(False)
            except Exception:
                self._restore_locked(snapshot)
                raise

    async def async_record_exhaust(
        self,
        *,
        at: int,
        room_id: str,
        observation: ShowerExhaustObservation,
        decision: ShowerExhaustDecision,
        enabled: bool = False,
    ) -> None:
        """Journal one shower exhaust decision with its honest command flag."""

        mode = "live" if enabled else "shadow"
        entry = {
            "at": at,
            "roomId": room_id,
            "mode": mode,
            "commandsEnabled": enabled,
            "showerExhaust": {
                "presence": observation.presence.value,
                "humidity": observation.humidity,
                "fan": observation.fan.value,
                "fanOwned": observation.fan_owned,
                "lightsOwnedOn": observation.lights_owned_on,
                "pendingTimer": observation.pending_timer,
                "transition": decision.transition.value,
                "action": (
                    decision.action.value if decision.action is not None else None
                ),
                "armTimer": (
                    decision.arm_timer.value if decision.arm_timer is not None else None
                ),
                "armSeconds": decision.arm_seconds,
                "blocked": decision.blocked,
            },
        }
        async with self._lock:
            await self._ensure_loaded_locked()
            snapshot = self._snapshot_locked()
            self._entries.append(entry)
            self._entries = self._entries[-MAX_JOURNAL_ENTRIES:]
            try:
                await self._save_if_due_locked(enabled)
            except Exception:
                self._restore_locked(snapshot)
                raise

    async def async_flush(self) -> None:
        """Force a write, for example before shutdown."""

        async with self._lock:
            await self._ensure_loaded_locked()
            snapshot = self._snapshot_locked()
            try:
                await self._save_document_locked(self._document_with_capacity_locked())
            except Exception:
                self._restore_locked(snapshot)
                raise
            self._dispatch_failures_present = True
            self._last_save_at = self._clock()

    async def _save_if_due_locked(self, enabled: bool) -> None:
        now = self._clock()
        if (
            self._last_save_at is not None
            and now - self._last_save_at < self._save_interval
        ):
            return
        await self._save_document_locked(self._document_with_capacity_locked(enabled))
        self._dispatch_failures_present = True
        self._last_save_at = now

    def _document(self, commands_enabled: bool | None = None) -> dict[str, object]:
        if commands_enabled is None:
            entry = self._entries[-1] if self._entries else None
            enabled = bool(entry.get("commandsEnabled")) if entry else False
        else:
            enabled = bool(commands_enabled)
        return {
            "version": ROOM_LIGHTING_SHADOW_VERSION,
            "mode": "live" if enabled else "shadow",
            "commandsEnabled": enabled,
            "entries": self._entries,
            "dispatchFailures": self._dispatch_failure_payload(),
        }

    def journal_payload(self) -> dict[str, object]:
        document: dict[str, object] = {
            "version": ROOM_LIGHTING_SHADOW_VERSION,
            "mode": "shadow",
            "commandsEnabled": False,
            "entries": list(self._entries),
        }
        if (
            self._dispatch_failures_present
            or self._dispatch_failures
            or self._dispatch_overflow_latch
        ):
            document["dispatchFailures"] = self._dispatch_failure_payload()
        return document


class HomeAssistantRoomLightingShadowStore:
    """Persist bounded shadow journal entries for one config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        from homeassistant.helpers.storage import Store

        self._store: Store[dict[str, object]] = Store(
            hass,
            ROOM_LIGHTING_SHADOW_VERSION,
            f"hausman_hub.room_lighting_shadow.{entry_id}",
        )

    async def async_load(self) -> object | None:
        return await self._store.async_load()

    async def async_save(self, payload: dict[str, object]) -> None:
        await self._store.async_save(payload)
