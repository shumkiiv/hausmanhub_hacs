# Hausman Hub HACS 1.52.135: release audit

Дата: 2026-08-22

## Результат

- Главная приведена к масштабу остальных страниц панели. Типографика, Hero,
  карточки метрик, интервалы и правая колонка больше не выглядят
  увеличенными относительно «Освещения».
- В «Освещение» перенесены последние планшетные правки: отдельные физические
  каналы, образ потолочного светильника и парные диапазоны яркости и цветовой
  температуры.
- Изменения ограничены HACS frontend. Android, backend, API, contracts и
  storage не менялись.
- Feature commits: `e6e2cf4`, `0a65de4`.
- Release commit/tag: `12c4609`, `v1.52.135`.
- GitHub Release без assets:
  https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.52.135.

## Проверки выпуска

- Staged `python3 tools/check_local_release.py`: 1548 tests, 4 skipped.
  Fixtures, Android compatibility, README sync, version gate, HACS package и
  repository safety успешны.
- GitHub Actions `32552485736` завершён со статусом `success`.

## Production deploy

- Цель: Home Assistant `172.30.0.92`.
- Перед установкой создан full backup `362c8bac` с Home Assistant, базой,
  10 add-ons и папкой `ssl`. Копии по 916940800 байт подтверждены в
  `hassio.local` и `hassio.KeeneticSSD`.
- Через `update.install` установлена явная версия `v1.52.135` без второго
  backup. Штатный `homeassistant.check_config` завершился HTTP 200.
- Home Assistant перезапущен ровно один раз и вернулся примерно за 25 секунд.

## Проверка после restart

- Update entity: installed/latest `v1.52.135`, `in_progress=false`.
- Статическая панель отвечает HTTP 200 и загружает cache version `1.52.135`.
- SHA-256 всех 30 изменённых frontend assets на production совпадает с
  файлами release commit.
- Доступны 10 релевантных сущностей Hausman Hub, unavailable и unknown нет.
- System log содержит одно штатное WARNING custom integration, ошибок и
  traceback Hausman Hub нет.
- На production Chrome при viewport 2560x1306 вычисленная ширина `main`
  главной и «Освещения» равна 1600 px. Горизонтального overflow нет,
  страницы визуально проверены после сброса прокрутки.
- JS exceptions отсутствуют. Браузерная сессия пишет пять network 403 для
  scope-limited `capabilities/events`; отдельно проверено, что capabilities
  с read token отвечает HTTP 200. Это не регрессия frontend выпуска.
- Физические команды устройствам, сценариям и климату не отправлялись.

## Откат

При проблеме восстановить full backup `362c8bac`. Он содержит состояние до
установки `v1.52.135` и доступен в local и KeeneticSSD.
