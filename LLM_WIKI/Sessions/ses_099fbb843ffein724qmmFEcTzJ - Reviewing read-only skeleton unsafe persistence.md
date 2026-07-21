# Reviewing read-only skeleton unsafe persistence

- Session: `ses_099fbb843ffein724qmmFEcTzJ`
- Agent: `ivsh-plan`
- Project: `/home/ivsh/projects/hausmanhub_hasc`

## 2026-07-15T16:43:04+03:00 - user - user-prompt

- Part: `prt_f66044809001j8d9gzaJVwXaDS`
- Message: `msg_f66044801001OI22p1NQzANVtV`

"Read-only final code review only. Inspect the current uncommitted git diff in this repository. Do not edit, commit, push, use network, or access any real Home Assistant. Changed files are only docs/read-only-skeleton.md, tests/test_read_only_skeleton.py, and tools/check_home_assistant_core.py. This diff adds isolated temporary-Core coverage for unsafe persisted OPTIONS: create safe HASC with safe shadow option, deliberately replace temporary options with proxy, require reload fail closed and a temporary restart still closed, restore exact original safe options, require reload and a further empty restart with exact same 9 sensors/diagnostics/authenticated GET-only route/no services/devices/control, then removal and final restart absent. It also makes the generic unsafe-persistence check assert options and makes diagnostics mode follow safe options when present. Review correctness, Core 2026.6.4/2026.7.0 compatibility, exact nine-count boundary, collision fixture, cleanup, docs/tests alignment, and any data/control leak. Reply only with concise findings including file:line or NO FINDINGS. Do not delegate."

## 2026-07-15T16:53:06+03:00 - user - user-prompt

- Part: `prt_f660d77e100168RFHc0A66WC13`
- Message: `msg_f660d77df001VNuipdOxflyqSX`

"Продолжи только незавершённую исходную задачу. Сначала проверь контекст и текущее состояние. Не удаляй сессии, не очищай базы или кэши и не управляй процессами или службами. Не выполняй commit, push или развёртывание, если этого не было в исходном запросе. Если работы уже нет, дай краткий отчёт."

## 2026-07-15T16:53:57+03:00 - assistant - progress-note

- Part: `prt_f660e2c62001bEiJBbUGXVsdhh`
- Message: `msg_f660ddb6c0011kcs04jtttNBzV`

NO FINDINGS.

Проверено:
- `tools/check_home_assistant_core.py:748` — `assert_persisted_unsafe_entry_stays_closed` теперь сверяет и `entry.options`; логика fail-closed для options-коррупции симметрична существующей проверке data-коррупции.
- `tools/check_home_assistant_core.py:825` — diagnostics режим корректно берётся из `expected_options["mode"]` с fallback на `expected_data["mode"]`.
- Core 2026.6.4/2026.7.0 compatibility: используются стабильные API (`async_update_entry`, `async_reload`, `ConfigEntryState`, entity/device registry, `async_services`).
- exact nine-count boundary: `assert_summary_sensor_registry` строго проверяет `len(SUMMARY_SENSOR_KEYS)` (9) и уникальные ID.
- collision fixture: `assert_reserved_collision_entry_is_unchanged` вызывается после recovery и после removal.
- cleanup: все временные `HomeAssistant` останавливаются в `finally`.
- docs/tests alignment: `docs/read-only-skeleton.md`, `tests/test_read_only_skeleton.py` и `tools/check_home_assistant_core.py` описывают одну и ту же последовательность.
- data/control leak: в recovered-состоянии `assert_summary_sensor_registry` проверяет отсутствие services/devices, `assert_local_summary_view` + `async_assert_authenticated_local_summary_http_access` проверяют GET-only route и отклонение POST; proxy-options не загружаются, reload возвращает `False`, state ≠ `LOADED`, local summary недоступен.

