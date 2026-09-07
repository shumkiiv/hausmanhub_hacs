# Восемь контроллеров: новый проход реализации

> Исполнителю: использовать `superpowers:executing-plans`, проходить части
> последовательно. Координатор передаёт ограниченные части одному writer.
> Промежуточный компонент не означает готовность всего запроса.

**Цель:** настоящая миграция трёх контроллеров к восьми и работающие правила
с долговечными таймерами, ручным приоритетом и проверяемыми командами.

**Архитектура:** Node-RED получает серверный снимок и возвращает typed plan.
Сервер хранит настройки, владение и таймеры. Миграция закрывает исполнение
старого поколения до открытия нового; общей транзакции HA/Node-RED/storage
нет, поэтому корректность доказывается журналом восстановления и CAS.

**Технологии:** существующие Python/HA, Node-RED JavaScript, pytest, verified
safety storage. Android и HACS frontend не изменяются.

**Основа:** `2026-09-07-consolidation-rescue.md`, первоначальная spec и новые
14 требований владельца от 2026-09-07. Новые требования имеют приоритет.
Проход разрешён владельцем после независимого разбора. Начальный HEAD e629fa1.

## Подтверждённая отправная точка

- Закрытый снимок `/tmp/hausman-rescue-inventory-20260907-4knbgk42` содержит
  58 полных definitions, каталог, три source envelopes, 18 native rules.
  Полный config/health и секреты в публичные fixtures не копировать.
- Managed replace: душевая revision4, малый коридор3, тамбур8. Пять новых
  сценариев отсутствуют. Старые fixtures3/1/7 и hashes из цифр не использовать.
- Существующий receipt: version2, migrationId `managed-switches`, state
  `completed`, manifestHash
  `a3554f0a7108160238cbd4fd49f2f643ad3d446d7f344dec618d9a0d979d2c60`.
  Его точная копия в `managed-switch-receipt.json` рядом со снимком.
- 46 сценариев включены; часовой пояс `Asia/Omsk`.
- Aqara малого коридора: motion `entity_a371cea02388be65`, lux
  `entity_2e306a9650ac5728`, отдельного presence не найдено.
- Тамбур: motion `entity_10b78187426f8485`, presence
  `entity_156050daca86aa6c` и `entity_402b26d150a1ef3f`.
- Вытяжка кладовки не привязана, задан отдельный вопрос владельцу.

## 1. Независимые данные и безопасная миграция

Файлы: `application/scenario_consolidation_inventory.py`,
`application/scenario_consolidation_transaction.py`,
`application/managed_switch_migration.py`, `application/scenario_service.py`,
`application/scenario_node_red.py`, профильные tests/fixtures.

- [ ] Независимый fixture58, ровно3 managed, пяти новых ID нет.
- [ ] Для каждой исходной записи ровно одна операция: replace, disable,
  preserve. Create содержит expected=None и требует отсутствия ID/flow.
- [ ] Expected проверяет revision/enabled/definition/source/topology.
  Транзитивный обход run_scenario исключает воду, автоматы и защиту протечек
  по точным целям, не по названию. Неразрешённые ссылки не отключать вслепую.
- [ ] RED реального3->8, затем реализация. Не вызывать публичный CRUD из-под
  `ScenarioService._lock`: он повторно захватывает блокировку.
- [ ] Capture/CAS under lock, журнал before/after, внешняя подготовка
  отключённых flows вне lock, повторная проверка, короткий registry commit.
- [ ] Закрыть затронутые старые запуски и отменить таймеры до переключения.
  После durable completed receipt и verify открыть новое поколение.
- [ ] Явное обновление известной completed/v2 квитанции в v3. Произвольный
  старый hash отклоняется; новый completed не заменяет actual verification.
- [ ] Ошибка/отмена/restart после каждой записи и внешней операции:
  восстанавливаются только собственные неизменившиеся объекты. Чужой новый
  ID, изменение source или пользовательский drift запрещают overwrite.
- [ ] Idempotence, endpoint uniqueness, защита посторонних flow,
  отсутствие одновременного исполнения старого и нового, lock timeout test.

Native disable после готовности замен ровно5:

```text
hausman_night_absence_tambur_light_off
hausman_night_absence_small_corridor_light_off
hausman_shower_cabinet_off_absence_failsafe
hausman_shower_fan_humidity_on_failsafe
hausman_shower_fan_off_absence_normal_humidity
```

