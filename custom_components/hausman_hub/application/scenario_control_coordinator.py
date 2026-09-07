"""Durable server coordinator for consolidated managed controllers."""

from __future__ import annotations

import asyncio
import copy
import math
import time
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime

from ..domain.scenario_controls import (
    OccupancyEvidence,
    ScenarioControlDocument,
    scenario_control_policy_to_payload,
)

STORAGE_SCENARIO_ID = "system-storage-light-controller"
STORAGE_LIGHT_TARGET_ID = "entity_0ec37ef18b4b39a6"
STORAGE_MOTION_TARGET_ID = "entity_00dcf0ebdc0bc6cb"
_TRANSITIONS = frozenset(
    {
        "idle",
        "occupied_hold",
        "occupied_light_unknown",
        "storage_light_on",
        "storage_light_on_failed",
        "absence_pending",
        "manual_light_hold",
        "occupancy_unknown",
        "storage_light_off_due",
        "storage_light_off_failed",
        "storage_exhaust_unbound",
        "storage_exhaust_on",
        "storage_exhaust_running",
        "storage_exhaust_off",
        "storage_exhaust_failed",
        "policy_changed",
        "stale_generation",
    }
)


def _empty_storage_record(policy_revision: int) -> dict[str, object]:
    return {
        "generation": 0,
        "policyRevision": policy_revision,
        "evidence": {
            "motion": None,
            "presence": None,
            "light": None,
            "ownershipRevision": None,
        },
        "absenceStartedAtMs": None,
        "deadlineMs": None,
        "exhaustDeadlineMs": None,
        "transition": "idle",
        "fractionalRemainder": 0.0,
        "correlationId": None,
    }


def _validated_storage_record(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping) or set(value) != {
        "generation",
        "policyRevision",
        "evidence",
        "absenceStartedAtMs",
        "deadlineMs",
        "exhaustDeadlineMs",
        "transition",
        "fractionalRemainder",
        "correlationId",
    }:
        return None
    if (
        type(value.get("generation")) is not int
        or not 0 <= int(value["generation"]) <= 2**31 - 1
        or type(value.get("policyRevision")) is not int
        or not 0 <= int(value["policyRevision"]) <= 2**31 - 1
        or value.get("transition") not in _TRANSITIONS
    ):
        return None
    evidence = value.get("evidence")
    if not isinstance(evidence, Mapping) or set(evidence) != {
        "motion", "presence", "light", "ownershipRevision"
    }:
        return None
    if any(
        item is not None and (not isinstance(item, str) or len(item) > 256)
        for item in evidence.values()
    ):
        return None
    for key in ("absenceStartedAtMs", "deadlineMs", "exhaustDeadlineMs"):
        item = value.get(key)
        if item is not None and (
            type(item) is not int or not 0 <= item <= 2**63 - 1
        ):
            return None
    remainder = value.get("fractionalRemainder")
    if (
        not isinstance(remainder, (int, float))
        or isinstance(remainder, bool)
        or not math.isfinite(float(remainder))
        or not 0 <= float(remainder) < 1
    ):
        return None
    correlation = value.get("correlationId")
    if correlation is not None and (
        not isinstance(correlation, str) or not correlation or len(correlation) > 128
    ):
        return None
    record = copy.deepcopy(dict(value))
    record["evidence"] = copy.deepcopy(dict(evidence))
    record["fractionalRemainder"] = float(remainder)
    return record


def valid_scenario_control_state_payload(value: object) -> bool:
    """Validate the exact single-record controller state document."""

    return bool(
        isinstance(value, Mapping)
        and set(value) == {"version", "storage"}
        and value.get("version") == 1
        and _validated_storage_record(value.get("storage")) is not None
    )


