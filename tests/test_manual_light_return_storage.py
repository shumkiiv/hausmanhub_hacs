"""The actual staged-file commit closes the async capture/publication race."""

import asyncio
import copy
import json
import os
from pathlib import Path

import pytest

from custom_components.hausman_hub.application.manual_light_authority import (
    ManualLightAuthority, valid_manual_light_authority_payload,
)
from custom_components.hausman_hub.verified_safety_storage import VerifiedSafetyStore
from tests.test_verified_safety_storage import FakeBackend


class DiskBackend(FakeBackend):
    async def async_load(self):
        path = Path(self.path)
        return json.loads(path.read_text())["data"] if path.exists() else None


async def make_authority(tmp_path, hook=None):
    from custom_components.hausman_hub.application.related_light_sources import RelatedLightSources
    path = tmp_path / "authority.json"

    async def run_sync(function, *args):
        result = function(*args)
        if hook and function.__name__ == "_prepare_conditional_document":
            await hook()
        return result

    backend = DiskBackend(path)
    store = VerifiedSafetyStore(backend, run_sync, payload_validator=valid_manual_light_authority_payload)
    authority = ManualLightAuthority(store)
    await authority.async_load()
    authority.set_related_sources(RelatedLightSources(True, {
        "light.demo": frozenset({"light.demo"}),
        "light.other": frozenset({"light.other"}),
    }))
    await authority.async_capture("light.demo", "on", "service", "old")
    token = await authority.async_begin_explicit_return(("light.demo",))
    assert token is not None
    assert json.loads(path.read_text())["data"]["version"] == 3
    return authority, store, token, path


async def test_conditional_commit_preserves_envelope_and_pending_backup(tmp_path):
    authority, store, token, path = await make_authority(tmp_path)
    assert await authority.async_finish_explicit_return(token, persisted=True)
    document = json.loads(path.read_text())
    assert document["minor_version"] == 1
    assert document["version"] == 1
    assert document["data"]["version"] == 2
    assert document["data"]["records"] == []
    assert json.loads(Path(f"{path}.previous").read_text())["data"]["version"] == 3
    assert not authority.blocks("light.demo")
    assert list(tmp_path.glob(".authority.json.return.*")) == []
    # An action after the atomic commit is an ordinary new protected action.
    await authority.async_capture("light.demo", "off", "service", "after")
    assert authority.blocks("light.demo")
    assert json.loads(path.read_text())["data"]["records"][0]["fingerprint"] == "after"


async def test_independent_pending_returns_survive_disk_commit_and_restart(tmp_path):
    authority, store, first, path = await make_authority(tmp_path)
    second = await authority.async_begin_explicit_return(
        ("light.other",), all_manual=True, observations_current=lambda: True,
    )
    assert second is not None
    await authority.async_abort_explicit_return(second)
    disk = json.loads(path.read_text())["data"]
    assert disk["version"] == 4
    assert {tuple(op["entityIds"]) for op in disk["returnOperations"]} == {
        ("light.demo",), ("light.other",),
    }
    assert await authority.async_finish_explicit_return(first, persisted=True)
    assert authority.barrier_reason_for("light.demo") is None
    assert authority.barrier_reason_for("light.other") == "return_pending"
    restarted = ManualLightAuthority(VerifiedSafetyStore(
        DiskBackend(path), lambda fn, *args: asyncio.to_thread(fn, *args),
        payload_validator=valid_manual_light_authority_payload,
    ))
    await restarted.async_load()
    restarted.finish_startup_observation(("light.demo", "light.other"))
    assert restarted.barrier_reason_for("light.other") == "return_pending"
    assert restarted.blocks("light.other")