Остальные13 сохранить. Native ручное зеркало1_single/1_double не дублировать
listener. Три unavailable `codex_living_ac_virtual_*` с GET404 не трогать.
Отключённые старые definitions не удалять. Физическое применение делает
только root после всех проверок; worker тестирует с fake HA/Node-RED.

Checkpoint: механизм части 1 зафиксирован в `56c205f`. Полный pytest:
2576 passed, 4 skipped, 1235 subtests. Активация всех восьми записей
остаётся закрытой через `activation_ready=False`. Отключение конечного
набора 29 registry duplicates и замена двух ручных сцен штор выполняются
при общей приёмке после готовности источников. Точная матрица находится
в корневом `docs/migration/SCENARIO_REGISTRY_DISPOSITION_2026-09-07.md`.
Эта отметка не означает завершение остальных пунктов или проверочной цепочки.

## 2. Настройки, снимок и протокол

Файлы: `domain/scenario_controls.py`, `scenario_control_storage.py`,
backend options flow в `config_flow.py`, `application/scenario_node_red.py`.

- [ ] Сохраняемые и редактируемые policy: сроки, расписания, K, lux,
  hysteresis/hold, яркость, caps и nullable binding вытяжки.
- [ ] Валидировать ranges/взаимосвязи и capabilities. Смена policy отменяет
  старое generation. Backend options flow допустим без HACS frontend edits.
- [ ] `context.controls` с policyRevision/policy/state формируется только
  сервером, не принимается из произвольного клиентского trigger.
- [ ] Точечные snapshot allowlists для current_position четырёх штор,
  brightness и CT. Не передавать весь HA attributes.
- [ ] Все8 runtime sources проходят настоящий `async_plan`, выполняющий JS
  transport double, typed envelope, trace.id/title/status, hashes/topology.
  Файлы `tools/managed_scenarios` не считаются runtime evidence.

## 3. Координатор, кладовка, ручной приоритет и питание

Checkpoint `a4dd87a`: настройки и серверный снимок части 2, durable
координатор кладовки и её сквозной execution path проверены исполнителем.
Полный pytest: 2610 passed, 4 skipped, 1238 subtests; package PASS до
и после commit. Ручная защита общего контура, питание и последующие
комнаты ещё требуют реализации. Все восемь activation_ready=False.
Успех транспортных тестов восьми JS не означает готовность восьми правил.

Файлы: `application/scenario_control_coordinator.py`, существующие
`scenario_light_priority`, `manual_light_off_protection`,
`light_safety_obligations`, `scenario_executor`, `__init__.py`, storage JS.

- [ ] Одна серверная запись generation/policyRevision/evidence/absence start/
  deadline/transition/fractional remainder/correlation. Использовать
  VerifiedSafetyStore и существующее ownership, не второе владение в JS.
- [ ] После restart перечитать датчики и ownership перед действием.
- [ ] Motion OR presence, один установленный тип достаточен. Motion event
  реагирует сразу; presence для подъёма >10s, кладовка включается сразу.
- [ ] Unknown/unavailable не absence. Отсутствие10s либо minimum датчика
  входит в основной120/300s, не суммируется с ним. Любой положительный
  сигнал сбрасывает таймер. Кладовка sensor timeout15s уже установлен.
- [ ] Кладовка off120s от начала отсутствия, только подтверждённо свой свет.
  Boundary9.999/10/119.999/120, restart60, manual on, unavailable.
- [ ] Вытяжка11:00 и20:00 на1800s. Нет binding: понятный skip только этой
  ветки, не чужое реле и не глобальная блокировка остальных контроллеров.
- [ ] Manual верх/hold при подтверждённой поддержке100% neutral в любое
  время; приоритет выше ночных запретов. YNDX_00532 объявляет отдельные
  up/down endpoints; отсутствие буквального hold не доказывает отсутствие
  долгого нажатия. Соответствие on/off/toggle типам кликов сначала проверить.
- [ ] Возврат после absence300s либо manualoff; без sensors автосброса нет.
  Ручной источник блокирует весь взаимозаменяемый световой профиль.
- [ ] Manualoff block300s, новое explicit manualon проходит. Не присваивать
  задним числом автоматику уже горящему ручному свету.
- [ ] Питание: разрешённое relay-on, подтверждение, lamp readiness, затем
  параметры. Не отправлять яркость offline и не повторять uncertain send.

## 4. Яркость и разные расписания двух коридоров

- [ ] `fade(80,300s,floor20)==72`, шаг1 процентный пункт, дробное накопление
  исключает застревание. Это10% текущего уровня, не10 процентных пунктов.
