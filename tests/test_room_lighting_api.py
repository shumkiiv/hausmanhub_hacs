"""HTTP boundary tests for room lighting config, status, templates and live tests.

Responses are validated against the contract schemas from the contract worktree.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

from jsonschema import Draft202012Validator

from tests.test_local_summary_access import (
    FAKE_MODULE_NAMES,
    FakeHomeAssistant,
    FakeJsonRequest,
    FakeRequest,
    fake_home_assistant_modules,
    reader_user,
)

PACKAGE_MODULE = "custom_components.hausman_hub"
API_MODULE = f"{PACKAGE_MODULE}.room_lighting_api"
_CONTRACT_DIR = Path(
    os.environ.get(
        "HAUSMANHUB_CONTRACT_DIR",
        "/home/ivsh/projects/HausmanHub/worktrees/"
        "codex-room-lighting-contract-2026-09-11/schemas/v1",
    )
)


def _validate(schema_name: str, payload: object) -> None:
    path = _CONTRACT_DIR / schema_name
    if not path.is_file():
        # A missing contract checkout must skip, not error. ``pytest.skip``
        # raises ``_pytest.outcomes.Skipped``, which unittest reports as an
        # error when raised inside ``asyncio.run``. ``unittest.SkipTest`` is
        # honoured by both runners.
        raise unittest.SkipTest(
            f"room lighting contract schema is not available: {schema_name}"
        )
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(payload)


def _config_payload(room_id: str = "room_demo_entry", name: str = "Тамбур") -> dict:
    return {
        "contract": {"name": "hausman-hub-room-lighting-config", "version": 1},
        "roomId": room_id,
        "name": name,
        "version": 1,
        "devices": {
            "sensors": [
                {
                    "id": "sensor_demo_presence",
                    "name": "Присутствие",
                    "kind": "presence",
                    "entityId": "binary_sensor.demo_presence",
                    "autoAdoptOverride": None,
                }
            ],
            "light_targets": [
                {
                    "id": "light_main",
                    "name": "Люстра",
                    "kind": "light",
                    "entityId": "light.demo_main",
                    "role": "main",
                    "groupId": None,
                    "brightness": True,
                    "color_temperature": True,
                    "autoAdoptOverride": None,
                }
            ],
            "power_switch": None,
            "wireless_switches": [],
            "selectAll": False,
        },
        "schedule": [
            {
                "id": "sch_day",
                "title": "День",
                "when": {
                    "daysOfWeek": "all",
                    "holiday": False,
                    "anchor": {"kind": "fixed", "time": "09:00", "offsetMinutes": 0},
                },
                "targets": {"lightTargets": ["light_main"], "groupIds": [], "roles": []},
                "how": {
                    "brightness": 60,
                    "colorTemperature": 3000,
                    "fade": True,
                    "mode": "on_presence",
                    "minOnSeconds": 0,
                },
            }
        ],
        "switchBindings": [],
        "illumination": None,
        "dimming": {
            "enabled": True,
            "onAbsence": True,
            "fadeSeconds": 20,
            "targetPercent": 0,
        },
        "manualOffProtection": {
            "enabled": True,
            "minimumIntervalSeconds": 600,
            "releaseMode": "timer_and_absence",
            "stableAbsenceSeconds": 30,
            "priority": "manual_above_auto",
        },
        "awayBehavior": {"mode": "none"},
        "autoAdopt": True,
        "updatedAt": 1,
        "overrides": {},
    }


class _MemoryStore:
    def __init__(self) -> None:
        self.configs: dict[str, object] = {}

    async def async_get(self, room_id: str):
        return self.configs.get(room_id)

    async def async_load_all(self):
        return tuple(self.configs.values())

    async def async_upsert(self, config) -> None:
        self.configs[config.room_id] = config

    async def async_delete(self, room_id: str) -> bool:
        return self.configs.pop(room_id, None) is not None


class _DelayedMemoryStore(_MemoryStore):
    """Hold one configuration save open to inspect the concurrent status read."""

    def __init__(self, config: object) -> None:
        super().__init__()
        self.configs[config.room_id] = config
        self.upsert_started = asyncio.Event()
        self.release = asyncio.Event()

    async def async_upsert(self, config) -> None:
        self.upsert_started.set()
        await self.release.wait()
        self.configs[config.room_id] = config


class _SpyExecutor:
    def __init__(self) -> None:
        self.calls: list[object] = []

    async def __call__(self, command: object) -> dict:
        self.calls.append(command)
        return {"confirmed": True}


def _request(path: str, **match: str) -> FakeRequest:
    request = FakeRequest("127.0.0.1", reader_user("admin", admin=True), path=path)
    request.match_info = match
    return request


def _json_request(path: str, payload: object, **match: str) -> FakeJsonRequest:
    request = FakeJsonRequest(
        "127.0.0.1", reader_user("admin", admin=True), path, payload
    )
    request.match_info = match
    return request


class RoomLightingApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.previous_modules = {
            name: sys.modules.get(name)
            for name in (*FAKE_MODULE_NAMES, PACKAGE_MODULE, API_MODULE)
        }
        for name in (*FAKE_MODULE_NAMES, PACKAGE_MODULE, API_MODULE):
            sys.modules.pop(name, None)
        sys.modules.update(fake_home_assistant_modules())
        importlib.import_module(PACKAGE_MODULE)
        cls.api = importlib.import_module(API_MODULE)

    @classmethod
    def tearDownClass(cls) -> None:
        for name in (*FAKE_MODULE_NAMES, PACKAGE_MODULE, API_MODULE):
            sys.modules.pop(name, None)
        sys.modules.update(
            {
                name: module
                for name, module in cls.previous_modules.items()
                if module is not None
            }
        )

    def setUp(self) -> None:
        from datetime import datetime, timezone

        from custom_components.hausman_hub.application.room_lighting_service import (
            RoomLightingService,
        )

        now = int(datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc).timestamp() * 1000)

        def context_factory(_config):
            return self._context(now)

        def live_context_factory(_stage):
            return self._context(now)

        self.hass = FakeHomeAssistant()
        self.store = _MemoryStore()
        self.hass.data["hausman_hub"] = {
            self.api.DATA_ROOM_LIGHTING_SERVICE: RoomLightingService(self.store),
            self.api.DATA_ROOM_LIGHTING_LIVE_TESTS: {},
            self.api.DATA_ROOM_LIGHTING_CONTEXT: context_factory,
            self.api.DATA_ROOM_LIGHTING_LIVE_CONTEXT: live_context_factory,
            self.api.DATA_ROOM_LIGHTING_LIVE_TEST_SLEEP: lambda _seconds: asyncio.sleep(0),
        }
        self.config_view = self.api.RoomLightingConfigView(self.hass)
        self.status_view = self.api.RoomLightingStatusView(self.hass)
        self.templates_view = self.api.RoomLightingTemplatesView(self.hass)
        self.apply_view = self.api.RoomLightingTemplateApplyView(self.hass)
        self.live_view = self.api.RoomLightingLiveTestsView(self.hass)
        self.live_result_view = self.api.RoomLightingLiveTestView(self.hass)

    @staticmethod
    def _context(now: int):
        from datetime import time, timezone

        from custom_components.hausman_hub.domain.room_lighting import SensorKind
        from custom_components.hausman_hub.domain.room_lighting_engine import (
            LightSnapshot,
            RoomLightingContext,
            SensorSnapshot,
        )
        from custom_components.hausman_hub.domain.room_lighting_ownership import (
            SensorState,
        )

        return RoomLightingContext(
            now=now,
            timezone=timezone.utc,
            sunrise=time(7, 0),
            sunset=time(19, 0),
            sensors=(
                SensorSnapshot(
                    "sensor_demo_presence", SensorKind.PRESENCE, SensorState.ON, now
                ),
            ),
            lights=(LightSnapshot("light_main", SensorState.OFF, now),),
        )

    def _config_path(self, room_id: str = "room_demo_entry") -> str:
        return self.api.ROOM_LIGHTING_CONFIG_PATH.format(room_id=room_id)

    def test_config_round_trip_and_revision_conflict(self) -> None:
        async def flow() -> None:
            room_id = "room_demo_entry"
            missing = await self.config_view.get(
                _request(self._config_path(room_id), room_id=room_id)
            )
            self.assertEqual(404, missing.status)

            put = await self.config_view.put(
                _json_request(
                    self._config_path(room_id), _config_payload(room_id), room_id=room_id
                )
            )
            self.assertEqual(200, put.status)
            _validate("room-lighting-config.schema.json", put.payload)
            self.assertEqual(1, put.payload["version"])

            got = await self.config_view.get(
                _request(self._config_path(room_id), room_id=room_id)
            )
            self.assertEqual(200, got.status)
            _validate("room-lighting-config.schema.json", got.payload)
            self.assertEqual("Тамбур", got.payload["name"])

            changed = _config_payload(room_id, name="Другое")
            changed["version"] = 1
            updated = await self.config_view.put(
                _json_request(self._config_path(room_id), changed, room_id=room_id)
            )
            self.assertEqual(2, updated.payload["version"])

            conflict = _config_payload(room_id, name="Старое")
            conflict["version"] = 1
            stale = await self.config_view.put(
                _json_request(self._config_path(room_id), conflict, room_id=room_id)
            )
            self.assertEqual(409, stale.status)
            self.assertEqual("revision_conflict", stale.payload["code"])

        asyncio.run(flow())

    def test_legacy_put_preserves_existing_manual_priority(self) -> None:
        async def flow() -> None:
            payload = _config_payload()
            payload["manualControlReturn"] = {"prioritySeconds": 7200}
            created = await self.config_view.put(
                _json_request(self._config_path(), payload, room_id=payload["roomId"])
            )
            self.assertEqual(created.status, 200)
            legacy = _config_payload(name="Новое название")
            updated = await self.config_view.put(
                _json_request(self._config_path(), legacy, room_id=legacy["roomId"])
            )
            self.assertEqual(updated.status, 200)
            self.assertEqual(updated.payload["name"], "Новое название")
            self.assertEqual(updated.payload["manualControlReturn"]["prioritySeconds"], 7200)
        asyncio.run(flow())

    def test_curve_params_creation_normalizes_dimming_target_to_five(self) -> None:
        async def flow() -> None:
            payload = _config_payload()
            payload["profile"] = "day_curve"
            payload["curveParams"] = {
                "confirmedAbsenceSeconds": 600,
                "riseSeconds": 10,
                "dayStart": "09:00",
                "morningRamp": {
                    "startPercent": 20,
                    "fullPercent": 100,
                    "fullBy": "12:00",
                },
                "eveningDimming": {
                    "targetPercent": 5,
                    "targetBy": "21:00",
                    "reserveStart": "20:00",
                },
            }
            del payload["dimming"]["targetPercent"]  # omitted, not zero
            created = await self.config_view.put(
                _json_request(self._config_path(), payload, room_id=payload["roomId"])
            )
            self.assertEqual(created.status, 200)
            # The approved creation rule: a curve room created without an
            # explicit dimming target gets the documented decline goal 5.
            self.assertEqual(created.payload["dimming"]["targetPercent"], 5)
            self.assertEqual(
                created.payload["curveParams"],
                payload["curveParams"],
            )

            # An update that omits the whole block keeps the saved curve.
            update = _config_payload(name="Новое название")
            update["version"] = created.payload["version"]
            del update["dimming"]["targetPercent"]
            updated = await self.config_view.put(
                _json_request(self._config_path(), update, room_id=update["roomId"])
            )
            self.assertEqual(updated.status, 200)
            self.assertEqual(updated.payload["curveParams"], payload["curveParams"])
            self.assertEqual(updated.payload["dimming"]["targetPercent"], 5)
        asyncio.run(flow())

    def test_curve_params_creation_respects_explicit_zero_target(self) -> None:
        async def flow() -> None:
            payload = _config_payload()
            payload["profile"] = "day_curve"
            payload["curveParams"] = {"riseSeconds": 10}
            payload["dimming"]["targetPercent"] = 0
            created = await self.config_view.put(
                _json_request(self._config_path(), payload, room_id=payload["roomId"])
            )
            self.assertEqual(created.status, 200)
            self.assertEqual(created.payload["dimming"]["targetPercent"], 0)
        asyncio.run(flow())

    def test_curve_params_forms_never_mix_over_http(self) -> None:
        async def flow() -> None:
            payload = _config_payload()
            payload["profile"] = "day_curve"
            payload["curveParams"] = {"eveningMaxPercent": 40, "riseSeconds": 10}
            rejected = await self.config_view.put(
                _json_request(self._config_path(), payload, room_id=payload["roomId"])
            )
            self.assertEqual(rejected.status, 400)
            self.assertIn("curveParams", str(rejected.payload))
        asyncio.run(flow())

    def test_curve_params_explicit_null_is_rejected_over_http(self) -> None:
        """AUD-CURVE-01: an explicit ``curveParams: null`` must be a 400 —
        only the MISSING key preserves a saved block, so accepting null
        would silently erase the saved ceiling (the live tambur document
        carries the legacy evening maximum)."""
        async def flow() -> None:
            payload = _config_payload()
            payload["profile"] = "day_curve"
            payload["curveParams"] = {"eveningMaxPercent": 40}
            created = await self.config_view.put(
                _json_request(self._config_path(), payload, room_id=payload["roomId"])
            )
            self.assertEqual(created.status, 200)
            update = _config_payload(name="Новое название")
            update["version"] = created.payload["version"]
            update["curveParams"] = None
            rejected = await self.config_view.put(
                _json_request(self._config_path(), update, room_id=update["roomId"])
            )
            self.assertEqual(rejected.status, 400)
            self.assertIn("curveParams", str(rejected.payload))
            got = await self.config_view.get(
                _request(self._config_path(), room_id=payload["roomId"])
            )
            self.assertEqual(got.status, 200)
            self.assertEqual(got.payload["curveParams"], {"eveningMaxPercent": 40})
        asyncio.run(flow())

    def test_put_rejects_revision_changed_by_other_service_writer(self) -> None:
        async def flow() -> None:
            from custom_components.hausman_hub.application.room_lighting_service import RoomLightingService
            from custom_components.hausman_hub.domain.room_lighting import config_from_payload

            lock = asyncio.Lock()
            service = RoomLightingService(self.store, mutation_lock=lock)
            self.hass.data["hausman_hub"][self.api.DATA_ROOM_LIGHTING_SERVICE] = service
            original = _config_payload()
            await service.async_put_config(config_from_payload(original))
            await lock.acquire()
            fresh = _config_payload(name="Изменено другим действием")
            direct = asyncio.create_task(service.async_put_config(config_from_payload(fresh)))
            old = _config_payload(name="Устаревшее изменение клиента")
            http = asyncio.create_task(self.config_view.put(
                _json_request(self._config_path(), old, room_id=old["roomId"])
            ))
            await asyncio.sleep(0)
            lock.release()
            await direct
            response = await http
            self.assertEqual(response.status, 409)
            self.assertEqual(response.payload["code"], "revision_conflict")
            self.assertEqual(response.payload["details"]["actualRevision"], 2)
            stored = await service.async_get_config(old["roomId"])
            self.assertEqual(stored.name, "Изменено другим действием")
            self.assertEqual(stored.version, 2)
        asyncio.run(flow())

    def test_manual_priority_explicit_update_legacy_create_and_noop(self) -> None:
        async def flow() -> None:
            payload = _config_payload()
            created = await self.config_view.put(
                _json_request(self._config_path(), payload, room_id=payload["roomId"])
            )
            self.assertEqual(created.status, 200)
            self.assertEqual(created.payload["manualControlReturn"]["prioritySeconds"], 3600)
            payload["manualControlReturn"] = {"prioritySeconds": 7200}
            changed = await self.config_view.put(
                _json_request(self._config_path(), payload, room_id=payload["roomId"])
            )
            self.assertEqual(changed.status, 200)
            self.assertEqual(changed.payload["version"], 2)
            legacy = _config_payload()
            legacy["version"] = 2
            unchanged = await self.config_view.put(
                _json_request(self._config_path(), legacy, room_id=legacy["roomId"])
            )
            self.assertEqual(unchanged.status, 200)
            self.assertEqual(unchanged.payload, changed.payload)
            payload["version"] = 2
            payload["manualControlReturn"] = {"prioritySeconds": 5400}
            explicit = await self.config_view.put(
                _json_request(self._config_path(), payload, room_id=payload["roomId"])
            )
            self.assertEqual(explicit.status, 200)
            self.assertEqual(explicit.payload["manualControlReturn"]["prioritySeconds"], 5400)
            self.assertEqual(explicit.payload["version"], 3)
        asyncio.run(flow())

    def test_guarded_put_keeps_manual_graph_closed_until_durable_save(self) -> None:
        """CRUD safety only; status contract validation is a separate known gap."""
        async def flow() -> None:
            from custom_components.hausman_hub.application.manual_light_authority import ManualLightAuthority
            from custom_components.hausman_hub.application.related_light_sources import RelatedLightSources
            from custom_components.hausman_hub.application.room_lighting_service import RoomLightingService
            from custom_components.hausman_hub.domain.room_lighting import config_from_payload

            initial = config_from_payload(_config_payload())
            store = _DelayedMemoryStore(initial)
            authority = ManualLightAuthority()
            authority.set_related_sources(RelatedLightSources.from_configs((initial,)))
            service = RoomLightingService(store, topology_invalidator=authority.invalidate_related_sources)
            self.hass.data["hausman_hub"][self.api.DATA_ROOM_LIGHTING_SERVICE] = service
            self.hass.data["hausman_hub"]["manual_light_authority"] = authority
            changed = _config_payload(name="Изменённая конфигурация")
            changed["devices"]["light_targets"][0]["entityId"] = "light.demo_changed"
            pending = asyncio.create_task(self.config_view.put(
                _json_request(self._config_path(), changed, room_id=changed["roomId"])
            ))
            await store.upsert_started.wait()
            try:
                authority.set_related_sources(RelatedLightSources.from_configs((initial,)))
                self.assertEqual(authority.barrier_reason_for("light.demo_main"), "attribution_uncertain")
                self.assertEqual(store.configs[initial.room_id], initial)
                status = await self.status_view.get(_request(
                    self.api.ROOM_LIGHTING_STATUS_PATH.format(room_id=initial.room_id), room_id=initial.room_id,
                ))
                self.assertEqual(status.status, 200)
                self.assertTrue(status.payload["manual_control_return"]["active"])
                self.assertEqual(status.payload["manual_control_return"]["reason"], "attribution_uncertain")
            finally:
                store.release.set()
                response = await pending
            self.assertEqual(response.status, 200)
            self.assertEqual(response.payload["version"], 2)
            self.assertEqual(store.configs[initial.room_id].devices.light_targets[0].entity_id, "light.demo_changed")
        asyncio.run(flow())

    def test_invalid_config_returns_readable_400_without_details(self) -> None:
        async def flow() -> None:
            room_id = "room_demo_entry"
            payload = _config_payload(room_id)
            payload["schedule"][0]["how"]["brightness"] = 101
            response = await self.config_view.put(
                _json_request(self._config_path(room_id), payload, room_id=room_id)
            )
            self.assertEqual(400, response.status)
            self.assertEqual("invalid_request", response.payload["code"])
            self.assertIn("Конфигурация освещения", response.payload["message"])
            details = response.payload.get("details", {})
            self.assertNotIn("violations", details)

        asyncio.run(flow())

    def test_status_matches_contract_schema(self) -> None:
        async def flow() -> None:
            room_id = "room_demo_entry"
            await self.config_view.put(
                _json_request(
                    self._config_path(room_id), _config_payload(room_id), room_id=room_id
                )
            )
            status = await self.status_view.get(
                _request(
                    self.api.ROOM_LIGHTING_STATUS_PATH.format(room_id=room_id),
                    room_id=room_id,
                )
            )
            self.assertEqual(200, status.status)
            _validate("room-lighting-status.schema.json", status.payload)
            self.assertNotIn("commandsEnabled", status.payload)
            self.assertNotIn("shadowDecision", status.payload)

        asyncio.run(flow())

    def test_status_reports_each_manual_return_source_without_false_countdown(self) -> None:
        from custom_components.hausman_hub.application.manual_light_authority import (
            ManualLightAuthority,
        )

        async def flow() -> None:
            room_id = "room_demo_entry"
            config = _config_payload(room_id)
            config["devices"]["light_targets"].append({
                "id": "switch_lamp", "name": "Ручной свет", "kind": "switch",
                "entityId": "switch.demo_lamp", "role": None, "groupId": None,
                "brightness": False, "color_temperature": False,
                "autoControl": False, "autoAdoptOverride": None,
            })
            response = await self.config_view.put(
                _json_request(self._config_path(room_id), config, room_id=room_id)
            )
            self.assertEqual(200, response.status)
            authority = ManualLightAuthority()
            authority.capture(
                "light.demo_main", "on", "service", "context-1",
                observed_at_ms=1_000,
            )
            self.hass.data["hausman_hub"]["manual_light_authority"] = authority
            request = _request(
                self.api.ROOM_LIGHTING_STATUS_PATH.format(room_id=room_id),
                room_id=room_id,
            )
            status = await self.status_view.get(request)
            self.assertEqual(200, status.status)
            returned = status.payload["manual_control_return"]
            self.assertTrue(returned["active"])
            self.assertEqual("manual_only_target", returned["reason"])
            self.assertEqual("blocked", returned["scope"])
            self.assertIsNone(returned["expires_at"])
            self.assertIsNone(returned["remaining_seconds"])
            self.assertIsNone(returned["since"])
            self.assertEqual({
                "light.demo_main": "manual_on_wait",
                "switch.demo_lamp": "manual_only_target",
            }, {source["entityId"]: source["reason"] for source in returned["sources"]})
            self.assertTrue(all(source["scope"] == "blocked" for source in returned["sources"]))
            _validate("room-lighting-status.schema.json", status.payload)

            authority.mark_unhealthy()
            failed = (await self.status_view.get(request)).payload["manual_control_return"]
            self.assertEqual("storage_unavailable", failed["reason"])
            self.assertTrue(all(
                source["reason"] == "storage_unavailable"
                for source in failed["sources"]
            ))
            self.assertIsNone(failed["remaining_seconds"])
            _validate("room-lighting-status.schema.json", {
                **status.payload, "manual_control_return": failed,
            })

            class _RestoredStore:
                recovered_previous = False

                async def async_load(self):
                    return {"version": 2, "records": [], "recentFingerprints": []}

                async def async_save(self, _payload):
                    return None

            restored = ManualLightAuthority(_RestoredStore())
            await restored.async_load()
            self.hass.data["hausman_hub"]["manual_light_authority"] = restored
            restart = (await self.status_view.get(request)).payload
            self.assertEqual(
                "restart_uncertain", restart["manual_control_return"]["reason"]
            )
            self.assertIsNone(
                restart["manual_control_return"]["remaining_seconds"]
            )
            _validate("room-lighting-status.schema.json", restart)

        asyncio.run(flow())

    def test_status_manual_off_and_inactive_return_sources(self) -> None:
        from custom_components.hausman_hub.application.manual_light_authority import (
            ManualLightAuthority,
        )

        async def flow() -> None:
            room_id = "room_demo_entry"
            response = await self.config_view.put(
                _json_request(
                    self._config_path(room_id), _config_payload(room_id),
                    room_id=room_id,
                )
            )
            self.assertEqual(200, response.status)
            authority = ManualLightAuthority()
            self.hass.data["hausman_hub"]["manual_light_authority"] = authority
            request = _request(
                self.api.ROOM_LIGHTING_STATUS_PATH.format(room_id=room_id),
                room_id=room_id,
            )
            inactive = (await self.status_view.get(request)).payload
            self.assertEqual({
                "active": False, "reason": "none", "expires_at": None,
                "scope": "normal", "remaining_seconds": 0, "since": None,
                "sources": [{
                    "entityId": "light.demo_main", "active": False,
                    "reason": "none", "scope": "normal",
                }],
            }, inactive["manual_control_return"])
            _validate("room-lighting-status.schema.json", inactive)

            authority.capture(
                "light.demo_main", "off", "service", "context-off",
                observed_at_ms=2_000,
            )
            manual_off = (await self.status_view.get(request)).payload
            returned = manual_off["manual_control_return"]
            self.assertTrue(returned["active"])
            self.assertEqual("manual_off_wait", returned["reason"])
            self.assertEqual("blocked", returned["scope"])
            self.assertEqual(2, returned["since"])
            self.assertIsNone(returned["remaining_seconds"])
            self.assertEqual("manual_off_wait", returned["sources"][0]["reason"])
            self.assertEqual("blocked", returned["sources"][0]["scope"])
            _validate("room-lighting-status.schema.json", manual_off)

            initial = ManualLightAuthority()
            initial.capture_legacy(
                "light.demo_main", source="startup_unobserved"
            )
            self.hass.data["hausman_hub"]["manual_light_authority"] = initial
            startup = (await self.status_view.get(request)).payload
            uncertain = startup["manual_control_return"]
            self.assertTrue(uncertain["active"])
            self.assertEqual("restart_uncertain", uncertain["reason"])
            self.assertEqual(
                "startup_unobserved", uncertain["sources"][0]["source"]
            )
            self.assertIsNone(uncertain["since"])
            self.assertIsNone(uncertain["remaining_seconds"])
            _validate("room-lighting-status.schema.json", startup)

        asyncio.run(flow())

    def test_status_deduplicates_aliases_and_fails_closed_on_conflict(self) -> None:
        from custom_components.hausman_hub.application.manual_light_authority import (
            ManualLightAuthority,
        )

        async def flow() -> None:
            room_id = "room_demo_entry"
            config = _config_payload(room_id)
            alias = {
                "id": "light_demo_main_alias", "name": "Люстра, второй ID",
                "kind": "light", "entityId": "light.demo_main", "role": "other",
                "groupId": None, "brightness": True, "color_temperature": True,
                "autoControl": True, "autoAdoptOverride": None,
            }
            config["devices"]["light_targets"].append(alias)
            response = await self.config_view.put(
                _json_request(self._config_path(room_id), config, room_id=room_id)
            )
            self.assertEqual(200, response.status)
            authority = ManualLightAuthority()
            authority.capture("light.demo_main", "on", "service", "alias", observed_at_ms=1_000)
            self.hass.data["hausman_hub"]["manual_light_authority"] = authority
            request = _request(
                self.api.ROOM_LIGHTING_STATUS_PATH.format(room_id=room_id), room_id=room_id
            )
            deduplicated = (await self.status_view.get(request)).payload
            returned = deduplicated["manual_control_return"]
            self.assertEqual(["light.demo_main"], [
                source["entityId"] for source in returned["sources"]
            ])
            self.assertEqual("manual_on_wait", returned["reason"])
            self.assertEqual(1, returned["since"])
            _validate("room-lighting-status.schema.json", deduplicated)

            alias["autoControl"] = False
            response = await self.config_view.put(
                _json_request(self._config_path(room_id), config, room_id=room_id)
            )
            self.assertEqual(200, response.status)
            conflicted = (await self.status_view.get(request)).payload
            returned = conflicted["manual_control_return"]
            self.assertEqual("attribution_uncertain", returned["reason"])
            self.assertEqual("blocked", returned["scope"])
            self.assertIsNone(returned["since"])
            self.assertEqual("attribution_uncertain", returned["sources"][0]["reason"])
            _validate("room-lighting-status.schema.json", conflicted)

        asyncio.run(flow())

    def test_status_aggregate_since_comes_from_selected_reason_source(self) -> None:
        from custom_components.hausman_hub.application.manual_light_authority import (
            ManualLightAuthority,
        )

        async def flow() -> None:
            room_id = "room_demo_entry"
            config = _config_payload(room_id)
            config["devices"]["light_targets"].append({
                "id": "light_demo_secondary", "name": "Второй свет", "kind": "light",
                "entityId": "light.demo_secondary", "role": "other", "groupId": None,
                "brightness": True, "color_temperature": False,
                "autoControl": True, "autoAdoptOverride": None,
            })
            response = await self.config_view.put(
                _json_request(self._config_path(room_id), config, room_id=room_id)
            )
            self.assertEqual(200, response.status)
            authority = ManualLightAuthority()
            authority.capture_legacy("light.demo_main", source="startup_unobserved")
            authority.capture(
                "light.demo_secondary", "off", "service", "manual-off", observed_at_ms=2_000
            )
            self.hass.data["hausman_hub"]["manual_light_authority"] = authority
            request = _request(
                self.api.ROOM_LIGHTING_STATUS_PATH.format(room_id=room_id), room_id=room_id
            )
            status = (await self.status_view.get(request)).payload
            returned = status["manual_control_return"]
            self.assertEqual("restart_uncertain", returned["reason"])
            self.assertEqual("blocked", returned["scope"])
            self.assertIsNone(returned["since"])
            self.assertIsNone(returned["remaining_seconds"])
            _validate("room-lighting-status.schema.json", status)

        asyncio.run(flow())

    def test_status_projects_related_sibling_manual_evidence(self) -> None:
        from custom_components.hausman_hub.application.manual_light_authority import ManualLightAuthority
        from custom_components.hausman_hub.application.related_light_sources import RelatedLightSources

        async def flow() -> None:
            room_id = "room_demo_entry"
            config = _config_payload(room_id)
            config["devices"]["light_targets"].append({
                "id": "switch_related", "name": "Связанный свет", "kind": "switch",
                "entityId": "switch.demo_related", "role": "other", "groupId": None,
                "brightness": False, "color_temperature": False,
                "autoControl": True, "autoAdoptOverride": None,
            })
            response = await self.config_view.put(_json_request(self._config_path(room_id), config, room_id=room_id))
            self.assertEqual(200, response.status)
            authority = ManualLightAuthority()
            graph = RelatedLightSources.from_configs((
                SimpleNamespace(room_id=room_id, devices=SimpleNamespace(light_targets=(
                    SimpleNamespace(entity_id="light.demo_main"), SimpleNamespace(entity_id="switch.demo_related"),
                ))),
            ))
            authority.set_related_sources(graph)
            authority.capture("light.demo_main", "on", "service", "related-api", observed_at_ms=2_000)
            self.hass.data["hausman_hub"]["manual_light_authority"] = authority
            request = _request(self.api.ROOM_LIGHTING_STATUS_PATH.format(room_id=room_id), room_id=room_id)
            status = (await self.status_view.get(request)).payload
            returned = status["manual_control_return"]
            self.assertEqual(("blocked", "manual_on_wait", 2), (returned["scope"], returned["reason"], returned["since"]))
            sibling = next(item for item in returned["sources"] if item["entityId"] == "switch.demo_related")
            self.assertEqual((True, "blocked", "manual_on_wait"), (sibling["active"], sibling["scope"], sibling["reason"]))
            _validate("room-lighting-status.schema.json", status)

        asyncio.run(flow())

    def test_status_projects_selector_uncertainty_to_related_sibling(self) -> None:
        from custom_components.hausman_hub.application.manual_light_authority import ManualLightAuthority
        from custom_components.hausman_hub.application.related_light_sources import RelatedLightSources

        async def flow() -> None:
            room_id = "room_demo_entry"
            config = _config_payload(room_id)
            config["devices"]["light_targets"].append({
                "id": "switch_related", "name": "Связанный свет", "kind": "switch",
                "entityId": "switch.demo_related", "role": "other", "groupId": None,
                "brightness": False, "color_temperature": False,
                "autoControl": True, "autoAdoptOverride": None,
            })
            response = await self.config_view.put(_json_request(self._config_path(room_id), config, room_id=room_id))
            self.assertEqual(200, response.status)
            authority = ManualLightAuthority()
            authority.set_related_sources(RelatedLightSources.from_configs((
                SimpleNamespace(room_id=room_id, devices=SimpleNamespace(light_targets=(
                    SimpleNamespace(entity_id="light.demo_main"), SimpleNamespace(entity_id="switch.demo_related"),
                ))),
            )))
            authority.mark_selector_uncertain("light")
            self.hass.data["hausman_hub"]["manual_light_authority"] = authority
            request = _request(self.api.ROOM_LIGHTING_STATUS_PATH.format(room_id=room_id), room_id=room_id)
            status = (await self.status_view.get(request)).payload
            returned = status["manual_control_return"]
            self.assertEqual((True, "selector_uncertain", "blocked"), (
                returned["active"], returned["reason"], returned["scope"],
            ))
            self.assertEqual({
                "light.demo_main": "selector_uncertain",
                "switch.demo_related": "selector_uncertain",
            }, {source["entityId"]: source["reason"] for source in returned["sources"]})
            _validate("room-lighting-status.schema.json", status)

        asyncio.run(flow())

    def test_registered_service_closes_fresh_config_until_runtime_rebuilds_graph(self) -> None:
        """A saved room must not inherit the previous topology's authority."""
        from custom_components.hausman_hub.application.manual_light_authority import (
            ManualLightAuthority,
        )
        from custom_components.hausman_hub.application.related_light_sources import (
            RelatedLightSources,
        )
        from custom_components.hausman_hub.domain.room_lighting import (
            config_from_payload,
        )

        async def flow() -> None:
            hass = FakeHomeAssistant()
            authority = ManualLightAuthority()
            hass.data["hausman_hub"] = {"manual_light_authority": authority}
            self.api.register_room_lighting_api(hass, "entry_graph_invalidation")
            service = hass.data["hausman_hub"][self.api.DATA_ROOM_LIGHTING_SERVICE]
            self.assertIsNotNone(service)

            config = _config_payload("room_graph_invalidation")
            saved = await service.async_put_config(config_from_payload(config))
            authority.set_related_sources(RelatedLightSources.from_configs((saved,)))
            self.assertEqual(
                "attribution_uncertain",
                authority.barrier_reason_for("light.demo_main"),
            )

            status = await self.api.RoomLightingStatusView(hass).get(
                _request(
                    self.api.ROOM_LIGHTING_STATUS_PATH.format(
                        room_id="room_graph_invalidation"
                    ),
                    room_id="room_graph_invalidation",
                )
            )
            self.assertEqual(200, status.status)
            returned = status.payload["manual_control_return"]
            self.assertEqual("attribution_uncertain", returned["reason"])
            self.assertEqual("attribution_uncertain", returned["sources"][0]["reason"])
            _validate("room-lighting-status.schema.json", status.payload)

        asyncio.run(flow())

    def test_pending_changed_config_cannot_reopen_stale_manual_graph(self) -> None:
        """Status stays fail-closed while a changed topology is being saved."""
        from custom_components.hausman_hub.application.manual_light_authority import (
            ManualLightAuthority,
        )
        from custom_components.hausman_hub.application.related_light_sources import (
            RelatedLightSources,
        )
        from custom_components.hausman_hub.application.room_lighting_service import (
            RoomLightingService,
        )
        from custom_components.hausman_hub.domain.room_lighting import (
            config_from_payload,
        )

        async def flow() -> None:
            room_id = "room_demo_entry"
            initial = config_from_payload(_config_payload(room_id))
            store = _DelayedMemoryStore(initial)
            authority = ManualLightAuthority()
            authority.set_related_sources(RelatedLightSources.from_configs((initial,)))
            self.hass.data["hausman_hub"][self.api.DATA_ROOM_LIGHTING_SERVICE] = (
                RoomLightingService(
                    store,
                    topology_invalidator=authority.invalidate_related_sources,
                )
            )
            self.hass.data["hausman_hub"]["manual_light_authority"] = authority

            changed = _config_payload(room_id)
            changed["devices"]["light_targets"].append({
                "id": "light_secondary", "name": "Второй свет", "kind": "light",
                "entityId": "light.demo_secondary", "role": "other", "groupId": None,
                "brightness": True, "color_temperature": False,
                "autoControl": True, "autoAdoptOverride": None,
            })
            put = asyncio.create_task(self.config_view.put(
                _json_request(self._config_path(room_id), changed, room_id=room_id)
            ))
            await store.upsert_started.wait()

            authority.set_related_sources(RelatedLightSources.from_configs((initial,)))
            self.assertEqual(
                "attribution_uncertain", authority.barrier_reason_for("light.demo_main")
            )
            status = await self.status_view.get(
                _request(
                    self.api.ROOM_LIGHTING_STATUS_PATH.format(room_id=room_id),
                    room_id=room_id,
                )
            )
            self.assertEqual(200, status.status)
            returned = status.payload["manual_control_return"]
            self.assertEqual("attribution_uncertain", returned["reason"])
            self.assertTrue(returned["active"])
            self.assertEqual("attribution_uncertain", returned["sources"][0]["reason"])
            _validate("room-lighting-status.schema.json", status.payload)

            store.release.set()
            saved = await put
            self.assertEqual(200, saved.status)

        asyncio.run(flow())

    def test_status_uses_the_running_runtime_context(self) -> None:
        from datetime import datetime, timezone

        now = int(datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc).timestamp() * 1000)
        calls: list[object] = []

        class _Runtime:
            running = True

            async def async_context_for(self, config):
                calls.append(config.room_id)
                return RoomLightingApiTest._context(now)

        async def flow() -> None:
            room_id = "room_demo_entry"
            await self.config_view.put(
                _json_request(
                    self._config_path(room_id), _config_payload(room_id), room_id=room_id
                )
            )
            self.hass.data["hausman_hub"][
                self.api.DATA_ROOM_LIGHTING_RUNTIME
            ] = _Runtime()
            status = await self.status_view.get(
                _request(
                    self.api.ROOM_LIGHTING_STATUS_PATH.format(room_id=room_id),
                    room_id=room_id,
                )
            )
            self.assertEqual(200, status.status)
            _validate("room-lighting-status.schema.json", status.payload)
            self.assertTrue(status.payload["fresh"])
            self.assertEqual("idle", status.payload["phase"])
            self.assertEqual([room_id], calls)

        asyncio.run(flow())

    def test_status_away_uses_runtime_flag(self) -> None:
        from datetime import datetime, timezone

        now = int(datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc).timestamp() * 1000)

        class _Runtime:
            running = True
            away = True

            async def async_context_for(self, config):
                return RoomLightingApiTest._context(now)

        async def flow() -> None:
            room_id = "room_demo_entry"
            await self.config_view.put(
                _json_request(
                    self._config_path(room_id), _config_payload(room_id), room_id=room_id
                )
            )
            self.hass.data["hausman_hub"][
                self.api.DATA_ROOM_LIGHTING_RUNTIME
            ] = _Runtime()
            status = await self.status_view.get(
                _request(
                    self.api.ROOM_LIGHTING_STATUS_PATH.format(room_id=room_id),
                    room_id=room_id,
                )
            )
            self.assertEqual(200, status.status)
            self.assertTrue(status.payload["away"])

            # Without a running runtime the config fallback applies.
            self.hass.data["hausman_hub"].pop(
                self.api.DATA_ROOM_LIGHTING_RUNTIME, None
            )
            fallback = await self.status_view.get(
                _request(
                    self.api.ROOM_LIGHTING_STATUS_PATH.format(room_id=room_id),
                    room_id=room_id,
                )
            )
            self.assertEqual(200, fallback.status)
            self.assertFalse(fallback.payload["away"])

        asyncio.run(flow())

    def test_templates_are_full_contract_documents(self) -> None:
        async def flow() -> None:
            room_id = "room_demo_entry"
            templates = await self.templates_view.get(
                _request(
                    self.api.ROOM_LIGHTING_TEMPLATES_PATH.format(room_id=room_id),
                    room_id=room_id,
                )
            )
            self.assertEqual(200, templates.status)
            self.assertTrue(templates.payload["templates"])
            for document in templates.payload["templates"]:
                _validate("room-lighting-template.schema.json", document)

        asyncio.run(flow())

    def test_apply_template_returns_valid_config_without_null_entity(self) -> None:
        async def flow() -> None:
            room_id = "room_demo_entry"
            response = await self.apply_view.post(
                _json_request(
                    self.api.ROOM_LIGHTING_TEMPLATE_APPLY_PATH.format(room_id=room_id),
                    {"templateId": "day_profile", "keepDevices": True},
                    room_id=room_id,
                )
            )
            self.assertEqual(200, response.status)
            _validate("room-lighting-config.schema.json", response.payload)
            for target in response.payload["devices"]["light_targets"]:
                if "entityId" in target:
                    self.assertIsNotNone(target["entityId"])

        asyncio.run(flow())

    def test_unbound_template_target_omits_optional_entity_id(self) -> None:
        async def flow() -> None:
            room_id = "room_demo_entry"
            response = await self.apply_view.post(_json_request(
                self.api.ROOM_LIGHTING_TEMPLATE_APPLY_PATH.format(room_id=room_id),
                {"templateId": "day_profile", "keepDevices": True}, room_id=room_id,
            ))
            self.assertEqual(response.status, 200)
            _validate("room-lighting-config.schema.json", response.payload)
            self.assertTrue(response.payload["devices"]["light_targets"])
            for target in response.payload["devices"]["light_targets"]:
                self.assertNotIn("entityId", target)
        asyncio.run(flow())

    def test_live_test_safe_is_background_and_command_free(self) -> None:
        async def flow() -> None:
            room_id = "room_demo_entry"
            spy = _SpyExecutor()
            self.hass.data["hausman_hub"][self.api.DATA_ROOM_LIGHTING_EXECUTOR] = spy
            await self.config_view.put(
                _json_request(
                    self._config_path(room_id), _config_payload(room_id), room_id=room_id
                )
            )
            response = await self.live_view.post(
                _json_request(
                    self.api.ROOM_LIGHTING_LIVE_TESTS_PATH.format(room_id=room_id),
                    {"mode": "safe", "correlationId": "live-api-safe-1"},
                    room_id=room_id,
                )
            )
            self.assertEqual(202, response.status)
            _validate("room-lighting-live-test.schema.json", response.payload)
            self.assertEqual("request", response.payload["kind"])

            registry = self.hass.data["hausman_hub"][
                self.api.DATA_ROOM_LIGHTING_LIVE_TESTS
            ]
            await registry["live-api-safe-1"]["task"]

            path = self.api.ROOM_LIGHTING_LIVE_TEST_PATH.format(
                room_id=room_id, correlation_id="live-api-safe-1"
            )
            result = await self.live_result_view.get(
                _request(path, room_id=room_id, correlation_id="live-api-safe-1")
            )
            self.assertEqual(200, result.status)
            _validate("room-lighting-live-test.schema.json", result.payload)
            self.assertEqual("result", result.payload["kind"])
            self.assertEqual(0, result.payload["result"]["commands_sent"])
            self.assertEqual([], spy.calls)

        asyncio.run(flow())

    def test_live_test_real_without_executor_is_capability_unavailable(self) -> None:
        async def flow() -> None:
            room_id = "room_demo_entry"
            await self.config_view.put(
                _json_request(
                    self._config_path(room_id), _config_payload(room_id), room_id=room_id
                )
            )
            response = await self.live_view.post(
                _json_request(
                    self.api.ROOM_LIGHTING_LIVE_TESTS_PATH.format(room_id=room_id),
                    {"mode": "real", "correlationId": "live-api-real-1"},
                    room_id=room_id,
                )
            )
            self.assertEqual(409, response.status)
            self.assertEqual("capability_unavailable", response.payload["code"])

        asyncio.run(flow())

    def test_live_test_real_with_executor_records_receipts(self) -> None:
        async def flow() -> None:
            room_id = "room_demo_entry"
            spy = _SpyExecutor()
            self.hass.data["hausman_hub"][self.api.DATA_ROOM_LIGHTING_EXECUTOR] = spy
            await self.config_view.put(
                _json_request(
                    self._config_path(room_id), _config_payload(room_id), room_id=room_id
                )
            )
            response = await self.live_view.post(
                _json_request(
                    self.api.ROOM_LIGHTING_LIVE_TESTS_PATH.format(room_id=room_id),
                    {"mode": "real", "correlationId": "live-api-real-2"},
                    room_id=room_id,
                )
            )
            self.assertEqual(202, response.status)
            registry = self.hass.data["hausman_hub"][
                self.api.DATA_ROOM_LIGHTING_LIVE_TESTS
            ]
            await registry["live-api-real-2"]["task"]
            path = self.api.ROOM_LIGHTING_LIVE_TEST_PATH.format(
                room_id=room_id, correlation_id="live-api-real-2"
            )
            result = await self.live_result_view.get(
                _request(path, room_id=room_id, correlation_id="live-api-real-2")
            )
            self.assertEqual(200, result.status)
            _validate("room-lighting-live-test.schema.json", result.payload)
            self.assertTrue(result.payload["result"]["commands_sent"] > 0)
            self.assertTrue(spy.calls)

        asyncio.run(flow())

    def test_live_test_real_away_block_is_failed_and_never_counts_a_command(self) -> None:
        """A runtime pre-executor Away block is a terminal real-test failure."""

        class _AwayBlockedExecutor:
            def __init__(self) -> None:
                self.calls: list[object] = []

            async def __call__(self, command: object) -> dict[str, object]:
                self.calls.append(command)
                return {
                    "blocked": True,
                    "confirmed": False,
                    "sent": False,
                    "sendOutcome": "not_sent",
                    "reason": "away_sensor_unavailable",
                }

        async def flow() -> None:
            room_id = "room_demo_entry"
            executor = _AwayBlockedExecutor()
            self.hass.data["hausman_hub"][self.api.DATA_ROOM_LIGHTING_EXECUTOR] = executor
            await self.config_view.put(
                _json_request(
                    self._config_path(room_id), _config_payload(room_id), room_id=room_id
                )
            )
            response = await self.live_view.post(
                _json_request(
                    self.api.ROOM_LIGHTING_LIVE_TESTS_PATH.format(room_id=room_id),
                    {"mode": "real", "correlationId": "live-api-away-block-1"},
                    room_id=room_id,
                )
            )
            self.assertEqual(202, response.status)
            registry = self.hass.data["hausman_hub"][
                self.api.DATA_ROOM_LIGHTING_LIVE_TESTS
            ]
            await registry["live-api-away-block-1"]["task"]
            path = self.api.ROOM_LIGHTING_LIVE_TEST_PATH.format(
                room_id=room_id, correlation_id="live-api-away-block-1"
            )
            result = await self.live_result_view.get(
                _request(path, room_id=room_id, correlation_id="live-api-away-block-1")
            )
            self.assertEqual(200, result.status)
            _validate("room-lighting-live-test.schema.json", result.payload)
            trace = result.payload["result"]
            self.assertEqual("failed", trace["status"])
            self.assertEqual("sensor_unavailable", trace["reason"])
            self.assertEqual(0, trace["commands_sent"])
            self.assertEqual(
                "away_sensor_unavailable", result.payload["steps"][-1]["detail"]
            )
            self.assertEqual(1, len(executor.calls))

        asyncio.run(flow())

    def test_live_test_background_exception_publishes_terminal_failed_trace(self) -> None:
        """Unexpected runner failures must not leave a polling client pending."""

        async def broken_run(*_args, **_kwargs):
            raise RuntimeError("unexpected runner failure")

        async def flow() -> None:
            room_id = "room_demo_entry"
            await self.config_view.put(
                _json_request(
                    self._config_path(room_id), _config_payload(room_id), room_id=room_id
                )
            )
            original = self.api.RoomLightingLiveTestRunner.run
            self.api.RoomLightingLiveTestRunner.run = broken_run
            try:
                response = await self.live_view.post(
                    _json_request(
                        self.api.ROOM_LIGHTING_LIVE_TESTS_PATH.format(room_id=room_id),
                        {"mode": "safe", "correlationId": "live-api-failure-1"},
                        room_id=room_id,
                    )
                )
                self.assertEqual(202, response.status)
                registry = self.hass.data["hausman_hub"][
                    self.api.DATA_ROOM_LIGHTING_LIVE_TESTS
                ]
                await registry["live-api-failure-1"]["task"]
            finally:
                self.api.RoomLightingLiveTestRunner.run = original

            path = self.api.ROOM_LIGHTING_LIVE_TEST_PATH.format(
                room_id=room_id, correlation_id="live-api-failure-1"
            )
            result = await self.live_result_view.get(
                _request(path, room_id=room_id, correlation_id="live-api-failure-1")
            )
            self.assertEqual(200, result.status)
            _validate("room-lighting-live-test.schema.json", result.payload)
            trace = result.payload["result"]
            self.assertEqual("failed", trace["status"])
            self.assertEqual("unknown", trace["reason"])
            self.assertEqual(0, trace["commands_sent"])

        asyncio.run(flow())

    def test_live_test_cancel(self) -> None:
        async def flow() -> None:
            room_id = "room_demo_entry"
            await self.config_view.put(
                _json_request(
                    self._config_path(room_id), _config_payload(room_id), room_id=room_id
                )
            )
            started = await self.live_view.post(
                _json_request(
                    self.api.ROOM_LIGHTING_LIVE_TESTS_PATH.format(room_id=room_id),
                    {"mode": "safe", "correlationId": "live-api-cancel-1"},
                    room_id=room_id,
                )
            )
            self.assertEqual(202, started.status)
            registry = self.hass.data["hausman_hub"][
                self.api.DATA_ROOM_LIGHTING_LIVE_TESTS
            ]
            entry = registry["live-api-cancel-1"]
            await asyncio.sleep(0)

            path = self.api.ROOM_LIGHTING_LIVE_TEST_PATH.format(
                room_id=room_id, correlation_id="live-api-cancel-1"
            )
            cancelled = await self.live_result_view.post(
                _request(path, room_id=room_id, correlation_id="live-api-cancel-1")
            )
            self.assertEqual(200, cancelled.status)
            _validate("room-lighting-live-test.schema.json", cancelled.payload)
            self.assertEqual("cancelled", cancelled.payload["result"]["status"])

            await entry["task"]
            result = await self.live_result_view.get(
                _request(path, room_id=room_id, correlation_id="live-api-cancel-1")
            )
            self.assertEqual("cancelled", result.payload["result"]["status"])

        asyncio.run(flow())

    def test_unauthorized_is_forbidden(self) -> None:
        async def flow() -> None:
            room_id = "room_demo_entry"
            path = self._config_path(room_id)
            request = FakeRequest("8.8.8.8", reader_user("guest"), path=path)
            request.match_info = {"room_id": room_id}
            response = await self.config_view.get(request)
            self.assertEqual(403, response.status)

        asyncio.run(flow())

    def test_config_omits_absent_entity_id(self) -> None:
        async def flow() -> None:
            room_id = "room_demo_entry"
            payload = _config_payload(room_id)
            payload["devices"]["sensors"] = []
            payload["devices"]["light_targets"][0].pop("entityId")
            put = await self.config_view.put(
                _json_request(self._config_path(room_id), payload, room_id=room_id)
            )
            self.assertEqual(200, put.status)
            _validate("room-lighting-config.schema.json", put.payload)
            self.assertNotIn("entityId", put.payload["devices"]["light_targets"][0])

            got = await self.config_view.get(
                _request(self._config_path(room_id), room_id=room_id)
            )
            _validate("room-lighting-config.schema.json", got.payload)
            self.assertNotIn("entityId", got.payload["devices"]["light_targets"][0])

        asyncio.run(flow())

    def test_status_sensor_state_enum_and_since_seconds(self) -> None:
        from datetime import datetime, time as dt_time, timezone

        from custom_components.hausman_hub.domain.room_lighting import SensorKind
        from custom_components.hausman_hub.domain.room_lighting_engine import (
            LightSnapshot,
            ProtectionSnapshot,
            RoomLightingContext,
            SensorSnapshot,
        )
        from custom_components.hausman_hub.domain.room_lighting_ownership import (
            SensorState,
        )

        async def flow() -> None:
            room_id = "room_demo_entry"
            now_ms = int(datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc).timestamp() * 1000)
            payload = _config_payload(room_id)
            payload["devices"]["sensors"].append(
                {
                    "id": "sensor_demo_lux",
                    "name": "Освещённость",
                    "kind": "illuminance",
                    "entityId": "sensor.demo_lux",
                    "autoAdoptOverride": None,
                }
            )
            payload["illumination"] = {
                "sensor": "sensor.demo_lux",
                "calibration": {"offset": 0, "multiplier": 1},
                "hysteresis": 5,
                "minLux": 0,
                "maxLux": 20000,
                "thresholds": [],
                "failClosed": True,
            }
            await self.config_view.put(
                _json_request(self._config_path(room_id), payload, room_id=room_id)
            )
            status_path = self.api.ROOM_LIGHTING_STATUS_PATH.format(room_id=room_id)

            def provider(lux_healthy: bool, state: SensorState):
                def factory(_config):
                    return RoomLightingContext(
                        now=now_ms,
                        timezone=timezone.utc,
                        sunrise=dt_time(7, 0),
                        sunset=dt_time(19, 0),
                        sensors=(
                            SensorSnapshot(
                                "sensor_demo_lux",
                                SensorKind.ILLUMINANCE,
                                state,
                                now_ms,
                                lux=120.0,
                                lux_healthy=lux_healthy,
                            ),
                        ),
                        lights=(LightSnapshot("light_main", SensorState.OFF, now_ms),),
                        protection=ProtectionSnapshot(
                            active=True,
                            started_at=now_ms,
                            minimum_interval_seconds=600,
                        ),
                    )

                return factory

            self.hass.data["hausman_hub"][
                self.api.DATA_ROOM_LIGHTING_CONTEXT
            ] = provider(True, SensorState.OFF)
            response = await self.status_view.get(
                _request(status_path, room_id=room_id)
            )
            _validate("room-lighting-status.schema.json", response.payload)
            self.assertEqual("ok", response.payload["illumination"]["sensorState"])
            self.assertEqual(
                now_ms // 1000, response.payload["manual_protection"]["since"]
            )

            self.hass.data["hausman_hub"][
                self.api.DATA_ROOM_LIGHTING_CONTEXT
            ] = provider(False, SensorState.OFF)
            stale = await self.status_view.get(_request(status_path, room_id=room_id))
            _validate("room-lighting-status.schema.json", stale.payload)
            self.assertEqual("stale", stale.payload["illumination"]["sensorState"])

        asyncio.run(flow())

    def test_config_rejects_entity_shared_with_another_room(self) -> None:
        async def flow() -> None:
            first = await self.config_view.put(
                _json_request(
                    self._config_path("room_a"),
                    _config_payload("room_a"),
                    room_id="room_a",
                )
            )
            self.assertEqual(200, first.status)
            second = await self.config_view.put(
                _json_request(
                    self._config_path("room_b"),
                    _config_payload("room_b"),
                    room_id="room_b",
                )
            )
            self.assertEqual(400, second.status)
            self.assertEqual("invalid_request", second.payload["code"])

        asyncio.run(flow())

    def test_status_remaining_seconds_is_the_actual_remainder(self) -> None:
        from datetime import datetime, time as dt_time, timezone

        from custom_components.hausman_hub.domain.room_lighting_engine import (
            LightSnapshot,
            ProtectionSnapshot,
            RoomLightingContext,
        )
        from custom_components.hausman_hub.domain.room_lighting_ownership import (
            SensorState,
        )

        async def flow() -> None:
            room_id = "room_demo_entry"
            now_ms = int(
                datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc).timestamp() * 1000
            )
            await self.config_view.put(
                _json_request(
                    self._config_path(room_id),
                    _config_payload(room_id),
                    room_id=room_id,
                )
            )

            def provider(_config):
                return RoomLightingContext(
                    now=now_ms,
                    timezone=timezone.utc,
                    sunrise=dt_time(7, 0),
                    sunset=dt_time(19, 0),
                    lights=(LightSnapshot("light_main", SensorState.OFF, now_ms),),
                    protection=ProtectionSnapshot(
                        active=True,
                        started_at=now_ms - 100_000,
                        minimum_interval_seconds=600,
                    ),
                )

            self.hass.data["hausman_hub"][
                self.api.DATA_ROOM_LIGHTING_CONTEXT
            ] = provider
            response = await self.status_view.get(
                _request(
                    self.api.ROOM_LIGHTING_STATUS_PATH.format(room_id=room_id),
                    room_id=room_id,
                )
            )
            self.assertEqual(200, response.status)
            _validate("room-lighting-status.schema.json", response.payload)
            self.assertEqual(
                500, response.payload["manual_protection"]["remaining_seconds"]
            )

        asyncio.run(flow())

if __name__ == "__main__":
    unittest.main()
