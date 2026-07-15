"""Run the HASC read-only skeleton against an isolated Home Assistant Core.

This is an explicit compatibility smoke check, not a live-home test. It copies
the integration into a new temporary Home Assistant configuration directory,
exercises safe ``read-only`` and ``shadow`` config entries, and removes all
temporary files when finished. It never receives a real credential, connects
to a real or remote network, or calls Home Assistant services or devices. Its
only HTTP check starts a temporary loopback server to test the authenticated
local nine-count route. The test permits exactly nine HASC diagnostic sensors
and no other HASC entity or service.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from http import HTTPStatus
import importlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any

from aiohttp.test_utils import TestClient, TestServer
from homeassistant import config_entries
from homeassistant.auth.const import GROUP_ID_ADMIN, GROUP_ID_READ_ONLY
from homeassistant import loader
from homeassistant.bootstrap import async_from_config_dict
from homeassistant.config_entries import ConfigEntry, ConfigEntryDisabler
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import InvalidData
from homeassistant.helpers import device_registry, entity_registry


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
INTEGRATION_SOURCE = REPOSITORY_ROOT / "custom_components" / "hausman_hub"
SUMMARY_SENSOR_KEYS = (
    "areas_count",
    "devices_count",
    "entities_count",
    "sensors_count",
    "available_entities_count",
    "unavailable_entities_count",
    "unknown_entities_count",
    "not_reported_entities_count",
    "disabled_entities_count",
)
PROTECTED_SUMMARY_SENSOR_ENTITY_IDS = frozenset(
    f"sensor.hausman_hub_hasc_{key}" for key in SUMMARY_SENSOR_KEYS
)
RESERVED_SUMMARY_SENSOR_ENTITY_ID = "sensor.hausman_hub_hasc_areas_count"
EXTERNAL_COLLISION_PLATFORM = "homeassistant"
LOCAL_SUMMARY_ACTIVE_ENTRY = "local_summary_active_entry"
LOCAL_SUMMARY_PATH = "/api/hausman_hub/local-summary"
# These are the generic names Home Assistant produced for version 0.3.0.
# They are fixed here only to prove that an existing registry keeps them on
# update; no live Home Assistant names are read or stored by this check.
LEGACY_SUMMARY_SENSOR_ENTITY_IDS = frozenset(
    {
        "sensor.areas",
        "sensor.available_entities",
        "sensor.devices",
        "sensor.disabled_entities",
        "sensor.entities_with_unknown_state",
        "sensor.entities_without_a_state",
        "sensor.home_assistant_entities",
        "sensor.sensors",
        "sensor.unavailable_entities",
    }
)
UNSAFE_PROXY_DATA = {
    "mode": "proxy",
    "direct_execution_status": "direct_execution_blocked",
}
UNSAFE_ALLOWED_DIRECT_EXECUTION_DATA = {
    "mode": "read-only",
    "direct_execution_status": "allowed",
}
UNSAFE_MISSING_DIRECT_EXECUTION_DATA = {"mode": "read-only"}
UNSAFE_MISSING_MODE_DATA = {
    "direct_execution_status": "direct_execution_blocked",
}
UNSAFE_EXTRA_FIELD_DATA = {
    "mode": "read-only",
    "direct_execution_status": "direct_execution_blocked",
    "unmodelled": "outside_contract",
}
UNSAFE_PROXY_OPTIONS = {"mode": "proxy"}
UNSAFE_EXTRA_FIELD_OPTIONS = {
    "mode": "shadow",
    "unmodelled": "outside_contract",
}


@dataclass(frozen=True)
class ReservedCollisionEntry:
    """Remember the disposable external entry that HASC must never change."""

    registry_id: str
    entity_id: str
    unique_id: str
    platform: str
    config_entry_id: str | None
    device_id: str | None


@dataclass(frozen=True)
class RemovedHascEntry:
    """Keep only disposable HASC identifiers needed after the final restart."""

    entry_id: str
    entity_ids: frozenset[str]


def load_integration_domain() -> str:
    """Read the tested integration's domain without importing its source."""

    manifest_path = INTEGRATION_SOURCE / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return manifest["domain"]


def assert_result(actual: Any, expected: Any, message: str) -> None:
    """Raise a clear error when a Home Assistant flow result is unsafe."""

    if actual != expected:
        raise RuntimeError(f"{message}: expected {expected!r}, got {actual!r}")


async def async_create_safe_entry(
    hass: HomeAssistant,
    domain: str,
    mode: str,
) -> ConfigEntry:
    """Create and load one approved mode through the real config flow."""

    created_entry = await hass.config_entries.flow.async_init(
        domain,
        context={"source": config_entries.SOURCE_USER},
        data={"mode": mode},
    )
    assert_result(
        created_entry["type"],
        "create_entry",
        f"{mode} must create an entry",
    )
    entry = created_entry["result"]
    assert_result(entry.data["mode"], mode, "entry must preserve the safe mode")
    assert_result(
        entry.data["direct_execution_status"],
        "direct_execution_blocked",
        "direct execution must stay blocked",
    )
    await hass.async_block_till_done()
    assert_result(
        entry.state,
        config_entries.ConfigEntryState.LOADED,
        "safe entry must load successfully",
    )
    return entry


async def async_assert_second_entry_is_rejected(
    hass: HomeAssistant,
    domain: str,
    entry: ConfigEntry,
    expected_entry_state: config_entries.ConfigEntryState = config_entries.ConfigEntryState.LOADED,
    expected_disabled_by: ConfigEntryDisabler | None = None,
) -> None:
    """Reject a second safe setup without changing the existing one."""

    entry_data_before = dict(entry.data)
    entry_options_before = dict(entry.options)
    duplicate = await hass.config_entries.flow.async_init(
        domain,
        context={"source": config_entries.SOURCE_USER},
        data={"mode": "shadow"},
    )
    assert_result(
        duplicate["type"],
        "abort",
        "a second HASC setup must be rejected",
    )
    assert_result(
        duplicate["reason"],
        "single_instance_allowed",
        "a second HASC setup must report that only one setup is allowed",
    )
    await hass.async_block_till_done()

    entries = hass.config_entries.async_entries(domain)
    assert_result(
        len(entries),
        1,
        "the integration must retain exactly one setup",
    )
    assert_result(
        entries[0].entry_id,
        entry.entry_id,
        "the existing HASC setup must remain the only setup",
    )
    assert_result(
        entry.data,
        entry_data_before,
        "a rejected second setup must not change the first setup data",
    )
    assert_result(
        entry.options,
        entry_options_before,
        "a rejected second setup must not change the first setup options",
    )
    assert_result(
        entry.disabled_by,
        expected_disabled_by,
        "a rejected second setup must preserve HASC deactivation state",
    )
    assert_result(
        entry.state,
        expected_entry_state,
        "a rejected second setup must keep the existing HASC state",
    )


async def async_remove_safe_entry(
    hass: HomeAssistant,
    entry_id: str,
) -> RemovedHascEntry:
    """Remove a safe entry and retain only names needed for restart checks."""

    owned_entity_ids = frozenset(
        entry.entity_id
        for entry in entity_registry.async_entries_for_config_entry(
            entity_registry.async_get(hass),
            entry_id,
        )
    )
    removal = await hass.config_entries.async_remove(entry_id)
    assert_result(
        removal["require_restart"],
        False,
        "safe entry must unload and remove cleanly",
    )
    await hass.async_block_till_done()
    assert_result(
        hass.config_entries.async_get_entry(entry_id),
        None,
        "removed entry must no longer be registered",
    )
    assert_result(
        entity_registry.async_entries_for_config_entry(
            entity_registry.async_get(hass),
            entry_id,
        ),
        [],
        "removed entry must not leave entities behind",
    )
    for entity_id in owned_entity_ids:
        if hass.states.get(entity_id) is not None:
            raise RuntimeError("removed entry must not leave state values behind")
    return RemovedHascEntry(entry_id=entry_id, entity_ids=owned_entity_ids)


async def async_unload_safe_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Stop a safe entry without removing or user-deactivating its setup."""

    unloaded = await hass.config_entries.async_unload(entry.entry_id)
    assert_result(unloaded, True, "safe entry must unload successfully")
    await hass.async_block_till_done()

    retained_entry = hass.config_entries.async_get_entry(entry.entry_id)
    if retained_entry is None:
        raise RuntimeError("ordinary unload must retain the saved HASC setup")
    assert_result(
        retained_entry.entry_id,
        entry.entry_id,
        "ordinary unload must retain the same HASC setup",
    )
    assert_result(
        entry.disabled_by,
        None,
        "ordinary unload must not user-deactivate HASC",
    )
    assert_result(
        entry.state,
        config_entries.ConfigEntryState.NOT_LOADED,
        "ordinary unload must leave HASC not loaded",
    )


async def async_setup_safe_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Start the same safe, user-enabled entry after an ordinary unload."""

    started = await hass.config_entries.async_setup(entry.entry_id)
    assert_result(started, True, "safe entry must start successfully")
    await hass.async_block_till_done()
    assert_result(
        entry.disabled_by,
        None,
        "ordinary setup must keep HASC user-enabled",
    )
    assert_result(
        entry.state,
        config_entries.ConfigEntryState.LOADED,
        "ordinary setup must load HASC successfully",
    )


