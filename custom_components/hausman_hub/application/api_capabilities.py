"""Public discovery contract for the local HausmanHub tablet API."""

from __future__ import annotations

from .android_climate_values import (
    ANDROID_CLIMATE_CONTRACT_NAME,
    ANDROID_CLIMATE_CONTRACT_VERSION,
)
from .contour_apply import (
    CLIMATE_CONTROL_RECEIPT_CONTRACT_NAME,
    CLIMATE_CONTROL_RECEIPT_CONTRACT_VERSION,
    CONTOUR_APPLY_CONTRACT_VERSION,
    CONTOUR_APPLY_REQUEST_CONTRACT_NAME,
)
from .contour_override import (
    TEMPORARY_TEMPERATURE_REQUEST_CONTRACT_NAME,
    TEMPORARY_TEMPERATURE_REQUEST_CONTRACT_VERSION,
)
from .contours import CONTOUR_CONTRACT_NAME, CONTOUR_CONTRACT_VERSION


API_CAPABILITIES_CONTRACT_NAME = "hausman-hub-capabilities"
API_CAPABILITIES_CONTRACT_VERSION = 1
API_MAJOR_VERSION = 1
API_BASE_PATH = "/api/hausman_hub/v1"

CAPABILITIES_PATH = f"{API_BASE_PATH}/capabilities"
HOME_PATH = f"{API_BASE_PATH}/home"
DASHBOARD_PATH = f"{API_BASE_PATH}/dashboard"
TABLET_PROFILE_PATH = f"{API_BASE_PATH}/tablet-profile"
ENERGY_SETTINGS_PATH = f"{API_BASE_PATH}/energy-settings"
ENERGY_HISTORY_PATH = f"{API_BASE_PATH}/energy/history"
EVENT_STREAM_PATH = f"{API_BASE_PATH}/events"
DEVICE_ACTIONS_PATH = f"{API_BASE_PATH}/device-actions"
CONTOURS_PATH = f"{API_BASE_PATH}/contours"
CONTOUR_APPLY_PREVIEW_PATH = f"{CONTOURS_PATH}/apply-preview"
CONTOUR_APPLY_PATH = f"{CONTOURS_PATH}/apply"
TEMPORARY_TEMPERATURE_PATH = f"{CONTOURS_PATH}/temporary-temperature"
HOME_CLIMATE_TARGETS_PATH = f"{CONTOURS_PATH}/home-targets"
SCENARIOS_PATH = f"{API_BASE_PATH}/scenarios"
SCENARIOS_CATALOG_PATH = f"{SCENARIOS_PATH}/catalog"
SCENARIOS_TEST_PATH = f"{SCENARIOS_PATH}/test"
SCENARIOS_DELETE_PATH = f"{SCENARIOS_PATH}/delete"
SCENARIOS_RUN_PATH = f"{SCENARIOS_PATH}/run"
SCENARIOS_ACTION_PATH = f"{SCENARIOS_PATH}/action"
VOICE_GREETING_PATH = f"{API_BASE_PATH}/voice/yandex-greeting"
VOICE_GREETING_TEST_PATH = f"{VOICE_GREETING_PATH}/test"
CLIMATE_RUNTIME_PATH = f"{API_BASE_PATH}/climate/runtime"
CLIMATE_ACTION_PATH = f"{API_BASE_PATH}/climate/actions"
CLIMATE_OPERATION_PATH = f"{API_BASE_PATH}/climate/operations/{{operation_id}}"


