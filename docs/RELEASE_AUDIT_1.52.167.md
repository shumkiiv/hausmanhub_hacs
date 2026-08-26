# Release audit HACS 1.52.167

Дата: 2026-08-26.

## Результат

- Сценарии поддерживают одну или несколько комнат, пустая область означает
  «Весь дом». Старое поле `roomId` продолжает читаться и записывается как
  зеркало первой комнаты.
- Backend проверяет выбранные комнаты по живому каталогу и не теряет прежнюю
  область при временной деградации каталога.
- Редактор HACS группирует цели по комнате, типу, физическому устройству и
  команде. Добавлены фильтры, предпросмотр, восстановление после ошибок,
  выборочное дублирование и пакетные операции.
- Конфликт ревизий сообщает изменённые комнаты и действия, а событие
  `scenario_changed` перечисляет изменённые поля.

## Версия и публикация

- Version/tag: `1.52.167`, `v1.52.167`.
- Release commit: `623c98d`.
- Release: `https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.52.167`.
- Release не содержит assets.
- Pin: contracts `0.58.0`, source commit `828b288`.

## Проверки

- Полный local release gate: 1702 теста, 4 skipped.
- Critical runtime: 148 тестов, 77% branch coverage.
- Chromium browser gate: 13 из 13.
- Проверки fixtures, совместимости Android, staged version, README sync,
  package safety и product naming завершены без ошибок.
- Самостоятельное ревью исправило очистку области при деградации каталога и
  восстановление редактора после отсутствующей комнаты.

## Production deploy

- Использована автоматическая защищённая копия `0907efca`: Home Assistant и
  база включены, размер локального файла 883333120 байт, failed agents нет.
- Дополнительное копирование backup долго оставалось на
  `copy_additional_locations`; локальный файл уже был готов. После
  обязательного restart job завершился со 100%, backup manager вернулся в
  `idle`.
- Три config checks приняты как `valid`: до установки, после установки и
  после restart.
- Выполнен точный `update.install` версии `v1.52.167` с `backup=false`.
- Выполнен один restart Home Assistant.
- Installed/latest равны `v1.52.167`.
- Read-only smoke: capabilities, dashboard, scenarios, scenario catalog и
  scenario health отвечают HTTP 200; список содержит 41 сценарий, каталог
  41 действие.
- Все 11 sensor Hausman доступны. Панель отдаёт asset версии `1.52.167`,
  ошибок `custom_components.hausman_hub` уровня ERROR/CRITICAL нет.
- Физические команды не отправлялись.

## Rollback

Восстановить backup `0907efca` либо явно установить `v1.52.165`, затем
выполнить config check, один restart и read-only smoke.
