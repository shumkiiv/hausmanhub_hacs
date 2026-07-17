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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from http import HTTPStatus
import importlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any

from aiohttp.test_utils import TestClient, TestServer
import voluptuous_serialize
from homeassistant import config_entries
from homeassistant.auth.const import GROUP_ID_ADMIN, GROUP_ID_READ_ONLY, GROUP_ID_USER
from homeassistant import loader
from homeassistant.bootstrap import async_from_config_dict
from homeassistant.config_entries import ConfigEntry, ConfigEntryDisabler
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import InvalidData
from homeassistant.helpers import config_validation, device_registry, entity_registry
from homeassistant.helpers.entity_component import DATA_INSTANCES


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
SUMMARY_SENSOR_ICONS = {
    "areas_count": "mdi:floor-plan",
    "devices_count": "mdi:devices",
    "entities_count": "mdi:shape",
    "sensors_count": "mdi:eye-outline",
    "available_entities_count": "mdi:check-circle-outline",
    "unavailable_entities_count": "mdi:alert-circle-outline",
    "unknown_entities_count": "mdi:help-circle-outline",
    "not_reported_entities_count": "mdi:minus-circle-outline",
    "disabled_entities_count": "mdi:pause-circle-outline",
}
PROTECTED_SUMMARY_SENSOR_ENTITY_IDS = frozenset(
    f"sensor.hausman_hub_hasc_{key}" for key in SUMMARY_SENSOR_KEYS
)
RESERVED_SUMMARY_SENSOR_ENTITY_ID = "sensor.hausman_hub_hasc_areas_count"
EXTERNAL_COLLISION_PLATFORM = "homeassistant"
LOCAL_SUMMARY_ACTIVE_ENTRY = "local_summary_active_entry"
LOCAL_SUMMARY_ENABLED_FIELD = "local_summary_enabled"
SUMMARY_UPDATE_INTERVAL_FIELD = "summary_update_interval"
SUMMARY_UPDATE_INTERVAL_DEFAULT = "5m"
SUMMARY_UPDATE_INTERVAL_MINUTES = {
    "5m": 5,
    "15m": 15,
    "30m": 30,
}
LOCAL_SUMMARY_PATH = "/api/hausman_hub/local-summary"
ALTERNATE_LOCAL_SUMMARY_TARGET_STATUSES = {
    f"{LOCAL_SUMMARY_PATH}/": HTTPStatus.NOT_FOUND,
    f"{LOCAL_SUMMARY_PATH}?unexpected=1": HTTPStatus.NOT_FOUND,
}
NON_GET_LOCAL_SUMMARY_STATUSES = {
    "HEAD": HTTPStatus.METHOD_NOT_ALLOWED,
    "OPTIONS": HTTPStatus.FORBIDDEN,
    "POST": HTTPStatus.METHOD_NOT_ALLOWED,
    "PUT": HTTPStatus.METHOD_NOT_ALLOWED,
    "PATCH": HTTPStatus.METHOD_NOT_ALLOWED,
    "DELETE": HTTPStatus.METHOD_NOT_ALLOWED,
    "TRACE": HTTPStatus.METHOD_NOT_ALLOWED,
    "CONNECT": HTTPStatus.NOT_FOUND,
}
APPROVED_LOCAL_SUMMARY_ORIGINS = (
    "127.0.0.0",
    "127.255.255.255",
    "10.0.0.0",
    "10.255.255.255",
    "172.16.0.0",
    "172.31.255.255",
    "192.168.0.0",
    "192.168.255.255",
    "::1",
    "fc00::",
    "fdff:ffff:ffff:ffff:ffff:ffff:ffff:ffff",
    "::ffff:127.0.0.0",
    "::ffff:127.255.255.255",
    "::ffff:10.0.0.0",
    "::ffff:10.255.255.255",
    "::ffff:172.16.0.0",
    "::ffff:172.31.255.255",
    "::ffff:192.168.0.0",
    "::ffff:192.168.255.255",
)
DISALLOWED_LOCAL_SUMMARY_ORIGINS = (
    "0.0.0.0",
    "::",
    "::2",
    "::ffff:0.0.0.0",
    "126.255.255.255",
    "128.0.0.0",
    "9.255.255.255",
    "11.0.0.0",
    "172.15.255.255",
    "172.32.0.0",
    "192.167.255.255",
    "192.169.0.0",
    "192.0.2.1",
    "198.51.100.1",
    "203.0.113.1",
    "169.254.1.1",
    "100.64.0.1",
    "fbff:ffff:ffff:ffff:ffff:ffff:ffff:ffff",
    "fe00::",
    "fe80::1",
    "2001:db8::1",
    "::ffff:126.255.255.255",
    "::ffff:128.0.0.0",
    "::ffff:9.255.255.255",
    "::ffff:11.0.0.0",
    "::ffff:192.0.2.1",
    "::ffff:172.15.255.255",
    "::ffff:172.32.0.0",
    "::ffff:192.167.255.255",
    "::ffff:192.169.0.0",
)
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


class DirectLocalSummaryRequest(dict[str, Any]):
    """Provide the minimal authenticated request shape for one direct view check."""

    def __init__(self, remote: str, user: Any) -> None:
        super().__init__(hass_user=user)
        self.remote = remote
        self.path = LOCAL_SUMMARY_PATH
        self.query_string = ""


def load_integration_domain() -> str:
    """Read the tested integration's domain without importing its source."""

    manifest_path = INTEGRATION_SOURCE / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return manifest["domain"]


def assert_result(actual: Any, expected: Any, message: str) -> None:
    """Raise a clear error when a Home Assistant flow result is unsafe."""

    if actual != expected:
        raise RuntimeError(f"{message}: expected {expected!r}, got {actual!r}")


@asynccontextmanager
async def async_block_home_summary_reads(
    hass: HomeAssistant,
    domain: str,
    scenario_name: str,
) -> AsyncIterator[None]:
    """Make every HASC home-summary reader fail during one safety action."""

    integration = await loader.async_get_integration(hass, domain)
    adapters = (
        await integration.async_get_platform("sensor"),
        await integration.async_get_platform("diagnostics"),
        await integration.async_get_platform("local_summary"),
    )
    original_collectors = tuple(
        (adapter, adapter.collect_home_summary) for adapter in adapters
    )

    def fail_if_home_is_read(*_: object, **__: object) -> object:
        raise RuntimeError(f"{scenario_name} must not read the home")

    for adapter, _ in original_collectors:
        adapter.collect_home_summary = fail_if_home_is_read
    try:
        yield
    finally:
        for adapter, original_collect_home_summary in original_collectors:
            adapter.collect_home_summary = original_collect_home_summary


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


async def async_add_disposable_persisted_duplicate_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> ConfigEntry:
    """Insert one valid duplicate only in the empty Core test configuration.

    The normal user flow correctly refuses a second HASC setup. This helper
    deliberately bypasses that flow only inside ``TemporaryDirectory`` so the
    running empty Core and a later restart can prove that malformed saved
    duplicates fail closed. It does not read or write a real Home Assistant
    configuration.
    """

    duplicate_entry = ConfigEntry(
        created_at=entry.created_at,
        data=dict(entry.data),
        discovery_keys=entry.discovery_keys,
        domain=entry.domain,
        entry_id=f"{entry.entry_id}-persisted-duplicate",
        minor_version=entry.minor_version,
        modified_at=entry.modified_at,
        options=dict(entry.options),
        pref_disable_new_entities=entry.pref_disable_new_entities,
        pref_disable_polling=entry.pref_disable_polling,
        source=entry.source,
        subentries_data=tuple(
            subentry.as_dict() for subentry in entry.subentries.values()
        ),
        title=entry.title,
        unique_id=entry.unique_id,
        version=entry.version,
    )
    await hass.config_entries.async_add(duplicate_entry)
    await hass.async_block_till_done()
    assert_result(
        {
            configured_entry.entry_id
            for configured_entry in hass.config_entries.async_entries(entry.domain)
        },
        {entry.entry_id, duplicate_entry.entry_id},
        "the disposable duplicate fixture must retain two saved HASC entries",
    )
    if duplicate_entry.state is config_entries.ConfigEntryState.LOADED:
        raise RuntimeError("a disposable duplicate HASC entry must fail closed")
    return duplicate_entry


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


async def async_enable_unsafe_entry_without_reading_home(
    hass: HomeAssistant,
    domain: str,
    entry: ConfigEntry,
    scenario_name: str,
) -> None:
    """Prove explicit activation cannot load an unsafe disabled HASC setup."""

    assert_result(
        entry.disabled_by,
        ConfigEntryDisabler.USER,
        f"{scenario_name} must begin user-disabled",
    )
    assert_result(
        entry.state,
        config_entries.ConfigEntryState.NOT_LOADED,
        f"{scenario_name} must begin not loaded",
    )
    reload_calls: list[str] = []
    original_async_reload = hass.config_entries.async_reload

    async def async_recording_reload(entry_id: str) -> bool:
        """Record the one framework reload attempted for unsafe activation."""

        reload_calls.append(entry_id)
        return await original_async_reload(entry_id)

    hass.config_entries.async_reload = async_recording_reload
    try:
        async with async_block_home_summary_reads(hass, domain, scenario_name):
            enabled = await hass.config_entries.async_set_disabled_by(entry.entry_id, None)
            assert_result(enabled, False, f"{scenario_name} must reject unsafe activation")
            await hass.async_block_till_done()
    finally:
        hass.config_entries.async_reload = original_async_reload

    assert_result(
        entry.disabled_by,
        None,
        f"{scenario_name} must record the user's activation attempt",
    )
    assert_result(
        reload_calls,
        [entry.entry_id],
        f"{scenario_name} must attempt exactly one HASC reload",
    )
    assert_result(
        entry.state,
        config_entries.ConfigEntryState.SETUP_ERROR,
        f"{scenario_name} must leave unsafe HASC closed with a setup error",
    )
    assert_result(
        hass.services.async_services().get(domain),
        None,
        f"{scenario_name} must not register services",
    )


async def async_repair_unsafe_entry_after_rejected_activation(
    hass: HomeAssistant,
    domain: str,
    entry: ConfigEntry,
    safe_data: dict[str, str],
    safe_options: dict[str, Any],
    expected_entity_ids: frozenset[str],
    scenario_name: str,
    *,
    restore_main_data: bool,
) -> str:
    """Restore exact safe settings and explicitly start only safe HASC."""

    assert_result(
        entry.disabled_by,
        None,
        f"{scenario_name} must begin after the user's activation attempt",
    )
    assert_result(
        entry.state,
        config_entries.ConfigEntryState.SETUP_ERROR,
        f"{scenario_name} must begin with the rejected setup error",
    )
    reload_calls: list[str] = []
    original_async_reload = hass.config_entries.async_reload

    async def async_recording_reload(entry_id: str) -> bool:
        """Record the one reload requested after manual repair."""

        reload_calls.append(entry_id)
        return await original_async_reload(entry_id)

    hass.config_entries.async_reload = async_recording_reload
    try:
        async with async_block_home_summary_reads(
            hass,
            domain,
            f"{scenario_name} before explicit reload",
        ):
            if restore_main_data:
                hass.config_entries.async_update_entry(entry, data=dict(safe_data))
            else:
                hass.config_entries.async_update_entry(entry, options=dict(safe_options))
            await hass.async_block_till_done()
        reloaded = await hass.config_entries.async_reload(entry.entry_id)
        assert_result(reloaded, True, f"{scenario_name} must reload after manual repair")
        await hass.async_block_till_done()
    finally:
        hass.config_entries.async_reload = original_async_reload

    assert_result(
        reload_calls,
        [entry.entry_id],
        f"{scenario_name} must reload HASC exactly once after manual repair",
    )
    assert_result(
        dict(entry.data),
        safe_data,
        f"{scenario_name} must restore only the approved saved data",
    )
    assert_result(
        dict(entry.options),
        safe_options,
        f"{scenario_name} must restore only the approved saved options",
    )
    assert_result(
        entry.disabled_by,
        None,
        f"{scenario_name} must keep the user's activation choice",
    )
    assert_result(
        entry.state,
        config_entries.ConfigEntryState.LOADED,
        f"{scenario_name} must load only after the exact safe repair",
    )
    assert_result(
        entry.data["direct_execution_status"],
        "direct_execution_blocked",
        f"{scenario_name} must restore the direct execution block",
    )
    expected_mode = safe_options.get("mode", safe_data["mode"])
    if not isinstance(expected_mode, str):
        raise RuntimeError(f"{scenario_name} must restore a string safe mode")
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
        f"HASC repaired {scenario_name} temporary",
    )
    return await async_create_test_read_only_access_token(
        hass,
        f"HASC repaired {scenario_name} removal test user",
    )


