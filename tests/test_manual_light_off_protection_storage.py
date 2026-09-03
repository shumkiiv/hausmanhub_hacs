"""Persistence bounds and corruption handling for manual-off protection."""

import copy
import hashlib
import json

from custom_components.hausman_hub.application.manual_light_off_protection import (
    valid_manual_light_off_protection_payload,
)
from custom_components.hausman_hub.domain.manual_light_off_protection import (
    resolve_manual_off_policy,
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
        "protections": [], "completed": [], "receipts": [], "frozenSensors": [],
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


def test_payload_accepts_exact_capacity_and_rejects_overflow_and_strict_receipt_corruption() -> None:
    payload = _payload()
    profile = {
        "roomId": "room", "profileId": "profile", "lightIds": ["light.room"],
        "presenceSensorIds": [],
    }
    payload["settings"]["profiles"] = [
        {**profile, "roomId": f"room_{index}", "profileId": f"profile_{index}"}
        for index in range(64)
    ]
    protection = _protection(payload)
    payload["protections"] = [
        {**protection, "roomId": f"active_{index}", "profileId": f"profile_{index}"}
        for index in range(64)
    ]
    payload["frozenSensors"] = [
        {"protectionId": _protection_id(f"active_{index}", f"profile_{index}"), "presenceSensorIds": []}
        for index in range(64)
    ]
    payload["completed"] = [
        {**protection, "roomId": "complete", "profileId": "profile", "revision": index,
         "state": "released"}
        for index in range(256)
    ]
    payload["receipts"] = [_settings_receipt(f"receipt.{index}", index) for index in range(128)]
    assert valid_manual_light_off_protection_payload(payload)
    for field in ("protections", "completed", "receipts"):
        overflow = copy.deepcopy(payload)
        overflow[field].append(copy.deepcopy(overflow[field][-1]))
        assert not valid_manual_light_off_protection_payload(overflow)
    profiles_overflow = copy.deepcopy(payload)
    profiles_overflow["settings"]["profiles"].append(copy.deepcopy(profile))
    assert not valid_manual_light_off_protection_payload(profiles_overflow)
    for mutation in (
        {"receipt": {"requestId": "receipt.0"}},
        {"receipt": {"contract": {"name": "wrong", "version": 1}}},
        {"receipt": {"revision": -1}},
        {"receipt": {"operation": "settings_updated"}},
        {"receipt": {"operation": "manual_release"}},
    ):
        corrupt = copy.deepcopy(payload)
        corrupt["receipts"][0].update(mutation)
        assert not valid_manual_light_off_protection_payload(corrupt)


def _payload() -> dict[str, object]:
    return {
        "version": 1, "settingsRevision": 0, "stateRevision": 0,
        "settings": {
            "globalPolicy": {
                "enabled": True, "minimumIntervalSeconds": 600,
                "releaseMode": "timer_and_absence", "stableAbsenceSeconds": 30,
                "extendOnRepeatedManualOff": True, "noSensorFallback": "timer_only",
                "protectedScope": "profile", "allowManualRelease": True,
            }, "roomOverrides": {}, "profileOverrides": {}, "profiles": [],
        }, "protections": [], "completed": [], "receipts": [], "frozenSensors": [],
    }


def _protection(payload: dict[str, object]) -> dict[str, object]:
    return {
        "roomId": "room", "profileId": "profile", "lightIds": ["light.room"],
        "startedAt": "2026-09-03T12:00:00Z", "notBefore": "2026-09-03T12:10:00Z",
        "absenceSince": None, "effectivePolicy": payload["settings"]["globalPolicy"],
        "effectivePolicySource": "global", "policyFingerprint": resolve_manual_off_policy(
            payload["settings"], "room", "profile"
        ).fingerprint,
        "reason": "manual_off", "attributionSource": "state_transition",
        "attributionId": "light.room", "revision": 0, "state": "active",
    }


def _settings_receipt(request_id: str, revision: int) -> dict[str, object]:
    return {"requestId": request_id, "receipt": {
        "contract": {"name": "hausman-hub-manual-light-off-protection-command-receipt", "version": 1},
        "requestId": request_id, "operation": "settings_updated", "accepted": True,
        "confirmed": True, "status": "confirmed", "revision": revision,
        "settings": _payload()["settings"],
    }}


def _protection_id(room_id: str, profile_id: str) -> str:
    encoded = json.dumps([room_id, profile_id, None], separators=(",", ":")).encode()
    return f"p_{hashlib.sha256(encoded).hexdigest()}"
