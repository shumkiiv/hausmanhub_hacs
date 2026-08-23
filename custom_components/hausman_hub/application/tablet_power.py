"""Bounded wall-tablet power telemetry and charging-policy decisions."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import re
import time

TABLET_POWER_REQUEST_CONTRACT = "hausman-hub-tablet-power-status-request"
TABLET_POWER_RECEIPT_CONTRACT = "hausman-hub-tablet-power-status-receipt"
TABLET_POWER_CONTRACT_VERSION = 1
TABLET_BATTERY_ENTITY_ID = "sensor.hausman_hub_tablet_battery"
TABLET_POWER_ENTITY_ID = "sensor.hausman_hub_tablet_power"
TABLET_POWER_STALE_AFTER_MS = 20 * 60 * 1000
TABLET_CHARGE_ON_BELOW_PERCENT = 40
TABLET_CHARGE_OFF_AT_PERCENT = 80
_CORRELATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_TABLET_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_POWER_SOURCES = frozenset({"battery", "ac", "usb", "wireless", "unknown"})


class TabletPowerViolation(ValueError):
    """The local tablet telemetry does not satisfy the public contract."""


@dataclass(frozen=True, slots=True)
class TabletPowerStatus:
    correlation_id: str
    tablet_id: str
    battery_percent: int
    charging: bool
    power_source: str
    battery_temperature_c: float | None
    reported_at: int


def parse_tablet_power_status(value: object) -> TabletPowerStatus:
    """Validate one exact bounded request without retaining the raw mapping."""

    if not isinstance(value, Mapping) or set(value) not in (
        {
            "contract",
            "correlationId",
            "tabletId",
            "batteryPercent",
            "charging",
            "powerSource",
            "reportedAt",
        },
        {
            "contract",
            "correlationId",
            "tabletId",
            "batteryPercent",
            "charging",
            "powerSource",
            "batteryTemperatureC",
            "reportedAt",
        },
    ):
        raise TabletPowerViolation("tablet power request shape is invalid")
    contract = value.get("contract")
    if contract != {
        "name": TABLET_POWER_REQUEST_CONTRACT,
        "version": TABLET_POWER_CONTRACT_VERSION,
    }:
        raise TabletPowerViolation("tablet power contract is invalid")
    correlation_id = value.get("correlationId")
    tablet_id = value.get("tabletId")
    battery_percent = value.get("batteryPercent")
    charging = value.get("charging")
    power_source = value.get("powerSource")
    temperature = value.get("batteryTemperatureC")
    reported_at = value.get("reportedAt")
    if not isinstance(correlation_id, str) or _CORRELATION_ID.fullmatch(correlation_id) is None:
        raise TabletPowerViolation("correlation id is invalid")
    if not isinstance(tablet_id, str) or _TABLET_ID.fullmatch(tablet_id) is None:
        raise TabletPowerViolation("tablet id is invalid")
    if type(battery_percent) is not int or not 0 <= battery_percent <= 100:
        raise TabletPowerViolation("battery percent is invalid")
    if (
        type(charging) is not bool
        or not isinstance(power_source, str)
        or power_source not in _POWER_SOURCES
    ):
        raise TabletPowerViolation("power state is invalid")
    if temperature is not None and (
        type(temperature) not in {int, float}
        or type(temperature) is bool
        or not -20 <= float(temperature) <= 80
    ):
        raise TabletPowerViolation("battery temperature is invalid")
    if type(reported_at) is not int or reported_at < 0:
        raise TabletPowerViolation("reported time is invalid")
    return TabletPowerStatus(
        correlation_id=correlation_id,
        tablet_id=tablet_id,
        battery_percent=battery_percent,
        charging=charging,
        power_source=str(power_source),
        battery_temperature_c=float(temperature) if temperature is not None else None,
        reported_at=reported_at,
    )


def charging_policy_decision(
    battery_percent: int | None,
    *,
    battery_available: bool = True,
    plug_available: bool = True,
) -> str:
    """Return the fail-safe 40/80 decision used by the standard automation."""

    if not battery_available or not plug_available or battery_percent is None:
        return "fallback_on"
    if battery_percent < TABLET_CHARGE_ON_BELOW_PERCENT:
        return "turn_on"
    if battery_percent >= TABLET_CHARGE_OFF_AT_PERCENT:
        return "turn_off"
    return "hold"


class TabletPowerService:
    """Keep only the latest bounded status in memory and notify HA entities."""

    def __init__(self, *, now_ms: Callable[[], int] | None = None) -> None:
        self._now_ms = now_ms or (lambda: time.time_ns() // 1_000_000)
        self._status: TabletPowerStatus | None = None
        self._listeners: set[Callable[[], None]] = set()
        self._expired = True

    @property
    def status(self) -> TabletPowerStatus | None:
        return self._status

    def available(self) -> bool:
        status = self._status
        return (
            status is not None
            and self._now_ms() - status.reported_at <= TABLET_POWER_STALE_AFTER_MS
        )

    def update(self, value: object) -> TabletPowerStatus:
        status = parse_tablet_power_status(value)
        now = self._now_ms()
        if (
            status.reported_at > now + 5 * 60 * 1000
            or now - status.reported_at > 24 * 60 * 60 * 1000
        ):
            raise TabletPowerViolation("reported time is outside the accepted window")
        self._status = status
        self._expired = False
        self._notify()
        return status

    def expire(self) -> bool:
        expired = not self.available()
        if expired == self._expired:
            return False
        self._expired = expired
        self._notify()
        return True

    def subscribe(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            listener()
