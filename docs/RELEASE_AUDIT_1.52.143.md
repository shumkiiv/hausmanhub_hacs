# Hausman Hub HACS 1.52.143: аудит защиты от протечек

Дата: 2026-08-22

## Результат

- Добавлен отдельный water safety runtime по contracts `0.50.0`
  (`92b050a`): четыре датчика, два редуктора, quorum, debounce,
  получатели тревог и durable latch.
- Автозакрытие требует ручной проверки направления обоих
  редукторов и доступного notify recipient. После restart latched
  закрытие повторно подтверждается по physical read-back.
- Автоматическое открытие воды запрещено. Stale sensor или unknown
  valve state блокируют ручное открытие и сброс тревоги.
- Время протечки, состояние воды и статус команды публикуются
  в dashboard и critical SSE. Закрытие и latch clear пишутся в
  durable operation journal.
- Визуальная компоновка `1.52.142` не менялась. Frontend получил
  только cache-bust версии.

## Релиз и проверки

- Commit/tag `775ec8a`, `v1.52.143`.
- GitHub Release:
  https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.52.143.
- Contracts validator: 54 schemas, 99 fixtures, 41 OpenAPI paths, HACS source
  совпадает с contracts `0.50.0` (`92b050a`).
- Профильный gate: 215 tests и 244 subtests. Полный pytest:
  1596 passed, 4 skipped, 987 subtests. Финальный staged release-gate:
  1594 tests, 4 skipped, все fixtures, compatibility, package и safety
  checks успешны.
- GitHub Actions `32565864180` завершён успешно.

## Production deploy

- До установки создан full backup `c79a46e3`. Копии по
  922449920 байт подтверждены в `hassio.local` и `hassio.KeeneticSSD`.
- До и после установки `homeassistant.check_config` вернул HTTP 200.
- `v1.52.143` установлена явно через `update.install` с `backup=false`,
  после чего Home Assistant перезапущен ровно один раз.
- После restart update entity подтвердил installed/latest
  `v1.52.143`, `in_progress=false`; panel отдаёт cache version `1.52.143`.

## Безопасная production-конфигурация

- Все четыре датчика показали `off`; оба редуктора показали
  `on`, то есть вода открыта по настроенному read-back.
- Monitoring и notify recipient включены. `directionVerified=false` и
  `autoCloseEnabled=false`, поэтому physical close не мог быть отправлен.
- Оба direction-test вернули `commandSent=false`, `readBack=open`.
  Sequence operation journal осталась `827`, то есть команды
  кранам во время настройки не отправлялись.

## Проверка после restart

- Water safety, dashboard, scenario list и operation journal проходят
  JSON Schema.
- В production 39 сценариев, из них 9 сохранили
  `commandMode=shadow`. Node-RED остаётся physical owner до конца soak.
- Все 88 frontend JS/CSS файлов production побайтно совпадают с
  release. Изменений компоновки нет.
- В system log нет ошибок Hausman. Осталось одно известное
  warning климата.

## Откат и оставшийся live-gate

- Для полного отката использовать backup `c79a46e3`.
- Автозакрытие не включать до отдельно согласованного physical
  rehearsal: проверки закрытия обоих редукторов, rollback,
  получения тревоги и нулевого duplicate writer.
