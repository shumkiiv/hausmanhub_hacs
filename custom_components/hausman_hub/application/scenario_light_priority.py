"""Preserve a user's pre-existing light choice during sensor automation."""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import time
from typing import Protocol

from ..domain.device_power_dependencies import (
    DevicePowerDependency,
    effective_device_state,
)
from ..domain.scenarios import ScenarioAction, ScenarioActionType
from .scenarios import ScenarioCatalog, ScenarioDeviceEntry

MANUAL_LIGHT_PRIORITY_REASON = "manual_light_already_on"
LIGHT_STATE_FRESHNESS_SECONDS = 300
_SENSOR_DOMAINS = frozenset({"binary_sensor", "sensor"})
_TAMBUR_ADAPTIVE_SCENARIO_ID = "system-tambur-adaptive-controller"

_LIGHT_WORDS = (
    "light",
    "lamp",
    "chandelier",
    "mirror",
    "свет",
    "ламп",
    "люстр",
    "подсвет",
    "зеркал",
    "ночник",
)
_NON_LIGHT_SWITCH_WORDS = (
    "fan",
    "vent",
    "pump",
    "valve",
    "outlet",
    "socket",
    "heater",
    "humidifier",
    "siren",
    "lock",
    "boiler",
    "alarm",
    "вытяж",
    "вентил",
    "насос",
    "клапан",
    "розет",
    "обогрев",
    "увлажн",
    "сирен",
    "замок",
    "бойлер",
    "тревог",
    "кондиционер",
)

# This stable catalog target is the second relay of a mixed shower switch.
# Its legacy physical device name contains "доп свет", although the relay is
# wired to the exhaust fan. Keep the safety classification bound to the
# release-owned target ID instead of trusting an editable display label.
_NON_LIGHT_SWITCH_TARGET_IDS = frozenset({"entity_afef5df0e0cae309"})


class LightAutomationPriorityStore(Protocol):
    async def async_load(self) -> object | None: ...

    async def async_save(self, payload: dict[str, object]) -> None: ...


def _text(*values: object) -> str:
    return " ".join(str(value).casefold() for value in values if value)


def _contains_any(value: str, words: tuple[str, ...]) -> bool:
    return any(word in value for word in words)


def _state_revision(state: object | None) -> str | None:
    if state is None:
        return None
    # HA keeps ``last_changed`` stable for attribute-only reports. Evidence
    # revisions must advance for brightness, colour temperature and a fresh
    # report that reasserts the same ``on`` state.
    changed = getattr(state, "last_updated", None) or getattr(
        state, "last_changed", None
    )
    if isinstance(changed, datetime):
        return changed.isoformat()
    return None


def _ownership_revision(state: object | None) -> str | None:
    """Track state transitions without treating attribute telemetry as manual input."""

    if state is None:
        return None
    changed = getattr(state, "last_changed", None)
    if isinstance(changed, datetime):
        return changed.isoformat()
    return _state_revision(state)


def _state_is_fresh(state: object | None) -> bool:
    """Trust a raw manual-on signal only inside a bounded HA evidence window."""

    if state is None:
        return False
    attributes = getattr(state, "attributes", {})
    if (
        getattr(state, "assumed_state", False) is True
        or getattr(state, "is_assumed_state", False) is True
        or isinstance(attributes, Mapping)
        and (
            attributes.get("assumed_state") is True
            or attributes.get("restored") is True
            or attributes.get("cached") is True
            or attributes.get("cache") is True
            or attributes.get("evidence_source") in {"restore", "cache"}
        )
    ):
        return False
    observed = getattr(state, "last_changed", None) or getattr(
        state, "last_updated", None
    )
    if not isinstance(observed, datetime):
        # Small unit-test adapters do not carry HA timestamps. Real HA states do.
        return True
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - observed).total_seconds() <= (
        LIGHT_STATE_FRESHNESS_SECONDS
    )


def _reassert_identity(state: object | None) -> tuple[str, int] | None:
    """Return one shared revision/sequence identity for stale-light recovery."""

    if state is None:
        return None
    observed = getattr(state, "last_updated", None) or getattr(
        state, "last_changed", None
    )
    if not isinstance(observed, datetime):
        return None
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    sequence = max(0, int(observed.timestamp() * 1000))
    raw_revision = _state_revision(state)
    if not isinstance(raw_revision, str):
        raw_revision = f"evidence.{sequence}"
    normalized = "".join(
        character
        if character.isalnum() or character in "._:-"
        else "."
        for character in raw_revision
    )[:128]
    return (normalized or f"evidence.{sequence}", sequence)


