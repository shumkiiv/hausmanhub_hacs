# Task 3: локальные привязки и закрытый запуск Тамбура

## Итог

Запуск ручного контура Тамбура теперь зависит только от полного проверенного
локального документа привязок. При отсутствии, повреждении, восстановлении
предыдущей записи, неполном наборе или ошибке разрешения адаптер выключателей
и координатор миграции не создаются. Диагностика сообщает
`bindings_unavailable`; подписок, миграции и команд этого контура нет.

Подготовка привязок добавлена в существующий локальный путь настроек. Она
принимает полный JSON-документ, проверяет следующую revision перед записью и
сохраняет только локальное хранилище. Параметры entry, обработчик перезагрузки,
события, журналы и публичные ответы не меняются.

## RED

После добавления целевых тестов выполнена команда:

```sh
PYTHONDONTWRITEBYTECODE=1 /tmp/hausman-ci-6Ksvvz/venv/bin/python -m pytest -q tests/test_tambur_room_migration.py tests/test_config_flow_adapter.py -k 'binding or prepare'
```

Результат: остановка на импорте с `ImportError` для отсутствующей функции
локального разрешения привязок. Это ожидаемое исходное отсутствие точки
интеграции, которое фиксирует новый тест до добавления production-кода.

## GREEN

После реализации повторно выполнены команды:

```sh
PYTHONDONTWRITEBYTECODE=1 /tmp/hausman-ci-6Ksvvz/venv/bin/python -m pytest -q tests/test_tambur_room_migration.py tests/test_config_flow_adapter.py -k 'binding or prepare'
PYTHONDONTWRITEBYTECODE=1 /tmp/hausman-ci-6Ksvvz/venv/bin/python -m pytest -q tests/test_local_summary_access.py -k 'missing_bindings_prevent_tambur_migration_and_subscription or setup_defers_incomplete_global_controller_content_in_room_mode'
PYTHONDONTWRITEBYTECODE=1 /tmp/hausman-ci-6Ksvvz/venv/bin/python -m pytest -q tests/test_smart_switch_bindings.py tests/test_smart_switch_runtime.py tests/test_tambur_room_migration.py tests/test_config_flow_adapter.py tests/test_tambur_task5_controls.py
PYTHONDONTWRITEBYTECODE=1 /tmp/hausman-ci-6Ksvvz/venv/bin/python -m json.tool custom_components/hausman_hub/strings.json >/dev/null
git diff --check
```

Результаты: 2 selected tests passed, 2 direct setup tests passed, 134 targeted
tests passed, JSON корректен, ошибок пробелов в diff нет.

## Изменённые файлы

- `custom_components/hausman_hub/__init__.py`: локальная проверка complete
  trigger scope до создания адаптера и миграции, fail-closed статусы.
- `custom_components/hausman_hub/config_flow.py`: локальная форма подготовки,
  revision compare-and-save без изменения options.
- `custom_components/hausman_hub/strings.json`: тексты локальной формы и ошибок.
- `tests/test_tambur_room_migration.py`: отсутствие документа не разрешает scope.
- `tests/test_smart_switch_bindings.py`: неполный документ не разрешает scope.
- `tests/test_config_flow_adapter.py`: сохранение revision 1, отказ старой
  revision, отсутствие вызовов сервисов и изменения options.
- `tests/test_local_summary_access.py`: сквозная проверка, что при отсутствии
  документа не создаются adapter и room startup coordinator.

## Self-review

- Удалён production-вызов с `included_bindings`; adapter получает только
  локально разрешённые `resolved_triggers`.
- Проверены все закрывающие ветви чтения: ошибка чтения, recovered запись,
  неверный документ и неполный состав возвращают один нейтральный статус без
  раскрытия идентификаторов.
- Подготовка сериализует compare-and-save, повторно читает документ перед
  атомарной записью и требует ровно следующую revision. При конфликте старый
  документ сохраняется.
