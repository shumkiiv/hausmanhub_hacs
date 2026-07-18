# Неактивный checklist one-room climate canary

Дата: 2026-07-18. Подготовлен для HASC 0.5.6.

Этот checklist не разрешает и не запускает физическое управление. Он нужен,
чтобы будущий canary одной комнаты начинался только после отдельного явного
разрешения владельца на конкретную комнату и минимальный набор действий.

## До запроса разрешения

- Установленная версия HASC и GitHub Release совпадают, а climate bridge
  завершает обычный deploy в `disabled` без target и canary room.
- Реестр собран через явный выбор кандидатов, прошёл preview и сохранён только
  после отдельного подтверждения. Android видит только публичные HASC ID.
- Readiness не показывает missing, moved, stale или authority mismatch.
- Для одной кандидатной комнаты evidence имеет `ready`: не менее трёх
  разнесённых наблюдений, shadow-перевод target и room off, ноль аномалий.
- Shadow-проверка измерила ноль Climate API command POST, а текущий
  climate-core остаётся владельцем policy, cooldown, safety и feedback.
- Проверен обратимый возврат в `disabled`; он удаляет target и canary room и
  закрывает Android controls до выполнения команды.
- В options HASC выполнен «Проверить готовность canary одной комнаты» для
  сохранённой публичной room ID. Запрашивать разрешение можно только при
  `ready`, `operation=clear`, `rollback=ready` и scope ровно
  `set_room_target`, `turn_room_off`. Сам этот результат ничего не включает.
- Если проверку читает local-admin consumer, его response обязан пройти
  установленную schema v1, иметь `state_fresh=true` и ещё не наступивший
  `state_valid_until`. Планшетный аккаунт не должен иметь доступ к route.
- Home-контракт планшета обязан иметь version 3. Только разрешённая комната
  может получить `control.enabled=true`; список `actions` ограничен target и
  room off, `action_inputs` задаёт для target диапазон 18–28 °C с шагом
  0,5 °C, а `blocked_reasons` пуст только у действительно исполняемой комнаты.

Если хотя бы один пункт не выполнен, разрешение на физический canary не
запрашивается.

## Отдельное разрешение владельца

Разрешение должно явно назвать одну публичную HASC room ID и допустимые
действия. Первый набор ограничен `set_room_target` и `turn_room_off`. Разрешение
на публикацию релиза, установку HACS, shadow или этот документ разрешением на
физическую команду не считается.

## Порядок только после разрешения

1. Непосредственно перед включением перечитать readiness и evidence. Любая
   новая причина или несвежий snapshot отменяет запуск.
2. Оставить ровно одно устройство комнаты со scope `canary`, owner
   `climate_core` и подтверждёнными capability/binding; снова пройти preview.
3. Включить canary только для разрешённой комнаты и убедиться, что Android не
   показывает другие исполняемые комнаты или действия.
4. Выполнить ровно одно разрешённое изменение target. HTTP acceptance считать
   только `pending`, а успехом — лишь `confirmed` из последующего read-only
   snapshot и физического feedback текущего climate-core.
5. Не отправлять вторую операцию, пока первая pending. Затем отдельно проверить
   room off только если оно было включено в явное разрешение.
6. Сразу вернуть bridge в `disabled` и убедиться, что target/canary room
   удалены, controls закрыты, а новые действия fail closed.

## Немедленная остановка

Нужно вернуть `disabled` без повторной команды при timeout, rejected,
неизвестном результате, stale state, authority change, missing/moved binding,
неожиданном физическом состоянии, транспортной неопределённости или любом
несовпадении с разрешённой комнатой. HASC не должен угадывать успех и не должен
обходить cooldown или rollback текущего climate-core.

В checklist не записываются адреса, токены, source/entity ID, backend payload,
названия реальных устройств или данные дома. Допустимы только публичный HASC
room ID, время, coarse status и итог `confirmed`/`rejected`/`timed_out`.
