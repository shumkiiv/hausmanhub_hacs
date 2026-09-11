"""Rule matrix tests for the deterministic room lighting engine."""

from __future__ import annotations

from datetime import datetime, time, timezone

from custom_components.hausman_hub.domain.room_lighting import config_from_payload
from custom_components.hausman_hub.domain.room_lighting_engine import (
    DecisionReason,
    EnginePolicy,
    LightAction,
    LightSnapshot,
    ProtectionSnapshot,
    RoomLightingContext,
    SensorSnapshot,
    SkipReason,
    decision_target,
    evaluate_room_lighting,
)
from custom_components.hausman_hub.domain.room_lighting_ownership import (
    OwnershipSnapshot,
    OwnershipSource,
    SensorState,
)
from custom_components.hausman_hub.domain.room_lighting import SensorKind

_TZ = timezone.utc
_SUNRISE = time(7, 0)
_SUNSET = time(19, 0)


def _at(hour: int, minute: int = 0) -> int:
    return int(datetime(2026, 9, 11, hour, minute, tzinfo=_TZ).timestamp() * 1000)


def _schedule_day() -> dict[str, object]:
    return {
        "id": "sch_day",
        "title": "День",
        "when": {
            "daysOfWeek": "all",
            "holiday": False,
            "anchor": {"kind": "fixed", "time": "09:00", "offsetMinutes": 0},
        },
        "targets": {"lightTargets": ["light_main"], "groupIds": [], "roles": []},
        "how": {
            "brightness": 80,
            "colorTemperature": 3000,
            "fade": True,
            "mode": "on_presence",
        },
    }


def _schedule_off() -> dict[str, object]:
    return {
        "id": "sch_off",
        "title": "Выключение",
        "when": {
            "daysOfWeek": "all",
            "holiday": False,
            "anchor": {"kind": "fixed", "time": "23:00", "offsetMinutes": 0},
        },
        "targets": {"lightTargets": ["light_main"], "groupIds": [], "roles": []},
        "how": {
            "brightness": None,
            "colorTemperature": None,
            "fade": True,
            "mode": "off",
        },
    }


def _schedule_night() -> dict[str, object]:
    return {
        "id": "sch_night",
        "title": "Ночная подсветка",
        "when": {
            "daysOfWeek": "all",
            "holiday": False,
            "anchor": {"kind": "fixed", "time": "02:00", "offsetMinutes": 0},
        },
        "targets": {"lightTargets": ["light_mirror"], "groupIds": [], "roles": []},
        "how": {
            "brightness": 10,
            "colorTemperature": None,
            "fade": True,
            "mode": "night_light",
            "minOnSeconds": 600,
        },
    }


def _config(schedule: list[dict[str, object]] | None = None, illumination: dict[str, object] | None = None):
    payload: dict[str, object] = {
        "contract": {"name": "hausman-hub-room-lighting-config", "version": 1},
        "roomId": "room_demo_entry",
        "name": "Тамбур",
        "version": 1,
        "devices": {
            "sensors": [
                {
                    "id": "sensor_demo_presence",
                    "name": "Присутствие",
                    "kind": "presence",
                    "entityId": "binary_sensor.demo_presence",
                    "autoAdoptOverride": None,
                },
                {
                    "id": "sensor_demo_lux",
                    "name": "Освещённость",
                    "kind": "illuminance",
                    "entityId": "sensor.demo_lux",
                    "autoAdoptOverride": None,
                },
            ],
            "light_targets": [
                {
                    "id": "light_main",
                    "name": "Люстра",
                    "kind": "light",
                    "entityId": "light.demo_main",
                    "role": "main",
                    "groupId": "grp_main",
                    "brightness": True,
                    "color_temperature": True,
                    "autoAdoptOverride": None,
                },
                {
                    "id": "light_mirror",
                    "name": "Зеркало",
                    "kind": "light",
                    "entityId": "light.demo_mirror",
                    "role": "mirror",
                    "groupId": None,
                    "brightness": True,
                    "color_temperature": False,
                    "autoAdoptOverride": None,
                },
            ],
            "power_switch": None,
            "wireless_switches": [],
            "selectAll": False,
        },
        "schedule": schedule if schedule is not None else [_schedule_day()],
        "switchBindings": [],
        "illumination": illumination,
        "dimming": {
            "enabled": True,
            "onAbsence": True,
            "fadeSeconds": 20,
            "targetPercent": 0,
        },
        "manualOffProtection": {
            "enabled": True,
            "minimumIntervalSeconds": 600,
            "releaseMode": "timer_and_absence",
            "stableAbsenceSeconds": 30,
            "priority": "manual_above_auto",
        },
        "awayBehavior": {"mode": "none"},
        "autoAdopt": True,
        "updatedAt": 1,
        "overrides": {},
    }
    return config_from_payload(payload)