def _sensor_triggered(
    trigger_context: Mapping[str, object] | None,
    catalog: ScenarioCatalog,
) -> bool:
    if not isinstance(trigger_context, Mapping):
        return False
    source = trigger_context.get("source")
    if source not in {"device_state", "nested"}:
        return False
    target_id = trigger_context.get("target_id")
    device = catalog.device(target_id) if isinstance(target_id, str) else None
    if device is None:
        return False
    domain = device.entity_id.split(".", 1)[0]
    return domain in _SENSOR_DOMAINS


@dataclass(frozen=True, slots=True)
class LightPriorityPlan:
    """One run-local classification and pre-command state snapshot."""

    light_action_ids: frozenset[str]
    light_target_ids: frozenset[str]
    guarded_target_ids: frozenset[str]
    manual_target_ids: frozenset[str]
    manual_claims: Mapping[str, str]
    clear_entity_ids: frozenset[str]
    authority_tokens: Mapping[str, tuple[object, ...] | None]
    pre_states: Mapping[str, str | None]
    pre_revisions: Mapping[str, str | None]
    pre_freshness: Mapping[str, bool]
    only_lighting_effects: bool

    @property
    def applied(self) -> bool:
        return bool(self.guarded_target_ids)


class LightAutomationPriority:
    """Track confirmed automatic light ownership across HA restarts."""

    def __init__(self, store: LightAutomationPriorityStore | None = None) -> None:
        self._store = store
        self._owned_revisions: dict[str, str | None] = {}
        self._owned_records: dict[str, dict[str, object]] = {}
        self._manual_records: dict[str, dict[str, object]] = {}
        self._reassert_records: dict[str, dict[str, object]] = {}
        self._dirty = False
        self._authority_lock = asyncio.Lock()

    async def async_load(self) -> None:
        if self._store is None:
            return
        payload = await self._store.async_load()
        if payload is None:
            return
        if not isinstance(payload, Mapping) or payload.get("version") != 1:
            raise RuntimeError("light priority store is corrupt")
        records = payload.get("records")
        if not isinstance(records, list):
            raise RuntimeError("light priority store is corrupt")
        if len(records) > 256:
            raise RuntimeError("light priority store is over capacity")
        now_ms = time.time_ns() // 1_000_000
        for item in records:
            if not isinstance(item, Mapping):
                raise RuntimeError("light priority store is corrupt")
            entity_id = item.get("entityId")
            target_id = item.get("targetId")
            revision = item.get("evidenceRevision")
            expires_at = item.get("expiresAt")
            ownership = item.get("ownership", "automation")
            if (
                not isinstance(entity_id, str)
                or not isinstance(target_id, str)
                or revision is not None and not isinstance(revision, str)
                or ownership not in {"automation", "manual", "reassert"}
            ):
                raise RuntimeError("light priority store is corrupt")
            record = dict(item)
            if ownership == "manual":
                if (
                    entity_id in self._manual_records
                    or entity_id in self._owned_records
                    or entity_id in self._reassert_records
                ):
                    raise RuntimeError("light priority store has duplicates")
                self._manual_records[entity_id] = record
            elif ownership == "automation":
                if type(expires_at) is not int:
                    raise RuntimeError("light priority store is corrupt")
                if expires_at <= now_ms:
                    continue
                if (
                    entity_id in self._owned_records
                    or entity_id in self._manual_records
                    or entity_id in self._reassert_records
                ):
                    raise RuntimeError("light priority store has duplicates")
                self._owned_records[entity_id] = record
                self._owned_revisions[entity_id] = revision
            else:
                if (
                    revision is None
                    or type(item.get("attemptedAt")) is not int
                    or (
                        item.get("claimId") is not None
                        and (
                            not isinstance(item.get("claimId"), str)
                            or not str(item.get("claimId"))
                            or len(str(item.get("claimId"))) > 128
                        )
                    )
                ):
                    raise RuntimeError("light priority store is corrupt")
                if (
                    entity_id in self._owned_records
                    or entity_id in self._manual_records
                    or entity_id in self._reassert_records
                ):
                    raise RuntimeError("light priority store has duplicates")
                self._reassert_records[entity_id] = record
        if getattr(self._store, "recovered_previous", False):
            # N-1 may predate a newer manual command. It can conservatively
            # preserve blockers, but it must never restore authority to turn a
            # light off automatically.
            self._owned_records.clear()
            self._owned_revisions.clear()
            await self._store.async_save(self._payload())

    def plan(
        self,
        actions: Sequence[ScenarioAction],
        catalog: ScenarioCatalog,
        hass: object,
        *,
        scenario_id: str = "",
        scenario_text: str,
        trigger_context: Mapping[str, object] | None,
        power_dependencies: Mapping[str, DevicePowerDependency],
    ) -> LightPriorityPlan:
        light_actions: list[tuple[ScenarioAction, ScenarioDeviceEntry]] = []
        has_non_lighting_effect = False
        for action in actions:
            classified = self._lighting_device(action, catalog, scenario_text)
            if classified is not None:
                light_actions.append((action, classified))
            elif action.type is not ScenarioActionType.DELAY:
                has_non_lighting_effect = True

        light_target_ids = frozenset(
            action.target_id
            for action, _device in light_actions
            if action.target_id is not None
        )
        light_action_ids = frozenset(action.id for action, _device in light_actions)
        pre_states = {
            device.entity_id: self._effective_state(
                device.entity_id, hass, power_dependencies
            )
            for _action, device in light_actions
        }
        # A persisted manual claim is only a blocker while the light is
        # actually on. Clear it only from fresh off evidence. Restored or
        # cached states must not grant automation authority after a restart.
        for entity_id in pre_states:
            if (
                pre_states.get(entity_id) == "off"
                and entity_id in self._manual_records
                and self._effective_off_is_fresh(
                    entity_id, hass, power_dependencies
                )
            ):
                self._discard(entity_id)
        clear_entity_ids = frozenset(
            entity_id
            for entity_id, state in pre_states.items()
            if state == "off"
            and self._effective_off_is_fresh(
                entity_id, hass, power_dependencies
            )
        )
        authority_tokens = {
            entity_id: self._authority_token(entity_id) for entity_id in pre_states
        }
        pre_revisions = {
            entity_id: _state_revision(self._state(hass, entity_id))
            for entity_id in pre_states
        }
        pre_freshness = {
            entity_id: _state_is_fresh(self._state(hass, entity_id))
            for entity_id in pre_states
        }

        guarded_target_ids: frozenset[str] = frozenset()
        manual_target_ids: frozenset[str] = frozenset()
        manual_claims: dict[str, str] = {}
        has_activating_action = any(
            (allowed := device.action(action.action_id or "")) is not None
            and (allowed.service == "turn_on" or action.action_id == "toggle")
            for action, device in light_actions
        )
        if (
            light_actions
            and has_activating_action
            and _sensor_triggered(trigger_context, catalog)
        ):
            upstream_sources = {
                dependency.power_source_entity_id
                for _action, device in light_actions
                if (dependency := power_dependencies.get(device.entity_id)) is not None
            }
            visible = [
                (action, device)
                for action, device in light_actions
                if device.entity_id not in upstream_sources
            ]
            manual_target_ids = frozenset(
                action.target_id
                for action, device in visible
                if action.target_id is not None
                and pre_states.get(device.entity_id) == "on"
                and (
                    device.entity_id in self._manual_records
                    or (
                        pre_freshness.get(device.entity_id) is True
                        and not self._is_owned(device.entity_id, hass)
                    )
                )
            )
            if manual_target_ids:
                # The tambur chandelier and spots are complementary sources.
                # A manual chandelier must stay untouched without suppressing
                # the spots that independently follow presence. Other light
                # profiles keep the conservative whole-branch guard because
                # their sources may be interchangeable.
                guarded_target_ids = (
                    manual_target_ids
                    if scenario_id == _TAMBUR_ADAPTIVE_SCENARIO_ID
                    else light_target_ids
                )
                manual_claims = {
                    action.target_id: device.entity_id
                    for action, device in visible
                    if action.target_id in manual_target_ids
                    and action.target_id is not None
                }

        return LightPriorityPlan(
            light_action_ids=light_action_ids,
            light_target_ids=light_target_ids,
            guarded_target_ids=guarded_target_ids,
            manual_target_ids=manual_target_ids,
            manual_claims=manual_claims,
            clear_entity_ids=clear_entity_ids,
            authority_tokens=authority_tokens,
            pre_states=pre_states,
            pre_revisions=pre_revisions,
            pre_freshness=pre_freshness,
            only_lighting_effects=bool(light_actions) and not has_non_lighting_effect,
        )

    async def note_results(
        self,
        actions: Sequence[ScenarioAction],
        receipts: Sequence[Mapping[str, object]],
        plan: LightPriorityPlan,
        catalog: ScenarioCatalog,
        hass: object,
        *,
        automatic: bool,
        dry_run: bool,
        scenario_id: str = "",
        run_id: str = "",
        authority_lock_held: bool = False,
    ) -> None:
        if authority_lock_held:
            await self._note_results_unlocked(
                actions, receipts, plan, catalog, hass, automatic=automatic,
                dry_run=dry_run, scenario_id=scenario_id, run_id=run_id,
            )
            return
        async with self._authority_lock:
            await self._note_results_unlocked(
                actions,
                receipts,
                plan,
                catalog,
                hass,
                automatic=automatic,
                dry_run=dry_run,
                scenario_id=scenario_id,
                run_id=run_id,
            )

    async def _note_results_unlocked(
        self,
        actions: Sequence[ScenarioAction],
        receipts: Sequence[Mapping[str, object]],
        plan: LightPriorityPlan,
        catalog: ScenarioCatalog,
        hass: object,
        *,
        automatic: bool,
        dry_run: bool,
        scenario_id: str = "",
        run_id: str = "",
    ) -> None:
        if dry_run:
            return
        for entity_id in plan.clear_entity_ids:
            if self._authority_token(entity_id) == plan.authority_tokens.get(
                entity_id
            ):
                self._discard(entity_id)
        by_action_id = {
            str(receipt.get("action_id")): receipt for receipt in receipts
        }
        for target_id, entity_id in plan.manual_claims.items():
            action_receipt = next(
                (
                    by_action_id.get(action.id)
                    for action in actions
                    if action.target_id == target_id
                    and action.id in plan.light_action_ids
                ),
                None,
            )
            if (
                isinstance(action_receipt, Mapping)
                and action_receipt.get("status") == "completed"
                and action_receipt.get("skipped") is True
            ):
                self._set_manual(entity_id, target_id, hass)
        for action in actions:
            if action.id not in plan.light_action_ids or action.target_id is None:
                continue
            receipt = by_action_id.get(action.id)
            if receipt is None or receipt.get("status") != "completed":
                continue
            if receipt.get("skipped") is True:
                if not automatic:
                    device = catalog.device(action.target_id)
                    allowed = device.action(action.action_id or "") if device else None
                    if (
                        device is not None
                        and allowed is not None
                        and allowed.service == "turn_on"
                        and plan.pre_states.get(device.entity_id) == "on"
                    ):
                        self._set_manual(device.entity_id, action.target_id, hass)
                continue
            device = catalog.device(action.target_id)
            allowed = device.action(action.action_id or "") if device else None
            if device is None or allowed is None:
                continue
            if allowed.service == "turn_off":
                if automatic and device.entity_id in self._manual_records:
                    continue
                self._discard(device.entity_id)
                continue
            if allowed.service != "turn_on" and action.action_id != "toggle":
                continue
            if receipt.get("confirmed") is not True:
                continue
            if not automatic:
                self._set_manual(device.entity_id, action.target_id, hass)
            elif (
                (
                    plan.pre_states.get(device.entity_id) != "on"
                    or (
                        plan.pre_freshness.get(device.entity_id) is not True
                        and isinstance(receipt.get("read_back"), Mapping)
                        and receipt["read_back"].get("isNewEvidence") is True
                    )
                )
                and device.entity_id not in self._manual_records
            ):
                self._ensure_room(device.entity_id)
                revision = _ownership_revision(self._state(hass, device.entity_id))
                now_ms = time.time_ns() // 1_000_000
                self._owned_revisions[device.entity_id] = revision
                self._owned_records[device.entity_id] = {
                    "entityId": device.entity_id,
                    "targetId": action.target_id,
                    "scenarioId": scenario_id,
                    "runId": run_id,
                    "actionId": action.action_id,
                    "ownership": "automation",
                    "confirmedAt": now_ms,
                    "evidenceRevision": revision,
                    "expiresAt": now_ms + 24 * 60 * 60 * 1000,
                }
                self._reassert_records.pop(device.entity_id, None)
                self._dirty = True
        await self._async_save_if_dirty()

    def authority_lock(self) -> asyncio.Lock:
        """Serialize manual claims with each automatic lighting side effect."""

        return self._authority_lock

    async def async_has_manual_claim(
        self,
        plan: LightPriorityPlan,
        catalog: ScenarioCatalog,
        hass: object,
        power_dependencies: Mapping[str, DevicePowerDependency] | None = None,
        *,
        target_ids: frozenset[str] | None = None,
    ) -> bool:
        """Recheck API claims and fresh physical on events under the lock."""

        for target_id in target_ids or plan.light_target_ids:
            device = catalog.device(target_id)
            if device is None:
                continue
            entity_id = device.entity_id
            state = self._state(hass, entity_id)
            if entity_id in self._manual_records:
                effective_state = self._effective_state(
                    entity_id,
                    hass,
                    power_dependencies or {},
                )
                if effective_state == "off" and self._effective_off_is_fresh(
                    entity_id,
                    hass,
                    power_dependencies or {},
                ):
                    # The plan normally clears this stale claim before the
                    # authority lock is acquired. Recheck here as well so a
                    # concurrent manual claim followed by a physical or
                    # power-source off does not consume the first
                    # presence-triggered turn-on.
                    self._discard(entity_id)
                    await self._async_save_if_dirty()
                else:
                    return True
            if (
                str(getattr(state, "state", "unknown")) == "on"
                and _state_is_fresh(state)
                and not self._is_owned(entity_id, hass)
                and (
                    plan.pre_states.get(entity_id) != "on"
                    or _state_revision(state) != plan.pre_revisions.get(entity_id)
                )
            ):
                self._set_manual(entity_id, target_id, hass)
                await self._async_save_if_dirty()
                return True
        return False

    async def async_validate_reassert(
        self,
        target_id: str,
        catalog: ScenarioCatalog,
        hass: object,
        *,
        expected_revision: str,
        expected_sequence: int,
    ) -> bool:
        """Revalidate exact stale unknown evidence at the dispatch boundary."""

        device = catalog.device(target_id)
        if device is None or device.entity_id in self._manual_records:
            return False
        state = self._state(hass, device.entity_id)
        if (
            str(getattr(state, "state", "unknown")) != "on"
            or _state_is_fresh(state)
            or self._is_owned(device.entity_id, hass)
        ):
            return False
        identity = _reassert_identity(state)
        if identity is None:
            return False
        return (
            identity[0] == expected_revision
            and identity[1] == expected_sequence
        )

    def reassert_identity(
        self, target_id: str, catalog: ScenarioCatalog, hass: object
    ) -> tuple[str, int] | None:
        """Expose the exact evidence identity used by API and scenario paths."""

        device = catalog.device(target_id)
        if device is None:
            return None
        return _reassert_identity(self._state(hass, device.entity_id))

    async def async_note_reassert(
        self,
        target_id: str,
        action_id: str,
        catalog: ScenarioCatalog,
        hass: object,
        *,
        correlation_id: str,
        confirmed: bool,
    ) -> None:
        """Persist confirmed automatic recovery ownership after new evidence."""

        if not confirmed:
            return
        device = catalog.device(target_id)
        if device is None or device.entity_id in self._manual_records:
            return
        action = ScenarioAction(
            id="reassert_ownership",
            type=ScenarioActionType.DEVICE_ACTION,
            target_id=target_id,
            action_id=action_id,
        )
        if not self.is_lighting_action(action, catalog):
            return
        self._ensure_room(device.entity_id)
        revision = _ownership_revision(self._state(hass, device.entity_id))
        now_ms = time.time_ns() // 1_000_000
        self._owned_revisions[device.entity_id] = revision
        self._owned_records[device.entity_id] = {
            "entityId": device.entity_id,
            "targetId": target_id,
            "scenarioId": "device_action_reassert",
            "runId": correlation_id,
            "actionId": action_id,
            "ownership": "automation",
            "confirmedAt": now_ms,
            "evidenceRevision": revision,
            "expiresAt": now_ms + 24 * 60 * 60 * 1000,
        }
        self._reassert_records.pop(device.entity_id, None)
        self._dirty = True
        await self._async_save_if_dirty()

    async def async_claim_stale_reassert(
        self,
        target_id: str,
        catalog: ScenarioCatalog,
        hass: object,
        *,
        claim_id: str | None = None,
    ) -> bool:
        """Persist the one allowed automatic turn-on attempt per evidence revision."""

        async with self._authority_lock:
            return await self._async_claim_stale_reassert_unlocked(
                target_id, catalog, hass, claim_id=claim_id
            )

    async def _async_claim_stale_reassert_unlocked(
        self,
        target_id: str,
        catalog: ScenarioCatalog,
        hass: object,
        *,
        claim_id: str | None = None,
    ) -> bool:
        """Claim stale evidence while the shared authority lock is held."""

        device = catalog.device(target_id)
        if device is None or device.entity_id in self._manual_records:
            return False
        state = self._state(hass, device.entity_id)
        revision = _state_revision(state)
        if (
            state is None
            or str(getattr(state, "state", "unknown")) != "on"
            or _state_is_fresh(state)
            or self._is_owned(device.entity_id, hass)
            or revision is None
        ):
            return False
        existing = self._reassert_records.get(device.entity_id)
        if existing is not None and existing.get("evidenceRevision") == revision:
            return bool(
                isinstance(claim_id, str)
                and existing.get("claimId") == claim_id
            )
        self._ensure_room(device.entity_id)
        record: dict[str, object] = {
            "entityId": device.entity_id,
            "targetId": target_id,
            "ownership": "reassert",
            "evidenceRevision": revision,
            "attemptedAt": time.time_ns() // 1_000_000,
        }
        if isinstance(claim_id, str) and claim_id:
            record["claimId"] = claim_id[:128]
        self._reassert_records[device.entity_id] = record
        self._dirty = True
        await self._async_save_if_dirty()
        return True

    async def note_direct_action(
        self,
        target_id: str,
        action_id: str,
        receipt: Mapping[str, object],
        catalog: ScenarioCatalog,
        hass: object,
    ) -> None:
        device = catalog.device(target_id)
        allowed = device.action(action_id) if device else None
        if (
            device is None
            or allowed is None
            or allowed.domain not in {"light", "switch"}
            or receipt.get("status") != "completed"
        ):
            return
        if allowed.service in {"turn_on", "turn_off"} or action_id == "toggle":
            if allowed.service == "turn_off":
                self._discard(device.entity_id)
            else:
                self._set_manual(device.entity_id, target_id, hass)
            await self._async_save_if_dirty()

    async def async_prepare_direct_action(
        self,
        target_id: str,
        action_id: str,
        catalog: ScenarioCatalog,
        hass: object,
    ) -> bool:
        """Persist manual authority before any physical light dispatch."""

        token = await self.async_begin_direct_action(
            target_id, action_id, catalog, hass
        )
        return token is not None

    async def async_begin_direct_action(
        self,
        target_id: str,
        action_id: str,
        catalog: ScenarioCatalog,
        hass: object,
    ) -> dict[str, object] | None:
        """Persist provisional manual authority immediately before dispatch."""

        async with self._authority_lock:
            return await self._async_begin_direct_action_unlocked(
                target_id, action_id, catalog, hass
            )

    async def _async_begin_direct_action_unlocked(
        self,
        target_id: str,
        action_id: str,
        catalog: ScenarioCatalog,
        hass: object,
    ) -> dict[str, object] | None:
        """Begin direct action while the shared authority lock is held."""

        device = catalog.device(target_id)
        allowed = device.action(action_id) if device else None
        action = ScenarioAction(
            id="manual_pre_dispatch",
            type=ScenarioActionType.DEVICE_ACTION,
            target_id=target_id,
            action_id=action_id,
        )
        if (
            device is None
            or allowed is None
            or not self.is_lighting_action(action, catalog)
        ):
            return None
        previous = {
            "owned": copy.deepcopy(self._owned_records.get(device.entity_id)),
            "ownedRevision": self._owned_revisions.get(device.entity_id),
            "manual": copy.deepcopy(self._manual_records.get(device.entity_id)),
            "reassert": copy.deepcopy(self._reassert_records.get(device.entity_id)),
        }
        if allowed.service == "turn_off":
            self._discard(device.entity_id)
        else:
            self._set_manual(device.entity_id, target_id, hass)
        try:
            await self._async_save_if_dirty()
        except Exception:
            self._restore_direct_action_snapshot(device.entity_id, previous)
            raise
        return {"entityId": device.entity_id, "previous": previous}

    async def async_rollback_direct_action(self, token: Mapping[str, object]) -> None:
        """Restore the authority snapshot when descriptor or service dispatch fails."""

        async with self._authority_lock:
            await self._async_rollback_direct_action_unlocked(token)

    async def _async_rollback_direct_action_unlocked(
        self, token: Mapping[str, object]
    ) -> None:
        """Restore a provisional claim while the shared lock is held."""

        entity_id = token.get("entityId")
        previous = token.get("previous")
        if not isinstance(entity_id, str) or not isinstance(previous, Mapping):
            return
        self._restore_direct_action_snapshot(entity_id, previous)
        self._dirty = True
        await self._async_save_if_dirty()

    def _restore_direct_action_snapshot(
        self, entity_id: str, previous: Mapping[str, object]
    ) -> None:
        self._owned_records.pop(entity_id, None)
        self._owned_revisions.pop(entity_id, None)
        self._manual_records.pop(entity_id, None)
        self._reassert_records.pop(entity_id, None)
        owned = previous.get("owned")
        if isinstance(owned, Mapping):
            self._owned_records[entity_id] = copy.deepcopy(dict(owned))
            self._owned_revisions[entity_id] = previous.get("ownedRevision")
        manual = previous.get("manual")
        if isinstance(manual, Mapping):
            self._manual_records[entity_id] = copy.deepcopy(dict(manual))
        reassert = previous.get("reassert")
        if isinstance(reassert, Mapping):
            self._reassert_records[entity_id] = copy.deepcopy(dict(reassert))
        self._dirty = True

    def _lighting_device(
        self,
        action: ScenarioAction,
        catalog: ScenarioCatalog,
        scenario_text: str,
    ) -> ScenarioDeviceEntry | None:
        if (
            action.type is not ScenarioActionType.DEVICE_ACTION
            or action.target_id is None
            or action.action_id is None
        ):
            return None
        device = catalog.device(action.target_id)
        allowed = device.action(action.action_id) if device else None
        if device is None or allowed is None:
            return None
        if allowed.domain == "light":
            return device
        if allowed.domain != "switch":
            return None
        if action.target_id in _NON_LIGHT_SWITCH_TARGET_IDS:
            return None
        target_text = _text(
            device.name,
            device.physical_name,
            device.capability_name,
            device.device_type_name,
        )
        if _contains_any(target_text, _LIGHT_WORDS):
            return device
        if _contains_any(target_text, _NON_LIGHT_SWITCH_WORDS):
            return None
        return device if _contains_any(scenario_text, _LIGHT_WORDS) else None

    def _is_owned(self, entity_id: str, hass: object) -> bool:
        if entity_id not in self._owned_revisions:
            return False
        record = self._owned_records.get(entity_id)
        expires_at = record.get("expiresAt") if isinstance(record, Mapping) else None
        if type(expires_at) is not int or expires_at <= time.time_ns() // 1_000_000:
            return False
        recorded = self._owned_revisions[entity_id]
        current = _ownership_revision(self._state(hass, entity_id))
        if recorded is not None and current is not None and recorded != current:
            return False
        return True

    def is_owned(self, entity_id: str, hass: object) -> bool:
        """Expose confirmed runtime ownership to the shared command coordinator."""

        return self._is_owned(entity_id, hass)

    def ownership_revision(self, entity_id: str, hass: object) -> str | None:
        """Return current confirmed ownership revision or no authority."""

        if not self._is_owned(entity_id, hass):
            return None
        return self._owned_revisions.get(entity_id)

    async def async_clear_ownership(self, entity_id: str) -> None:
        async with self._authority_lock:
            await self._async_clear_ownership_unlocked(entity_id)

    async def _async_clear_ownership_unlocked(self, entity_id: str) -> None:
        """Clear automation ownership while the shared authority lock is held."""

        self._discard(entity_id)
        await self._async_save_if_dirty()

    def is_lighting_action(
        self,
        action: ScenarioAction,
        catalog: ScenarioCatalog,
        *,
        scenario_text: str = "",
    ) -> bool:
        return self._lighting_device(action, catalog, scenario_text) is not None

    def _discard(self, entity_id: str) -> None:
        removed = self._owned_revisions.pop(entity_id, None)
        record = self._owned_records.pop(entity_id, None)
        manual = self._manual_records.pop(entity_id, None)
        reassert = self._reassert_records.pop(entity_id, None)
        if (
            removed is not None
            or record is not None
            or manual is not None
            or reassert is not None
        ):
            self._dirty = True

    def _authority_token(self, entity_id: str) -> tuple[object, ...] | None:
        record = self._manual_records.get(entity_id)
        if record is None:
            record = self._owned_records.get(entity_id)
        if record is None:
            return None
        return (
            record.get("ownership"),
            record.get("confirmedAt"),
            record.get("evidenceRevision"),
            record.get("targetId"),
        )

    def _set_manual(self, entity_id: str, target_id: str, hass: object) -> None:
        self._ensure_room(entity_id)
        self._owned_revisions.pop(entity_id, None)
        self._owned_records.pop(entity_id, None)
        self._reassert_records.pop(entity_id, None)
        revision = _ownership_revision(self._state(hass, entity_id))
        record = {
            "entityId": entity_id,
            "targetId": target_id,
            "ownership": "manual",
            "evidenceRevision": revision,
            "confirmedAt": time.time_ns() // 1_000_000,
        }
        if self._manual_records.get(entity_id) != record:
            self._manual_records[entity_id] = record
            self._dirty = True

    def _ensure_room(self, entity_id: str) -> None:
        if (
            entity_id in self._owned_records
            or entity_id in self._manual_records
            or entity_id in self._reassert_records
        ):
            return
        if (
            len(self._owned_records)
            + len(self._manual_records)
            + len(self._reassert_records)
            >= 256
        ):
            raise RuntimeError("light priority store is full")

    async def _async_save_if_dirty(self) -> None:
        if not self._dirty or self._store is None:
            return
        await self._store.async_save(self._payload())
        self._dirty = False

    def _payload(self) -> dict[str, object]:
        """Return one complete, bounded storage document."""

        records = [
            *(dict(item) for item in self._owned_records.values()),
            *(dict(item) for item in self._manual_records.values()),
            *(dict(item) for item in self._reassert_records.values()),
        ]
        if len(records) > 256:
            raise RuntimeError("light priority store is full")
        return {
            "version": 1,
            "records": records,
        }

    @staticmethod
    def _state(hass: object, entity_id: str) -> object | None:
        states = getattr(hass, "states", None)
        return states.get(entity_id) if states is not None else None

    @classmethod
    def _effective_state(
        cls,
        entity_id: str,
        hass: object,
        power_dependencies: Mapping[str, DevicePowerDependency],
    ) -> str | None:
        state, _status = effective_device_state(
            entity_id,
            power_dependencies,
            lambda requested: (
                str(getattr(current, "state", "unknown"))
                if (current := cls._state(hass, requested)) is not None
                else None
            ),
        )
        return state

    @classmethod
    def _effective_off_is_fresh(
        cls,
        entity_id: str,
        hass: object,
        power_dependencies: Mapping[str, DevicePowerDependency],
    ) -> bool:
        """Require fresh physical evidence for the off that clears authority."""

        current_entity_id = entity_id
        visited: set[str] = set()
        while current_entity_id not in visited:
            visited.add(current_entity_id)
            current = cls._state(hass, current_entity_id)
            if (
                str(getattr(current, "state", "unknown")) == "off"
                and _state_is_fresh(current)
            ):
                return True
            dependency = power_dependencies.get(current_entity_id)
            if dependency is None:
                return False
            current_entity_id = dependency.power_source_entity_id
        return False


