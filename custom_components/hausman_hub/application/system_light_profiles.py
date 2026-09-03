"""Disabled, catalog-audited first-wave light protection profiles.

These are declarations, not bootstrap settings.  Keeping them separate from
the coordinator means an integration upgrade cannot start protecting a live
automation or alter the enabled state of an existing scenario.
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


SYSTEM_LIGHT_PROFILES: tuple[ProtectedLightProfile, ...] = (
    ProtectedLightProfile("tambur", "tambur-chandelier", ("entity_71859313239a14e4",)),
    ProtectedLightProfile("tambur", "tambur-points", ("entity_cd0098e5ff95da46",)),
    ProtectedLightProfile("tambur", "tambur-mirror", ("entity_fbdf27871edb89bf",)),
    ProtectedLightProfile("small-corridor", "small-corridor-lights", ("entity_c9d6bc67f172f30d", "entity_9ed909332fdaa8fd")),
    ProtectedLightProfile("shower", "shower-lights", ("entity_4be32416634e6416", "entity_1fdcd8b244637246", "entity_e7a7c61eec7bdff8")),
    ProtectedLightProfile("storage", "storage-line-1", ("entity_0ec37ef18b4b39a6",)),
    ProtectedLightProfile("toilet", "toilet-lights", ("entity_6667b3400bce7970", "entity_5d95de599d2b5cec")),
    ProtectedLightProfile("bathroom", "bathroom-lights", ("entity_a591e035e3e5b34f", "entity_d82766182d69dd51")),
)


def audit_system_light_protection_coverage(
    profiles: tuple[ProtectedLightProfile, ...],
    scenario_targets: tuple[ScenarioTarget, ...],
) -> LightProtectionCoverageReport:
    """Fail closed when a first-wave automatic light lacks one owner.

    Every item must retain the catalog's stable entity/target binding. A switch
    is accepted only after an explicit light role classification.
    """

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
        elif domain == "light":
            continue
        elif domain != "switch" or role != "light":
            unsafe.append(target.target_id)

    return LightProtectionCoverageReport(
        unowned_target_ids=tuple(sorted(target_id for target_id in target_ids if ownership.get(target_id, 0) == 0)),
        multiply_owned_target_ids=tuple(sorted(target_id for target_id in target_ids if ownership.get(target_id, 0) > 1)),
        unsafe_target_ids=tuple(sorted(set(unsafe))),
    )