async def async_assert_partial_main_repair_keeps_hasc_closed(
    hass: HomeAssistant,
    domain: str,
    entry: ConfigEntry,
    safe_data: dict[str, str],
    unsafe_options: dict[str, str],
    expected_entity_ids: frozenset[str],
    reader_token: str,
    scenario_name: str,
) -> None:
    """Prove correcting only one of two broken mappings cannot start HASC."""

    assert_result(
        entry.state,
        config_entries.ConfigEntryState.SETUP_ERROR,
        f"{scenario_name} must begin with the rejected setup error",
    )
    reload_calls: list[str] = []
    original_async_reload = hass.config_entries.async_reload

    async def async_recording_reload(entry_id: str) -> bool:
        """Record any unexpected reload during the incomplete repair."""

        reload_calls.append(entry_id)
        return await original_async_reload(entry_id)

    hass.config_entries.async_reload = async_recording_reload
    try:
        async with async_block_home_summary_reads(
            hass,
            domain,
            f"{scenario_name} after repairing only main data",
        ):
            hass.config_entries.async_update_entry(entry, data=dict(safe_data))
            await hass.async_block_till_done()
    finally:
        hass.config_entries.async_reload = original_async_reload

    assert_result(
        reload_calls,
        [],
        f"{scenario_name} must not reload after an incomplete repair",
    )
    assert_result(
        dict(entry.data),
        safe_data,
        f"{scenario_name} must preserve the repaired main data",
    )
    assert_result(
        dict(entry.options),
        unsafe_options,
        f"{scenario_name} must retain the remaining unsafe option",
    )
    assert_result(
        entry.disabled_by,
        None,
        f"{scenario_name} must preserve the user's activation attempt",
    )
    await async_assert_unsafe_saved_update_closes_hasc(
        hass,
        domain,
        entry,
        expected_entity_ids,
        reader_token,
        f"{scenario_name} after repairing only main data",
    )


async def async_update_safe_options(
    hass: HomeAssistant,
    entry: ConfigEntry,
    target_mode: str,
) -> None:
    """Reject unsafe options and verify the saved mode applies immediately."""

    current_local_page_enabled = entry.options.get(LOCAL_SUMMARY_ENABLED_FIELD, True)
    if type(current_local_page_enabled) is not bool:
        raise RuntimeError("safe options must retain a boolean optional local page choice")
    current_summary_update_interval = entry.options.get(
        SUMMARY_UPDATE_INTERVAL_FIELD,
        SUMMARY_UPDATE_INTERVAL_DEFAULT,
    )
    if current_summary_update_interval not in SUMMARY_UPDATE_INTERVAL_MINUTES:
        raise RuntimeError("safe options must retain an approved summary update interval")

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
    assert_options_form_uses_safe_native_selectors(options_form)
    reload_calls: list[str] = []
    original_async_reload = hass.config_entries.async_reload

    async def async_recording_reload(entry_id: str) -> bool:
        """Record the entry selected by HASC's own saved-setting listener."""

        reload_calls.append(entry_id)
        return await original_async_reload(entry_id)

    hass.config_entries.async_reload = async_recording_reload
    try:
        safe_options = await hass.config_entries.options.async_configure(
            options_form["flow_id"],
            {
                "mode": target_mode,
                LOCAL_SUMMARY_ENABLED_FIELD: current_local_page_enabled,
                SUMMARY_UPDATE_INTERVAL_FIELD: current_summary_update_interval,
            },
        )
        await hass.async_block_till_done()
    finally:
        hass.config_entries.async_reload = original_async_reload
    assert_result(
        safe_options["type"],
        "create_entry",
        f"{target_mode} must be accepted in options",
    )
    assert_result(
        entry.options.get("mode"),
        target_mode,
        "options must preserve the safe mode",
    )
    assert_result(
        entry.options.get(LOCAL_SUMMARY_ENABLED_FIELD),
        current_local_page_enabled,
        "mode settings must preserve the optional local page choice",
    )
    assert_result(
        entry.options.get(SUMMARY_UPDATE_INTERVAL_FIELD),
        current_summary_update_interval,
        "mode settings must preserve the summary update interval",
    )
    assert_result(
        entry.data["direct_execution_status"],
        "direct_execution_blocked",
        "options must not change the direct execution block",
    )
    assert_result(
        reload_calls,
        [entry.entry_id],
        "saving safe options must reload only HASC",
    )
    assert_result(
        entry.state,
        config_entries.ConfigEntryState.LOADED,
        "saving safe options must leave HASC loaded",
    )


def assert_options_form_uses_safe_native_selectors(options_form: dict[str, Any]) -> None:
    """Require the page and cadence to remain native fixed frontend selectors."""

    data_schema = options_form.get("data_schema")
    serialized_schema = voluptuous_serialize.convert(
        data_schema,
        custom_serializer=config_validation.custom_serializer,
    )
    if not isinstance(serialized_schema, list):
        raise RuntimeError("options form schema must serialize to frontend fields")
    local_page_fields = [
        field
        for field in serialized_schema
        if isinstance(field, dict)
        and field.get("name") == LOCAL_SUMMARY_ENABLED_FIELD
    ]
    if len(local_page_fields) != 1:
        raise RuntimeError("options form must serialize one optional local page field")
    assert_result(
        local_page_fields[0].get("selector"),
        {"boolean": {}},
        "optional local page must serialize as Home Assistant's native boolean selector",
    )
    interval_fields = [
        field
        for field in serialized_schema
        if isinstance(field, dict)
        and field.get("name") == SUMMARY_UPDATE_INTERVAL_FIELD
    ]
    if len(interval_fields) != 1:
        raise RuntimeError("options form must serialize one summary update interval field")
    interval_selector = interval_fields[0].get("selector")
    if not isinstance(interval_selector, dict):
        raise RuntimeError("summary update interval must serialize as a selector")
    select_config = interval_selector.get("select")
    if not isinstance(select_config, dict):
        raise RuntimeError("summary update interval must use a native select selector")
    assert_result(
        select_config.get("translation_key"),
        SUMMARY_UPDATE_INTERVAL_FIELD,
        "summary update interval selector must retain its translated fixed choices",
    )
    serialized_options = select_config.get("options")
    if not isinstance(serialized_options, list):
        raise RuntimeError("summary update interval selector must serialize fixed options")
    assert_result(
        [option.get("value") for option in serialized_options],
        list(SUMMARY_UPDATE_INTERVAL_MINUTES),
        "summary update interval selector must expose only 5, 15, and 30 minutes",
    )


async def async_update_optional_local_page(
    hass: HomeAssistant,
    domain: str,
    entry: ConfigEntry,
    enabled: bool,
    reader_token: str,
    expected_entity_ids: frozenset[str] | None,
) -> None:
    """Toggle only the existing optional page and prove the nine counts stay safe."""

    current_mode = entry.options.get("mode", entry.data["mode"])
    if not isinstance(current_mode, str):
        raise RuntimeError("the optional local page must retain a string safe mode")
    current_summary_update_interval = entry.options.get(
        SUMMARY_UPDATE_INTERVAL_FIELD,
        SUMMARY_UPDATE_INTERVAL_DEFAULT,
    )
    if current_summary_update_interval not in SUMMARY_UPDATE_INTERVAL_MINUTES:
        raise RuntimeError(
            "the optional local page must retain an approved summary update interval"
        )

    rejected_form = await hass.config_entries.options.async_init(entry.entry_id)
    assert_result(
        rejected_form["type"],
        "form",
        "optional local page setting must show a form before rejecting text",
    )
    options_before_rejection = dict(entry.options)
    try:
        await hass.config_entries.options.async_configure(
            rejected_form["flow_id"],
            {
                "mode": current_mode,
                LOCAL_SUMMARY_ENABLED_FIELD: "false",
                SUMMARY_UPDATE_INTERVAL_FIELD: current_summary_update_interval,
            },
        )
    except InvalidData:
        hass.config_entries.options.async_abort(rejected_form["flow_id"])
    else:
        raise RuntimeError("optional local page must reject a truth-like text value")
    assert_result(
        dict(entry.options),
        options_before_rejection,
        "rejected optional local page text must not change saved options",
    )

    options_form = await hass.config_entries.options.async_init(entry.entry_id)
    assert_result(
        options_form["type"],
        "form",
        "optional local page settings must show a form",
    )
    reload_calls: list[str] = []
    original_async_reload = hass.config_entries.async_reload

    async def async_recording_reload(entry_id: str) -> bool:
        """Record the one HASC-only reload caused by this local page choice."""

        reload_calls.append(entry_id)
        return await original_async_reload(entry_id)

    hass.config_entries.async_reload = async_recording_reload
    try:
        result = await hass.config_entries.options.async_configure(
            options_form["flow_id"],
            {
                "mode": current_mode,
                LOCAL_SUMMARY_ENABLED_FIELD: enabled,
                SUMMARY_UPDATE_INTERVAL_FIELD: current_summary_update_interval,
            },
        )
        await hass.async_block_till_done()
    finally:
        hass.config_entries.async_reload = original_async_reload

    assert_result(
        result["type"],
        "create_entry",
        "optional local page setting must be accepted",
    )
    assert_result(
        dict(entry.options),
        {
            "mode": current_mode,
            LOCAL_SUMMARY_ENABLED_FIELD: enabled,
            SUMMARY_UPDATE_INTERVAL_FIELD: current_summary_update_interval,
        },
        "optional local page must retain only the three approved saved options",
    )
    assert_result(
        entry.data["direct_execution_status"],
        "direct_execution_blocked",
        "optional local page must not change the direct execution block",
    )
    assert_result(
        reload_calls,
        [entry.entry_id],
        "changing the optional local page must reload only HASC",
    )
    assert_result(
        entry.state,
        config_entries.ConfigEntryState.LOADED,
        "changing the optional local page must leave HASC loaded",
    )
    assert_entry_has_only_summary_sensors(
        hass,
        domain,
        entry.entry_id,
        expected_entity_ids=expected_entity_ids,
    )
    await async_assert_safe_diagnostics(hass, domain, entry, current_mode)
    assert_entry_uses_summary_update_interval(
        hass,
        domain,
        entry.entry_id,
        current_summary_update_interval,
    )

    if enabled:
        assert_local_summary_view(hass, domain)
        await async_assert_authenticated_local_summary_http_access(
            hass,
            "HASC optional page enabled temporary",
        )
        return

    # The nine normal HASC rows intentionally remain loaded and may refresh
    # their already-approved aggregate snapshot during the reload above. This
    # separate guard proves only that a request to the now-closed extra page
    # cannot trigger another home read.
    async with async_block_home_summary_reads(
        hass,
        domain,
        "closed optional local page request",
    ):
        await async_assert_local_summary_is_unavailable(
            hass,
            domain,
            reader_token,
            "closed optional local page request",
        )


