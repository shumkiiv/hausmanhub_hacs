# Hausman for Home Assistant 1.52.148: зрелое управление освещением

Дата: 2026-08-22

## Что изменено

- Каталог действий света формируется из реальных HA capabilities:
  relay-only получает on/off, dimmer дополнительно brightness/adaptive/night,
  CCT получает цветовую температуру, RGB получает `set_rgb_color`.
- При отсутствии runtime attributes возможности закрываются fail-closed и не
  выдумываются по одному только домену entity.
- Adaptive brightness и night light представлены понятными value policies с
  preview. Night light ограничен безопасным диапазоном 1-30 процентов.
- Добавлен ordered batch до 64 device actions с одним обновлением каталога,
  запретом дубликатов и отдельным receipt/SSE-событием для каждого устройства.
  Aggregate receipt честно различает confirmed, accepted, partial и failed.
- Durable operation journal сохраняет источник запуска, стабильный trigger
  target ID, recovery marker для `unavailable -> available`, target ID каждого
  действия, old/new/target values и command mode.
- Pin обновлён до contracts `0.53.0`. Frontend-компоновка релиза 1.52.147
  сохранена: выполнены только cache-bust и fail-closed snapshot additions.

## Проверено до релиза

- Contracts validator: 59 schemas, 108 fixtures, 44 OpenAPI paths,
  20 error policies, 11 correlation command surfaces, 5 pagination surfaces,
  19 device feature types и 3 migration views.
- Полный HACS gate после rebase на UI 1.52.147: 1618 passed, 4 skipped,
  995 subtests.
- GitHub Actions `32578493597`: success.
- Release/tag target `e9ec853`, Release `v1.52.148`, assets отсутствуют.

## Production deploy

- Перед установкой config check вернул HTTP 200.
- Автоматический backup `3a6e9bfd` защищён и подтверждён в `hassio.local` и
  `hassio.KeeneticSSD`, по 922449920 байт. В составе Home Assistant с БД,
  `share`, `ssl`, `media` и 10 add-ons.
- HACS refresh увидел `1.52.148`; выполнен exact `update.install` без второго
  backup. Installed/latest после установки равны `v1.52.148`.
- После установки и после единственного restart config check снова вернул
  HTTP 200. Config entry `hausman_hub` загружен.
- Все 88 frontend JS/CSS assets побайтно совпадают с release. В system log нет
  ошибок Hausman.
- Capabilities, scenario catalog и operation journal проходят JSON Schema.

## Live matrix

- Production catalog: 466 devices и 39 scenarios. Матрица света содержит
  4 relay-only, 3 dimmer+CCT, 1 dimmer+CCT+RGB и 1 dimmer+RGB профиль.
- Shadow-run `system-away-turn-off` завершил 24 действия без физических HA
  service calls. Запись журнала sequence 1000 сохранила source `manual`,
  command mode `shadow`, outcome `completed` и стабильные target ID.
- Relay `Ночник у двери`: безопасный no-op `turn_off` подтверждён read-back за
  одну попытку, состояние осталось off.
- `Люстра кабинет`: no-op текущей brightness 255 и CCT 4000 подтверждён за
  одну попытку, состояние не изменилось.
- Недоступная RGB `Люстра прибор`: безопасный `turn_off` принят, но не выдан за
  подтверждённый, reason `state_not_confirmed`, observed state `unavailable`.
- Принудительный сетевой outage не создавался. Recovery marker закрыт
  integration-тестом, а живой `unavailable -> available` остаётся
  наблюдательным gate без вмешательства в сеть дома.

## Совместимость

- Contracts `0.53.0`, Hausman for Home Assistant `1.52.148`, Android
  `1.0.246` build `250`.
