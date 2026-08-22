# Release audit HACS 1.52.153

Дата: 2026-08-22. Итоговая версия направления 17 после production dry-run.

## Что выпущено

- Vendor resilience ограничивает медленные `media_player`, `remote`, Yandex
  Station TTS и conversation по времени. Circuit breaker изолирует только
  отказавший vendor-сервис и не блокирует core-команды дома.
- Настроенный домофон требует явного подтверждения пользователя. Серверный
  `dryRun=true` проверяет маршрут без физической команды.
- Реальное включение домофона получает bounded hold 15 секунд, обязательный
  `turn_off`, физический read-back и отдельную release receipt.
- Durable journal хранит только стабильную публичную цель, correlation,
  outcome и причину. Внутренний Home Assistant entity ID не сохраняется.
- HACS frontend добавляет подтверждение во все пути вызова домофона. Внешняя
  компоновка и уже выверенный интерфейс не менялись.

## Проверки релиза

- Contracts: `hausmanhub-contracts 0.55.0`, commit `154d231`, schemas и
  fixtures закреплены локально.
- Feature release `1.52.152`: commit `59f4bcb`, GitHub Actions
  `32590855161` завершился успешно.
- Production dry-run выявил пропущенную передачу `dry_run` между
  `ScenarioService` и executor. Физическая команда не отправлялась.
- Hotfix `1.52.153`: commit/tag `e793219`, GitHub Actions `32591818087`
  завершился успешно. GitHub Release:
  `https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.52.153`.
- Полный локальный gate: `1639 passed, 4 skipped, 999 subtests passed`.
  Дополнительно добавлен прямой service-level regression test.

## Production deploy

- До направления создан полный защищённый backup `89c8919a`. Копии по
  927508480 байт подтверждены в `hassio.local` и `hassio.KeeneticSSD`.
- Выполнена явная установка `v1.52.153` через `update.install` с
  `backup=false`. До установки, после установки и после единственного restart
  `homeassistant.check_config` вернул HTTP 200.
- После restart installed/latest равны `v1.52.153`. Capability
  `intercom_safety` опубликована, panel asset содержит marker версии.
- Live dry-run настроенного домофона вернул HTTP 200, `accepted=true`,
  `confirmed=false`, `dryRun=true`. Поля auto-release отсутствуют, реле не
  включалось.
- Operation journal содержит две записи: `device_action` и
  `intercom_release` с причиной `dry_run`. В обеих нет `entityId` или
  `entity_id`.
- В последних 100 строках Core после hotfix нет error, traceback или
  exception от `hausman_hub`.

## Откат

При проблеме восстановить полный backup `89c8919a`. Он содержит состояние до
установки направления 17 и доступен в обоих хранилищах.
