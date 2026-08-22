# Hausman Hub HACS 1.52.144: аудит настроек комнат

Дата: 2026-08-22

## Результат

- Добавлен единый серверный документ настроек комнат по contracts `0.51.0`
  (`d54aa32`): назначение, каноническая иконка, порядок и видимость.
- Запись защищена optimistic locking, сверяет полный каталог Home Assistant
  Area Registry и откатывает иконки при ошибке storage.
- Dashboard публикует `type`, `order`, `visible`, CO2, PM2.5, tVOC и
  `roomId` сценария. Комнаты сортируются по сохранённому порядку.
- Старое tablet preferences storage безопасно мигрирует с пустым документом
  комнат. Визуальная компоновка HACS не менялась, выполнен только cache-bust.

## Релиз и проверки

- Commit/tag `08f3adc`, `v1.52.144`.
- GitHub Release:
  https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.52.144.
- Contracts `0.51.0` опубликован отдельно:
  https://github.com/shumkiiv/hausmanhub-contracts/releases/tag/v0.51.0.
- Локальный release-gate: 1599 tests, 4 skipped, contracts, fixtures,
  Android compatibility, package и safety checks успешны.
- GitHub Actions `32568920665` завершён успешно за 3 минуты 29 секунд.

## Production deploy

- До установки создан full backup `e70bddac`. Копии по 923740160 байт
  подтверждены в `hassio.local` и `hassio.KeeneticSSD`.
- До и после установки `homeassistant.check_config` вернул HTTP 200.
- `v1.52.144` установлена явно через `update.install` с `backup=false`, затем
  Home Assistant перезапущен ровно один раз и вернулся примерно за 25 секунд.
- Update entity подтвердил installed/latest `v1.52.144`, panel отдаёт cache
  version `1.52.144`.

## Проверка после restart

- Room settings, water safety, dashboard, scenario list и operation journal
  проходят JSON Schema.
- Read-only GET room settings вернул 13 комнат с revision 0. PUT не вызывался,
  поэтому иконки и порядок Area Registry в production не менялись.
- Dashboard каталог комнат совпадает с room settings и содержит новые поля
  presentation и air quality. Все dashboard scenarios содержат `roomId`.
- В production 39 сценариев, из них 9 остаются в `commandMode=shadow`.
- Journal sequence равна 941. После начала текущего deploy не добавлено ни
  одной operation, физические команды при установке не отправлялись.
- Все 88 frontend JS/CSS файлов production побайтно совпадают с release.
- В system log нет ошибок Hausman. Сохранился один известный warning климата.

## Откат

- Для полного отката использовать backup `e70bddac`.
- Запись room settings откатывает Area Registry при storage failure. До
  клиентского редактора production остаётся на безопасном read-only GET.
