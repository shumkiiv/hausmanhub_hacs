# Аудит выпуска Hausman for Home Assistant 1.52.181

Дата: 2026-08-27.

## Результат

- Выпущены contracts `0.62.0` и HACS `1.52.181` без assets.
- HACS tag `v1.52.181` указывает на `8e610d5`, GitHub Actions
  `33059483823` завершился успешно.
- Production Home Assistant использует installed/latest `v1.52.181`.

## Проверки до установки

- Contracts: 72 schemas, 131 fixtures, 50 OpenAPI paths.
- HACS local release gate: 1734 теста, 4 skipped.
- Browser gate: 19 Chromium-тестов, включая visual и accessibility.
- Репозиторий и release package не содержат запрещённых файлов или секретов.

## Резервная копия и установка

- Автоматическая копия со всеми настроенными агентами не была принята из-за
  недоступного `KeeneticSSD`.
- Отдельно создан защищённый full backup `6d01ac53` в `hassio.local`, размер
  882913280 байт. Он содержит Home Assistant, базу и все add-ons.
- Версия установлена точно через update service, config checks приняты, Core
  перезапущен один раз.

## Проверка production

- 12 служебных сущностей Hausman доступны, `unavailable=0`.
- В каталоге 53 сценария, все 53 включены; Scenario Health `healthy`.
- Node-RED 22.0.2 установлен, запущен и подключён; доступны два managed flow.
- Live CSS и JS имеют cache version `1.52.181` и содержат полный каталог,
  отступ заголовка от иконки, высоту Hero 54 px и запрет hover-сдвига.
- Read-only smoke прошёл, максимальная задержка API 100 мс. Физические команды
  дому не отправлялись.
- В system log после restart нет записей Hausman.

## Откат

Восстановить protected full backup `6d01ac53` либо точно установить
`v1.52.180`, выполнить config check и один restart.
