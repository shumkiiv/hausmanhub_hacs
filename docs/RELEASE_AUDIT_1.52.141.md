# Hausman Hub HACS 1.52.141: shadow migration release audit

Дата: 2026-08-22

## Результат

- `commandMode=shadow` вычисляет условия и действия, но не вызывает
  физические Home Assistant services и не подтверждает изменение состояния.
- Operation journal сохраняет `command_mode` и redacted trace. Shadow-запись
  никогда не получает физическое `confirmed=true`.
- Восемь веток ванной перенесены из Node-RED в shadow. Away-план содержит 23
  device actions и уведомление, всего 24 действия.
- Старые live-дубли ванной, away и системных сумеречных штор отключены.
  Node-RED остаётся физическим владельцем до завершения 7-14 суток shadow.
- `turn_off` зависимого устройства при уже выключенном источнике считается
  эффективно выполненным без вызова HA. Другие команды остаются fail-safe.
- Stale target в test API возвращает структурированный HTTP 400 вместо 500.
- UI baseline `1.52.138` сохранён без визуальных правок.

## Релизы

- Feature `0161482`, release `647acee`, tag `v1.52.139`.
- Correction `9b10dbd`, release `69d4968`, tag `v1.52.140`.
- API correction `04c8057`, release `ecbe69d`, tag `v1.52.141`.
- GitHub Releases опубликованы без assets:
  https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.52.139,
  https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.52.140,
  https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.52.141.

## Проверки выпуска

- Contracts validator: 52 schemas, 97 fixtures, 39 OpenAPI paths, HACS
  source matches contracts `0.48.1` (`2f0f919`).
- Финальный staged `tools/check_local_release.py`: 1574 tests, 4 skipped.
- GitHub Actions `32559417129`, `32560627162` и `32561158937` завершены
  успешно.
- Package, fixtures, Android compatibility, README sync, version gate,
  repository safety и frontend cache contract успешны.

## Production deploy

- До изменений создан automatic full backup `fe727f76`. Копии по 914862080
  байт находятся в `hassio.local` и `hassio.KeeneticSSD`. Backup включает
  Home Assistant, базу, 10 add-ons и папки `ssl`, `share`, `media`; ошибок
  backup agents нет.
- Версии `v1.52.139`, `v1.52.140`, `v1.52.141` установлены явно через
  `update.install` с `backup=false`, используя один свежий rollback backup.
  До и после установок `homeassistant.check_config` отвечал HTTP 200. После
  каждой версии выполнен один restart.
- Node-RED не изменялся. Live `flows.json` совпадает с инвентарным backup:
  `7e2d3830ea4712531e0898a78c6c9bc53c42f80ff0b415a1831f03018a36a316`.

## Проверка после restart

- Update entity: installed/latest `v1.52.141`, in_progress false.
- Config entry HausmanHub loaded. Dashboard, upcoming events, scenario API,
  operation journal и panel отвечают HTTP 200.
- В registry 39 сценариев, 9 из них shadow. Away включён в shadow и содержит
  24 действия. Старые live-дубли выключены.
- Контрольный away shadow-run не отправил физических команд. Journal хранит
  `command_mode=shadow`, confirmed false. Запуск остановился partial на
  `power_source_unavailable`, что является ожидаемым fail-safe результатом.
- Operation journal проходит JSON Schema, sequence после проверки 435.
- Все 27 frontend assets release-коммита совпадают с production по SHA-256.
- Ошибок Hausman в system log нет. Остаются сторонние ошибки MQTT number и
  Xiaomi Miot, а также известное WARNING о расхождении уличных температур.

## Откат и следующий шаг

- При проблеме восстановить full backup `fe727f76`.
- Собирать shadow journal 7-14 суток, сравнить исходы с Node-RED и проверить
  fault/recovery. До этого физические ветки Node-RED не отключать.
