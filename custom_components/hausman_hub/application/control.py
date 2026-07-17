"""Use case for authorizing one fixed input-boolean canary command."""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.configuration import SafeConfiguration


class CanaryControlViolation(ValueError):
    """Raised before an unsafe or stale canary command can reach an adapter."""


@dataclass(frozen=True, slots=True)
class CanaryControlCommand:
    """Validated intent passed to the Home Assistant boundary."""

    target_entity_id: str
    turn_on: bool


def canary_control_command(
    configuration: SafeConfiguration,
    entity_id: object,
    turn_on: object,
) -> CanaryControlCommand:
    """Authorize only the currently configured canary target and boolean action."""

    if not configuration.canary_control_enabled:
        raise CanaryControlViolation("canary control is disabled")
    target = configuration.canary_control_target
    if target is None or entity_id != target.entity_id:
        raise CanaryControlViolation("canary control target does not match")
    if type(turn_on) is not bool:
        raise CanaryControlViolation("canary control action must be boolean")
    return CanaryControlCommand(target_entity_id=target.entity_id, turn_on=turn_on)
