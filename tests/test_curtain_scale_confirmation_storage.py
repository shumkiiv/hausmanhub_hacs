"""Home Assistant adapter tests for office curtain scale authority."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

from custom_components.hausman_hub.application.curtain_command_policy import (
    OFFICE_CURTAIN_TARGET,
)
from custom_components.hausman_hub.application.curtain_scale_confirmation import (
    OFFICE_CURTAIN_ENTITY_ID,
    CurtainScaleConfirmation,
)


ADAPTER_MODULE = "custom_components.hausman_hub.curtain_scale_confirmation_storage"


@pytest.fixture(autouse=True)
def unload_fake_storage_adapter() -> object:
    """Do not leak the fake HA Store class into later integration tests."""

    sys.modules.pop(ADAPTER_MODULE, None)
    yield
    sys.modules.pop(ADAPTER_MODULE, None)


class FakeStore:
    """Mirror HA Store's on-disk envelope and corrupt-load behavior."""

    def __init__(
        self,
        hass: object,
        version: int,
        key: str,
        *,
        atomic_writes: bool,
    ) -> None:
        assert atomic_writes is True
        self.version = version
        self.key = key
        self.path = str(Path(hass.config_dir, ".storage", key))

    async def async_load(self) -> object | None:
        try:
            document = json.loads(Path(self.path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return document.get("data")

    async def async_save(self, payload: dict[str, object]) -> None:
        target = Path(self.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "version": self.version,
                    "minor_version": 1,
                    "key": self.key,
                    "data": payload,
                }
            ),
            encoding="utf-8",
        )


def install_storage_modules(monkeypatch: pytest.MonkeyPatch, hass: object) -> None:
    homeassistant = ModuleType("homeassistant")
    helpers = ModuleType("homeassistant.helpers")
    storage = ModuleType("homeassistant.helpers.storage")
    entity_registry = ModuleType("homeassistant.helpers.entity_registry")
    device_registry = ModuleType("homeassistant.helpers.device_registry")
    storage.Store = FakeStore  # type: ignore[attr-defined]
    entity_registry.async_get = lambda _hass: hass.entity_registry  # type: ignore[attr-defined]
    device_registry.async_get = lambda _hass: hass.device_registry  # type: ignore[attr-defined]
    helpers.storage = storage  # type: ignore[attr-defined]
    helpers.entity_registry = entity_registry  # type: ignore[attr-defined]
    helpers.device_registry = device_registry  # type: ignore[attr-defined]
    homeassistant.helpers = helpers  # type: ignore[attr-defined]
    for name, module in {
        "homeassistant": homeassistant,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.storage": storage,
        "homeassistant.helpers.entity_registry": entity_registry,
        "homeassistant.helpers.device_registry": device_registry,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)


@pytest.mark.asyncio
async def test_real_shape_registry_adapter_and_verified_recovery_stay_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recovered confirmed N-1 record must not become authority."""

    entity_entry = SimpleNamespace(
        entity_id=OFFICE_CURTAIN_ENTITY_ID,
        platform="mqtt",
        unique_id="0x0011223344556677_cover_zigbee2mqtt",
        device_id="device-office-curtain",
    )
    device_entry = SimpleNamespace(
        id="device-office-curtain",
        identifiers={("mqtt", "zigbee2mqtt_0x0011223344556677")},
        connections=set(),
        manufacturer="Lilistore",
        model="Cover motor",
    )
    hass = SimpleNamespace(
        config_dir=str(tmp_path),
        entity_registry=SimpleNamespace(async_get=lambda entity_id: entity_entry),
        device_registry=SimpleNamespace(async_get=lambda device_id: device_entry),
    )

    async def run_sync(function, *args):
        return function(*args)

    hass.async_add_executor_job = run_sync
    install_storage_modules(monkeypatch, hass)
    adapter = importlib.import_module(ADAPTER_MODULE)
    catalog_device = SimpleNamespace(entity_id=OFFICE_CURTAIN_ENTITY_ID)
    resolver = lambda target_id: adapter.resolve_office_curtain_identity(
        hass,
        lambda requested: catalog_device if requested == OFFICE_CURTAIN_TARGET else None,
        target_id,
    )

    store = adapter.HomeAssistantCurtainScaleConfirmationStore(hass, "entry-1")
    service = CurtainScaleConfirmation(
        store,
        entry_id="entry-1",
        identity_resolver=resolver,
        now_ms=lambda: 100,
    )
    await service.async_load()
    identity = service.office_confirmation_view().identity
    assert identity.entity_unique_id == "0x0011223344556677_cover_zigbee2mqtt"
    assert identity.device_identifiers == (
        ("mqtt", "zigbee2mqtt_0x0011223344556677"),
    )

    await service.async_confirm_office(0, identity.identity_digest)
    await service.async_revoke(1)
    Path(store._store._backend.path).write_text("{broken", encoding="utf-8")

    recovered_store = adapter.HomeAssistantCurtainScaleConfirmationStore(
        hass, "entry-1"
    )
    recovered = CurtainScaleConfirmation(
        recovered_store,
        entry_id="entry-1",
        identity_resolver=resolver,
        now_ms=lambda: 200,
    )
    await recovered.async_load()
    assert recovered_store.recovered_previous is True
    assert recovered.authorization_snapshot(OFFICE_CURTAIN_TARGET).confirmed is False

    restarted_store = adapter.HomeAssistantCurtainScaleConfirmationStore(
        hass, "entry-1"
    )
    restarted = CurtainScaleConfirmation(
        restarted_store,
        entry_id="entry-1",
        identity_resolver=resolver,
        now_ms=lambda: 300,
    )
    await restarted.async_load()
    assert restarted_store.recovered_previous is True
    assert restarted.authorization_snapshot(OFFICE_CURTAIN_TARGET).confirmed is False


def test_registry_adapter_rejects_entity_without_physical_device(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = SimpleNamespace(
        config_dir=str(tmp_path),
        entity_registry=SimpleNamespace(
            async_get=lambda _entity_id: SimpleNamespace(
                unique_id="entity-only", device_id=None
            )
        ),
        device_registry=SimpleNamespace(async_get=lambda _device_id: None),
    )
    install_storage_modules(monkeypatch, hass)
    adapter = importlib.import_module(ADAPTER_MODULE)

    assert adapter.resolve_office_curtain_identity(
        hass,
        lambda _target_id: SimpleNamespace(entity_id=OFFICE_CURTAIN_ENTITY_ID),
        OFFICE_CURTAIN_TARGET,
    ) is None
