"""Independent utility meter collection with a legacy primary projection."""

from __future__ import annotations

import asyncio
import re
from copy import deepcopy
from datetime import date, datetime, timezone
from typing import Callable, Mapping

from .energy_meter import (
    EnergyMeterService,
    EnergyMeterViolation,
    default_energy_meter_settings,
)


ENERGY_METERS_CONTRACT = "hausman-hub-energy-meters"
PRIMARY_METER_ID = "meter_main"
MAX_METERS = 16
_METER_ID = re.compile(r"^meter_[a-z0-9_]{1,58}$")
Projection = tuple[
    float | None,
    str | None,
    str | None,
    str | None,
    list[dict[str, object]] | None,
]


class _MemoryStore:
    def __init__(self, loaded: dict[str, object]) -> None:
        self.loaded = deepcopy(loaded)
        self.saved: dict[str, object] | None = None

    async def async_load(self) -> dict[str, object]:
        return deepcopy(self.loaded)

    async def async_save(self, value: dict[str, object]) -> None:
        self.saved = deepcopy(value)


class EnergyMetersService:
    """Persist additional meters while preserving the old primary endpoint."""

    def __init__(
        self,
        store: object,
        primary: EnergyMeterService,
        *,
        now: Callable[[], datetime] | None = None,
        local_today: Callable[[], date] | None = None,
    ) -> None:
        self._store = store
        self._primary = primary
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._local_today = local_today or (lambda: self._now().date())
        self._state: dict[str, object] | None = None
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        loaded = await self._store.async_load()
        if loaded is None:
            loaded = {"revision": 0, "updatedAt": self._timestamp(), "meters": {}}
        self._state = _validate_collection_state(loaded)

    @property
    def source_bindings(self) -> dict[str, list[str]]:
        result = {PRIMARY_METER_ID: self._primary.source_device_ids}
        for meter_id, record in self._meters().items():
            state = record["state"]
            settings = state["settings"]
            source_ids = settings.get("sourceDeviceIds", [])
            result[meter_id] = list(source_ids) if isinstance(source_ids, list) else []
        return result

    async def async_document(
        self,
        projections: Mapping[str, Projection],
    ) -> dict[str, object]:
        state = self._required_state()
        primary_projection = projections.get(PRIMARY_METER_ID, (None, None, None, None, None))
        primary_document = self._primary.document(*primary_projection)
        meters = [self._public_meter(PRIMARY_METER_ID, "Основной счётчик", True, primary_document)]
        for meter_id, record in sorted(self._meters().items()):
            service, _store = await self._service_for(record["state"])
            projection = projections.get(meter_id, (None, None, None, None, None))
            meters.append(
                self._public_meter(
                    meter_id,
                    str(record["name"]),
                    False,
                    service.document(*projection),
                )
            )
        return {
            "contract": {"name": ENERGY_METERS_CONTRACT, "version": 1},
            "revision": state["revision"],
            "updatedAt": state["updatedAt"],
            "meters": meters,
        }

    async def async_action(
        self,
        payload: object,
        projections: Mapping[str, Projection],
    ) -> dict[str, object]:
        request = _validate_collection_action(payload)
        meter_id = str(request["meterId"])
        if meter_id == PRIMARY_METER_ID:
            raise EnergyMeterViolation(
                "primary meter mutations use the compatible singular endpoint"
            )
        async with self._lock:
            state = self._required_state()
            if request["expectedRevision"] != state["revision"]:
                raise EnergyMeterViolation("energy meters revision is stale", stale=True)
            next_state = deepcopy(state)
            meters = next_state["meters"]
            action = request["action"]
            if action == "delete":
                if meter_id not in meters:
                    raise EnergyMeterViolation("energy meter is unknown")
                del meters[meter_id]
            else:
                record = meters.get(meter_id)
                if record is None:
                    if action != "upsert" or len(meters) >= MAX_METERS - 1:
                        raise EnergyMeterViolation("energy meter is unknown or limit reached")
                    record = {
                        "name": request["name"],
                        "state": _default_meter_state(self._timestamp()),
                    }
                service, memory = await self._service_for(record["state"])
                projection = projections.get(meter_id, (None, None, None, None, None))
                if action == "upsert":
                    child_payload = {
                        "expectedRevision": service.document(*projection)["revision"],
                        "action": "configure",
                        "settings": request["settings"],
                    }
                    record["name"] = request["name"]
                else:
                    child_payload = {
                        "expectedRevision": service.document(*projection)["revision"],
                        "action": action,
                        "readingKwh": request["readingKwh"],
                    }
                await service.async_action(child_payload, *projection)
                if memory.saved is None:
                    raise EnergyMeterViolation("energy meter mutation was not persisted")
                record["state"] = memory.saved
                meters[meter_id] = record
            next_state["revision"] = int(state["revision"]) + 1
            next_state["updatedAt"] = self._timestamp()
            validated = _validate_collection_state(next_state)
            await self._store.async_save(validated)
            self._state = validated
        return await self.async_document(projections)

    async def _service_for(
        self, state: dict[str, object]
    ) -> tuple[EnergyMeterService, _MemoryStore]:
        memory = _MemoryStore(state)
        service = EnergyMeterService(
            memory,
            now=self._now,
            local_today=self._local_today,
        )
        await service.async_load()
        return service, memory

    @staticmethod
    def _public_meter(
        meter_id: str,
        name: str,
        primary: bool,
        document: Mapping[str, object],
    ) -> dict[str, object]:
        return {
            "meterId": meter_id,
            "name": name,
            "primary": primary,
            "settings": deepcopy(document["settings"]),
            "source": deepcopy(document["source"]),
            "reading": deepcopy(document["reading"]),
            "cycle": deepcopy(document["cycle"]),
            "submission": deepcopy(document["submission"]),
            "history": deepcopy(document["history"]),
        }

    def _meters(self) -> dict[str, dict[str, object]]:
        return self._required_state()["meters"]  # type: ignore[return-value]

    def _required_state(self) -> dict[str, object]:
        if self._state is None:
            raise EnergyMeterViolation("energy meters service is not loaded")
        return self._state

    def _timestamp(self) -> str:
        value = self._now()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _default_meter_state(timestamp: str) -> dict[str, object]:
    return {
        "revision": 0,
        "updatedAt": timestamp,
        "settings": default_energy_meter_settings(),
        "anchor": None,
        "cycle": None,
        "lastSubmissionDate": None,
        "history": [],
    }


