# Release audit HACS 1.52.154

Дата: 2026-08-22. Backend направления 20, зрелость редактора сценариев.

## Что выпущено

- Runtime-каталог добавляет числовым действиям политику значения: единицу,
  шаг и доступные границы Home Assistant. Клиенту не нужно угадывать параметры.
- Dry-run сценария возвращает строгий человекочитаемый `report` с условиями,
  шагами и публичными названиями устройств. В отчёте всегда
  `commandSent=false`, технические entity ID не публикуются.
- Поддержка `roomId` уже присутствовала с contracts 0.51.0 и HACS 1.52.144,
  поэтому повторная несовместимая реализация не создавалась.
- Выверенный HACS frontend сохранён. Изменён только cache-bust версии.

## Проверки релиза

- Contracts `0.56.0`, commit `078ebd0`: 62 schemas, 121 fixtures,
  45 OpenAPI paths, 20 error policies, 11 correlation surfaces,
  5 pagination/retention surfaces, 19 device types и 3 migration views.
- HACS commit/tag `029e4ab`, GitHub Actions `32595001493` завершился успешно.
  GitHub Release:
  `https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.52.154`.
- Полный локальный gate: `1640 passed, 4 skipped, 1003 subtests passed`.
  Targeted gate: 75 tests и 13 subtests.
- Все 88 frontend JS/CSS assets в production побайтно совпадают с release.

## Production deploy

- Перед установкой создан backup `80718cd9`. Home Assistant и база включены,
  копии по 928481280 байт подтверждены в `hassio.local` и
  `hassio.KeeneticSSD`, ошибки backup agents отсутствуют.
- Выполнена точная установка `v1.52.154` через `update.install` с
  `backup=false`. До установки и после единственного restart
  `homeassistant.check_config` вернул HTTP 200.
- После restart installed/latest равны `v1.52.154`, config entry
  `HausmanHub` загружен. В system log нет ошибок Hausman.
- Production catalog содержит 464 устройства и 40 числовых action policies.
- Live dry-run проверил три цепочки: движение и свет, закат и шторы,
  влажность и вытяжка. Рабочие цели получили `planned`, во всех отчётах
  `commandSent=false`; entity ID отсутствуют.
- Sequence operation journal до и после dry-run равна 1394. Сценарии не
  сохранялись, физические команды не отправлялись.

## Откат

При проблеме восстановить backup `80718cd9` из локального хранилища или
KeeneticSSD. Он возвращает Home Assistant и базу к состоянию до установки
HACS 1.52.154.
