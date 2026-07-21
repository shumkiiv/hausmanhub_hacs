# Read-only review Home Assistant unload/setup

- Session: `ses_0997292a2ffe3wshWIg8hztjw3`
- Agent: `build`
- Project: `/home/ivsh/projects/hausmanhub_hasc`

## 2026-07-15T19:12:53+03:00 - user - user-prompt

- Part: `prt_f668d6de9001LKs2NAtVuDr27T`
- Message: `msg_f668d6ddf0010iikctpl3xFSqC`

"Ты единственный финальный Kimi-рецензент. Не делегируй задачу другим агентам. Выполни только read-only код-ревью текущих незакоммиченных изменений в /home/ivsh/projects/hausmanhub_hasc: tools/check_home_assistant_core.py, tests/test_read_only_skeleton.py, docs/read-only-skeleton.md, AI_CONTEXT.md. Изменение добавляет учебный цикл прямого Home Assistant config_entries.async_unload(entry_id) затем async_setup(entry_id) для одной user-enabled HASC entry. Проверь: после unload есть await async_block_till_done, entry сохранена/not disabled/NOT_LOADED; ровно 9 registry records enabled, states отсутствуют, route returns 503 without counts; после setup есть await block, data/options не меняются, 9 states/diagnostics/one GET-only page возвращаются; user deactivation остается отдельным. Реальные Core 2026.6.4 и 2026.7.0 уже прошли этот сценарий только во временных пустых папках. Не меняй файлы, не выполняй commit/push/deploy, не делай внешних или реальных HA запросов. После чтения и при необходимости локальных безопасных проверок закончи ровно одной строкой: NO FINDINGS либо FINDING: путь:строка — проблема."

