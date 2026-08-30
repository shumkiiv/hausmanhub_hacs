"""Home Assistant Repairs adapter for lost delayed-light authority."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib

from .const import DOMAIN


def _issue_id(target_id: str) -> str:
    digest = hashlib.sha256(target_id.encode()).hexdigest()[:16]
    return f"light_safety_obligation_lost_{digest}"


class HomeAssistantLightSafetyIssueReporter:
    """Create guidance only, never repair or control a device."""

    def __init__(self, hass: object) -> None:
        self._hass = hass

    async def async_report_failure(self, record: Mapping[str, object]) -> None:
        from homeassistant.helpers import issue_registry as ir  # noqa: PLC0415

        target_id = str(record["targetId"])
        ir.async_create_issue(
            self._hass,
            DOMAIN,
            _issue_id(target_id),
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="light_safety_obligation_lost",
            translation_placeholders={
                "scenario_id": str(record["scenarioId"]),
            },
        )

    async def async_clear(self, target_id: str) -> None:
        from homeassistant.helpers import issue_registry as ir  # noqa: PLC0415

        ir.async_delete_issue(self._hass, DOMAIN, _issue_id(target_id))
