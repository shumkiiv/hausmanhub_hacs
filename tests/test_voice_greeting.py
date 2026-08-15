"""Contract tests for the voice greeting service (Yandex Station)."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import unittest

from custom_components.hausman_hub.application.voice_dialog import (
    dialog_static_answer,
    match_room,
)
from custom_components.hausman_hub.application.voice_greeting import (
    VoiceGreetingViolation,
    default_voice_greeting_settings,
    validate_stored_document,
    validate_voice_greeting_settings,
    validate_voice_test_request,
)
from custom_components.hausman_hub.application.voice_greeting_service import (
    VoiceGreetingService,
)
from custom_components.hausman_hub.application.voice_summary import (
    air_quality_label,
    build_greeting_speech,
)


NOW = datetime(2026, 8, 5, 6, 30, tzinfo=timezone.utc)


class _Store:
    def __init__(self, value: object = None) -> None:
        self.value = deepcopy(value)
        self.saved: list[dict[str, object]] = []
        self.fail = False

    async def async_load(self) -> object:
        return deepcopy(self.value)

    async def async_save(self, value: dict[str, object]) -> None:
        if self.fail:
            raise RuntimeError("storage failed")
        self.value = deepcopy(value)
        self.saved.append(deepcopy(value))


class _Gateway:
    def __init__(self) -> None:
        self.stations = [
            {
                "entityId": "media_player.yandex_station_demo",
                "name": "Яндекс Станция",
                "roomId": "kitchen",
                "roomName": "Кухня",
                "available": True,
                "localDialogSupported": True,
            }
        ]
        self.spoken: list[tuple[str, str]] = []
        self.say_fail = False
        self.away_state = "off"
        self.climate = {
            "rooms": [
                {"roomName": "Гостиная", "temperatureC": 24.5, "humidityPercent": 44.0},
                {"roomName": "Кухня", "temperatureC": 25.1, "humidityPercent": 41.0},
            ],
            "co2Ppm": 612,
        }
        self.security = {
            "leaks": [],
            "openings": [],
            "hazards": [],
            "lowBatteries": [],
        }
        self.conversation_answer: str | None = "Ответ от Home Assistant"
        self.conversation_fail = False

    async def async_stations(self) -> list[dict[str, object]]:
        return deepcopy(self.stations)

    async def async_say_text(self, entity_id: str, text: str) -> None:
        if self.say_fail:
            raise RuntimeError("tts failed")
        self.spoken.append((entity_id, text))

    async def async_home_climate(self) -> dict[str, object]:
        return deepcopy(self.climate)

    async def async_security_state(self) -> dict[str, list[str]]:
        return deepcopy(self.security)

    async def async_conversation(self, text: str) -> str | None:
        if self.conversation_fail:
            raise RuntimeError("conversation failed")
        return self.conversation_answer

    async def async_away_state(self) -> str | None:
        return self.away_state


class VoiceGreetingServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.store = _Store()
        self.gateway = _Gateway()
        self.receipts: list[tuple[dict[str, object], str]] = []
        self.sleeps: list[float] = []
        self.ids = iter(range(1, 100))
        self.service = VoiceGreetingService(
            self.store,
            self.gateway,
            now=lambda: NOW,
            id_factory=lambda: f"voice-test-{next(self.ids):03d}",
            sleep=self._record_sleep,
            publish_receipt=lambda receipt, operation: self.receipts.append(
                (receipt, operation)
            ),
        )
        await self.service.async_load()

    async def _record_sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def _enabled_settings(self) -> dict[str, object]:
        settings = default_voice_greeting_settings()
        settings["enabled"] = True
        settings["stationEntityId"] = "media_player.yandex_station_demo"
        return settings

    async def test_defaults_are_safe_and_contract_shaped(self) -> None:
        config = self.service.config
        self.assertEqual(
            {"name": "hausman-hub-voice-greeting-config", "version": 1},
            config["contract"],
        )
        self.assertEqual(0, config["revision"])
        self.assertFalse(config["settings"]["enabled"])
        self.assertFalse(config["settings"]["homeQuestionsEnabled"])
        self.assertEqual("2026-08-05T06:30:00Z", config["updatedAt"])

    async def test_settings_validation_invariants(self) -> None:
        settings = default_voice_greeting_settings()
        settings["followUpEnabled"] = True
        with self.assertRaises(VoiceGreetingViolation):
            validate_voice_greeting_settings(settings)

        settings = default_voice_greeting_settings()
        settings["summaryItems"] = []
        with self.assertRaises(VoiceGreetingViolation):
            validate_voice_greeting_settings(settings)

        settings = default_voice_greeting_settings()
        settings["stationEntityId"] = "switch.not_a_station"
        with self.assertRaises(VoiceGreetingViolation):
            validate_voice_greeting_settings(settings)

        settings = default_voice_greeting_settings()
        settings["delaySeconds"] = 61
        with self.assertRaises(VoiceGreetingViolation):
            validate_voice_greeting_settings(settings)

    async def test_replace_is_atomic_and_emits_saved_receipt(self) -> None:
        saved = await self.service.async_replace(0, self._enabled_settings())
        self.assertEqual(1, saved["revision"])
        self.assertTrue(saved["settings"]["enabled"])
        self.assertEqual(1, len(self.receipts))
        receipt, operation = self.receipts[0]
        self.assertEqual("voice.yandexGreeting.save", operation)
        self.assertEqual("saved", receipt["code"])
        self.assertEqual(1, receipt["revision"])

        with self.assertRaises(VoiceGreetingViolation) as caught:
            await self.service.async_replace(0, self._enabled_settings())
        self.assertTrue(caught.exception.stale)

    async def test_failed_save_does_not_change_memory(self) -> None:
        before = self.service.config
        self.store.fail = True
        with self.assertRaises(RuntimeError):
            await self.service.async_replace(0, self._enabled_settings())
        self.assertEqual(before, self.service.config)

    def _test_request(self, **overrides: object) -> dict[str, object]:
        request: dict[str, object] = {
            "contract": {
                "name": "hausman-hub-voice-greeting-test-request",
                "version": 1,
            },
            "stationEntityId": "media_player.yandex_station_demo",
            "useCurrentHomeState": True,
            "includeGreeting": True,
            "summaryItems": ["temperature", "humidity"],
            "includeFollowUp": False,
            "speechText": "Добро пожаловать домой. Дома 24,5 градуса.",
            "openDialog": False,
        }
        request.update(overrides)
        return request

    async def test_test_request_speaks_exact_text_and_echoes(self) -> None:
        receipt = await self.service.async_test(
            self._test_request(correlationId="corr.voice.tablet-1")
        )
        self.assertTrue(receipt["confirmed"])
        self.assertEqual("spoken", receipt["code"])
        self.assertEqual("corr.voice.tablet-1", receipt["correlationId"])
        self.assertEqual(
            [("media_player.yandex_station_demo", "Добро пожаловать домой. Дома 24,5 градуса.")],
            self.gateway.spoken,
        )
        self.assertEqual(
            {"includeGreeting": True, "summaryItems": ["temperature", "humidity"], "includeFollowUp": False},
            receipt["echo"],
        )
        self.assertEqual(("voice.yandexGreeting.test",), tuple(op for _, op in self.receipts))

    async def test_test_request_fail_closed_codes(self) -> None:
        receipt = await self.service.async_test(
            self._test_request(stationEntityId="media_player.yandex_station_missing")
        )
        self.assertEqual("station_not_found", receipt["code"])
        self.assertFalse(receipt["confirmed"])

        self.gateway.stations[0]["available"] = False
        receipt = await self.service.async_test(self._test_request())
        self.assertEqual("station_unavailable", receipt["code"])

        self.gateway.stations[0]["available"] = True
        self.gateway.stations[0]["localDialogSupported"] = False
        receipt = await self.service.async_test(self._test_request(openDialog=True))
        self.assertEqual("dialog_not_supported", receipt["code"])
        self.assertEqual([], self.gateway.spoken)

        self.gateway.stations[0]["localDialogSupported"] = True
        self.gateway.say_fail = True
        receipt = await self.service.async_test(self._test_request())
        self.assertEqual("provider_error", receipt["code"])

        receipt = await self.service.async_test({"bad": "body"})
        self.assertEqual("invalid_request", receipt["code"])
        self.assertFalse(receipt["accepted"])

    async def test_watcher_speaks_once_after_confirmed_transition(self) -> None:
        await self.service.async_replace(0, self._enabled_settings())
        self.receipts.clear()

        ignored = await self.service.async_home_mode_changed("off", "off")
        self.assertEqual("cancelled", ignored["code"])
        ignored = await self.service.async_home_mode_changed("off", "on")
        self.assertEqual("cancelled", ignored["code"])
        self.receipts.clear()

        receipt = await self.service.async_home_mode_changed("on", "off")
        self.assertEqual("spoken", receipt["code"])
        self.assertEqual([3.0], self.sleeps)
        self.assertEqual(1, len(self.gateway.spoken))
        entity_id, text = self.gateway.spoken[0]
        self.assertEqual("media_player.yandex_station_demo", entity_id)
        self.assertIn("Добро пожаловать домой", text)
        self.assertIn("24,5", text)
        operations = [op for _, op in self.receipts]
        self.assertEqual(
            ["voice.yandexGreeting.scheduled", "voice.yandexGreeting.run"], operations
        )

    async def test_watcher_cancels_when_mode_flips_back(self) -> None:
        await self.service.async_replace(0, self._enabled_settings())
        self.receipts.clear()
        self.gateway.away_state = "on"

        receipt = await self.service.async_home_mode_changed("on", "off")
        self.assertEqual("mode_changed", receipt["code"])
        self.assertFalse(receipt["confirmed"])
        self.assertEqual([], self.gateway.spoken)

    async def test_saved_settings_cancel_a_pending_greeting(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def blocked_sleep(_: float) -> None:
            started.set()
            await release.wait()

        service = VoiceGreetingService(
            self.store,
            self.gateway,
            now=lambda: NOW,
            id_factory=lambda: f"voice-test-{next(self.ids):03d}",
            sleep=blocked_sleep,
            publish_receipt=lambda receipt, operation: self.receipts.append(
                (receipt, operation)
            ),
        )
        await service.async_load()
        await service.async_replace(0, self._enabled_settings())
        pending = asyncio.create_task(service.async_home_mode_changed("on", "off"))
        await started.wait()

        disabled = self._enabled_settings()
        disabled["enabled"] = False
        await service.async_replace(1, disabled)
        release.set()

        receipt = await pending
        self.assertEqual("cancelled", receipt["code"])
        self.assertEqual([], self.gateway.spoken)

    async def test_watcher_reports_unavailable_summary_and_station(self) -> None:
        await self.service.async_replace(0, self._enabled_settings())
        self.receipts.clear()
        self.gateway.climate = {"rooms": [], "co2Ppm": None}
        settings = default_voice_greeting_settings()
        settings["enabled"] = True
        settings["stationEntityId"] = "media_player.yandex_station_demo"
        settings["summaryItems"] = ["air_quality"]
        await self.service.async_replace(1, settings)

        receipt = await self.service.async_home_mode_changed("on", "off")
        self.assertEqual("summary_unavailable", receipt["code"])

        self.gateway.climate = {
            "rooms": [{"roomName": "Гостиная", "temperatureC": 24.5, "humidityPercent": None}],
            "co2Ppm": 612,
        }
        self.gateway.stations[0]["available"] = False
        receipt = await self.service.async_home_mode_changed("on", "off")
        self.assertEqual("station_unavailable", receipt["code"])

    async def test_watcher_disabled_reports_cancelled(self) -> None:
        receipt = await self.service.async_home_mode_changed("on", "off")
        self.assertEqual("cancelled", receipt["code"])
        self.assertFalse(receipt["accepted"])
        operations = [op for _, op in self.receipts]
        self.assertEqual(["voice.yandexGreeting.run"], operations)
        self.assertEqual([], self.gateway.spoken)

    async def test_dialog_turn_requires_opt_in(self) -> None:
        self.assertIsNone(await self.service.async_dialog_turn("что происходит дома"))

        settings = self._enabled_settings()
        settings["homeQuestionsEnabled"] = True
        await self.service.async_replace(0, settings)

        answer = await self.service.async_dialog_turn("какое качество воздуха")
        self.assertIn("612", answer)
        self.assertIn("хорошее", answer)

        answer = await self.service.async_dialog_turn("какая температура в гостиной")
        self.assertIn("24,5", answer)

        answer = await self.service.async_dialog_turn("включи торжественную музыку")
        self.assertEqual("Ответ от Home Assistant", answer)

        self.gateway.conversation_fail = True
        answer = await self.service.async_dialog_turn("расскажи что-нибудь неизвестное")
        self.assertIn("Не удалось получить ответ", answer)

    async def test_stored_document_validation_fails_closed(self) -> None:
        with self.assertRaises(VoiceGreetingViolation):
            validate_stored_document({"contract": {"name": "other", "version": 1}})
        document = {
            "contract": {"name": "hausman-hub-voice-greeting-config", "version": 1},
            "revision": 0,
            "updatedAt": "2026-08-05T06:30:00Z",
            "settings": default_voice_greeting_settings(),
        }
        validated = validate_stored_document(document)
        self.assertEqual(0, validated["revision"])
        broken = deepcopy(document)
        broken["settings"]["delaySeconds"] = 999
        with self.assertRaises(VoiceGreetingViolation):
            validate_stored_document(broken)


class VoiceSummaryTest(unittest.TestCase):
    def test_summary_blocks_and_follow_up(self) -> None:
        settings = default_voice_greeting_settings()
        settings["followUpEnabled"] = True
        settings["homeQuestionsEnabled"] = True
        speech = build_greeting_speech(
            settings,
            rooms=[{"roomName": "Гостиная", "temperatureC": 24.5, "humidityPercent": 44.0}],
            co2_ppm=612,
            leaks=[],
            openings=[],
            hazards=[],
        )
        self.assertIn("Добро пожаловать домой", speech)
        self.assertIn("24,5", speech)
        self.assertIn("44", speech)
        self.assertIn("612 ppm", speech)
        self.assertIn("Безопасность в порядке", speech)
        self.assertTrue(speech.endswith("Что ещё рассказать."))

    def test_security_problems_are_spoken_first_class(self) -> None:
        settings = default_voice_greeting_settings()
        settings["summaryItems"] = ["security"]
        speech = build_greeting_speech(
            settings,
            rooms=[],
            co2_ppm=None,
            leaks=["Ванная"],
            openings=["Окно кухня"],
            hazards=[],
        )
        self.assertIn("протечка: Ванная", speech)
        self.assertIn("открыто: Окно кухня", speech)

    def test_no_data_means_no_speech(self) -> None:
        settings = default_voice_greeting_settings()
        settings["summaryItems"] = ["air_quality"]
        self.assertIsNone(
            build_greeting_speech(
                settings, rooms=[], co2_ppm=None, leaks=[], openings=[], hazards=[],
                include_greeting=False,
            )
        )

    def test_air_quality_label(self) -> None:
        self.assertEqual("не определено", air_quality_label(None))
        self.assertEqual("хорошее", air_quality_label(612))
        self.assertEqual("допустимое", air_quality_label(900))
        self.assertEqual("требует проветривания", air_quality_label(1500))


class VoiceDialogTest(unittest.TestCase):
    def _snapshot(self) -> dict[str, object]:
        return {
            "rooms": [
                {"roomName": "Гостиная", "temperatureC": 24.5, "humidityPercent": 44.0},
                {"roomName": "Кухня", "temperatureC": 25.0, "humidityPercent": 41.0},
            ],
            "co2_ppm": 612,
            "leaks": [],
            "openings": [],
            "hazards": [],
            "low_batteries": [],
            "greeting_speech": "Добро пожаловать домой. Сводка.",
        }

    def test_known_branches(self) -> None:
        snapshot = self._snapshot()
        self.assertIn(
            "Добро пожаловать домой",
            dialog_static_answer("приветствие при возвращении домой", **snapshot),
        )
        self.assertIn("могу", dialog_static_answer("что ты умеешь", **snapshot).lower())
        self.assertIn("Протечек не обнаружено", dialog_static_answer("есть протечки", **snapshot))
        self.assertIn("закрыты", dialog_static_answer("что открыто", **snapshot))
        self.assertIn("в норме", dialog_static_answer("как батареи", **snapshot))
        self.assertIn("спокойно", dialog_static_answer("всё ли спокойно", **snapshot))
        self.assertIn("612", dialog_static_answer("какой co2", **snapshot))
        self.assertIn("24,5", dialog_static_answer("температура в гостиной", **snapshot))
        self.assertIn("по дому", dialog_static_answer("какая температура дома", **snapshot))
        self.assertIn("Сводка", dialog_static_answer("дай сводку", **snapshot))
        self.assertIsNone(dialog_static_answer("совершенно посторонний вопрос", **snapshot))

    def test_room_matching(self) -> None:
        self.assertEqual("гостиная", match_room("в гостиной холодно"))
        self.assertEqual("детская", match_room("детская комната"))
        self.assertIsNone(match_room("на балконе"))

    def test_missing_room_sensor(self) -> None:
        snapshot = self._snapshot()
        answer = dialog_static_answer("какая температура в спальне", **snapshot)
        self.assertIn("не найден доступный климатический датчик", answer)


class VoiceTestRequestValidationTest(unittest.TestCase):
    def test_request_validation(self) -> None:
        valid = {
            "contract": {
                "name": "hausman-hub-voice-greeting-test-request",
                "version": 1,
            },
            "stationEntityId": "media_player.yandex_station_demo",
            "useCurrentHomeState": True,
            "includeGreeting": True,
            "summaryItems": [],
            "includeFollowUp": False,
            "speechText": "Текст",
            "openDialog": False,
        }
        validated = validate_voice_test_request(valid)
        self.assertEqual([], validated["summaryItems"])
        correlated = validate_voice_test_request(
            {**valid, "correlationId": "corr.voice.tablet-1"}
        )
        self.assertEqual("corr.voice.tablet-1", correlated["correlationId"])

        for broken in (
            {**valid, "speechText": ""},
            {**valid, "speechText": "x" * 513},
            {**valid, "stationEntityId": "light.kitchen"},
            {**valid, "summaryItems": ["unknown_block"]},
            {**valid, "contract": {"name": "other", "version": 1}},
            {**valid, "correlationId": "invalid value"},
        ):
            with self.assertRaises(VoiceGreetingViolation):
                validate_voice_test_request(broken)


class VoiceApiViewSourceTest(unittest.TestCase):
    """Static guards for the Home Assistant view layer.

    The view module imports Home Assistant, so it cannot be imported here.
    These checks scan the source and pin the HomeAssistantView.json call
    signature: the keyword is ``status_code``, not ``status`` (a bare
    ``status=`` raises TypeError on a real Home Assistant and breaks the
    endpoint with HTTP 500, as observed on the live instance).
    """

    def test_json_calls_use_status_code_keyword(self) -> None:
        import re
        from pathlib import Path

        source = Path(
            "custom_components/hausman_hub/voice_api.py"
        ).read_text(encoding="utf-8")
        for call in re.findall(r"self\.json\([^)]*\)", source):
            self.assertNotRegex(
                call,
                r"(?<![_a-z])status\s*=",
                f"self.json call must use status_code, not status: {call}",
            )

    def test_unload_cancels_pending_voice_tasks(self) -> None:
        from pathlib import Path

        source = Path(
            "custom_components/hausman_hub/voice_api.py"
        ).read_text(encoding="utf-8")
        self.assertIn('DATA_VOICE_TASKS = "voice_greeting_tasks"', source)
        self.assertIn("tasks.add(task)", source)
        self.assertIn("task.cancel()", source)


if __name__ == "__main__":
    unittest.main()
