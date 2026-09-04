"""Manual light-off protection API boundary tests."""

import ast
from pathlib import Path

from custom_components.hausman_hub.application.api_capabilities import (
    MANUAL_LIGHT_OFF_PROTECTION_PATH,
    api_capabilities_snapshot,
)
from custom_components.hausman_hub.application.manual_light_off_protection import (
    ManualLightOffProtectionCoordinator,
)
import asyncio


class _Store:
    async def async_load(self):
        return None

    async def async_save(self, payload):
        return None


def test_manual_protection_views_leave_options_to_home_assistant_cors() -> None:
    source = Path(
        "custom_components/hausman_hub/manual_light_off_protection_api.py"
    ).read_text(encoding="utf-8")
    module = ast.parse(source)
    view_classes = {
        node.name: node
        for node in module.body
        if isinstance(node, ast.ClassDef)
        and node.name in {
            "ManualLightOffProtectionView",
            "ManualLightOffProtectionReleaseView",
        }
    }

    assert set(view_classes) == {
        "ManualLightOffProtectionView",
        "ManualLightOffProtectionReleaseView",
    }
    for view_class in view_classes.values():
        assert not any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "options"
            for node in view_class.body
        )


def test_capabilities_publish_the_exact_manual_protection_contract() -> None:
    capability = api_capabilities_snapshot()["capabilities"]["manual_light_off_protection"]

    assert capability["available"] is True
    assert capability["settings_path"] == MANUAL_LIGHT_OFF_PROTECTION_PATH
    assert capability["settings_methods"] == ["GET", "PUT"]
    assert capability["release_path"] == f"{MANUAL_LIGHT_OFF_PROTECTION_PATH}/release"
    assert capability["release_method"] == "POST"


def test_release_by_contract_profile_identity_never_needs_a_private_storage_key() -> None:
    async def exercise() -> None:
        coordinator = ManualLightOffProtectionCoordinator(_Store())
        await coordinator.async_load()
        await coordinator.async_replace_settings("settings.1", 0, {
            "globalPolicy": {"enabled": True, "minimumIntervalSeconds": 30, "releaseMode": "timer_only", "stableAbsenceSeconds": 5, "extendOnRepeatedManualOff": True, "noSensorFallback": "timer_only", "protectedScope": "profile", "allowManualRelease": True},
            "roomOverrides": {}, "profileOverrides": {},
            "profiles": [{"roomId": "tambur", "profileId": "tambur-points", "lightIds": ["light.points"], "presenceSensorIds": []}],
        })
        from types import SimpleNamespace
        await coordinator.async_note_state_transition("light.points", SimpleNamespace(state="on"), SimpleNamespace(state="off"), None)
        receipt = await coordinator.async_release_profile(
            "release.1", "tambur", "tambur-points", 0
        )
        assert receipt["operation"] == "manual_release"
    asyncio.run(exercise())
