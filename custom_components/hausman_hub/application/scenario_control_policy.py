"""CAS lifecycle for the server-owned consolidated-controller policy."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import replace

from ..domain.scenario_controls import (
    ScenarioControlDocument,
    ScenarioControlPolicy,
    scenario_control_document_from_payload,
    scenario_control_document_to_payload,
    validate_scenario_control_policy,
)


class ScenarioControlPolicyConflict(RuntimeError):
    """An editor attempted to replace a stale policy revision."""


Observer = Callable[[ScenarioControlDocument], Awaitable[object] | object]


class ScenarioControlPolicyService:
    """Persist one policy and publish its new generation after the write."""

    def __init__(
        self,
        store: object,
        *,
        capability_resolver: Callable[[str], object | None] | None = None,
        binding_safety_validator: Callable[[str, object], bool] | None = None,
    ) -> None:
        self._store = store
        self._capability_resolver = capability_resolver
        self._binding_safety_validator = binding_safety_validator
        self._current: ScenarioControlDocument | None = None
        self._observers: list[Observer] = []
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        payload = await self._store.async_load()
        if payload is None:
            document = ScenarioControlDocument()
            await self._store.async_save(scenario_control_document_to_payload(document))
        else:
            try:
                document = scenario_control_document_from_payload(payload)
            except (TypeError, ValueError) as error:
                raise RuntimeError("scenario control policy storage is invalid") from error
        if getattr(self._store, "recovered_previous", False):
            if document.policy_revision >= 2**31 - 1:
                raise RuntimeError("recovered scenario control revision is exhausted")
            document = ScenarioControlDocument(
                document.policy_revision + 1,
                replace(document.policy, storage_exhaust_target_id=None),
            )
            await self._store.async_save(scenario_control_document_to_payload(document))
        self._validate_capabilities(document.policy)
        self._current = document

    @property
    def current(self) -> ScenarioControlDocument:
        if self._current is None:
            raise RuntimeError("scenario control policy is not loaded")
        return self._current

    def add_observer(self, observer: Observer) -> Callable[[], None]:
        self._observers.append(observer)

        def remove() -> None:
            try:
                self._observers.remove(observer)
            except ValueError:
                pass

        return remove

    async def async_replace(
        self,
        expected_revision: int,
        policy: ScenarioControlPolicy,
    ) -> ScenarioControlDocument:
        if type(expected_revision) is not int:
            raise ScenarioControlPolicyConflict("scenario control revision is invalid")
        validate_scenario_control_policy(policy)
        self._validate_capabilities(policy)
        async with self._lock:
            current = self.current
            if expected_revision != current.policy_revision:
                raise ScenarioControlPolicyConflict("scenario control policy changed")
            if current.policy_revision >= 2**31 - 1:
                raise ScenarioControlPolicyConflict("scenario control policy revision exhausted")
            if policy == current.policy:
                return current
            updated = ScenarioControlDocument(current.policy_revision + 1, policy)
            await self._store.async_save(scenario_control_document_to_payload(updated))
            self._current = updated
            observers = tuple(self._observers)
        for observer in observers:
            result = observer(updated)
            if inspect.isawaitable(result):
                await result
        return updated

    def _validate_capabilities(self, policy: ScenarioControlPolicy) -> None:
        target_id = policy.storage_exhaust_target_id
        if target_id is None:
            return
        if self._capability_resolver is None:
            raise ValueError("storage exhaust capability resolver is unavailable")
        device = self._capability_resolver(target_id)
        if device is None:
            raise ValueError("storage exhaust target is not in the server catalog")
        action = getattr(device, "action", None)
        turn_on = action("turn_on") if callable(action) else None
        turn_off = action("turn_off") if callable(action) else None
        domains = {
            getattr(turn_on, "domain", None),
            getattr(turn_off, "domain", None),
        }
        identity = " ".join(
            str(getattr(device, field, "") or "")
            for field in (
                "name",
                "physical_name",
                "device_type",
                "device_type_name",
                "capability_name",
            )
        ).casefold()
        if (
            turn_on is None
            or turn_off is None
            or getattr(turn_on, "service", None) != "turn_on"
            or getattr(turn_off, "service", None) != "turn_off"
            or not domains.issubset({"fan", "switch"})
            or not any(word in identity for word in ("fan", "вентил", "вытяж"))
        ):
            raise ValueError("storage exhaust target must support turn_on and turn_off")
        if (
            self._binding_safety_validator is not None
            and not self._binding_safety_validator(target_id, device)
        ):
            raise ValueError("storage exhaust target is safety-protected")
