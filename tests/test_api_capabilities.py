"""Regression checks for optional public API capabilities."""

from custom_components.hausman_hub.application.api_capabilities import (
    MANUAL_LIGHT_OFF_PROTECTION_PATH,
    api_capabilities_snapshot,
)


def test_manual_light_off_protection_capability_is_advertised() -> None:
    capability = api_capabilities_snapshot()["capabilities"]["manual_light_off_protection"]

    assert capability["available"] is True
    assert capability["settings_path"] == MANUAL_LIGHT_OFF_PROTECTION_PATH
    assert capability["release_path"] == f"{MANUAL_LIGHT_OFF_PROTECTION_PATH}/release"
