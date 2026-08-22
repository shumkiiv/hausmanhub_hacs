# HACS: русская активность и кликабельные боковые карточки

Дата: 2026-08-22
Агент: Codex

## Результат

- Изменён только HACS frontend на ветке
  `codex/hacs-expanded-panels-overlap-2026-08-22` поверх локального исправления
  раскрытых панелей и базы `1.52.148`.
- Технические причины `scenario_failed`, `restarted_by_new_trigger` и другие
  известные коды преобразуются в понятные русские подписи. Для неизвестного
  системного кода используется безопасная русская подпись без показа
  внутреннего идентификатора.
- «Дом сейчас» открывает раздел «Комнаты», «Последняя активность» открывает
  раздел «Сценарии». Карточки работают мышью, Enter и пробелом, имеют
  заметные hover и focus состояния.

## Проверено

- `git diff --check`.
- `node --check custom_components/hausman_hub/frontend/hausman-hub-overview-side.js`.
- `python -m pytest -q tests/test_hausmanhub_panel.py
  tests/test_hausmanhub_panel_settings.py
  tests/test_frontend_pagination_retention.py
  tests/test_frontend_correlation_id.py tests/test_frontend_ui_state.py`:
  141 passed, 50 subtests.
- Тесты подтверждают русские подписи для известных и неизвестного кода,
  отсутствие исходных технических строк, переходы мышью и с клавиатуры.

## Ограничения

- Версия остаётся `1.52.148`.
- Release, push и deploy не выполнялись.
- Android, backend, API, contracts и storage не менялись.
