"""Offline end-to-end client adapter tests against the real guarded runtime."""

import importlib.util
import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from tests.test_guarded_lighting_return import (
    ACTOR, _ROOM_ID, case_with_intent, drain_events, new_manual,
)

CLIENT = Path(__file__).parents[1] / "local_tools/guarded_return.py"
if not CLIENT.exists():
    CLIENT = Path(__file__).resolve().parents[3] / "work/guarded-return-client-20261017/guarded_return.py"
spec = importlib.util.spec_from_file_location("guarded_return_client", CLIENT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

CONTRACT = Path(__file__).parents[1] / "contract"
SCHEMAS = {name: json.loads((CONTRACT / f"schemas/v1/{name}.schema.json").read_text())
           for name in ("room-lighting-return-intent", "room-lighting-guarded-return",
                        "room-lighting-guarded-return-request")}
VALIDATORS = {name: Draft202012Validator(schema) for name, schema in SCHEMAS.items()}


def validate(name, payload):
    VALIDATORS[name].validate(payload)


def attempt(room, version, request, **kwargs):
    return module.GuardedReturnAttempt(room, version, request, validate=validate, **kwargs)


@pytest.mark.parametrize("change", [None, "on", "off", "brightness", "color", "timeout", "client-stale"])
async def test_exact_original_intent_is_spent_once_without_fallback(change):
    case = await case_with_intent()
    calls = []

    async def request(method, path, body):
        calls.append((method, path, body))
        if method == "GET":
            assert path.endswith("/force-auto/intent") and body is None
            return {"contract": {"name": "hausman-hub-room-lighting-return-intent", "version": 1},
                    **case.runtime.issue_force_auto_intent(_ROOM_ID, actor_id=ACTOR)}
        assert path.endswith("/force-auto/guarded") and set(body) == {"intentToken"}
        if change == "timeout": raise TimeoutError("fixture uncertain transport")
        if change in {"on", "off", "brightness", "color"}:
            new_manual(case, change)
            await drain_events(case)
        result = await case.runtime.async_force_auto_guarded(
            _ROOM_ID, actor_id=ACTOR, intent_token=body["intentToken"], reason="fixture")
        return {"contract": {"name": "hausman-hub-room-lighting-guarded-return", "version": 1}, **result}

    client = attempt(_ROOM_ID, 1, request, clock=lambda: case.clock[0])
    try:
        await client.prepare()
        with pytest.raises(RuntimeError): await client.prepare()
        if change == "timeout":
            with pytest.raises(TimeoutError): await client.consume(lambda: True)
        elif change == "client-stale":
            with pytest.raises(RuntimeError): await client.consume(lambda: False)
        else:
            result = await client.consume(lambda: True)
            assert result["outcome"] == ("returned" if change is None else "blocked")
            assert (not case.authority.blocks("light.demo_main")) == (change is None)
        with pytest.raises(RuntimeError): await client.consume(lambda: True)
        assert len(calls) == (1 if change == "client-stale" else 2)
        assert not case.executor.calls
    finally:
        await case.runtime.stop()


@pytest.mark.parametrize("bad", ["contract", "room", "version", "token", "ttl", "extra"])
async def test_invalid_issue_response_cannot_be_reissued_or_consumed(bad):
    calls = []

    async def request(*args):
        calls.append(args)
        result = {"contract": {"name": "hausman-hub-room-lighting-return-intent", "version": 1},
                  "room": _ROOM_ID, "configurationVersion": 1, "intentToken": "0" * 64,
                  "expiresInSeconds": 300}
        key, value = {"contract": ("contract", {}), "room": ("room", "other"),
                      "version": ("configurationVersion", True), "token": ("intentToken", None),
                      "ttl": ("expiresInSeconds", 301), "extra": ("retry", True)}[bad]
        result[key] = value
        return result

    client = attempt(_ROOM_ID, 1, request)
    with pytest.raises((ValueError, ValidationError)): await client.prepare()
    with pytest.raises(RuntimeError): await client.prepare()
    with pytest.raises(RuntimeError): await client.consume(lambda: True)
    assert len(calls) == 1


def fixture(name):
    return json.loads((CONTRACT / f"fixtures/v1/{name}.json").read_text())


INTENT = fixture("room-lighting-return-intent")
SUCCESS = fixture("room-lighting-guarded-return-success")
BLOCKED = fixture("room-lighting-guarded-return-blocked")


def malformed_cases(payload, schema_name):
    cases = []
    for key in SCHEMAS[schema_name]["required"]:
        result = copy.deepcopy(payload)
        del result[key]
        cases.append(pytest.param(result, id=f"missing-{key}"))
    for key, value in (("unexpected", True), ("contract", {**payload["contract"], "unexpected": True}),
                       ("contract", {**payload["contract"], "version": True}),
                       ("contract", {**payload["contract"], "version": 2}),
                       ("room", "different_room")):
        result = copy.deepcopy(payload)
        result[key] = value
        cases.append(pytest.param(result, id=f"invalid-{key}-{len(cases)}"))
    return cases


BAD_INTENTS = malformed_cases(INTENT, "room-lighting-return-intent")
for key, value in (("configurationVersion", True), ("configurationVersion", -1),
                   ("configurationVersion", 2), ("expiresInSeconds", True),
                   ("expiresInSeconds", 0), ("expiresInSeconds", 301),
                   ("intentToken", "0" * 63), ("intentToken", "G" * 64),
                   ("intentToken", None)):
    result = copy.deepcopy(INTENT)
    result[key] = value
    BAD_INTENTS.append(pytest.param(result, id=f"invalid-{key}-{len(BAD_INTENTS)}"))

BAD_RECEIPTS = malformed_cases(SUCCESS, "room-lighting-guarded-return")
for change in (
    {"released": True}, {"released": -1}, {"at": True}, {"at": -1},
    {"operationId": True}, {"operationId": 0}, {"known": "true"},
    {"releasedTargets": ["light_main", "light_main"], "released": 2},
    {"outcome": "success"}, {"evaluation": "unknown"},
    {"reason": "x" * 121}, {"releasedTargets": [1]},
    {"blockers": ["unknown_blocker"]}, {"blockers": ["return_intent_changed"]},
    {"evaluation": "not_requested"},
    {"outcome": "blocked"}, {"outcome": "incomplete"},
    {"released": 0}, {"known": False},
):
    result = copy.deepcopy(SUCCESS)
    result.update(change)
    BAD_RECEIPTS.append(pytest.param(result, id=f"invalid-receipt-{len(BAD_RECEIPTS)}"))
BAD_RECEIPTS.append(pytest.param({"contract": {**SUCCESS["contract"], "version": True},
                                "room": INTENT["room"], "outcome": "returned"},
                               id="original-partial-boolean-version-reproduction"))
for outcome in ("blocked", "incomplete"):
    for change in ({"released": 1, "releasedTargets": ["light_main"]},
                   {"evaluation": "requested"}):
        result = copy.deepcopy(BLOCKED)
        result.update(outcome=outcome, **change)
        BAD_RECEIPTS.append(pytest.param(result, id=f"invalid-{outcome}-{len(BAD_RECEIPTS)}"))


@pytest.mark.parametrize("payload", BAD_INTENTS)
async def test_full_intent_validation_is_fail_closed_and_never_reissues(payload):
    calls = []

    async def request(*args):
        calls.append(args)
        return copy.deepcopy(payload)

    client = attempt(INTENT["room"], INTENT["configurationVersion"], request)
    with pytest.raises((ValueError, ValidationError)):
        await client.prepare()
    with pytest.raises(RuntimeError):
        await client.prepare()
    with pytest.raises(RuntimeError):
        await client.consume(lambda: True)
    assert [call[0] for call in calls] == ["GET"]


@pytest.mark.parametrize("payload", BAD_RECEIPTS)
async def test_full_receipt_validation_spends_attempt_without_retry(payload):
    calls = []

    async def request(method, path, body):
        calls.append((method, path, body))
        return copy.deepcopy(INTENT if method == "GET" else payload)

    client = attempt(INTENT["room"], INTENT["configurationVersion"], request)
    await client.prepare()
    with pytest.raises((ValueError, ValidationError)):
        await client.consume(lambda: True)
    with pytest.raises(RuntimeError):
        await client.prepare()
    with pytest.raises(RuntimeError):
        await client.consume(lambda: True)
    assert [call[0] for call in calls] == ["GET", "POST"]
    assert calls[1][1].endswith("/force-auto/guarded")
    VALIDATORS["room-lighting-guarded-return-request"].validate(calls[1][2])


@pytest.mark.parametrize("variant", ["success", "blocked", "incomplete", "zero-release", "no-operation"])
async def test_canonical_receipts_are_accepted_without_inventing_requirements(variant):
    payload = copy.deepcopy(SUCCESS if variant in {"success", "zero-release", "no-operation"} else BLOCKED)
    if variant == "incomplete":
        payload["outcome"] = "incomplete"
    if variant == "zero-release":
        payload.update(released=0, releasedTargets=[])
    if variant == "no-operation":
        payload.pop("operationId", None)
    calls = []

    async def request(method, path, body):
        calls.append((method, path, body))
        return copy.deepcopy(INTENT if method == "GET" else payload)

    client = attempt(INTENT["room"], INTENT["configurationVersion"], request)
    await client.prepare()
    assert await client.consume(lambda: True) == payload
    assert len(calls) == 2


def test_schema_validator_is_required():
    with pytest.raises(TypeError):
        module.GuardedReturnAttempt("room_demo", 1, None)
    with pytest.raises(TypeError):
        module.GuardedReturnAttempt("room_demo", 1, None, validate=None)


@pytest.mark.parametrize("phase", ["intent", "receipt"])
async def test_boolean_contract_version_is_rejected_even_by_noop_validator(phase):
    async def request(method, path, body):
        payload = copy.deepcopy(INTENT if method == "GET" else SUCCESS)
        if (method == "GET") == (phase == "intent"):
            payload["contract"]["version"] = True
        return payload

    client = module.GuardedReturnAttempt(INTENT["room"], 1, request, validate=lambda *args: None)
    if phase == "intent":
        with pytest.raises(ValueError):
            await client.prepare()
    else:
        await client.prepare()
        with pytest.raises(ValueError):
            await client.consume(lambda: True)


async def test_validator_returning_false_is_not_silently_accepted():
    async def request(*args):
        return copy.deepcopy(INTENT)

    client = module.GuardedReturnAttempt(INTENT["room"], 1, request, validate=lambda *args: False)
    with pytest.raises(ValueError):
        await client.prepare()
