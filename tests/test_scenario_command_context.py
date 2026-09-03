"""Tests for automatic relay command attribution."""

from types import SimpleNamespace

from custom_components.hausman_hub.application.scenario_command_context import (
    ScenarioCommandContextRegistry,
)


def test_matching_context_is_consumed_once() -> None:
    registry = ScenarioCommandContextRegistry(
        monotonic=lambda: 10.0,
        context_factory=lambda: SimpleNamespace(id="automatic.1", parent_id=None),
    )
    context = registry.create("switch.wall", "on")
    assert registry.consume(context, "switch.wall", "on") is True
    assert registry.consume(context, "switch.wall", "on") is False


def test_matching_context_can_be_read_by_multiple_listeners_before_consumption() -> None:
    """Removing non-destructive lookup would hide automatic origin by listener order."""

    registry = ScenarioCommandContextRegistry(
        monotonic=lambda: 10.0,
        context_factory=lambda: SimpleNamespace(id="automatic.shared", parent_id=None),
    )
    context = registry.create("light.tambur", "off")

    first = registry.match(context, "light.tambur", "off")
    second = registry.match(context, "light.tambur", "off")

    assert first == second
    assert first is not None
    assert first.origin == "automatic"
    assert first.entity_id == "light.tambur"
    assert first.expected_state == "off"
    assert first.request_id == "automatic.shared"
    assert registry.consume(context, "light.tambur", "off") is True


def test_child_context_matches_registered_parent() -> None:
    registry = ScenarioCommandContextRegistry(
        monotonic=lambda: 10.0,
        context_factory=lambda: SimpleNamespace(id="automatic.2", parent_id=None),
    )
    registry.create("switch.wall", "on")
    child = SimpleNamespace(id="child.1", parent_id="automatic.2")
    assert registry.consume(child, "switch.wall", "on") is True


def test_wrong_entity_or_state_never_consumes_context() -> None:
    registry = ScenarioCommandContextRegistry(
        monotonic=lambda: 10.0,
        context_factory=lambda: SimpleNamespace(id="automatic.3", parent_id=None),
    )
    context = registry.create("switch.wall", "on")
    assert registry.consume(context, "switch.other", "on") is False
    assert registry.consume(context, "switch.wall", "off") is False
    assert registry.consume(context, "switch.wall", "on") is True
