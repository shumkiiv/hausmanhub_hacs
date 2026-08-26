# Release audit HACS 1.52.168

Дата: 2026-08-26.

## Результат

- Runtime pin обновлён до contracts `0.58.1`, commit `b0f5811`.
- Contracts объединяет `roomIds` с накопленными `revision`,
  `contentRevision`, catalog readiness, health и `scenario_changed`.
- Vendored schemas совпадают с опубликованным contracts source.
- Runtime-логика сценариев относительно `1.52.167` не менялась.

## Проверки и публикация

- Полный local release gate: 1702 теста, 4 skipped.
- Critical runtime: 148 тестов, 77% branch coverage.
- Chromium browser gate: 13 из 13.
- Tag и Release: `v1.52.168`, release commit `2245c9b`.
- Release не содержит assets.

## Production deploy

- Повторно использован защищённый локальный backup `0907efca`: Home Assistant
  и база включены, failed agents нет, размер 883333120 байт.
- Config check принят как `valid` до установки, после установки и после
  restart.
- Выполнен точный `update.install` версии `v1.52.168` с `backup=false` и один
  restart Home Assistant.
- Installed/latest равны `v1.52.168`.
- Capabilities, dashboard, scenarios, catalog и health отвечают HTTP 200;
  доступны 41 сценарий и 41 действие каталога.
- После штатной перепубликации телеметрии планшета все 11 sensor Hausman
  доступны. Панель отдаёт asset `1.52.168`, ошибок интеграции нет.
- Физические команды и ручной запуск сценариев не выполнялись.

## Rollback

Восстановить backup `0907efca` либо явно установить `v1.52.167`, затем
выполнить config check, один restart и read-only smoke.
