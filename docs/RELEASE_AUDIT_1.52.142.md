# Hausman Hub HACS 1.52.142: аудит классификации сценариев

Дата: 2026-08-22

## Результат

- Список сценариев теперь соответствует контракту
  `hausman-hub-scenario-list` v1 из contracts `0.49.0` (`5478341`).
- Backend определяет `activationKind`, `roomId` и `protected`, а также
  добавляет вычисляемые `nextRun`, `lastResult` и `temporaryException`.
- Поддержаны `manual`, `automatic`, `hybrid` и `system`. Защищённые системные
  сценарии нельзя удалить, обновление не снимает их системную группу.
- Старое storage без новых полей загружается и безопасно мигрирует при чтении.
- UI baseline `1.52.138` сохранён: визуальная структура и поведение frontend
  не менялись, выполнен только cache-bust версии.

## Релиз и проверки

- Feature commit `04064d2`, release commit/tag `3a1fb54`, `v1.52.142`.
- GitHub Release:
  https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.52.142.
- Contracts validator: 53 schemas, 98 fixtures, 39 OpenAPI paths. Vendored
  source совпадает с contracts `0.49.0` (`5478341`).
- Финальный staged `tools/check_local_release.py`: 1580 tests, 4 skipped.
  Fixtures, Android compatibility, package, version, safety и repository
  gates успешны.
- GitHub Actions `32562805751` завершён успешно.

## Production deploy

- До установки создан full backup `7a4b14cd`. Копии по 921886720 байт
  подтверждены в `hassio.local` и `hassio.KeeneticSSD`.
- До и после установки `homeassistant.check_config` вернул HTTP 200.
- `v1.52.142` установлена явно через `update.install` с `backup=false`, затем
  Home Assistant перезапущен ровно один раз.
- После restart update entity подтверждает installed/latest `v1.52.142`,
  `in_progress=false`. Config entry Hausman Hub находится в состоянии
  `loaded`.

## Проверка после restart

- Production response списка из 39 сценариев проходит JSON Schema. В нём 24
  системных защищённых сценария, 9 ручных и 6 автоматических. Все записи
  содержат новые поля контракта.
- Девять ранее настроенных shadow-сценариев остались в
  `commandMode=shadow`. Node-RED не изменялся и продолжает владеть
  физическими ветками во время soak.
- Operation journal из 100 последних записей проходит JSON Schema.
- Все 88 frontend JS/CSS файлов production совпадают с release по SHA-256.
- Ошибок Hausman в system log нет. Сохраняется известное предупреждение о
  расхождении физического датчика улицы и погодного сервиса.
- Во время деплоя scenario run, test и command API не вызывались.

## Откат и следующий шаг

- Для полного отката использовать backup `7a4b14cd`.
- Android и HACS frontend должны начать использовать серверную классификацию
  отдельными UI-релизами. До этого новые поля остаются обратно совместимыми.
- Shadow soak Node-RED продолжается. Переключать физического владельца до
  завершения сравнения нельзя.
