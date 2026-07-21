# Review HASC extra-field saved setting

- Session: `ses_099aba006ffeVcbhIJ0HB2Y2Dw`
- Agent: `build`
- Project: `/home/ivsh/projects/hausmanhub_hasc`

## 2026-07-15T18:10:33+03:00 - user - user-prompt

- Part: `prt_f6654602f001wBbi0c9YL2uk5X`
- Message: `msg_f66546026001UyaAYzBMsRntHJ`

"Ты Kimi. Выполни короткий финальный read-only review только следующего изменения HASC. НЕ используй task, НЕ читай/не меняй файлы, НЕ делай commit/push. Ответь только конкретными findings с уровнем или NO FINDINGS.\n\nЦель: в одноразовой пустой учебной копии Home Assistant проверить третью плохую главную сохранённую настройку. Это не меняет код HASC, не добавляет данных и не обращается к настоящему дому.\n\nДобавлено:\n```python\nUNSAFE_EXTRA_FIELD_DATA = {\n    \"mode\": \"read-only\",\n    \"direct_execution_status\": \"direct_execution_blocked\",\n    \"unmodelled\": \"outside_contract\",\n}\n# после уже проверенных proxy и direct_execution_status=allowed:\nremoved_entries.append(await async_assert_invalid_saved_data_lifecycle(\n    config_directory, domain, tuple(removed_entries), reserved_entry,\n    UNSAFE_EXTRA_FIELD_DATA, \"extra-field data\"\n))\n```\n\nУже существующая `async_assert_invalid_saved_data_lifecycle` для каждого случая: создаёт безопасную HASC, портит только его временную главную настройку, требует отказ при перезагрузке, отсутствие её записей и состояний, затем запускает новую пустую копию, требует сохранения закрытого состояния, восстанавливает точную безопасную настройку, требует ровно те же девять общих чисел и затем удаляет временную HASC. Внешняя учебная запись проверяется неизменной.\n\nТест теперь требует наличие константы, три запуска этого сценария и порядок proxy → снятый запрет → лишнее поле. Документация и AI_CONTEXT совпадают с этим. 81 локальный тест и пустые Core 2026.6.4/2026.7.0 прошли. Оцени: достаточность проверки лишнего главного поля, границы ровно девяти чисел, отсутствие управления домом и чистоту решения."

