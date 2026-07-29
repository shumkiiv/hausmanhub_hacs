# Перенос настроек из Node-RED

## Граница первого этапа

`POST /api/hausman_hub/v1/admin/legacy-settings/preview` принимает только явно
переданный экспорт global context. Маршрут доступен локальному администратору,
не читает Node-RED по сети и не вызывает ни одно хранилище Home Assistant.

Ответ разделяет данные на пять групп:

1. `migratable` — общая целевая температура и найденные в снимке цели комнат;
2. `recognized_pending` — пользовательские настройки, для которых ещё нужно
   создать нативное хранилище HausmanHub;
3. `ignored_runtime` — наблюдения, таймеры, блокировки и служебное состояние;
4. `rejected_sensitive` — секреты и идентификаторы получателей уведомлений;
5. `unknown` — неизвестные поля, значения которых никогда не отражаются в
   ответе.

`write_performed` в контракте preview всегда равен `false`. Значения секретов
не входят ни в ответ, ни в `preview_id`.

## Инвентаризация legacy global context

Переносимая климатическая настройка:

- `home_target_temp`;
- из `climate_rooms` берутся только `comfortTemp`/`targetTemperature` и
  `targetHumidity`/`comfortHumidity`. Остальные поля комнаты являются снимком
  работы контроллера и не переносятся.

Распознаны и уже имеют нативную storage-модель, но ещё требуют подтверждаемой
операции импорта:

- `smart_home_light_preset`;
- `smart_home_light_off_preset`;
- `smart_home_tv_off_entities`;
- `climate_telegram_reports_enabled`;
- `kitchen_curtain_holidays`.

Не являются настройками: `ac_manual_overrides`, `ac_pause_until`, счётчики и
времена запуска кондиционеров, `not_home_mode`, погодные наблюдения,
`climate_learning`, статистика, события и защитные latch-флаги.

Никогда не переносятся этим API: `max_alert_chat_ids`, `max_alert_user_ids`,
`max_bot_access_token`, а также любое поле с маркером token/password/secret или
credential. Такие значения должны заново настраиваться через штатные secret и
config-entry механизмы Home Assistant.

## Следующий этап

До появления подтверждённой операции записи нужно:

- сопоставить legacy room id с сохранёнными комнатами климатического контура;
- добавить compare-and-swap подтверждение по `preview_id`;
- проверить миграцию, перезапуск Home Assistant и повторный импорт;
- только затем открыть отдельный apply-маршрут. Preview не станет неявно
  сохранять данные.
