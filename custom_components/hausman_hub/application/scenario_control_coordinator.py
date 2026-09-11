"""Durable server coordinator for consolidated managed controllers."""

from __future__ import annotations

import asyncio
import copy
import math
import time
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..domain.scenario_controls import (
    OccupancyEvidence,
    ScenarioControlDocument,
    brightness_sequence_deadline_ms,
    brightness_sequence_step,
    maximum_brightness,
    scenario_control_policy_to_payload,
)

STORAGE_SCENARIO_ID = "system-storage-light-controller"
STORAGE_LIGHT_TARGET_ID = "entity_0ec37ef18b4b39a6"
STORAGE_MOTION_TARGET_ID = "entity_00dcf0ebdc0bc6cb"
TAMBUR_SCENARIO_ID = "system-tambur-adaptive-controller"
TAMBUR_MOTION_TARGET_ID = "entity_10b78187426f8485"
TAMBUR_PRESENCE_TARGET_IDS = (
    "entity_156050daca86aa6c",
    "entity_402b26d150a1ef3f",
)
TAMBUR_CHANDELIER_TARGET_ID = "entity_71859313239a14e4"
TAMBUR_POINTS_TARGET_ID = "entity_cd0098e5ff95da46"
TAMBUR_MIRROR_TARGET_ID = "entity_fbdf27871edb89bf"
TAMBUR_POWER_TARGET_ID = "entity_b47991988cc6b9f3"
TAMBUR_ENTRY_DOOR_TARGET_ID = "entity_170c7a4e2505b803"
SMALL_CORRIDOR_SCENARIO_ID = "system-small-corridor-light-controller"
SMALL_CORRIDOR_MOTION_TARGET_ID = "entity_a371cea02388be65"
SMALL_CORRIDOR_LUX_TARGET_ID = "entity_2e306a9650ac5728"
SMALL_CORRIDOR_RELAY_TARGET_ID = "entity_4be32416634e6416"
SMALL_CORRIDOR_CHANDELIER_TARGET_ID = "entity_9ed909332fdaa8fd"
SUN_TARGET_ID = "entity_6b9ccdab9bb484b2"
SHOWER_SCENARIO_ID = "system-shower-comfort-controller"
SHOWER_PRESENCE_TARGET_ID = "entity_d1fb2cbf2a691bba"
SHOWER_HUMIDITY_TARGET_ID = "entity_fd3945cf1a2110f8"
SHOWER_MAIN_TARGET_ID = "entity_46174e1ff9913212"
SHOWER_EXTRA_TARGET_ID = "entity_1fdcd8b244637246"
SHOWER_FAN_TARGET_ID = "entity_afef5df0e0cae309"
SHOWER_CABINET_TARGET_ID = "entity_e7a7c61eec7bdff8"
TOILET_SCENARIO_ID = "system-toilet-comfort-controller"
TOILET_MOTION_TARGET_IDS = (
    "entity_ce73f88bda2e6812",
    "entity_56650c782076ed4d",
)
TOILET_MAIN_TARGET_ID = "entity_5d95de599d2b5cec"
TOILET_NIGHT_TARGET_ID = "entity_6667b3400bce7970"
TOILET_FAN_TARGET_ID = "entity_9bbb3b0e8cd98627"
TOILET_AWAY_TARGET_ID = "entity_3f343b8d6f58f5b4"
BATHROOM_SCENARIO_ID = "system-bathroom-exhaust-controller"
BATHROOM_LIGHT_TARGET_IDS = (
    "entity_a591e035e3e5b34f",
    "entity_d82766182d69dd51",
)
BATHROOM_HUMIDITY_TARGET_ID = "entity_436e12f71ce7b08b"
BATHROOM_FAN_TARGET_ID = "entity_c15f5df5382ee180"
OFFICE_SCENARIO_ID = "system-cabinet-light-controller"
OFFICE_LIGHT_TARGET_ID = "entity_aeaf7c250c68e8c2"
OFFICE_RELAY_TARGET_ID = "entity_7ff6d09cfa68fa5a"
OFFICE_LUX_TARGET_ID = "entity_5f3b4436fb7b6f2b"
_LIGHT_ACTION_IDS = frozenset(
    {"turn_on", "turn_off", "set_brightness_percent", "set_color_temperature"}
)
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
        "presence_rise_pending",
        "manual_release_pending",
        "manual_release_completed",
        "manual_profile_hold",
        "lux_hold_pending",
        "lux_too_bright",
        "night_automatic_off",
        "night_mirror_only",
        "light_action",
        "light_action_failed",
        "brightness_sequence",
        "brightness_sequence_failed",
        "brightness_sequence_completed",
        "controller_unknown",
        "controller_idle",
        "shower_profile",
        "shower_presence_pending",
        "shower_absence_pending",
        "toilet_profile",
        "toilet_absence_pending",
        "toilet_fan_off_pending",
        "bathroom_hold",
        "bathroom_day_off_pending",
        "office_power_settle",
        "office_program",
        "office_profile_applied",
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
        "action": None,
        "sequence": None,
        "manualAbsenceStartedAtMs": None,
        "luxCandidateSinceMs": None,
        "welcomeArmed": True,
        "timerKind": None,
        "ownedTargets": {},
        "program": None,
    }


def _validated_storage_record(value: object) -> dict[str, object] | None:
    legacy_keys = {
        "generation",
        "policyRevision",
        "evidence",
        "absenceStartedAtMs",
        "deadlineMs",
        "exhaustDeadlineMs",
        "transition",
        "fractionalRemainder",
        "correlationId",
    }
    current_keys = legacy_keys | {
        "action",
        "sequence",
        "manualAbsenceStartedAtMs",
        "luxCandidateSinceMs",
        "welcomeArmed",
    }
    extended_keys = current_keys | {"timerKind", "ownedTargets", "program"}
    if not isinstance(value, Mapping) or (
        frozenset(value) not in {frozenset(legacy_keys), frozenset(current_keys), frozenset(extended_keys)}
    ):
        return None
    normalized = dict(value)
    for key, default in (
        ("action", None),
        ("sequence", None),
        ("manualAbsenceStartedAtMs", None),
        ("luxCandidateSinceMs", None),
        ("welcomeArmed", True),
        ("timerKind", None),
        ("ownedTargets", {}),
        ("program", None),
    ):
        normalized.setdefault(key, default)
    value = normalized
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
    for key in (
        "absenceStartedAtMs",
        "deadlineMs",
        "exhaustDeadlineMs",
        "manualAbsenceStartedAtMs",
        "luxCandidateSinceMs",
    ):
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
    if type(value.get("welcomeArmed")) is not bool:
        return None
    timer_kind = value.get("timerKind")
    if timer_kind is not None and timer_kind not in {
        "shower_presence", "shower_absence", "toilet_absence",
        "toilet_fan_off", "bathroom_day_off", "office_program",
    }:
        return None
    owned_targets = value.get("ownedTargets")
    if not isinstance(owned_targets, Mapping) or any(
        not isinstance(key, str) or not key or len(key) > 128
        or not isinstance(token, str) or not token or len(token) > 128
        for key, token in owned_targets.items()
    ):
        return None
    action = value.get("action")
    if action is not None and (
        not isinstance(action, Mapping)
        or set(action) != {"targetId", "actionId", "value"}
        or not isinstance(action.get("targetId"), str)
        or not 1 <= len(str(action["targetId"])) <= 128
        or action.get("actionId") not in _LIGHT_ACTION_IDS
        or action.get("value") is not None
        and (type(action.get("value")) is not int or not 0 <= int(action["value"]) <= 6500)
    ):
        return None
    sequence = value.get("sequence")
    if sequence is not None and (
        not isinstance(sequence, Mapping)
        or set(sequence) != {
            "kind", "startedAtMs", "deadlineMs", "start", "target", "current"
        }
        or sequence.get("kind") not in {"ramp", "fade", "cap"}
        or any(
            type(sequence.get(key)) is not int
            for key in ("startedAtMs", "deadlineMs", "start", "target", "current")
        )
        or not 0 <= int(sequence["startedAtMs"]) < int(sequence["deadlineMs"]) <= 2**63 - 1
        or any(not 0 <= int(sequence[key]) <= 100 for key in ("start", "target", "current"))
    ):
        return None
    program = value.get("program")
    if program is not None and (
        not isinstance(program, Mapping)
        or set(program) != {
            "profile", "step", "deadlineMs", "brightness", "primeKelvin", "targetKelvin"
        }
        or not isinstance(program.get("profile"), str)
        or not 1 <= len(str(program["profile"])) <= 64
        or type(program.get("step")) is not int
        or not 0 <= int(program["step"]) <= 3
        or type(program.get("deadlineMs")) is not int
        or not 0 <= int(program["deadlineMs"]) <= 2**63 - 1
        or type(program.get("brightness")) is not int
        or not 0 <= int(program["brightness"]) <= 100
        or any(
            type(program.get(key)) is not int or not 1500 <= int(program[key]) <= 6500
            for key in ("primeKelvin", "targetKelvin")
        )
    ):
        return None
    record = copy.deepcopy(dict(value))
    record["evidence"] = copy.deepcopy(dict(evidence))
    record["ownedTargets"] = copy.deepcopy(dict(owned_targets))
    record["program"] = copy.deepcopy(dict(program)) if isinstance(program, Mapping) else None
    record["fractionalRemainder"] = float(remainder)
    return record