def _peer_config():
    """Two interchangeable targets in one group commanded by one entry."""

    payload = _config().to_dict()
    payload["devices"]["light_targets"].append(
        {
            "id": "light_main_2",
            "name": "Люстра 2",
            "kind": "light",
            "entityId": "light.demo_main_2",
            "role": "main",
            "groupId": "grp_main",
            "brightness": True,
            "color_temperature": False,
            "autoAdoptOverride": None,
        }
    )
    payload["schedule"] = [
        {
            "id": "sch_group",
            "title": "Группа",
            "when": {
                "daysOfWeek": "all",
                "holiday": False,
                "anchor": {"kind": "fixed", "time": "09:00", "offsetMinutes": 0},
            },
            "targets": {"lightTargets": [], "groupIds": ["grp_main"], "roles": []},
            "how": {
                "brightness": 60,
                "colorTemperature": None,
                "fade": True,
                "mode": "on_presence",
                "minOnSeconds": 0,
            },
        }
    ]
    return config_from_payload(payload)


def _presence(state: SensorState, at: int) -> SensorSnapshot:
    return SensorSnapshot(
        sensor_id="sensor_demo_presence",
        kind=SensorKind.PRESENCE,
        state=state,
        last_changed=at,
    )


def _light(
    target_id: str,
    state: SensorState,
    at: int,
    brightness: int | None = None,
    color: int | None = None,
) -> LightSnapshot:
    return LightSnapshot(
        target_id=target_id,
        state=state,
        last_changed=at,
        brightness=brightness,
        color_temperature=color,
    )


def _auto(target_id: str, at: int) -> OwnershipSnapshot:
    return OwnershipSnapshot(target_id, OwnershipSource.AUTO, True, at)


def _ctx(
    now: int,
    *,
    presence: SensorState = SensorState.ON,
    presence_at: int | None = None,
    lights: tuple[LightSnapshot, ...] = (),
    ownership: tuple[OwnershipSnapshot, ...] = (),
    protection: ProtectionSnapshot | None = None,
    sensors: tuple[SensorSnapshot, ...] = (),
    unobserved_since: int | None = None,
) -> RoomLightingContext:
    return RoomLightingContext(
        now=now,
        timezone=_TZ,
        sunrise=_SUNRISE,
        sunset=_SUNSET,
        sensors=sensors or (_presence(presence, presence_at or now),),
        lights=lights,
        ownership=ownership,
        protection=protection or ProtectionSnapshot(),
        unobserved_since=unobserved_since,
    )


def test_presence_turns_on_schedule_limit() -> None:
    now = _at(10, 0)
    decision = evaluate_room_lighting(_config(), _ctx(now))
    main = decision_target(decision, "light_main")
    assert main is not None
    assert [command.action for command in main.commands] == [
        LightAction.TURN_ON,
        LightAction.SET_BRIGHTNESS,
        LightAction.SET_COLOR_TEMPERATURE,
    ]
    assert main.commands[0].reason is DecisionReason.PRESENCE
    assert main.commands[1].brightness == 80
    assert main.commands[2].color_temperature == 3000


def test_manual_on_before_sensor_event_skips_auto_branch() -> None:
    now = _at(10, 0)
    decision = evaluate_room_lighting(
        _config(),
        _ctx(
            now,
            lights=(_light("light_main", SensorState.ON, now - 5000, brightness=50),),
        ),
    )
    main = decision_target(decision, "light_main")
    assert main is not None
    assert main.commands == ()
    assert main.skips[0].reason is SkipReason.MANUAL_OWNERSHIP


def test_auto_owned_on_light_is_idempotent() -> None:
    now = _at(10, 0)
    context = _ctx(
        now,
        lights=(
            _light(
                "light_main",
                SensorState.ON,
                now - 1000,
                brightness=80,
                color=3000,
            ),
        ),
        ownership=(_auto("light_main", now - 1000),),
    )
    first = evaluate_room_lighting(_config(), context)
    second = evaluate_room_lighting(_config(), context)
    for decision in (first, second):
        main = decision_target(decision, "light_main")
        assert main is not None
        assert main.commands == ()
        assert main.skips[0].reason is SkipReason.IDEMPOTENT


