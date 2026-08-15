# Release audit HausmanHub 1.52.103

Дата: 2026-08-15.

## Причина и изменение

- HACS закреплён на contracts `0.37.0` (`643e97e`) и публикует единую
  feature matrix для 19 типов устройств, 24 групп управления и 41 привязки
  действий.
- Новый защищённый read-only endpoint
  `/api/hausman_hub/v1/device-features` доступен локальному планшету и
  администратору. Ответ использует `Cache-Control: no-store`.
- Матрица задаёт верхнюю границу возможностей. Фактически доступные действия
  по-прежнему приходят из runtime scenario catalog. Неизвестные типы остаются
  read-only, неизвестные control скрываются, клиент не создаёт action ID.
- Capabilities API сообщает путь, метод и контракт матрицы через optional
  metadata API v1. Старые клиенты сохраняют совместимость.
- Backend commit: `6558af4`; release commit и tag target: `23c3209`.

## Проверки выпуска

- Focused gate: 71 тест и 325 subtests пройдены.
- Full pytest до release bump: 1423 passed, 4 skipped, 971 subtests.
- Финальный staged release-gate: 1421 тест пройден, 4 пропущено.
- Contract validator подтвердил 46 schemas, 66 fixtures, 36 OpenAPI paths и
  19 device feature types. HACS и Android source совпадают с контрактом.
- Synthetic fixtures, Android compatibility, staged version, product naming,
  HACS package и обе repository boundary проверки пройдены.
- `git diff --check`, проверка механического frontend cache bump и финальное
  ревью release diff пройдены.
- GitHub Actions `31887924256` завершён успешно.
- Annotated tag и GitHub Release `v1.52.103` опубликованы из `23c3209`.

## Production

- До установки создан полный automatic backup `350aa3e0`. Он включает Home
  Assistant 2026.8.2, базу данных, три папки и десять add-ons. Ошибок записи
  папок, add-ons и агентов нет.
- HACS потребовались повторные попытки из-за медленного соединения production
  host с GitHub. Установка штатно завершилась, после restart предупреждение
  исчезло из system log.
- `v1.52.103` установлена явным `update.install` без второго backup. Config
  check установленного кода перед restart и повторный check после загрузки
  вернули `valid` без ошибок и предупреждений. Выполнен один restart.
- Финальные installed/latest равны `v1.52.103`. Panel script содержит 35 cache
  refs `1.52.103` и не содержит `1.52.102`.
- Все девять сущностей платформы доступны. Runtime fresh, phase `managed`,
  active operations и blocked reasons равны нулю. Dashboard содержит 13
  комнат, 86 устройств, три сценария, 47 событий и четыре alarms.
- Live capabilities рекламирует feature matrix. Ответ endpoint семантически
  совпадает с release fixture: 19 типов, шесть read-only типов, 24 control,
  41 action binding и 25 уникальных action ID. Authority равна
  `upper_bound/scenario_catalog`, синтез действий клиентом запрещён.
- Финальный system log не содержит записей HACS или HausmanHub. Физические
  команды, климатические цели и сценарии при deploy не запускались.

## Откат

При необходимости восстановить полный backup `350aa3e0`. Он возвращает
production к HACS 1.52.102.
