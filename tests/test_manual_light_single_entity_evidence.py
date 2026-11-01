"""Checks of the registry-backed single-device evidence adapter.

Six positive conditions must ALL hold before an entity is proven to be one
physical device; any missing piece yields no evidence. Groups, wrong
platforms, wrong config entries, wrong unique ids, disabled entries and
registry failures are all negative inputs.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

from custom_components.hausman_hub.adapters.lighting_mqtt_observations import (
    MQTT_ENTRY,
    ROLLOUT_BINDINGS,
)
from custom_components.hausman_hub.adapters.manual_light_single_entity_evidence import (
    proven_single_entity_evidence,
)

_ENTITY = ROLLOUT_BINDINGS[0].entity_id
_UNIQUE_ID = ROLLOUT_BINDINGS[0].unique_id
_ER_MODULE = "homeassistant.helpers.entity_registry"


def _entry(**overrides):
    values = dict(platform="mqtt", config_entry_id=MQTT_ENTRY,
                  unique_id=_UNIQUE_ID, disabled_by=None)
    values.update(overrides)
    return SimpleNamespace(**values)


def _install_registry(monkeypatch, table):
    root = types.ModuleType("homeassistant")
    helpers = types.ModuleType("homeassistant.helpers")
    entity_registry = types.ModuleType(_ER_MODULE)
    entity_registry.async_get = lambda _hass: SimpleNamespace(
        async_get=lambda entity_id: table.get(entity_id)
    )
    helpers.entity_registry = entity_registry
    monkeypatch.setitem(sys.modules, "homeassistant", root)
    monkeypatch.setitem(sys.modules, "homeassistant.helpers", helpers)
    monkeypatch.setitem(sys.modules, _ER_MODULE, entity_registry)


def test_exact_binding_entry_is_proven(monkeypatch):
    _install_registry(monkeypatch, {_ENTITY: _entry()})
    assert proven_single_entity_evidence(object(), (_ENTITY,)) == frozenset({_ENTITY})


def test_missing_entry_gives_no_evidence(monkeypatch):
    _install_registry(monkeypatch, {})
    assert proven_single_entity_evidence(object(), (_ENTITY,)) == frozenset()


@pytest.mark.parametrize("platform", ["group", "template", "light group", "demo"])
def test_non_device_platforms_never_proven(monkeypatch, platform):
    _install_registry(monkeypatch, {_ENTITY: _entry(platform=platform)})
    assert proven_single_entity_evidence(object(), (_ENTITY,)) == frozenset()


def test_wrong_config_entry_never_proven(monkeypatch):
    _install_registry(monkeypatch, {_ENTITY: _entry(config_entry_id="other-entry")})
    assert proven_single_entity_evidence(object(), (_ENTITY,)) == frozenset()


def test_wrong_unique_id_never_proven(monkeypatch):
    _install_registry(monkeypatch, {_ENTITY: _entry(unique_id="0xghost_light_zigbee2mqtt")})
    assert proven_single_entity_evidence(object(), (_ENTITY,)) == frozenset()


def test_disabled_entry_never_proven(monkeypatch):
    _install_registry(monkeypatch, {_ENTITY: _entry(disabled_by="user")})
    assert proven_single_entity_evidence(object(), (_ENTITY,)) == frozenset()


def test_unbound_candidate_never_proven(monkeypatch):
    _install_registry(monkeypatch, {"light.unknown": _entry()})
    assert proven_single_entity_evidence(object(), ("light.unknown",)) == frozenset()


def test_registry_failure_gives_no_evidence(monkeypatch):
    root = types.ModuleType("homeassistant")
    helpers = types.ModuleType("homeassistant.helpers")
    entity_registry = types.ModuleType(_ER_MODULE)

    def broken(_hass):
        raise RuntimeError("registry exploded")

    entity_registry.async_get = broken
    helpers.entity_registry = entity_registry
    monkeypatch.setitem(sys.modules, "homeassistant", root)
    monkeypatch.setitem(sys.modules, "homeassistant.helpers", helpers)
    monkeypatch.setitem(sys.modules, _ER_MODULE, entity_registry)
    assert proven_single_entity_evidence(object(), (_ENTITY,)) == frozenset()


def test_missing_homeassistant_gives_no_evidence():
    # No fake module installed: the real import fails and yields no evidence.
    assert proven_single_entity_evidence(object(), (_ENTITY,)) == frozenset()
