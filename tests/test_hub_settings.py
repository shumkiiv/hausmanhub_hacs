"""Strict codec tests for native HausmanHub user settings."""

from __future__ import annotations

import copy
import unittest

from custom_components.hausman_hub.domain.hub_settings import (
    HausmanHubSettings,
    HausmanHubSettingsViolation,
    hub_settings_from_payload,
    hub_settings_to_payload,
)


def settings() -> HausmanHubSettings:
    return HausmanHubSettings(
        light_on_entities=("light.living", "switch.kitchen_led"),
        light_off_entities=("light.living",),
        tv_off_entities=("media_player.living_tv",),
        climate_reports_enabled=False,
        curtain_holidays=("2026-01-01", "2026-05-01"),
    )


class HausmanHubSettingsTest(unittest.TestCase):
    def test_current_document_round_trips_exactly(self) -> None:
        payload = hub_settings_to_payload(settings())

        self.assertEqual(settings(), hub_settings_from_payload(payload))
        self.assertEqual(
            {
                "version",
                "light_on_entities",
                "light_off_entities",
                "tv_off_entities",
                "climate_reports_enabled",
                "curtain_holidays",
            },
            set(payload),
        )

    def test_codec_rejects_unknown_missing_and_old_fields(self) -> None:
        payload = hub_settings_to_payload(settings())
        cases = []
        extra = copy.deepcopy(payload)
        extra["runtime_pause"] = 1
        cases.append(extra)
        missing = copy.deepcopy(payload)
        missing.pop("tv_off_entities")
        cases.append(missing)
        old = copy.deepcopy(payload)
        old["version"] = 0
        cases.append(old)

        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(HausmanHubSettingsViolation):
                    hub_settings_from_payload(case)

    def test_entity_domains_counts_and_uniqueness_are_bounded(self) -> None:
        invalid = (
            {"light_on_entities": ("media_player.tv",)},
            {"tv_off_entities": ("light.ceiling",)},
            {"light_off_entities": ("light.same", "light.same")},
            {"light_on_entities": tuple(f"light.item_{index}" for index in range(41))},
            {"tv_off_entities": tuple(f"media_player.tv_{index}" for index in range(13))},
        )
        for update in invalid:
            with self.subTest(update=update):
                with self.assertRaises(HausmanHubSettingsViolation):
                    HausmanHubSettings(**update)  # type: ignore[arg-type]

    def test_holidays_are_real_unique_canonical_dates(self) -> None:
        for values in (
            ("2026-02-30",),
            ("2026-1-01",),
            ("2026-01-01", "2026-01-01"),
            tuple(f"2026-01-{(index % 28) + 1:02d}" for index in range(65)),
        ):
            with self.subTest(values=values):
                with self.assertRaises(HausmanHubSettingsViolation):
                    HausmanHubSettings(curtain_holidays=values)


if __name__ == "__main__":
    unittest.main()
