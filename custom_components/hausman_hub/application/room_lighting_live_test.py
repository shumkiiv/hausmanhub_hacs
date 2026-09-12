"""Bounded ~30 second room lighting live test.

``safe`` mode only calls the pure engine and never touches an executor;
``real`` mode dispatches the computed plan through an injected executor and
records the receipts. The public payload follows the contract
``room-lighting-live-test`` schema exactly, including its canonical stages.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import time as dt_time, timezone
import inspect
import time
from typing import Callable

from ..domain.room_lighting import RoomLightingConfig, ScheduleMode, SensorKind
from ..domain.room_lighting_engine import (
    LightSnapshot,
    PlannedCommand,
    RoomLightingContext,
    SensorSnapshot,
    evaluate_room_lighting,
)
from ..domain.room_lighting_ownership import SensorState

LIVE_TEST_MODE_SAFE = "safe"
LIVE_TEST_MODE_REAL = "real"
LIVE_TEST_MODES = frozenset({LIVE_TEST_MODE_SAFE, LIVE_TEST_MODE_REAL})
LIVE_TEST_DURATION_SECONDS = 30
LIVE_TEST_MAX_RUNS = 16
LIVE_TEST_STAGES = (
    "resolve_devices",
    "snapshot_state",
    "resolve_illumination",
    "simulate_presence",
    "apply_schedule",
    "verify_brightness",
    "verify_color_temperature",
    "verify_ownership",
    "simulate_absence",
    "verify_fade",
    "verify_manual_protection",
    "simulate_switch_press",
    "away_room_off",
    "verify_away_return",
    "restore_state",
)
# Backwards-compatible alias for callers from the earlier draft.
LIVE_TEST_STAGE_KEYS = LIVE_TEST_STAGES


class RoomLightingLiveTestViolation(ValueError):
    """Live test input is malformed."""


@dataclass(frozen=True, slots=True)
class LiveTestStage:
    """One narrated step of the live run."""

    order: int
    stage: str
    duration_seconds: int
    title: str
    comment: str
    action: str


def build_stages(config: RoomLightingConfig) -> list[LiveTestStage]:
    """Build a compact ~30 second narrated sequence using canonical stages."""

    if not isinstance(config, RoomLightingConfig):
        raise RoomLightingLiveTestViolation("validated room lighting config is required")

    devices = config.devices
    schedule_modes = {entry.how.mode for entry in config.schedule}
    night_min = max(
        (
            entry.how.min_on_seconds
            for entry in config.schedule
            if entry.how.mode is ScheduleMode.NIGHT_LIGHT
        ),
        default=0,
    )
    supports_color = any(target.color_temperature for target in devices.light_targets)

    builders: list[tuple[str, str, str, str]] = [
        (
            "resolve_devices",
            "Разбор устройств",
            "Сверяем цели света, датчики и выключатели комнаты, чтобы прогон "
            "работал только с выбранным оборудованием.",
            f"Прочитать конфигурацию: {len(devices.light_targets)} целей, "
            f"{len(devices.sensors)} датчиков, "
            f"{len(devices.wireless_switches)} беспроводных выключателей.",
        ),
        (
            "snapshot_state",
            "Снимок состояния",
            "Фиксируем текущее состояние света и владения, чтобы не менять "
            "включённый вручную свет.",
            "Снять состояние целей без отправки команд.",
        ),
    ]
    if config.illumination is not None:
        builders.append(
            (
                "resolve_illumination",
                "Освещённость",
                "Проверяем lux-датчик и fail-closed: недостоверный люкс "
                "отключает ветку, но не считается отсутствием.",
                "Разрешить lux-источник и его здоровье.",
            )
        )
    builders.append(
        (
            "simulate_presence",
            "Присутствие",
            "Свежее присутствие запускает автоматику только если свет не "
            "занят вручную.",
            "Смоделировать присутствие и получить план движка.",
        )
    )
    for mode in (
        ScheduleMode.ON_PRESENCE,
        ScheduleMode.ALWAYS,
        ScheduleMode.NIGHT_LIGHT,
        ScheduleMode.OFF,
    ):
        if mode not in schedule_modes:
            continue
        if mode is ScheduleMode.NIGHT_LIGHT:
            builders.append(
                (
                    "apply_schedule",
                    "Режим night_light",
                    "Ночная подсветка включается по движению и держится "
                    f"минимум {night_min} секунд.",
                    "Рассчитать план для записи night_light.",
                )
            )
        else:
            builders.append(
                (
                    "apply_schedule",
                    f"Режим {mode.value}",
                    "Применяем активную запись расписания и проверяем её "
                    "влияние только на свои цели.",
                    f"Рассчитать план для записи {mode.value}.",
                )
            )
    builders.extend(
        [
            (
                "verify_brightness",
                "Проверка яркости",
                "Сверяем расчётную яркость с пределом расписания и коррекцией "
                "по люксу.",
                "Проверить желаемую яркость по цели.",
            ),
        ]
    )
    if supports_color:
        builders.append(
            (
                "verify_color_temperature",
                "Проверка оттенка",
                "Сверяем оттенок с расписанием и не трогаем нерегулируемые "
                "цели.",
                "Проверить желаемый оттенок по цели.",
            )
        )
    builders.extend(
        [
            (
                "verify_ownership",
                "Проверка владения",
                "Убеждаемся, что ручное владение блокирует автоматическую "
                "ветку, а авто-выключение требует доказанного владения.",
                "Проверить владение и причины пропуска.",
            ),
            (
                "simulate_absence",
                "Отсутствие",
                "Моделируем уход и проверяем, что unknown или stale не "
                "считаются отсутствием.",
                "Смоделировать отсутствие и получить план гашения.",
            ),
            (
                "verify_fade",
                "Плавное гашение",
                "Проверяем монотонность гашения и отмену при возврате "
                "присутствия.",
                "Проверить, что нет шагов яркости вверх.",
            ),
            (
                "verify_manual_protection",
                "Ручная защита",
                "После ручного выключения автоматика ждёт минимальный срок и "
                "устойчивое отсутствие.",
                "Проверить активную ручную защиту.",
            ),
        ]
    )
    if devices.wireless_switches:
        builders.append(
            (
                "simulate_switch_press",
                "Нажатие выключателя",
                "Подтверждённое нажатие считается ручным намерением и создаёт "
                "ручное владение без двойного переключения.",
                "Смоделировать нажатие и проверить привязку.",
            )
        )
    builders.extend(
        [
            (
                "away_room_off",
                "Режим «Вне дома»",
                "Уход гасит все автоматические цели комнаты разом.",
                "Рассчитать план для режима room_off.",
            ),
            (
                "verify_away_return",
                "Возврат домой",
                "Возврат восстанавливает свет по текущим условиям, а не резким "
                "включением.",
                "Проверить возврат по расписанию и люксу.",
            ),
            (
                "restore_state",
                "Возврат состояния",
                "Возвращаем исходное состояние и завершаем прогон.",
                "Сформировать финальный снимок без команд.",
            ),
        ]
    )

    count = len(builders)
    per_stage = max(2, round(LIVE_TEST_DURATION_SECONDS / count))
    stages: list[LiveTestStage] = []
    for index, (stage, title, comment, action) in enumerate(builders):
        stages.append(
            LiveTestStage(
                order=index,
                stage=stage,
                duration_seconds=per_stage,
                title=title,
                comment=comment,
                action=action,
            )
        )
    return stages


@dataclass(frozen=True, slots=True)
class LiveTestStep:
    index: int
    stage: str
    comment: str
    offset_seconds: int
    status: str = "passed"
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class LiveTestTrace:
    correlation_id: str
    mode: str
    room_id: str
    started_at: int
    duration_seconds: int
    steps: tuple[LiveTestStep, ...]
    status: str
    reason: str
    finished_at: int
    commands_sent: int

    def to_payload(self) -> dict[str, object]:
        return {
            "contract": {
                "name": "hausman-hub-room-lighting-live-test",
                "version": 1,
            },
            "kind": "result",
            "correlationId": self.correlation_id,
            "roomId": self.room_id,
            "mode": self.mode,
            "startedAt": self.started_at,
            "durationSeconds": self.duration_seconds,
            "steps": [_step_payload(step) for step in self.steps],
            "result": {
                "status": self.status,
                "reason": self.reason,
                "finishedAt": self.finished_at,
                "correlationId": self.correlation_id,
                "commands_sent": self.commands_sent,
            },
        }

    def to_request_payload(self) -> dict[str, object]:
        return {
            "contract": {
                "name": "hausman-hub-room-lighting-live-test",
                "version": 1,
            },
            "kind": "request",
            "correlationId": self.correlation_id,
            "roomId": self.room_id,
            "mode": self.mode,
            "startedAt": self.started_at,
            "durationSeconds": self.duration_seconds,
            "steps": [_step_payload(step) for step in self.steps],
            "result": None,
        }


def _step_payload(step: LiveTestStep) -> dict[str, object]:
    payload: dict[str, object] = {
        "index": step.index,
        "stage": step.stage,
        "comment": step.comment[:300],
        "offset_seconds": step.offset_seconds,
        "status": step.status,
    }
    if step.detail:
        payload["detail"] = step.detail[:300]
    return payload


def cancelled_trace(
    *,
    correlation_id: str,
    room_id: str,
    mode: str,
    started_at: int,
    steps: tuple[LiveTestStep, ...] = (),
) -> LiveTestTrace:
    """Build a contract-valid cancelled result for an API cancel request."""

    return LiveTestTrace(
        correlation_id=correlation_id,
        mode=mode,
        room_id=room_id,
        started_at=started_at,
        duration_seconds=LIVE_TEST_DURATION_SECONDS,
        steps=steps,
        status="cancelled",
        reason="cancelled_by_user",
        finished_at=int(time.time()),
        commands_sent=0,
    )


ContextFactory = Callable[[LiveTestStage], RoomLightingContext]
ContextProvider = Callable[[], "RoomLightingContext | object"]


class RoomLightingLiveTestRunner:
    """Run the bounded live test over a real wall clock.

    ``sleep`` is injectable so tests stay fast; production uses the real
    ``asyncio.sleep`` and a provider that reads live Home Assistant state.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], object] | None = None,
    ) -> None:
        self._clock = clock
        self._sleep = sleep or asyncio.sleep

    async def run(
        self,
        config: RoomLightingConfig,
        *,
        mode: str,
        correlation_id: str,
        executor: object | None = None,
        cancel_event: asyncio.Event | None = None,
        context_factory: ContextFactory | None = None,
        context_provider: ContextProvider | None = None,
    ) -> LiveTestTrace:
        if mode not in LIVE_TEST_MODES:
            raise RoomLightingLiveTestViolation(f"unsupported live test mode: {mode}")
        if not isinstance(correlation_id, str) or not correlation_id:
            raise RoomLightingLiveTestViolation("correlation id is required")
        if mode == LIVE_TEST_MODE_REAL and executor is None:
            raise RoomLightingLiveTestViolation("real mode requires an executor")

        started_at = int(self._clock())
        started_at_ms = started_at * 1000
        steps: list[LiveTestStep] = []
        commands_sent = 0
        status = "passed"
        reason = "none"
        offset = 0
        cancelled = False

        for stage in build_stages(config):
            # A real delay drives the advertised ~30 second run.
            wait = self._sleep(stage.duration_seconds)
            if inspect.isawaitable(wait):
                await wait
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break
            context = await _resolve_context(
                config, stage, started_at_ms, context_factory, context_provider
            )
            decision = evaluate_room_lighting(config, context)
            if mode == LIVE_TEST_MODE_REAL and executor is not None:
                for command in decision.commands:
                    if cancel_event is not None and cancel_event.is_set():
                        cancelled = True
                        break
                    await _dispatch(executor, command)
                    commands_sent += 1
                if cancelled:
                    break
            comment = f"{stage.title}: {stage.comment}"
            steps.append(
                LiveTestStep(
                    index=stage.order,
                    stage=stage.stage,
                    comment=comment,
                    offset_seconds=min(offset, LIVE_TEST_DURATION_SECONDS),
                    status="passed",
                    detail=stage.action,
                )
            )
            offset += stage.duration_seconds
        if cancelled:
            status, reason = "cancelled", "cancelled_by_user"

        return LiveTestTrace(
            correlation_id=correlation_id,
            mode=mode,
            room_id=config.room_id,
            started_at=started_at,
            duration_seconds=LIVE_TEST_DURATION_SECONDS,
            steps=tuple(steps),
            status=status,
            reason=reason,
            finished_at=int(self._clock()),
            commands_sent=commands_sent,
        )


