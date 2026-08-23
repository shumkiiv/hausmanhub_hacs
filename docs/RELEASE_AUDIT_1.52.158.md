# Release audit HACS 1.52.158

Дата: 2026-08-23.

## Результат

HACS и operations часть направления 28 закрыты. Принятый интерфейс не
перекомпоновывался. Добавлены read-only smoke, ежедневный timer, fault matrix,
soak policy, rollback thresholds и безопасные virtual helpers.

## Версия и публикация

- Version/tag: `1.52.158`, `v1.52.158`.
- Feature commit: `b03a25b`.
- GitHub Release:
  `https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.52.158`.
- Release не содержит assets.

## Проверки

- Полный HACS gate: 1646 test, 4 skipped.
- Critical runtime: 119 test, branch coverage 75%.
- Playwright browser gate: 13 test.
- Fault matrix: 97 профильных test и 4 program guard test.
- GitHub Actions `32608714394`: success.
- Systemd daily smoke: enabled, первый запуск success.

## Production canary

- Config check до установки: успешно.
- Backup `9f28fd76`, две копии `hassio.local` и `hassio.KeeneticSSD`,
  по 905635840 байт, agent errors нет.
- Установлена версия `v1.52.158`, latest также `v1.52.158`.
- Выполнен один restart и повторный config check.
- Config entry `HausmanHub`: `loaded`.
- Проверено 88 frontend assets, несовпадений нет.
- Ошибок Hausman уровня ERROR или CRITICAL нет.
- Первый post-restart smoke увидел переходный offline count 58 вместо 10.
  Повторный smoke через 41 секунду подтвердил восстановление до 10.
- Финальный max request latency: 123 ms, active/pending operations: 0,
  fresh climate: true, физических команд: 0.

## Virtual test entities

- `input_boolean.hausmanhub_test_bez_ustroistv` для reversible canary.
- `input_number.hausmanhub_testovoe_znachenie` для numeric/stale проверок.
- Helpers не связаны с automation или физическими устройствами. Daily smoke
  их не переключает.

## Soak verdict

Релиз не меняет сеть и не меняет climate/scenario writer, поэтому новое
24-часовое и 14-дневное окно для него не требуется. Любое будущее изменение
сети требует 24 часа reconnect soak, смена writer требует 14 суток. Gate
зафиксирован в `docs/LIVE_SOAK_FAULT_CANARY_PROGRAM.md`.

## Rollback

Восстановить backup `9f28fd76` либо установить `v1.52.157`, выполнить config
check, один restart и read-only smoke. Автоматический rollback обязателен при
P0/P1, config entry не `loaded`, stale/pending старше 120 секунд,
несовпадении assets или незапланированной физической команде.
