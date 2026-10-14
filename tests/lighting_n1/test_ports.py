"""Checks of the external fixture, not acceptance of lighting behavior."""

from datetime import timedelta, timezone
import datetime
import json
from pathlib import Path
import socket
import subprocess
import time
import asyncio
import unittest

from ha_environment import Hass, Context, FileStore, external_clock, isolated_directory
from source_bootstrap import verify_source, close_network
from oracle_adapter import compare


class Ports(unittest.IsolatedAsyncioTestCase):
    async def test_executor_fault_and_after_save_observe_real_external_boundaries(self):
        with isolated_directory() as root:
            hass = Hass(root)
            seen = []
            def job(value):
                seen.append(value)
                return value + 1
            async def refuse(func, args):
                self.assertIs(func, job)
                self.assertEqual(args, (2,))
                raise OSError("external executor failure")
            hass.executor_hook = refuse
            with self.assertRaisesRegex(OSError, "executor failure"):
                await hass.async_add_executor_job(job, 2)
            self.assertEqual(seen, [])
            hass.executor_hook = None
            self.assertEqual(await hass.async_add_executor_job(job, 2), 3)
            store = FileStore(hass, 1, "n1")
            after = []
            async def observe(key, payload):
                after.append(await store.async_load())
                self.assertEqual(key, "n1")
            hass.save_after_hook = observe
            await store.async_save({"durable": True})
            self.assertEqual(after, [{"durable": True}])

    async def test_request_metadata_and_body_are_consistent(self):
        from ha_environment import Request
        request = Request("room_n1", {"name": "Свет"})
        self.assertEqual(request.content_type, "application/json")
        self.assertEqual(request.content_length, len(await request.read()))
        self.assertEqual(await request.json(), {"name": "Свет"})

    async def test_calendar_interval_and_later_cross_boundary_without_clock_rollback(self):
        from ha_environment import install_ports
        from virtual_scheduler import advance, calendar_matches
        with isolated_directory() as root:
            hass = Hass(root)
            install_ports(hass)
            import importlib
            event = importlib.import_module("homeassistant.helpers.event")
            hass.loop.set_debug(False)
            with external_clock(hass, virtual_monotonic=True):
                start = hass.now.replace(second=0, microsecond=0)
                hass.now = start + timedelta(seconds=58)
                seen = []
                def callback(moment):
                    seen.append(moment)
                    hass.now += timedelta(milliseconds=2)
                event.async_track_time_change(hass, callback, minute="*", second=0)
                event.async_track_time_interval(hass, callback, timedelta(seconds=2))
                event.async_call_later(hass, 2, callback)
                counts = await advance(hass, start+timedelta(minutes=1))
                self.assertEqual(counts["calendar"], 1)
                self.assertEqual(counts["interval"], 1)
                self.assertEqual(counts["later"], 1)
                self.assertEqual(hass.now, start+timedelta(minutes=1, milliseconds=6))
                with self.assertRaisesRegex(AssertionError, "backwards"):
                    await advance(hass, start+timedelta(minutes=1))
                with self.assertRaisesRegex(AssertionError, "unsupported"):
                    calendar_matches({"day": 14}, hass.now)

    async def test_monotonic_event_loop_and_sleep_share_virtual_elapsed_time(self):
        with isolated_directory() as root:
            hass = Hass(root)
            asyncio.get_running_loop().set_debug(False)
            with external_clock(hass, virtual_monotonic=True):
                mono = time.monotonic()
                loop_at = asyncio.get_running_loop().time()
                sleeper = asyncio.create_task(asyncio.sleep(2))
                await asyncio.sleep(0)
                hass.now += timedelta(seconds=1)
                await asyncio.sleep(0)
                self.assertFalse(sleeper.done())
                hass.now += timedelta(seconds=1)
                for _ in range(5):
                    await asyncio.sleep(0)
                self.assertTrue(sleeper.done())
                self.assertEqual(time.monotonic()-mono, 2)
                self.assertEqual(asyncio.get_running_loop().time()-loop_at, 2)

    async def test_comparison_detects_false_manual_without_changing_expected(self):
        expected = {"manualBlocked": False, "nativeCalls": 1}
        self.assertEqual(compare(expected, dict(expected)), {})
        self.assertEqual(compare(expected, {"manualBlocked": True, "nativeCalls": 1}),
                         {"manualBlocked": {"expected": False, "actual": True}})
        self.assertEqual(expected, {"manualBlocked": False, "nativeCalls": 1})

    async def test_clock_type_and_all_wall_clock_forms(self):
        with isolated_directory() as root:
            hass = Hass(root)
            with external_clock(hass):
                self.assertIsInstance(hass.now, datetime.datetime)
                self.assertEqual(datetime.datetime.now(timezone.utc), hass.now)
                self.assertEqual(int(time.time_ns() / 1_000_000), int(time.time() * 1000))
                hass.now += timedelta(seconds=60)
                self.assertEqual(datetime.datetime.now(timezone.utc), hass.now)

    async def test_file_store_survives_new_adapter_and_atomic_error(self):
        with isolated_directory() as root:
            hass = Hass(root)
            store = FileStore(hass, 1, "n1")
            await store.async_save({"manual": "off"})
            self.assertEqual(await FileStore(hass, 1, "n1").async_load(), {"manual": "off"})
            self.assertEqual(json.loads(Path(store.path).read_text())["key"], "n1")
            async def fail(key, data):
                raise OSError("synthetic write failure")
            hass.save_hook = fail
            with self.assertRaises(OSError):
                await store.async_save({"manual": "on"})
            self.assertEqual(await store.async_load(), {"manual": "off"})

    async def test_transport_preserves_context_and_all_properties(self):
        with isolated_directory() as root:
            hass = Hass(root)
            context = Context()
            events = []
            wildcard = []
            hass.bus.async_listen("state_changed", events.append)
            hass.bus.async_listen("*", wildcard.append)
            initial = hass.now
            await hass.services.async_call("light", "turn_on", {"entity_id": "light.n1_main",
                "brightness_pct": 60, "color_temp_kelvin": 4000}, context=context)
            state = hass.states.get("light.n1_main")
            self.assertIs(state.context, context)
            self.assertIs(events[-1].context, context)
            self.assertGreater(state.last_updated, initial)
            self.assertEqual(state.attributes["brightness"], 153)
            self.assertEqual(state.attributes["color_temp_kelvin"], 4000)
            self.assertEqual(len(wildcard), 2)

    async def test_transport_refuses_other_targets_and_empty_scope(self):
        with isolated_directory() as root:
            hass = Hass(root)
            for data in ({"entity_id": "light.not_n1"}, {}):
                with self.assertRaises(AssertionError):
                    await hass.services.async_call("light", "turn_on", data)
            self.assertEqual(hass.services.calls, [])

    async def test_source_rejects_unpinned_manifest(self):
        with isolated_directory() as root:
            candidate = Path(root)
            (candidate / "files").mkdir()
            (candidate / "verification.json").write_text(json.dumps({"files": {}}))
            with self.assertRaisesRegex(AssertionError, "declared installed"):
                verify_source(candidate)

    async def test_z_guard_network_files_and_processes(self):
        with isolated_directory() as root:
            close_network(read_roots=(Path(__file__).parent, "/usr/lib/python3.12"), write_root=root)
            Path(root, "allowed").write_text("synthetic")
            self.assertEqual(Path(root, "allowed").read_text(), "synthetic")
            with self.assertRaisesRegex(RuntimeError, "external network"):
                socket.socket(socket.AF_INET)
            with self.assertRaisesRegex(RuntimeError, "external network"):
                socket.socket(socket.AF_INET6)
            with self.assertRaisesRegex(RuntimeError, "external network"):
                subprocess.run(["/usr/bin/true"])
            with self.assertRaisesRegex(RuntimeError, "undeclared file read"):
                Path("/tmp/n1-forbidden-read").read_text()
            with self.assertRaisesRegex(RuntimeError, "write outside"):
                Path("/tmp/n1-forbidden-write").write_text("blocked")
            with self.assertRaisesRegex(RuntimeError, "mutation outside"):
                Path("/tmp/n1-forbidden-directory").mkdir()
