"""Contract and grouping tests for the universal dashboard projection."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

from custom_components.hausman_hub.application.dashboard_snapshot import (
    DashboardArea,
    DashboardDevice,
    DashboardEntity,
    DashboardScenario,
    build_dashboard_snapshot,
)
from custom_components.hausman_hub.domain.hub_settings import HausmanHubSettings


ROOT = Path(__file__).resolve().parents[1]


class DashboardSnapshotTest(unittest.TestCase):
    """Keep the tablet projection generic and one-card-per-device."""

    def setUp(self) -> None:
        self.snapshot = build_dashboard_snapshot(
            areas=(
                DashboardArea("living", "Гостиная", "mdi:sofa"),
                DashboardArea("kitchen", "Кухня", "mdi:countertop"),
            ),
            devices=(
                DashboardDevice(
                    "ha-device-ac",
                    "Кондиционер",
                    "living",
                    "AC Demo",
                    "Example",
                ),
                DashboardDevice("ha-device-kettle", "Чайник", "kitchen"),
                DashboardDevice("ha-device-leak", "Датчик протечки", "kitchen"),
            ),
            entities=(
                DashboardEntity(
                    "climate.living",
                    "climate",
                    "cool",
                    "Кондиционер",
                    {
                        "temperature": 25,
                        "current_temperature": 25.4,
                        "fan_mode": "medium",
                        "access_token": "must-not-leak",
                    },
                    "ha-device-ac",
                    "living",
                ),
                DashboardEntity(
                    "sensor.living_temperature",
                    "sensor",
                    "25.4",
                    "Температура",
                    {"device_class": "temperature", "unit_of_measurement": "°C"},
                    "ha-device-ac",
                    "living",
                ),
                DashboardEntity(
                    "switch.kettle",
                    "switch",
                    "on",
                    "Нагрев",
                    {},
                    "ha-device-kettle",
                    "kitchen",
                ),
                DashboardEntity(
                    "sensor.kettle_power",
                    "sensor",
                    "1850",
                    "Мощность",
                    {"device_class": "power", "unit_of_measurement": "W"},
                    "ha-device-kettle",
                    "kitchen",
                ),
                DashboardEntity(
                    "sensor.kettle_current",
                    "sensor",
                    "8.04",
                    "Ток",
                    {"device_class": "current", "unit_of_measurement": "A"},
                    "ha-device-kettle",
                    "kitchen",
                ),
                DashboardEntity(
                    "sensor.kettle_voltage",
                    "sensor",
                    "230.1",
                    "Напряжение",
                    {"device_class": "voltage", "unit_of_measurement": "V"},
                    "ha-device-kettle",
                    "kitchen",
                ),
                DashboardEntity(
                    "binary_sensor.kitchen_leak",
                    "binary_sensor",
                    "on",
                    "Протечка под мойкой",
                    {"device_class": "moisture"},
                    "ha-device-leak",
                    "kitchen",
                ),
                DashboardEntity(
                    "light.floor_lamp",
                    "light",
                    "on",
                    "Торшер",
                    {"brightness": 180},
                    None,
                    "living",
                ),
                DashboardEntity(
                    "sensor.orphan_diagnostic",
                    "sensor",
                    "42",
                    "Диагностика",
                    {"device_class": "signal_strength"},
                ),
                DashboardEntity(
                    "weather.home",
                    "weather",
                    "partlycloudy",
                    "Погода",
                    {
                        "temperature": 29.4,
                        "humidity": 52,
                        "apparent_temperature": 30.1,
                        "wind_speed": 2.4,
                        "wind_speed_unit": "m/s",
                        "is_daytime": True,
                    },
                ),
            ),
            scenarios=(
                DashboardScenario(
                    "movie",
                    "Кино",
                    group="Комфорт",
                    favorite=True,
                ),
            ),
            generated_at_ms=1_782_225_600_000,
            local_iso="2026-06-23T20:00:00+03:00",
            state_revision=42,
            energy_settings=HausmanHubSettings(energy_use_all_devices=True),
        )

    def test_snapshot_matches_public_schema(self) -> None:
        schema_path = (
            ROOT
            / "custom_components"
            / "hausman_hub"
            / "contracts"
            / "v1"
            / "dashboard-snapshot.schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(self.snapshot)

    def test_multiple_entities_make_one_physical_device_card(self) -> None:
        devices = self.snapshot["devices"]
        self.assertEqual(4, len(devices))
        self.assertEqual(len(devices), len({device["physicalId"] for device in devices}))

        air_conditioner = next(
            device for device in devices if device["name"] == "Кондиционер"
        )
        self.assertEqual(2, len(air_conditioner["details"]))
        self.assertEqual(
            {"climate", "sensor"},
            {detail["domain"] for detail in air_conditioner["details"]},
        )

        kettle = next(device for device in devices if device["name"] == "Чайник")
        self.assertEqual(4, len(kettle["details"]))
        self.assertNotIn(
            "sensor.orphan_diagnostic",
            {device["entityId"] for device in devices},
        )

    def test_cards_use_physical_purpose_instead_of_diagnostics_or_services(self) -> None:
        snapshot = build_dashboard_snapshot(
            areas=(DashboardArea("kitchen", "Кухня"),),
            devices=(
                DashboardDevice(
                    "climate-sensor",
                    "Климат кухни",
                    "kitchen",
                    "Temperature and humidity sensor",
                    "Kojima",
                    integrations=("mqtt",),
                ),
                DashboardDevice(
                    "leak-sensor",
                    "Датчик протечки",
                    "kitchen",
                    "Water leak sensor",
                    "Tuya",
                    integrations=("mqtt",),
                ),
                DashboardDevice(
                    "backup-service",
                    "Backup",
                    entry_type="service",
                    integrations=("backup",),
                ),
                DashboardDevice(
                    "yandex-buttons",
                    "Кондиционер 25",
                    model="YNDX-0006",
                    manufacturer="Yandex",
                    integrations=("yandex_smart_home",),
                ),
                DashboardDevice(
                    "yandex-speaker",
                    "Станция Мини",
                    "kitchen",
                    "Станция Мини",
                    "Яндекс",
                    integrations=("yandex_station",),
                ),
            ),
            entities=(
                DashboardEntity(
                    "sensor.kitchen_battery",
                    "sensor",
                    "100",
                    "Климат кухни Батарея",
                    {"device_class": "battery", "unit_of_measurement": "%"},
                    "climate-sensor",
                    "kitchen",
                ),
                DashboardEntity(
                    "sensor.kitchen_humidity",
                    "sensor",
                    "53",
                    "Климат кухни Влажность",
                    {"device_class": "humidity", "unit_of_measurement": "%"},
                    "climate-sensor",
                    "kitchen",
                ),
                DashboardEntity(
                    "sensor.kitchen_temperature",
                    "sensor",
                    "26.3",
                    "Климат кухни Температура",
                    {"device_class": "temperature", "unit_of_measurement": "°C"},
                    "climate-sensor",
                    "kitchen",
                ),
                DashboardEntity(
                    "binary_sensor.leak_battery_low",
                    "binary_sensor",
                    "off",
                    "Датчик протечки Батарея",
                    {"device_class": "battery"},
                    "leak-sensor",
                    "kitchen",
                ),
                DashboardEntity(
                    "binary_sensor.leak_water",
                    "binary_sensor",
                    "off",
                    "Датчик протечки Влага",
                    {"device_class": "moisture"},
                    "leak-sensor",
                    "kitchen",
                ),
                DashboardEntity(
                    "sensor.leak_battery",
                    "sensor",
                    "37",
                    "Датчик протечки Батарея",
                    {"device_class": "battery", "unit_of_measurement": "%"},
                    "leak-sensor",
                    "kitchen",
                ),
                DashboardEntity(
                    "sensor.backup_state",
                    "sensor",
                    "idle",
                    "Backup Состояние",
                    {"device_class": "enum"},
                    "backup-service",
                ),
                DashboardEntity(
                    "button.yandex_mute",
                    "button",
                    "unknown",
                    "Кондиционер 25 Отключить звук",
                    {},
                    "yandex-buttons",
                ),
                DashboardEntity(
                    "remote.yandex_ac",
                    "remote",
                    "unavailable",
                    "Кондиционер 25",
                    {},
                    "yandex-buttons",
                ),
                DashboardEntity(
                    "media_player.yandex_mini",
                    "media_player",
                    "idle",
                    "Станция Мини",
                    {},
                    "yandex-speaker",
                    "kitchen",
                ),
            ),
            generated_at_ms=1,
            local_iso="2026-07-31T16:00:00+06:00",
        )

        self.assertEqual(
            {"Климат кухни", "Датчик протечки", "Станция Мини"},
            {device["name"] for device in snapshot["devices"]},
        )
        climate = next(
            device for device in snapshot["devices"] if device["name"] == "Климат кухни"
        )
        self.assertEqual("temperature", climate["category"])
        self.assertEqual("sensor.kitchen_temperature", climate["entityId"])
        leak = next(
            device
            for device in snapshot["devices"]
            if device["name"] == "Датчик протечки"
        )
        self.assertEqual("moisture", leak["category"])
        self.assertEqual("binary_sensor.leak_water", leak["entityId"])
        self.assertEqual(1, sum(detail["label"] == "Заряд" for detail in leak["details"]))
        self.assertEqual(
            {"physical", "virtual"},
            {device["kind"] for device in snapshot["inventory"]["devices"]},
        )
        self.assertEqual(2, snapshot["inventory"]["summary"]["virtualCount"])

    def test_snapshot_is_read_only_and_filters_private_attributes(self) -> None:
        serialized = json.dumps(self.snapshot, ensure_ascii=False)
        self.assertNotIn("access_token", serialized)
        self.assertNotIn("must-not-leak", serialized)
        self.assertFalse(self.snapshot["capabilities"]["actions"])
        self.assertFalse(self.snapshot["capabilities"]["events"])
        self.assertTrue(all(not device["actions"] for device in self.snapshot["devices"]))

    def test_energy_groups_measurements_by_physical_device(self) -> None:
        energy = self.snapshot["energy"]
        self.assertTrue(energy["available"])
        self.assertEqual(1850.0, energy["currentPowerW"])
        self.assertEqual(8.04, energy["currentA"])
        self.assertEqual(230.1, energy["voltageV"])
        self.assertEqual(1, len(energy["sources"]))
        self.assertEqual("Чайник", energy["sources"][0]["name"])
        self.assertEqual("watts", energy["settings"]["displayUnits"])

    def test_energy_source_is_offline_when_switch_is_unavailable(self) -> None:
        snapshot = build_dashboard_snapshot(
            areas=(),
            devices=(DashboardDevice("breaker", "Автомат", None),),
            entities=(
                DashboardEntity(
                    "switch.breaker",
                    "switch",
                    "unavailable",
                    "Автомат",
                    {},
                    "breaker",
                ),
                DashboardEntity(
                    "sensor.breaker_power",
                    "sensor",
                    "0",
                    "Мощность",
                    {"device_class": "power", "unit_of_measurement": "W"},
                    "breaker",
                ),
                DashboardEntity(
                    "sensor.breaker_voltage",
                    "sensor",
                    "224",
                    "Напряжение",
                    {"device_class": "voltage", "unit_of_measurement": "V"},
                    "breaker",
                ),
            ),
            generated_at_ms=1,
            local_iso="2026-08-01T12:00:00+06:00",
            energy_settings=HausmanHubSettings(energy_use_all_devices=True),
        )

        source = snapshot["energy"]["sources"][0]
        self.assertFalse(source["available"])
        self.assertIsNone(source["powered"])
        self.assertFalse(snapshot["energy"]["available"])
        self.assertIsNone(snapshot["energy"]["currentPowerW"])
        self.assertIsNone(snapshot["energy"]["voltageV"])

    def test_energy_source_reports_switch_state_separately_from_reachability(self) -> None:
        snapshot = build_dashboard_snapshot(
            areas=(),
            devices=(DashboardDevice("breaker", "Автомат", None),),
            entities=(
                DashboardEntity(
                    "switch.breaker", "switch", "off", "Автомат", {}, "breaker"
                ),
                DashboardEntity(
                    "sensor.breaker_power",
                    "sensor",
                    "0",
                    "Мощность",
                    {"device_class": "power", "unit_of_measurement": "W"},
                    "breaker",
                ),
                DashboardEntity(
                    "sensor.breaker_voltage",
                    "sensor",
                    "224",
                    "Напряжение",
                    {"device_class": "voltage", "unit_of_measurement": "V"},
                    "breaker",
                ),
            ),
            generated_at_ms=1,
            local_iso="2026-08-01T12:00:00+06:00",
            energy_settings=HausmanHubSettings(energy_use_all_devices=True),
        )

        source = snapshot["energy"]["sources"][0]
        self.assertTrue(source["available"])
        self.assertFalse(source["powered"])
        self.assertEqual(0.0, source["currentPowerW"])
        self.assertEqual(224.0, source["voltageV"])

    def test_default_energy_selection_avoids_implicit_double_counting(self) -> None:
        snapshot = build_dashboard_snapshot(
            areas=(),
            devices=(),
            entities=(
                DashboardEntity(
                    "sensor.whole_home_power",
                    "sensor",
                    "1000",
                    "Общий счётчик",
                    {"device_class": "power", "unit_of_measurement": "W"},
                    "whole-home-meter",
                ),
                DashboardEntity(
                    "sensor.plug_power",
                    "sensor",
                    "200",
                    "Розетка",
                    {"device_class": "power", "unit_of_measurement": "W"},
                    "child-plug",
                ),
            ),
            generated_at_ms=1,
            local_iso="2026-07-30T12:00:00+06:00",
        )

        self.assertFalse(snapshot["energy"]["available"])
        self.assertEqual([], snapshot["energy"]["selectedSourceIds"])
        self.assertFalse(snapshot["energy"]["settings"]["useAllDevices"])

    def test_battery_voltage_does_not_create_an_energy_device(self) -> None:
        """A lock or sensor battery is not a mains load merely because it reports volts."""

        snapshot = build_dashboard_snapshot(
            areas=(DashboardArea("hall", "Тамбур"),),
            devices=(
                DashboardDevice("lock", "Умный замок", "hall"),
                DashboardDevice("meter", "Сетевой вольтметр", "hall"),
            ),
            entities=(
                DashboardEntity(
                    "sensor.lock_voltage",
                    "sensor",
                    "6",
                    "Напряжение батареи",
                    {"device_class": "voltage", "unit_of_measurement": "V"},
                    "lock",
                    "hall",
                ),
                DashboardEntity(
                    "sensor.mains_voltage",
                    "sensor",
                    "223",
                    "Напряжение сети",
                    {"device_class": "voltage", "unit_of_measurement": "V"},
                    "meter",
                    "hall",
                ),
            ),
            generated_at_ms=1,
            local_iso="2026-07-30T18:00:00+06:00",
            energy_settings=HausmanHubSettings(energy_use_all_devices=True),
        )

        sources = snapshot["energy"]["sources"]
        self.assertEqual(["Сетевой вольтметр"], [source["name"] for source in sources])
        self.assertEqual(223.0, snapshot["energy"]["voltageV"])

    def test_room_weather_alarm_and_summary_are_human_readable(self) -> None:
        living = next(room for room in self.snapshot["rooms"] if room["id"] == "living")
        self.assertEqual(25.4, living["temp"])
        self.assertEqual(25.0, living["targetTemp"])
        self.assertEqual("cool", living["climateState"])
        self.assertTrue(living["climateRunning"])

        summary = self.snapshot["summary"]
        self.assertEqual(2.4, summary["weatherWindSpeed"])
        self.assertEqual("m/s", summary["weatherWindSpeedUnit"])
        self.assertEqual(1, summary["activeLights"])
        self.assertEqual(1, summary["activeClimate"])
        self.assertEqual(1, summary["activeAlarms"])

        alarm = self.snapshot["alarms"][0]
        self.assertTrue(alarm["active"])
        self.assertEqual("bad", alarm["level"])
        self.assertEqual("kitchen", alarm["roomId"])

    def test_inventory_canonicalizes_only_probable_virtual_duplicates(self) -> None:
        snapshot = build_dashboard_snapshot(
            areas=(DashboardArea("kids", "Детская"), DashboardArea("living", "Гостиная")),
            devices=(
                DashboardDevice(
                    "yandex-ac-live",
                    "Кондиционер",
                    "kids",
                    "YNDX-0006",
                    "Yandex",
                    integrations=("yandex_smart_home",),
                ),
                DashboardDevice(
                    "yandex-ac-shadow",
                    "Кондиционер",
                    "kids",
                    "YNDX-0006",
                    "Yandex",
                    integrations=("yandex_smart_home",),
                ),
                DashboardDevice(
                    "switch-left",
                    "Выключатель гостиная",
                    "living",
                    "TS0012",
                    "Tuya",
                    integrations=("mqtt",),
                    image_url="https://www.zigbee2mqtt.io/images/devices/TS0012.png",
                ),
                DashboardDevice(
                    "switch-right",
                    "Выключатель гостиная",
                    "living",
                    "TS0012",
                    "Tuya",
                    integrations=("mqtt",),
                ),
                DashboardDevice(
                    "empty-registry-device",
                    "Старое устройство",
                    None,
                    integrations=("mqtt",),
                ),
            ),
            entities=(
                DashboardEntity(
                    "climate.kids_live",
                    "climate",
                    "cool",
                    "Кондиционер",
                    {"temperature": 24},
                    "yandex-ac-live",
                    "kids",
                ),
                DashboardEntity(
                    "climate.kids_shadow",
                    "climate",
                    "unavailable",
                    "Кондиционер",
                    {"temperature": 24},
                    "yandex-ac-shadow",
                    "kids",
                ),
                DashboardEntity(
                    "switch.living_left",
                    "switch",
                    "off",
                    "Левая клавиша",
                    {},
                    "switch-left",
                    "living",
                ),
                DashboardEntity(
                    "switch.living_right",
                    "switch",
                    "off",
                    "Правая клавиша",
                    {},
                    "switch-right",
                    "living",
                ),
            ),
            generated_at_ms=1,
            local_iso="2026-07-30T12:00:00+06:00",
        )

        visible_names = [device["name"] for device in snapshot["devices"]]
        self.assertNotIn("Кондиционер", visible_names)
        self.assertEqual(2, visible_names.count("Выключатель гостиная"))

        inventory = snapshot["inventory"]
        self.assertEqual(5, inventory["summary"]["registeredCount"])
        self.assertEqual(1, inventory["summary"]["duplicateGroupCount"])
        self.assertEqual(1, inventory["summary"]["emptyCount"])
        air_conditioners = [
            item for item in inventory["devices"] if item["name"] == "Кондиционер"
        ]
        self.assertEqual(2, len(air_conditioners))
        self.assertEqual(1, sum(item["canonical"] for item in air_conditioners))
        shadow = next(item for item in air_conditioners if not item["canonical"])
        self.assertTrue(shadow["possibleDuplicate"])
        self.assertIsNotNone(shadow["duplicateOf"])
        switch = next(item for item in inventory["devices"] if item["name"] == "Выключатель гостиная")
        self.assertEqual(
            "https://www.zigbee2mqtt.io/images/devices/TS0012.png",
            switch["imageUrl"],
        )

        living = next(room for room in snapshot["rooms"] if room["id"] == "living")
        self.assertEqual(2, len(living["deviceIds"]))
        kids = next(room for room in snapshot["rooms"] if room["id"] == "kids")
        self.assertEqual([], kids["deviceIds"])

        serialized = json.dumps(inventory, ensure_ascii=False)
        for source_id in (
            "yandex-ac-live",
            "yandex-ac-shadow",
            "switch-left",
            "switch-right",
            "empty-registry-device",
        ):
            self.assertNotIn(source_id, serialized)

    def test_media_appliance_merges_distinct_integration_facades(self) -> None:
        snapshot = build_dashboard_snapshot(
            areas=(DashboardArea("living", "Гостиная"),),
            devices=(
                DashboardDevice(
                    "tv-cast", "58PUS8506/60", "living",
                    "2021/22 Philips UHD Android TV", "TPV",
                    integrations=("cast",),
                ),
                DashboardDevice(
                    "tv-android", "58PUS8506/60", "living",
                    "2021/22 Philips UHD Android TV", "TPV",
                    integrations=("androidtv_remote",),
                ),
                DashboardDevice(
                    "tv-philips", "58PUS8506/60", "living",
                    "58PUS8506/60", "Philips",
                    integrations=("philips_js",),
                ),
                DashboardDevice(
                    "speaker-one", "Колонка", "living", "Audio 100", "Acme",
                    integrations=("cast",),
                ),
                DashboardDevice(
                    "speaker-two", "Колонка", "living", "Audio 100", "Acme",
                    integrations=("cast",),
                ),
            ),
            entities=(
                DashboardEntity(
                    "media_player.tv_cast", "media_player", "off", "Телевизор",
                    {}, "tv-cast", "living",
                ),
                DashboardEntity(
                    "media_player.tv_android", "media_player", "off", "Телевизор",
                    {}, "tv-android", "living",
                ),
                DashboardEntity(
                    "remote.tv_android", "remote", "on", "Пульт",
                    {}, "tv-android", "living",
                ),
                DashboardEntity(
                    "media_player.tv_philips", "media_player", "off", "Телевизор",
                    {}, "tv-philips", "living",
                ),
                DashboardEntity(
                    "light.tv_ambilight", "light", "off", "Ambilight",
                    {}, "tv-philips", "living",
                ),
                DashboardEntity(
                    "switch.tv_screen", "switch", "off", "Экран",
                    {}, "tv-philips", "living",
                ),
                DashboardEntity(
                    "media_player.speaker_one", "media_player", "off", "Колонка",
                    {}, "speaker-one", "living",
                ),
                DashboardEntity(
                    "media_player.speaker_two", "media_player", "off", "Колонка",
                    {}, "speaker-two", "living",
                ),
            ),
            generated_at_ms=1,
            local_iso="2026-07-31T14:00:00+06:00",
        )

        television_cards = [
            device for device in snapshot["devices"] if device["name"] == "58PUS8506/60"
        ]
        self.assertEqual(1, len(television_cards))
        television = television_cards[0]
        self.assertEqual("media_player", television["domain"])
        self.assertEqual(6, len(television["details"]))
        self.assertEqual(
            {
                "media_player.tv_cast",
                "media_player.tv_android",
                "remote.tv_android",
                "media_player.tv_philips",
                "light.tv_ambilight",
                "switch.tv_screen",
            },
            {detail["entityId"] for detail in television["details"]},
        )
        speaker_cards = [
            device for device in snapshot["devices"] if device["name"] == "Колонка"
        ]
        self.assertEqual(2, len(speaker_cards))
        self.assertEqual(
            {
                frozenset({"media_player.speaker_one"}),
                frozenset({"media_player.speaker_two"}),
            },
            {
                frozenset(detail["entityId"] for detail in device["details"])
                for device in speaker_cards
            },
        )

        inventory = snapshot["inventory"]
        self.assertEqual(1, inventory["summary"]["duplicateGroupCount"])
        television_records = [
            device for device in inventory["devices"] if device["name"] == "58PUS8506/60"
        ]
        self.assertEqual(3, len(television_records))
        self.assertEqual(1, sum(device["canonical"] for device in television_records))
        self.assertEqual(1, len({device["canonicalId"] for device in television_records}))
        self.assertEqual(
            {"cast", "androidtv_remote", "philips_js"},
            {
                integration
                for device in television_records
                for integration in device["integrations"]
            },
        )
        speaker_records = [
            device for device in inventory["devices"] if device["name"] == "Колонка"
        ]
        self.assertEqual(2, len(speaker_records))
        self.assertTrue(all(device["canonical"] for device in speaker_records))
        self.assertTrue(all(not device["possibleDuplicate"] for device in speaker_records))
        self.assertEqual(2, len({device["canonicalId"] for device in speaker_records}))

    def test_security_devices_use_russian_semantic_states_and_category(self) -> None:
        snapshot = build_dashboard_snapshot(
            areas=(DashboardArea("entry", "Тамбур"),),
            devices=(
                DashboardDevice("front-lock", "Aqara Smart Lock A100", "entry"),
                DashboardDevice("entry-alarm", "EZVIZ Alarm", "entry"),
                DashboardDevice("leak-sensor", "Датчик протечки", "entry"),
            ),
            entities=(
                DashboardEntity(
                    "lock.front_door", "lock", "locked", "Замок",
                    {}, "front-lock", "entry",
                ),
                DashboardEntity(
                    "sensor.front_lock_battery", "sensor", "88", "Заряд",
                    {"device_class": "battery", "unit_of_measurement": "%"},
                    "front-lock", "entry",
                ),
                DashboardEntity(
                    "alarm_control_panel.entry", "alarm_control_panel", "disarmed", "Охрана",
                    {}, "entry-alarm", "entry",
                ),
                DashboardEntity(
                    "binary_sensor.entry_leak", "binary_sensor", "off", "Протечка",
                    {"device_class": "moisture"}, "leak-sensor", "entry",
                ),
            ),
            generated_at_ms=1,
            local_iso="2026-08-01T12:00:00+06:00",
        )

        cards = {device["name"]: device for device in snapshot["devices"]}
        lock = cards["Aqara Smart Lock A100"]
        alarm = cards["EZVIZ Alarm"]
        leak = cards["Датчик протечки"]

        self.assertEqual(("security", "закрыт"), (lock["category"], lock["stateLabel"]))
        self.assertEqual("закрыт", lock["details"][0]["value"])
        self.assertEqual(
            ("security", "охрана выключена"),
            (alarm["category"], alarm["stateLabel"]),
        )
        self.assertEqual("Охрана", alarm["details"][0]["label"])
        self.assertEqual("охрана выключена", alarm["details"][0]["value"])
        self.assertEqual("moisture", leak["category"])
        self.assertNotEqual(lock["state"], lock["stateLabel"])
        self.assertNotEqual(alarm["state"], alarm["stateLabel"])


if __name__ == "__main__":
    unittest.main()
