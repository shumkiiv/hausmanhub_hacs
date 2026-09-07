# Подтверждение текущей шкалы только шторы кабинета

Статус: завершённый read-only план rescue_planner, принят root.
Продолжение частей6.3/6.4 после9e17d50. Нового публичного API нет.

## Основание и границы

Владелец разрешил текущую шкалу без повторной калибровки. Кабинет:
target `entity_9164132c7692d6f5`, entity `cover.0xa4c1381b3fb1c985`, CMD900E.
Владелец подтвердил закрытие0, половину после запроса50/HA49 и примерно90
после запроса90/первогоHA90/окончательногоHA88 после защитного STOP.
Это пригодность приблизительной шкалы для cap90, не точность каждого
процента. Допуск квитанции автоматически до2 пунктов не расширять.

Кухня не подтверждена. SET/RESET/reverse/invert, калибровочные команды,
физические команды, HA writes, установка, релиз и активация исключены.
Все activation_ready остаются false до отдельной общей приёмки.
Обычный JSON команды или редактора policy не создаёт разрешение.
Прямой HA и аппаратный пульт могут обходить программный cap Hausman.

## 1. Отдельное проверяемое хранилище

Создать application/curtain_scale_confirmation.py,
curtain_scale_confirmation_storage.py, tests/test_curtain_scale_confirmation.py.
Использовать VerifiedSafetyStore, ключ
`hausman_hub.curtain_scale_confirmation.<entry_id>`.

Schema1: revision, entryId, targetId, entityId, entityUniqueId, deviceId,
deviceIdentifiers, confirmedAt, confirmationSource=owner_observed_current_scale,
state=confirmed|revoked. Identity формирует сервер из entity/device registries;
нужен реальный физический identifier, не только имя или deviceId.
Первый проход разрешает административное подтверждение только точного
кабинета, но на новой установке store пуст и разрешения нет.

Интерфейсы:

- async_load();
- async_confirm_office(expected_revision, expected_identity_digest);
- async_revoke(expected_revision);
- authorization_snapshot(target_id) -> confirmed, revision, identity_digest.

Разрешение выдаётся лишь при совпадении сохранённой и текущей identity.
Пустой store, недоступный registry, другая entry, повреждение main/sidecar,
rollback старой записи, неподтверждённая запись закрывают допуск. Revoke
сохраняется после restart. Подмена uniqueId/device/physical identifiers
аннулирует подтверждение; возврат прежнего имени не восстанавливает его.
Ошибка save не публикует разрешение в памяти.

## 2. Узкое административное действие

Изменить config_flow.py, strings.json/используемые переводы,
tests/test_config_flow_adapter.py. Отдельный штатный HA options step:
«Подтверждение шкалы шторы кабинета», действия confirm/revoke.
Форма показывает точную цель и серверную identity. При показе сохраняет
revision/digest на сервере, при отправке перечитывает identity и выполняет
CAS. Никаких target/entity/fingerprint/timestamp/list из отправленной формы.
Не хранить bool-authority вторично в entry.options: вызвать сервис и вернуть
существующие options по образцу async_step_scenario_controls.

Тесты: stale form, замена между показом и submit, отказ произвольным полям,
кухне/другим целям; обычный device-action и policy JSON не подтверждают
шкалу. Существующая admin boundary не ослабляется. Ноль HA motion services.

## 3. Динамическая общая политика

Изменить application/curtain_command_policy.py, __init__.py, при необходимости
финальную перепроверку scenario_executor.py и tests/test_curtain_command_path.py.
Загрузить сервис до executor, передать динамический
scale_authorization_provider=scale_confirmation.authorization_snapshot.
Не hardcode confirmed office глобально, не вычислять frozenset один раз.
В policy revision включить revision подтверждения и identity digest;
перед dispatch перечитать разрешение. Отзыв/подмена отменяют старый план.
Тестовые with_confirmed_scales не использовать в production composition.
Ошибка store закрывает только зависимые команды; STOP остаётся доступен.

Тесты настоящего пути: office open ->90; request100/applied90 с сохранённой
публичной идентичностью; restart; kitchen scale_unconfirmed; revoke и
смена identity после планирования дают ноль отправок; scalar/batch/scenario/
nested имеют одну authority; STOP при неисправном store; guard office
current_position>20 перед закрытием не меняется. Revoke не обещает отмену
уже отправленного движения и сам не отправляет STOP.

## 4. Проверка и передача

RED/GREEN на каждом этапе, затем полный pytest, профильный набор,
config-flow/storage/restart tests, package, compileall и итоговый diff.
Не менять независимый fixture58 под реализацию. Рабочий отчёт с точными
командами/результатами и commit передать root. AI_CONTEXT и LLM_WIKI ведёт root.

Root после всех общих release checks и разрешённой установки сверяет
конкретную physical identity, использует только штатное admin действие,
не правит .storage вручную. Проверяет сохранение после предусмотренного
restart без движения: office confirmed, kitchen not confirmed, officecap90.
Текущее разрешение владельца не требует повторного вопроса о калибровке.

## 5. Связь с общей приёмкой части7

После этого инкремента worker завершает часть7 существующего recovery plan:
неизменённый fixture58 -> конечная миграция -> все8 service/async_plan/executor
с fake HA; точные hashes; fault/restart/dryrun/corruption/duplicate listener;
schema/compatibility и полный suite, runtime JS, действующие локальные checks.
Production activation не включать для имитации PASS. Версия релиза пока
не повышается. Завершённый worker handoff допускает tester; затем только
после PASS reviewer и security_auditor. Передачи между стадиями ведёт root.
