"""Boundary checks of the HA target-resolution surface for manual lights.

These tests verify the production adapter paths against the narrow test
double from ``manual_light_target_fixture``: direct entities, real group
expansion through ``hass.states``, and every fail-closed branch. They do NOT
prove compatibility with a real Home Assistant installation.
"""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

from manual_light_target_fixture import (
    TARGET_MODULE,
    install_target_boundary,
    states_table,
)

from custom_components.hausman_hub.application.manual_light_authority import (
    ManualLightAuthority,
)
from custom_components.hausman_hub.manual_light_authority_events import (
    ManualLightAuthorityEvents,
)


class _Hass:
    def __init__(self, states):
        self.tasks = []
        self.states = states

    def async_create_task(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.append(task)
        return task


def _fire_service(listener, service_data, *, domain="light", service="turn_on",
                  context="boundary"):
    listener.service_event(SimpleNamespace(
        data={"domain": domain, "service": service,
              "service_data": service_data},
        context=SimpleNamespace(id=context), time_fired=1,
    ))


async def test_direct_single_device_captures_manual_action(monkeypatch) -> None:
    install_target_boundary(monkeypatch, entities=("light.demo",))
    authority = ManualLightAuthority()
    listener = ManualLightAuthorityEvents(
        _Hass(states_table({"light.demo": "on"})),
        authority, lambda: {"light.demo"}, None, lambda: None,
    )
    _fire_service(listener, {"entity_id": "light.demo"})
    assert authority.record("light.demo").action == "on"
    assert authority.barrier_reason_for("light.demo") is None


async def test_direct_group_expands_through_real_states(monkeypatch) -> None:
    install_target_boundary(monkeypatch, entities=("light.group",))

    def get(entity_id):
        if entity_id == "light.group":
            return SimpleNamespace(
                state="off",
                attributes={"entity_id": ["light.member_a", "light.member_b"]},
            )
        return states_table({
            "light.member_a": "off", "light.member_b": "off",
        }).get(entity_id)

    authority = ManualLightAuthority()
    listener = ManualLightAuthorityEvents(
        _Hass(SimpleNamespace(get=get)),
        authority, lambda: {"light.member_a", "light.member_b"}, None,
        lambda: None,
    )
    _fire_service(listener, {"entity_id": "light.group"})
    assert authority.record("light.member_a").action == "on"
    assert authority.record("light.member_b").action == "on"


async def test_nested_group_still_protects_members(monkeypatch) -> None:
    install_target_boundary(monkeypatch, entities=("light.outer",))

    def get(entity_id):
        table = {
            "light.outer": SimpleNamespace(
                state="off",
                attributes={"entity_id": ["light.inner"]},
            ),
            "light.inner": SimpleNamespace(
                state="off",
                attributes={"entity_id": ["light.deep"]},
            ),
            "light.deep": SimpleNamespace(state="off", attributes={}),
        }
        return table.get(entity_id)

    authority = ManualLightAuthority()
    listener = ManualLightAuthorityEvents(
        _Hass(SimpleNamespace(get=get)),
        authority, lambda: {"light.deep"}, None, lambda: None,
    )
    _fire_service(listener, {"entity_id": "light.outer"})
    assert authority.record("light.deep").action == "on"


async def test_unknown_direct_entity_stays_uncertain(monkeypatch) -> None:
    install_target_boundary(monkeypatch, entities=("light.known",))
    authority = ManualLightAuthority()
    listener = ManualLightAuthorityEvents(
        _Hass(states_table({"light.known": "off"})),
        authority, lambda: {"light.known"}, None, lambda: None,
    )
    _fire_service(listener, {"entity_id": "light.unknown"})
    assert authority.record("light.known") is None
    assert authority.barrier_reason_for("light.known") == "selector_uncertain"


async def test_extraction_failure_stays_uncertain(monkeypatch) -> None:
    calls = install_target_boundary(monkeypatch, entities=("light.demo",))

    def broken(_hass, _selection):
        raise RuntimeError("registry exploded")

    monkeypatch.setattr(
        sys.modules[TARGET_MODULE], "async_extract_referenced_entity_ids", broken,
    )
    authority = ManualLightAuthority()
    listener = ManualLightAuthorityEvents(
        _Hass(states_table({"light.demo": "off"})),
        authority, lambda: {"light.demo"}, None, lambda: None,
    )
    _fire_service(listener, {"entity_id": "light.demo"})
    assert calls == []
    # Exact direct evidence survives the failed resolution; the selection
    # itself stays uncertain (plan r2 required-cases table).
    assert authority.record("light.demo").action == "on"
    assert authority.barrier_reason_for("light.demo") == "selector_uncertain"


async def test_absent_states_keep_protection(monkeypatch) -> None:
    install_target_boundary(monkeypatch, entities=("light.demo",))
    authority = ManualLightAuthority()
    listener = ManualLightAuthorityEvents(
        _Hass(states_table({})),
        authority, lambda: {"light.demo"}, None, lambda: None,
    )
    _fire_service(listener, {"entity_id": "light.demo"})
    # Absent state keeps the exact call fact; the barrier protects the
    # unknown full selection (plan r2 required-cases table).
    assert authority.record("light.demo").action == "on"
    assert authority.barrier_reason_for("light.demo") == "selector_uncertain"


async def test_stateless_entity_keeps_protection(monkeypatch) -> None:
    install_target_boundary(monkeypatch, entities=("light.demo",))
    authority = ManualLightAuthority()
    listener = ManualLightAuthorityEvents(
        _Hass(states_table({"light.demo": None})),
        authority, lambda: {"light.demo"}, None, lambda: None,
    )
    _fire_service(listener, {"entity_id": "light.demo"})
    # Absent state keeps the exact call fact; the barrier protects the
    # unknown full selection (plan r2 required-cases table).
    assert authority.record("light.demo").action == "on"
    assert authority.barrier_reason_for("light.demo") == "selector_uncertain"


async def test_corrupted_group_composition_keeps_protection(monkeypatch) -> None:
    install_target_boundary(monkeypatch, entities=("light.group",))
    for members in ([], "light.member", [None], [12], ["light.member "], 7):
        def get(entity_id, _members=members):
            if entity_id == "light.group":
                return SimpleNamespace(
                    state="off", attributes={"entity_id": _members},
                )
            return None

        authority = ManualLightAuthority()
        listener = ManualLightAuthorityEvents(
            _Hass(SimpleNamespace(get=get)),
            authority, lambda: {"light.member"}, None, lambda: None,
        )
        _fire_service(listener, {"entity_id": "light.group"})
        assert authority.record("light.member") is None
        assert authority.barrier_reason_for("light.member") == "selector_uncertain"


async def test_unknown_area_and_device_never_resolve(monkeypatch) -> None:
    install_target_boundary(
        monkeypatch,
        entities=("light.demo",),
        areas={"known-area": ("light.demo",)},
        devices={"known-device": ("light.demo",)},
    )
    authority = ManualLightAuthority()
    listener = ManualLightAuthorityEvents(
        _Hass(states_table({"light.demo": "off"})),
        authority, lambda: {"light.demo"}, None, lambda: None,
    )
    _fire_service(listener, {"area_id": "ghost-area"}, context="area-ghost")
    assert authority.record("light.demo") is None
    assert authority.barrier_reason_for("light.demo") == "selector_uncertain"
    _fire_service(listener, {"device_id": "ghost-device"}, context="device-ghost")
    assert authority.barrier_reason_for("light.demo") == "selector_uncertain"
    # Known selectors still resolve to the managed entity.
    _fire_service(listener, {"area_id": "known-area"}, context="area-known")
    assert authority.record("light.demo").action == "on"


async def test_boundary_injection_is_fully_reverted(monkeypatch) -> None:
    install_target_boundary(monkeypatch, entities=("light.demo",))
    assert TARGET_MODULE in sys.modules
    monkeypatch.undo()
    assert TARGET_MODULE not in sys.modules
    try:
        from homeassistant.helpers import target  # noqa: F401
    except ModuleNotFoundError:
        pass
    else:  # pragma: no cover - a real installation may provide it.
        pytest_skip_hint = "real homeassistant present; revert check is vacuous"
        assert pytest_skip_hint


async def test_registry_loss_between_events_narrows_published_graph(monkeypatch) -> None:
    """A lost single-device proof must remove the bypass mid-stream."""
    from custom_components.hausman_hub.application.related_light_sources import (
        RelatedLightSources,
    )

    install_target_boundary(monkeypatch, entities=("switch.lamp", "switch.other"),
                            areas={"room": ("switch.lamp", "switch.other")})
    evidence = {"switch.lamp", "switch.other"}
    authority = ManualLightAuthority()
    graph = RelatedLightSources.from_power_dependencies(
        (_config_room(),), {}, complete=True,
        proven_single_entities=frozenset(evidence),
    )
    authority.set_related_sources(graph)
    listener = ManualLightAuthorityEvents(
        _Hass(states_table({"switch.lamp": "off", "switch.other": "unavailable"})),
        authority, lambda: {"switch.lamp", "switch.other"}, None, lambda: None,
        single_entity_evidence=lambda candidates: frozenset(candidates) & frozenset(evidence),
    )
    _fire_service(listener, {"area_id": "room"}, domain="switch", service="turn_off")
    assert authority.barrier_reason_for("switch.other") != "selector_uncertain"
    # The registry proof disappears without any runtime rebuild.
    evidence.clear()
    _fire_service(listener, {"area_id": "room"}, domain="switch", service="turn_off",
                  context="boundary-2")
    assert authority.related_sources.proven_entities == frozenset()
    assert authority.related_sources != graph
    assert authority.barrier_reason_for("switch.other") == "selector_uncertain"


def _config_room():
    from types import SimpleNamespace as NS

    return NS(room_id="room", devices=NS(light_targets=(
        NS(entity_id="switch.lamp"), NS(entity_id="switch.other"),
    )))


async def test_manual_off_blocks_auto_off_of_group_members(monkeypatch) -> None:
    """Known composition: the manual OFF protects every managed member."""
    install_target_boundary(monkeypatch, entities=("light.group",))

    def get(entity_id):
        if entity_id == "light.group":
            return SimpleNamespace(
                state="off",
                attributes={"entity_id": ["light.member_a", "light.member_b"]},
            )
        return states_table({
            "light.member_a": "off", "light.member_b": "off",
        }).get(entity_id)

    authority = ManualLightAuthority()
    listener = ManualLightAuthorityEvents(
        _Hass(SimpleNamespace(get=get)),
        authority, lambda: {"light.member_a", "light.member_b"}, None, lambda: None,
    )
    _fire_service(listener, {"entity_id": "light.group"}, domain="light",
                  service="turn_off")
    for member in ("light.member_a", "light.member_b"):
        assert authority.record(member).action == "off"
        assert authority.blocks(member)


async def test_expansion_limit_512_succeeds(monkeypatch) -> None:
    install_target_boundary(monkeypatch, entities=("light.huge",))
    members = [f"light.member_{index}" for index in range(511)]
    table = {member: "off" for member in members}

    def get(entity_id):
        if entity_id == "light.huge":
            return SimpleNamespace(state="off", attributes={"entity_id": members})
        return states_table(table).get(entity_id)

    authority = ManualLightAuthority()
    listener = ManualLightAuthorityEvents(
        _Hass(SimpleNamespace(get=get)),
        authority, lambda: {"light.member_0"}, None, lambda: None,
    )
    _fire_service(listener, {"entity_id": "light.huge"})
    assert authority.record("light.member_0").action == "on"
    assert authority.record("light.member_510") is None


async def test_expansion_limit_513_stays_closed(monkeypatch) -> None:
    install_target_boundary(monkeypatch, entities=("light.huge",))
    members = [f"light.member_{index}" for index in range(512)]
    table = {member: "off" for member in members}

    def get(entity_id):
        if entity_id == "light.huge":
            return SimpleNamespace(state="off", attributes={"entity_id": members})
        return states_table(table).get(entity_id)

    authority = ManualLightAuthority()
    listener = ManualLightAuthorityEvents(
        _Hass(SimpleNamespace(get=get)),
        authority, lambda: frozenset(members), None, lambda: None,
    )
    _fire_service(listener, {"entity_id": "light.huge"})
    assert authority.record("light.member_0") is None
    assert authority.barrier_reason_for("light.member_0") == "selector_uncertain"


async def test_resolution_failure_keeps_direct_evidence_and_barrier(monkeypatch) -> None:
    """No HA target module: exact direct evidence must survive the failure."""
    authority = ManualLightAuthority()
    listener = ManualLightAuthorityEvents(
        _Hass(states_table({"light.demo": "on"})),
        authority, lambda: {"light.demo"}, None, lambda: None,
    )
    _fire_service(listener, {"entity_id": "light.demo"})
    assert authority.record("light.demo").action == "on"
    assert authority.barrier_reason_for("light.demo") == "selector_uncertain"


async def test_unknown_state_keeps_direct_evidence_and_barrier(monkeypatch) -> None:
    install_target_boundary(monkeypatch, entities=("light.demo",))
    authority = ManualLightAuthority()
    listener = ManualLightAuthorityEvents(
        _Hass(states_table({"light.demo": "unknown"})),
        authority, lambda: {"light.demo"}, None, lambda: None,
    )
    _fire_service(listener, {"entity_id": "light.demo"})
    assert authority.record("light.demo").action == "on"
    assert authority.barrier_reason_for("light.demo") == "selector_uncertain"


async def test_unavailable_state_keeps_direct_evidence_and_barrier(monkeypatch) -> None:
    install_target_boundary(monkeypatch, entities=("light.demo",))
    authority = ManualLightAuthority()
    listener = ManualLightAuthorityEvents(
        _Hass(states_table({"light.demo": "unavailable"})),
        authority, lambda: {"light.demo"}, None, lambda: None,
    )
    _fire_service(listener, {"entity_id": "light.demo"}, service="turn_off")
    assert authority.record("light.demo").action == "off"
    assert authority.barrier_reason_for("light.demo") == "selector_uncertain"


async def test_missing_state_keeps_direct_evidence_and_barrier(monkeypatch) -> None:
    install_target_boundary(monkeypatch, entities=("light.demo",))
    authority = ManualLightAuthority()
    listener = ManualLightAuthorityEvents(
        _Hass(states_table({})),
        authority, lambda: {"light.demo"}, None, lambda: None,
    )
    _fire_service(listener, {"entity_id": "light.demo"})
    assert authority.record("light.demo").action == "on"
    assert authority.barrier_reason_for("light.demo") == "selector_uncertain"


async def test_malformed_group_composition_records_only_addressed_entity(monkeypatch) -> None:
    install_target_boundary(monkeypatch, entities=("light.group",))
    authority = ManualLightAuthority()
    listener = ManualLightAuthorityEvents(
        _Hass(SimpleNamespace(get=lambda entity_id: SimpleNamespace(
            state="off",
            attributes={"entity_id": [42, None, "switch.other"]},
        ) if entity_id == "light.group" else None)),
        authority, lambda: {"light.group"}, None, lambda: None,
    )
    _fire_service(listener, {"entity_id": "light.group"}, service="turn_off")
    assert authority.record("light.group").action == "off"
    assert authority.record("switch.other") is None
    assert authority.barrier_reason_for("light.group") == "selector_uncertain"


async def test_multiple_direct_targets_with_duplicate_are_captured(monkeypatch) -> None:
    authority = ManualLightAuthority()
    listener = ManualLightAuthorityEvents(
        _Hass(states_table({"light.a": "off", "light.b": "off"})),
        authority, lambda: {"light.a", "light.b"}, None, lambda: None,
    )
    _fire_service(listener, {"entity_id": ["light.a", "light.a", "light.b"]})
    assert authority.record("light.a").action == "on"
    assert authority.record("light.b").action == "on"
    assert authority.barrier_reason_for("light.a") == "selector_uncertain"


async def test_wide_outer_target_gets_no_direct_fallback(monkeypatch) -> None:
    install_target_boundary(monkeypatch, entities=("light.demo",))
    authority = ManualLightAuthority()
    listener = ManualLightAuthorityEvents(
        _Hass(states_table({"light.demo": "on"})),
        authority, lambda: {"light.demo"}, None, lambda: None,
    )
    event = SimpleNamespace(
        data={"domain": "light", "service": "turn_on",
              "service_data": {"entity_id": "light.demo"},
              "target": {"area_id": "room"}},
        context=SimpleNamespace(id="wide"), time_fired=1,
    )
    listener.service_event(event)
    assert authority.record("light.demo") is None
    assert authority.barrier_reason_for("light.demo") == "selector_uncertain"


async def test_resolution_failure_with_known_group_records_only_direct(monkeypatch) -> None:
    """Review r3: an uncertain resolution must never capture group members."""
    install_target_boundary(monkeypatch, entities=("light.group",))
    monkeypatch.setattr(
        sys.modules[TARGET_MODULE], "async_extract_referenced_entity_ids",
        lambda *_args, **_kwargs: None,
    )

    def get(entity_id):
        if entity_id == "light.group":
            return SimpleNamespace(state="off", attributes={
                "entity_id": ["light.member_a", "light.member_b"]})
        return states_table({
            "light.member_a": "off", "light.member_b": "off",
        }).get(entity_id)

    authority = ManualLightAuthority()
    listener = ManualLightAuthorityEvents(
        _Hass(SimpleNamespace(get=get)),
        authority, lambda: {"light.group", "light.member_a", "light.member_b"},
        None, lambda: None,
    )
    _fire_service(listener, {"entity_id": "light.group"}, service="turn_off")
    assert authority.record("light.group").action == "off"
    assert authority.record("light.member_a") is None
    assert authority.record("light.member_b") is None
    assert authority.barrier_reason_for("light.group") == "selector_uncertain"


async def test_startup_uncertain_branch_respects_automatic_provenance(monkeypatch) -> None:
    """Review r3: buffered startup events pass the same automatic check."""
    automatic = SimpleNamespace(match=lambda context, entity_id, action: SimpleNamespace(
        origin="automatic", request_id="req-1"))

    class _BusHass(_Hass):
        def __init__(self, states):
            super().__init__(states)
            self.bus = SimpleNamespace(async_listen=lambda *_args: lambda: None)

    authority = ManualLightAuthority()
    listener = ManualLightAuthorityEvents(
        _BusHass(states_table({"light.demo": "on"})),
        authority, lambda: {"light.demo"}, automatic, lambda: None,
    )
    listener.start(buffer_startup=True)
    try:
        _fire_service(listener, {"entity_id": "light.demo"}, context="own-1")
        assert listener._startup_evidence == []
        listener._automatic_contexts = None
        _fire_service(listener, {"entity_id": "light.demo"}, context="human-1")
        assert len(listener._startup_evidence) == 1
        # Duplicate direct addresses in one event are buffered once.
        listener._startup_evidence.clear()
        event = SimpleNamespace(
            data={"domain": "light", "service": "turn_on",
                  "service_data": {"entity_id": ["light.demo", "light.demo"]}},
            context=SimpleNamespace(id=None), time_fired=7,
        )
        listener.service_event(event)
        assert len(listener._startup_evidence) == 1
    finally:
        listener._managed_ready = True
