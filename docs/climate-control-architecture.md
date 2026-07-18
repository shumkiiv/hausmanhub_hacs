# Климат через HASC: архитектура, реестр и Android API

Версия документа: 2026-07-18. Реализация относится к HASC 0.5.8.

## Итоговая схема

```text
Android-планшет
       ↕ локальный аутентифицированный HASC API
HASC: реестр комнат и устройств, публичные capabilities, проверка команд
       ↕ два фиксированных маршрута Climate API v1
существующий climate-core / Smart Home Center
       ↕ его текущие адаптеры, cooldown, safety и physical feedback
Home Assistant / Node-RED / физические устройства
```

HASC становится единственной точкой, которую должен знать Android. При этом
HASC не копирует климатические алгоритмы. Автоматический и ручной режим,
cooldown, выбор фактического исполнительного устройства, authority, защита от
устаревшего состояния и подтверждение физического результата остаются в
текущем климатическом контуре.

## Как описывается устройство

Владелец создаёт в реестре HASC логическое устройство. Одному устройству можно
назначить несколько приватных Home Assistant entities с фиксированными ролями:

- `control` — исполнитель;
- `temperature` и `humidity` — измерения;
- `physical_feedback` — подтверждение физического состояния;
- `fan`, `valve_position`, `child_lock`, `water_level` — дополнительные
  наблюдаемые части.

Android получает только стабильный HASC ID, имя, тип, доступность и список
возможностей. Он не получает `source_id` и `entity_id`.

Поддерживаемые типы:

| Тип HASC | Минимальные возможности | Исполнительный контракт |
| --- | --- | --- |
| `air_conditioner` | power, target temperature | `climate.*` |
| `radiator_thermostat` | target temperature | `trv.set_temperature` |
| `humidifier` | power, target humidity | `humidifier.*` |
| `floor_heating` | power, target temperature | `climate.*` или строго power через `switch.*` |
| `temperature_sensor` | только наблюдение | команд нет |
| `humidity_sensor` | только наблюдение | команд нет |

Реестр не создаётся из найденного оборудования автоматически. Сначала HASC
показывает администратору кандидатов из текущего Climate API, затем владелец
явно сохраняет полное соответствие. Это исключает случайный захват похожего
или перемещённого устройства.

В options Home Assistant пункт «Настроить климатические устройства» открывает
пошаговый локальный мастер. Основной путь выполняет свежий read-only импорт и
показывает кандидатов по имени и комнате. Значения selector имеют временный
вид `candidate_001`: приватный `source_id` не показывается и не копируется
вручную. Оператор задаёт публичный HASC ID и выбирает control entity штатным
Home Assistant selector. HASC повторно читает snapshot, требует неизменный
кандидат и сам подставляет private binding и только объявленные capabilities.

Комнаты и устройства всё ещё можно добавить вручную через отдельные поля.
После любого пути HASC показывает preview со статусом, счётчиками и
нормализованными причинами. Реестр не сохраняется до отдельного подтверждения.
Полный JSON доступен только как расширенный вариант. Импорт не добавляет
невыбранные устройства, не удаляет записи и не выполняет climate POST.

Пример синтетической записи:

```json
{
  "version": 1,
  "rooms": [{"id": "living", "name": "Гостиная"}],
  "devices": [
    {
      "id": "living_ac",
      "name": "Кондиционер",
      "room_id": "living",
      "kind": "air_conditioner",
      "source_id": "private-climate-source",
      "control_scope": "canary",
      "control_owner": "climate_core",
      "capabilities": ["power", "target_temperature", "hvac_mode", "fan_mode"],
      "endpoints": [
        {"role": "control", "entity_id": "climate.private_control"}
      ]
    }
  ]
}
```

`source_id` и `entity_id` хранятся только в приватном Home Assistant Store и
видны только через локальный административный маршрут.

## Android API

Все маршруты требуют обычную Home Assistant Bearer-аутентификацию, разрешают
только loopback/RFC1918/ULA источник, не поддерживают CORS и возвращают
`Cache-Control: no-store`.

