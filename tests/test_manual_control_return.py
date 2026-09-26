"""Virtual-time tests for the pure bounded manual return policy."""

import pytest

from custom_components.hausman_hub.domain.manual_control_return import (
    AutomaticLightAction,
    AutomaticLightIntent,
    AutomaticScope,
    ManualAction,
    ManualControlReturnInput,
    ManualControlReturnIntent,
    ManualControlReturnPolicy,
    ManualControlReturnViolation,
)


def _proposed(**changes: object) -> AutomaticLightIntent:
    values: dict[str, object] = {
        "action": AutomaticLightAction.SET_BRIGHTNESS,
        "entity_id": "light.tambour_chandelier",
        "source_is_on": True,
        "lowers_brightness": True,
        "confirmed_absence": True,
    }
    values.update(changes)
    return AutomaticLightIntent(**values)  # type: ignore[arg-type]


def _manual(action: ManualAction = ManualAction.ON) -> ManualControlReturnIntent:
    return ManualControlReturnIntent(action, "light.tambour_chandelier", 100)


@pytest.mark.parametrize(
    ("now", "allowed", "scope", "reason"),
    [
        (3699, False, AutomaticScope.BLOCKED, "manual_on_wait"),
        (3700, True, AutomaticScope.SAME_SOURCE_BRIGHTNESS_DOWN, "manual_on_limited"),
        (3701, True, AutomaticScope.SAME_SOURCE_BRIGHTNESS_DOWN, "manual_on_limited"),
    ],
)
def test_manual_on_return_boundary_uses_virtual_time(
    now: int, allowed: bool, scope: AutomaticScope, reason: str
) -> None:
    decision = ManualControlReturnPolicy().decide(
        ManualControlReturnInput(now, _proposed(), _manual())
    )
    assert (decision.allowed, decision.scope, decision.reason) == (allowed, scope, reason)


@pytest.mark.parametrize(
    "proposed",
    [
        _proposed(entity_id="light.other"),
        _proposed(action=AutomaticLightAction.TURN_OFF),
        _proposed(source_is_on=False),
        _proposed(lowers_brightness=False),
        _proposed(confirmed_absence=False),
    ],
)
def test_manual_on_only_allows_same_source_lower_brightness(
    proposed: AutomaticLightIntent,
) -> None:
    decision = ManualControlReturnPolicy().decide(
        ManualControlReturnInput(3700, proposed, _manual())
    )
    assert (decision.allowed, decision.scope, decision.reason) == (
        False,
        AutomaticScope.BLOCKED,
        "manual_on_limited",
    )


@pytest.mark.parametrize("flag", ["legacy", "restart_uncertain", "manual_only", "unknown"])
def test_uncertain_or_nonautomatic_lineage_fails_closed(flag: str) -> None:
    kwargs = {flag: True}
    decision = ManualControlReturnPolicy().decide(
        ManualControlReturnInput(3701, _proposed(), _manual(), **kwargs)
    )
    assert decision.allowed is False
    assert decision.scope is AutomaticScope.BLOCKED


def test_manual_off_stays_with_separate_protection_after_return_time() -> None:
    decision = ManualControlReturnPolicy().decide(
        ManualControlReturnInput(3701, _proposed(), _manual(ManualAction.OFF))
    )
    assert (decision.allowed, decision.scope, decision.reason) == (
        False,
        AutomaticScope.BLOCKED,
        "manual_off_separate_protection",
    )


def test_no_manual_event_keeps_normal_scope() -> None:
    decision = ManualControlReturnPolicy().decide(ManualControlReturnInput(100, _proposed()))
    assert (decision.allowed, decision.scope, decision.reason) == (
        True,
        AutomaticScope.NORMAL,
        "no_manual_control",
    )


@pytest.mark.parametrize("value", [14, 86401, True])
def test_priority_seconds_are_bounded(value: object) -> None:
    with pytest.raises(ManualControlReturnViolation):
        ManualControlReturnPolicy(value)  # type: ignore[arg-type]
