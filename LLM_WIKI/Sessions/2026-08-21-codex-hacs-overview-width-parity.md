# Главная HACS: ширина как у остальных страниц

Дата: 2026-08-21

Агент: Codex

Репозиторий: `components/integration/hausmanhub_hacs`

Ветка: `kimi/hacs-tablet-parity-2026-08-20`

## Результат

- Изменён только интерфейс HACS. Backend, API, contracts, Android и storage
  не затрагивались.
- Причиной лишней ширины было отдельное правило главной с `max-width: none`
  и нулевым внешним отступом. Обычные страницы используют предел 1600 px,
  центрирование и горизонтальные поля 34 px.
- Главная получила те же внешние параметры. Внутренняя планшетная сетка и
  вертикальная геометрия сохранены.

## Проверка

- На viewport 1920 px главная и «Освещение» имеют одинаковые границы `main`:
  160-1760 px, ширина 1600 px, поля 34 px. Результат одинаков для раскрытого
  и свёрнутого rail.
- На 1280x800 проверены светлая и тёмная темы, оба состояния rail. Обрезки и
  горизонтального overflow нет.
- `git diff --check` прошёл.
- `python3 -m pytest -q tests/test_hausmanhub_panel.py`: 31 passed,
  50 subtests.
- `python3 -m pytest -q tests/test_hausmanhub_panel.py tests/test_hausmanhub_panel_settings.py tests/test_hausmanhub_modal_theme.py`:
  118 passed, 82 subtests. Остались 24 предсуществующих `SyntaxWarning` в
  `test_hausmanhub_panel_settings.py`.

## Публикация

- Версия остаётся `1.52.133`.
- Release, push и deploy не выполнялись, production не менялся.
