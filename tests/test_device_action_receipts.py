"""Runtime full receipts remain valid against the canonical 0.63 schema."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from jsonschema import Draft202012Validator, RefResolver
import json

from custom_components.hausman_hub.application.device_action_receipts import (
    evidence_snapshot,
    full_action_receipt,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "custom_components" / "hausman_hub" / "contracts"


def _validator(name: str, definition: str | None = None) -> Draft202012Validator:
    schema = json.loads((SCHEMAS / name).read_text(encoding="utf-8"))
    store = {}
    for schema_path in SCHEMAS.rglob("*.json"):
        candidate = json.loads(schema_path.read_text(encoding="utf-8"))
        if isinstance(candidate, dict) and isinstance(candidate.get("$id"), str):
            store[candidate["$id"]] = candidate
    return Draft202012Validator(
        schema["$defs"][definition] if definition is not None else schema,
        resolver=RefResolver.from_schema(schema, store=store),
    )


def test_confirmed_manual_light_full_receipt_matches_contract() -> None:
    before = SimpleNamespace(
        state="off",
        attributes={},
        last_updated=datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc),
    )
    after = SimpleNamespace(
        state="on",
        attributes={},
        last_updated=datetime.now(timezone.utc),
    )
    actions = ("turn_on", "turn_off", "set_brightness")
    pre = evidence_snapshot(
        target_id="light_demo",
        state=before,
        allowed_actions=actions,
    )
    receipt = full_action_receipt(
        payload={
            "contract": {
                "name": "hausman-hub-device-action-request",
                "version": 1,
            },
            "correlationId": "manual.light.1",
            "targetId": "light_demo",
            "actionId": "turn_on",
        },
        result={
            "correlationId": "manual.light.1",
            "requestId": "manual.light.request.1",
            "accepted": True,
            "confirmed": True,
            "status": "confirmed",
            "statusName": "Выполнено",
            "targetId": "light_demo",
            "actionId": "turn_on",
            "appliedAt": 1788000000000,
            "message": "Свет включён.",
            "confirmationWindowMs": 8000,
            "readBack": {
                "attempted": True,
                "matched": True,
                "observedAt": 1788000000100,
                "observedState": "on",
                "attempts": 1,
                "isNewEvidence": True,
            },
            "reason": None,
        },
        target_type="light",
        state=after,
        allowed_actions=actions,
        pre_command_evidence=pre,
        decision_at=1788000000000,
    )
    _validator("v1/device-action-receipt.schema.json", "full").validate(receipt)


def test_stale_on_evidence_exposes_one_light_reassert_budget() -> None:
    evidence = evidence_snapshot(
        target_id="light_demo",
        state=SimpleNamespace(
            state="on",
            attributes={},
            last_updated=datetime.now(timezone.utc) - timedelta(minutes=10),
        ),
        allowed_actions=("turn_on", "turn_off", "set_brightness"),
    )

    assert evidence["reassertPolicy"] == "light_turn_on"
    assert evidence["reassertBudget"] == 1
    assert evidence["allowedActions"] == ["turn_on"]
    _validator("v1/device-state-evidence.schema.json").validate(evidence)


def test_confirmed_automatic_reassert_evidence_matches_contract() -> None:
    evidence = evidence_snapshot(
        target_id="light_demo",
        state=SimpleNamespace(
            state="on",
            attributes={},
            last_updated=datetime.now(timezone.utc),
        ),
        allowed_actions=("turn_on", "turn_off", "set_brightness"),
        reason_code="reasserted_light_on",
        ownership="automation",
        request_id="reassert.request.1",
        action_id="turn_on",
        command_sent_at=1788000000000,
        confirmed=True,
    )

    assert evidence["confidence"] == "confirmed"
    assert evidence["ownership"] == "automation"
    assert evidence["ownershipSource"] == "automation_command"
    assert evidence["ownershipEvidence"]["requestId"] == "reassert.request.1"
    _validator("v1/device-state-evidence.schema.json").validate(evidence)


def test_skipped_effectively_off_receipt_never_claims_command_sent() -> None:
    state = SimpleNamespace(
        state="on",
        attributes={},
        last_changed=datetime.now(timezone.utc),
    )
    actions = ("turn_on", "turn_off")
    pre = evidence_snapshot(
        target_id="light_demo", state=state, allowed_actions=actions
    )
    receipt = full_action_receipt(
        payload={
            "targetId": "light_demo",
            "actionId": "turn_off",
            "correlationId": "effective.off.1",
        },
        result={
            "correlationId": "effective.off.1",
            "requestId": "effective.off.request.1",
            "accepted": True,
            "confirmed": True,
            "skipped": True,
            "reason": "already_effectively_off",
            "effectiveState": "off",
            "readBack": {
                "attempted": False,
                "matched": True,
                "observedState": "off",
                "attempts": 0,
            },
        },
        target_type="light",
        state=state,
        allowed_actions=actions,
        pre_command_evidence=pre,
        decision_at=1788000000000,
    )

    assert receipt["decision"] == "skipped"
    assert receipt["commandSent"] is False
    assert "commandSentAt" not in receipt
    _validator("v1/device-action-receipt.schema.json", "full").validate(receipt)
