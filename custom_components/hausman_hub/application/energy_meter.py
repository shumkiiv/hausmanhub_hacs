"""Durable manual utility-meter anchor and monthly energy cycle."""

from __future__ import annotations

import asyncio
import calendar
from copy import deepcopy
from datetime import date, datetime, timezone
from typing import Callable


ENERGY_METER_CONTRACT = "hausman-hub-energy-meter"
_ACTIONS = frozenset({"configure", "submit", "correct"})
_HISTORY_KINDS = frozenset({"submission", "correction"})
_MAX_HISTORY = 60


class EnergyMeterViolation(ValueError):
    """The stored state or requested meter mutation is invalid."""

    def __init__(self, message: str, *, stale: bool = False) -> None:
        super().__init__(message)
        self.stale = stale


def default_energy_meter_settings() -> dict[str, object]:
    return {
        "enabled": False,
        "submissionDayOfMonth": 25,
        "reminderDaysBefore": 3,
        "sourceDeviceId": None,
        "sourceDeviceIds": [],
    }


class EnergyMeterService:
    """Persist anchors and project them over an aggregate HA energy source."""

    def __init__(
        self,
        store: object,
        *,
        now: Callable[[], datetime] | None = None,
        local_today: Callable[[], date] | None = None,
    ) -> None:
        self._store = store
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._local_today = local_today or (lambda: self._now().date())
        self._state: dict[str, object] | None = None
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        loaded = await self._store.async_load()
        if loaded is None:
            loaded = {
                "revision": 0,
                "updatedAt": self._timestamp(),
                "settings": default_energy_meter_settings(),
                "anchor": None,
                "cycle": None,
                "lastSubmissionDate": None,
                "history": [],
            }
        self._state = _validate_state(_migrate_state(loaded))

    @property
    def source_device_id(self) -> str | None:
        """Return the selected opaque energy device without exposing storage."""

        settings = _validate_settings(self._require_state()["settings"])
        source_device_id = settings["sourceDeviceId"]
        return source_device_id if isinstance(source_device_id, str) else None

    @property
    def source_device_ids(self) -> list[str]:
        """Return every selected energy device (multi-source binding)."""

        settings = _validate_settings(self._require_state()["settings"])
        return list(settings["sourceDeviceIds"])

    def document(
        self,
        source_total_kwh: object,
        source_signature: str | None = None,
        source_device_id: str | None = None,
        source_name: str | None = None,
        source_readings: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        state = self._require_state()
        source_total = _optional_number(source_total_kwh, "source total")
        anchor = state["anchor"]
        cycle = state["cycle"]
        source_state = "available" if source_total is not None else "unavailable"
        reset_detected = False
        for candidate in (anchor, cycle):
            if not isinstance(candidate, dict) or source_total is None:
                continue
            baseline = candidate.get("sourceTotalKwh")
            if isinstance(baseline, (int, float)) and source_total + 0.0005 < baseline:
                reset_detected = True
            stored_signature = candidate.get("sourceSignature")
            if (
                isinstance(stored_signature, str)
                and isinstance(source_signature, str)
                and stored_signature != source_signature
            ):
                reset_detected = True
        if reset_detected:
            source_state = "reset_detected"

        reading: float | None = None
        adjusted_at: str | None = None
        estimated = False
        if isinstance(anchor, dict):
            reading = float(anchor["readingKwh"])
            adjusted_at = str(anchor["recordedAt"])
            anchor_source = anchor.get("sourceTotalKwh")
            if (
                source_total is not None
                and isinstance(anchor_source, (int, float))
                and not reset_detected
            ):
                reading += source_total - float(anchor_source)
                estimated = source_total > float(anchor_source) + 0.0005
            reading = round(reading, 3)

        cycle_started: str | None = None
        baseline_reading: float | None = None
        consumption: float | None = None
        if isinstance(cycle, dict):
            cycle_started = str(cycle["startedAt"])
            baseline_reading = float(cycle["baselineReadingKwh"])
            if reading is not None and not reset_detected:
                consumption = round(max(0.0, reading - baseline_reading), 3)

        reminder = _submission_projection(
            _validate_settings(state["settings"]),
            _optional_date(state["lastSubmissionDate"], "last submission date"),
            self._local_today(),
        )
        source_block: dict[str, object] = {
            "deviceId": source_device_id,
            "name": source_name,
            "available": source_total is not None,
            "currentTotalKwh": round(source_total, 3) if source_total is not None else None,
            "state": source_state,
        }
        if source_readings is not None:
            source_block["sources"] = [
                {
                    "deviceId": reading["deviceId"],
                    "name": reading["name"],
                    "available": reading["available"] is True,
                    "currentTotalKwh": _optional_number(
                        reading["currentTotalKwh"], "source reading"
                    ),
                    "state": "available"
                    if reading["available"] is True
                    else "unavailable",
                }
                for reading in source_readings
            ]
        return {
            "contract": {"name": ENERGY_METER_CONTRACT, "version": 1},
            "revision": state["revision"],
            "updatedAt": state["updatedAt"],
            "settings": deepcopy(state["settings"]),
            "source": source_block,
            "reading": {
                "currentKwh": reading,
                "adjustedAt": adjusted_at,
                "estimated": estimated,
            },
            "cycle": {
                "startedAt": cycle_started,
                "baselineReadingKwh": baseline_reading,
                "consumptionKwh": consumption,
            },
            "submission": reminder,
            "history": deepcopy(state["history"]),
        }

    async def async_action(
        self,
        payload: object,
        source_total_kwh: object,
        source_signature: str | None = None,
        source_device_id: str | None = None,
        source_name: str | None = None,
        source_readings: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        request = _validate_action(payload)
        source_total = _optional_number(source_total_kwh, "source total")
        async with self._lock:
            state = self._require_state()
            if request["expectedRevision"] != state["revision"]:
                raise EnergyMeterViolation("energy meter revision is stale", stale=True)
            next_state = deepcopy(state)
            action = request["action"]
            timestamp = self._timestamp()
            next_revision = int(state["revision"]) + 1
            if action == "configure":
                next_state["settings"] = request["settings"]
            else:
                reading = float(request["readingKwh"])
                anchor = {
                    "readingKwh": reading,
                    "sourceTotalKwh": source_total,
                    "sourceSignature": source_signature,
                    "recordedAt": timestamp,
                }
                next_state["anchor"] = anchor
                kind = "submission" if action == "submit" else "correction"
                if action == "submit":
                    next_state["cycle"] = {
                        "baselineReadingKwh": reading,
                        "sourceTotalKwh": source_total,
                        "sourceSignature": source_signature,
                        "startedAt": timestamp,
                    }
                    next_state["lastSubmissionDate"] = self._local_today().isoformat()
                history = list(next_state["history"])
                history.insert(
                    0,
                    {
                        "id": f"reading_{next_revision}",
                        "kind": kind,
                        "readingKwh": reading,
                        "sourceTotalKwh": source_total,
                        "sourceDeviceId": source_device_id,
                        "recordedAt": timestamp,
                    },
                )
                next_state["history"] = history[:_MAX_HISTORY]
            next_state["revision"] = next_revision
            next_state["updatedAt"] = timestamp
            validated = _validate_state(next_state)
            await self._store.async_save(validated)
            self._state = validated
        return self.document(
            source_total,
            source_signature,
            source_device_id,
            source_name,
            source_readings,
        )

    def _timestamp(self) -> str:
        value = self._now()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def _require_state(self) -> dict[str, object]:
        if self._state is None:
            raise EnergyMeterViolation("energy meter service is not loaded")
        return self._state


def _submission_projection(
    settings: dict[str, object], last_submission: date | None, today: date
) -> dict[str, object]:
    if settings["enabled"] is not True:
        return {"lastDate": last_submission.isoformat() if last_submission else None,
                "nextDate": None, "status": "disabled", "daysUntil": None}
    day = int(settings["submissionDayOfMonth"])
    target = _clamped_date(today.year, today.month, day)
    if last_submission is not None and (
        last_submission.year == target.year and last_submission.month == target.month
    ):
        year = target.year + (1 if target.month == 12 else 0)
        month = 1 if target.month == 12 else target.month + 1
        target = _clamped_date(year, month, day)
    days_until = (target - today).days
    if days_until < 0:
        status = "overdue"
    elif days_until == 0:
        status = "due"
    elif days_until <= int(settings["reminderDaysBefore"]):
        status = "upcoming"
    else:
        status = "none"
    return {
        "lastDate": last_submission.isoformat() if last_submission else None,
        "nextDate": target.isoformat(),
        "status": status,
        "daysUntil": days_until,
    }


def _clamped_date(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _validate_action(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise EnergyMeterViolation("energy meter action must be an object")
    action = value.get("action")
    expected = {"expectedRevision", "action", "settings" if action == "configure" else "readingKwh"}
    if set(value) != expected or action not in _ACTIONS:
        raise EnergyMeterViolation("energy meter action fields are invalid")
    revision = value.get("expectedRevision")
    if type(revision) is not int or revision < 0:
        raise EnergyMeterViolation("expected energy meter revision is invalid")
    result = {"expectedRevision": revision, "action": action}
    if action == "configure":
        result["settings"] = _validate_settings(value.get("settings"))
    else:
        result["readingKwh"] = _number(value.get("readingKwh"), "meter reading")
    return result


def _migrate_state(value: object) -> object:
    """Add source fields to pre-0.30 durable documents without losing history."""

    if not isinstance(value, dict):
        return value
    migrated = deepcopy(value)
    settings = migrated.get("settings")
    if isinstance(settings, dict):
        settings.setdefault("sourceDeviceId", None)
        if "sourceDeviceIds" not in settings:
            single = settings["sourceDeviceId"]
            settings["sourceDeviceIds"] = [single] if isinstance(single, str) else []
    history = migrated.get("history")
    if isinstance(history, list):
        for record in history:
            if isinstance(record, dict):
                record.setdefault("sourceDeviceId", None)
    return migrated


def _validate_state(value: object) -> dict[str, object]:
    required = {"revision", "updatedAt", "settings", "anchor", "cycle", "lastSubmissionDate", "history"}
    if not isinstance(value, dict) or set(value) != required:
        raise EnergyMeterViolation("stored energy meter fields are invalid")
    revision = value["revision"]
    if type(revision) is not int or revision < 0:
        raise EnergyMeterViolation("stored energy meter revision is invalid")
    if not isinstance(value["updatedAt"], str):
        raise EnergyMeterViolation("stored energy meter timestamp is invalid")
    _validate_settings(value["settings"])
    for key, cycle in (("anchor", False), ("cycle", True)):
        candidate = value[key]
        if candidate is None:
            continue
        fields = {"readingKwh", "sourceTotalKwh", "sourceSignature", "recordedAt"}
        if cycle:
            fields = {"baselineReadingKwh", "sourceTotalKwh", "sourceSignature", "startedAt"}
        if not isinstance(candidate, dict) or set(candidate) != fields:
            raise EnergyMeterViolation(f"stored energy meter {key} is invalid")
        _number(candidate["baselineReadingKwh" if cycle else "readingKwh"], key)
        _optional_number(candidate["sourceTotalKwh"], key)
        if candidate["sourceSignature"] is not None and not isinstance(
            candidate["sourceSignature"], str
        ):
            raise EnergyMeterViolation(f"stored energy meter {key} source is invalid")
        if not isinstance(candidate["startedAt" if cycle else "recordedAt"], str):
            raise EnergyMeterViolation(f"stored energy meter {key} timestamp is invalid")
    _optional_date(value["lastSubmissionDate"], "last submission date")
    history = value["history"]
    if not isinstance(history, list) or len(history) > _MAX_HISTORY:
        raise EnergyMeterViolation("stored energy meter history is invalid")
    for record in history:
        if not isinstance(record, dict) or set(record) != {
            "id", "kind", "readingKwh", "sourceTotalKwh", "sourceDeviceId", "recordedAt"
        }:
            raise EnergyMeterViolation("stored energy meter record is invalid")
        if not isinstance(record["id"], str) or not record["id"].startswith("reading_"):
            raise EnergyMeterViolation("stored energy meter record id is invalid")
        if record["kind"] not in _HISTORY_KINDS or not isinstance(record["recordedAt"], str):
            raise EnergyMeterViolation("stored energy meter record metadata is invalid")
        _number(record["readingKwh"], "history reading")
        _optional_number(record["sourceTotalKwh"], "history source")
        _optional_device_id(record["sourceDeviceId"], "history source device")
    return deepcopy(value)


def _validate_settings(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not set(value).issubset(
        {"enabled", "submissionDayOfMonth", "reminderDaysBefore", "sourceDeviceId",
         "sourceDeviceIds"}
    ) or not {"enabled", "submissionDayOfMonth", "reminderDaysBefore"}.issubset(value):
        raise EnergyMeterViolation("energy meter settings are invalid")
    enabled = value["enabled"]
    day = value["submissionDayOfMonth"]
    reminder = value["reminderDaysBefore"]
    if type(enabled) is not bool or type(day) is not int or not 1 <= day <= 31:
        raise EnergyMeterViolation("energy meter settings are invalid")
    if type(reminder) is not int or not 0 <= reminder <= 14:
        raise EnergyMeterViolation("energy meter reminder is invalid")
    source_ids = value.get("sourceDeviceIds")
    if source_ids is None:
        # Legacy payloads carry only the single-source mirror.
        single = _optional_device_id(value.get("sourceDeviceId"), "energy meter source device")
        source_ids = [single] if single is not None else []
    if (
        not isinstance(source_ids, list)
        or len(source_ids) > 16
        or len(source_ids) != len(set(source_ids))
    ):
        raise EnergyMeterViolation("energy meter source devices are invalid")
    validated_ids = [
        _optional_device_id(candidate, "energy meter source device")
        for candidate in source_ids
    ]
    if any(candidate is None for candidate in validated_ids):
        raise EnergyMeterViolation("energy meter source devices are invalid")
    return {
        "enabled": enabled,
        "submissionDayOfMonth": day,
        "reminderDaysBefore": reminder,
        # The single-source field stays as a mirror of the first binding so
        # older clients keep working unchanged.
        "sourceDeviceId": validated_ids[0] if validated_ids else None,
        "sourceDeviceIds": validated_ids,
    }


def _optional_device_id(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) != 23 or not value.startswith("device_"):
        raise EnergyMeterViolation(f"{field} is invalid")
    suffix = value[7:]
    if any(character not in "0123456789abcdef" for character in suffix):
        raise EnergyMeterViolation(f"{field} is invalid")
    return value


def _number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EnergyMeterViolation(f"{field} is invalid")
    result = float(value)
    if result < 0 or result > 1_000_000_000:
        raise EnergyMeterViolation(f"{field} is invalid")
    return round(result, 3)


def _optional_number(value: object, field: str) -> float | None:
    return None if value is None else _number(value, field)


def _optional_date(value: object, field: str) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise EnergyMeterViolation(f"{field} is invalid")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise EnergyMeterViolation(f"{field} is invalid") from error