Для отдельного обычного пользователя планшета, состоящего ровно в стандартной
группе Home Assistant `system-users`:

- `GET /api/hausman_hub/v1/home` — комнаты, логические устройства, состояния,
  targets, capabilities и состояние сверки без приватных ID;
- `POST /api/hausman_hub/v1/actions` — только фиксированные типизированные
  действия и версионная квитанция;
- `POST /api/hausman_hub/v1/operations` — чтение одной квитанции по непрозрачному
  `operation_id` без приватных backend-полей.

Для локального администратора:

- `GET /api/hausman_hub/v1/admin/climate-import` — read-only кандидаты и
  расхождения с текущим Climate API;
- `GET /api/hausman_hub/v1/admin/climate-registry` — сохранённый приватный
  реестр;
- `POST /api/hausman_hub/v1/admin/climate-registry` — полная атомарная замена
  реестра после строгой проверки всех полей; в активном `canary` замена
  запрещена, сначала требуется вернуться в `shadow` или `disabled`;
- `POST /api/hausman_hub/v1/admin/climate-registry-preview` — проверка и
  сверка несохранённого черновика без мутации;
- `GET /api/hausman_hub/v1/admin/climate-readiness` — только rollout mode,
  freshness, счётчики, результат сверки и нормализованные причины без адреса,
  `source_id` и `entity_id`;
- `POST /api/hausman_hub/v1/admin/climate-shadow-evidence` — результат
  `collecting`, `blocked` или `ready` для одного публичного HASC room ID и
  только обезличенные shadow-счётчики.
- `POST /api/hausman_hub/v1/admin/climate-canary-preflight` — тот же полный
  one-room preflight, что показывает options-flow, плюс явные времена проверки,
  исходного state и окончания его свежести. Принимается только публичный HASC
  room ID; активация структурно запрещена.

Аккаунт планшета не может читать административные маршруты. Администратор не
подменяет обычный аккаунт на Android-маршрутах.

Точные машинные контракты устанавливаются вместе с интеграцией в
`custom_components/hausman_hub/contracts/`. Все административные и командные
контракты остаются в `v1`; прежние home schema v1 и v2 сохранены для явного
распознавания старых ответов. В 0.5.8 актуальный Android home-контракт находится
в `v3/climate-home.schema.json`. Android должен закрепить поддерживаемую версию
и закрыться при неизвестной версии, а не угадывать форму ответа. Всего
устанавливается четырнадцать схем.

Home v3 возвращает для каждой комнаты публичный результат управления и точное
описание входных данных:

```json
{
  "control": {
    "enabled": false,
    "actions": ["set_room_target", "turn_room_off"],
    "action_inputs": {
      "set_room_target": {
        "target_temperature": {
          "type": "number",
          "required": true,
          "minimum": 18.0,
          "maximum": 28.0,
          "step": 0.5,
          "unit": "°C"
        }
      }
    },
    "blocked_reasons": ["shadow_only"]
  }
}
```

`actions` строится из сохранённого типа устройства и фактически объявленных
Climate API command types. `action_inputs` содержит ограничения только для
объявленных действий, которым действительно нужны значения; выключение
комнаты дополнительных данных не требует. `enabled=true` возможен только для выбранной canary
комнаты, когда свежесть, registry, authority, доступность устройства, shadow
evidence и отсутствие pending-операции проходят тот же runtime gate. В
`blocked_reasons` разрешены только нормализованные публичные причины:
`bridge_disabled`, `shadow_only`, `room_not_selected`, `state_stale`,
`registry_mismatch`, `authority_not_ready`, `device_unavailable`,
`actions_unsupported`, `evidence_not_ready`, `operation_pending`. Приватные
binding, backend payload и operation ID туда не попадают.

## Команды Android

Android отправляет только публичные ID HASC и типизированное значение:

```json
{
  "request_id": "android-0001",
  "action": "set_room_target",
  "room_id": "living",
  "target_temperature": 24.5
}
```