async def async_update_summary_interval(
    hass: HomeAssistant,
    domain: str,
    entry: ConfigEntry,
    target_interval: str,
    expected_entity_ids: frozenset[str] | None,
) -> None:
    """Apply one slower fixed cadence and prove it reaches all nine sensors."""

    if target_interval not in SUMMARY_UPDATE_INTERVAL_MINUTES:
        raise RuntimeError("summary interval check requires an approved target")
    current_mode = entry.options.get("mode", entry.data["mode"])
    current_local_page_enabled = entry.options.get(LOCAL_SUMMARY_ENABLED_FIELD, True)
    if not isinstance(current_mode, str) or type(current_local_page_enabled) is not bool:
        raise RuntimeError("summary interval settings must preserve safe current options")

    rejected_form = await hass.config_entries.options.async_init(entry.entry_id)
    options_before_rejection = dict(entry.options)
    try:
        await hass.config_entries.options.async_configure(
            rejected_form["flow_id"],
            {
                "mode": current_mode,
                LOCAL_SUMMARY_ENABLED_FIELD: current_local_page_enabled,
                SUMMARY_UPDATE_INTERVAL_FIELD: "1m",
            },
        )
    except InvalidData:
        hass.config_entries.options.async_abort(rejected_form["flow_id"])
    else:
        raise RuntimeError("summary interval must reject an unapproved faster choice")
    assert_result(
        dict(entry.options),
        options_before_rejection,
        "rejected summary interval must not mutate the entry",
    )

    options_form = await hass.config_entries.options.async_init(entry.entry_id)
    assert_options_form_uses_safe_native_selectors(options_form)
    reload_calls: list[str] = []
    original_async_reload = hass.config_entries.async_reload

    async def async_recording_reload(entry_id: str) -> bool:
        """Record the HASC-only reload caused by a cadence choice."""

        reload_calls.append(entry_id)
        return await original_async_reload(entry_id)

    hass.config_entries.async_reload = async_recording_reload
    try:
        result = await hass.config_entries.options.async_configure(
            options_form["flow_id"],
            {
                "mode": current_mode,
                LOCAL_SUMMARY_ENABLED_FIELD: current_local_page_enabled,
                SUMMARY_UPDATE_INTERVAL_FIELD: target_interval,
            },
        )
        await hass.async_block_till_done()
    finally:
        hass.config_entries.async_reload = original_async_reload

    assert_result(result["type"], "create_entry", "safe summary interval must save")
    assert_result(
        dict(entry.options),
        {
            "mode": current_mode,
            LOCAL_SUMMARY_ENABLED_FIELD: current_local_page_enabled,
            SUMMARY_UPDATE_INTERVAL_FIELD: target_interval,
        },
        "summary interval must retain only the three approved saved options",
    )
    assert_result(
        reload_calls,
        [entry.entry_id],
        "changing summary interval must reload only HASC",
    )
    assert_result(
        entry.state,
        config_entries.ConfigEntryState.LOADED,
        "changing summary interval must leave HASC loaded",
    )
    assert_entry_has_only_summary_sensors(
        hass,
        domain,
        entry.entry_id,
        expected_entity_ids,
    )
    assert_entry_uses_summary_update_interval(
        hass,
        domain,
        entry.entry_id,
        target_interval,
    )
    await async_assert_safe_diagnostics(hass, domain, entry, current_mode)


async def async_update_inactive_safe_options_without_reading_home(
    hass: HomeAssistant,
    domain: str,
    entry: ConfigEntry,
    target_mode: str,
    expected_disabled_by: ConfigEntryDisabler | None,
    *,
    target_local_page_enabled: bool,
    target_summary_update_interval: str,
) -> None:
    """Save allowed settings while inactive without restarting or reading the home."""

    if type(target_local_page_enabled) is not bool:
        raise RuntimeError("inactive options require a boolean optional local page choice")
    if target_summary_update_interval not in SUMMARY_UPDATE_INTERVAL_MINUTES:
        raise RuntimeError("inactive options require an approved summary update interval")

    assert_result(
        entry.state,
        config_entries.ConfigEntryState.NOT_LOADED,
        "inactive safe options must begin with HASC not loaded",
    )
    assert_result(
        entry.disabled_by,
        expected_disabled_by,
        "inactive safe options must begin with the expected user state",
    )
    reload_calls: list[str] = []
    original_async_reload = hass.config_entries.async_reload

    async def async_recording_reload(entry_id: str) -> bool:
        """Record any unexpected attempt to restart inactive HASC."""

        reload_calls.append(entry_id)
        return await original_async_reload(entry_id)

    hass.config_entries.async_reload = async_recording_reload
    try:
        async with async_block_home_summary_reads(hass, domain, "inactive safe options"):
            options_form = await hass.config_entries.options.async_init(entry.entry_id)
            assert_result(options_form["type"], "form", "inactive options must show a form")
            assert_options_form_uses_safe_native_selectors(options_form)
            safe_options = await hass.config_entries.options.async_configure(
                options_form["flow_id"],
                {
                    "mode": target_mode,
                    LOCAL_SUMMARY_ENABLED_FIELD: target_local_page_enabled,
                    SUMMARY_UPDATE_INTERVAL_FIELD: target_summary_update_interval,
                },
            )
            await hass.async_block_till_done()
    finally:
        hass.config_entries.async_reload = original_async_reload

    assert_result(
        safe_options["type"],
        "create_entry",
        f"inactive HASC must accept {target_mode} options",
    )
    assert_result(
        entry.options.get("mode"),
        target_mode,
        "inactive safe options must preserve the selected mode",
    )
    assert_result(
        dict(entry.options),
        {
            "mode": target_mode,
            LOCAL_SUMMARY_ENABLED_FIELD: target_local_page_enabled,
            SUMMARY_UPDATE_INTERVAL_FIELD: target_summary_update_interval,
        },
        "inactive safe options must keep the exact safe option shape",
    )
    assert_result(
        entry.data["direct_execution_status"],
        "direct_execution_blocked",
        "inactive safe options must keep direct execution blocked",
    )
    assert_result(
        entry.disabled_by,
        expected_disabled_by,
        "inactive safe options must preserve the user state",
    )
    assert_result(
        reload_calls,
        [],
        "inactive safe options must not reload HASC",
    )
    assert_result(
        entry.state,
        config_entries.ConfigEntryState.NOT_LOADED,
        "inactive safe options must leave HASC not loaded",
    )


async def async_assert_broken_options_form_defaults_to_read_only(
    hass: HomeAssistant,
    entry: ConfigEntry,
    scenario_name: str,
) -> None:
    """Keep a damaged saved setup from displaying shadow as its choice."""

    saved_data = dict(entry.data)
    saved_options = dict(entry.options)
    options_form = await hass.config_entries.options.async_init(entry.entry_id)
    assert_result(
        options_form["type"],
        "form",
        f"{scenario_name} options form must still open for manual repair",
    )
    schema = options_form.get("data_schema")
    schema_fields = getattr(schema, "schema", None)
    if not isinstance(schema_fields, dict) or len(schema_fields) != 3:
        raise RuntimeError(f"{scenario_name} options form must expose only three safe settings")
    mode_field, local_page_field, summary_interval_field = schema_fields
    mode_default_factory = getattr(mode_field, "default", None)
    local_page_default_factory = getattr(local_page_field, "default", None)
    summary_interval_default_factory = getattr(summary_interval_field, "default", None)
    if (
        not callable(mode_default_factory)
        or not callable(local_page_default_factory)
        or not callable(summary_interval_default_factory)
    ):
        raise RuntimeError(f"{scenario_name} options form must provide safe defaults")
    assert_result(
        mode_default_factory(),
        "read-only",
        f"{scenario_name} options form must default to read-only",
    )
    assert_result(
        local_page_default_factory(),
        True,
        f"{scenario_name} options form must default to an enabled optional local page",
    )
    assert_result(
        summary_interval_default_factory(),
        SUMMARY_UPDATE_INTERVAL_DEFAULT,
        f"{scenario_name} options form must default to the established five minutes",
    )
    hass.config_entries.options.async_abort(options_form["flow_id"])
    await hass.async_block_till_done()
    assert_result(
        dict(entry.data),
        saved_data,
        f"opening {scenario_name} options must not repair saved data",
    )
    assert_result(
        dict(entry.options),
        saved_options,
        f"opening {scenario_name} options must not repair saved options",
    )


