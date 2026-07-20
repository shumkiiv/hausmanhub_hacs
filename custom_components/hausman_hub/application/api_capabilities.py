"""Public discovery contract for the local HausmanHub tablet API."""

from __future__ import annotations

from .android_climate import (
    ANDROID_CLIMATE_CONTRACT_NAME,
    ANDROID_CLIMATE_CONTRACT_VERSION,
)
from .climate_commands import (
    CLIMATE_ACTION_REQUEST_CONTRACT_NAME,
    CLIMATE_ACTION_REQUEST_CONTRACT_VERSION,
)
from .climate_operations import (
    OPERATION_CONTRACT_NAME,
    OPERATION_CONTRACT_VERSION,
    OPERATION_QUERY_CONTRACT_NAME,
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
CONTOURS_PATH = f"{API_BASE_PATH}/contours"
CONTOUR_APPLY_PREVIEW_PATH = f"{CONTOURS_PATH}/apply-preview"
CONTOUR_APPLY_PATH = f"{CONTOURS_PATH}/apply"
TEMPORARY_TEMPERATURE_PATH = f"{CONTOURS_PATH}/temporary-temperature"
ACTION_PATH = f"{API_BASE_PATH}/actions"
OPERATION_PATH = f"{API_BASE_PATH}/operations"


def api_capabilities_snapshot() -> dict[str, object]:
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
            "climate_actions": {
                "available": True,
                "path": ACTION_PATH,
                "method": "POST",
                "request_contract": {
                    "name": CLIMATE_ACTION_REQUEST_CONTRACT_NAME,
                    "version": CLIMATE_ACTION_REQUEST_CONTRACT_VERSION,
                },
                "response_contract": {
                    "name": OPERATION_CONTRACT_NAME,
                    "version": OPERATION_CONTRACT_VERSION,
                },
            },
            "operation_receipts": {
                "available": True,
                "path": OPERATION_PATH,
                "method": "POST",
                "query_contract": {
                    "name": OPERATION_QUERY_CONTRACT_NAME,
                    "version": OPERATION_CONTRACT_VERSION,
                },
                "response_contract": {
                    "name": OPERATION_CONTRACT_NAME,
                    "version": OPERATION_CONTRACT_VERSION,
                },
            },
        },
    }
