# Release audit HausmanHub 1.52.101

Дата: 2026-08-15.

## Причина и изменение

- HACS закреплён на contracts `0.35.0` (`0327f2c`) и принимает optional
  correlation ID на всех публичных command surfaces.
- Один ID проходит через command receipt, SSE event и operation journal.
  Dashboard activity, alarms, уведомления о новых устройствах и metadata
  сценарных уведомлений тоже содержат безопасный correlation ID.
- Если клиент не прислал ID, backend использует стабильный request ID либо
  создаёт новый. Некорректный ID отклоняется до выполнения команды.
- Backend commit: `62945bf`; release commit и tag target: `ae64952`.

## Проверки выпуска

- Финальный staged release-gate: 1417 тестов пройдено, 4 пропущено.
- Synthetic fixtures, Android compatibility, staged version, product naming,
  HACS package и обе repository boundary проверки пройдены.
- `git diff --check` и финальное ревью release diff пройдены.
- GitHub Actions `31883493197` завершён успешно.
- Annotated tag и GitHub Release `v1.52.101` опубликованы из `ae64952`.

## Production

- До установки создан полный защищённый backup `87c14426` размером
  900 362 240 байт. Копии находятся локально и на KeeneticSSD; включены Home
  Assistant 2026.8.2, база данных, папки `media`, `share`, `ssl` и десять
  add-ons.
- `v1.52.101` установлена явным `update.install` без второго backup. Config
  check до restart и повторный check после загрузки вернули `valid` без ошибок
  и предупреждений. Выполнен один restart.
- Финальные installed/latest равны `v1.52.101`. Panel script содержит 35 cache
  refs `1.52.101` и не содержит `1.52.100`.
- Все девять сущностей платформы доступны. Runtime fresh, phase `managed`,
  active operations и blocked reasons равны нулю. Dashboard содержит 13 комнат,
  86 устройств и три сценария.
- Все 47 dashboard events и четыре alarms содержат correlation ID. SSE `hello`
  содержит новый безопасный ID, operation journal сохраняет correlation ID.
- Invalid-ID probe вернул HTTP 400 до обращения к исполнителю. System log не
  содержит записей HausmanHub. Физические команды, климатические цели и
  сценарии при deploy не запускались.

## Откат

При необходимости восстановить полный backup `87c14426` с локального хранилища
или KeeneticSSD. Он возвращает production к HACS 1.52.100.
