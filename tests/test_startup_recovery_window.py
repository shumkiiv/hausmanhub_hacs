"""Bounded startup recovery window: arithmetic and lifecycle through ports."""

import inspect

import pytest

from custom_components.hausman_hub.application.startup_recovery_window import (
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_MAX_PASSES,
    StartupRecoveryWindow,
    new_window_state,
)

pytestmark = pytest.mark.asyncio


class FakeClock:
    def __init__(self, start=1_000_000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeScheduler:
    def __init__(self, clock):
        self.clock = clock
        self.timers = []  # dicts: delay, action, cancelled, fired
        self.cancel_calls = []

    def schedule(self, delay_s, action):
        timer = {"delay": delay_s, "action": action,
                 "cancelled": False, "fired": False}
        self.timers.append(timer)
        return lambda: self._cancel(timer)

    def _cancel(self, timer):
        timer["cancelled"] = True
        self.cancel_calls.append(timer)

    def active(self):
        return [t for t in self.timers if not t["cancelled"] and not t["fired"]]

    def fire_next(self):
        pending = [t for t in self.timers if not t["cancelled"] and not t["fired"]]
        assert pending, "no pending timer to fire"
        timer = pending[0]
        timer["fired"] = True
        self.clock.advance(timer["delay"])
        timer["action"]()
        return timer


class FakeTasks:
    def __init__(self):
        self.handles = []
        self.cancel_calls = 0

    def spawn(self, factory):
        # Home Assistant calls the zero-arg factory immediately to obtain
        # the coroutine; mirror that contract.
        result = factory()
        if inspect.iscoroutine(result):
            result.close()
        handle = {"done": False, "cancelled": False, "factory": factory,
                  "result": result}
        self.handles.append(handle)
        return handle

    def cancel(self, handle):
        handle["cancelled"] = True
        self.cancel_calls += 1

    @staticmethod
    def is_done(handle):
        return handle["done"]


def make_window(clock, scheduler, tasks, state=None, **kwargs):
    runs = []
    kwargs.setdefault("window_s", DEFAULT_MAX_PASSES * DEFAULT_INTERVAL_SECONDS * 4)

    def action():
        runs.append(clock())
        return None

    window = StartupRecoveryWindow(
        state if state is not None else new_window_state(),
        now=clock,
        schedule_timer=scheduler.schedule,
        spawn=tasks.spawn,
        cancel=tasks.cancel,
        is_done=FakeTasks.is_done,
        action=action,
        **kwargs,
    )
    return window, runs


async def test_first_pass_not_earlier_than_60_seconds():
    clock = FakeClock()
    scheduler, tasks = FakeScheduler(clock), FakeTasks()
    window, runs = make_window(clock, scheduler, tasks)
    window.on_started()
    assert scheduler.active() and scheduler.active()[0]["delay"] == 60.0
    # Firing earlier must not happen: the scheduler only fires the armed delay.
    scheduler.fire_next()
    assert runs and len(runs) == 1
    assert window.state["passes"] == 1
    assert window.state["next_ok"] == clock.now + 300.0


async def test_interval_spacing_and_pass_cap():
    clock = FakeClock()
    scheduler, tasks = FakeScheduler(clock), FakeTasks()
    window, runs = make_window(clock, scheduler, tasks)
    window.on_started()
    for _ in range(DEFAULT_MAX_PASSES):
        for handle in tasks.handles:
            handle["done"] = True
        scheduler.fire_next()
    assert window.state["passes"] == DEFAULT_MAX_PASSES
    assert len(runs) == DEFAULT_MAX_PASSES
    # The window is exhausted: no timer remains, further fires do nothing.
    assert not scheduler.active()
    passes_before = window.state["passes"]
    window.on_started()
    assert not scheduler.active()
    assert window.state["passes"] == passes_before


async def test_hard_deadline_stops_passes():
    clock = FakeClock()
    scheduler, tasks = FakeScheduler(clock), FakeTasks()
    window, runs = make_window(clock, scheduler, tasks, window_s=100.0)
    window.on_started()
    scheduler.fire_next()          # t=60: first pass
    assert len(runs) == 1
    scheduler.fire_next()          # armed min(300, deadline-now) -> t=100: deadline
    assert len(runs) == 1
    assert not window.is_open()
    assert not scheduler.active()


async def test_reload_preserves_window_and_interval():
    clock = FakeClock()
    scheduler1, tasks1 = FakeScheduler(clock), FakeTasks()
    state = new_window_state()
    window1, runs1 = make_window(clock, scheduler1, tasks1, state=state)
    window1.on_started()
    scheduler1.fire_next()         # pass 1 at t=60; next_ok = t+300
    assert state["passes"] == 1 and state["deadline"] is not None
    window1.close()

    clock.advance(100.0)           # reload 40s after the first pass
    scheduler2, tasks2 = FakeScheduler(clock), FakeTasks()
    window2, runs2 = make_window(clock, scheduler2, tasks2, state=state)
    window2.on_started()
    # The reloaded timer waits for the shared interval guard, not a fresh 60s.
    assert scheduler2.active()[0]["delay"] == 200.0
    scheduler2.fire_next()
    assert len(runs2) == 1 and state["passes"] == 2
    assert state["deadline"] == window1.state["deadline"]


async def test_close_cancels_timer_and_task_late_callbacks_safe():
    clock = FakeClock()
    scheduler, tasks = FakeScheduler(clock), FakeTasks()
    window, runs = make_window(clock, scheduler, tasks)
    window.on_started()
    scheduler.fire_next()          # pass spawned, next timer armed
    assert len(tasks.handles) == 1
    assert len(scheduler.active()) == 1
    armed_timer = scheduler.active()[0]
    window.close()
    assert not scheduler.active()          # no timer left armed
    assert armed_timer["cancelled"] is True
    assert tasks.cancel_calls == 1 and tasks.handles[0]["cancelled"]
    # A late timer callback after close must do nothing.
    pending = [t for t in scheduler.timers if not t["fired"]]
    for timer in pending:
        timer["action"]()
    assert not scheduler.active()
    assert len(runs) == 1
    assert window.state["passes"] == 1


async def test_repeated_start_does_not_create_second_timer():
    clock = FakeClock()
    scheduler, tasks = FakeScheduler(clock), FakeTasks()
    window, runs = make_window(clock, scheduler, tasks)
    window.on_started()
    window.on_started()
    assert len(scheduler.active()) == 1


async def test_long_pass_never_overlaps_next():
    clock = FakeClock()
    scheduler, tasks = FakeScheduler(clock), FakeTasks()
    window, runs = make_window(clock, scheduler, tasks)
    window.on_started()
    scheduler.fire_next()          # pass 1 spawned, still running (done=False)
    assert len(runs) == 1
    scheduler.fire_next()          # 300s later: previous pass still running
    assert len(runs) == 1          # no overlap, no extra pass consumed
    tasks.handles[0]["done"] = True
    scheduler.fire_next()
    assert len(runs) == 2
    assert window.state["passes"] == 2


async def test_interval_guard_blocks_early_fire_without_consuming_pass():
    clock = FakeClock()
    scheduler, tasks = FakeScheduler(clock), FakeTasks()
    state = new_window_state()
    window, runs = make_window(clock, scheduler, tasks, state=state)
    window.on_started()
    scheduler.fire_next()
    assert state["passes"] == 1
    window.close()
    for handle in tasks.handles:
        handle["done"] = True
    # Simulate a reload firing before the shared next_ok moment.
    state["next_ok"] = clock() + 250.0
    window2, runs2 = make_window(clock, scheduler, tasks, state=state)
    window2.on_started()
    armed = scheduler.active()[-1]
    assert armed["delay"] == 250.0
    timer = scheduler.fire_next()
    assert len(runs2) == 1         # ran exactly at the allowed moment
    assert state["passes"] == 2


async def test_window_module_has_no_mqtt_dependency():
    """The pure window never touches MQTT; the edge owns the short wait."""
    import inspect

    from custom_components.hausman_hub.application import (
        startup_recovery_window as window_module,
    )

    source = inspect.getsource(window_module)
    assert "mqtt" not in source.lower()


# --- HA boundary: timer callbacks receive the fire time -------------------


def _extract_edge_timer_factory():
    """Load the REAL _schedule_startup_recovery_timer from __init__.py.

    Home Assistant is not installed locally, so the single function
    definition is extracted with AST and executed against fakes that
    mirror the HA contract (async_call_later, callback decorator, hass).
    """
    import ast
    import pathlib

    init_path = pathlib.Path(__file__).resolve().parents[1] / (
        "custom_components/hausman_hub/__init__.py"
    )
    tree = ast.parse(init_path.read_text(encoding="utf-8"))
    funcs = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "_schedule_startup_recovery_timer"
    ]
    assert len(funcs) == 1, "exactly one timer adapter expected in __init__.py"
    module_ast = ast.Module(body=[funcs[0]], type_ignores=[])
    namespace = {
        "hass": None,                   # closure name; fakes ignore it
        "async_call_later": None,       # replaced per test
        "_startup_recovery_callback": lambda fn: fn,
    }
    exec(compile(module_ast, str(init_path), "exec"), namespace)
    return namespace


