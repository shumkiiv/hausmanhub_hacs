"""External UTC HA scheduling port, not a substitute Hausman evaluator.

Supports the actual registered hour/minute/second calendar fields, intervals
and one-shot callbacks. OS time is untouched. Real HA/DST are not simulated.
"""

import inspect
from datetime import timedelta


def calendar_matches(fields, moment):
    if set(fields) - {"hour", "minute", "second"}:
        raise AssertionError("unsupported HA calendar field")
    for name, expected in fields.items():
        value = getattr(moment, name)
        if expected is None or expected == "*":
            continue
        if isinstance(expected, int):
            expected = (expected,)
        if value not in expected:
            return False
    return True


async def advance(hass, target):
    if target < hass.now:
        raise AssertionError("virtual time cannot move backwards")
    counts = {"calendar": 0, "interval": 0, "later": 0, "roomCalendar": 0}
    second = hass.now.replace(microsecond=0) + timedelta(seconds=1)
    while second <= target:
        hass.now = second
        delivered = False
        for record in tuple(hass.schedules):
            if not record.active:
                continue
            if record.kind == "calendar":
                due = calendar_matches(record.fields, second)
            else:
                due = record.due <= second
            if not due:
                continue
            delivered = True
            counts[record.kind] += 1
            if record.kind == "calendar" and record.callback.__name__ == "_clock_event":
                counts["roomCalendar"] += 1
            if record.kind == "later":
                record.active = False
            elif record.kind == "interval":
                if record.period <= timedelta(0):
                    raise AssertionError("invalid HA interval")
                record.due += record.period
            result = record.callback(second)
            if inspect.isawaitable(result):
                hass.async_create_task(result)
        if delivered:
            await hass.drain()
        second += timedelta(seconds=1)
    if hass.now < target:
        hass.now = target
    return counts
