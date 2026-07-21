# Read-only code review commit f5291e0

- Session: `ses_0a49f6c60ffeu1o6zvE8GqasQ4`
- Agent: `ivsh-plan`
- Project: `/home/ivsh/projects/hausmanhub_hasc`

## 2026-07-13T15:08:04+03:00 - user - user-prompt

- Part: `prt_f5b6095ce001fGV0LJK7OBmcmb`
- Message: `msg_f5b6095c2001NHxYiRCnr1U2W6`

"Проведи Kimi-backed read-only baseline code review только изменений commit f5291e0 относительно f5291e0^. Цель: найти реальные correctness bugs, нарушения Clean Code/Clean Architecture, нарушения HASC safety boundaries и пробелы tests в synthetic static validation harness. Контекст: это только local JSON fixtures и Python standard library; запрещены Home Assistant/Node-RED runtime, сети, live identifiers, service paths, commands, proxy и direct execution. Просмотри validators, CLI, tests, fixtures и относящиеся docs. Разрешены только read-only команды и локальные tests. Не редактируй файлы, не делай commit/push, не создавай custom_components или hacs.json. Верни findings по severity с точными путями и строками; если findings нет, явно скажи это и перечисли residual risks. Остановись после отчёта."

## 2026-07-13T15:19:15+03:00 - assistant - final-report

- Part: `prt_f5b6a77e3001J4Z89oTKlgKe7w`
- Message: `msg_f5b6a00e6001JxyDcwJTzTGCIX`

Готово: проведён read-only review изменений `f5291e0^..f5291e0`. Найдено 6 замечаний.

## Findings

### High — вложенный `direct_execution` проходит проверку

**Код:** `hasc_validation/validators.py:59-84`, `395`, `506`

`direct_execution` исключён из `EVIDENCE_FORBIDDEN_KEYS`, чтобы разрешить `direct_execution_status`. Исключение действует на всё дерево JSON, а не только на разрешённые пути.

Подтверждено: валидаторы возвращают `[]` для:

```json
{"snapshot": {"direct_execution": "enabled"}}
```

и

```json
{"entry_summary": {"direct_execution": "enabled"}}
```

Это противоречит `docs/shadow-evidence-contract.md:27-29` и `docs/diagnostics-repairs-contract.md:11-16`.

**Исправление:** разрешать только точные пути `$.direct_execution_status` и `$.safety_model.direct_execution_status`, отклоняя остальные поля `direct_execution`.

**Нет тестов:** вложенный `direct_execution` в shadow и diagnostics.

---

### High — service paths, live identifiers и secrets обходят фильтр под нейтральными ключами

**Код:** `hasc_validation/validators.py:59-94`, `150-165`, `410-416`, `515-518`

Проверка анализирует запрещённые подстроки преимущественно в именах ключей, а в значениях ищет только четыре фразы. Неизвестные поля разрешены.

Без ошибок прошли:

- Common: `"secret": "real_secret_value"` и `"live_reference": "light.kitchen"`;
- shadow/diagnostics: `"endpoint": "/api/services/light/turn_on"`.

Это нарушает границы из `docs/static-validation.md:16-18` и `docs/diagnostics-repairs-contract.md:14-16`.

**Исправление:** ввести разрешённые поля для каждой структуры и отдельную проверку чувствительных значений. Common должен применять те же ограничения на secrets и live identifiers.

**Нет тестов:** чувствительное значение под нейтральным ключом, неизвестные поля, live ID и secret в Common.

---

### Medium — malformed diagnostics вызывает `TypeError`

**Код:** `hasc_validation/validators.py:520-522`, `528`, `534`, `570`; `tools/validate_fixture.py:37`

После `_list()` код повторно берёт исходное значение через `root.get()` и сразу итерирует его. Например:

```json
{"selected_references": 42}
```

вызывает:

```text
TypeError: 'int' object is not iterable
```

Вместо списка ошибок валидатор и CLI завершаются traceback.

**Исправление:** сохранять результаты `_list(...) or []` и итерировать только проверенные списки.

**Нет тестов:** scalar/null/object вместо каждой diagnostics-коллекции и проверка CLI без traceback.

---

### Medium — blocked redaction принимается с закрытым или отклонённым repair

**Код:** `hasc_validation/validators.py:569-591`  
**Контракт:** `docs/diagnostics-repairs-contract.md:30-33`

Документация требует для blocked export **видимый** critical `redaction_failure`. Код проверяет только категорию и severity. Запись со `status: "dismissed"` успешно проходит.

**Исправление:** при blocked redaction требовать `category == "redaction_failure"`, `severity == "critical"` и `status == "visible"`.

**Нет тестов:** blocked export с `dismissed`, `resolved_manually` или `acknowledged` redaction issue.

---

### Low — acceptance thresholds не обязаны оставаться placeholders

**Код:** `hasc_validation/validators.py:481-484`  
**Контракт:** `docs/shadow-evidence-contract.md:10-11`

Поля проверяются лишь как непустые строки. Все значения можно заменить на `"approved"`, и fixture останется валидным.

**Исправление:** задать допустимые placeholder-значения либо явно ослабить утверждение документации.

**Нет тестов:** конкретные или утверждённые thresholds при unresolved parity.

---

### Low — отрицательные тесты не проверяют причину отказа и поведение CLI

**Код:** `tests/test_static_contract_validation.py:28-29`, `35-56`, `58-71`

`assert_invalid` проверяет только наличие любой ошибки. Fixture может продолжить проходить тест из-за посторонней ошибки после регрессии целевого ограничения. CLI проверяется только на успешном вводе.

**Исправление:** проверять ожидаемые пути и сообщения ошибок; добавить CLI-сценарии для exit codes `1` и `2`, malformed JSON и некорректных типов.

## Проверки

- `python3 -m unittest discover -s tests -v`: успешно, 6 тестов.
- Точечные in-memory проверки всех описанных сценариев: проблемы воспроизведены.
- `git status --short`: успешно, рабочее дерево чистое.
- Файлы не менялись; commit/push не выполнялись; `custom_components/` и `hacs.json` не создавались.
- Home Assistant, Node-RED, сеть и live systems не использовались.

Kimi-backed `ivsh-review` участвовал в анализе, но его проходы достигли лимита шагов. Финальные воспроизведения и сводка завершены оркестратором. Отдельных существенных нарушений границ Clean Architecture, кроме описанной fail-open структуры валидаторов и слабой изоляции проверки типов, не обнаружено.

**Остаточный риск:** эвристический фильтр по строкам в принципе не способен доказать, что произвольная строка действительно synthetic; нужна закрытая схема или строгие allowlists.

**Время:** начало 2026-07-13 15:08, окончание 2026-07-13 15:18, всего 0:10.