- Новые исходники, тесты, строки и отчёт используют только синтетические
  идентификаторы. Внешние вызовы, Home Assistant, Node-RED, публикация,
  установка, reload и restart не выполнялись.

## Остаточный риск

Локальная форма намеренно не применяет запись автоматически. После подготовки
нужен отдельный контролируемый перезапуск интеграции, чтобы новый документ
прочитался и ручной контур мог быть допущен. Это fail-closed ограничение,
а не незавершённость реализации.

## Fix round 1: локализация результата options flow

Исправлено размещение ключа `smart_switch_bindings_saved`: он перенесён из
`config.abort` в `options.abort`, потому что результат возвращает именно
`HausmanHubOptionsFlow`. Добавлен regression-тест, который проверяет оба
условия: ключ есть в options namespace и отсутствует в config namespace.

RED выполнен до правки:

```sh
PYTHONDONTWRITEBYTECODE=1 /tmp/hausman-ci-6Ksvvz/venv/bin/python -m pytest -q tests/test_config_flow_adapter.py -k 'smart_switch_bindings_saved_abort'
```

Вывод: `1 failed, 26 deselected`; причина `KeyError: 'abort'` в
`strings["options"]["abort"]`, что подтвердило отсутствующую локализацию
options flow.

После правки выполнена команда:

```sh
PYTHONDONTWRITEBYTECODE=1 /tmp/hausman-ci-6Ksvvz/venv/bin/python -m pytest -q tests/test_config_flow_adapter.py -k 'smart_switch_bindings_saved_abort or prepare_smart_switch_bindings'
PYTHONDONTWRITEBYTECODE=1 /tmp/hausman-ci-6Ksvvz/venv/bin/python -m json.tool custom_components/hausman_hub/strings.json >/dev/null
git diff --check
```

Вывод: `2 passed, 25 deselected`; JSON корректен, ошибок пробелов в diff нет.
Изменены только `custom_components/hausman_hub/strings.json`,
`tests/test_config_flow_adapter.py` и этот отчёт. Риск отсутствует: ключ не
содержит идентификаторов устройств и не меняет поведение сохранения.

## Final fix wave: переводы options flow

В `translations/en.json` и `translations/ru.json` синхронизированы все
строки локальной подготовки привязок: пункт и описание меню, экран формы,
результат сохранения и обе ошибки. Добавлен focused regression-тест, который
сверяет эти ветви с `strings.json` для обоих языков.

RED выполнен до правки:

```sh
PYTHONDONTWRITEBYTECODE=1 /tmp/hausman-ci-6Ksvvz/venv/bin/python -m pytest -q tests/test_config_flow_adapter.py -k 'smart_switch_binding_options_strings_match_all_translations'
```

Вывод: `1 failed, 27 deselected`; причина `KeyError: 'smart_switch_bindings'`
в `options.step.advanced_settings.menu_options`, что подтвердило отсутствие
переводной ветви.

После синхронизации выполнена команда:

```sh
PYTHONDONTWRITEBYTECODE=1 /tmp/hausman-ci-6Ksvvz/venv/bin/python -m pytest -q tests/test_config_flow_adapter.py -k 'smart_switch'
PYTHONDONTWRITEBYTECODE=1 /tmp/hausman-ci-6Ksvvz/venv/bin/python -m json.tool custom_components/hausman_hub/strings.json >/dev/null
PYTHONDONTWRITEBYTECODE=1 /tmp/hausman-ci-6Ksvvz/venv/bin/python -m json.tool custom_components/hausman_hub/translations/en.json >/dev/null
PYTHONDONTWRITEBYTECODE=1 /tmp/hausman-ci-6Ksvvz/venv/bin/python -m json.tool custom_components/hausman_hub/translations/ru.json >/dev/null
git diff --check
```

Вывод: `3 passed, 25 deselected`; все три JSON-документа корректны, ошибок
пробелов в diff нет. Runtime, документация и идентификаторы устройств не
менялись.