async def test_two_rooms_can_finish_in_reverse_order_without_losing_fences(tmp_path):
    authority, _store, first, path = await make_authority(tmp_path)
    second = await authority.async_begin_explicit_return(
        ("light.other",), all_manual=True, observations_current=lambda: True,
    )
    assert second is not None
    assert await authority.async_finish_explicit_return(second, persisted=True, observations_current=lambda: True)
    assert authority.barrier_reason_for("light.demo") == "return_pending"
    assert authority.barrier_reason_for("light.other") is None
    data = json.loads(path.read_text())["data"]
    assert data["returnOperation"]["entityIds"] == ["light.demo"]
    assert await authority.async_finish_explicit_return(first, persisted=True)
    assert not authority.blocks("light.demo")
    assert not authority.blocks("light.other")


async def test_older_loader_recovering_from_v4_marks_recovery_required(tmp_path):
    authority, _store, first, path = await make_authority(tmp_path)
    second = await authority.async_begin_explicit_return(
        ("light.other",), all_manual=True, observations_current=lambda: True,
    )
    assert second is not None
    assert json.loads(path.read_text())["data"]["version"] == 4
    old_store = VerifiedSafetyStore(
        DiskBackend(path), lambda fn, *args: asyncio.to_thread(fn, *args),
        payload_validator=lambda value: valid_manual_light_authority_payload(value)
        and value["version"] in {2, 3},
    )
    payload = await old_store.async_load()
    assert old_store.recovered_previous
    assert payload["version"] == 3
    assert payload["returnOperation"]["entityIds"] == ["light.demo"]
    assert Path(f"{path}.recovery-required").exists()
    await authority.async_abort_explicit_return(first)
    await authority.async_abort_explicit_return(second)


async def test_new_explicit_intent_can_commit_after_interruption_on_real_files(tmp_path):
    holder = {}
    async def intervene():
        authority = holder.get("authority")
        if authority is not None:
            holder.clear()
            authority.capture("light.demo", "on", "service", "new-owner-action")
    authority, store, token, path = await make_authority(tmp_path, intervene)
    holder["authority"] = authority
    assert not await authority.async_finish_explicit_return(token, persisted=True)
    await authority.async_drain()
    assert json.loads(path.read_text())["data"]["version"] == 3
    replacement = await authority.async_begin_explicit_return(
        ("light.demo",), all_manual=True, config_version=1, observations_current=lambda: True,
    )
    assert replacement is not None
    assert replacement.operation_id > token.operation_id
    assert await authority.async_finish_explicit_return(replacement, persisted=True, observations_current=lambda: True)
    assert json.loads(path.read_text())["data"]["version"] == 2
    assert not authority.blocks("light.demo")


@pytest.mark.parametrize("mutation", ["on", "off", "selector", "graph"])
async def test_new_event_during_preparation_cannot_publish_v2(tmp_path, mutation):
    holder = {}

    async def hook():
        authority = holder["authority"]
        if mutation in {"on", "off"}:
            authority.capture("light.demo", mutation, "service", "new")
        elif mutation == "selector":
            authority.mark_selector_uncertain("light")
        else:
            authority.invalidate_related_sources()
        await asyncio.sleep(0)

    authority, store, token, path = await make_authority(tmp_path, hook)
    holder["authority"] = authority
    assert not await authority.async_finish_explicit_return(token, persisted=True)
    assert json.loads(path.read_text())["data"]["version"] == 3
    assert list(tmp_path.glob(".authority.json.return.*")) == []
    # Even loss of the subsequent event save cannot erase the transaction deny.
    async def fail_save(_payload):
        raise OSError("later drain unavailable")
    store.async_save = fail_save
    if mutation in {"on", "off"}:
        with pytest.raises(OSError):
            await authority.async_drain()
    restarted = ManualLightAuthority(VerifiedSafetyStore(
        DiskBackend(path), lambda fn, *args: asyncio.to_thread(fn, *args),
        payload_validator=valid_manual_light_authority_payload,
    ))
    await restarted.async_load()
    restarted.finish_startup_observation(("light.demo",))
    assert restarted.barrier_reason == "return_pending"
    assert restarted.blocks("light.demo")


