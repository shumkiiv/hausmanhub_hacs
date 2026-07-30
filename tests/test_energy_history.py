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


if __name__ == "__main__":
    unittest.main()
