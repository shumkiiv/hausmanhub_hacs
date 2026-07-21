# Код-ревью HASC read-only skeleton

- Session: `ses_09978d159ffeg6SDsu982wE8CI`
- Agent: `ivsh-plan`
- Project: `/home/ivsh/projects/hausmanhub_hasc`

## 2026-07-15T19:06:03+03:00 - user - user-prompt

- Part: `prt_f66872f15001xu38pW0fGyQR7D`
- Message: `msg_f66872f0c001FNtCQJyIzF3nWP`

"Проведи обязательное независимое код-ревью незакоммиченных изменений в /home/ivsh/projects/hausmanhub_hasc. Контекст: это только локальная учебная проверка HASC. Изменены tools/check_home_assistant_core.py, tests/test_read_only_skeleton.py, docs/read-only-skeleton.md и AI_CONTEXT.md. Новая логика проверки в временной пустой Home Assistant должна отдельно выполнить прямую пару config_entries.async_unload(entry_id) и async_setup(entry_id) для уже безопасной, user-enabled HASC entry. Между ними она обязана доказать: сохранённая entry и все 9 registry records остаются; records не disabled; текущие 9 states отсутствуют; guarded local route выдаёт 503 без чисел. После setup та же entry должна иметь те же данные/options, 9 active count sensors, fixed diagnostics and exactly one GET-only guarded local route. Это не должно менять runtime package, HACS metadata или создавать реальный доступ к HA, devices, services, network or home data. Проверь корректность Core API, порядок async_block_till_done, нет ли дублирования с user deactivation, качество/безопасность/точность документации и тестовой защиты. Разрешены только чтение и локальные тесты; не меняй файлы, не выполняй commit/push/deploy и не подключайся к реальному Home Assistant. Верни строго: NO FINDINGS либо конкретные findings с путями и строками."

