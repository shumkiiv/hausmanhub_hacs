# Шторы: уточнение части 6

Планировщик завершил read-only проверку на основе кода `9b127c3`,
контекст компонента `664d5fe`. Ограничения кухня80/кабинет90 одобрены.
Новая публичная команда или версия контракта не требуется: исполнимая
проба full receipt validator допускает requested100/observed80, а
open_cover остаётся без числовых полей. Это локальный план, не установка.

## 6.1. Единый путь команды

В `application/curtain_command_policy.py` отделить неизменяемый внутренний
план отправки от первоначального payload: target/entity, public action,
requested, service/data, confirmation action/value, policy revision,
generation, limited. Requested сохраняет идентичность и fingerprint.
Applied задаёт разрешённую физическую цель. Observed берётся только из
фактического проверяемого чтения, связанного с этой командой.

В `application/scenario_executor.py` scalar/batch, обычный и вложенный
сценарии должны сходиться до dispatch. Ограниченный open_cover отправляет
set_cover_position80/90, но публичные actionId/targetId/index не меняются.
Отсутствие нужного descriptor запрещает отправку, fallback open недопустим.
Проверять target/entity, descriptor, revision/generation перед отправкой.
NaN, infinity, bool, out-of-range отклоняются до clamp.

RED/GREEN: все четыре входа, порядок batch, похожее имя чужой цели,
подмена descriptor/generation, один физический вызов, отсутствие raw open.

## 6.2. Квитанция и наблюдение

В `application/device_action_receipts.py` и executor менять только ветку
штор. `set_position` сохраняет actionValue=requested100, observedValue
получает actual80. `open_cover` не получает числовых полей; actual state
и пояснение ограничения возвращаются в существующих message/reason.
Подтверждение limited open проверяет позицию, не один open/opening.
Не брать observed из payload, applied либо позднего несвязанного чтения.

После отправки требуется новое достоверное evidence позиции. Restored,
cached, bool, NaN, неизвестная позиция и attribute-only обновления не
подтверждают движение. Значение79 не подтверждает цель80 без отдельного
согласованного допуска. При unknown не повторять отправку.

No-op разрешён по свежей доверенной исходной позиции: skipped,
commandSent=false, confirmed=false, terminal=true, без readBack.
Replay возвращает исходную квитанцию без пересчёта applied по новой policy;
другой payload с тем же ключом конфликтует. Незавершённая отправка
разрешает повторное чтение, но не повторное движение.

RED/GREEN:100/80/80,100/80/79, open без position, unknown, обе схемы,
replay, no-op fullCore, фактические timestamps и evidence identity.
Новый контракт понадобится только при новом требовании машинного applied
до подтверждения или numeric observed для open_cover. Сейчас его нет.

## 6.3. Защита каждой шторы

В coordinator и verified state storage хранить target/entity, generation,
подтверждённое automatic-close, manual-open evidence, latchedAt,
конкретный releaseSunriseAt и lastProcessedSunrise. Node-RED получает
серверный снимок, не владеет latch.

Manual-open после собственного auto-close создаёт личную latch. Manual
intent временно запрещает конфликтующий auto-close уже до подтверждения;
unknown не даёт права немедленно закрыть. Manual-close не очищает latch.
Только следующий достоверный sunrise снимает её, сначала durable save,
потом открытие. Duplicate/старое событие не снимает новую защиту.
Restart сохраняет её; corrupt store запрещает auto-close, но не обычное
авторизованное ручное управление. Внешнее неатрибутированное изменение
позиции считать ручным вмешательством, а не своим подтверждением.

RED/GREEN: независимые targets, out-of-order sunrise, restart у границы,
manual-close, unknown, corrupt store, смена identity/generation, сбой save.

## 6.4. Источник и миграция

Все четыре точных target из catalog включить в runtime source и обе
общие ручные сцены, без прямого service обхода. Сохранить manual attribution
во вложенных сценах. Источник выполняется через настоящий async_plan,
server snapshot, typed actions, source/hash/trace/bindings validation.
Runtime/tools/hash/manifest синхронизировать после финальных байтов.

RED/GREEN: sunrise4, manual-open из0, reached no-op, одна unavailable
или latch не мешает остальным, sunset/lux/light уважают latch, dry-run
не отправляет и не пишет state. Конечный fixture58 проверяется без
изменения независимого before ради прохождения теста.

## Аппаратная граница

Физический смысл80/90 и перевод в установленную HA-шкалу не подтверждены.
Policy содержит явное подтверждение шкалы; локальные тесты используют
синтетическую известную шкалу. Не включать зависимое production-исполнение
до подтверждения. HA80 не выдавать за доказанные80% полного хода.

Сохранить кабинетный guard: close_cover только при достоверном position>20.
При<=20 пропуск, unknown не разрешает закрытие. Это не set_position20 и
не нижний предел движения.

Calibration authority только серверная, точная цель/срок/операция,
с проверками expiry/другой цели/reuse/revoke. JSON-флаг не создаёт authority.
Production authority не выдавать. SET/RESET limits, reverse/invert не
менять; GET полного калибровочного состояния не предполагать. Прямые HA
services и аппаратный пульт могут обходить Hausman policy.

## Завершение

Части выполнять последовательно одним writer, root пишет контекст.
После сквозных/schema tests полный pytest, package, итоговый diff и commit.
Все activation_ready остаются закрытыми до общей приёмки. Tester начинает
только после завершённого handoff worker, затем review/security по цепочке.
Программный PASS не подтверждает калибровку и не разрешает движение сам по себе.
