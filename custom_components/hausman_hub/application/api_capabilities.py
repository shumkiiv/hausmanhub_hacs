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
from .device_features import (
    DEVICE_FEATURE_MATRIX_CONTRACT_NAME,
    DEVICE_FEATURE_MATRIX_CONTRACT_VERSION,
)
from .event_stream import (
    EVENT_STREAM_HEARTBEAT_SECONDS,
    EVENT_STREAM_QUEUE_SIZE,
    EVENT_STREAM_REPLAY_SIZE,
)
from .voice_greeting import (
    VOICE_GREETING_CONTRACT_NAME,
    VOICE_GREETING_CONTRACT_VERSION,
    VOICE_RECEIPT_CONTRACT_NAME,
    VOICE_RECEIPT_CONTRACT_VERSION,
    VOICE_TEST_REQUEST_CONTRACT_NAME,
    VOICE_TEST_REQUEST_CONTRACT_VERSION,
)

API_CAPABILITIES_CONTRACT_NAME = "hausman-hub-capabilities"
API_CAPABILITIES_CONTRACT_VERSION = 1
API_MAJOR_VERSION = 1
API_BASE_PATH = "/api/hausman_hub/v1"

CAPABILITIES_PATH = f"{API_BASE_PATH}/capabilities"
HOME_PATH = f"{API_BASE_PATH}/home"
DASHBOARD_PATH = f"{API_BASE_PATH}/dashboard"
TABLET_PROFILE_PATH = f"{API_BASE_PATH}/tablet-profile"
TABLET_POWER_PATH = f"{API_BASE_PATH}/tablet-power-status"
ROOM_SETTINGS_PATH = f"{API_BASE_PATH}/room-settings"
ENERGY_SETTINGS_PATH = f"{API_BASE_PATH}/energy-settings"
ENERGY_HISTORY_PATH = f"{API_BASE_PATH}/energy/history"
ENERGY_METER_PATH = f"{API_BASE_PATH}/energy/meter"
ENERGY_METERS_PATH = f"{API_BASE_PATH}/energy/meters"
DEVICE_DISCOVERY_PATH = f"{API_BASE_PATH}/device-discovery"
EVENT_STREAM_PATH = f"{API_BASE_PATH}/events"
DEVICE_ACTIONS_PATH = f"{API_BASE_PATH}/device-actions"
DEVICE_ACTIONS_BATCH_PATH = f"{DEVICE_ACTIONS_PATH}/batch"
DEVICE_FEATURES_PATH = f"{API_BASE_PATH}/device-features"
CONTOURS_PATH = f"{API_BASE_PATH}/contours"
CONTOUR_APPLY_PREVIEW_PATH = f"{CONTOURS_PATH}/apply-preview"
CONTOUR_APPLY_PATH = f"{CONTOURS_PATH}/apply"
TEMPORARY_TEMPERATURE_PATH = f"{CONTOURS_PATH}/temporary-temperature"
HOME_CLIMATE_TARGETS_PATH = f"{CONTOURS_PATH}/home-targets"
SCENARIOS_PATH = f"{API_BASE_PATH}/scenarios"
SCENARIOS_CATALOG_PATH = f"{SCENARIOS_PATH}/catalog"
SCENARIOS_HEALTH_PATH = f"{SCENARIOS_PATH}/health"
SCENARIOS_NODE_RED_PATH = f"{SCENARIOS_PATH}/node-red"
SCENARIOS_NODE_RED_SOURCE_PATH = f"{SCENARIOS_NODE_RED_PATH}/source/{{scenario_id}}"
SCENARIOS_TEST_PATH = f"{SCENARIOS_PATH}/test"
SCENARIOS_DELETE_PATH = f"{SCENARIOS_PATH}/delete"
SCENARIOS_RUN_PATH = f"{SCENARIOS_PATH}/run"
SCENARIOS_ACTION_PATH = f"{SCENARIOS_PATH}/action"
SCENARIOS_AI_DRAFT_PATH = f"{SCENARIOS_PATH}/ai-draft"
SCENARIOS_UPCOMING_PATH = f"{SCENARIOS_PATH}/upcoming"
SCENARIOS_UPCOMING_CANCEL_PATH = f"{SCENARIOS_UPCOMING_PATH}/cancel"
VOICE_GREETING_PATH = f"{API_BASE_PATH}/voice/yandex-greeting"
VOICE_GREETING_TEST_PATH = f"{VOICE_GREETING_PATH}/test"
CLIMATE_RUNTIME_PATH = f"{API_BASE_PATH}/climate/runtime"
CLIMATE_ACTION_PATH = f"{API_BASE_PATH}/climate/actions"
CLIMATE_OPERATION_PATH = f"{API_BASE_PATH}/climate/operations/{{operation_id}}"
CLIMATE_CONTROL_OPERATION_PATH = f"{API_BASE_PATH}/climate/control/operations/{{operation_id}}"
CLIMATE_RECOVERY_PATH = f"{API_BASE_PATH}/climate/recovery/rooms/{{room_id}}"
CLIMATE_RECOVERY_PREFLIGHT_PATH = f"{CLIMATE_RECOVERY_PATH}/preflight"
CLIMATE_RECOVERY_OPERATION_PATH = f"{API_BASE_PATH}/climate/recovery/operations/{{operation_id}}"


