"""Process-level window for the read-only startup lighting recovery pass.

Pure scheduling state with no Home Assistant imports: monotonic time,
timer scheduling, task spawning and cancellation arrive as injected ports.
The state dict is owned by the caller and is meant to be shared across
integration reloads of the same Home Assistant process, so a reload can
neither reopen a fresh window nor shorten the interval between passes.

The window owns only arithmetic and its own timer lifecycle. It never
touches lighting state, never dispatches anything and never releases
authority; the pass action itself is a caller-provided function.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

DEFAULT_FIRST_DELAY_SECONDS = 60.0
DEFAULT_INTERVAL_SECONDS = 300.0
DEFAULT_WINDOW_SECONDS = 3600.0
DEFAULT_MAX_PASSES = 12


def new_window_state() -> dict[str, Any]:
    """Fresh shared window state (deadline/passes/next allowed run)."""
    return {"deadline": None, "passes": 0, "next_ok": None}


class StartupRecoveryWindow:
    """Bounded pass window: one shot per interval, hard deadline, no reload resets."""

    def __init__(
        self,
        state: dict[str, Any],
        *,
        now: Callable[[], float],
        schedule_timer: Callable[[float, Callable[[], None]], Callable[[], Any]],
        spawn: Callable[[Callable[[], Any]], Any],
        cancel: Callable[[Any], None],
        is_done: Callable[[Any], bool],
        action: Callable[[], Any],
        first_delay_s: float = DEFAULT_FIRST_DELAY_SECONDS,
        interval_s: float = DEFAULT_INTERVAL_SECONDS,
        window_s: float = DEFAULT_WINDOW_SECONDS,
        max_passes: int = DEFAULT_MAX_PASSES,
    ) -> None:
        self._state = state
        self._now = now
        self._schedule_timer = schedule_timer
        self._spawn = spawn
        self._cancel = cancel
        self._is_done = is_done
        self._action = action
        self._first_delay_s = first_delay_s
        self._interval_s = interval_s
        self._window_s = window_s
        self._max_passes = max_passes
        self._closed = False
        self._timer: dict[str, Callable[[], Any] | None] = {"cancel": None}
        self._task: dict[str, Any] = {"handle": None}
        self.state.setdefault("deadline", None)
        self.state.setdefault("passes", 0)
        self.state.setdefault("next_ok", None)

    @property
    def state(self) -> dict[str, Any]:
        return self._state

    def is_open(self) -> bool:
        """False after close, before start, past the deadline or budget."""
        if self._closed:
            return False
        if self._state["deadline"] is None:
            return False
        if self._state["passes"] >= self._max_passes:
            return False
        return self._now() < self._state["deadline"]

    def may_run_now(self) -> bool:
        """Open and the interval guard (shared across reloads) allows it."""
        if not self.is_open():
            return False
        next_ok = self._state["next_ok"]
        return next_ok is None or self._now() >= next_ok

    def on_started(self) -> None:
        """Home Assistant started: open the window once, arm the first timer."""
        if self._closed:
            return
        if self._state["deadline"] is None:
            self._state["deadline"] = self._now() + self._window_s
        self._arm(self._first_delay_s)

    def close(self) -> None:
        """Stop this connection: cancel timer and in-flight task, ignore later calls.

        The shared window state is deliberately NOT reset: a reload must not
        reopen a fresh window or shorten the interval.
        """
        self._closed = True
        timer_cancel = self._timer["cancel"]
        self._timer["cancel"] = None
        if timer_cancel is not None:
            try:
                timer_cancel()
            except Exception:  # noqa: BLE001 - shutdown must never raise
                pass
        handle = self._task["handle"]
        self._task["handle"] = None
        if handle is not None and not self._is_done(handle):
            try:
                self._cancel(handle)
            except Exception:  # noqa: BLE001 - shutdown must never raise
                pass

    def _arm(self, delay_s: float) -> None:
        if self._closed or self._timer["cancel"] is not None:
            return
        if not self.is_open():
            return
        now = self._now()
        delay = delay_s
        next_ok = self._state["next_ok"]
        if next_ok is not None:
            delay = max(delay, next_ok - now)
        deadline = self._state["deadline"]
        delay = min(delay, max(0.0, deadline - now))
        if delay <= 0 and not self.may_run_now():
            return
        self._timer["cancel"] = self._schedule_timer(
            max(0.0, delay), self._fire,
        )

    def _fire(self) -> None:
        self._timer["cancel"] = None
        if self._closed:
            return
        if not self.is_open():
            return
        if not self.may_run_now():
            # Interval guard (e.g. a reload landed mid-window): retry at the
            # next allowed moment without consuming a pass.
            self._arm(self._interval_s)
            return
        handle = self._task["handle"]
        if handle is not None and not self._is_done(handle):
            # A long pass must never overlap the next one; retry later
            # without consuming the pass budget.
            self._arm(self._interval_s)
            return
        self._state["passes"] += 1
        self._state["next_ok"] = self._now() + self._interval_s
        self._task["handle"] = self._spawn(self._run_pass)
        self._arm(self._interval_s)

    def _run_pass(self) -> Any:
        return self._action()
