"""Bounded, process-local capabilities retaining an original manual intent."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import re

RETURN_INTENT_TTL_SECONDS = 300
MAX_RETURN_INTENTS = 64
TOKEN_PATTERN = re.compile(r"[a-f0-9]{64}")


class ManualLightReturnIntentChanged(Exception):
    """A known stale intent, not a failed or ambiguous storage operation."""


@dataclass(frozen=True, slots=True)
class LightingReturnIntent:
    room_id: str
    actor_id: str
    issued_at: float
    expires_at: float
    configuration_version: int
    before_release: Callable[[], bool]
    during_release: Callable[[], bool]

    def current(self, now: float, *, releasing: bool = False) -> bool:
        return (self.issued_at <= now < self.expires_at
                and (self.during_release() if releasing else self.before_release()))


class LightingReturnIntents:
    """No IO: time and opaque ID creation are explicit input ports."""

    def __init__(self, clock: Callable[[], float], token_factory: Callable[[], str]) -> None:
        self.clock = clock
        self._token_factory = token_factory
        self._intents: dict[str, LightingReturnIntent] = {}

    def clear(self) -> None:
        self._intents.clear()

    def issue(
        self, room_id: str, actor_id: str, configuration_version: int,
        before_release: Callable[[], bool], during_release: Callable[[], bool],
    ) -> str | None:
        now = self.clock()
        self._intents = {key: value for key, value in self._intents.items()
                         if value.issued_at <= now < value.expires_at}
        if len(self._intents) >= MAX_RETURN_INTENTS or not actor_id or not before_release():
            return None
        token = self._token_factory()
        if (not isinstance(token, str) or TOKEN_PATTERN.fullmatch(token) is None
                or token in self._intents):
            return None
        self._intents[token] = LightingReturnIntent(
            room_id, actor_id, now, now + RETURN_INTENT_TTL_SECONDS,
            configuration_version, before_release, during_release,
        )
        return token

    def consume(self, token: object, room_id: str, actor_id: str) -> LightingReturnIntent | None:
        if not isinstance(token, str) or TOKEN_PATTERN.fullmatch(token) is None:
            return None
        intent = self._intents.get(token)
        if intent is None or intent.room_id != room_id or intent.actor_id != actor_id:
            return None
        # Spend even a stale matching attempt. Never retry against a newer snapshot.
        del self._intents[token]
        return intent if intent.current(self.clock()) else None


def records_match(
    original: Mapping[str, tuple[object, object]], current: Mapping[str, object],
    *, allow_removed: bool,
) -> bool:
    """Keep objects alive to reject equal replacements and identity reuse."""
    return ((allow_removed and current.keys() <= original.keys() or current.keys() == original.keys())
            and all(current[key] is original[key][0] and current[key] == original[key][1]
                    for key in current))