def api_capabilities_snapshot(
    *, device_actions_available: bool = True,
    voice_available: bool = False,
    voice_stations: tuple[dict[str, object], ...] = (),
    climate_runtime_available: bool = False,
    climate_phase: str = "unavailable",
    climate_commands_enabled: bool = False,
    climate_reliability_available: bool = False,
    climate_recovery_available: bool = False,
    scenario_ai_available: bool = False,
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
                "fullProtocol": {
                    "fullAvailable": True,
                    "singleRequestMediaType": "application/vnd.hausmanhub.device-action-request.full+json",
                    "singleResponseMediaType": "application/vnd.hausmanhub.device-action-receipt.full+json",
                    "batchRequestMediaType": "application/vnd.hausmanhub.device-action-batch-request.full+json",
                    "batchResponseMediaType": "application/vnd.hausmanhub.device-action-batch-receipt.full+json",
                    "evidenceContractVersion": "1",
                    "dangerousPolicyVersion": "1",
                },
                "feature_matrix_path": DEVICE_FEATURES_PATH,
                "feature_matrix_method": "GET",
                "feature_matrix_contract": {
                    "name": DEVICE_FEATURE_MATRIX_CONTRACT_NAME,
                    "version": DEVICE_FEATURE_MATRIX_CONTRACT_VERSION,
                },
                "batch_path": DEVICE_ACTIONS_BATCH_PATH,
                "batch_method": "POST",
                "batch_request_contract": {
                    "name": "hausman-hub-device-action-batch-request",
                    "version": 1,
                },
                "batch_response_contract": {
                    "name": "hausman-hub-device-action-batch-receipt",
                    "version": 1,
                },
                "intercom_safety": {
                    "confirmation_field": "confirmedByUser",
                    "dry_run_field": "dryRun",
                    "default_hold_seconds": 15,
                    "maximum_hold_seconds": 20,
                    "release_receipt_contract": {
                        "name": "hausman-hub-intercom-release-receipt",
                        "version": 1,
                    },
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
            "climate_room_recovery_v1": {
                # v1 recovery remains discoverable but terminal/read-only.
                # Negotiated physical recovery moves to the v2 capability.
                "available": False,
                "path": CLIMATE_RECOVERY_PATH,
                "method": "POST",
                "operation_path_template": CLIMATE_RECOVERY_OPERATION_PATH,
                "operation_method": "GET",
                "request_contract": {"name": "hausman-hub-climate-room-recovery-request", "version": 1},
                "receipt_contract": {"name": "hausman-hub-climate-room-recovery-receipt", "version": 1},
                "durable_idempotency": True,
                "receipt_polling": True,
            },
            "climate_room_recovery_v2": {
                # The read-only preflight endpoint is deliberately visible
                # before its durable POST ledger is ready.  Availability is
                # the authority to dispatch, never merely route existence.
                "available": climate_recovery_available,
                "path": CLIMATE_RECOVERY_PATH,
                "method": "POST",
                "operation_path_template": CLIMATE_RECOVERY_OPERATION_PATH,
                "operation_method": "GET",
                "request_contract": {"name": "hausman-hub-climate-room-recovery-request-v2", "version": 2},
                "receipt_contract": {"name": "hausman-hub-climate-room-recovery-receipt-v2", "version": 2},
                "durable_idempotency": True,
                "receipt_polling": True,
                "preflight_path": CLIMATE_RECOVERY_PREFLIGHT_PATH,
                "preflight_method": "GET",
                "preflight_contract": {"name": "hausman-hub-climate-room-recovery-v2-preflight", "version": 2},
            },
            "climate_reliability_v1": {
                "available": False,
                "producer_guarantees_full_branch": False,
                "runtime_path": CLIMATE_RUNTIME_PATH,
                "runtime_method": "GET",
                "runtime_contract": {"name": "hausman-hub-climate-runtime", "version": 1},
                "action_path": CLIMATE_ACTION_PATH,
                "action_method": "POST",
                "operation_path_template": CLIMATE_OPERATION_PATH,
                "operation_method": "GET",
                "operation_receipt_contract": {"name": "hausman-hub-climate-operation", "version": 1},
                "control_paths": [CONTOUR_APPLY_PATH, TEMPORARY_TEMPERATURE_PATH],
                "control_method": "POST",
                "control_operation_path_template": CLIMATE_CONTROL_OPERATION_PATH,
                "control_operation_method": "GET",
                "control_receipt_contract": {"name": CLIMATE_CONTROL_RECEIPT_CONTRACT_NAME, "version": 1},
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
            "tablet_power": {
                "available": True,
                "path": TABLET_POWER_PATH,
                "method": "POST",
                "request_contract": {
                    "name": "hausman-hub-tablet-power-status-request",
                    "version": 1,
                },
                "response_contract": {
                    "name": "hausman-hub-tablet-power-status-receipt",
                    "version": 1,
                },
            },
            "room_settings": {
                "available": True,
                "path": ROOM_SETTINGS_PATH,
                "methods": ["GET", "PUT"],
                "optimistic_locking": True,
                "response_contract": {
                    "name": "hausman-hub-room-settings",
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
            "energy_meter": {
                "available": True,
                "path": ENERGY_METER_PATH,
                "methods": ["GET", "POST"],
                "optimistic_locking": True,
                "response_contract": {
                    "name": "hausman-hub-energy-meter",
                    "version": 1,
                },
                "collection_path": ENERGY_METERS_PATH,
                "collection_methods": ["GET", "POST"],
                "collection_response_contract": {
                    "name": "hausman-hub-energy-meters",
                    "version": 1,
                },
            },
            "device_discovery": {
                "available": True,
                "path": DEVICE_DISCOVERY_PATH,
                "methods": ["GET", "POST"],
                "optimistic_locking": True,
                "response_contract": {
                    "name": "hausman-hub-device-discovery",
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
                "heartbeat_seconds": EVENT_STREAM_HEARTBEAT_SECONDS,
                "resume": {
                    "strategy": "last_event_id",
                    "request_header": "Last-Event-ID",
                    "max_events": EVENT_STREAM_REPLAY_SIZE,
                    "delivery_queue_limit": EVENT_STREAM_QUEUE_SIZE,
                    "survives_restart": False,
                },
            },
            "scenarios": {
                "available": True,
                "read": True,
                "write": True,
                "test": True,
                "delete": True,
                "run": True,
                "aiDraft": {
                    "available": scenario_ai_available,
                    "path": SCENARIOS_AI_DRAFT_PATH,
                    "method": "POST",
                    "request_contract": {
                        "name": "hausman-hub-scenario-ai-draft-request",
                        "version": 1,
                    },
                    "response_contract": {
                        "name": "hausman-hub-scenario-ai-draft",
                        "version": 1,
                    },
                    "saves": False,
                    "sends_commands": False,
                },
                "upcoming": True,
                "definitionVersions": [1],
                "paths": {
                    "list": SCENARIOS_PATH,
                    "catalog": SCENARIOS_CATALOG_PATH,
                    "health": SCENARIOS_HEALTH_PATH,
                    "nodeRedStatus": SCENARIOS_NODE_RED_PATH,
                    "nodeRedSourceTemplate": SCENARIOS_NODE_RED_SOURCE_PATH,
                    "test": SCENARIOS_TEST_PATH,
                    "delete": SCENARIOS_DELETE_PATH,
                    "run": SCENARIOS_RUN_PATH,
                    "action": SCENARIOS_ACTION_PATH,
                    "aiDraft": SCENARIOS_AI_DRAFT_PATH,
                    "upcoming": SCENARIOS_UPCOMING_PATH,
                    "upcomingCancel": SCENARIOS_UPCOMING_CANCEL_PATH,
                },
                "nodeRed": {
                    "available": True,
                    "statusPath": SCENARIOS_NODE_RED_PATH,
                    "statusMethod": "GET",
                    "sourcePathTemplate": SCENARIOS_NODE_RED_SOURCE_PATH,
                    "sourceReadMethod": "GET",
                    "sourceWriteMethod": "PUT",
                    "source_contract": {
                        "name": "hausman-hub-scenario-node-red-source",
                        "version": 1,
                    },
                    "source_update_request_contract": {
                        "name": "hausman-hub-scenario-node-red-source-update-request",
                        "version": 1,
                    },
                    "source_update_receipt_contract": {
                        "name": "hausman-hub-scenario-node-red-source-update-receipt",
                        "version": 1,
                    },
                    "maxSourceBytes": 65_536,
                    "optimisticLocking": True,
                    "dryRunBeforeSave": True,
                    "executionBackends": ["hausman", "node_red"],
                    "physicalCommandsOwnedByHausman": True,
                    "managedFlowStyle": "function",
                },
            },
            "voice_greeting": {
                "available": voice_available,
                "provider": "alexxit_yandex_station",
                "dialogSupported": voice_available,
                "path": VOICE_GREETING_PATH,
                "methods": ["GET", "PUT"],
                "testPath": VOICE_GREETING_TEST_PATH,
                "testMethod": "POST",
                "optimistic_locking": True,
                "response_contract": {
                    "name": VOICE_GREETING_CONTRACT_NAME,
                    "version": VOICE_GREETING_CONTRACT_VERSION,
                },
                "test_request_contract": {
                    "name": VOICE_TEST_REQUEST_CONTRACT_NAME,
                    "version": VOICE_TEST_REQUEST_CONTRACT_VERSION,
                },
                "test_receipt_contract": {
                    "name": VOICE_RECEIPT_CONTRACT_NAME,
                    "version": VOICE_RECEIPT_CONTRACT_VERSION,
                },
                "stations": list(voice_stations),
            },
        },
    }