def test_color_temperature_round_trip_is_idempotent() -> None:
    now = _at(10, 0)
    # 3000 K -> mired -> 3003 K is the real round trip; ±50 K must not loop.
    context = _ctx(
        now,
        lights=(
            _light("light_main", SensorState.ON, now - 1000, brightness=81, color=3003),
        ),
        ownership=(_auto("light_main", now - 1000),),
    )
    decision = evaluate_room_lighting(_config(), context)
    main = decision_target(decision, "light_main")
    assert main is not None
    assert main.commands == ()
    assert main.skips[0].reason is SkipReason.IDEMPOTENT


def test_manual_peer_in_group_blocks_sibling_auto_on() -> None:
    now = _at(10, 0)
    decision = evaluate_room_lighting(
        _peer_config(),
        _ctx(
            now,
            lights=(
                _light("light_main", SensorState.ON, now - 1000, brightness=60),
                _light("light_main_2", SensorState.OFF, now - 1000),
            ),
        ),
    )
    main = decision_target(decision, "light_main")
    sibling = decision_target(decision, "light_main_2")
    assert main is not None and sibling is not None
    assert main.commands == ()
    assert main.skips[0].reason is SkipReason.MANUAL_OWNERSHIP
    assert sibling.commands == ()
    assert sibling.skips[0].reason is SkipReason.MANUAL_PEER


def test_manual_protection_blocks_auto_on_until_release() -> None:
    now = _at(10, 0)
    protection = ProtectionSnapshot(
        active=True,
        started_at=now,
        minimum_interval_seconds=600,
        stable_absence_seconds=30,
        release_mode="timer_and_absence",
        reason="manual_off",
    )
    decision = evaluate_room_lighting(
        _config(),
        _ctx(now, lights=(_light("light_main", SensorState.OFF, now - 1000),), protection=protection),
    )
    main = decision_target(decision, "light_main")
    assert main is not None
    assert main.commands == ()
    assert main.skips[0].reason is SkipReason.MANUAL_PROTECTION


def test_protection_timer_expiry_without_absence_still_blocks() -> None:
    now = _at(10, 12)
    protection = ProtectionSnapshot(
        active=True,
        started_at=now - 700_000,
        minimum_interval_seconds=600,
        stable_absence_seconds=30,
        release_mode="timer_and_absence",
        reason="manual_off",
    )
    decision = evaluate_room_lighting(
        _config(),
        _ctx(now, lights=(_light("light_main", SensorState.OFF, now - 1000),), protection=protection),
    )
    main = decision_target(decision, "light_main")
    assert main is not None
    assert main.skips[0].reason is SkipReason.MANUAL_PROTECTION


def test_manual_ownership_not_removed_by_schedule_off() -> None:
    now = _at(23, 30)
    decision = evaluate_room_lighting(
        _config(schedule=[_schedule_day(), _schedule_off()]),
        _ctx(
            now,
            lights=(_light("light_main", SensorState.ON, now - 1000, brightness=40),),
            ownership=(
                OwnershipSnapshot("light_main", OwnershipSource.MANUAL, True, now - 1000),
            ),
        ),
    )
    main = decision_target(decision, "light_main")
    assert main is not None
    assert main.commands == ()
    assert main.skips[0].reason is SkipReason.MANUAL_OWNERSHIP


def test_absence_before_night_waits_600_seconds() -> None:
    now = _at(12, 0)
    lights = (_light("light_main", SensorState.ON, now - 1000, brightness=40),)
    ownership = (_auto("light_main", now - 1000),)

    early = evaluate_room_lighting(
        _config(),
        _ctx(
            now,
            presence=SensorState.OFF,
            presence_at=now - 500_000,
            lights=lights,
            ownership=ownership,
        ),
    )
    early_main = decision_target(early, "light_main")
    assert early_main is not None
    assert early_main.commands == ()
    assert any(skip.reason is SkipReason.ABSENCE_UNPROVEN for skip in early_main.skips)

    due = evaluate_room_lighting(
        _config(),
        _ctx(
            now,
            presence=SensorState.OFF,
            presence_at=now - 600_000,
            lights=lights,
            ownership=ownership,
        ),
    )
    due_main = decision_target(due, "light_main")
    assert due_main is not None
    assert due_main.commands[0].action is LightAction.SET_BRIGHTNESS
    assert due_main.commands[0].reason is DecisionReason.DIMMING
    assert due_main.commands[0].brightness == 0


