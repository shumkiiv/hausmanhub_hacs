# Сессия: график энергии и карточка счётчика HACS

Дата: 2026-08-22
Агент: Codex
Компонент: `hausmanhub_hacs`
Ветка: `codex/hacs-expanded-panels-overlap-2026-08-22`
Версия: `1.52.148`

## Результат

- Удалена неиспользуемая колонка 275 px из компоновки истории энергии.
- Canvas рисуется по фактическому размеру элемента и device pixel ratio,
  поэтому линия и подписи больше не масштабируются браузером.
- Добавлены ровная шкала Вт/кВт, пять временных отметок, даты на границах
  суток, часовой пояс Home Assistant и точный диапазон периода.
- Метрика «Сейчас» заменена на «Последний час» или «Последний день».
- Добавлены средняя линия, указатель точки, tooltip для мыши и касания,
  управление стрелками, Home, End и Escape.
- Карточка счётчика показывает odometer, дату показания, текущий цикл и
  следующую передачу. Исправлен конфликт селекторов, из-за которого карточка
  становилась вертикальной и оставляла большое пустое место.
- При смене периода очищаются история мощности и история расхода, поэтому
  данные прошлого периода не остаются на экране.

## Референсы

- Home Assistant Statistics graph:
  https://www.home-assistant.io/dashboards/statistics-graph/
- Grafana Time series:
  https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/visualizations/time-series/
- Tesla Energy Data:
  https://www.tesla.com/support/powershare/energy-data

## Проверки

- `node --check` для обоих энергетических JS-модулей.
- `git diff --check`.
- `python -m pytest tests/test_hausmanhub_panel.py -q`: 34 passed, 50
  subtests passed.
- Расширенный frontend-профиль: 166 passed, 50 subtests passed.
- `python tools/check_local_release.py`: 1620 tests, 4 skipped, все
  package и safety checks пройдены.
- Browser QA: 1504x1200 light/dark, 1100x1050 light, пик 1418,8 Вт,
  интерактивный tooltip, без runtime errors и горизонтального overflow.

## Git и границы

- Feature commit: `4d6b2a9`.
- Release, push и deploy не выполнялись.
- Android, backend, API, contracts и storage не менялись.