async def async_disable_safe_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Use Home Assistant's normal user deactivation path in the empty check."""

    disabled = await hass.config_entries.async_set_disabled_by(
        entry.entry_id,
        ConfigEntryDisabler.USER,
    )
    assert_result(disabled, True, "safe entry must deactivate successfully")
    await hass.async_block_till_done()
    assert_result(
        entry.disabled_by,
        ConfigEntryDisabler.USER,
        "deactivated HASC must record user deactivation",
    )
    assert_result(
        entry.state,
        config_entries.ConfigEntryState.NOT_LOADED,
        "deactivated HASC must no longer stay loaded",
    )


async def async_enable_safe_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Restore an entry through Home Assistant's normal user activation path."""

    enabled = await hass.config_entries.async_set_disabled_by(entry.entry_id, None)
    assert_result(enabled, True, "safe entry must activate successfully")
    await hass.async_block_till_done()
    assert_result(
        entry.disabled_by,
        None,
        "reactivated HASC must clear user deactivation",
    )
    assert_result(
        entry.state,
        config_entries.ConfigEntryState.LOADED,
        "reactivated HASC must load successfully",
    )


async def async_update_safe_options(
    hass: HomeAssistant,
    entry: ConfigEntry,
    target_mode: str,
) -> None:
    """Reject unsafe options, persist a safe mode, and verify a safe reload."""

    rejected_options_form = await hass.config_entries.options.async_init(entry.entry_id)
    assert_result(
        rejected_options_form["type"],
        "form",
        "options flow must show a form",
    )
    options_before_rejection = dict(entry.options)
    rejected_flow_id = rejected_options_form["flow_id"]
    try:
        await hass.config_entries.options.async_configure(
            rejected_flow_id,
            {"mode": "proxy"},
        )
    except InvalidData:
        hass.config_entries.options.async_abort(rejected_flow_id)
    else:
        raise RuntimeError("proxy must be rejected by the options schema")
    assert_result(
        entry.options,
        options_before_rejection,
        "rejected options must not mutate the entry",
    )
    assert_result(
        entry.state,
        config_entries.ConfigEntryState.LOADED,
        "rejected options must keep the entry loaded",
    )

    options_form = await hass.config_entries.options.async_init(entry.entry_id)
    assert_result(options_form["type"], "form", "options flow must show a form")
    safe_options = await hass.config_entries.options.async_configure(
        options_form["flow_id"],
        {"mode": target_mode},
    )
    assert_result(
        safe_options["type"],
        "create_entry",
        f"{target_mode} must be accepted in options",
    )
    await hass.async_block_till_done()
    assert_result(
        entry.options.get("mode"),
        target_mode,
        "options must preserve the safe mode",
    )
    assert_result(
        entry.data["direct_execution_status"],
        "direct_execution_blocked",
        "options must not change the direct execution block",
    )
    assert_result(
        entry.state,
        config_entries.ConfigEntryState.LOADED,
        "updating safe options must keep the entry loaded",
    )
    reloaded = await hass.config_entries.async_reload(entry.entry_id)
    assert_result(reloaded, True, "safe entry must reload successfully")
    await hass.async_block_till_done()
    assert_result(
        entry.state,
        config_entries.ConfigEntryState.LOADED,
        "safe entry must stay loaded after a safe reload",
    )


async def async_assert_safe_diagnostics(
    hass: HomeAssistant,
    domain: str,
    entry: ConfigEntry,
    expected_mode: str,
) -> None:
    """Load diagnostics and verify its fixed safe shape with aggregate counts."""

    integration = await loader.async_get_integration(hass, domain)
    diagnostics_platform = await integration.async_get_platform("diagnostics")
    snapshot = await diagnostics_platform.async_get_config_entry_diagnostics(hass, entry)

    home_summary = snapshot.pop("home_summary", None)
    assert_result(
        snapshot,
        {
            "entry_summary": {
                "mode": expected_mode,
                "single_config_entry": True,
            },
            "safety_model": {
                "device_authority": "not_granted",
                "direct_execution_status": "direct_execution_blocked",
                "proxy_status": "not_approved",
            },
            "shadow_parity": {
                "parity_status": "unresolved",
                "evidence_status": "not_collected",
            },
            "repairs_summary": {
                "automatic_repairs": "disabled",
                "manual_guidance_only": True,
            },
            "redaction_report": {
                "status": "passed",
                "strategy": "allow_list_only_with_aggregate_home_summary",
            },
        },
        "diagnostics must contain only the fixed safe report",
    )
    assert_safe_home_summary(home_summary)


def assert_safe_home_summary(home_summary: Any) -> None:
    """Validate the only permitted dynamic diagnostics section.

    A blank Home Assistant still has its own built-in registry entries, so this
    check proves the count-only contract rather than assuming every total is
    zero. This assertion does not create or modify any Home Assistant object.
    """

    if not isinstance(home_summary, dict):
        raise RuntimeError("home summary must be a dictionary")

    expected_keys = {
        "areas_count",
        "devices_count",
        "entities_count",
        "sensors_count",
        "available_entities_count",
        "unavailable_entities_count",
        "unknown_entities_count",
        "not_reported_entities_count",
        "disabled_entities_count",
    }
    assert_result(set(home_summary), expected_keys, "home summary keys must be fixed")
    if any(type(value) is not int or value < 0 for value in home_summary.values()):
        raise RuntimeError("home summary values must be non-negative integers")
    if home_summary["sensors_count"] > home_summary["entities_count"]:
        raise RuntimeError("home summary sensor count must not exceed entity count")
    if (
        home_summary["available_entities_count"]
        + home_summary["unavailable_entities_count"]
        + home_summary["unknown_entities_count"]
        + home_summary["not_reported_entities_count"]
        + home_summary["disabled_entities_count"]
        != home_summary["entities_count"]
    ):
        raise RuntimeError("home summary availability counts must equal entity count")


def assert_summary_sensor_registry(
    hass: HomeAssistant,
    domain: str,
    entry_id: str,
    expected_entity_ids: frozenset[str] | None = PROTECTED_SUMMARY_SENSOR_ENTITY_IDS,
) -> list[Any]:
    """Require only the nine approved HASC sensor registry records.

    A fresh installation must use the exact protected names. A collision check
    may instead pass ``None`` to require the protected name prefix only.
    """

    assert_result(
        hass.services.async_services().get(domain),
        None,
        "the integration must not register services",
    )
    assert_result(
        device_registry.async_entries_for_config_entry(
            device_registry.async_get(hass),
            entry_id,
        ),
        [],
        "the integration must not create devices",
    )
    entries = entity_registry.async_entries_for_config_entry(
        entity_registry.async_get(hass),
        entry_id,
    )
    assert_result(
        len(entries),
        len(SUMMARY_SENSOR_KEYS),
        "the integration must create exactly nine summary sensors",
    )
    assert_result(
        {entry.unique_id for entry in entries},
        {f"{entry_id}_{key}" for key in SUMMARY_SENSOR_KEYS},
        "the integration must create exactly the approved summary sensors",
    )
    actual_entity_ids = {entry.entity_id for entry in entries}
    if expected_entity_ids is not None:
        assert_result(
            actual_entity_ids,
            expected_entity_ids,
            "HASC display entities must keep their expected safe names",
        )
    elif any(
        not entity_id.startswith("sensor.hausman_hub_hasc_")
        for entity_id in actual_entity_ids
    ):
        raise RuntimeError("new HASC display entities must keep the protected prefix")
    for entry in entries:
        assert_result(
            entry.device_id,
            None,
            "a HASC summary sensor must not be attached to a device",
        )
    return entries


def assert_entry_has_only_summary_sensors(
    hass: HomeAssistant,
    domain: str,
    entry_id: str,
    expected_entity_ids: frozenset[str] | None = PROTECTED_SUMMARY_SENSOR_ENTITY_IDS,
) -> None:
    """Require nine enabled count sensors with current non-negative states."""

    entries = assert_summary_sensor_registry(
        hass,
        domain,
        entry_id,
        expected_entity_ids,
    )
    for entry in entries:
        assert_result(
            entry.disabled_by,
            None,
            "an active HASC summary sensor must be enabled",
        )
        state = hass.states.get(entry.entity_id)
        if state is None:
            raise RuntimeError("every HASC summary sensor must have a state")
        try:
            value = int(state.state)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("every HASC summary sensor must be a whole number") from exc
        if value < 0:
            raise RuntimeError("every HASC summary sensor must be non-negative")


