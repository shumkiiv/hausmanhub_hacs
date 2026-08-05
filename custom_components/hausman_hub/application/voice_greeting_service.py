"""Voice greeting service: settings, watcher, test runs, and dialog turns.

Framework-free core. Every side effect (speech, Home Assistant data, receipt
publishing, sleeping) is injected, so unit tests drive the service with fakes
and the Home Assistant adapter stays thin.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import secrets
from typing import Any, Awaitable, Callable, Protocol, Sequence

from .voice_dialog import dialog_answer
from .voice_greeting import (
    VoiceGreetingViolation,
    default_voice_greeting_settings,
    greeting_document,
    validate_stored_document,
    validate_voice_greeting_settings,
    validate_voice_test_request,
    voice_receipt,
)
from .voice_summary import build_greeting_speech


class VoiceGateway(Protocol):
    """External world required by the voice greeting service."""

    async def async_stations(self) -> list[dict[str, Any]]:
        """List known Yandex Station media players with availability flags."""

    async def async_say_text(self, entity_id: str, text: str) -> None:
        """Speak ``text`` on one station; raise on provider failure."""

    async def async_home_climate(self) -> dict[str, Any]:
        """Return ``{"rooms": [...], "co2Ppm": int | None}``."""

    async def async_security_state(self) -> dict[str, list[str]]:
        """Return leaks, openings, hazards, and lowBatteries name lists."""

    async def async_conversation(self, text: str) -> str | None:
        """Answer ``text`` through Home Assistant conversation, or None."""

    async def async_away_state(self) -> str | None:
        """Current value of the away-mode entity ("on"/"off"), or None."""


class VoiceGreetingService:
    """Owns the voice greeting document and every spoken action."""

    def __init__(
        self,
        store: Any,
        gateway: VoiceGateway,
        *,
        now: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        publish_receipt: Callable[[dict[str, Any], str], None] | None = None,
    ) -> None:
        self._store = store
        self._gateway = gateway
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._id_factory = id_factory or (
            lambda: f"voice-{secrets.token_hex(8)}"
        )
        self._sleep = sleep or asyncio.sleep
        self._publish_receipt = publish_receipt or (lambda receipt, operation: None)
        self._state: dict[str, Any] | None = None
        self._lock = asyncio.Lock()
        self._watcher_epoch = 0

    async def async_load(self) -> None:
        loaded = await self._store.async_load()
        if loaded is None:
            loaded = greeting_document(
                0, self._timestamp(), default_voice_greeting_settings()
            )
        self._state = validate_stored_document(loaded)

    @property
    def config(self) -> dict[str, Any]:
        return deepcopy(self._document())

    async def async_stations(self) -> list[dict[str, Any]]:
        return await self._gateway.async_stations()

    async def async_replace(
        self, expected_revision: object, settings: object
    ) -> dict[str, Any]:
        validated = validate_voice_greeting_settings(settings)
        async with self._lock:
            current = self._document()
            if type(expected_revision) is not int or expected_revision < 0:
                raise VoiceGreetingViolation("expected revision is invalid")
            if expected_revision != current["revision"]:
                raise VoiceGreetingViolation("settings revision changed", stale=True)
            updated = greeting_document(
                expected_revision + 1, self._timestamp(), validated
            )
            await self._store.async_save(updated)
            self._state = updated
        self._emit(
            voice_receipt(
                self._command_id(),
                accepted=True,
                confirmed=True,
                code="saved",
                detail="Настройки приветствия сохранены",
                station_entity_id=validated["stationEntityId"],
                revision=updated["revision"],
                timestamp=self._timestamp(),
            ),
            "voice.yandexGreeting.save",
        )
        return deepcopy(updated)

    async def async_test(self, request: object) -> dict[str, Any]:
        """Speak the exact preview text; never change settings or home mode."""

        try:
            validated = validate_voice_test_request(request)
        except VoiceGreetingViolation:
            return self._finish(
                accepted=False,
                confirmed=False,
                code="invalid_request",
                detail="Тело проверочного запуска заполнено неверно",
                station_entity_id=None,
                operation="voice.yandexGreeting.test",
            )
        station_id = validated["stationEntityId"]
        echo = {
            "includeGreeting": validated["includeGreeting"],
            "summaryItems": validated["summaryItems"],
            "includeFollowUp": validated["includeFollowUp"],
        }
        station = await self._station(station_id)
        if station is None:
            return self._finish(
                accepted=True, confirmed=False, code="station_not_found",
                detail="Выбранная Станция не найдена среди устройств дома",
                station_entity_id=station_id,
                operation="voice.yandexGreeting.test", echo=echo,
            )
        if not station.get("available"):
            return self._finish(
                accepted=True, confirmed=False, code="station_unavailable",
                detail="Выбранная Станция недоступна, речь не отправлена",
                station_entity_id=station_id,
                operation="voice.yandexGreeting.test", echo=echo,
            )
        if validated["openDialog"] and not station.get("localDialogSupported"):
            return self._finish(
                accepted=True, confirmed=False, code="dialog_not_supported",
                detail="Станция не поддерживает локальный диалог, речь не отправлена",
                station_entity_id=station_id,
                operation="voice.yandexGreeting.test", echo=echo,
            )
        try:
            await self._gateway.async_say_text(station_id, validated["speechText"])
        except Exception:
            return self._finish(
                accepted=True, confirmed=False, code="provider_error",
                detail="Голосовой провайдер не принял речь",
                station_entity_id=station_id,
                operation="voice.yandexGreeting.test", echo=echo,
            )
        room = station.get("roomName")
        where = f" ({room})" if isinstance(room, str) and room else ""
        return self._finish(
            accepted=True, confirmed=True, code="spoken",
            detail=f"Тестовая сводка произнесена на Станции{where}",
            station_entity_id=station_id,
            operation="voice.yandexGreeting.test", echo=echo,
        )

    async def async_home_mode_changed(
        self, old_state: str | None, new_state: str | None
    ) -> dict[str, Any] | None:
        """Confirmed away->home transition speaks the greeting exactly once."""

        settings = self._document()["settings"]
        if not settings["enabled"]:
            return self._finish(
                accepted=False,
                confirmed=False,
                code="cancelled",
                detail="Voice contour is disabled, greeting not scheduled",
                station_entity_id=settings["stationEntityId"],
                operation="voice.yandexGreeting.run",
            )
        if old_state != "on" or new_state != "off":
            return self._finish(
                accepted=False,
                confirmed=False,
                code="cancelled",
                detail=f"Transition {old_state}->{new_state} is not a home return",
                station_entity_id=settings["stationEntityId"],
                operation="voice.yandexGreeting.run",
            )
        try:
            return await self._run_greeting(settings)
        except Exception as exc:  # pragma: no cover - live diagnostics
            return self._finish(
                accepted=True,
                confirmed=False,
                code="provider_error",
                detail=f"Voice contour error: {type(exc).__name__}: {exc}"[:180],
                station_entity_id=settings["stationEntityId"],
                operation="voice.yandexGreeting.run",
            )

    async def _run_greeting(self, settings: dict[str, Any]) -> dict[str, Any] | None:
        """Body of the away->home greeting, isolated for error reporting."""

        station_id = settings["stationEntityId"]
        self._watcher_epoch += 1
        epoch = self._watcher_epoch
        self._emit(
            voice_receipt(
                self._command_id(),
                accepted=True, confirmed=False, code="scheduled",
                detail="Возвращение домой подтверждено, приветствие запланировано",
                station_entity_id=station_id,
                timestamp=self._timestamp(),
            ),
            "voice.yandexGreeting.scheduled",
        )
        delay = settings["delaySeconds"]
        if delay > 0:
            await self._sleep(delay)
        current = await self._gateway.async_away_state()
        if epoch != self._watcher_epoch or current != "off":
            return self._finish(
                accepted=True, confirmed=False, code="mode_changed",
                detail="Режим дома изменился до истечения задержки, речь отменена",
                station_entity_id=station_id,
                operation="voice.yandexGreeting.run",
            )
        speech = await self._summary_speech(settings, include_follow_up=True)
        if speech is None:
            return self._finish(
                accepted=True, confirmed=False, code="summary_unavailable",
                detail="Нет данных для сводки, речь не отправлена",
                station_entity_id=station_id,
                operation="voice.yandexGreeting.run",
            )
        station = await self._station(station_id)
        if station is None:
            return self._finish(
                accepted=True, confirmed=False, code="station_not_found",
                detail="Настроенная Станция не найдена среди устройств дома",
                station_entity_id=station_id,
                operation="voice.yandexGreeting.run",
            )
        if not station.get("available"):
            return self._finish(
                accepted=True, confirmed=False, code="station_unavailable",
                detail="Настроенная Станция недоступна, речь не отправлена",
                station_entity_id=station_id,
                operation="voice.yandexGreeting.run",
            )
        try:
            await self._gateway.async_say_text(station_id, speech)
        except Exception:
            return self._finish(
                accepted=True, confirmed=False, code="provider_error",
                detail="Голосовой провайдер не принял приветствие",
                station_entity_id=station_id,
                operation="voice.yandexGreeting.run",
            )
        return self._finish(
            accepted=True, confirmed=True, code="spoken",
            detail="Приветствие произнесено на Станции",
            station_entity_id=station_id,
            operation="voice.yandexGreeting.run",
        )

    async def async_dialog_turn(self, text: object) -> str | None:
        """Answer one private-skill question, or None when questions are off."""

        settings = self._document()["settings"]
        if not settings["homeQuestionsEnabled"]:
            return None
        if not isinstance(text, str) or not text.strip():
            return None
        try:
            snapshot = await self._snapshot()
            greeting = await self._summary_speech(settings, include_follow_up=True)
            return await dialog_answer(
                text.strip(),
                rooms=snapshot["rooms"],
                co2_ppm=snapshot["co2Ppm"],
                leaks=snapshot["leaks"],
                openings=snapshot["openings"],
                hazards=snapshot["hazards"],
                low_batteries=snapshot["lowBatteries"],
                greeting_speech=greeting,
                conversation=self._gateway.async_conversation,
            )
        except Exception as exc:  # pragma: no cover - live diagnostics
            return f"Voice dialog error: {type(exc).__name__}: {exc}"[:180]

    async def _summary_speech(
        self, settings: dict[str, Any], *, include_follow_up: bool
    ) -> str | None:
        snapshot = await self._snapshot()
        return build_greeting_speech(
            settings,
            rooms=snapshot["rooms"],
            co2_ppm=snapshot["co2Ppm"],
            leaks=snapshot["leaks"],
            openings=snapshot["openings"],
            hazards=snapshot["hazards"],
            include_follow_up=include_follow_up,
        )

    async def _snapshot(self) -> dict[str, Any]:
        climate = await self._gateway.async_home_climate()
        security = await self._gateway.async_security_state()
        return {
            "rooms": climate.get("rooms", ()),
            "co2Ppm": climate.get("co2Ppm"),
            "leaks": security.get("leaks", ()),
            "openings": security.get("openings", ()),
            "hazards": security.get("hazards", ()),
            "lowBatteries": security.get("lowBatteries", ()),
        }

    async def _station(self, entity_id: str) -> dict[str, Any] | None:
        for station in await self._gateway.async_stations():
            if station.get("entityId") == entity_id:
                return station
        return None

    def _finish(
        self,
        *,
        accepted: bool,
        confirmed: bool,
        code: str,
        detail: str,
        station_entity_id: str | None,
        operation: str,
        echo: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        receipt = voice_receipt(
            self._command_id(),
            accepted=accepted,
            confirmed=confirmed,
            code=code,
            detail=detail,
            station_entity_id=station_entity_id,
            timestamp=self._timestamp(),
            echo=echo,
        )
        self._emit(receipt, operation)
        return receipt

    def _emit(self, receipt: dict[str, Any], operation: str) -> None:
        self._publish_receipt(deepcopy(receipt), operation)

    def _command_id(self) -> str:
        return self._id_factory()

    def _timestamp(self) -> str:
        value = self._now().astimezone(timezone.utc).isoformat(timespec="seconds")
        return value.replace("+00:00", "Z")

    def _document(self) -> dict[str, Any]:
        if self._state is None:
            raise RuntimeError("voice greeting service is not loaded")
        return self._state
