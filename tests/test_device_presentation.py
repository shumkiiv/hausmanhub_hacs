"""Tests for shared physical-device presentation metadata."""

from __future__ import annotations

import unittest

from custom_components.hausman_hub.application.device_presentation import (
    zigbee2mqtt_image_url,
)


class DevicePresentationTest(unittest.TestCase):
    def test_zigbee2mqtt_image_is_preferred_for_an_identified_device(self) -> None:
        self.assertEqual(
            "https://www.zigbee2mqtt.io/images/devices/TS011F%20plug.png",
            zigbee2mqtt_image_url(
                "TS011F plug",
                (("mqtt", "zigbee2mqtt_0x00124b"),),
            ),
        )

    def test_unknown_or_non_zigbee_device_has_no_misleading_image(self) -> None:
        self.assertIsNone(zigbee2mqtt_image_url(None, ()))
        self.assertIsNone(
            zigbee2mqtt_image_url("PIR", (("matter", "motion-1"),))
        )