def assert_entry_has_unloaded_summary_sensors(
    hass: HomeAssistant,
    domain: str,
    entry_id: str,
    expected_entity_ids: frozenset[str] | None = PROTECTED_SUMMARY_SENSOR_ENTITY_IDS,
) -> None:
    """Require ordinary unload to retain enabled records but clear values."""

    entries = assert_summary_sensor_registry(
        hass,
        domain,
        entry_id,
        expected_entity_ids,
    )
    for entry in entries:
        assert_result(
            entry.disabled_by,
            None,
            "an unloaded HASC summary sensor must remain enabled",
        )
        if hass.states.get(entry.entity_id) is not None:
            raise RuntimeError("an unloaded HASC summary sensor must not keep a state")


def assert_entry_has_disabled_summary_sensors(
    hass: HomeAssistant,
    domain: str,
    entry_id: str,
    expected_entity_ids: frozenset[str] | None = PROTECTED_SUMMARY_SENSOR_ENTITY_IDS,
) -> None:
    """Require deactivation to disable all nine count sensors and clear values."""

    entries = assert_summary_sensor_registry(
        hass,
        domain,
        entry_id,
        expected_entity_ids,
    )
    for entry in entries:
        assert_result(
            entry.disabled_by,
            entity_registry.RegistryEntryDisabler.CONFIG_ENTRY,
            "a deactivated HASC summary sensor must be disabled by its setup",
        )
        if hass.states.get(entry.entity_id) is not None:
            raise RuntimeError("a deactivated HASC summary sensor must not keep a state")


def reserve_summary_sensor_name_for_test(hass: HomeAssistant) -> ReservedCollisionEntry:
    """Reserve one HASC-like name only inside the disposable Core check."""

    reserved_entry = entity_registry.async_get(hass).async_get_or_create(
        "sensor",
        EXTERNAL_COLLISION_PLATFORM,
        "reserved_summary_sensor_name",
        suggested_object_id="hausman_hub_hasc_areas_count",
    )
    assert_result(
        reserved_entry.entity_id,
        RESERVED_SUMMARY_SENSOR_ENTITY_ID,
        "the disposable collision fixture must reserve the base HASC-like name",
    )
    assert_result(
        reserved_entry.platform,
        EXTERNAL_COLLISION_PLATFORM,
        "the disposable collision fixture must stay outside HASC",
    )
    assert_result(
        reserved_entry.config_entry_id,
        None,
        "the disposable collision fixture must not belong to a HASC setup",
    )
    assert_result(
        reserved_entry.device_id,
        None,
        "the disposable collision fixture must not belong to a device",
    )
    return ReservedCollisionEntry(
        registry_id=reserved_entry.id,
        entity_id=reserved_entry.entity_id,
        unique_id=reserved_entry.unique_id,
        platform=reserved_entry.platform,
        config_entry_id=reserved_entry.config_entry_id,
        device_id=reserved_entry.device_id,
    )


def assert_reserved_name_does_not_block_hasc(
    hass: HomeAssistant,
    entry_id: str,
    reserved_entry: ReservedCollisionEntry,
) -> None:
    """Require HASC to keep all nine sensors when a similar name is occupied."""

    entries = entity_registry.async_entries_for_config_entry(
        entity_registry.async_get(hass),
        entry_id,
    )
    entity_id_by_unique_id = {entry.unique_id: entry.entity_id for entry in entries}
    collision_sensor_id = entity_id_by_unique_id[f"{entry_id}_areas_count"]
    if collision_sensor_id == reserved_entry.entity_id:
        raise RuntimeError("HASC must not reuse an occupied summary sensor name")
    if not collision_sensor_id.startswith("sensor.hausman_hub_hasc_"):
        raise RuntimeError("HASC must keep its protected name prefix after a collision")
    assert_result(
        {
            entity_id
            for unique_id, entity_id in entity_id_by_unique_id.items()
            if unique_id != f"{entry_id}_areas_count"
        },
        PROTECTED_SUMMARY_SENSOR_ENTITY_IDS - {RESERVED_SUMMARY_SENSOR_ENTITY_ID},
        "only the occupied HASC-like name may change in the disposable collision check",
    )


def assert_reserved_collision_entry_is_unchanged(
    hass: HomeAssistant,
    reserved_entry: ReservedCollisionEntry,
) -> None:
    """Require HASC removal to leave the external collision entry unchanged."""

    current_entry = entity_registry.async_get(hass).async_get(reserved_entry.entity_id)
    if current_entry is None:
        raise RuntimeError("HASC removal must keep the external collision fixture")
    current_snapshot = ReservedCollisionEntry(
        registry_id=current_entry.id,
        entity_id=current_entry.entity_id,
        unique_id=current_entry.unique_id,
        platform=current_entry.platform,
        config_entry_id=current_entry.config_entry_id,
        device_id=current_entry.device_id,
    )
    assert_result(
        current_snapshot,
        reserved_entry,
        "HASC removal must not change the external collision fixture",
    )


def find_local_summary_routes(hass: HomeAssistant) -> tuple[Any, ...]:
    """Return every fixed local route without making an HTTP request."""

    return tuple(
        candidate
        for candidate in hass.http.app.router.resources()
        if getattr(candidate, "canonical", None) == LOCAL_SUMMARY_PATH
    )


def assert_local_summary_view(hass: HomeAssistant, domain: str) -> None:
    """Require one authenticated GET-only route for the approved nine counts."""

    runtime = hass.data.get(domain)
    if not isinstance(runtime, dict):
        raise RuntimeError("local summary runtime data must be present")

    view = runtime.get("local_summary_view")
    if view is None:
        raise RuntimeError("local summary view must be registered")
    assert_result(
        getattr(view, "requires_auth", None),
        True,
        "local summary view must require Home Assistant authentication",
    )
    assert_result(
        getattr(view, "cors_allowed", None),
        False,
        "local summary view must not allow cross-origin access",
    )

    resources = find_local_summary_routes(hass)
    if len(resources) != 1:
        raise RuntimeError(
            "local summary must register exactly one GET route, "
            f"got {len(resources)}"
        )
    resource = resources[0]
    methods = {route.method for route in resource}
    if not methods or not methods <= {"GET", "HEAD", "OPTIONS"}:
        raise RuntimeError(f"local summary route must be GET-only, got {methods!r}")


def assert_deactivated_entry_stays_inactive_after_restart(
    hass: HomeAssistant,
    domain: str,
    entry: ConfigEntry,
    expected_entity_ids: frozenset[str],
) -> None:
    """Require a user's deactivation to survive the temporary restart/update."""

    assert_result(
        entry.disabled_by,
        ConfigEntryDisabler.USER,
        "restart must preserve user deactivation",
    )
    assert_result(
        entry.state,
        config_entries.ConfigEntryState.NOT_LOADED,
        "a deactivated HASC must stay unloaded after restart",
    )
    if hass.data.get(domain) is not None:
        raise RuntimeError("a deactivated HASC must not restore runtime data after restart")
    if find_local_summary_routes(hass):
        raise RuntimeError("a deactivated HASC must not restore its local page after restart")
    assert_entry_has_disabled_summary_sensors(
        hass,
        domain,
        entry.entry_id,
        expected_entity_ids,
    )
    for summary_sensor in entity_registry.async_entries_for_config_entry(
        entity_registry.async_get(hass),
        entry.entry_id,
    ):
        if hass.states.get(summary_sensor.entity_id) is not None:
            raise RuntimeError(
                "a deactivated HASC must not restore state values after restart"
            )


async def async_assert_ordinary_unloaded_entry_recovers_after_restart(
    hass: HomeAssistant,
    domain: str,
    entry_id: str,
    expected_data: dict[str, str],
    expected_options: dict[str, Any],
    expected_entity_ids: frozenset[str],
) -> ConfigEntry:
    """Require an ordinary unloaded, enabled entry to load after restart."""

    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None:
        raise RuntimeError("ordinary unload restart must retain the saved HASC setup")
    assert_result(
        [
            configured_entry.entry_id
            for configured_entry in hass.config_entries.async_entries(domain)
        ],
        [entry_id],
        "ordinary unload restart must preserve only the safe HASC setup",
    )
    assert_result(
        dict(entry.data),
        expected_data,
        "ordinary unload restart must preserve safe entry data",
    )
    assert_result(
        dict(entry.options),
        expected_options,
        "ordinary unload restart must preserve safe entry options",
    )
    assert_result(
        entry.disabled_by,
        None,
        "ordinary unload restart must keep HASC user-enabled",
    )
    assert_result(
        entry.state,
        config_entries.ConfigEntryState.LOADED,
        "ordinary unload restart must auto-load HASC",
    )
    assert_result(
        entry.data.get("direct_execution_status"),
        "direct_execution_blocked",
        "ordinary unload restart must keep direct execution blocked",
    )
    expected_mode = expected_options.get("mode", expected_data["mode"])
    if not isinstance(expected_mode, str):
        raise RuntimeError("ordinary unload restart must retain a string safe mode")
    await async_assert_safe_diagnostics(hass, domain, entry, expected_mode)
    assert_entry_has_only_summary_sensors(
        hass,
        domain,
        entry.entry_id,
        expected_entity_ids=expected_entity_ids,
    )
    assert_local_summary_view(hass, domain)
    await async_assert_authenticated_local_summary_http_access(
        hass,
        "HASC ordinary-unload restart temporary",
    )
    return entry


