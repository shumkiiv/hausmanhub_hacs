# Release audit HausmanHub 1.52.100

Дата: 2026-08-15.

## Причина и изменение

- Strict tablet и climate routes используют canonical error taxonomy из
  contracts `0.34.0` (`7d4a2f9`). Code, HTTP status, retryable и безопасное
  русское сообщение выбираются по закреплённой policy.
- Произвольный exception text не возвращается. Unknown code fail-closed
  превращается в `internal_error`, request ID ограничен, details фильтруются
  по allowlist.
- Production-аудит промежуточной `1.52.99` нашёл blocking-call warning при
  первом lazy-чтении packaged taxonomy. В `1.52.100` policies загружаются
  через Home Assistant executor до регистрации API.
- Backend commits: `1b1571b`, `ef98566`; release commit: `43dc778`.

## Проверки выпуска

- Полный gate `1.52.99`: 1414 тестов пройдено, 4 пропущено. GitHub Actions
  `31880486914` завершён успешно.
- Полный gate `1.52.100`: 1415 тестов пройдено, 4 пропущено. Synthetic
  fixtures, Android compatibility, staged version, product naming, HACS
  package и обе repository boundary проверки пройдены.
- Финальный аудит release diff подтвердил только changelog, manifest version,
  cache-bust frontend и согласованные ожидания тестов.
- `git diff --check` пройден. GitHub Actions `31881355990` завершён успешно.
- Annotated tag и GitHub Release `v1.52.100` опубликованы из `43dc778`.

## Production

- До первой установки config check принят.
- Создан полный compressed backup `b9e79fa4` размером 870.88 MB на активном
  KeeneticSSD. Копия включает Home Assistant 2026.8.2, базу данных, три папки
  и десять add-ons.
- `v1.52.99` установлена явным `update.install` без второго backup. После
  config check выполнен один restart. Live-аудит обнаружил только новый
  blocking-call warning, поэтому выпуск сразу заменён исправлением.
- `v1.52.100` установлена явным `update.install` с тем же свежим rollback
  backup. После config check выполнен один restart.
- Финальные installed/latest и admin panel равны `1.52.100`. Panel script
  содержит cache refs 1.52.100 и не содержит 1.52.99.
- Все девять сущностей платформы доступны. Runtime fresh, phase `managed`,
  authority `hausman_hub`, active operations и blocked reasons равны нулю.
  Dashboard отвечает и содержит 13 комнат, 86 устройств и три сценария.
- Read-only strict-error probe вернул `hausman-hub-error v1`; после него
  system log не содержит записей HausmanHub. Физические команды, климатические
  цели и сценарии при deploy не запускались.

## Откат

При необходимости восстановить полный backup `b9e79fa4` с KeeneticSSD. Он
возвращает production к проверенной HACS 1.52.98 до обеих установок этого
release-цикла.
