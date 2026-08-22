# Hausman Hub HACS 1.52.146: синхронизация совместимой линии

Дата: 2026-08-22

## Результат

- Runtime consumer pin обновлён до contracts `0.52.1` (`5e89ca2`). Этот
  patch синхронизирует production-screen consumer с Android `1.0.245` build
  `249`.
- API, JSON Schema, runtime защиты, storage и production monitor-политика
  полностью совпадают с 1.52.145.
- Готовый интерфейс HACS сохранён. Во frontend изменена только версия кеша.

## Релиз и проверки

- Release commit/tag: `e2b103a`, `v1.52.146`.
- GitHub Actions `32575357359` завершён успешно за 3 минуты 21 секунду.
- Полный локальный gate: 1609 tests, 4 skipped; package, contracts и
  repository safety checks успешны.
- Cross-repository validator подтвердил совпадение contracts, HACS и Android.

## Production deploy

- До установки создан full backup `8e8032ca`. Он включает Home Assistant,
  базу, 10 add-ons и папки `media`, `share`, `ssl`. Защищённые копии по
  921159680 байт подтверждены в `hassio.local` и `hassio.KeeneticSSD`,
  agent errors отсутствуют.
- До и после установки `homeassistant.check_config` вернул HTTP 200.
- `v1.52.146` установлена явно через `update.install` с `backup=false`, затем
  Home Assistant перезапущен ровно один раз и восстановился примерно за
  20 секунд.
- Update entity подтвердил installed/latest `v1.52.146`.

## Проверка после restart

- Monitor revision 1 для кондиционера детской сохранился без изменений.
  Guard не вооружён, cooldown отсутствует, climate deviation events нет.
- Operation journal остался на sequence 981. Его последние изменения были
  только плановыми shadow-run сценариев до deploy.
- Dashboard и climate runtime отвечают HTTP 200. Все 88 frontend JS/CSS
  файлов production побайтно совпадают с release.
- В system log нет ошибок Hausman. Физические команды при patch-deploy не
  отправлялись.

## Откат

- Для полного отката использовать backup `8e8032ca`.
- Runtime-код идентичен 1.52.145, поэтому быстрый source rollback возможен на
  `v1.52.145`; monitor storage совместим.
