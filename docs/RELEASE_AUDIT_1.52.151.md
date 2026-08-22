# Hausman for Home Assistant 1.52.151: зрелая энергетика

Дата: 2026-08-22

## Что изменено

- Contracts `0.54.0` и HACS добавляют коллекцию до 16 счётчиков через
  `/api/hausman_hub/v1/energy/meters`. Старый основной счётчик и его singular
  API сохранены без несовместимого изменения.
- История поддерживает календарные окна `day`, `week`, `month` с явной
  временной зоной и корректной границей суток при переходах DST.
- Настраиваемая аномалия мощности с порогом и длительностью срабатывает только
  после устойчивого превышения. При неполных или устаревших данных состояние
  закрывается fail-closed.
- Интерфейсный baseline другой ветки сохранён. В энергетическом экране сделаны
  только совместимые additions и cache-bust.

## Релиз и проверки

- Contracts `0.54.0`: 60 schemas, 115 fixtures, 45 OpenAPI paths,
  20 error policies, 11 correlation surfaces, 5 pagination surfaces,
  19 feature types и 3 migration views. Проверены коллекции из 1, 2 и 10
  счётчиков, restart, stale data и API E2E.
- HACS feature release `1.52.149`, commit `1979ad2`, прошёл полный gate:
  1626 passed, 4 skipped, 998 subtests. GitHub Actions `32584959669` success.
- Эксплуатационная проверка 1.52.149 выявила ошибку передачи календарного окна
  в HA adapter. Исправление выпущено как `1.52.150`, commit `6da2ce0`, Actions
  `32585958509` success.
- На живой системе также обнаружен `NameError` климатического tick при
  непустом deviation guard. Импорт `replace` восстановлен и закрыт отдельным
  регрессионным тестом. Финальный release `1.52.151`, commit `47edbf4`, полный
  gate: 1625 passed, 4 skipped. Actions `32586366768` success.
- GitHub Releases опубликованы без assets:
  `v1.52.149`, `v1.52.150`, `v1.52.151`.

## Production deploy

- Использован свежий полный защищённый rollback backup `74abc5cf`, подтверждённый
  ранее в `hassio.local` и `hassio.KeeneticSSD`. Повторная копия для hotfix не
  создавалась.
- До установки и после restart config check вернул HTTP 200 без ошибок и
  предупреждений. HACS refresh увидел точную `v1.52.151`; exact
  `update.install` выполнен с `backup=false`.
- Installed и latest равны `v1.52.151`, config entries HACS и Hausman Hub
  загружены. Dashboard вернул 13 комнат, 87 устройств, 35 сценариев и два
  источника энергии. Коллекция счётчиков отвечает HTTP 200.
- Календарная история суток `Europe/Moscow` отвечает HTTP 200: 12 series и
  237 points, границы `2026-08-22T00:00:00+03:00` и
  `2026-08-23T00:00:00+03:00`.
- Все 88 frontend JS/CSS файлов совпадают с release по SHA-256. После двух
  полных климатических интервалов журнал Hausman остался пустым, прежний
  `NameError` не повторился.

## Совместимость

- Contracts `0.54.0`, Hausman for Home Assistant `1.52.151`, Android
  `1.0.246` build `250` до выпуска клиентской части направления 16.
