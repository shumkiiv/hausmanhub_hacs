# Release audit HACS 1.52.159

Дата: 2026-08-23.

## Результат

Направление 30 по эксплуатации и Definition of Done закрыто для HACS.
Ежедневный smoke получил redacted local alert, закреплены SLO, P0-P3,
rollback, release blocker и ежемесячный UX reminder. Принятый интерфейс не
перекомпоновывался, изменён только cache-bust версии.

## Версия и публикация

- Version/tag: `1.52.159`, `v1.52.159`.
- Feature commit: `8b58f54`.
- Release:
  `https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.52.159`.
- Release не содержит assets.
- GitHub Actions `32611390522`: success, 4 минуты 40 секунд.

## Проверки

- Полный HACS gate: 1650 tests, 4 skipped.
- Critical runtime: 119 tests, branch coverage 75%.
- Chromium browser visual/accessibility gate: 13 tests.
- Operational readiness: 4 tests; live/fault program: 5 tests.
- systemd units прошли `systemd-analyze --user verify`.
- В frontend нет изменений кроме `1.52.158 -> 1.52.159` в cache-bust.

## Production canary

- Config check до установки и после неё: `valid`.
- Automatic full backup `11c0460b`: база и Home Assistant включены,
  `failed_agent_ids` пуст.
- Защищённые копии `hassio.local` и `hassio.KeeneticSSD` по 907 284 480 байт.
- HACS repository обновлён штатной WebSocket-командой refresh, затем выполнен
  `update.install` с явной `v1.52.159` и без второго backup.
- Выполнен один restart Home Assistant.
- Config entry `HausmanHub`: `loaded`.
- Installed/latest: `v1.52.159`.
- Все 88 frontend assets совпадают с release по SHA-256.
- Финальный read-only smoke: passed, max latency 115 ms, dashboard age 192 ms,
  climate age 96 ms, fresh `true`, active/pending operations 0, unavailable
  devices 10, physical commands `false`.

## Operations

- `hausman-live-smoke.timer` активен, следующий запуск 2026-08-24.
- `OnFailure` создаёт только allowlisted P1/P2 alert без identity fields.
- `hausman-monthly-ux-audit.timer` активен, следующий запуск 2026-09-01.
- Сторонний crash/analytics SDK не подключён. Crash/ANR остаются локальными и
  входят только в redacted export после согласия.

## Rollback

Восстановить backup `11c0460b` либо установить `v1.52.158`, выполнить config
check, один restart и read-only smoke. Release блокируется при P0/P1, красном
gate, неизвестном owner, устаревшей product documentation или
неподтверждённом rollback.
