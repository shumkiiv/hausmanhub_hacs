"""Pure lighting module of the lighting path (HH-014.b, K-04).

Wraps the existing pure engine ``evaluate_room_lighting`` in the core
module contract: the plugin converts the normalized core frame into the
engine context, delegates the calculation and returns proposals. No
Home Assistant, no storage, no dispatch, no wall clock: every input is
in the frame or the frozen construction-time profile.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Mapping

from .room_lighting_engine import (
    LightAction,
    LightSnapshot,
    RoomLightingConfig,
    RoomLightingContext,
    SensorKind as EngineSensorKind,
    SensorSnapshot,
    SensorState,
    evaluate_room_lighting,
)
from .core.models import (
    Evaluation,
    InputFrame,
    LightStateTarget,
    Proposal,
    ProposalKind,
)

_COUNTED_PROPERTIES = ("on", "occupancy", "motion", "brightness_pct")


def _sensor_state(value: object) -> SensorState:
    """The engine speaks on/off for presence and motion inputs."""
    return SensorState.ON if bool(value) else SensorState.OFF


class LightingPlugin:
    """Core module for one lighting profile (K-04, delivery HH-014.b).

    The room configuration is frozen at construction; the module owns no
    mutable policy. ``evaluate`` is deterministic for equal inputs.
    """

    def __init__(
        self,
        module_id: str,
        config: RoomLightingConfig,
        *,
        timezone,
        sunrise,
        sunset,
        policy_priority: int = 50,
        proposal_ttl_mono_ms: int = 5000,
    ) -> None:
        self.module_id = module_id
        self._module_id = module_id
        self._config = config
        self._timezone = timezone
        self._sunrise = sunrise
        self._sunset = sunset
        self._policy_priority = policy_priority
        self._proposal_ttl_mono_ms = proposal_ttl_mono_ms
        self._input_revision = 0

    @property
    def config(self) -> RoomLightingConfig:
        return self._config

    def validate_config(self, config: Mapping[str, object], catalog: object):
        # The profile config is validated by config_from_payload at
        # construction; nothing additional applies at evaluation time.
        return ()

    def evaluate(self, frame: InputFrame, previous_memory: object) -> Evaluation:
        self._input_revision += 1
        sensors, lights = self._engine_snapshots(frame.observations)
        context = RoomLightingContext(
            now=frame.now_utc_ms,
            timezone=self._timezone,
            sunrise=self._sunrise,
            sunset=self._sunset,
            sensors=sensors,
            lights=lights,
        )
        decision = evaluate_room_lighting(
            self._config,
            context,
            mode="shadow",
        )
        now = frame.now_mono_ms
        proposals = tuple(
            Proposal(
                proposal_id=f"{self._module_id}.{command.target_id}",
                module_id=self._module_id,
                resource_id=command.target_id,
                kind=ProposalKind.TARGET,
                target=LightStateTarget(
                    on=command.action is LightAction.TURN_ON,
                    brightness_pct=(
                        Decimal(command.brightness)
                        if command.brightness is not None
                        else None
                    ),
                ),
                policy_priority=self._policy_priority,
                created_at_mono_ms=now,
                valid_until_mono_ms=now + self._proposal_ttl_mono_ms,
                input_revision=self._input_revision,
                config_revision=frame.config_revision,
                reason_code=command.reason.value,
            )
            for command in decision.commands
        )
        return Evaluation(
            module_id=self._module_id,
            config_revision=frame.config_revision,
            input_revision=self._input_revision,
            proposals=proposals,
        )

    def _engine_snapshots(self, observations):
        """Latest state per resource from the normalized core frame.

        Light resources produce ``LightSnapshot``; configured presence and
        motion sensors produce ``SensorSnapshot``. Resources without a
        current value are simply absent from the context.
        """

        latest: dict[str, dict[str, object]] = {}
        for observation in observations:
            if observation.property not in _COUNTED_PROPERTIES:
                continue
            resource = latest.setdefault(observation.resource_id, {})
            resource[observation.property] = observation
        known_targets = {
            target.id for target in self._config.devices.light_targets
        }
        known_sensors = {
            sensor.id: sensor for sensor in self._config.devices.sensors
        }
        lights: list[LightSnapshot] = []
        sensors: list[SensorSnapshot] = []
        for resource_id in sorted(latest):
            properties = latest[resource_id]
            on = properties.get("on")
            if resource_id in known_targets and on is not None:
                brightness_observation = properties.get("brightness_pct")
                brightness = (
                    int(brightness_observation.value)
                    if brightness_observation is not None
                    and brightness_observation.value is not None
                    else None
                )
                lights.append(
                    LightSnapshot(
                        target_id=resource_id,
                        state=_sensor_state(bool(on.value)),
                        last_changed=on.received_at_mono_ms,
                        brightness=brightness,
                    )
                )
                continue
            occupancy = properties.get("occupancy")
            motion = properties.get("motion")
            source = occupancy or motion
            sensor = known_sensors.get(resource_id)
            if sensor is None or source is None:
                continue
            kind = EngineSensorKind(sensor.kind)
            sensors.append(
                SensorSnapshot(
                    sensor_id=resource_id,
                    kind=kind,
                    state=_sensor_state(
                        source.value in (True, "on", "active")
                    ),
                    last_changed=source.received_at_mono_ms,
                )
            )
        return sensors, lights
