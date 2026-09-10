from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import types

import pytest

from custom_components.hausman_hub.application.smart_switch_bindings import (
    HomeAssistantSmartSwitchBindingsStore,
    bindings_from_payload,
    resolve_trigger_bindings,
)


def test_resolver_builds_only_fixed_triggers_for_synthetic_bindings() -> None:
    bindings = bindings_from_payload(
        {
            "version": 1,
            "revision": 1,
            "devices": {
                "shower": "device-shower",
                "passthrough": "device-pass",
                "marmitek": "device-mirror",
            },
        }
    )

    assert bindings is not None

    resolved = resolve_trigger_bindings(
        bindings,
        frozenset(
            {
                "tambur-light-group",
                "tambur-mirror-left",
                "tambur-master-off",
            }
        ),
    )

    assert {item.binding for item in resolved} == {
        "tambur-light-group",
        "tambur-mirror-left",
        "tambur-master-off",
    }
    assert all(
        item.config["domain"] == "mqtt" and item.config["type"] == "action"
        for item in resolved
    )


def test_invalid_or_reused_profile_ids_do_not_resolve() -> None:
    assert (
        bindings_from_payload(
            {
                "version": 1,
                "revision": 1,
                "devices": {"passthrough": "same", "marmitek": "same"},
            }
        )
        is None
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"version": 2, "revision": 1, "devices": {}},
        {"version": 1, "revision": 0, "devices": {}},
        {"version": 1, "revision": True, "devices": {}},
        {"version": 1, "revision": 1, "devices": {"unknown": "device"}},
        {"version": 1, "revision": 1, "devices": {"shower": ""}},
        {"version": 1, "revision": 1, "devices": {}, "extra": "value"},
    ],
)
def test_invalid_binding_payloads_fail_closed(payload: object) -> None:
    assert bindings_from_payload(payload) is None


@pytest.mark.asyncio
async def test_recovered_binding_store_is_not_trusted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    storage = types.ModuleType("homeassistant.helpers.storage")

    class FakeStore:
        def __init__(
            self, _hass: object, version: int, key: str, **_kwargs: object
        ) -> None:
            self.version = version
            self.key = key
            self.path = str(tmp_path / key)

        async def async_load(self) -> object | None:
            return None

        async def async_save(self, payload: dict[str, object]) -> None:
            Path(self.path).write_text(
                json.dumps(
                    {"version": self.version, "key": self.key, "data": payload}
                ),
                encoding="utf-8",
            )

    storage.Store = FakeStore  # type: ignore[attr-defined]
    helpers = types.ModuleType("homeassistant.helpers")
    helpers.__path__ = []  # type: ignore[attr-defined]
    helpers.storage = storage  # type: ignore[attr-defined]
    homeassistant = types.ModuleType("homeassistant")
    homeassistant.__path__ = []  # type: ignore[attr-defined]
    homeassistant.helpers = helpers  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "homeassistant", homeassistant)
    monkeypatch.setitem(sys.modules, "homeassistant.helpers", helpers)
    monkeypatch.setitem(sys.modules, "homeassistant.helpers.storage", storage)

    class FakeHass:
        async def async_add_executor_job(self, func: object, *args: object) -> object:
            return func(*args)  # type: ignore[operator]

    payload = {
        "version": 1,
        "revision": 1,
        "devices": {"passthrough": "device-pass"},
    }
    key = "hausman_hub.smart_switch_bindings.entry-a"
    path = tmp_path / key
    path.write_text("{broken", encoding="utf-8")
    Path(f"{path}.previous").write_text(
        json.dumps({"version": 1, "key": key, "data": payload}), encoding="utf-8"
    )

    store = HomeAssistantSmartSwitchBindingsStore(FakeHass(), "entry-a")

    assert await store.async_load() == payload
    assert store.recovered_previous is True