def test_absence_after_night_waits_180_seconds() -> None:
    now = _at(23, 30)
    lights = (_light("light_main", SensorState.ON, now - 1000, brightness=40),)
    ownership = (_auto("light_main", now - 1000),)

    early = evaluate_room_lighting(
        _config(),
        _ctx(
            now,
            presence=SensorState.OFF,
            presence_at=now - 100_000,
            lights=lights,
            ownership=ownership,
        ),
    )
    assert decision_target(early, "light_main").commands == ()

    due = evaluate_room_lighting(
        _config(),
        _ctx(
            now,
            presence=SensorState.OFF,
            presence_at=now - 180_000,
            lights=lights,
            ownership=ownership,
        ),
    )
    assert decision_target(due, "light_main").commands


def test_restart_unobserved_period_is_not_absence() -> None:
    now = _at(12, 0)
    decision = evaluate_room_lighting(
        _config(),
        _ctx(
            now,
            presence=SensorState.OFF,
            presence_at=now - 900_000,
            lights=(_light("light_main", SensorState.ON, now - 1000, brightness=40),),
            ownership=(_auto("light_main", now - 60_000),),
            unobserved_since=now - 30_000,
        ),
    )
    main = decision_target(decision, "light_main")
    assert main is not None
    assert main.commands == ()
    assert any(skip.reason is SkipReason.UNOBSERVED for skip in main.skips)


def test_restart_unobserved_blocks_auto_owned_adjustment() -> None:
    now = _at(12, 0)
    # The light was auto-owned before the restart and still reads a different
    # brightness, but the unobserved gap means the ownership is not proven
    # fresh: the adjust branch must not dispatch SET_BRIGHTNESS.
    decision = evaluate_room_lighting(
        _config(),
        _ctx(
            now,
            presence=SensorState.ON,
            presence_at=now,
            lights=(_light("light_main", SensorState.ON, now - 1000, brightness=30),),
            ownership=(_auto("light_main", now - 60_000),),
            unobserved_since=now - 30_000,
        ),
    )
    main = decision_target(decision, "light_main")
    assert main is not None
    assert main.commands == ()
    assert any(skip.reason is SkipReason.UNOBSERVED for skip in main.skips)


def test_fail_closed_lux_disables_illumination_branch_only() -> None:
    now = _at(10, 0)
    illumination = {
        "sensor": "sensor.demo_lux",
        "calibration": {"offset": 0, "multiplier": 1},
        "hysteresis": 5,
        "minLux": 0,
        "maxLux": 20000,
        "thresholds": [
            {"lux": 200, "brightness": 20, "colorTemperature": 2700, "modifier": None}
        ],
        "failClosed": True,
    }
    context = _ctx(
        now,
        lights=(_light("light_main", SensorState.OFF, now - 1000),),
        sensors=(
            _presence(SensorState.ON, now),
            SensorSnapshot(
                sensor_id="sensor_demo_lux",
                kind=SensorKind.ILLUMINANCE,
                state=SensorState.ON,
                last_changed=now,
                lux=100.0,
                lux_healthy=False,
            ),
        ),
    )
    decision = evaluate_room_lighting(_config(illumination=illumination), context)
    main = decision_target(decision, "light_main")
    assert main is not None
    assert main.commands
    assert main.commands[1].brightness == 80  # schedule limit, lux ignored
    assert any(skip.reason is SkipReason.LUX_FAIL_CLOSED for skip in main.skips)


def test_lux_threshold_adjusts_brightness_when_healthy() -> None:
    now = _at(10, 0)
    illumination = {
        "sensor": "sensor.demo_lux",
        "calibration": {"offset": 0, "multiplier": 1},
        "hysteresis": 5,
        "minLux": 0,
        "maxLux": 20000,
        "thresholds": [
            {"lux": 200, "brightness": 20, "colorTemperature": 2700, "modifier": None}
        ],
        "failClosed": True,
    }
    context = _ctx(
        now,
        lights=(_light("light_main", SensorState.OFF, now - 1000),),
        sensors=(
            _presence(SensorState.ON, now),
            SensorSnapshot(
                sensor_id="sensor_demo_lux",
                kind=SensorKind.ILLUMINANCE,
                state=SensorState.ON,
                last_changed=now,
                lux=300.0,
                lux_healthy=True,
            ),
        ),
    )
    decision = evaluate_room_lighting(_config(illumination=illumination), context)
    main = decision_target(decision, "light_main")
    assert main is not None
    assert main.commands[0].reason is DecisionReason.LUX
    assert main.commands[1].brightness == 20
    assert main.commands[2].color_temperature == 2700


