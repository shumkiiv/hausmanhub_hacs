"""Pure scheduling math for scenario time/sunrise/sunset triggers.

No Home Assistant imports: the HA clock adapter lives in
``custom_components/hausman_hub/scenario_schedule.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

from ..domain.scenarios import Scenario, ScenarioTriggerType, _offset_minutes

UPCOMING_HORIZON = timedelta(hours=48)


@dataclass(frozen=True)
class ScheduledRun:
    """One concrete upcoming run of one scenario trigger."""

    scenario_id: str
    scenario_title: str
    trigger_id: str
    trigger_type: str
    run_at: datetime

    @property
    def skip_key(self) -> str:
        return skip_key_for(self.scenario_id, self.trigger_id, self.run_at.date().isoformat())


def skip_key_for(scenario_id: str, trigger_id: str, day: str) -> str:
    """Identify one scheduled occurrence: scenario, trigger and local day."""

    return f"{scenario_id}|{trigger_id}|{day}"


def prune_skip_keys(keys: Iterable[str], today_iso: str) -> set[str]:
    """Drop skips for past days; a consumed or stale skip must not linger."""

    return {key for key in keys if key.rsplit("|", 1)[-1] >= today_iso}


def _next_clock_run(value: object, now: datetime) -> datetime | None:
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        return None
    try:
        hour = int(value[:2])
        minute = int(value[3:])
    except ValueError:
        return None
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def _next_sun_run(
    trigger_type: ScenarioTriggerType,
    value: object,
    next_sunrise: datetime | None,
    next_sunset: datetime | None,
    now: datetime,
) -> datetime | None:
    base = next_sunrise if trigger_type is ScenarioTriggerType.SUNRISE else next_sunset
    if base is None:
        return None
    offset = timedelta(minutes=_offset_minutes(value, f"{trigger_type.value} trigger offset"))
    candidate = base + offset
    if now.tzinfo is not None:
        candidate = candidate.astimezone(now.tzinfo)
    if candidate <= now:
        return None
    return candidate


def compute_upcoming_runs(
    scenarios: Iterable[Scenario],
    now: datetime,
    next_sunrise: datetime | None,
    next_sunset: datetime | None,
    skipped: set[str] | frozenset[str] = frozenset(),
    horizon: timedelta = UPCOMING_HORIZON,
) -> list[ScheduledRun]:
    """Next run per enabled scenario trigger, minus cancelled occurrences."""

    runs: list[ScheduledRun] = []
    for scenario in scenarios:
        if not scenario.enabled:
            continue
        for trigger in scenario.definition.triggers:
            if trigger.type is ScenarioTriggerType.TIME:
                run_at = _next_clock_run(trigger.value, now)
            elif trigger.type in (
                ScenarioTriggerType.SUNRISE,
                ScenarioTriggerType.SUNSET,
            ):
                run_at = _next_sun_run(
                    trigger.type, trigger.value, next_sunrise, next_sunset, now
                )
            else:
                continue
            if run_at is None or run_at - now > horizon:
                continue
            scheduled = ScheduledRun(
                scenario_id=scenario.id,
                scenario_title=scenario.title,
                trigger_id=trigger.id,
                trigger_type=trigger.type.value,
                run_at=run_at,
            )
            if scheduled.skip_key in skipped:
                continue
            runs.append(scheduled)
    runs.sort(key=lambda run: run.run_at)
    return runs
