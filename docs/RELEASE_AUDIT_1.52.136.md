# Hausman Hub HACS 1.52.136: release audit

Дата: 2026-08-22

## Результат

- Исполнение сценариев стало ограниченным и наблюдаемым: очередь имеет
  предел, переполнение получает явный результат, а режимы single, restart и
  queued сохраняют прежнюю семантику.
- Триггеры поддерживают выдержку состояния, debounce, cooldown и явное
  разрешение recovery-событий. Отложенные задачи отменяются при unload.
- Условия одного запуска читаются единым snapshot. Недоступные данные и
  устаревшие доказательства для критичных lock/valve-команд блокируют
  выполнение.
- Добавлены общая идемпотентность действий, результат partial, ограничение
  вложенности и проверка циклов до сохранения.
- Журнал операций хранит редактированный trace решений, действий и исходов
  без entity ID, пользовательских имён и текста исключений.
- Контракты закреплены на `hausmanhub-contracts 0.47.0`, commit `57a1b04`.
- Feature commit `e4bca0a`, release commit/tag `02206a7`, `v1.52.136`.
- Визуальная компоновка HACS 1.52.135 сохранена. Frontend изменён только
  механическим cache/version bump.
- GitHub Release без assets:
  https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.52.136.

## Проверки выпуска

- Профиль safety: 158 tests и 3 subtests.
- Полный `python3 tools/check_local_release.py`: 1562 tests, 4 skipped.
  Fixtures, Android compatibility, README sync, version gate, HACS package и
  repository safety успешны.
- GitHub Actions `32554721280` завершён со статусом `success`.

## Production deploy

- Цель: Home Assistant `172.30.0.92`.
- До установки `homeassistant.check_config` завершился HTTP 200.
- Создан full backup `f86830d3`, 917975040 байт в `hassio.local`. В backup
  входят Home Assistant 2026.8.2, база, 10 add-ons и папка `ssl`; списки
  failed add-ons, folders и agents пусты.
- Через `update.install` установлена явная версия `v1.52.136` без второго
  backup. HACS обновил cached latest в ходе штатной установки по GitHub-тегу.
- Повторный `homeassistant.check_config` завершился HTTP 200. Home Assistant
  перезапущен ровно один раз и вернулся через 41.7 секунды.

## Проверка после restart

- Update entity: installed/latest `v1.52.136`, `in_progress=false`.
- Config entry Hausman Hub имеет состояние loaded.
- Статическая панель отвечает HTTP 200 и содержит cache version `1.52.136`.
- SHA-256 всех 27 изменённых frontend assets на production совпадает с
  release commit.
- Доступны 12 релевантных сущностей, unavailable и unknown нет.
- Operation journal отвечает HTTP 200 и проходит новую JSON Schema; проверено
  5 последних записей.
- System log не содержит ошибок или traceback Hausman Hub. Есть одно
  известное WARNING о расхождении физического и погодного источников наружной
  температуры; команды остаются разблокированы.
- Физические команды устройствам, сценариям и климату не отправлялись.

## Откат

При проблеме восстановить full backup `f86830d3`. Он содержит состояние до
установки `v1.52.136`.
