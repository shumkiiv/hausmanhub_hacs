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
from custom_components.hausman_hub import _async_resolve_tambur_smart_switch_triggers


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


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_profile", ("shower", "passthrough", "marmitek"))
async def test_startup_rejects_each_incomplete_profile_set(
    missing_profile: str,
) -> None:
    """A saved partial draft remains inert at the Tambur startup boundary."""

    devices = {
        "shower": "test-shower-device",
        "passthrough": "test-passthrough-device",
        "marmitek": "test-marmitek-device",
    }
    del devices[missing_profile]

    class Store:
        recovered_previous = False
        loads = 0

        async def async_load(self) -> object:
            self.loads += 1
            return {
                "version": 1,
                "revision": 1,
                "devices": devices,
            }

    store = Store()

    assert await _async_resolve_tambur_smart_switch_triggers(
        object(), "entry-a", store=store
    ) is None
    assert store.loads == 1


@pytest.mark.asyncio
async def test_startup_constructs_tambur_triggers_from_only_local_synthetic_ids() -> None:
    """The public startup boundary exposes only IDs supplied by local bindings."""

    payload = {
        "version": 1,
        "revision": 1,
        "devices": {
            "shower": "synthetic-shower-device",
            "passthrough": "synthetic-passthrough-device",
            "marmitek": "synthetic-mirror-device",
        },
    }

    class Store:
        recovered_previous = False

        async def async_load(self) -> object:
            return payload

    context = await _async_resolve_tambur_smart_switch_triggers(
        object(), "entry-a", store=Store()
    )

    assert context is not None
    bindings, resolved = context
    assert bindings.devices["marmitek"] == "synthetic-mirror-device"
    assert tuple((item.binding, dict(item.config)) for item in resolved) == (
        (
            "tambur-light-group",
            {
                "platform": "device",
                "domain": "mqtt",
                "type": "action",
                "device_id": "synthetic-passthrough-device",
                "subtype": "on_down",
            },
        ),
        (
            "tambur-light-group",
            {
                "platform": "device",
                "domain": "mqtt",
                "type": "action",
                "device_id": "synthetic-passthrough-device",
                "subtype": "toggle_down",
            },
        ),
        (
            "tambur-light-group",
            {
                "platform": "device",
                "domain": "mqtt",
                "type": "action",
                "device_id": "synthetic-passthrough-device",
                "subtype": "off_up",
            },
        ),
        (
            "tambur-mirror-left",
            {
                "platform": "device",
                "domain": "mqtt",
                "type": "action",
                "device_id": "synthetic-mirror-device",
                "subtype": "1_single",
            },
        ),
        (
            "tambur-mirror-left",
            {
                "platform": "device",
                "domain": "mqtt",
                "type": "action",
                "device_id": "synthetic-mirror-device",
                "subtype": "1_double",
            },
        ),
        (
            "tambur-master-off",
            {
                "platform": "device",
                "domain": "mqtt",
                "type": "action",
                "device_id": "synthetic-mirror-device",
                "subtype": "2_single",
            },
        ),
        (
            "tambur-master-off",
            {
                "platform": "device",
                "domain": "mqtt",
                "type": "action",
                "device_id": "synthetic-mirror-device",
                "subtype": "2_double",
            },
        ),
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