def api_capabilities_snapshot(
    *, device_actions_available: bool = True,
    voice_available: bool = False,
    voice_stations: tuple[dict[str, object], ...] = (),
    climate_runtime_available: bool = False,
    climate_phase: str = "unavailable",
    climate_commands_enabled: bool = False,
) -> dict[str, object]:
    """Describe only the stable, local, tablet-facing HausmanHub API surface."""

    return {
        "contract": {
            "name": API_CAPABILITIES_CONTRACT_NAME,
            "version": API_CAPABILITIES_CONTRACT_VERSION,
        },
        "api": {
            "major_version": API_MAJOR_VERSION,
            "base_path": API_BASE_PATH,
        },
        "capabilities": {
            "device_actions": {
                "available": device_actions_available,
                "path": DEVICE_ACTIONS_PATH,
                "method": "POST",
                "requires_confirmation": False,
                "receipt_read_back": True,
                "request_contract": {
                    "name": "hausman-hub-device-action-request",
                    "version": 1,
                },
                "response_contract": {
                    "name": "hausman-hub-device-action-receipt",
                    "version": 1,
                },
            },
            "dashboard_snapshot": {
                "available": True,
                "path": DASHBOARD_PATH,
                "method": "GET",
                "response_contract": {
                    "name": "universal-home",
                    "version": 1,
                },
                "read_only": True,
            },
            "climate_home": {
                "available": True,
                "path": HOME_PATH,
                "method": "GET",
                "response_contract": {
                    "name": ANDROID_CLIMATE_CONTRACT_NAME,
                    "version": ANDROID_CLIMATE_CONTRACT_VERSION,
                },
            },
            "automatic_contours": {
                "available": True,
                "path": CONTOURS_PATH,
                "method": "GET",
                "response_contract": {
                    "name": CONTOUR_CONTRACT_NAME,
                    "version": CONTOUR_CONTRACT_VERSION,
                },
            },
            "contour_settings_apply": {
                "available": True,
                "preview_path": CONTOUR_APPLY_PREVIEW_PATH,
                "path": CONTOUR_APPLY_PATH,
                "method": "POST",
                "requires_confirmation": True,
                "request_contract": {
                    "name": CONTOUR_APPLY_REQUEST_CONTRACT_NAME,
                    "version": CONTOUR_APPLY_CONTRACT_VERSION,
                },
                "response_contract": {
                    "name": CLIMATE_CONTROL_RECEIPT_CONTRACT_NAME,
                    "version": CLIMATE_CONTROL_RECEIPT_CONTRACT_VERSION,
                },
            },
            "temporary_room_temperature": {
                "available": True,
                "path": TEMPORARY_TEMPERATURE_PATH,
                "method": "POST",
                "requires_confirmation": True,
                "request_contract": {
                    "name": TEMPORARY_TEMPERATURE_REQUEST_CONTRACT_NAME,
                    "version": TEMPORARY_TEMPERATURE_REQUEST_CONTRACT_VERSION,
                },
                "response_contract": {
                    "name": CLIMATE_CONTROL_RECEIPT_CONTRACT_NAME,
                    "version": CLIMATE_CONTROL_RECEIPT_CONTRACT_VERSION,
                },
            },
            "home_climate_targets": {
                "available": True,
                "path": HOME_CLIMATE_TARGETS_PATH,
                "method": "POST",
                "requires_confirmation": True,
                "request_contract": {
                    "name": "hausman-hub-home-climate-targets-request",
                    "version": 1,
                },
                "response_contract": {
                    "name": CLIMATE_CONTROL_RECEIPT_CONTRACT_NAME,
                    "version": CLIMATE_CONTROL_RECEIPT_CONTRACT_VERSION,
                },
            },
            "climate_runtime": {
                "available": climate_runtime_available,
                "phase": climate_phase,
                "commands_enabled": climate_commands_enabled,
                "read_path": CLIMATE_RUNTIME_PATH,
                "action_path": CLIMATE_ACTION_PATH,
                "operation_path_template": CLIMATE_OPERATION_PATH,
                "read_method": "GET",
                "action_method": "POST",
                "operation_method": "GET",
                "durable_idempotency": True,
                "receipt_events": True,
                "read_contract": {
                    "name": "hausman-hub-climate-runtime",
                    "version": 1,
                },
                "request_contract": {
                    "name": "hausman-hub-climate-action-request",
                    "version": 1,
                },
                "receipt_contract": {
                    "name": "hausman-hub-climate-operation",
                    "version": 1,
                },
            },
            "tablet_profile": {
                "available": True,
                "path": TABLET_PROFILE_PATH,
                "methods": ["GET", "PUT"],
                "optimistic_locking": True,
                "response_contract": {
                    "name": "hausman-hub-tablet-profile",
                    "version": 1,
                },
            },
            "energy_settings": {
                "available": True,
                "path": ENERGY_SETTINGS_PATH,
                "methods": ["GET", "PUT"],
                "optimistic_locking": True,
                "response_contract": {
                    "name": "hausman-hub-energy-settings",
                    "version": 1,
                },
            },
            "energy_history": {
                "available": True,
                "path": ENERGY_HISTORY_PATH,
                "method": "GET",
                "read_only": True,
                "response_contract": {
                    "name": "hausman-hub-energy-history",
                    "version": 1,
                },
            },
            "event_stream": {
                "available": True,
                "path": EVENT_STREAM_PATH,
                "method": "GET",
                "transport": "text/event-stream",
                "response_contract": {
                    "name": "hausman-hub-event",
                    "version": 1,
                },
                "heartbeat_seconds": 30,
            },
            "scenarios": {
                "available": True,
                "read": True,
                "write": True,
                "test": True,
                "delete": True,
                "run": True,
                "definitionVersions": [1],
                "paths": {
                    "list": SCENARIOS_PATH,
                    "catalog": SCENARIOS_CATALOG_PATH,
                    "test": SCENARIOS_TEST_PATH,
                    "delete": SCENARIOS_DELETE_PATH,
                    "run": SCENARIOS_RUN_PATH,
                    "action": SCENARIOS_ACTION_PATH,
                },
            },
            "voice_greeting": {
                "available": voice_available,
                "provider": "alexxit_yandex_station",
                "dialogSupported": voice_available,
                "path": VOICE_GREETING_PATH,
                "methods": ["GET", "PUT"],
                "testPath": VOICE_GREETING_TEST_PATH,
                "optimistic_locking": True,
                "response_contract": {
                    "name": "hausman-hub-voice-greeting-config",
                    "version": 1,
                },
                "stations": list(voice_stations),
            },
        },
    }
