"""Room lighting editor service: device catalog and safe draft preview.

The service only reads one validated configuration and plain snapshot data. It
never receives a storage handle or an executor, so building a catalog or
previewing a draft cannot persist data, change ownership or send a physical
command. All filesystem and Home Assistant access stays in the adapter.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from ..domain.room_lighting import (
    RoomLightingConfig,
    RoomLightingViolation,
    config_from_payload,
)
from ..domain.room_lighting_engine import (
    RoomLightingContext,
    SkipReason,
    evaluate_room_lighting,
)

EDITOR_CATALOG_CONTRACT = "hausman-hub-room-lighting-editor-catalog"
ROOM_PREVIEW_CONTRACT = "hausman-hub-room-lighting-preview"

CANONICAL_SECTIONS = (
    "overview",
    "lights",
    "inputs",
    "periods",
    "conditions",
    "manual-away",
    "review-save",
)

_TYPE_LABELS = {
    "light": "Светильник",
    "sensor": "Датчик",
    "switch": "Выключатель",
}

_SECTION_BY_MARKER: tuple[tuple[str, str], ...] = (
    ("sensor", "inputs"),
    ("power switch", "inputs"),
    ("wireless switch", "inputs"),
    ("illumination", "conditions"),
    ("dimming", "conditions"),
    ("timer", "conditions"),
    ("schedule", "periods"),
    ("segment", "periods"),
    ("manual off protection", "manual-away"),
    ("away", "manual-away"),
    ("light", "lights"),
    ("device", "lights"),
    ("config", "overview"),
)

_SKIP_TEXT: dict[str, str] = {
    SkipReason.MANUAL_OWNERSHIP.value: "свет уже включён вручную, автоматика не вмешивается",
    SkipReason.MANUAL_MODE.value: "цель отключена от автоматики",
    SkipReason.MANUAL_PEER.value: "взаимозаменяемый источник уже включён вручную",
    SkipReason.MANUAL_PROTECTION.value: "действует защита после ручного выключения",
    SkipReason.SENSOR_UNKNOWN.value: "датчик присутствия недоступен",
    SkipReason.SENSOR_STALE.value: "показания датчика устарели",
    SkipReason.ABSENCE_UNPROVEN.value: "отсутствие не подтверждено",
    SkipReason.LUX_FAIL_CLOSED.value: "датчик освещённости недоступен",
    SkipReason.IDEMPOTENT.value: "состояние уже соответствует решению",
    SkipReason.NIGHT_MINIMUM.value: "ночной минимум ещё не истёк",
    SkipReason.NO_SCHEDULE.value: "для цели нет активного периода",
    SkipReason.UNOBSERVED.value: "период наблюдения начинается заново",
    SkipReason.NO_AUTO_OWNERSHIP.value: "нет подтверждённого владения автоматикой",
}


@dataclass(frozen=True, slots=True)
class EditorDevice:
    """One physical entity the editor may offer for a room."""

    entity_id: str
    kind: str
    user_label: str | None = None
    physical_device_label: str | None = None
    channel_label: str | None = None
    friendly_name: str | None = None
    available: bool = True
    supports_brightness: bool = False
    supports_color_temperature: bool = False
    gestures: tuple[str, ...] = ()


class RoomLightingEditorService:
    """Build the editor catalog and preview a draft without any side effect."""

    def __init__(
        self,
        *,
        configs: Mapping[str, RoomLightingConfig],
        devices: Callable[[str], Sequence[EditorDevice]],
        context: RoomLightingContext,
    ) -> None:
        self._configs = dict(configs)
        self._devices = devices
        self._context = context

    def catalog(self, room_id: str) -> dict[str, object]:
        config = self._config(room_id)
        snapshot = tuple(self._devices(room_id))
        physical_counts = Counter(
            device.physical_device_label
            for device in snapshot
            if device.physical_device_label
        )
        used_labels: set[str] = set()
        devices: list[dict[str, object]] = []
        for device in snapshot:
            label = _catalog_label(device, physical_counts)
            label = _unique_label(label, used_labels, device)
            used_labels.add(label)
            devices.append(
                {
                    "id": _device_id(device.entity_id),
                    "label": label,
                    "physicalDeviceLabel": device.physical_device_label
                    or label,
                    "channelLabel": device.channel_label,
                    "kind": device.kind,
                    "available": device.available,
                    "supportsBrightness": device.supports_brightness,
                    "supportsColorTemperature": device.supports_color_temperature,
                    "gestures": list(device.gestures),
                }
            )
        del config
        return {
            "contract": {"name": EDITOR_CATALOG_CONTRACT, "version": 1},
            "roomId": room_id,
            "devices": devices,
        }

    def preview(self, room_id: str, draft: object) -> dict[str, object]:
        self._config(room_id)
        try:
            config = config_from_payload(draft) if not isinstance(
                draft, RoomLightingConfig
            ) else draft
        except RoomLightingViolation as error:
            message = str(error)
            return _preview_payload(
                safe=False,
                summary="Предпросмотр нашёл ошибку в черновике.",
                steps=(),
                issues=(_issue_for_message(message),),
            )
        if config.room_id != room_id:
            return _preview_payload(
                safe=False,
                summary="Черновик относится к другой комнате.",
                steps=(),
                issues=(
                    {
                        "section": "overview",
                        "code": "room_mismatch",
                        "message": "Комната черновика не совпадает с адресом запроса.",
                        "field": "roomId",
                    },
                ),
            )
        decision = evaluate_room_lighting(config, self._context)
        steps: list[dict[str, object]] = [
            {
                "section": "overview",
                "title": "Событие",
                "detail": "Проверяем черновик по текущему состоянию комнаты.",
                "status": "ok",
            }
        ]
        for target in config.devices.light_targets:
            target_decision = next(
                (
                    candidate
                    for candidate in decision.targets
                    if candidate.target_id == target.id
                ),
                None,
            )
            if target_decision is None:
                continue
            if target_decision.commands:
                actions = ", ".join(
                    command.action.value for command in target_decision.commands
                )
                steps.append(
                    {
                        "section": "lights",
                        "title": target.name,
                        "detail": f"Автоматика выполнит: {actions}.",
                        "status": "ok",
                    }
                )
                continue
            reason = (
                target_decision.skips[0].reason.value
                if target_decision.skips
                else SkipReason.IDEMPOTENT.value
            )
            steps.append(
                {
                    "section": "lights",
                    "title": target.name,
                    "detail": (
                        "Действие пропущено. Причина: "
                        f"{reason} — {_SKIP_TEXT.get(reason, reason)}."
                    ),
                    "status": "skipped",
                }
            )
        steps.append(
            {
                "section": "review-save",
                "title": "Предпросмотр",
                "detail": (
                    "Черновик безопасен, команды устройству не отправляются."
                ),
                "status": "ok",
            }
        )
        return _preview_payload(
            safe=True,
            summary=(
                "Черновик проверен. Предпросмотр не сохраняет настройку и не "
                "отправляет команд устройству."
            ),
            steps=tuple(steps),
            issues=(),
        )

    def _config(self, room_id: str) -> RoomLightingConfig:
        config = self._configs.get(room_id)
        if config is None:
            raise RoomLightingViolation("room lighting configuration not found")
        return config


def _device_id(entity_id: str) -> str:
    """Derive a stable, contract-safe id from one entity id."""

    candidate = entity_id.split(".", 1)[-1].lower()
    cleaned = "".join(
        character if character.isalnum() or character in "_-" else "_"
        for character in candidate
    )
    cleaned = cleaned.lstrip("_") or "device"
    if not cleaned[0].isalpha():
        cleaned = f"device_{cleaned}"
    return cleaned[:64]


def _catalog_label(
    device: EditorDevice, physical_counts: Counter[str | None]
) -> str:
    """Apply the documented readable-name order."""

    base: str
    if device.user_label:
        base = device.user_label
    elif device.physical_device_label and device.channel_label:
        base = f"{device.physical_device_label} · {device.channel_label}"
    elif device.physical_device_label:
        base = device.physical_device_label
    elif device.friendly_name:
        base = device.friendly_name
    else:
        base = _TYPE_LABELS.get(device.kind, "Устройство")
    if (
        device.channel_label
        and device.physical_device_label
        and physical_counts.get(device.physical_device_label, 0) > 1
        and " · " not in base
    ):
        base = f"{base} · {device.channel_label}"
    return base


def _unique_label(
    label: str, used_labels: set[str], device: EditorDevice
) -> str:
    if label not in used_labels:
        return label
    suffix = device.channel_label or _device_id(device.entity_id)
    candidate = f"{label} · {suffix}"
    counter = 2
    while candidate in used_labels:
        candidate = f"{label} · {suffix} {counter}"
        counter += 1
    return candidate


def _issue_for_message(message: str) -> dict[str, object]:
    normalized = message.lower()
    section = "overview"
    for marker, candidate in _SECTION_BY_MARKER:
        if marker in normalized:
            section = candidate
            break
    code = "invalid_draft"
    for marker in ("sensor", "schedule", "illumination", "dimming", "timer", "away"):
        if marker in normalized:
            code = f"{marker}_invalid"
            break
    return {
        "section": section,
        "code": code,
        "message": message,
        "field": None,
    }


def _preview_payload(
    *,
    safe: bool,
    summary: str,
    steps: Sequence[Mapping[str, object]],
    issues: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    return {
        "contract": {"name": ROOM_PREVIEW_CONTRACT, "version": 1},
        "safe": safe,
        "summary": summary,
        "steps": [dict(step) for step in steps],
        "sectionIssues": [dict(issue) for issue in issues],
    }


__all__ = [
    "CANONICAL_SECTIONS",
    "EDITOR_CATALOG_CONTRACT",
    "EditorDevice",
    "ROOM_PREVIEW_CONTRACT",
    "RoomLightingEditorService",
]
