# Финальный Kimi review HASC callback

- Session: `ses_099b8dbd2fferIqVX9G8QqdKw7`
- Agent: `ivsh-plan`
- Project: `/home/ivsh/projects/hausmanhub_hasc`

## 2026-07-15T17:56:06+03:00 - user - user-prompt

- Part: `prt_f66472465001KQXjZ2BCuXXoaL`
- Message: `msg_f6647245d001Aqsuxbjd6TN5g3`

"Финальный узкий Kimi review после исправления из предыдущей проверки. Читай только текущие: custom_components/hausman_hub/__init__.py, tests/test_local_summary_access.py и нужные места tools/check_home_assistant_core.py. Проверь, что внутреннее действие, запланированное на старт Home Assistant, отмечено @callback и потому выполняется в основном потоке; что тест требует эту отметку; что меняются только собственные HASC-состояния и строки HASC; что нет управления домом и нет затрагивания чужих данных. Не меняй файлы, не запускай реальный HA, не делай commit/push. Уже прошли 81 unit test и пустые Core-проверки 2026.6.4/2026.7.0. Ответь строго конкретными findings с уровнем либо NO FINDINGS."

## 2026-07-15T18:02:46+03:00 - assistant - progress-note

- Part: `prt_f664d25090010zlJpeTaKj6SUC`
- Message: `msg_f664cf3e6001W5QSfIxDd79xFL`

**NO FINDINGS**

Проверка показала, что исправление удовлетворяет всем заявленным требованиям.

**Конкретные проверки:**

1. **`@callback` на стартовом действии** — `custom_components/hausman_hub/__init__.py`, строки 52–73.
   - Функция `clear_hasc_records_after_start` декорирована `@callback` (импорт из `homeassistant.core`).
   - Она передаётся в `async_at_started(hass, clear_hasc_records_after_start)`.
   - Внутри только `hass.states.async_remove` и `entity_registry.async_remove` для сущностей, принадлежащих `entry.entry_id`.

2. **Тест требует отметку `@callback`** — `tests/test_local_summary_access.py`, строки 221–245.
   - Фейковый `@callback` устанавливает атрибут `_hass_callback = True`.
   - Фейковый `async_at_started` выбрасывает `RuntimeError`, если `_hass_callback` отсутствует.
   - Тест `test_setup_rejects_invalid_saved_configuration_before_loading` проходит через этот путь.

3. **Только собственные HASC-данные** — `custom_components/hausman_hub/__init__.py`, строки 58–65.
   - Отбор идёт через `entity_registry.async_entries_for_config_entry(..., entry.entry_id)`.
   - Удаляются только состояния и записи реестра этой конкретной HASC-config entry.
   - Нет вызовов сервисов, нет изменения чужих сущностей, устройств, runtime data других интеграций.

4. **Core-проверки не допускают восстановления чужих/небезопасных данных** — `tools/check_home_assistant_core.py`.
   - `assert_persisted_unsafe_entry_stays_closed` (строки 759–804) проверяет, что invalid saved entry не восстанавливает сервисы, `runtime data`, local-summary route, entity registry records, devices, state values.
   - `assert_reserved_collision_entry_is_unchanged` (строки 621–642) проверяет, что внешняя collision-запись остаётся нетронутой.
   - Оба lifecycle-сценария (`async_assert_invalid_saved_data_lifecycle` и `async_assert_invalid_saved_options_lifecycle`) вызывают эти проверки.

**Дополнительно:**
- Файлы не изменялись.
- Реальный Home Assistant не запускался.
- `python -m unittest tests.test_local_summary_access` — 7 тестов, `OK`.

