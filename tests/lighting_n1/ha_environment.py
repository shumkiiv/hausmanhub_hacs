"""External HA ports only. No Hausman class, method or decision is patched."""

import asyncio
import contextlib
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import datetime as datetime_module
from enum import StrEnum
import inspect
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import ModuleType, SimpleNamespace
from uuid import uuid4
from unittest.mock import patch


@contextlib.contextmanager
def isolated_directory():
    temporary = tempfile.TemporaryDirectory(prefix="hausman-n1-")
    previous = os.getcwd()
    os.chdir(temporary.name)
    try:
        yield temporary.name
    finally:
        try:
            temporary.cleanup()
        finally:
            os.chdir(previous)


@contextlib.contextmanager
def external_clock(hass, *, virtual_monotonic=False):
    real_datetime = datetime
    started_wall, started_monotonic = hass.now, time.monotonic()
    class ClockDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            value = hass.now
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)
        @classmethod
        def utcnow(cls):
            return hass.now.replace(tzinfo=None)
    hass.now = ClockDateTime.fromtimestamp(hass.now.timestamp(), timezone.utc)
    with patch.object(datetime_module, "datetime", ClockDateTime), \
         patch.object(time, "time", lambda: hass.now.timestamp()), \
         patch.object(time, "time_ns", lambda: int(hass.now.timestamp()) * 1_000_000_000 + hass.now.microsecond * 1000), \
         contextlib.ExitStack() as extra:
        if virtual_monotonic:
            extra.enter_context(patch.object(time, "monotonic", lambda:
                started_monotonic + (hass.now - started_wall).total_seconds()))
        yield


class Context:
    def __init__(self, id=None, user_id=None, parent_id=None):
        self.id = id or uuid4().hex
        self.user_id = user_id
        self.parent_id = parent_id


class Bus:
    def __init__(self, hass):
        self.hass = hass
        self.listeners = defaultdict(list)

    def async_listen(self, kind, callback):
        self.listeners[kind].append(callback)
        def cancel():
            if callback in self.listeners[kind]:
                self.listeners[kind].remove(callback)
        return cancel

    def async_listen_once(self, kind, callback):
        def once(event):
            cancel()
            return callback(event)
        cancel = self.async_listen(kind, once)
        return cancel

    def async_fire(self, kind, data=None, context=None, *, time_fired=None):
        event = SimpleNamespace(event_type=kind, data=data or {}, context=context or Context(),
                                time_fired=time_fired or self.hass.now)
        callbacks = tuple(self.listeners[kind])
        if kind != "*":
            callbacks += tuple(self.listeners["*"])
        for callback in callbacks:
            result = callback(event)
            if inspect.isawaitable(result):
                self.hass.async_create_task(result)


class States:
    def __init__(self, hass):
        self.hass = hass
        self.values = {}

    def get(self, entity):
        return self.values.get(entity)

    def async_all(self, domain_filter=None):
        return [s for s in self.values.values()
                if domain_filter is None or s.entity_id.startswith(domain_filter + ".")]

    def async_entity_ids(self, domain_filter=None):
        return [s.entity_id for s in self.async_all(domain_filter)]

    def async_set(self, entity, value, attributes=None, force_update=False, context=None):
        old = self.get(entity)
        context = context or Context()
        state = SimpleNamespace(entity_id=entity, state=value, attributes=attributes or {},
            last_changed=self.hass.now if old is None or old.state != value else old.last_changed,
            last_updated=self.hass.now, last_reported=self.hass.now, context=context)
        self.values[entity] = state
        self.hass.bus.async_fire("state_changed",
            {"entity_id": entity, "old_state": old, "new_state": state}, context)