async def test_cancelled_preparation_reaps_stage_without_opening_pending(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()

    async def hook():
        entered.set()
        await release.wait()

    authority, _store, token, path = await make_authority(tmp_path, hook)
    finishing = asyncio.create_task(authority.async_finish_explicit_return(token, persisted=True))
    await entered.wait()
    finishing.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await finishing
    assert json.loads(path.read_text())["data"]["version"] == 3
    assert authority.blocks("light.demo")
    assert list(tmp_path.glob(".authority.json.return.*")) == []


async def test_atomic_rename_error_keeps_original_pending(tmp_path, monkeypatch):
    authority, _store, token, path = await make_authority(tmp_path)
    original_replace = os.replace

    def fail_commit(source, destination):
        if str(destination) == str(path):
            raise OSError("commit unavailable")
        return original_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_commit)
    with pytest.raises(OSError):
        await authority.async_finish_explicit_return(token, persisted=True)
    assert json.loads(path.read_text())["data"]["version"] == 3
    assert authority.blocks("light.demo")


async def test_old_v2_loader_cannot_restore_open_authority_from_pending(tmp_path):
    holder = {}

    async def hook():
        holder["authority"].capture("light.demo", "off", "service", "new")

    authority, _store, token, path = await make_authority(tmp_path, hook)
    holder["authority"] = authority
    assert not await authority.async_finish_explicit_return(token, persisted=True)
    old_store = VerifiedSafetyStore(
        DiskBackend(path), lambda fn, *args: asyncio.to_thread(fn, *args),
        payload_validator=lambda value: isinstance(value, dict) and value.get("version") == 2,
    )
    with pytest.raises(RuntimeError):
        await old_store.async_load()


async def test_stale_fence_replaced_by_new_explicit_intent_on_real_files(tmp_path):
    authority, store, token, path = await make_authority(tmp_path)
    await authority.async_abort_explicit_return(token)

    def stale(_entity_ids, old_version):
        return old_version == 1

    replacement = await authority.async_begin_explicit_return(
        ("light.demo",), all_manual=True, config_version=2,
        observations_current=lambda: True, allow_stale_replacement=stale,
    )
    assert replacement is not None
    assert replacement.operation_id > token.operation_id
    document = json.loads(path.read_text())["data"]
    assert document["version"] == 3
    assert document["returnOperation"]["operationId"] == replacement.operation_id
    assert document["returnOperation"]["configVersion"] == 2
    assert document["returnOperation"]["entityIds"] == ["light.demo"]
    assert authority.blocks("light.demo")
    # The replacement survives a restart as the pending fence.
    restarted = ManualLightAuthority(VerifiedSafetyStore(
        DiskBackend(path), lambda fn, *args: asyncio.to_thread(fn, *args),
        payload_validator=valid_manual_light_authority_payload,
    ))
    await restarted.async_load()
    restarted.finish_startup_observation(("light.demo",))
    assert restarted.barrier_reason == "return_pending"
    assert restarted._return_operation["operationId"] == replacement.operation_id
    assert await authority.async_finish_explicit_return(
        replacement, persisted=True, observations_current=lambda: True)
    document = json.loads(path.read_text())["data"]
    assert document["version"] == 2
    assert "returnOperation" not in document
    assert not authority.blocks("light.demo")


async def test_stale_replacement_refusal_keeps_the_old_document(tmp_path):
    holder = {}

    async def new_event():
        holder["authority"].capture("light.demo", "off", "physical", "during-replace")

    authority, store, token, path = await make_authority(tmp_path, new_event)
    holder["authority"] = authority
    await authority.async_abort_explicit_return(token)

    def stale(_entity_ids, old_version):
        return old_version == 1

    refused = await authority.async_begin_explicit_return(
        ("light.demo",), all_manual=True, config_version=2,
        observations_current=lambda: True, allow_stale_replacement=stale,
    )
    assert refused is None
    await authority.async_drain()
    document = json.loads(path.read_text())["data"]
    assert document["version"] == 3
    assert document["returnOperation"]["operationId"] == token.operation_id
    assert document["returnOperation"]["configVersion"] == token.config_version
    assert document["records"][0]["fingerprint"] == "during-replace"
    assert authority.barrier_reason == "return_pending"
    assert authority.blocks("light.demo")
