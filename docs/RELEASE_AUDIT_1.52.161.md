# Release audit HACS 1.52.161

Дата: 2026-08-23.

## Результат

Накопленная HACS UI-линия аккуратно объединена с актуальной `main` без
перезаписи истории. В релиз вошли согласованные планшетные представления
главной, освещения, климата, безопасности, энергии, физических устройств и
сценариев. Android, backend, API, contracts, storage и исполнение команд не
менялись.

## Версия и публикация

- Version/tag: `1.52.161`, `v1.52.161`.
- UI merge commit: `d29488a`.
- Итоговый merge с последним `main`: `fa47abe`.
- Release:
  `https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.52.161`.
- Release не содержит assets.
- GitHub Actions `32616059573`: success, 4 минуты 40 секунд.

## Проверки

- Полный HACS gate: 1662 tests, 4 skipped.
- Critical runtime: 119 tests, branch coverage 75%.
- Chromium browser visual/accessibility gate: 13 tests.
- Вручную приняты четыре изменённых visual baseline: overview light,
  overview dark tablet, lighting и scenarios.
- Все конфликты merge разрешены с сохранением настроек аномалий энергии,
  обязательного подтверждения домофона и tablet power gate из `main`.

## Production deploy

- Read-only smoke до установки: passed, max latency 129 ms, unavailable 10,
  active operations 0.
- Config checks до установки, после установки и после restart: `valid`,
  HTTP 200.
- Full backup `f251700b` содержит Home Assistant, базу, 10 add-ons и `ssl`.
  Копии `hassio.local` и `hassio.KeeneticSSD` совпадают по размеру:
  914 780 160 байт. Ошибок backup-agent нет.
- HACS repository обновлён штатной WebSocket-командой refresh, затем выполнен
  точный `update.install` версии `v1.52.161` с `backup=false`.
- Выполнен один restart Home Assistant.
- Config entry `HausmanHub`: `loaded`.
- Installed/latest: `v1.52.161`.
- Все 92 frontend assets совпадают с release по SHA-256.
- Ошибок Hausman уровня ERROR/CRITICAL после restart нет.
- Первый smoke сразу после restart зафиксировал ожидаемый прогрев каталога:
  unavailable временно выросло с 10 до 58. Через 41 секунду повторный smoke
  прошёл с unavailable 10, max latency 97 ms, свежими dashboard и climate,
  active/pending operations 0, 87 устройствами, 11 ближайшими событиями и
  одним электросчётчиком.
- Существующий managed climate остаётся включённым. Во время deploy команды
  физическим устройствам не отправлялись.

## Rollback

Восстановить full backup `f251700b` либо явно установить `v1.52.160`, затем
выполнить config check, один restart и read-only smoke.