def test_night_light_window_turns_on_mirror() -> None:
    now = _at(3, 0)
    decision = evaluate_room_lighting(
        _config(schedule=[_schedule_day(), _schedule_night()]),
        _ctx(now, lights=(_light("light_mirror", SensorState.OFF, now - 1000),)),
    )
    mirror = decision_target(decision, "light_mirror")
    assert mirror is not None
    assert mirror.commands[0].action is LightAction.TURN_ON
    assert mirror.commands[0].reason is DecisionReason.NIGHT_LIGHT


def test_unknown_sensor_does_not_trigger_and_does_not_block_by_itself() -> None:
    now = _at(10, 0)
    decision = evaluate_room_lighting(
        _config(),
        _ctx(
            now,
            presence=SensorState.UNKNOWN,
            lights=(_light("light_main", SensorState.OFF, now - 1000),),
        ),
    )
    main = decision_target(decision, "light_main")
    assert main is not None
    assert main.commands == ()
    assert main.skips[0].reason is SkipReason.SENSOR_UNKNOWN


def test_policy_from_config_uses_dimming_and_protection() -> None:
    config = _config()
    policy = EnginePolicy.from_config(config)
    assert policy.fade_seconds == 20
    assert policy.absence_seconds_before_night == 600


def test_schedule_entry_does_not_affect_other_targets() -> None:
    now = _at(3, 0)
    decision = evaluate_room_lighting(
        _config(schedule=[_schedule_night()]),
        _ctx(now, lights=(_light("light_main", SensorState.OFF, now - 1000), _light("light_mirror", SensorState.OFF, now - 1000))),
    )
    main = decision_target(decision, "light_main")
    mirror = decision_target(decision, "light_mirror")
    assert main is not None
    assert mirror is not None
    assert main.commands == ()
    assert any(skip.reason is SkipReason.NO_SCHEDULE for skip in main.skips)
    assert mirror.commands[0].action is LightAction.TURN_ON
    assert mirror.commands[0].reason is DecisionReason.NIGHT_LIGHT


def test_long_absence_still_dims_past_600_seconds() -> None:
    now = _at(12, 0)
    for elapsed_ms in (601_000, 900_000, 3_600_000):
        decision = evaluate_room_lighting(
            _config(),
            _ctx(
                now,
                presence=SensorState.OFF,
                presence_at=now - elapsed_ms,
                lights=(_light("light_main", SensorState.ON, now - 1000, brightness=40),),
                ownership=(_auto("light_main", now - 1000),),
            ),
        )
        main = decision_target(decision, "light_main")
        assert main is not None
        assert main.commands, f"absence {elapsed_ms} not due"
        assert main.commands[0].reason is DecisionReason.DIMMING


def test_unknown_sensor_breaks_absence() -> None:
    now = _at(12, 0)
    decision = evaluate_room_lighting(
        _config(),
        _ctx(
            now,
            presence=SensorState.UNKNOWN,
            lights=(_light("light_main", SensorState.ON, now - 1000, brightness=40),),
            ownership=(_auto("light_main", now - 1000),),
        ),
    )
    main = decision_target(decision, "light_main")
    assert main is not None
    assert main.commands == ()
    assert any(skip.reason is SkipReason.SENSOR_UNKNOWN for skip in main.skips)


def test_stale_beyond_staleness_reports_sensor_stale() -> None:
    now = _at(12, 0)
    stale_at = now - (EnginePolicy().staleness_seconds + 1) * 1000
    decision = evaluate_room_lighting(
        _config(),
        _ctx(
            now,
            presence=SensorState.OFF,
            presence_at=stale_at,
            lights=(_light("light_main", SensorState.ON, now - 1000, brightness=40),),
            ownership=(_auto("light_main", now - 1000),),
        ),
    )
    main = decision_target(decision, "light_main")
    assert main is not None
    assert main.commands == ()
    assert any(skip.reason is SkipReason.SENSOR_STALE for skip in main.skips)


def test_manual_off_release_then_new_presence_turns_on() -> None:
    now = _at(10, 0)
    protection = ProtectionSnapshot(
        active=False,
        minimum_interval_seconds=600,
        stable_absence_seconds=30,
        absence_confirmed=True,
        absence_since=now - 40_000,
    )
    decision = evaluate_room_lighting(
        _config(),
        _ctx(
            now,
            presence=SensorState.ON,
            lights=(_light("light_main", SensorState.OFF, now - 1000),),
            ownership=(
                OwnershipSnapshot("light_main", OwnershipSource.MANUAL, True, now - 700_000),
            ),
            protection=protection,
        ),
    )
    main = decision_target(decision, "light_main")
    assert main is not None
    assert main.commands
    assert main.commands[0].action is LightAction.TURN_ON


