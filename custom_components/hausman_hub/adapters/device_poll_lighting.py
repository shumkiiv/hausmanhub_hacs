"""Adapter for periodic reads of the existing proven lighting channels only.

This does not start a task, discover devices, change ownership or issue SETs.
"""
from __future__ import annotations

from .lighting_mqtt_refresh import ALL_READ_CHANNELS


class DevicePollLightingAdapter:
    def __init__(self, refresher: object) -> None:
        self._refresher = refresher
        self.entities = frozenset(row[0] for row in ALL_READ_CHANNELS)

    async def poll(self, entity: str) -> bool:
        if entity not in self.entities:
            return False
        refresh = getattr(self._refresher, "refresh_confirmation", None)
        if not callable(refresh):
            return False
        active = True

        def current() -> bool:
            return active

        try:
            return await refresh(entity, current) is True
        except OSError:
            return False
        finally:
            active = False
