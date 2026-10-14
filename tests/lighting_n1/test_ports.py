"""Checks of the external fixture, not acceptance of lighting behavior."""

from datetime import timedelta, timezone
import datetime
import json
from pathlib import Path
import socket
import subprocess
import time
import unittest

from ha_environment import Hass, Context, FileStore, external_clock, isolated_directory
from source_bootstrap import verify_source, close_network
from oracle_adapter import compare


class Ports(unittest.IsolatedAsyncioTestCase):
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
