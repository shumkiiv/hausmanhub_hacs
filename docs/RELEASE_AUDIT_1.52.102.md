# Release audit HausmanHub 1.52.102

Дата: 2026-08-15.

## Причина и изменение

- HACS закреплён на contracts `0.36.0` (`28e1f4e`) и публикует единую
  bounded policy для SSE, energy history и operation journal.
- SSE хранит 128 domain events, ограничивает очередь клиента 32 сообщениями и
  использует новый stream ID после каждого restart. Неизвестный
  `Last-Event-ID` приводит к gap signal и безопасному перечитыванию snapshot.
- Operation journal получил keyset pagination через exclusive
  `before_sequence`, page metadata и прежний durable retention 512 записей без
  TTL. Фильтры применяются до пагинации.
- Energy history использует непересекающиеся окна `[from, to)` до 31 дня,
  максимум 128 series и 8928 points на series. Retention зависит от Home
  Assistant Recorder, отдельная копия не создаётся.
- Backend commit: `c3b64df`; release commit и tag target: `5dde51c`.

## Проверки выпуска

- Focused gate: 98 тестов и 323 subtests пройдены.
- Full pytest до release bump: 1422 passed, 4 skipped, 969 subtests.
- Финальный staged release-gate: 1420 тестов пройдено, 4 пропущено.
- Synthetic fixtures, Android compatibility, staged version, product naming,
  HACS package и обе repository boundary проверки пройдены.
- `git diff --check` и финальное ревью release diff пройдены.
- GitHub Actions `31885789878` завершён успешно.
- Annotated tag и GitHub Release `v1.52.102` опубликованы из `5dde51c`.

## Production

- До установки создан полный automatic backup `0037d467` размером
  900 915 200 байт. Защищённые копии находятся локально и на KeeneticSSD;
  включены Home Assistant 2026.8.2, база данных, папки `media`, `share`, `ssl`
  и десять add-ons. Ошибок записи нет.
- `v1.52.102` установлена явным `update.install` без второго backup. Config
  check до restart и повторный check после загрузки вернули `valid` без
  ошибок. Выполнен один restart.
- Финальные installed/latest равны `v1.52.102`. Panel script содержит 35 cache
  refs `1.52.102` и не содержит `1.52.101`.
- Все девять сущностей платформы доступны. Runtime fresh, phase `managed`,
  active operations и blocked reasons равны нулю. Dashboard содержит 13
  комнат, 86 устройств, три сценария, 47 событий и четыре alarms.
- Live operation journal вернул keyset page с `retention_limit=512`; invalid
  cursor отклонён HTTP 400. Одночасовое read-only окно energy history вернуло
  12 series и 57 points вместе с лимитами 31/128/8928 и Recorder retention.
- SSE `hello` содержит уникальный stream ID, `Last-Event-ID`, retention 128,
  queue limit 32 и `survives_restart=false`. System log не содержит записей
  HausmanHub. Физические команды, климатические цели и сценарии при deploy не
  запускались.

## Откат

При необходимости восстановить полный backup `0037d467` с локального
хранилища или KeeneticSSD. Он возвращает production к HACS 1.52.101.
