"""Atomic-enough explicit return from a manual light choice to automation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable

from .manual_light_authority import ManualLightAuthority


class ManualLightAutoReturn:
    """Coordinate the two durable journals without issuing a device command."""

    def __init__(self, authority: ManualLightAuthority) -> None:
        self._authority = authority

    async def async_return(
        self,
        entity_ids: Iterable[str],
        *,
        room_history_is_releasable: Callable[[], bool],
        release_room_history: Callable[[], tuple[str, ...] | None],
        persist_room_history: Callable[[], Awaitable[bool]],
    ) -> tuple[str, ...]:
        """Release a complete room only after both journals have persisted.

        A failure after the authority write deliberately leaves the authority
        unhealthy.  This preserves the deny boundary instead of pretending
        that automatic ownership was restored.
        """

        if not room_history_is_releasable():
            return ()
        token = await self._authority.async_begin_explicit_return(entity_ids)
        if token is None:
            return ()
        try:
            released = release_room_history()
            persisted = released is not None and await persist_room_history()
        except BaseException:
            self._authority.async_finish_explicit_return(token, persisted=False)
            raise
        self._authority.async_finish_explicit_return(token, persisted=persisted)
        return released if persisted else ()