def valid_scenario_control_state_payload(value: object) -> bool:
    """Validate the legacy storage record or the complete controller state."""

    if not isinstance(value, Mapping) or value.get("version") != 1:
        return False
    if set(value) == {"version", "storage"}:
        return _validated_storage_record(value.get("storage")) is not None
    legacy_complete = {"version", "storage", "tambur", "smallCorridor"}
    complete = legacy_complete | {"shower", "toilet", "bathroom", "office"}
    return bool(
        frozenset(value) in {frozenset(legacy_complete), frozenset(complete)}
        and all(
            _validated_storage_record(value.get(key)) is not None
            for key in set(value) - {"version"}
        )
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
        now: Callable[[], datetime] | None = None,
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
        self._now = now
        self._schedule_tasks = schedule_tasks
        self._storage = _empty_storage_record(0)
        self._tambur = _empty_storage_record(0)
        self._small_corridor = _empty_storage_record(0)
        self._shower = _empty_storage_record(0)
        self._toilet = _empty_storage_record(0)
        self._bathroom = _empty_storage_record(0)
        self._office = _empty_storage_record(0)
        self._light_task: asyncio.Task[None] | None = None
        self._exhaust_task: asyncio.Task[None] | None = None
        self._zone_tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()
        self._decision_lock = asyncio.Lock()
        self._remove_policy_observer: Callable[[], None] | None = None
        self._schedule_unsubs: list[Callable[[], None]] = []
        self._activation_latch: object | None = None
        self._started = False
        self._externally_managed_scenarios: frozenset[str] = frozenset()

    def set_externally_managed_scenarios(self, scenario_ids: frozenset[str]) -> None:
        """Exclude scenarios owned by another runtime (for example Tambur)."""

        self._externally_managed_scenarios = frozenset(scenario_ids)

    def _tambur_external(self) -> bool:
        return TAMBUR_SCENARIO_ID in getattr(
            self, "_externally_managed_scenarios", frozenset()
        )

    @property
    def owned_scenario_ids(self) -> frozenset[str]:
        """Scenario IDs whose device and clock triggers are coordinated here."""

        return frozenset(
            {
                STORAGE_SCENARIO_ID,
                TAMBUR_SCENARIO_ID,
                SMALL_CORRIDOR_SCENARIO_ID,
                SHOWER_SCENARIO_ID,
                TOILET_SCENARIO_ID,
                BATHROOM_SCENARIO_ID,
                OFFICE_SCENARIO_ID,
            }
        ) - getattr(self, "_externally_managed_scenarios", frozenset())

    async def async_load(self) -> None:
        document = self._policy_service.current
        payload = await self._store.async_load()
        if payload is None:
            self._storage = _empty_storage_record(document.policy_revision)
            self._tambur = _empty_storage_record(document.policy_revision)
            self._small_corridor = _empty_storage_record(document.policy_revision)
            self._shower = _empty_storage_record(document.policy_revision)
            self._toilet = _empty_storage_record(document.policy_revision)
            self._bathroom = _empty_storage_record(document.policy_revision)
            self._office = _empty_storage_record(document.policy_revision)
            await self._save()
        elif not valid_scenario_control_state_payload(payload):
            raise RuntimeError("scenario control state storage is invalid")
        else:
            record = _validated_storage_record(payload["storage"])
            assert record is not None
            self._storage = record
            tambur = _validated_storage_record(payload.get("tambur"))
            small = _validated_storage_record(payload.get("smallCorridor"))
            self._tambur = tambur or _empty_storage_record(document.policy_revision)
            self._small_corridor = small or _empty_storage_record(document.policy_revision)
            self._shower = _validated_storage_record(payload.get("shower")) or _empty_storage_record(document.policy_revision)
            self._toilet = _validated_storage_record(payload.get("toilet")) or _empty_storage_record(document.policy_revision)
            self._bathroom = _validated_storage_record(payload.get("bathroom")) or _empty_storage_record(document.policy_revision)
            self._office = _validated_storage_record(payload.get("office")) or _empty_storage_record(document.policy_revision)
            if set(payload) != {
                "version", "storage", "tambur", "smallCorridor",
                "shower", "toilet", "bathroom", "office",
            }:
                await self._save()
        if getattr(self._store, "recovered_previous", False):
            for record in (
                self._storage,
                self._tambur,
                self._small_corridor,
                self._shower,
                self._toilet,
                self._bathroom,
                self._office,
            ):
                record.clear()
                record.update(_empty_storage_record(document.policy_revision))
                record["transition"] = "stale_generation"
            await self._save()
        elif any(
            record["policyRevision"] != document.policy_revision
            for record in self._all_records()
        ):
            self._storage = self._next_record_for(
                self._storage,
                transition="policy_changed",
                policy_revision=document.policy_revision,
                clear_absence=True,
                clear_exhaust=True,
                clear_sequence=True,
            )
            self._tambur = self._next_record_for(
                self._tambur,
                transition="policy_changed",
                policy_revision=document.policy_revision,
                clear_absence=True,
                clear_sequence=True,
            )
            self._small_corridor = self._next_record_for(
                self._small_corridor,
                transition="policy_changed",
                policy_revision=document.policy_revision,
                clear_absence=True,
                clear_sequence=True,
            )
            self._shower = self._policy_reset(self._shower, document.policy_revision)
            self._toilet = self._policy_reset(self._toilet, document.policy_revision)
            self._bathroom = self._policy_reset(self._bathroom, document.policy_revision)
            self._office = self._policy_reset(self._office, document.policy_revision)
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
            if entity_id in self._storage_entity_ids():
                await self.async_handle_storage_change()
            if entity_id in self._tambur_entity_ids() and not self._tambur_external():
                await self.async_handle_tambur_change(
                    trigger_entity_id=entity_id,
                    old_state=data.get("old_state"),
                    new_state=data.get("new_state"),
                )
            if entity_id in self._small_corridor_entity_ids():
                await self.async_handle_small_corridor_change()
            if entity_id in self._shower_entity_ids():
                await self.async_handle_shower_change()
            if entity_id in self._toilet_entity_ids():
                await self.async_handle_toilet_change()
            if entity_id in self._bathroom_entity_ids():
                await self.async_handle_bathroom_change()
            if entity_id in self._office_entity_ids():
                await self.async_handle_office_change()

        unsubscribe = bus.async_listen("state_changed", state_changed)
        getattr(entry, "async_on_unload")(unsubscribe)
        getattr(entry, "async_on_unload")(self.stop_runtime)
        self._started = True
        self._rearm_schedules()

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
            if not self._tambur_external():
                await self.async_handle_tambur_change(
                    recovery=True,
                    allow_activation=False,
                )
            await self.async_handle_small_corridor_change(
                recovery=True,
                allow_activation=False,
            )
            await self.async_handle_shower_change(recovery=True, allow_activation=False)
            await self.async_handle_toilet_change(recovery=True, allow_activation=False)
            await self.async_handle_bathroom_change(recovery=True, allow_activation=False)
            await self.async_handle_office_change(recovery=True)
            for scenario_id in self.owned_scenario_ids - {STORAGE_SCENARIO_ID}:
                self._schedule_zone_due(scenario_id)

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
        return {
            "version": 1,
            "storage": self.storage_state,
            "tambur": copy.deepcopy(self._tambur),
            "smallCorridor": copy.deepcopy(self._small_corridor),
            "shower": copy.deepcopy(self._shower),
            "toilet": copy.deepcopy(self._toilet),
            "bathroom": copy.deepcopy(self._bathroom),
            "office": copy.deepcopy(self._office),
        }

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

        document: ScenarioControlDocument = self._policy_service.current
        policy = scenario_control_policy_to_payload(document.policy)
        record = self._record_for_scenario(scenario_id)
        manual = trigger.get("source") == "manual"
        if record is None:
            state: dict[str, object] = {
                "ready": False,
                "transition": "incomplete",
                "correlationId": run_id,
            }
        else:
            state = copy.deepcopy(record)
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
            state["ready"] = matching or manual
            if not matching:
                if not manual:
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

    async def async_handle_tambur_change(
        self,
        *,
        recovery: bool = False,
        allow_activation: bool = True,
        trigger_entity_id: str | None = None,
        old_state: object = None,
        new_state: object = None,
    ) -> None:
        """Derive one bounded tambur action from current server evidence."""

        async with self._decision_lock:
            await self._async_handle_tambur_change(
                recovery=recovery,
                allow_activation=allow_activation,
                trigger_entity_id=trigger_entity_id,
                old_state=old_state,
                new_state=new_state,
            )

    async def _async_handle_tambur_change(
        self,
        *,
        recovery: bool,
        allow_activation: bool,
        trigger_entity_id: str | None,
        old_state: object,
        new_state: object,
    ) -> None:
        motion = self._target_state(TAMBUR_MOTION_TARGET_ID)
        presence = self._combined_target_state(TAMBUR_PRESENCE_TARGET_IDS)
        chandelier = self._target_state(TAMBUR_CHANDELIER_TARGET_ID)
        evidence = self._evidence_payload(motion, presence, chandelier, None)
        if self._failed_with_same_evidence(self._tambur, evidence):
            return
        sun_entity = self._target_entity_id(SUN_TARGET_ID)
        sun_changed_to_evening = bool(
            trigger_entity_id == sun_entity
            and self._state_value(old_state) != "below_horizon"
            and self._state_value(new_state) == "below_horizon"
        )
        if (
            (sun_changed_to_evening or recovery and self._tambur_band() == "evening")
            and self._target_state(TAMBUR_MIRROR_TARGET_ID) == "off"
            and not self._manual_claims((TAMBUR_MIRROR_TARGET_ID,))
        ):
            await self._run_zone_action(
                TAMBUR_SCENARIO_ID,
                TAMBUR_MIRROR_TARGET_ID,
                "turn_on",
                None,
                evidence=evidence,
            )
            if sun_changed_to_evening:
                await self._start_tambur_evening_cap_if_owned()
            return
        if sun_changed_to_evening:
            await self._start_tambur_evening_cap_if_owned()
            return
        door_entity = self._target_entity_id(TAMBUR_ENTRY_DOOR_TARGET_ID)
        if trigger_entity_id == door_entity:
            old = self._state_value(old_state)
            new = self._state_value(new_state)
            if new == "locked" and old != new:
                await self._set_zone_transition(
                    TAMBUR_SCENARIO_ID,
                    "controller_idle",
                    evidence=evidence,
                    welcome_armed=True,
                    clear_absence=True,
                    clear_sequence=True,
                    clear_manual_absence=True,
                )
            elif new == "unlocked" and old != new and self._tambur["welcomeArmed"]:
                await self._set_zone_transition(
                    TAMBUR_SCENARIO_ID,
                    "controller_idle",
                    evidence=evidence,
                    welcome_armed=False,
                )
                # The welcome follows the same safe occupancy profile once.
                motion = "on"
        occupied = await self._confirmed_occupancy(
            TAMBUR_SCENARIO_ID,
            motion,
            presence,
            evidence,
            recovery=recovery,
        )
        if occupied is None:
            if (
                OccupancyEvidence.from_states(motion, presence)
                is OccupancyEvidence.UNKNOWN
            ):
                self._cancel_zone_task(TAMBUR_SCENARIO_ID)
                await self._set_zone_transition(
                    TAMBUR_SCENARIO_ID,
                    "controller_unknown",
                    evidence=evidence,
                    clear_absence=True,
                    clear_sequence=True,
                    clear_manual_absence=True,
                )
            return
        if occupied is False:
            await self._handle_zone_absence(
                TAMBUR_SCENARIO_ID,
                (TAMBUR_CHANDELIER_TARGET_ID, TAMBUR_POINTS_TARGET_ID),
                evidence,
            )
            return
        await self._clear_manual_absence(TAMBUR_SCENARIO_ID, evidence)
        if self._manual_claims(
            (TAMBUR_CHANDELIER_TARGET_ID, TAMBUR_POINTS_TARGET_ID)
        ):
            self._cancel_zone_task(TAMBUR_SCENARIO_ID)
            await self._set_zone_transition(
                TAMBUR_SCENARIO_ID,
                "manual_profile_hold",
                evidence=evidence,
                clear_absence=True,
                clear_sequence=True,
                clear_manual_absence=True,
            )
            return
        active_sequence = self._tambur.get("sequence")
        if isinstance(active_sequence, Mapping) and active_sequence.get("kind") == "fade":
            self._cancel_zone_task(TAMBUR_SCENARIO_ID)
            await self._set_zone_transition(
                TAMBUR_SCENARIO_ID,
                "controller_idle",
                evidence=evidence,
                clear_absence=True,
                clear_sequence=True,
            )
        band = self._tambur_band()
        if band == "night":
            await self._turn_off_owned_targets(
                TAMBUR_SCENARIO_ID,
                (TAMBUR_CHANDELIER_TARGET_ID, TAMBUR_POINTS_TARGET_ID),
            )
            await self._set_zone_transition(
                TAMBUR_SCENARIO_ID,
                "night_mirror_only",
                evidence=evidence,
                clear_absence=True,
                clear_sequence=True,
            )
            return
        if self._tambur.get("sequence") is not None:
            self._schedule_zone_due(TAMBUR_SCENARIO_ID)
            return
        brightness = self._target_brightness_percent(TAMBUR_CHANDELIER_TARGET_ID)
        cap = self._tambur_cap_percent(band)
        if chandelier == "off":
            if not allow_activation:
                await self._set_zone_transition(
                    TAMBUR_SCENARIO_ID,
                    "controller_idle",
                    evidence=evidence,
                    clear_sequence=True,
                )
                return
            if not await self._run_zone_action(
                TAMBUR_SCENARIO_ID,
                TAMBUR_CHANDELIER_TARGET_ID,
                "set_brightness_percent",
                5,
                evidence=evidence,
            ):
                return
            await self._start_brightness_sequence(
                TAMBUR_SCENARIO_ID, "ramp", 5, cap
            )
            return
        if chandelier not in {"on", "off"} or brightness is None:
            await self._set_zone_transition(
                TAMBUR_SCENARIO_ID,
                "controller_unknown",
                evidence=evidence,
                clear_sequence=True,
            )
            return
        if brightness != cap:
            duration = (
                self._seconds_until_clock(self._policy_service.current.policy.tambur_main_off)
                if brightness > cap and band == "evening"
                else self._policy_service.current.policy.brightness_ramp_seconds
            )
            await self._start_brightness_sequence(
                TAMBUR_SCENARIO_ID,
                "cap" if brightness > cap else "ramp",
                brightness,
                cap,
                duration_seconds=max(1, duration),
            )
            return
        target_kelvin = (
            self._zone_color_temperature(TAMBUR_SCENARIO_ID, evening=True)
            if band == "evening"
            else self._zone_color_temperature(TAMBUR_SCENARIO_ID, evening=False)
        )
        if self._target_color_temperature(TAMBUR_CHANDELIER_TARGET_ID) != target_kelvin:
            await self._run_zone_action(
                TAMBUR_SCENARIO_ID,
                TAMBUR_CHANDELIER_TARGET_ID,
                "set_color_temperature",
                target_kelvin,
                evidence=evidence,
            )
            return
        if self._target_state(TAMBUR_POINTS_TARGET_ID) == "off":
            await self._run_zone_action(
                TAMBUR_SCENARIO_ID,
                TAMBUR_POINTS_TARGET_ID,
                "turn_on",
                None,
                evidence=evidence,
            )
            return
        if band == "evening" and await self._start_tambur_evening_cap_if_owned():
            return
        await self._set_zone_transition(
            TAMBUR_SCENARIO_ID,
            "occupied_hold",
            evidence=evidence,
            clear_absence=True,
        )

    async def async_handle_small_corridor_change(
        self,
        *,
        recovery: bool = False,
        allow_activation: bool = True,
    ) -> None:
        """Derive one bounded small-corridor action from live sensor evidence."""

        async with self._decision_lock:
            motion = self._target_state(SMALL_CORRIDOR_MOTION_TARGET_ID)
            chandelier = self._target_state(SMALL_CORRIDOR_CHANDELIER_TARGET_ID)
            evidence = self._evidence_payload(motion, None, chandelier, None)
            if self._failed_with_same_evidence(self._small_corridor, evidence):
                return
            if motion == "on":
                occupied: bool | None = True
            elif motion == "off":
                occupied = False
            else:
                occupied = None
            if occupied is None:
                self._cancel_zone_task(SMALL_CORRIDOR_SCENARIO_ID)
                await self._set_zone_transition(
                    SMALL_CORRIDOR_SCENARIO_ID,
                    "controller_unknown",
                    evidence=evidence,
                    clear_absence=True,
                    clear_sequence=True,
                    clear_lux_candidate=True,
                )
                return
            if not occupied:
                await self._handle_zone_absence(
                    SMALL_CORRIDOR_SCENARIO_ID,
                    (SMALL_CORRIDOR_RELAY_TARGET_ID, SMALL_CORRIDOR_CHANDELIER_TARGET_ID),
                    evidence,
                )
                return
            await self._clear_manual_absence(SMALL_CORRIDOR_SCENARIO_ID, evidence)
            if self._manual_claims(
                (SMALL_CORRIDOR_RELAY_TARGET_ID, SMALL_CORRIDOR_CHANDELIER_TARGET_ID)
            ):
                self._cancel_zone_task(SMALL_CORRIDOR_SCENARIO_ID)
                await self._set_zone_transition(
                    SMALL_CORRIDOR_SCENARIO_ID,
                    "manual_profile_hold",
                    evidence=evidence,
                    clear_absence=True,
                    clear_sequence=True,
                    clear_manual_absence=True,
                )
                return
            active_sequence = self._small_corridor.get("sequence")
            if (
                isinstance(active_sequence, Mapping)
                and active_sequence.get("kind") == "fade"
            ):
                self._cancel_zone_task(SMALL_CORRIDOR_SCENARIO_ID)
                await self._set_zone_transition(
                    SMALL_CORRIDOR_SCENARIO_ID,
                    "controller_idle",
                    evidence=evidence,
                    clear_absence=True,
                    clear_sequence=True,
                )
            band = self._small_corridor_band()
            if band == "night":
                await self._turn_off_owned_targets(
                    SMALL_CORRIDOR_SCENARIO_ID,
                    (
                        SMALL_CORRIDOR_CHANDELIER_TARGET_ID,
                        SMALL_CORRIDOR_RELAY_TARGET_ID,
                    ),
                )
                await self._set_zone_transition(
                    SMALL_CORRIDOR_SCENARIO_ID,
                    "night_automatic_off",
                    evidence=evidence,
                    clear_sequence=True,
                    clear_lux_candidate=True,
                )
                return
            if self._small_corridor.get("sequence") is not None:
                self._schedule_zone_due(SMALL_CORRIDOR_SCENARIO_ID)
                return
            brightness = self._target_brightness_percent(
                SMALL_CORRIDOR_CHANDELIER_TARGET_ID
            )
            cap = 5 if band == "late" else 80
            if chandelier == "off":
                if not allow_activation:
                    await self._set_zone_transition(
                        SMALL_CORRIDOR_SCENARIO_ID,
                        "controller_idle",
                        evidence=evidence,
                        clear_sequence=True,
                        clear_lux_candidate=True,
                    )
                    return
                if band == "day" and not await self._small_corridor_lux_allows_on(
                    evidence
                ):
                    return
                if not await self._run_zone_action(
                    SMALL_CORRIDOR_SCENARIO_ID,
                    SMALL_CORRIDOR_CHANDELIER_TARGET_ID,
                    "set_brightness_percent",
                    5,
                    evidence=evidence,
                ):
                    return
                await self._start_brightness_sequence(
                    SMALL_CORRIDOR_SCENARIO_ID, "ramp", 5, cap
                )
                return
            if chandelier not in {"on", "off"} or brightness is None:
                await self._set_zone_transition(
                    SMALL_CORRIDOR_SCENARIO_ID,
                    "controller_unknown",
                    evidence=evidence,
                    clear_sequence=True,
                )
                return
            # A lit lamp contaminates local lux. Never derive an off decision
            # or reset the dark hold from its own light.
            if brightness != cap:
                await self._start_brightness_sequence(
                    SMALL_CORRIDOR_SCENARIO_ID,
                    "cap" if brightness > cap else "ramp",
                    brightness,
                    cap,
                )
                return
            target_kelvin = (
                self._zone_color_temperature(
                    SMALL_CORRIDOR_SCENARIO_ID, evening=True
                )
                if band == "late"
                else self._zone_color_temperature(
                    SMALL_CORRIDOR_SCENARIO_ID, evening=False
                )
            )
            if self._target_color_temperature(SMALL_CORRIDOR_CHANDELIER_TARGET_ID) != target_kelvin:
                await self._run_zone_action(
                    SMALL_CORRIDOR_SCENARIO_ID,
                    SMALL_CORRIDOR_CHANDELIER_TARGET_ID,
                    "set_color_temperature",
                    target_kelvin,
                    evidence=evidence,
                )
                return
            await self._set_zone_transition(
                SMALL_CORRIDOR_SCENARIO_ID,
                "occupied_hold",
                evidence=evidence,
                clear_absence=True,
                clear_lux_candidate=True,
            )

    async def _small_corridor_lux_allows_on(
        self, evidence: Mapping[str, object]
    ) -> bool:
        lux = self._target_numeric_state(SMALL_CORRIDOR_LUX_TARGET_ID)
        policy = self._policy_service.current.policy
        if lux is None:
            await self._set_zone_transition(
                SMALL_CORRIDOR_SCENARIO_ID,
                "controller_unknown",
                evidence=evidence,
                clear_lux_candidate=True,
            )
            return False
        dark_limit = policy.small_corridor_lux_threshold - policy.lux_hysteresis
        if lux >= dark_limit:
            await self._set_zone_transition(
                SMALL_CORRIDOR_SCENARIO_ID,
                "lux_too_bright",
                evidence=evidence,
                clear_lux_candidate=True,
            )
            return False
        started = self._small_corridor.get("luxCandidateSinceMs")
        if type(started) is not int:
            started = self._now_ms()
            await self._set_zone_transition(
                SMALL_CORRIDOR_SCENARIO_ID,
                "lux_hold_pending",
                evidence=evidence,
                lux_candidate_since_ms=started,
                deadline_ms=started + policy.lux_hold_seconds * 1000,
            )
            self._schedule_zone_due(SMALL_CORRIDOR_SCENARIO_ID)
            return policy.lux_hold_seconds == 0
        if self._now_ms() - started < policy.lux_hold_seconds * 1000:
            self._schedule_zone_due(SMALL_CORRIDOR_SCENARIO_ID)
            return False
        return True

    async def _confirmed_occupancy(
        self,
        scenario_id: str,
        motion: object,
        presence: object,
        evidence: Mapping[str, object],
        *,
        recovery: bool,
    ) -> bool | None:
        if motion == "on":
            return True
        if presence == "on":
            policy = self._policy_service.current.policy
            record = self._record_for_scenario(scenario_id)
            assert record is not None
            if record.get("transition") == "presence_rise_pending":
                deadline = record.get("deadlineMs")
                if type(deadline) is int and self._now_ms() >= deadline:
                    return True
                self._schedule_zone_due(scenario_id)
                return False if recovery and type(deadline) is not int else None
            started = self._now_ms()
            await self._set_zone_transition(
                scenario_id,
                "presence_rise_pending",
                evidence=evidence,
                absence_started_at_ms=started,
                deadline_ms=started + policy.presence_rise_seconds * 1000,
                clear_sequence=True,
            )
            self._schedule_zone_due(scenario_id)
            return True if policy.presence_rise_seconds == 0 else None
        evidence_value = OccupancyEvidence.from_states(motion, presence)
        if evidence_value is OccupancyEvidence.ABSENT:
            return False
        return None

    async def _handle_zone_absence(
        self,
        scenario_id: str,
        target_ids: tuple[str, ...],
        evidence: Mapping[str, object],
    ) -> None:
        record = self._record_for_scenario(scenario_id)
        assert record is not None
        first_absence = bool(
            record.get("transition") == "presence_rise_pending"
            or type(record.get("absenceStartedAtMs")) is not int
        )
        if first_absence:
            await self._set_zone_transition(
                scenario_id,
                "controller_idle",
                evidence=evidence,
                absence_started_at_ms=self._now_ms(),
            )
            record = self._record_for_scenario(scenario_id)
            assert record is not None
        claims = self._manual_claims(target_ids)
        if claims:
            started = record.get("manualAbsenceStartedAtMs")
            if type(started) is not int:
                started = self._now_ms()
                await self._set_zone_transition(
                    scenario_id,
                    "manual_release_pending",
                    evidence=evidence,
                    manual_absence_started_at_ms=started,
                    deadline_ms=(
                        started
                        + self._policy_service.current.policy.manual_release_seconds
                        * 1000
                    ),
                    clear_sequence=True,
                )
                self._schedule_zone_due(scenario_id)
                return
            deadline = started + self._policy_service.current.policy.manual_release_seconds * 1000
            if self._now_ms() < deadline:
                self._schedule_zone_due(scenario_id)
                return
            await self._light_priority.async_release_manual_claims(claims)
            await self._set_zone_transition(
                scenario_id,
                "manual_release_completed",
                evidence=evidence,
                clear_absence=True,
                clear_sequence=True,
                clear_manual_absence=True,
            )
        absence_started = record.get("absenceStartedAtMs")
        assert type(absence_started) is int
        confirmation_deadline = (
            absence_started
            + self._policy_service.current.policy.absence_confirmation_seconds
            * 1000
        )
        if self._now_ms() < confirmation_deadline:
            await self._set_zone_transition(
                scenario_id,
                "absence_pending",
                evidence=evidence,
                absence_started_at_ms=absence_started,
                deadline_ms=confirmation_deadline,
            )
            self._schedule_zone_due(scenario_id)
            return
        await self._clear_manual_absence(scenario_id, evidence)
        primary = target_ids[-1] if scenario_id == SMALL_CORRIDOR_SCENARIO_ID else target_ids[0]
        entity_id = self._target_entity_id(primary)
        brightness = self._target_brightness_percent(primary)
        if (
            entity_id is None
            or self._target_state(primary) != "on"
            or brightness is None
            or not self._light_priority.is_owned(entity_id, self._hass)
        ):
            self._cancel_zone_task(scenario_id)
            await self._set_zone_transition(
                scenario_id,
                "controller_idle",
                evidence=evidence,
                clear_absence=True,
                clear_sequence=True,
            )
            return
        if record.get("sequence") is not None:
            self._schedule_zone_due(scenario_id)
            return
        if (
            not first_absence
            and record.get("transition") == "brightness_sequence_completed"
        ):
            return
        policy = self._policy_service.current.policy
        floor = (
            policy.evening_brightness_floor_percent
            if self._is_evening_or_night()
            else policy.day_brightness_floor_percent
        )
        target = max(
            floor,
            round(brightness * (100 - policy.relative_fade_percent) / 100),
        )
        if target == brightness:
            await self._set_zone_transition(
                scenario_id,
                "controller_idle",
                evidence=evidence,
                clear_absence=True,
            )
            return
        target_kelvin = self._zone_color_temperature(scenario_id, evening=True)
        if self._target_color_temperature(primary) != target_kelvin:
            if not await self._run_zone_action(
                scenario_id,
                primary,
                "set_color_temperature",
                target_kelvin,
                evidence=evidence,
            ):
                return
        await self._start_brightness_sequence(
            scenario_id,
            "fade",
            brightness,
            target,
            duration_seconds=policy.relative_fade_period_seconds,
            started_at_ms=absence_started,
            evidence=evidence,
        )

    async def _clear_manual_absence(
        self, scenario_id: str, evidence: Mapping[str, object]
    ) -> None:
        record = self._record_for_scenario(scenario_id)
        if record is not None and record.get("manualAbsenceStartedAtMs") is not None:
            await self._set_zone_transition(
                scenario_id,
                "controller_idle",
                evidence=evidence,
                clear_manual_absence=True,
                clear_absence=True,
            )

    async def async_handle_shower_change(
        self, *, recovery: bool = False, allow_activation: bool = True
    ) -> None:
        """Apply the shower profile while keeping both long timers durable."""

        async with self._decision_lock:
            presence = self._target_state(SHOWER_PRESENCE_TARGET_ID)
            humidity = self._target_numeric_state(SHOWER_HUMIDITY_TARGET_ID)
            lights = tuple(
                self._target_state(target)
                for target in (SHOWER_MAIN_TARGET_ID, SHOWER_EXTRA_TARGET_ID, SHOWER_CABINET_TARGET_ID)
            )
            fan = self._target_state(SHOWER_FAN_TARGET_ID)
            evidence = self._room_evidence(presence, humidity, lights, fan)
            if self._failed_dispatch_blocks(self._shower, evidence, recovery=recovery):
                return
            if humidity is not None and humidity > 55 and fan == "off" and allow_activation:
                if not await self._run_zone_action(
                    SHOWER_SCENARIO_ID,
                    SHOWER_FAN_TARGET_ID,
                    "turn_on",
                    None,
                    evidence=evidence,
                ):
                    return
                fan = "on"
            if presence not in {"on", "off"}:
                await self._room_hold(SHOWER_SCENARIO_ID, "controller_unknown", evidence)
                return
            if presence == "on":
                if (
                    recovery
                    and fan == "off"
                    and self._shower.get("timerKind") == "shower_presence"
                    and type(self._shower.get("deadlineMs")) is int
                ):
                    self._schedule_zone_due(SHOWER_SCENARIO_ID)
                    return
                self._cancel_zone_task(SHOWER_SCENARIO_ID)
                await self._set_zone_transition(
                    SHOWER_SCENARIO_ID, "shower_profile", evidence=evidence,
                    clear_absence=True, clear_timer=True,
                )
                shower_lights = (
                    SHOWER_MAIN_TARGET_ID,
                    SHOWER_EXTRA_TARGET_ID,
                    SHOWER_CABINET_TARGET_ID,
                )
                if allow_activation and not self._manual_profile_active(
                    SHOWER_SCENARIO_ID, shower_lights
                ):
                    desired = self._shower_profile()
                    if desired is not None:
                        for target, should_be_on in desired.items():
                            current = self._target_state(target)
                            if should_be_on and current == "off":
                                if not await self._run_zone_action(
                                    SHOWER_SCENARIO_ID, target, "turn_on", None, evidence=evidence
                                ):
                                    return
                            elif not should_be_on and current == "on" and self._is_room_owned(
                                SHOWER_SCENARIO_ID, target
                            ):
                                if not await self._run_zone_action(
                                    SHOWER_SCENARIO_ID, target, "turn_off", None, evidence=evidence
                                ):
                                    return
                if humidity is not None and humidity > 55:
                    return
                if fan == "off" and allow_activation:
                    await self._ensure_room_timer(
                        SHOWER_SCENARIO_ID,
                        "shower_presence",
                        self._policy_service.current.policy.shower_fan_presence_seconds,
                        evidence,
                    )
                return

            manual_claims = self._manual_claims(
                (
                    SHOWER_MAIN_TARGET_ID,
                    SHOWER_EXTRA_TARGET_ID,
                    SHOWER_CABINET_TARGET_ID,
                )
            )
            if manual_claims:
                record = self._record_for_scenario(SHOWER_SCENARIO_ID)
                assert record is not None
                started = record.get("manualAbsenceStartedAtMs")
                release_seconds = (
                    self._policy_service.current.policy.manual_release_seconds
                )
                if type(started) is not int:
                    started = self._now_ms()
                    await self._set_zone_transition(
                        SHOWER_SCENARIO_ID,
                        "manual_release_pending",
                        evidence=evidence,
                        manual_absence_started_at_ms=started,
                        deadline_ms=started + release_seconds * 1000,
                        clear_sequence=True,
                    )
                    self._schedule_zone_due(SHOWER_SCENARIO_ID)
                    return
                if self._now_ms() < started + release_seconds * 1000:
                    self._schedule_zone_due(SHOWER_SCENARIO_ID)
                    return
                await self._light_priority.async_release_manual_claims(
                    manual_claims
                )
                await self._set_zone_transition(
                    SHOWER_SCENARIO_ID,
                    "manual_release_completed",
                    evidence=evidence,
                    clear_absence=True,
                    clear_sequence=True,
                    clear_manual_absence=True,
                )
            owned_lights = tuple(
                target for target in (SHOWER_MAIN_TARGET_ID, SHOWER_EXTRA_TARGET_ID, SHOWER_CABINET_TARGET_ID)
                if self._target_state(target) == "on" and self._is_room_owned(SHOWER_SCENARIO_ID, target)
            )
            fan_can_stop = bool(
                fan == "on" and humidity is not None and humidity <= 55
                and self._is_room_owned(SHOWER_SCENARIO_ID, SHOWER_FAN_TARGET_ID)
            )
            if not owned_lights and not fan_can_stop:
                await self._room_hold(
                    SHOWER_SCENARIO_ID,
                    "controller_unknown" if fan == "on" and humidity is None else "controller_idle",
                    evidence,
                )
                return
            seconds = min(
                self._policy_service.current.policy.shower_absence_seconds
                if owned_lights else 3601,
                self._policy_service.current.policy.shower_fan_off_seconds
                if fan_can_stop else 3601,
            )
            await self._ensure_room_timer(
                SHOWER_SCENARIO_ID, "shower_absence", seconds, evidence
            )

    async def async_handle_toilet_change(
        self, *, recovery: bool = False, allow_activation: bool = True
    ) -> None:
        """Select one toilet light profile and manage its independent fan."""

        async with self._decision_lock:
            motion = self._combined_target_state(TOILET_MOTION_TARGET_IDS)
            main = self._target_state(TOILET_MAIN_TARGET_ID)
            night = self._target_state(TOILET_NIGHT_TARGET_ID)
            fan = self._target_state(TOILET_FAN_TARGET_ID)
            evidence = self._room_evidence(motion, self._target_state(TOILET_AWAY_TARGET_ID), (main, night), fan)
            if self._failed_dispatch_blocks(self._toilet, evidence, recovery=recovery):
                return
            if motion == "unknown":
                if self._toilet_fan_window() and "on" in {main, night} and fan == "off" and allow_activation:
                    if not await self._run_zone_action(
                        TOILET_SCENARIO_ID,
                        TOILET_FAN_TARGET_ID,
                        "turn_on",
                        None,
                        evidence=evidence,
                    ):
                        return
                await self._room_hold(TOILET_SCENARIO_ID, "controller_unknown", evidence)
                return
            if motion == "on":
                self._cancel_zone_task(TOILET_SCENARIO_ID)
                await self._set_zone_transition(
                    TOILET_SCENARIO_ID, "toilet_profile", evidence=evidence,
                    clear_absence=True, clear_timer=True,
                )
                if (
                    self._target_state(TOILET_AWAY_TARGET_ID) != "on"
                    and not self._manual_profile_active(
                        TOILET_SCENARIO_ID,
                        (TOILET_MAIN_TARGET_ID, TOILET_NIGHT_TARGET_ID)
                    )
                ):
                    target = self._toilet_profile_target()
                    if target is not None and allow_activation:
                        other = TOILET_NIGHT_TARGET_ID if target == TOILET_MAIN_TARGET_ID else TOILET_MAIN_TARGET_ID
                        if self._target_state(target) == "off" and not await self._run_zone_action(
                            TOILET_SCENARIO_ID, target, "turn_on", None, evidence=evidence
                        ):
                            return
                        if self._target_state(other) == "on" and self._is_room_owned(TOILET_SCENARIO_ID, other):
                            if not await self._run_zone_action(
                                TOILET_SCENARIO_ID, other, "turn_off", None, evidence=evidence
                            ):
                                return
            else:
                owned_lights = tuple(
                    target for target in (TOILET_MAIN_TARGET_ID, TOILET_NIGHT_TARGET_ID)
                    if self._target_state(target) == "on" and self._is_room_owned(TOILET_SCENARIO_ID, target)
                )
                if owned_lights:
                    await self._ensure_room_timer(
                        TOILET_SCENARIO_ID, "toilet_absence",
                        self._policy_service.current.policy.toilet_absence_seconds,
                        evidence,
                    )
                    return
            if self._toilet_fan_window() and "on" in {
                self._target_state(TOILET_MAIN_TARGET_ID), self._target_state(TOILET_NIGHT_TARGET_ID)
            }:
                if self._target_state(TOILET_FAN_TARGET_ID) == "off" and allow_activation:
                    await self._run_zone_action(
                        TOILET_SCENARIO_ID, TOILET_FAN_TARGET_ID, "turn_on", None,
                        evidence=evidence,
                    )
                return
            if (
                self._target_state(TOILET_MAIN_TARGET_ID) == "off"
                and self._target_state(TOILET_NIGHT_TARGET_ID) == "off"
                and self._target_state(TOILET_FAN_TARGET_ID) == "on"
                and self._is_room_owned(TOILET_SCENARIO_ID, TOILET_FAN_TARGET_ID)
            ):
                await self._ensure_room_timer(
                    TOILET_SCENARIO_ID, "toilet_fan_off",
                    self._policy_service.current.policy.toilet_fan_off_seconds,
                    evidence,
                )
            elif motion == "off":
                await self._room_hold(TOILET_SCENARIO_ID, "controller_idle", evidence)

    async def async_handle_bathroom_change(
        self, *, recovery: bool = False, allow_activation: bool = True
    ) -> None:
        """Control only the bathroom fan from two light inputs and humidity."""

        async with self._decision_lock:
            light1, light2 = (self._target_state(target) for target in BATHROOM_LIGHT_TARGET_IDS)
            humidity = self._target_numeric_state(BATHROOM_HUMIDITY_TARGET_ID)
            fan = self._target_state(BATHROOM_FAN_TARGET_ID)
            evidence = self._room_evidence(light1, light2, humidity, fan)
            if self._failed_dispatch_blocks(self._bathroom, evidence, recovery=recovery):
                return
            band = self._bathroom_band()
            should_on = (
                band == "day" and humidity is not None
                and humidity >= self._policy_service.current.policy.bathroom_humidity_threshold
                and "on" in {light1, light2}
                or band == "night" and "on" in {light1, light2}
                or band == "quiet" and light1 == "on" and light2 == "off"
            )
            should_off_now = bool(
                humidity is not None and fan == "on" and (
                    band == "night" and light1 == light2 == "off"
                    or band == "quiet" and (light2 == "on" or light1 == light2 == "off")
                )
            )
            if should_on:
                self._cancel_zone_task(BATHROOM_SCENARIO_ID)
                await self._set_zone_transition(
                    BATHROOM_SCENARIO_ID, "bathroom_hold", evidence=evidence,
                    clear_absence=True, clear_timer=True,
                )
                if fan == "off" and allow_activation:
                    if not await self._run_zone_action(
                        BATHROOM_SCENARIO_ID, BATHROOM_FAN_TARGET_ID, "turn_on", None,
                        evidence=evidence,
                    ):
                        return
                return
            if light1 not in {"on", "off"} or light2 not in {"on", "off"}:
                await self._room_hold(BATHROOM_SCENARIO_ID, "controller_unknown", evidence)
                return
            if should_off_now and self._is_room_owned(BATHROOM_SCENARIO_ID, BATHROOM_FAN_TARGET_ID):
                if not await self._run_zone_action(
                    BATHROOM_SCENARIO_ID, BATHROOM_FAN_TARGET_ID, "turn_off", None,
                    evidence=evidence,
                ):
                    return
                await self._room_hold(BATHROOM_SCENARIO_ID, "controller_idle", evidence)
                return
            if (
                band == "day" and light1 == light2 == "off" and fan == "on"
                and humidity is not None
                and humidity < self._policy_service.current.policy.bathroom_humidity_threshold
                and self._is_room_owned(BATHROOM_SCENARIO_ID, BATHROOM_FAN_TARGET_ID)
            ):
                await self._ensure_room_timer(
                    BATHROOM_SCENARIO_ID, "bathroom_day_off",
                    self._policy_service.current.policy.bathroom_day_off_seconds,
                    evidence,
                )
                return
            await self._room_hold(
                BATHROOM_SCENARIO_ID,
                "controller_unknown" if fan == "on" and humidity is None else "bathroom_hold",
                evidence,
            )

    async def async_handle_office_change(self, *, recovery: bool = False) -> None:
        """Start or resume the exact seven-profile TS0502B program."""

        async with self._decision_lock:
            light = self._target_state(OFFICE_LIGHT_TARGET_ID)
            relay = self._target_state(OFFICE_RELAY_TARGET_ID)
            lux = self._target_numeric_state(OFFICE_LUX_TARGET_ID)
            sun = self._target_state(SUN_TARGET_ID)
            profile = self._office_profile(lux, sun)
            evidence = self._room_evidence(light, relay, lux, sun)
            if self._failed_dispatch_blocks(self._office, evidence, recovery=recovery):
                return
            if light != "on" or relay != "on" or profile is None:
                await self._room_hold(
                    OFFICE_SCENARIO_ID,
                    "controller_unknown" if light not in {"on", "off"} or relay not in {"on", "off"} else "controller_idle",
                    evidence,
                    clear_program=True,
                )
                return
            current = self._office.get("program")
            if isinstance(current, Mapping) and current.get("profile") == profile[0]:
                self._schedule_zone_due(OFFICE_SCENARIO_ID)
                return
            if recovery and self._office.get("transition") == "office_profile_applied" and self._office.get("evidence") == evidence:
                return
            target_kelvin = profile[2]
            prime = target_kelvin - 100 if target_kelvin >= 4000 else target_kelvin + 100
            program = {
                "profile": profile[0], "step": 0,
                "deadlineMs": self._now_ms() + self._policy_service.current.policy.office_power_settle_seconds * 1000,
                "brightness": profile[1], "primeKelvin": prime,
                "targetKelvin": target_kelvin,
            }
            await self._set_zone_transition(
                OFFICE_SCENARIO_ID, "office_power_settle", evidence=evidence,
                deadline_ms=int(program["deadlineMs"]), timer_kind="office_program",
                program=program,
            )
            self._schedule_zone_due(OFFICE_SCENARIO_ID)

    async def _async_reconcile_room_due(self, scenario_id: str) -> None:
        record = self._record_for_scenario(scenario_id)
        if record is None:
            return
        if str(record.get("transition", "")).endswith("failed"):
            return
        deadline = record.get("deadlineMs")
        if type(deadline) is not int or self._now_ms() < deadline:
            self._schedule_zone_due(scenario_id)
            return
        kind = record.get("timerKind")
        if scenario_id == SHOWER_SCENARIO_ID:
            await self._async_reconcile_shower_due(str(kind))
        elif scenario_id == TOILET_SCENARIO_ID:
            await self._async_reconcile_toilet_due(str(kind))
        elif scenario_id == BATHROOM_SCENARIO_ID:
            await self._async_reconcile_bathroom_due(str(kind))
        elif scenario_id == OFFICE_SCENARIO_ID:
            await self._async_reconcile_office_due()

    async def _async_reconcile_shower_due(self, kind: str) -> None:
        presence = self._target_state(SHOWER_PRESENCE_TARGET_ID)
        humidity = self._target_numeric_state(SHOWER_HUMIDITY_TARGET_ID)
        evidence = self._room_evidence(presence, humidity, tuple(
            self._target_state(target) for target in (SHOWER_MAIN_TARGET_ID, SHOWER_EXTRA_TARGET_ID, SHOWER_CABINET_TARGET_ID)
        ), self._target_state(SHOWER_FAN_TARGET_ID))
        if kind == "shower_presence":
            if presence == "on" and self._target_state(SHOWER_FAN_TARGET_ID) == "off":
                if not await self._run_zone_action(
                    SHOWER_SCENARIO_ID,
                    SHOWER_FAN_TARGET_ID,
                    "turn_on",
                    None,
                    evidence=evidence,
                ):
                    return
            await self._room_hold(SHOWER_SCENARIO_ID, "occupied_hold", evidence)
            return
        if kind != "shower_absence" or presence != "off":
            await self._room_hold(SHOWER_SCENARIO_ID, "controller_unknown" if presence not in {"on", "off"} else "controller_idle", evidence)
            return
        started = self._shower.get("absenceStartedAtMs")
        elapsed = 0 if type(started) is not int else max(0, (self._now_ms() - started) // 1000)
        policy = self._policy_service.current.policy
        if elapsed >= policy.shower_absence_seconds:
            for target in (SHOWER_MAIN_TARGET_ID, SHOWER_EXTRA_TARGET_ID, SHOWER_CABINET_TARGET_ID):
                if self._target_state(target) == "on" and self._is_room_owned(SHOWER_SCENARIO_ID, target):
                    if not await self._run_zone_action(SHOWER_SCENARIO_ID, target, "turn_off", None, evidence=evidence):
                        return
        fan_pending = bool(
            self._target_state(SHOWER_FAN_TARGET_ID) == "on" and humidity is not None and humidity <= 55
            and self._is_room_owned(SHOWER_SCENARIO_ID, SHOWER_FAN_TARGET_ID)
        )
        if fan_pending and elapsed >= policy.shower_fan_off_seconds:
            if not await self._run_zone_action(SHOWER_SCENARIO_ID, SHOWER_FAN_TARGET_ID, "turn_off", None, evidence=evidence):
                return
            fan_pending = False
        light_pending = any(
            self._target_state(target) == "on" and self._is_room_owned(SHOWER_SCENARIO_ID, target)
            for target in (SHOWER_MAIN_TARGET_ID, SHOWER_EXTRA_TARGET_ID, SHOWER_CABINET_TARGET_ID)
        )
        if light_pending or fan_pending:
            remaining = min(
                max(0, policy.shower_absence_seconds - elapsed) if light_pending else 3601,
                max(0, policy.shower_fan_off_seconds - elapsed) if fan_pending else 3601,
            )
            await self._set_zone_transition(
                SHOWER_SCENARIO_ID, "shower_absence_pending", evidence=evidence,
                deadline_ms=self._now_ms() + remaining * 1000,
                timer_kind="shower_absence",
            )
            self._schedule_zone_due(SHOWER_SCENARIO_ID)
        else:
            await self._room_hold(
                SHOWER_SCENARIO_ID,
                "controller_unknown"
                if self._target_state(SHOWER_FAN_TARGET_ID) == "on"
                and humidity is None
                else "controller_idle",
                evidence,
            )

    async def _async_reconcile_toilet_due(self, kind: str) -> None:
        motion = self._combined_target_state(TOILET_MOTION_TARGET_IDS)
        evidence = self._room_evidence(motion, self._target_state(TOILET_AWAY_TARGET_ID), (
            self._target_state(TOILET_MAIN_TARGET_ID), self._target_state(TOILET_NIGHT_TARGET_ID)
        ), self._target_state(TOILET_FAN_TARGET_ID))
        if kind == "toilet_absence" and motion == "off":
            for target in (TOILET_MAIN_TARGET_ID, TOILET_NIGHT_TARGET_ID):
                if self._target_state(target) == "on" and self._is_room_owned(TOILET_SCENARIO_ID, target):
                    if not await self._run_zone_action(TOILET_SCENARIO_ID, target, "turn_off", None, evidence=evidence):
                        return
            if self._target_state(TOILET_MAIN_TARGET_ID) == self._target_state(TOILET_NIGHT_TARGET_ID) == "off" and self._target_state(TOILET_FAN_TARGET_ID) == "on" and self._is_room_owned(TOILET_SCENARIO_ID, TOILET_FAN_TARGET_ID):
                await self._set_zone_transition(
                    TOILET_SCENARIO_ID, "toilet_fan_off_pending", evidence=evidence,
                    absence_started_at_ms=self._now_ms(),
                    deadline_ms=self._now_ms() + self._policy_service.current.policy.toilet_fan_off_seconds * 1000,
                    timer_kind="toilet_fan_off",
                )
                self._schedule_zone_due(TOILET_SCENARIO_ID)
                return
        elif kind == "toilet_fan_off" and self._target_state(TOILET_MAIN_TARGET_ID) == self._target_state(TOILET_NIGHT_TARGET_ID) == "off":
            if self._target_state(TOILET_FAN_TARGET_ID) == "on" and self._is_room_owned(TOILET_SCENARIO_ID, TOILET_FAN_TARGET_ID):
                if not await self._run_zone_action(TOILET_SCENARIO_ID, TOILET_FAN_TARGET_ID, "turn_off", None, evidence=evidence):
                    return
        await self._room_hold(
            TOILET_SCENARIO_ID,
            "controller_unknown" if motion == "unknown" else "controller_idle",
            evidence,
        )

    async def _async_reconcile_bathroom_due(self, kind: str) -> None:
        light1, light2 = (self._target_state(target) for target in BATHROOM_LIGHT_TARGET_IDS)
        humidity = self._target_numeric_state(BATHROOM_HUMIDITY_TARGET_ID)
        fan = self._target_state(BATHROOM_FAN_TARGET_ID)
        evidence = self._room_evidence(light1, light2, humidity, fan)
        if (
            kind == "bathroom_day_off" and self._bathroom_band() == "day"
            and light1 == light2 == "off" and humidity is not None
            and humidity < self._policy_service.current.policy.bathroom_humidity_threshold
            and fan == "on" and self._is_room_owned(BATHROOM_SCENARIO_ID, BATHROOM_FAN_TARGET_ID)
        ):
            if not await self._run_zone_action(BATHROOM_SCENARIO_ID, BATHROOM_FAN_TARGET_ID, "turn_off", None, evidence=evidence):
                return
        await self._room_hold(
            BATHROOM_SCENARIO_ID,
            "controller_unknown" if humidity is None and fan == "on" else "controller_idle",
            evidence,
        )

    async def _async_reconcile_office_due(self) -> None:
        program = self._office.get("program")
        if not isinstance(program, Mapping):
            return
        evidence = self._room_evidence(
            self._target_state(OFFICE_LIGHT_TARGET_ID), self._target_state(OFFICE_RELAY_TARGET_ID),
            self._target_numeric_state(OFFICE_LUX_TARGET_ID), self._target_state(SUN_TARGET_ID),
        )
        if self._target_state(OFFICE_LIGHT_TARGET_ID) != "on" or self._target_state(OFFICE_RELAY_TARGET_ID) != "on":
            await self._room_hold(OFFICE_SCENARIO_ID, "controller_idle", evidence, clear_program=True)
            return
        step = int(program["step"])
        if step == 0:
            for action_id, value in (
                ("set_brightness_percent", int(program["brightness"])),
                ("set_color_temperature", int(program["primeKelvin"])),
            ):
                if not await self._run_zone_action(OFFICE_SCENARIO_ID, OFFICE_LIGHT_TARGET_ID, action_id, value, evidence=evidence):
                    return
            next_step = 1
        elif step == 1:
            if not await self._run_zone_action(OFFICE_SCENARIO_ID, OFFICE_LIGHT_TARGET_ID, "set_color_temperature", int(program["targetKelvin"]), evidence=evidence):
                return
            next_step = 2
        else:
            if not await self._run_zone_action(
                OFFICE_SCENARIO_ID,
                OFFICE_LIGHT_TARGET_ID,
                "set_color_temperature",
                int(program["targetKelvin"]),
                evidence=evidence,
                trigger_id="office_temperature_confirm",
            ):
                return
            await self._set_zone_transition(
                OFFICE_SCENARIO_ID, "office_profile_applied", evidence=evidence,
                clear_absence=True, clear_timer=True, clear_program=True,
            )
            return
        updated = dict(program)
        updated["step"] = next_step
        updated["deadlineMs"] = self._now_ms() + self._policy_service.current.policy.office_temperature_settle_seconds * 1000
        await self._set_zone_transition(
            OFFICE_SCENARIO_ID, "office_program", evidence=evidence,
            deadline_ms=int(updated["deadlineMs"]), timer_kind="office_program",
            program=updated,
        )
        self._schedule_zone_due(OFFICE_SCENARIO_ID)

    async def _ensure_room_timer(
        self, scenario_id: str, kind: str, seconds: int,
        evidence: Mapping[str, object],
    ) -> None:
        record = self._record_for_scenario(scenario_id)
        assert record is not None
        if record.get("timerKind") == kind and type(record.get("deadlineMs")) is int:
            self._schedule_zone_due(scenario_id)
            return
        now = self._now_ms()
        transition = {
            "shower_presence": "shower_presence_pending",
            "shower_absence": "shower_absence_pending",
            "toilet_absence": "toilet_absence_pending",
            "toilet_fan_off": "toilet_fan_off_pending",
            "bathroom_day_off": "bathroom_day_off_pending",
        }[kind]
        await self._set_zone_transition(
            scenario_id, transition, evidence=evidence,
            absence_started_at_ms=now, deadline_ms=now + seconds * 1000,
            timer_kind=kind,
        )
        self._schedule_zone_due(scenario_id)

    async def _room_hold(
        self, scenario_id: str, transition: str, evidence: Mapping[str, object],
        *, clear_program: bool = False,
    ) -> None:
        self._cancel_zone_task(scenario_id)
        await self._set_zone_transition(
            scenario_id, transition, evidence=evidence, clear_absence=True,
            clear_timer=True, clear_program=clear_program,
        )

    def _is_room_owned(self, scenario_id: str, target_id: str) -> bool:
        entity_id = self._target_entity_id(target_id)
        if entity_id is not None and self._light_priority.is_owned(entity_id, self._hass):
            return True
        if target_id not in {
            SHOWER_FAN_TARGET_ID,
            TOILET_FAN_TARGET_ID,
            BATHROOM_FAN_TARGET_ID,
        }:
            return False
        record = self._record_for_scenario(scenario_id)
        token = (
            record.get("ownedTargets", {}).get(target_id)
            if record is not None
            and isinstance(record.get("ownedTargets"), Mapping)
            else None
        )
        return isinstance(token, str) and token == self._target_state_revision(target_id)

    def _manual_profile_active(
        self, scenario_id: str, target_ids: tuple[str, ...]
    ) -> bool:
        """Treat any on light without current automation ownership as manual."""

        return bool(
            self._manual_claims(target_ids)
            or any(
                self._target_state(target_id) == "on"
                and not self._is_room_owned(scenario_id, target_id)
                for target_id in target_ids
            )
        )

    @staticmethod
    def _room_dispatch_failed(record: Mapping[str, object]) -> bool:
        """Never repeat a room command whose physical outcome is uncertain."""

        return str(record.get("transition", "")).endswith("failed")

    def _failed_dispatch_blocks(
        self,
        record: Mapping[str, object],
        evidence: Mapping[str, object],
        *,
        recovery: bool,
    ) -> bool:
        """Keep a failed room blocked while its evidence is unchanged.

        A transient source failure must not disable a room forever: a new event
        that produced different evidence re-arms one attempt, while restart
        recovery and byte-identical evidence stay fail-closed.
        """

        if not self._room_dispatch_failed(record):
            return False
        return recovery or self._failed_with_same_evidence(record, evidence)

    def _target_state_revision(self, target_id: str) -> str | None:
        state = self._target_state_object(target_id)
        observed = getattr(state, "last_changed", None)
        if not isinstance(observed, datetime):
            return None
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        return observed.isoformat()

    @staticmethod
    def _room_evidence(*values: object) -> dict[str, object]:
        encoded = [repr(value)[:256] for value in values]
        encoded.extend([None] * (4 - len(encoded)))
        return {
            "motion": encoded[0], "presence": encoded[1],
            "light": encoded[2], "ownershipRevision": encoded[3],
        }

    def _shower_profile(self) -> dict[str, bool] | None:
        minute = self._now_minutes()
        if minute >= 23 * 60 or minute < 9 * 60:
            return {SHOWER_MAIN_TARGET_ID: False, SHOWER_EXTRA_TARGET_ID: False, SHOWER_CABINET_TARGET_ID: True}
        sun = self._target_state(SUN_TARGET_ID)
        if sun == "above_horizon":
            return {SHOWER_MAIN_TARGET_ID: True, SHOWER_EXTRA_TARGET_ID: False, SHOWER_CABINET_TARGET_ID: True}
        if sun == "below_horizon":
            return {SHOWER_MAIN_TARGET_ID: False, SHOWER_EXTRA_TARGET_ID: True, SHOWER_CABINET_TARGET_ID: True}
        return None

    def _toilet_profile_target(self) -> str | None:
        sun = self._target_state(SUN_TARGET_ID)
        if sun == "above_horizon":
            return TOILET_MAIN_TARGET_ID
        minute = self._now_minutes()
        if sun == "below_horizon":
            return TOILET_NIGHT_TARGET_ID if minute >= 23 * 60 or minute < 12 * 60 else TOILET_MAIN_TARGET_ID
        return None

    def _toilet_fan_window(self) -> bool:
        policy = self._policy_service.current.policy
        minute = self._now_minutes()
        return self._clock_minutes(policy.toilet_fan_start) <= minute < self._clock_minutes(policy.toilet_fan_end)

    def _bathroom_band(self) -> str:
        policy = self._policy_service.current.policy
        minute = self._now_minutes()
        quiet = self._clock_minutes(policy.bathroom_quiet_start)
        day = self._clock_minutes(policy.bathroom_day_start)
        night = self._clock_minutes(policy.bathroom_night_start)
        if quiet <= minute < day:
            return "quiet"
        if day <= minute < night:
            return "day"
        return "night"

    def _office_profile(self, lux: float | None, sun: object) -> tuple[str, int, int] | None:
        if lux is None or sun not in {"above_horizon", "below_horizon"}:
            return None
        policy = self._policy_service.current.policy
        if sun == "below_horizon" and self._now_minutes() < 12 * 60:
            return "night", policy.office_night_brightness, policy.office_night_kelvin
        level = "low" if lux < policy.office_low_lux_threshold else "bright" if lux >= policy.office_high_lux_threshold else "medium"
        if sun == "above_horizon":
            return {
                "low": ("day_low", policy.office_day_low_brightness, policy.office_day_low_kelvin),
                "medium": ("day_medium", policy.office_day_medium_brightness, policy.office_day_medium_kelvin),
                "bright": ("day_bright", policy.office_day_bright_brightness, policy.office_day_bright_kelvin),
            }[level]
        return {
            "low": ("evening_dark", policy.office_evening_dark_brightness, policy.office_evening_dark_kelvin),
            "medium": ("evening_medium", policy.office_evening_medium_brightness, policy.office_evening_medium_kelvin),
            "bright": ("evening_bright", policy.office_evening_bright_brightness, policy.office_evening_bright_kelvin),
        }[level]

    async def _start_brightness_sequence(
        self,
        scenario_id: str,
        kind: str,
        start: int,
        target: int,
        *,
        duration_seconds: int | None = None,
        started_at_ms: int | None = None,
        evidence: Mapping[str, object] | None = None,
    ) -> None:
        if start == target:
            return
        duration = duration_seconds or self._policy_service.current.policy.brightness_ramp_seconds
        started = self._now_ms() if started_at_ms is None else started_at_ms
        sequence = {
            "kind": kind,
            "startedAtMs": started,
            "deadlineMs": started + duration * 1000,
            "start": start,
            "target": target,
            "current": start,
        }
        await self._set_zone_transition(
            scenario_id,
            "brightness_sequence",
            evidence=evidence,
            sequence=sequence,
            clear_absence=kind != "fade",
        )
        self._schedule_zone_due(scenario_id)

    async def async_reconcile_zone_due(self, scenario_id: str) -> None:
        async with self._decision_lock:
            record = self._record_for_scenario(scenario_id)
            if record is None or scenario_id == STORAGE_SCENARIO_ID:
                return
            if scenario_id in {
                SHOWER_SCENARIO_ID,
                TOILET_SCENARIO_ID,
                BATHROOM_SCENARIO_ID,
                OFFICE_SCENARIO_ID,
            }:
                await self._async_reconcile_room_due(scenario_id)
                return
            sequence = record.get("sequence")
            if isinstance(sequence, Mapping):
                current = int(sequence["current"])
                due = brightness_sequence_deadline_ms(
                    int(sequence["startedAtMs"]),
                    int(sequence["deadlineMs"]),
                    int(sequence["start"]),
                    int(sequence["target"]),
                    current,
                )
                if due is None:
                    await self._set_zone_transition(
                        scenario_id,
                        "brightness_sequence_completed",
                        clear_sequence=True,
                    )
                    return
                if self._now_ms() < due:
                    self._schedule_zone_due(scenario_id)
                    return
                target_id = (
                    TAMBUR_CHANDELIER_TARGET_ID
                    if scenario_id == TAMBUR_SCENARIO_ID
                    else SMALL_CORRIDOR_CHANDELIER_TARGET_ID
                )
                next_value, remainder = brightness_sequence_step(
                    current,
                    start=int(sequence["start"]),
                    target=int(sequence["target"]),
                    started_at_ms=int(sequence["startedAtMs"]),
                    deadline_ms=int(sequence["deadlineMs"]),
                    now_ms=self._now_ms(),
                )
                if not await self._run_zone_action(
                    scenario_id,
                    target_id,
                    "set_brightness_percent",
                    next_value,
                ):
                    await self._set_zone_transition(
                        scenario_id,
                        "brightness_sequence_failed",
                        clear_sequence=True,
                    )
                    return
                updated = dict(sequence)
                updated["current"] = next_value
                if next_value == int(sequence["target"]):
                    await self._set_zone_transition(
                        scenario_id,
                        "brightness_sequence_completed",
                        clear_sequence=True,
                    )
                    if scenario_id == TAMBUR_SCENARIO_ID:
                        await self._async_handle_tambur_change(
                            recovery=True,
                            allow_activation=True,
                            trigger_entity_id=None,
                            old_state=None,
                            new_state=None,
                        )
                    elif self._target_state(SMALL_CORRIDOR_MOTION_TARGET_ID) == "on":
                        band = self._small_corridor_band()
                        target_kelvin = (
                            self._zone_color_temperature(
                                SMALL_CORRIDOR_SCENARIO_ID, evening=True
                            )
                            if band == "late"
                            else self._zone_color_temperature(
                                SMALL_CORRIDOR_SCENARIO_ID, evening=False
                            )
                        )
                        if (
                            self._target_color_temperature(
                                SMALL_CORRIDOR_CHANDELIER_TARGET_ID
                            )
                            != target_kelvin
                        ):
                            await self._run_zone_action(
                                SMALL_CORRIDOR_SCENARIO_ID,
                                SMALL_CORRIDOR_CHANDELIER_TARGET_ID,
                                "set_color_temperature",
                                target_kelvin,
                            )
                        else:
                            await self._set_zone_transition(
                                SMALL_CORRIDOR_SCENARIO_ID,
                                "occupied_hold",
                                clear_absence=True,
                                clear_lux_candidate=True,
                            )
                else:
                    await self._set_zone_transition(
                        scenario_id,
                        "brightness_sequence",
                        sequence=updated,
                    )
                    current_record = self._record_for_scenario(scenario_id)
                    assert current_record is not None
                    current_record["fractionalRemainder"] = remainder
                    await self._save()
                    self._schedule_zone_due(scenario_id)
                return
            deadline = record.get("deadlineMs")
            if type(deadline) is int and self._now_ms() < deadline:
                self._schedule_zone_due(scenario_id)
                return
            if scenario_id == TAMBUR_SCENARIO_ID:
                await self._async_handle_tambur_change(
                    recovery=True,
                    allow_activation=True,
                    trigger_entity_id=None,
                    old_state=None,
                    new_state=None,
                )
            else:
                # Avoid reacquiring the decision lock via the public wrapper.
                motion = self._target_state(SMALL_CORRIDOR_MOTION_TARGET_ID)
                if motion == "on" and record.get("transition") == "lux_hold_pending":
                    evidence = self._evidence_payload(
                        motion,
                        None,
                        self._target_state(SMALL_CORRIDOR_CHANDELIER_TARGET_ID),
                        None,
                    )
                    if await self._small_corridor_lux_allows_on(evidence):
                        if await self._run_zone_action(
                            SMALL_CORRIDOR_SCENARIO_ID,
                            SMALL_CORRIDOR_CHANDELIER_TARGET_ID,
                            "set_brightness_percent",
                            5,
                            evidence=evidence,
                        ):
                            await self._start_brightness_sequence(
                                SMALL_CORRIDOR_SCENARIO_ID, "ramp", 5, 80
                            )
                else:
                    await self._handle_zone_absence(
                        SMALL_CORRIDOR_SCENARIO_ID,
                        (SMALL_CORRIDOR_RELAY_TARGET_ID, SMALL_CORRIDOR_CHANDELIER_TARGET_ID),
                        self._evidence_payload(
                            motion,
                            None,
                            self._target_state(SMALL_CORRIDOR_CHANDELIER_TARGET_ID),
                            None,
                        ),
                    )

    async def _run_zone_action(
        self,
        scenario_id: str,
        target_id: str,
        action_id: str,
        value: int | None,
        *,
        evidence: Mapping[str, object] | None = None,
        trigger_id: str = "light_action",
    ) -> bool:
        correlation = f"{scenario_id.rsplit('-', 2)[0]}.{uuid.uuid4().hex}"
        await self._set_zone_transition(
            scenario_id,
            "light_action",
            evidence=evidence,
            correlation_id=correlation,
            action={"targetId": target_id, "actionId": action_id, "value": value},
        )
        try:
            result = await self._scenario_service.async_run_scenario(
                scenario_id,
                correlation_id=correlation,
                trigger_context={
                    "source": "scenario_control",
                    "trigger_id": trigger_id,
                    "recovery": False,
                },
            )
        except Exception:
            result = {"status": "failed", "confirmed": False}
        completed = bool(
            isinstance(result, Mapping)
            and result.get("status") == "completed"
            and result.get("confirmed") is True
        )
        if not completed:
            await self._set_zone_transition(
                scenario_id,
                "light_action_failed",
                evidence=evidence,
                clear_sequence=True,
            )
        elif action_id in {"turn_on", "turn_off"}:
            async with self._lock:
                record = self._record_for_scenario(scenario_id)
                if record is not None:
                    owned = dict(record.get("ownedTargets", {}))
                    if action_id == "turn_on":
                        revision = self._target_state_revision(target_id)
                        if revision is not None:
                            owned[target_id] = revision
                    else:
                        owned.pop(target_id, None)
                    updated = self._next_record_for(
                        record,
                        transition=str(record.get("transition", "light_action")),
                        evidence=evidence,
                        owned_targets=owned,
                    )
                    self._assign_record(scenario_id, updated)
                    await self._save()
        return completed

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
            self._cancel_zone_task(TAMBUR_SCENARIO_ID)
            self._cancel_zone_task(SMALL_CORRIDOR_SCENARIO_ID)
            self._cancel_zone_task(SHOWER_SCENARIO_ID)
            self._cancel_zone_task(TOILET_SCENARIO_ID)
            self._cancel_zone_task(BATHROOM_SCENARIO_ID)
            self._cancel_zone_task(OFFICE_SCENARIO_ID)
            async with self._lock:
                self._storage = self._next_record_for(
                    self._storage,
                    transition="policy_changed",
                    policy_revision=document.policy_revision,
                    clear_absence=True,
                    clear_exhaust=True,
                    clear_sequence=True,
                )
                self._tambur = self._next_record_for(
                    self._tambur,
                    transition="policy_changed",
                    policy_revision=document.policy_revision,
                    clear_absence=True,
                    clear_sequence=True,
                    clear_manual_absence=True,
                    clear_lux_candidate=True,
                )
                self._small_corridor = self._next_record_for(
                    self._small_corridor,
                    transition="policy_changed",
                    policy_revision=document.policy_revision,
                    clear_absence=True,
                    clear_sequence=True,
                    clear_manual_absence=True,
                    clear_lux_candidate=True,
                )
                self._shower = self._policy_reset(self._shower, document.policy_revision)
                self._toilet = self._policy_reset(self._toilet, document.policy_revision)
                self._bathroom = self._policy_reset(self._bathroom, document.policy_revision)
                self._office = self._policy_reset(self._office, document.policy_revision)
                await self._save()
        if self._started:
            self._rearm_schedules()

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

    async def _set_zone_transition(
        self,
        scenario_id: str,
        transition: str,
        *,
        evidence: Mapping[str, object] | None = None,
        absence_started_at_ms: int | None = None,
        deadline_ms: int | None = None,
        correlation_id: str | None = None,
        action: Mapping[str, object] | None = None,
        sequence: Mapping[str, object] | None = None,
        manual_absence_started_at_ms: int | None = None,
        lux_candidate_since_ms: int | None = None,
        welcome_armed: bool | None = None,
        timer_kind: str | None = None,
        owned_targets: Mapping[str, str] | None = None,
        program: Mapping[str, object] | None = None,
        clear_absence: bool = False,
        clear_sequence: bool = False,
        clear_manual_absence: bool = False,
        clear_lux_candidate: bool = False,
        clear_timer: bool = False,
        clear_program: bool = False,
    ) -> None:
        async with self._lock:
            current = self._record_for_scenario(scenario_id)
            if current is None or scenario_id == STORAGE_SCENARIO_ID:
                raise ValueError("zone scenario id is invalid")
            updated = self._next_record_for(
                current,
                transition=transition,
                evidence=evidence,
                absence_started_at_ms=absence_started_at_ms,
                deadline_ms=deadline_ms,
                correlation_id=correlation_id,
                action=action,
                sequence=sequence,
                manual_absence_started_at_ms=manual_absence_started_at_ms,
                lux_candidate_since_ms=lux_candidate_since_ms,
                welcome_armed=welcome_armed,
                timer_kind=timer_kind,
                owned_targets=owned_targets,
                program=program,
                clear_absence=clear_absence,
                clear_sequence=clear_sequence,
                clear_manual_absence=clear_manual_absence,
                clear_lux_candidate=clear_lux_candidate,
                clear_timer=clear_timer,
                clear_program=clear_program,
            )
            self._assign_record(scenario_id, updated)
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
        return self._next_record_for(
            self._storage,
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

    def _next_record_for(
        self,
        current: Mapping[str, object],
        *,
        transition: str,
        evidence: Mapping[str, object] | None = None,
        policy_revision: int | None = None,
        absence_started_at_ms: int | None = None,
        deadline_ms: int | None = None,
        exhaust_deadline_ms: int | None = None,
        correlation_id: str | None = None,
        action: Mapping[str, object] | None = None,
        sequence: Mapping[str, object] | None = None,
        manual_absence_started_at_ms: int | None = None,
        lux_candidate_since_ms: int | None = None,
        welcome_armed: bool | None = None,
        timer_kind: str | None = None,
        owned_targets: Mapping[str, str] | None = None,
        program: Mapping[str, object] | None = None,
        clear_absence: bool = False,
        clear_exhaust: bool = False,
        clear_sequence: bool = False,
        clear_manual_absence: bool = False,
        clear_lux_candidate: bool = False,
        clear_timer: bool = False,
        clear_program: bool = False,
    ) -> dict[str, object]:
        record = copy.deepcopy(dict(current))
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
        else:
            if absence_started_at_ms is not None:
                record["absenceStartedAtMs"] = absence_started_at_ms
            if deadline_ms is not None:
                record["deadlineMs"] = deadline_ms
        if clear_exhaust:
            record["exhaustDeadlineMs"] = None
        elif exhaust_deadline_ms is not None:
            record["exhaustDeadlineMs"] = exhaust_deadline_ms
        record["correlationId"] = correlation_id
        record["action"] = copy.deepcopy(dict(action)) if action is not None else None
        if clear_sequence:
            record["sequence"] = None
        elif sequence is not None:
            record["sequence"] = copy.deepcopy(dict(sequence))
        if clear_manual_absence:
            record["manualAbsenceStartedAtMs"] = None
        elif manual_absence_started_at_ms is not None:
            record["manualAbsenceStartedAtMs"] = manual_absence_started_at_ms
        if clear_lux_candidate:
            record["luxCandidateSinceMs"] = None
        elif lux_candidate_since_ms is not None:
            record["luxCandidateSinceMs"] = lux_candidate_since_ms
        if welcome_armed is not None:
            record["welcomeArmed"] = welcome_armed
        if clear_timer:
            record["timerKind"] = None
        elif timer_kind is not None:
            record["timerKind"] = timer_kind
        if owned_targets is not None:
            record["ownedTargets"] = dict(owned_targets)
        if clear_program:
            record["program"] = None
        elif program is not None:
            record["program"] = copy.deepcopy(dict(program))
        return record

    def _record_for_scenario(
        self, scenario_id: str
    ) -> dict[str, object] | None:
        return {
            STORAGE_SCENARIO_ID: self._storage,
            TAMBUR_SCENARIO_ID: self._tambur,
            SMALL_CORRIDOR_SCENARIO_ID: self._small_corridor,
            SHOWER_SCENARIO_ID: self._shower,
            TOILET_SCENARIO_ID: self._toilet,
            BATHROOM_SCENARIO_ID: self._bathroom,
            OFFICE_SCENARIO_ID: self._office,
        }.get(scenario_id)

    def _assign_record(self, scenario_id: str, record: dict[str, object]) -> None:
        attribute = {
            TAMBUR_SCENARIO_ID: "_tambur",
            SMALL_CORRIDOR_SCENARIO_ID: "_small_corridor",
            SHOWER_SCENARIO_ID: "_shower",
            TOILET_SCENARIO_ID: "_toilet",
            BATHROOM_SCENARIO_ID: "_bathroom",
            OFFICE_SCENARIO_ID: "_office",
        }.get(scenario_id)
        if attribute is None:
            raise ValueError("zone scenario id is invalid")
        setattr(self, attribute, record)

    def _all_records(self) -> tuple[dict[str, object], ...]:
        return (
            self._storage,
            self._tambur,
            self._small_corridor,
            self._shower,
            self._toilet,
            self._bathroom,
            self._office,
        )

    def _policy_reset(
        self, record: Mapping[str, object], policy_revision: int
    ) -> dict[str, object]:
        return self._next_record_for(
            record,
            transition="policy_changed",
            policy_revision=policy_revision,
            clear_absence=True,
            clear_exhaust=True,
            clear_sequence=True,
            clear_manual_absence=True,
            clear_lux_candidate=True,
            clear_timer=True,
            clear_program=True,
        )

    async def async_validate_generation(
        self, scenario_id: str, correlation_id: str
    ) -> bool:
        """Recheck the durable transition immediately before physical dispatch."""

        async with self._lock:
            record = self._record_for_scenario(scenario_id)
            return bool(
                record is not None
                and record.get("correlationId") == correlation_id
                and record.get("policyRevision")
                == self._policy_service.current.policy_revision
            )

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

    def _rearm_schedules(self) -> None:
        for unsubscribe in self._schedule_unsubs:
            unsubscribe()
        self._schedule_unsubs.clear()
        if not self._started:
            return
        from homeassistant.helpers.event import async_track_time_change

        policy = self._policy_service.current.policy
        clocks = sorted(
            set(policy.storage_exhaust_times)
            | {
                policy.tambur_main_off,
                policy.small_corridor_main_off,
                policy.toilet_fan_start,
                policy.toilet_fan_end,
                policy.bathroom_quiet_start,
                policy.bathroom_day_start,
                policy.bathroom_night_start,
                "00:00",
                policy.evening_latest,
                "09:00",
                "10:00",
                "23:00",
            }
        )
        for clock in clocks:
            hour, minute = (int(part) for part in clock.split(":"))

            async def due(_now: datetime, scheduled: str = clock) -> None:
                if self._is_active():
                    if scheduled in self._policy_service.current.policy.storage_exhaust_times:
                        await self.async_handle_storage_exhaust_schedule(scheduled)
                    await self.async_handle_controller_clock(scheduled)
                    if scheduled in {
                        self._policy_service.current.policy.toilet_fan_start,
                        self._policy_service.current.policy.toilet_fan_end,
                        "23:00",
                        "00:00",
                    }:
                        await self.async_handle_toilet_change(
                            recovery=True, allow_activation=True
                        )
                    if scheduled in {
                        self._policy_service.current.policy.bathroom_quiet_start,
                        self._policy_service.current.policy.bathroom_day_start,
                        self._policy_service.current.policy.bathroom_night_start,
                    }:
                        await self.async_handle_bathroom_change(
                            recovery=True, allow_activation=True
                        )
                    if scheduled == "00:00":
                        await self.async_handle_office_change(recovery=True)

            self._schedule_unsubs.append(
                async_track_time_change(
                    self._hass,
                    due,
                    hour=hour,
                    minute=minute,
                    second=0,
                )
            )

    async def async_handle_controller_clock(self, clock: str) -> None:
        """Apply exact corridor boundaries without generic scenario timers."""

        async with self._decision_lock:
            policy = self._policy_service.current.policy
            if clock in {policy.evening_latest, "23:00"} and not self._tambur_external():
                if (
                    self._target_state(TAMBUR_MIRROR_TARGET_ID) == "off"
                    and not self._manual_claims((TAMBUR_MIRROR_TARGET_ID,))
                ):
                    await self._run_zone_action(
                        TAMBUR_SCENARIO_ID,
                        TAMBUR_MIRROR_TARGET_ID,
                        "turn_on",
                        None,
                    )
                if clock == policy.evening_latest:
                    await self._start_tambur_evening_cap_if_owned()
            if clock == policy.tambur_main_off and not self._tambur_external():
                self._cancel_zone_task(TAMBUR_SCENARIO_ID)
                if not self._manual_claims(
                    (TAMBUR_CHANDELIER_TARGET_ID, TAMBUR_POINTS_TARGET_ID)
                ):
                    await self._turn_off_owned_targets(
                        TAMBUR_SCENARIO_ID,
                        (TAMBUR_CHANDELIER_TARGET_ID, TAMBUR_POINTS_TARGET_ID),
                    )
            if clock == policy.small_corridor_main_off:
                self._cancel_zone_task(SMALL_CORRIDOR_SCENARIO_ID)
                if not self._manual_claims(
                    (SMALL_CORRIDOR_RELAY_TARGET_ID, SMALL_CORRIDOR_CHANDELIER_TARGET_ID)
                ):
                    await self._turn_off_owned_targets(
                        SMALL_CORRIDOR_SCENARIO_ID,
                        (SMALL_CORRIDOR_CHANDELIER_TARGET_ID, SMALL_CORRIDOR_RELAY_TARGET_ID),
                    )
            if clock == "10:00" and not self._tambur_external():
                mirror_entity = self._target_entity_id(TAMBUR_MIRROR_TARGET_ID)
                if (
                    mirror_entity is not None
                    and self._target_state(TAMBUR_MIRROR_TARGET_ID) == "on"
                    and self._light_priority.is_owned(mirror_entity, self._hass)
                ):
                    await self._run_zone_action(
                        TAMBUR_SCENARIO_ID,
                        TAMBUR_MIRROR_TARGET_ID,
                        "turn_off",
                        None,
                    )
            if clock in {"09:00", "10:00"} and not self._tambur_external():
                await self._async_handle_tambur_change(
                    recovery=True,
                    allow_activation=False,
                    trigger_entity_id=None,
                    old_state=None,
                    new_state=None,
                )

    async def _turn_off_owned_targets(
        self, scenario_id: str, target_ids: tuple[str, ...]
    ) -> None:
        small_dependent = self._target_entity_id(
            SMALL_CORRIDOR_CHANDELIER_TARGET_ID
        )
        small_profile_owned = bool(
            small_dependent is not None
            and self._light_priority.is_owned(small_dependent, self._hass)
        )
        for target_id in target_ids:
            entity_id = self._target_entity_id(target_id)
            if (
                entity_id is None
                or self._target_state(target_id) != "on"
                or not (
                    self._light_priority.is_owned(entity_id, self._hass)
                    or target_id == SMALL_CORRIDOR_RELAY_TARGET_ID
                    and small_profile_owned
                )
            ):
                continue
            if not await self._run_zone_action(
                scenario_id, target_id, "turn_off", None
            ):
                return

    async def _start_tambur_evening_cap_if_owned(self) -> bool:
        """Move an active automatic main light to five percent by 23:00."""

        if (
            self._tambur.get("sequence") is not None
            or self._manual_claims(
                (TAMBUR_CHANDELIER_TARGET_ID, TAMBUR_POINTS_TARGET_ID)
            )
        ):
            return False
        entity_id = self._target_entity_id(TAMBUR_CHANDELIER_TARGET_ID)
        brightness = self._target_brightness_percent(
            TAMBUR_CHANDELIER_TARGET_ID
        )
        if (
            entity_id is None
            or self._target_state(TAMBUR_CHANDELIER_TARGET_ID) != "on"
            or brightness is None
            or brightness <= 5
            or not self._light_priority.is_owned(entity_id, self._hass)
        ):
            return False
        duration = self._seconds_until_clock(
            self._policy_service.current.policy.tambur_main_off
        )
        if duration <= 0:
            return False
        await self._start_brightness_sequence(
            TAMBUR_SCENARIO_ID,
            "cap",
            brightness,
            5,
            duration_seconds=duration,
        )
        return True

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
    def _state_value(value: object) -> str | None:
        state = getattr(value, "state", value)
        return (
            str(state).strip().casefold()
            if state is not None
            else None
        )

    def _target_entity_id(self, target_id: str) -> str | None:
        device = self._catalog_resolver(target_id)
        entity_id = getattr(device, "entity_id", None)
        return entity_id if isinstance(entity_id, str) else None

    def _target_state_object(self, target_id: str) -> object | None:
        entity_id = self._target_entity_id(target_id)
        return self._hass.states.get(entity_id) if entity_id is not None else None

    def _target_numeric_state(self, target_id: str) -> float | None:
        raw = self._target_state(target_id)
        if raw in {None, "unknown", "unavailable"}:
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) else None

    def _target_brightness_percent(self, target_id: str) -> int | None:
        state = self._target_state_object(target_id)
        attributes = getattr(state, "attributes", {})
        if not isinstance(attributes, Mapping):
            return None
        raw = attributes.get("brightness")
        if type(raw) not in {int, float} or isinstance(raw, bool):
            return None
        return max(0, min(100, round(float(raw) * 100 / 255)))

    def _target_color_temperature(self, target_id: str) -> int | None:
        state = self._target_state_object(target_id)
        attributes = getattr(state, "attributes", {})
        if not isinstance(attributes, Mapping):
            return None
        direct = attributes.get("color_temp_kelvin")
        if type(direct) in {int, float} and not isinstance(direct, bool):
            return round(float(direct))
        mired = attributes.get("color_temp")
        if type(mired) in {int, float} and not isinstance(mired, bool) and mired > 0:
            return round(1_000_000 / float(mired))
        return None

    def _combined_target_state(self, target_ids: tuple[str, ...]) -> str:
        states = tuple(self._target_state(target_id) for target_id in target_ids)
        if "on" in states:
            return "on"
        if states and all(state == "off" for state in states):
            return "off"
        return "unknown"

    def _manual_claims(self, target_ids: tuple[str, ...]) -> frozenset[str]:
        entity_ids = frozenset(
            entity_id
            for target_id in target_ids
            if (entity_id := self._target_entity_id(target_id)) is not None
        )
        resolver = getattr(self._light_priority, "manual_claim_entity_ids", None)
        if not callable(resolver):
            return frozenset()
        return resolver(entity_ids)

    @staticmethod
    def _failed_with_same_evidence(
        record: Mapping[str, object], evidence: Mapping[str, object]
    ) -> bool:
        return bool(
            str(record.get("transition", "")).endswith("failed")
            and record.get("evidence") == evidence
        )

    def _local_now(self) -> datetime:
        if self._now is not None:
            value = self._now()
            return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        zone_name = getattr(getattr(self._hass, "config", None), "time_zone", "UTC")
        try:
            zone = ZoneInfo(str(zone_name))
        except ZoneInfoNotFoundError:
            zone = timezone.utc
        return datetime.fromtimestamp(self._now_ms() / 1000, timezone.utc).astimezone(zone)

    @staticmethod
    def _clock_minutes(clock: str) -> int:
        hour, minute = (int(part) for part in clock.split(":"))
        return hour * 60 + minute

    def _now_minutes(self) -> int:
        current = self._local_now()
        return current.hour * 60 + current.minute

    def _is_evening_or_night(self) -> bool:
        now_minutes = self._now_minutes()
        policy = self._policy_service.current.policy
        if (
            now_minutes < 9 * 60
            or now_minutes >= self._clock_minutes(policy.evening_latest)
        ):
            return True
        if self._target_state(SUN_TARGET_ID) != "below_horizon":
            return False
        now = self._local_now()
        changed = getattr(self._target_state_object(SUN_TARGET_ID), "last_changed", None)
        if isinstance(changed, datetime):
            changed = (
                changed.astimezone(now.tzinfo)
                if changed.tzinfo
                else changed.replace(tzinfo=now.tzinfo)
            )
            return changed.date() == now.date() and changed <= now
        # A timestamp-free adapter cannot distinguish last night's state from
        # today's sunset. Noon is the conservative dividing point.
        return now_minutes >= 12 * 60

    def _tambur_band(self) -> str:
        now_minutes = self._now_minutes()
        policy = self._policy_service.current.policy
        off = self._clock_minutes(policy.tambur_main_off)
        if now_minutes >= off or now_minutes < 9 * 60:
            return "night"
        if now_minutes < 10 * 60:
            return "morning"
        if self._is_evening_or_night():
            return "evening"
        return "day"

    def _small_corridor_band(self) -> str:
        now_minutes = self._now_minutes()
        policy = self._policy_service.current.policy
        off = self._clock_minutes(policy.small_corridor_main_off)
        if now_minutes >= off or (
            self._target_state(SUN_TARGET_ID) == "below_horizon"
            and now_minutes < 12 * 60
        ):
            return "night"
        if now_minutes >= 23 * 60:
            return "late"
        return "day"

    def _tambur_cap_percent(self, band: str) -> int:
        now = self._local_now()
        if band == "morning":
            start = now.replace(hour=9, minute=0, second=0, microsecond=0)
            end = now.replace(hour=10, minute=0, second=0, microsecond=0)
            return maximum_brightness(now, start, end, 5, 80)
        if band != "evening":
            return 80
        policy = self._policy_service.current.policy
        latest_hour, latest_minute = (
            int(part) for part in policy.evening_latest.split(":")
        )
        start = now.replace(
            hour=latest_hour, minute=latest_minute, second=0, microsecond=0
        )
        sun = self._target_state_object(SUN_TARGET_ID)
        changed = getattr(sun, "last_changed", None)
        if (
            self._target_state(SUN_TARGET_ID) == "below_horizon"
            and isinstance(changed, datetime)
        ):
            changed = changed.astimezone(now.tzinfo) if changed.tzinfo else changed.replace(tzinfo=now.tzinfo)
            if changed.date() == now.date():
                start = min(start, changed)
        off_hour, off_minute = (int(part) for part in policy.tambur_main_off.split(":"))
        deadline = now.replace(
            hour=off_hour, minute=off_minute, second=0, microsecond=0
        )
        return max(5, min(80, maximum_brightness(now, start, deadline, 80, 5)))

    def _zone_color_temperature(self, scenario_id: str, *, evening: bool) -> int:
        """Translate the shared warmth policy for the inverted tambur lamp."""

        policy = self._policy_service.current.policy
        if not evening:
            return policy.neutral_color_temperature_kelvin
        if scenario_id != TAMBUR_SCENARIO_ID:
            return policy.evening_color_temperature_kelvin
        neutral = policy.neutral_color_temperature_kelvin
        return max(
            1500,
            min(6500, neutral + (neutral - policy.evening_color_temperature_kelvin)),
        )

    def _seconds_until_clock(self, clock: str) -> int:
        now = self._local_now()
        hour, minute = (int(part) for part in clock.split(":"))
        deadline = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return max(0, math.ceil((deadline - now).total_seconds()))

    def _all_controller_entity_ids(self) -> frozenset[str]:
        return (
            self._storage_entity_ids()
            | self._tambur_entity_ids()
            | self._small_corridor_entity_ids()
            | self._shower_entity_ids()
            | self._toilet_entity_ids()
            | self._bathroom_entity_ids()
            | self._office_entity_ids()
        )

    def _tambur_entity_ids(self) -> frozenset[str]:
        return self._entity_ids_for_targets(
            (
                TAMBUR_MOTION_TARGET_ID,
                *TAMBUR_PRESENCE_TARGET_IDS,
                TAMBUR_CHANDELIER_TARGET_ID,
                TAMBUR_POINTS_TARGET_ID,
                TAMBUR_MIRROR_TARGET_ID,
                TAMBUR_POWER_TARGET_ID,
                TAMBUR_ENTRY_DOOR_TARGET_ID,
                SUN_TARGET_ID,
            )
        )

    def _small_corridor_entity_ids(self) -> frozenset[str]:
        return self._entity_ids_for_targets(
            (
                SMALL_CORRIDOR_MOTION_TARGET_ID,
                SMALL_CORRIDOR_LUX_TARGET_ID,
                SMALL_CORRIDOR_RELAY_TARGET_ID,
                SMALL_CORRIDOR_CHANDELIER_TARGET_ID,
                SUN_TARGET_ID,
            )
        )

    def _shower_entity_ids(self) -> frozenset[str]:
        return self._entity_ids_for_targets((
            SHOWER_PRESENCE_TARGET_ID, SHOWER_HUMIDITY_TARGET_ID, SUN_TARGET_ID,
            SHOWER_MAIN_TARGET_ID, SHOWER_EXTRA_TARGET_ID,
            SHOWER_CABINET_TARGET_ID, SHOWER_FAN_TARGET_ID,
        ))

    def _toilet_entity_ids(self) -> frozenset[str]:
        return self._entity_ids_for_targets((
            *TOILET_MOTION_TARGET_IDS, TOILET_MAIN_TARGET_ID,
            TOILET_NIGHT_TARGET_ID, TOILET_FAN_TARGET_ID,
            TOILET_AWAY_TARGET_ID, SUN_TARGET_ID,
        ))

    def _bathroom_entity_ids(self) -> frozenset[str]:
        return self._entity_ids_for_targets((
            *BATHROOM_LIGHT_TARGET_IDS, BATHROOM_HUMIDITY_TARGET_ID,
            BATHROOM_FAN_TARGET_ID,
        ))

    def _office_entity_ids(self) -> frozenset[str]:
        return self._entity_ids_for_targets((
            OFFICE_LIGHT_TARGET_ID, OFFICE_RELAY_TARGET_ID,
            OFFICE_LUX_TARGET_ID, SUN_TARGET_ID,
        ))

    def _entity_ids_for_targets(self, target_ids: tuple[str, ...]) -> frozenset[str]:
        return frozenset(
            entity_id
            for target_id in target_ids
            if (entity_id := self._target_entity_id(target_id)) is not None
        )

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

    def _schedule_zone_due(self, scenario_id: str) -> None:
        if not self._schedule_tasks:
            return
        record = self._record_for_scenario(scenario_id)
        if record is None or str(record.get("transition", "")).endswith("failed"):
            return
        sequence = record.get("sequence")
        deadline: int | None = None
        if isinstance(sequence, Mapping):
            deadline = brightness_sequence_deadline_ms(
                int(sequence["startedAtMs"]),
                int(sequence["deadlineMs"]),
                int(sequence["start"]),
                int(sequence["target"]),
                int(sequence["current"]),
            )
        if deadline is None and type(record.get("deadlineMs")) is int:
            deadline = int(record["deadlineMs"])
        if deadline is None:
            return
        self._cancel_zone_task(scenario_id)

        async def due() -> None:
            try:
                await asyncio.sleep(max(0, deadline - self._now_ms()) / 1000)
                await self.async_reconcile_zone_due(scenario_id)
            finally:
                if self._zone_tasks.get(scenario_id) is asyncio.current_task():
                    self._zone_tasks.pop(scenario_id, None)

        self._zone_tasks[scenario_id] = asyncio.create_task(due())

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

    def _cancel_zone_task(self, scenario_id: str) -> None:
        task = self._zone_tasks.pop(scenario_id, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    def stop_runtime(self) -> None:
        self._cancel_light_task()
        self._cancel_exhaust_task()
        for scenario_id in tuple(self._zone_tasks):
            self._cancel_zone_task(scenario_id)
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
