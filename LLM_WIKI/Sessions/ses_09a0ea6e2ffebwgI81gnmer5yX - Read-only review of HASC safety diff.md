# Read-only review of HASC safety diff

- Session: `ses_09a0ea6e2ffebwgI81gnmer5yX`
- Agent: `ivsh-plan`
- Project: `/home/ivsh/projects/hausmanhub_hasc`

## 2026-07-15T16:22:24+03:00 - user - user-prompt

- Part: `prt_f65f15973001VbcYDDD6wiP3Xs`
- Message: `msg_f65f15968001MV2Z2GqdRciyyQ`

"Ты Kimi-рецензент. Проведи короткий, но полный read-only code review текущего незакоммиченного diff только в /home/ivsh/projects/hausmanhub_hasc. Не меняй файлы, не запускай сети, реальный Home Assistant, commit/push. Изменены только docs/read-only-skeleton.md, tests/test_read_only_skeleton.py, tools/check_home_assistant_core.py. Задача diff: в пустой временной Home Assistant безопасная HASC получает плохие сохранённые data (proxy), закрывается, переживает пустой перезапуск закрытой, затем там же вручную возвращается ровно исходный approved data и reload. Проверь: 1) ни одна операция не трогает реальный дом; 2) восстановление доказывает только исходные 9 aggregate sensors, diagnostics и authenticated GET-only local page, без devices/services/proxy/direct control; 3) entity IDs сохранены точно, включая collision fixture; 4) async_reload после SETUP_ERROR допустим в Core 2026.6.4/2026.7.0; 5) remove плюс final empty restart действительно закрывает state/registry/runtime/page/services/devices; 6) docs/tests соответствуют коду. Если есть проблема, дай конкретный путь/строку и риск. В конце ровно `NO FINDINGS` или `FINDINGS:`. Не делегируй задачу и не делай файлы."

## 2026-07-15T16:25:21+03:00 - assistant - progress-note

- Part: `prt_f65f3d3e3001Pm6tRSVt0FJZ5J`
- Message: `msg_f65f210c20011kJaTXYfBQBmq1`

Read-only review завершён. Проверял только `docs/read-only-skeleton.md`, `tests/test_read_only_skeleton.py`, `tools/check_home_assistant_core.py` в `/home/ivsh/projects/hausmanhub_hasc`; файлы и окружение не изменял, сети/реальный HA/commit/push не запускал.

1. **Ни одна операция не трогает реальный дом** — `ok`. Весь новый поток работает внутри `tempfile.TemporaryDirectory(prefix="hasc-core-check-")` (`tools/check_home_assistant_core.py:968`). Все `HomeAssistant` инстансы создаются через `async_start_empty_home_assistant(config_directory)` с пустым конфигом (`tools/check_home_assistant_core.py:913-927`). HTTP — только `TestServer/TestClient` на `127.0.0.1` (`tools/check_home_assistant_core.py:831`, `891`). Пользователи — синтетические (`tools/check_home_assistant_core.py:785-810`).

2. **Восстановление доказывает только 9 aggregate sensors, diagnostics, authenticated GET-only local page, без devices/services/proxy/direct control** — `ok`. После `async_reload` восстановленной записи вызывается цепочка: `assert_result(recovered_entry.data, recovered_entry_data, ...)` (`tools/check_home_assistant_core.py:1366-1369`) → `async_assert_safe_diagnostics` с `proxy_status: "not_approved"` и `direct_execution_status: "direct_execution_blocked"` (`tools/check_home_assistant_core.py:347-387`) → `assert_entry_has_only_summary_sensors` (ровно 9 сенсоров, `device_id=None`, нет сервисов) (`tools/check_home_assistant_core.py:488-517`) → `assert_local_summary_view` + `async_assert_authenticated_local_summary_http_access` (GET-only, auth, 403 для admin, 401 без auth, 405 для POST) (`tools/check_home_assistant_core.py:642-672`, `813-869`). `proxy` в данных не остаётся, так как восстанавливается только `recovered_entry_data`, скопированный из исходного `read-only` entry.

3. **Entity IDs сохранены точно, включая collision fixture** — `ok`. `invalid_entry_entity_ids` захватывается как `frozenset` сразу после создания `read-only` entry (`tools/check_home_assistant_core.py:1288-1294`) и затем передана в `assert_entry_has_only_summary_sensors(..., expected_entity_ids=invalid_entry_entity_ids)` (`tools/check_home_assistant_core.py:1382-1386`). Внешняя collision fixture проверяется неизменной через `assert_reserved_collision_entry_is_unchanged` после reload и после removal (`tools/check_home_assistant_core.py:1393`, `1407`).

4. **async_reload после SETUP_ERROR допустим в Core 2026.6.4/2026.7.0** — `ok` в рамках diff. Код вызывает `async_reload(recovered_entry.entry_id)` на entry, которая пережила restart с `mode: "proxy"` и поэтому находится в failed/не-LOADED состоянии (`tools/check_home_assistant_core.py:1357-1359`). Это именно тот сценарий, который требуется. Тест ожидает `True` и `LOADED` после reload — так он и доказывает допустимость операции.

5. **remove + final empty restart действительно закрывает state/registry/runtime/page/services/devices** — `ok`. Восстановленный entry удаляется через `async_remove_safe_entry` (`tools/check_home_assistant_core.py:1398-1400`), которая проверяет отсутствие записи, отсутствие registry-записей и отсутствие state values. После этого `async_assert_local_summary_is_unavailable` проверяет, что страница недоступна и не возвращает count-значения (`tools/check_home_assistant_core.py:1401-1406`). Затем `recovered_removal_hass` запускается, и `assert_hasc_stays_removed_after_restart` проверяет отсутствие config entries, services, runtime data, routes, entities, devices и state values (`tools/check_home_assistant_core.py:713-743`).

6. **docs/tests соответствуют коду** — `ok`. Документация (`docs/read-only-skeleton.md:142-146`) описывает тот же сценарий: bad saved setting → correction to read-only → reload → restore only 9 sensors/diagnostics/GET-only page → removal + one more empty restart. Тест `test_core_smoke_check_recovers_corrected_saved_configuration` (`tests/test_read_only_skeleton.py:538-565`) проверяет наличие ключевых строк и правильный порядок операций в `async_run_check`.

NO FINDINGS

