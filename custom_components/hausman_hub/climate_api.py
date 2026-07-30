"""Authenticated local HTTP facade for tablet and climate administration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from http import HTTPStatus
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address
import json
import pathlib
from typing import TYPE_CHECKING, Any, Final

from homeassistant.components.http import HomeAssistantView
from homeassistant.util import dt as dt_util

from .application.api_capabilities import (
    CAPABILITIES_PATH,
    CONTOURS_PATH,
    CONTOUR_APPLY_PATH,
    CONTOUR_APPLY_PREVIEW_PATH,
    DASHBOARD_PATH,
    HOME_PATH,
    HOME_CLIMATE_TARGETS_PATH,
    TEMPORARY_TEMPERATURE_PATH,
    api_capabilities_snapshot,
)
from .application.ai_assistant import AiAssistantService
from .application.ai_assistant_config import (
    AiAssistantBinding,
    ai_assistant_binding_from_entry_data,
    ai_assistant_binding_update,
    ai_assistant_entry_data,
    ai_assistant_public_settings,
)
from .application.ai_assistant_storage import ai_assistant_state_to_payload
from .application.configuration import (
    CONNECTION_MODE_DEFAULT,
    CONNECTION_MODE_FIELD,
    HOME_ASSISTANT_URL_FIELD,
    SMART_HOME_CENTER_URL_FIELD,
    create_options,
    effective_configuration,
)
from .application.climate_signal_settings import (
    CENTRAL_HEATING_SIGNAL,
    OUTDOOR_TEMPERATURE_SIGNAL,
    PRESENCE_SIGNAL,
    ROOM_PRESENCE_SIGNAL,
    WINDOW_SIGNAL,
    ClimateSignalSettingsViolation,
    validate_climate_mode_update,
    validate_home_environment_update,
    validate_room_signal_update,
    validate_room_signal_updates,
    validate_room_window_update,
)
from .application.climate_comparison import climate_comparison_to_payload
from .application.climate_shadow_window import ClimateShadowWindowService
from .application.climate_area_assignment import ClimateAreaAssignmentViolation
from .application.climate_device_bindings import ClimateDeviceBindingViolation
from .application.contour_apply import ContourApplyViolation
from .application.contour_override import TemporaryTemperatureViolation
from .application.home_climate_targets import HomeClimateTargetsViolation
from .application.legacy_settings_import import (
    LegacySettingsImportViolation,
    preview_legacy_settings,
)
from .application.legacy_settings_apply import LegacySettingsApplyViolation
from .application.climate_registry import ClimateRegistryViolation
from .application.climate_runtime import (
    ClimateRuntime,
    ClimateRuntimeUnavailable,
    ClimateSnapshotUnavailable,
)
from .application.climate_setup import ClimateSetupViolation
from .domain.ai_assistant import AiAdvisoryStatus, AiAssistantViolation
from .domain.hub_settings import HausmanHubSettings
from .domain.hub_settings import HausmanHubSettingsViolation
from .dashboard_ha_snapshot import async_dashboard_snapshot

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .application.scenario_service import ScenarioService


DOMAIN = "hausman_hub"
DATA_CLIMATE_RUNTIME = "climate_runtime"
DATA_CLIMATE_VIEWS = "climate_views"
DATA_AI_ASSISTANT = "ai_assistant"
DATA_CLIMATE_SHADOW = "climate_shadow"


def _integration_version() -> str | None:
    """Read the installed integration version from its own manifest."""

    try:
        manifest = json.loads(
            (pathlib.Path(__file__).resolve().parent / "manifest.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, ValueError):
        return None
    version = manifest.get("version")
    return version if type(version) is str and version else None


ADMIN_IMPORT_PATH = "/api/hausman_hub/v1/admin/climate-import"
ADMIN_LEGACY_SETTINGS_PREVIEW_PATH = (
    "/api/hausman_hub/v1/admin/legacy-settings/preview"
)
ADMIN_LEGACY_SETTINGS_APPLY_PATH = "/api/hausman_hub/v1/admin/legacy-settings/apply"
ADMIN_DRAFT_PATH = "/api/hausman_hub/v1/admin/climate-drafts"
ADMIN_DRAFT_CURRENT_PATH = "/api/hausman_hub/v1/admin/climate-drafts/current"
ADMIN_DRAFT_VALIDATION_PATH = "/api/hausman_hub/v1/admin/climate-drafts/validate"
ADMIN_DRAFT_SAVE_PATH = "/api/hausman_hub/v1/admin/climate-drafts/save"
ADMIN_DEVICE_AREA_ASSIGNMENTS_PATH = (
    "/api/hausman_hub/v1/admin/device-area-assignments"
)
ADMIN_DEVICE_BINDINGS_PATH = "/api/hausman_hub/v1/admin/climate-device-bindings"
ADMIN_DEVICE_BINDINGS_PREVIEW_PATH = f"{ADMIN_DEVICE_BINDINGS_PATH}/preview"
ADMIN_PROFILE_UPDATE_PATH = "/api/hausman_hub/v1/admin/climate-profiles"
ADMIN_SCHEDULE_UPDATE_PATH = "/api/hausman_hub/v1/admin/climate-schedule"
ADMIN_REGISTRY_PATH = "/api/hausman_hub/v1/admin/climate-registry"
ADMIN_REGISTRY_PREVIEW_PATH = "/api/hausman_hub/v1/admin/climate-registry-preview"
ADMIN_READINESS_PATH = "/api/hausman_hub/v1/admin/climate-readiness"
ADMIN_SHADOW_COMPARISON_PATH = (
    "/api/hausman_hub/v1/admin/climate-shadow-comparison"
)
ADMIN_SHADOW_WINDOW_PATH = "/api/hausman_hub/v1/admin/climate-shadow-window"
ADMIN_CANARY_PREFLIGHT_PATH = "/api/hausman_hub/v1/admin/climate-canary-preflight"
ADMIN_PANEL_PATH = "/api/hausman_hub/v1/admin/panel"
ADMIN_PANEL_APPLY_PATH = "/api/hausman_hub/v1/admin/panel/apply"
ADMIN_PANEL_TEMPORARY_PATH = "/api/hausman_hub/v1/admin/panel/temporary-temperature"
ADMIN_CLIMATE_MODE_PATH = "/api/hausman_hub/v1/admin/climate-mode"
ADMIN_HOME_ENVIRONMENT_PATH = "/api/hausman_hub/v1/admin/home-environment"
ADMIN_ROOM_SIGNALS_PATH = "/api/hausman_hub/v1/admin/climate-room-signals"
ADMIN_AI_ASSISTANT_PATH = "/api/hausman_hub/v1/admin/ai-assistant"
ADMIN_AI_ASSISTANT_SETTINGS_PATH = f"{ADMIN_AI_ASSISTANT_PATH}/settings"
ADMIN_AI_ASSISTANT_REFRESH_PATH = f"{ADMIN_AI_ASSISTANT_PATH}/refresh"
ADMIN_CONNECTION_SETTINGS_PATH = "/api/hausman_hub/v1/admin/connection-settings"
ADMIN_ENERGY_SETTINGS_PATH = "/api/hausman_hub/v1/admin/energy-settings"
ADMIN_RESET_PATH = "/api/hausman_hub/v1/admin/reset"
NO_STORE_HEADERS = {"Cache-Control": "no-store"}
MAX_ACTION_BODY_BYTES = 16 * 1024
MAX_CLIMATE_SETUP_BODY_BYTES = 256 * 1024
_DRAFT_CONFLICT_CODES = frozenset(
    {"snapshot_changed", "setup_changed", "data_stale"}
)
TABLET_GROUP_ID = "system-users"
HOME_IPV4_NETWORKS: Final[tuple[IPv4Network, ...]] = (
    IPv4Network("10.0.0.0/8"),
    IPv4Network("172.16.0.0/12"),
    IPv4Network("192.168.0.0/16"),
)
HOME_IPV6_NETWORK: Final[IPv6Network] = IPv6Network("fc00::/7")


def register_climate_api(
    hass: HomeAssistant,
    runtime: ClimateRuntime,
    ai_assistant: AiAssistantService | None = None,
    scenario_service: ScenarioService | None = None,
    ir_code_service: object | None = None,
    climate_shadow: ClimateShadowWindowService | None = None,
) -> None:
    """Register fixed routes once and point them at the loaded HausmanHub runtime."""

    data = hass.data.setdefault(DOMAIN, {})
    data[DATA_CLIMATE_RUNTIME] = runtime
    if ai_assistant is not None:
        data[DATA_AI_ASSISTANT] = ai_assistant
    if scenario_service is not None:
        data["scenario_service"] = scenario_service
    if ir_code_service is not None:
        data["ir_code_service"] = ir_code_service
    if climate_shadow is not None:
        data[DATA_CLIMATE_SHADOW] = climate_shadow
    if DATA_CLIMATE_VIEWS not in data:
        views = [
            ClimateCapabilitiesView(hass),
            DashboardView(hass),
            ClimateHomeView(hass),
            ContoursView(hass),
            ContourApplyPreviewView(hass),
            ContourApplyView(hass),
            TemporaryTemperatureView(hass),
            HomeClimateTargetsView(hass),
            ClimateAdminImportView(hass),
            LegacySettingsPreviewView(hass),
            LegacySettingsApplyView(hass),
            ClimateAdminDraftView(hass),
            ClimateAdminDraftCurrentView(hass),
            ClimateAdminDraftValidationView(hass),
            ClimateAdminDraftSaveView(hass),
            ClimateAdminDeviceAreaAssignmentsView(hass),
            ClimateAdminDeviceBindingsView(hass),
            ClimateAdminDeviceBindingsPreviewView(hass),
            ClimateAdminProfileUpdateView(hass),
            ClimateAdminScheduleUpdateView(hass),
            ClimateAdminRegistryView(hass),
            ClimateAdminRegistryPreviewView(hass),
            ClimateAdminReadinessView(hass),
            ClimateAdminShadowComparisonView(hass),
            ClimateAdminShadowWindowView(hass),
            ClimateAdminPanelView(hass),
            ClimateAdminPanelApplyView(hass),
            ClimateAdminPanelTemporaryView(hass),
            ClimateAdminClimateModeView(hass),
            ClimateAdminHomeEnvironmentView(hass),
            ClimateAdminRoomSignalsView(hass),
            ClimateAdminAiAssistantView(hass),
            ClimateAdminAiAssistantSettingsView(hass),
            ClimateAdminAiAssistantRefreshView(hass),
            ClimateAdminConnectionSettingsView(hass),
            ClimateAdminEnergySettingsView(hass),
            ClimateAdminResetView(hass),
        ]
        if scenario_service is not None:
            from .scenario_api import scenario_api_views
            from .device_action_api import DeviceActionView

            views.extend(scenario_api_views(hass, scenario_service))
            views.append(DeviceActionView(hass))
        if ir_code_service is not None:
            from .ir_code_api import ir_code_api_views

            views.extend(ir_code_api_views(hass, ir_code_service))
        for view in views:
            hass.http.register_view(view)
        data[DATA_CLIMATE_VIEWS] = views


def clear_climate_api(hass: HomeAssistant, entry_id: str) -> None:
    """Revoke every climate route while retaining one non-duplicated view set."""

    data = hass.data.get(DOMAIN)
    if data is None:
        return
    runtime = data.get(DATA_CLIMATE_RUNTIME)
    if runtime is not None and runtime.entry_id == entry_id:
        data.pop(DATA_CLIMATE_RUNTIME, None)
        data.pop(DATA_AI_ASSISTANT, None)
        data.pop("scenario_service", None)
        data.pop("ir_code_service", None)
        data.pop("settings_service", None)
        data.pop(DATA_CLIMATE_SHADOW, None)


class _ClimateView(HomeAssistantView):
    requires_auth = True
    cors_allowed = False
    extra_urls: tuple[str, ...] = ()

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    def _runtime(self) -> ClimateRuntime | None:
        data = self._hass.data.get(DOMAIN)
        if data is None:
            return None
        runtime = data.get(DATA_CLIMATE_RUNTIME)
        if not isinstance(runtime, ClimateRuntime):
            return None
        entries = self._hass.config_entries.async_entries(DOMAIN)
        if len(entries) != 1 or entries[0].entry_id != runtime.entry_id:
            return None
        loaded = self._hass.config_entries.async_loaded_entries(DOMAIN)
        if not any(entry.entry_id == runtime.entry_id for entry in loaded):
            return None
        return runtime

    def _unavailable(self) -> Any:
        return self.json_message(
            "The HausmanHub climate API is unavailable.",
            HTTPStatus.SERVICE_UNAVAILABLE,
            headers=NO_STORE_HEADERS,
        )

    def _ai_assistant(self) -> AiAssistantService | None:
        if self._runtime() is None:
            return None
        candidate = self._hass.data.get(DOMAIN, {}).get(DATA_AI_ASSISTANT)
        return candidate if isinstance(candidate, AiAssistantService) else None

    def _climate_shadow(self) -> ClimateShadowWindowService | None:
        if self._runtime() is None:
            return None
        candidate = self._hass.data.get(DOMAIN, {}).get(DATA_CLIMATE_SHADOW)
        return (
            candidate
            if isinstance(candidate, ClimateShadowWindowService)
            else None
        )


class ClimateCapabilitiesView(_ClimateView):
    """Advertise only installed, stable HausmanHub tablet API capabilities."""

    url = CAPABILITIES_PATH
    name = "api:hausman_hub:capabilities"

    async def get(self, request: Any) -> Any:
        if not _is_exact_request(request, CAPABILITIES_PATH):
            return _not_found(self)
        if not _is_local_tablet_request(request):
            return _forbidden(self)
        if self._runtime() is None:
            return self._unavailable()
        data = self._hass.data.get(DOMAIN, {})
        return self.json(
            api_capabilities_snapshot(
                device_actions_available=data.get("scenario_service") is not None
            ),
            headers=NO_STORE_HEADERS,
        )


class ClimateHomeView(_ClimateView):
    """Serve the private-id-free state contract to one local tablet user."""

    url = HOME_PATH
    name = "api:hausman_hub:climate_home"

    async def get(self, request: Any) -> Any:
        if not _is_exact_request(request, HOME_PATH):
            return _not_found(self)
        if not _is_local_tablet_request(request):
            return _forbidden(self)
        runtime = self._runtime()
        if runtime is None:
            return self._unavailable()
        try:
            payload = await runtime.async_public_snapshot()
        except Exception:
            return self._unavailable()
        return self.json(payload, headers=NO_STORE_HEADERS)


class DashboardView(_ClimateView):
    """Serve one read-only, physical-device-grouped home snapshot."""

    url = DASHBOARD_PATH
    name = "api:hausman_hub:dashboard"

    async def get(self, request: Any) -> Any:
        if not _is_exact_request(request, DASHBOARD_PATH):
            return _not_found(self)
        if not _is_local_tablet_request(request):
            return _forbidden(self)
        if self._runtime() is None:
            return self._unavailable()
        data = self._hass.data.get(DOMAIN, {})
        scenario_service = data.get("scenario_service")
        settings_service = data.get("settings_service")
        try:
            payload = await async_dashboard_snapshot(
                self._hass,
                scenario_service if scenario_service is not None else None,
                settings_service.current if settings_service is not None else None,
            )
        except Exception:
            return self._unavailable()
        return self.json(payload, headers=NO_STORE_HEADERS)


class ContoursView(_ClimateView):
    """Serve public automatic-contour status to the local tablet."""

    url = CONTOURS_PATH
    name = "api:hausman_hub:contours"

    async def get(self, request: Any) -> Any:
        if not _is_exact_request(request, CONTOURS_PATH):
            return _not_found(self)
        if not _is_local_tablet_request(request):
            return _forbidden(self)
        runtime = self._runtime()
        if runtime is None:
            return self._unavailable()
        try:
            payload = await runtime.async_contours_snapshot()
        except Exception:
            return self._unavailable()
        return self.json(payload, headers=NO_STORE_HEADERS)


class ContourApplyPreviewView(_ClimateView):
    """Describe exact saved-contour changes before tablet confirmation."""

    url = CONTOUR_APPLY_PREVIEW_PATH
    name = "api:hausman_hub:contour_apply_preview"

    async def get(self, request: Any) -> Any:
        if not _is_exact_request(request, CONTOUR_APPLY_PREVIEW_PATH):
            return _not_found(self)
        if not _is_local_tablet_request(request):
            return _forbidden(self)
        runtime = self._runtime()
        if runtime is None:
            return self._unavailable()
        try:
            payload = await runtime.async_contour_apply_preview()
        except ContourApplyViolation:
            return self.json_message(
                "The climate contour cannot be applied.",
                HTTPStatus.CONFLICT,
                headers=NO_STORE_HEADERS,
            )
        except Exception:
            return self._unavailable()
        return self.json(payload, headers=NO_STORE_HEADERS)


class ContourApplyView(_ClimateView):
    """Apply only saved contour settings after explicit tablet confirmation."""

    url = CONTOUR_APPLY_PATH
    name = "api:hausman_hub:contour_apply"

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, CONTOUR_APPLY_PATH):
            return _not_found(self)
        if not _is_local_tablet_request(request):
            return _forbidden(self)
        runtime = self._runtime()
        if runtime is None:
            return self._unavailable()
        try:
            payload = await _request_json(request)
            receipt = await runtime.async_apply_contour(payload)
        except ContourApplyViolation:
            return self.json_message(
                "The climate contour application is invalid.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        except ClimateRuntimeUnavailable:
            return self._unavailable()
        except Exception:
            return self._unavailable()
        return self.json(receipt.as_payload(), headers=NO_STORE_HEADERS)


class TemporaryTemperatureView(_ClimateView):
    """Set or clear one room temperature until the next schedule boundary."""

    url = TEMPORARY_TEMPERATURE_PATH
    name = "api:hausman_hub:temporary_temperature"

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, TEMPORARY_TEMPERATURE_PATH):
            return _not_found(self)
        if not _is_local_tablet_request(request):
            return _forbidden(self)
        runtime = self._runtime()
        if runtime is None:
            return self._unavailable()
        try:
            payload = await _request_json(request)
            receipt = await runtime.async_temporary_temperature(
                payload,
                dt_util.now(),
            )
        except TemporaryTemperatureViolation:
            return self.json_message(
                "The temporary climate temperature request is invalid.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        except ContourApplyViolation:
            return self.json_message(
                "The temporary climate temperature is not ready.",
                HTTPStatus.CONFLICT,
                headers=NO_STORE_HEADERS,
            )
        except ClimateRuntimeUnavailable:
            return self._unavailable()
        except Exception:
            return self._unavailable()
        return self.json(receipt.as_payload(), headers=NO_STORE_HEADERS)


class HomeClimateTargetsView(_ClimateView):
    """Set a common target for every room and apply it as one operation."""

    url = HOME_CLIMATE_TARGETS_PATH
    name = "api:hausman_hub:home_climate_targets"

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, HOME_CLIMATE_TARGETS_PATH):
            return _not_found(self)
        if not _is_local_tablet_request(request):
            return _forbidden(self)
        runtime = self._runtime()
        if runtime is None:
            return self._unavailable()
        try:
            payload = await _request_json(request)
            receipt = await runtime.async_home_climate_targets(payload)
        except HomeClimateTargetsViolation:
            return self.json_message(
                "The home climate target request is invalid.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        except (ContourApplyViolation, ClimateRuntimeUnavailable):
            return self._unavailable()
        except Exception:
            return self._unavailable()
        return self.json(receipt.as_payload(), headers=NO_STORE_HEADERS)


class ClimateAdminImportView(_ClimateView):
    """Expose private import candidates only to a local administrator."""

    url = ADMIN_IMPORT_PATH
    name = "api:hausman_hub:climate_admin_import"

    async def get(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_IMPORT_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        runtime = self._runtime()
        if runtime is None:
            return self._unavailable()
        try:
            payload = await runtime.async_admin_import_snapshot()
        except Exception:
            return self._unavailable()
        return self.json(payload, headers=NO_STORE_HEADERS)


class LegacySettingsPreviewView(_ClimateView):
    """Preview one explicit Node-RED settings export without persistence."""

    url = ADMIN_LEGACY_SETTINGS_PREVIEW_PATH
    name = "api:hausman_hub:legacy_settings_preview"

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_LEGACY_SETTINGS_PREVIEW_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        try:
            payload = await _request_json(
                request,
                maximum_bytes=MAX_CLIMATE_SETUP_BODY_BYTES,
            )
            result = preview_legacy_settings(payload)
        except (LegacySettingsImportViolation, ValueError):
            return self.json_message(
                "Экспорт настроек Node-RED заполнен неверно.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        return self.json(result, headers=NO_STORE_HEADERS)


class LegacySettingsApplyView(_ClimateView):
    """Apply one unchanged preview to native stores without device commands."""

    url = ADMIN_LEGACY_SETTINGS_APPLY_PATH
    name = "api:hausman_hub:legacy_settings_apply"

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_LEGACY_SETTINGS_APPLY_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        runtime = self._runtime()
        data = self._hass.data.get(DOMAIN, {})
        settings_service = data.get("settings_service")
        if runtime is None or settings_service is None:
            return self._unavailable()
        try:
            payload = await _request_json(
                request,
                maximum_bytes=MAX_CLIMATE_SETUP_BODY_BYTES,
            )
            result = await runtime.async_apply_legacy_settings(
                payload,
                settings_service,
            )
        except LegacySettingsApplyViolation as error:
            status = (
                HTTPStatus.CONFLICT
                if error.code in {"preview_changed", "climate_not_configured"}
                else HTTPStatus.BAD_REQUEST
            )
            return self.json_message(
                "Не удалось применить экспорт настроек Node-RED.",
                status,
                headers=NO_STORE_HEADERS,
            )
        except ClimateRuntimeUnavailable:
            return self._unavailable()
        except ValueError:
            return self.json_message(
                "Экспорт настроек Node-RED заполнен неверно.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        except Exception:
            return self._unavailable()
        return self.json(result, headers=NO_STORE_HEADERS)


class ClimateAdminDraftView(_ClimateView):
    """Create an unsaved climate contour draft for one local administrator."""

    url = ADMIN_DRAFT_PATH
    name = "api:hausman_hub:climate_admin_drafts"

    async def get(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_DRAFT_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        runtime = self._runtime()
        if runtime is None:
            return self._unavailable()
        try:
            result = await runtime.async_climate_setup_options()
        except ClimateRuntimeUnavailable:
            return self._unavailable()
        except Exception:
            return self._unavailable()
        return self.json(result, headers=NO_STORE_HEADERS)


    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_DRAFT_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        runtime = self._runtime()
        if runtime is None:
            return self._unavailable()
        try:
            payload = await _request_json(
                request,
                maximum_bytes=MAX_CLIMATE_SETUP_BODY_BYTES,
            )
            result = await runtime.async_create_contour_draft(payload)
        except ClimateSetupViolation as error:
            status = (
                HTTPStatus.CONFLICT
                if error.code in _DRAFT_CONFLICT_CODES
                else HTTPStatus.BAD_REQUEST
            )
            return self.json_message(
                "Не удалось создать черновик климатического контура.",
                status,
                headers=NO_STORE_HEADERS,
            )
        except ValueError:
            return self.json_message(
                "Запрос черновика климатического контура заполнен неверно.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        except ClimateRuntimeUnavailable:
            return self._unavailable()
        except Exception:
            return self._unavailable()
        return self.json(result, headers=NO_STORE_HEADERS)


class ClimateAdminDeviceAreaAssignmentsView(_ClimateView):
    """Persist one explicit batch of room assignments in Home Assistant."""

    url = ADMIN_DEVICE_AREA_ASSIGNMENTS_PATH
    name = "api:hausman_hub:climate_admin_device_area_assignments"

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_DEVICE_AREA_ASSIGNMENTS_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        runtime = self._runtime()
        if runtime is None:
            return self._unavailable()
        try:
            payload = await _request_json(
                request,
                maximum_bytes=MAX_CLIMATE_SETUP_BODY_BYTES,
            )
            result = await runtime.async_assign_home_assistant_areas(payload)
        except ClimateAreaAssignmentViolation as error:
            status = (
                HTTPStatus.CONFLICT
                if error.code == "snapshot_changed"
                else (
                    HTTPStatus.SERVICE_UNAVAILABLE
                    if error.code == "registry_unavailable"
                    else HTTPStatus.BAD_REQUEST
                )
            )
            return self.json_message(
                "Не удалось сохранить привязки комнат в Home Assistant.",
                status,
                headers=NO_STORE_HEADERS,
            )
        except ValueError:
            return self.json_message(
                "Запрос привязки комнат заполнен неверно.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        except ClimateRuntimeUnavailable:
            return self._unavailable()
        except Exception:
            return self._unavailable()
        return self.json(result, headers=NO_STORE_HEADERS)


class ClimateAdminDeviceBindingsView(_ClimateView):
    """List and atomically save explicit native HA device bindings."""

    url = ADMIN_DEVICE_BINDINGS_PATH
    name = "api:hausman_hub:climate_admin_device_bindings"

    async def get(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_DEVICE_BINDINGS_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        runtime = self._runtime()
        if runtime is None:
            return self._unavailable()
        try:
            result = await runtime.async_climate_device_binding_options()
        except Exception:
            return self._unavailable()
        return self.json(result, headers=NO_STORE_HEADERS)

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_DEVICE_BINDINGS_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        runtime = self._runtime()
        if runtime is None:
            return self._unavailable()
        try:
            payload = await _request_json(
                request,
                maximum_bytes=MAX_CLIMATE_SETUP_BODY_BYTES,
            )
            result = await runtime.async_save_climate_device_bindings(payload)
        except ClimateDeviceBindingViolation as error:
            status = (
                HTTPStatus.CONFLICT
                if error.code in {"snapshot_changed", "preview_changed"}
                else HTTPStatus.BAD_REQUEST
            )
            return self.json_message(
                "Не удалось сохранить привязки устройств.",
                status,
                headers=NO_STORE_HEADERS,
            )
        except ValueError:
            return self.json_message(
                "Привязки устройств заполнены неверно.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        except Exception:
            return self._unavailable()
        return self.json(result, headers=NO_STORE_HEADERS)


class ClimateAdminDeviceBindingsPreviewView(_ClimateView):
    """Check explicit native bindings without persistence or commands."""

    url = ADMIN_DEVICE_BINDINGS_PREVIEW_PATH
    name = "api:hausman_hub:climate_admin_device_bindings_preview"

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_DEVICE_BINDINGS_PREVIEW_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        runtime = self._runtime()
        if runtime is None:
            return self._unavailable()
        try:
            payload = await _request_json(
                request,
                maximum_bytes=MAX_CLIMATE_SETUP_BODY_BYTES,
            )
            result = await runtime.async_preview_climate_device_bindings(payload)
        except ClimateDeviceBindingViolation as error:
            status = (
                HTTPStatus.CONFLICT
                if error.code == "snapshot_changed"
                else HTTPStatus.BAD_REQUEST
            )
            return self.json_message(
                "Не удалось проверить привязки устройств.",
                status,
                headers=NO_STORE_HEADERS,
            )
        except ValueError:
            return self.json_message(
                "Привязки устройств заполнены неверно.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        except Exception:
            return self._unavailable()
        return self.json(result, headers=NO_STORE_HEADERS)


class ClimateAdminDraftCurrentView(_ClimateView):
    """Return the current saved climate setup to one local administrator."""

    url = ADMIN_DRAFT_CURRENT_PATH
    name = "api:hausman_hub:climate_admin_draft_current"

    async def get(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_DRAFT_CURRENT_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        runtime = self._runtime()
        if runtime is None:
            return self._unavailable()
        try:
            result = await runtime.async_current_contour_setup()
        except ClimateRuntimeUnavailable:
            return self._unavailable()
        except Exception:
            return self._unavailable()
        return self.json(result, headers=NO_STORE_HEADERS)


class ClimateAdminDraftValidationView(_ClimateView):
    """Validate an unchanged draft deeply without persistence or commands."""

    url = ADMIN_DRAFT_VALIDATION_PATH
    name = "api:hausman_hub:climate_admin_draft_validation"

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_DRAFT_VALIDATION_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        runtime = self._runtime()
        if runtime is None:
            return self._unavailable()
        try:
            payload = await _request_json(
                request,
                maximum_bytes=MAX_CLIMATE_SETUP_BODY_BYTES,
            )
            result = await runtime.async_validate_contour_draft(payload)
        except ClimateSetupViolation as error:
            status = (
                HTTPStatus.CONFLICT
                if error.code in _DRAFT_CONFLICT_CODES
                else HTTPStatus.BAD_REQUEST
            )
            return self.json_message(
                "Не удалось проверить черновик климатического контура.",
                status,
                headers=NO_STORE_HEADERS,
            )
        except ValueError:
            return self.json_message(
                "Черновик климатического контура заполнен неверно.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        except ClimateRuntimeUnavailable:
            return self._unavailable()
        except Exception:
            return self._unavailable()
        return self.json(result, headers=NO_STORE_HEADERS)


class ClimateAdminDraftSaveView(_ClimateView):
    """Atomically save rooms, devices, and parameters from one exact draft."""

    url = ADMIN_DRAFT_SAVE_PATH
    name = "api:hausman_hub:climate_admin_draft_save"

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_DRAFT_SAVE_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        runtime = self._runtime()
        if runtime is None:
            return self._unavailable()
        try:
            payload = await _request_json(
                request,
                maximum_bytes=MAX_CLIMATE_SETUP_BODY_BYTES,
            )
            result = await runtime.async_save_contour_draft(payload)
        except ClimateSetupViolation as error:
            status = (
                HTTPStatus.CONFLICT
                if error.code in _DRAFT_CONFLICT_CODES
                else HTTPStatus.BAD_REQUEST
            )
            return self.json_message(
                "Не удалось сохранить климатический контур.",
                status,
                headers=NO_STORE_HEADERS,
            )
        except ValueError:
            return self.json_message(
                "Черновик климатического контура заполнен неверно.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        except ClimateRuntimeUnavailable:
            return self._unavailable()
        except Exception:
            return self._unavailable()
        return self.json(result, headers=NO_STORE_HEADERS)


class ClimateAdminProfileUpdateView(_ClimateView):
    """Replace saved day/night profiles for all configured climate rooms."""

    url = ADMIN_PROFILE_UPDATE_PATH
    name = "api:hausman_hub:climate_admin_profile_update"

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_PROFILE_UPDATE_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        runtime = self._runtime()
        if runtime is None:
            return self._unavailable()
        try:
            payload = await _request_json(
                request,
                maximum_bytes=MAX_CLIMATE_SETUP_BODY_BYTES,
            )
            result = await runtime.async_update_climate_profiles(payload)
        except ClimateSetupViolation as error:
            status = (
                HTTPStatus.CONFLICT
                if error.code in {"setup_changed", "not_configured"}
                else HTTPStatus.BAD_REQUEST
            )
            return self.json_message(
                "Не удалось сохранить профили «День» и «Ночь».",
                status,
                headers=NO_STORE_HEADERS,
            )
        except ValueError:
            return self.json_message(
                "Профили «День» и «Ночь» заполнены неверно.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        except ClimateRuntimeUnavailable:
            return self._unavailable()
        except Exception:
            return self._unavailable()
        return self.json(result, headers=NO_STORE_HEADERS)


class ClimateAdminScheduleUpdateView(_ClimateView):
    """Configure or disarm the automatic local-time climate schedule."""

    url = ADMIN_SCHEDULE_UPDATE_PATH
    name = "api:hausman_hub:climate_admin_schedule_update"

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_SCHEDULE_UPDATE_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        runtime = self._runtime()
        if runtime is None:
            return self._unavailable()
        try:
            payload = await _request_json(
                request,
                maximum_bytes=MAX_CLIMATE_SETUP_BODY_BYTES,
            )
            result = await runtime.async_update_climate_schedule(payload)
        except ClimateSetupViolation as error:
            status = (
                HTTPStatus.CONFLICT
                if error.code in {"setup_changed", "not_configured"}
                else HTTPStatus.BAD_REQUEST
            )
            return self.json_message(
                "Не удалось сохранить автоматическое расписание.",
                status,
                headers=NO_STORE_HEADERS,
            )
        except ValueError:
            return self.json_message(
                "Автоматическое расписание заполнено неверно.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        except ClimateRuntimeUnavailable:
            return self._unavailable()
        except Exception:
            return self._unavailable()
        return self.json(result, headers=NO_STORE_HEADERS)


class ClimateAdminRegistryView(_ClimateView):
    """Read or atomically replace the private registry as a local admin."""

    url = ADMIN_REGISTRY_PATH
    name = "api:hausman_hub:climate_admin_registry"

    async def get(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_REGISTRY_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        runtime = self._runtime()
        if runtime is None:
            return self._unavailable()
        try:
            payload = await runtime.async_registry_payload()
        except Exception:
            return self._unavailable()
        return self.json(payload, headers=NO_STORE_HEADERS)

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_REGISTRY_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        runtime = self._runtime()
        if runtime is None:
            return self._unavailable()
        try:
            payload = await _request_json(request)
            result = await runtime.async_replace_registry(payload)
        except (ClimateRegistryViolation, ValueError):
            return self.json_message(
                "The climate registry is invalid.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        except Exception:
            return self._unavailable()
        return self.json(result, headers=NO_STORE_HEADERS)


class ClimateAdminRegistryPreviewView(_ClimateView):
    """Validate and reconcile an unsaved private registry without mutation."""

    url = ADMIN_REGISTRY_PREVIEW_PATH
    name = "api:hausman_hub:climate_admin_registry_preview"

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_REGISTRY_PREVIEW_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        runtime = self._runtime()
        if runtime is None:
            return self._unavailable()
        try:
            payload = await _request_json(request)
            result = await runtime.async_preview_registry(payload)
        except (ClimateRegistryViolation, ValueError):
            return self.json_message(
                "The climate registry preview is invalid.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        except Exception:
            return self._unavailable()
        return self.json(result, headers=NO_STORE_HEADERS)


class ClimateAdminReadinessView(_ClimateView):
    """Expose only coarse climate rollout readiness to a local admin."""

    url = ADMIN_READINESS_PATH
    name = "api:hausman_hub:climate_admin_readiness"

    async def get(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_READINESS_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        runtime = self._runtime()
        if runtime is None:
            return self._unavailable()
        try:
            result = await runtime.async_readiness()
        except Exception:
            return self._unavailable()
        return self.json(result, headers=NO_STORE_HEADERS)


class ClimateAdminShadowComparisonView(_ClimateView):
    """Expose one redacted command-free climate comparison to a local admin."""

    url = ADMIN_SHADOW_COMPARISON_PATH
    name = "api:hausman_hub:climate_admin_shadow_comparison"

    async def get(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_SHADOW_COMPARISON_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        runtime = self._runtime()
        if runtime is None:
            return self._unavailable()
        try:
            comparison = await runtime.async_native_climate_comparison()
            if comparison is None:
                return self._unavailable()
            result = climate_comparison_to_payload(comparison)
        except Exception:
            return self._unavailable()
        return self.json(result, headers=NO_STORE_HEADERS)


class ClimateAdminShadowWindowView(_ClimateView):
    """Expose bounded persisted shadow evidence to a local administrator."""

    url = ADMIN_SHADOW_WINDOW_PATH
    name = "api:hausman_hub:climate_admin_shadow_window"

    async def get(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_SHADOW_WINDOW_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        service = self._climate_shadow()
        if service is None:
            return self._unavailable()
        try:
            result = await service.async_snapshot(
                generated_at=int(dt_util.now().timestamp() * 1000)
            )
        except Exception:
            return self._unavailable()
        return self.json(result, headers=NO_STORE_HEADERS)


class ClimateAdminPanelView(_ClimateView):
    """Serve the combined admin panel read payload to a local admin."""

    url = ADMIN_PANEL_PATH
    name = "api:hausman_hub:climate_admin_panel"

    async def get(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_PANEL_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        runtime = self._runtime()
        if runtime is None:
            return self._unavailable()
        try:
            readiness = await runtime.async_readiness()
            try:
                snapshot = await runtime.async_public_snapshot()
            except ClimateSnapshotUnavailable:
                # A disabled or not-yet-observable climate contour is a valid
                # panel state. Keep the page available so it can explain that
                # status while exposing no rooms, actions, or invented data.
                snapshot = None
        except Exception:
            return self._unavailable()
        integration_version = await self._hass.async_add_executor_job(
            _integration_version
        )
        return self.json(
            {
                "contract": {
                    "name": "hausman-hub-admin-panel",
                    "version": 2,
                },
                "integration_version": integration_version,
                "snapshot": snapshot,
                "readiness": readiness,
            },
            headers=NO_STORE_HEADERS,
        )


class ClimateAdminPanelApplyView(_ClimateView):
    """Apply saved contour settings after explicit admin confirmation."""

    url = ADMIN_PANEL_APPLY_PATH
    name = "api:hausman_hub:climate_admin_panel_apply"

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_PANEL_APPLY_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        runtime = self._runtime()
        if runtime is None:
            return self._unavailable()
        try:
            payload = await _request_json(request)
        except ValueError:
            return self.json_message(
                "The climate contour application body is invalid.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        try:
            receipt = await runtime.async_apply_contour(payload)
        except ContourApplyViolation:
            return self.json_message(
                "The climate contour application is invalid.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        except ClimateRuntimeUnavailable:
            return self._unavailable()
        return self.json(receipt.as_payload(), headers=NO_STORE_HEADERS)


class ClimateAdminPanelTemporaryView(_ClimateView):
    """Set or clear one room temperature for an admin until the boundary."""

    url = ADMIN_PANEL_TEMPORARY_PATH
    name = "api:hausman_hub:climate_admin_panel_temporary"

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_PANEL_TEMPORARY_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        runtime = self._runtime()
        if runtime is None:
            return self._unavailable()
        try:
            payload = await _request_json(request)
        except ValueError:
            return self.json_message(
                "The temporary climate temperature body is invalid.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        try:
            receipt = await runtime.async_temporary_temperature(
                payload,
                dt_util.now(),
            )
        except TemporaryTemperatureViolation:
            return self.json_message(
                "The temporary climate temperature request is invalid.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        except ContourApplyViolation:
            return self.json_message(
                "The temporary climate temperature is not ready.",
                HTTPStatus.CONFLICT,
                headers=NO_STORE_HEADERS,
            )
        except ClimateRuntimeUnavailable:
            return self._unavailable()
        return self.json(receipt.as_payload(), headers=NO_STORE_HEADERS)


class ClimateAdminClimateModeView(_ClimateView):
    """Read or explicitly switch the saved climate control mode."""

    url = ADMIN_CLIMATE_MODE_PATH
    name = "api:hausman_hub:climate_admin_mode"

    async def get(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_CLIMATE_MODE_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        runtime = self._runtime()
        if runtime is None:
            return self._unavailable()
        try:
            status = await runtime.async_climate_mode_status()
        except Exception:
            return self._unavailable()
        return self.json(status, headers=NO_STORE_HEADERS)

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_CLIMATE_MODE_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        runtime = self._runtime()
        if runtime is None:
            return self._unavailable()
        try:
            payload = await _request_json(request)
        except ValueError:
            return self.json_message(
                "Тело запроса смены режима климатического управления неверно.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        try:
            status = await runtime.async_climate_mode_status()
            mode = validate_climate_mode_update(status["mode"], payload)
        except ClimateSignalSettingsViolation as error:
            return self.json_message(
                "Не удалось изменить режим климатического управления.",
                (
                    HTTPStatus.CONFLICT
                    if error.code == "mode_changed"
                    else HTTPStatus.BAD_REQUEST
                ),
                headers=NO_STORE_HEADERS,
            )
        except Exception:
            return self._unavailable()
        if mode == "managed" and status["contour_configured"] is not True:
            return self.json_message(
                "Управляемый режим требует настроенного климатического контура.",
                HTTPStatus.CONFLICT,
                headers=NO_STORE_HEADERS,
            )
        entries = self._hass.config_entries.async_entries(DOMAIN)
        if len(entries) != 1:
            return self._unavailable()
        entry = entries[0]
        try:
            current = effective_configuration(
                entry.data,
                entry.options,
            )
        except Exception:
            return self._unavailable()
        # The authoritative optimistic lock reads the saved options again
        # immediately before the write: a concurrent mode change must lose
        # with HTTP 409 instead of silently overwriting the first request.
        if payload["expected_mode"] != current.climate_bridge_mode.value:
            return self.json_message(
                "Не удалось изменить режим климатического управления.",
                HTTPStatus.CONFLICT,
                headers=NO_STORE_HEADERS,
            )
        options = create_options(
            mode_value=current.mode,
            local_summary_enabled_value=current.local_summary_enabled,
            summary_update_interval_value=current.summary_update_interval,
            canary_control_enabled_value=current.canary_control_enabled,
            canary_control_target_value=(
                None
                if current.canary_control_target is None
                else current.canary_control_target.entity_id
            ),
            climate_bridge_mode_value=mode,
            climate_bridge_target_value=None,
            climate_canary_room_id_value=None,
            native_climate_mode_value=current.native_climate_policy.mode.value,
            native_climate_room_id_value=current.native_climate_policy.room_id,
            native_target_temperature_value=(
                current.native_climate_policy.target_temperature
            ),
            native_target_humidity_value=current.native_climate_policy.target_humidity,
            connection_mode_value=current.connection_mode,
            smart_home_center_url_value=current.smart_home_center_url,
            home_assistant_url_value=current.home_assistant_url,
        )
        self._hass.config_entries.async_update_entry(entry, options=options)
        return self.json(
            {
                "mode": mode,
                "contour_configured": status["contour_configured"],
            },
            headers=NO_STORE_HEADERS,
        )


class ClimateAdminHomeEnvironmentView(_ClimateView):
    """Read or atomically replace the home climate signal bindings."""

    url = ADMIN_HOME_ENVIRONMENT_PATH
    name = "api:hausman_hub:climate_admin_home_environment"

    async def get(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_HOME_ENVIRONMENT_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        runtime = self._runtime()
        if runtime is None:
            return self._unavailable()
        try:
            payload = await runtime.async_registry_payload()
            candidates = {
                "outdoor_temperature": await runtime.async_signal_catalog(
                    OUTDOOR_TEMPERATURE_SIGNAL
                ),
                "presence": await runtime.async_signal_catalog(PRESENCE_SIGNAL),
                "central_heating": await runtime.async_signal_catalog(
                    CENTRAL_HEATING_SIGNAL
                ),
            }
        except Exception:
            return self._unavailable()
        return self.json(
            {
                "home": payload.get("home"),
                "candidates": candidates,
            },
            headers=NO_STORE_HEADERS,
        )

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_HOME_ENVIRONMENT_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        runtime = self._runtime()
        if runtime is None:
            return self._unavailable()
        try:
            payload = await _request_json(request)
        except ValueError:
            return self.json_message(
                "Тело настроек сигналов дома заполнено неверно.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        try:
            home = validate_home_environment_update(
                payload,
                entity_known=runtime.signal_entity_known,
            )
            registry = await runtime.async_registry_payload()
            current_home = registry.get("home")
            if not isinstance(current_home, Mapping) or not _home_signals_suitable(
                runtime,
                home,
                current_home,
            ):
                raise ClimateSignalSettingsViolation("unsuitable_entity")
        except ClimateSignalSettingsViolation:
            return self.json_message(
                "Настройки сигналов дома заполнены неверно.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        except Exception:
            return self._unavailable()
        try:
            result = await runtime.async_update_home_environment(home)
        except Exception:
            return self._unavailable()
        return self.json(
            {
                "home": result.get("home"),
                "setup_revision": result.get("setup_revision"),
            },
            headers=NO_STORE_HEADERS,
        )


class ClimateAdminRoomSignalsView(_ClimateView):
    """Read or atomically replace one room's window and presence bindings."""

    url = ADMIN_ROOM_SIGNALS_PATH
    name = "api:hausman_hub:climate_admin_room_signals"

    async def get(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_ROOM_SIGNALS_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        runtime = self._runtime()
        if runtime is None:
            return self._unavailable()
        try:
            payload = await runtime.async_registry_payload()
            candidates = await runtime.async_signal_catalog(WINDOW_SIGNAL)
            presence_candidates = await runtime.async_signal_catalog(
                ROOM_PRESENCE_SIGNAL
            )
        except Exception:
            return self._unavailable()
        return self.json(
            {
                "rooms": _room_signal_payloads(payload),
                "candidates": candidates,
                "presence_candidates": presence_candidates,
            },
            headers=NO_STORE_HEADERS,
        )

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_ROOM_SIGNALS_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        runtime = self._runtime()
        if runtime is None:
            return self._unavailable()
        try:
            payload = await _request_json(request)
        except ValueError:
            return self.json_message(
                "Тело привязки сигналов комнаты заполнено неверно.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        try:
            registry = await runtime.async_registry_payload()
            room_ids = frozenset(
                room["id"] for room in _room_signal_payloads(registry)
            )
            if isinstance(payload, Mapping) and set(payload) == {"rooms"}:
                updates = validate_room_signal_updates(
                    payload,
                    room_ids=room_ids,
                    entity_known=runtime.signal_entity_known,
                )
                room_id = None
                entity_id = None
                presence_entity_ids = None
            elif isinstance(payload, Mapping) and "presence_entity_ids" in payload:
                room_id, entity_id, presence_entity_ids = (
                    validate_room_signal_update(
                        payload,
                        room_ids=room_ids,
                        entity_known=runtime.signal_entity_known,
                    )
                )
                updates = None
            else:
                room_id, entity_id = validate_room_window_update(
                    payload,
                    room_ids=room_ids,
                    entity_known=runtime.signal_entity_known,
                )
                presence_entity_ids = None
                updates = None
            checked_updates = (
                updates
                if updates is not None
                else ((room_id, entity_id, presence_entity_ids or ()),)
            )
            if not _room_signals_suitable(
                runtime,
                checked_updates,
                _room_signal_payloads(registry),
            ):
                raise ClimateSignalSettingsViolation("unsuitable_entity")
        except ClimateSignalSettingsViolation:
            return self.json_message(
                "Привязка сигналов комнаты заполнена неверно.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        except Exception:
            return self._unavailable()
        try:
            result = (
                await runtime.async_update_room_signal_batch(updates)
                if updates is not None
                else (
                    await runtime.async_update_room_window(room_id, entity_id)
                    if presence_entity_ids is None
                    else await runtime.async_update_room_signals(
                        room_id,
                        entity_id,
                        presence_entity_ids,
                    )
                )
            )
        except ClimateRegistryViolation:
            return self.json_message(
                "Привязка сигналов комнаты заполнена неверно.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        except Exception:
            return self._unavailable()
        return self.json(
            {"rooms": _room_signal_payloads(result)},
            headers=NO_STORE_HEADERS,
        )


class ClimateAdminAiAssistantView(_ClimateView):
    url = ADMIN_AI_ASSISTANT_PATH
    name = "api:hausman_hub:climate_admin_ai_assistant"

    async def get(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_AI_ASSISTANT_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        assistant = self._ai_assistant()
        if assistant is None:
            return self._unavailable()
        state = ai_assistant_state_to_payload(await assistant.async_state())
        return self.json(
            {
                "settings": ai_assistant_public_settings(
                    _ai_assistant_binding(self._hass)
                ),
                "stats": state["stats"],
                "last_advisory": state["last_advisory"],
            },
            headers=NO_STORE_HEADERS,
        )


class ClimateAdminAiAssistantSettingsView(_ClimateView):
    url = ADMIN_AI_ASSISTANT_SETTINGS_PATH
    name = "api:hausman_hub:climate_admin_ai_assistant_settings"

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_AI_ASSISTANT_SETTINGS_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        if self._ai_assistant() is None:
            return self._unavailable()
        try:
            payload = await _request_json(request)
            binding = ai_assistant_binding_update(
                payload,
                _ai_assistant_binding(self._hass),
            )
        except (AiAssistantViolation, ValueError) as error:
            code = error.code if isinstance(error, AiAssistantViolation) else "invalid_request"
            return self.json(
                {"error": code},
                status_code=HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        entry = _single_hausmanhub_entry(self._hass)
        if entry is None:
            return self._unavailable()
        self._hass.config_entries.async_update_entry(
            entry,
            data=ai_assistant_entry_data(entry.data, binding),
        )
        return self.json(
            {"settings": ai_assistant_public_settings(binding)},
            headers=NO_STORE_HEADERS,
        )


class ClimateAdminAiAssistantRefreshView(_ClimateView):
    url = ADMIN_AI_ASSISTANT_REFRESH_PATH
    name = "api:hausman_hub:climate_admin_ai_assistant_refresh"

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_AI_ASSISTANT_REFRESH_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        assistant = self._ai_assistant()
        if assistant is None:
            return self._unavailable()
        try:
            await _request_json(request)
            advisory = await assistant.async_refresh()
            state = ai_assistant_state_to_payload(await assistant.async_state())
        except ValueError:
            return self.json(
                {"error": "invalid_request"},
                status_code=HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        except AiAssistantViolation:
            return self._unavailable()
        payload = {"advisory": state["last_advisory"]}
        latest = state["stats"]["recent_calls"]
        if (
            advisory.status is AiAdvisoryStatus.PROVIDER_ERROR
            and latest
            and latest[-1]["error_class"] == "auth"
        ):
            payload["error"] = "provider_auth"
            return self.json(
                payload,
                status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                headers=NO_STORE_HEADERS,
            )
        return self.json(payload, headers=NO_STORE_HEADERS)


class ClimateAdminConnectionSettingsView(_ClimateView):
    """Read or update the two connection addresses used by the app."""

    url = ADMIN_CONNECTION_SETTINGS_PATH
    name = "api:hausman_hub:climate_admin_connection_settings"

    async def get(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_CONNECTION_SETTINGS_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        entry = _single_hausmanhub_entry(self._hass)
        if entry is None:
            return self._unavailable()
        try:
            current = effective_configuration(entry.data, entry.options)
        except Exception:
            return self._unavailable()
        return self.json(
            {
                "connection_mode": current.connection_mode,
                "smart_home_center_url": current.smart_home_center_url,
                "home_assistant_url": current.home_assistant_url,
            },
            headers=NO_STORE_HEADERS,
        )

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_CONNECTION_SETTINGS_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        entry = _single_hausmanhub_entry(self._hass)
        if entry is None:
            return self._unavailable()
        try:
            payload = await _request_json(request)
        except ValueError:
            return self.json_message(
                "The connection settings body is invalid.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        if not isinstance(payload, Mapping):
            return self.json_message(
                "The connection settings body must be an object.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        try:
            current = effective_configuration(entry.data, entry.options)
        except Exception:
            return self._unavailable()
        connection_mode = payload.get(CONNECTION_MODE_FIELD, current.connection_mode)
        smart_home_center_url = payload.get(
            SMART_HOME_CENTER_URL_FIELD, current.smart_home_center_url
        )
        home_assistant_url = payload.get(
            HOME_ASSISTANT_URL_FIELD, current.home_assistant_url
        )
        try:
            options = create_options(
                mode_value=current.mode,
                local_summary_enabled_value=current.local_summary_enabled,
                summary_update_interval_value=current.summary_update_interval,
                canary_control_enabled_value=current.canary_control_enabled,
                canary_control_target_value=(
                    None
                    if current.canary_control_target is None
                    else current.canary_control_target.entity_id
                ),
                climate_bridge_mode_value=current.climate_bridge_mode.value,
                climate_bridge_target_value=None,
                climate_canary_room_id_value=None,
                native_climate_mode_value=current.native_climate_policy.mode.value,
                native_climate_room_id_value=current.native_climate_policy.room_id,
                native_target_temperature_value=current.native_climate_policy.target_temperature,
                native_target_humidity_value=current.native_climate_policy.target_humidity,
                connection_mode_value=connection_mode,
                smart_home_center_url_value=smart_home_center_url,
                home_assistant_url_value=home_assistant_url,
            )
        except ConfigurationViolation as error:
            return self.json_message(
                str(error), HTTPStatus.BAD_REQUEST, headers=NO_STORE_HEADERS
            )
        self._hass.config_entries.async_update_entry(entry, options=options)
        return self.json(
            {
                "connection_mode": options.get(
                    CONNECTION_MODE_FIELD, CONNECTION_MODE_DEFAULT
                ),
                "smart_home_center_url": options.get(SMART_HOME_CENTER_URL_FIELD),
                "home_assistant_url": options.get(HOME_ASSISTANT_URL_FIELD),
            },
            headers=NO_STORE_HEADERS,
        )


def _energy_settings_payload(settings: HausmanHubSettings) -> dict[str, object]:
    return {
        "displayUnits": settings.energy_display_units,
        "showVoltage": settings.energy_show_voltage,
        "aggregation": settings.energy_aggregation,
        "useAllDevices": settings.energy_use_all_devices,
        "selectedDeviceIds": list(settings.energy_selected_device_ids),
    }


class ClimateAdminEnergySettingsView(_ClimateView):
    """Persist the shared dashboard energy presentation in Home Assistant."""

    url = ADMIN_ENERGY_SETTINGS_PATH
    name = "api:hausman_hub:climate_admin_energy_settings"

    def _service(self) -> object | None:
        if self._runtime() is None:
            return None
        return self._hass.data.get(DOMAIN, {}).get("settings_service")

    async def get(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_ENERGY_SETTINGS_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        service = self._service()
        if service is None:
            return self._unavailable()
        return self.json(_energy_settings_payload(service.current), headers=NO_STORE_HEADERS)

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_ENERGY_SETTINGS_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        service = self._service()
        if service is None:
            return self._unavailable()
        try:
            payload = await _request_json(request)
        except ValueError:
            payload = None
        required = {
            "displayUnits",
            "showVoltage",
            "aggregation",
            "useAllDevices",
            "selectedDeviceIds",
        }
        if not isinstance(payload, Mapping) or set(payload) != required:
            return self.json_message(
                "The energy settings body is invalid.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        selected = payload.get("selectedDeviceIds")
        try:
            updated = replace(
                service.current,
                energy_display_units=payload.get("displayUnits"),
                energy_show_voltage=payload.get("showVoltage"),
                energy_aggregation=payload.get("aggregation"),
                energy_use_all_devices=payload.get("useAllDevices"),
                energy_selected_device_ids=tuple(selected)
                if isinstance(selected, list)
                else selected,
            )
            await service.async_replace(updated)
        except (HausmanHubSettingsViolation, TypeError):
            return self.json_message(
                "The energy settings values are invalid.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        return self.json(_energy_settings_payload(updated), headers=NO_STORE_HEADERS)


class ClimateAdminResetView(_ClimateView):
    """Reset HausmanHub-owned settings without changing Home Assistant devices."""

    url = ADMIN_RESET_PATH
    name = "api:hausman_hub:climate_admin_reset"

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, ADMIN_RESET_PATH):
            return _not_found(self)
        if not _is_local_admin_request(request):
            return _forbidden(self)
        try:
            payload = await _request_json(request)
        except ValueError:
            payload = None
        if not isinstance(payload, Mapping) or payload.get("confirmation") != "RESET_HAUSMANHUB":
            return self.json_message(
                "Для полного сброса требуется явное подтверждение.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        runtime = self._runtime()
        entry = _single_hausmanhub_entry(self._hass)
        data = self._hass.data.get(DOMAIN, {})
        scenario_service = data.get("scenario_service")
        ir_code_service = data.get("ir_code_service")
        settings_service = data.get("settings_service")
        assistant = self._ai_assistant()
        if any(item is None for item in (
            runtime, entry, scenario_service, ir_code_service, settings_service, assistant,
        )):
            return self._unavailable()
        try:
            current = effective_configuration(entry.data, entry.options)
            await runtime.async_reset_configuration()
            await scenario_service.async_reset()
            await ir_code_service.async_reset()
            await settings_service.async_replace(HausmanHubSettings())
            await assistant.async_reset_state()
            self._hass.config_entries.async_update_entry(
                entry,
                data=ai_assistant_entry_data(
                    entry.data,
                    AiAssistantBinding(None, None),
                ),
                options=create_options(mode_value=current.mode),
            )
        except Exception:
            return self._unavailable()
        return self.json(
            {
                "status": "reset",
                "reset": [
                    "climate",
                    "device_bindings",
                    "home_signals",
                    "scenarios",
                    "ir_codes",
                    "assistant",
                    "connection",
                ],
                "preserved": ["home_assistant_areas", "home_assistant_devices"],
            },
            headers=NO_STORE_HEADERS,
        )


def _single_hausmanhub_entry(hass: HomeAssistant) -> Any | None:
    entries = hass.config_entries.async_entries(DOMAIN)
    return entries[0] if len(entries) == 1 else None


def _ai_assistant_binding(hass: HomeAssistant) -> AiAssistantBinding:
    entry = _single_hausmanhub_entry(hass)
    if entry is None:
        return AiAssistantBinding(None, None)
    try:
        return ai_assistant_binding_from_entry_data(entry.data)
    except AiAssistantViolation:
        return AiAssistantBinding(None, None)


def _room_signal_payloads(registry_payload: dict[str, object]) -> list[dict[str, object]]:
    """Reduce a registry payload to bounded per-room signal bindings."""

    rooms = registry_payload.get("rooms")
    if not isinstance(rooms, list):
        return []
    return [
        {
            "id": room.get("id"),
            "name": room.get("name"),
            "window_entity_id": room.get("window_entity_id"),
            "presence_entity_ids": (
                room.get("presence_entity_ids")
                if isinstance(room.get("presence_entity_ids"), list)
                else []
            ),
        }
        for room in rooms
        if isinstance(room, dict)
    ]


def _home_signals_suitable(
    runtime: ClimateRuntime,
    home: Mapping[str, object],
    current: Mapping[str, object],
) -> bool:
    """Allow only purpose-specific candidates, retaining exact legacy bindings."""

    return all(
        _signal_suitable_or_current(
            runtime,
            signal_kind,
            home.get(field),
            current.get(field),
        )
        for field, signal_kind in (
            ("outdoor_temperature_entity_id", OUTDOOR_TEMPERATURE_SIGNAL),
            ("presence_entity_id", PRESENCE_SIGNAL),
            ("central_heating_entity_id", CENTRAL_HEATING_SIGNAL),
        )
    )


def _room_signals_suitable(
    runtime: ClimateRuntime,
    updates: tuple[tuple[str, str | None, tuple[str, ...]], ...],
    current_rooms: list[dict[str, object]],
) -> bool:
    """Require new room bindings to match both their purpose and HA room."""

    current_by_id = {
        room["id"]: room
        for room in current_rooms
        if isinstance(room.get("id"), str)
    }
    for room_id, window_entity_id, presence_entity_ids in updates:
        current = current_by_id.get(room_id, {})
        if not _signal_suitable_or_current(
            runtime,
            WINDOW_SIGNAL,
            window_entity_id,
            current.get("window_entity_id"),
            room_id=room_id,
        ):
            return False
        current_presence = current.get("presence_entity_ids")
        retained = (
            frozenset(current_presence)
            if isinstance(current_presence, list)
            else frozenset()
        )
        if any(
            entity_id not in retained
            and not runtime.signal_entity_suitable(
                ROOM_PRESENCE_SIGNAL,
                entity_id,
                room_id=room_id,
            )
            for entity_id in presence_entity_ids
        ):
            return False
    return True


def _signal_suitable_or_current(
    runtime: ClimateRuntime,
    signal_kind: str,
    value: object,
    current: object,
    *,
    room_id: str | None = None,
) -> bool:
    """Keep an unchanged binding or require it to exist in the suitable catalog."""

    return (
        value is None
        or value == current
        or (
            isinstance(value, str)
            and runtime.signal_entity_suitable(
                signal_kind,
                value,
                room_id=room_id,
            )
        )
    )


async def _request_json(
    request: Any,
    *,
    maximum_bytes: int = MAX_ACTION_BODY_BYTES,
) -> object:
    length = getattr(request, "content_length", None)
    if type(length) is not int or not 0 < length <= maximum_bytes:
        raise ValueError("request body size is invalid")
    if getattr(request, "content_type", None) != "application/json":
        raise ValueError("request body must be JSON")
    return await request.json()


def _is_exact_request(request: Any, path: str) -> bool:
    return (
        getattr(request, "path", None) == path
        and getattr(request, "query_string", None) == ""
    )


def _is_local_tablet_request(request: Any) -> bool:
    user = _request_user(request)
    if not _is_local_address(getattr(request, "remote", None)):
        return False
    if user is None or getattr(user, "is_admin", True) or getattr(user, "system_generated", True):
        return False
    groups = getattr(user, "groups", None)
    if not isinstance(groups, (frozenset, list, set, tuple)):
        return False
    return {getattr(group, "id", None) for group in groups} == {TABLET_GROUP_ID}


def _is_local_admin_request(request: Any) -> bool:
    user = _request_user(request)
    return (
        _is_local_address(
            getattr(request, "remote", None),
            allow_ipv6_link_local=True,
        )
        and user is not None
        and getattr(user, "is_admin", False) is True
        and getattr(user, "system_generated", True) is False
    )


def _request_user(request: Any) -> object | None:
    try:
        return request["hass_user"]
    except (KeyError, TypeError):
        return None


def _is_local_address(
    remote: object,
    *,
    allow_ipv6_link_local: bool = False,
) -> bool:
    if not isinstance(remote, str):
        return False
    try:
        address = ip_address(remote)
    except ValueError:
        return False
    if isinstance(address, IPv4Address):
        return address.is_loopback or any(
            address in network for network in HOME_IPV4_NETWORKS
        )
    mapped = address.ipv4_mapped
    if mapped is not None:
        return mapped.is_loopback or any(
            mapped in network for network in HOME_IPV4_NETWORKS
        )
    return (
        address.is_loopback
        or (allow_ipv6_link_local and address.is_link_local)
        or address in HOME_IPV6_NETWORK
    )


def _not_found(view: HomeAssistantView) -> Any:
    return view.json_message(
        "The HausmanHub climate API route was not found.",
        HTTPStatus.NOT_FOUND,
        headers=NO_STORE_HEADERS,
    )


def _forbidden(view: HomeAssistantView) -> Any:
    return view.json_message(
        "Local HausmanHub access is required.",
        HTTPStatus.FORBIDDEN,
        headers=NO_STORE_HEADERS,
    )