def _validate_collection_state(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {"revision", "updatedAt", "meters"}:
        raise EnergyMeterViolation("stored energy meter collection is invalid")
    if type(value["revision"]) is not int or value["revision"] < 0:
        raise EnergyMeterViolation("stored energy meter collection revision is invalid")
    if not isinstance(value["updatedAt"], str) or not isinstance(value["meters"], dict):
        raise EnergyMeterViolation("stored energy meter collection metadata is invalid")
    if len(value["meters"]) > MAX_METERS - 1:
        raise EnergyMeterViolation("stored energy meter collection limit is exceeded")
    for meter_id, record in value["meters"].items():
        if (
            not isinstance(meter_id, str)
            or not _METER_ID.fullmatch(meter_id)
            or meter_id == PRIMARY_METER_ID
            or not isinstance(record, dict)
            or set(record) != {"name", "state"}
            or not isinstance(record["name"], str)
            or not record["name"].strip()
            or len(record["name"]) > 120
            or not isinstance(record["state"], dict)
        ):
            raise EnergyMeterViolation("stored energy meter record is invalid")
    return deepcopy(value)


def _validate_collection_action(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise EnergyMeterViolation("energy meter collection action is invalid")
    action = value.get("action")
    required = {"expectedRevision", "action", "meterId"}
    allowed = set(required)
    if action == "upsert":
        required |= {"name", "settings"}
        allowed = required | {"primary"}
    elif action in {"submit", "correct"}:
        required.add("readingKwh")
        allowed = set(required)
    elif action != "delete":
        raise EnergyMeterViolation("energy meter collection action is invalid")
    if not required.issubset(value) or not set(value) <= allowed:
        raise EnergyMeterViolation("energy meter collection fields are invalid")
    revision = value["expectedRevision"]
    meter_id = value["meterId"]
    if type(revision) is not int or revision < 0 or not isinstance(meter_id, str) or not _METER_ID.fullmatch(meter_id):
        raise EnergyMeterViolation("energy meter collection identity is invalid")
    result = dict(value)
    if action == "upsert":
        name = value["name"]
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name) > 120
            or value.get("primary") not in {None, False}
        ):
            raise EnergyMeterViolation("energy meter collection name or primary is invalid")
    return result
