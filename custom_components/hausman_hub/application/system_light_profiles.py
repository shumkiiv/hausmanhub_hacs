"""Privacy-safe light protection profile helpers.

The public package ships no first-wave private light topology. Installed homes
keep their persisted room-lighting configuration; tests and admin tools may pass
explicit profiles into the audit helpers.
"""

from __future__ import annotations

from dataclasses import dataclass

from .scenario_catalog import _stable_target_id_from_entity


@dataclass(frozen=True, slots=True)
class ProtectedLightProfile:
    room_id: str
    profile_id: str
    light_ids: tuple[str, ...]
    presence_sensor_ids: tuple[str, ...] = ()
    enabled: bool = False


@dataclass(frozen=True, slots=True)
class ScenarioTarget:
    """Catalog classification used by the coverage audit.

    ``role`` is intentionally explicit.  A Home Assistant ``switch`` domain
    alone cannot be accepted as a light because breakers, valves and outlets
    use that same domain.
    """

    target_id: str
    entity_id: str
    role: str


@dataclass(frozen=True, slots=True)
class LightProtectionCoverageReport:
    unowned_target_ids: tuple[str, ...] = ()
    multiply_owned_target_ids: tuple[str, ...] = ()
    unsafe_target_ids: tuple[str, ...] = ()

    @property
    def healthy(self) -> bool:
        return not (
            self.unowned_target_ids
            or self.multiply_owned_target_ids
            or self.unsafe_target_ids
        )


SYSTEM_LIGHT_PROFILES: tuple[ProtectedLightProfile, ...] = ()


@dataclass(frozen=True, slots=True)
class FirstWaveAutoOnTarget:
    target_id: str
    role: str
    entity_id: str | None = None


FIRST_WAVE_AUTO_ON_TARGETS: tuple[FirstWaveAutoOnTarget, ...] = ()


def scenario_targets_for_system_light_profiles(catalog: object) -> tuple[ScenarioTarget, ...]:
    """Read every declared first-wave target from the live catalog fail-closed."""

    targets: list[ScenarioTarget] = []
    device_for = getattr(catalog, "device", None)
    for declared in FIRST_WAVE_AUTO_ON_TARGETS:
        device = device_for(declared.target_id) if callable(device_for) else None
        entity_id = getattr(device, "entity_id", None)
        actions = getattr(device, "actions", ())
        has_auto_on = any(
            getattr(action, "action_id", None) == "turn_on"
            and getattr(action, "domain", None) in {"light", "switch"}
            for action in actions
        )
        if not isinstance(entity_id, str) or not has_auto_on or (
            declared.entity_id is not None and entity_id != declared.entity_id
        ):
            targets.append(ScenarioTarget(declared.target_id, "invalid.invalid", "invalid"))
        else:
            targets.append(ScenarioTarget(declared.target_id, entity_id, declared.role))
    return tuple(targets)


def audit_system_light_protection_coverage(
    profiles: tuple[ProtectedLightProfile, ...],
    scenario_targets: tuple[ScenarioTarget, ...],
) -> LightProtectionCoverageReport:
    """Fail closed when an automatic light lacks exactly one owner."""

    ownership: dict[str, int] = {}
    for profile in profiles:
        for target_id in profile.light_ids:
            ownership[target_id] = ownership.get(target_id, 0) + 1

    target_ids: list[str] = []
    unsafe: list[str] = []
    for target in scenario_targets:
        if not isinstance(target, ScenarioTarget):
            raise TypeError("scenario target must retain its catalog classification")
        target_ids.append(target.target_id)
        role = " ".join(target.role.casefold().replace("_", " ").split())
        domain = target.entity_id.partition(".")[0]
        if target.target_id != _stable_target_id_from_entity(target.entity_id):
            unsafe.append(target.target_id)
        elif domain not in {"light", "switch"} or role != "light":
            unsafe.append(target.target_id)

    return LightProtectionCoverageReport(
        unowned_target_ids=tuple(sorted(target_id for target_id in target_ids if ownership.get(target_id, 0) == 0)),
        multiply_owned_target_ids=tuple(sorted(target_id for target_id in target_ids if ownership.get(target_id, 0) > 1)),
        unsafe_target_ids=tuple(sorted(set(unsafe))),
    )
