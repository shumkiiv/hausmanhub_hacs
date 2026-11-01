"""Narrow test boundary of the Home Assistant target-resolution interface.

Provides exactly the surface used by
``custom_components.hausman_hub.manual_light_authority_events``:
``homeassistant.helpers.target.TargetSelection`` and the synchronous
``async_extract_referenced_entity_ids``. The double NEVER expands groups: a
group id is returned as referenced and the production
``_expand_group_members`` must read its composition from ``hass.states``.

Injection is done through ``monkeypatch.setitem(sys.modules, ...)`` only, so
it is fully reverted after every test and no ``homeassistant`` package is
ever created on disk.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

TARGET_MODULE = "homeassistant.helpers.target"
HELPERS_MODULE = "homeassistant.helpers"
ROOT_MODULE = "homeassistant"


def install_target_boundary(
    monkeypatch,
    *,
    entities: tuple[str, ...] = (),
    devices: dict[str, tuple[str, ...]] | None = None,
    areas: dict[str, tuple[str, ...]] | None = None,
) -> list[dict]:
    """Provide the fake ``homeassistant.helpers.target`` module.

    ``entities``: direct entity ids that resolve to themselves.
    ``devices``: mapping device_id -> entity ids it resolves to.
    ``areas``: mapping area_id -> entity ids it resolves to.
    Unknown selectors surface through the ``missing_*`` fields, which makes
    the production ``_resolve_ha_selector`` return None (fail closed).
    Returns the recorded extraction calls for assertions.
    """
    direct = frozenset(entities)
    device_map = {key: tuple(value) for key, value in (devices or {}).items()}
    area_map = {key: tuple(value) for key, value in (areas or {}).items()}
    calls: list[dict] = []

    class _TargetSelection:
        def __init__(self, mapping):
            self.mapping = dict(mapping)

    def _extract(_hass, selection):
        referenced: set[str] = set()
        indirectly: set[str] = set()
        missing_devices: set[str] = set()
        missing_areas: set[str] = set()
        missing_floors: set[str] = set()
        missing_labels: set[str] = set()
        for key, values in selection.mapping.items():
            for value in values:
                if key == "entity_id":
                    if value in direct:
                        referenced.add(value)
                    else:
                        missing_devices.add(value)
                elif key == "device_id":
                    found = device_map.get(value)
                    if found is None:
                        missing_devices.add(value)
                    else:
                        indirectly.update(found)
                elif key == "area_id":
                    found = area_map.get(value)
                    if found is None:
                        missing_areas.add(value)
                    else:
                        indirectly.update(found)
                elif key == "floor_id":
                    missing_floors.add(value)
                else:
                    missing_labels.add(value)
        calls.append({"selection": dict(selection.mapping)})
        return SimpleNamespace(
            referenced=frozenset(referenced),
            indirectly_referenced=frozenset(indirectly),
            missing_devices=frozenset(missing_devices),
            missing_areas=frozenset(missing_areas),
            missing_floors=frozenset(missing_floors),
            missing_labels=frozenset(missing_labels),
        )

    root = types.ModuleType(ROOT_MODULE)
    helpers = types.ModuleType(HELPERS_MODULE)
    target = types.ModuleType(TARGET_MODULE)
    target.TargetSelection = _TargetSelection
    target.async_extract_referenced_entity_ids = _extract
    helpers.target = target
    monkeypatch.setitem(sys.modules, ROOT_MODULE, root)
    monkeypatch.setitem(sys.modules, HELPERS_MODULE, helpers)
    monkeypatch.setitem(sys.modules, TARGET_MODULE, target)
    return calls


def states_table(table: dict[str, str | None]):
    """Build a ``hass.states`` double from an explicit table.

    ``{entity_id: "on"|"off"}`` entries become simple states with empty
    attributes; missing or None ids return None. There is deliberately no
    universal "any id exists" fallback: negative fixtures keep returning None.
    """

    def get(entity_id):
        value = table.get(entity_id) if isinstance(table, dict) else None
        if value is None:
            return None
        return SimpleNamespace(state=value, attributes={})

    return SimpleNamespace(get=get)
