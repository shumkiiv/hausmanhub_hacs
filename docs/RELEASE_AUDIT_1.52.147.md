# Hausman Hub HACS 1.52.147: плотность интерфейса

Дата: 2026-08-22

## Результат

- Главная HACS синхронизирует высоту левого меню, центральной ленты и правой
  активности. Энергия и освещение раскрываются вместе и заполняют доступное
  место без пустого участка снизу.
- Hero увеличен до 280 px. Ближайшие события получили компактную высоту и
  внутреннюю прокрутку. Активная комната больше не теряет верхнюю рамку.
- Физические карточки устройств приведены к масштабу остальных страниц:
  высота 150 px, изображение 56 px, заголовок 16 px.
- Изменены только HACS frontend, его тесты, документация и cache/version.
  Android, backend, API, contracts, storage и runtime-политики не менялись.

## Релиз и проверки

- Feature commits: `305c83c`, `ddd6d6e`, `5a2387f`.
- Release commit/tag: `aa4ef59`, `v1.52.147`.
- GitHub Release:
  https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.52.147.
- Полный локальный gate: 1609 passed, 4 skipped; package, contracts,
  README version sync и repository safety checks успешны.
- UI-профиль: 102 passed, 50 subtests. Browser QA охватил главную в светлой
  и тёмной темах 1504x1146, а устройства в светлой и тёмной темах
  1504x1800 и в светлой теме 900x1200. Обрезки, горизонтального overflow и
  runtime errors нет.
- GitHub Actions `32576732490` завершён успешно за 3 минуты 18 секунд:
  https://github.com/shumkiiv/hausmanhub_hacs/actions/runs/32576732490.

## Production deploy

- До установки создан full backup `aab91cba`. Защищённые копии по
  921815040 байт подтверждены в `hassio.local` и `hassio.KeeneticSSD`,
  `failed_agent_ids` пуст.
- До установки и после restart `homeassistant.check_config` вернул HTTP 200.
- `v1.52.147` установлена явно через `update.install` с `backup=false`.
  Home Assistant перезапущен ровно один раз, прошёл недоступное состояние и
  восстановился в `RUNNING`.
- Update entity подтвердил installed/latest `v1.52.147`, in_progress false.
  Config entry `HausmanHub` находится в состоянии loaded.

## Проверка после restart

- Dashboard и список ближайших событий отвечают HTTP 200. Dashboard вернул
  13 комнат, 87 устройств, 35 сценариев и 0 ошибок. Upcoming API вернул
  11 событий.
- Climate deviation guard сохранил revision 1 и одну настройку monitor.
  Вооружённых настроек нет.
- Operation journal остался на sequence 998. Deploy и restart не добавили
  операций или climate guard events.
- Все 88 production JS/CSS-файлов побайтно совпадают с release по SHA-256.
  Panel JS отвечает HTTP 200 и содержит cache version `1.52.147`.
- В retained system log нет записей и ошибок Hausman. Физические команды не
  отправлялись.

## Откат

- Для полного отката использовать backup `aab91cba`.
- Исходный frontend можно вернуть на `v1.52.146`; backend и storage
  совместимы, потому что релиз меняет только интерфейс.
