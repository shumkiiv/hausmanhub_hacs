"""Bounded ~30 second room lighting live test.

``safe`` mode only calls the pure engine and never touches an executor;
``real`` mode dispatches the computed plan through an injected executor and
records the receipts. The runner carries one ``correlation_id`` for the whole
trace and supports cancellation between stages.
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
LIVE_TEST_TARGET_SECONDS = 30
LIVE_TEST_STAGE_KEYS = (
    "resolve_devices",
    "snapshot_state",
    "presence_on",
    "schedule_on_presence",
    "schedule_always",
    "schedule_night_light",
    "schedule_off",
    "lux_correction",
    "absence_dimming",
    "manual_off_protection",
    "away_room_off",
    "restore_state",
)


class RoomLightingLiveTestViolation(ValueError):
    """Live test input is malformed."""


@dataclass(frozen=True, slots=True)
class LiveTestStage:
    """One narrated step of the live run."""

    order: int
    key: str
    duration_seconds: int
    title: str
    comment: str
    action: str


def build_stages(config: RoomLightingConfig) -> list[LiveTestStage]:
    """Build a compact ~30 second narrated sequence for one room."""

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
        (
            "presence_on",
            "Присутствие",
            "Проверяем, что свежее присутствие запускает автоматику только "
            "если свет не занят вручную.",
            "Смоделировать присутствие и получить план движка.",
        ),
    ]

    if ScheduleMode.ON_PRESENCE in schedule_modes:
        builders.append(
            (
                "schedule_on_presence",
                "Режим on_presence",
                "Проверяем включение по присутствию в пределах яркости и "
                "оттенка активной записи расписания.",
                "Рассчитать план для записи on_presence.",
            )
        )
    if ScheduleMode.ALWAYS in schedule_modes:
        builders.append(
            (
                "schedule_always",
                "Режим always",
                "Проверяем постоянное включение по расписанию без ожидания "
                "присутствия.",
                "Рассчитать план для записи always.",
            )
        )
    if ScheduleMode.NIGHT_LIGHT in schedule_modes:
        builders.append(
            (
                "schedule_night_light",
                "Режим night_light",
                "Проверяем ночную подсветку: включение по движению и "
                f"удержание минимум {night_min} секунд.",
                "Рассчитать план для записи night_light с minOnSeconds.",
            )
        )
    if ScheduleMode.OFF in schedule_modes:
        builders.append(
            (
                "schedule_off",
                "Режим off",
                "Проверяем выключение по расписанию только для света под "
                "подтверждённым автоматическим владением.",
                "Рассчитать план выключения по записи off.",
            )
        )

    if config.illumination is not None:
        builders.append(
            (
                "lux_correction",
                "Коррекция по люксу",
                "Проверяем порог освещённости и fail-closed: недостоверный "
                "люкс отключает ветку, но не считается отсутствием.",
                "Рассчитать план с коррекцией по порогам освещённости.",
            )
        )

    builders.extend(
        [
            (
                "absence_dimming",
                "Плавное гашение",
                "Проверяем монотонное гашение после подтверждённого "
                "отсутствия и отмену при возврате присутствия.",
                "Рассчитать план гашения без шагов вверх.",
            ),
            (
                "manual_off_protection",
                "Ручная защита",
                "Проверяем, что после ручного выключения автоматика ждёт "
                "минимальный срок и устойчивое отсутствие.",
                "Рассчитать план при активной ручной защите.",
            ),
            (
                "away_room_off",
                "Режим «Вне дома»",
                "Проверяем, что уход гасит все автоматические цели комнаты "
                "разом без резкого включения при возврате.",
                "Рассчитать план для режима room_off.",
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
    per_stage = max(2, round(LIVE_TEST_TARGET_SECONDS / count))
    stages: list[LiveTestStage] = []
    for index, (key, title, comment, action) in enumerate(builders):
        stages.append(
            LiveTestStage(
                order=index,
                key=key,
                duration_seconds=per_stage,
                title=title,
                comment=comment,
                action=action,
            )
        )
    return stages


@dataclass(frozen=True, slots=True)
class LiveTestStep:
    order: int
    key: str
    title: str
    comment: str
    action: str
    status: str
    desired_state: str
    commands: tuple[dict[str, object], ...] = ()
    receipts: tuple[dict[str, object], ...] = ()
    detail: str = ""


@dataclass(frozen=True, slots=True)
class LiveTestTrace:
    correlation_id: str
    mode: str
    room_id: str
    started_at: int
    finished_at: int
    status: str
    steps: tuple[LiveTestStep, ...]
    commands_sent: int
    receipts: tuple[dict[str, object], ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "correlationId": self.correlation_id,
            "mode": self.mode,
            "roomId": self.room_id,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "status": self.status,
            "commandsSent": self.commands_sent,
            "steps": [
                {
                    "order": step.order,
                    "key": step.key,
                    "title": step.title,
                    "comment": step.comment,
                    "action": step.action,
                    "status": step.status,
                    "desiredState": step.desired_state,
                    "commands": list(step.commands),
                    "receipts": list(step.receipts),
                    "detail": step.detail,
                }
                for step in self.steps
            ],
            "receipts": list(self.receipts),
        }


ContextFactory = Callable[[LiveTestStage], RoomLightingContext]


class RoomLightingLiveTestRunner:
    """Run the bounded live test without owning any Home Assistant dependency."""

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock

    async def run(
        self,
        config: RoomLightingConfig,
        *,
        mode: str,
        correlation_id: str,
        executor: object | None = None,
        cancel_event: asyncio.Event | None = None,
        context_factory: ContextFactory | None = None,
    ) -> LiveTestTrace:
        if mode not in LIVE_TEST_MODES:
            raise RoomLightingLiveTestViolation(f"unsupported live test mode: {mode}")
        if not isinstance(correlation_id, str) or not correlation_id:
            raise RoomLightingLiveTestViolation("correlation id is required")
        if mode == LIVE_TEST_MODE_REAL and executor is None:
            raise RoomLightingLiveTestViolation("real mode requires an executor")

        started_at = int(self._clock() * 1000)
        steps: list[LiveTestStep] = []
        receipts: list[dict[str, object]] = []
        commands_sent = 0
        status = "completed"

        for stage in build_stages(config):
            if cancel_event is not None and cancel_event.is_set():
                steps.append(_cancelled_step(stage))
                status = "cancelled"
                break
            context = (
                context_factory(stage)
                if context_factory is not None
                else _default_context(stage, config, started_at)
            )
            decision = evaluate_room_lighting(config, context)
            planned = decision.commands
            step_commands = tuple(_command_payload(command) for command in planned)
            step_receipts: list[dict[str, object]] = []
            step_status = "completed"
            if mode == LIVE_TEST_MODE_REAL and executor is not None:
                for command in planned:
                    if cancel_event is not None and cancel_event.is_set():
                        step_status = "cancelled"
                        status = "cancelled"
                        break
                    receipt = await _dispatch(executor, command)
                    step_receipts.append(receipt)
                    receipts.append(receipt)
                    commands_sent += 1
            desired = {
                target.target_id: target.desired_state
                for target in decision.targets
            }
            steps.append(
                LiveTestStep(
                    order=stage.order,
                    key=stage.key,
                    title=stage.title,
                    comment=stage.comment,
                    action=stage.action,
                    status=step_status,
                    desired_state=", ".join(
                        f"{target_id}={state}" for target_id, state in desired.items()
                    ),
                    commands=step_commands,
                    receipts=tuple(step_receipts),
                    detail=(
                        "План построен без команд."
                        if mode == LIVE_TEST_MODE_SAFE
                        else f"Отправлено команд: {len(step_receipts)}."
                    ),
                )
            )
            if status == "cancelled":
                break

        return LiveTestTrace(
            correlation_id=correlation_id,
            mode=mode,
            room_id=config.room_id,
            started_at=started_at,
            finished_at=int(self._clock() * 1000),
            status=status,
            steps=tuple(steps),
            commands_sent=commands_sent,
            receipts=tuple(receipts),
        )


def _cancelled_step(stage: LiveTestStage) -> LiveTestStep:
    return LiveTestStep(
        order=stage.order,
        key=stage.key,
        title=stage.title,
        comment=stage.comment,
        action=stage.action,
        status="cancelled",
        desired_state="",
        detail="Прогон отменён до выполнения стадии.",
    )


def _command_payload(command: PlannedCommand) -> dict[str, object]:
    return {
        "targetId": command.target_id,
        "action": command.action.value,
        "brightness": command.brightness,
        "colorTemperature": command.color_temperature,
        "reason": command.reason.value,
    }


async def _dispatch(executor: object, command: PlannedCommand) -> dict[str, object]:
    result = executor(command)  # type: ignore[operator]
    if inspect.isawaitable(result):
        result = await result
    if result is None:
        return {"targetId": command.target_id, "action": command.action.value, "confirmed": True}
    if isinstance(result, dict):
        return result
    return {"targetId": command.target_id, "action": command.action.value, "confirmed": bool(result)}


def _default_context(
    stage: LiveTestStage, config: RoomLightingConfig, now: int
) -> RoomLightingContext:
    del stage
    return RoomLightingContext(
        now=now,
        timezone=timezone.utc,
        sunrise=dt_time(7, 0),
        sunset=dt_time(19, 0),
        sensors=tuple(
            SensorSnapshot(
                sensor_id=sensor.id,
                kind=sensor.kind,
                state=SensorState.ON,
                last_changed=now,
                lux=100.0 if sensor.kind is SensorKind.ILLUMINANCE else None,
            )
            for sensor in config.devices.sensors
        ),
        lights=tuple(
            LightSnapshot(target_id=target.id, state=SensorState.OFF, last_changed=now)
            for target in config.devices.light_targets
        ),
    )