def assert_hasc_stays_removed_after_restart(
    hass: HomeAssistant,
    domain: str,
    removed_entries: tuple[RemovedHascEntry, ...],
    reserved_entry: ReservedCollisionEntry,
) -> None:
    """Require the final empty restart to keep HASC completely absent."""

    if not removed_entries:
        raise RuntimeError("the lifecycle check must record removals before restart")
    if hass.config_entries.async_entries(domain):
        raise RuntimeError("removed HASC must not restore config entries after restart")
    if hass.services.async_services().get(domain) is not None:
        raise RuntimeError("removed HASC must not restore services after restart")
    if hass.data.get(domain) is not None:
        raise RuntimeError("removed HASC must not restore runtime data after restart")
    if find_local_summary_routes(hass):
        raise RuntimeError("removed HASC must not restore local summary route after restart")

    entities = entity_registry.async_get(hass)
    devices = device_registry.async_get(hass)
    for removed_entry in removed_entries:
        if entity_registry.async_entries_for_config_entry(entities, removed_entry.entry_id):
            raise RuntimeError("removed HASC must not restore entities after restart")
        if device_registry.async_entries_for_config_entry(devices, removed_entry.entry_id):
            raise RuntimeError("removed HASC must not restore devices after restart")
        for entity_id in removed_entry.entity_ids:
            if hass.states.get(entity_id) is not None:
                raise RuntimeError("removed HASC must not restore state values after restart")

    assert_reserved_collision_entry_is_unchanged(hass, reserved_entry)


def assert_persisted_unsafe_entry_stays_closed(
    hass: HomeAssistant,
    domain: str,
    entry_id: str,
    expected_data: dict[str, str],
    expected_options: dict[str, Any],
    expected_entity_ids: frozenset[str],
    reserved_entry: ReservedCollisionEntry,
) -> None:
    """Require an invalid saved entry to fail closed after a restart."""

    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None:
        raise RuntimeError("the temporary invalid HASC entry must remain inspectable")
    assert_result(
        dict(entry.data),
        expected_data,
        "restart must preserve the temporary invalid entry data",
    )
    assert_result(
        dict(entry.options),
        expected_options,
        "restart must preserve the temporary invalid entry options",
    )
    if entry.state is config_entries.ConfigEntryState.LOADED:
        raise RuntimeError("an invalid saved HASC entry must not load after restart")
    if hass.services.async_services().get(domain) is not None:
        raise RuntimeError("an invalid saved HASC entry must not restore services")
    if hass.data.get(domain) is not None:
        raise RuntimeError("an invalid saved HASC entry must not restore runtime data")
    if find_local_summary_routes(hass):
        raise RuntimeError("an invalid saved HASC entry must not restore its local page")

    entries = entity_registry.async_entries_for_config_entry(
        entity_registry.async_get(hass),
        entry_id,
    )
    if entries:
        raise RuntimeError("an invalid saved HASC entry must not restore entity registry records")
    if device_registry.async_entries_for_config_entry(device_registry.async_get(hass), entry_id):
        raise RuntimeError("an invalid saved HASC entry must not restore devices")
    for entity_id in expected_entity_ids:
        if hass.states.get(entity_id) is not None:
            raise RuntimeError("an invalid saved HASC entry must not restore count states")

    assert_reserved_collision_entry_is_unchanged(hass, reserved_entry)


async def async_assert_corrected_entry_stays_safe_after_restart(
    hass: HomeAssistant,
    domain: str,
    entry_id: str,
    expected_data: dict[str, str],
    expected_options: dict[str, Any],
    expected_entity_ids: frozenset[str],
    reserved_entry: ReservedCollisionEntry,
) -> ConfigEntry:
    """Require a manually corrected temporary entry to survive an empty restart."""

    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None:
        raise RuntimeError("the manually corrected HASC entry must remain registered")
    assert_result(
        [configured_entry.entry_id for configured_entry in hass.config_entries.async_entries(domain)],
        [entry_id],
        "restart must preserve only the corrected HASC entry",
    )
    assert_result(
        dict(entry.data),
        expected_data,
        "restart must preserve the manually corrected safe entry data",
    )
    assert_result(
        dict(entry.options),
        expected_options,
        "restart must preserve the manually corrected safe entry options",
    )
    assert_result(
        entry.state,
        config_entries.ConfigEntryState.LOADED,
        "a manually corrected HASC entry must load after restart",
    )
    expected_mode = expected_options.get("mode", expected_data["mode"])
    if not isinstance(expected_mode, str):
        raise RuntimeError("the manually corrected HASC mode must remain a string")
    await async_assert_safe_diagnostics(hass, domain, entry, expected_mode)
    assert_entry_has_only_summary_sensors(
        hass,
        domain,
        entry.entry_id,
        expected_entity_ids=expected_entity_ids,
    )
    assert_local_summary_view(hass, domain)
    await async_assert_authenticated_local_summary_http_access(
        hass,
        "HASC corrected-settings restart temporary",
    )
    assert_reserved_collision_entry_is_unchanged(hass, reserved_entry)
    return entry


async def async_create_test_access_token(
    hass: HomeAssistant,
    user: Any,
) -> str:
    """Create a short-lived synthetic token only inside the temporary Core."""

    refresh_token = await hass.auth.async_create_refresh_token(
        user,
        client_id="https://hasc-local-check.invalid",
    )
    return hass.auth.async_create_access_token(refresh_token, "127.0.0.1")


async def async_create_test_read_only_access_token(
    hass: HomeAssistant,
    user_name: str,
) -> str:
    """Create one temporary exact read-only user only inside the empty Core."""

    reader = await hass.auth.async_create_user(
        user_name,
        group_ids=[GROUP_ID_READ_ONLY],
        local_only=True,
    )
    assert_result(reader.is_admin, False, "temporary reader must not be an administrator")
    return await async_create_test_access_token(hass, reader)