`request_id` обязателен и идемпотентен. Повтор с тем же ID и тем же intent
возвращает прежнюю квитанцию без второго чтения или POST; тот же ID с другим
intent отклоняется. HASC отвечает, например:

```json
{
  "contract": {"name": "hausman-hasc-operation", "version": 1},
  "operation_id": "0123456789abcdef0123456789abcdef",
  "request_id": "android-0001",
  "action": "set_room_target",
  "room_id": "living",
  "device_id": null,
  "status": "accepted",
  "execution": "shadow",
  "created_at": 1784280005000,
  "updated_at": 1784280005000,
  "known": true
}
```

Статусы: `accepted` для проверенного shadow intent, `pending` после принятого
canary POST, `confirmed` только после явно наблюдаемого read-only результата,
`rejected` при явном отрицательном ответе climate-core, `timed_out` при
отсутствии подтверждения и `unknown` для неизвестного или уже вытесненного
bounded-квитанцией operation ID. Транспортная неопределённость не выдаётся за
`rejected`, а возвращает недоступность. HTTP 200 от climate-core сам по себе
физическим подтверждением не является. Пока операция комнаты `pending`, второй
canary POST этой комнаты блокируется.

HASC резервирует operation/request ID до canary POST. Если после отправки
возникает транспортная неопределённость, первый HTTP-запрос завершается как
недоступный, но повтор с тем же request ID получает уже существующую `pending`
квитанцию и не создаёт второй POST.

После вытеснения квитанции её `request_id` остаётся в bounded fail-closed
фильтре загруженного runtime: прежнюю квитанцию уже нельзя получить, но повтор
не создаст второй POST. После перезагрузки runtime Android обязан не повторять
старые завершённые request ID.

Разрешены:

- для комнаты: `set_room_target`, `set_room_mode`, `set_room_min_target`,
  `set_room_target_strategy`, `turn_room_off`;
- для устройства: `set_device_power`, `set_device_target_temperature`,
  `set_device_target_humidity`, `set_device_hvac_mode`,
  `set_device_fan_mode`.

Температура ограничена диапазоном 18–28 °C и шагом 0,5 °C; humidity — целым
значением 30–70%; room mode — только `auto`/`manual`; target strategy — только
`soft`/`normal`/`aggressive`. Набор HVAC и fan modes также закрыт.

HASC сам находит приватный source, проверяет тип устройства и capability, а
затем переводит intent в существующий контракт climate-core. Клиент не может
передать service, backend command type, source ID, entity ID, произвольный URL
или произвольный вложенный payload.

## Disabled, shadow и canary

Климатический мост настраивается отдельно от прежнего режима наблюдения HASC.

- `disabled` — полный откат. Адрес и canary-комната удаляются из options,
  чтения и команды выключены; локальный администратор всё ещё может подготовить
  реестр.
- `shadow` — HASC читает Climate API, строит Android snapshot и проверяет
  перевод команд, но никогда не выполняет POST. Квитанция имеет статус
  `accepted` и `execution=shadow`. Не чаще одного раза в пять минут HASC
  сохраняет обезличенный результат сверки в rolling-окне на 24 часа.
- `canary` — POST разрешён только для одной явно выбранной комнаты, evidence
  которой уже имеет статус `ready`.

Shadow evidence хранит только время, публичный room ID, разрешённое имя
действия и счётчики `matched`, `missing`, `moved`, `stale`, `rejected`,
`translated`. Окно привязано хешем к точному реестру и очищается при любом
изменении его публичной или приватной части. Source IDs, entity IDs, адрес
Climate API, команды, payload и backend-ответы в evidence не попадают.

HASC 0.5.4 объединяет rollout-проверки в options-пункте «Проверить готовность
canary одной комнаты». Комната выбирается только из сохранённого registry.
Preflight выполняет свежий state GET лишь в `shadow`, повторно сверяет весь
реестр, показывает evidence, точный начальный command scope, состояние
pending-операции и готовность отката в `disabled`. Он возвращает только
публичный room ID и обезличенные статусы/счётчики. Даже результат `ready`
означает только `ready_for_authorization`: экран всегда оставляет
`activation.allowed=false`, не меняет options/registry, не включает canary и
не выполняет command POST.

