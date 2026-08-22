# HACS: иконка кондиционеров и контраст предупреждений

Дата: 2026-08-22
Агент: Codex

## Результат

- Изменён только HACS frontend на ветке
  `codex/hacs-expanded-panels-overlap-2026-08-22` поверх `1.52.148`.
- Категория «Кондиционеры» использует иконку настенного блока вместо
  снежинки режима охлаждения.
- Общий Hero библиотечных страниц больше не окрашивает рамку и текст в
  оранжевый. Предупреждение читается основным цветом и использует компактный
  красный индикатор проблемы.

## Проверено

- Browser QA: 1504x900 light/dark и 900x1100 light.
- `python -m pytest -q tests/test_hausmanhub_panel.py
  tests/test_hausmanhub_panel_settings.py
  tests/test_frontend_pagination_retention.py
  tests/test_frontend_correlation_id.py tests/test_frontend_ui_state.py`:
  142 passed, 50 subtests.
- `node --check` для модуля климата и `git diff --check` прошли.
- Тест закрепляет иконку устройства и отсутствие прежних оранжевых значений
  в общем Hero.

## Ограничения

- Feature commit `0952098`.
- Версия остаётся `1.52.148`.
- Release, push и deploy не выполнялись.
- Android, backend, API, contracts и storage не менялись.
