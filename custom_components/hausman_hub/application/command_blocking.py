"""Normalize internal command blockers for public clients."""

from __future__ import annotations

from collections.abc import Mapping


_PUBLIC_BLOCKS: Mapping[str, tuple[str, str]] = {
    "offline": (
        "Устройство или сервис не отвечает.",
        "check_device",
    ),
    "stale_evidence": (
        "Данные устарели и не подтверждают безопасное выполнение команды.",
        "refresh",
    ),
    "unsupported_action": (
        "Устройство не поддерживает эту команду.",
        "review_settings",
    ),
    "safety_policy": (
        "Команда заблокирована правилами безопасности.",
        "review_settings",
    ),
    "conflict": (
        "Состояние изменилось. Обновите данные и повторите команду.",
        "refresh",
    ),
    "dependency_unavailable": (
        "Сначала проверьте связанное устройство питания.",
        "check_device",
    ),
}

_INTERNAL_TO_PUBLIC = {
    "unavailable": "offline",
    "device_unavailable": "offline",
    "station_unavailable": "offline",
    "climate_state_stale": "stale_evidence",
    "state_stale": "stale_evidence",
    "evidence_not_ready": "stale_evidence",
    "climate_action_unsupported": "unsupported_action",
    "action_unsupported": "unsupported_action",
    "actions_unsupported": "unsupported_action",
    "capability_unavailable": "unsupported_action",
    "climate_disabled": "safety_policy",
    "climate_shadow_only": "safety_policy",
    "climate_authority_not_ready": "safety_policy",
    "climate_cooldown": "safety_policy",
    "bridge_disabled": "safety_policy",
    "shadow_only": "safety_policy",
    "authority_not_ready": "safety_policy",
    "conflict": "conflict",
    "revision_conflict": "conflict",
    "climate_operation_pending": "conflict",
    "operation_pending": "conflict",
    "climate_registry_mismatch": "conflict",
    "registry_mismatch": "conflict",
    "dependency_unavailable": "dependency_unavailable",
    "power_source_off": "dependency_unavailable",
    "power_source_unavailable": "dependency_unavailable",
}


def public_command_block(internal_reason: str) -> dict[str, str] | None:
    """Return a bounded public blocker, or None for non-command errors."""

    code = _INTERNAL_TO_PUBLIC.get(internal_reason)
    if code is None:
        return None
    message, recommended_action = _PUBLIC_BLOCKS[code]
    return {
        "code": code,
        "message": message,
        "recommendedAction": recommended_action,
    }
