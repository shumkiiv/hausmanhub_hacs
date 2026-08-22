"""Tests for the sustained energy anomaly policy."""

from datetime import datetime, timedelta, timezone
import unittest

from custom_components.hausman_hub.application.energy_anomaly import EnergyAnomalyTracker


class EnergyAnomalyTrackerTest(unittest.TestCase):
    def test_alert_requires_the_whole_sustained_window(self) -> None:
        now = datetime(2026, 8, 22, 9, 0, tzinfo=timezone.utc)
        tracker = EnergyAnomalyTracker(now=lambda: now)
        self.assertFalse(tracker.observe(5000, 4500, 10)["active"])
        now += timedelta(minutes=9, seconds=59)
        self.assertFalse(tracker.observe(5000, 4500, 10)["active"])
        now += timedelta(seconds=1)
        result = tracker.observe(5000, 4500, 10)
        self.assertTrue(result["active"])
        self.assertEqual("2026-08-22T09:10:00Z", result["triggeredAt"])

    def test_below_threshold_missing_data_and_policy_change_reset_observation(self) -> None:
        now = datetime(2026, 8, 22, 9, 0, tzinfo=timezone.utc)
        tracker = EnergyAnomalyTracker(now=lambda: now)
        tracker.observe(5000, 4500, 10)
        now += timedelta(minutes=20)
        self.assertFalse(tracker.observe(None, 4500, 10)["active"])
        self.assertIsNone(tracker.observe(4000, 4500, 10)["observedSince"])
        self.assertFalse(tracker.observe(5000, 6000, 10)["active"])

    def test_unconfigured_policy_never_warns(self) -> None:
        result = EnergyAnomalyTracker().observe(999999, None, None)
        self.assertFalse(result["configured"])
        self.assertFalse(result["active"])
