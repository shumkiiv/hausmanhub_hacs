"""CCT inversion acts on the device's reciprocal-temperature scale."""

import math

import pytest

from custom_components.hausman_hub.application.room_lighting_color import (
    device_kelvin_bounds, reflect_inverted_kelvin,
)


@pytest.mark.parametrize("logical, raw", [(2000, 6535), (2200, 5038), (3000, 3128),
                                          (3400, 2786), (4000, 2481), (6500, 2003), (6535, 2000)])
def test_device_scale_reflection_has_no_policy_kelvin_pivot(logical, raw):
    assert reflect_inverted_kelvin(logical, 2000, 6535) == raw


@pytest.mark.parametrize("lo, hi", [(2000, 6535), (1500, 6500), (2200, 6500), (2700, 6000)])
def test_full_scale_is_inverse_and_monotonic(lo, hi):
    previous = hi + 1
    # Two integer-Kelvin roundings: half a raw Kelvin is magnified by
    # at most (hi/lo)^2 on the inverse, then rounded once more. This bound
    # follows from quantization, not from a relaxed device colour tolerance.
    quantization_bound = math.ceil(0.5 * (hi / lo) ** 2 + 0.5)
    for logical in range(lo, hi + 1):
        raw = reflect_inverted_kelvin(logical, lo, hi)
        assert lo <= raw <= hi
        assert raw <= previous
        assert abs(reflect_inverted_kelvin(raw, lo, hi) - logical) <= quantization_bound
        # Model HA -> integer Zigbee mired -> HA Kelvin read-back separately.
        transport_mired = round(1_000_000 / raw)
        reported_kelvin = round(1_000_000 / transport_mired)
        # HA clamps its exposed Kelvin range; a transport unit may round beyond it.
        reported_kelvin = max(lo, min(hi, reported_kelvin))
        assert abs(reflect_inverted_kelvin(reported_kelvin, lo, hi) - logical) <= 50
        previous = raw


@pytest.mark.parametrize("value, expected", [(0, 6535), (-10, 6535), (1500, 6535), (7000, 2000)])
def test_out_of_range_requests_clamp_before_reciprocal(value, expected):
    assert reflect_inverted_kelvin(value, 2000, 6535) == expected


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, 0, -10, True, "2000", None])
def test_invalid_device_bounds_use_fallback(bad):
    assert device_kelvin_bounds({"min_color_temp_kelvin": bad, "max_color_temp_kelvin": 6535}) == (1500, 6500)
    assert device_kelvin_bounds({"min_color_temp_kelvin": 2000, "max_color_temp_kelvin": bad}) == (1500, 6500)
