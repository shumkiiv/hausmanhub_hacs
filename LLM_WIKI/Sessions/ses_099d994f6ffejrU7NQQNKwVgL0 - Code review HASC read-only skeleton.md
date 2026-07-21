# Code review HASC read-only skeleton

- Session: `ses_099d994f6ffejrU7NQQNKwVgL0`
- Agent: `ivsh-plan`
- Project: `/home/ivsh/projects/hausmanhub_hasc`

## 2026-07-15T17:20:22+03:00 - user - user-prompt

- Part: `prt_f66266b47001jMG0qlYLw0NXYj`
- Message: `msg_f66266b3f001vf1WCWjxFueDu0`

"Проверь текущий незакоммиченный diff HASC как обязательный Kimi code review. Область: только локальная учебная проверка и документация; никаких изменений реального Home Assistant, Node-RED, устройств, сервисов, сети дома или HASC runtime. Изменённые файлы: tools/check_home_assistant_core.py, tests/test_read_only_skeleton.py, docs/read-only-skeleton.md. Новый смысл: из текущего повторяющегося учебного цикла для неверных сохранённых основных настроек выделен один небольшой помощник. Он сохраняет в пустом временном Core два ошибочных варианта: запрещённый mode=proxy и ложную отметку direct_execution_status=allowed. Оба обязаны отказать при reload и после restart, не оставить страницу, девять чисел, устройство, сервис, данные или изменение внешней временной записи. Только точное восстановление исходных безопасных данных возвращает те же девять чисел и GET-only страницу, затем удаление и финальный пустой restart. Проверь чистоту/понятность кода, отсутствие скрытых мутаций, типы, Core 2026.6.4/2026.7.0, границы ровно девяти чисел, отсутствие реального доступа или управления и соответствие тестов/документации. Уже успешно: 81 локальный тест; isolated Core check на 2026.6.4 и 2026.7.0. Отвечай только конкретными находками с уровнем важности, либо NO FINDINGS."

## 2026-07-15T17:26:14+03:00 - assistant - final-report

- Part: `prt_f662bbdaf001VBHlHkiTcIYO3P`
- Message: `msg_f662b527100165YDgSiNCqHSpl`

Готово: review завершён; высоких находок нет.

Находки:

- **Средняя.** В `tools/check_home_assistant_core.py` функция `assert_persisted_unsafe_entry_stays_closed` не проверяет, что для невалидной config entry в `entity_registry` не осталось записей. Она проверяет только runtime-данные, устройства, состояния и GET-страницу, но не сам факт наличия сущностей в реестре. Это слабее, чем `assert_hasc_stays_removed_after_restart`, где такая проверка есть явно. Если интеграция по какой-то причине оставит отключённые сущности, проверка промолчит. Рекомендация: добавить `if entity_registry.async_entries_for_config_entry(..., entry_id): raise RuntimeError(...)`.

- **Низкая.** `async_assert_invalid_saved_data_lifecycle` и `async_assert_invalid_saved_options_lifecycle` дублируют почти одинаковый цикл: создание → порча данных/опций → reload → restart → исправление → restart → удаление → финальный restart. Рассмотрите выделение общего шаблона, чтобы уменьшить дублирование и риск рассинхронизации двух helper’ов.

- **Низкая.** В `docs/read-only-skeleton.md` абзац про восстановление использует единственное число: «the deliberately bad saved setting is then corrected», хотя выше речь идёт о двух сценариях. Лучше перефразировать, например: «each deliberately bad saved setting is then corrected».

Примечание: Kimi-агент `ivsh-review` не завершил review из-за внутреннего лимита шагов, поэтому анализ закончен вручную (OpenCode fallback).

