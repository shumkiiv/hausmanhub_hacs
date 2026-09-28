"""Compatibility gates of the common manual intake (S-06.a, HH-014.b).

Old request/receipt fixtures must stay valid against the vendored 0.72.1
schemas, the new core-manual fixtures must validate against them, and
the envelope → coreOperation echo pairing must hold. Nothing here
executes UI: static client checks are recorded as analysis in the
delivery card, per the assignment.
"""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, RefResolver
import pytest

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "custom_components" / "hausman_hub" / "contracts" / "v1"
OLD_BATCH_FIXTURES = ROOT / "fixtures" / "hausmanhub_device_action_batch_v1"
STAND_FIXTURES = ROOT / "fixtures" / "core_manual_input"


def _validator(schema_name: str, definition: str | None = None):
    schema = json.loads((CONTRACTS / schema_name).read_text(encoding="utf-8"))
    store = {}
    for schema_path in CONTRACTS.rglob("*.json"):
        candidate = json.loads(schema_path.read_text(encoding="utf-8"))
        if isinstance(candidate, dict) and isinstance(candidate.get("$id"), str):
            store[candidate["$id"]] = candidate
    return Draft202012Validator(
        schema["$defs"][definition] if definition is not None else schema,
        resolver=RefResolver.from_schema(schema, store=store),
    )


def test_old_batch_fixtures_stay_valid_against_updated_schemas():
    """The 0.72.0 batch fixtures remain valid under 0.72.1: the new
    envelope is optional and nothing existing became required."""

    request = json.loads(
        (OLD_BATCH_FIXTURES / "request.json").read_text(encoding="utf-8")
    )
    receipt = json.loads(
        (OLD_BATCH_FIXTURES / "receipt.json").read_text(encoding="utf-8")
    )
    _validator("device-action-batch-request.schema.json").validate(request)
    _validator("device-action-batch-receipt.schema.json").validate(receipt)


def test_legacy_single_request_without_envelope_stays_valid():
    """A plain old single request (no coreControl) validates against the
    updated request schema."""

    request = {
        "contract": {"name": "hausman-hub-device-action-request", "version": 1},
        "correlationId": "corr.old.client.1",
        "targetId": "light.kitchen",
        "actionId": "turn_on",
    }
    _validator("device-action-request.schema.json").validate(request)


def test_new_core_manual_fixtures_validate_against_schemas():
    single_request = json.loads(
        (CONTRACTS / "fixtures" / "core-manual-single-request.json").read_text(
            encoding="utf-8"
        )
    )
    single_receipt = json.loads(
        (CONTRACTS / "fixtures" / "core-manual-single-receipt.json").read_text(
            encoding="utf-8"
        )
    )
    batch_request = json.loads(
        (CONTRACTS / "fixtures" / "core-manual-batch-request.json").read_text(
            encoding="utf-8"
        )
    )
    batch_receipt = json.loads(
        (CONTRACTS / "fixtures" / "core-manual-batch-receipt.json").read_text(
            encoding="utf-8"
        )
    )
    # Requests carrying the envelope are the explicitly selected full
    # form (0.72.1); receipts are validated against the full variants.
    _validator("device-action-request.schema.json", "full").validate(
        single_request
    )
    _validator("device-action-receipt.schema.json", "full").validate(
        single_receipt
    )
    _validator("device-action-batch-request.schema.json", "full").validate(
        batch_request
    )
    _validator(
        "device-action-batch-receipt.schema.json",
        "full",
    ).validate(batch_receipt)


def test_envelope_pairs_with_core_operation_echo():
    """Pairing rule of the 0.72.1 validator: the receipt echo carries a
    core-issued operation id that is never the legacy requestId, plus
    the accepted revisions."""

    single_request = json.loads(
        (CONTRACTS / "fixtures" / "core-manual-single-request.json").read_text(
            encoding="utf-8"
        )
    )
    single_receipt = json.loads(
        (CONTRACTS / "fixtures" / "core-manual-single-receipt.json").read_text(
            encoding="utf-8"
        )
    )
    envelope = single_request["coreControl"]
    echo = single_receipt["coreOperation"]
    assert echo["version"] == 1
    assert echo["operationId"] != envelope["requestId"]
    assert echo["controlRevision"] >= envelope["expectedControlRevision"]
    assert single_receipt["requestId"] in (envelope["requestId"],)


def test_stand_fixture_declares_the_transfer_boundaries():
    """The stand passport keeps the delivery boundaries explicit: the
    synthetic resource, its binary property, and the closed send."""

    stand = json.loads(
        (STAND_FIXTURES / "stand.json").read_text(encoding="utf-8")
    )
    resource = stand["resource"]
    assert resource["synthetic"] is True
    assert resource["property"] == "on"
    assert resource["allowedActions"] == ["turn_on", "turn_off"]
    assert stand["boundaries"]["physicalSend"] == "closed"
    assert stand["neighbour"]["transferred"] is False


def test_anonymized_admitted_receipt_matches_the_contract():
    """The anonymized admitted receipt of the integration stand is a
    valid full receipt and echoes a core operation."""

    receipt = json.loads(
        (STAND_FIXTURES / "single-admitted-receipt.json").read_text(
            encoding="utf-8"
        )
    )
    _validator("device-action-receipt.schema.json", "full").validate(receipt)
    assert receipt["coreOperation"]["operationId"] != receipt["requestId"]
    assert receipt["commandSent"] is False
    assert receipt["reasonCode"] == "core_manual_admitted"


def test_mixed_batch_fixture_is_schema_valid():
    """The anonymized mixed batch (transferred participant + neighbour)
    validates against the updated batch request schema."""

    batch = json.loads(
        (STAND_FIXTURES / "batch-mixed.json").read_text(encoding="utf-8")
    )
    _validator("device-action-batch-request.schema.json", "full").validate(
        batch
    )
    transferred = [
        item for item in batch["actions"] if "coreControl" in item
    ]
    assert len(transferred) == 1


@pytest.mark.parametrize(
    "schema_name",
    [
        "device-action-request.schema.json",
        "device-action-batch-request.schema.json",
        "device-action-receipt.schema.json",
        "core-manual-envelope.schema.json",
    ],
)
def test_vendored_schemas_carry_the_core_manual_surface(schema_name):
    """The vendored 0.72.1 copies expose the reviewed envelope surface."""

    schema = json.loads((CONTRACTS / schema_name).read_text(encoding="utf-8"))
    text = json.dumps(schema)
    if "request" in schema_name or schema_name.startswith("core-manual"):
        assert "coreControl" in text or "coreManualEnvelope" in text
    if "receipt" in schema_name:
        assert "coreOperation" in text
        assert "core_manual_admitted" in text
