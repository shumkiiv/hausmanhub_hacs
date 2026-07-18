"""Universal automatic-contour model owned by HASC.

The first supported contour is climate.  It deliberately delegates the actual
climate algorithm to the existing ``hausman-climate`` engine instead of
reimplementing that mature policy inside the Home Assistant integration.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import re


CONTOUR_REGISTRY_VERSION = 1
MAX_CONTOURS = 32
MAX_CONTOUR_ROOMS = 128
MAX_CONTOUR_DEVICES = 512
CLIMATE_TARGET_TEMPERATURE_DEFAULT = 25.0
CLIMATE_TARGET_HUMIDITY_DEFAULT = 45
CLIMATE_TARGET_TEMPERATURE_MINIMUM = 18.0
CLIMATE_TARGET_TEMPERATURE_MAXIMUM = 28.0
CLIMATE_TARGET_TEMPERATURE_STEP = 0.5
CLIMATE_TARGET_HUMIDITY_MINIMUM = 30
CLIMATE_TARGET_HUMIDITY_MAXIMUM = 70
CLIMATE_TARGET_HUMIDITY_STEP = 5

_STABLE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class ContourViolation(ValueError):
    """A contour definition is incomplete or internally inconsistent."""


class ContourKind(StrEnum):
    """Contour kinds supported by the current HASC package."""

    CLIMATE = "climate"


class ContourMode(StrEnum):
    """Common lifecycle shared by present and future contour kinds."""

    DISABLED = "disabled"
    OBSERVE = "observe"
    AUTOMATIC = "automatic"


class ContourEngine(StrEnum):
    """Typed engine implementations hidden behind the HASC contour."""

    EXISTING_CLIMATE_CORE = "existing_climate_core"


class ClimateStrategy(StrEnum):
    """Existing climate-core target strategies exposed in plain HASC UI."""

    SOFT = "soft"
    NORMAL = "normal"
    AGGRESSIVE = "aggressive"


@dataclass(frozen=True, slots=True)
class ClimateContourRoom:
    """One room assignment and its user-facing comfort parameters."""

    room_id: str
    device_ids: tuple[str, ...]
    target_temperature: float = CLIMATE_TARGET_TEMPERATURE_DEFAULT
    target_humidity: int = CLIMATE_TARGET_HUMIDITY_DEFAULT
    strategy: ClimateStrategy = ClimateStrategy.NORMAL

    def __post_init__(self) -> None:
        _stable_id(self.room_id, "contour room id")
        if not self.device_ids:
            raise ContourViolation("contour room needs at least one device")
        if len(self.device_ids) > MAX_CONTOUR_DEVICES:
            raise ContourViolation("contour room has too many devices")
        for device_id in self.device_ids:
            _stable_id(device_id, "contour device id")
        _unique(self.device_ids, "contour device ids")
        _temperature(self.target_temperature)
        _humidity(self.target_humidity)
        if not isinstance(self.strategy, ClimateStrategy):
            raise ContourViolation("climate strategy must be approved")


@dataclass(frozen=True, slots=True)
class ContourDefinition:
    """One autonomous contour exposed to Home Assistant and Android."""

    contour_id: str
    name: str
    kind: ContourKind
    mode: ContourMode
    engine: ContourEngine
    rooms: tuple[ClimateContourRoom, ...]

    def __post_init__(self) -> None:
        _stable_id(self.contour_id, "contour id")
        _name(self.name, "contour name")
        if self.kind is not ContourKind.CLIMATE:
            raise ContourViolation("contour kind is unsupported")
        if not isinstance(self.mode, ContourMode):
            raise ContourViolation("contour mode must be approved")
        if self.engine is not ContourEngine.EXISTING_CLIMATE_CORE:
            raise ContourViolation("contour engine is unsupported")
        if not self.rooms:
            raise ContourViolation("climate contour needs at least one room")
        if len(self.rooms) > MAX_CONTOUR_ROOMS:
            raise ContourViolation("climate contour has too many rooms")
        if any(not isinstance(room, ClimateContourRoom) for room in self.rooms):
            raise ContourViolation("climate contour room must be validated")
        _unique((room.room_id for room in self.rooms), "contour room ids")
        _unique(
            (device_id for room in self.rooms for device_id in room.device_ids),
            "devices assigned to contour rooms",
        )


@dataclass(frozen=True, slots=True)
class ContourRegistry:
    """Complete versioned collection of HASC-owned contours."""

    contours: tuple[ContourDefinition, ...] = ()
    version: int = CONTOUR_REGISTRY_VERSION

    def __post_init__(self) -> None:
        if self.version != CONTOUR_REGISTRY_VERSION:
            raise ContourViolation("unsupported contour registry version")
        if len(self.contours) > MAX_CONTOURS:
            raise ContourViolation("too many contours")
        if any(not isinstance(item, ContourDefinition) for item in self.contours):
            raise ContourViolation("contour must be validated")
        _unique((item.contour_id for item in self.contours), "contour ids")

    def contour(self, contour_id: str) -> ContourDefinition | None:
        """Return one contour by its stable public identifier."""

        return next(
            (item for item in self.contours if item.contour_id == contour_id),
            None,
        )


def climate_contour_room(
    *,
    room_id: object,
    device_ids: object,
    target_temperature: object,
    target_humidity: object,
    strategy: object,
) -> ClimateContourRoom:
    """Build one exact room policy from a form or persisted payload."""

    if not isinstance(room_id, str):
        raise ContourViolation("contour room id is required")
    if not isinstance(device_ids, (list, tuple)) or any(
        not isinstance(value, str) for value in device_ids
    ):
        raise ContourViolation("contour device ids must be a list of strings")
    try:
        selected_strategy = ClimateStrategy(strategy)
    except (TypeError, ValueError) as error:
        raise ContourViolation("climate strategy must be approved") from error
    return ClimateContourRoom(
        room_id=room_id,
        device_ids=tuple(device_ids),
        target_temperature=_temperature(target_temperature),
        target_humidity=_humidity(target_humidity),
        strategy=selected_strategy,
    )


def _temperature(value: object) -> float:
    if isinstance(value, bool):
        raise ContourViolation("target temperature must be numeric")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ContourViolation("target temperature must be numeric") from error
    if (
        not number.is_finite()
        or not Decimal(str(CLIMATE_TARGET_TEMPERATURE_MINIMUM))
        <= number
        <= Decimal(str(CLIMATE_TARGET_TEMPERATURE_MAXIMUM))
        or number % Decimal(str(CLIMATE_TARGET_TEMPERATURE_STEP)) != 0
    ):
        raise ContourViolation("target temperature must be 18..28 in 0.5 steps")
    return float(number)


def _humidity(value: object) -> int:
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if (
        type(value) is not int
        or not CLIMATE_TARGET_HUMIDITY_MINIMUM
        <= value
        <= CLIMATE_TARGET_HUMIDITY_MAXIMUM
        or value % CLIMATE_TARGET_HUMIDITY_STEP != 0
    ):
        raise ContourViolation("target humidity must be 30..70 in 5 steps")
    return value


def _stable_id(value: object, label: str) -> None:
    if not isinstance(value, str) or not _STABLE_ID.fullmatch(value):
        raise ContourViolation(f"{label} must be a stable lowercase id")


def _name(value: object, label: str) -> None:
    if not isinstance(value, str) or value != value.strip() or not value or len(value) > 120:
        raise ContourViolation(f"{label} must be non-empty and at most 120 characters")


def _unique(values: object, label: str) -> None:
    items = tuple(values)  # type: ignore[arg-type]
    if len(items) != len(set(items)):
        raise ContourViolation(f"{label} must be unique")
