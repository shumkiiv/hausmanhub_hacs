"""Atomic-enough explicit return from a manual light choice to automation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

from .manual_light_authority import ManualLightAuthority
from .lighting_return_intent import ManualLightReturnIntentChanged


@dataclass(frozen=True, slots=True)
class ManualLightReturnResult:
    outcome: str
    released: tuple[str, ...] = ()
    operation_id: int | None = None


class ManualLightAutoReturn:
    """Coordinate durable manual-control participants without a device command."""

    def __init__(self, authority: ManualLightAuthority) -> None:
        self._authority = authority

    async def async_return(
        self,
        entity_ids: Iterable[str],
        *,
        room_history_is_releasable: Callable[[], bool],
        release_room_history: Callable[[], tuple[str, ...] | None],
        persist_room_history: Callable[[], Awaitable[bool]],
        release_other_histories: Iterable[Callable[[], Awaitable[bool]]] = (),
        all_manual: bool = False,
        config_version: int = 1,
        observations_current: Callable[[], bool] | None = None,
        commit_is_current: Callable[[], bool] | None = None,
        stage_room_handoff: Callable[[int], None] | None = None,
        release_is_current: Callable[[], bool] | None = None,
    ) -> ManualLightReturnResult:
        """Release a complete room only after every participant has persisted.

        A failure after the authority write deliberately leaves the authority
        unhealthy.  This preserves the deny boundary instead of pretending
        that automatic ownership was restored.
        """

        if not room_history_is_releasable():
            return ManualLightReturnResult("blocked")
        token = await self._authority.async_begin_explicit_return(
            entity_ids, all_manual=all_manual, config_version=config_version,
            observations_current=observations_current,
        )
        if token is None:
            return ManualLightReturnResult("blocked")

        def require_current() -> None:
            if release_is_current is not None and not release_is_current():
                raise ManualLightReturnIntentChanged

        try:
            require_current()
            released = release_room_history()
            if released is not None and stage_room_handoff is not None:
                stage_room_handoff(token.operation_id)
            persisted = released is not None and await persist_room_history()
            for release in release_other_histories:
                if not persisted:
                    break
                require_current()
                persisted = await release()
                if persisted:
                    require_current()
        except ManualLightReturnIntentChanged:
            await self._authority.async_abort_explicit_return(token)
            return ManualLightReturnResult("incomplete", (), token.operation_id)
        except BaseException:
            await self._authority.async_finish_explicit_return(token, persisted=False)
            raise
        completed = await self._authority.async_finish_explicit_return(
            token, persisted=persisted,
            observations_current=commit_is_current or observations_current,
        )
        if completed:
            return ManualLightReturnResult("returned", released or (), token.operation_id)
        outcome = "incomplete" if self._authority.barrier_reason else "blocked"
        return ManualLightReturnResult(outcome, (), token.operation_id)
