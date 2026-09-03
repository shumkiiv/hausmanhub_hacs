"""Attribute automatic power-source state changes to Hausman commands."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable, Literal


_CONTEXT_TTL_SECONDS = 30.0
_MAX_CONTEXTS = 128


@dataclass(frozen=True, slots=True)
class _AutomaticContext:
    entity_id: str
    expected_state: str
    expires_at: float


@dataclass(frozen=True, slots=True)
class ScenarioCommandAttribution:
    """Read-only provenance for a transition expected from a Hausman command."""

    origin: Literal["automatic", "manual"]
    entity_id: str
    expected_state: str
    request_id: str | None


class ScenarioCommandContextRegistry:
    """Keep a bounded set of HA contexts created by automatic relay commands."""

    def __init__(
        self,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        context_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._monotonic = monotonic
        self._context_factory = context_factory
        self._records: dict[str, _AutomaticContext] = {}

    def create(self, entity_id: str, expected_state: str) -> Any:
        """Create and register a context before an automatic service call."""

        if self._context_factory is None:
            from homeassistant.core import Context  # noqa: PLC0415

            context = Context()
        else:
            context = self._context_factory()
        context_id = getattr(context, "id", None)
        if not isinstance(context_id, str) or not context_id:
            raise RuntimeError("Home Assistant command context is unavailable")
        self._prune()
        if len(self._records) >= _MAX_CONTEXTS:
            oldest = next(iter(self._records))
            self._records.pop(oldest, None)
        self._records[context_id] = _AutomaticContext(
            entity_id=entity_id,
            expected_state=expected_state,
            expires_at=self._monotonic() + _CONTEXT_TTL_SECONDS,
        )
        return context

    def consume(self, context: object | None, entity_id: str, state: object) -> bool:
        """Return true once for the matching automatic state transition."""

        attribution = self.match(context, entity_id, state)
        if attribution is None:
            return False
        if attribution.request_id is not None:
            self._records.pop(attribution.request_id, None)
        return True

    def match(
        self, context: object | None, entity_id: str, state: object
    ) -> ScenarioCommandAttribution | None:
        """Look up automatic provenance without consuming it."""

        self._prune()
        expected_state = str(state)
        for candidate in (
            getattr(context, "id", None),
            getattr(context, "parent_id", None),
        ):
            if not isinstance(candidate, str):
                continue
            record = self._records.get(candidate)
            if record is None:
                continue
            if (
                record.entity_id == entity_id
                and record.expected_state == expected_state
            ):
                return ScenarioCommandAttribution(
                    origin="automatic",
                    entity_id=record.entity_id,
                    expected_state=record.expected_state,
                    request_id=candidate,
                )
        return None

    def discard(self, context: object | None) -> None:
        """Forget a context when its service call failed synchronously."""

        context_id = getattr(context, "id", None)
        if isinstance(context_id, str):
            self._records.pop(context_id, None)

    def _prune(self) -> None:
        now = self._monotonic()
        for context_id, record in tuple(self._records.items()):
            if record.expires_at <= now:
                self._records.pop(context_id, None)
