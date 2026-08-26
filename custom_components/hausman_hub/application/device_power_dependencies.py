"""Durable optimistic-locking service for device power dependencies."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from typing import Callable

from ..domain.device_power_dependencies import (
    DevicePowerDependency,
    DevicePowerDependencyViolation,
    device_power_dependencies_to_payload,
    device_power_dependency_mapping,
    validate_device_power_dependencies,
)


DEVICE_POWER_DEPENDENCY_CONTRACT = "hausman-hub-device-power-dependencies"


class DevicePowerDependencyServiceViolation(ValueError):
    """A dependency update is malformed, stale or references missing entities."""

    def __init__(self, message: str, *, stale: bool = False) -> None:
        super().__init__(message)
        self.stale = stale


class DevicePowerDependencyService:
    """Load and atomically replace one dependency graph per config entry."""

    def __init__(
        self,
        store: object,
        *,
        entity_pair_validator: Callable[[str, str], bool] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._entity_pair_validator = entity_pair_validator
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._revision = 0
        self._updated_at = ""
        self._dependencies: tuple[DevicePowerDependency, ...] = ()
        self._loaded = False
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        loaded = await self._store.async_load()
        if loaded is None:
            self._revision = 0
            self._updated_at = self._timestamp()
            self._dependencies = ()
            self._loaded = True
            return
        if not isinstance(loaded, dict) or set(loaded) != {
            "revision",
            "updatedAt",
            "dependencies",
        }:
            raise DevicePowerDependencyServiceViolation(
                "stored device power dependencies are invalid"
            )
        revision = loaded.get("revision")
        updated_at = loaded.get("updatedAt")
        if type(revision) is not int or revision < 0 or not isinstance(updated_at, str):
            raise DevicePowerDependencyServiceViolation(
                "stored device power dependency metadata is invalid"
            )
        try:
            dependencies = validate_device_power_dependencies(loaded["dependencies"])
        except DevicePowerDependencyViolation as error:
            raise DevicePowerDependencyServiceViolation(str(error)) from error
        self._revision = revision
        self._updated_at = updated_at
        self._dependencies = dependencies
        self._loaded = True

    @property
    def document(self) -> dict[str, object]:
        self._require_loaded()
        return {
            "contract": {
                "name": DEVICE_POWER_DEPENDENCY_CONTRACT,
                "version": 1,
            },
            "revision": self._revision,
            "updatedAt": self._updated_at,
            "dependencies": deepcopy(
                device_power_dependencies_to_payload(self._dependencies)
            ),
        }

    @property
    def mapping(self) -> dict[str, DevicePowerDependency]:
        self._require_loaded()
        return device_power_dependency_mapping(self._dependencies)

    async def async_replace(
        self, expected_revision: object, dependencies: object
    ) -> dict[str, object]:
        self._require_loaded()
        if type(expected_revision) is not int or expected_revision < 0:
            raise DevicePowerDependencyServiceViolation(
                "expected dependency revision is invalid"
            )
        try:
            validated = validate_device_power_dependencies(dependencies)
        except DevicePowerDependencyViolation as error:
            raise DevicePowerDependencyServiceViolation(str(error)) from error
        if self._entity_pair_validator is not None and any(
            not self._entity_pair_validator(
                item.dependent_entity_id, item.power_source_entity_id
            )
            for item in validated
        ):
            raise DevicePowerDependencyServiceViolation(
                "device power dependency references an unavailable entity"
            )
        async with self._lock:
            if expected_revision != self._revision:
                raise DevicePowerDependencyServiceViolation(
                    "device power dependency revision is stale", stale=True
                )
            next_revision = self._revision + 1
            updated_at = self._timestamp()
            stored = {
                "revision": next_revision,
                "updatedAt": updated_at,
                "dependencies": device_power_dependencies_to_payload(validated),
            }
            await self._store.async_save(stored)
            self._revision = next_revision
            self._updated_at = updated_at
            self._dependencies = validated
        return self.document

    async def async_reset(self) -> dict[str, object]:
        """Remove every Hausman-owned power link using the same atomic write."""

        return await self.async_replace(self._revision, [])

    def _timestamp(self) -> str:
        value = self._now()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def _require_loaded(self) -> None:
        if not self._loaded:
            raise DevicePowerDependencyServiceViolation(
                "device power dependency service is not loaded"
            )
