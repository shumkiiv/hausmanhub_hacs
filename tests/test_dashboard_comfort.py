"""Server-side dashboard comfort scoring tests."""

from __future__ import annotations

from custom_components.hausman_hub.application.dashboard_comfort import (
    build_dashboard_comfort,
)


def test_complete_observations_publish_fresh_weighted_score() -> None:
    comfort = build_dashboard_comfort(
        (
            {
                "temp": 25.5,
                "targetTemp": 25.0,
                "humidity": 48.0,
                "targetHumidity": 50.0,
            },
        ),
        co2=700,
    )

    assert comfort == {
        "available": True,
        "score": 95,
        "statusLabel": "Отлично",
        "dataQuality": "fresh",
    }


def test_temperature_target_is_required_for_any_comfort_score() -> None:
    comfort = build_dashboard_comfort(
        ({"temp": 25.0, "humidity": 50.0},),
        co2=600,
    )

    assert comfort == {
        "available": False,
        "score": None,
        "statusLabel": None,
        "dataQuality": "limited",
    }


def test_missing_secondary_channels_keep_score_but_mark_it_limited() -> None:
    comfort = build_dashboard_comfort(
        ({"temp": 27.0, "targetTemp": 25.0},),
        co2=None,
    )

    assert comfort == {
        "available": True,
        "score": 70,
        "statusLabel": "Нормально",
        "dataQuality": "limited",
    }


def test_explicit_stale_room_status_wins_over_complete_coverage() -> None:
    comfort = build_dashboard_comfort(
        (
            {
                "temp": 25.0,
                "targetTemp": 25.0,
                "humidity": 50.0,
                "targetHumidity": 50.0,
                "status": "stale",
            },
        ),
        co2=700,
    )

    assert comfort["score"] == 100
    assert comfort["dataQuality"] == "stale"


def test_coverage_below_sixty_percent_is_limited() -> None:
    comfort = build_dashboard_comfort(
        (
            {"temp": 25.0, "targetTemp": 25.0, "humidity": 50.0, "targetHumidity": 50.0},
            {"temp": None, "targetTemp": 25.0, "humidity": None, "targetHumidity": 50.0},
        ),
        co2=700,
    )

    assert comfort["available"] is True
    assert comfort["dataQuality"] == "limited"
