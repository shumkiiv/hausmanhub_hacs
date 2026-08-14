# Production canary HACS

Этот gate применяется после подготовки релиза, но до полного HACS deploy. Он
не связан с выбором климатической canary-комнаты и не выдаёт право на
физическое управление.

## Последовательность

1. Создать полный backup и дождаться его готовности локально и во внешнем
   хранилище. Записать безопасный идентификатор в `backupId`.
2. Выполнить `homeassistant.check_config`. Только принятый результат даёт
   `configCheckPassed: true`.
3. После установки canary build выполнить ровно одну health-пробу
   `GET /api/config`. Команда устройству, вызов service и любой `POST` не
   считаются health-пробой.
4. Не меньше 60 секунд собрать как минимум 12 результатов
   `GET /api/hausman_hub/v1/dashboard`. Для каждого результата записать HTTP
   status, latency, `inventory.summary.unavailableCount` и максимальный возраст
   незавершённой операции из read-only admin journal. Если pending нет,
   использовать `null`.
5. Сохранить только обезличенный JSON вне репозитория и запустить:

   ```text
   python3 tools/check_production_canary.py /secure/path/canary.json
   ```

Код 0 и `decision=proceed` разрешают полный deploy. Код 10 и
`decision=rollback` требуют автоматического возврата к backup или предыдущей
проверенной версии. Код 2 означает неполное доказательство и тоже запрещает
продолжение.

## Критерии rollback

- error rate больше 1%;
- p95 latency больше 1000 мс;
- максимальный pending age больше 60 секунд;
- unavailable count выше baseline хотя бы в одном snapshot;
- окно короче 60 секунд, меньше 12 samples или пропущена метрика;
- backup, config check либо единственная read-only health-проба не доказаны.

Artifact и вывод gate не содержат token, адрес дома, entity ID, имена устройств
или полные ответы API. Фактический rollback выполняется release-процедурой, а
не проверяющим скриптом.
