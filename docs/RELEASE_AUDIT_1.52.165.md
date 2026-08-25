# Release audit HACS 1.52.165

Дата: 2026-08-25.

## Результат

Главная страница больше не вводит в заблуждение при частичном dashboard-ответе:
без readiness она показывает «Состояние обновляется». Стабильное красное
состояние при реальной недоступности Home Assistant сохранено.

## Версия и публикация

- Version/tag: `1.52.165`, `v1.52.165`.
- Release commit: `8d0f36e`.
- Release: `https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.52.165`.
- Release не содержит assets.

## Проверки

- Целевой panel fallback test: passed.
- Chromium browser gate: 13 tests.
- Полный local release gate: 1693 tests, 4 skipped.
- Проверки staged version и README version sync: passed.

## Production deploy

- Перед установкой создан full backup `545e7403` с Home Assistant и базой.
- Два config checks приняты Home Assistant.
- Выполнен точный `update.install` версии `v1.52.165` с `backup=false`.
- После одного restart live API подтвердил installed/latest `v1.52.165`.
- Core API отвечает HTTP 200, все sensor Hausman доступны.
- Во время deploy физические команды не отправлялись.

## Rollback

Восстановить full backup `545e7403` либо явно установить `v1.52.162`, затем
выполнить config check, один restart и read-only smoke.
