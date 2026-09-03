"""First-wave manual light-off protection profile catalog tests."""

from custom_components.hausman_hub.application.system_light_profiles import (
    ScenarioTarget,
    SYSTEM_LIGHT_PROFILES,
    audit_system_light_protection_coverage,
)
from custom_components.hausman_hub.application.scenario_catalog import (
    _stable_target_id_from_entity,
)
from custom_components.hausman_hub.application.system_light_profiles import ProtectedLightProfile
from custom_components.hausman_hub.application.system_light_profiles import (
    FIRST_WAVE_AUTO_ON_TARGETS,
    scenario_targets_for_system_light_profiles,
)
from types import SimpleNamespace


def test_first_wave_profiles_use_the_catalog_target_ids_and_exclude_non_lights() -> None:
    profiles = {profile.profile_id: profile for profile in SYSTEM_LIGHT_PROFILES}

    assert profiles["tambur-chandelier"].light_ids == ("entity_71859313239a14e4",)
    assert profiles["tambur-points"].light_ids == ("entity_cd0098e5ff95da46",)
    assert profiles["tambur-mirror"].light_ids == ("entity_fbdf27871edb89bf",)
    assert profiles["storage-line-1"].light_ids == ("entity_0ec37ef18b4b39a6",)
    all_lights = {light for profile in profiles.values() for light in profile.light_ids}
    assert "entity_ff0244d6b760be7e" not in all_lights
    assert "entity_afef5df0e0cae309" not in all_lights
    assert "entity_9bbb3b0e8cd98627" not in all_lights
    assert "entity_c15f5df5382ee180" not in all_lights


def test_coverage_audit_is_unhealthy_for_missing_or_duplicate_profile_ownership() -> None:
    first = _stable_target_id_from_entity("light.first")
    second = _stable_target_id_from_entity("light.second")
    profiles = (ProtectedLightProfile("room", "profile", (first, second)),)
    targets = (
        ScenarioTarget(first, "light.first", "light"),
        ScenarioTarget(second, "light.second", "light"),
    )
    report = audit_system_light_protection_coverage(
        (ProtectedLightProfile("room", "profile", (first,)),),
        targets,
    )

    assert not report.healthy
    assert second in report.unowned_target_ids

    duplicate = audit_system_light_protection_coverage(
        profiles + profiles,
        (ScenarioTarget(first, "light.first", "light"),),
    )
    assert not duplicate.healthy
    assert duplicate.multiply_owned_target_ids == (first,)


def test_coverage_audit_rejects_non_light_switch_roles() -> None:
    report = audit_system_light_protection_coverage(
        SYSTEM_LIGHT_PROFILES,
        (
            ScenarioTarget(
                "entity_71859313239a14e4", "switch.breaker", "breaker"
            ),
            ScenarioTarget(
                "entity_cd0098e5ff95da46", "switch.outlet", "generic outlet"
            ),
        ),
    )

    assert not report.healthy
    assert report.unsafe_target_ids == (
        "entity_71859313239a14e4",
        "entity_cd0098e5ff95da46",
    )


def test_profiles_remain_disabled_and_live_catalog_requires_each_typed_auto_on_target() -> None:
    assert not any(profile.enabled for profile in SYSTEM_LIGHT_PROFILES)
    devices = {
        target.target_id: SimpleNamespace(
            entity_id=target.entity_id or "light.unpinned_catalog_target",
            actions=(SimpleNamespace(action_id="turn_on", domain="light"),),
        )
        for target in FIRST_WAVE_AUTO_ON_TARGETS
    }
    catalog = SimpleNamespace(device=devices.get)
    targets = scenario_targets_for_system_light_profiles(catalog)
    assert len(targets) == len(devices)
    assert {target.role for target in targets} == {"light"}

    devices.pop(next(iter(devices)))
    assert any(target.role == "invalid" for target in scenario_targets_for_system_light_profiles(catalog))
