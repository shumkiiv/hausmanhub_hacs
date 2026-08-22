"""Focused runtime helper regressions found by production observation."""

from custom_components.hausman_hub.application.climate_command_guard import (
    GuardedClimatePlan,
)
from custom_components.hausman_hub.application.climate_runtime import (
    _without_guarded_devices,
)
from custom_components.hausman_hub.domain.climate_ha_calls import (
    ClimateHaCallPlanSnapshot,
)
from custom_components.hausman_hub.domain.contours import ContourMode


def test_deviation_guard_can_remove_active_devices_without_name_error() -> None:
    guarded = GuardedClimatePlan(
        call_plan=ClimateHaCallPlanSnapshot(
            contour_id="climate",
            contour_mode=ContourMode.AUTOMATIC,
            observed_at=1,
            rooms=(),
        ),
        devices=(),
    )

    result = _without_guarded_devices(guarded, frozenset({"managed_ac"}))

    assert result == guarded
