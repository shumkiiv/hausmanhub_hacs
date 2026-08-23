# HACS: раскрытые панели без наложения

Дата: 2026-08-22
Агент: Codex

## Результат

- Исправлен только HACS frontend на ветке
  `codex/hacs-expanded-panels-overlap-2026-08-22` от `v1.52.148`.
- Энергия и освещение остаются в одной строке. На container 1050-1219 px их
  подробное содержимое перестраивается под узкую центральную колонку.
- Карточки ограничивают содержимое своими границами. Подробные списки имеют
  внутреннюю прокрутку и не могут перекрыть соседние панели.

## Проверено

- `git diff --check`.
- `python -m pytest -q tests/test_hausmanhub_panel.py
  tests/test_hausmanhub_panel_settings.py`: 102 passed, 50 subtests.
- Browser QA: 1504x1146 light/dark, 1280x900 light, 1100x900 light/dark.
  До исправления на 1100 px энергия заходила в освещение примерно на 31 px.
  После исправления horizontal intrusion равен -19 px, gap до событий 8 px,
  нижние границы центральной и правой колонок совпадают.
- Runtime errors и горизонтальный overflow отсутствуют.

## Ограничения

- Версия остаётся `1.52.148`.
- Release, push и deploy не выполнялись.
- Android, backend, API, contracts и storage не менялись.
