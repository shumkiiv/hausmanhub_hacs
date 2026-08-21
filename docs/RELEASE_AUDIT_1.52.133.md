# Hausman Hub HACS 1.52.133: release audit

Дата: 2026-08-21

## Результат

- Главная Hausman for Home Assistant и режим киоска повторяют актуальную
  планшетную компоновку. Сохранены белые кнопки левого меню и реальные
  значения HACS без демонстрационных подстановок.
- Изменения ограничены frontend HACS, его тестами и документацией. Backend,
  API, contracts, Android и storage не менялись.
- Feature commits: `e69d482`, `c24b339`.
- Release commit/tag: `b69d84c`, `v1.52.133`.
- GitHub Release без assets:
  https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.52.133.

## Проверки выпуска

- Визуально проверены light/dark, 1280x800 и 1600x1000, раскрытый и
  свёрнутый rail, обычная главная и kiosk. Overflow и runtime errors не
  обнаружены.
- Полный pytest до подготовки версии: 1548 passed, 4 skipped, 984 subtests.
- Staged `python3 tools/check_local_release.py`: 1546 passed, 4 skipped;
  package, schema, fixtures, Android compatibility, README sync и version
  gate успешны.
- GitHub Actions `32507160930` для release commit завершён со статусом
  `success`.

## Production deploy

- Цель: Home Assistant Core `2026.8.2`, `172.30.0.92`.
- Перед установкой создан full backup `a2c07631` с Core, базой, всеми
  дополнениями и папками media/share. Backup защищён и проверен в
  `hassio.local` и `hassio.KeeneticSSD`, размер каждой копии 926402560 байт.
- Через `update.install` установлена явная версия `v1.52.133` без второго
  backup. Штатный `homeassistant.check_config` завершился HTTP 200.
- Core перезапущен один раз. Повторная команда restart не отправлялась.

## Проверка после restart

- Update entity: installed/latest `v1.52.133`, `in_progress=false`.
- Статическая панель отвечает HTTP 200 и содержит cache version `1.52.133`.
- SHA-256 всех 31 изменённых frontend-файлов на production совпадает с
  файлами release commit, расхождений нет.
- Доступны 10 сущностей Hausman Hub, unavailable или unknown среди них нет.
- В журнале после запуска нет ERROR, CRITICAL или traceback для Hausman Hub.
  Две строки WARNING являются штатным предупреждением Home Assistant о
  сторонней custom integration.
- Физические команды устройствам, сценариям и климату не отправлялись.

## Откат

При проблеме восстановить full backup `a2c07631`. Он содержит состояние до
установки `v1.52.133` и доступен в двух настроенных хранилищах.
