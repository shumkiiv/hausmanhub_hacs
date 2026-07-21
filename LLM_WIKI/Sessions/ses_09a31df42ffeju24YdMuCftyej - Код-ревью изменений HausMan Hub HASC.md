# Код-ревью изменений HausMan Hub HASC

- Session: `ses_09a31df42ffeju24YdMuCftyej`
- Agent: `ivsh-plan`
- Project: `/home/ivsh/projects/hausmanhub_hasc`

## 2026-07-15T15:43:55+03:00 - user - user-prompt

- Part: `prt_f65ce20f6001zfklS0jS6L36w0`
- Message: `msg_f65ce20ee001zsXz9CkpMN2nSY`

"Проведи независимое код-ревью текущих незакоммиченных изменений в отдельном репозитории HausMan Hub HASC. Проверь конкретно: корректно ли новая проверка считает все маршруты /api/hausman_hub/local-summary и требует ровно один маршрут в активном состоянии; не требует ли она ошибочно ноль маршрутов сразу после отключения/удаления в том же процессе; корректно ли после полного временного перезапуска требует отсутствие маршрута; нет ли регрессии, утечки личных данных, обращения к реальному Home Assistant, сервисов, управления устройствами, proxy/direct execution. Нужен только результат ревью. Если замечаний нет, закончи строкой NO FINDINGS."

## 2026-07-15T15:45:13+03:00 - assistant - progress-note

- Part: `prt_f65cf32530018GL4YV7Xh9QNV8`
- Message: `msg_f65cf08e5001JqFb9Ld6dBxNn3`

Результат независимого код-ревью незакоммиченных изменений в `hausmanhub_hasc`.

Что проверено:
- `tools/check_home_assistant_core.py` — изменение `find_local_summary_route` → `find_local_summary_routes` и усиление проверок маршрута.
- `tests/test_read_only_skeleton.py` — новый тест `test_core_smoke_check_keeps_one_local_summary_route`.
- `docs/read-only-skeleton.md` — описание нового требования.

Оценка по конкретным вопросам:

1. Подсчёт всех маршрутов `/api/hausman_hub/local-summary` и требование ровно одного активного.
   - `find_local_summary_routes` проходит по всем `hass.http.app.router.resources()` и фильтрует по `canonical == LOCAL_SUMMARY_PATH`, то есть действительно считает все маршруты с этим путём.
   - `assert_local_summary_view` требует `len(resources) == 1` и выбрасывает ошибку при 0 или 2+.
   - Корректно.

2. Ошибочное требование нуля маршрутов сразу после отключения/удаления в том же процессе.
   - Интеграция в `clear_local_summary_access` убирает только активную запись из `hass.data`, но не отрегистрирует view; маршрут остаётся.
   - `async_assert_local_summary_is_unavailable` вызывается после `async_disable_safe_entry` и `async_remove_safe_entry` в том же процессе и требует ровно один маршрут (`len(...) != 1` → ошибка), а не ноль.
   - Корректно.

3. Требование отсутствия маршрута после полного временного перезапуска.
   - `assert_deactivated_entry_stays_inactive_after_restart` и `assert_hasc_stays_removed_after_restart` используют `if find_local_summary_routes(hass):`, то есть пустой кортеж проходит, любой маршрут — ошибка.
   - После перезапуска `hass` новый, `hass.data` пустой, `async_setup_entry` не вызывается для деактивированной/удалённой записи, маршрут не регистрируется.
   - Корректно.

4. Регрессия, утечка личных данных, обращение к реальному Home Assistant, сервисы, управление устройствами, proxy/direct execution.
   - Изменения касаются только инструмента проверки и его теста; `custom_components/hausman_hub/` не изменён.
   - Проверка всё ещё работает с изолированным временным Home Assistant, не с реальным.
   - Сервисы, устройства, proxy и direct execution по-прежнему отсутствуют и блокируются схемой конфигурации; код-ревью не выявило новых путей их появления.
   - Персональные данные не собираются и не передаются.

Локальные тесты (`python3 -m unittest tests.test_read_only_skeleton -v`) пройдены: 34/34 успешно.

NO FINDINGS.

