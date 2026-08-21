# Hausman Hub HACS 1.52.134: release audit

Дата: 2026-08-21

## Результат

- Главная Hausman for Home Assistant получила тот же предел ширины, что и
  обычные страницы: 1600 px с центрированием и полями 34 px.
- Планшетная компоновка внутри главной, Android, API и contracts не менялись.
- Изменение задачи ограничено HACS frontend. Release собран поверх текущего
  `origin/main` и содержит ранее принятый backend commit `4c79d8d`; backend
  этой задачей не редактировался.
- Feature commit: `a1e06e8`.
- Release commit/tag: `287f1e4`, `v1.52.134`.
- GitHub Release без assets:
  https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.52.134.

## Проверки выпуска

- Профильные проверки до выпуска: 118 passed, 82 subtests. Контракт главной:
  31 passed, 50 subtests.
- Staged `python3 tools/check_local_release.py`: 1547 tests, 4 skipped;
  package, fixtures, Android compatibility, README sync и version gate
  успешны.
- GitHub Actions `32514457615` завершён со статусом `success`.

## Production deploy

- Цель: Home Assistant `172.30.0.92`.
- Перед установкой создан full backup `2ac3c237` с Home Assistant, базой,
  10 дополнениями и папками media/share/ssl. Защищённые копии подтверждены
  в local и KeeneticSSD, размер каждой 928583680 байт.
- Через `update.install` установлена явная версия `v1.52.134` без второго
  backup. Штатный `homeassistant.check_config` завершился HTTP 200.
- Home Assistant перезапущен один раз. Повторная команда restart не
  отправлялась.

## Проверка после restart

- Update entity: installed/latest `v1.52.134`, `in_progress=false`.
- Статическая панель и CSS главной отвечают HTTP 200, cache version равен
  `1.52.134`.
- SHA-256 всех 28 изменённых frontend-файлов на production совпадает с
  файлами release commit.
- Доступны 10 релевантных сущностей Hausman Hub, unavailable или unknown
  среди них нет. В системном журнале нет ошибок или traceback Hausman Hub.
- В production Chrome при viewport 2560x1306 вычисленная ширина `main`
  главной и «Освещения» равна 1600 px. Обе страницы центрируются и имеют
  горизонтальные поля 34 px, runtime errors отсутствуют.
- Физические команды устройствам, сценариям и климату не отправлялись.

## Откат

При проблеме восстановить full backup `2ac3c237`. Он содержит состояние до
установки `v1.52.134` и доступен в двух настроенных хранилищах.