async def async_assert_authenticated_local_summary_http_access(
    hass: HomeAssistant,
    test_user_prefix: str = "HASC temporary",
) -> None:
    """Exercise the actual auth middleware against one disposable loopback app."""

    # The first synthetic user is an owner so the next one can stay read-only.
    owner = await hass.auth.async_create_user(
        f"{test_user_prefix} test owner",
        group_ids=[GROUP_ID_ADMIN],
    )
    assert_result(owner.is_admin, True, "temporary owner must be an administrator")
    reader_token = await async_create_test_read_only_access_token(
        hass,
        f"{test_user_prefix} read-only test user",
    )

    owner_token = await async_create_test_access_token(hass, owner)
    server = TestServer(hass.http.app, host="127.0.0.1")
    client = TestClient(server)
    try:
        await client.start_server()
        unauthenticated = await client.get("/api/hausman_hub/local-summary")
        assert_result(
            unauthenticated.status,
            401,
            "local summary must reject an unauthenticated request",
        )

        rejected_owner = await client.get(
            "/api/hausman_hub/local-summary",
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert_result(
            rejected_owner.status,
            403,
            "local summary must reject an administrator",
        )

        accepted_reader = await client.get(
            "/api/hausman_hub/local-summary",
            headers={"Authorization": f"Bearer {reader_token}"},
        )
        assert_result(
            accepted_reader.status,
            200,
            "local summary must accept the exact local read-only user",
        )
        assert_safe_home_summary(await accepted_reader.json())

        mutation = await client.post(
            "/api/hausman_hub/local-summary",
            headers={"Authorization": f"Bearer {reader_token}"},
        )
        assert_result(mutation.status, 405, "local summary must not accept POST")
    finally:
        await client.close()


async def async_assert_local_summary_is_unavailable(
    hass: HomeAssistant,
    domain: str,
    reader_token: str,
    unavailable_after: str,
) -> None:
    """Require the retained GET route to fail closed without an active entry."""

    runtime = hass.data.get(domain)
    if not isinstance(runtime, dict):
        raise RuntimeError("the retained local summary route must keep its runtime data")
    if len(find_local_summary_routes(hass)) != 1:
        raise RuntimeError("the retained local summary route must remain unique")
    assert_result(
        runtime.get(LOCAL_SUMMARY_ACTIVE_ENTRY),
        None,
        f"{unavailable_after} must clear the active local summary entry",
    )

    server = TestServer(hass.http.app, host="127.0.0.1")
    client = TestClient(server)
    try:
        await client.start_server()
        unavailable = await client.get(
            "/api/hausman_hub/local-summary",
            headers={"Authorization": f"Bearer {reader_token}"},
        )
        assert_result(
            unavailable.status,
            HTTPStatus.SERVICE_UNAVAILABLE,
            f"local summary must become unavailable after {unavailable_after}",
        )
        payload = await unavailable.json()
        if not isinstance(payload, dict):
            raise RuntimeError("unavailable local summary response must be a dictionary")
        if set(payload) & set(SUMMARY_SENSOR_KEYS):
            raise RuntimeError("unavailable local summary must not return count values")
    finally:
        await client.close()


async def async_start_empty_home_assistant(config_directory: Path) -> HomeAssistant:
    """Start one disposable, empty Home Assistant configuration."""

    hass = HomeAssistant(str(config_directory))
    loader.async_setup(hass)
    hass.config.skip_pip = True
    configured_hass = await async_from_config_dict({}, hass)
    assert_result(
        configured_hass,
        hass,
        "empty Home Assistant configuration must bootstrap successfully",
    )
    await hass.async_start()
    await hass.async_block_till_done()
    return hass


def refresh_test_integration(config_directory: Path, domain: str) -> None:
    """Replace and reload the temporary integration copy before a restart."""

    integration_target = config_directory / "custom_components" / domain
    shutil.rmtree(integration_target)
    shutil.copytree(INTEGRATION_SOURCE, integration_target)
    importlib.invalidate_caches()
    module_prefix = f"custom_components.{domain}"
    for module_name in tuple(sys.modules):
        if module_name == module_prefix or module_name.startswith(f"{module_prefix}."):
            del sys.modules[module_name]
    custom_components = sys.modules.get("custom_components")
    if custom_components is not None:
        vars(custom_components).pop(domain, None)


def install_legacy_sensor_names_for_test(integration_target: Path) -> None:
    """Make only the temporary copy emulate the generic names from 0.3.0."""

    sensor_path = integration_target / "sensor.py"
    source = sensor_path.read_text(encoding="utf-8")
    # Exact single-line replacements fail loudly if the source changes, rather
    # than silently claiming to emulate the legacy version incorrectly.
    for current_line in (
        'SENSOR_ENTITY_ID_PREFIX: Final = f"sensor.{DOMAIN}_hasc"\n',
        '        self.entity_id = f"{SENSOR_ENTITY_ID_PREFIX}_{summary_key}"\n',
    ):
        if source.count(current_line) != 1:
            raise RuntimeError("temporary legacy sensor setup no longer matches the source")
        source = source.replace(current_line, "")
    sensor_path.write_text(source, encoding="utf-8")


async def async_assert_invalid_saved_data_lifecycle(
    config_directory: Path,
    domain: str,
    previous_removed_entries: tuple[RemovedHascEntry, ...],
    reserved_entry: ReservedCollisionEntry,
    unsafe_data: dict[str, str],
    scenario_name: str,
    safe_options_mode: str | None = None,
) -> RemovedHascEntry:
    """Prove one unsafe saved main-settings block stays closed until corrected."""

    invalid_data_hass = await async_start_empty_home_assistant(config_directory)
    invalid_entry_id: str | None = None
    invalid_entry_entity_ids: frozenset[str] | None = None
    recovered_entry_data: dict[str, str] | None = None
    recovered_entry_options: dict[str, Any] | None = None
    saved_unsafe_data = dict(unsafe_data)
    try:
        assert_hasc_stays_removed_after_restart(
            invalid_data_hass,
            domain,
            previous_removed_entries,
            reserved_entry,
        )
        invalid_entry = await async_create_safe_entry(invalid_data_hass, domain, "read-only")
        if safe_options_mode is not None:
            await async_update_safe_options(
                invalid_data_hass,
                invalid_entry,
                safe_options_mode,
            )
        safe_mode = safe_options_mode or "read-only"
        invalid_entry_id = invalid_entry.entry_id
        recovered_entry_data = dict(invalid_entry.data)
        recovered_entry_options = dict(invalid_entry.options)
        invalid_entry_entity_ids = frozenset(
            entry.entity_id
            for entry in entity_registry.async_entries_for_config_entry(
                entity_registry.async_get(invalid_data_hass),
                invalid_entry.entry_id,
            )
        )
        assert_result(
            len(invalid_entry_entity_ids),
            len(SUMMARY_SENSOR_KEYS),
            "the temporary invalid-data HASC entry must start with nine count sensors",
        )
        await async_assert_safe_diagnostics(
            invalid_data_hass,
            domain,
            invalid_entry,
            safe_mode,
        )
        assert_entry_has_only_summary_sensors(
            invalid_data_hass,
            domain,
            invalid_entry.entry_id,
            expected_entity_ids=invalid_entry_entity_ids,
        )
        assert_local_summary_view(invalid_data_hass, domain)
        invalid_reader_token = await async_create_test_read_only_access_token(
            invalid_data_hass,
            f"HASC temporary {scenario_name} test user",
        )
        invalid_data_hass.config_entries.async_update_entry(
            invalid_entry,
            data=saved_unsafe_data,
        )
        await invalid_data_hass.async_block_till_done()
        assert_result(
            dict(invalid_entry.data),
            saved_unsafe_data,
            "the temporary unsafe HASC data must persist",
        )
        reloaded_invalid_entry = await invalid_data_hass.config_entries.async_reload(
            invalid_entry.entry_id
        )
        assert_result(
            reloaded_invalid_entry,
            False,
            "an unsafe saved HASC data entry must reject reload",
        )
        await invalid_data_hass.async_block_till_done()
        if invalid_entry.state is config_entries.ConfigEntryState.LOADED:
            raise RuntimeError("unsafe saved HASC data must unload on reload")
        if entity_registry.async_entries_for_config_entry(
            entity_registry.async_get(invalid_data_hass),
            invalid_entry.entry_id,
        ):
            raise RuntimeError("unsafe saved HASC data must clear entity registry records on reload")
        await async_assert_local_summary_is_unavailable(
            invalid_data_hass,
            domain,
            invalid_reader_token,
            f"{scenario_name} reload",
        )
    finally:
        await invalid_data_hass.async_stop()

    if (
        invalid_entry_id is None
        or invalid_entry_entity_ids is None
        or recovered_entry_data is None
        or recovered_entry_options is None
    ):
        raise RuntimeError(f"the lifecycle check must create its temporary {scenario_name} entry")

    recovered_data_hass = await async_start_empty_home_assistant(config_directory)
    try:
        assert_persisted_unsafe_entry_stays_closed(
            recovered_data_hass,
            domain,
            invalid_entry_id,
            saved_unsafe_data,
            recovered_entry_options,
            invalid_entry_entity_ids,
            reserved_entry,
        )
        recovered_entry = recovered_data_hass.config_entries.async_get_entry(invalid_entry_id)
        if recovered_entry is None:
            raise RuntimeError("the temporary invalid HASC entry must remain repairable")
        recovered_data_hass.config_entries.async_update_entry(
            recovered_entry,
            data=recovered_entry_data,
        )
        await recovered_data_hass.async_block_till_done()
        reloaded_recovered_entry = await recovered_data_hass.config_entries.async_reload(
            recovered_entry.entry_id
        )
        assert_result(
            reloaded_recovered_entry,
            True,
            "a manually corrected HASC data entry must reload successfully",
        )
        await recovered_data_hass.async_block_till_done()
        assert_result(
            dict(recovered_entry.data),
            recovered_entry_data,
            "manual data correction must restore approved entry data",
        )
        assert_result(
            dict(recovered_entry.options),
            recovered_entry_options,
            "manual data correction must preserve approved options",
        )
        assert_result(
            recovered_entry.state,
            config_entries.ConfigEntryState.LOADED,
            "a manually corrected HASC data entry must load safely",
        )
        await async_assert_safe_diagnostics(
            recovered_data_hass,
            domain,
            recovered_entry,
            safe_mode,
        )
        assert_entry_has_only_summary_sensors(
            recovered_data_hass,
            domain,
            recovered_entry.entry_id,
            expected_entity_ids=invalid_entry_entity_ids,
        )
        assert_local_summary_view(recovered_data_hass, domain)
        await async_assert_authenticated_local_summary_http_access(
            recovered_data_hass,
            f"HASC corrected {scenario_name} temporary",
        )
        assert_reserved_collision_entry_is_unchanged(recovered_data_hass, reserved_entry)
    finally:
        await recovered_data_hass.async_stop()

    recovered_data_restart_hass = await async_start_empty_home_assistant(config_directory)
    removed_entry: RemovedHascEntry | None = None
    try:
        recovered_entry = await async_assert_corrected_entry_stays_safe_after_restart(
            recovered_data_restart_hass,
            domain,
            invalid_entry_id,
            recovered_entry_data,
            recovered_entry_options,
            invalid_entry_entity_ids,
            reserved_entry,
        )
        recovery_removal_reader_token = await async_create_test_read_only_access_token(
            recovered_data_restart_hass,
            f"HASC corrected {scenario_name} removal test user",
        )
        removed_entry = await async_remove_safe_entry(
            recovered_data_restart_hass,
            recovered_entry.entry_id,
        )
        await async_assert_local_summary_is_unavailable(
            recovered_data_restart_hass,
            domain,
            recovery_removal_reader_token,
            "corrected HASC data removal",
        )
        assert_reserved_collision_entry_is_unchanged(
            recovered_data_restart_hass,
            reserved_entry,
        )
    finally:
        await recovered_data_restart_hass.async_stop()

    if removed_entry is None:
        raise RuntimeError(f"the lifecycle check must remove its corrected {scenario_name} entry")

    recovered_data_removal_hass = await async_start_empty_home_assistant(
        config_directory
    )
    try:
        assert_hasc_stays_removed_after_restart(
            recovered_data_removal_hass,
            domain,
            (*previous_removed_entries, removed_entry),
            reserved_entry,
        )
    finally:
        await recovered_data_removal_hass.async_stop()

    return removed_entry


async def async_assert_invalid_saved_options_lifecycle(
    config_directory: Path,
    domain: str,
    previous_removed_entries: tuple[RemovedHascEntry, ...],
    reserved_entry: ReservedCollisionEntry,
    unsafe_options: dict[str, str],
    scenario_name: str,
) -> RemovedHascEntry:
    """Prove one unsafe saved mode-choice block stays closed until corrected."""

    invalid_options_hass = await async_start_empty_home_assistant(config_directory)
    invalid_options_entry_id: str | None = None
    invalid_options_data: dict[str, str] | None = None
    invalid_options_safe_options: dict[str, Any] | None = None
    invalid_options_entity_ids: frozenset[str] | None = None
    saved_unsafe_options = dict(unsafe_options)
    try:
        assert_hasc_stays_removed_after_restart(
            invalid_options_hass,
            domain,
            previous_removed_entries,
            reserved_entry,
        )
        invalid_options_entry = await async_create_safe_entry(
            invalid_options_hass,
            domain,
            "read-only",
        )
        await async_update_safe_options(
            invalid_options_hass,
            invalid_options_entry,
            "shadow",
        )
        invalid_options_entry_id = invalid_options_entry.entry_id
        invalid_options_data = dict(invalid_options_entry.data)
        invalid_options_safe_options = dict(invalid_options_entry.options)
        invalid_options_entity_ids = frozenset(
            entry.entity_id
            for entry in entity_registry.async_entries_for_config_entry(
                entity_registry.async_get(invalid_options_hass),
                invalid_options_entry.entry_id,
            )
        )
        assert_result(
            len(invalid_options_entity_ids),
            len(SUMMARY_SENSOR_KEYS),
            "the temporary invalid-options HASC entry must start with nine count sensors",
        )
        await async_assert_safe_diagnostics(
            invalid_options_hass,
            domain,
            invalid_options_entry,
            "shadow",
        )
        assert_entry_has_only_summary_sensors(
            invalid_options_hass,
            domain,
            invalid_options_entry.entry_id,
            expected_entity_ids=invalid_options_entity_ids,
        )
        assert_local_summary_view(invalid_options_hass, domain)
        invalid_options_reader_token = await async_create_test_read_only_access_token(
            invalid_options_hass,
            f"HASC temporary {scenario_name} test user",
        )
        invalid_options_hass.config_entries.async_update_entry(
            invalid_options_entry,
            options=saved_unsafe_options,
        )
        await invalid_options_hass.async_block_till_done()
        assert_result(
            dict(invalid_options_entry.options),
            saved_unsafe_options,
            "the temporary unsafe HASC options must persist",
        )
        reloaded_invalid_options_entry = await invalid_options_hass.config_entries.async_reload(
            invalid_options_entry.entry_id
        )
        assert_result(
            reloaded_invalid_options_entry,
            False,
            "an unsafe saved HASC options entry must reject reload",
        )
        await invalid_options_hass.async_block_till_done()
        if invalid_options_entry.state is config_entries.ConfigEntryState.LOADED:
            raise RuntimeError("unsafe saved HASC options must unload on reload")
        if entity_registry.async_entries_for_config_entry(
            entity_registry.async_get(invalid_options_hass),
            invalid_options_entry.entry_id,
        ):
            raise RuntimeError(
                "unsafe saved HASC options must clear entity registry records on reload"
            )
        await async_assert_local_summary_is_unavailable(
            invalid_options_hass,
            domain,
            invalid_options_reader_token,
            f"{scenario_name} reload",
        )
    finally:
        await invalid_options_hass.async_stop()

    if (
        invalid_options_entry_id is None
        or invalid_options_data is None
        or invalid_options_safe_options is None
        or invalid_options_entity_ids is None
    ):
        raise RuntimeError(f"the lifecycle check must create its temporary {scenario_name} entry")

    invalid_options_restarted_hass = await async_start_empty_home_assistant(config_directory)
    try:
        assert_persisted_unsafe_entry_stays_closed(
            invalid_options_restarted_hass,
            domain,
            invalid_options_entry_id,
            invalid_options_data,
            saved_unsafe_options,
            invalid_options_entity_ids,
            reserved_entry,
        )
        recovered_options_entry = invalid_options_restarted_hass.config_entries.async_get_entry(
            invalid_options_entry_id
        )
        if recovered_options_entry is None:
            raise RuntimeError("the temporary invalid HASC options entry must remain repairable")
        invalid_options_restarted_hass.config_entries.async_update_entry(
            recovered_options_entry,
            options=invalid_options_safe_options,
        )
        await invalid_options_restarted_hass.async_block_till_done()
        reloaded_recovered_options_entry = (
            await invalid_options_restarted_hass.config_entries.async_reload(
                recovered_options_entry.entry_id
            )
        )
        assert_result(
            reloaded_recovered_options_entry,
            True,
            "manually corrected HASC options must reload successfully",
        )
        await invalid_options_restarted_hass.async_block_till_done()
        assert_result(
            dict(recovered_options_entry.data),
            invalid_options_data,
            "manual options correction must preserve approved entry data",
        )
        assert_result(
            dict(recovered_options_entry.options),
            invalid_options_safe_options,
            "manual options correction must restore approved options",
        )
        assert_result(
            recovered_options_entry.state,
            config_entries.ConfigEntryState.LOADED,
            "manually corrected HASC options must load safely",
        )
        safe_mode = invalid_options_safe_options.get("mode")
        if not isinstance(safe_mode, str):
            raise RuntimeError("the corrected HASC options must retain a string mode")
        await async_assert_safe_diagnostics(
            invalid_options_restarted_hass,
            domain,
            recovered_options_entry,
            safe_mode,
        )
        assert_entry_has_only_summary_sensors(
            invalid_options_restarted_hass,
            domain,
            recovered_options_entry.entry_id,
            expected_entity_ids=invalid_options_entity_ids,
        )
        assert_local_summary_view(invalid_options_restarted_hass, domain)
        await async_assert_authenticated_local_summary_http_access(
            invalid_options_restarted_hass,
            f"HASC corrected {scenario_name} temporary",
        )
        assert_reserved_collision_entry_is_unchanged(
            invalid_options_restarted_hass,
            reserved_entry,
        )
    finally:
        await invalid_options_restarted_hass.async_stop()

    recovered_options_hass = await async_start_empty_home_assistant(config_directory)
    removed_entry: RemovedHascEntry | None = None
    try:
        recovered_options_entry = await async_assert_corrected_entry_stays_safe_after_restart(
            recovered_options_hass,
            domain,
            invalid_options_entry_id,
            invalid_options_data,
            invalid_options_safe_options,
            invalid_options_entity_ids,
            reserved_entry,
        )
        options_recovery_removal_reader_token = await async_create_test_read_only_access_token(
            recovered_options_hass,
            f"HASC corrected {scenario_name} removal test user",
        )
        removed_entry = await async_remove_safe_entry(
            recovered_options_hass,
            recovered_options_entry.entry_id,
        )
        await async_assert_local_summary_is_unavailable(
            recovered_options_hass,
            domain,
            options_recovery_removal_reader_token,
            "corrected HASC options removal",
        )
        assert_reserved_collision_entry_is_unchanged(recovered_options_hass, reserved_entry)
    finally:
        await recovered_options_hass.async_stop()

    if removed_entry is None:
        raise RuntimeError(f"the lifecycle check must remove its corrected {scenario_name} entry")

    recovered_options_removal_hass = await async_start_empty_home_assistant(
        config_directory
    )
    try:
        assert_hasc_stays_removed_after_restart(
            recovered_options_removal_hass,
            domain,
            (*previous_removed_entries, removed_entry),
            reserved_entry,
        )
    finally:
        await recovered_options_removal_hass.async_stop()

    return removed_entry


async def async_run_check() -> None:
    """Exercise safe lifecycle, restart, and removal in a blank Core."""

    domain = load_integration_domain()

    with tempfile.TemporaryDirectory(prefix="hasc-core-check-") as temporary_directory:
        config_directory = Path(temporary_directory)
        integration_target = config_directory / "custom_components" / domain
        integration_target.parent.mkdir(parents=True)
        shutil.copytree(INTEGRATION_SOURCE, integration_target)
        install_legacy_sensor_names_for_test(integration_target)

        hass = await async_start_empty_home_assistant(config_directory)
        try:
            rejected_proxy = await hass.config_entries.flow.async_init(
                domain,
                context={"source": config_entries.SOURCE_USER},
                data={"mode": "proxy"},
            )
            assert_result(rejected_proxy["type"], "form", "proxy must not create an entry")
            assert_result(
                rejected_proxy["errors"],
                {"mode": "unsafe_mode"},
                "proxy must be rejected",
            )

            read_only_entry = await async_create_safe_entry(hass, domain, "read-only")
            await async_assert_second_entry_is_rejected(hass, domain, read_only_entry)
            assert_entry_has_only_summary_sensors(
                hass,
                domain,
                read_only_entry.entry_id,
                LEGACY_SUMMARY_SENSOR_ENTITY_IDS,
            )
            await async_update_safe_options(hass, read_only_entry, "shadow")
            assert_result(
                read_only_entry.data["mode"],
                "read-only",
                "options must not mutate the initial entry mode",
            )
            await async_assert_safe_diagnostics(hass, domain, read_only_entry, "shadow")
            assert_local_summary_view(hass, domain)
            await async_assert_authenticated_local_summary_http_access(hass)
            assert_entry_has_only_summary_sensors(
                hass,
                domain,
                read_only_entry.entry_id,
                LEGACY_SUMMARY_SENSOR_ENTITY_IDS,
            )

            ordinary_unload_data = dict(read_only_entry.data)
            ordinary_unload_options = dict(read_only_entry.options)
            ordinary_unload_reader_token = await async_create_test_read_only_access_token(
                hass,
                "HASC temporary ordinary-unload test user",
            )
            await async_unload_safe_entry(hass, read_only_entry)
            assert_result(
                read_only_entry.data,
                ordinary_unload_data,
                "ordinary unload must not mutate safe entry data",
            )
            assert_result(
                read_only_entry.options,
                ordinary_unload_options,
                "ordinary unload must not mutate safe entry options",
            )
            assert_entry_has_unloaded_summary_sensors(
                hass,
                domain,
                read_only_entry.entry_id,
                LEGACY_SUMMARY_SENSOR_ENTITY_IDS,
            )
            await async_assert_local_summary_is_unavailable(
                hass,
                domain,
                ordinary_unload_reader_token,
                "HASC ordinary unload",
            )
            await async_setup_safe_entry(hass, read_only_entry)
            assert_result(
                read_only_entry.data,
                ordinary_unload_data,
                "ordinary setup must preserve safe entry data",
            )
            assert_result(
                read_only_entry.options,
                ordinary_unload_options,
                "ordinary setup must preserve safe entry options",
            )
            assert_entry_has_only_summary_sensors(
                hass,
                domain,
                read_only_entry.entry_id,
                LEGACY_SUMMARY_SENSOR_ENTITY_IDS,
            )
            await async_assert_safe_diagnostics(hass, domain, read_only_entry, "shadow")
            assert_local_summary_view(hass, domain)
            await async_assert_authenticated_local_summary_http_access(
                hass,
                "HASC ordinary setup",
            )

            deactivation_reader_token = await async_create_test_read_only_access_token(
                hass,
                "HASC temporary deactivation test user",
            )
            await async_disable_safe_entry(hass, read_only_entry)
            assert_entry_has_disabled_summary_sensors(
                hass,
                domain,
                read_only_entry.entry_id,
                LEGACY_SUMMARY_SENSOR_ENTITY_IDS,
            )
            await async_assert_local_summary_is_unavailable(
                hass,
                domain,
                deactivation_reader_token,
                "HASC deactivation",
            )
            await async_enable_safe_entry(hass, read_only_entry)
            assert_entry_has_only_summary_sensors(
                hass,
                domain,
                read_only_entry.entry_id,
                LEGACY_SUMMARY_SENSOR_ENTITY_IDS,
            )
            await async_assert_safe_diagnostics(hass, domain, read_only_entry, "shadow")
            assert_local_summary_view(hass, domain)
            await async_assert_authenticated_local_summary_http_access(
                hass,
                "HASC temporary reactivation",
            )
            await async_disable_safe_entry(hass, read_only_entry)
            assert_entry_has_disabled_summary_sensors(
                hass,
                domain,
                read_only_entry.entry_id,
                LEGACY_SUMMARY_SENSOR_ENTITY_IDS,
            )
            await async_assert_local_summary_is_unavailable(
                hass,
                domain,
                deactivation_reader_token,
                "HASC deactivation before restart",
            )

            entry_id = read_only_entry.entry_id
            expected_data = dict(read_only_entry.data)
            expected_options = dict(read_only_entry.options)
        finally:
            await hass.async_stop()

        refresh_test_integration(config_directory, domain)
        removed_entries: list[RemovedHascEntry] = []
        reserved_entry: ReservedCollisionEntry | None = None
        restarted_hass = await async_start_empty_home_assistant(config_directory)
        try:
            restored_entry = restarted_hass.config_entries.async_get_entry(entry_id)
            if restored_entry is None:
                raise RuntimeError("safe entry must persist after the disposable restart")
            assert_result(
                restored_entry.data,
                expected_data,
                "restart must preserve the initial safe entry data",
            )
            assert_result(
                restored_entry.options,
                expected_options,
                "restart must preserve the selected safe options",
            )
            assert_deactivated_entry_stays_inactive_after_restart(
                restarted_hass,
                domain,
                restored_entry,
                LEGACY_SUMMARY_SENSOR_ENTITY_IDS,
            )
            await async_assert_second_entry_is_rejected(
                restarted_hass,
                domain,
                restored_entry,
                expected_entry_state=config_entries.ConfigEntryState.NOT_LOADED,
                expected_disabled_by=ConfigEntryDisabler.USER,
            )
            assert_deactivated_entry_stays_inactive_after_restart(
                restarted_hass,
                domain,
                restored_entry,
                LEGACY_SUMMARY_SENSOR_ENTITY_IDS,
            )
            await async_enable_safe_entry(restarted_hass, restored_entry)
            assert_result(
                restored_entry.data["direct_execution_status"],
                "direct_execution_blocked",
                "restart must not change the direct execution block",
            )
            await async_assert_safe_diagnostics(
                restarted_hass,
                domain,
                restored_entry,
                "shadow",
            )
            assert_local_summary_view(restarted_hass, domain)
            assert_entry_has_only_summary_sensors(
                restarted_hass,
                domain,
                restored_entry.entry_id,
                LEGACY_SUMMARY_SENSOR_ENTITY_IDS,
            )
            await async_assert_authenticated_local_summary_http_access(
                restarted_hass,
                "HASC disabled-restart temporary",
            )
            ordinary_unload_restart_data = dict(restored_entry.data)
            ordinary_unload_restart_options = dict(restored_entry.options)
            ordinary_unload_restart_reader_token = (
                await async_create_test_read_only_access_token(
                    restarted_hass,
                    "HASC temporary ordinary-unload restart test user",
                )
            )
            await async_unload_safe_entry(restarted_hass, restored_entry)
            assert_result(
                dict(restored_entry.data),
                ordinary_unload_restart_data,
                "ordinary unload before restart must preserve safe entry data",
            )
            assert_result(
                dict(restored_entry.options),
                ordinary_unload_restart_options,
                "ordinary unload before restart must preserve safe entry options",
            )
            assert_entry_has_unloaded_summary_sensors(
                restarted_hass,
                domain,
                restored_entry.entry_id,
                LEGACY_SUMMARY_SENSOR_ENTITY_IDS,
            )
            await async_assert_second_entry_is_rejected(
                restarted_hass,
                domain,
                restored_entry,
                expected_entry_state=config_entries.ConfigEntryState.NOT_LOADED,
            )
            assert_entry_has_unloaded_summary_sensors(
                restarted_hass,
                domain,
                restored_entry.entry_id,
                LEGACY_SUMMARY_SENSOR_ENTITY_IDS,
            )
            await async_assert_local_summary_is_unavailable(
                restarted_hass,
                domain,
                ordinary_unload_restart_reader_token,
                "HASC ordinary unload before restart",
            )
        finally:
            await restarted_hass.async_stop()

        disabled_reinstall_entry_id: str | None = None
        disabled_reinstall_entity_ids: frozenset[str] | None = None
        ordinary_unload_restarted_hass = await async_start_empty_home_assistant(
            config_directory
        )
        try:
            removal_reader_token = await async_create_test_read_only_access_token(
                ordinary_unload_restarted_hass,
                "HASC temporary removal test user",
            )
            restored_entry = await async_assert_ordinary_unloaded_entry_recovers_after_restart(
                ordinary_unload_restarted_hass,
                domain,
                entry_id,
                ordinary_unload_restart_data,
                ordinary_unload_restart_options,
                LEGACY_SUMMARY_SENSOR_ENTITY_IDS,
            )
            removed_entries.append(
                await async_remove_safe_entry(
                    ordinary_unload_restarted_hass,
                    restored_entry.entry_id,
                )
            )
            await async_assert_local_summary_is_unavailable(
                ordinary_unload_restarted_hass,
                domain,
                removal_reader_token,
                "HASC removal",
            )

            reserved_entry = reserve_summary_sensor_name_for_test(
                ordinary_unload_restarted_hass
            )
            shadow_entry = await async_create_safe_entry(
                ordinary_unload_restarted_hass,
                domain,
                "shadow",
            )
            await async_update_safe_options(
                ordinary_unload_restarted_hass,
                shadow_entry,
                "read-only",
            )
            assert_result(
                shadow_entry.data["mode"],
                "shadow",
                "options must not mutate the initial entry mode",
            )
            await async_assert_safe_diagnostics(
                ordinary_unload_restarted_hass,
                domain,
                shadow_entry,
                "read-only",
            )
            assert_local_summary_view(ordinary_unload_restarted_hass, domain)
            assert_entry_has_only_summary_sensors(
                ordinary_unload_restarted_hass,
                domain,
                shadow_entry.entry_id,
                expected_entity_ids=None,
            )
            assert_reserved_name_does_not_block_hasc(
                ordinary_unload_restarted_hass,
                shadow_entry.entry_id,
                reserved_entry,
            )
            removed_entries.append(
                await async_remove_safe_entry(
                    ordinary_unload_restarted_hass,
                    shadow_entry.entry_id,
                )
            )
            await async_assert_local_summary_is_unavailable(
                ordinary_unload_restarted_hass,
                domain,
                removal_reader_token,
                "HASC removal",
            )
            assert_reserved_collision_entry_is_unchanged(
                ordinary_unload_restarted_hass,
                reserved_entry,
            )

            reinstalled_entry = await async_create_safe_entry(
                ordinary_unload_restarted_hass,
                domain,
                "read-only",
            )
            assert_entry_has_only_summary_sensors(
                ordinary_unload_restarted_hass,
                domain,
                reinstalled_entry.entry_id,
                expected_entity_ids=None,
            )
            assert_reserved_name_does_not_block_hasc(
                ordinary_unload_restarted_hass,
                reinstalled_entry.entry_id,
                reserved_entry,
            )
            assert_reserved_collision_entry_is_unchanged(
                ordinary_unload_restarted_hass,
                reserved_entry,
            )
            await async_disable_safe_entry(
                ordinary_unload_restarted_hass,
                reinstalled_entry,
            )
            assert_entry_has_disabled_summary_sensors(
                ordinary_unload_restarted_hass,
                domain,
                reinstalled_entry.entry_id,
                expected_entity_ids=None,
            )
            disabled_reinstall_entry_id = reinstalled_entry.entry_id
            disabled_reinstall_entity_ids = frozenset(
                entry.entity_id
                for entry in entity_registry.async_entries_for_config_entry(
                    entity_registry.async_get(ordinary_unload_restarted_hass),
                    reinstalled_entry.entry_id,
                )
            )
            await async_assert_local_summary_is_unavailable(
                ordinary_unload_restarted_hass,
                domain,
                removal_reader_token,
                "HASC deactivation",
            )
            assert_reserved_collision_entry_is_unchanged(
                ordinary_unload_restarted_hass,
                reserved_entry,
            )
        finally:
            await ordinary_unload_restarted_hass.async_stop()

        if reserved_entry is None:
            raise RuntimeError("the lifecycle check must reserve its external fixture")
        if (
            disabled_reinstall_entry_id is None
            or disabled_reinstall_entity_ids is None
        ):
            raise RuntimeError(
                "the lifecycle check must retain a disabled HASC setup for removal"
            )

        disabled_removal_hass = await async_start_empty_home_assistant(config_directory)
        try:
            disabled_reinstall_entry = disabled_removal_hass.config_entries.async_get_entry(
                disabled_reinstall_entry_id
            )
            if disabled_reinstall_entry is None:
                raise RuntimeError("disabled HASC setup must persist until its removal")
            assert_deactivated_entry_stays_inactive_after_restart(
                disabled_removal_hass,
                domain,
                disabled_reinstall_entry,
                disabled_reinstall_entity_ids,
            )
            assert_reserved_collision_entry_is_unchanged(
                disabled_removal_hass,
                reserved_entry,
            )
            removed_entries.append(
                await async_remove_safe_entry(
                    disabled_removal_hass,
                    disabled_reinstall_entry.entry_id,
                )
            )
            assert_reserved_collision_entry_is_unchanged(
                disabled_removal_hass,
                reserved_entry,
            )
        finally:
            await disabled_removal_hass.async_stop()

        post_removal_hass = await async_start_empty_home_assistant(config_directory)
        try:
            assert_hasc_stays_removed_after_restart(
                post_removal_hass,
                domain,
                tuple(removed_entries),
                reserved_entry,
            )
            fresh_entry = await async_create_safe_entry(
                post_removal_hass,
                domain,
                "read-only",
            )
            if fresh_entry.entry_id in {
                removed_entry.entry_id for removed_entry in removed_entries
            }:
                raise RuntimeError("fresh HASC setup must use a new entry identifier")
            assert_entry_has_only_summary_sensors(
                post_removal_hass,
                domain,
                fresh_entry.entry_id,
                expected_entity_ids=None,
            )
            assert_reserved_name_does_not_block_hasc(
                post_removal_hass,
                fresh_entry.entry_id,
                reserved_entry,
            )
            assert_reserved_collision_entry_is_unchanged(
                post_removal_hass,
                reserved_entry,
            )
            await async_assert_safe_diagnostics(
                post_removal_hass,
                domain,
                fresh_entry,
                "read-only",
            )
            assert_local_summary_view(post_removal_hass, domain)
            await async_assert_authenticated_local_summary_http_access(
                post_removal_hass,
                "HASC post-restart temporary",
            )
            fresh_removal_reader_token = await async_create_test_read_only_access_token(
                post_removal_hass,
                "HASC post-restart removal test user",
            )
            removed_entries.append(
                await async_remove_safe_entry(post_removal_hass, fresh_entry.entry_id)
            )
            await async_assert_local_summary_is_unavailable(
                post_removal_hass,
                domain,
                fresh_removal_reader_token,
                "HASC removal",
            )
            assert_reserved_collision_entry_is_unchanged(
                post_removal_hass,
                reserved_entry,
            )
        finally:
            await post_removal_hass.async_stop()

        removed_entries.append(
            await async_assert_invalid_saved_data_lifecycle(
                config_directory,
                domain,
                tuple(removed_entries),
                reserved_entry,
                UNSAFE_PROXY_DATA,
                "invalid-mode data",
            )
        )
        removed_entries.append(
            await async_assert_invalid_saved_data_lifecycle(
                config_directory,
                domain,
                tuple(removed_entries),
                reserved_entry,
                UNSAFE_ALLOWED_DIRECT_EXECUTION_DATA,
                "unblocked-execution data",
            )
        )
        removed_entries.append(
            await async_assert_invalid_saved_data_lifecycle(
                config_directory,
                domain,
                tuple(removed_entries),
                reserved_entry,
                UNSAFE_MISSING_DIRECT_EXECUTION_DATA,
                "missing-execution-block data",
            )
        )
        removed_entries.append(
            await async_assert_invalid_saved_data_lifecycle(
                config_directory,
                domain,
                tuple(removed_entries),
                reserved_entry,
                UNSAFE_MISSING_MODE_DATA,
                "missing-mode data",
                safe_options_mode="shadow",
            )
        )
        removed_entries.append(
            await async_assert_invalid_saved_data_lifecycle(
                config_directory,
                domain,
                tuple(removed_entries),
                reserved_entry,
                UNSAFE_EXTRA_FIELD_DATA,
                "extra-field data",
            )
        )

        removed_entries.append(
            await async_assert_invalid_saved_options_lifecycle(
                config_directory,
                domain,
                tuple(removed_entries),
                reserved_entry,
                UNSAFE_PROXY_OPTIONS,
                "invalid-mode options",
            )
        )
        removed_entries.append(
            await async_assert_invalid_saved_options_lifecycle(
                config_directory,
                domain,
                tuple(removed_entries),
                reserved_entry,
                UNSAFE_EXTRA_FIELD_OPTIONS,
                "extra-field options",
            )
        )


def main() -> None:
    """Run the isolated Core compatibility check from a Python 3.14 environment."""

    asyncio.run(async_run_check())
    print("Home Assistant Core compatibility check passed.")


if __name__ == "__main__":
    main()
