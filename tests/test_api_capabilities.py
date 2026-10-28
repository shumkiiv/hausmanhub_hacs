"""Regression checks for optional public API capabilities."""

from custom_components.hausman_hub.application.api_capabilities import (
    MANUAL_LIGHT_OFF_PROTECTION_PATH,
    api_capabilities_snapshot,
)

import json
import ast
from pathlib import Path
from types import SimpleNamespace
import pytest
from jsonschema import Draft202012Validator


@pytest.mark.asyncio
async def test_actual_http_handler_requires_all_running_return_dependencies():
    """Only handler readiness, not startup, route registration or authentication."""
    root = Path(__file__).resolve().parents[1] / "custom_components/hausman_hub"
    tree = ast.parse((root / "climate_api.py").read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ClimateCapabilitiesView")
    handler = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "get")
    environment = {
        "Any": object, "DOMAIN": "hausman_hub", "CAPABILITIES_PATH": "/api/hausman_hub/v1/capabilities",
        "NO_STORE_HEADERS": {"Cache-Control": "no-store"},
        "_is_exact_request": lambda *_: True,
        "_is_local_climate_control_request": lambda *_: True,
        "api_capabilities_snapshot": api_capabilities_snapshot,
    }
    exec(compile(ast.Module(body=[handler], type_ignores=[]), "climate_api.py", "exec"), environment)
    schema = json.loads((root / "contracts/v1/api-capabilities.schema.json").read_text())
    ready = {
        "away_runtime": SimpleNamespace(return_policy_available=True),
        "room_lighting_service": SimpleNamespace(async_policy_snapshot=lambda: None),
        "room_lighting_runtime": SimpleNamespace(running=True),
    }
    data = {}
    view = SimpleNamespace(
        _runtime=lambda: object(), _climate_tablet=lambda: None,
        _hass=SimpleNamespace(data={"hausman_hub": data}),
        json=lambda payload, **_: payload,
    )
    cases = [({}, True)]
    cases += [({key: None}, False) for key in ready]
    cases += [
        ({"away_runtime": SimpleNamespace(return_policy_available=False)}, False),
        ({"room_lighting_service": SimpleNamespace(async_policy_snapshot=None)}, False),
        ({"room_lighting_runtime": SimpleNamespace(running=False)}, False),
    ]
    for changed, expected in cases:
        data.clear()
        data.update(ready)
        data.update(changed)
        payload = await environment["get"](view, object())
        assert payload["capabilities"]["room_lighting_return_policy"]["available"] is expected
        Draft202012Validator(schema).validate(payload)


def test_return_policy_discovery_requires_explicit_runtime_readiness():
    for available in (False, True):
        payload = api_capabilities_snapshot(
            room_lighting_return_policy_available=available,
        )
        capability = payload["capabilities"]["room_lighting_return_policy"]
        assert capability["available"] is available
        assert capability["initial_actions"] == ["evaluate_profile", "auto_only"]
        assert capability["policy_version"] == 1
        assert capability["general_return_precedence"] == "room_policy"
        schema = json.loads((Path(__file__).resolve().parents[1] /
            "custom_components/hausman_hub/contracts/v1/api-capabilities.schema.json").read_text())
        Draft202012Validator(schema).validate(payload)
    assert api_capabilities_snapshot()["capabilities"]["room_lighting_return_policy"]["available"] is False


def test_manual_light_off_protection_capability_is_advertised() -> None:
    capability = api_capabilities_snapshot()["capabilities"]["manual_light_off_protection"]

    assert capability["available"] is True
    assert capability["settings_path"] == MANUAL_LIGHT_OFF_PROTECTION_PATH
    assert capability["release_path"] == f"{MANUAL_LIGHT_OFF_PROTECTION_PATH}/release"
