# HACS: чистые карточки освещения

Дата: 2026-08-22
Агент: Codex

## Результат

- Изменён только HACS frontend раздела «Освещение» на ветке
  `codex/hacs-expanded-panels-overlap-2026-08-22` поверх `1.52.148`.
- Карточки комнат, боковые списки и физические устройства используют чистые
  поверхности, тонкие синие границы, мягкую глубину и единые hover/focus
  состояния.
- Тёплый цвет сохранён только как небольшой признак включённого света.
  Оранжевые рамки, текст состояния и серо-оранжевые подложки удалены.
- Фильтры и переключатели каналов получили синее активное состояние.

## Проверено

- Browser QA: 1504x1146 light/dark и 900x1200 light.
- `python -m pytest -q tests/test_hausmanhub_panel.py
  tests/test_hausmanhub_panel_settings.py
  tests/test_frontend_pagination_retention.py
  tests/test_frontend_correlation_id.py tests/test_frontend_ui_state.py`:
  141 passed, 50 subtests.
- `node --check` для модулей освещения и `git diff --check` прошли.
- В тестах закреплено отсутствие старой оранжевой рамки активной комнаты.

## Ограничения

- Feature commit `c896ef8`.
- Версия остаётся `1.52.148`.
- Release, push и deploy не выполнялись.
- Android, backend, API, contracts и storage не менялись.
