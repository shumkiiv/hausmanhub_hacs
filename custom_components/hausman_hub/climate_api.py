"""Authenticated local HTTP facade for tablet and climate administration."""

from __future__ import annotations

from http import HTTPStatus
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address
from typing import TYPE_CHECKING, Any, Final

from homeassistant.components.http import HomeAssistantView
from homeassistant.util import dt as dt_util

from .application.api_capabilities import (
    CAPABILITIES_PATH,
    CONTOURS_PATH,
    CONTOUR_APPLY_PATH,
    CONTOUR_APPLY_PREVIEW_PATH,
    HOME_PATH,
    TEMPORARY_TEMPERATURE_PATH,
    api_capabilities_snapshot,
)
from .application.configuration import (
    create_options,
    effective_configuration,
)
from .application.climate_signal_settings import (
    CENTRAL_HEATING_DOMAINS,
    OUTDOOR_TEMPERATURE_DOMAINS,
    PRESENCE_DOMAINS,
    WINDOW_DOMAINS,
    ClimateSignalSettingsViolation,
    validate_climate_mode_update,
    validate_home_environment_update,
    validate_room_window_update,
)
from .application.contour_apply import ContourApplyViolation
from .application.contour_override import TemporaryTemperatureViolation
from .application.climate_registry import ClimateRegistryViolation
from .application.climate_runtime import (
    ClimateRuntime,
    ClimateRuntimeUnavailable,
    ClimateSnapshotUnavailable,
)
from .application.climate_setup import ClimateSetupViolation

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


DOMAIN = "hausman_hub"
DATA_CLIMATE_RUNTIME = "climate_runtime"
DATA_CLIMATE_VIEWS = "climate_views"
ADMIN_IMPORT_PATH = "/api/hausman_hub/v1/admin/climate-import"
ADMIN_DRAFT_PATH = "/api/hausman_hub/v1/admin/climate-drafts"
ADMIN_DRAFT_CURRENT_PATH = "/api/hausman_hub/v1/admin/climate-drafts/current"
ADMIN_DRAFT_VALIDATION_PATH = "/api/hausman_hub/v1/admin/climate-drafts/validate"
ADMIN_DRAFT_SAVE_PATH = "/api/hausman_hub/v1/admin/climate-drafts/save"
ADMIN_PROFILE_UPDATE_PATH = "/api/hausman_hub/v1/admin/climate-profiles"
ADMIN_SCHEDULE_UPDATE_PATH = "/api/hausman_hub/v1/admin/climate-schedule"
ADMIN_REGISTRY_PATH = "/api/hausman_hub/v1/admin/climate-registry"
ADMIN_REGISTRY_PREVIEW_PATH = "/api/hausman_hub/v1/admin/climate-registry-preview"
ADMIN_READINESS_PATH = "/api/hausman_hub/v1/admin/climate-readiness"
ADMIN_SHADOW_EVIDENCE_PATH = "/api/hausman_hub/v1/admin/climate-shadow-evidence"
ADMIN_CANARY_PREFLIGHT_PATH = "/api/hausman_hub/v1/admin/climate-canary-preflight"
ADMIN_PANEL_PATH = "/api/hausman_hub/v1/admin/panel"
ADMIN_PANEL_APPLY_PATH = "/api/hausman_hub/v1/admin/panel/apply"
ADMIN_PANEL_TEMPORARY_PATH = "/api/hausman_hub/v1/admin/panel/temporary-temperature"
ADMIN_CLIMATE_MODE_PATH = "/api/hausman_hub/v1/admin/climate-mode"
ADMIN_HOME_ENVIRONMENT_PATH = "/api/hausman_hub/v1/admin/home-environment"
ADMIN_ROOM_SIGNALS_PATH = "/api/hausman_hub/v1/admin/climate-room-signals"
NO_STORE_HEADERS = {"Cache-Control": "no-store"}
MAX_ACTION_BODY_BYTES = 16 * 1024
MAX_CLIMATE_SETUP_BODY_BYTES = 256 * 1024
TABLET_GROUP_ID = "system-users"
HOME_IPV4_NETWORKS: Final[tuple[IPv4Network, ...]] = (
    IPv4Network("10.0.0.0/8"),
    IPv4Network("172.16.0.0/12"),
    IPv4Network("192.168.0.0/16"),
)
HOME_IPV6_NETWORK: Final[IPv6Network] = IPv6Network("fc00::/7")


