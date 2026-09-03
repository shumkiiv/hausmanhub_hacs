# Task 5, fix round 4

Исходный HEAD: `f8457ac fix(lighting): finalize protection API gates`.

## Изменения

- Из малого коридора удалён `entity_c9d6bc67f172f30d`: это вход
  bright/dark, а не управляемый свет. В профиле и реестре первой волны
  остался только `entity_9ed909332fdaa8fd`; реле
  `entity_ff0244d6b760be7e` не включается в защиту.
- Исполнитель сохраняет точную fail-closed причину
  `manual_off_protection_unhealthy` и не доходит до подготовки питания или
  вызова сервиса.
- HTTP-проверки manual protection теперь используют только
  зарегистрированный `FakeHttp.dispatch`: повтор release не сохраняет данные
  заново, любые изменения release-полей конфликтуют, а unload/reload сохраняет
  те же два объекта маршрутов.
- Старый тест событий ожидает типизированную
  `ManualLightOffProtectionPersistenceError` и подтверждает unhealthy и ноль
  физических вызовов.

## Проверки

- RED: 3 ожидаемых падения до изменения production-кода: неверный состав
  профиля, неверный реестр и потеря причины unhealthy в исполнителе.
- Expanded 11-file suite: `260 passed, 414 subtests passed`.
- Broad protection suite: `149 passed, 5 subtests passed`.
- `python -m compileall -q custom_components tests/test_local_summary_access.py tests/test_system_light_profiles.py tests/test_manual_light_off_protection_events.py tests/test_scenario_executor.py`: успешно.
- `git diff --check`: успешно.

## Самопроверка

- Изменённые production-файлы ограничены профилем первой волны и выдачей
  точной причины в executor.
- Все профили по-прежнему выключены по умолчанию, bootstrap settings не
  добавлялись.
- Внешние команды, Android, frontend и физические устройства не затрагивались.
