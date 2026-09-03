"""Persistence bounds and corruption handling for manual-off protection."""

from custom_components.hausman_hub.application.manual_light_off_protection import (
    valid_manual_light_off_protection_payload,
)


def test_store_payload_rejects_corruption_and_never_evicts_active_protection() -> None:
    assert not valid_manual_light_off_protection_payload({"version": 1})
    payload = {
        "version": 1,
        "settingsRevision": 0,
        "stateRevision": 0,
        "settings": {
            "globalPolicy": {
                "enabled": True, "minimumIntervalSeconds": 600,
                "releaseMode": "timer_and_absence", "stableAbsenceSeconds": 30,
                "extendOnRepeatedManualOff": True, "noSensorFallback": "timer_only",
                "protectedScope": "profile", "allowManualRelease": True,
            },
            "roomOverrides": {}, "profileOverrides": {}, "profiles": [],
        },
        "protections": [], "completed": [], "receipts": [],
    }
    assert valid_manual_light_off_protection_payload(payload)

    protection = {
        "roomId": "tambur", "profileId": "tambur_points",
        "lightIds": ["light.tambur_points"],
        "startedAt": "2026-09-03T12:00:00Z",
        "notBefore": "2026-09-03T12:10:00Z", "absenceSince": None,
        "effectivePolicy": payload["settings"]["globalPolicy"],
        "effectivePolicySource": "global", "policyFingerprint": "g" * 64,
        "reason": "manual_off", "attributionSource": "state_transition",
        "attributionId": "light.tambur_points", "revision": 0, "state": "active",
    }
    invalid_protection = {**payload, "protections": [protection]}
    assert not valid_manual_light_off_protection_payload(invalid_protection)
    protection["policyFingerprint"] = "0" * 64
    assert not valid_manual_light_off_protection_payload(
        {**payload, "protections": [protection]}
    )
    invalid_receipt = {**payload, "receipts": [{"requestId": "bad", "receipt": {}}]}
    assert not valid_manual_light_off_protection_payload(invalid_receipt)
