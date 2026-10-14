"""Bounded negative cases through actual startup, HTTP and event subscribers.

Run one N1_CASE per fresh sanitized process: the filesystem audit is permanent.
"""

import asyncio
import importlib
import json
import os
from datetime import datetime, timedelta, timezone
import time
import unittest
from pathlib import Path

from ha_environment import Hass, Context, Request, install_ports, external_clock, isolated_directory
from fixtures import ROOM, LIGHT, PRESENCE, room_config, off_settings
from source_bootstrap import load_installed, verify_source
import test_repaired_path

CASES = {"manual_on", "unknown_presence", "unavailable_presence", "partial_report",
         "timeout_report", "manual_during_send", "save_failure", "parallel_return",
         "manual_during_save", "duplicate_manual", "manual_on_during_send",
         "manual_parameter_during_send", "manual_related_during_send", "manual_unrelated_during_send", "calendar_day",
         "writer_wait_manual", "sensor_stale_followup", "adjustment_timeout", "physical_parameter_during_adjustment",
         "physical_color_during_adjustment"}

STORE_BOUNDARIES = {"common_pending": ("manual_light_authority", 1),
    "room": ("room_lighting_ownership", 1), "scenario": ("light_automation_priority", 1),
    "off_protection": ("manual_light_off_protection", 1), "common_commit": ("manual_light_authority", 2)}
CASES.update("store_"+action+"_"+participant for action in ("failure", "manual") for participant in STORE_BOUNDARIES)
CASES.update("store_manualon_"+participant for participant in STORE_BOUNDARIES)
CASES.update({"old_writer_planned", "old_writer_durable"})
CASES.update({"power_off_return", "power_unknown_return", "power_manual_wait",
              "version_during_save", "restart_partial_report", "restart_timeout_report"})
CASES.update({"old_writer_power_wait", "old_writer_partial", "old_writer_unknown", "old_writer_pending_restart"})
CASES.add("pending_restart_without_manual")
CASES.update({"power_delayed_report", "manual_mirror_before_return"})
CASES.add("manual_independent_mirror_return")