def valid_light_priority_payload(value: object) -> bool:
    if not isinstance(value, Mapping) or value.get("version") != 1:
        return False
    records = value.get("records")
    if not isinstance(records, list) or len(records) > 256:
        return False
    entity_ids: list[str] = []
    for item in records:
        if not isinstance(item, Mapping):
            return False
        entity_id = item.get("entityId")
        target_id = item.get("targetId")
        revision = item.get("evidenceRevision")
        ownership = item.get("ownership", "automation")
        if (
            not isinstance(entity_id, str)
            or not isinstance(target_id, str)
            or revision is not None and not isinstance(revision, str)
            or ownership not in {"automation", "manual", "reassert"}
            or (
                ownership == "automation"
                and type(item.get("expiresAt")) is not int
            )
            or (
                ownership == "reassert"
                and (
                    not isinstance(revision, str)
                    or type(item.get("attemptedAt")) is not int
                )
            )
        ):
            return False
        entity_ids.append(entity_id)
    return len(entity_ids) == len(set(entity_ids))


def skipped_light_receipt(
    action: ScenarioAction,
    catalog: ScenarioCatalog,
    *,
    correlation_id: str,
    dry_run: bool,
) -> dict[str, object]:
    """Return a stable trace receipt without sending a physical command."""

    device = catalog.device(action.target_id or "")
    allowed = device.action(action.action_id or "") if device else None
    receipt: dict[str, object] = {
        "action_id": action.id,
        "correlation_id": correlation_id,
        "type": action.type,
        "status": "completed",
        "target_id": action.target_id,
        "skipped": True,
        "reason": MANUAL_LIGHT_PRIORITY_REASON,
    }
    if device is not None:
        receipt["entity_id"] = device.entity_id
    if allowed is not None:
        receipt["domain"] = allowed.domain
        receipt["service"] = allowed.service
    if dry_run:
        receipt["planned"] = True
        receipt["confirmed"] = None
    else:
        receipt["confirmed"] = True
        receipt["read_back"] = {
            "attempted": False,
            "matched": True,
            "observedAt": None,
            "observedState": "on",
            "attempts": 0,
        }
    return receipt
