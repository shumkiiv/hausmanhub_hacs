# HACS 1.52.162: надёжность сценариев и подготовка релиза

Дата: 2026-08-24.

## Результат

- В restart-режиме вытесненный запуск возвращает вызывающей стороне статус
  cancelled, но не записывается в operation journal. Вложенный сценарий с
  причиной restarted_by_new_trigger учитывается как пропущенный шаг, поэтому
  родительский сценарий не получает ложную ошибку.
- После успешного turn_on и ошибки следующего шага исполнитель ищет далее
  предусмотренный turn_off того же target и выполняет его сразу. Защитная
  квитанция помечается safety_cleanup. Повторный turn_off для той же цели не
  запускается.
- List payload покрыт регрессией для полного упорядоченного definition,
  включая disabled scenario. Это подтверждает backend-часть канала планшетного
  редактора; Android должен получать этот endpoint отдельным клиентским
  треком.
- Версия подготовлена как `1.52.162`; cache-ссылки HACS frontend и проверки
  синхронизированы с manifest.

## Проверка

- Целевые тесты: 105 passed, 3 subtests passed.
- Полный pytest: 1669 passed, 4 skipped, 1006 subtests passed.
- Staged local release gate: 1665 tests, 4 skipped, все synthetic fixtures,
  Android compatibility, version, README, naming, HACS package и repository
  safety passed.
- Kimi read-only review был запрошен для четырёх исходных файлов, но не
  вернулся за контрольное время. Его процесс остановлен, изменений от Kimi
  нет. Итоговый self-review сделал Codex.

## Граница deploy

- Commit и push разрешены владельцем. Фактический deploy не запускался: нужен
  отдельный явный запрос с целевой средой и окном работ.
