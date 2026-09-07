# Честные квитанции штор при возможном эхе позиции

Статус: bounded addendum rescue_planner с уточнением root. Реализовать
после отдельного scale authority, до финальной общей приёмки7.
Диагноз root: docs/migration/CURTAIN_POSITION_EVIDENCE_2026-09-07.md
в корневом проекте. Никаких новых transport, Z2M settings, HA writes,
motor limits, калибровки, UI или публичного API.

## Основание и решение

Установленный Tuya converter возвращает requested position после set,
Z2M публикует его optimistic по умолчанию, подавление не настроено.
HA не помечает это assumed_state. Поэтому свежесть, смена числа и его
равенство цели не доказывают physical result. Scale authority владельца
даёт право cap90, но не доверие любому новому position.

Не вводить общий запрет автозакрытия четырёх штор. Первое закрытие по
прежним утверждённым guards допустимо; неопределённый исход не повторять
автоматически в том же цикле. Это не означает, что оно выполнено физически.

## 1. Разделить reported и физическое подтверждение

application/curtain_command_policy.py, scenario_executor.py, __init__.py,
tests/test_curtain_command_path.py и receipt tests.

Серверная evidence policy по точным четырём target/entity identities:
position_provenance=unverified_optimistic|verified_device_report.
Для известных четырёх production целей значение unverified_optimistic.
В этом инкременте нет получения verified_device_report из обычного HA
state. Положительное значение только для явного синтетического adapter
в тестах, не из JSON, options или одного scale-confirmation bool.
Смена identity не переносит положительное доверие. Lights и чужие covers
не меняются. Не переопределять скрыто смысл trusted_curtain_position:
его числовая/временная проверка остаётся reported evidence для guards,
дополнительная проверка can_confirm_curtain_result отвечает за provenance.

Проверить scalar и deferred batch/scenario readback для open/close/
set_position/STOP. STOP всегда имеет безопасный доступный путь, но эхо
STOP не доказывает физическую остановку.

## 2. Честный совместимый ответ

После успешного вызова: accepted=true, confirmed=false, status=accepted,
reason=curtain_position_provenance_unverified,
message=«Команда передана. Физическое положение не подтверждено.»
Full сохраняет decision=executed, фактический commandSent, IDs/actionValue/
порядок batch. Сделать реальное чтение: attempted=true, matched=false,
реальные время/observedState. Echo не публиковать как доказанный
observedValue; reported число допустимо во внутренней диагностике.
Не ждать полное окно ради непроверяемого потока и не повышать статус
поздним echo. Не расширять tolerance до2 пунктов по пробам владельца.

## 3. No-op и один automatic dispatch за цикл

По echo нельзя сообщать already_in_target_state как доказанный результат.
Full replay возвращает сохранённый accepted/unconfirmed без движения.
Новая явная ручная команда с новым ID может выполняться по cap/guards;
её не подавлять только потому, что echo равно requested. Защитный skip
office reported<=20 остаётся, но с причиной защиты, не physical reached.

В curtain_protection.py и его verified store добавить при необходимости:

cycleId, generation, automaticCloseIntent(operationId,identityDigest,
sourceHash,phase=reserved|dispatch_intent|unconfirmed),
manualInhibitUntilSunrise. Утреннее открытие имеет собственный one-shot intent.

Все sunset/lux/light триггеры одной цели используют один cycleId.
Цикл меняется только доверенным sunrise, не eventID/restart. До вызова
сохранить dispatch_intent. Crash после этой границы означает возможную
отправку и запрещает повтор. reserved освобождается лишь при доказанном
отказе ДО перехода к dispatch. Ошибка save запрещает отправку.
Optimistic echo не завершает intent и не создаёт confirmedAutomaticClose.
Смена source/identity не стирает intent для повторной отправки автоматически.

Первое обычное закрытие: existingguards -> exact identity/generation ->
manual latch -> durable intent -> один сервис. Следующие sunset/lux/light
дают curtain_close_already_attempted, не «уже закрыта».
Компромисс: при неизвестном исходе автоматического повтора нет до нового
доверенного цикла; пользователь может отправить новую ручную команду.

## 4. Ручная защита и restart

После automaticCloseIntent (включая uncertainty) явное ручное открытие
или позиционирование усиливает latch до следующего sunrise и отменяет
устаревшие automatic планы через generation. Manual-close не очищает
latch/intent. Внешнее неатрибутированное движение может усилить защиту,
но не снять её или создать automatic ownership. Собственное echo не
считать ручным вмешательством и не использовать для снятия защиты.

Старые confirmedAutomaticClose без независимого provenance нельзя
восстановить как доказанные: консервативно понизить до historical/
unconfirmed intent, сохранив существующую manual latch. Старое состояние
своей атрибуции не даёт права повторить возможно выполненную команду.
Доверенный следующий sunrise атомарно завершает прошлый цикл/intent/latch,
затем разрешает одно утреннее открытие по scale guards. Старые callbacks
и повторные sunrise не меняют новую защиту и не повторяют dispatch.

## 5. Сохранить точные границы

Первое auto-close блокируется только конкретным непройденным guard:
office unknown/<=20; любая цель manual latch/предыдущий intent/identity
или source-generation mismatch/ошибка safety storage. Scale-dependent
позиционные ограничения сохраняются. Кухня scale-unconfirmed, но обычный
close_cover не блокировать только из-за отсутствия scale authority, если
прежняя policy его разрешает. Guard20 не физический нижний предел.

## 6. RED/GREEN и полная приёмка

- Первое разрешённое auto-close всех4: один call, unconfirmed, без ownership.
- Echo90 через0,11s, повтор через5/30s, последующее88 и новые timestamps
  не подтверждают; отсутствие assumed_state не является положительным proof.
- Включённая office scale authority не меняет provenance.
- Scalar/batch/scenario/nested и full/legacy schema, cap90, kitchen unconfirmed.
- No-op из echo не скрывает новую ручную команду, replay не двигает повторно.
- Sunset/lux/light afterattempt, restart dispatch_intent: ноль новыхcalls.
- Доказанный pre-dispatch отказ не помечается физической попыткой.
- Manualopen после uncertainclose latch, manualclose не clear.
- Sunrise ровно один новый цикл/open; stale/callback/sourceidentity drift.
- Собственное sunrise echo через настоящий state event не создаёт false
  manual latch, настоящее ручное событие защищено. Не тестировать только
  прямые callbacks, нужен полный service/async_plan/executor/eventbus путь.
- STOP доступен при неисправном store, но не falselyconfirmed; lights и
  посторонние covers сохраняют поведение; synthetic proven evidence работает.

После изменения production-байтов полный suite/package/итоговыйdiff
повторить до commit/handoff. Все activation_ready false, releases нет.