def register_climate_api(hass: HomeAssistant, runtime: ClimateRuntime) -> None:
    """Register fixed routes once and point them at the loaded HausmanHub runtime."""

    data = hass.data.setdefault(DOMAIN, {})
    data[DATA_CLIMATE_RUNTIME] = runtime
    if DATA_CLIMATE_VIEWS not in data:
        views = (
            ClimateCapabilitiesView(hass),
            ClimateHomeView(hass),
            ContoursView(hass),
            ContourApplyPreviewView(hass),
            ContourApplyView(hass),
            TemporaryTemperatureView(hass),
            ClimateAdminImportView(hass),
            ClimateAdminDraftView(hass),
            ClimateAdminDraftCurrentView(hass),
            ClimateAdminDraftValidationView(hass),
            ClimateAdminDraftSaveView(hass),
            ClimateAdminProfileUpdateView(hass),
            ClimateAdminScheduleUpdateView(hass),
            ClimateAdminRegistryView(hass),
            ClimateAdminRegistryPreviewView(hass),
            ClimateAdminReadinessView(hass),
            ClimateAdminPanelView(hass),
            ClimateAdminPanelApplyView(hass),
            ClimateAdminPanelTemporaryView(hass),
            ClimateAdminClimateModeView(hass),
            ClimateAdminHomeEnvironmentView(hass),
            ClimateAdminRoomSignalsView(hass),
        )
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
        return self.json(api_capabilities_snapshot(), headers=NO_STORE_HEADERS)


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
                if error.code in {"snapshot_changed", "data_stale"}
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
                if error.code in {"snapshot_changed", "data_stale"}
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
                if error.code in {"snapshot_changed", "data_stale"}
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
        return self.json(
            {
                "contract": {
                    "name": "hausman-hub-admin-panel",
                    "version": 2,
                },
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
                    OUTDOOR_TEMPERATURE_DOMAINS
                ),
                "presence": await runtime.async_signal_catalog(PRESENCE_DOMAINS),
                "central_heating": await runtime.async_signal_catalog(
                    CENTRAL_HEATING_DOMAINS
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
        except ClimateSignalSettingsViolation:
            return self.json_message(
                "Настройки сигналов дома заполнены неверно.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        try:
            result = await runtime.async_update_home_environment(home)
        except Exception:
            return self._unavailable()
        return self.json({"home": result.get("home")}, headers=NO_STORE_HEADERS)


class ClimateAdminRoomSignalsView(_ClimateView):
    """Read or atomically replace one room window signal binding."""

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
            candidates = await runtime.async_signal_catalog(WINDOW_DOMAINS)
        except Exception:
            return self._unavailable()
        return self.json(
            {
                "rooms": _room_signal_payloads(payload),
                "candidates": candidates,
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
                "Тело привязки окна комнаты заполнено неверно.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        try:
            registry = await runtime.async_registry_payload()
            room_ids = frozenset(
                room["id"] for room in _room_signal_payloads(registry)
            )
            room_id, entity_id = validate_room_window_update(
                payload,
                room_ids=room_ids,
                entity_known=runtime.signal_entity_known,
            )
        except ClimateSignalSettingsViolation:
            return self.json_message(
                "Привязка окна комнаты заполнена неверно.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        except Exception:
            return self._unavailable()
        try:
            result = await runtime.async_update_room_window(room_id, entity_id)
        except ClimateRegistryViolation:
            return self.json_message(
                "Привязка окна комнаты заполнена неверно.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        except Exception:
            return self._unavailable()
        return self.json(
            {"rooms": _room_signal_payloads(result)},
            headers=NO_STORE_HEADERS,
        )


def _room_signal_payloads(registry_payload: dict[str, object]) -> list[dict[str, object]]:
    """Reduce a registry payload to the bounded per-room window bindings."""

    rooms = registry_payload.get("rooms")
    if not isinstance(rooms, list):
        return []
    return [
        {
            "id": room.get("id"),
            "name": room.get("name"),
            "window_entity_id": room.get("window_entity_id"),
        }
        for room in rooms
        if isinstance(room, dict)
    ]


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
