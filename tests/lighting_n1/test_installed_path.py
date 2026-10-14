"""Bounded installed-startup probe; no house access or runtime monkeypatches."""

import asyncio
import importlib
import json
import os
from datetime import timedelta
import unittest

from ha_environment import Hass, install_ports, external_clock, Context, Request, isolated_directory
from fixtures import ROOM, LIGHT, PRESENCE, room_config, off_settings
from oracle_adapter import v01_expected, compare
from source_bootstrap import load_installed, verify_source, EXPECTED_SOURCE_DIGEST


class InstalledPath(unittest.IsolatedAsyncioTestCase):
    source_digest = EXPECTED_SOURCE_DIGEST
    verify_repaired_followup = False

    async def test_V01_return_preserves_automatic_authority(self):
        with isolated_directory() as root:
            hass = Hass(root)
            install_ports(hass)
            candidate = os.environ["N1_CANDIDATE"]
            with external_clock(hass):
                oracle_root = os.environ["N1_ORACLE"]
                package, digest = load_installed(candidate, temporary_root=root, oracle_root=oracle_root,
                                                expected_digest=self.source_digest)
                expected = v01_expected(oracle_root)
                print("N1_SOURCE", digest, flush=True)
                config = importlib.import_module("custom_components.hausman_hub.domain.room_lighting")
                storage = importlib.import_module("custom_components.hausman_hub.application.room_lighting_storage")
                await storage.HomeAssistantRoomLightingStore(hass, hass.entry.entry_id).async_upsert(
                    config.config_from_payload(room_config()))
                hass.states.async_set(PRESENCE, "on")
                hass.states.async_set("binary_sensor.n1_away", "off")
                hass.states.async_set(LIGHT, "off", {"supported_color_modes": ["color_temp"],
                    "min_color_temp_kelvin": 2000, "max_color_temp_kelvin": 6500})
                topology_module = importlib.import_module("custom_components.hausman_hub.application.protected_topology")
                topology_store = importlib.import_module("custom_components.hausman_hub.protected_topology_storage")
                topology = topology_module.ProtectedTopologyService(
                    topology_store.HomeAssistantProtectedTopologyStore(hass, hass.entry.entry_id),
                    entity_validator=lambda entity: hass.states.get(entity) is not None)
                await topology.async_load()
                await topology.async_replace(0, [{"role": topology_module.ROLE_AWAY_SENSOR,
                                                  "entityId": "binary_sensor.n1_away"}])
                off_module = importlib.import_module("custom_components.hausman_hub.application.manual_light_off_protection")
                off_store = importlib.import_module("custom_components.hausman_hub.manual_light_off_protection_storage")
                initial_off = off_module.ManualLightOffProtectionCoordinator(
                    off_store.HomeAssistantManualLightOffProtectionStore(hass, hass.entry.entry_id))
                await initial_off.async_load()
                await initial_off.async_replace_settings("n1_settings", 0, off_settings())
                try:
                    self.assertTrue(await asyncio.wait_for(package.async_setup_entry(hass, hass.entry), 20))
                    await hass.drain()
                    data = hass.data["hausman_hub"]
                    off = data["manual_light_off_protection"]
                    await hass.services.async_call("light", "turn_on", {"entity_id": LIGHT},
                                                   context=Context(user_id="n1_user"))
                    await hass.drain()
                    self.assertIn(LIGHT, data["light_automation_priority"].manual_entity_ids())
                    await hass.services.async_call("light", "turn_off", {"entity_id": LIGHT},
                                                   context=Context(user_id="n1_user"))
                    await hass.drain()
                    self.assertTrue(await hass.unload(package))
                    hass.data.clear()
                    hass.views.clear()
                    from ha_environment import Entry
                    hass.entry = Entry()
                    self.assertTrue(await asyncio.wait_for(package.async_setup_entry(hass, hass.entry), 20))
                    await hass.drain()
                    data = hass.data["hausman_hub"]
                    off = data["manual_light_off_protection"]
                    authority = data["manual_light_authority"]
                    # Fresh external sensor report AFTER the startup observation floor.
                    # The sensor stays ON; no manual light action or SUT method is substituted.
                    hass.states.async_set(PRESENCE, "on", force_update=True)
                    await hass.drain()
                    self.assertTrue(authority.blocks(LIGHT))
                    self.assertTrue(off.snapshot()["protections"])
                    runtime = data["room_lighting_runtime"]
                    priority = data["light_automation_priority"]
                    print("N1_MANUAL_READY", authority.record(LIGHT),
                          runtime._ownership.to_payload(), off.snapshot(),
                          priority.manual_entity_ids(), flush=True)
                    self.assertTrue(runtime.running)
                    before = len(hass.services.calls)
                    view = next(view for view in hass.views if view.name == "api:hausman_hub:room_lighting_force_auto")
                    response = await view.post(Request(ROOM))
                    await hass.drain()
                    print("N1_RETURN", response.status, response.data, flush=True)
                    print("N1_CALLS", hass.services.calls[before:], flush=True)
                    self.assertEqual(response.data.get("outcome"), "returned")
                    self.assertEqual(len(hass.services.calls[before:]), 1)
                    domain, service, payload, _ = hass.services.calls[before]
                    self.assertEqual((domain, service), ("light", "turn_on"))
                    self.assertEqual(payload, {"entity_id": LIGHT,
                        "brightness_pct": 60, "color_temp_kelvin": 4000})
                    self.assertEqual(hass.states.get(LIGHT).attributes["brightness"], 153)
                    self.assertEqual(hass.states.get(LIGHT).attributes["color_temp_kelvin"], 4000)
                    self.assertFalse(authority.blocks(LIGHT))
                    print("N1_AFTER_RETURN", runtime._ownership.to_payload(),
                          off.snapshot(), priority.manual_entity_ids(),
                          runtime._shadow.journal_payload(), flush=True)
                    false_manual = priority.manual_entity_ids()
                    self.assertFalse(off.snapshot()["protections"])
                    records = runtime._ownership.snapshots_for(ROOM, ["n1_main"])
                    auto_confirmed = bool(records and records[-1].source.value == "auto" and records[-1].confirmed)
                    self.assertTrue(auto_confirmed)
                    owned_context = hass.states.get(LIGHT).context
                    self.assertTrue(runtime._executor.owns_service_context(owned_context, LIGHT, "on"))
                    actual = {"outcome": response.data["outcome"], "nativeCalls": len(hass.services.calls[before:]),
                              "autoConfirmed": auto_confirmed, "manualBlocked": bool(false_manual) or authority.blocks(LIGHT)}
                    difference = compare(expected, actual)
                    print("N1_ORACLE_DIFF", json.dumps(difference), flush=True)
                    print("N1_FACTS", json.dumps({"case": "V01", "outcome": response.data["outcome"],
                        "operationId": response.data["operationId"], "nativeCalls": len(hass.services.calls[before:]),
                        "authorityBlocked": authority.blocks(LIGHT), "roomAutoConfirmed": True,
                        "offProtections": len(off.snapshot()["protections"]),
                        "scenarioManual": sorted(false_manual), "executorRecognizesContext": True}), flush=True)
                    hass.now = hass.now.replace(second=0, microsecond=0) + timedelta(minutes=1)
                    calendar_callbacks = 0
                    for record in tuple(hass.schedules):
                        if record.active and record.kind == "calendar" and record.callback.__name__ == "_clock_event":
                            calendar_callbacks += 1
                            record.callback(hass.now)
                    self.assertEqual(calendar_callbacks, 1)
                    await hass.drain()
                    if self.verify_repaired_followup:
                        print("R1_CALENDAR_CALLS", hass.services.calls[before:], flush=True)
                        print("R1_CALENDAR_JOURNAL", runtime._shadow.journal_payload()["entries"][-8:], flush=True)
                        self.assertFalse(priority.manual_entity_ids())
                        self.assertFalse(authority.blocks(LIGHT))
                        # Existing engine emits separate property adjustments
                        # for an already owned ON light, not a second initial ON.
                        self.assertEqual(len(hass.services.calls[before:]), 3)
                        self.assertEqual([call[2] for call in hass.services.calls[before + 1:]], [
                            {"entity_id": LIGHT, "brightness_pct": 40},
                            {"entity_id": LIGHT, "color_temp_kelvin": 3000},
                        ])
                        self.assertEqual(hass.states.get(LIGHT).attributes["brightness"], 102)
                        self.assertEqual(hass.states.get(LIGHT).attributes["color_temp_kelvin"], 3000)
                    print("N1_FOLLOW_UP", json.dumps({"nativeCallsSinceReturn": len(hass.services.calls[before:]),
                        "light": hass.states.get(LIGHT).state, "scenarioManual": sorted(priority.manual_entity_ids()),
                        "calendarCallbacks": calendar_callbacks,
                        "lastEntries": runtime._shadow.journal_payload()["entries"][-3:]}), flush=True)
                    self.assertTrue(await hass.unload(package))
                    hass.data.clear()
                    hass.views.clear()
                    from ha_environment import Entry
                    hass.entry = Entry()
                    self.assertTrue(await asyncio.wait_for(package.async_setup_entry(hass, hass.entry), 20))
                    await hass.drain()
                    restarted = hass.data["hausman_hub"]
                    print("N1_RESTART", json.dumps({
                        "authorityBlocked": restarted["manual_light_authority"].blocks(LIGHT),
                        "authorityBarrier": restarted["manual_light_authority"].barrier_reason_for(LIGHT),
                        "authorityRecord": repr(restarted["manual_light_authority"].record(LIGHT)),
                        "blockingEvidence": repr(restarted["manual_light_authority"].blocking_evidence(LIGHT)),
                        "scenarioManual": sorted(restarted["light_automation_priority"].manual_entity_ids()),
                        "roomOwnership": restarted["room_lighting_runtime"]._ownership.to_payload(),
                        "offProtections": len(restarted["manual_light_off_protection"].snapshot()["protections"])}), flush=True)
                    if self.verify_repaired_followup:
                        # A reload closes dispatch until fresh recovery proof.
                        # No false manual record must be confused with that guard.
                        self.assertEqual(restarted["manual_light_authority"].barrier_reason_for(LIGHT), "restart_uncertain")
                        self.assertIsNone(restarted["manual_light_authority"].record(LIGHT))
                        self.assertIsNone(restarted["manual_light_authority"].blocking_evidence(LIGHT))
                        self.assertFalse(restarted["light_automation_priority"].manual_entity_ids())
                        self.assertFalse(restarted["manual_light_off_protection"].snapshot()["protections"])
                        hass.now += timedelta(milliseconds=1)
                        hass.states.async_set(PRESENCE, "on", force_update=True)
                        await hass.drain()
                        after_reload = len(hass.services.calls)
                        resumed_view = next(view for view in hass.views if view.name == "api:hausman_hub:room_lighting_force_auto")
                        resumed = await resumed_view.post(Request(ROOM))
                        await hass.drain()
                        self.assertEqual(resumed.data["outcome"], "returned")
                        self.assertFalse(restarted["manual_light_authority"].blocks(LIGHT))
                        self.assertEqual(len(hass.services.calls), after_reload)
                        await hass.services.async_call("light", "turn_off", {"entity_id": LIGHT},
                                                       context=Context(user_id="n1_user"))
                        await hass.drain()
                        self.assertTrue(restarted["manual_light_authority"].blocks(LIGHT))
                        self.assertTrue(restarted["manual_light_off_protection"].snapshot()["protections"])
                        await hass.services.async_call("light", "turn_on", {"entity_id": LIGHT},
                                                       context=Context(user_id="n1_user"))
                        await hass.drain()
                        self.assertIn(LIGHT, restarted["light_automation_priority"].manual_entity_ids())
                        self.assertTrue(restarted["manual_light_authority"].blocks(LIGHT))
                    self.assertEqual(verify_source(candidate, expected_digest=self.source_digest)[1], digest)
                    self.assertFalse(difference, "automatic room report was misclassified as manual")
                finally:
                    await hass.unload(package)


if __name__ == "__main__":
    unittest.main()