class RepairedMatrix(unittest.IsolatedAsyncioTestCase):
    async def test_real_entry_points(self):
        case = os.environ["N1_CASE"]
        self.assertIn(case, CASES)
        with isolated_directory() as root:
            hass = Hass(root)
            if case == "calendar_day":
                # Asyncio slow-callback diagnostics measure our intentionally
                # jumped external clock; do not emit 1440 false slow warnings.
                hass.loop.set_debug(False)
            install_ports(hass)
            candidate = os.environ["N1_CANDIDATE"]
            pin = test_repaired_path.RepairedPath.source_digest
            with external_clock(hass, virtual_monotonic=case == "calendar_day"):
                package, digest = load_installed(candidate, temporary_root=root,
                    oracle_root=os.environ["N1_ORACLE"], expected_digest=pin)
                config = importlib.import_module("custom_components.hausman_hub.domain.room_lighting")
                storage = importlib.import_module("custom_components.hausman_hub.application.room_lighting_storage")
                payload = room_config()
                if case.startswith("power_") or case == "old_writer_power_wait":
                    payload["devices"]["power_switch"] = {
                        "id": "n1_power", "name": "N1 power", "entityId": "switch.n1_power",
                        "autoAdoptOverride": None}
                    hass.states.async_set("switch.n1_power", "on" if case == "old_writer_power_wait" else
                                          "unknown" if case == "power_unknown_return" else "off")
                if case == "manual_mirror_before_return":
                    payload["profile"] = "day_curve"
                if case in {"manual_related_during_send", "manual_mirror_before_return", "manual_independent_mirror_return"}:
                    payload["devices"]["light_targets"].append(dict(
                        payload["devices"]["light_targets"][0], id="n1_alt", entityId="light.n1_alt", role="mirror"))
                    hass.states.async_set("light.n1_alt", "off")
                if case == "manual_unrelated_during_send":
                    hass.states.async_set("light.n1_other", "off")
                await storage.HomeAssistantRoomLightingStore(hass, hass.entry.entry_id).async_upsert(
                    config.config_from_payload(payload))
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
                await initial_off.async_replace_settings("matrix_settings", 0, off_settings())
                self.assertTrue(await asyncio.wait_for(package.async_setup_entry(hass, hass.entry), 20))
                try:
                    await hass.services.async_call("light", "turn_on", {"entity_id": LIGHT},
                                                   context=Context(user_id="matrix_user"))
                    await hass.drain()
                    if case != "manual_on" and not case.endswith("_scenario"):
                        await hass.services.async_call("light", "turn_off", {"entity_id": LIGHT},
                                                       context=Context(user_id="matrix_user"))
                        await hass.drain()
                    data = hass.data["hausman_hub"]
                    authority = data["manual_light_authority"]
                    runtime = data["room_lighting_runtime"]
                    priority = data["light_automation_priority"]
                    view = next(view for view in hass.views if view.name == "api:hausman_hub:room_lighting_force_auto")
                    before = len(hass.services.calls)
                    if case in {"manual_mirror_before_return", "manual_independent_mirror_return"}:
                        await hass.services.async_call("light", "turn_on", {"entity_id": "light.n1_alt"},
                            context=Context(user_id="manual_mirror"))
                        await hass.drain()
                        before = len(hass.services.calls)
                        response = await view.post(Request(ROOM))
                        await hass.drain()
                        self.assertEqual(response.data["outcome"], "returned")
                        expected = 0 if case == "manual_mirror_before_return" else 1
                        self.assertEqual(len(hass.services.calls)-before, expected)
                        self.assertEqual(hass.states.get("light.n1_alt").state, "on")
                        self.assertEqual(hass.states.get(LIGHT).state, "off" if expected == 0 else "on")
                        records = runtime._ownership.snapshots_for(ROOM, ["n1_main", "n1_alt"])
                        self.assertFalse(any(r.source.value == "auto" and r.confirmed and
                                             (expected == 0 or r.target_id == "n1_alt") for r in records))
                        self.assertTrue(all(c[2]["entity_id"] != "light.n1_alt" for c in hass.services.calls[before:]))
                        if expected == 0:
                            calendar = next(r for r in hass.schedules if r.kind == "calendar" and r.callback.__name__ == "_clock_event")
                            hass.now += timedelta(minutes=1)
                            hass.states.async_set(PRESENCE, "on", force_update=True)
                            calendar.callback(hass.now)
                            await hass.drain()
                            self.assertEqual(len(hass.services.calls), before)
                            self.assertEqual(hass.states.get(LIGHT).state, "off")
                        print("R1_MANUAL_MIRROR", case, response.data, "mirror kept ON, untouched", flush=True)
                        return
                    prior = authority.record(LIGHT)
                    second = None
                    if case == "version_during_save":
                        fired = False
                        changed_response = None
                        async def change_version(key, saved_payload):
                            nonlocal fired, changed_response
                            if fired or key != "hausman_hub.manual_light_authority.n1_entry":
                                return
                            fired = True
                            hass.save_hook = None
                            config_view = next(v for v in hass.views if v.name == "api:hausman_hub:room_lighting_config")
                            request = Request(ROOM, dict(payload, name="N1 changed configuration"))
                            request.path = f"/api/hausman_hub/v1/rooms/{ROOM}/lighting/config"
                            changed = await config_view.put(request)
                            changed_response = changed
                        hass.save_hook = change_version
                        response = await view.post(Request(ROOM))
                        await hass.drain()
                        self.assertTrue(fired)
                        self.assertIsNotNone(changed_response)
                        self.assertEqual(changed_response.status, 200, changed_response.data)
                        self.assertEqual(changed_response.data["version"], 2)
                        self.assertNotEqual(response.data["outcome"], "returned")
                        self.assertTrue(authority.blocks(LIGHT))
                        self.assertEqual(len(hass.services.calls), before)
                        print("R1_VERSION", response.data, flush=True)
                        return
                    if case.startswith("power_"):
                        if case == "power_delayed_report":
                            async def delayed_power_report(domain, service, service_data, context):
                                if domain != "switch":
                                    return
                                hass.services.hook = None
                                hass.services.report_mode = "timeout"
                                async def report_later():
                                    await asyncio.sleep(0.05)
                                    hass.now += timedelta(milliseconds=1000)
                                    hass.states.async_set("switch.n1_power", "on", context=context)
                                    hass.services.report_mode = "full"
                                hass.async_create_task(report_later())
                            hass.services.hook = delayed_power_report
                        if case == "power_manual_wait":
                            async def manual_at_power(domain, service, service_data, context):
                                if domain != "switch":
                                    return
                                hass.services.hook = None
                                await hass.services.async_call("light", "turn_on", {"entity_id": LIGHT},
                                    context=Context(user_id="manual_during_power_wait"))
                            hass.services.hook = manual_at_power
                        response = await view.post(Request(ROOM))
                        await hass.drain()
                        print("R1_POWER", case, response.data, hass.services.calls[before:],
                              runtime._shadow.journal_payload()["entries"][-3:], flush=True)
                        if case == "power_unknown_return":
                            # Logical release is not a delivery certificate.
                            # P1 requires a separate blocked native receipt.
                            self.assertEqual(response.data["outcome"], "returned")
                            self.assertEqual(len(hass.services.calls), before)
                            dispatch = [e for e in runtime._shadow.journal_payload()["entries"] if "dispatch" in e][-1]["dispatch"]
                            self.assertEqual(dispatch[0]["reason"], "power_source_unconfirmed")
                            self.assertFalse(dispatch[0]["sent"])
                            self.assertEqual(dispatch[0]["ownershipChanged"], "none")
                        elif case == "power_manual_wait":
                            self.assertTrue(authority.blocks(LIGHT))
                            self.assertEqual(len(hass.services.calls)-before, 2)
                            records = runtime._ownership.snapshots_for(ROOM, ["n1_main"])
                            self.assertFalse(any(r.source.value == "auto" and r.confirmed for r in records))
                        else:
                            self.assertEqual(response.data["outcome"], "returned")
                            self.assertEqual([c[0] for c in hass.services.calls[before:]], ["switch", "light"])
                            self.assertEqual(hass.states.get(LIGHT).state, "on")
                            records = runtime._ownership.snapshots_for(ROOM, ["n1_main"])
                            self.assertTrue(any(r.source.value == "auto" and r.confirmed for r in records))
                            if case == "power_delayed_report":
                                dispatch = [e for e in runtime._shadow.journal_payload()["entries"] if "dispatch" in e and
                                            any("targetId" in d for d in e["dispatch"])][-1]["dispatch"]
                                self.assertGreater(dispatch[0]["power"]["powerPreparation"]["waitedSeconds"], 0.05)
                        return
                    if case.startswith("store_"):
                        action, participant = case.removeprefix("store_").split("_", 1)
                        key_part, occurrence = STORE_BOUNDARIES[participant]
                        seen = 0
                        fired = False
                        async def boundary_hook(key, payload):
                            nonlocal seen, fired
                            if key != f"hausman_hub.{key_part}.n1_entry":
                                return
                            seen += 1
                            if seen != occurrence:
                                return
                            fired = True
                            hass.save_hook = None
                            if action == "failure":
                                raise OSError("synthetic participant failure: " + participant)
                            await hass.services.async_call("light", "turn_on" if action == "manualon" else "turn_off", {"entity_id": LIGHT},
                                context=Context(user_id="manual_during_"+participant))
                        if participant == "common_commit":
                            async def executor_boundary(func, args):
                                if func.__name__ != "_prepare_conditional_document":
                                    return
                                hass.executor_hook = None
                                # The conditional publication uses the real HA
                                # executor port rather than Store.async_save.
                                # Select this external job, then use the same
                                # assertions as other durable participants.
                                nonlocal seen
                                seen = occurrence - 1
                                await boundary_hook(args[3], args[4])
                            hass.executor_hook = executor_boundary
                        else:
                            hass.save_hook = boundary_hook
                        response = await view.post(Request(ROOM))
                        await hass.drain()
                        self.assertTrue(fired, participant)
                        self.assertNotEqual(response.data["outcome"], "returned")
                        self.assertTrue(authority.blocks(LIGHT))
                        self.assertEqual(len(hass.services.calls)-before, 0 if action == "failure" else 1)
                        print("R1_STORE_BOUNDARY", case, json.dumps(response.data), flush=True)
                        self.assertTrue(await hass.unload(package))
                        hass.data.clear()
                        hass.views.clear()
                        from ha_environment import Entry
                        hass.entry = Entry()
                        self.assertTrue(await package.async_setup_entry(hass, hass.entry))
                        await hass.drain()
                        self.assertTrue(hass.data["hausman_hub"]["manual_light_authority"].blocks(LIGHT))
                        self.assertEqual(verify_source(candidate, expected_digest=pin)[1], digest)
                        return
                    if case.endswith("presence"):
                        hass.states.async_set(PRESENCE, case.removesuffix("_presence"))
                        await hass.drain()
                    if case in {"partial_report", "timeout_report", "restart_partial_report", "restart_timeout_report"}:
                        hass.services.report_mode = case.removeprefix("restart_").removesuffix("_report")
                    if case.endswith("during_send"):
                        async def intervene(*args):
                            hass.services.hook = None
                            target = {"manual_related_during_send": "light.n1_alt",
                                      "manual_unrelated_during_send": "light.n1_other"}.get(case, LIGHT)
                            manual_data = {"entity_id": target}
                            if case == "manual_parameter_during_send":
                                manual_data["brightness_pct"] = 100
                            service = "turn_off" if case == "manual_during_send" else "turn_on"
                            await hass.services.async_call("light", service, manual_data,
                                                           context=Context(user_id="new_manual"))
                        hass.services.hook = intervene
                    if case in {"save_failure", "manual_during_save", "parallel_return"}:
                        entered, release = asyncio.Event(), asyncio.Event()
                        fired = False
                        async def save_boundary(key, payload):
                            nonlocal fired
                            if fired:
                                return
                            fired = True
                            hass.save_hook = None
                            if case == "save_failure":
                                raise OSError("synthetic storage failure")
                            if case == "manual_during_save":
                                await hass.services.async_call("light", "turn_off", {"entity_id": LIGHT},
                                                               context=Context(user_id="new_manual"))
                            else:
                                entered.set()
                                await release.wait()
                        hass.save_hook = save_boundary
                    if case == "duplicate_manual":
                        stamp = prior.observed_at_ms
                        context_id = prior.fingerprint.split(":")[2]
                        # Duplicate the exact native service event, including time.
                        hass.bus.async_fire("call_service", {"domain": "light", "service": "turn_off",
                            "service_data": {"entity_id": LIGHT}}, Context(id=context_id, user_id="matrix_user"),
                            time_fired=datetime.fromtimestamp(stamp / 1000, timezone.utc))
                        await hass.drain()
                        self.assertEqual(authority.record(LIGHT).generation, prior.generation)
                        self.assertEqual(authority.record(LIGHT).deadline_ms, prior.deadline_ms)
                        print("R1_MATRIX", json.dumps({"case": case, "manualPreserved": True,
                            "nativeCalls": len(hass.services.calls) - before}), flush=True)
                        self.assertEqual(len(hass.services.calls), before)
                        return
                    if case == "parallel_return":
                        first_task = asyncio.create_task(view.post(Request(ROOM)))
                        await asyncio.wait_for(entered.wait(), 10)
                        try:
                            second = await asyncio.wait_for(view.post(Request(ROOM)), 10)
                            self.assertEqual(second.data["outcome"], "blocked")
                            self.assertEqual(second.data["blockers"], ["return_busy"])
                        finally:
                            release.set()
                            response = await first_task
                    else:
                        response = await view.post(Request(ROOM))
                    await hass.drain()
                    if case.startswith("restart_"):
                        records = runtime._ownership.snapshots_for(ROOM, ["n1_main"])
                        self.assertFalse(any(r.source.value == "auto" and r.confirmed for r in records))
                        self.assertEqual(len(hass.services.calls)-before, 1)
                        self.assertTrue(await hass.unload(package))
                        hass.data.clear()
                        hass.views.clear()
                        from ha_environment import Entry
                        hass.entry = Entry()
                        self.assertTrue(await package.async_setup_entry(hass, hass.entry))
                        await hass.drain()
                        records = hass.data["hausman_hub"]["room_lighting_runtime"]._ownership.snapshots_for(ROOM, ["n1_main"])
                        self.assertFalse(any(r.source.value == "auto" and r.confirmed for r in records))
                        self.assertTrue(hass.data["hausman_hub"]["manual_light_authority"].blocks(LIGHT))
                        self.assertEqual(len(hass.services.calls)-before, 1)
                        print("R1_RESTART_UNCONFIRMED", case, response.data, flush=True)
                        return
                    if case in {"writer_wait_manual", "old_writer_planned", "old_writer_durable", "old_writer_power_wait", "old_writer_partial", "old_writer_unknown", "old_writer_pending_restart", "pending_restart_without_manual", "sensor_stale_followup", "adjustment_timeout", "physical_parameter_during_adjustment", "physical_color_during_adjustment"}:
                        self.assertEqual(response.data["outcome"], "returned")
                        self.assertEqual(len(hass.services.calls)-before, 1)
                        calendar = next(record for record in hass.schedules
                            if record.kind == "calendar" and record.callback.__name__ == "_clock_event")
                        hass.now += timedelta(minutes=6 if case in {"sensor_stale_followup", "adjustment_timeout"} else 1)
                        if case in {"writer_wait_manual", "old_writer_planned", "old_writer_durable", "old_writer_power_wait", "old_writer_partial", "old_writer_unknown", "old_writer_pending_restart", "pending_restart_without_manual"}:
                            entered, release = asyncio.Event(), asyncio.Event()
                            held_writer = None
                            held_pending = None
                            async def hold_existing_writer(*args):
                                nonlocal held_writer, held_pending
                                if case == "old_writer_power_wait" and args[0] != "switch":
                                    return
                                hass.services.hook = None
                                held_writer = asyncio.current_task()
                                held_pending = next(e for e in reversed(runtime._shadow.journal_payload()["entries"])
                                                    if "dispatchPending" in e)
                                entered.set()
                                await release.wait()
                                if case == "old_writer_partial":
                                    hass.services.report_mode = "partial"
                                if case == "old_writer_unknown":
                                    raise OSError("synthetic unknown old transport outcome")
                            hass.services.hook = hold_existing_writer
                            if case in {"old_writer_planned", "old_writer_durable"}:
                                hass.services.hook = None
                                async def hold_pending_writer(key, payload):
                                    if not key.startswith("hausman_hub.room_lighting_shadow.") or not any(
                                            "dispatchPending" in entry for entry in payload.get("entries", [])):
                                        return
                                    hass.save_hook = None
                                    hass.save_after_hook = None
                                    entered.set()
                                    await release.wait()
                                if case == "old_writer_planned":
                                    hass.save_hook = hold_pending_writer
                                else:
                                    hass.save_after_hook = hold_pending_writer
                            if case == "old_writer_power_wait":
                                # A device observation gap, then a fresh OFF,
                                # not a physical ON->OFF gesture. Let the real
                                # state listener and room writer prepare power.
                                hass.states.async_set("switch.n1_power", "unavailable")
                                hass.states.async_set("switch.n1_power", "off")
                            hass.states.async_set(PRESENCE, "on", force_update=True)
                            calendar.callback(hass.now)
                            await asyncio.wait_for(entered.wait(), 5)
                            if case == "pending_restart_without_manual":
                                held_writer.cancel()
                                await asyncio.gather(held_writer, return_exceptions=True)
                                before_restart = len(hass.services.calls)
                                self.assertTrue(await hass.unload(package))
                                persisted = json.loads((Path(root)/".storage"/"hausman_hub.room_lighting_shadow.n1_entry").read_text())["data"]["entries"]
                                self.assertIn(held_pending, persisted)
                                self.assertFalse(any(e.get("operationId") == held_pending["operationId"]
                                    and any(r.get("action") == held_pending["dispatchPending"]["action"]
                                            for r in e.get("dispatch", [])) for e in persisted))
                                hass.data.clear()
                                hass.views.clear()
                                from ha_environment import Entry
                                hass.entry = Entry()
                                self.assertTrue(await package.async_setup_entry(hass, hass.entry))
                                await hass.drain()
                                restored_authority = hass.data["hausman_hub"]["manual_light_authority"]
                                self.assertTrue(restored_authority.blocks(LIGHT))
                                self.assertEqual(len(hass.services.calls), before_restart)
                                hass.states.async_set(PRESENCE, "on", force_update=True)
                                await hass.drain()
                                self.assertTrue(restored_authority.blocks(LIGHT))
                                self.assertEqual(len(hass.services.calls), before_restart)
                                print("R1_PENDING_WITHOUT_MANUAL", held_pending, restored_authority.barrier_reason_for(LIGHT), flush=True)
                                return
                            queued = asyncio.create_task(view.post(Request(ROOM)))
                            try:
                                for _ in range(10):
                                    await asyncio.sleep(0)
                                if queued.done():
                                    print("R1_WRITER_SETUP", queued.result().data, flush=True)
                                self.assertFalse(queued.done())
                                self.assertTrue(runtime._explicit_return_active)
                                await hass.services.async_call("light", "turn_on", {"entity_id": LIGHT},
                                    context=Context(user_id="after_queued_click"))
                                if case == "old_writer_pending_restart":
                                    self.assertIsNotNone(held_writer)
                                    held_writer.cancel()
                                    await asyncio.gather(held_writer, return_exceptions=True)
                            finally:
                                release.set()
                                queued_response = await asyncio.wait_for(queued, 10)
                            await hass.drain()
                            self.assertEqual(queued_response.data["outcome"], "blocked")
                            self.assertTrue(authority.blocks(LIGHT))
                            records = runtime._ownership.snapshots_for(ROOM, ["n1_main"])
                            manual_at = max(r.at for r in records if r.source.value == "manual")
                            self.assertTrue(all(r.at < manual_at for r in records if r.source.value == "auto"))
                            print("R1_WRITER_RECORDS", runtime._ownership.to_payload(), flush=True)
                            print("R1_WRITER_WAIT", json.dumps(queued_response.data), flush=True)
                            if case in {"old_writer_partial", "old_writer_unknown", "old_writer_power_wait", "old_writer_pending_restart"}:
                                if case == "old_writer_pending_restart":
                                    persisted = json.loads((Path(root)/".storage"/"hausman_hub.room_lighting_shadow.n1_entry").read_text())["data"]["entries"]
                                    self.assertIn(held_pending, persisted)
                                    self.assertFalse(any(e.get("operationId") == held_pending["operationId"]
                                        and any(r.get("action") == held_pending["dispatchPending"]["action"]
                                                for r in e.get("dispatch", [])) for e in persisted),
                                        "interrupted actual send has no durable closing receipt")
                                    print("R1_PENDING_BEFORE_RESTART", held_pending, flush=True)
                                calls_before_restart = len(hass.services.calls)
                                self.assertTrue(await hass.unload(package))
                                hass.data.clear()
                                hass.views.clear()
                                from ha_environment import Entry
                                hass.entry = Entry()
                                self.assertTrue(await package.async_setup_entry(hass, hass.entry))
                                await hass.drain()
                                self.assertTrue(hass.data["hausman_hub"]["manual_light_authority"].blocks(LIGHT))
                                self.assertEqual(len(hass.services.calls), calls_before_restart)
                                print("R1_WRITER_RESTART", case, "manual blocked, no new calls", flush=True)
                            return
                        elif case in {"physical_parameter_during_adjustment", "physical_color_during_adjustment"}:
                            async def physical_parameter_change(*args):
                                hass.services.hook = None
                                attrs = dict(hass.states.get(LIGHT).attributes)
                                attrs.update({"brightness": 51} if case == "physical_parameter_during_adjustment" else
                                             {"color_temp_kelvin": 5000})
                                hass.states.async_set(LIGHT, "on", attrs, context=Context())
                            hass.services.hook = physical_parameter_change
                            hass.states.async_set(PRESENCE, "on", force_update=True)
                            await hass.drain()
                            print("R1_PHYSICAL_PARAMETER", json.dumps({"blocked": authority.blocks(LIGHT),
                                "ownership": runtime._ownership.to_payload(),
                                "entries": runtime._shadow.journal_payload()["entries"][-4:]}), flush=True)
                            self.assertTrue(authority.blocks(LIGHT))
                            records = runtime._ownership.snapshots_for(ROOM, ["n1_main"])
                            manual_at = authority.record(LIGHT).observed_at_ms
                            self.assertTrue(all(r.at < manual_at for r in records if r.source.value == "auto"))
                            calls_after = len(hass.services.calls)
                            calendar.callback(hass.now)
                            await hass.drain()
                            self.assertEqual(len(hass.services.calls), calls_after)
                            self.assertEqual(calls_after-before, 2, "only initial ON and in-flight brightness; no following colour")
                            self.assertTrue(await hass.unload(package))
                            hass.data.clear()
                            hass.views.clear()
                            from ha_environment import Entry
                            hass.entry = Entry()
                            self.assertTrue(await package.async_setup_entry(hass, hass.entry))
                            await hass.drain()
                            authority = hass.data["hausman_hub"]["manual_light_authority"]
                            self.assertTrue(authority.blocks(LIGHT))
                            self.assertIsNotNone(authority.record(LIGHT))
                        elif case == "sensor_stale_followup":
                            calendar.callback(hass.now)
                            await hass.drain()
                            self.assertEqual(len(hass.services.calls)-before, 1)
                            self.assertIn("sensor_stale", json.dumps(runtime._shadow.journal_payload()["entries"][-1]))
                        else:
                            hass.services.report_mode = "timeout"
                            hass.states.async_set(PRESENCE, "on", force_update=True)
                            await hass.drain()
                            sent = len(hass.services.calls)
                            self.assertGreater(sent-before, 1)
                            for _ in range(3):
                                calendar.callback(hass.now)
                                await hass.drain()
                            self.assertEqual(len(hass.services.calls), sent, "no unlimited adjustment retries")
                            records = runtime._ownership.snapshots_for(ROOM, ["n1_main"])
                            self.assertTrue(all(r.at < int(hass.now.timestamp()*1000) for r in records))
                        self.assertEqual(verify_source(candidate, expected_digest=pin)[1], digest)
                        print("R1_EXTRA", case, "PASS", flush=True)
                        return
                    if case == "calendar_day":
                        from virtual_scheduler import advance
                        start = hass.now.replace(second=0, microsecond=0)
                        mono_start = time.monotonic()
                        counts = {"calendar": 0, "interval": 0, "later": 0, "roomCalendar": 0}
                        for minute in range(1440):
                            # A stationary sensor can publish fresh reports.
                            # Deliver them before the boundary, so the new period
                            # must be applied by the actual registered clock.
                            previous = start + timedelta(minutes=minute, seconds=59)
                            step = await advance(hass, previous)
                            for key in counts:
                                counts[key] += step[key]
                            hass.states.async_set(PRESENCE, "on", force_update=True)
                            await hass.drain()
                            boundary = start + timedelta(minutes=minute + 1)
                            step = await advance(hass, boundary)
                            for key in counts:
                                counts[key] += step[key]
                            expected_brightness, expected_kelvin = (40, 3000) if boundary.hour >= 12 and (
                                boundary.hour > 12 or boundary.minute >= 1) else (60, 4000)
                            if hass.states.get(LIGHT).attributes["brightness"] != round(expected_brightness * 255 / 100):
                                print("R1_DAY_DIFFERENCE", json.dumps({"minute": minute,
                                    "boundary": str(boundary), "now": str(hass.now),
                                    "counts": counts, "calls": hass.services.calls[before:],
                                    "entries": runtime._shadow.journal_payload()["entries"][-5:]}), flush=True)
                            self.assertEqual(hass.states.get(LIGHT).attributes["brightness"], round(expected_brightness * 255 / 100))
                            self.assertEqual(hass.states.get(LIGHT).attributes["color_temp_kelvin"], expected_kelvin)
                            self.assertFalse(priority.manual_entity_ids())
                            self.assertFalse(authority.blocks(LIGHT))
                        self.assertEqual(counts["roomCalendar"], 1440)
                        self.assertAlmostEqual(time.monotonic() - mono_start, 86400 - 0.003, places=2)
                        self.assertEqual(len(hass.services.calls) - before, 5)
                        print("R1_DAY", json.dumps({"minutes": 1440, "callbacks": counts,
                            "nativeCalls": len(hass.services.calls) - before,
                            "wallElapsed": (hass.now - start).total_seconds(),
                            "monotonicElapsed": time.monotonic() - mono_start,
                            "stationarySensorReports": 1440, "uiOpen": False}), flush=True)
                    records = runtime._ownership.snapshots_for(ROOM, ["n1_main"])
                    auto = any(r.source.value == "auto" and r.confirmed for r in records)
                    calls = len(hass.services.calls) - before
                    print("R1_MATRIX", json.dumps({"case": case, "status": response.status,
                        "response": response.data, "nativeCalls": calls, "autoConfirmed": auto,
                        "ownership": runtime._ownership.to_payload(),
                        "blocked": authority.blocks(LIGHT), "scenarioManual": sorted(priority.manual_entity_ids()),
                        "barrier": authority.barrier_reason_for(LIGHT)}), flush=True)
                    if case in {"unknown_presence", "unavailable_presence"}:
                        self.assertEqual(response.data["outcome"], "blocked")
                        self.assertEqual(response.data["blockers"], ["observations_unavailable"])
                        self.assertEqual(calls, 0)
                        self.assertEqual(authority.record(LIGHT), prior)
                    elif case in {"save_failure", "manual_during_save"}:
                        self.assertEqual(response.data["outcome"], "incomplete")
                        self.assertTrue(authority.blocks(LIGHT))
                        self.assertEqual(calls, 1 if case == "manual_during_save" else 0)
                    elif case.endswith("during_send"):
                        self.assertEqual(authority.blocks(LIGHT), case != "manual_unrelated_during_send")
                        self.assertEqual(calls, 2)
                    elif case == "manual_on":
                        self.assertEqual(response.data["outcome"], "returned")
                        self.assertEqual(calls, 0)
                    elif case != "calendar_day":
                        self.assertEqual(response.data["outcome"], "returned")
                        self.assertEqual(calls, 1)
                        self.assertFalse(priority.manual_entity_ids())
                    if case not in {"parallel_return", "manual_unrelated_during_send", "calendar_day"}:
                        self.assertFalse(auto, "manual, partial or absent report cannot grant confirmed AUTO")
                    else:
                        self.assertTrue(auto)
                    if case.endswith("during_send") and case != "manual_unrelated_during_send":
                        dispatch = [entry for entry in runtime._shadow.journal_payload()["entries"] if "dispatch" in entry][-1]["dispatch"]
                        self.assertTrue(dispatch[0]["confirmed"])
                        self.assertEqual(dispatch[0]["ownershipChanged"], "none")
                        self.assertEqual(dispatch[0]["reason"], "manual_superseded")
                        self.assertTrue(await hass.unload(package))
                        hass.data.clear()
                        hass.views.clear()
                        from ha_environment import Entry
                        hass.entry = Entry()
                        self.assertTrue(await package.async_setup_entry(hass, hass.entry))
                        await hass.drain()
                        self.assertTrue(hass.data["hausman_hub"]["manual_light_authority"].blocks(LIGHT))
                        restored = hass.data["hausman_hub"]["room_lighting_runtime"]._ownership.snapshots_for(ROOM, ["n1_main"])
                        self.assertFalse(any(r.source.value == "auto" and r.confirmed for r in restored))
                    self.assertEqual(verify_source(candidate, expected_digest=pin)[1], digest)
                finally:
                    hass.save_hook = None
                    hass.save_after_hook = None
                    hass.executor_hook = None
                    hass.services.hook = None
                    await hass.unload(package)
                    self.assertEqual(verify_source(candidate, expected_digest=pin)[1], digest)
