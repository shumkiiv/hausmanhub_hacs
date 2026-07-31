"""Bounded energy history tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from custom_components.hausman_hub.application.energy_history import (
    EnergySeriesDescriptor,
    build_energy_history,
)


class EnergyHistoryTest(unittest.TestCase):
    def test_fifteen_minute_series_is_bounded_aggregated_and_private(self) -> None:
        start = datetime(2026, 7, 31, 6, 0, tzinfo=timezone.utc)
        descriptor = EnergySeriesDescriptor(
            entity_id="sensor.private_power",
            source_id="device_public:power",
            device_id="device_public",
            name="Гостиная · Мощность",
            room_id="living",
            unit="W",
            value_key="mean",
        )
        rows = {
            descriptor.entity_id: [
                {"start": start + timedelta(minutes=minute), "mean": value}
                for minute, value in ((0, 60), (5, 90), (10, 120), (15, 150))
            ]
        }

        payload = build_energy_history(
            start=start,
            end=start + timedelta(hours=1),
            interval="15m",
            descriptors=(descriptor,),
            rows_by_entity=rows,
        )

        self.assertEqual(
            [90.0, 150.0],
            [point["value"] for point in payload["series"][0]["points"]],
        )
        self.assertNotIn("sensor.private_power", repr(payload))
        self.assertEqual("device_public:power", payload["series"][0]["sourceId"])
        self.assertEqual("power", payload["series"][0]["metric"])
        self.assertEqual("device", payload["series"][0]["scope"])
        self.assertEqual("selection:power", payload["series"][1]["sourceId"])
        self.assertEqual([90.0, 150.0], [point["value"] for point in payload["series"][1]["points"]])

    def test_selection_is_summed_and_energy_counter_becomes_interval_delta(self) -> None:
        start = datetime(2026, 7, 31, 6, 0, tzinfo=timezone.utc)
        power_a = EnergySeriesDescriptor(
            "sensor.a_power", "a:power", "a", "A · Мощность", "living", "W", "mean", metric="power"
        )
        power_b = EnergySeriesDescriptor(
            "sensor.b_power", "b:power", "b", "B · Мощность", "kitchen", "W", "mean", metric="power"
        )
        energy = EnergySeriesDescriptor(
            "sensor.a_energy", "a:energy", "a", "A · Энергия", "living", "kWh", "sum", metric="energy"
        )
        rows = {
            "sensor.a_power": [
                {"start": start, "mean": 100},
                {"start": start + timedelta(minutes=15), "mean": 120},
            ],
            "sensor.b_power": [
                {"start": start, "mean": 40},
                {"start": start + timedelta(minutes=15), "mean": 60},
            ],
            "sensor.a_energy": [
                {"start": start, "sum": 12.0},
                {"start": start + timedelta(minutes=15), "sum": 12.2},
                {"start": start + timedelta(minutes=30), "sum": 0.1},
            ],
        }

        payload = build_energy_history(
            start=start,
            end=start + timedelta(hours=1),
            interval="15m",
            descriptors=(power_a, power_b, energy),
            rows_by_entity=rows,
        )
        by_source = {item["sourceId"]: item for item in payload["series"]}

        self.assertEqual([140.0, 180.0], [point["value"] for point in by_source["selection:power"]["points"]])
        self.assertEqual([0.2, 0.1], [point["value"] for point in by_source["a:energy"]["points"]])
        self.assertEqual("delta", by_source["a:energy"]["aggregation"])
        self.assertEqual("sum", by_source["selection:energy"]["aggregation"])


if __name__ == "__main__":
    unittest.main()
