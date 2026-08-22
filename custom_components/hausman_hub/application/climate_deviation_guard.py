"""Restart-safe guard for climate devices that drift from a confirmed off plan."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import re
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Protocol

from ..domain.climate_ha_calls import (
    ClimateHaCallPlanSnapshot,
    ClimateHaHvacMode,
    ClimateHaService,
    ClimateHaServiceCall,
)


CONTRACT_NAME = "hausman-hub-climate-deviation-guard"
CONTRACT_VERSION = 1
STORAGE_VERSION = 1
MAX_POLICIES = 128
_STABLE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class ClimateDeviationGuardViolation(ValueError):
    """Settings or restart memory are malformed or unsafe."""

    def __init__(self, message: str, *, stale: bool = False) -> None:
        super().__init__(message)
        self.stale = stale


class ClimateDeviationGuardStore(Protocol):
    async def async_load(self) -> object | None: ...

    async def async_save(self, payload: dict[str, object]) -> None: ...


@dataclass(frozen=True, slots=True)
class ClimateDeviationPolicy:
    device_id: str
    mode: str
    grace_seconds: int
    retry_cooldown_seconds: int
    max_retries: int

    def __post_init__(self) -> None:
        if not isinstance(self.device_id, str) or _STABLE_ID.fullmatch(self.device_id) is None:
            raise ClimateDeviationGuardViolation("device id is invalid")
        if self.mode not in {"monitor", "enforce"}:
            raise ClimateDeviationGuardViolation("guard mode is invalid")
        if type(self.grace_seconds) is not int or not 20 <= self.grace_seconds <= 600:
            raise ClimateDeviationGuardViolation("guard grace is invalid")
        if (
            type(self.retry_cooldown_seconds) is not int
            or not 30 <= self.retry_cooldown_seconds <= 3600
        ):
            raise ClimateDeviationGuardViolation("guard cooldown is invalid")
        if type(self.max_retries) is not int or not 1 <= self.max_retries <= 5:
            raise ClimateDeviationGuardViolation("guard retry limit is invalid")

    def wire(self) -> dict[str, object]:
        return {
            "deviceId": self.device_id,
            "mode": self.mode,
            "graceSeconds": self.grace_seconds,
            "retryCooldownSeconds": self.retry_cooldown_seconds,
            "maxRetries": self.max_retries,
        }


@dataclass(frozen=True, slots=True)
class ClimateDeviationState:
    device_id: str
    off_commanded_at: int | None = None
    armed_at: int | None = None
    deviation_at: int | None = None
    retry_count: int = 0
    last_retry_at: int | None = None
    escalated_at: int | None = None
    status: str = "armed"

    def __post_init__(self) -> None:
        if not isinstance(self.device_id, str) or _STABLE_ID.fullmatch(self.device_id) is None:
            raise ClimateDeviationGuardViolation("guard state device id is invalid")
        timestamps = (
            self.off_commanded_at,
            self.armed_at,
            self.deviation_at,
            self.last_retry_at,
            self.escalated_at,
        )
        if any(value is not None and (type(value) is not int or value < 0) for value in timestamps):
            raise ClimateDeviationGuardViolation("guard state timestamp is invalid")
        if type(self.retry_count) is not int or not 0 <= self.retry_count <= 5:
            raise ClimateDeviationGuardViolation("guard state retry count is invalid")
        if self.status not in {"armed", "grace", "observed", "cooldown", "retrying", "escalated"}:
            raise ClimateDeviationGuardViolation("guard state status is invalid")


@dataclass(frozen=True, slots=True)
class ClimateDeviationRetry:
    device_id: str
    room_id: str
    call: ClimateHaServiceCall


class ClimateDeviationGuardService:
    """Persist settings and evaluate only previously confirmed off intents."""

    def __init__(
        self,
        store: ClimateDeviationGuardStore,
        *,
        operation_journal: object | None = None,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self._store = store
        self._operation_journal = operation_journal
        self._now_ms = now_ms or (lambda: time.time_ns() // 1_000_000)
        self._lock = asyncio.Lock()
        self._revision = 0
        self._updated_at = 0
        self._policies: tuple[ClimateDeviationPolicy, ...] = ()
        self._states: tuple[ClimateDeviationState, ...] = ()

    async def async_load(self, allowed_device_ids: Sequence[str]) -> None:
        """Load exact storage and remove policies for devices no longer present."""

        allowed = frozenset(allowed_device_ids)
        payload = await self._store.async_load()
        if payload is None:
            self._updated_at = self._safe_now()
            await self._async_save()
            return
        revision, updated_at, stored_policies, stored_states = _parse_storage(payload)
        now = self._safe_now()
        policies = stored_policies
        states = () if updated_at > now else stored_states
        policies = tuple(policy for policy in policies if policy.device_id in allowed)
        policy_ids = {policy.device_id for policy in policies}
        states = tuple(state for state in states if state.device_id in policy_ids)
        self._revision = revision
        self._updated_at = min(updated_at, now)
        self._policies = policies
        self._states = states
        if (
            updated_at > now
            or len(policies) != len(stored_policies)
            or len(states) != len(stored_states)
        ):
            await self._async_save()

    @property
    def document(self) -> dict[str, object]:
        return {
            "contract": {"name": CONTRACT_NAME, "version": CONTRACT_VERSION},
            "revision": self._revision,
            "updatedAt": _iso_timestamp(self._updated_at),
            "settings": {"devices": [policy.wire() for policy in self._policies]},
        }

    @property
    def active_device_ids(self) -> frozenset[str]:
        """Return armed devices whose retries are owned by this guard."""

        return frozenset(
            state.device_id for state in self._states if state.armed_at is not None
        )

    async def async_replace(
        self,
        expected_revision: object,
        settings: object,
        *,
        allowed_device_ids: Sequence[str],
    ) -> dict[str, object]:
        policies = parse_settings(settings)
        allowed = frozenset(allowed_device_ids)
        if any(policy.device_id not in allowed for policy in policies):
            raise ClimateDeviationGuardViolation("guard device is not a managed air conditioner")
        async with self._lock:
            if type(expected_revision) is not int or expected_revision < 0:
                raise ClimateDeviationGuardViolation("expected revision is invalid")
            if expected_revision != self._revision:
                raise ClimateDeviationGuardViolation("guard settings are stale", stale=True)
            retained_ids = {policy.device_id for policy in policies}
            self._states = tuple(state for state in self._states if state.device_id in retained_ids)
            self._policies = policies
            self._revision += 1
            self._updated_at = self._safe_now()
            await self._async_save()
            return self.document

    async def async_note_off_commands(
        self,
        device_ids: Sequence[str],
        *,
        commanded_at: int,
    ) -> None:
        """Remember accepted off calls, but do not arm before observed read-back."""

        configured = {policy.device_id for policy in self._policies}
        if not configured.intersection(device_ids):
            return
        async with self._lock:
            by_id = {state.device_id: state for state in self._states}
            changed = False
            for device_id in device_ids:
                if device_id not in configured:
                    continue
                prior = by_id.get(device_id, ClimateDeviationState(device_id))
                by_id[device_id] = replace(prior, off_commanded_at=commanded_at)
                changed = True
            if changed:
                self._states = tuple(by_id[key] for key in sorted(by_id))
                self._updated_at = commanded_at
                await self._async_save()

    async def async_confirm_off_commands(
        self,
        device_ids: Sequence[str],
        *,
        confirmed_at: int,
    ) -> None:
        """Arm only devices whose off state was observed after an accepted call."""

        async with self._lock:
            by_id = {state.device_id: state for state in self._states}
            changed = False
            for device_id in device_ids:
                state = by_id.get(device_id)
                if state is None or state.off_commanded_at is None:
                    continue
                candidate = replace(
                    state,
                    armed_at=confirmed_at,
                    deviation_at=None,
                    retry_count=0,
                    last_retry_at=None,
                    escalated_at=None,
                    status="armed",
                )
                if candidate != state:
                    by_id[device_id] = candidate
                    changed = True
            if changed:
                self._states = tuple(by_id[key] for key in sorted(by_id))
                self._updated_at = confirmed_at
                await self._async_save()

    async def async_evaluate(
        self,
        call_plan: ClimateHaCallPlanSnapshot,
        *,
        managed_room_ids: Sequence[str],
        state_lookup: Callable[[str], object | None],
        now_ms: int,
    ) -> tuple[ClimateDeviationRetry, ...]:
        """Observe desired off devices and return bounded enforce retries."""

        if not self._policies:
            return ()
        managed = frozenset(managed_room_ids)
        planned = {
            device.device_id: (device.room_id, _off_call(device.calls))
            for room in call_plan.rooms
            for device in room.devices
            if room.room_id in managed
        }
        retries: list[ClimateDeviationRetry] = []
        events: list[tuple[str, str, int]] = []
        async with self._lock:
            by_id = {state.device_id: state for state in self._states}
            changed = False
            for policy in self._policies:
                state = by_id.get(policy.device_id)
                room_id, off_call = planned.get(policy.device_id, ("", None))
                if off_call is None:
                    if state is not None:
                        by_id.pop(policy.device_id, None)
                        changed = True
                    continue
                observed = _observed_state(state_lookup(off_call.entity_id))
                if state is None or state.off_commanded_at is None:
                    continue
                if observed == "off":
                    candidate = replace(
                        state,
                        armed_at=state.armed_at or now_ms,
                        deviation_at=None,
                        retry_count=0,
                        last_retry_at=None,
                        escalated_at=None,
                        status="armed",
                    )
                elif state.armed_at is None or observed in {"unavailable", "unknown"}:
                    candidate = state
                else:
                    first_deviation = state.deviation_at is None
                    deviation_at = state.deviation_at or now_ms
                    if policy.mode == "monitor":
                        candidate = replace(state, deviation_at=deviation_at, status="observed")
                    elif state.retry_count >= policy.max_retries:
                        escalated_at = state.escalated_at or now_ms
                        candidate = replace(
                            state,
                            deviation_at=deviation_at,
                            escalated_at=escalated_at,
                            status="escalated",
                        )
                        if state.escalated_at is None:
                            events.append((policy.device_id, "escalated", now_ms))
                    elif now_ms < deviation_at + policy.grace_seconds * 1000:
                        candidate = replace(state, deviation_at=deviation_at, status="grace")
                    elif (
                        state.last_retry_at is not None
                        and now_ms < state.last_retry_at + policy.retry_cooldown_seconds * 1000
                    ):
                        candidate = replace(state, deviation_at=deviation_at, status="cooldown")
                    else:
                        candidate = replace(state, deviation_at=deviation_at, status="retrying")
                        retries.append(ClimateDeviationRetry(policy.device_id, room_id, off_call))
                    if first_deviation:
                        events.append((policy.device_id, "observed", now_ms))
                if candidate != state:
                    by_id[policy.device_id] = candidate
                    changed = True
            if changed:
                self._states = tuple(by_id[key] for key in sorted(by_id))
                self._updated_at = now_ms
                await self._async_save()
        for device_id, event, occurred_at in events:
            await self._async_journal_event(device_id, event, occurred_at)
        return tuple(retries)

    async def async_record_retry(
        self,
        device_id: str,
        *,
        attempted_at: int,
        accepted: bool,
    ) -> None:
        """Count every physical attempt before another retry can be selected."""

        async with self._lock:
            by_id = {state.device_id: state for state in self._states}
            state = by_id.get(device_id)
            policy = self._policy(device_id)
            if state is None or policy is None or policy.mode != "enforce":
                return
            retry_count = min(policy.max_retries, state.retry_count + 1)
            escalated = not accepted and retry_count >= policy.max_retries
            by_id[device_id] = replace(
                state,
                retry_count=retry_count,
                last_retry_at=attempted_at,
                escalated_at=attempted_at if escalated else state.escalated_at,
                status="escalated" if escalated else "retrying",
            )
            self._states = tuple(by_id[key] for key in sorted(by_id))
            self._updated_at = attempted_at
            await self._async_save()
        if escalated:
            await self._async_journal_event(device_id, "escalated", attempted_at)

    def device_status(self, device_id: str, observed_state: object) -> dict[str, object] | None:
        policy = self._policy(device_id)
        state = next((item for item in self._states if item.device_id == device_id), None)
        if policy is None or state is None or state.armed_at is None:
            return None
        observed = observed_state if observed_state in {"off", "working", "idle", "unavailable", "unknown"} else "unknown"
        next_retry_at: int | None = None
        if state.status == "grace" and state.deviation_at is not None:
            next_retry_at = state.deviation_at + policy.grace_seconds * 1000
        elif state.status in {"cooldown", "retrying"} and state.last_retry_at is not None:
            next_retry_at = state.last_retry_at + policy.retry_cooldown_seconds * 1000
        return {
            "mode": policy.mode,
            "status": state.status,
            "expected_state": "off",
            "observed_state": observed,
            "retry_count": state.retry_count,
            "max_retries": policy.max_retries,
            "last_deviation_at": state.deviation_at,
            "next_retry_at": next_retry_at,
            "escalated_at": state.escalated_at,
        }

    def _policy(self, device_id: str) -> ClimateDeviationPolicy | None:
        return next((policy for policy in self._policies if policy.device_id == device_id), None)

    def _safe_now(self) -> int:
        value = self._now_ms()
        if type(value) is not int or value < 0:
            raise ClimateDeviationGuardViolation("guard clock is invalid")
        return value

    async def _async_save(self) -> None:
        await self._store.async_save(
            {
                "version": STORAGE_VERSION,
                "revision": self._revision,
                "updated_at": self._updated_at,
                "policies": [policy.wire() for policy in self._policies],
                "states": [_state_payload(state) for state in self._states],
            }
        )

    async def _async_journal_event(self, device_id: str, event: str, occurred_at: int) -> None:
        append = getattr(self._operation_journal, "async_append", None)
        if not callable(append):
            return
        try:
            await append(
                {
                    "correlation_id": f"climate.guard.{device_id}.{occurred_at}",
                    "operation": (
                        "climate_deviation_escalation"
                        if event == "escalated"
                        else "climate_deviation_observed"
                    ),
                    "accepted": False,
                    "confirmed": False,
                    "status": "failed",
                    "reason": (
                        "Устройство не удержало подтверждённое выключенное состояние."
                        if event == "escalated"
                        else "Устройство отклонилось от подтверждённого выключенного состояния."
                    ),
                    "error_code": (
                        "climate_deviation_escalated"
                        if event == "escalated"
                        else "climate_deviation_observed"
                    ),
                }
            )
        except Exception:
            return


def parse_settings(payload: object) -> tuple[ClimateDeviationPolicy, ...]:
    if not isinstance(payload, Mapping) or set(payload) != {"devices"}:
        raise ClimateDeviationGuardViolation("guard settings are invalid")
    devices = payload.get("devices")
    if not isinstance(devices, list) or len(devices) > MAX_POLICIES:
        raise ClimateDeviationGuardViolation("guard devices are invalid")
    policies: list[ClimateDeviationPolicy] = []
    for item in devices:
        if not isinstance(item, Mapping) or set(item) != {
            "deviceId", "mode", "graceSeconds", "retryCooldownSeconds", "maxRetries"
        }:
            raise ClimateDeviationGuardViolation("guard device policy is invalid")
        policies.append(
            ClimateDeviationPolicy(
                device_id=item.get("deviceId"),  # type: ignore[arg-type]
                mode=item.get("mode"),  # type: ignore[arg-type]
                grace_seconds=item.get("graceSeconds"),  # type: ignore[arg-type]
                retry_cooldown_seconds=item.get("retryCooldownSeconds"),  # type: ignore[arg-type]
                max_retries=item.get("maxRetries"),  # type: ignore[arg-type]
            )
        )
    if len(policies) != len({policy.device_id for policy in policies}):
        raise ClimateDeviationGuardViolation("guard device policies are duplicated")
    return tuple(sorted(policies, key=lambda policy: policy.device_id))


def _parse_storage(payload: object) -> tuple[int, int, tuple[ClimateDeviationPolicy, ...], tuple[ClimateDeviationState, ...]]:
    if not isinstance(payload, Mapping) or set(payload) != {
        "version", "revision", "updated_at", "policies", "states"
    } or payload.get("version") != STORAGE_VERSION:
        raise ClimateDeviationGuardViolation("stored guard is invalid")
    revision = payload.get("revision")
    updated_at = payload.get("updated_at")
    if type(revision) is not int or revision < 0 or type(updated_at) is not int or updated_at < 0:
        raise ClimateDeviationGuardViolation("stored guard header is invalid")
    policies = parse_settings({"devices": payload.get("policies")})
    raw_states = payload.get("states")
    if not isinstance(raw_states, list) or len(raw_states) > MAX_POLICIES:
        raise ClimateDeviationGuardViolation("stored guard states are invalid")
    states = tuple(_state_from_payload(item) for item in raw_states)
    if len(states) != len({state.device_id for state in states}):
        raise ClimateDeviationGuardViolation("stored guard states are duplicated")
    return revision, updated_at, policies, states


def _state_payload(state: ClimateDeviationState) -> dict[str, object]:
    return {
        "device_id": state.device_id,
        "off_commanded_at": state.off_commanded_at,
        "armed_at": state.armed_at,
        "deviation_at": state.deviation_at,
        "retry_count": state.retry_count,
        "last_retry_at": state.last_retry_at,
        "escalated_at": state.escalated_at,
        "status": state.status,
    }


def _state_from_payload(payload: object) -> ClimateDeviationState:
    if not isinstance(payload, Mapping) or set(payload) != {
        "device_id", "off_commanded_at", "armed_at", "deviation_at", "retry_count",
        "last_retry_at", "escalated_at", "status"
    }:
        raise ClimateDeviationGuardViolation("stored guard state is invalid")
    return ClimateDeviationState(
        device_id=payload.get("device_id"),  # type: ignore[arg-type]
        off_commanded_at=payload.get("off_commanded_at"),  # type: ignore[arg-type]
        armed_at=payload.get("armed_at"),  # type: ignore[arg-type]
        deviation_at=payload.get("deviation_at"),  # type: ignore[arg-type]
        retry_count=payload.get("retry_count"),  # type: ignore[arg-type]
        last_retry_at=payload.get("last_retry_at"),  # type: ignore[arg-type]
        escalated_at=payload.get("escalated_at"),  # type: ignore[arg-type]
        status=payload.get("status"),  # type: ignore[arg-type]
    )


def _off_call(calls: Sequence[ClimateHaServiceCall]) -> ClimateHaServiceCall | None:
    return next(
        (
            call for call in calls
            if call.service is ClimateHaService.CLIMATE_SET_HVAC_MODE
            and call.hvac_mode is ClimateHaHvacMode.OFF
        ),
        None,
    )


def _observed_state(value: object | None) -> str:
    state = getattr(value, "state", None)
    if state == "off":
        return "off"
    if state == "unavailable":
        return "unavailable"
    if state in {None, "", "unknown"}:
        return "unknown"
    return "working"


def _iso_timestamp(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")
