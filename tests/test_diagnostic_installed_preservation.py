"""Filesystem preservation evidence for the installed diagnostic overlay."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace

import pytest

from custom_components.hausman_hub.application.manual_light_authority import (
    ManualLightAuthority, valid_manual_light_authority_payload,
)
from custom_components.hausman_hub.application.manual_light_off_protection import (
    ManualLightOffProtectionCoordinator, valid_manual_light_off_protection_payload,
)
from custom_components.hausman_hub.application.room_lighting_runtime import RoomLightingOwnershipJournal
from custom_components.hausman_hub.application.room_lighting_shadow import RoomLightingShadowService
from custom_components.hausman_hub.application.scenario_light_priority import (
    LightAutomationPriority, valid_light_priority_payload,
)
from custom_components.hausman_hub.verified_safety_storage import VerifiedSafetyStore, _write_document_atomic
from tests.test_manual_light_off_protection import _settings
from tests.test_room_lighting_runtime import _NOW_DT, _NOW_MS


WORKSPACE = Path(__file__).resolve().parents[1]
ARTIFACTS = Path('/home/ivsh/projects/Hausman/work/lighting-rooms-20261022')
FROZEN = ARTIFACTS / 'checked-manual-callback-r1'
EXPECTED_BEFORE = "0801e65d511e598270dccaa7009228f88bd29ebae7354cf7e22b173e5d0e6a3c"
EXPECTED_AFTER = "5070f01ddb070efa1f00470140b2bf2b23fe2e6199e854a712c4e861556764aa"
EXCLUDED = {
    "custom_components/hausman_hub/adapters/core/ha_transport.py",
    "custom_components/hausman_hub/application/away_return_policy.py",
    "custom_components/hausman_hub/frontend/hausman-hub-room-lighting.css",
}


class DiskBackend:
    """HA Store-shaped backend that durably writes an entire envelope."""

    version = 1

    def __init__(self, path: Path, key: str) -> None:
        self.path = str(path)
        self.key = key

    async def async_load(self) -> object | None:
        path = Path(self.path)
        return json.loads(path.read_text(encoding="utf-8"))["data"] if path.exists() else None

    async def async_save(self, payload: dict[str, object]) -> None:
        _write_document_atomic(self.path, {
            "version": self.version, "minor_version": 1, "key": self.key, "data": payload,
        })


async def _run_sync(function, *args):
    return function(*args)


def _make_disk_bundle(directory: Path):
    names = ("authority", "priority", "off", "ownership", "shadow")
    files = {name: directory / f"{name}.json" for name in names}
    authority = VerifiedSafetyStore(DiskBackend(files["authority"], "hausman_hub.authority"), _run_sync, payload_validator=valid_manual_light_authority_payload)
    priority = VerifiedSafetyStore(DiskBackend(files["priority"], "hausman_hub.priority"), _run_sync, payload_validator=valid_light_priority_payload)
    off = VerifiedSafetyStore(DiskBackend(files["off"], "hausman_hub.off"), _run_sync, payload_validator=valid_manual_light_off_protection_payload)
    return files, authority, priority, off, DiskBackend(files["ownership"], "hausman_hub.ownership"), DiskBackend(files["shadow"], "hausman_hub.shadow")


def _snapshot_documents(files):
    """Record every envelope/sidecar, including absence, without filtering fields."""
    paths = [Path(f"{path}{suffix}") for path in files.values()
             for suffix in ("", ".previous", ".recovery-required")]
    return {path.name: path.read_bytes() if path.exists() else None for path in paths}


async def _load_all_five(directory):
    files, authority_store, priority_store, off_store, ownership_store, shadow_store = _make_disk_bundle(directory)
    authority = ManualLightAuthority(authority_store)
    priority = LightAutomationPriority(priority_store)
    off = ManualLightOffProtectionCoordinator(off_store, now=lambda: _NOW_DT)
    journal = RoomLightingOwnershipJournal(now_ms=lambda: _NOW_MS)
    shadow = RoomLightingShadowService(shadow_store, save_interval_seconds=0, clock=lambda: 100.0)
    await authority.async_load()
    await priority.async_load()
    await off.async_load()
    journal.restore(await ownership_store.async_load())
    await shadow.async_load()
    return files, (authority, priority, off, journal, shadow), (ownership_store, shadow_store)


def _full_payloads(objects):
    authority, priority, off, journal, shadow = objects
    return copy.deepcopy({
        "authority": authority._payload(authority._records, authority.generation),
        "priority": priority._payload(), "off": off._payload(),
        "ownership": journal.to_payload(), "shadow": shadow.journal_payload(),
    })


@pytest.mark.parametrize("return_pending", [False, True], ids=["normal", "authority_v3"])
async def test_five_real_disk_stores_keep_full_documents_across_reload(tmp_path, monkeypatch, return_pending):
    # Fix wall clocks only; asyncio's monotonic scheduling is untouched.
    monkeypatch.setattr("time.time", lambda: _NOW_MS / 1000)
    monkeypatch.setattr("time.time_ns", lambda: _NOW_MS * 1_000_000)
    files, objects, backends = await _load_all_five(tmp_path)
    authority, priority, off, journal, shadow = objects
    await authority.async_capture("light.demo_main", "on" if return_pending else "off", "service", "fixed-manual-event",
                                  observed_at_ms=_NOW_MS)
    priority._manual_records["light.demo_main"] = {
        "entityId": "light.demo_main", "targetId": "light_main", "ownership": "manual",
        "evidenceRevision": None, "confirmedAt": _NOW_MS,
    }
    await priority._store.async_save(priority._payload())
    settings = _settings()
    settings["profiles"][0].update(roomId="room", lightIds=["light.demo_main"], presenceSensorIds=[])
    await off.async_replace_settings("settings", 0, settings)
    await off.async_note_state_transition("light.demo_main", SimpleNamespace(state="on"),
                                         SimpleNamespace(state="off"), None)
    journal.record_manual("room", "light_main", _NOW_MS, turned_off=True)
    await backends[0].async_save(journal.to_payload())
    await shadow.async_record_pending_dispatch(
        at=_NOW_MS, room_id="room", target_id="light_main", action="turn_on",
        evidence_revision="fixed-evidence", operation_id="op-fixed", config_version=1,
    )
    if return_pending:
        token = await authority.async_begin_explicit_return(("light.demo_main",))
        assert token is not None
    before_payloads = _full_payloads(objects)
    before_documents = _snapshot_documents(files)
    assert all(before_documents[f"{name}.json"] is not None for name in files)
    for name, path in files.items():
        document = json.loads(before_documents[path.name])
        assert set(document) == {"version", "minor_version", "key", "data"}
        assert document["version"] == document["minor_version"] == 1
        assert document["key"] == f"hausman_hub.{name}"
        if name == "shadow":
            assert document["data"] == shadow._document(True)
            assert document["data"]["mode"] == "live"
            assert document["data"]["commandsEnabled"] is True
            public_expected = copy.deepcopy(document["data"])
            public_expected.update(mode="shadow", commandsEnabled=False)
            assert public_expected == before_payloads[name]
        else:
            assert document["data"] == before_payloads[name]
    assert before_payloads["authority"]["records"][0]["fingerprint"] == "fixed-manual-event"
    assert before_payloads["priority"]["records"] == [priority._manual_records["light.demo_main"]]
    assert len(before_payloads["off"]["protections"]) == 1
    assert before_payloads["ownership"]["manualOff"] == [
        {"roomId": "room", "targetId": "light_main", "at": _NOW_MS}]
    assert before_payloads["shadow"]["dispatchFailures"][0]["blockedAfterMs"] == _NOW_MS

    new_files, restored, new_backends = await _load_all_five(tmp_path)
    assert all(new is not old for new, old in zip(restored, objects))
    assert all(new is not old for new, old in zip(new_backends, backends))
    assert _full_payloads(restored) == before_payloads
    assert _snapshot_documents(new_files) == before_documents
    assert restored[-1]._document(True) == json.loads(before_documents["shadow.json"])["data"]
    restored_authority, restored_priority, restored_off, restored_journal, restored_shadow = restored
    assert restored_authority.blocks("light.demo_main")
    assert restored_authority.record("light.demo_main") == authority.record("light.demo_main")
    assert restored_priority.manual_entity_ids() == frozenset({"light.demo_main"})
    assert restored_off.snapshot()["protections"] == off.snapshot()["protections"]
    assert restored_journal.newest_manual_action_is_off("room", "light_main")
    assert restored_shadow.journal_payload()["dispatchFailures"] == before_payloads["shadow"]["dispatchFailures"]
    if return_pending:
        assert before_payloads["authority"]["version"] == 3
        assert restored_authority._return_operation == before_payloads["authority"]["returnOperation"]
        assert restored_authority._explicit_return_pending
        restored_authority.finish_startup_observation(("light.demo_main",))
        assert restored_authority.barrier_reason == "return_pending"
    else:
        assert before_payloads["authority"]["version"] == 2
        assert "returnOperation" not in before_payloads["authority"]


def test_installed_source_basis_is_exact_before_logger_delta():
    root = Path(__file__).resolve().parents[1]
    verification = (FROZEN / "verification.json").read_bytes()
    assert hashlib.sha256(verification).hexdigest() == "3a5d2484ee7a3d9db82e0a92049fcab0c051619a7aec373ddda7aa69369b8865"
    manifest = json.loads(verification)["files"]
    assert len(manifest) == 579
    for name, metadata in manifest.items():
        frozen_bytes = (FROZEN / "files" / name).read_bytes()
        assert len(frozen_bytes) == metadata["size"]
        assert hashlib.sha256(frozen_bytes).hexdigest() == metadata["sha256"]
    selected = sorted(
        path.relative_to(root).as_posix() for path in (root / "custom_components/hausman_hub").rglob("*")
        if path.is_file() and path.relative_to(root).as_posix() not in EXCLUDED
    )
    assert len(selected) == 702
    projection = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in selected}
    fingerprint = hashlib.sha256(json.dumps(projection, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    runtime = projection["custom_components/hausman_hub/application/room_lighting_runtime.py"]
    refresh = projection["custom_components/hausman_hub/adapters/lighting_mqtt_refresh.py"]
    if runtime == "dbfef1ef91e610e06030de60bee75ab3bd648496785e5e22f7a330ed0ad77146":
        assert fingerprint == EXPECTED_BEFORE
        assert refresh == "fcf75a7903aa0f936ec4a97232291dc641685a93699e1a9fc38bd8a2e40ebb24"
    else:
        assert fingerprint == EXPECTED_AFTER
        assert runtime == "50a470f2fc164cfa2ad1aba7c6d47cb46225147fda02ca9a233ec616ba369349"
        assert refresh == "a14c7447afe0bd2679af4c2921f3b2f9f9dc2ba8d534f5af43f2465e779e820d"
    assert "custom_components.hausman_hub.adapters.core.ha_transport" not in sys.modules
    assert "custom_components.hausman_hub.application.away_return_policy" not in sys.modules


RUNTIME = "custom_components/hausman_hub/application/room_lighting_runtime.py"
REFRESH = "custom_components/hausman_hub/adapters/lighting_mqtt_refresh.py"
SOURCE_PINS = {
    RUNTIME: ("dbfef1ef91e610e06030de60bee75ab3bd648496785e5e22f7a330ed0ad77146",
              "50a470f2fc164cfa2ad1aba7c6d47cb46225147fda02ca9a233ec616ba369349"),
    REFRESH: ("fcf75a7903aa0f936ec4a97232291dc641685a93699e1a9fc38bd8a2e40ebb24",
              "a14c7447afe0bd2679af4c2921f3b2f9f9dc2ba8d534f5af43f2465e779e820d"),
}


def _declared_sources():
    """Materialize precisely the four pinned hunks in memory, never on disk."""
    patch = (ARTIFACTS / "stale-refresh-diagnostic-r1.patch").read_bytes()
    assert hashlib.sha256(patch).hexdigest() == "e1b849686400fe519a99e58f7c3a53547d6d156d68c7854027a8c494abdc96b4"
    sources = {}
    sections = re.split(r"(?m)^--- ", patch.decode())
    for name, pins in SOURCE_PINS.items():
        before = (FROZEN / "files" / name).read_text()
        assert hashlib.sha256(before.encode()).hexdigest() == pins[0]
        section, = [part for part in sections if part.splitlines()
                    and part.splitlines()[0].split("\t")[0].endswith('/' + Path(name).name)]
        hunks = re.split(r"(?m)^@@[^\n]*\n", section)[1:]
        assert len(hunks) == (1 if name == RUNTIME else 3)
        after = before
        for hunk in hunks:
            lines = [line for line in hunk.splitlines(keepends=True)
                     if line.startswith((" ", "+", "-"))]
            old = ''.join(line[1:] for line in lines if line[0] in ' -')
            new = ''.join(line[1:] for line in lines if line[0] in ' +')
            assert after.count(old) == 1
            after = after.replace(old, new, 1)
        assert hashlib.sha256(after.encode()).hexdigest() == pins[1]
        sources[name] = before, after
    return sources


def _dump(node):
    return ast.dump(node, include_attributes=False)


def _method(module, name):
    methods = [node for node in ast.walk(module)
               if isinstance(node, ast.AsyncFunctionDef) and node.name == name]
    method, = methods
    return method


class _DeclaredDiagnosticNodes(ast.NodeTransformer):
    """Only used after the entire module matched the exact approved AST.

    Consequently names alone never authorize deletion of an unknown node.
    The exact four-hunk shape gate rejects unknown code before this visitor.
    """

    def __init__(self, *, refresh):
        self.refresh = refresh
        self.names = ({"scope", "phase", "outcome", "channel_index", "completed_reads", "bulb_skipped", "ready"}
                      if refresh else {"outcome", "safe_room"})

    def visit_Assign(self, node):
        targets = {item.id for target in node.targets for item in ast.walk(target)
                   if isinstance(item, ast.Name)}
        if targets and targets <= self.names:
            return None
        return self.generic_visit(node)

    def visit_AugAssign(self, node):
        if self.refresh and isinstance(node.target, ast.Name) and node.target.id == "completed_reads":
            return None
        return self.generic_visit(node)

    def visit_Nonlocal(self, node):
        assert self.refresh and node.names == ["phase", "outcome"]
        return None

    def visit_If(self, node):
        if self.refresh and isinstance(node.test, ast.Name) and node.test.id == "ready":
            return None
        return self.generic_visit(node)

    def visit_Return(self, node):
        if self.refresh and isinstance(node.value, ast.Name) and node.value.id == "ready":
            return ast.Return(value=ast.Call(func=ast.Name(id="current", ctx=ast.Load()), args=[], keywords=[]))
        return self.generic_visit(node)

    def visit_Expr(self, node):
        if (not self.refresh and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and _dump(node.value.func) == _dump(ast.parse("_LOGGER.log").body[0].value)):
            return None
        return self.generic_visit(node)

    def visit_For(self, node):
        if self.refresh:
            assert _dump(node.iter) == _dump(ast.parse("enumerate(channels, 1)").body[0].value)
            # Exact reference matching also proves index has only one consumer:
            # the diagnostic channel_index assignment, removed above.
            assert isinstance(node.target, ast.Tuple) and len(node.target.elts) == 2
            node.target = node.target.elts[1]
            node.iter = ast.Name(id="channels", ctx=ast.Load())
        return self.generic_visit(node)


def _control_projection(name, source):
    before, approved = _declared_sources()[name]
    module, baseline, expected = ast.parse(source), ast.parse(before), ast.parse(approved)
    shape = _dump(module)
    if shape == _dump(baseline):
        return shape
    # This is deliberately bounded, not a general purpose code analyzer.
    # New assignments, guards, calls, awaits or storage operations are refused.
    assert shape == _dump(expected), "unapproved module shape"
    if name == RUNTIME:
        method = _method(module, "_refresh_stale_handoff_observations")
        _DeclaredDiagnosticNodes(refresh=False).visit(method)
    else:
        method = _method(module, "__call__")
        outer = method.body[-1]
        assert isinstance(outer, ast.Try) and len(outer.handlers) == len(outer.finalbody) == 1
        assert _dump(outer.handlers[0].type) == _dump(ast.parse("asyncio.CancelledError").body[0].value)
        # The approved outer handler only marks outcome then performs bare raise;
        # the approved finally contains only the finite-field logger call.
        assert isinstance(outer.handlers[0].body[-1], ast.Raise)
        assert outer.handlers[0].body[-1].exc is None
        method.body = [method.body[0], *outer.body]
        _DeclaredDiagnosticNodes(refresh=True).visit(method)
        first, second = method.body[1:3]
        assert isinstance(first, ast.If) and isinstance(second, ast.If)
        assert not first.orelse and not second.orelse and _dump(first.body[0]) == _dump(second.body[0])
        # Restores precisely A or B, preserving Python short-circuit order.
        first.test = ast.BoolOp(op=ast.Or(), values=[first.test, second.test])
        del method.body[2]
        inner = method.body[-1]
        assert isinstance(inner, ast.Try) and len(inner.handlers) == 2
        assert [handler.type.id for handler in inner.handlers] == ["TimeoutError", "OSError"]
        assert _dump(inner.handlers[0].body[0]) == _dump(inner.handlers[1].body[0])
        inner.handlers[0].type = ast.Tuple(elts=[handler.type for handler in inner.handlers], ctx=ast.Load())
        inner.handlers = inner.handlers[:1]
        module.body = [node for node in module.body
                       if not (isinstance(node, ast.Import) and _dump(node) == _dump(ast.parse("import logging").body[0]))
                       and not (isinstance(node, ast.Assign) and _dump(node) == _dump(ast.parse("_LOGGER = logging.getLogger(__name__)").body[0]))]
    projected = _dump(module)
    assert projected == _dump(baseline), "control projection differs from installed basis"
    return projected


@pytest.mark.parametrize("name", [RUNTIME, REFRESH], ids=["runtime", "refresh"])
def test_only_four_declared_diagnostic_hunks_preserve_whole_module_control_ast(name):
    before, approved = _declared_sources()[name]
    assert _control_projection(name, approved) == _dump(ast.parse(before))
    current = (Path(__file__).resolve().parents[1] / name).read_text()
    assert _control_projection(name, current) == _dump(ast.parse(before))


@pytest.mark.parametrize("mutation", ["extra_current", "extra_publish", "guard", "clear_attempt", "store_write"])
def test_control_projection_rejects_unapproved_in_memory_mutations(mutation):
    name = RUNTIME if mutation in {"clear_attempt", "store_write"} else REFRESH
    before, approved = _declared_sources()[name]
    module = ast.parse(approved)
    method = _method(module, "_refresh_stale_handoff_observations" if name == RUNTIME else "__call__")
    if mutation == "guard":
        guard = next(node for node in ast.walk(method) if isinstance(node, ast.If))
        guard.test = ast.UnaryOp(op=ast.Not(), operand=guard.test)
    else:
        statement = {
            "extra_current": "current()",
            "extra_publish": "await self._publish('synthetic/get', '{}')",
            "clear_attempt": "self._adjustment_refresh_attempts.clear()",
            "store_write": "await self._store.async_save({})",
        }[mutation]
        method.body.insert(1, ast.parse(statement).body[0])
    with pytest.raises(AssertionError, match="unapproved module shape"):
        _control_projection(name, ast.unparse(ast.fix_missing_locations(module)))
