# Аудит выпуска Hausman for Home Assistant 1.52.183

Дата: 2026-08-27.

## Результат

- Исправлено наложение описаний в выборе движка редактора сценария. Текст
  Hausman и Node-RED переносится внутри своей карточки и не пересекается.
- Tag `v1.52.183` и Release без assets указывают на `3969a41`.
- GitHub Actions `33065225032` завершился успешно.
- Production Home Assistant использует installed/latest `v1.52.183`.

## Проверки до установки

- Local release gate: 1734 теста, 4 skipped.
- Browser gate: 19 Chromium-тестов, включая visual и accessibility.
- Геометрический тест проверяет отсутствие горизонтального и вертикального
  переполнения обеих карточек, вложенность текста и интервал между заголовком
  и описанием.
- Пакет HACS, версия, README и границы публичного репозитория проверены.

## Резервная копия и установка

- Повторно использован созданный в тот же день protected full backup
  `6d01ac53` в `hassio.local`, размер 882913280 байт.
- Версия установлена точно через update service с `backup=false`.
- Config check принят до установки и после неё, Core перезапущен один раз и
  штатно поднялся.

## Проверка production

- installed/latest равны `v1.52.183`, обновление не выполняется.
- В каталоге 53 сценария, все 53 включены; Scenario Health `healthy`.
- Node-RED 22.0.2 установлен, запущен и подключён; доступны два managed flow.
- Live CSS содержит `white-space:normal`, `overflow-wrap:break-word` и
  `overflow:hidden`. Основной CSS и JS используют cache version `1.52.183`.
- Read-only smoke прошёл, максимальная задержка API 102 мс. Физические команды
  дому не отправлялись.
- Все 12 служебных сущностей доступны. В system log нет записей Hausman.

## Откат

Восстановить protected full backup `6d01ac53` либо точно установить
`v1.52.182`, выполнить config check и один restart.
