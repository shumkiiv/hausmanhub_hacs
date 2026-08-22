# Hausman Hub HACS 1.52.137: release audit

Дата: 2026-08-22

## Результат

- Исправлен release-line drift между закреплённым contracts `0.47.0` и
  vendored snapshot HACS.
- Dashboard schema получила `renameable`, добавлена water-meter schema,
  energy-meter fixture обновлён до нескольких источников.
- Hash-тест закрепляет три исправленных файла. Внешний validator подтверждает
  точное совпадение всех 52 schemas и 97 fixtures contracts с HACS source.
- Runtime сценариев и визуальная компоновка `1.52.136` не менялись.
- Feature commit `e51d58f`, release commit/tag `538bcc4`, `v1.52.137`.
- GitHub Release без assets:
  https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.52.137.

## Проверки выпуска

- Профиль contracts: 16 tests.
- External contract validator: 52 schemas, 97 fixtures, 39 OpenAPI paths,
  HACS source matches.
- Полный `python3 tools/check_local_release.py`: 1562 tests, 4 skipped.
- GitHub Actions `32555953795` завершён со статусом `success`.

## Production deploy

- До установки `homeassistant.check_config` завершился HTTP 200.
- Создан full backup `6b14607f`, 918568960 байт в `hassio.local`. В backup
  входят Home Assistant, база, 10 add-ons и `ssl`; failed-списки пусты.
- Явная `v1.52.137` установлена через `update.install` без второго backup.
- После установки `homeassistant.check_config` завершился HTTP 200. Home
  Assistant перезапущен ровно один раз и вернулся через 41.5 секунды.

## Проверка после restart

- Update entity: installed/latest `v1.52.137`, `in_progress=false`.
- Config entry Hausman Hub имеет состояние loaded.
- Доступны 12 релевантных сущностей, unavailable и unknown нет.
- Operation journal отвечает HTTP 200 и проходит JSON Schema.
- Панель отвечает HTTP 200, содержит cache version `1.52.137`; SHA-256 всех
  27 frontend assets release-коммита совпадает с production.
- System log не содержит записей Hausman Hub, ошибок и traceback нет.
- Физические команды не отправлялись.

## Откат

При проблеме восстановить full backup `6b14607f`. Он содержит состояние до
установки `v1.52.137`.
