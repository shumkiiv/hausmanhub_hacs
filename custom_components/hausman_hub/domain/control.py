"""Domain contract for the first deliberately narrow HASC control canary."""

from __future__ import annotations

from dataclasses import dataclass
import re


INPUT_BOOLEAN_DOMAIN = "input_boolean"
CANARY_CONTROL_SCOPE = "single_input_boolean"
_INPUT_BOOLEAN_ENTITY_ID = re.compile(r"input_boolean\.[a-z0-9_]+\Z")


class UnsafeCanaryTargetError(ValueError):
    """Raised when a control target leaves the input-boolean canary boundary."""


@dataclass(frozen=True, slots=True)
class CanaryControlTarget:
    """One exact Home Assistant helper selected by the owner."""

    entity_id: str


def canary_control_target(value: object) -> CanaryControlTarget:
    """Accept only one ordinary ``input_boolean`` entity identifier."""

    if not isinstance(value, str) or _INPUT_BOOLEAN_ENTITY_ID.fullmatch(value) is None:
        raise UnsafeCanaryTargetError(
            "canary target must be one input_boolean entity"
        )
    return CanaryControlTarget(entity_id=value)
