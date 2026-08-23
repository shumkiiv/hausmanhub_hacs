# HACS UI 1.52.161: merge, release и production deploy

Дата: 2026-08-23.

## Решение

- Накопленная HACS UI-линия объединена с актуальной `main` без force push и
  без потери изменений обеих веток.
- Исторически занятые версии `1.52.149` и `1.52.150` не переиспользовались.
  Итоговый UI-выпуск получил версию `1.52.161`.
- Последний параллельный хотфикс tablet power из `main` влит отдельным
  обычным merge и подтверждён профильными тестами.

## Проверка и публикация

- Full gate: 1662 tests, 4 skipped.
- Critical runtime: 119 tests, branch coverage 75%.
- Browser gate: 13 tests, включая visual, accessibility и keyboard.
- Merge commits: `d29488a`, `fa47abe`.
- Tag и Release: `v1.52.161`, без assets.
- GitHub Actions `32616059573`: success.

## Production

- Config checks до установки, после установки и после restart: valid.
- Full backup `f251700b`: local и KeeneticSSD по 914780160 байт, база,
  Home Assistant, 10 add-ons и `ssl` включены.
- HACS exact install `v1.52.161`, один restart.
- Installed/latest `v1.52.161`, config entry loaded, 92 assets match,
  Hausman errors 0.
- После штатного прогрева unavailable вернулось 58 -> 10 за 41 секунду.
  Финальный read-only smoke passed, pending operations 0.
- Во время deploy физические команды не отправлялись.

Подробный аудит: `docs/RELEASE_AUDIT_1.52.161.md`.