def test_schedule_off_uses_schedule_reason() -> None:
    now = _at(23, 30)
    decision = evaluate_room_lighting(
        _config(schedule=[_schedule_day(), _schedule_off()]),
        _ctx(
            now,
            presence=SensorState.ON,
            lights=(_light("light_main", SensorState.ON, now - 1000, brightness=60),),
            ownership=(_auto("light_main", now - 1000),),
        ),
    )
    main = decision_target(decision, "light_main")
    assert main is not None
    assert main.commands
    assert main.commands[0].reason is DecisionReason.SCHEDULE


def _schedule_always() -> dict[str, object]:
    return {
        "id": "sch_mirror_always",
        "title": "Зеркало всегда",
        "when": {
            "daysOfWeek": "all",
            "holiday": False,
            "anchor": {"kind": "fixed", "time": "23:00", "offsetMinutes": 0},
        },
        "targets": {"lightTargets": ["light_mirror"], "groupIds": [], "roles": []},
        "how": {
            "brightness": 10,
            "colorTemperature": None,
            "fade": True,
            "mode": "always",
            "minOnSeconds": 0,
        },
    }


def _schedule_mirror_off() -> dict[str, object]:
    return {
        "id": "sch_mirror_off",
        "title": "Зеркало выключить",
        "when": {
            "daysOfWeek": "all",
            "holiday": False,
            "anchor": {"kind": "fixed", "time": "23:30", "offsetMinutes": 0},
        },
        "targets": {"lightTargets": ["light_mirror"], "groupIds": [], "roles": []},
        "how": {
            "brightness": 0,
            "colorTemperature": None,
            "fade": False,
            "mode": "off",
            "minOnSeconds": 0,
        },
    }


def test_always_mode_turns_on_and_next_off_entry_turns_off() -> None:
    on_now = _at(23, 15)
    on_decision = evaluate_room_lighting(
        _config(schedule=[_schedule_always(), _schedule_mirror_off()]),
        _ctx(on_now, lights=(_light("light_mirror", SensorState.OFF, on_now - 1000),)),
    )
    mirror_on = decision_target(on_decision, "light_mirror")
    assert mirror_on is not None
    assert mirror_on.commands[0].action is LightAction.TURN_ON
    assert mirror_on.commands[0].reason is DecisionReason.SCHEDULE

    off_now = _at(23, 45)
    off_decision = evaluate_room_lighting(
        _config(schedule=[_schedule_always(), _schedule_mirror_off()]),
        _ctx(
            off_now,
            lights=(
                _light("light_mirror", SensorState.ON, off_now - 1000, brightness=10, color=2700),
            ),
            ownership=(_auto("light_mirror", off_now - 1000),),
        ),
    )
    mirror_off = decision_target(off_decision, "light_mirror")
    assert mirror_off is not None
    assert mirror_off.commands
    assert mirror_off.commands[0].reason is DecisionReason.SCHEDULE


def test_night_light_min_on_seconds_holds_then_turns_off() -> None:
    now = _at(2, 5)
    lights = (_light("light_mirror", SensorState.ON, now - 1000, brightness=10),)

    hold = evaluate_room_lighting(
        _config(schedule=[_schedule_night()]),
        _ctx(
            now,
            presence=SensorState.OFF,
            presence_at=now - 800_000,
            lights=lights,
            ownership=(_auto("light_mirror", now - 100_000),),
        ),
    )
    hold_mirror = decision_target(hold, "light_mirror")
    assert hold_mirror is not None
    assert hold_mirror.commands == ()
    assert any(skip.reason is SkipReason.NIGHT_MINIMUM for skip in hold_mirror.skips)

    due = evaluate_room_lighting(
        _config(schedule=[_schedule_night()]),
        _ctx(
            now,
            presence=SensorState.OFF,
            presence_at=now - 800_000,
            lights=lights,
            ownership=(_auto("light_mirror", now - 700_000),),
        ),
    )
    due_mirror = decision_target(due, "light_mirror")
    assert due_mirror is not None
    assert due_mirror.commands
    assert due_mirror.commands[0].reason is DecisionReason.DIMMING