HASC 0.5.5 публикует тот же результат для local admin по фиксированному POST.
Поле `freshness` содержит `checked_at`, `state_generated_at`,
`state_valid_until` и `state_fresh`. Время state действительно не более пяти
минут; просроченное или слишком будущее состояние добавляет
`preflight_state_not_fresh` и блокирует готовность. Route отвечает с
`Cache-Control: no-store`, запрещён аккаунту планшета и не имеет мутационной
ветки.

Для одной комнаты `ready` означает одновременно:

1. текущий snapshot свежий, зарегистрированные привязки точны и authority
   комнаты разрешён текущим climate-core;
2. `set_room_target` и `turn_room_off` поддерживаются текущим контрактом;
3. в окне есть не менее трёх совпавших наблюдений, разнесённых фиксированным
   пятиминутным интервалом;
4. обе начальные команды успешно прошли shadow-трансляцию;
5. для комнаты нет missing/moved/stale/rejected аномалии.

Перед каждым canary POST HASC заново требует:

1. свежий Climate API snapshot;
2. точное совпадение комнаты и сохранённой привязки;
3. `authority_eligible=true` от текущего climate-core;
4. scope устройства `canary` или `managed` и owner `climate_core`;
5. объявленный самим climate-core точный backend command type;
6. текущее устройство не помечено недоступным;
7. сохранённый shadow evidence `ready` для этой же комнаты.

Первый исполняемый canary-набор HASC 0.5.2 и новее дополнительно ограничен
`set_room_target` и `turn_room_off`. Остальные типизированные действия можно
проверять в shadow, но runtime не отправит их в физический canary.

Сам climate-core после этого сохраняет свои обычные safety, cooldown,
автоматическую политику и physical feedback. HTTP redirect, публичный адрес,
DNS-имя, credentials в URL, неизвестная команда, лишнее поле, большой или
повреждённый ответ закрывают операцию.

## Порядок первого включения

1. Установить HASC 0.5.8, оставить climate bridge в `disabled`.
2. В options выбрать `shadow` и указать только приватный literal-IP origin
   текущего Climate API, например `http://192.168.1.10:1880`.
3. В options выбрать настройку климатических устройств. Для каждого свежего
   кандидата задать публичный HASC ID, выбрать control entity из списка,
   проверить preview и отдельно подтвердить atomic save.
4. Проверить Android snapshot и оба начальных shadow-действия. Ни одно из них
   не должно выполнить POST. Подождать три разнесённых наблюдения.
5. В мастере выбрать «Посмотреть shadow-доказательства», затем комнату. Пока
   результат не `ready`, canary остаётся закрыт.
6. Выбрать «Проверить готовность canary одной комнаты». Требовать полный
   preflight `ready`, `operation=clear`, `rollback=ready` и точный scope из
   двух начальных действий. Это ещё не включает управление.
7. Проверить неактивный [one-room rollout
   checklist](climate-canary-rollout-checklist.md). Только после отдельного
   разрешения на живой физический canary назначить
   устройству scope `canary`, включить bridge mode `canary` и указать тот же
   room ID.
8. Проверить одно изменение target, затем перечитать snapshot и физическое
   подтверждение в существующем контуре.
9. При любом расхождении вернуть `disabled`: это удаляет адрес и room ID из
   options и немедленно закрывает командный путь.

В репозитории нет live-адреса и готового реестра конкретного дома. Версия
0.5.8 подготавливает для Android публичный home v3 со статусом кнопок,
причинами блокировки и точными ограничениями температуры. Она не меняет
Android-репозиторий и не разрешает
физический canary. Переключение установленного дома и Android-приложения
выполняется отдельным контролируемым этапом после shadow.