class FileStore:
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, hass, version, key, **kwargs):
        self.hass, self.version, self.key = hass, version, key
        self.path = str(Path(hass.config.config_dir) / ".storage" / key)

    async def async_load(self):
        path = Path(self.path)
        if not path.exists():
            return None
        return json.loads(path.read_text())["data"]

    async def async_save(self, data):
        if self.hass.save_hook:
            await self.hass.save_hook(self.key, data)
        path = Path(self.path)
        path.parent.mkdir(exist_ok=True)
        fd, staged = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump({"version": self.version, "minor_version": 1,
                           "key": self.key, "data": data}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(staged, path)
            if self.hass.save_after_hook:
                await self.hass.save_after_hook(self.key, data)
        finally:
            if os.path.exists(staged):
                os.unlink(staged)

    def async_delay_save(self, data_func, delay):
        self.hass.async_create_task(self.async_save(data_func()))


class View:
    def json(self, data, status_code=200, headers=None, **kwargs):
        return SimpleNamespace(status=status_code, data=data, headers=headers or {})

    def json_message(self, message, status_code=200, **kwargs):
        return self.json({"message": message}, status_code, **kwargs)


class Services:
    def __init__(self, hass):
        self.hass = hass
        self.calls = []
        self.registered = {}
        self.report_mode = "full"
        self.hook = None

    def async_register(self, domain, service, handler, **kwargs):
        self.registered[domain, service] = handler

    def has_service(self, domain, service):
        return (domain, service) in self.registered or domain in {"light", "switch"}

    async def async_call(self, domain, service, data=None, blocking=False, context=None, **kwargs):
        data = dict(data or {})
        context = context or Context()
        entities = data.get("entity_id", [])
        if isinstance(entities, str):
            entities = [entities]
        if not entities or domain not in {"light", "switch"} or any(not e.startswith(("light.n1_", "switch.n1_")) for e in entities):
            raise AssertionError("N1 refuses non-synthetic service target")
        self.calls.append((domain, service, data, context.id))
        self.hass.bus.async_fire("call_service", {"domain": domain, "service": service,
                                                 "service_data": data}, context)
        if self.hook:
            await self.hook(domain, service, data, context)
        if self.report_mode == "timeout":
            return
        for entity in entities:
            self.hass.now += timedelta(milliseconds=1)
            old = self.hass.states.get(entity)
            attrs = dict(old.attributes if old else {})
            if self.report_mode == "full":
                if "brightness_pct" in data:
                    attrs["brightness"] = round(data["brightness_pct"] * 255 / 100)
                for key in ("brightness", "color_temp_kelvin", "color_temp"):
                    if key in data:
                        attrs[key] = data[key]
            self.hass.states.async_set(entity, "on" if service == "turn_on" else "off", attrs, context=context)


class Entry:
    domain = "hausman_hub"
    entry_id = "n1_entry"
    data = {"mode": "read-only", "direct_execution_status": "direct_execution_blocked"}
    options = {"local_summary_enabled": False}
    title = "N1 isolated"

    def __init__(self):
        self.unloads = []

    def async_on_unload(self, callback):
        self.unloads.append(callback)

    def add_update_listener(self, callback):
        return lambda: None


class Hass:
    def __init__(self, root):
        self.now = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
        self.config = SimpleNamespace(config_dir=str(root), time_zone="UTC",
                                     latitude=0, longitude=0, country="GB")
        self.config.path = lambda *parts: str(Path(root).joinpath(*parts))
        self.data = {}
        self.tasks = set()
        self.task_errors = []
        self.save_hook = None
        self.save_after_hook = None
        self.executor_hook = None
        self.bus = Bus(self)
        self.states = States(self)
        self.services = Services(self)
        self.views = []
        self.http = SimpleNamespace(register_view=self.views.append,
                                    async_register_static_paths=self.forward)
        self.entry = Entry()
        self.config_entries = SimpleNamespace(async_entries=lambda domain=None: [self.entry],
            async_loaded_entries=lambda domain=None: [self.entry],
            async_forward_entry_setups=self.forward, async_unload_platforms=self.forward,
            async_update_entry=lambda *args, **kwargs: None)
        self.schedules = []
        self.is_running = True
        self.loop = asyncio.get_running_loop()

    async def forward(self, *args):
        return True

    async def async_add_executor_job(self, func, *args):
        if self.executor_hook:
            await self.executor_hook(func, args)
        return await asyncio.to_thread(func, *args)

    def async_create_task(self, coro, *args, **kwargs):
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        def complete(task):
            self.tasks.discard(task)
            if not task.cancelled() and task.exception():
                self.task_errors.append(task.exception())
        task.add_done_callback(complete)
        return task

    async def drain(self):
        for _ in range(50):
            tasks = tuple(task for task in self.tasks if not task.cancelled()
                          and task.get_coro().__name__ != "_async_catalog_warmup")
            if not tasks:
                await asyncio.sleep(0)
                if not self.tasks:
                    break
            else:
                await asyncio.gather(*tasks, return_exceptions=True)
        if self.task_errors:
            raise self.task_errors.pop(0)

    async def unload(self, package):
        for callback in reversed(self.entry.unloads):
            result = callback()
            if inspect.isawaitable(result):
                await result
        self.entry.unloads.clear()
        await self.drain()
        return await package.async_unload_entry(self, self.entry)


class Request(dict):
    def __init__(self, room_id, payload=None):
        super().__init__(hass_user=SimpleNamespace(is_admin=True, system_generated=False))
        self.path = f"/api/hausman_hub/v1/rooms/{room_id}/lighting/force-auto"
        self.query_string = ""
        self.query = {}
        self.remote = "127.0.0.1"
        self.match_info = {"room_id": room_id}
        self.payload = payload
        self.content_type = "application/json"
        self.content_length = len(json.dumps(payload).encode("utf-8"))

    async def json(self):
        return self.payload

    async def read(self):
        return json.dumps(self.payload).encode("utf-8")


def install_ports(hass):
    def module(name, **fields):
        parent, _, leaf = name.rpartition(".")
        if parent and parent not in sys.modules:
            module(parent)
        result = ModuleType(name)
        result.__path__ = []
        result.__dict__.update(fields)
        sys.modules[name] = result
        if parent:
            setattr(sys.modules[parent], leaf, result)
        return result
    class Platform(StrEnum):
        SENSOR = "sensor"
        SWITCH = "switch"
    module("homeassistant.core", Context=Context, HomeAssistant=Hass,
           callback=lambda func: func, Event=SimpleNamespace, State=SimpleNamespace,
           ServiceCall=SimpleNamespace)
    module("homeassistant.config_entries", ConfigEntry=Entry)
    module("homeassistant.const", Platform=Platform, EVENT_HOMEASSISTANT_STARTED="homeassistant_started",
           EVENT_HOMEASSISTANT_STOP="homeassistant_stop", STATE_ON="on", STATE_OFF="off",
           STATE_UNAVAILABLE="unavailable", STATE_UNKNOWN="unknown", MATCH_ALL="*")
    module("homeassistant.components.http", HomeAssistantView=View,
           StaticPathConfig=lambda *args: args)
    module("homeassistant.components.frontend", async_panel_exists=lambda hass, path: False,
           async_remove_panel=lambda *args, **kwargs: None)
    async def panel(*args, **kwargs):
        return None
    module("homeassistant.components.panel_custom", async_register_panel=panel)
    module("homeassistant.auth.const", GROUP_ID_READ_ONLY="system-read-only")
    module("homeassistant.helpers.start", async_at_started=lambda hass, callback: callback(hass))
    module("homeassistant.helpers.storage", Store=FileStore)
    module("homeassistant.util.dt", now=lambda: hass.now, utcnow=lambda: hass.now,
           as_local=lambda value: value, as_utc=lambda value: value.astimezone(timezone.utc),
           DEFAULT_TIME_ZONE=timezone.utc, get_time_zone=lambda name: timezone.utc,
           parse_datetime=datetime.fromisoformat)
    def states(hass, entities, callback):
        entities = {entities} if isinstance(entities, str) else set(entities)
        return hass.bus.async_listen("state_changed", lambda event:
            callback(event) if "*" in entities or event.data.get("entity_id") in entities else None)
    def calendar(hass, callback, **fields):
        record = SimpleNamespace(kind="calendar", callback=callback, fields=fields, active=True)
        hass.schedules.append(record)
        return lambda: setattr(record, "active", False)
    def interval(hass, callback, period):
        record = SimpleNamespace(kind="interval", callback=callback, period=period, active=True,
                                 due=hass.now + period)
        hass.schedules.append(record)
        return lambda: setattr(record, "active", False)
    def later(hass, delay, callback):
        record = SimpleNamespace(kind="later", callback=callback, due=hass.now+timedelta(seconds=delay), active=True)
        hass.schedules.append(record)
        return lambda: setattr(record, "active", False)
    module("homeassistant.helpers.event", async_track_state_change_event=states,
           async_track_time_change=calendar, async_track_time_interval=interval, async_call_later=later,
           async_track_point_in_time=lambda hass, callback, when: later(hass, (when-hass.now).total_seconds(), callback))
    for kind, collection in (("area", "areas"), ("device", "devices"), ("entity", "entities")):
        registry = SimpleNamespace(**{collection: {}})
        if kind == "area":
            registry.async_list_areas = lambda: []
        module("homeassistant.helpers." + kind + "_registry", async_get=lambda hass, r=registry: r,
               async_entries_for_config_entry=lambda registry, entry_id: [])