class ScenarioControlCoordinator:
    """Own durable generations while Node-RED selects typed actions only."""

    def __init__(
        self,
        hass: object,
        scenario_service: object,
        policy_service: object,
        store: object,
        light_priority: object,
        *,
        catalog_resolver: Callable[[str], object | None],
        storage_motion_target_id: str | None = STORAGE_MOTION_TARGET_ID,
        storage_presence_target_id: str | None = None,
        now_ms: Callable[[], int] | None = None,
        schedule_tasks: bool = True,
    ) -> None:
        self._hass = hass
        self._scenario_service = scenario_service
        self._policy_service = policy_service
        self._store = store
        self._light_priority = light_priority
        self._catalog_resolver = catalog_resolver
        self._storage_motion_target_id = storage_motion_target_id
        self._storage_presence_target_id = storage_presence_target_id
        self._now_ms = now_ms or (lambda: time.time_ns() // 1_000_000)
        self._schedule_tasks = schedule_tasks
        self._storage = _empty_storage_record(0)
        self._light_task: asyncio.Task[None] | None = None
        self._exhaust_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._decision_lock = asyncio.Lock()
        self._remove_policy_observer: Callable[[], None] | None = None
        self._schedule_unsubs: list[Callable[[], None]] = []
        self._activation_latch: object | None = None
        self._started = False

    @property
    def owned_scenario_ids(self) -> frozenset[str]:
        """Scenario IDs whose device and clock triggers are coordinated here."""

        return frozenset({STORAGE_SCENARIO_ID})

    async def async_load(self) -> None:
        document = self._policy_service.current
        payload = await self._store.async_load()
        if payload is None:
            self._storage = _empty_storage_record(document.policy_revision)
            await self._save()
        elif not valid_scenario_control_state_payload(payload):
            raise RuntimeError("scenario control state storage is invalid")
        else:
            record = _validated_storage_record(payload["storage"])
            assert record is not None
            self._storage = record
        if getattr(self._store, "recovered_previous", False):
            self._storage = _empty_storage_record(document.policy_revision)
            self._storage["transition"] = "stale_generation"
            await self._save()
        elif self._storage["policyRevision"] != document.policy_revision:
            self._storage = self._next_record(
                transition="policy_changed",
                policy_revision=document.policy_revision,
                clear_absence=True,
                clear_exhaust=True,
            )
            await self._save()
        self._remove_policy_observer = self._policy_service.add_observer(
            self._async_policy_changed
        )

    async def async_start(self, entry: object, activation_latch: object) -> None:
        """Attach real HA events and exact policy-owned schedule callbacks."""

        if self._started:
            raise RuntimeError("scenario control coordinator is already started")
        self._activation_latch = activation_latch
        bus = getattr(self._hass, "bus", None)
        if bus is None:
            raise RuntimeError("Home Assistant event bus is unavailable")

        async def state_changed(event: object) -> None:
            if not self._is_active():
                return
            data = getattr(event, "data", {})
            entity_id = data.get("entity_id") if isinstance(data, Mapping) else None
            if entity_id not in self._storage_entity_ids():
                return
            await self.async_handle_storage_change()

        unsubscribe = bus.async_listen("state_changed", state_changed)
        getattr(entry, "async_on_unload")(unsubscribe)
        getattr(entry, "async_on_unload")(self.stop_runtime)
        self._started = True
        self._rearm_exhaust_schedule()

    def activate(self) -> None:
        """Reconcile durable state once the shared activation latch is open."""

        async def reconcile() -> None:
            if not self._is_active():
                return
            if type(self._storage.get("deadlineMs")) is int:
                await self.async_reconcile_storage_startup()
            else:
                await self.async_handle_storage_change()
            deadline = self._storage.get("exhaustDeadlineMs")
            if type(deadline) is int:
                if deadline <= self._now_ms():
                    await self.async_reconcile_storage_exhaust_due()
                else:
                    self._schedule_exhaust_due()

        create_task = getattr(self._hass, "async_create_task", None)
        coroutine = reconcile()
        if callable(create_task):
            create_task(coroutine)
        else:
            asyncio.create_task(coroutine)

    @property
    def storage_state(self) -> dict[str, object]:
        return copy.deepcopy(self._storage)

    @property
    def payload(self) -> dict[str, object]:
        return {"version": 1, "storage": self.storage_state}

    @property
    def storage_remaining_seconds(self) -> int | None:
        deadline = self._storage.get("deadlineMs")
        if type(deadline) is not int:
            return None
        return max(0, math.ceil((deadline - self._now_ms()) / 1000))

    async def async_control_context(
        self,
        scenario_id: str,
        run_id: str,
        trigger: Mapping[str, object],
    ) -> dict[str, object]:
        """Return only server state, never fields from the trigger body."""

        del trigger
        document: ScenarioControlDocument = self._policy_service.current
        policy = scenario_control_policy_to_payload(document.policy)
        if scenario_id != STORAGE_SCENARIO_ID:
            state: dict[str, object] = {
                "ready": False,
                "transition": "incomplete",
                "correlationId": run_id,
            }
        else:
            state = self.storage_state
            matching = (
                state.get("correlationId") == run_id
                and state.get("policyRevision") == document.policy_revision
            )
            absence_started = state.get("absenceStartedAtMs")
            state["absenceConfirmed"] = bool(
                type(absence_started) is int
                and self._now_ms() - absence_started
                >= document.policy.absence_confirmation_seconds * 1000
            )
            state["ready"] = matching
            if not matching:
                state["transition"] = "stale_generation"
        return {
            "policyRevision": document.policy_revision,
            "policy": policy,
            "state": state,
        }

    async def async_handle_storage_change(self) -> None:
        """Re-read both occupancy types and derive one durable transition."""

        async with self._decision_lock:
            await self._async_handle_storage_change()

    async def _async_handle_storage_change(self) -> None:

        motion, presence, light, entity_id = self._storage_evidence()
        evidence = OccupancyEvidence.from_states(motion, presence)
        if evidence is OccupancyEvidence.OCCUPIED:
            self._cancel_light_task()
            if light == "off":
                if self._storage.get("transition") == "storage_light_on_failed":
                    return
                result = await self._run_storage_transition(
                    "storage_light_on",
                    evidence=self._evidence_payload(motion, presence, light, None),
                    clear_absence=True,
                )
                if not (
                    result.get("confirmed") is True
                    and result.get("status") == "completed"
                ):
                    await self._set_transition(
                        "storage_light_on_failed",
                        evidence=self._evidence_payload(
                            motion, presence, light, None
                        ),
                        clear_absence=True,
                    )
            else:
                await self._set_transition(
                    "occupied_hold" if light == "on" else "occupied_light_unknown",
                    evidence=self._evidence_payload(motion, presence, light, None),
                    clear_absence=True,
                )
            return
        if evidence is OccupancyEvidence.UNKNOWN:
            self._cancel_light_task()
            await self._set_transition(
                "occupancy_unknown",
                evidence=self._evidence_payload(motion, presence, light, None),
                clear_absence=True,
            )
            return
        ownership_revision = (
            self._light_priority.ownership_revision(entity_id, self._hass)
            if entity_id is not None and light == "on"
            else None
        )
        owned = bool(
            entity_id is not None
            and light == "on"
            and self._light_priority.is_owned(entity_id, self._hass)
            and ownership_revision is not None
        )
        if not owned:
            self._cancel_light_task()
            await self._set_transition(
                "manual_light_hold" if light == "on" else "idle",
                evidence=self._evidence_payload(
                    motion, presence, light, ownership_revision
                ),
                clear_absence=True,
            )
            return
        if (
            self._storage.get("transition") == "storage_light_off_failed"
            and self._storage.get("evidence", {}).get("ownershipRevision")
            == ownership_revision
        ):
            return
        current_start = self._storage.get("absenceStartedAtMs")
        current_deadline = self._storage.get("deadlineMs")
        current_policy = self._policy_service.current
        if (
            type(current_start) is int
            and type(current_deadline) is int
            and self._storage.get("policyRevision") == current_policy.policy_revision
        ):
            self._schedule_light_due()
            return
        started = self._now_ms()
        await self._set_transition(
            "absence_pending",
            evidence=self._evidence_payload(
                motion, presence, light, ownership_revision
            ),
            absence_started_at_ms=started,
            deadline_ms=(
                started + current_policy.policy.storage_absence_seconds * 1000
            ),
        )
        self._schedule_light_due()

    async def async_reconcile_storage_startup(self) -> None:
        """Re-read sensors and existing ownership before resuming a deadline."""

        async with self._decision_lock:
            await self._async_reconcile_storage_startup()

    async def _async_reconcile_storage_startup(self) -> None:

        if type(self._storage.get("deadlineMs")) is not int:
            return
        original_started = self._storage.get("absenceStartedAtMs")
        original_deadline = self._storage.get("deadlineMs")
        original_generation = self._storage.get("generation")
        original_revision = self._storage.get("policyRevision")
        original_ownership = self._storage.get("evidence", {}).get(
            "ownershipRevision"
        )
        motion, presence, light, entity_id = self._storage_evidence()
        current_ownership = (
            self._light_priority.ownership_revision(entity_id, self._hass)
            if entity_id is not None
            else None
        )
        if (
            OccupancyEvidence.from_states(motion, presence)
            is not OccupancyEvidence.ABSENT
            or light != "on"
            or entity_id is None
            or not self._light_priority.is_owned(entity_id, self._hass)
            or current_ownership is None
            or current_ownership != original_ownership
            or original_revision != self._policy_service.current.policy_revision
            or type(original_started) is not int
            or type(original_deadline) is not int
            or type(original_generation) is not int
        ):
            await self._async_handle_storage_change()
            return
        if original_deadline <= self._now_ms():
            await self._async_reconcile_storage_due()
        else:
            self._schedule_light_due()

    async def async_reconcile_storage_due(self) -> None:
        """Turn off only the still-owned light at the original deadline."""

        async with self._decision_lock:
            await self._async_reconcile_storage_due()

    async def _async_reconcile_storage_due(self) -> None:

        deadline = self._storage.get("deadlineMs")
        if (
            type(deadline) is not int
            or self._now_ms() < deadline
        ):
            return
        motion, presence, light, entity_id = self._storage_evidence()
        stored_ownership = self._storage.get("evidence", {}).get(
            "ownershipRevision"
        )
        current_ownership = (
            self._light_priority.ownership_revision(entity_id, self._hass)
            if entity_id is not None
            else None
        )
        if (
            OccupancyEvidence.from_states(motion, presence)
            is not OccupancyEvidence.ABSENT
            or light != "on"
            or entity_id is None
            or not self._light_priority.is_owned(entity_id, self._hass)
            or current_ownership is None
            or current_ownership != stored_ownership
            or self._storage.get("policyRevision")
            != self._policy_service.current.policy_revision
        ):
            await self._async_handle_storage_change()
            return
        result = await self._run_storage_transition(
            "storage_light_off_due",
            evidence=self._evidence_payload(
                motion, presence, light, current_ownership
            ),
        )
        if result.get("confirmed") is True and result.get("status") == "completed":
            await self._set_transition(
                "idle",
                evidence=self._evidence_payload(motion, presence, "off", None),
                clear_absence=True,
            )
        else:
            await self._set_transition(
                "storage_light_off_failed",
                clear_absence=True,
            )

    async def async_handle_storage_exhaust_schedule(self, clock: str) -> str:
        """Run one fixed schedule or record a local unbound skip."""

        async with self._decision_lock:
            return await self._async_handle_storage_exhaust_schedule(clock)

    async def _async_handle_storage_exhaust_schedule(self, clock: str) -> str:

        document = self._policy_service.current
        if clock not in document.policy.storage_exhaust_times:
            return "not_scheduled"
        target_id = document.policy.storage_exhaust_target_id
        if target_id is None:
            await self._run_storage_transition("storage_exhaust_unbound")
            return "unbound"
        result = await self._run_storage_transition("storage_exhaust_on")
        if result.get("confirmed") is True and result.get("status") == "completed":
            deadline = self._now_ms() + document.policy.storage_exhaust_run_seconds * 1000
            await self._set_transition(
                "storage_exhaust_running", exhaust_deadline_ms=deadline
            )
            self._schedule_exhaust_due()
            return "started"
        await self._set_transition("storage_exhaust_failed", clear_exhaust=True)
        return "failed"

    async def async_reconcile_storage_exhaust_due(self) -> None:
        async with self._decision_lock:
            await self._async_reconcile_storage_exhaust_due()

    async def _async_reconcile_storage_exhaust_due(self) -> None:
        deadline = self._storage.get("exhaustDeadlineMs")
        if (
            type(deadline) is not int
            or self._now_ms() < deadline
        ):
            return
        result = await self._run_storage_transition("storage_exhaust_off")
        await self._set_transition(
            "idle"
            if result.get("confirmed") is True and result.get("status") == "completed"
            else "storage_exhaust_failed",
            clear_exhaust=True,
        )

    async def _run_storage_transition(
        self,
        transition: str,
        *,
        evidence: Mapping[str, object] | None = None,
        clear_absence: bool = False,
    ) -> dict[str, object]:
        correlation = f"storage.{uuid.uuid4().hex}"
        await self._set_transition(
            transition,
            evidence=evidence,
            clear_absence=clear_absence,
            correlation_id=correlation,
        )
        try:
            result = await self._scenario_service.async_run_scenario(
                STORAGE_SCENARIO_ID,
                correlation_id=correlation,
                trigger_context={
                    "source": "scenario_control",
                    "trigger_id": transition,
                    "recovery": False,
                },
            )
        except Exception:
            return {"status": "failed", "confirmed": False}
        return dict(result) if isinstance(result, Mapping) else {
            "status": "failed", "confirmed": False
        }

    async def _async_policy_changed(self, document: ScenarioControlDocument) -> None:
        async with self._decision_lock:
            self._cancel_light_task()
            self._cancel_exhaust_task()
            await self._set_transition(
                "policy_changed",
                policy_revision=document.policy_revision,
                clear_absence=True,
                clear_exhaust=True,
            )
        if self._started:
            self._rearm_exhaust_schedule()

    async def _set_transition(
        self,
        transition: str,
        *,
        evidence: Mapping[str, object] | None = None,
        policy_revision: int | None = None,
        absence_started_at_ms: int | None = None,
        deadline_ms: int | None = None,
        exhaust_deadline_ms: int | None = None,
        correlation_id: str | None = None,
        clear_absence: bool = False,
        clear_exhaust: bool = False,
    ) -> None:
        async with self._lock:
            self._storage = self._next_record(
                transition=transition,
                evidence=evidence,
                policy_revision=policy_revision,
                absence_started_at_ms=absence_started_at_ms,
                deadline_ms=deadline_ms,
                exhaust_deadline_ms=exhaust_deadline_ms,
                correlation_id=correlation_id,
                clear_absence=clear_absence,
                clear_exhaust=clear_exhaust,
            )
            await self._save()

    def _next_record(
        self,
        *,
        transition: str,
        evidence: Mapping[str, object] | None = None,
        policy_revision: int | None = None,
        absence_started_at_ms: int | None = None,
        deadline_ms: int | None = None,
        exhaust_deadline_ms: int | None = None,
        correlation_id: str | None = None,
        clear_absence: bool = False,
        clear_exhaust: bool = False,
    ) -> dict[str, object]:
        record = self.storage_state
        generation = int(record.get("generation", 0))
        if generation >= 2**31 - 1:
            raise RuntimeError("scenario control generation exhausted")
        record["generation"] = generation + 1
        record["policyRevision"] = (
            self._policy_service.current.policy_revision
            if policy_revision is None
            else policy_revision
        )
        record["transition"] = transition
        if evidence is not None:
            record["evidence"] = dict(evidence)
        if clear_absence:
            record["absenceStartedAtMs"] = None
            record["deadlineMs"] = None
        elif absence_started_at_ms is not None or deadline_ms is not None:
            record["absenceStartedAtMs"] = absence_started_at_ms
            record["deadlineMs"] = deadline_ms
        if clear_exhaust:
            record["exhaustDeadlineMs"] = None
        elif exhaust_deadline_ms is not None:
            record["exhaustDeadlineMs"] = exhaust_deadline_ms
        record["correlationId"] = correlation_id
        return record

    def _storage_evidence(self) -> tuple[object, object, str | None, str | None]:
        motion = self._target_state(self._storage_motion_target_id)
        presence = self._target_state(self._storage_presence_target_id)
        light_device = self._catalog_resolver(STORAGE_LIGHT_TARGET_ID)
        entity_id = getattr(light_device, "entity_id", None)
        light = self._entity_state(entity_id if isinstance(entity_id, str) else None)
        return motion, presence, light, entity_id if isinstance(entity_id, str) else None

    def _storage_entity_ids(self) -> frozenset[str]:
        result: set[str] = set()
        for target_id in (
            self._storage_motion_target_id,
            self._storage_presence_target_id,
            STORAGE_LIGHT_TARGET_ID,
        ):
            if target_id is None:
                continue
            device = self._catalog_resolver(target_id)
            entity_id = getattr(device, "entity_id", None)
            if isinstance(entity_id, str):
                result.add(entity_id)
        return frozenset(result)

    def _is_active(self) -> bool:
        return bool(
            self._activation_latch is None
            or getattr(self._activation_latch, "is_open", False)
        )

    def _rearm_exhaust_schedule(self) -> None:
        for unsubscribe in self._schedule_unsubs:
            unsubscribe()
        self._schedule_unsubs.clear()
        if not self._started:
            return
        from homeassistant.helpers.event import async_track_time_change

        for clock in self._policy_service.current.policy.storage_exhaust_times:
            hour, minute = (int(part) for part in clock.split(":"))

            async def due(_now: datetime, scheduled: str = clock) -> None:
                if self._is_active():
                    await self.async_handle_storage_exhaust_schedule(scheduled)

            self._schedule_unsubs.append(
                async_track_time_change(
                    self._hass,
                    due,
                    hour=hour,
                    minute=minute,
                    second=0,
                )
            )

    def _target_state(self, target_id: str | None) -> object:
        if target_id is None:
            return None
        device = self._catalog_resolver(target_id)
        entity_id = getattr(device, "entity_id", None)
        if not isinstance(entity_id, str):
            return "unknown"
        state = self._entity_state(entity_id)
        return "unknown" if state is None else state

    def _entity_state(self, entity_id: str | None) -> str | None:
        if entity_id is None:
            return None
        state = self._hass.states.get(entity_id)
        value = getattr(state, "state", None)
        return str(value).strip().casefold() if value is not None else None

    @staticmethod
    def _evidence_payload(
        motion: object,
        presence: object,
        light: object,
        ownership_revision: object,
    ) -> dict[str, object]:
        return {
            "motion": None if motion is None else str(motion),
            "presence": None if presence is None else str(presence),
            "light": None if light is None else str(light),
            "ownershipRevision": (
                ownership_revision if isinstance(ownership_revision, str) else None
            ),
        }

    def _schedule_light_due(self) -> None:
        if not self._schedule_tasks or self.storage_remaining_seconds is None:
            return
        self._cancel_light_task()

        async def due() -> None:
            try:
                await asyncio.sleep(self.storage_remaining_seconds or 0)
                await self.async_reconcile_storage_due()
            finally:
                if self._light_task is asyncio.current_task():
                    self._light_task = None

        self._light_task = asyncio.create_task(due())

    def _schedule_exhaust_due(self) -> None:
        deadline = self._storage.get("exhaustDeadlineMs")
        if not self._schedule_tasks or type(deadline) is not int:
            return
        self._cancel_exhaust_task()

        async def due() -> None:
            try:
                await asyncio.sleep(max(0, deadline - self._now_ms()) / 1000)
                await self.async_reconcile_storage_exhaust_due()
            finally:
                if self._exhaust_task is asyncio.current_task():
                    self._exhaust_task = None

        self._exhaust_task = asyncio.create_task(due())

    def _cancel_light_task(self) -> None:
        task = self._light_task
        self._light_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    def _cancel_exhaust_task(self) -> None:
        task = self._exhaust_task
        self._exhaust_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    def stop_runtime(self) -> None:
        self._cancel_light_task()
        self._cancel_exhaust_task()
        for unsubscribe in self._schedule_unsubs:
            unsubscribe()
        self._schedule_unsubs.clear()
        self._started = False

    def cancel(self) -> None:
        self.stop_runtime()
        if self._remove_policy_observer is not None:
            self._remove_policy_observer()
            self._remove_policy_observer = None

    async def _save(self) -> None:
        payload = self.payload
        if not valid_scenario_control_state_payload(payload):
            raise RuntimeError("scenario control state is invalid")
        await self._store.async_save(payload)
