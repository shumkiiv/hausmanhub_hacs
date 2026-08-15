"""Public tablet API views and lifecycle wiring for voice greeting."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from homeassistant.components.http import HomeAssistantView

from .application.api_capabilities import API_BASE_PATH
from .application.voice_greeting import (
    DEFAULT_AWAY_ENTITY_ID,
    VoiceGreetingViolation,
)
from .application.voice_greeting_service import VoiceGreetingService
from .climate_api import (
    NO_STORE_HEADERS,
    _forbidden,
    _is_exact_request,
    _is_local_tablet_request,
    _not_found,
    _request_json,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


DOMAIN = "hausman_hub"
DATA_VOICE_SERVICE = "voice_greeting_service"
DATA_VOICE_VIEWS = "voice_greeting_views"
DATA_VOICE_UNSUBSCRIBE = "voice_greeting_unsubscribe"
DATA_VOICE_TASKS = "voice_greeting_tasks"

VOICE_GREETING_PATH = f"{API_BASE_PATH}/voice/yandex-greeting"
VOICE_GREETING_TEST_PATH = f"{VOICE_GREETING_PATH}/test"
YANDEX_INTENT_EVENT = "yandex_intent"


def voice_greeting_service(hass: HomeAssistant) -> VoiceGreetingService | None:
    candidate = hass.data.get(DOMAIN, {}).get(DATA_VOICE_SERVICE)
    return candidate if isinstance(candidate, VoiceGreetingService) else None


async def async_start_voice_greeting(hass: HomeAssistant, entry: Any) -> VoiceGreetingService:
    """Build the service, subscribe the watcher and the dialog, register views."""

    from .realtime_api import publish_command_receipt
    from .voice_greeting_ha_gateway import HomeAssistantVoiceGateway
    from .voice_greeting_storage import HomeAssistantVoiceGreetingStore

    def _publish(receipt: dict[str, Any], operation: str) -> None:
        publish_command_receipt(
            hass,
            {
                "requestId": receipt.get("commandId"),
                "correlationId": receipt.get("correlationId"),
                "accepted": receipt.get("accepted"),
                "confirmed": receipt.get("confirmed"),
                "targetId": receipt.get("stationEntityId"),
                "message": receipt.get("detail"),
                "error": None if receipt.get("confirmed") else receipt.get("code"),
            },
            operation=operation,
        )

    gateway = HomeAssistantVoiceGateway(hass, away_entity_id=DEFAULT_AWAY_ENTITY_ID)
    service = VoiceGreetingService(
        HomeAssistantVoiceGreetingStore(hass, entry.entry_id),
        gateway,
        publish_receipt=_publish,
    )
    await service.async_load()
    data = hass.data.setdefault(DOMAIN, {})
    data[DATA_VOICE_SERVICE] = service

    unsubscribers = []
    tasks: set[Any] = data.setdefault(DATA_VOICE_TASKS, set())

    def _schedule(coro: Any) -> None:
        task = hass.async_create_task(coro)
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    def _state_changed(event: Any) -> None:
        event_data = getattr(event, "data", {})
        if not isinstance(event_data, dict) or event_data.get("entity_id") != gateway.away_entity_id:
            return
        old = getattr(event_data.get("old_state"), "state", None)
        new = getattr(event_data.get("new_state"), "state", None)
        _schedule(service.async_home_mode_changed(old, new))

    async def _yandex_intent(event: Any) -> None:
        event_data = getattr(event, "data", {})
        text = event_data.get("text") if isinstance(event_data, dict) else None
        answer = await service.async_dialog_turn(text)
        if answer is not None:
            await gateway.async_publish_dialog_answer(answer)

    def _intent_listener(event: Any) -> None:
        _schedule(_yandex_intent(event))

    bus = getattr(hass, "bus", None)
    if bus is not None:
        unsubscribers.append(bus.async_listen("state_changed", _state_changed))
        unsubscribers.append(bus.async_listen(YANDEX_INTENT_EVENT, _intent_listener))
    data[DATA_VOICE_UNSUBSCRIBE] = tuple(unsubscribers)

    if DATA_VOICE_VIEWS not in data:
        views = [VoiceGreetingView(hass), VoiceGreetingTestView(hass)]
        for view in views:
            hass.http.register_view(view)
        data[DATA_VOICE_VIEWS] = views
    return service


def clear_voice_greeting(hass: HomeAssistant, entry_id: str) -> None:
    data = hass.data.get(DOMAIN)
    if not isinstance(data, dict):
        return
    service = data.get(DATA_VOICE_SERVICE)
    if isinstance(service, VoiceGreetingService):
        data.pop(DATA_VOICE_SERVICE, None)
    for task in data.pop(DATA_VOICE_TASKS, set()) or ():
        task.cancel()
    for unsubscribe in data.pop(DATA_VOICE_UNSUBSCRIBE, ()) or ():
        unsubscribe()


class _VoiceViewBase(HomeAssistantView):
    requires_auth = True
    cors_allowed = False

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    def _service(self) -> VoiceGreetingService | None:
        service = voice_greeting_service(self._hass)
        if service is None:
            return None
        from .climate_api import DATA_CLIMATE_VIEWS

        if DATA_CLIMATE_VIEWS not in self._hass.data.get(DOMAIN, {}):
            return None
        return service

    def _unavailable(self) -> Any:
        return self.json_message(
            "The HausmanHub voice API is unavailable.",
            HTTPStatus.SERVICE_UNAVAILABLE,
            headers=NO_STORE_HEADERS,
        )


class VoiceGreetingView(_VoiceViewBase):
    """Read and atomically replace the Yandex Station greeting settings."""

    url = VOICE_GREETING_PATH
    name = "api:hausman_hub:voice_greeting"

    async def get(self, request: Any) -> Any:
        if not _is_exact_request(request, VOICE_GREETING_PATH):
            return _not_found(self)
        if not _is_local_tablet_request(request):
            return _forbidden(self)
        service = self._service()
        if service is None:
            return self._unavailable()
        return self.json(service.config, headers=NO_STORE_HEADERS)

    async def put(self, request: Any) -> Any:
        if not _is_exact_request(request, VOICE_GREETING_PATH):
            return _not_found(self)
        if not _is_local_tablet_request(request):
            return _forbidden(self)
        service = self._service()
        if service is None:
            return self._unavailable()
        try:
            payload = await _request_json(request, maximum_bytes=16 * 1024)
            if not isinstance(payload, dict) or set(payload) != {
                "expectedRevision",
                "settings",
            }:
                raise VoiceGreetingViolation("voice greeting body is invalid")
            result = await service.async_replace(
                payload["expectedRevision"], payload["settings"]
            )
        except VoiceGreetingViolation as error:
            return self.json_message(
                "Настройки уже изменились на другом клиенте. Обновите данные."
                if error.stale
                else "Настройки приветствия заполнены неверно.",
                HTTPStatus.CONFLICT if error.stale else HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        return self.json(result, headers=NO_STORE_HEADERS)


class VoiceGreetingTestView(_VoiceViewBase):
    """Speak a test summary without changing settings or the home mode."""

    url = VOICE_GREETING_TEST_PATH
    name = "api:hausman_hub:voice_greeting_test"

    async def post(self, request: Any) -> Any:
        if not _is_exact_request(request, VOICE_GREETING_TEST_PATH):
            return _not_found(self)
        if not _is_local_tablet_request(request):
            return _forbidden(self)
        service = self._service()
        if service is None:
            return self._unavailable()
        try:
            payload = await _request_json(request, maximum_bytes=16 * 1024)
        except ValueError:
            return self.json_message(
                "Тело проверочного запуска заполнено неверно.",
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        receipt = await service.async_test(payload)
        if receipt["code"] == "invalid_request":
            return self.json_message(
                receipt["detail"],
                HTTPStatus.BAD_REQUEST,
                headers=NO_STORE_HEADERS,
            )
        return self.json(
            receipt,
            status_code=HTTPStatus.ACCEPTED,
            headers=NO_STORE_HEADERS,
        )
