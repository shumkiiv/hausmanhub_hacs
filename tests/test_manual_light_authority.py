from __future__ import annotations

import pytest

from custom_components.hausman_hub.application.manual_light_authority import ManualLightAuthority


class _Store:
    recovered_previous = False
    def __init__(self) -> None: self.payload = None
    async def async_load(self): return self.payload
    async def async_save(self, payload): self.payload = payload


async def test_capture_is_durable_and_duplicate_is_not_extended() -> None:
    store = _Store()
    authority = ManualLightAuthority(store)
    assert await authority.async_capture("light.demo", "off", "yandex", "ctx-1")
    assert authority.blocks("light.demo")
    assert not await authority.async_capture("light.demo", "off", "yandex", "ctx-1")
    assert store.payload["records"][0]["generation"] == 1


async def test_storage_failure_remains_deny_only() -> None:
    class _FailingStore(_Store):
        async def async_save(self, payload): raise RuntimeError("disk")
    authority = ManualLightAuthority(_FailingStore())
    with pytest.raises(RuntimeError):
        await authority.async_capture("light.demo", "on", "manual", "ctx-2")
    assert authority.blocks("light.other")
