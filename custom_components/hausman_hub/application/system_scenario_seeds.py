"""Built-in system scenario seeds ported from the Node-RED leftovers.

Перенос остатков Node-RED в системные сценарии Hausman Hub, спецификация:
``docs/migration/NODE_RED_REMAINING_SCENARIOS_2026-08-20.md`` (workspace).

Правила сидирования:

- сценарий создаётся только если его id ещё нет в реестре: правки
  пользователя и переименования не затираются никогда;
- seed пропускается, когда обязательных устройств нет в живом каталоге
  (чужая инсталляция HA без этих сущностей не получает мёртвые сценарии);
- для сценариев выключения «всего подряд» (режим «не дома») список
  действий фильтруется по каталогу: отсутствующие устройства молча
  пропускаются, сценарий создаётся, если живо хотя бы одно действие.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .scenario_catalog import _stable_target_id_from_entity
from .scenarios import ScenarioCatalog

if TYPE_CHECKING:  # pragma: no cover
    from .scenario_service import ScenarioService

_LOGGER = logging.getLogger(__name__)


# --- Payload builders (wire format домена scenarios) ---


def _target(entity_id: str) -> str:
    """Стабильный targetId каталога для entity id (см. scenario_catalog)."""

    return _stable_target_id_from_entity(entity_id)


def _device_trigger(
    rule_id: str,
    entity_id: str,
    comparison: str,
    value: object | None = None,
    *,
    for_seconds: int = 0,
) -> dict[str, object]:
    trigger: dict[str, object] = {
        "id": rule_id,
        "type": "device_state",
        "targetId": _target(entity_id),
        "property": "state",
        "comparison": comparison,
    }
    if value is not None:
        trigger["value"] = value
    if for_seconds:
        trigger["forSeconds"] = for_seconds
    return trigger


def _device_condition(
    rule_id: str, entity_id: str, comparison: str, value: object
) -> dict[str, object]:
    return {
        "id": rule_id,
        "type": "device_state",
        "targetId": _target(entity_id),
        "property": "state",
        "comparison": comparison,
        "value": value,
    }


def _device_action(
    rule_id: str, entity_id: str, action_id: str, value: object | None = None
) -> dict[str, object]:
    action: dict[str, object] = {
        "id": rule_id,
        "type": "device_action",
        "targetId": _target(entity_id),
        "actionId": action_id,
    }
    if value is not None:
        action["value"] = value
    return action


def _delay(rule_id: str, seconds: int) -> dict[str, object]:
    return {"id": rule_id, "type": "delay", "delaySeconds": seconds}


def _notify(rule_id: str, message: str) -> dict[str, object]:
    return {"id": rule_id, "type": "notification", "message": message}


def _with_target_names(
    items: tuple[Mapping[str, object], ...] | list[Mapping[str, object]],
    catalog: ScenarioCatalog,
) -> list[Mapping[str, object]]:
    """Подставить targetName из живого каталога в шаги с targetId."""

    enriched: list[Mapping[str, object]] = []
    for item in items:
        target_id = item.get("targetId")
        if isinstance(target_id, str) and "targetName" not in item:
            device = catalog.device(target_id)
            if device is not None and device.name:
                item = {**item, "targetName": device.name}
        enriched.append(item)
    return enriched


# --- Каталог сущностей этого дома (снимок 2026-08-20) ---

COVER_LIVING = "cover.shtory_gostinaia"
COVER_KITCHEN = "cover.0xa4c1385a4bcce3d6"

LEAK_TOILET = "binary_sensor.0xa4c1384e7279d71e_water_leak"
LEAK_BATHROOM = "binary_sensor.0xa4c13881dcd61534_water_leak"
LEAK_KITCHEN = "binary_sensor.0xa4c138df5797120e_water_leak"
LEAK_EXTRA = "binary_sensor.0x983268fffe63cb6c_water_leak"

MOTION_TOILET = "binary_sensor.datchik_dvizheniia_tualet_zaniatost"
MOTION_TOILET_TUYA = "binary_sensor.0xa4c13889c39443d5_occupancy"
AWAY_A100 = "binary_sensor.a100_away_zaniatost"
SUN = "sun.sun"

SWITCH_TOILET_LIGHT_1 = "switch.0xacbac0fffebde2d3_1"
SWITCH_TOILET_LIGHT_2 = "switch.0xacbac0fffebde2d3_2"
SWITCH_TOILET_FAN = "switch.0x54ef44100019f608"
SWITCH_BATHROOM_LIGHT_1 = "switch.0xacbac0fffebe38d0_1"
SWITCH_BATHROOM_LIGHT_2 = "switch.0xacbac0fffebe38d0_2"
SWITCH_BATHROOM_FAN = "switch.0x54ef44100019fca5"

LIGHT_HALLWAY = "light.0xa4c138784e5cbcd1"

HUMIDITY_BATHROOM = "sensor.klimat_vanna_humidity"

AWAY_OFF_ENTITIES: tuple[tuple[str, str], ...] = (
    # (entity_id, turn_off action domain comes from the catalog entry)
    ("climate.gostinnaia_konditsioner", "turn_off"),
    ("climate.detskaia_konditsioner", "turn_off"),
    ("climate.konditsioner_shaft", "turn_off"),
    ("climate.konditsioner_alisa", "turn_off"),
    ("climate.konditsioner_kabinet", "turn_off"),
    ("climate.0x8c73dafffe237e63", "turn_off"),
    ("climate.0x8c73dafffe1173c4", "turn_off"),
    ("light.kabinet_osvetitelnyi_pribor", "turn_off"),
    ("light.nochnik_u_dveri", "turn_off"),
    ("light.nochnik_u_okna", "turn_off"),
    (LIGHT_HALLWAY, "turn_off"),
    ("light.0xa4c138d69d102803", "turn_off"),
    ("light.0xa4c1385600dc0551", "turn_off"),
    ("switch.0x603d61fffe764d10_1", "turn_off"),
    ("switch.0x603d61fffe764d10_2", "turn_off"),
    ("switch.0x603d61fffe75e62f_1", "turn_off"),
    ("switch.0x603d61fffe75e62f_2", "turn_off"),
    ("switch.0x54ef4410006807e0_left", "turn_off"),
    ("switch.0x54ef4410006807e0_right", "turn_off"),
    ("switch.0x54ef441000680683_left", "turn_off"),
    ("switch.0x54ef441000680683_right", "turn_off"),
    ("switch.0x54ef4410006819fc_left", "turn_off"),
    ("switch.0x54ef4410006819fc_right", "turn_off"),
    ("switch.0xa4c138e4e8eeb315_left", "turn_off"),
    ("switch.0xa4c138e4e8eeb315_center", "turn_off"),
    ("switch.0xa4c138e4e8eeb315_right", "turn_off"),
    (SWITCH_TOILET_LIGHT_1, "turn_off"),
    (SWITCH_TOILET_LIGHT_2, "turn_off"),
    (SWITCH_BATHROOM_LIGHT_1, "turn_off"),
    (SWITCH_BATHROOM_LIGHT_2, "turn_off"),
    ("switch.0xacbac0fffebbe3c4_1", "turn_off"),
    ("switch.0xacbac0fffebbe3c4_2", "turn_off"),
    ("switch.0x603d61fffe759363_1", "turn_off"),
    ("switch.0xa4c1385af46163eb", "turn_off"),
    ("switch.0x603d61fffe761c63_1", "turn_off"),
    ("switch.0xa4c138ffecbc07b5_l1", "turn_off"),
    ("switch.0x603d61fffe75c334_1", "turn_off"),
    ("switch.0x603d61fffe767806_1", "turn_off"),
    ("switch.0x603d61fffe767806_2", "turn_off"),
    (SWITCH_TOILET_FAN, "turn_off"),
    (SWITCH_BATHROOM_FAN, "turn_off"),
    ("water_heater.kukhnia_chainik", "turn_off"),
    ("switch.kukhnia_chainik_podderzhanie_tepla", "turn_off"),
    ("switch.kukhnia_chainik_podsvetka", "turn_off"),
    ("humidifier.deerma_jsq2w_836b_humidifier", "turn_off"),
    ("humidifier.deerma_jsq2w_89c5_humidifier", "turn_off"),
    ("media_player.gostinnaia_televizor", "turn_off"),
    ("media_player.televizor_na_kukhne_2", "turn_off"),
    ("switch.0x54ef441001301a68", "turn_off"),
    ("switch.0x54ef44100130084b", "turn_off"),
)


@dataclass(frozen=True, slots=True)
class SystemScenarioSeed:
    """Одно определение системного сценария."""

    scenario_id: str
    title: str
    description: str
    icon: str
    triggers: tuple[Mapping[str, object], ...]
    conditions: tuple[Mapping[str, object], ...]
    actions: tuple[Mapping[str, object], ...]
    execution_mode: str = "single"
    command_mode: str = "live"
    required_entities: tuple[str, ...] = ()
    optional_actions: tuple[tuple[str, str], ...] = ()

    def build_payload(self, catalog: ScenarioCatalog) -> dict[str, Any] | None:
        """Собрать payload для async_update_scenario или None, если рано."""

        for entity_id in self.required_entities:
            device = catalog.device(_target(entity_id))
            if device is None or device.entity_id != entity_id:
                _LOGGER.debug(
                    "system scenario %s skipped: %s not in catalog",
                    self.scenario_id,
                    entity_id,
                )
                return None
        actions: list[Mapping[str, object]] = []
        for index, (entity_id, action_id) in enumerate(self.optional_actions):
            device = catalog.device(_target(entity_id))
            if device is None or device.entity_id != entity_id:
                continue
            actions.append(_device_action(f"ax{index}", entity_id, action_id))
        actions.extend(self.actions)
        if self.optional_actions and not any(
            item.get("type") == "device_action" for item in actions
        ):
            return None
        return {
            "id": self.scenario_id,
            "title": self.title,
            "group": "system",
            "description": self.description,
            "icon": self.icon,
            "enabled": True,
            "triggerDescription": "",
            "conditionDescription": "",
            "actionDescription": "",
            "definition": {
                "version": 1,
                "executionMode": self.execution_mode,
                **(
                    {"commandMode": self.command_mode}
                    if self.command_mode != "live"
                    else {}
                ),
                # Решение владельца 2026-08-20: имена устройств подставляются из
                # живого каталога, чтобы лента активности и редактор показывали
                # «Люстра кухни: выключить», а не безликое «Устройство: ...».
                "triggers": _with_target_names(self.triggers, catalog),
                "conditions": _with_target_names(self.conditions, catalog),
                "actions": _with_target_names(actions, catalog),
            },
        }


SYSTEM_SCENARIO_SEEDS: tuple[SystemScenarioSeed, ...] = (
    SystemScenarioSeed(
        scenario_id="system-twilight-curtains-close",
        title="Сумерки: закрыть шторы",
        description=(
            "Перенос из Node-RED «Сумерки»: на закате закрыть шторы гостиной "
            "и кухни. В оригинале на закате проверка освещённости не "
            "выполнялась, поведение сохранено."
        ),
        icon="mdi:blinds",
        triggers=({"id": "t1", "type": "sunset"},),
        conditions=(),
        actions=(
            _device_action("a1", COVER_LIVING, "close_cover"),
            _device_action("a2", COVER_KITCHEN, "close_cover"),
        ),
        required_entities=(COVER_LIVING, COVER_KITCHEN),
    ),
    SystemScenarioSeed(
        scenario_id="system-kitchen-curtains-open-weekday",
        title="Шторы кухня: утро будни",
        description=(
            "Перенос из Node-RED: открыть шторы кухни на 80% не раньше 07:00 "
            "в будни. Праздничный календарь Node-RED не переносится."
        ),
        icon="mdi:blinds-open",
        triggers=({"id": "t1", "type": "time", "value": "07:00"},),
        conditions=({"id": "c1", "type": "weekday", "value": "пн, вт, ср, чт, пт"},),
        actions=(_device_action("a1", COVER_KITCHEN, "set_position", 80),),
        required_entities=(COVER_KITCHEN,),
    ),
    SystemScenarioSeed(
        scenario_id="system-kitchen-curtains-open-weekend",
        title="Шторы кухня: утро выходных",
        description=(
            "Перенос из Node-RED: открыть шторы кухни на 80% не раньше 09:30 "
            "в выходные. Праздничный календарь Node-RED не переносится."
        ),
        icon="mdi:blinds-open",
        triggers=({"id": "t1", "type": "time", "value": "09:30"},),
        conditions=({"id": "c1", "type": "weekday", "value": "сб, вс"},),
        actions=(_device_action("a1", COVER_KITCHEN, "set_position", 80),),
        required_entities=(COVER_KITCHEN,),
    ),
    SystemScenarioSeed(
        scenario_id="system-leak-toilet-alert",
        title="Протечка туалет: оповещение",
        description="Перенос из Node-RED: датчик протечки туалета сообщил о воде.",
        icon="mdi:water-alert",
        triggers=(_device_trigger("t1", LEAK_TOILET, "equals", "on"),),
        conditions=(),
        actions=(
            _notify(
                "a1", "Протечка в туалете: датчик сообщил о воде. Проверьте сантехнику."
            ),
        ),
        required_entities=(LEAK_TOILET,),
    ),
    SystemScenarioSeed(
        scenario_id="system-leak-bathroom-alert",
        title="Протечка ванная: оповещение",
        description="Перенос из Node-RED: датчик протечки ванной сообщил о воде.",
        icon="mdi:water-alert",
        triggers=(_device_trigger("t1", LEAK_BATHROOM, "equals", "on"),),
        conditions=(),
        actions=(
            _notify(
                "a1", "Протечка в ванной: датчик сообщил о воде. Проверьте сантехнику."
            ),
        ),
        required_entities=(LEAK_BATHROOM,),
    ),
    SystemScenarioSeed(
        scenario_id="system-leak-kitchen-alert",
        title="Протечка кухня: оповещение",
        description="Перенос из Node-RED: датчик протечки кухни сообщил о воде.",
        icon="mdi:water-alert",
        triggers=(_device_trigger("t1", LEAK_KITCHEN, "equals", "on"),),
        conditions=(),
        actions=(
            _notify(
                "a1", "Протечка на кухне: датчик сообщил о воде. Проверьте сантехнику."
            ),
        ),
        required_entities=(LEAK_KITCHEN,),
    ),
    SystemScenarioSeed(
        scenario_id="system-leak-extra-bathroom-alert",
        title="Протечка доп ванная: оповещение",
        description=(
            "Перенос из Node-RED: датчик протечки Sonoff сообщил о воде. "
            "Автоматическое перекрытие воды не переносится: привязка к "
            "редукторам в Node-RED была неявной."
        ),
        icon="mdi:water-alert",
        triggers=(_device_trigger("t1", LEAK_EXTRA, "equals", "on"),),
        conditions=(),
        actions=(
            _notify(
                "a1",
                "Протечка (доп ванная): датчик сообщил о воде. Проверьте сантехнику.",
            ),
        ),
        required_entities=(LEAK_EXTRA,),
    ),
    SystemScenarioSeed(
        scenario_id="system-toilet-light-motion",
        title="Туалет: основной свет днём",
        description=(
            "Движение днём включает только основной свет. Через 8 минут без "
            "нового движения оба канала выключаются, таймер перезапускается."
        ),
        icon="mdi:motion-sensor",
        execution_mode="restart",
        triggers=(
            _device_trigger("t1", MOTION_TOILET, "equals", "on"),
            _device_trigger("t2", MOTION_TOILET_TUYA, "equals", "on"),
        ),
        conditions=(
            _device_condition("c1", SUN, "equals", "above_horizon"),
            _device_condition("c2", AWAY_A100, "equals", "off"),
        ),
        actions=(
            _device_action("a1", SWITCH_TOILET_LIGHT_2, "turn_on"),
            _delay("a2", 480),
            _device_action("a3", SWITCH_TOILET_LIGHT_1, "turn_off"),
            _device_action("a4", SWITCH_TOILET_LIGHT_2, "turn_off"),
        ),
        required_entities=(
            MOTION_TOILET,
            MOTION_TOILET_TUYA,
            SWITCH_TOILET_LIGHT_1,
            SWITCH_TOILET_LIGHT_2,
            AWAY_A100,
            SUN,
        ),
    ),
    SystemScenarioSeed(
        scenario_id="system-toilet-light-motion-evening",
        title="Туалет: основной свет вечером",
        description=(
            "После заката и до 23:00 движение включает только основной свет. "
            "Через 8 минут без нового движения оба канала выключаются."
        ),
        icon="mdi:motion-sensor",
        execution_mode="restart",
        triggers=(
            _device_trigger("t1", MOTION_TOILET, "equals", "on"),
            _device_trigger("t2", MOTION_TOILET_TUYA, "equals", "on"),
        ),
        conditions=(
            {"id": "c1", "type": "time_window", "value": "12:00-22:59"},
            _device_condition("c2", SUN, "equals", "below_horizon"),
            _device_condition("c3", AWAY_A100, "equals", "off"),
        ),
        actions=(
            _device_action("a1", SWITCH_TOILET_LIGHT_2, "turn_on"),
            _delay("a2", 480),
            _device_action("a3", SWITCH_TOILET_LIGHT_1, "turn_off"),
            _device_action("a4", SWITCH_TOILET_LIGHT_2, "turn_off"),
        ),
        required_entities=(
            MOTION_TOILET,
            MOTION_TOILET_TUYA,
            SWITCH_TOILET_LIGHT_1,
            SWITCH_TOILET_LIGHT_2,
            AWAY_A100,
            SUN,
        ),
    ),
    SystemScenarioSeed(
        scenario_id="system-toilet-light-motion-night",
        title="Туалет: дополнительный свет ночью",
        description=(
            "С 23:00 и до рассвета движение включает только дополнительный "
            "свет. Через 8 минут без нового движения оба канала выключаются."
        ),
        icon="mdi:weather-night",
        execution_mode="restart",
        triggers=(
            _device_trigger("t1", MOTION_TOILET, "equals", "on"),
            _device_trigger("t2", MOTION_TOILET_TUYA, "equals", "on"),
        ),
        conditions=(
            {"id": "c1", "type": "time_window", "value": "23:00-12:00"},
            _device_condition("c2", SUN, "equals", "below_horizon"),
            _device_condition("c3", AWAY_A100, "equals", "off"),
        ),
        actions=(
            _device_action("a1", SWITCH_TOILET_LIGHT_1, "turn_on"),
            _delay("a2", 480),
            _device_action("a3", SWITCH_TOILET_LIGHT_1, "turn_off"),
            _device_action("a4", SWITCH_TOILET_LIGHT_2, "turn_off"),
        ),
        required_entities=(
            MOTION_TOILET,
            MOTION_TOILET_TUYA,
            SWITCH_TOILET_LIGHT_1,
            SWITCH_TOILET_LIGHT_2,
            AWAY_A100,
            SUN,
        ),
    ),
    SystemScenarioSeed(
        scenario_id="system-toilet-fan-day",
        title="Туалет: вытяжка днём со светом",
        description=(
            "Перенос из Node-RED: свет в туалете включает вытяжку, но не в "
            "тихие часы 22:30-08:30."
        ),
        icon="mdi:fan",
        triggers=(
            _device_trigger("t1", SWITCH_TOILET_LIGHT_1, "equals", "on"),
            _device_trigger("t2", SWITCH_TOILET_LIGHT_2, "equals", "on"),
        ),
        conditions=({"id": "c1", "type": "time_window", "value": "08:30-22:30"},),
        actions=(_device_action("a1", SWITCH_TOILET_FAN, "turn_on"),),
        required_entities=(
            SWITCH_TOILET_LIGHT_1,
            SWITCH_TOILET_LIGHT_2,
            SWITCH_TOILET_FAN,
        ),
    ),
    SystemScenarioSeed(
        scenario_id="system-toilet-fan-off-delay",
        title="Туалет: вытяжка off после света",
        description=(
            "Перенос из Node-RED: свет погас - вытяжка выключается через "
            "3 минуты, только если оба канала оставались выключенными весь "
            "интервал. Новое включение отменяет отложенный запуск."
        ),
        icon="mdi:fan-off",
        execution_mode="restart",
        triggers=(
            _device_trigger("t1", SWITCH_TOILET_LIGHT_1, "changed"),
            _device_trigger("t2", SWITCH_TOILET_LIGHT_2, "changed"),
        ),
        conditions=(
            _device_condition("c1", SWITCH_TOILET_LIGHT_1, "equals", "off"),
            _device_condition("c2", SWITCH_TOILET_LIGHT_2, "equals", "off"),
        ),
        actions=(
            _delay("a1", 180),
            _device_action("a2", SWITCH_TOILET_FAN, "turn_off"),
        ),
        required_entities=(
            SWITCH_TOILET_LIGHT_1,
            SWITCH_TOILET_LIGHT_2,
            SWITCH_TOILET_FAN,
        ),
    ),
    SystemScenarioSeed(
        scenario_id="system-bathroom-fan-day-light-1",
        title="Ванная: вытяжка днём, линия 1",
        description=(
            "Shadow-перенос Node-RED: днём линия 1 включает вытяжку только "
            "при влажности не ниже 65%."
        ),
        icon="mdi:fan",
        command_mode="shadow",
        triggers=(
            _device_trigger("t_humidity", HUMIDITY_BATHROOM, "changed"),
            _device_trigger("t_light", SWITCH_BATHROOM_LIGHT_1, "equals", "on"),
            {"id": "t_start", "type": "time", "value": "08:00"},
        ),
        conditions=(
            {"id": "c_window", "type": "time_window", "value": "08:00-22:00"},
            _device_condition("c_humidity", HUMIDITY_BATHROOM, "above", 64.999),
            _device_condition("c_light", SWITCH_BATHROOM_LIGHT_1, "equals", "on"),
        ),
        actions=(_device_action("a1", SWITCH_BATHROOM_FAN, "turn_on"),),
        required_entities=(
            HUMIDITY_BATHROOM,
            SWITCH_BATHROOM_LIGHT_1,
            SWITCH_BATHROOM_FAN,
        ),
    ),
    SystemScenarioSeed(
        scenario_id="system-bathroom-fan-day-light-2",
        title="Ванная: вытяжка днём, линия 2",
        description=(
            "Shadow-перенос Node-RED: днём линия 2 включает вытяжку только "
            "при влажности не ниже 65%."
        ),
        icon="mdi:fan",
        command_mode="shadow",
        triggers=(
            _device_trigger("t_humidity", HUMIDITY_BATHROOM, "changed"),
            _device_trigger("t_light", SWITCH_BATHROOM_LIGHT_2, "equals", "on"),
            {"id": "t_start", "type": "time", "value": "08:00"},
        ),
        conditions=(
            {"id": "c_window", "type": "time_window", "value": "08:00-22:00"},
            _device_condition("c_humidity", HUMIDITY_BATHROOM, "above", 64.999),
            _device_condition("c_light", SWITCH_BATHROOM_LIGHT_2, "equals", "on"),
        ),
        actions=(_device_action("a1", SWITCH_BATHROOM_FAN, "turn_on"),),
        required_entities=(
            HUMIDITY_BATHROOM,
            SWITCH_BATHROOM_LIGHT_2,
            SWITCH_BATHROOM_FAN,
        ),
    ),
    SystemScenarioSeed(
        scenario_id="system-bathroom-fan-night-light-1",
        title="Ванная: вытяжка ночью, линия 1",
        description="Shadow-перенос Node-RED: линия 1 включает вытяжку с 22:00 до 06:00.",
        icon="mdi:fan",
        command_mode="shadow",
        triggers=(
            _device_trigger("t_light", SWITCH_BATHROOM_LIGHT_1, "equals", "on"),
            {"id": "t_start", "type": "time", "value": "22:00"},
        ),
        conditions=(
            {"id": "c_window", "type": "time_window", "value": "22:00-06:00"},
            _device_condition("c_light", SWITCH_BATHROOM_LIGHT_1, "equals", "on"),
        ),
        actions=(_device_action("a1", SWITCH_BATHROOM_FAN, "turn_on"),),
        required_entities=(SWITCH_BATHROOM_LIGHT_1, SWITCH_BATHROOM_FAN),
    ),
    SystemScenarioSeed(
        scenario_id="system-bathroom-fan-night-light-2",
        title="Ванная: вытяжка ночью, линия 2",
        description="Shadow-перенос Node-RED: линия 2 включает вытяжку с 22:00 до 06:00.",
        icon="mdi:fan",
        command_mode="shadow",
        triggers=(
            _device_trigger("t_light", SWITCH_BATHROOM_LIGHT_2, "equals", "on"),
            {"id": "t_start", "type": "time", "value": "22:00"},
        ),
        conditions=(
            {"id": "c_window", "type": "time_window", "value": "22:00-06:00"},
            _device_condition("c_light", SWITCH_BATHROOM_LIGHT_2, "equals", "on"),
        ),
        actions=(_device_action("a1", SWITCH_BATHROOM_FAN, "turn_on"),),
        required_entities=(SWITCH_BATHROOM_LIGHT_2, SWITCH_BATHROOM_FAN),
    ),
    SystemScenarioSeed(
        scenario_id="system-bathroom-fan-morning-light-1",
        title="Ванная: вытяжка утром, линия 1",
        description=(
            "Shadow-перенос Node-RED: с 06:00 до 08:00 линия 1 включает "
            "вытяжку, только когда линия 2 выключена."
        ),
        icon="mdi:fan",
        command_mode="shadow",
        triggers=(
            _device_trigger("t_light_1", SWITCH_BATHROOM_LIGHT_1, "equals", "on"),
            _device_trigger("t_light_2", SWITCH_BATHROOM_LIGHT_2, "equals", "off"),
            {"id": "t_start", "type": "time", "value": "06:00"},
        ),
        conditions=(
            {"id": "c_window", "type": "time_window", "value": "06:00-08:00"},
            _device_condition("c_light_1", SWITCH_BATHROOM_LIGHT_1, "equals", "on"),
            _device_condition("c_light_2", SWITCH_BATHROOM_LIGHT_2, "equals", "off"),
        ),
        actions=(_device_action("a1", SWITCH_BATHROOM_FAN, "turn_on"),),
        required_entities=(
            SWITCH_BATHROOM_LIGHT_1,
            SWITCH_BATHROOM_LIGHT_2,
            SWITCH_BATHROOM_FAN,
        ),
    ),
    SystemScenarioSeed(
        scenario_id="system-bathroom-fan-morning-quiet",
        title="Ванная: тихое утро, линия 2",
        description="Shadow-перенос Node-RED: с 06:00 до 08:00 линия 2 не оставляет вытяжку включённой.",
        icon="mdi:fan-off",
        command_mode="shadow",
        triggers=(
            _device_trigger("t_light", SWITCH_BATHROOM_LIGHT_2, "equals", "on"),
            {"id": "t_start", "type": "time", "value": "06:00"},
        ),
        conditions=(
            {"id": "c_window", "type": "time_window", "value": "06:00-08:00"},
            _device_condition("c_light", SWITCH_BATHROOM_LIGHT_2, "equals", "on"),
        ),
        actions=(_device_action("a1", SWITCH_BATHROOM_FAN, "turn_off"),),
        required_entities=(SWITCH_BATHROOM_LIGHT_2, SWITCH_BATHROOM_FAN),
    ),
    SystemScenarioSeed(
        scenario_id="system-bathroom-fan-off-night",
        title="Ванная: вытяжка off ночью",
        description="Shadow-перенос Node-RED: ночью оба погасших канала выключают вытяжку сразу.",
        icon="mdi:fan-off",
        command_mode="shadow",
        triggers=(
            _device_trigger("t1", SWITCH_BATHROOM_LIGHT_1, "equals", "off"),
            _device_trigger("t2", SWITCH_BATHROOM_LIGHT_2, "equals", "off"),
            {"id": "t_start", "type": "time", "value": "22:00"},
        ),
        conditions=(
            _device_condition("c1", SWITCH_BATHROOM_LIGHT_1, "equals", "off"),
            _device_condition("c2", SWITCH_BATHROOM_LIGHT_2, "equals", "off"),
            {"id": "c_window", "type": "time_window", "value": "22:00-08:00"},
        ),
        actions=(_device_action("a1", SWITCH_BATHROOM_FAN, "turn_off"),),
        required_entities=(
            SWITCH_BATHROOM_LIGHT_1,
            SWITCH_BATHROOM_LIGHT_2,
            SWITCH_BATHROOM_FAN,
        ),
    ),
    SystemScenarioSeed(
        scenario_id="system-bathroom-fan-off-day-sustained",
        title="Ванная: вытяжка off после света",
        description=(
            "Shadow-перенос Node-RED: днём вытяжка выключается, если оба "
            "канала света оставались выключенными 30 минут."
        ),
        icon="mdi:fan-off",
        command_mode="shadow",
        execution_mode="restart",
        triggers=(
            _device_trigger("t1", SWITCH_BATHROOM_LIGHT_1, "changed"),
            _device_trigger("t2", SWITCH_BATHROOM_LIGHT_2, "changed"),
        ),
        conditions=(
            _device_condition("c1", SWITCH_BATHROOM_LIGHT_1, "equals", "off"),
            _device_condition("c2", SWITCH_BATHROOM_LIGHT_2, "equals", "off"),
            {"id": "c_window", "type": "time_window", "value": "08:00-22:00"},
        ),
        actions=(
            _delay("a1", 1800),
            _device_action("a2", SWITCH_BATHROOM_FAN, "turn_off"),
        ),
        required_entities=(
            SWITCH_BATHROOM_LIGHT_1,
            SWITCH_BATHROOM_LIGHT_2,
            SWITCH_BATHROOM_FAN,
        ),
    ),
    SystemScenarioSeed(
        scenario_id="system-away-turn-off",
        title="Не дома: безопасное выключение дома",
        description=(
            "При уходе выключаются свет, кондиционеры, чайник, вытяжки, "
            "увлажнители и телевизоры, затем закрываются горячая и холодная "
            "вода. Охрана, сеть, холодильники, датчики протечки и голосовые "
            "оповещения остаются включёнными."
        ),
        icon="mdi:home-export-outline",
        command_mode="live",
        triggers=({"id": "t1", "type": "presence", "value": "away"},),
        conditions=(),
        actions=(
            _notify(
                "a99",
                "Режим «не дома»: свет, климат и бытовые нагрузки выключены, "
                "вода закрыта.",
            ),
        ),
        optional_actions=AWAY_OFF_ENTITIES,
    ),
    # Домофон не сеется: удержание реле 15 секунд уже встроено в движок
    # (ScenarioService.async_schedule_intercom_release), отдельный сценарий
    # дублировал бы штатное поведение.
)


async def async_seed_system_scenarios(service: ScenarioService) -> tuple[str, ...]:
    """Создать отсутствующие системные сценарии. Идемпотентно."""

    catalog = service.current_catalog()
    existing = {scenario.id for scenario in await service.async_list_scenarios()}
    created: list[str] = []
    for seed in SYSTEM_SCENARIO_SEEDS:
        if seed.scenario_id in existing:
            continue
        payload = seed.build_payload(catalog)
        if payload is None:
            continue
        try:
            await service.async_update_scenario(payload)
        except Exception:
            _LOGGER.warning(
                "system scenario %s failed validation; will retry on next start",
                seed.scenario_id,
                exc_info=True,
            )
            continue
        created.append(seed.scenario_id)
    return tuple(created)
