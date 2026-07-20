"""Apply the final room policy ladder without creating executable commands.

The working climate contour gives presence, safety, freshness, forced
automation, manual control, and ordinary automation a fixed order.  This pure
layer preserves that order and produces only typed HausmanHub results.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math

from .climate import ClimateModelViolation, ClimateRoom
from .climate_equipment import (
    ClimateEquipmentAction,
    ClimateRoomEquipmentPlan,
)
from .climate_observation import (
    ClimateControlObservation,
    ClimateDataStatus,
    ClimateDelayedIntentState,
    ClimateDeviceActivity,
    ClimateDeviceAvailability,
    ClimateDeviceObservation,
    ClimateExecutionGuardState,
    ClimateFanMode,
    ClimateHomeObservation,
    ClimateObservationDeviceKind,
    ClimateOccupancyMode,
    ClimatePhysicalFeedback,
    ClimateRoomMode,
    ClimateRoomObservation,
    ClimateTemperatureQuality,
    ClimateWindowState,
)
from .climate_resolution import (
    ClimateRoomThermalResolution,
    ClimateThermalResolution,
)
from .climate_stability import (
    ClimateRoomStabilityPlan,
    ClimateStabilityAction,
    ClimateStabilityProtection,
)
from .contours import ContourMode


CLIMATE_POLICY_MODEL_VERSION = 1


class ClimatePolicyViolation(ValueError):
    """A final climate policy result is mixed, mutable, or contradictory."""


class ClimateRoomPolicy(StrEnum):
    """The preserved room policy priority ladder."""

    AWAY = "away"
    SAFETY_LOCKOUT = "safety_lockout"
    FRESHNESS_GUARD = "freshness_guard"
    FORCED_AUTO_ONLY = "forced_auto_only"
    MANUAL = "manual"
    AUTO = "auto"


CLIMATE_POLICY_PRIORITY: tuple[str, ...] = (
    ClimateRoomPolicy.AWAY.value,
    ClimateRoomPolicy.SAFETY_LOCKOUT.value,
    ClimateRoomPolicy.FRESHNESS_GUARD.value,
    ClimateRoomPolicy.FORCED_AUTO_ONLY.value,
    ClimateRoomPolicy.MANUAL.value,
    ClimateRoomPolicy.AUTO.value,
    "direct_device_command",
)


class ClimatePolicyAction(StrEnum):
    """One room-level outcome after the priority ladder."""

    COOL = "cool"
    MAINTAIN = "maintain"
    OFF = "off"
    SAFE_OFF = "safe_off"
    OBSERVE = "observe"


class ClimatePolicyReason(StrEnum):
    """Stable explanation of the selected room policy."""

    AWAY_SAFE_OFF = "away_safe_off"
    AWAY_KEEP = "away_keep_air_conditioners"
    SAFETY_LOCKOUT = "safety_lockout"
    FRESHNESS_GUARD = "freshness_guard"
    MANUAL_OBSERVE = "manual_observe"
    FORCED_AUTOMATION = "forced_automation"
    AUTOMATIC = "automatic"


class ClimatePolicyBlocker(StrEnum):
    """Bounded facts which selected or constrained the policy."""

    AWAY = "away"
    WINDOW = "window"
    CRITICAL_SENSOR = "critical_sensor"
    COOLING_BLOCKED = "cooling_blocked"
    HEATING_BLOCKED = "heating_blocked"
    STALE_STATE = "stale_state"
    TEMPERATURE_JUMP = "temperature_jump"
    STALE_DELAYED_COMMAND = "stale_delayed_command"
    ROOM_MODE_UNKNOWN = "room_mode_unknown"
    FORCED_AUTO_ONLY = "forced_auto_only"
    MANUAL_NO_AUTOMATIC_PLAN = "manual_no_automatic_plan"
    COOLDOWN = "cooldown"
    DUPLICATE = "duplicate"
    AUTHORITY_NOT_GRANTED = "authority_not_granted"
    PHYSICAL_FEEDBACK_UNCONFIRMED = "physical_feedback_unconfirmed"
    MINIMUM_OFF_PAUSE = "minimum_off_pause"
    MINIMUM_RUN_HOLD = "minimum_run_hold"
    DEVICE_UNAVAILABLE = "device_unavailable"


class ClimateFinalDeviceAction(StrEnum):
    """Command-free final state requested for one selected climate device."""

    COOL = "cool"
    MAINTAIN = "maintain"
    OFF = "off"
    SAFE_OFF = "safe_off"
    HUMIDIFY = "humidify"
    HEAT = "heat"
    SET_TEMPERATURE = "set_temperature"
    HOLD = "hold"
    OBSERVE = "observe"
    UNAVAILABLE = "unavailable"


class ClimateFinalDeviceReason(StrEnum):
    """Why one final device result exists."""

    AUTOMATIC_PLAN = "automatic_plan"
    POLICY_SAFE_OFF = "policy_safe_off"
    ALREADY_OFF = "already_off"
    POLICY_OBSERVE = "policy_observe"
    DEVICE_UNAVAILABLE = "device_unavailable"


@dataclass(frozen=True, slots=True)
class ClimateFinalDevicePlan:
    """One redacted final device result, never an intent or service call."""

    device_id: str
    room_id: str
    kind: ClimateObservationDeviceKind
    action: ClimateFinalDeviceAction
    target_temperature: float | None
    fan_mode: ClimateFanMode | None
    quiet: bool | None
    reason: ClimateFinalDeviceReason

    def __post_init__(self) -> None:
        _stable_id(self.device_id, "final device id")
        _stable_id(self.room_id, "final device room id")
        if not isinstance(self.kind, ClimateObservationDeviceKind):
            raise ClimatePolicyViolation("final device kind must be approved")
        if not isinstance(self.action, ClimateFinalDeviceAction):
            raise ClimatePolicyViolation("final device action must be approved")
        _optional_number(
            self.target_temperature,
            10,
            35,
            "final target temperature",
        )
        if self.fan_mode is not None and not isinstance(
            self.fan_mode,
            ClimateFanMode,
        ):
            raise ClimatePolicyViolation("final fan mode must be approved")
        if self.quiet is not None and type(self.quiet) is not bool:
            raise ClimatePolicyViolation("final quiet state must be boolean")
        if not isinstance(self.reason, ClimateFinalDeviceReason):
            raise ClimatePolicyViolation("final device reason must be approved")
        if self.action not in {
            ClimateFinalDeviceAction.COOL,
            ClimateFinalDeviceAction.MAINTAIN,
            ClimateFinalDeviceAction.SET_TEMPERATURE,
        } and any(
            value is not None
            for value in (self.target_temperature, self.fan_mode, self.quiet)
        ):
            raise ClimatePolicyViolation(
                "non-setting final action must not retain device settings"
            )

    @property
    def safe_stop_required(self) -> bool:
        """Report a requested stop without granting permission to perform it."""

        return self.action is ClimateFinalDeviceAction.SAFE_OFF


@dataclass(frozen=True, slots=True)
class ClimateRoomPolicyPlan:
    """Strict final policy result for one configured room."""

    room: ClimateRoomObservation
    home: ClimateHomeObservation
    control: ClimateControlObservation
    resolution: ClimateRoomThermalResolution
    equipment: ClimateRoomEquipmentPlan
    stability: ClimateRoomStabilityPlan
    selected_devices: tuple[ClimateDeviceObservation, ...]
    contour_mode: ContourMode
    observed_at: int
    policy: ClimateRoomPolicy
    action: ClimatePolicyAction
    reason: ClimatePolicyReason
    blockers: tuple[ClimatePolicyBlocker, ...]
    devices: tuple[ClimateFinalDevicePlan, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.room, ClimateRoomObservation):
            raise ClimatePolicyViolation("a validated room is required")
        if not isinstance(self.home, ClimateHomeObservation):
            raise ClimatePolicyViolation("a validated home state is required")
        if not isinstance(self.control, ClimateControlObservation):
            raise ClimatePolicyViolation("validated control state is required")
        if not isinstance(self.resolution, ClimateRoomThermalResolution):
            raise ClimatePolicyViolation("a validated thermal resolution is required")
        if not isinstance(self.equipment, ClimateRoomEquipmentPlan):
            raise ClimatePolicyViolation("a validated equipment plan is required")
        if not isinstance(self.stability, ClimateRoomStabilityPlan):
            raise ClimatePolicyViolation("a validated stability plan is required")
        if type(self.selected_devices) is not tuple or any(
            not isinstance(device, ClimateDeviceObservation)
            for device in self.selected_devices
        ):
            raise ClimatePolicyViolation(
                "selected devices must be an immutable typed tuple"
            )
        if len(self.selected_devices) != len(
            {device.device_id for device in self.selected_devices}
        ):
            raise ClimatePolicyViolation("selected device ids must be unique")
        if any(device.room_id != self.room.room_id for device in self.selected_devices):
            raise ClimatePolicyViolation("selected devices must match their room")
        if not isinstance(self.contour_mode, ContourMode):
            raise ClimatePolicyViolation("policy contour mode must be approved")
        if len(
            {
                self.room.room_id,
                self.resolution.room_id,
                self.equipment.room_id,
                self.stability.room_id,
            }
        ) != 1:
            raise ClimatePolicyViolation("policy inputs must reference one room")
        if (
            self.resolution.season is not self.home.season
            or self.resolution.occupancy is not self.home.occupancy
            or self.resolution.central_heating_on is not self.home.central_heating_on
            or self.equipment.thermal is not self.resolution.thermal
        ):
            raise ClimatePolicyViolation("policy inputs must describe one home state")
        selected_by_id = {
            device.device_id: device for device in self.selected_devices
        }
        expected_equipment_ids = {
            device.device_id
            for device in self.selected_devices
            if device.kind
            in {
                ClimateObservationDeviceKind.AIR_CONDITIONER,
                ClimateObservationDeviceKind.RADIATOR_THERMOSTAT,
                ClimateObservationDeviceKind.FLOOR_HEATING,
            }
        }
        expected_stability_ids = {
            device.device_id
            for device in self.selected_devices
            if device.kind
            in {
                ClimateObservationDeviceKind.AIR_CONDITIONER,
                ClimateObservationDeviceKind.HUMIDIFIER,
            }
        }
        if expected_equipment_ids != {
            device.device_id for device in self.equipment.devices
        } or expected_stability_ids != {
            device.device_id for device in self.stability.devices
        }:
            raise ClimatePolicyViolation(
                "final policy must cover exactly the selected climate devices"
            )
        if any(
            selected_by_id[device.device_id].availability is not device.availability
            or selected_by_id[device.device_id].activity is not device.activity
            or device.room_data_status is not self.room.data_status
            for device in self.equipment.devices
        ):
            raise ClimatePolicyViolation(
                "equipment and policy observations must match"
            )
        if any(
            device.device != selected_by_id[device.device_id]
            or device.room != self.room
            for device in self.stability.devices
        ):
            raise ClimatePolicyViolation(
                "stability and policy observations must match"
            )
        _timestamp(self.observed_at, "policy observation time")
        if any(
            device.observed_at != self.observed_at
            for device in self.equipment.devices
        ) or any(
            device.observed_at != self.observed_at
            for device in self.stability.devices
        ):
            raise ClimatePolicyViolation("policy plans must use one observation time")
        if not isinstance(self.policy, ClimateRoomPolicy):
            raise ClimatePolicyViolation("room policy must be approved")
        if not isinstance(self.action, ClimatePolicyAction):
            raise ClimatePolicyViolation("room policy action must be approved")
        if not isinstance(self.reason, ClimatePolicyReason):
            raise ClimatePolicyViolation("room policy reason must be approved")
        if type(self.blockers) is not tuple or any(
            not isinstance(blocker, ClimatePolicyBlocker)
            for blocker in self.blockers
        ):
            raise ClimatePolicyViolation("policy blockers must be immutable and typed")
        if len(self.blockers) != len(set(self.blockers)):
            raise ClimatePolicyViolation("policy blockers must be unique")
        if type(self.devices) is not tuple or any(
            not isinstance(device, ClimateFinalDevicePlan) for device in self.devices
        ):
            raise ClimatePolicyViolation("final devices must be immutable and typed")
        if len(self.devices) != len({device.device_id for device in self.devices}):
            raise ClimatePolicyViolation("final device ids must be unique")
        if any(device.room_id != self.room.room_id for device in self.devices):
            raise ClimatePolicyViolation("final devices must match their room")
        expected = _expected_policy_output(
            room=self.room,
            home=self.home,
            control=self.control,
            resolution=self.resolution,
            equipment=self.equipment,
            stability=self.stability,
            selected_devices=self.selected_devices,
        )
        if (
            self.policy,
            self.action,
            self.reason,
            self.blockers,
            self.devices,
        ) != expected:
            raise ClimatePolicyViolation(
                "final policy output must match its validated inputs"
            )

    @property
    def room_id(self) -> str:
        return self.room.room_id

    @property
    def safe_stop_device_ids(self) -> tuple[str, ...]:
        """Return stable ids only for devices which need a protective stop."""

        return tuple(
            device.device_id for device in self.devices if device.safe_stop_required
        )


@dataclass(frozen=True, slots=True)
class ClimatePolicySnapshot:
    """Complete final policy result for one contour observation."""

    contour_id: str
    contour_mode: ContourMode
    observed_at: int
    rooms: tuple[ClimateRoomPolicyPlan, ...]
    version: int = CLIMATE_POLICY_MODEL_VERSION

    def __post_init__(self) -> None:
        _stable_id(self.contour_id, "policy contour id")
        if not isinstance(self.contour_mode, ContourMode):
            raise ClimatePolicyViolation("policy contour mode must be approved")
        _timestamp(self.observed_at, "policy snapshot time")
        if type(self.rooms) is not tuple or any(
            not isinstance(room, ClimateRoomPolicyPlan) for room in self.rooms
        ):
            raise ClimatePolicyViolation("policy rooms must be immutable and typed")
        if not self.rooms:
            raise ClimatePolicyViolation("policy rooms must not be empty")
        if len(self.rooms) != len({room.room_id for room in self.rooms}):
            raise ClimatePolicyViolation("policy room ids must be unique")
        if any(room.observed_at != self.observed_at for room in self.rooms):
            raise ClimatePolicyViolation("policy rooms must use one observation time")
        if any(room.contour_mode is not self.contour_mode for room in self.rooms):
            raise ClimatePolicyViolation("policy rooms must match the contour mode")
        if self.version != CLIMATE_POLICY_MODEL_VERSION:
            raise ClimatePolicyViolation("policy model version is unsupported")

    @property
    def commands_enabled(self) -> bool:
        """The final policy still cannot authorize Home Assistant commands."""

        return False

    def room(self, room_id: str) -> ClimateRoomPolicyPlan | None:
        return next((room for room in self.rooms if room.room_id == room_id), None)


def resolve_climate_room_policy(
    room: ClimateRoomObservation,
    home: ClimateHomeObservation,
    control: ClimateControlObservation,
    resolution: ClimateRoomThermalResolution,
    equipment: ClimateRoomEquipmentPlan,
    stability: ClimateRoomStabilityPlan,
    selected_devices: tuple[ClimateDeviceObservation, ...],
    *,
    contour_mode: ContourMode = ContourMode.AUTOMATIC,
    observed_at: int,
) -> ClimateRoomPolicyPlan:
    """Apply the fixed priority ladder to one room without creating commands."""

    output = _expected_policy_output(
        room=room,
        home=home,
        control=control,
        resolution=resolution,
        equipment=equipment,
        stability=stability,
        selected_devices=selected_devices,
    )
    return ClimateRoomPolicyPlan(
        room=room,
        home=home,
        control=control,
        resolution=resolution,
        equipment=equipment,
        stability=stability,
        selected_devices=selected_devices,
        contour_mode=contour_mode,
        observed_at=observed_at,
        policy=output[0],
        action=output[1],
        reason=output[2],
        blockers=output[3],
        devices=output[4],
    )


def _expected_policy_output(
    *,
    room: ClimateRoomObservation,
    home: ClimateHomeObservation,
    control: ClimateControlObservation,
    resolution: ClimateRoomThermalResolution,
    equipment: ClimateRoomEquipmentPlan,
    stability: ClimateRoomStabilityPlan,
    selected_devices: tuple[ClimateDeviceObservation, ...],
) -> tuple[
    ClimateRoomPolicy,
    ClimatePolicyAction,
    ClimatePolicyReason,
    tuple[ClimatePolicyBlocker, ...],
    tuple[ClimateFinalDevicePlan, ...],
]:
    if not all(
        (
            isinstance(room, ClimateRoomObservation),
            isinstance(home, ClimateHomeObservation),
            isinstance(control, ClimateControlObservation),
            isinstance(resolution, ClimateRoomThermalResolution),
            isinstance(equipment, ClimateRoomEquipmentPlan),
            isinstance(stability, ClimateRoomStabilityPlan),
            type(selected_devices) is tuple,
        )
    ):
        raise ClimatePolicyViolation("validated policy inputs are required")

    if home.occupancy is not ClimateOccupancyMode.HOME:
        if home.occupancy is ClimateOccupancyMode.AWAY_KEEP:
            return (
                ClimateRoomPolicy.AWAY,
                ClimatePolicyAction.OBSERVE,
                ClimatePolicyReason.AWAY_KEEP,
                (ClimatePolicyBlocker.AWAY,),
                (),
            )
        return (
            ClimateRoomPolicy.AWAY,
            ClimatePolicyAction.SAFE_OFF,
            ClimatePolicyReason.AWAY_SAFE_OFF,
            (ClimatePolicyBlocker.AWAY,),
            _safe_off_devices(selected_devices),
        )

    safety = _safety_blockers(room)
    if safety:
        return (
            ClimateRoomPolicy.SAFETY_LOCKOUT,
            ClimatePolicyAction.SAFE_OFF,
            ClimatePolicyReason.SAFETY_LOCKOUT,
            safety,
            _safe_off_devices(selected_devices),
        )

    freshness = _freshness_blockers(room, control)
    if freshness:
        return (
            ClimateRoomPolicy.FRESHNESS_GUARD,
            ClimatePolicyAction.OBSERVE,
            ClimatePolicyReason.FRESHNESS_GUARD,
            freshness,
            (),
        )

    manual_request = (
        control.manual_request and control.manual_request_room_id == room.room_id
    )
    if room.mode is ClimateRoomMode.FORCED_AUTO_ONLY:
        blockers = (
            (ClimatePolicyBlocker.FORCED_AUTO_ONLY,) if manual_request else ()
        ) + _automatic_blockers(
            room.room_id,
            control,
            selected_devices,
            stability,
            resolution,
        )
        return (
            ClimateRoomPolicy.FORCED_AUTO_ONLY,
            _automatic_room_action(resolution, equipment, stability),
            ClimatePolicyReason.FORCED_AUTOMATION,
            blockers,
            _automatic_devices(selected_devices, equipment, stability),
        )
    if room.mode is ClimateRoomMode.MANUAL or manual_request:
        return (
            ClimateRoomPolicy.MANUAL,
            ClimatePolicyAction.OBSERVE,
            ClimatePolicyReason.MANUAL_OBSERVE,
            (ClimatePolicyBlocker.MANUAL_NO_AUTOMATIC_PLAN,),
            (),
        )
    return (
        ClimateRoomPolicy.AUTO,
        _automatic_room_action(resolution, equipment, stability),
        ClimatePolicyReason.AUTOMATIC,
        _automatic_blockers(
            room.room_id,
            control,
            selected_devices,
            stability,
            resolution,
        ),
        _automatic_devices(selected_devices, equipment, stability),
    )


def _safety_blockers(
    room: ClimateRoomObservation,
) -> tuple[ClimatePolicyBlocker, ...]:
    blockers: list[ClimatePolicyBlocker] = []
    if room.window in {ClimateWindowState.OPEN, ClimateWindowState.UNKNOWN}:
        blockers.append(ClimatePolicyBlocker.WINDOW)
    if room.temperature is None:
        blockers.append(ClimatePolicyBlocker.CRITICAL_SENSOR)
    if room.cooling_allowed is False:
        blockers.append(ClimatePolicyBlocker.COOLING_BLOCKED)
    if room.heating_allowed is False:
        blockers.append(ClimatePolicyBlocker.HEATING_BLOCKED)
    return tuple(blockers)


def _freshness_blockers(
    room: ClimateRoomObservation,
    control: ClimateControlObservation,
) -> tuple[ClimatePolicyBlocker, ...]:
    blockers: list[ClimatePolicyBlocker] = []
    if room.data_status is not ClimateDataStatus.FRESH:
        blockers.append(ClimatePolicyBlocker.STALE_STATE)
    if room.temperature_quality is ClimateTemperatureQuality.SUSPECT:
        blockers.append(ClimatePolicyBlocker.TEMPERATURE_JUMP)
    if (
        control.delayed_intent is ClimateDelayedIntentState.STALE_AFTER_CONTROL_CHANGE
        and control.delayed_intent_room_id == room.room_id
    ):
        blockers.append(ClimatePolicyBlocker.STALE_DELAYED_COMMAND)
    if room.mode is ClimateRoomMode.UNKNOWN:
        blockers.append(ClimatePolicyBlocker.ROOM_MODE_UNKNOWN)
    return tuple(blockers)


def _automatic_blockers(
    room_id: str,
    control: ClimateControlObservation,
    selected_devices: tuple[ClimateDeviceObservation, ...],
    stability: ClimateRoomStabilityPlan,
    resolution: ClimateRoomThermalResolution,
) -> tuple[ClimatePolicyBlocker, ...]:
    mapping = {
        ClimateExecutionGuardState.COOLDOWN_ACTIVE: ClimatePolicyBlocker.COOLDOWN,
        ClimateExecutionGuardState.DUPLICATE: ClimatePolicyBlocker.DUPLICATE,
        ClimateExecutionGuardState.AUTHORITY_MISSING: ClimatePolicyBlocker.AUTHORITY_NOT_GRANTED,
    }
    blockers: list[ClimatePolicyBlocker] = []
    if control.execution_guard_room_id == room_id:
        blocker = mapping.get(control.execution_guard)
        if blocker is not None:
            blockers.append(blocker)
    if any(
        device.protection is ClimateStabilityProtection.MINIMUM_OFF
        for device in stability.devices
    ):
        blockers.append(ClimatePolicyBlocker.MINIMUM_OFF_PAUSE)
    if any(
        device.protection is ClimateStabilityProtection.MINIMUM_RUN
        for device in stability.devices
    ):
        blockers.append(ClimatePolicyBlocker.MINIMUM_RUN_HOLD)
    unavailable_device = any(
        device.availability is not ClimateDeviceAvailability.AVAILABLE
        for device in selected_devices
        if device.kind
        in {
            ClimateObservationDeviceKind.AIR_CONDITIONER,
            ClimateObservationDeviceKind.RADIATOR_THERMOSTAT,
            ClimateObservationDeviceKind.HUMIDIFIER,
            ClimateObservationDeviceKind.FLOOR_HEATING,
        }
    )
    if (
        resolution.thermal is ClimateThermalResolution.COOLING
        and not any(
            device.kind is ClimateObservationDeviceKind.AIR_CONDITIONER
            and device.availability is ClimateDeviceAvailability.AVAILABLE
            for device in selected_devices
        )
    ) or (
        resolution.thermal is ClimateThermalResolution.HEATING
        and not any(
            device.kind
            in {
                ClimateObservationDeviceKind.RADIATOR_THERMOSTAT,
                ClimateObservationDeviceKind.FLOOR_HEATING,
            }
            and device.availability is ClimateDeviceAvailability.AVAILABLE
            for device in selected_devices
        )
    ):
        unavailable_device = True
    if unavailable_device:
        blockers.append(ClimatePolicyBlocker.DEVICE_UNAVAILABLE)
    if any(
        device.kind is ClimateObservationDeviceKind.AIR_CONDITIONER
        and device.cooling_evidence_confirmed
        and device.physical_feedback is ClimatePhysicalFeedback.STALE
        for device in selected_devices
    ):
        blockers.append(ClimatePolicyBlocker.PHYSICAL_FEEDBACK_UNCONFIRMED)
    return tuple(blockers)


def _automatic_room_action(
    resolution: ClimateRoomThermalResolution,
    equipment: ClimateRoomEquipmentPlan,
    stability: ClimateRoomStabilityPlan,
) -> ClimatePolicyAction:
    ac = next(
        (
            device
            for device in stability.devices
            if device.kind is ClimateObservationDeviceKind.AIR_CONDITIONER
        ),
        None,
    )
    if ac is not None and ac.action is not ClimateStabilityAction.UNAVAILABLE:
        mapped = {
            ClimateStabilityAction.COOL: ClimatePolicyAction.COOL,
            ClimateStabilityAction.MAINTAIN: ClimatePolicyAction.MAINTAIN,
            ClimateStabilityAction.OFF: ClimatePolicyAction.OFF,
            ClimateStabilityAction.SAFE_OFF: ClimatePolicyAction.SAFE_OFF,
            ClimateStabilityAction.OBSERVE: ClimatePolicyAction.OBSERVE,
        }.get(ac.action)
        if mapped is not None:
            return mapped
    if any(
        device.kind
        in {
            ClimateObservationDeviceKind.RADIATOR_THERMOSTAT,
            ClimateObservationDeviceKind.FLOOR_HEATING,
        }
        for device in equipment.devices
    ):
        return ClimatePolicyAction.OBSERVE
    if resolution.thermal is ClimateThermalResolution.COOLING:
        return ClimatePolicyAction.COOL
    if resolution.thermal is ClimateThermalResolution.SAFE_OFF:
        return ClimatePolicyAction.SAFE_OFF
    if resolution.thermal in {
        ClimateThermalResolution.HEATING,
        ClimateThermalResolution.OBSERVE,
        ClimateThermalResolution.UNAVAILABLE,
    }:
        return ClimatePolicyAction.OBSERVE
    return ClimatePolicyAction.OFF


def _automatic_devices(
    selected: tuple[ClimateDeviceObservation, ...],
    equipment: ClimateRoomEquipmentPlan,
    stability: ClimateRoomStabilityPlan,
) -> tuple[ClimateFinalDevicePlan, ...]:
    stable = {device.device_id: device for device in stability.devices}
    thermal = {device.device_id: device for device in equipment.devices}
    result: list[ClimateFinalDevicePlan] = []
    for observed in selected:
        protected = stable.get(observed.device_id)
        if protected is not None:
            result.append(
                ClimateFinalDevicePlan(
                    device_id=observed.device_id,
                    room_id=observed.room_id,
                    kind=observed.kind,
                    action=ClimateFinalDeviceAction(protected.action.value),
                    target_temperature=protected.target_temperature,
                    fan_mode=protected.fan_mode,
                    quiet=protected.quiet,
                    reason=(
                        ClimateFinalDeviceReason.DEVICE_UNAVAILABLE
                        if protected.action is ClimateStabilityAction.UNAVAILABLE
                        else ClimateFinalDeviceReason.AUTOMATIC_PLAN
                    ),
                )
            )
            continue
        planned = thermal.get(observed.device_id)
        if planned is not None:
            result.append(
                ClimateFinalDevicePlan(
                    device_id=observed.device_id,
                    room_id=observed.room_id,
                    kind=observed.kind,
                    action=_equipment_action(planned.action),
                    target_temperature=planned.target_temperature,
                    fan_mode=planned.fan_mode,
                    quiet=planned.quiet,
                    reason=(
                        ClimateFinalDeviceReason.DEVICE_UNAVAILABLE
                        if planned.action is ClimateEquipmentAction.UNAVAILABLE
                        else ClimateFinalDeviceReason.AUTOMATIC_PLAN
                    ),
                )
            )
    return tuple(result)


def _safe_off_devices(
    selected: tuple[ClimateDeviceObservation, ...],
) -> tuple[ClimateFinalDevicePlan, ...]:
    actuators = {
        ClimateObservationDeviceKind.AIR_CONDITIONER,
        ClimateObservationDeviceKind.RADIATOR_THERMOSTAT,
        ClimateObservationDeviceKind.HUMIDIFIER,
        ClimateObservationDeviceKind.FLOOR_HEATING,
    }
    stopped = {ClimateDeviceActivity.STOPPED, ClimateDeviceActivity.IDLE}
    result: list[ClimateFinalDevicePlan] = []
    for device in selected:
        if device.kind not in actuators:
            continue
        if device.availability is not ClimateDeviceAvailability.AVAILABLE:
            action = ClimateFinalDeviceAction.UNAVAILABLE
            reason = ClimateFinalDeviceReason.DEVICE_UNAVAILABLE
        elif device.kind is ClimateObservationDeviceKind.RADIATOR_THERMOSTAT:
            action = ClimateFinalDeviceAction.OBSERVE
            reason = ClimateFinalDeviceReason.POLICY_OBSERVE
        elif device.activity in stopped:
            action = ClimateFinalDeviceAction.OFF
            reason = ClimateFinalDeviceReason.ALREADY_OFF
        else:
            action = ClimateFinalDeviceAction.SAFE_OFF
            reason = ClimateFinalDeviceReason.POLICY_SAFE_OFF
        result.append(
            ClimateFinalDevicePlan(
                device_id=device.device_id,
                room_id=device.room_id,
                kind=device.kind,
                action=action,
                target_temperature=None,
                fan_mode=None,
                quiet=None,
                reason=reason,
            )
        )
    return tuple(result)


def _equipment_action(action: ClimateEquipmentAction) -> ClimateFinalDeviceAction:
    mapping = {
        ClimateEquipmentAction.COOL: ClimateFinalDeviceAction.COOL,
        ClimateEquipmentAction.SET_TEMPERATURE: ClimateFinalDeviceAction.SET_TEMPERATURE,
        ClimateEquipmentAction.HEAT: ClimateFinalDeviceAction.HEAT,
        ClimateEquipmentAction.SAFE_OFF: ClimateFinalDeviceAction.SAFE_OFF,
        ClimateEquipmentAction.HOLD: ClimateFinalDeviceAction.HOLD,
        ClimateEquipmentAction.OBSERVE: ClimateFinalDeviceAction.OBSERVE,
        ClimateEquipmentAction.UNAVAILABLE: ClimateFinalDeviceAction.UNAVAILABLE,
    }
    return mapping[action]


def _stable_id(value: object, label: str) -> None:
    try:
        ClimateRoom(value, "Object")  # type: ignore[arg-type]
    except ClimateModelViolation as error:
        raise ClimatePolicyViolation(f"{label} must be stable") from error


def _timestamp(value: object, label: str) -> None:
    if type(value) is not int or not 0 <= value <= 9_999_999_999_999:
        raise ClimatePolicyViolation(f"{label} must be bounded milliseconds")


def _optional_number(
    value: object,
    minimum: float,
    maximum: float,
    label: str,
) -> None:
    if value is not None and (
        type(value) not in {int, float}
        or not math.isfinite(value)
        or not minimum <= value <= maximum
    ):
        raise ClimatePolicyViolation(f"{label} is outside fixed bounds")
