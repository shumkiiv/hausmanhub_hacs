"""Shared colour-temperature inversion helpers for room lighting.

Some Tuya ``TS0502B`` lamps mirror their CCT scale: a higher raw command
kelvin is physically warmer. Zigbee CCT is measured in reciprocal megakelvin
(mired), not linear Kelvin. Reflect that scale between the device endpoints.
The same formula converts logical -> raw and raw -> logical, independently
of the policy's preferred daytime or evening colour.
"""

from __future__ import annotations

import math

# Used only when the device does not expose its own kelvin bounds; it mirrors
# the clamp of the legacy tambur controller.
FALLBACK_MIN_KELVIN = 1500
FALLBACK_MAX_KELVIN = 6500


def reflect_inverted_kelvin(
    kelvin: int,
    minimum: int = FALLBACK_MIN_KELVIN,
    maximum: int = FALLBACK_MAX_KELVIN,
) -> int:
    """Reverse the device's mired scale, returning integer Kelvin.

    The reflection is its own inverse, so the same call converts a logical
    value into the raw command and a raw observation back into the logical
    value (apart from integer rounding). Clamp before taking reciprocals,
    preserving both endpoints instead of saturating an entire half-range.
    """

    lower, upper = int(minimum), int(maximum)
    if lower <= 0 or lower >= upper:
        raise ValueError("invalid colour-temperature bounds")
    bounded = max(lower, min(upper, int(kelvin)))
    reflected = round(1.0 / (1.0 / lower + 1.0 / upper - 1.0 / bounded))
    return max(lower, min(upper, reflected))


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
    if not math.isfinite(value) or value < 1:
        return None
    return int(value)


__all__ = [
    "FALLBACK_MIN_KELVIN",
    "FALLBACK_MAX_KELVIN",
    "reflect_inverted_kelvin",
    "device_kelvin_bounds",
]