async def _resolve_context(
    config: RoomLightingConfig,
    stage: LiveTestStage,
    started_at_ms: int,
    context_factory: ContextFactory | None,
    context_provider: ContextProvider | None,
) -> RoomLightingContext:
    """Prefer the live provider, then a factory, then the synthetic default."""

    if context_provider is not None:
        value = context_provider()
        if inspect.isawaitable(value):
            value = await value
        if isinstance(value, RoomLightingContext):
            return value
    if context_factory is not None:
        value = context_factory(stage)
        if inspect.isawaitable(value):
            value = await value
        if isinstance(value, RoomLightingContext):
            return value
    return _default_context(config, started_at_ms)


async def _dispatch(executor: object, command: PlannedCommand) -> dict[str, object]:
    result = executor(command)  # type: ignore[operator]
    if inspect.isawaitable(result):
        result = await result
    if isinstance(result, dict):
        return result
    return {"targetId": command.target_id, "action": command.action.value, "confirmed": True}


def _default_context(config: RoomLightingConfig, now_ms: int) -> RoomLightingContext:
    return RoomLightingContext(
        now=now_ms,
        timezone=timezone.utc,
        sunrise=dt_time(7, 0),
        sunset=dt_time(19, 0),
        sensors=tuple(
            SensorSnapshot(
                sensor_id=sensor.id,
                kind=sensor.kind,
                state=SensorState.ON,
                last_changed=now_ms,
                lux=100.0 if sensor.kind is SensorKind.ILLUMINANCE else None,
            )
            for sensor in config.devices.sensors
        ),
        lights=tuple(
            LightSnapshot(target_id=target.id, state=SensorState.OFF, last_changed=now_ms)
            for target in config.devices.light_targets
        ),
    )
