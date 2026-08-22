# 2026-08-22: режимы карточек главной HACS

## Область работы

- Изменялся только HACS frontend и его тестовый harness.
- Android, Python backend, API, contracts и storage не менялись.
- Работа выполнена поверх опубликованного HACS `1.52.137` в отдельной ветке
  `codex/hacs-overview-utility-modes-2026-08-22`.

## Сделано

- Для «Показаний энергии» и «Освещения» добавлены независимые компактный и
  развёрнутый режимы. Компактный сохранён как исходный вид.
- Развёрнутые карточки используют реальные данные Dashboard snapshot. Режимы
  сохраняются только в localStorage браузера.
- Панель «Активность» выровнена по ширине и высоте с левым меню, показывает
  до 12 событий и прокручивает записи внутри карточки.
- Ниже основных карточек подключена существующая лента ближайших событий.
  Cancellable-запуск можно пропустить через штатный upcoming cancel API.
- Горизонтальное центрирование комнаты Hero больше не прокручивает страницу
  вниз при первой загрузке.

## Проверка

- `python3 -m pytest -q tests/test_hausmanhub_panel.py tests/test_hausmanhub_panel_settings.py`:
  102 passed, 50 subtests.
- `python3 -m pytest -q`: 1566 passed, 4 skipped, 985 subtests.
- Browser QA 1440x1400: меню и Activity 238x1360 px, scrollY 0,
  горизонтального overflow и runtime errors нет. Компактные режимы являются
  начальными, переключение и localStorage проверены, cancel POST подтверждён.

## Результат

- Feature commit: `2921547`.
- Release, push и deploy не выполнялись.