async def async_assert_safe_diagnostics(
    hass: HomeAssistant,
    domain: str,
    entry: ConfigEntry,
    expected_mode: str,
) -> None:
    """Load diagnostics and verify its fixed safe shape with aggregate counts."""

    expected_local_summary_enabled = entry.options.get(
        LOCAL_SUMMARY_ENABLED_FIELD,
        True,
    )
    if type(expected_local_summary_enabled) is not bool:
        raise RuntimeError("diagnostics test entry has an invalid local page setting")
    expected_summary_update_interval = entry.options.get(
        SUMMARY_UPDATE_INTERVAL_FIELD,
        SUMMARY_UPDATE_INTERVAL_DEFAULT,
    )
    if expected_summary_update_interval not in SUMMARY_UPDATE_INTERVAL_MINUTES:
        raise RuntimeError("diagnostics test entry has an invalid refresh interval")

    integration = await loader.async_get_integration(hass, domain)
    diagnostics_platform = await integration.async_get_platform("diagnostics")
    snapshot = await diagnostics_platform.async_get_config_entry_diagnostics(hass, entry)

    home_summary = snapshot.pop("home_summary", None)
    assert_result(
        snapshot,
        {
            "entry_summary": {
                "mode": expected_mode,
                "local_summary_enabled": expected_local_summary_enabled,
                "summary_update_interval": expected_summary_update_interval,
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


async def async_assert_closed_diagnostics(
    hass: HomeAssistant,
    domain: str,
    entry: ConfigEntry,
    unavailable_after: str,
) -> None:
    """Require an inactive or ambiguous setup to read no home data at all."""

    integration = await loader.async_get_integration(hass, domain)
    diagnostics_platform = await integration.async_get_platform("diagnostics")
    original_collect_home_summary = diagnostics_platform.collect_home_summary

    def fail_if_home_is_read(*_: object, **__: object) -> object:
        raise RuntimeError("closed diagnostics must not read the home summary")

    diagnostics_platform.collect_home_summary = fail_if_home_is_read
    try:
        snapshot = await diagnostics_platform.async_get_config_entry_diagnostics(hass, entry)
    finally:
        diagnostics_platform.collect_home_summary = original_collect_home_summary

    assert_result(
        snapshot,
        {"diagnostics_status": "unavailable"},
        f"diagnostics must stay closed after {unavailable_after}",
    )


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


def assert_local_summary_response_is_not_stored(response: Any, response_name: str) -> None:
    """Require HASC's own local-page response to prevent browser caching."""

    headers = getattr(response, "headers", None)
    if headers is None:
        raise RuntimeError(f"{response_name} must include HTTP response headers")
    assert_result(
        headers.get("Cache-Control"),
        "no-store",
        f"{response_name} must not be stored by the browser",
    )


async def async_assert_http_response_omits_summary_keys(
    response: Any,
    response_name: str,
) -> None:
    """Require a closed HTTP response to omit every approved count name."""

    response_body = await response.read()
    if any(key.encode() in response_body for key in SUMMARY_SENSOR_KEYS):
        raise RuntimeError(f"{response_name} must not return count keys")


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
        summary_key = entry.unique_id.removeprefix(f"{entry_id}_")
        expected_icon = SUMMARY_SENSOR_ICONS.get(summary_key)
        if expected_icon is None:
            raise RuntimeError("a HASC summary sensor must have an approved icon key")
        assert_result(
            state.attributes.get("icon"),
            expected_icon,
            "a HASC summary sensor must keep its fixed visual icon",
        )
        try:
            value = int(state.state)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("every HASC summary sensor must be a whole number") from exc
        if value < 0:
            raise RuntimeError("every HASC summary sensor must be non-negative")


def assert_entry_uses_summary_update_interval(
    hass: HomeAssistant,
    domain: str,
    entry_id: str,
    expected_interval: str,
) -> None:
    """Require every live HASC count sensor to share the selected cadence."""

    expected_minutes = SUMMARY_UPDATE_INTERVAL_MINUTES.get(expected_interval)
    if expected_minutes is None:
        raise RuntimeError("summary update interval assertion requires an approved choice")
    assert_result(
        hass.services.async_services().get(domain),
        None,
        "summary interval choice must not add a HASC service",
    )
    entries = entity_registry.async_entries_for_config_entry(
        entity_registry.async_get(hass),
        entry_id,
    )
    assert_result(
        {entry.unique_id for entry in entries},
        {f"{entry_id}_{key}" for key in SUMMARY_SENSOR_KEYS},
        "summary interval check must inspect exactly the nine HASC count sensors",
    )
    entity_components = hass.data.get(DATA_INSTANCES)
    if not isinstance(entity_components, dict):
        raise RuntimeError("Home Assistant must expose loaded entity components")
    sensor_component = entity_components.get("sensor")
    if sensor_component is None:
        raise RuntimeError("Home Assistant must keep its loaded sensor component")

    live_entities = [sensor_component.get_entity(entry.entity_id) for entry in entries]
    if any(entity is None for entity in live_entities):
        raise RuntimeError("every active HASC registry row must have a live sensor entity")
    coordinators = {id(entity.coordinator): entity.coordinator for entity in live_entities}
    assert_result(
        len(coordinators),
        1,
        "all nine HASC count sensors must share one read-only coordinator",
    )
    coordinator = next(iter(coordinators.values()))
    assert_result(
        coordinator.update_interval,
        timedelta(minutes=expected_minutes),
        "HASC coordinator must use the selected fixed summary interval",
    )


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
        getattr(view, "url", None),
        LOCAL_SUMMARY_PATH,
        "local summary view must keep its one fixed URL",
    )
    if tuple(getattr(view, "extra_urls", ())) != ():
        raise RuntimeError("local summary view must not define alternate URLs")
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
    if methods != {"GET", "OPTIONS"}:
        raise RuntimeError(
            "local summary route must register GET and Home Assistant's safe OPTIONS only, "
            f"got {methods!r}"
        )


def assert_local_summary_is_not_registered(
    hass: HomeAssistant,
    domain: str,
    scenario_name: str,
) -> None:
    """Require a closed saved choice to leave no page runtime or route."""

    if hass.data.get(domain) is not None:
        raise RuntimeError(f"{scenario_name} must not keep local summary runtime data")
    if find_local_summary_routes(hass):
        raise RuntimeError(f"{scenario_name} must not register a local summary route")


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
    expected_summary_update_interval = expected_options.get(
        SUMMARY_UPDATE_INTERVAL_FIELD,
        SUMMARY_UPDATE_INTERVAL_DEFAULT,
    )
    if expected_summary_update_interval not in SUMMARY_UPDATE_INTERVAL_MINUTES:
        raise RuntimeError(
            "ordinary unload restart must retain an approved summary update interval"
        )
    assert_entry_uses_summary_update_interval(
        hass,
        domain,
        entry.entry_id,
        expected_summary_update_interval,
    )
    local_page_enabled = expected_options.get(LOCAL_SUMMARY_ENABLED_FIELD, True)
    if type(local_page_enabled) is not bool:
        raise RuntimeError("ordinary unload restart must retain a boolean local page choice")
    if local_page_enabled:
        assert_local_summary_view(hass, domain)
        await async_assert_authenticated_local_summary_http_access(
            hass,
            "HASC ordinary-unload restart temporary",
        )
    else:
        assert_local_summary_is_not_registered(
            hass,
            domain,
            "ordinary unload restart with its optional local page closed",
        )
    return entry


async def async_assert_ordinary_unloaded_entry_can_be_removed(
    hass: HomeAssistant,
    domain: str,
    entry: ConfigEntry,
    expected_data: dict[str, str],
    expected_options: dict[str, Any],
    expected_entity_ids: frozenset[str],
    reader_token: str,
) -> RemovedHascEntry:
    """Require an ordinary stopped, still-enabled HASC entry to remove cleanly."""

    await async_unload_safe_entry(hass, entry)
    assert_result(
        dict(entry.data),
        expected_data,
        "ordinary unload before removal must preserve safe entry data",
    )
    assert_result(
        dict(entry.options),
        expected_options,
        "ordinary unload before removal must preserve safe entry options",
    )
    assert_entry_has_unloaded_summary_sensors(
        hass,
        domain,
        entry.entry_id,
        expected_entity_ids,
    )
    await async_assert_closed_diagnostics(
        hass,
        domain,
        entry,
        "HASC ordinary unload before removal",
    )
    local_page_enabled = expected_options.get(LOCAL_SUMMARY_ENABLED_FIELD, True)
    if type(local_page_enabled) is not bool:
        raise RuntimeError("ordinary unload removal must retain a boolean local page choice")
    if local_page_enabled:
        await async_assert_local_summary_is_unavailable(
            hass,
            domain,
            reader_token,
            "HASC ordinary unload before removal",
        )
    else:
        assert_local_summary_is_not_registered(
            hass,
            domain,
            "ordinary unload before removal with its optional local page closed",
        )

    removed_entry = await async_remove_safe_entry(hass, entry.entry_id)
    await async_assert_closed_diagnostics(
        hass,
        domain,
        entry,
        "HASC removal after ordinary unload",
    )
    if local_page_enabled:
        await async_assert_local_summary_is_unavailable(
            hass,
            domain,
            reader_token,
            "HASC removal after ordinary unload",
        )
    else:
        assert_local_summary_is_not_registered(
            hass,
            domain,
            "removal after ordinary unload with its optional local page closed",
        )
    return removed_entry


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


def assert_persisted_duplicate_entries_stay_closed(
    hass: HomeAssistant,
    domain: str,
    first_entry_id: str,
    duplicate_entry_id: str,
    first_entry_disabled_by: ConfigEntryDisabler | None,
    expected_data: dict[str, str],
    expected_options: dict[str, Any],
    expected_stale_entity_ids: frozenset[str],
    reserved_entry: ReservedCollisionEntry,
    expect_retained_local_summary_route: bool = False,
) -> tuple[ConfigEntry, ConfigEntry]:
    """Require a saved HASC pair to expose no active HASC data."""

    entries_by_id = {
        entry.entry_id: entry for entry in hass.config_entries.async_entries(domain)
    }
    assert_result(
        set(entries_by_id),
        {first_entry_id, duplicate_entry_id},
        "a malformed saved pair must retain both HASC entries for manual repair",
    )
    first_entry = entries_by_id[first_entry_id]
    duplicate_entry = entries_by_id[duplicate_entry_id]
    assert_result(
        first_entry.disabled_by,
        first_entry_disabled_by,
        "the first HASC entry must retain its saved activation state",
    )
    assert_result(
        duplicate_entry.disabled_by,
        None,
        "the disposable duplicate must remain user-enabled for the guard check",
    )
    for configured_entry in (first_entry, duplicate_entry):
        assert_result(
            dict(configured_entry.data),
            expected_data,
            "a malformed duplicate must not change saved HASC data",
        )
        assert_result(
            dict(configured_entry.options),
            expected_options,
            "a malformed duplicate must not change saved HASC options",
        )
        if configured_entry.state is config_entries.ConfigEntryState.LOADED:
            raise RuntimeError("a duplicate saved HASC entry must not load")

    if hass.services.async_services().get(domain) is not None:
        raise RuntimeError("duplicate saved HASC entries must not restore services")
    if expect_retained_local_summary_route:
        runtime = hass.data.get(domain)
        if not isinstance(runtime, dict):
            raise RuntimeError("a live duplicate must retain its fail-closed local route")
        assert_result(
            runtime.get(LOCAL_SUMMARY_ACTIVE_ENTRY),
            None,
            "a live duplicate must clear the active local summary entry",
        )
        if len(find_local_summary_routes(hass)) != 1:
            raise RuntimeError("a live duplicate must retain exactly one closed local route")
    else:
        if hass.data.get(domain) is not None:
            raise RuntimeError("duplicate saved HASC entries must not restore runtime data")
        if find_local_summary_routes(hass):
            raise RuntimeError("duplicate saved HASC entries must not restore the local page")

    entities = entity_registry.async_get(hass)
    devices = device_registry.async_get(hass)
    for entry_id in (first_entry_id, duplicate_entry_id):
        if entity_registry.async_entries_for_config_entry(entities, entry_id):
            raise RuntimeError("duplicate saved HASC entries must not restore count records")
        if device_registry.async_entries_for_config_entry(devices, entry_id):
            raise RuntimeError("duplicate saved HASC entries must not restore devices")
    for entity_id in expected_stale_entity_ids:
        if hass.states.get(entity_id) is not None:
            raise RuntimeError("duplicate saved HASC entries must not restore count states")

    assert_reserved_collision_entry_is_unchanged(hass, reserved_entry)
    return first_entry, duplicate_entry


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


async def async_create_test_read_only_user(
    hass: HomeAssistant,
    user_name: str,
) -> Any:
    """Create one temporary exact read-only user only inside the empty Core."""

    reader = await hass.auth.async_create_user(
        user_name,
        group_ids=[GROUP_ID_READ_ONLY],
        local_only=True,
    )
    assert_result(reader.is_admin, False, "temporary reader must not be an administrator")
    return reader


async def async_create_test_read_only_access_token(
    hass: HomeAssistant,
    user_name: str,
) -> str:
    """Create a token for one temporary exact read-only user."""

    reader = await async_create_test_read_only_user(hass, user_name)
    return await async_create_test_access_token(hass, reader)


async def async_assert_approved_local_summary_origins_are_accepted(
    hass: HomeAssistant,
    reader: Any,
) -> None:
    """Prove every exact approved range boundary can read only safe totals."""

    domain = load_integration_domain()
    runtime = hass.data.get(domain)
    if not isinstance(runtime, dict):
        raise RuntimeError("the local summary runtime data must be present")
    view = runtime.get("local_summary_view")
    if view is None:
        raise RuntimeError("the local summary view must be registered")

    for remote in APPROVED_LOCAL_SUMMARY_ORIGINS:
        accepted = await view.get(DirectLocalSummaryRequest(remote, reader))
        assert_result(
            accepted.status,
            HTTPStatus.OK,
            f"local summary must accept approved origin {remote}",
        )
        assert_local_summary_response_is_not_stored(
            accepted,
            f"approved local summary origin {remote}",
        )
        assert_safe_home_summary(json.loads(accepted.body))


async def async_assert_disallowed_local_summary_origins_are_rejected(
    hass: HomeAssistant,
    reader: Any,
    test_user_prefix: str,
) -> None:
    """Prove the real view closes every non-home source before any home read."""

    domain = load_integration_domain()
    runtime = hass.data.get(domain)
    if not isinstance(runtime, dict):
        raise RuntimeError("the local summary runtime data must be present")
    view = runtime.get("local_summary_view")
    if view is None:
        raise RuntimeError("the local summary view must be registered")

    async with async_block_home_summary_reads(
        hass,
        domain,
        f"{test_user_prefix} disallowed local origin",
    ):
        for remote in DISALLOWED_LOCAL_SUMMARY_ORIGINS:
            rejected = await view.get(DirectLocalSummaryRequest(remote, reader))
            assert_result(
                rejected.status,
                HTTPStatus.FORBIDDEN,
                f"local summary must reject disallowed origin {remote}",
            )
            assert_local_summary_response_is_not_stored(
                rejected,
                f"rejected disallowed local summary origin {remote}",
            )
            payload = json.loads(rejected.body)
            if not isinstance(payload, dict):
                raise RuntimeError("rejected disallowed origin response must be a dictionary")
            if set(payload) & set(SUMMARY_SENSOR_KEYS):
                raise RuntimeError("rejected disallowed origin must not return count values")


async def async_assert_local_summary_observation_failure_is_unavailable(
    hass: HomeAssistant,
    reader: Any,
    test_user_prefix: str,
) -> None:
    """Require an unexpected local reader failure to return no summary details."""

    domain = load_integration_domain()
    runtime = hass.data.get(domain)
    if not isinstance(runtime, dict):
        raise RuntimeError("the local summary runtime data must be present")
    view = runtime.get("local_summary_view")
    if view is None:
        raise RuntimeError("the local summary view must be registered")

    async with async_block_home_summary_reads(
        hass,
        domain,
        f"{test_user_prefix} local summary observation failure",
    ):
        unavailable = await view.get(DirectLocalSummaryRequest("127.0.0.1", reader))

    assert_result(
        unavailable.status,
        HTTPStatus.SERVICE_UNAVAILABLE,
        "local summary observation failure must return unavailable",
    )
    assert_local_summary_response_is_not_stored(
        unavailable,
        "failed local summary observation",
    )
    payload = json.loads(unavailable.body)
    if not isinstance(payload, dict):
        raise RuntimeError("failed local summary response must be a dictionary")
    if payload != {"message": "The local summary is unavailable."}:
        raise RuntimeError("failed local summary response must not expose error details")
    if set(payload) & set(SUMMARY_SENSOR_KEYS):
        raise RuntimeError("failed local summary observation must not return count values")


async def async_assert_local_summary_rejects_non_get_requests(
    hass: HomeAssistant,
    client: TestClient,
    reader_token: str,
    test_user_prefix: str,
) -> None:
    """Require every non-GET request to fail before it can read the home."""

    domain = load_integration_domain()
    async with async_block_home_summary_reads(
        hass,
        domain,
        f"{test_user_prefix} non-GET local request",
    ):
        for method, expected_status in NON_GET_LOCAL_SUMMARY_STATUSES.items():
            rejected = await client.request(
                method,
                LOCAL_SUMMARY_PATH,
                headers={"Authorization": f"Bearer {reader_token}"},
            )
            assert_result(
                rejected.status,
                expected_status,
                f"local summary must reject {method}",
            )
            await async_assert_http_response_omits_summary_keys(
                rejected,
                f"local summary must reject {method}",
            )


async def async_assert_local_summary_rejects_alternate_paths(
    hass: HomeAssistant,
    client: TestClient,
    reader_token: str,
    test_user_prefix: str,
) -> None:
    """Require a small address variation to stay outside the one safe route."""

    domain = load_integration_domain()
    async with async_block_home_summary_reads(
        hass,
        domain,
        f"{test_user_prefix} alternate local summary path",
    ):
        for alternate_target, expected_status in ALTERNATE_LOCAL_SUMMARY_TARGET_STATUSES.items():
            rejected = await client.get(
                alternate_target,
                headers={"Authorization": f"Bearer {reader_token}"},
                allow_redirects=False,
            )
            assert_result(
                rejected.status,
                expected_status,
                f"local summary must reject alternate target {alternate_target}",
            )
            await async_assert_http_response_omits_summary_keys(
                rejected,
                f"local summary must reject alternate target {alternate_target}",
            )


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
    reader = await async_create_test_read_only_user(
        hass,
        f"{test_user_prefix} read-only test user",
    )
    await async_assert_approved_local_summary_origins_are_accepted(hass, reader)
    await async_assert_disallowed_local_summary_origins_are_rejected(
        hass,
        reader,
        test_user_prefix,
    )
    await async_assert_local_summary_observation_failure_is_unavailable(
        hass,
        reader,
        test_user_prefix,
    )
    reader_token = await async_create_test_access_token(hass, reader)

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
        await async_assert_http_response_omits_summary_keys(
            unauthenticated,
            "unauthenticated local summary response",
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
        assert_local_summary_response_is_not_stored(
            rejected_owner,
            "rejected local summary owner response",
        )
        await async_assert_http_response_omits_summary_keys(
            rejected_owner,
            "rejected local summary owner response",
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
        assert_local_summary_response_is_not_stored(
            accepted_reader,
            "accepted local summary response",
        )
        assert_safe_home_summary(await accepted_reader.json())

        await async_assert_local_summary_rejects_non_get_requests(
            hass,
            client,
            reader_token,
            test_user_prefix,
        )
        await async_assert_local_summary_rejects_alternate_paths(
            hass,
            client,
            reader_token,
            test_user_prefix,
        )

        await hass.auth.async_update_user(reader, group_ids=[GROUP_ID_USER])
        await hass.async_block_till_done()
        async with async_block_home_summary_reads(
            hass,
            load_integration_domain(),
            f"{test_user_prefix} demoted reader",
        ):
            rejected_demoted_reader = await client.get(
                "/api/hausman_hub/local-summary",
                headers={"Authorization": f"Bearer {reader_token}"},
            )
        assert_result(
            rejected_demoted_reader.status,
            HTTPStatus.FORBIDDEN,
            "local summary must reject a demoted read-only user",
        )
        assert_local_summary_response_is_not_stored(
            rejected_demoted_reader,
            "rejected local summary demoted-reader response",
        )
        demoted_payload = await rejected_demoted_reader.json()
        if not isinstance(demoted_payload, dict):
            raise RuntimeError("demoted local summary response must be a dictionary")
        if set(demoted_payload) & set(SUMMARY_SENSOR_KEYS):
            raise RuntimeError("demoted local summary must not return count values")
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
        assert_local_summary_response_is_not_stored(
            unavailable,
            f"unavailable local summary response after {unavailable_after}",
        )
        payload = await unavailable.json()
        if not isinstance(payload, dict):
            raise RuntimeError("unavailable local summary response must be a dictionary")
        if set(payload) & set(SUMMARY_SENSOR_KEYS):
            raise RuntimeError("unavailable local summary must not return count values")
    finally:
        await client.close()


async def async_save_unsafe_hasc_setting_without_reading_home(
    hass: HomeAssistant,
    domain: str,
    entry: ConfigEntry,
    scenario_name: str,
    *,
    data: dict[str, str] | None = None,
    options: dict[str, str] | None = None,
) -> None:
    """Save unsafe mappings while every HASC home reader fails immediately."""

    if data is None and options is None:
        raise RuntimeError("the unsafe HASC update must change a saved mapping")

    async with async_block_home_summary_reads(
        hass,
        domain,
        f"{scenario_name} automatic closure",
    ):
        if data is not None and options is not None:
            hass.config_entries.async_update_entry(entry, data=data, options=options)
        elif data is not None:
            hass.config_entries.async_update_entry(entry, data=data)
        else:
            hass.config_entries.async_update_entry(entry, options=options)
        await hass.async_block_till_done()


async def async_assert_unsafe_saved_update_closes_hasc(
    hass: HomeAssistant,
    domain: str,
    entry: ConfigEntry,
    expected_entity_ids: frozenset[str],
    reader_token: str | None,
    scenario_name: str,
    *,
    expect_retained_local_summary_route: bool = True,
) -> None:
    """Prove an unsafe saved setting leaves every HASC display closed."""

    if entry.state is config_entries.ConfigEntryState.LOADED:
        raise RuntimeError(f"{scenario_name} must close HASC automatically")
    if entity_registry.async_entries_for_config_entry(
        entity_registry.async_get(hass),
        entry.entry_id,
    ):
        raise RuntimeError(f"{scenario_name} must clear entity registry records automatically")
    for entity_id in expected_entity_ids:
        if hass.states.get(entity_id) is not None:
            raise RuntimeError(f"{scenario_name} must clear count states automatically")
    await async_assert_closed_diagnostics(hass, domain, entry, scenario_name)
    if expect_retained_local_summary_route:
        if reader_token is None:
            raise RuntimeError(
                f"{scenario_name} must keep a reader token for its closed route"
            )
        await async_assert_local_summary_is_unavailable(
            hass,
            domain,
            reader_token,
            scenario_name,
        )
    else:
        if hass.data.get(domain) is not None:
            raise RuntimeError(f"{scenario_name} must not restore HASC runtime data")
        if find_local_summary_routes(hass):
            raise RuntimeError(f"{scenario_name} must not restore the local summary route")


async def async_assert_stale_local_summary_pointer_is_unavailable_without_reading(
    hass: HomeAssistant,
    domain: str,
    entry: ConfigEntry,
    reader_token: str,
    unavailable_after: str,
) -> None:
    """Require a stale local page pointer to close before reading the home."""

    runtime = hass.data.get(domain)
    if not isinstance(runtime, dict):
        raise RuntimeError("the retained local summary route must keep its runtime data")
    assert_result(
        entry.state,
        config_entries.ConfigEntryState.NOT_LOADED,
        f"{unavailable_after} must keep HASC unloaded",
    )
    assert_result(
        runtime.get(LOCAL_SUMMARY_ACTIVE_ENTRY),
        None,
        f"{unavailable_after} must start with no active local summary entry",
    )
    if len(find_local_summary_routes(hass)) != 1:
        raise RuntimeError("the retained local summary route must remain unique")

    runtime[LOCAL_SUMMARY_ACTIVE_ENTRY] = entry
    integration = await loader.async_get_integration(hass, domain)
    local_summary_platform = await integration.async_get_platform("local_summary")
    original_collect_home_summary = local_summary_platform.collect_home_summary

    def fail_if_home_is_read(*_: object, **__: object) -> object:
        raise RuntimeError("a stale local summary pointer must not read the home")

    local_summary_platform.collect_home_summary = fail_if_home_is_read
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
        if runtime.get(LOCAL_SUMMARY_ACTIVE_ENTRY) is entry:
            runtime.pop(LOCAL_SUMMARY_ACTIVE_ENTRY, None)
        local_summary_platform.collect_home_summary = original_collect_home_summary
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


async def async_assert_persisted_duplicate_entry_lifecycle(
    config_directory: Path,
    domain: str,
    previous_removed_entries: tuple[RemovedHascEntry, ...],
    reserved_entry: ReservedCollisionEntry,
    first_entry_is_user_disabled: bool,
) -> tuple[RemovedHascEntry, RemovedHascEntry]:
    """Prove a saved HASC pair stays closed until one record is removed.

    The check runs both relevant saved states: an enabled pair, where the
    remaining entry requires an explicit reload after repair, and a pair with
    one user-deactivated entry, where it requires an explicit activation.
    """

    seed_hass = await async_start_empty_home_assistant(config_directory)
    first_entry_id: str | None = None
    duplicate_entry_id: str | None = None
    expected_data: dict[str, str] | None = None
    expected_options: dict[str, Any] | None = None
    expected_stale_entity_ids: frozenset[str] | None = None
    expected_first_disabled_by = (
        ConfigEntryDisabler.USER if first_entry_is_user_disabled else None
    )
    scenario_name = (
        "user-deactivated first entry" if first_entry_is_user_disabled else "enabled pair"
    )
    try:
        assert_hasc_stays_removed_after_restart(
            seed_hass,
            domain,
            previous_removed_entries,
            reserved_entry,
        )
        first_entry = await async_create_safe_entry(seed_hass, domain, "read-only")
        assert_entry_has_only_summary_sensors(
            seed_hass,
            domain,
            first_entry.entry_id,
            expected_entity_ids=None,
        )
        assert_reserved_name_does_not_block_hasc(
            seed_hass,
            first_entry.entry_id,
            reserved_entry,
        )
        if first_entry_is_user_disabled:
            await async_disable_safe_entry(seed_hass, first_entry)
            assert_entry_has_disabled_summary_sensors(
                seed_hass,
                domain,
                first_entry.entry_id,
                expected_entity_ids=None,
            )
        first_entry_id = first_entry.entry_id
        expected_data = dict(first_entry.data)
        expected_options = dict(first_entry.options)
        expected_stale_entity_ids = frozenset(
            registry_entry.entity_id
            for registry_entry in entity_registry.async_entries_for_config_entry(
                entity_registry.async_get(seed_hass),
                first_entry.entry_id,
            )
        )
        assert_result(
            len(expected_stale_entity_ids),
            len(SUMMARY_SENSOR_KEYS),
            "the duplicate fixture must start with nine count records",
        )
        duplicate_entry = await async_add_disposable_persisted_duplicate_entry(
            seed_hass,
            first_entry,
        )
        duplicate_entry_id = duplicate_entry.entry_id
        closed_first_entry, _ = assert_persisted_duplicate_entries_stay_closed(
            seed_hass,
            domain,
            first_entry_id,
            duplicate_entry_id,
            expected_first_disabled_by,
            expected_data,
            expected_options,
            expected_stale_entity_ids,
            reserved_entry,
            expect_retained_local_summary_route=True,
        )
        if not first_entry_is_user_disabled:
            assert_result(
                closed_first_entry.state,
                config_entries.ConfigEntryState.NOT_LOADED,
                "adding a duplicate must unload the remaining enabled HASC",
            )
        await async_assert_closed_diagnostics(
            seed_hass,
            domain,
            closed_first_entry,
            f"adding a duplicate with {scenario_name}",
        )
        await async_assert_closed_diagnostics(
            seed_hass,
            domain,
            duplicate_entry,
            f"adding a duplicate with {scenario_name}",
        )
        duplicate_reader_token = await async_create_test_read_only_access_token(
            seed_hass,
            f"HASC live duplicate {scenario_name} test user",
        )
        await async_assert_local_summary_is_unavailable(
            seed_hass,
            domain,
            duplicate_reader_token,
            "adding a duplicate saved HASC entry",
        )
    finally:
        await seed_hass.async_stop()

    if (
        first_entry_id is None
        or duplicate_entry_id is None
        or expected_data is None
        or expected_options is None
        or expected_stale_entity_ids is None
    ):
        raise RuntimeError("the duplicate lifecycle must create both temporary entries")

    duplicate_hass = await async_start_empty_home_assistant(config_directory)
    duplicate_removal: RemovedHascEntry | None = None
    recovered_removal: RemovedHascEntry | None = None
    try:
        first_entry, duplicate_entry = assert_persisted_duplicate_entries_stay_closed(
            duplicate_hass,
            domain,
            first_entry_id,
            duplicate_entry_id,
            expected_first_disabled_by,
            expected_data,
            expected_options,
            expected_stale_entity_ids,
            reserved_entry,
        )
        await async_assert_closed_diagnostics(
            duplicate_hass,
            domain,
            first_entry,
            f"restarting a duplicate with {scenario_name}",
        )
        await async_assert_closed_diagnostics(
            duplicate_hass,
            domain,
            duplicate_entry,
            f"restarting a duplicate with {scenario_name}",
        )
        duplicate_removal = await async_remove_safe_entry(
            duplicate_hass,
            duplicate_entry.entry_id,
        )
        assert_result(
            [entry.entry_id for entry in duplicate_hass.config_entries.async_entries(domain)],
            [first_entry.entry_id],
            "removing the duplicate must retain only the original HASC entry",
        )
        if first_entry_is_user_disabled:
            await async_enable_safe_entry(duplicate_hass, first_entry)
        else:
            if first_entry.state is config_entries.ConfigEntryState.LOADED:
                raise RuntimeError(
                    "removing a duplicate must not automatically load the remaining HASC"
                )
            reloaded = await duplicate_hass.config_entries.async_reload(first_entry.entry_id)
            assert_result(
                reloaded,
                True,
                "the remaining enabled HASC entry must reload after duplicate removal",
            )
            await duplicate_hass.async_block_till_done()
            assert_result(
                first_entry.state,
                config_entries.ConfigEntryState.LOADED,
                "an explicitly reloaded remaining HASC entry must load successfully",
            )
        assert_entry_has_only_summary_sensors(
            duplicate_hass,
            domain,
            first_entry.entry_id,
            expected_entity_ids=None,
        )
        assert_reserved_name_does_not_block_hasc(
            duplicate_hass,
            first_entry.entry_id,
            reserved_entry,
        )
        await async_assert_safe_diagnostics(
            duplicate_hass,
            domain,
            first_entry,
            "read-only",
        )
        assert_local_summary_view(duplicate_hass, domain)
        await async_assert_authenticated_local_summary_http_access(
            duplicate_hass,
            f"HASC corrected {scenario_name} temporary",
        )
        recovery_reader_token = await async_create_test_read_only_access_token(
            duplicate_hass,
            f"HASC corrected {scenario_name} removal test user",
        )
        recovered_removal = await async_remove_safe_entry(
            duplicate_hass,
            first_entry.entry_id,
        )
        await async_assert_closed_diagnostics(
            duplicate_hass,
            domain,
            first_entry,
            f"removing the corrected {scenario_name}",
        )
        await async_assert_local_summary_is_unavailable(
            duplicate_hass,
            domain,
            recovery_reader_token,
            f"corrected {scenario_name} removal",
        )
        assert_reserved_collision_entry_is_unchanged(duplicate_hass, reserved_entry)
    finally:
        await duplicate_hass.async_stop()

    if duplicate_removal is None or recovered_removal is None:
        raise RuntimeError("the duplicate lifecycle must remove both temporary entries")

    final_hass = await async_start_empty_home_assistant(config_directory)
    try:
        assert_hasc_stays_removed_after_restart(
            final_hass,
            domain,
            (*previous_removed_entries, duplicate_removal, recovered_removal),
            reserved_entry,
        )
    finally:
        await final_hass.async_stop()

    return duplicate_removal, recovered_removal


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
        await async_save_unsafe_hasc_setting_without_reading_home(
            invalid_data_hass,
            domain,
            invalid_entry,
            f"{scenario_name} saved main settings",
            data=saved_unsafe_data,
        )
        assert_result(
            dict(invalid_entry.data),
            saved_unsafe_data,
            "the temporary unsafe HASC data must persist",
        )
        await async_assert_broken_options_form_defaults_to_read_only(
            invalid_data_hass,
            invalid_entry,
            f"{scenario_name} saved main settings",
        )
        await async_assert_unsafe_saved_update_closes_hasc(
            invalid_data_hass,
            domain,
            invalid_entry,
            invalid_entry_entity_ids,
            invalid_reader_token,
            f"{scenario_name} saved main settings",
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
        await async_save_unsafe_hasc_setting_without_reading_home(
            invalid_options_hass,
            domain,
            invalid_options_entry,
            f"{scenario_name} saved options",
            options=saved_unsafe_options,
        )
        assert_result(
            dict(invalid_options_entry.options),
            saved_unsafe_options,
            "the temporary unsafe HASC options must persist",
        )
        await async_assert_broken_options_form_defaults_to_read_only(
            invalid_options_hass,
            invalid_options_entry,
            f"{scenario_name} saved options",
        )
        await async_assert_unsafe_saved_update_closes_hasc(
            invalid_options_hass,
            domain,
            invalid_options_entry,
            invalid_options_entity_ids,
            invalid_options_reader_token,
            f"{scenario_name} saved options",
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


async def async_assert_user_deactivated_unsafe_settings_cannot_enable_lifecycle(
    config_directory: Path,
    domain: str,
    previous_removed_entries: tuple[RemovedHascEntry, ...],
    reserved_entry: ReservedCollisionEntry,
    *,
    unsafe_data: dict[str, str] | None = None,
    unsafe_options: dict[str, str] | None = None,
    scenario_name: str,
    restart_before_activation: bool = False,
    repair_after_rejected_activation: bool = False,
    partial_main_repair_before_options: bool = False,
    reclose_after_recovery: bool = False,
    repair_after_repeat_closure: bool = False,
    restart_after_repeat_repair: bool = False,
    reclose_after_repeat_repair_restart: bool = False,
) -> RemovedHascEntry:
    """Prove manual activation cannot bypass unsafe saved HASC settings."""

    if unsafe_data is None and unsafe_options is None:
        raise RuntimeError("unsafe activation must change a saved mapping")
    if (
        unsafe_data is not None
        and unsafe_options is not None
        and not partial_main_repair_before_options
    ):
        raise RuntimeError("two unsafe mappings require a partial main repair")
    if partial_main_repair_before_options and (
        not repair_after_rejected_activation
        or unsafe_data is None
        or unsafe_options is None
        or restart_before_activation
    ):
        raise RuntimeError(
            "partial main repair requires unsafe data, unsafe options, final recovery, and no restart"
        )
    if partial_main_repair_before_options and any(
        (
            reclose_after_recovery,
            repair_after_repeat_closure,
            restart_after_repeat_repair,
            reclose_after_repeat_repair_restart,
        )
    ):
        raise RuntimeError("partial main repair does not support repeated recovery")
    if reclose_after_recovery and not repair_after_rejected_activation:
        raise RuntimeError("repeat closure requires a completed safe recovery")
    if repair_after_repeat_closure and not reclose_after_recovery:
        raise RuntimeError("repeat repair requires a completed repeat closure")
    if restart_after_repeat_repair and not repair_after_repeat_closure:
        raise RuntimeError("repeat repair restart requires a completed repeat repair")
    if reclose_after_repeat_repair_restart and not restart_after_repeat_repair:
        raise RuntimeError("restart repeat closure requires a completed repeat repair restart")

    unsafe_hass = await async_start_empty_home_assistant(config_directory)
    activation_hass: HomeAssistant | None = None
    unsafe_hass_stopped = False
    removed_entry: RemovedHascEntry | None = None
    try:
        assert_hasc_stays_removed_after_restart(
            unsafe_hass,
            domain,
            previous_removed_entries,
            reserved_entry,
        )
        unsafe_entry = await async_create_safe_entry(unsafe_hass, domain, "read-only")
        unsafe_entry_entity_ids = frozenset(
            entry.entity_id
            for entry in entity_registry.async_entries_for_config_entry(
                entity_registry.async_get(unsafe_hass),
                unsafe_entry.entry_id,
            )
        )
        assert_result(
            len(unsafe_entry_entity_ids),
            len(SUMMARY_SENSOR_KEYS),
            "the unsafe-activation fixture must begin with nine count sensors",
        )
        unsafe_reader_token = await async_create_test_read_only_access_token(
            unsafe_hass,
            f"HASC {scenario_name} activation test user",
        )
        await async_disable_safe_entry(unsafe_hass, unsafe_entry)
        assert_entry_has_disabled_summary_sensors(
            unsafe_hass,
            domain,
            unsafe_entry.entry_id,
            unsafe_entry_entity_ids,
        )
        await async_assert_closed_diagnostics(
            unsafe_hass,
            domain,
            unsafe_entry,
            f"{scenario_name} before user activation",
        )
        await async_assert_local_summary_is_unavailable(
            unsafe_hass,
            domain,
            unsafe_reader_token,
            f"{scenario_name} before user activation",
        )

        safe_data = dict(unsafe_entry.data)
        safe_options = dict(unsafe_entry.options)
        expected_data = dict(unsafe_data) if unsafe_data is not None else dict(safe_data)
        expected_options = (
            dict(unsafe_options) if unsafe_options is not None else dict(safe_options)
        )
        await async_save_unsafe_hasc_setting_without_reading_home(
            unsafe_hass,
            domain,
            unsafe_entry,
            f"{scenario_name} before user activation",
            data=unsafe_data,
            options=unsafe_options,
        )
        assert_result(
            dict(unsafe_entry.data),
            expected_data,
            "unsafe saved data must remain available for manual repair",
        )
        assert_result(
            dict(unsafe_entry.options),
            expected_options,
            "unsafe saved options must remain available for manual repair",
        )
        assert_result(
            unsafe_entry.disabled_by,
            ConfigEntryDisabler.USER,
            "saving unsafe settings must keep HASC user-disabled",
        )
        assert_result(
            unsafe_entry.state,
            config_entries.ConfigEntryState.NOT_LOADED,
            "saving unsafe settings must keep disabled HASC not loaded",
        )
        assert_entry_has_disabled_summary_sensors(
            unsafe_hass,
            domain,
            unsafe_entry.entry_id,
            unsafe_entry_entity_ids,
        )
        await async_assert_closed_diagnostics(
            unsafe_hass,
            domain,
            unsafe_entry,
            f"{scenario_name} before user activation",
        )
        await async_assert_local_summary_is_unavailable(
            unsafe_hass,
            domain,
            unsafe_reader_token,
            f"{scenario_name} before user activation",
        )

        activation_entry = unsafe_entry
        activation_reader_token: str | None = unsafe_reader_token
        expect_retained_local_summary_route = True
        if restart_before_activation:
            await unsafe_hass.async_stop()
            unsafe_hass_stopped = True
            activation_hass = await async_start_empty_home_assistant(config_directory)
            activation_entry = activation_hass.config_entries.async_get_entry(
                unsafe_entry.entry_id
            )
            if activation_entry is None:
                raise RuntimeError("the restarted unsafe HASC entry must remain repairable")
            assert_result(
                dict(activation_entry.data),
                expected_data,
                "restart must preserve unsafe activation data for manual repair",
            )
            assert_result(
                dict(activation_entry.options),
                expected_options,
                "restart must preserve unsafe activation options for manual repair",
            )
            assert_deactivated_entry_stays_inactive_after_restart(
                activation_hass,
                domain,
                activation_entry,
                unsafe_entry_entity_ids,
            )
            await async_assert_closed_diagnostics(
                activation_hass,
                domain,
                activation_entry,
                f"{scenario_name} after restart before user activation",
            )
            assert_reserved_collision_entry_is_unchanged(activation_hass, reserved_entry)
            activation_reader_token = None
            expect_retained_local_summary_route = False
        else:
            activation_hass = unsafe_hass

        if activation_hass is None:
            raise RuntimeError(
                "unsafe activation lifecycle must keep a Home Assistant instance"
            )
        await async_enable_unsafe_entry_without_reading_home(
            activation_hass,
            domain,
            activation_entry,
            f"user activation with {scenario_name}",
        )
        assert_result(
            dict(activation_entry.data),
            expected_data,
            "unsafe activation must leave damaged data for manual repair",
        )
        assert_result(
            dict(activation_entry.options),
            expected_options,
            "unsafe activation must leave manual repair possible",
        )
        await async_assert_unsafe_saved_update_closes_hasc(
            activation_hass,
            domain,
            activation_entry,
            unsafe_entry_entity_ids,
            activation_reader_token,
            f"user activation with {scenario_name}",
            expect_retained_local_summary_route=expect_retained_local_summary_route,
        )
        if partial_main_repair_before_options:
            if activation_reader_token is None:
                raise RuntimeError(
                    "partial repair requires the retained local summary reader token"
                )
            await async_assert_partial_main_repair_keeps_hasc_closed(
                activation_hass,
                domain,
                activation_entry,
                safe_data,
                expected_options,
                unsafe_entry_entity_ids,
                activation_reader_token,
                scenario_name,
            )
        if repair_after_rejected_activation:
            activation_reader_token = (
                await async_repair_unsafe_entry_after_rejected_activation(
                    activation_hass,
                    domain,
                    activation_entry,
                    safe_data,
                    safe_options,
                    unsafe_entry_entity_ids,
                    scenario_name,
                    restore_main_data=(
                        unsafe_data is not None
                        and not partial_main_repair_before_options
                    ),
                )
            )
            expect_retained_local_summary_route = True
            assert_reserved_collision_entry_is_unchanged(activation_hass, reserved_entry)
        if reclose_after_recovery:
            if activation_reader_token is None:
                raise RuntimeError(
                    "recovered HASC must keep a reader token before repeat closure"
                )
            await async_save_unsafe_hasc_setting_without_reading_home(
                activation_hass,
                domain,
                activation_entry,
                f"{scenario_name} after recovery",
                data=unsafe_data,
                options=unsafe_options,
            )
            assert_result(
                dict(activation_entry.data),
                expected_data,
                "repeated unsafe data must remain available for manual repair",
            )
            assert_result(
                dict(activation_entry.options),
                expected_options,
                "repeated unsafe options must remain available for manual repair",
            )
            await async_assert_unsafe_saved_update_closes_hasc(
                activation_hass,
                domain,
                activation_entry,
                unsafe_entry_entity_ids,
                activation_reader_token,
                f"{scenario_name} after recovery",
            )
            assert_reserved_collision_entry_is_unchanged(activation_hass, reserved_entry)
        if repair_after_repeat_closure:
            activation_reader_token = (
                await async_repair_unsafe_entry_after_rejected_activation(
                    activation_hass,
                    domain,
                    activation_entry,
                    safe_data,
                    safe_options,
                    unsafe_entry_entity_ids,
                    f"{scenario_name} after repeat closure",
                    restore_main_data=unsafe_data is not None,
                )
            )
            expect_retained_local_summary_route = True
            assert_reserved_collision_entry_is_unchanged(activation_hass, reserved_entry)
        if restart_after_repeat_repair:
            await activation_hass.async_stop()
            if activation_hass is unsafe_hass:
                unsafe_hass_stopped = True
            activation_hass = await async_start_empty_home_assistant(config_directory)
            activation_entry = await async_assert_corrected_entry_stays_safe_after_restart(
                activation_hass,
                domain,
                activation_entry.entry_id,
                safe_data,
                safe_options,
                unsafe_entry_entity_ids,
                reserved_entry,
            )
            activation_reader_token = await async_create_test_read_only_access_token(
                activation_hass,
                f"HASC {scenario_name} repeat-repair restart removal test user",
            )
            expect_retained_local_summary_route = True
        if reclose_after_repeat_repair_restart:
            if activation_reader_token is None:
                raise RuntimeError(
                    "restarted HASC must keep a reader token before repeat closure"
                )
            await async_save_unsafe_hasc_setting_without_reading_home(
                activation_hass,
                domain,
                activation_entry,
                f"{scenario_name} after repeat-repair restart",
                data=unsafe_data,
                options=unsafe_options,
            )
            assert_result(
                dict(activation_entry.data),
                expected_data,
                "restart repeat unsafe data must remain available for manual repair",
            )
            assert_result(
                dict(activation_entry.options),
                expected_options,
                "restart repeat unsafe options must remain available for manual repair",
            )
            await async_assert_unsafe_saved_update_closes_hasc(
                activation_hass,
                domain,
                activation_entry,
                unsafe_entry_entity_ids,
                activation_reader_token,
                f"{scenario_name} after repeat-repair restart",
            )
            assert_reserved_collision_entry_is_unchanged(activation_hass, reserved_entry)
        removed_entry = await async_remove_safe_entry(
            activation_hass,
            activation_entry.entry_id,
        )
        await async_assert_closed_diagnostics(
            activation_hass,
            domain,
            activation_entry,
            f"removing {scenario_name} activation fixture",
        )
        if expect_retained_local_summary_route:
            if activation_reader_token is None:
                raise RuntimeError("removed unsafe activation must keep a reader token")
            await async_assert_local_summary_is_unavailable(
                activation_hass,
                domain,
                activation_reader_token,
                f"removing {scenario_name} activation fixture",
            )
        else:
            if activation_hass.data.get(domain) is not None:
                raise RuntimeError("removed unsafe activation must not restore HASC runtime data")
            if find_local_summary_routes(activation_hass):
                raise RuntimeError("removed unsafe activation must not restore local summary route")
        assert_reserved_collision_entry_is_unchanged(activation_hass, reserved_entry)
    finally:
        if activation_hass is not None and activation_hass is not unsafe_hass:
            await activation_hass.async_stop()
        if not unsafe_hass_stopped:
            await unsafe_hass.async_stop()

    if removed_entry is None:
        raise RuntimeError(f"the {scenario_name} activation fixture must remove its HASC entry")

    removal_hass = await async_start_empty_home_assistant(config_directory)
    try:
        assert_hasc_stays_removed_after_restart(
            removal_hass,
            domain,
            (*previous_removed_entries, removed_entry),
            reserved_entry,
        )
    finally:
        await removal_hass.async_stop()

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
            assert_entry_uses_summary_update_interval(
                hass,
                domain,
                read_only_entry.entry_id,
                SUMMARY_UPDATE_INTERVAL_DEFAULT,
            )
            legacy_default_entry_id = read_only_entry.entry_id
            legacy_default_entry_data = dict(read_only_entry.data)
            legacy_default_entry_options = dict(read_only_entry.options)
            assert_result(
                legacy_default_entry_options,
                {},
                "a legacy HASC entry must begin without the new interval option",
            )
            await hass.async_stop()
            hass = await async_start_empty_home_assistant(config_directory)
            read_only_entry = hass.config_entries.async_get_entry(legacy_default_entry_id)
            if read_only_entry is None:
                raise RuntimeError("legacy default interval entry must survive a restart")
            assert_result(
                dict(read_only_entry.data),
                legacy_default_entry_data,
                "legacy default interval restart must preserve entry data",
            )
            assert_result(
                dict(read_only_entry.options),
                legacy_default_entry_options,
                "legacy default interval restart must not invent a saved option",
            )
            assert_entry_has_only_summary_sensors(
                hass,
                domain,
                read_only_entry.entry_id,
                LEGACY_SUMMARY_SENSOR_ENTITY_IDS,
            )
            await async_assert_safe_diagnostics(
                hass,
                domain,
                read_only_entry,
                "read-only",
            )
            assert_entry_uses_summary_update_interval(
                hass,
                domain,
                read_only_entry.entry_id,
                SUMMARY_UPDATE_INTERVAL_DEFAULT,
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

            await async_update_summary_interval(
                hass,
                domain,
                read_only_entry,
                "15m",
                LEGACY_SUMMARY_SENSOR_ENTITY_IDS,
            )
            assert_local_summary_view(hass, domain)
            await async_assert_authenticated_local_summary_http_access(
                hass,
                "HASC slower summary interval temporary",
            )

            await async_update_safe_options(hass, read_only_entry, "read-only")
            assert_result(
                read_only_entry.data["mode"],
                "read-only",
                "a second safe options update must not mutate the initial entry mode",
            )
            await async_assert_safe_diagnostics(hass, domain, read_only_entry, "read-only")
            assert_local_summary_view(hass, domain)
            await async_assert_authenticated_local_summary_http_access(hass)
            assert_entry_has_only_summary_sensors(
                hass,
                domain,
                read_only_entry.entry_id,
                LEGACY_SUMMARY_SENSOR_ENTITY_IDS,
            )
            assert_entry_uses_summary_update_interval(
                hass,
                domain,
                read_only_entry.entry_id,
                "15m",
            )

            optional_page_reader_token = await async_create_test_read_only_access_token(
                hass,
                "HASC optional local page test user",
            )
            read_only_entry_id = read_only_entry.entry_id
            await async_update_optional_local_page(
                hass,
                domain,
                read_only_entry,
                False,
                optional_page_reader_token,
                LEGACY_SUMMARY_SENSOR_ENTITY_IDS,
            )
            await hass.async_stop()
            hass = await async_start_empty_home_assistant(config_directory)
            read_only_entry = hass.config_entries.async_get_entry(read_only_entry_id)
            if read_only_entry is None:
                raise RuntimeError("disabled optional local page entry must survive a restart")
            assert_result(
                read_only_entry.state,
                config_entries.ConfigEntryState.LOADED,
                "disabled optional local page must keep HASC's count display loaded",
            )
            assert_entry_has_only_summary_sensors(
                hass,
                domain,
                read_only_entry.entry_id,
                LEGACY_SUMMARY_SENSOR_ENTITY_IDS,
            )
            assert_entry_uses_summary_update_interval(
                hass,
                domain,
                read_only_entry.entry_id,
                "15m",
            )
            await async_assert_safe_diagnostics(hass, domain, read_only_entry, "read-only")
            if hass.data.get(domain) is not None:
                raise RuntimeError("disabled optional local page must not restore HASC page data")
            if find_local_summary_routes(hass):
                raise RuntimeError("disabled optional local page must not restore its route")

            optional_page_reader_token = await async_create_test_read_only_access_token(
                hass,
                "HASC optional local page restart test user",
            )
            await async_update_optional_local_page(
                hass,
                domain,
                read_only_entry,
                True,
                optional_page_reader_token,
                LEGACY_SUMMARY_SENSOR_ENTITY_IDS,
            )

            ordinary_unload_data = dict(read_only_entry.data)
            ordinary_unload_options_before_save = dict(read_only_entry.options)
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
                ordinary_unload_options_before_save,
                "ordinary unload must not mutate safe entry options",
            )
            assert_entry_has_unloaded_summary_sensors(
                hass,
                domain,
                read_only_entry.entry_id,
                LEGACY_SUMMARY_SENSOR_ENTITY_IDS,
            )
            await async_assert_closed_diagnostics(
                hass,
                domain,
                read_only_entry,
                "HASC ordinary unload",
            )
            await async_assert_local_summary_is_unavailable(
                hass,
                domain,
                ordinary_unload_reader_token,
                "HASC ordinary unload",
            )
            await async_update_inactive_safe_options_without_reading_home(
                hass,
                domain,
                read_only_entry,
                "shadow",
                expected_disabled_by=None,
                target_local_page_enabled=False,
                target_summary_update_interval="30m",
            )
            assert_result(
                read_only_entry.data,
                ordinary_unload_data,
                "stopped safe options must not mutate safe entry data",
            )
            assert_entry_has_unloaded_summary_sensors(
                hass,
                domain,
                read_only_entry.entry_id,
                LEGACY_SUMMARY_SENSOR_ENTITY_IDS,
            )
            await async_assert_closed_diagnostics(
                hass,
                domain,
                read_only_entry,
                "saving safe options while HASC is stopped",
            )
            await async_assert_local_summary_is_unavailable(
                hass,
                domain,
                ordinary_unload_reader_token,
                "saving safe options while HASC is stopped",
            )
            await async_assert_stale_local_summary_pointer_is_unavailable_without_reading(
                hass,
                domain,
                read_only_entry,
                ordinary_unload_reader_token,
                "saving safe options while HASC is stopped with a stale local-summary pointer",
            )
            ordinary_unload_options = dict(read_only_entry.options)
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
            assert_entry_uses_summary_update_interval(
                hass,
                domain,
                read_only_entry.entry_id,
                "30m",
            )
            await async_assert_safe_diagnostics(hass, domain, read_only_entry, "shadow")
            async with async_block_home_summary_reads(
                hass,
                domain,
                "HASC ordinary setup with its optional local page closed",
            ):
                await async_assert_local_summary_is_unavailable(
                    hass,
                    domain,
                    ordinary_unload_reader_token,
                    "HASC ordinary setup with its optional local page closed",
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
            await async_assert_closed_diagnostics(
                hass,
                domain,
                read_only_entry,
                "HASC deactivation",
            )
            await async_assert_local_summary_is_unavailable(
                hass,
                domain,
                deactivation_reader_token,
                "HASC deactivation",
            )
            deactivation_data = dict(read_only_entry.data)
            await async_update_inactive_safe_options_without_reading_home(
                hass,
                domain,
                read_only_entry,
                "read-only",
                expected_disabled_by=ConfigEntryDisabler.USER,
                target_local_page_enabled=True,
                target_summary_update_interval="5m",
            )
            assert_result(
                dict(read_only_entry.data),
                deactivation_data,
                "user-deactivated safe options must not mutate entry data",
            )
            assert_entry_has_disabled_summary_sensors(
                hass,
                domain,
                read_only_entry.entry_id,
                LEGACY_SUMMARY_SENSOR_ENTITY_IDS,
            )
            await async_assert_closed_diagnostics(
                hass,
                domain,
                read_only_entry,
                "saving safe options while HASC is user-deactivated",
            )
            await async_assert_local_summary_is_unavailable(
                hass,
                domain,
                deactivation_reader_token,
                "saving safe options while HASC is user-deactivated",
            )
            deactivation_options = dict(read_only_entry.options)
            await async_enable_safe_entry(hass, read_only_entry)
            assert_result(
                dict(read_only_entry.data),
                deactivation_data,
                "user reactivation must preserve safe entry data",
            )
            assert_result(
                dict(read_only_entry.options),
                deactivation_options,
                "user reactivation must preserve saved safe options",
            )
            assert_entry_has_only_summary_sensors(
                hass,
                domain,
                read_only_entry.entry_id,
                LEGACY_SUMMARY_SENSOR_ENTITY_IDS,
            )
            assert_entry_uses_summary_update_interval(
                hass,
                domain,
                read_only_entry.entry_id,
                "5m",
            )
            await async_assert_safe_diagnostics(hass, domain, read_only_entry, "read-only")
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
            await async_assert_closed_diagnostics(
                restarted_hass,
                domain,
                restored_entry,
                "HASC deactivation restart",
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
            disabled_restart_data = dict(restored_entry.data)
            await async_update_inactive_safe_options_without_reading_home(
                restarted_hass,
                domain,
                restored_entry,
                "shadow",
                expected_disabled_by=ConfigEntryDisabler.USER,
                target_local_page_enabled=False,
                target_summary_update_interval="15m",
            )
            assert_result(
                dict(restored_entry.data),
                disabled_restart_data,
                "user-disabled safe options after restart must not mutate entry data",
            )
            assert_deactivated_entry_stays_inactive_after_restart(
                restarted_hass,
                domain,
                restored_entry,
                LEGACY_SUMMARY_SENSOR_ENTITY_IDS,
            )
            await async_assert_closed_diagnostics(
                restarted_hass,
                domain,
                restored_entry,
                "saving safe options while HASC is user-deactivated after restart",
            )
            disabled_restart_options = dict(restored_entry.options)
            await async_enable_safe_entry(restarted_hass, restored_entry)
            assert_result(
                restored_entry.data["direct_execution_status"],
                "direct_execution_blocked",
                "restart must not change the direct execution block",
            )
            assert_result(
                dict(restored_entry.data),
                disabled_restart_data,
                "user reactivation after restart must preserve safe entry data",
            )
            assert_result(
                dict(restored_entry.options),
                disabled_restart_options,
                "user reactivation after restart must preserve saved safe options",
            )
            await async_assert_safe_diagnostics(
                restarted_hass,
                domain,
                restored_entry,
                "shadow",
            )
            assert_entry_has_only_summary_sensors(
                restarted_hass,
                domain,
                restored_entry.entry_id,
                LEGACY_SUMMARY_SENSOR_ENTITY_IDS,
            )
            assert_entry_uses_summary_update_interval(
                restarted_hass,
                domain,
                restored_entry.entry_id,
                "15m",
            )
            assert_local_summary_is_not_registered(
                restarted_hass,
                domain,
                "user reactivation after restart with its optional local page closed",
            )
            ordinary_unload_restart_data = dict(restored_entry.data)
            ordinary_unload_restart_options = dict(restored_entry.options)
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
            await async_assert_closed_diagnostics(
                restarted_hass,
                domain,
                restored_entry,
                "ordinary HASC stop before restart",
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
            assert_local_summary_is_not_registered(
                restarted_hass,
                domain,
                "ordinary HASC stop before restart with its optional local page closed",
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
            reserved_entry = reserve_summary_sensor_name_for_test(
                ordinary_unload_restarted_hass
            )
            removed_entries.append(
                await async_assert_ordinary_unloaded_entry_can_be_removed(
                    ordinary_unload_restarted_hass,
                    domain,
                    restored_entry,
                    ordinary_unload_restart_data,
                    ordinary_unload_restart_options,
                    LEGACY_SUMMARY_SENSOR_ENTITY_IDS,
                    removal_reader_token,
                )
            )
            assert_reserved_collision_entry_is_unchanged(
                ordinary_unload_restarted_hass,
                reserved_entry,
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
            reinstalled_entry_data = dict(reinstalled_entry.data)
            reinstalled_entry_options = dict(reinstalled_entry.options)
            await async_unload_safe_entry(
                ordinary_unload_restarted_hass,
                reinstalled_entry,
            )
            assert_result(
                dict(reinstalled_entry.data),
                reinstalled_entry_data,
                "ordinary unload before deactivation must preserve safe entry data",
            )
            assert_result(
                dict(reinstalled_entry.options),
                reinstalled_entry_options,
                "ordinary unload before deactivation must preserve safe entry options",
            )
            assert_entry_has_unloaded_summary_sensors(
                ordinary_unload_restarted_hass,
                domain,
                reinstalled_entry.entry_id,
                expected_entity_ids=None,
            )
            await async_assert_closed_diagnostics(
                ordinary_unload_restarted_hass,
                domain,
                reinstalled_entry,
                "HASC ordinary unload before deactivation",
            )
            await async_assert_local_summary_is_unavailable(
                ordinary_unload_restarted_hass,
                domain,
                removal_reader_token,
                "HASC ordinary unload before deactivation",
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
            await async_assert_closed_diagnostics(
                ordinary_unload_restarted_hass,
                domain,
                reinstalled_entry,
                "HASC deactivation after ordinary unload",
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
            await async_enable_safe_entry(
                ordinary_unload_restarted_hass,
                reinstalled_entry,
            )
            assert_result(
                dict(reinstalled_entry.data),
                reinstalled_entry_data,
                "ordinary-unload reactivation must preserve safe entry data",
            )
            assert_result(
                dict(reinstalled_entry.options),
                reinstalled_entry_options,
                "ordinary-unload reactivation must preserve safe entry options",
            )
            assert_entry_has_only_summary_sensors(
                ordinary_unload_restarted_hass,
                domain,
                reinstalled_entry.entry_id,
                expected_entity_ids=None,
            )
            await async_assert_safe_diagnostics(
                ordinary_unload_restarted_hass,
                domain,
                reinstalled_entry,
                "read-only",
            )
            assert_local_summary_view(ordinary_unload_restarted_hass, domain)
            await async_assert_authenticated_local_summary_http_access(
                ordinary_unload_restarted_hass,
                "HASC reactivation after ordinary unload",
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
            await async_assert_closed_diagnostics(
                ordinary_unload_restarted_hass,
                domain,
                reinstalled_entry,
                "HASC second deactivation after ordinary unload",
            )
            await async_assert_local_summary_is_unavailable(
                ordinary_unload_restarted_hass,
                domain,
                removal_reader_token,
                "HASC second deactivation after ordinary unload",
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
            assert_entry_uses_summary_update_interval(
                post_removal_hass,
                domain,
                fresh_entry.entry_id,
                SUMMARY_UPDATE_INTERVAL_DEFAULT,
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
            await async_assert_closed_diagnostics(
                post_removal_hass,
                domain,
                fresh_entry,
                "HASC removal",
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
        removed_entries.append(
            await async_assert_user_deactivated_unsafe_settings_cannot_enable_lifecycle(
                config_directory,
                domain,
                tuple(removed_entries),
                reserved_entry,
                unsafe_options=UNSAFE_PROXY_OPTIONS,
                scenario_name="unsafe saved mode",
            )
        )
        removed_entries.append(
            await async_assert_user_deactivated_unsafe_settings_cannot_enable_lifecycle(
                config_directory,
                domain,
                tuple(removed_entries),
                reserved_entry,
                unsafe_options=UNSAFE_PROXY_OPTIONS,
                scenario_name="unsafe proxy option repair",
                repair_after_rejected_activation=True,
            )
        )
        removed_entries.append(
            await async_assert_user_deactivated_unsafe_settings_cannot_enable_lifecycle(
                config_directory,
                domain,
                tuple(removed_entries),
                reserved_entry,
                unsafe_data=UNSAFE_PROXY_DATA,
                scenario_name="unsafe proxy data repair",
                repair_after_rejected_activation=True,
            )
        )
        removed_entries.append(
            await async_assert_user_deactivated_unsafe_settings_cannot_enable_lifecycle(
                config_directory,
                domain,
                tuple(removed_entries),
                reserved_entry,
                unsafe_data=UNSAFE_MISSING_DIRECT_EXECUTION_DATA,
                scenario_name="unsafe missing execution-block repair",
                repair_after_rejected_activation=True,
            )
        )
        removed_entries.append(
            await async_assert_user_deactivated_unsafe_settings_cannot_enable_lifecycle(
                config_directory,
                domain,
                tuple(removed_entries),
                reserved_entry,
                unsafe_data=UNSAFE_MISSING_MODE_DATA,
                scenario_name="unsafe missing mode repair",
                repair_after_rejected_activation=True,
            )
        )
        removed_entries.append(
            await async_assert_user_deactivated_unsafe_settings_cannot_enable_lifecycle(
                config_directory,
                domain,
                tuple(removed_entries),
                reserved_entry,
                unsafe_data=UNSAFE_EXTRA_FIELD_DATA,
                scenario_name="unsafe extra-field data repair",
                repair_after_rejected_activation=True,
            )
        )
        removed_entries.append(
            await async_assert_user_deactivated_unsafe_settings_cannot_enable_lifecycle(
                config_directory,
                domain,
                tuple(removed_entries),
                reserved_entry,
                unsafe_options=UNSAFE_EXTRA_FIELD_OPTIONS,
                scenario_name="unsafe extra-field option repair",
                repair_after_rejected_activation=True,
            )
        )
        removed_entries.append(
            await async_assert_user_deactivated_unsafe_settings_cannot_enable_lifecycle(
                config_directory,
                domain,
                tuple(removed_entries),
                reserved_entry,
                unsafe_data=UNSAFE_ALLOWED_DIRECT_EXECUTION_DATA,
                scenario_name="unsafe direct-execution block",
            )
        )
        removed_entries.append(
            await async_assert_user_deactivated_unsafe_settings_cannot_enable_lifecycle(
                config_directory,
                domain,
                tuple(removed_entries),
                reserved_entry,
                unsafe_data=UNSAFE_ALLOWED_DIRECT_EXECUTION_DATA,
                scenario_name="unsafe direct-execution repair",
                repair_after_rejected_activation=True,
            )
        )
        removed_entries.append(
            await async_assert_user_deactivated_unsafe_settings_cannot_enable_lifecycle(
                config_directory,
                domain,
                tuple(removed_entries),
                reserved_entry,
                unsafe_data=UNSAFE_ALLOWED_DIRECT_EXECUTION_DATA,
                unsafe_options=UNSAFE_PROXY_OPTIONS,
                scenario_name="unsafe partial repair",
                repair_after_rejected_activation=True,
                partial_main_repair_before_options=True,
            )
        )
        removed_entries.append(
            await async_assert_user_deactivated_unsafe_settings_cannot_enable_lifecycle(
                config_directory,
                domain,
                tuple(removed_entries),
                reserved_entry,
                unsafe_data=UNSAFE_ALLOWED_DIRECT_EXECUTION_DATA,
                scenario_name="unsafe direct-execution block after restart",
                restart_before_activation=True,
            )
        )
        removed_entries.append(
            await async_assert_user_deactivated_unsafe_settings_cannot_enable_lifecycle(
                config_directory,
                domain,
                tuple(removed_entries),
                reserved_entry,
                unsafe_data=UNSAFE_ALLOWED_DIRECT_EXECUTION_DATA,
                scenario_name="unsafe direct-execution repair after restart",
                restart_before_activation=True,
                repair_after_rejected_activation=True,
                reclose_after_recovery=True,
                repair_after_repeat_closure=True,
                restart_after_repeat_repair=True,
                reclose_after_repeat_repair_restart=True,
            )
        )
        removed_entries.extend(
            await async_assert_persisted_duplicate_entry_lifecycle(
                config_directory,
                domain,
                tuple(removed_entries),
                reserved_entry,
                first_entry_is_user_disabled=True,
            )
        )
        removed_entries.extend(
            await async_assert_persisted_duplicate_entry_lifecycle(
                config_directory,
                domain,
                tuple(removed_entries),
                reserved_entry,
                first_entry_is_user_disabled=False,
            )
        )


def main() -> None:
    """Run the isolated Core compatibility check from a Python 3.14 environment."""

    asyncio.run(async_run_check())
    print("Home Assistant Core compatibility check passed.")


if __name__ == "__main__":
    main()