- [ ] `ramp(5,80,cap80,30s)==80`; cap5 никогда не превышается.
- [ ] Day floor20, evening5, floor<=cap. Fade теплеет, дневной подъём
  возвращает neutral в пределах capabilities. Никаких старых75/50/25/5.
- [ ] Evening=min(sunset,21:00), независимый cap5 к23:00. Смена расписания
  не включает выключенный свет. Manual priority выше автоматических caps.
- [ ] Тамбур mirror с начала вечера; к23 main5 затем off. До09 только
  mirror автоматически;09-10 рост maincap. Unlock/away-to-home сохраняются,
  приветствие один раз. Проходной не меняет зеркало.
- [ ] Коридор новый Aqara local lux<450 днём, hysteresis/hold/self-light
  защита;23-23:30 cap5,23:30-sunrise autooff. Утро по sunrise, не09-10.
- [ ] Временные границы, ранний/поздний sunset, timezone, отмена перехода,
  lux bounce и отключённая лампа покрыты тестами настоящего execution path.

## 5. Остальные комнаты

- [ ] Душевая: светoff300s absence, ручной источник не переключать;
  fan humidity>55 или presence120s, off300 при нормальной известной humidity.
  Клавиша2 одно действие на подтверждённое нажатие. Не считать `up`
  отпусканием без доказательства: это отдельная верхняя область клавиши.
  Offline1 не подменять.
- [ ] Туалет: старые профили из snapshot взаимоисключающие, автоматический
  off480s только своего света после отсутствия; fan08:30-22:30 по свету,
  off180s после выключения обоих. `entity_3f343b8d6f58f5b4` это A100 Away,
  не presence туалета. Старый toilet motion unavailable, Tuya доступен.
- [ ] Ванная: только fan `entity_c15f5df5382ee180` как output. Световые линии
  лишь inputs. Перенести day08-22 humidity/light, night22-06 light,
  quiet06-08 и dayoff1800 из snapshot; не включать свет. Неизвестная
  humidity запрещает unsafe automaticoff.
- [ ] Кабинет: семь существующих профилей, neutral/CT компенсация по
  capabilities; lux не presence, sensors людей нет, auto manual-reset нет.
  Штора у отдельного controller, не кабинета.
- [ ] Сохранить подтверждённую инверсию TS0502B кабинета и тамбура:
  более высокий commandK физически теплее. Для тамбура command3000K
  подтверждён как нейтральный. Generic raw4000/2200 не подменяет известное
  аппаратное преобразование. Инверсия малого коридора не подтверждена.

## 6. Шторы

- [ ] Четыре targets из catalog, sunrise, manual-open из0, no-op reached.
- [ ] Clamp в последнем общем пути Hausman commands, не только JS:
  manual API, nested scenario, open -> set_position, cap80kitchen/90office.
- [ ] Шкала физического полного хода пока не подтверждена. Старый safe
  floor20 кабинета сохранять до визуальной калибровки root с владельцем.
- [ ] Calibration capability отдельная, ограниченная и недоступная через
  произвольное тело обычного запроса. Во время калибровки full travel допустим.
- [ ] Личная latch каждой шторы после manualopen following autoclose,
  сохраняется после restart, очищается только следующим sunrise, не close.
- [ ] Прямой HA service/аппаратный пульт могут обходить Hausman policy.
  Не обещать аппаратный предел до отдельной подтверждённой настройки мотора.

## 7. Общая приёмка

- [ ] На неизменённом fixture58 проходит конечная миграция, затем все8 через
  service -> async_plan -> executor с fake HA. Точные конечные hashes.
- [ ] Все fault/restart cases, dry-run без state/ownership mutations,
  corrupt safety storage fail-closed, повторный старт без duplicate listener.
- [ ] `PYTHONPATH=. pytest -q`, schema/compatibility, config-flow/storage/
  migration/restart, runtime JS и действующие release checks.
- [ ] Завершённый worker handoff, tester; только PASS допускает reviewer и
  security_auditor. Первый defect: один repair/retest, повторный BLOCKED.

Root отдельно завершает климатическую приёмку/latency, диагностику интервью,
калибровку, Issues, backup, выпуск, установку и физические тесты. Перед каждым
физическим тестом требуется объявление на всех подходящих Яндекс-устройствах;
вода и автоматы исключены, возврат состояния не затирает изменения владельца.
До всех проверок физические действия не выполнять. Неизвестные аппаратные
условия останавливают только зависимое движение, не независимый код.
