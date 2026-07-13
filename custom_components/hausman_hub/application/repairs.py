"""Manual-only repair guidance for safe configuration review."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ManualRepairGuidance:
    """A user-facing explanation that cannot perform a repair itself."""

    category: str
    severity: str
    message: str


_GUIDANCE = {
    "missing_references": ManualRepairGuidance(
        category="missing_references",
        severity="warning",
        message="Проверьте сопоставление в безопасной конфигурации вручную.",
    ),
    "unsafe_mode": ManualRepairGuidance(
        category="unsafe_mode",
        severity="error",
        message="Выберите только режим чтения или shadow-проверки.",
    ),
    "unresolved_owner_contour": ManualRepairGuidance(
        category="unresolved_owner_contour",
        severity="error",
        message="Запросите у владельца контура явное назначение ответственности.",
    ),
    "stale_parity": ManualRepairGuidance(
        category="stale_parity",
        severity="warning",
        message="Обновите evidence отдельным read-only или shadow-процессом.",
    ),
    "redaction_failure": ManualRepairGuidance(
        category="redaction_failure",
        severity="critical",
        message="Остановите экспорт и проверьте маскировку данных вручную.",
    ),
}


def manual_guidance_for(category: str) -> ManualRepairGuidance:
    """Return a fixed explanation; unknown categories are never auto-repaired."""

    try:
        return _GUIDANCE[category]
    except KeyError as error:
        raise ValueError(f"unknown manual repair category: {category}") from error
