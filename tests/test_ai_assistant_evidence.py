from __future__ import annotations

import unittest

from custom_components.hausman_hub.application.ai_assistant_evidence import (
    ai_evidence_from_observation,
)
from custom_components.hausman_hub.domain.climate_observation import (
    ClimateControlObservation,
    ClimateDataStatus,
    ClimateHomeObservation,
    ClimateObservationSnapshot,
    ClimateRoomObservation,
    ClimateTemperatureQuality,
    ClimateWindowState,
)
from custom_components.hausman_hub.domain.contours import (
    ClimateComfortSettings,
    ClimateContourRoom,
    ClimateStrategy,
    ContourDefinition,
    ContourEngine,
    ContourKind,
    ContourMode,
    ContourRegistry,
)


class AiAssistantEvidenceTest(unittest.TestCase):
    def test_evidence_uses_bounded_public_room_facts_without_bindings(self) -> None:
        observation = ClimateObservationSnapshot(
            observed_at=1_800_000_000_000,
            source_generated_at=1_800_000_000_000,
            data_status=ClimateDataStatus.FRESH,
            home=ClimateHomeObservation(outdoor_temperature=4.5),
            control=ClimateControlObservation(),
            rooms=(
                ClimateRoomObservation(
                    room_id="living",
                    name="Гостиная",
                    data_status=ClimateDataStatus.FRESH,
                    temperature=26.0,
                    humidity=40.0,
                    temperature_quality=ClimateTemperatureQuality.NORMAL,
                    window=ClimateWindowState.CLOSED,
                    observed_target_temperature=24.0,
                    observed_target_humidity=45.0,
                ),
            ),
            devices=(),
        )
        profile = ClimateComfortSettings(25.0, 45, ClimateStrategy.NORMAL)
        contours = ContourRegistry(
            (
                ContourDefinition(
                    contour_id="climate",
                    name="Климат",
                    kind=ContourKind.CLIMATE,
                    mode=ContourMode.OBSERVE,
                    engine=ContourEngine.EXISTING_CLIMATE_CORE,
                    rooms=(
                        ClimateContourRoom(
                            room_id="living",
                            device_ids=("living_ac",),
                            day_profile=profile,
                            night_profile=profile,
                        ),
                    ),
                ),
            )
        )

        evidence = ai_evidence_from_observation(observation, contours)

        self.assertEqual(4.5, evidence["outdoor_temperature"])
        self.assertEqual(["living"], evidence["mismatch_room_ids"])
        self.assertEqual("living", evidence["rooms"][0]["id"])
        self.assertNotIn("name", evidence["rooms"][0])
        self.assertNotIn("device_ids", evidence["rooms"][0])
