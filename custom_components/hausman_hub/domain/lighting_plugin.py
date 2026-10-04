"""Pure lighting module of the lighting path (HH-014.b, K-04).

Wraps the existing pure engine ``evaluate_room_lighting`` in the core
module contract: the plugin converts the normalized core frame into the
engine context, delegates the calculation and returns proposals. No
Home Assistant, no storage, no dispatch, no wall clock: every input is
in the frame or the frozen construction-time profile.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
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
from .room_lighting_ownership import OwnershipSnapshot, OwnershipSource
from .core.models import (
    Authority,
    Evaluation,
    Evidence,
    InputFrame,
    LightStateTarget,
    SwitchStateTarget,
    Proposal,
    ProposalKind,
    Quality,
)

_COUNTED_PROPERTIES = ("on", "occupancy", "motion", "brightness_pct", "color_temperature_k")


@dataclass(frozen=True)
class ProfileTransitionMemory:
    """One bounded L-SYN-1 transition, replaced atomically on every frame.

    This deliberately contains only the participants and the exact target
    whose proof may complete the transition.  It is not a second authority
    store and cannot accumulate input history.
    """
    main_id: str
    power_id: str
    mirror_ids: tuple[str, ...]
    desired_main_target: LightStateTarget
    boot_id: str
    config_revision: int
    main_binding_revision: int
    cancel_versions: tuple[tuple[str, int], ...]
    ready_at_mono_ms: int | None = None


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
        power_warmup_ms: int = 0,
    ) -> None:
        self.module_id = module_id
        self._module_id = module_id
        self._config = config
        self._timezone = timezone
        self._sunrise = sunrise
        self._sunset = sunset
        self._policy_priority = policy_priority
        self._proposal_ttl_mono_ms = proposal_ttl_mono_ms
        if (
            isinstance(power_warmup_ms, bool)
            or not isinstance(power_warmup_ms, int)
            or power_warmup_ms < 0
        ):
            raise ValueError("power_warmup_ms must be a non-negative int")
        self._power_warmup_ms = power_warmup_ms
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
        sensors, lights = self._engine_snapshots(frame)
        context = RoomLightingContext(
            now=frame.now_utc_ms,
            timezone=self._timezone,
            sunrise=self._sunrise,
            sunset=self._sunset,
            sensors=sensors,
            lights=lights,
            ownership=self._ownership_snapshots(frame),
        )
        decision = evaluate_room_lighting(
            self._config,
            context,
            mode="shadow",
        )
        now = frame.now_mono_ms
        proposals = tuple(
            Proposal(
                proposal_id=f"{self._module_id}.{target.target_id}",
                module_id=self._module_id,
                resource_id=target.target_id,
                kind=ProposalKind.TARGET,
                target=LightStateTarget(
                    # A SET is always an enabled adjustment.  Only a real
                    # TURN_OFF creates an OFF target.
                    on=False if any(c.action is LightAction.TURN_OFF for c in target.commands) else True,
                    brightness_pct=(
                        Decimal(target.desired_brightness)
                        if target.desired_state == "on" and target.desired_brightness is not None
                        else Decimal(next(c.brightness for c in target.commands if c.brightness is not None))
                        if target.desired_state != "on" and any(c.brightness is not None for c in target.commands)
                        else None
                    ),
                    color_temperature_k=(
                        target.desired_color_temperature
                        if target.desired_state == "on"
                        else None
                    ),
                ),
                policy_priority=self._policy_priority,
                created_at_mono_ms=now,
                valid_until_mono_ms=now + self._proposal_ttl_mono_ms,
                input_revision=self._input_revision,
                config_revision=frame.config_revision,
                reason_code=target.commands[0].reason.value,
            )
            for target in decision.targets if target.commands
        )
        proposals, memory, wakeup = self._apply_power_transition(
            frame, proposals, previous_memory
        )
        return Evaluation(
            module_id=self._module_id,
            config_revision=frame.config_revision,
            input_revision=self._input_revision,
            proposals=proposals,
            next_memory=memory,
            next_wakeup_mono_ms=wakeup,
        )

    def _apply_power_transition(self, frame, proposals, previous_memory):
        power = self._config.devices.power_switch
        main = next((x for x in self._config.devices.light_targets
                     if x.role is not None and x.role.value == "main"), None)
        if power is None or main is None:
            return proposals, None, None
        wanted = next((p for p in proposals if p.resource_id == main.id and p.target.on), None)
        power_fact = next((o for o in frame.observations if o.resource_id == power.id and o.property == "on"), None)
        now = frame.now_mono_ms
        mirror_ids = {item.id for item in self._config.devices.light_targets
                      if item.role is not None and item.role.value == "mirror"}
        def without_mirror_off(items):
            return tuple(item for item in items if not (
                item.resource_id in mirror_ids and item.target.on is False
            ))
        current = next((record for record in frame.resource_controls if record.resource_id == main.id), None)
        memory = previous_memory if isinstance(previous_memory, ProfileTransitionMemory) else None
        if wanted is None and memory is not None:
            # The engine can omit an already-dispatched request.  Retain the
            # exact target from the transition, never infer it from an ON.
            wanted_target = memory.desired_main_target
        elif wanted is not None:
            wanted_target = wanted.target
        else:
            # A scheduled mirror OFF is never independent of the main
            # transition.  Without a current automatic target there is no
            # target against which its ownership can be proved; in
            # particular, a manually confirmed main ON must not leave a
            # mirror OFF proposal behind.
            return without_mirror_off(proposals), None, None
        cancel_versions = tuple(sorted(
            (record.resource_id, record.generation)
            for record in frame.resource_controls
            if record.resource_id in {main.id, *mirror_ids}
        ))
        if memory is None or (
            memory.main_id != main.id
            or memory.power_id != power.id
            or memory.desired_main_target != wanted_target
            or memory.boot_id != frame.boot_id
            or memory.config_revision != frame.config_revision
            or memory.cancel_versions != cancel_versions
        ):
            memory = ProfileTransitionMemory(
                main_id=main.id,
                power_id=power.id,
                mirror_ids=tuple(sorted(mirror_ids)),
                desired_main_target=wanted_target,
                boot_id=frame.boot_id,
                config_revision=frame.config_revision,
                main_binding_revision=(current.binding_revision if current else 0),
                cancel_versions=cancel_versions,
            )
        def proven_main_target() -> bool:
            if (
                current is None
                or current.manual_active_at_utc(frame.now_utc_ms)
                or current.authority is not Authority.AUTOMATIC_ELIGIBLE
                or current.uncertainty_reason is not None
            ):
                return False
            proof = current.auto_proof
            if not (
                proof is not None
                and proof.generation == current.generation
                and proof.binding_revision == current.binding_revision
                and current.binding_revision == memory.main_binding_revision
                and proof.confirmed_target == memory.desired_main_target
            ):
                return False
            expected = {
                "on": memory.desired_main_target.on,
                "brightness_pct": memory.desired_main_target.brightness_pct,
                "color_temperature_k": memory.desired_main_target.color_temperature_k,
            }
            facts = {
                observation.property: observation
                for observation in frame.observations
                if observation.resource_id == main.id
            }
            return all(
                expected[name] is None or (
                    (fact := facts.get(name)) is not None
                    # The application frame projects auto_proof only while
                    # the current report matches this requested target under
                    # its capability passport.  The pure module therefore
                    # never repeats tolerance arithmetic or compares against
                    # a historical observation value.
                    and fact.quality is Quality.FRESH
                    and fact.evidence_kind is Evidence.DEVICE_REPORT
                    and fact.boot_id == frame.boot_id
                    and fact.binding_revision == current.binding_revision
                    and fact.source_sequence is not None
                )
                for name in expected
            )
        def mirrors_are_proven() -> bool:
            controls = {record.resource_id: record for record in frame.resource_controls}
            return all(
                (record := controls.get(mirror_id)) is not None
                and not record.manual_active_at_utc(frame.now_utc_ms)
                and record.authority is Authority.AUTOMATIC_ELIGIBLE
                and record.uncertainty_reason is None
                and record.auto_proof is not None
                and record.auto_proof.generation == record.generation
                and record.auto_proof.binding_revision == record.binding_revision
                for mirror_id in memory.mirror_ids
            )
        def mirrors_are_confirmed_off() -> bool:
            facts = {
                (observation.resource_id, observation.property): observation
                for observation in frame.observations
            }
            return all(
                (fact := facts.get((mirror_id, "on"))) is not None
                and fact.value is False
                and fact.quality is Quality.FRESH
                and fact.evidence_kind is Evidence.DEVICE_REPORT
                and fact.boot_id == frame.boot_id
                for mirror_id in memory.mirror_ids
            )
        if proven_main_target() and mirrors_are_confirmed_off():
            # The bounded transition ends only after the old automatic
            # source has actually reported OFF, never when OFF is queued.
            return proposals, None, None
        if proven_main_target() and mirrors_are_proven():
            return proposals, memory, None
        power_is_freshly_confirmed = bool(
            power_fact is not None
            and power_fact.value is True
            and power_fact.quality is Quality.FRESH
            and power_fact.evidence_kind is Evidence.DEVICE_REPORT
            and power_fact.boot_id == frame.boot_id
            and power_fact.source_sequence is not None
        )
        if not power_is_freshly_confirmed:
            filtered = without_mirror_off(tuple(p for p in proposals if p.resource_id != main.id))
            switch = Proposal(
                proposal_id=f"{self._module_id}.{power.id}.on", module_id=self._module_id,
                resource_id=power.id, kind=ProposalKind.TARGET, target=SwitchStateTarget(True),
                policy_priority=self._policy_priority, created_at_mono_ms=now,
                valid_until_mono_ms=now + self._proposal_ttl_mono_ms,
                input_revision=self._input_revision, config_revision=frame.config_revision,
                reason_code="power_dependency",
            )
            return filtered + (switch,), memory, None
        if memory.main_id == main.id:
            if memory.ready_at_mono_ms is None:
                ready = power_fact.received_at_mono_ms + self._power_warmup_ms
                if now < ready:
                    return without_mirror_off(tuple(p for p in proposals if p.resource_id != main.id)), replace(memory, ready_at_mono_ms=ready), ready
            elif now < memory.ready_at_mono_ms:
                return without_mirror_off(tuple(p for p in proposals if p.resource_id != main.id)), memory, memory.ready_at_mono_ms
            # A main proof is still required before a mirror OFF can leave
            # this module, even after the warm-up has elapsed.
            return without_mirror_off(proposals), memory, None
        return proposals, None, None

    def _ownership_snapshots(self, frame: InputFrame) -> tuple[OwnershipSnapshot, ...]:
        """Project the core's proven authority into the existing engine model.

        A reported ON alone has no automatic ownership.  Only a current
        matching proof earns AUTO; a live manual hold wins over it.
        """
        targets = {item.id for item in self._config.devices.light_targets}
        result: list[OwnershipSnapshot] = []
        for record in frame.resource_controls:
            if record.resource_id not in targets:
                continue
            if record.manual_active_at_utc(frame.now_utc_ms):
                source = OwnershipSource.MANUAL
                confirmed = True
            elif (
                record.auto_proof is not None
                and record.auto_proof.generation == record.generation
                and record.auto_proof.binding_revision == record.binding_revision
                and getattr(record.auto_proof.confirmed_target, "on", False) is True
            ):
                source = OwnershipSource.AUTO
                confirmed = True
            else:
                source = OwnershipSource.UNKNOWN
                confirmed = False
            result.append(OwnershipSnapshot(
                target_id=record.resource_id,
                source=source,
                confirmed=confirmed,
                at=(record.auto_proof.confirmed_at_utc_ms
                    if source is OwnershipSource.AUTO and record.auto_proof else frame.now_utc_ms),
            ))
        return tuple(result)

    def _engine_snapshots(self, frame: InputFrame):
        """Latest state per resource from the normalized core frame.

        Light resources produce ``LightSnapshot``; configured presence and
        motion sensors produce ``SensorSnapshot``. A configured presence or
        motion sensor without a current value is represented as UNKNOWN;
        other resources without a current value are absent from the context.
        """

        latest: dict[str, dict[str, object]] = {}
        for observation in frame.observations:
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
        def valid(observation):
            if observation is None:
                return False
            age = frame.now_mono_ms - observation.received_at_mono_ms
            return (
                observation.quality is Quality.FRESH
                and observation.boot_id == frame.boot_id
                and observation.evidence_kind not in (Evidence.RESTORED, Evidence.OPTIMISTIC)
                and age >= 0
                and frame.now_utc_ms >= age
            )

        def changed(observation):
            return frame.now_utc_ms - (frame.now_mono_ms - observation.received_at_mono_ms)

        for resource_id in sorted(known_targets):
            properties = latest.get(resource_id, {})
            on = properties.get("on")
            if on is not None:
                brightness_observation = properties.get("brightness_pct")
                color_observation = properties.get("color_temperature_k")
                lights.append(LightSnapshot(
                    target_id=resource_id,
                    state=_sensor_state(on.value) if valid(on) else SensorState.UNKNOWN,
                    last_changed=changed(on) if valid(on) else frame.now_utc_ms,
                    brightness=(int(Decimal(str(brightness_observation.value))) if valid(brightness_observation) and brightness_observation.value is not None else None),
                    color_temperature=(int(color_observation.value) if valid(color_observation) and color_observation.value is not None else None),
                ))
        for resource_id in sorted(known_sensors):
            properties = latest.get(resource_id, {})
            occupancy = properties.get("occupancy")
            motion = properties.get("motion")
            source = occupancy or motion
            sensor = known_sensors.get(resource_id)
            kind = EngineSensorKind(sensor.kind)
            if source is None and kind not in (
                EngineSensorKind.PRESENCE,
                EngineSensorKind.MOTION,
            ):
                continue
            sensors.append(
                SensorSnapshot(
                    sensor_id=resource_id,
                    kind=kind,
                    state=(_sensor_state(source.value in (True, "on", "active")) if valid(source) else SensorState.UNKNOWN),
                    last_changed=changed(source) if valid(source) else frame.now_utc_ms,
                )
            )
        return sensors, lights
