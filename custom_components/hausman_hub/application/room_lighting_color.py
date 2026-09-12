"""Shared colour-temperature inversion helpers for room lighting.

Some Tuya ``TS0502B`` lamps mirror their CCT scale: a higher raw command
kelvin is physically warmer. The logical value and the raw command are then
reflections of each other around the shared neutral point, and the same
formula converts in both directions. The executor (logical -> raw command) and
the state provider (raw observation -> logical) must use these exact
constants so they can never drift apart.
"""

from __future__ import annotations

# The shared default neutral point of the warmth policy.
INVERTED_NEUTRAL_KELVIN = 3000
# Used only when the device does not expose its own kelvin bounds; it mirrors
# the clamp of the legacy tambur controller.
FALLBACK_MIN_KELVIN = 1500
FALLBACK_MAX_KELVIN = 6500


def reflect_inverted_kelvin(
    kelvin: int,
    minimum: int = FALLBACK_MIN_KELVIN,
    maximum: int = FALLBACK_MAX_KELVIN,
) -> int:
    """Reflect a kelvin value around the neutral point.

    The reflection is its own inverse, so the same call converts a logical
    value into the raw command and a raw observation back into the logical
    value. The result is clamped to the supplied device bounds.
    """

    reflected = 2 * INVERTED_NEUTRAL_KELVIN - int(kelvin)
    return max(int(minimum), min(int(maximum), reflected))


def device_kelvin_bounds(attributes: object) -> tuple[int, int]:
    """Return the device kelvin bounds or the conservative fallback."""

    if not isinstance(attributes, dict):
        return FALLBACK_MIN_KELVIN, FALLBACK_MAX_KELVIN
    minimum = _kelvin_bound(attributes.get("min_color_temp_kelvin"))
    maximum = _kelvin_bound(attributes.get("max_color_temp_kelvin"))
    if minimum is None or maximum is None or minimum >= maximum:
        return FALLBACK_MIN_KELVIN, FALLBACK_MAX_KELVIN
    return minimum, maximum


def _kelvin_bound(value: object) -> int | None:
    if type(value) not in {int, float} or isinstance(value, bool):
        return None
    return int(value)


__all__ = [
    "INVERTED_NEUTRAL_KELVIN",
    "FALLBACK_MIN_KELVIN",
    "FALLBACK_MAX_KELVIN",
    "reflect_inverted_kelvin",
    "device_kelvin_bounds",
]
