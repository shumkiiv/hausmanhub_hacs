"""Framework-free voice greeting domain: settings, validation, and receipts.

The module mirrors contract package 0.17.0 (hausman-hub-voice-greeting-config,
hausman-hub-voice-greeting-test-request, hausman-hub-voice-command-receipt).
It performs no Home Assistant I/O; all runtime effects go through the gateway
protocol in ``voice_greeting_service``.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from ..correlation import CorrelationIdError, validate_correlation_id


VOICE_GREETING_CONTRACT_NAME = "hausman-hub-voice-greeting-config"
VOICE_GREETING_CONTRACT_VERSION = 1
VOICE_TEST_REQUEST_CONTRACT_NAME = "hausman-hub-voice-greeting-test-request"
VOICE_TEST_REQUEST_CONTRACT_VERSION = 1
VOICE_RECEIPT_CONTRACT_NAME = "hausman-hub-voice-command-receipt"
VOICE_RECEIPT_CONTRACT_VERSION = 1

DEFAULT_AWAY_ENTITY_ID = "input_boolean.away_from_home"
MAX_TEXT_LENGTH = 180
MAX_SPEECH_LENGTH = 512
MAX_SUMMARY_ITEMS = 6
SUMMARY_ITEMS = frozenset(
    {"temperature", "humidity", "air_quality", "security", "outdoor", "low_battery"}
)
SUMMARY_STYLES = frozenset({"numbers", "human"})
DEFAULT_SUMMARY_STYLE = "human"
RECEIPT_CODES = frozenset(
    {
        "saved",
        "scheduled",
        "spoken",
        "cancelled",
        "station_unavailable",
        "station_not_found",
        "mode_changed",
        "summary_unavailable",
        "dialog_not_supported",
        "conversation_timeout",
        "provider_error",
        "invalid_request",
    }
)
_MEDIA_PLAYER = re.compile(r"^media_player\.[a-z0-9_]+$")


class VoiceGreetingViolation(ValueError):
    """A voice greeting document is malformed, stale, or outside safe bounds."""

    def __init__(self, message: str, *, stale: bool = False) -> None:
        super().__init__(message)
        self.stale = stale


def default_voice_greeting_settings() -> dict[str, Any]:
    return {
        "enabled": False,
        "stationEntityId": "media_player.yandex_station",
        "delaySeconds": 3,
        "greetingText": "Добро пожаловать домой",
        "summaryItems": ["temperature", "humidity", "air_quality", "security"],
        "summaryStyle": DEFAULT_SUMMARY_STYLE,
        "followUpEnabled": False,
        "followUpText": "Что ещё рассказать?",
        "homeQuestionsEnabled": False,
    }


def validate_voice_greeting_settings(value: object) -> dict[str, Any]:
    required = {
        "enabled",
        "stationEntityId",
        "delaySeconds",
        "greetingText",
        "summaryItems",
        "followUpEnabled",
        "followUpText",
        "homeQuestionsEnabled",
    }
    if (
        not isinstance(value, dict)
        or not required <= set(value)
        or set(value) - required > {"summaryStyle"}
    ):
        raise VoiceGreetingViolation("voice greeting settings fields are invalid")
    result = deepcopy(value)
    # Documents saved before contracts 0.43.0 carry no summaryStyle; reading
    # them must not fail, the default keeps the human phrasing.
    style = result.setdefault("summaryStyle", DEFAULT_SUMMARY_STYLE)
    if style not in SUMMARY_STYLES:
        raise VoiceGreetingViolation("summaryStyle is invalid")
    for key in ("enabled", "followUpEnabled", "homeQuestionsEnabled"):
        _boolean(result[key], key)
    station = result["stationEntityId"]
    if not isinstance(station, str) or not _MEDIA_PLAYER.fullmatch(station):
        raise VoiceGreetingViolation("stationEntityId must be a media_player entity")
    delay = result["delaySeconds"]
    if type(delay) is not int or not 0 <= delay <= 60:
        raise VoiceGreetingViolation("delaySeconds is out of range")
    for key in ("greetingText", "followUpText"):
        text = result[key]
        if not isinstance(text, str) or not text or len(text) > MAX_TEXT_LENGTH:
            raise VoiceGreetingViolation(f"{key} is invalid")
    items = result["summaryItems"]
    if (
        not isinstance(items, list)
        or not items
        or len(items) > MAX_SUMMARY_ITEMS
        or len(items) != len(set(items))
        or any(item not in SUMMARY_ITEMS for item in items)
    ):
        raise VoiceGreetingViolation("summaryItems are invalid")
    if result["followUpEnabled"] and not result["homeQuestionsEnabled"]:
        raise VoiceGreetingViolation("followUpEnabled requires homeQuestionsEnabled")
    return result


def validate_voice_test_request(value: object) -> dict[str, Any]:
    required_fields = {
        "contract",
        "stationEntityId",
        "useCurrentHomeState",
        "includeGreeting",
        "summaryItems",
        "includeFollowUp",
        "speechText",
        "openDialog",
    }
    if (
        not isinstance(value, dict)
        or not required_fields <= set(value)
        or set(value) - required_fields > {"correlationId"}
    ):
        raise VoiceGreetingViolation("voice test request fields are invalid")
    result = deepcopy(value)
    if "correlationId" in result:
        try:
            result["correlationId"] = validate_correlation_id(result["correlationId"])
        except CorrelationIdError as error:
            raise VoiceGreetingViolation("correlationId is invalid") from error
    if result.pop("contract") != {
        "name": VOICE_TEST_REQUEST_CONTRACT_NAME,
        "version": VOICE_TEST_REQUEST_CONTRACT_VERSION,
    }:
        raise VoiceGreetingViolation("voice test request contract is invalid")
    station = result["stationEntityId"]
    if not isinstance(station, str) or not _MEDIA_PLAYER.fullmatch(station):
        raise VoiceGreetingViolation("stationEntityId must be a media_player entity")
    for key in ("useCurrentHomeState", "includeGreeting", "includeFollowUp", "openDialog"):
        _boolean(result[key], key)
    items = result["summaryItems"]
    if (
        not isinstance(items, list)
        or len(items) > MAX_SUMMARY_ITEMS
        or len(items) != len(set(items))
        or any(item not in SUMMARY_ITEMS for item in items)
    ):
        raise VoiceGreetingViolation("summaryItems are invalid")
    speech = result["speechText"]
    if not isinstance(speech, str) or not speech or len(speech) > MAX_SPEECH_LENGTH:
        raise VoiceGreetingViolation("speechText is invalid")
    return result


def voice_receipt(
    command_id: str,
    *,
    accepted: bool,
    confirmed: bool,
    code: str,
    detail: str,
    station_entity_id: str | None,
    timestamp: str,
    revision: int | None = None,
    echo: dict[str, Any] | None = None,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """Build one contract-shaped voice command receipt."""

    if code not in RECEIPT_CODES:
        raise ValueError(f"unknown voice receipt code: {code}")
    receipt: dict[str, Any] = {
        "contract": {
            "name": VOICE_RECEIPT_CONTRACT_NAME,
            "version": VOICE_RECEIPT_CONTRACT_VERSION,
        },
        "commandId": command_id,
        "correlationId": correlation_id or command_id,
        "accepted": accepted,
        "confirmed": confirmed,
        "code": code,
        "detail": detail,
        "stationEntityId": station_entity_id,
        "timestamp": timestamp,
    }
    if revision is not None:
        receipt["revision"] = revision
    if echo is not None:
        receipt["echo"] = {
            "includeGreeting": echo["includeGreeting"],
            "summaryItems": list(echo["summaryItems"]),
            "includeFollowUp": echo["includeFollowUp"],
        }
    return receipt


def greeting_document(
    revision: int, updated_at: str, settings: dict[str, Any]
) -> dict[str, Any]:
    return {
        "contract": {
            "name": VOICE_GREETING_CONTRACT_NAME,
            "version": VOICE_GREETING_CONTRACT_VERSION,
        },
        "revision": revision,
        "updatedAt": updated_at,
        "settings": deepcopy(settings),
    }


def validate_stored_document(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "contract",
        "revision",
        "updatedAt",
        "settings",
    }:
        raise VoiceGreetingViolation("stored voice greeting document is invalid")
    result = deepcopy(value)
    if result["contract"] != {
        "name": VOICE_GREETING_CONTRACT_NAME,
        "version": VOICE_GREETING_CONTRACT_VERSION,
    }:
        raise VoiceGreetingViolation("stored voice greeting contract is invalid")
    revision = result["revision"]
    if type(revision) is not int or revision < 0:
        raise VoiceGreetingViolation("stored voice greeting revision is invalid")
    if not isinstance(result["updatedAt"], str) or not result["updatedAt"]:
        raise VoiceGreetingViolation("stored voice greeting timestamp is invalid")
    result["settings"] = validate_voice_greeting_settings(result["settings"])
    return result


def _boolean(value: object, label: str) -> None:
    if type(value) is not bool:
        raise VoiceGreetingViolation(f"{label} must be boolean")
