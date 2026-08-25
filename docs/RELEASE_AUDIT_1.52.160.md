# Аудит релиза Hausman for Home Assistant 1.52.160

Дата: 2026-08-23.

## Результат

- Добавлен локальный POST API `/api/hausman_hub/v1/tablet-power-status` по
  contracts `0.57.0`. Он принимает только ограниченную телеметрию батареи,
  не сохраняет приватные идентификаторы и не отправляет физических команд.
- Home Assistant получил датчики
  `sensor.hausman_hub_tablet_battery` и
  `sensor.hausman_hub_tablet_power`. Статус устаревает через 20 минут без
  обновления.
- Добавлена безопасная политика зарядки: включить питание ниже 40%, выключить
  при 80%, между порогами ничего не менять. При недоступном датчике или
  розетке применяется fallback `включить питание` и уведомить владельца.
- В production установлен стандартный blueprint
  `hausman_hub/tablet_charging.yaml`. Автоматизация не создана, потому что в
  текущем Home Assistant нет отдельной сущности умной розетки планшета.
  Случайный выключатель намеренно не выбран.
- HACS frontend визуально не менялся. Выполнен только cache-bust версии.

## Проверки

- Полный локальный gate: 1656 tests, 4 skipped.
- Critical runtime gate: 119 tests, branch coverage 75%.
- Chromium browser gate: 13 из 13.
- GitHub Actions `32615474963` завершён успешно. Предыдущий запуск упал только
  из-за лишней тестовой зависимости PyYAML, после чего тест переведён на
  dependency-free проверку без изменения runtime.
- Тесты политики подтвердили 39% -> `turn_on`, 80% -> `turn_off`, отсутствие
  датчика или розетки -> `fallback_on`.

## Production

- Release: `v1.52.160`, runtime commit `974f942`, test-only commit `99f77e9`.
- Backup `b09b2e3c` полный, включает базу и хранится в `hassio.local` и
  `hassio.KeeneticSSD`, размер каждой копии 908881920 байт.
- После HACS refresh, установки точной версии, двух valid config checks и
  одного restart installed/latest равны `v1.52.160`, config entry загружена.
- Совпадают все 88 frontend assets. Live smoke прошёл, максимальная задержка
  118 мс, pending operations 0.
- Живые POST с 39% и 80% подтверждены operation journal как
  `tablet_power_update`; `physicalCommandsSent=false`.

## Ограничение оборудования

До подключения и явного выбора отдельной умной розетки зарядки blueprint не
может быть активирован. После появления сущности владелец выбирает её в одном
экземпляре automation. Новый релиз или restart Home Assistant для этого не
нужен.