async def test_ha_timer_adapter_survives_time_argument_and_close():
    namespace = _extract_edge_timer_factory()

    stored = []
    cancels = []

    def fake_async_call_later(hass, delay_s, callback):
        entry = {"delay": delay_s, "callback": callback, "cancelled": False}
        stored.append(entry)

        def cancel():
            entry["cancelled"] = True
            cancels.append(entry)

        return cancel

    namespace["async_call_later"] = fake_async_call_later

    runs = []

    def action():
        runs.append(1)
        return None

    schedule = namespace["_schedule_startup_recovery_timer"]
    cancel_handle = schedule(60.0, action)
    assert stored and stored[0]["delay"] == 60.0

    # Home Assistant passes the fire time; the wrapped action must run
    # exactly once and must not raise TypeError.
    stored[0]["callback"](object())
    assert len(runs) == 1

    # Wiring check on the REAL window: its schedule_timer port is this
    # adapter, so the whole chain works with HA-shaped callbacks.
    clock = FakeClock()
    state = new_window_state()
    window, _ = make_window(clock, FakeScheduler(clock), FakeTasks(),
                            state=state)
    window._schedule_timer = lambda delay_s, act: schedule(None, act)
    window.on_started()
    ha_entry = stored[-1]
    ha_entry["callback"](object())
    assert state["passes"] == 1
    assert not ha_entry["cancelled"]

    window.close()
    assert any(e["cancelled"] for e in stored), "close cancels the HA timer"

    # A late HA callback after close must do nothing at all.
    passes_before = state["passes"]
    for entry in stored:
        if not entry["cancelled"]:
            entry["callback"](object())
    assert state["passes"] == passes_before
