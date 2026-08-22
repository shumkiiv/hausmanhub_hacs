# Паритет Hausman Android и HACS

Дата проверки: 2026-08-23.

Эталон и граница сравнения: Hausman Android `1.0.251` задаёт временную
информационную и визуальную иерархию. HACS сохраняет браузерные преимущества и
не копирует буквальные размеры Compose. Общий вход для сравнения -
`fixtures/hausmanhub_dashboard_v1/dashboard.json`.

| Контур | Статус | Проверяемое правило |
| --- | --- | --- |
| Header | Готово | Общая библиотечная hero-шапка, одинаковая иерархия названия, статуса и фактов |
| Card | Готово | Одна физическая карточка и единые loading, ready, offline, pending, failed, disabled |
| Detail | Готово | Общий modal helper, начальный focus, Tab trap, Esc и возврат focus |
| Control | Готово | Общие кнопки и состояния hover, focus, pending и disabled |
| Notice | Готово | Русские receipts, polite aria-live, assertive для ошибки |
| Picker | Готово | Клавиатура, Esc и сохранение draft при фоновом refresh |
| Empty state | Готово | Единый fail-closed `hausman-hub-ui-state` v1 |
| Responsive | Готово | Матрица 900, 1280, 1440, 1920 px, light/dark, zoom 125/150% |

Допустимые отличия платформ:

- HACS сохраняет URL navigation, keyboard focus, hover и responsive reflow;
- Android сохраняет touch-first навигацию и восстановление lifecycle state;
- размеры HACS выводятся из доступного browser viewport, а не копируются из
  Compose dp.

Автоматический gate `tests/test_hacs_android_parity.py` проверяет реестр,
semantic tokens, общую fixture, modal accessibility, aria-live и наличие всех
точек входа. Визуальная компоновка frontend при добавлении gate не менялась.
